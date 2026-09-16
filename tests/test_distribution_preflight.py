from __future__ import annotations

import importlib
import json
import subprocess
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def preflight(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.syspath_prepend(str(ROOT / "script"))
    return importlib.import_module("distribution_preflight")


def _git(root: Path, *args: str) -> None:
    subprocess.run(("/usr/bin/git", "-C", str(root), *args), check=True, capture_output=True)


@pytest.fixture
def repository(tmp_path: Path) -> Path:
    _git(tmp_path, "init")
    (tmp_path / "src").mkdir()
    (tmp_path / "src/main.py").write_text("original", encoding="utf-8")
    _git(tmp_path, "add", "src/main.py")
    _git(
        tmp_path,
        "-c",
        "user.name=Fixture",
        "-c",
        "user.email=fixture@example.invalid",
        "-c",
        "commit.gpgsign=false",
        "commit",
        "-m",
        "fixture",
    )
    return tmp_path


def test_fingerprint_binds_untracked_modified_and_deleted_build_inputs(
    preflight,
    repository,
) -> None:
    baseline = preflight.source_fingerprint(repository)
    new = repository / "src/new.py"
    new.write_text("new module", encoding="utf-8")
    addition = preflight.source_fingerprint(repository)
    assert addition != baseline
    new.write_text("changed module", encoding="utf-8")
    assert preflight.source_fingerprint(repository) != addition
    new.unlink()
    assert preflight.source_fingerprint(repository) == baseline
    (repository / "src/main.py").unlink()
    assert preflight.source_fingerprint(repository) != baseline


def test_clean_source_gate_rejects_untracked_compiled_code_but_not_editor_preferences(
    preflight,
    repository,
) -> None:
    preflight.evidence._require_clean_tracked_tree(repository)
    (repository / ".vscode").mkdir()
    (repository / ".vscode/settings.json").write_text("{}", encoding="utf-8")
    preflight.evidence._require_clean_tracked_tree(repository)
    (repository / "src/uncommitted.py").write_text("new", encoding="utf-8")
    with pytest.raises(preflight.evidence.ReleaseEvidenceError, match="untracked build inputs"):
        preflight.evidence._require_clean_tracked_tree(repository)


def test_fingerprint_never_follows_source_symlinks(preflight, repository, tmp_path) -> None:
    (repository / "src/link.py").symlink_to(tmp_path / "unread-private-target")
    with pytest.raises(preflight.evidence.ReleaseEvidenceError, match="symbolic link"):
        preflight.source_fingerprint(repository)


def test_preflight_is_noninteractive_and_keeps_voice_and_public_release_pending(
    preflight,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    commands = []

    def run(*command, **kwargs):
        commands.append(command)
        return b"/toolchain\n"

    monkeypatch.setattr(preflight, "_run", run)
    report = preflight.preflight(ROOT)
    assert commands == [
        ("/usr/bin/xcrun", "--find", "swift"),
        ("/usr/bin/xcrun", "--sdk", "macosx", "--show-sdk-path"),
    ]
    assert report["public_release_qualified"] is False
    assert report["deferred"]["owner_voice"] == "awaiting_explicit_user_authorization"
    assert all(value is False for value in report["privacy"].values())
    assert str(Path.home()) not in json.dumps(report)


def test_candidate_checks_provenance_before_building_or_notarizing() -> None:
    script = (ROOT / "script/release_macos.sh").read_text(encoding="utf-8")
    candidate = script.split("build_local_candidate() {", 1)[1].split("\n}", 1)[0]
    signed = script.split("build_archive() {", 1)[1].split("\n}", 1)[0]
    for body in (candidate, signed):
        assert body.index(" preflight ") < body.index("build_and_run.sh")


def test_invalid_build_mode_exits_without_killing_apps() -> None:
    result = subprocess.run(
        (str(ROOT / "script/build_and_run.sh"), "invalid-mode"),
        capture_output=True,
        text=True,
        check=False,
        timeout=5,
    )
    assert result.returncode == 2
    assert result.stderr.strip() == "Invalid build mode"


@pytest.fixture
def publication(preflight, monkeypatch, tmp_path):
    publisher = importlib.import_module("release_publish")
    destination = tmp_path / "dist"
    destination.mkdir(mode=0o700)
    with tempfile.TemporaryDirectory(prefix="aegis-distribution.", dir="/private/tmp") as name:
        staging = Path(name) / "bundle/artifacts"
        staging.parent.mkdir(mode=0o700)
        staging.mkdir(mode=0o700)
        for artifact in publisher.ARTIFACT_NAMES:
            path = destination / artifact
            path.write_bytes(b"previous-" + artifact.encode())
            path.chmod(0o600)
        for artifact in publisher.REQUIRED:
            path = staging / artifact
            path.write_bytes(b"next-" + artifact.encode())
            path.chmod(0o600)
        monkeypatch.setattr(publisher.evidence, "verify", lambda *args, **kwargs: None)
        yield publisher, tmp_path, staging


def test_promotion_preserves_previous_set_and_removes_stale_channel_evidence(publication) -> None:
    publisher, root, staging = publication
    history = publisher.promote(root, staging, "local-development")
    for name in publisher.ARTIFACT_NAMES:
        assert (history / name).read_bytes() == b"previous-" + name.encode()
        if name in publisher.REQUIRED:
            assert (root / "dist" / name).read_bytes() == b"next-" + name.encode()
        else:
            assert not (root / "dist" / name).exists()
    assert not (root / "dist/.release-publish.lock").exists()


def test_failed_promotion_restores_every_old_artifact(publication, monkeypatch) -> None:
    publisher, root, staging = publication
    replace = publisher.os.replace
    failed = False

    def fail_once(source, destination):
        nonlocal failed
        if Path(source).parent.name == "incoming" and Path(source).name == "Jarvis.spdx.json":
            failed = True
            raise OSError("simulated disk failure")
        replace(source, destination)

    monkeypatch.setattr(publisher.os, "replace", fail_once)
    with pytest.raises(OSError, match="disk failure"):
        publisher.promote(root, staging, "local-development")
    assert failed
    for name in publisher.ARTIFACT_NAMES:
        assert (root / "dist" / name).read_bytes() == b"previous-" + name.encode()
    assert not (root / "dist/.release-publish.lock").exists()


def test_promotion_never_replaces_a_symlink_target(publication, tmp_path) -> None:
    publisher, root, staging = publication
    original = root / "dist/Jarvis.zip"
    original.unlink()
    outside = tmp_path / "user-data"
    outside.write_bytes(b"preserve")
    original.symlink_to(outside)
    with pytest.raises(publisher.evidence.ReleaseEvidenceError, match="unsafe release artifact"):
        publisher.promote(root, staging, "local-development")
    assert outside.read_bytes() == b"preserve"
    assert original.is_symlink()


def test_concurrent_promotion_fails_without_touching_live_artifacts(publication) -> None:
    publisher, root, staging = publication
    (root / "dist/.release-publish.lock").mkdir(mode=0o700)
    with pytest.raises(publisher.evidence.ReleaseEvidenceError, match="active"):
        publisher.promote(root, staging, "local-development")
    assert (root / "dist/Jarvis.zip").read_bytes() == b"previous-Jarvis.zip"


def test_release_validation_is_not_a_bypass_of_certified_candidate_provenance(preflight, tmp_path):
    # Same dirty bundle can be inspected for engineering, but never certified.
    import plistlib
    import zipfile

    revision = "b" * 40
    info = {
        "CFBundleIdentifier": "ai.aegis.menubar",
        "CFBundleName": "Jarvis",
        "CFBundleExecutable": "Jarvis",
        "AegisBuildRevision": revision,
        "AegisBuildDirty": True,
        "CFBundleShortVersionString": "0.1.0",
        "CFBundleVersion": "1",
        "LSMinimumSystemVersion": "14.0",
    }
    path = tmp_path / "Jarvis.zip"
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("Jarvis.app/Contents/Info.plist", plistlib.dumps(info))
        for name in preflight.evidence.REQUIRED_RUNTIME_FILES - {"Jarvis.app/Contents/Info.plist"}:
            archive.writestr(name, b"fixture")
    inspected = preflight.evidence.inspect_archive(path, revision, allow_dirty=True)
    assert inspected["build_dirty"] is True
    with pytest.raises(preflight.evidence.ReleaseEvidenceError, match="identity"):
        preflight.evidence.inspect_archive(path, revision)
