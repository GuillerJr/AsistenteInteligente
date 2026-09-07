from __future__ import annotations

import json
from collections import Counter
from pathlib import Path
from typing import Any

import pytest

from aegis_core.ipc.protocol import IpcAuthenticator, IpcResponse
from aegis_core.macos_qualification import QualificationStatus
from aegis_core.voice_qualification import (
    SpeakerCalibrationReport,
    VoiceQualificationGate,
)

AUTHENTICATOR = IpcAuthenticator(bytes.fromhex("8a" * 32))
BUILD_REVISION = "a" * 40
SOURCE_ID = "01234567-89ab-cdef-0123-456789abcdef"


class FakeVoiceClient:
    def __init__(self, payloads: dict[str, dict[str, Any]]) -> None:
        self.payloads = payloads
        self.calls: Counter[str] = Counter()

    async def call(self, method: str) -> IpcResponse:
        self.calls[method] += 1
        request = AUTHENTICATOR.create_request(method)
        return AUTHENTICATOR.create_response(
            request,
            ok=True,
            payload=self.payloads[method],
        )


def write_private_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")
    path.chmod(0o600)


def readiness_payload() -> dict[str, Any]:
    return {
        "schema_version": "2.0",
        "build_revision": BUILD_REVISION,
        "daemon": "online",
        "security": "intact",
        "provider": "configured",
        "local_brain_available": True,
        "microphone": "authorized",
        "speech_recognition": "authorized",
        "screen_capture_authorized": True,
        "computer_control": "ready",
        "wake_word": "ready",
        "wake_word_enabled": True,
        "speaker_identity": "ready",
    }


def evidence_payload() -> dict[str, Any]:
    return {
        "schema_version": "2.0",
        "build_revision": BUILD_REVISION,
        "voice_turns": 3,
        "owner_verified_voice_turns": 3,
        "last_voice_at": "2026-09-07T12:00:00Z",
        "last_voice_first_partial_ms": 180,
        "last_voice_total_ms": 900,
        "wake_word_detections": 2,
        "follow_up_voice_turns": 1,
        "successful_interruptions": 1,
        "completed_playbacks": 2,
        "screen_captures": 0,
        "computer_actions": 0,
        "verified_computer_actions": 0,
    }


def calibration_payload() -> dict[str, Any]:
    return {
        "accepted": True,
        "threshold": 0.78,
        "maximumDistractorConfidence": 0.12,
        "minimumOwnerConfidence": 0.96,
        "distractorCount": 15,
        "ownerCount": 21,
    }


def live_payloads(*, low_power_mode: bool = False) -> dict[str, dict[str, Any]]:
    return {
        "health": {
            "status": "ok",
            "architecture": "arm64",
            "build_revision": BUILD_REVISION,
        },
        "runtime.power.status": {
            "state": "suspended" if low_power_mode else "active",
            "cause": "low_power_mode" if low_power_mode else "low_power_disabled",
            "thermal_state": "nominal",
            "low_power_mode": low_power_mode,
            "source_id": SOURCE_ID,
            "sequence": 7,
            "changed": False,
            "power_source": "battery",
        },
        "jobs.metrics": {
            "build_revision": BUILD_REVISION,
            "jobs": 3,
            "success_rate": 1.0,
            "latency_ms": {
                "active_first_partial_p95_ms": 180,
                "conversation_p95": 900,
            },
            "quality": {
                "observed": {
                    "action_success_rate": None,
                    "owner_recognition_rate": 1.0,
                    "response_quality_pass_rate": 1.0,
                    "action_jobs": 0,
                    "voice_jobs": 3,
                }
            },
        },
    }


@pytest.mark.asyncio
async def test_p10_voice_qualification_requires_the_complete_live_flow(
    tmp_path: Path,
) -> None:
    readiness = tmp_path / "runtime-readiness.json"
    evidence = tmp_path / "runtime-evidence.json"
    calibration = tmp_path / "speaker-calibration.json"
    write_private_json(readiness, readiness_payload())
    write_private_json(evidence, evidence_payload())
    write_private_json(calibration, calibration_payload())
    client = FakeVoiceClient(live_payloads())

    report = await VoiceQualificationGate(
        client,  # type: ignore[arg-type]
        readiness_path=readiness,
        evidence_path=evidence,
        calibration_path=calibration,
    ).run()

    assert report.gate_passed
    assert report.score == 100
    assert report.status is QualificationStatus.PASSED
    assert client.calls == Counter(
        {"health": 1, "runtime.power.status": 1, "jobs.metrics": 1}
    )
    assert report.private_dict()["privacy"] == {
        "contains_prompt_text": False,
        "contains_transcripts": False,
        "contains_audio": False,
        "contains_speaker_identifier": False,
        "network_calls": 0,
    }


@pytest.mark.asyncio
async def test_p10_reports_physical_voice_steps_without_fabricating_them(
    tmp_path: Path,
) -> None:
    readiness = tmp_path / "runtime-readiness.json"
    evidence = tmp_path / "runtime-evidence.json"
    calibration = tmp_path / "speaker-calibration.json"
    empty = evidence_payload()
    empty.update(
        {
            "voice_turns": 0,
            "owner_verified_voice_turns": 0,
            "last_voice_at": None,
            "last_voice_first_partial_ms": None,
            "last_voice_total_ms": None,
            "wake_word_detections": 0,
            "follow_up_voice_turns": 0,
            "successful_interruptions": 0,
            "completed_playbacks": 0,
        }
    )
    write_private_json(readiness, readiness_payload())
    write_private_json(evidence, empty)
    write_private_json(calibration, calibration_payload())
    payloads = live_payloads()
    payloads["jobs.metrics"]["quality"]["observed"].update(  # type: ignore[index, union-attr]
        {"owner_recognition_rate": None, "voice_jobs": 0}
    )

    report = await VoiceQualificationGate(
        FakeVoiceClient(payloads),  # type: ignore[arg-type]
        readiness_path=readiness,
        evidence_path=evidence,
        calibration_path=calibration,
    ).run()
    checks = {check.check_id: check for check in report.checks}

    assert report.status is QualificationStatus.NEEDS_INTERACTION
    assert report.score == 40
    assert checks["voice.wake_word_activation"].reason == "live_wake_word_required"
    assert checks["voice.conversation_continuity"].reason == (
        "live_conversation_and_follow_up_required"
    )
    assert checks["voice.organic_interruption"].reason == "live_interruption_required"


@pytest.mark.asyncio
async def test_p10_blocks_when_low_power_mode_pauses_the_wake_word(
    tmp_path: Path,
) -> None:
    readiness = tmp_path / "runtime-readiness.json"
    evidence = tmp_path / "runtime-evidence.json"
    calibration = tmp_path / "speaker-calibration.json"
    write_private_json(readiness, readiness_payload())
    write_private_json(evidence, evidence_payload())
    write_private_json(calibration, calibration_payload())

    report = await VoiceQualificationGate(
        FakeVoiceClient(live_payloads(low_power_mode=True)),  # type: ignore[arg-type]
        readiness_path=readiness,
        evidence_path=evidence,
        calibration_path=calibration,
    ).run()
    runtime = next(check for check in report.checks if check.check_id == "voice.runtime")

    assert report.status is QualificationStatus.BLOCKED
    assert runtime.reason == "low_power_mode_active"


def test_calibration_rejects_overlapping_speaker_classes() -> None:
    payload = calibration_payload()
    payload["maximumDistractorConfidence"] = 0.81

    with pytest.raises(ValueError, match="overlap"):
        SpeakerCalibrationReport.model_validate(payload)
