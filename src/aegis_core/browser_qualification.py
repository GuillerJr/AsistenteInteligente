from __future__ import annotations

import asyncio
import json
import time
from typing import Protocol

from pydantic import BaseModel, ConfigDict, Field

from aegis_core.ipc.protocol import IpcRequest
from aegis_core.ipc.server import IpcHandlerResult, IpcMethodHandler
from aegis_core.tools.chrome_cdp import (
    BrowserApplication,
    ChromeDOMQualification,
    LocalBrowserDiscovery,
)
from aegis_core.tools.computer import ComputerRuntimeStatus, NativeComputerBridge

SCHEMA_VERSION = "1.0"
CHROME_BUNDLE_IDENTIFIER = "com.google.Chrome"
EXPECTED_CHECKS = (
    "chrome_driver_discovery",
    "chrome_runtime",
    "loopback_cdp_contract",
    "memory_only_dom",
    "dom_text_replacement",
    "dom_press_verification",
    "focus_and_pointer_isolation",
)
_PRIVATE_REPORT_KEYS = frozenset(
    {
        "schema_version",
        "profile",
        "score",
        "gate_passed",
        "checks_passed",
        "checks_expected",
        "duration_ms",
        "browser_major_version",
        "protocol_version",
        "checks",
        "privacy",
    }
)
_PRIVACY_CONTRACT = {
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


class BrowserDiscovery(Protocol):
    async def discover(self) -> tuple[BrowserApplication, ...]: ...


class ChromeQualificationDriver(Protocol):
    async def qualify_dom_driver(self) -> ChromeDOMQualification: ...


class RuntimeStateBridge(Protocol):
    def status(self) -> ComputerRuntimeStatus: ...


class BrowserQualificationAbort(RuntimeError):
    """A live prerequisite failed; dependent browser checks must not continue."""


class BrowserQualificationCheck(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    check_id: str = Field(min_length=3, max_length=64, pattern=r"^[a-z][a-z0-9_]+$")
    passed: bool
    latency_ms: int = Field(ge=0, le=2_147_483_647)
    reason_code: str = Field(min_length=3, max_length=96, pattern=r"^[a-z][a-z0-9_]+$")

    def private_dict(self) -> dict[str, str | bool | int]:
        return {
            "check_id": self.check_id,
            "passed": self.passed,
            "latency_ms": self.latency_ms,
            "reason_code": self.reason_code,
        }


class BrowserQualificationReport(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    checks: tuple[BrowserQualificationCheck, ...]
    duration_ms: int = Field(ge=0, le=2_147_483_647)
    browser_major_version: int = Field(ge=0, le=10_000)
    protocol_version: str = Field(max_length=16, pattern=r"^(|[0-9]+\.[0-9]+)$")

    @property
    def passed_checks(self) -> int:
        return sum(check.passed for check in self.checks)

    @property
    def gate_passed(self) -> bool:
        return (
            len(self.checks) == len(EXPECTED_CHECKS)
            and tuple(check.check_id for check in self.checks) == EXPECTED_CHECKS
            and self.passed_checks == len(EXPECTED_CHECKS)
        )

    @property
    def score(self) -> int:
        return round(self.passed_checks / len(EXPECTED_CHECKS) * 100)

    def private_dict(self) -> dict[str, object]:
        return {
            "schema_version": SCHEMA_VERSION,
            "profile": "live_isolated_chrome_driver",
            "score": self.score,
            "gate_passed": self.gate_passed,
            "checks_passed": self.passed_checks,
            "checks_expected": len(EXPECTED_CHECKS),
            "duration_ms": self.duration_ms,
            "browser_major_version": self.browser_major_version,
            "protocol_version": self.protocol_version,
            "checks": [check.private_dict() for check in self.checks],
            "privacy": dict(_PRIVACY_CONTRACT),
        }

    def private_json(self) -> str:
        return json.dumps(
            self.private_dict(),
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        )

    @classmethod
    def from_private_dict(cls, value: object) -> BrowserQualificationReport:
        if not isinstance(value, dict) or set(value) != _PRIVATE_REPORT_KEYS:
            raise ValueError("browser qualification report schema is invalid")
        if (
            value.get("schema_version") != SCHEMA_VERSION
            or value.get("profile") != "live_isolated_chrome_driver"
            or value.get("privacy") != _PRIVACY_CONTRACT
        ):
            raise ValueError("browser qualification report contract is invalid")
        report = cls.model_validate(
            {
                "checks": value.get("checks"),
                "duration_ms": value.get("duration_ms"),
                "browser_major_version": value.get("browser_major_version"),
                "protocol_version": value.get("protocol_version"),
            }
        )
        if report.private_dict() != value:
            raise ValueError("browser qualification report evidence is inconsistent")
        return report


class JarvisBrowserQualification:
    """Qualify Chrome CDP against an isolated in-memory DOM and native owner state."""

    def __init__(
        self,
        driver: ChromeQualificationDriver,
        *,
        bridge: RuntimeStateBridge | None = None,
        browser_discovery: BrowserDiscovery | None = None,
    ) -> None:
        self._driver = driver
        self._bridge = bridge or NativeComputerBridge()
        self._browser_discovery = browser_discovery or LocalBrowserDiscovery()

    async def run(self) -> BrowserQualificationReport:
        started = time.monotonic_ns()
        checks: list[BrowserQualificationCheck] = []
        browser_major_version = 0
        protocol_version = ""
        try:
            check_started = time.monotonic_ns()
            browsers = await self._browser_discovery.discover()
            chrome = next(
                (
                    browser
                    for browser in browsers
                    if browser.bundle_identifier.casefold()
                    == CHROME_BUNDLE_IDENTIFIER.casefold()
                ),
                None,
            )
            self._append(
                checks,
                "chrome_driver_discovery",
                chrome is not None,
                check_started,
                "chrome_detected" if chrome is not None else "chrome_not_installed",
            )
            if chrome is None:
                raise BrowserQualificationAbort("chrome_not_installed")

            check_started = time.monotonic_ns()
            self._append(
                checks,
                "chrome_runtime",
                chrome.running,
                check_started,
                "isolated_runtime_detected" if chrome.running else "chrome_not_running",
            )
            if not chrome.running:
                raise BrowserQualificationAbort("chrome_not_running")

            initial_status = await asyncio.to_thread(self._bridge.status)
            if initial_status.frontmost_bundle_identifier is None:
                raise BrowserQualificationAbort("frontmost_application_unavailable")

            check_started = time.monotonic_ns()
            sample = await self._driver.qualify_dom_driver()
            browser_major_version = sample.browser_major_version
            protocol_version = sample.protocol_version
            transport_valid = browser_major_version > 0 and bool(protocol_version)
            self._append(
                checks,
                "loopback_cdp_contract",
                transport_valid,
                check_started,
                "cdp_product_verified"
                if transport_valid
                else "cdp_product_invalid",
            )
            if not transport_valid:
                raise BrowserQualificationAbort("cdp_product_invalid")

            self._append_boolean_check(
                checks,
                "memory_only_dom",
                sample.fixture_loaded,
                "fixture_loaded",
                "fixture_not_loaded",
            )
            if not sample.fixture_loaded:
                raise BrowserQualificationAbort("fixture_not_loaded")

            self._append_boolean_check(
                checks,
                "dom_text_replacement",
                sample.text_replaced,
                "text_verified",
                "text_not_verified",
            )
            if not sample.text_replaced:
                raise BrowserQualificationAbort("text_not_verified")

            self._append_boolean_check(
                checks,
                "dom_press_verification",
                sample.press_verified,
                "press_verified",
                "press_not_verified",
            )
            if not sample.press_verified:
                raise BrowserQualificationAbort("press_not_verified")

            check_started = time.monotonic_ns()
            final_status = await asyncio.to_thread(self._bridge.status)
            owner_state_preserved = (
                final_status.frontmost_bundle_identifier
                == initial_status.frontmost_bundle_identifier
                and abs(final_status.cursor_position.x - initial_status.cursor_position.x) <= 0.5
                and abs(final_status.cursor_position.y - initial_status.cursor_position.y) <= 0.5
            )
            self._append(
                checks,
                "focus_and_pointer_isolation",
                owner_state_preserved,
                check_started,
                "owner_input_preserved" if owner_state_preserved else "owner_input_changed",
            )
        except (OSError, RuntimeError) as error:
            reason = self._bounded_reason(error)
            for check_id in EXPECTED_CHECKS[len(checks) :]:
                checks.append(
                    BrowserQualificationCheck(
                        check_id=check_id,
                        passed=False,
                        latency_ms=0,
                        reason_code=f"not_run_after_{reason}"[:96],
                    )
                )

        duration_ms = max(0, (time.monotonic_ns() - started) // 1_000_000)
        return BrowserQualificationReport(
            checks=tuple(checks),
            duration_ms=min(duration_ms, 2_147_483_647),
            browser_major_version=browser_major_version,
            protocol_version=protocol_version,
        )

    @classmethod
    def _append_boolean_check(
        cls,
        checks: list[BrowserQualificationCheck],
        check_id: str,
        passed: bool,
        success_reason: str,
        failure_reason: str,
    ) -> None:
        started = time.monotonic_ns()
        cls._append(
            checks,
            check_id,
            passed,
            started,
            success_reason if passed else failure_reason,
        )

    @staticmethod
    def _append(
        checks: list[BrowserQualificationCheck],
        check_id: str,
        passed: bool,
        started: int,
        reason_code: str,
    ) -> None:
        latency_ms = max(0, (time.monotonic_ns() - started) // 1_000_000)
        checks.append(
            BrowserQualificationCheck(
                check_id=check_id,
                passed=passed,
                latency_ms=min(latency_ms, 2_147_483_647),
                reason_code=reason_code,
            )
        )

    @staticmethod
    def _bounded_reason(error: BaseException) -> str:
        value = str(error).strip().casefold().replace(" ", "_")
        if not value or any(not (character.isalnum() or character == "_") for character in value):
            return "qualification_unavailable"
        return value[:64]


class BrowserQualificationIpcService:
    METHOD = "quality.browser"
    MAXIMUM_HANDLER_SECONDS = 45.0

    def __init__(self, qualification: JarvisBrowserQualification) -> None:
        self._qualification = qualification
        self._lock = asyncio.Lock()

    def handlers(self) -> dict[str, IpcMethodHandler]:
        return {self.METHOD: self.handle}

    async def handle(self, request: IpcRequest) -> IpcHandlerResult:
        if request.method != self.METHOD:
            return IpcHandlerResult(ok=False, error_code="method_not_found")
        if request.payload:
            return IpcHandlerResult(ok=False, error_code="invalid_payload")
        if self._lock.locked():
            return IpcHandlerResult(ok=False, error_code="qualification_busy")
        async with self._lock:
            report = await self._qualification.run()
        return IpcHandlerResult(ok=True, payload=report.private_dict())


__all__ = [
    "BrowserQualificationCheck",
    "BrowserQualificationIpcService",
    "BrowserQualificationReport",
    "JarvisBrowserQualification",
]
