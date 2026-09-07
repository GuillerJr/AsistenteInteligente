from __future__ import annotations

import pytest

from aegis_core.browser_qualification import (
    EXPECTED_CHECKS,
    BrowserQualificationCheck,
    BrowserQualificationIpcService,
    BrowserQualificationReport,
    JarvisBrowserQualification,
)
from aegis_core.ipc.protocol import IpcAuthenticator
from aegis_core.tools.chrome_cdp import BrowserApplication, ChromeDOMQualification
from aegis_core.tools.computer import ComputerPointerPosition, ComputerRuntimeStatus


class FakeBrowserDiscovery:
    def __init__(self, *, installed: bool = True, running: bool = True) -> None:
        self.installed = installed
        self.running = running

    async def discover(self) -> tuple[BrowserApplication, ...]:
        if not self.installed:
            return ()
        return (
            BrowserApplication(
                bundle_identifier="com.google.Chrome",
                name="Chrome",
                running=self.running,
            ),
        )


class FakeChromeDriver:
    def __init__(self, sample: ChromeDOMQualification | None = None) -> None:
        self.sample = sample or ChromeDOMQualification(
            browser_major_version=140,
            protocol_version="1.3",
            fixture_loaded=True,
            text_replaced=True,
            press_verified=True,
        )
        self.calls = 0

    async def qualify_dom_driver(self) -> ChromeDOMQualification:
        self.calls += 1
        return self.sample


class FakeRuntimeBridge:
    def __init__(self, *, move_pointer: bool = False) -> None:
        self.move_pointer = move_pointer
        self.calls = 0

    def status(self) -> ComputerRuntimeStatus:
        self.calls += 1
        pointer_x = 801.0 if self.move_pointer and self.calls > 1 else 800.0
        return ComputerRuntimeStatus(
            screen_capture=True,
            accessibility=True,
            frontmost_bundle_identifier="com.openai.codex",
            cursor_position=ComputerPointerPosition(x=pointer_x, y=450.0),
        )


@pytest.mark.asyncio
async def test_browser_qualification_proves_real_driver_contract_without_private_data() -> None:
    driver = FakeChromeDriver()
    report = await JarvisBrowserQualification(
        driver,
        bridge=FakeRuntimeBridge(),
        browser_discovery=FakeBrowserDiscovery(),
    ).run()

    assert report.gate_passed
    assert report.score == 100
    assert report.passed_checks == len(EXPECTED_CHECKS)
    assert report.browser_major_version == 140
    assert report.protocol_version == "1.3"
    assert driver.calls == 1
    assert report.private_dict()["privacy"] == {
        "contains_prompt_text": False,
        "contains_target_urls": False,
        "contains_transcripts": False,
        "contains_audio": False,
        "contains_images": False,
        "requires_owner_voice": False,
        "external_network_allowed": False,
        "user_browser_profile_accessed": False,
        "fixture_only_actions": True,
    }
    assert "P9 isolated DOM input" not in report.private_json()


@pytest.mark.asyncio
async def test_browser_qualification_stops_when_chrome_is_not_installed() -> None:
    driver = FakeChromeDriver()
    report = await JarvisBrowserQualification(
        driver,
        bridge=FakeRuntimeBridge(),
        browser_discovery=FakeBrowserDiscovery(installed=False),
    ).run()

    assert not report.gate_passed
    assert report.checks[0].reason_code == "chrome_not_installed"
    assert all(not check.passed for check in report.checks)
    assert driver.calls == 0


@pytest.mark.asyncio
async def test_browser_qualification_detects_owner_pointer_interference() -> None:
    report = await JarvisBrowserQualification(
        FakeChromeDriver(),
        bridge=FakeRuntimeBridge(move_pointer=True),
        browser_discovery=FakeBrowserDiscovery(),
    ).run()

    assert not report.gate_passed
    assert report.passed_checks == len(EXPECTED_CHECKS) - 1
    assert report.checks[-1].reason_code == "owner_input_changed"


@pytest.mark.asyncio
async def test_browser_qualification_ipc_service_returns_bound_report() -> None:
    qualification = JarvisBrowserQualification(
        FakeChromeDriver(),
        bridge=FakeRuntimeBridge(),
        browser_discovery=FakeBrowserDiscovery(),
    )
    service = BrowserQualificationIpcService(qualification)
    request = IpcAuthenticator(b"b" * 32).create_request(service.METHOD)

    response = await service.handle(request)

    assert response.ok
    report = BrowserQualificationReport.from_private_dict(response.payload)
    assert report.gate_passed


def test_browser_qualification_report_rejects_derived_field_tampering() -> None:
    report = BrowserQualificationReport(
        checks=tuple(
            BrowserQualificationCheck(
                check_id=check_id,
                passed=True,
                latency_ms=1,
                reason_code="verified",
            )
            for check_id in EXPECTED_CHECKS
        ),
        duration_ms=7,
        browser_major_version=140,
        protocol_version="1.3",
    )
    payload = report.private_dict()
    payload["checks_passed"] = 6

    with pytest.raises(ValueError, match="evidence is inconsistent"):
        BrowserQualificationReport.from_private_dict(payload)
