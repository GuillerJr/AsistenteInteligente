from __future__ import annotations

import asyncio
import base64
import binascii
import json
import time
from typing import Protocol

from pydantic import BaseModel, ConfigDict, Field

from aegis_core.ipc.protocol import IpcRequest
from aegis_core.ipc.server import IpcHandlerResult, IpcMethodHandler
from aegis_core.tools.background_automation import QuietActionVerification
from aegis_core.tools.chrome_cdp import BrowserApplication, LocalBrowserDiscovery
from aegis_core.tools.computer import (
    ComputerAction,
    ComputerObservation,
    ComputerPerceptionItem,
    ComputerRuntimeStatus,
    NativeComputerBridge,
)

SCHEMA_VERSION = "1.0"
FIXTURE_BUNDLE_IDENTIFIER = "ai.aegis.p8-fixture"
QUALIFICATION_TEXT = "P8 bounded text verified"
MAXIMUM_CAPTURE_BYTES = 32_768
EXPECTED_CHECKS = (
    "browser_driver_discovery",
    "runtime_permissions",
    "background_activation",
    "bounded_window_capture",
    "ax_text_replacement",
    "ax_press_verification",
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
        "browser_drivers_detected",
        "maximum_capture_bytes",
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
    "network_attempts": 0,
    "fixture_only_actions": True,
}


class ApplicationQualificationBridge(Protocol):
    def status(self) -> ComputerRuntimeStatus: ...

    def activate(self, bundle_identifier: str) -> None: ...

    def capture(self, expected_bundle_identifier: str) -> ComputerObservation: ...

    def act(
        self,
        action: ComputerAction,
        expected_bundle_identifier: str,
        expected_visual_context: str,
        expected_user_input_counter: int,
    ) -> QuietActionVerification | None: ...


class BrowserDiscovery(Protocol):
    async def discover(self) -> tuple[BrowserApplication, ...]: ...


class ApplicationQualificationAbort(RuntimeError):
    """A live prerequisite failed; dependent UI actions must not continue."""


class ApplicationQualificationCheck(BaseModel):
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


class ApplicationQualificationReport(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    checks: tuple[ApplicationQualificationCheck, ...]
    duration_ms: int = Field(ge=0, le=2_147_483_647)
    browser_drivers_detected: int = Field(ge=0, le=16)
    maximum_capture_bytes: int = Field(ge=0, le=MAXIMUM_CAPTURE_BYTES)

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
            "profile": "live_background_application_driver",
            "score": self.score,
            "gate_passed": self.gate_passed,
            "checks_passed": self.passed_checks,
            "checks_expected": len(EXPECTED_CHECKS),
            "duration_ms": self.duration_ms,
            "browser_drivers_detected": self.browser_drivers_detected,
            "maximum_capture_bytes": self.maximum_capture_bytes,
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
    def from_private_dict(cls, value: object) -> ApplicationQualificationReport:
        if not isinstance(value, dict) or set(value) != _PRIVATE_REPORT_KEYS:
            raise ValueError("application qualification report schema is invalid")
        if (
            value.get("schema_version") != SCHEMA_VERSION
            or value.get("profile") != "live_background_application_driver"
            or value.get("privacy") != _PRIVACY_CONTRACT
        ):
            raise ValueError("application qualification report contract is invalid")
        report = cls.model_validate(
            {
                "checks": value.get("checks"),
                "duration_ms": value.get("duration_ms"),
                "browser_drivers_detected": value.get("browser_drivers_detected"),
                "maximum_capture_bytes": value.get("maximum_capture_bytes"),
            }
        )
        if report.private_dict() != value:
            raise ValueError("application qualification report evidence is inconsistent")
        return report


class JarvisApplicationQualification:
    """Exercise the installed native driver against a disposable AppKit target."""

    def __init__(
        self,
        bridge: ApplicationQualificationBridge | None = None,
        *,
        browser_discovery: BrowserDiscovery | None = None,
    ) -> None:
        self._bridge = bridge or NativeComputerBridge()
        self._browser_discovery = browser_discovery or LocalBrowserDiscovery()

    async def run(self) -> ApplicationQualificationReport:
        started = time.monotonic_ns()
        checks: list[ApplicationQualificationCheck] = []
        browser_count = 0
        maximum_capture_bytes = 0
        try:
            check_started = time.monotonic_ns()
            browsers = await self._browser_discovery.discover()
            browser_count = len(browsers)
            self._append(
                checks,
                "browser_driver_discovery",
                bool(browsers) and all(browser.bundle_identifier for browser in browsers),
                check_started,
                "drivers_detected" if browsers else "no_supported_browser",
            )

            check_started = time.monotonic_ns()
            initial_status = await asyncio.to_thread(self._bridge.status)
            permissions_ready = (
                initial_status.screen_capture
                and initial_status.accessibility
                and initial_status.frontmost_bundle_identifier is not None
            )
            self._append(
                checks,
                "runtime_permissions",
                permissions_ready,
                check_started,
                "permissions_ready" if permissions_ready else "tcc_permission_missing",
            )
            if not permissions_ready:
                raise ApplicationQualificationAbort("tcc_permission_missing")

            check_started = time.monotonic_ns()
            await asyncio.to_thread(
                self._bridge.activate,
                FIXTURE_BUNDLE_IDENTIFIER,
            )
            activation_status = await asyncio.to_thread(self._bridge.status)
            background_activation = (
                activation_status.frontmost_bundle_identifier
                == initial_status.frontmost_bundle_identifier
            )
            self._append(
                checks,
                "background_activation",
                background_activation,
                check_started,
                "foreground_preserved" if background_activation else "focus_stolen",
            )
            if not background_activation:
                raise ApplicationQualificationAbort("focus_stolen")

            check_started = time.monotonic_ns()
            initial_observation = await asyncio.to_thread(
                self._bridge.capture,
                FIXTURE_BUNDLE_IDENTIFIER,
            )
            payload_size = self._capture_size(initial_observation)
            maximum_capture_bytes = max(maximum_capture_bytes, payload_size)
            field = self._find_control(
                initial_observation,
                marker="P8 Input",
                roles=frozenset({"SearchField", "TextArea", "TextField"}),
            )
            button = self._find_control(
                initial_observation,
                marker="P8 Advance",
                roles=frozenset({"Button"}),
                pressable=True,
            )
            bounded_capture = (
                payload_size <= MAXIMUM_CAPTURE_BYTES
                and not initial_observation.perception.secure_content
                and not initial_observation.perception.truncated
                and field is not None
                and button is not None
            )
            self._append(
                checks,
                "bounded_window_capture",
                bounded_capture,
                check_started,
                "capture_grounded" if bounded_capture else "capture_not_grounded",
            )
            if not bounded_capture or field is None or button is None:
                raise ApplicationQualificationAbort("capture_not_grounded")

            check_started = time.monotonic_ns()
            await asyncio.to_thread(
                self._bridge.act,
                ComputerAction(
                    action="replace_text",
                    x=field.x,
                    y=field.y,
                    target=field.text,
                    text=QUALIFICATION_TEXT,
                ),
                FIXTURE_BUNDLE_IDENTIFIER,
                initial_observation.visual_context,
                initial_observation.user_input_counter,
            )
            text_observation = await asyncio.to_thread(
                self._bridge.capture,
                FIXTURE_BUNDLE_IDENTIFIER,
            )
            payload_size = self._capture_size(text_observation)
            maximum_capture_bytes = max(maximum_capture_bytes, payload_size)
            replacement_verified = (
                text_observation.visual_signature != initial_observation.visual_signature
                and any(
                    item.source == "accessibility" and QUALIFICATION_TEXT in item.text
                    for item in text_observation.perception.items
                )
            )
            self._append(
                checks,
                "ax_text_replacement",
                replacement_verified,
                check_started,
                "text_verified" if replacement_verified else "text_not_verified",
            )
            if not replacement_verified:
                raise ApplicationQualificationAbort("text_not_verified")

            button = self._find_control(
                text_observation,
                marker="P8 Advance",
                roles=frozenset({"Button"}),
                pressable=True,
            )
            if button is None:
                raise ApplicationQualificationAbort("button_unavailable")
            check_started = time.monotonic_ns()
            verification = await asyncio.to_thread(
                self._bridge.act,
                ComputerAction(
                    action="click",
                    x=button.x,
                    y=button.y,
                    button="left",
                    click_count=1,
                    target=button.text,
                ),
                FIXTURE_BUNDLE_IDENTIFIER,
                text_observation.visual_context,
                text_observation.user_input_counter,
            )
            final_observation = await asyncio.to_thread(
                self._bridge.capture,
                FIXTURE_BUNDLE_IDENTIFIER,
            )
            payload_size = self._capture_size(final_observation)
            maximum_capture_bytes = max(maximum_capture_bytes, payload_size)
            press_verified = (
                verification is not None
                and verification.pathway == "accessibility"
                and verification.verified
                and verification.state_changed
                and final_observation.visual_signature != text_observation.visual_signature
                and any(
                    item.source == "accessibility" and "P8 Complete" in item.text
                    for item in final_observation.perception.items
                )
            )
            self._append(
                checks,
                "ax_press_verification",
                press_verified,
                check_started,
                "press_verified" if press_verified else "press_not_verified",
            )
            if not press_verified:
                raise ApplicationQualificationAbort("press_not_verified")

            check_started = time.monotonic_ns()
            final_status = await asyncio.to_thread(self._bridge.status)
            pointer_isolated = (
                final_status.frontmost_bundle_identifier
                == initial_status.frontmost_bundle_identifier
                and abs(final_status.cursor_position.x - initial_status.cursor_position.x) <= 0.5
                and abs(final_status.cursor_position.y - initial_status.cursor_position.y) <= 0.5
            )
            self._append(
                checks,
                "focus_and_pointer_isolation",
                pointer_isolated,
                check_started,
                "owner_input_preserved" if pointer_isolated else "owner_input_changed",
            )
        except (OSError, RuntimeError) as error:
            reason = self._bounded_reason(error)
            for check_id in EXPECTED_CHECKS[len(checks) :]:
                checks.append(
                    ApplicationQualificationCheck(
                        check_id=check_id,
                        passed=False,
                        latency_ms=0,
                        reason_code=f"not_run_after_{reason}"[:96],
                    )
                )

        duration_ms = max(0, (time.monotonic_ns() - started) // 1_000_000)
        return ApplicationQualificationReport(
            checks=tuple(checks),
            duration_ms=min(duration_ms, 2_147_483_647),
            browser_drivers_detected=min(browser_count, 16),
            maximum_capture_bytes=maximum_capture_bytes,
        )

    @staticmethod
    def _capture_size(observation: ComputerObservation) -> int:
        try:
            payload = base64.b64decode(observation.image.data_base64, validate=True)
        except (binascii.Error, ValueError) as error:
            raise ApplicationQualificationAbort("capture_payload_invalid") from error
        if not payload or len(payload) > MAXIMUM_CAPTURE_BYTES:
            raise ApplicationQualificationAbort("capture_payload_out_of_range")
        return len(payload)

    @staticmethod
    def _find_control(
        observation: ComputerObservation,
        *,
        marker: str,
        roles: frozenset[str],
        pressable: bool | None = None,
    ) -> ComputerPerceptionItem | None:
        matches = [
            item
            for item in observation.perception.items
            if item.source == "accessibility"
            and item.role in roles
            and marker in item.text
            and item.x is not None
            and item.y is not None
            and not item.sensitive
            and (pressable is None or item.pressable is pressable)
        ]
        return matches[0] if len(matches) == 1 else None

    @staticmethod
    def _append(
        checks: list[ApplicationQualificationCheck],
        check_id: str,
        passed: bool,
        started: int,
        reason_code: str,
    ) -> None:
        latency_ms = max(0, (time.monotonic_ns() - started) // 1_000_000)
        checks.append(
            ApplicationQualificationCheck(
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


class ApplicationQualificationIpcService:
    METHOD = "quality.application"
    MAXIMUM_HANDLER_SECONDS = 70.0

    def __init__(self, qualification: JarvisApplicationQualification) -> None:
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
    "FIXTURE_BUNDLE_IDENTIFIER",
    "ApplicationQualificationCheck",
    "ApplicationQualificationIpcService",
    "ApplicationQualificationReport",
    "JarvisApplicationQualification",
]
