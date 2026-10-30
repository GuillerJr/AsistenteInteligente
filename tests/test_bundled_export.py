"""Product export has a separate CI admission; local/legacy caps stay fixed."""

import plistlib
from pathlib import Path

import pytest
from test_bundled_runtime import COMMIT, fixture
from test_test_resource_guard import guard, options


def bundle(tmp_path):
    app = tmp_path.resolve() / "Jarvis.app"
    runtime = app / "Contents/Resources/JarvisRuntime"
    runtime.parent.mkdir(parents=True)
    _, _, pin = fixture(runtime)
    (app / "Contents/Info.plist").write_bytes(plistlib.dumps({
        "CFBundleExecutable": "Jarvis", "JarvisDistributionChannel": "self-contained",
        "JarvisRuntimeManifestSHA256": pin,
    }))
    return app


def admit_fixture_destination(monkeypatch):
    # The actual /private/tmp-only destination rule has its own oracle. This
    # small corpus stays in the test supervisor's private quota on both systems.
    def validate(path):
        assert path.is_absolute() and path.parent.is_dir() and not path.exists()
    monkeypatch.setattr(guard, "validate_export_destination", validate)


def test_product_export_verifies_before_and_after_without_raising_legacy_limit(
        tmp_path, monkeypatch):
    app = bundle(tmp_path)
    admit_fixture_destination(monkeypatch)
    # Scale units, not data: demonstrate the 256 cap with a 256 KiB fixture.
    monkeypatch.setattr(guard, "MIB", 1024)
    (app / "Contents/Resources/owned-large-resource").write_bytes(b"x" * (256 * 1024))
    with pytest.raises(guard.GuardError, match="límite"):
        guard.export_artifact(app, tmp_path / "legacy")
    assert not (tmp_path / "legacy").exists()
    result = guard.export_bundled_artifact(app, tmp_path / "product", source_commit=COMMIT)
    assert result == str(tmp_path / "product/Jarvis.app")
    assert (Path(result) / "Contents/Resources/owned-large-resource").stat().st_size == 256 * 1024


def test_wrong_product_commit_fails_before_creating_destination(tmp_path, monkeypatch):
    app = bundle(tmp_path)
    admit_fixture_destination(monkeypatch)
    with pytest.raises(guard.GuardError, match="SHA"):
        guard.export_bundled_artifact(app, tmp_path / "absent", source_commit="f" * 40)
    assert not (tmp_path / "absent").exists()


def test_copy_tamper_cannot_be_reported_as_exported(tmp_path, monkeypatch):
    app = bundle(tmp_path)
    admit_fixture_destination(monkeypatch)
    original = guard._copy_export

    def altered(*args, **kwargs):
        result = original(*args, **kwargs)
        (Path(result) / "Contents/Resources/JarvisRuntime/certs/cacert.pem").write_bytes(b"changed")
        return result

    monkeypatch.setattr(guard, "_copy_export", altered)
    with pytest.raises(guard.GuardError, match="SHA"):
        guard.export_bundled_artifact(app, tmp_path / "tampered", source_commit=COMMIT)


@pytest.mark.parametrize("fields", [
    {"profile": "local", "kind": "build", "full": False},
    {"profile": "ci", "kind": "python", "full": True},
    {"profile": "ci", "kind": "build", "full": True, "export_relative": "other"},
])
def test_product_export_flag_cannot_expand_local_or_other_contract(tmp_path, fields):
    identity = {"head": COMMIT}
    environment = {"GITHUB_ACTIONS": "true", "GITHUB_SHA": COMMIT,
                   "GITHUB_WORKSPACE": str(tmp_path)}
    values = {"self_contained_export": True, "export_relative": "output/Jarvis.app",
              "export_destination": Path("/private/tmp/jarvis-absent-export-fixture")}
    with pytest.raises(guard.GuardError, match="autocontenida"):
        guard.validate_options(options(**(values | fields)), environment, identity, tmp_path)
