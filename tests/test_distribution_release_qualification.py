from __future__ import annotations

import hashlib
import json
import plistlib
import zipfile
from dataclasses import replace
from pathlib import Path

import pytest

from aegis_core.distribution_release_qualification import (
    EXPECTED_PILOT_CHECKS,
    DistributionReleaseQualificationError,
    DistributionReleaseQualificationGate,
    MacOSDistributionArtifactAssessor,
    MacOSDistributionAssessment,
)
from aegis_core.macos_qualification import QualificationStatus

REVISION = "a" * 40
SOURCE_TREE_SHA256 = "b" * 64


class StaticAssessor:
    def __init__(self, assessment: MacOSDistributionAssessment) -> None:
        self.assessment = assessment
        self.calls: list[Path] = []

    def assess(self, archive_path: Path) -> MacOSDistributionAssessment:
        self.calls.append(archive_path)
        return self.assessment


def _passing_assessment() -> MacOSDistributionAssessment:
    return MacOSDistributionAssessment(
        code_signature_valid=True,
        identifier_valid=True,
        developer_id_valid=True,
        team_identifier_present=True,
        hardened_runtime=True,
        secure_timestamp=True,
        required_entitlements_present=True,
        forbidden_entitlements_absent=True,
        stapled_ticket_valid=True,
        gatekeeper_accepted=True,
    )


def _canonical(value: object) -> bytes:
    return (json.dumps(value, separators=(",", ":"), sort_keys=True) + "\n").encode()


def _write_private(path: Path, payload: bytes) -> None:
    path.write_bytes(payload)
    path.chmod(0o600)


def _pilot_payload(*, gate_passed: bool = True) -> bytes:
    checks = [
        {
            "check_id": check_id,
            "status": "passed" if gate_passed else "blocked",
            "reason": "verified" if gate_passed else "prerequisite_not_passed",
            "metrics": {},
        }
        for check_id in sorted(EXPECTED_PILOT_CHECKS)
    ]
    passed = 5 if gate_passed else 0
    return _canonical(
        {
            "schema_version": "1.0",
            "profile": "local_pilot_release_qualification",
            "build_revision": REVISION,
            "status": "passed" if gate_passed else "blocked",
            "score": passed * 20,
            "gate_passed": gate_passed,
            "passed": passed,
            "total": 5,
            "duration_ms": 25,
            "candidate_sha256": "c" * 64,
            "evidence_sha256": {
                "macos": "d" * 64,
                "release_manifest": "e" * 64,
                "sbom": "f" * 64,
                "voice": "1" * 64,
            },
            "checks": checks,
            "privacy": {
                "contains_prompt_text": False,
                "contains_transcripts": False,
                "contains_audio": False,
                "contains_images": False,
                "contains_credentials": False,
                "contains_absolute_paths": False,
                "network_calls": 0,
            },
        }
    )


def _fixture(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    assessor: StaticAssessor | None = None,
    pilot_passed: bool = True,
) -> tuple[DistributionReleaseQualificationGate, StaticAssessor]:
    archive = tmp_path / "Jarvis.zip"
    manifest = tmp_path / "Jarvis.release.json"
    sbom = tmp_path / "Jarvis.spdx.json"
    pilot = tmp_path / "Jarvis.pilot.json"
    info_payload = plistlib.dumps(
        {
            "CFBundleIdentifier": "ai.aegis.menubar",
            "CFBundleName": "Jarvis",
            "CFBundleExecutable": "Jarvis",
            "CFBundleShortVersionString": "0.1.0",
            "CFBundleVersion": "1",
            "LSMinimumSystemVersion": "14.0",
            "AegisBuildRevision": REVISION,
            "AegisBuildDirty": False,
        }
    )
    executable_payload = b"signed-arm64-executable"
    daemon_payload = b"frozen-arm64-daemon"
    with zipfile.ZipFile(archive, "w") as bundle:
        bundle.writestr("Jarvis.app/Contents/Info.plist", info_payload)
        bundle.writestr("Jarvis.app/Contents/MacOS/Jarvis", executable_payload)
        bundle.writestr(
            "Jarvis.app/Contents/Resources/Daemon/jarvis-daemon",
            daemon_payload,
        )
    archive.chmod(0o600)
    files = [
        {
            "path": "Jarvis.app/Contents/Info.plist",
            "sha256": hashlib.sha256(info_payload).hexdigest(),
            "size": len(info_payload),
        },
        {
            "path": "Jarvis.app/Contents/MacOS/Jarvis",
            "sha256": hashlib.sha256(executable_payload).hexdigest(),
            "size": len(executable_payload),
        },
        {
            "path": "Jarvis.app/Contents/Resources/Daemon/jarvis-daemon",
            "sha256": hashlib.sha256(daemon_payload).hexdigest(),
            "size": len(daemon_payload),
        },
    ]
    sbom_payload = _canonical(
        {
            "spdxVersion": "SPDX-2.3",
            "SPDXID": "SPDXRef-DOCUMENT",
            "packages": [
                {"SPDXID": "SPDXRef-Package-Jarvis", "name": "Jarvis"}
            ],
        }
    )
    _write_private(sbom, sbom_payload)
    _write_private(
        manifest,
        _canonical(
            {
                "schema_version": "1.0",
                "product": "Jarvis",
                "bundle_identifier": "ai.aegis.menubar",
                "version": "0.1.0",
                "build": "1",
                "build_revision": REVISION,
                "source_tree_sha256": SOURCE_TREE_SHA256,
                "created_at": "2026-09-08T00:00:00Z",
                "profile": "developer-id-notarized",
                "architecture": "arm64",
                "minimum_macos": "14.0",
                "artifact": {
                    "name": "Jarvis.zip",
                    "format": "zip",
                    "sha256": hashlib.sha256(archive.read_bytes()).hexdigest(),
                    "size": archive.stat().st_size,
                    "uncompressed_size": (
                        len(info_payload) + len(executable_payload) + len(daemon_payload)
                    ),
                },
                "bundle_files": files,
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
        ),
    )
    _write_private(pilot, _pilot_payload(gate_passed=pilot_passed))
    monkeypatch.setattr(
        "aegis_core.distribution_release_qualification._source_revision",
        lambda _root: REVISION,
    )
    monkeypatch.setattr(
        "aegis_core.distribution_release_qualification._source_tree_sha256",
        lambda _root: SOURCE_TREE_SHA256,
    )
    monkeypatch.setattr(
        "aegis_core.distribution_release_qualification._require_clean_tracked_tree",
        lambda _root: None,
    )
    selected_assessor = assessor or StaticAssessor(_passing_assessment())
    return (
        DistributionReleaseQualificationGate(
            project_root=tmp_path,
            pilot_report_path=pilot,
            release_manifest_path=manifest,
            release_sbom_path=sbom,
            release_archive_path=archive,
            assessor=selected_assessor,
        ),
        selected_assessor,
    )


def test_distribution_gate_authenticates_notarized_release(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    gate, assessor = _fixture(tmp_path, monkeypatch)

    report = gate.run()

    assert report.gate_passed is True
    assert report.score == 100
    assert report.status is QualificationStatus.PASSED
    assert assessor.calls == [tmp_path / "Jarvis.zip"]
    assert [check.check_id for check in report.checks] == [
        "release.pilot_chain",
        "release.provenance",
        "security.developer_id",
        "security.apple_notarization",
        "privacy.distribution_boundary",
    ]
    payload = report.private_dict()
    assert payload["privacy"]["model_api_calls"] == 0
    assert payload["privacy"]["apple_notary_transmits_user_content"] is False


def test_distribution_gate_preserves_pilot_as_hard_prerequisite(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    gate, _ = _fixture(tmp_path, monkeypatch, pilot_passed=False)

    report = gate.run()

    check = next(item for item in report.checks if item.check_id == "release.pilot_chain")
    assert check.status is QualificationStatus.BLOCKED
    assert report.score == 80
    assert report.gate_passed is False


def test_distribution_gate_rejects_unstapled_or_untrusted_candidate(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assessment = replace(
        _passing_assessment(),
        stapled_ticket_valid=False,
        gatekeeper_accepted=False,
    )
    gate, _ = _fixture(tmp_path, monkeypatch, assessor=StaticAssessor(assessment))

    report = gate.run()

    check = next(
        item for item in report.checks if item.check_id == "security.apple_notarization"
    )
    assert check.status is QualificationStatus.BLOCKED
    assert check.reason == "notarization_assessment_failed"


def test_distribution_gate_detects_archive_tampering(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    gate, _ = _fixture(tmp_path, monkeypatch)
    archive = tmp_path / "Jarvis.zip"
    _write_private(archive, archive.read_bytes() + b"offline-tampering")

    report = gate.run()

    check = next(item for item in report.checks if item.check_id == "release.provenance")
    assert check.status is QualificationStatus.BLOCKED
    assert report.gate_passed is False


def test_distribution_gate_rejects_non_notarized_manifest_profile(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    gate, _ = _fixture(tmp_path, monkeypatch)
    manifest = tmp_path / "Jarvis.release.json"
    payload = json.loads(manifest.read_text(encoding="utf-8"))
    payload["profile"] = "developer-id"
    _write_private(manifest, _canonical(payload))

    with pytest.raises(DistributionReleaseQualificationError, match="schema"):
        gate.run()


def test_entitlement_parser_ignores_codesign_diagnostics() -> None:
    payload = plistlib.dumps(
        {"com.apple.security.device.microphone": True},
        fmt=plistlib.FMT_XML,
    ).decode()

    parsed = MacOSDistributionArtifactAssessor._parse_entitlements(
        f"Executable=/private/tmp/Jarvis.app/Contents/MacOS/Jarvis\n{payload}\n"
    )

    assert parsed == {"com.apple.security.device.microphone": True}


def test_entitlement_parser_accepts_tahoe_codesign_structure() -> None:
    parsed = MacOSDistributionArtifactAssessor._parse_entitlements(
        """Executable=/private/tmp/Jarvis.app/Contents/MacOS/Jarvis
[Dict]
    [Key] com.apple.security.device.microphone
    [Value]
        [Bool] true
    [Key] com.apple.security.cs.get-task-allow
    [Value]
        [Bool] false
"""
    )

    assert parsed == {
        "com.apple.security.device.microphone": True,
        "com.apple.security.cs.get-task-allow": False,
    }


def test_native_assessor_requires_an_exact_developer_id_identity() -> None:
    with pytest.raises(DistributionReleaseQualificationError, match="identity"):
        MacOSDistributionArtifactAssessor("-")

    assessor = MacOSDistributionArtifactAssessor(
        "Developer ID Application: Guillermo Example (ABCDEFGHIJ)"
    )

    assert assessor is not None


def test_distribution_rejects_debug_entitlements_in_nested_daemon() -> None:
    assert MacOSDistributionArtifactAssessor._forbidden_entitlements_absent(
        {"com.apple.security.device.microphone": True},
        {},
    )
    assert not MacOSDistributionArtifactAssessor._forbidden_entitlements_absent(
        {"com.apple.security.device.microphone": True},
        {"com.apple.security.cs.disable-library-validation": True},
    )


def test_p12_script_preserves_gate_order_and_private_evidence() -> None:
    project_root = Path(__file__).resolve().parents[1]
    script = (project_root / "script/p12_distribution_release_gate.sh").read_text(
        encoding="utf-8"
    )

    assert script.index("./script/p11_pilot_release_gate.sh") < script.index(
        "./script/release_macos.sh notarize"
    )
    assert script.index("./script/release_macos.sh notarize") < script.index(
        "distribution-release-qualification"
    )
    assert 'AEGIS_FINAL_REPORT="$AEGIS_PROJECT_ROOT/dist/Jarvis.distribution.json"' in script
    assert "/bin/chmod 600" in script
    assert "AEGIS_CODESIGN_IDENTITY" in script
    assert "AEGIS_NOTARY_PROFILE" in script
    assert "AEGIS_P12_CODESIGN_IDENTITY" in script
