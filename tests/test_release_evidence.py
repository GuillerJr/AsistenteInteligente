from __future__ import annotations

import importlib.util
import json
import plistlib
import stat
import zipfile
from pathlib import Path
from types import ModuleType

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
REVISION = "a" * 40


def _load_release_evidence() -> ModuleType:
    path = PROJECT_ROOT / "script/release_evidence.py"
    spec = importlib.util.spec_from_file_location("aegis_release_evidence", path)
    if spec is None or spec.loader is None:
        raise RuntimeError("release evidence module is unavailable")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _project_with_locks(tmp_path: Path) -> Path:
    project = tmp_path / "project"
    swift = project / "native/AegisAudio"
    swift.mkdir(parents=True)
    (project / "uv.lock").write_text(
        """version = 1

[[package]]
name = "aegis-swarm"
version = "0.1.0"
source = { editable = "." }

[[package]]
name = "pydantic"
version = "2.11.9"
source = { registry = "https://pypi.org/simple" }
""",
        encoding="utf-8",
    )
    (swift / "Package.resolved").write_text(
        json.dumps(
            {
                "version": 3,
                "pins": [
                    {
                        "identity": "onnxruntime-swift-package-manager",
                        "location": (
                            "https://github.com/microsoft/"
                            "onnxruntime-swift-package-manager"
                        ),
                        "state": {"version": "1.24.2", "revision": "b" * 40},
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    return project


def _release_archive(tmp_path: Path, *, path_name: str | None = None) -> Path:
    archive_path = tmp_path / "Jarvis.zip"
    info = plistlib.dumps(
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
    with zipfile.ZipFile(archive_path, "w") as archive:
        archive.writestr(
            path_name or "Jarvis.app/Contents/Info.plist",
            info,
        )
        archive.writestr("Jarvis.app/Contents/MacOS/Jarvis", b"arm64-binary")
        archive.writestr(
            "Jarvis.app/Contents/Resources/Daemon/jarvis-daemon",
            b"frozen-arm64-daemon",
        )
    return archive_path


def test_release_documents_bind_source_bundle_archive_and_locked_dependencies(
    tmp_path: Path,
) -> None:
    release = _load_release_evidence()
    project = _project_with_locks(tmp_path)
    archive = _release_archive(tmp_path)

    manifest, sbom = release.build_documents(
        project,
        archive,
        profile="local-development",
        revision=REVISION,
        source_tree_sha256="c" * 64,
        created_at="2026-09-07T12:00:00Z",
        sbom_name="Jarvis.spdx.json",
    )

    assert manifest["build_revision"] == REVISION
    assert manifest["source_tree_sha256"] == "c" * 64
    assert manifest["artifact"]["sha256"]
    assert manifest["privacy"] == {
        "contains_prompts": False,
        "contains_transcripts": False,
        "contains_images": False,
        "contains_credentials": False,
        "contains_absolute_paths": False,
    }
    assert {package["name"] for package in sbom["packages"]} == {
        "Jarvis",
        "onnxruntime-swift-package-manager",
        "pydantic",
    }
    assert sbom["spdxVersion"] == "SPDX-2.3"


def test_release_evidence_rejects_archive_escape_and_identity_mismatch(tmp_path: Path) -> None:
    release = _load_release_evidence()
    escaped = _release_archive(tmp_path, path_name="../Info.plist")

    with pytest.raises(release.ReleaseEvidenceError, match="unsafe path"):
        release.inspect_archive(escaped, REVISION)

    valid = _release_archive(tmp_path)
    with pytest.raises(release.ReleaseEvidenceError, match="identity"):
        release.inspect_archive(valid, "d" * 40)


def test_release_evidence_accepts_only_resolving_internal_symbolic_links(
    tmp_path: Path,
) -> None:
    release = _load_release_evidence()
    archive = _release_archive(tmp_path)
    target = "Jarvis.app/Contents/Resources/Daemon/_internal/libmlx.dylib"
    link = "Jarvis.app/Contents/Resources/Daemon/_internal/libmlx-current.dylib"
    link_info = zipfile.ZipInfo(link)
    link_info.create_system = 3
    link_info.external_attr = (stat.S_IFLNK | 0o777) << 16
    with zipfile.ZipFile(archive, "a") as bundle:
        bundle.writestr(target, b"arm64-library")
        bundle.writestr(link_info, b"libmlx.dylib")

    inspected = release.inspect_archive(archive, REVISION)

    assert any(item["path"] == link for item in inspected["files"])


def test_release_evidence_rejects_symbolic_links_escaping_the_bundle(
    tmp_path: Path,
) -> None:
    release = _load_release_evidence()
    archive = _release_archive(tmp_path)
    link_info = zipfile.ZipInfo(
        "Jarvis.app/Contents/Resources/Daemon/_internal/Python"
    )
    link_info.create_system = 3
    link_info.external_attr = (stat.S_IFLNK | 0o777) << 16
    with zipfile.ZipFile(archive, "a") as bundle:
        bundle.writestr(link_info, b"../../../../../../../../etc/passwd")

    with pytest.raises(release.ReleaseEvidenceError, match="escapes the bundle"):
        release.inspect_archive(archive, REVISION)


def test_release_evidence_rejects_private_biometric_models(tmp_path: Path) -> None:
    release = _load_release_evidence()
    archive = _release_archive(tmp_path)
    with zipfile.ZipFile(archive, "a") as bundle:
        bundle.writestr(
            "Jarvis.app/Contents/Resources/JarvisSpeakerIdentity.mlmodelc/coremldata.bin",
            b"private-owner-model",
        )

    with pytest.raises(release.ReleaseEvidenceError, match="private biometric model"):
        release.inspect_archive(archive, REVISION)


def test_release_evidence_rejects_source_coupled_bundle(tmp_path: Path) -> None:
    release = _load_release_evidence()
    archive_path = tmp_path / "Jarvis.zip"
    info = plistlib.dumps(
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
    with zipfile.ZipFile(archive_path, "w") as archive:
        archive.writestr("Jarvis.app/Contents/Info.plist", info)
        archive.writestr("Jarvis.app/Contents/MacOS/Jarvis", b"arm64-binary")

    with pytest.raises(release.ReleaseEvidenceError, match="self-contained daemon"):
        release.inspect_archive(archive_path, REVISION)


def test_release_evidence_detects_manifest_or_sbom_tampering(tmp_path: Path) -> None:
    release = _load_release_evidence()
    project = _project_with_locks(tmp_path)
    archive = _release_archive(tmp_path)
    manifest, sbom = release.build_documents(
        project,
        archive,
        profile="developer-id",
        revision=REVISION,
        source_tree_sha256="e" * 64,
        created_at="2026-09-07T12:00:00Z",
        sbom_name="Jarvis.spdx.json",
    )
    altered = json.loads(json.dumps(manifest))
    altered["artifact"]["size"] += 1

    with pytest.raises(release.ReleaseEvidenceError, match="authentication failed"):
        release.validate_documents(
            altered,
            sbom,
            expected_manifest=manifest,
            expected_sbom=sbom,
        )


def test_release_pipeline_exposes_candidate_evidence_and_automatic_rollback() -> None:
    release_script = (PROJECT_ROOT / "script/release_macos.sh").read_text(encoding="utf-8")
    build_script = (PROJECT_ROOT / "script/build_and_run.sh").read_text(encoding="utf-8")
    install_script = (PROJECT_ROOT / "script/menu_bar_service.sh").read_text(
        encoding="utf-8"
    )

    assert "candidate)" in release_script
    assert "release_evidence generate local-development" in release_script
    assert "release_evidence verify developer-id-notarized" in release_script
    assert "rollback_failed_install" in install_script
    assert "rollback=restored" in install_script
    assert "Personal biometric models must never be copied" in build_script
    assert 'AEGIS_EMIT_ARCHIVE="${AEGIS_EMIT_ARCHIVE:-0}"' in build_script
    assert "AEGIS_INCLUDE_PERSONAL_MODELS=0" in release_script
    assert (
        "AEGIS_INCLUDE_PERSONAL_MODELS=1 AEGIS_EMIT_ARCHIVE=0 AEGIS_EMBED_DAEMON=1"
        in install_script
    )
    assert install_script.index("local previous=") < install_script.index(
        'rollback_failed_install app_not_running "$previous"'
    )


def test_distribution_governance_documents_exist_and_reject_false_app_store_claims() -> None:
    license_text = (PROJECT_ROOT / "LICENSE").read_text(encoding="utf-8")
    security = (PROJECT_ROOT / "SECURITY.md").read_text(encoding="utf-8")
    threat_model = (PROJECT_ROOT / "docs/THREAT_MODEL.md").read_text(encoding="utf-8")

    assert "All rights reserved" in license_text
    assert "Security Advisories" in security
    assert "no App Store" in threat_model
    assert "Developer ID + Hardened Runtime + notarización" in threat_model
