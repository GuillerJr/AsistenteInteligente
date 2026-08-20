from __future__ import annotations

import base64
from types import SimpleNamespace
from typing import Any, ClassVar

import pytest

from aegis_core import cli
from aegis_core.contracts import AgentResult, AgentRole, ToolCall


class FakeKeychain:
    def __init__(self, **_: object) -> None:
        pass

    def get(self) -> str:
        return "secret-value"


class FakeToolClient:
    result = AgentResult(
        role=AgentRole.CODE_SECURITY,
        model_id="fake/code-security",
        content="",
        tool_calls=(
            ToolCall(
                call_id="call-probe",
                tool_name="terminal_run_template",
                arguments={"template": "security_posture"},
                requested_by=AgentRole.CODE_SECURITY,
            ),
        ),
    )
    observed: ClassVar[dict[str, Any]] = {}

    def __init__(self, settings: object, api_key_loader: Any) -> None:
        del settings
        assert api_key_loader() == "secret-value"

    async def __aenter__(self) -> FakeToolClient:
        return self

    async def __aexit__(self, *_: object) -> None:
        pass

    async def complete(self, **kwargs: Any) -> AgentResult:
        type(self).observed = kwargs
        return type(self).result


class FakeSoakClient:
    calls = 0
    health_calls = 0
    restart = False

    def __init__(self, *_: object, **__: object) -> None:
        type(self).calls = 0
        type(self).health_calls = 0

    async def call(self, method: str) -> SimpleNamespace:
        type(self).calls += 1
        if method == "health":
            type(self).health_calls += 1
            pid = 101 if not self.restart or self.health_calls == 1 else 202
            payload = {"protocol_version": "1.0", "architecture": "arm64", "pid": pid}
        elif method == "runtime.info":
            payload = {"architecture": "arm64", "operating_system": "Darwin"}
        elif method == "security.status":
            payload = {"state": "intact"}
        else:
            assert method == "swarm.activity"
            payload = {"agents": []}
        return SimpleNamespace(ok=True, payload=payload)


@pytest.mark.asyncio
async def test_daemon_soak_checks_four_surfaces_per_cycle(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    settings = SimpleNamespace(
        ipc_socket_path="/tmp/fake.sock",
        ipc_max_frame_bytes=65_536,
        ipc_clock_skew_seconds=30,
    )
    FakeSoakClient.restart = False
    monkeypatch.setattr(cli, "Settings", lambda: settings)
    monkeypatch.setattr(cli, "_ipc_authenticator", lambda *_args, **_kwargs: object())
    monkeypatch.setattr(cli, "IpcClient", FakeSoakClient)

    status = await cli.daemon_soak(cycles=3, max_p95_ms=1_000)

    assert status == 0
    assert FakeSoakClient.calls == 12
    assert capsys.readouterr().out.startswith("status=ok cycles=3 p95_ms=")


@pytest.mark.asyncio
async def test_daemon_soak_detects_daemon_restart(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    settings = SimpleNamespace(
        ipc_socket_path="/tmp/fake.sock",
        ipc_max_frame_bytes=65_536,
        ipc_clock_skew_seconds=30,
    )
    FakeSoakClient.restart = True
    monkeypatch.setattr(cli, "Settings", lambda: settings)
    monkeypatch.setattr(cli, "_ipc_authenticator", lambda *_args, **_kwargs: object())
    monkeypatch.setattr(cli, "IpcClient", FakeSoakClient)

    status = await cli.daemon_soak(cycles=3, max_p95_ms=1_000)

    assert status == 1
    assert capsys.readouterr().out == "status=error reason=daemon_restarted\n"


@pytest.mark.asyncio
async def test_tool_probe_validates_function_call_without_execution(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(cli, "MacOSKeychain", FakeKeychain)
    monkeypatch.setattr(cli, "NvidiaNimClient", FakeToolClient)

    status = await cli.probe_nvidia_tools()

    assert status == 0
    assert capsys.readouterr().out == (
        "status=ok model=fake/code-security tool=terminal_run_template "
        "execution=none credential=keychain\n"
    )
    extra_body = FakeToolClient.observed["extra_body"]
    assert extra_body["tool_choice"] == "required"
    schemas = extra_body["tools"]
    assert len(schemas) == 1
    assert schemas[0]["function"]["name"] == "terminal_run_template"


@pytest.mark.asyncio
async def test_tool_probe_rejects_unexpected_provider_response(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(cli, "MacOSKeychain", FakeKeychain)
    monkeypatch.setattr(cli, "NvidiaNimClient", FakeToolClient)
    monkeypatch.setattr(
        FakeToolClient,
        "result",
        FakeToolClient.result.model_copy(update={"tool_calls": ()}),
    )

    status = await cli.probe_nvidia_tools()

    assert status == 1
    assert capsys.readouterr().out == "status=error reason=invalid_tool_call_response\n"


@pytest.mark.asyncio
async def test_vision_probe_uses_only_inline_synthetic_image(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(cli, "MacOSKeychain", FakeKeychain)
    monkeypatch.setattr(cli, "NvidiaNimClient", FakeToolClient)
    monkeypatch.setattr(
        FakeToolClient,
        "result",
        AgentResult(
            role=AgentRole.VISION,
            model_id="fake/vision",
            content="OK",
        ),
    )

    status = await cli.probe_nvidia_vision()

    assert status == 0
    output = capsys.readouterr().out
    assert output == "status=ok model=fake/vision input=synthetic credential=keychain\n"
    content = FakeToolClient.observed["messages"][0]["content"]
    assert content[1]["type"] == "image_url"
    data_uri = content[1]["image_url"]["url"]
    assert data_uri.startswith("data:image/png;base64,")
    assert base64.b64decode(data_uri.partition(",")[2])[25] == 2
    assert data_uri not in output


@pytest.mark.asyncio
async def test_vision_probe_rejects_empty_provider_response(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(cli, "MacOSKeychain", FakeKeychain)
    monkeypatch.setattr(cli, "NvidiaNimClient", FakeToolClient)
    monkeypatch.setattr(
        FakeToolClient,
        "result",
        AgentResult(role=AgentRole.VISION, model_id="fake/vision", content=""),
    )

    status = await cli.probe_nvidia_vision()

    assert status == 1
    assert capsys.readouterr().out == "status=error reason=invalid_vision_response\n"
