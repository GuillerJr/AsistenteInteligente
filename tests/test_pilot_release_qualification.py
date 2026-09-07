from __future__ import annotations

import hashlib
import json
import plistlib
from pathlib import Path

import pytest

from aegis_core.macos_qualification import QualificationStatus
from aegis_core.pilot_release_qualification import (
    MACOS_CHECK_IDS,
    VOICE_CHECK_IDS,
    PilotReleaseQualificationError,
    PilotReleaseQualificationGate,
)

REVISION = "a" * 40
SOURCE_TREE_SHA256 = "b" * 64


def _write_private(path: Path, payload: bytes) -> None:
    path.write_bytes(payload)
    path.chmod(0o600)


def _qualification(
    profile: str,
    check_ids: frozenset[str],
    *,
    status: str = "passed",
) -> bytes:
    ordered = sorted(check_ids)
    checks = [
        {
            "check_id": check_id,
            "status": "passed",
            "reason": "verified",
            "metrics": {},
        }
        for check_id in ordered
    ]
    if status != "passed":
        checks[-1]["status"] = status
        checks[-1]["reason"] = "live_evidence_required"
    passed = sum(check["status"] == "passed" for check in checks)
    privacy = (
        {
            "contains_prompt_text": False,
            "contains_transcripts": False,
            "contains_audio": False,
            "contains_speaker_identifier": False,
            "network_calls": 0,
        }
        if profile == "live_owner_voice_qualification"
        else {
            "contains_prompt_text": False,
            "contains_target_urls": False,
            "contains_transcripts": False,
            "contains_audio": False,
            "contains_images": False,
            "network_calls": 0,
        }
    )
    return json.dumps(
        {
            "schema_version": "1.0",
            "profile": profile,
            "build_revision": REVISION,
            "status": status,
            "score": round((passed / len(checks)) * 100),
            "gate_passed": status == "passed",
            "passed": passed,
            "total": len(checks),
            "duration_ms": 12,
            "checks": checks,
            "privacy": privacy,
        },
        separators=(",", ":"),
        sort_keys=True,
    ).encode()


def _gate(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    voice_status: str = "passed",
) -> PilotReleaseQualificationGate:
    voice = tmp_path / "voice.json"
    macos = tmp_path / "macos.json"
    archive = tmp_path / "Jarvis.zip"
    sbom = tmp_path / "Jarvis.spdx.json"
    manifest = tmp_path / "Jarvis.release.json"
    info = tmp_path / "Info.plist"
    archive_payload = b"authenticated pilot archive"
    sbom_payload = b'{"spdxVersion":"SPDX-2.3"}\n'

    _write_private(
        voice,
        _qualification(
            "live_owner_voice_qualification",
            VOICE_CHECK_IDS,
            status=voice_status,
        ),
    )
    _write_private(
        macos,
        _qualification("live_macos_qualification", MACOS_CHECK_IDS),
    )
    _write_private(archive, archive_payload)
    _write_private(sbom, sbom_payload)
    manifest_payload = {
        "schema_version": "1.0",
        "product": "Jarvis",
        "bundle_identifier": "ai.aegis.menubar",
        "version": "0.1.0",
        "build": "1",
        "build_revision": REVISION,
        "source_tree_sha256": SOURCE_TREE_SHA256,
        "created_at": "2026-09-07T00:00:00Z",
        "profile": "local-development",
        "architecture": "arm64",
        "minimum_macos": "14.0",
        "artifact": {
            "name": "Jarvis.zip",
            "format": "zip",
            "sha256": hashlib.sha256(archive_payload).hexdigest(),
            "size": len(archive_payload),
            "uncompressed_size": len(archive_payload),
        },
        "bundle_files": [
            {
                "path": "Jarvis.app/Contents/Info.plist",
                "sha256": "c" * 64,
                "size": 128,
            },
            {
                "path": "Jarvis.app/Contents/MacOS/Jarvis",
                "sha256": "d" * 64,
                "size": 1024,
            },
        ],
        "sbom": {
            "name": "Jarvis.spdx.json",
            "format": "SPDX-2.3",
            "sha256": hashlib.sha256(sbom_payload).hexdigest(),
        },
        "privacy": {
            "contains_prompts": False,
            "contains_transcripts": False,
            "contains_images": False,
            "contains_credentials": False,
            "contains_absolute_paths": False,
        },
    }
    _write_private(
        manifest,
        (json.dumps(manifest_payload, separators=(",", ":"), sort_keys=True) + "\n").encode(),
    )
    info.write_bytes(plistlib.dumps({"AegisBuildRevision": REVISION}))

    monkeypatch.setattr(
        "aegis_core.pilot_release_qualification._source_revision",
        lambda _root: REVISION,
    )
    monkeypatch.setattr(
        "aegis_core.pilot_release_qualification._require_clean_tracked_tree",
        lambda _root: None,
    )
    monkeypatch.setattr(
        "aegis_core.pilot_release_qualification._source_tree_sha256",
        lambda _root: SOURCE_TREE_SHA256,
    )
    return PilotReleaseQualificationGate(
        project_root=tmp_path,
        installed_info_path=info,
        voice_report_path=voice,
        macos_report_path=macos,
        release_manifest_path=manifest,
        release_sbom_path=sbom,
        release_archive_path=archive,
    )


def test_pilot_gate_binds_all_evidence_to_one_build(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    report = _gate(tmp_path, monkeypatch).run()

    assert report.gate_passed is True
    assert report.score == 100
    assert report.status is QualificationStatus.PASSED
    assert report.build_revision == REVISION
    assert len(report.evidence_sha256) == 4
    payload = report.private_dict()
    assert payload["privacy"]["network_calls"] == 0
    assert all(
        value is False
        for key, value in payload["privacy"].items()
        if key.startswith("contains_")
    )


def test_pilot_gate_preserves_physical_voice_as_a_hard_prerequisite(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    report = _gate(
        tmp_path,
        monkeypatch,
        voice_status="needs_interaction",
    ).run()

    check = next(item for item in report.checks if item.check_id == "quality.owner_voice")
    assert check.status is QualificationStatus.NEEDS_INTERACTION
    assert check.reason == "prerequisite_not_passed"
    assert report.gate_passed is False
    assert report.score == 80


def test_pilot_gate_detects_archive_tampering(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    gate = _gate(tmp_path, monkeypatch)
    archive = tmp_path / "Jarvis.zip"
    _write_private(archive, archive.read_bytes() + b"tampered")

    report = gate.run()

    check = next(item for item in report.checks if item.check_id == "release.candidate")
    assert check.status is QualificationStatus.BLOCKED
    assert check.reason == "release_evidence_mismatch"
    assert report.gate_passed is False


def test_pilot_gate_rejects_non_private_evidence(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    gate = _gate(tmp_path, monkeypatch)
    (tmp_path / "voice.json").chmod(0o644)

    with pytest.raises(PilotReleaseQualificationError, match="unsafe"):
        gate.run()
