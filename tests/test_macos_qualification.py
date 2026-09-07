from __future__ import annotations

import json
from collections import Counter
from pathlib import Path
from typing import Any

import pytest

from aegis_core.ipc.protocol import IpcAuthenticator, IpcResponse
from aegis_core.macos_qualification import (
    MacOSQualificationError,
    MacOSQualificationGate,
    QualificationStatus,
)

AUTHENTICATOR = IpcAuthenticator(bytes.fromhex("8a" * 32))
SOURCE_ID = "01234567-89ab-cdef-0123-456789abcdef"
BUILD_REVISION = "a" * 40


class FakeQualificationClient:
    def __init__(self, payloads: dict[str, dict[str, Any]]) -> None:
        self.payloads = payloads
        self.calls: Counter[str] = Counter()

    async def call(self, method: str) -> IpcResponse:
        self.calls[method] += 1
        request = AUTHENTICATOR.create_request(method)
        payload = self.payloads[method]
        return AUTHENTICATOR.create_response(request, ok=True, payload=payload)


def write_private_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")
    path.chmod(0o600)


def readiness_payload(
    *,
    wake_word_enabled: bool = True,
    build_revision: str = BUILD_REVISION,
    security: str = "intact",
) -> dict[str, Any]:
    return {
        "schema_version": "2.0",
        "build_revision": build_revision,
        "daemon": "online",
        "security": security,
        "provider": "configured",
        "local_brain_available": True,
        "microphone": "authorized",
        "speech_recognition": "authorized",
        "screen_capture_authorized": True,
        "computer_control": "ready",
        "wake_word": "ready",
        "wake_word_enabled": wake_word_enabled,
        "speaker_identity": "ready",
    }


def evidence_payload() -> dict[str, Any]:
    return {
        "schema_version": "2.0",
        "build_revision": BUILD_REVISION,
        "voice_turns": 2,
        "owner_verified_voice_turns": 2,
        "last_voice_at": "2026-09-01T12:00:00Z",
        "last_voice_first_partial_ms": 140,
        "last_voice_total_ms": 500,
        "wake_word_detections": 2,
        "follow_up_voice_turns": 1,
        "successful_interruptions": 1,
        "completed_playbacks": 2,
        "screen_captures": 1,
        "last_screen_capture_at": "2026-09-01T12:01:00Z",
        "last_screen_capture_ms": 80,
        "last_screen_payload_bytes": 20_000,
        "computer_actions": 2,
        "verified_computer_actions": 2,
        "last_computer_action_at": "2026-09-01T12:02:00Z",
        "last_computer_action_ms": 70,
    }


def live_payloads(*, suspended: bool = False) -> dict[str, dict[str, Any]]:
    return {
        "runtime.preflight": {
            "status": "ok",
            "provider": "nvidia_nim",
            "credential": "configured",
            "local_model": "available",
            "state": "intact",
        },
        "runtime.power.status": {
            "state": "suspended" if suspended else "active",
            "cause": "low_power_mode" if suspended else "low_power_disabled",
            "thermal_state": "nominal",
            "low_power_mode": suspended,
            "source_id": SOURCE_ID,
            "sequence": 4,
            "changed": False,
            "power_source": "ac",
        },
        "jobs.metrics": {
            "build_revision": BUILD_REVISION,
            "jobs": 25,
            "success_rate": 0.98,
            "latency_ms": {
                "active_first_partial_p95_ms": 1_800,
                "conversation_p95": 7_000,
            },
            "quality": {
                "observed": {
                    "action_success_rate": 0.97,
                    "owner_recognition_rate": 1.0,
                    "response_quality_pass_rate": 1.0,
                    "action_jobs": 12,
                    "voice_jobs": 20,
                }
            },
        },
        "health": {
            "status": "ok",
            "protocol_version": "1.0",
            "architecture": "arm64",
            "build_revision": BUILD_REVISION,
            "pid": 321,
            "runtime_state": "suspended" if suspended else "active",
        },
        "runtime.metrics": {
            "uptime_seconds": 100,
            "cpu_seconds": 1.2,
            "peak_rss_bytes": 40_000_000,
            "runtime_state": "active",
        },
        "security.status": {"state": "intact"},
    }


@pytest.mark.asyncio
async def test_live_macos_qualification_passes_only_with_real_evidence(
    tmp_path: Path,
) -> None:
    readiness = tmp_path / "runtime-readiness.json"
    evidence = tmp_path / "runtime-evidence.json"
    write_private_json(readiness, readiness_payload())
    write_private_json(evidence, evidence_payload())
    client = FakeQualificationClient(live_payloads())

    report = await MacOSQualificationGate(
        client,  # type: ignore[arg-type]
        readiness_path=readiness,
        evidence_path=evidence,
        cycles=20,
        process_probe=lambda: True,
    ).run()
    payload = report.private_dict()

    assert report.gate_passed
    assert report.score == 100
    assert report.status is QualificationStatus.PASSED
    assert payload["build_revision"] == BUILD_REVISION
    assert client.calls["health"] == 21
    assert payload["privacy"] == {
        "contains_prompt_text": False,
        "contains_target_urls": False,
        "contains_transcripts": False,
        "contains_audio": False,
        "contains_images": False,
        "network_calls": 0,
    }


@pytest.mark.asyncio
async def test_qualification_reports_power_and_human_interaction_blockers(
    tmp_path: Path,
) -> None:
    readiness = tmp_path / "runtime-readiness.json"
    evidence = tmp_path / "runtime-evidence.json"
    write_private_json(readiness, readiness_payload(wake_word_enabled=False))
    client = FakeQualificationClient(live_payloads(suspended=True))

    report = await MacOSQualificationGate(
        client,  # type: ignore[arg-type]
        readiness_path=readiness,
        evidence_path=evidence,
        cycles=20,
        process_probe=lambda: True,
    ).run()
    checks = {check.check_id: check for check in report.checks}

    assert report.status is QualificationStatus.BLOCKED
    assert report.score == 40
    assert checks["voice.owner_gate"].reason == "wake_word_disabled"
    assert checks["automation.visual"].status is QualificationStatus.NEEDS_INTERACTION
    assert checks["latency.endurance"].reason == "low_power_mode_active"
    assert client.calls["runtime.metrics"] == 0


@pytest.mark.asyncio
async def test_qualification_fails_closed_for_public_or_corrupt_evidence(
    tmp_path: Path,
) -> None:
    readiness = tmp_path / "runtime-readiness.json"
    evidence = tmp_path / "runtime-evidence.json"
    write_private_json(readiness, readiness_payload())
    readiness.chmod(0o644)
    client = FakeQualificationClient(live_payloads())
    gate = MacOSQualificationGate(
        client,  # type: ignore[arg-type]
        readiness_path=readiness,
        evidence_path=evidence,
        cycles=20,
        process_probe=lambda: True,
    )

    with pytest.raises(MacOSQualificationError, match="not private"):
        await gate.run()

    readiness.chmod(0o600)
    write_private_json(evidence, {"schema_version": "2.0", "voice_turns": -1})
    with pytest.raises(MacOSQualificationError, match="invalid"):
        await gate.run()


@pytest.mark.asyncio
async def test_qualification_rejects_legacy_readiness_schema(tmp_path: Path) -> None:
    readiness = tmp_path / "runtime-readiness.json"
    evidence = tmp_path / "runtime-evidence.json"
    legacy = readiness_payload()
    legacy["schema_version"] = "1.0"
    write_private_json(readiness, legacy)

    gate = MacOSQualificationGate(
        FakeQualificationClient(live_payloads()),  # type: ignore[arg-type]
        readiness_path=readiness,
        evidence_path=evidence,
        cycles=20,
        process_probe=lambda: True,
    )

    with pytest.raises(MacOSQualificationError, match="invalid"):
        await gate.run()


@pytest.mark.asyncio
async def test_qualification_never_reuses_operational_evidence_from_an_old_build(
    tmp_path: Path,
) -> None:
    readiness = tmp_path / "runtime-readiness.json"
    evidence = tmp_path / "runtime-evidence.json"
    write_private_json(readiness, readiness_payload())
    stale = evidence_payload()
    stale["build_revision"] = "b" * 40
    write_private_json(evidence, stale)

    report = await MacOSQualificationGate(
        FakeQualificationClient(live_payloads()),  # type: ignore[arg-type]
        readiness_path=readiness,
        evidence_path=evidence,
        cycles=20,
        process_probe=lambda: True,
    ).run()
    checks = {check.check_id: check for check in report.checks}

    assert report.status is QualificationStatus.NEEDS_INTERACTION
    assert checks["voice.owner_gate"].reason == "real_owner_voice_sample_required"
    assert checks["voice.owner_gate"].metrics["post_upgrade_voice_turns"] == 0
    assert checks["automation.visual"].reason == (
        "live_capture_and_verified_action_required"
    )
    assert checks["automation.visual"].metrics["post_upgrade_actions"] == 0


@pytest.mark.asyncio
async def test_qualification_reports_stale_native_binary_without_hiding_tcc_state(
    tmp_path: Path,
) -> None:
    readiness = tmp_path / "runtime-readiness.json"
    evidence = tmp_path / "runtime-evidence.json"
    write_private_json(
        readiness,
        readiness_payload(build_revision="b" * 40),
    )
    write_private_json(evidence, evidence_payload())

    report = await MacOSQualificationGate(
        FakeQualificationClient(live_payloads()),  # type: ignore[arg-type]
        readiness_path=readiness,
        evidence_path=evidence,
        cycles=20,
        process_probe=lambda: True,
    ).run()
    checks = {check.check_id: check for check in report.checks}

    assert checks["runtime.security"].reason == "native_build_mismatch"
    assert checks["macos.tcc"].reason == "native_build_mismatch"
    assert checks["macos.tcc"].metrics["microphone"] is True
    assert checks["voice.owner_gate"].reason == "native_build_mismatch"
    assert checks["automation.visual"].reason == "native_build_mismatch"


@pytest.mark.asyncio
async def test_qualification_treats_transient_security_unavailability_as_closed_state(
    tmp_path: Path,
) -> None:
    readiness = tmp_path / "runtime-readiness.json"
    evidence = tmp_path / "runtime-evidence.json"
    write_private_json(
        readiness,
        readiness_payload(security="unavailable"),
    )
    write_private_json(evidence, evidence_payload())

    report = await MacOSQualificationGate(
        FakeQualificationClient(live_payloads()),  # type: ignore[arg-type]
        readiness_path=readiness,
        evidence_path=evidence,
        cycles=20,
        process_probe=lambda: True,
    ).run()
    checks = {check.check_id: check for check in report.checks}

    assert report.status is QualificationStatus.BLOCKED
    assert checks["runtime.security"].reason == "security_not_intact"
    assert checks["runtime.security"].metrics["audit_intact"] is False
