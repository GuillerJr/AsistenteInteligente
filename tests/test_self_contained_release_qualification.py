from __future__ import annotations

import hashlib
import json
import zipfile
from dataclasses import replace
from pathlib import Path

import pytest

from aegis_core.macos_qualification import QualificationStatus
from aegis_core.self_contained_release_qualification import (
    FrozenRuntimeAssessment,
    SelfContainedReleaseQualificationGate,
)

REVISION = "a" * 40


class StaticRuntimeAssessor:
    def __init__(self, assessment: FrozenRuntimeAssessment) -> None:
        self.assessment = assessment
        self.calls: list[Path] = []

    def assess(self, archive_path: Path) -> FrozenRuntimeAssessment:
        self.calls.append(archive_path)
        return self.assessment


def _assessment() -> FrozenRuntimeAssessment:
    return FrozenRuntimeAssessment(
        daemon_importable=True,
        executable_present=True,
        executable_arm64=True,
        executable_mode_safe=True,
        self_test_passed=True,
        frozen_runtime=True,
        mlx_runtime_linked=True,
        numba_openmp_linked=True,
        pythonpath_absent=True,
        sqlite_fts5_available=True,
        sqlite_vec_available=True,
        self_test_network_calls=0,
        self_test_persistent_writes=0,
    )


def _canonical(value: object) -> bytes:
    return (json.dumps(value, separators=(",", ":"), sort_keys=True) + "\n").encode()


def _private(path: Path, payload: bytes) -> None:
    path.write_bytes(payload)
    path.chmod(0o600)


def _fixture(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    p12_passed: bool = True,
    assessment: FrozenRuntimeAssessment | None = None,
) -> tuple[SelfContainedReleaseQualificationGate, StaticRuntimeAssessor]:
    archive = tmp_path / "Jarvis.zip"
    report = tmp_path / "Jarvis.distribution.json"
    manifest = tmp_path / "Jarvis.release.json"
    payloads = {
        "Jarvis.app/Contents/Info.plist": b"plist",
        "Jarvis.app/Contents/MacOS/Jarvis": b"native-app",
        "Jarvis.app/Contents/Resources/Daemon/jarvis-daemon": b"arm64-daemon",
        "Jarvis.app/Contents/Resources/Daemon/_internal/base_library.zip": b"python",
    }
    with zipfile.ZipFile(archive, "w") as bundle:
        for name, payload in payloads.items():
            bundle.writestr(name, payload)
    archive.chmod(0o600)
    archive_sha256 = hashlib.sha256(archive.read_bytes()).hexdigest()
    checks = [
        {
            "check_id": check_id,
            "status": "passed" if p12_passed else "blocked",
            "reason": "verified" if p12_passed else "prerequisite_not_passed",
            "metrics": {},
        }
        for check_id in sorted(
            {
                "release.pilot_chain",
                "release.provenance",
                "security.developer_id",
                "security.apple_notarization",
                "privacy.distribution_boundary",
            }
        )
    ]
    passed = 5 if p12_passed else 0
    manifest_payload = _canonical(
        {
            "schema_version": "1.0",
            "product": "Jarvis",
            "bundle_identifier": "ai.aegis.menubar",
            "version": "0.1.0",
            "build": "1",
            "build_revision": REVISION,
            "source_tree_sha256": "e" * 64,
            "created_at": "2026-09-08T00:00:00Z",
            "profile": "developer-id-notarized",
            "architecture": "arm64",
            "minimum_macos": "14.0",
            "artifact": {
                "name": "Jarvis.zip",
                "format": "zip",
                "sha256": archive_sha256,
                "size": archive.stat().st_size,
                "uncompressed_size": sum(map(len, payloads.values())),
            },
            "bundle_files": [
                {
                    "path": name,
                    "sha256": hashlib.sha256(payload).hexdigest(),
                    "size": len(payload),
                }
                for name, payload in payloads.items()
            ],
            "sbom": {
                "name": "Jarvis.spdx.json",
                "format": "SPDX-2.3",
                "sha256": "f" * 64,
            },
            "privacy": {
                "contains_prompts": False,
                "contains_transcripts": False,
                "contains_images": False,
                "contains_credentials": False,
                "contains_absolute_paths": False,
            },
        }
    )
    _private(manifest, manifest_payload)
    _private(
        report,
        _canonical(
            {
                "schema_version": "1.0",
                "profile": "notarized_distribution_qualification",
                "build_revision": REVISION,
                "status": "passed" if p12_passed else "blocked",
                "score": passed * 20,
                "gate_passed": p12_passed,
                "passed": passed,
                "total": 5,
                "duration_ms": 1,
                "artifact_sha256": archive_sha256,
                "evidence_sha256": {
                    "pilot": "b" * 64,
                    "release_manifest": hashlib.sha256(manifest_payload).hexdigest(),
                    "sbom": "d" * 64,
                },
                "checks": checks,
                "privacy": {
                    "contains_prompt_text": False,
                    "contains_transcripts": False,
                    "contains_audio": False,
                    "contains_images": False,
                    "contains_credentials": False,
                    "contains_absolute_paths": False,
                    "model_api_calls": 0,
                    "apple_notary_transmits_user_content": False,
                },
            }
        ),
    )
    monkeypatch.setattr(
        "aegis_core.self_contained_release_qualification._source_revision",
        lambda _root: REVISION,
    )
    monkeypatch.setattr(
        "aegis_core.self_contained_release_qualification._require_clean_tracked_tree",
        lambda _root: None,
    )
    selected = StaticRuntimeAssessor(assessment or _assessment())
    return (
        SelfContainedReleaseQualificationGate(
            project_root=tmp_path,
            p12_report_path=report,
            release_manifest_path=manifest,
            release_archive_path=archive,
            assessor=selected,
        ),
        selected,
    )


def test_p13_authenticates_a_self_contained_runtime(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    gate, assessor = _fixture(tmp_path, monkeypatch)

    report = gate.run()

    assert report.gate_passed is True
    assert report.score == 100
    assert assessor.calls == [tmp_path / "Jarvis.zip"]
    assert [check.check_id for check in report.checks] == [
        "release.p12_chain",
        "runtime.embedded_daemon",
        "runtime.cold_start",
        "runtime.source_independence",
        "privacy.runtime_boundary",
    ]
    assert report.private_dict()["privacy"]["network_calls"] == 0


def test_p13_preserves_p12_as_a_hard_prerequisite(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    gate, _ = _fixture(tmp_path, monkeypatch, p12_passed=False)

    report = gate.run()

    check = next(item for item in report.checks if item.check_id == "release.p12_chain")
    assert check.status is QualificationStatus.BLOCKED
    assert report.gate_passed is False


def test_p13_fails_closed_when_frozen_runtime_needs_pythonpath(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    gate, _ = _fixture(
        tmp_path,
        monkeypatch,
        assessment=replace(_assessment(), pythonpath_absent=False),
    )

    report = gate.run()

    check = next(
        item for item in report.checks if item.check_id == "runtime.source_independence"
    )
    assert check.status is QualificationStatus.BLOCKED
    assert check.reason == "source_checkout_dependency_detected"


def test_p13_rejects_runtime_source_files(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    gate, _ = _fixture(tmp_path, monkeypatch)
    archive = tmp_path / "Jarvis.zip"
    with zipfile.ZipFile(archive, "a") as bundle:
        bundle.writestr(
            "Jarvis.app/Contents/Resources/Daemon/aegis_core/secrets.py",
            b"source",
        )
    archive.chmod(0o600)
    digest = hashlib.sha256(archive.read_bytes()).hexdigest()
    p12_path = tmp_path / "Jarvis.distribution.json"
    p12 = json.loads(p12_path.read_text(encoding="utf-8"))
    p12["artifact_sha256"] = digest
    _private(p12_path, _canonical(p12))
    manifest_path = tmp_path / "Jarvis.release.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["artifact"]["sha256"] = digest
    manifest["artifact"]["size"] = archive.stat().st_size
    manifest_payload = _canonical(manifest)
    _private(manifest_path, manifest_payload)
    p12 = json.loads(p12_path.read_text(encoding="utf-8"))
    p12["evidence_sha256"]["release_manifest"] = hashlib.sha256(
        manifest_payload
    ).hexdigest()
    _private(p12_path, _canonical(p12))

    report = gate.run()

    check = next(item for item in report.checks if item.check_id == "privacy.runtime_boundary")
    assert check.status is QualificationStatus.BLOCKED


def test_p13_pipeline_runs_after_p12_and_publishes_private_evidence() -> None:
    root = Path(__file__).resolve().parents[1]
    script = (root / "script/p13_self_contained_runtime_gate.sh").read_text(
        encoding="utf-8"
    )

    assert script.index("./script/p12_distribution_release_gate.sh") < script.index(
        "self-contained-release-qualification"
    )
    assert 'AEGIS_FINAL_REPORT="$AEGIS_PROJECT_ROOT/dist/Jarvis.runtime.json"' in script
    assert "/bin/chmod 600" in script
    assert '"$AEGIS_PROJECT_ROOT/script/aegis.sh"' in script
    assert ".venv/bin/jarvis" not in script
