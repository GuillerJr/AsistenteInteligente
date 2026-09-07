from __future__ import annotations

import asyncio
import json
import math
import os
import stat
import time
from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any, Literal, TypeVar
from uuid import UUID

import psutil
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from aegis_core.build_info import is_build_revision
from aegis_core.ipc.client import IpcClient

QUALIFICATION_SCHEMA_VERSION = "1.0"
READINESS_SCHEMA_VERSION = "2.0"
EVIDENCE_SCHEMA_VERSION = "2.0"
QUALIFICATION_CHECKS = 5
ModelT = TypeVar("ModelT", bound=BaseModel)


class MacOSQualificationError(RuntimeError):
    """Live qualification evidence could not be authenticated or validated."""


class QualificationStatus(StrEnum):
    PASSED = "passed"
    NEEDS_INTERACTION = "needs_interaction"
    NEEDS_ATTENTION = "needs_attention"
    BLOCKED = "blocked"


class ReadinessSnapshot(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["2.0"] = READINESS_SCHEMA_VERSION
    build_revision: str = Field(
        default="legacy",
        pattern=r"^(legacy|development|[0-9a-f]{40})$",
    )
    daemon: str = Field(pattern=r"^(online|offline|checking|unknown|security_failure)$")
    security: str = Field(
        pattern=r"^(intact|compromised|checking|unknown|unavailable)$"
    )
    provider: str = Field(pattern=r"^(configured|missing|unavailable|checking|unknown)$")
    local_brain_available: bool
    microphone: str = Field(pattern=r"^(authorized|denied|restricted|not_determined)$")
    speech_recognition: str = Field(pattern=r"^(authorized|denied|restricted|not_determined)$")
    screen_capture_authorized: bool
    computer_control: str = Field(
        pattern=(
            r"^(ready|screen_capture_missing|accessibility_missing|permissions_missing|"
            r"helper_unavailable)$"
        )
    )
    wake_word: str = Field(pattern=r"^(ready|missing|invalid|unavailable)$")
    wake_word_enabled: bool
    speaker_identity: str = Field(pattern=r"^(ready|missing|invalid|unavailable)$")


class OperationalEvidence(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["2.0"] = EVIDENCE_SCHEMA_VERSION
    build_revision: str = Field(
        default="legacy",
        pattern=r"^(legacy|development|[0-9a-f]{40})$",
    )
    voice_turns: int = Field(default=0, ge=0, le=1_000_000_000)
    owner_verified_voice_turns: int = Field(default=0, ge=0, le=1_000_000_000)
    last_voice_at: str | None = None
    last_voice_first_partial_ms: int | None = Field(default=None, ge=0, le=600_000)
    last_voice_total_ms: int | None = Field(default=None, ge=0, le=600_000)
    wake_word_detections: int = Field(default=0, ge=0, le=1_000_000_000)
    follow_up_voice_turns: int = Field(default=0, ge=0, le=1_000_000_000)
    successful_interruptions: int = Field(default=0, ge=0, le=1_000_000_000)
    completed_playbacks: int = Field(default=0, ge=0, le=1_000_000_000)
    screen_captures: int = Field(default=0, ge=0, le=1_000_000_000)
    last_screen_capture_at: str | None = None
    last_screen_capture_ms: int | None = Field(default=None, ge=0, le=60_000)
    last_screen_payload_bytes: int | None = Field(default=None, ge=1, le=32_768)
    computer_actions: int = Field(default=0, ge=0, le=1_000_000_000)
    verified_computer_actions: int = Field(default=0, ge=0, le=1_000_000_000)
    last_computer_action_at: str | None = None
    last_computer_action_ms: int | None = Field(default=None, ge=0, le=22_000)

    def model_post_init(self, __context: Any) -> None:
        if self.owner_verified_voice_turns > self.voice_turns:
            raise ValueError("owner verified turns exceed voice turns")
        if self.verified_computer_actions > self.computer_actions:
            raise ValueError("verified actions exceed computer actions")
        if (
            self.last_voice_first_partial_ms is not None
            and self.last_voice_total_ms is not None
            and self.last_voice_first_partial_ms > self.last_voice_total_ms
        ):
            raise ValueError("voice first partial exceeds total latency")


class RuntimePowerPayload(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    state: Literal["active", "suspended"]
    cause: str = Field(min_length=3, max_length=64, pattern=r"^[a-z][a-z0-9_]*$")
    thermal_state: Literal["nominal", "fair", "serious", "critical", "unknown"]
    low_power_mode: bool
    source_id: UUID
    sequence: int = Field(ge=0, le=9_007_199_254_740_991)
    changed: bool
    power_source: Literal["ac", "battery", "ups", "unknown"]


class JobLatencyMetrics(BaseModel):
    model_config = ConfigDict(extra="ignore", frozen=True)

    active_first_partial_p95_ms: int | None = Field(default=None, ge=0, le=600_000)
    conversation_p95: int | None = Field(default=None, ge=0, le=600_000)


class JobQualityObserved(BaseModel):
    model_config = ConfigDict(extra="ignore", frozen=True)

    action_success_rate: float | None = Field(default=None, ge=0, le=1)
    owner_recognition_rate: float | None = Field(default=None, ge=0, le=1)
    response_quality_pass_rate: float | None = Field(default=None, ge=0, le=1)
    action_jobs: int = Field(default=0, ge=0, le=1_000_000)
    voice_jobs: int = Field(default=0, ge=0, le=1_000_000)


class JobQuality(BaseModel):
    model_config = ConfigDict(extra="ignore", frozen=True)

    observed: JobQualityObserved


class JobMetricsPayload(BaseModel):
    model_config = ConfigDict(extra="ignore", frozen=True)

    build_revision: str = Field(pattern=r"^(development|[0-9a-f]{40})$")
    jobs: int = Field(ge=0, le=1_000_000)
    success_rate: float = Field(ge=0, le=1)
    latency_ms: JobLatencyMetrics
    quality: JobQuality


@dataclass(frozen=True, slots=True)
class SoakMetrics:
    cycles: int
    p95_ms: float
    maximum_ms: float
    rss_growth_bytes: int
    daemon_restarted: bool
    security_intact: bool

    @property
    def passed(self) -> bool:
        return (
            self.cycles > 0
            and self.p95_ms <= 250
            and self.rss_growth_bytes <= 8 * 1_024 * 1_024
            and not self.daemon_restarted
            and self.security_intact
        )


@dataclass(frozen=True, slots=True)
class QualificationCheck:
    check_id: str
    status: QualificationStatus
    reason: str
    metrics: dict[str, int | float | str | bool | None]

    def private_dict(self) -> dict[str, object]:
        return {
            "check_id": self.check_id,
            "status": self.status.value,
            "reason": self.reason,
            "metrics": self.metrics,
        }


@dataclass(frozen=True, slots=True)
class MacOSQualificationReport:
    build_revision: str
    checks: tuple[QualificationCheck, ...]
    duration_ms: int

    @property
    def passed(self) -> int:
        return sum(check.status is QualificationStatus.PASSED for check in self.checks)

    @property
    def score(self) -> int:
        return round((self.passed / QUALIFICATION_CHECKS) * 100)

    @property
    def gate_passed(self) -> bool:
        return len(self.checks) == QUALIFICATION_CHECKS and self.passed == QUALIFICATION_CHECKS

    @property
    def status(self) -> QualificationStatus:
        states = {check.status for check in self.checks}
        if self.gate_passed:
            return QualificationStatus.PASSED
        if QualificationStatus.BLOCKED in states:
            return QualificationStatus.BLOCKED
        if QualificationStatus.NEEDS_ATTENTION in states:
            return QualificationStatus.NEEDS_ATTENTION
        return QualificationStatus.NEEDS_INTERACTION

    def private_dict(self) -> dict[str, object]:
        return {
            "schema_version": QUALIFICATION_SCHEMA_VERSION,
            "profile": "live_macos_qualification",
            "build_revision": self.build_revision,
            "status": self.status.value,
            "score": self.score,
            "gate_passed": self.gate_passed,
            "passed": self.passed,
            "total": len(self.checks),
            "duration_ms": self.duration_ms,
            "checks": [check.private_dict() for check in self.checks],
            "privacy": {
                "contains_prompt_text": False,
                "contains_target_urls": False,
                "contains_transcripts": False,
                "contains_audio": False,
                "contains_images": False,
                "network_calls": 0,
            },
        }

    def private_json(self) -> str:
        return json.dumps(
            self.private_dict(),
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        )


class MacOSQualificationGate:
    def __init__(
        self,
        client: IpcClient,
        *,
        readiness_path: Path,
        evidence_path: Path,
        cycles: int = 100,
        process_probe: Callable[[], bool] | None = None,
    ) -> None:
        if not 20 <= cycles <= 1_000:
            raise ValueError("qualification soak cycles are out of range")
        self._client = client
        self._readiness_path = readiness_path
        self._evidence_path = evidence_path
        self._cycles = cycles
        self._process_probe = process_probe or _jarvis_process_is_running

    async def run(self) -> MacOSQualificationReport:
        started = time.monotonic_ns()
        readiness = _load_private_model(self._readiness_path, ReadinessSnapshot)
        evidence = (
            _load_private_model(self._evidence_path, OperationalEvidence)
            if self._evidence_path.exists()
            else OperationalEvidence()
        )
        try:
            (
                preflight_response,
                power_response,
                jobs_response,
                health_response,
            ) = await asyncio.gather(
                self._client.call("runtime.preflight"),
                self._client.call("runtime.power.status"),
                self._client.call("jobs.metrics"),
                self._client.call("health"),
            )
        except (OSError, TimeoutError, ValueError) as error:
            raise MacOSQualificationError("live IPC qualification failed") from error
        if not all(
            response.ok
            for response in (
                preflight_response,
                power_response,
                jobs_response,
                health_response,
            )
        ):
            raise MacOSQualificationError("live IPC qualification was rejected")
        try:
            power = RuntimePowerPayload.model_validate(power_response.payload)
            jobs = JobMetricsPayload.model_validate(jobs_response.payload)
        except ValidationError as error:
            raise MacOSQualificationError("live qualification payload is invalid") from error

        preflight = preflight_response.payload
        health = health_response.payload
        build_revision = health.get("build_revision")
        if not is_build_revision(build_revision) or jobs.build_revision != build_revision:
            raise MacOSQualificationError("live qualification build identity is invalid")
        current_evidence = (
            evidence
            if evidence.build_revision == build_revision
            else OperationalEvidence(build_revision=build_revision)
        )
        checks = [
            self._runtime_security_check(
                readiness,
                preflight,
                health,
                build_revision=build_revision,
            ),
            self._tcc_check(readiness, build_revision=build_revision),
            self._voice_check(
                readiness,
                current_evidence,
                jobs,
                build_revision=build_revision,
            ),
            self._visual_automation_check(
                readiness,
                current_evidence,
                jobs,
                build_revision=build_revision,
            ),
        ]
        soak: SoakMetrics | None = None
        if power.state == "active":
            soak = await self._run_soak()
        checks.append(self._latency_endurance_check(power, jobs, soak))
        duration_ms = max(0, (time.monotonic_ns() - started) // 1_000_000)
        return MacOSQualificationReport(
            build_revision=build_revision,
            checks=tuple(checks),
            duration_ms=min(duration_ms, 2_147_483_647),
        )

    def _runtime_security_check(
        self,
        readiness: ReadinessSnapshot,
        preflight: dict[str, Any],
        health: dict[str, Any],
        *,
        build_revision: str,
    ) -> QualificationCheck:
        menu_bar_running = self._process_probe()
        revision_matches = readiness.build_revision == build_revision
        valid = (
            revision_matches
            and readiness.daemon == "online"
            and readiness.security == "intact"
            and preflight.get("status") == "ok"
            and preflight.get("state") == "intact"
            and health.get("status") == "ok"
            and health.get("architecture") == "arm64"
            and menu_bar_running
        )
        if not revision_matches:
            reason = "native_build_mismatch"
        elif not menu_bar_running:
            reason = "menu_bar_not_running"
        elif readiness.security != "intact" or preflight.get("state") != "intact":
            reason = "security_not_intact"
        elif readiness.daemon != "online" or preflight.get("status") != "ok":
            reason = "daemon_not_ready"
        elif health.get("status") != "ok" or health.get("architecture") != "arm64":
            reason = "runtime_identity_invalid"
        else:
            reason = "verified"
        return QualificationCheck(
            check_id="runtime.security",
            status=QualificationStatus.PASSED if valid else QualificationStatus.BLOCKED,
            reason=reason,
            metrics={
                "build_revision_matches": revision_matches,
                "daemon_online": readiness.daemon == "online",
                "audit_intact": readiness.security == "intact",
                "architecture": str(health.get("architecture", "unknown"))[:16],
                "menu_bar_running": menu_bar_running,
            },
        )

    @staticmethod
    def _tcc_check(
        readiness: ReadinessSnapshot,
        *,
        build_revision: str,
    ) -> QualificationCheck:
        permissions = {
            "microphone": readiness.microphone == "authorized",
            "speech": readiness.speech_recognition == "authorized",
            "screen": readiness.screen_capture_authorized,
            "control": readiness.computer_control == "ready",
        }
        revision_matches = readiness.build_revision == build_revision
        passed = revision_matches and all(permissions.values())
        return QualificationCheck(
            check_id="macos.tcc",
            status=QualificationStatus.PASSED if passed else QualificationStatus.BLOCKED,
            reason=(
                "authorized"
                if passed
                else "native_build_mismatch"
                if not revision_matches
                else "permissions_incomplete"
            ),
            metrics={"build_revision_matches": revision_matches, **permissions},
        )

    @staticmethod
    def _voice_check(
        readiness: ReadinessSnapshot,
        evidence: OperationalEvidence,
        jobs: JobMetricsPayload,
        *,
        build_revision: str,
    ) -> QualificationCheck:
        revision_matches = readiness.build_revision == build_revision
        capability_ready = (
            readiness.microphone == "authorized"
            and readiness.speech_recognition == "authorized"
            and readiness.wake_word == "ready"
            and readiness.speaker_identity == "ready"
        )
        observed = jobs.quality.observed
        owner_rate = observed.owner_recognition_rate
        evidence_owner_rate = (
            evidence.owner_verified_voice_turns / evidence.voice_turns
            if evidence.voice_turns
            else None
        )
        metrics: dict[str, int | float | str | bool | None] = {
            "build_revision_matches": revision_matches,
            "wake_word_enabled": readiness.wake_word_enabled,
            "historical_voice_turns": observed.voice_jobs,
            "post_upgrade_voice_turns": evidence.voice_turns,
            "owner_recognition_rate": owner_rate,
            "post_upgrade_owner_rate": (
                round(evidence_owner_rate, 4) if evidence_owner_rate is not None else None
            ),
        }
        if not revision_matches:
            return QualificationCheck(
                "voice.owner_gate",
                QualificationStatus.BLOCKED,
                "native_build_mismatch",
                metrics,
            )
        if not capability_ready:
            return QualificationCheck(
                "voice.owner_gate",
                QualificationStatus.BLOCKED,
                "voice_capability_unavailable",
                metrics,
            )
        if not readiness.wake_word_enabled:
            return QualificationCheck(
                "voice.owner_gate",
                QualificationStatus.BLOCKED,
                "wake_word_disabled",
                metrics,
            )
        if observed.voice_jobs < 10 or evidence.voice_turns < 1:
            return QualificationCheck(
                "voice.owner_gate",
                QualificationStatus.NEEDS_INTERACTION,
                "real_owner_voice_sample_required",
                metrics,
            )
        passed = (
            owner_rate is not None
            and owner_rate >= 0.90
            and evidence_owner_rate is not None
            and evidence_owner_rate >= 0.90
        )
        return QualificationCheck(
            "voice.owner_gate",
            QualificationStatus.PASSED if passed else QualificationStatus.NEEDS_ATTENTION,
            "verified" if passed else "owner_recognition_below_target",
            metrics,
        )

    @staticmethod
    def _visual_automation_check(
        readiness: ReadinessSnapshot,
        evidence: OperationalEvidence,
        jobs: JobMetricsPayload,
        *,
        build_revision: str,
    ) -> QualificationCheck:
        revision_matches = readiness.build_revision == build_revision
        observed = jobs.quality.observed
        evidence_action_rate = (
            evidence.verified_computer_actions / evidence.computer_actions
            if evidence.computer_actions
            else None
        )
        metrics: dict[str, int | float | str | bool | None] = {
            "build_revision_matches": revision_matches,
            "screen_captures": evidence.screen_captures,
            "historical_actions": observed.action_jobs,
            "post_upgrade_actions": evidence.computer_actions,
            "historical_action_success_rate": observed.action_success_rate,
            "post_upgrade_action_success_rate": (
                round(evidence_action_rate, 4) if evidence_action_rate is not None else None
            ),
        }
        if not revision_matches:
            return QualificationCheck(
                "automation.visual",
                QualificationStatus.BLOCKED,
                "native_build_mismatch",
                metrics,
            )
        if not readiness.screen_capture_authorized or readiness.computer_control != "ready":
            return QualificationCheck(
                "automation.visual",
                QualificationStatus.BLOCKED,
                "screen_or_accessibility_unavailable",
                metrics,
            )
        if evidence.screen_captures < 1 or evidence.computer_actions < 1:
            return QualificationCheck(
                "automation.visual",
                QualificationStatus.NEEDS_INTERACTION,
                "live_capture_and_verified_action_required",
                metrics,
            )
        passed = (
            observed.action_jobs >= 10
            and observed.action_success_rate is not None
            and observed.action_success_rate >= 0.95
            and evidence_action_rate is not None
            and evidence_action_rate >= 0.95
        )
        return QualificationCheck(
            "automation.visual",
            QualificationStatus.PASSED if passed else QualificationStatus.NEEDS_ATTENTION,
            "verified" if passed else "action_reliability_below_target",
            metrics,
        )

    @staticmethod
    def _latency_endurance_check(
        power: RuntimePowerPayload,
        jobs: JobMetricsPayload,
        soak: SoakMetrics | None,
    ) -> QualificationCheck:
        latency = jobs.latency_ms
        metrics: dict[str, int | float | str | bool | None] = {
            "thermal_state": power.thermal_state,
            "low_power_mode": power.low_power_mode,
            "power_source": power.power_source,
            "first_partial_p95_ms": latency.active_first_partial_p95_ms,
            "conversation_p95_ms": latency.conversation_p95,
            "soak_cycles": soak.cycles if soak is not None else 0,
            "soak_p95_ms": round(soak.p95_ms, 2) if soak is not None else None,
            "soak_rss_growth_bytes": soak.rss_growth_bytes if soak is not None else None,
        }
        if power.state != "active":
            reason = (
                "low_power_mode_active"
                if power.low_power_mode
                else "thermal_pressure_active"
            )
            return QualificationCheck(
                "latency.endurance",
                QualificationStatus.BLOCKED,
                reason,
                metrics,
            )
        enough_data = jobs.jobs >= 20
        latency_passed = (
            latency.active_first_partial_p95_ms is not None
            and latency.active_first_partial_p95_ms <= 2_000
            and latency.conversation_p95 is not None
            and latency.conversation_p95 <= 8_000
        )
        if not enough_data:
            return QualificationCheck(
                "latency.endurance",
                QualificationStatus.NEEDS_INTERACTION,
                "current_build_samples_required",
                metrics,
            )
        if not latency_passed:
            return QualificationCheck(
                "latency.endurance",
                QualificationStatus.NEEDS_ATTENTION,
                "latency_target_missed",
                metrics,
            )
        passed = soak is not None and soak.passed
        return QualificationCheck(
            "latency.endurance",
            QualificationStatus.PASSED if passed else QualificationStatus.NEEDS_ATTENTION,
            "verified" if passed else "endurance_target_missed",
            metrics,
        )

    async def _run_soak(self) -> SoakMetrics:
        latencies: list[float] = []
        daemon_pid: int | None = None
        initial_peak_rss: int | None = None
        last_peak_rss = 0
        daemon_restarted = False
        security_intact = True
        for index in range(self._cycles):
            started = time.perf_counter_ns()
            health, metrics, security = await asyncio.gather(
                self._client.call("health"),
                self._client.call("runtime.metrics"),
                self._client.call("security.status"),
            )
            latencies.append((time.perf_counter_ns() - started) / 1_000_000)
            if not health.ok or not metrics.ok or not security.ok:
                raise MacOSQualificationError("soak IPC request failed")
            current_pid = health.payload.get("pid")
            peak_rss = metrics.payload.get("peak_rss_bytes")
            if (
                isinstance(current_pid, bool)
                or not isinstance(current_pid, int)
                or current_pid <= 1
                or isinstance(peak_rss, bool)
                or not isinstance(peak_rss, int)
                or peak_rss <= 0
            ):
                raise MacOSQualificationError("soak metrics are invalid")
            daemon_pid = current_pid if daemon_pid is None else daemon_pid
            daemon_restarted = daemon_restarted or current_pid != daemon_pid
            initial_peak_rss = peak_rss if initial_peak_rss is None else initial_peak_rss
            last_peak_rss = peak_rss
            security_intact = security_intact and security.payload.get("state") == "intact"
            if index % 20 == 19:
                await asyncio.sleep(0)
        ordered = sorted(latencies)
        rank = max(0, math.ceil(0.95 * len(ordered)) - 1)
        return SoakMetrics(
            cycles=len(ordered),
            p95_ms=ordered[rank],
            maximum_ms=ordered[-1],
            rss_growth_bytes=max(0, last_peak_rss - (initial_peak_rss or last_peak_rss)),
            daemon_restarted=daemon_restarted,
            security_intact=security_intact,
        )


def _load_private_model(path: Path, model: type[ModelT]) -> ModelT:
    try:
        metadata = path.lstat()
        if (
            not stat.S_ISREG(metadata.st_mode)
            or stat.S_ISLNK(metadata.st_mode)
            or metadata.st_uid != os.getuid()
            or stat.S_IMODE(metadata.st_mode) != 0o600
            or not 1 <= metadata.st_size <= 16_384
        ):
            raise MacOSQualificationError("qualification evidence is not private")
        payload = path.read_bytes()
        return model.model_validate_json(payload)
    except FileNotFoundError as error:
        raise MacOSQualificationError("qualification evidence is unavailable") from error
    except (OSError, ValidationError, ValueError) as error:
        raise MacOSQualificationError("qualification evidence is invalid") from error


def _jarvis_process_is_running() -> bool:
    current_uid = os.getuid()
    expected_path = Path.home() / "Applications/Jarvis.app/Contents/MacOS/Jarvis"
    try:
        expected_metadata = expected_path.stat(follow_symlinks=False)
        expected_resolved = expected_path.resolve(strict=True)
    except OSError:
        return False
    if (
        not stat.S_ISREG(expected_metadata.st_mode)
        or expected_path.is_symlink()
        or expected_metadata.st_uid != current_uid
        or stat.S_IMODE(expected_metadata.st_mode) & 0o022
    ):
        return False
    for process in psutil.process_iter(["exe", "uids"]):
        try:
            executable = process.info.get("exe")
            uids = process.info.get("uids")
            if uids is None or uids.real != current_uid or not isinstance(executable, str):
                continue
            if Path(executable).resolve(strict=True) == expected_resolved:
                return True
        except (OSError, psutil.AccessDenied, psutil.NoSuchProcess, psutil.ZombieProcess):
            continue
    return False
