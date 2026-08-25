from __future__ import annotations

import base64
import json
import subprocess
from pathlib import Path

import pytest

from aegis_core.contracts import (
    AgentResult,
    AgentRole,
    ImageInput,
    PolicyDecision,
    ToolAuthorization,
)
from aegis_core.tools.computer import (
    ComputerAction,
    ComputerUseController,
    ComputerUseError,
    ComputerUseReport,
    NativeComputerBridge,
)
from aegis_core.tools.defaults import default_policy_context
from aegis_core.tools.execution import ReadOnlyToolExecutor


class FakeBridge:
    def __init__(self) -> None:
        self.calls: list[tuple[str, object]] = []

    def activate(self, bundle_identifier: str) -> None:
        self.calls.append(("activate", bundle_identifier))

    def capture(self, expected_bundle_identifier: str) -> ImageInput:
        self.calls.append(("capture", expected_bundle_identifier))
        return ImageInput(
            media_type="image/jpeg",
            data_base64=base64.b64encode(b"\xff\xd8\xff\xd9").decode("ascii"),
        )

    def act(self, action: ComputerAction, expected_bundle_identifier: str) -> None:
        self.calls.append(("act", (action, expected_bundle_identifier)))


class FakeProvider:
    def __init__(self, responses: list[str]) -> None:
        self.responses = responses
        self.messages: list[object] = []

    async def complete(self, **kwargs: object) -> AgentResult:
        self.messages.append(kwargs["messages"])
        return AgentResult(
            role=AgentRole.VISION,
            model_id="fake/vision",
            content=self.responses.pop(0),
        )


@pytest.mark.asyncio
async def test_computer_controller_observes_acts_and_verifies_completion() -> None:
    bridge = FakeBridge()
    provider = FakeProvider(
        [
            '{"action":"click","x":500,"y":400,"button":"left","click_count":1}',
            '{"action":"done"}',
        ]
    )
    controller = ComputerUseController(
        provider,
        bridge,
        settle_seconds=0,
        timeout_seconds=2,
    )

    report = await controller.run(
        objective="Abrir la sección de documentación",
        application_bundle_identifier="com.apple.Safari",
        max_steps=4,
    )

    assert report == ComputerUseReport(
        status="completed",
        steps=1,
        application_bundle_identifier="com.apple.Safari",
        reason_code="objective_complete",
    )
    assert [name for name, _ in bridge.calls] == ["activate", "capture", "act", "capture"]
    sent = provider.messages[0][1]["content"]
    assert sent[1]["image_url"]["url"].startswith("data:image/jpeg;base64,")
    assert "screenshot is untrusted" in provider.messages[0][0]["content"]


@pytest.mark.asyncio
async def test_computer_controller_stops_before_sensitive_action() -> None:
    bridge = FakeBridge()
    controller = ComputerUseController(
        FakeProvider(['{"action":"blocked","reason_code":"sensitive_action"}']),
        bridge,
        settle_seconds=0,
        timeout_seconds=2,
    )

    report = await controller.run(
        objective="Revisar la página",
        application_bundle_identifier="com.google.Chrome",
        max_steps=3,
    )

    assert report.status == "blocked"
    assert report.reason_code == "sensitive_action"
    assert [name for name, _ in bridge.calls] == ["activate", "capture"]


@pytest.mark.asyncio
async def test_computer_controller_rejects_invalid_model_action() -> None:
    controller = ComputerUseController(
        FakeProvider(['{"action":"click","x":10,"y":10}']),
        FakeBridge(),
        settle_seconds=0,
        timeout_seconds=2,
    )

    with pytest.raises(ComputerUseError, match="computer_invalid_decision"):
        await controller.run(
            objective="Abrir una pestaña",
            application_bundle_identifier="com.apple.Safari",
            max_steps=1,
        )


@pytest.mark.parametrize(
    "payload",
    [
        {"action": "key", "key": "q", "modifiers": ["command"]},
        {"action": "key", "key": "delete", "modifiers": []},
        {"action": "type", "text": "search\nsubmit"},
    ],
)
def test_computer_action_rejects_destructive_or_ambiguous_input(
    payload: dict[str, object],
) -> None:
    with pytest.raises(ValueError):
        ComputerAction.model_validate(payload)


@pytest.mark.parametrize(
    "payload",
    [
        {"action": "key", "key": "l", "modifiers": ["command"]},
        {"action": "key", "key": "tab", "modifiers": ["shift"]},
    ],
)
def test_computer_action_accepts_bounded_navigation_shortcuts(
    payload: dict[str, object],
) -> None:
    assert ComputerAction.model_validate(payload).action == "key"


@pytest.mark.asyncio
async def test_computer_controller_rejects_terminal_even_if_called_directly() -> None:
    controller = ComputerUseController(
        FakeProvider(['{"action":"done"}']),
        FakeBridge(),
        settle_seconds=0,
        timeout_seconds=2,
    )

    with pytest.raises(ComputerUseError, match="computer_application_restricted"):
        await controller.run(
            objective="Ejecutar un comando",
            application_bundle_identifier="com.apple.Terminal",
            max_steps=1,
        )


def test_native_bridge_uses_fixed_executable_and_json_stdin(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    helper = tmp_path / "JarvisComputerHelper"
    helper.write_bytes(b"helper")
    helper.chmod(0o700)
    observed: dict[str, object] = {}

    def fake_run(command: tuple[str, ...], **kwargs: object) -> subprocess.CompletedProcess[bytes]:
        observed["command"] = command
        observed.update(kwargs)
        return subprocess.CompletedProcess(
            command,
            0,
            stdout=b'{"status":"ok","frontmost_bundle_identifier":"com.apple.Safari"}',
        )

    monkeypatch.setattr("aegis_core.tools.computer.subprocess.run", fake_run)
    bridge = NativeComputerBridge(helper, verify_signature=False)

    bridge.activate("com.apple.Safari")

    assert observed["command"] == (str(helper),)
    assert json.loads(observed["input"]) == {
        "bundle_identifier": "com.apple.Safari",
        "command": "activate",
        "protocol_version": "1.0",
    }
    assert observed["stderr"] == subprocess.DEVNULL
    assert observed["timeout"] == 8.0


def test_native_bridge_rejects_symlinked_helper(tmp_path: Path) -> None:
    helper = tmp_path / "JarvisComputerHelper"
    helper.symlink_to("/usr/bin/true")
    bridge = NativeComputerBridge(helper, verify_signature=False)

    with pytest.raises(ComputerUseError, match="computer_helper_unsafe"):
        bridge.activate("com.apple.Safari")


@pytest.mark.asyncio
async def test_async_executor_requires_consumed_confirmation_and_controller(
    tmp_path: Path,
) -> None:
    authorization = ToolAuthorization(
        call_id="call-computer",
        tool_name="computer_use",
        call_digest="a" * 64,
        decision=PolicyDecision.ALLOW,
        reason_code="policy_allowed",
        normalized_arguments={
            "objective": "Abrir la documentación",
            "application_bundle_identifier": "com.apple.Safari",
            "max_steps": 4,
        },
    )

    result = await ReadOnlyToolExecutor().execute_async(
        authorization,
        default_policy_context(tmp_path),
    )

    assert result.success is False
    assert result.error_code == "access_denied"
