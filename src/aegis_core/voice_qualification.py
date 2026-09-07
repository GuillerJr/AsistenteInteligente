from __future__ import annotations

import asyncio
import json
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from aegis_core.build_info import is_build_revision
from aegis_core.ipc.client import IpcClient
from aegis_core.macos_qualification import (
    JobMetricsPayload,
    OperationalEvidence,
    QualificationCheck,
    QualificationStatus,
    ReadinessSnapshot,
    RuntimePowerPayload,
    _load_private_model,
)

VOICE_QUALIFICATION_SCHEMA_VERSION = "1.0"
VOICE_QUALIFICATION_CHECKS = 5


class VoiceQualificationError(RuntimeError):
    """P10 evidence could not be authenticated or validated."""


class SpeakerCalibrationReport(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, populate_by_name=True)

    accepted: bool
    threshold: float | None = Field(default=None, ge=0.78, le=0.95)
    maximum_distractor_confidence: float = Field(
        alias="maximumDistractorConfidence",
        ge=0,
        le=1,
    )
    minimum_owner_confidence: float = Field(
        alias="minimumOwnerConfidence",
        ge=0,
        le=1,
    )
    distractor_count: int = Field(alias="distractorCount", ge=15, le=24)
    owner_count: int = Field(alias="ownerCount", ge=5, le=24)

    @model_validator(mode="after")
    def validate_separation(self) -> SpeakerCalibrationReport:
        if self.accepted:
            if self.threshold is None:
                raise ValueError("accepted calibration has no threshold")
            if not (
                self.maximum_distractor_confidence < self.threshold
                <= self.minimum_owner_confidence
            ):
                raise ValueError("accepted calibration classes overlap")
        return self


@dataclass(frozen=True, slots=True)
class VoiceQualificationReport:
    build_revision: str
    checks: tuple[QualificationCheck, ...]
    duration_ms: int

    @property
    def passed(self) -> int:
        return sum(check.status is QualificationStatus.PASSED for check in self.checks)

    @property
    def score(self) -> int:
        return round((self.passed / VOICE_QUALIFICATION_CHECKS) * 100)

    @property
    def gate_passed(self) -> bool:
        return len(self.checks) == VOICE_QUALIFICATION_CHECKS and self.passed == len(
            self.checks
        )

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
            "schema_version": VOICE_QUALIFICATION_SCHEMA_VERSION,
            "profile": "live_owner_voice_qualification",
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
                "contains_transcripts": False,
                "contains_audio": False,
                "contains_speaker_identifier": False,
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


class VoiceQualificationGate:
    def __init__(
        self,
        client: IpcClient,
        *,
        readiness_path: Path,
        evidence_path: Path,
        calibration_path: Path,
    ) -> None:
        self._client = client
        self._readiness_path = readiness_path
        self._evidence_path = evidence_path
        self._calibration_path = calibration_path

    async def run(self) -> VoiceQualificationReport:
        started = time.monotonic_ns()
        readiness = _load_private_model(self._readiness_path, ReadinessSnapshot)
        evidence = _load_private_model(self._evidence_path, OperationalEvidence)
        calibration = _load_private_model(
            self._calibration_path,
            SpeakerCalibrationReport,
        )
        try:
            health_response, power_response, jobs_response = await asyncio.gather(
                self._client.call("health"),
                self._client.call("runtime.power.status"),
                self._client.call("jobs.metrics"),
            )
        except (OSError, TimeoutError, ValueError) as error:
            raise VoiceQualificationError("P10 live IPC validation failed") from error
        if not health_response.ok or not power_response.ok or not jobs_response.ok:
            raise VoiceQualificationError("P10 live IPC validation was rejected")
        try:
            power = RuntimePowerPayload.model_validate(power_response.payload)
            jobs = JobMetricsPayload.model_validate(jobs_response.payload)
        except ValidationError as error:
            raise VoiceQualificationError("P10 live payload is invalid") from error
        health = health_response.payload
        build_revision = health.get("build_revision")
        if not is_build_revision(build_revision) or jobs.build_revision != build_revision:
            raise VoiceQualificationError("P10 build identity is invalid")

        checks = (
            self._runtime_check(readiness, evidence, power, health, build_revision),
            self._calibration_check(calibration),
            self._wake_word_check(evidence),
            self._conversation_check(evidence, jobs),
            self._interruption_check(evidence),
        )
        duration_ms = max(0, (time.monotonic_ns() - started) // 1_000_000)
        return VoiceQualificationReport(
            build_revision=build_revision,
            checks=checks,
            duration_ms=min(duration_ms, 2_147_483_647),
        )

    @staticmethod
    def _runtime_check(
        readiness: ReadinessSnapshot,
        evidence: OperationalEvidence,
        power: RuntimePowerPayload,
        health: dict[str, Any],
        build_revision: str,
    ) -> QualificationCheck:
        build_matches = (
            readiness.build_revision == build_revision
            and evidence.build_revision == build_revision
        )
        capabilities_ready = (
            readiness.daemon == "online"
            and readiness.security == "intact"
            and readiness.microphone == "authorized"
            and readiness.speech_recognition == "authorized"
            and readiness.wake_word == "ready"
            and readiness.wake_word_enabled
            and readiness.speaker_identity == "ready"
            and health.get("status") == "ok"
            and health.get("architecture") == "arm64"
        )
        power_ready = (
            power.state == "active"
            and not power.low_power_mode
            and power.thermal_state in {"nominal", "fair"}
        )
        passed = build_matches and capabilities_ready and power_ready
        if not build_matches:
            reason = "native_build_mismatch"
        elif not capabilities_ready:
            reason = "voice_capability_unavailable"
        elif power.low_power_mode:
            reason = "low_power_mode_active"
        elif power.thermal_state not in {"nominal", "fair"}:
            reason = "thermal_pressure_active"
        elif power.state != "active":
            reason = "runtime_suspended"
        else:
            reason = "verified"
        return QualificationCheck(
            "voice.runtime",
            QualificationStatus.PASSED if passed else QualificationStatus.BLOCKED,
            reason,
            {
                "build_revision_matches": build_matches,
                "microphone": readiness.microphone == "authorized",
                "speech": readiness.speech_recognition == "authorized",
                "wake_word_ready": readiness.wake_word == "ready",
                "wake_word_enabled": readiness.wake_word_enabled,
                "speaker_identity_ready": readiness.speaker_identity == "ready",
                "thermal_state": power.thermal_state,
                "low_power_mode": power.low_power_mode,
            },
        )

    @staticmethod
    def _calibration_check(calibration: SpeakerCalibrationReport) -> QualificationCheck:
        return QualificationCheck(
            "voice.adversarial_calibration",
            (
                QualificationStatus.PASSED
                if calibration.accepted
                else QualificationStatus.BLOCKED
            ),
            "verified" if calibration.accepted else "speaker_classes_overlap",
            {
                "threshold": calibration.threshold,
                "maximum_distractor_confidence": calibration.maximum_distractor_confidence,
                "minimum_owner_confidence": calibration.minimum_owner_confidence,
                "distractor_samples": calibration.distractor_count,
                "owner_samples": calibration.owner_count,
            },
        )

    @staticmethod
    def _wake_word_check(evidence: OperationalEvidence) -> QualificationCheck:
        passed = evidence.wake_word_detections >= 1
        return QualificationCheck(
            "voice.wake_word_activation",
            QualificationStatus.PASSED if passed else QualificationStatus.NEEDS_INTERACTION,
            "verified" if passed else "live_wake_word_required",
            {"wake_word_detections": evidence.wake_word_detections},
        )

    @staticmethod
    def _conversation_check(
        evidence: OperationalEvidence,
        jobs: JobMetricsPayload,
    ) -> QualificationCheck:
        observed = jobs.quality.observed
        evidence_owner_rate = (
            evidence.owner_verified_voice_turns / evidence.voice_turns
            if evidence.voice_turns
            else None
        )
        enough_live_flow = (
            evidence.voice_turns >= 2
            and evidence.completed_playbacks >= 2
            and evidence.follow_up_voice_turns >= 1
            and observed.voice_jobs >= 2
        )
        rates_pass = (
            evidence_owner_rate is not None
            and evidence_owner_rate >= 0.90
            and observed.owner_recognition_rate is not None
            and observed.owner_recognition_rate >= 0.90
        )
        latency_present = (
            evidence.last_voice_first_partial_ms is not None
            and evidence.last_voice_total_ms is not None
        )
        latency_pass = (
            latency_present
            and evidence.last_voice_first_partial_ms <= 2_000
            and evidence.last_voice_total_ms <= 8_000
        )
        metrics: dict[str, int | float | str | bool | None] = {
            "voice_turns": evidence.voice_turns,
            "owner_verified_voice_turns": evidence.owner_verified_voice_turns,
            "owner_rate": (
                round(evidence_owner_rate, 4) if evidence_owner_rate is not None else None
            ),
            "daemon_voice_jobs": observed.voice_jobs,
            "daemon_owner_rate": observed.owner_recognition_rate,
            "follow_up_voice_turns": evidence.follow_up_voice_turns,
            "completed_playbacks": evidence.completed_playbacks,
            "last_first_partial_ms": evidence.last_voice_first_partial_ms,
            "last_total_ms": evidence.last_voice_total_ms,
        }
        if not enough_live_flow or not latency_present:
            return QualificationCheck(
                "voice.conversation_continuity",
                QualificationStatus.NEEDS_INTERACTION,
                "live_conversation_and_follow_up_required",
                metrics,
            )
        passed = rates_pass and latency_pass
        return QualificationCheck(
            "voice.conversation_continuity",
            QualificationStatus.PASSED if passed else QualificationStatus.NEEDS_ATTENTION,
            "verified" if passed else "voice_quality_target_missed",
            metrics,
        )

    @staticmethod
    def _interruption_check(evidence: OperationalEvidence) -> QualificationCheck:
        passed = evidence.successful_interruptions >= 1
        return QualificationCheck(
            "voice.organic_interruption",
            QualificationStatus.PASSED if passed else QualificationStatus.NEEDS_INTERACTION,
            "verified" if passed else "live_interruption_required",
            {"successful_interruptions": evidence.successful_interruptions},
        )
