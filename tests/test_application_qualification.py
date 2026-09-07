from __future__ import annotations

import base64

import pytest

from aegis_core.application_qualification import (
    EXPECTED_CHECKS,
    FIXTURE_BUNDLE_IDENTIFIER,
    QUALIFICATION_TEXT,
    ApplicationQualificationCheck,
    ApplicationQualificationIpcService,
    ApplicationQualificationReport,
    JarvisApplicationQualification,
)
from aegis_core.contracts import ImageInput
from aegis_core.ipc.protocol import IpcAuthenticator
from aegis_core.tools.background_automation import QuietActionVerification
from aegis_core.tools.chrome_cdp import BrowserApplication
from aegis_core.tools.computer import (
    ComputerAction,
    ComputerObservation,
    ComputerPerception,
    ComputerPerceptionItem,
    ComputerPointerPosition,
    ComputerRuntimeStatus,
)


class FakeBrowserDiscovery:
    async def discover(self) -> tuple[BrowserApplication, ...]:
        return (
            BrowserApplication(
                bundle_identifier="com.apple.Safari",
                name="Safari",
                running=True,
            ),
        )


class FakeQualificationBridge:
    def __init__(
        self,
        *,
        permissions: bool = True,
        move_pointer: bool = False,
    ) -> None:
        self.permissions = permissions
        self.move_pointer = move_pointer
        self.state = "initial"
        self.status_calls = 0
        self.actions: list[ComputerAction] = []

    def status(self) -> ComputerRuntimeStatus:
        self.status_calls += 1
        pointer_x = 501.0 if self.move_pointer and self.status_calls >= 3 else 500.0
        return ComputerRuntimeStatus(
            screen_capture=self.permissions,
            accessibility=self.permissions,
            frontmost_bundle_identifier="com.openai.codex",
            cursor_position=ComputerPointerPosition(x=pointer_x, y=300.0),
        )

    def activate(self, bundle_identifier: str) -> None:
        assert bundle_identifier == FIXTURE_BUNDLE_IDENTIFIER

    def capture(self, expected_bundle_identifier: str) -> ComputerObservation:
        assert expected_bundle_identifier == FIXTURE_BUNDLE_IDENTIFIER
        field_text = "P8 Input"
        button_text = "P8 Advance"
        status_text = "P8 Ready"
        signature = "0" * 64
        if self.state in {"text", "complete"}:
            field_text = f"P8 Input {QUALIFICATION_TEXT}"
            signature = "1" * 64
        if self.state == "complete":
            button_text = "P8 Advance Complete"
            status_text = "P8 Complete"
            signature = "2" * 64
        return ComputerObservation(
            image=ImageInput(
                media_type="image/jpeg",
                data_base64=base64.b64encode(b"\xff\xd8\xff\xd9").decode("ascii"),
            ),
            perception=ComputerPerception(
                windows=("Jarvis P8 Qualification",),
                items=(
                    ComputerPerceptionItem(
                        source="accessibility",
                        role="SearchField",
                        text=field_text,
                        x=500,
                        y=400,
                    ),
                    ComputerPerceptionItem(
                        source="accessibility",
                        role="Button",
                        text=button_text,
                        x=500,
                        y=500,
                        pressable=True,
                    ),
                    ComputerPerceptionItem(
                        source="accessibility",
                        role="StaticText",
                        text=status_text,
                        x=600,
                        y=500,
                    ),
                ),
            ),
            visual_context="a" * 64,
            visual_signature=signature,
            user_input_counter=123_456,
        )

    def act(
        self,
        action: ComputerAction,
        expected_bundle_identifier: str,
        expected_visual_context: str,
        expected_user_input_counter: int,
    ) -> QuietActionVerification | None:
        assert expected_bundle_identifier == FIXTURE_BUNDLE_IDENTIFIER
        assert expected_visual_context == "a" * 64
        assert expected_user_input_counter == 123_456
        self.actions.append(action)
        if action.action == "replace_text":
            self.state = "text"
            return None
        assert action.action == "click"
        self.state = "complete"
        return QuietActionVerification(
            pathway="accessibility",
            before_sha256="3" * 64,
            after_sha256="4" * 64,
            state_changed=True,
            verified=True,
        )


@pytest.mark.asyncio
async def test_application_qualification_proves_background_native_driver() -> None:
    bridge = FakeQualificationBridge()
    report = await JarvisApplicationQualification(
        bridge,
        browser_discovery=FakeBrowserDiscovery(),
    ).run()

    assert report.gate_passed
    assert report.score == 100
    assert report.passed_checks == len(EXPECTED_CHECKS)
    assert [action.action for action in bridge.actions] == ["replace_text", "click"]
    payload = report.private_dict()
    assert payload["privacy"] == {
        "contains_prompt_text": False,
        "contains_target_urls": False,
        "contains_transcripts": False,
        "contains_audio": False,
        "contains_images": False,
        "requires_owner_voice": False,
        "network_attempts": 0,
        "fixture_only_actions": True,
    }
    serialized = report.private_json()
    assert QUALIFICATION_TEXT not in serialized
    assert base64.b64encode(b"\xff\xd8\xff\xd9").decode("ascii") not in serialized


@pytest.mark.asyncio
async def test_application_qualification_fails_closed_without_tcc() -> None:
    bridge = FakeQualificationBridge(permissions=False)
    report = await JarvisApplicationQualification(
        bridge,
        browser_discovery=FakeBrowserDiscovery(),
    ).run()

    assert not report.gate_passed
    assert report.score == 14
    assert report.checks[1].reason_code == "tcc_permission_missing"
    assert bridge.actions == []


@pytest.mark.asyncio
async def test_application_qualification_detects_hardware_pointer_change() -> None:
    bridge = FakeQualificationBridge(move_pointer=True)
    report = await JarvisApplicationQualification(
        bridge,
        browser_discovery=FakeBrowserDiscovery(),
    ).run()

    assert not report.gate_passed
    assert report.passed_checks == len(EXPECTED_CHECKS) - 1
    assert report.checks[-1].check_id == "focus_and_pointer_isolation"
    assert report.checks[-1].reason_code == "owner_input_changed"


@pytest.mark.asyncio
async def test_application_qualification_ipc_service_uses_the_relayed_bridge() -> None:
    qualification = JarvisApplicationQualification(
        FakeQualificationBridge(),
        browser_discovery=FakeBrowserDiscovery(),
    )
    service = ApplicationQualificationIpcService(qualification)
    request = IpcAuthenticator(b"q" * 32).create_request(service.METHOD)

    response = await service.handle(request)

    assert response.ok
    report = ApplicationQualificationReport.from_private_dict(response.payload)
    assert report.gate_passed
    assert report.score == 100


def test_application_qualification_report_rejects_derived_field_tampering() -> None:
    report = ApplicationQualificationReport(
        checks=tuple(
            ApplicationQualificationCheck(
                check_id=check_id,
                passed=True,
                latency_ms=1,
                reason_code="verified",
            )
            for check_id in EXPECTED_CHECKS
        ),
        duration_ms=7,
        browser_drivers_detected=1,
        maximum_capture_bytes=512,
    )
    payload = report.private_dict()
    payload["score"] = 99

    with pytest.raises(ValueError, match="evidence is inconsistent"):
        ApplicationQualificationReport.from_private_dict(payload)
