"""Own packaging/launcher fixtures; never a real app, account or owner profile."""

import json
import os
import plistlib
import subprocess
import sys
from dataclasses import replace
from pathlib import Path

import pytest
from test_local_release_update import artifact, context, install, profile_pin

from jarvis_core import local_release_package as package
from jarvis_core.local_release_launch import resolve_launch
from jarvis_core.local_release_update import ReleaseError
from jarvis_core.recovery import canonical
from jarvis_core.recovery_fs import FileIdentity

_REPO = Path(__file__).resolve().parents[1]


def own_inputs(tmp_path):
    selected = artifact(tmp_path / "selected")
    bundle = selected / "app/Jarvis.app"
    (bundle / "Contents/Info.plist").write_bytes(
        plistlib.dumps(
            {
                "CFBundleName": "Jarvis",
                "CFBundleExecutable": "Jarvis",
                "CFBundleIdentifier": "com.jarvis.desktop.baseline",
            }
        )
    )
    return selected / "python", bundle


def packaged(tmp_path):
    source, bundle = own_inputs(tmp_path)
    destination = tmp_path / "own-release"
    result = package.package_release(
        source,
        bundle,
        destination,
        "v1",
        {"state": [0, 2], "memory": [0, 2], "schedules": [0, 1]},
        context(),
    )
    return result, destination


def test_package_exact_own_files_install_and_resolve_without_executing_source(tmp_path):
    result, source = packaged(tmp_path)
    assert result["code_executed"] is False and result["signing_performed"] is False
    assert result["release_authenticity"] == "not_established"
    assert (source / "python/jarvis_core/service.py").read_text().startswith("raise RuntimeError")
    installation = tmp_path / "installation"
    installation.mkdir(mode=0o700)
    receipt = install(source, installation)
    plan = resolve_launch(
        installation, context(), profile_pin(), expected_current_sha256=receipt.current_sha256
    )
    assert plan.manifest_sha256 == result["manifest_sha256"]
    assert result["files"] == 5


def test_launch_explicit_dependency_path_is_one_argument_and_never_inferred(tmp_path):
    _, source = packaged(tmp_path)
    installation = tmp_path / "installation"
    installation.mkdir(mode=0o700)
    receipt = install(source, installation)
    plan = resolve_launch(
        installation, context(), profile_pin(), expected_current_sha256=receipt.current_sha256
    )
    base = dict(python=Path("/usr/bin/python3"), codex=Path("/private/tmp/fixture-codex"),
                state=tmp_path / "own-profile")
    legacy = plan.arguments(**base)
    assert "--dependencies" not in legacy
    chosen = tmp_path / "selected dependencies with spaces"
    assert plan.arguments(**base, dependencies=chosen) == [*legacy, "--dependencies", str(chosen)]
    for invalid in (Path("relative"), Path("/private/tmp/../foreign")):
        with pytest.raises(ReleaseError, match="release_launch_arguments_invalid"):
            plan.arguments(**base, dependencies=invalid)


def test_python_only_copy_skips_bytecode_icloud_duplicates_and_does_not_read_credentials(tmp_path):
    source, bundle = own_inputs(tmp_path)
    root = source / "jarvis_core"
    (root / "__pycache__").mkdir(mode=0o700)
    (root / "__pycache__/service.pyc").write_bytes(b"not source")
    (root / "service 2.py").write_bytes(b"not the selected source")
    (root / "auth.json").write_bytes(b"SYNTHETIC SECRET MUST NOT BE READ")
    destination = tmp_path / "release"
    package.package_release(
        source,
        bundle,
        destination,
        "v1",
        {"state": [0, 2], "memory": [0, 2], "schedules": [0, 1]},
        context(),
    )
    assert sorted(p.name for p in (destination / "python/jarvis_core").iterdir()) == [
        "__init__.py",
        "maintenance_journal.py",
        "service.py",
    ]


@pytest.mark.parametrize("kind", ["symlink", "hardlink", "credential", "foreign_brand"])
def test_unsafe_or_foreign_bundle_rejected_without_reading_credential_payload(
    tmp_path, monkeypatch, kind
):
    source, bundle = own_inputs(tmp_path)
    selected = bundle / "Contents/MacOS/Jarvis"
    credential_inode = None
    if kind == "symlink":
        data = selected.read_bytes()
        selected.unlink()
        outside = tmp_path / "outside"
        outside.write_bytes(data)
        selected.symlink_to(outside)
    elif kind == "hardlink":
        os.link(selected, tmp_path / "hardlinked")
    elif kind == "credential":
        secret = bundle / "Contents/Auth.JSON"
        secret.write_bytes(b"synthetic credential that must never be read")
        credential_inode = secret.stat().st_ino
    else:
        path = bundle / "Contents/Info.plist"
        value = plistlib.loads(path.read_bytes())
        value["CFBundleName"] = "ForeignProduct"
        path.write_bytes(plistlib.dumps(value))
    original = os.pread

    def no_secret_read(fd, length, offset):
        assert os.fstat(fd).st_ino != credential_inode
        return original(fd, length, offset)

    monkeypatch.setattr(package.os, "pread", no_secret_read)
    target = tmp_path / "new-release"
    with pytest.raises(ReleaseError):
        package.package_release(
            source,
            bundle,
            target,
            "v1",
            {"state": [0, 2], "memory": [0, 2], "schedules": [0, 1]},
            context(),
        )
    assert not target.exists()


def test_packager_cli_makes_an_artifact_and_never_runs_selected_code(tmp_path):
    source, bundle = own_inputs(tmp_path)
    target = tmp_path / "command-release"
    result = subprocess.run(
        [
            sys.executable,
            str(_REPO / "script/package_local_release.py"),
            "--python-source",
            str(source),
            "--native-bundle",
            str(bundle),
            "--destination",
            str(target),
            "--version-key",
            "fixture-v2",
        ],
        capture_output=True,
        text=True,
        timeout=10,
        check=True,
    )
    value = json.loads(result.stdout)
    assert value["version_key"] == "fixture-v2" and not value["code_executed"]
    assert value["files"] == 5 and (target / "release.json").is_file()


def own_launch(tmp_path):
    result, source = packaged(tmp_path)
    installation = tmp_path / "installation"
    installation.mkdir(mode=0o700)
    receipt = install(source, installation)
    profile = tmp_path / "own-synthetic-profile"
    profile.mkdir(mode=0o700)
    (profile / "profile.lock").touch(mode=0o600)
    pin = replace(
        profile_pin(),
        root_identity=FileIdentity.of(profile.stat()),
        schemas=(("state", 0), ("memory", 0), ("schedules", 0)),
    )
    pin_path = tmp_path / "explicit-profile-pin.json"
    pin_path.write_bytes(canonical(pin.metadata()))
    pin_path.chmod(0o600)
    arguments = [
        sys.executable,
        str(_REPO / "script/launch_local_release.py"),
        "--install-root",
        str(installation),
        "--profile-pin",
        str(pin_path),
        "--expected-current-sha256",
        receipt.current_sha256,
        "--python",
        os.path.realpath(sys.executable),
        "--codex",
        os.path.realpath(sys.executable),
        "--state",
        str(profile),
    ]
    return installation, profile, arguments, result


def test_launcher_cli_default_only_reports_verified_coherent_plan(tmp_path):
    installation, profile, arguments, _ = own_launch(tmp_path)
    before = {p.name: p.read_bytes() for p in profile.iterdir()}
    done = subprocess.run(arguments, capture_output=True, text=True, check=True, timeout=10)
    plan = json.loads(done.stdout)
    assert plan["launch_performed"] is False and plan["requires_fresh_host_pin"]
    assert plan["profile_content_checked_by_cli"] is False
    assert plan["arguments"][0].startswith(str(installation / "releases/v1/app"))
    assert plan["python_source"] == str(installation / "releases/v1/python")
    assert before == {p.name: p.read_bytes() for p in profile.iterdir()}


def test_launcher_never_admits_or_opens_a_restored_profile(tmp_path):
    _, profile, arguments, _ = own_launch(tmp_path)
    (profile / "restore_record.json").write_bytes(b"OWN SYNTHETIC MARKER")
    done = subprocess.run([*arguments, "--launch"], capture_output=True, text=True, timeout=10)
    assert done.returncode != 0
    assert "release_restored_profile_requires_owner_handoff" in done.stderr
    assert not (profile / "restore_admission.json").exists()


def test_launcher_refuses_schema_changed_since_host_pin_before_any_exec(tmp_path):
    from jarvis_core.state import StateStore

    _, profile, arguments, _ = own_launch(tmp_path)
    with StateStore(profile / "jarvis.sqlite3") as store:
        store.create_session("Cambio sintético después del pin")
    done = subprocess.run([*arguments, "--launch"], capture_output=True, text=True, timeout=10)
    assert done.returncode != 0
    assert "release_launch_profile_schema_changed" in done.stderr
    assert not (profile / "synthetic-launch-proof.json").exists()


def test_launch_schema_validation_reads_confirmed_wal_schema_and_preserves_all_originals(tmp_path):
    from wal_fixture import make_wal, oracle

    from jarvis_core.host_profile import ProfileLease
    from jarvis_core.local_release_launch import validate_profile_for_launch

    root = tmp_path / "own-cold-profile"
    make_wal(root, roles=("memory",))
    (root / "profile.lock").touch(mode=0o600)
    pin = replace(
        profile_pin(),
        root_identity=FileIdentity.of(root.stat()),
        schemas=(("state", 0), ("memory", 1), ("schedules", 0)),
    )
    before = oracle(root)
    lease = ProfileLease.acquire(root, create=False)
    try:
        families = validate_profile_for_launch(root, context(), pin, lease)
        assert len(families) == 3 and families[1].capture_mode == "cold_wal"
        assert before == oracle(root)
    finally:
        lease.close()


def test_launch_schema_validation_sees_schema_change_present_only_in_wal(tmp_path):
    from wal_fixture import make_wal, oracle

    from jarvis_core.host_profile import ProfileLease
    from jarvis_core.local_release_launch import validate_profile_for_launch
    from jarvis_core.memory import _COPY_SCHEMA

    root = tmp_path / "own-cold-profile"
    make_wal(root, roles=("memory",))
    (root / "profile.lock").touch(mode=0o600)
    script = (
        "import os,sqlite3,sys\n"
        "c=sqlite3.connect(sys.argv[1],isolation_level=None)\n"
        "c.executescript('BEGIN IMMEDIATE;'+sys.argv[2]+"
        "'PRAGMA user_version=2;COMMIT;')\n"
        "os._exit(0)\n"
    )
    subprocess.run(
        [
            os.path.realpath(sys.executable),
            "-I",
            "-c",
            script,
            str(root / "memory.sqlite3"),
            _COPY_SCHEMA,
        ],
        check=True,
        timeout=10,
    )
    # Opening the original only occurred in the synthetic writer before lease.
    assert int.from_bytes((root / "memory.sqlite3").read_bytes()[60:64], "big") == 1
    pin = replace(
        profile_pin(),
        root_identity=FileIdentity.of(root.stat()),
        schemas=(("state", 0), ("memory", 1), ("schedules", 0)),
    )
    before = oracle(root)
    lease = ProfileLease.acquire(root, create=False)
    try:
        with pytest.raises(ReleaseError, match="release_launch_profile_schema_changed"):
            validate_profile_for_launch(root, context(), pin, lease)
        assert before == oracle(root)
    finally:
        lease.close()


def test_launcher_executes_only_own_fixture_with_source_and_native_from_same_version(tmp_path):
    source, bundle = own_inputs(tmp_path)
    native = bundle / "Contents/MacOS/Jarvis"
    native.write_text(
        f"#!{os.path.realpath(sys.executable)}\n"
        + "import json,sys\nfrom pathlib import Path\n"
        + "a=sys.argv[1:]; f=dict(zip(a[::2],a[1::2]))\n"
        + "Path(f['--state'],'synthetic-launch-proof.json').write_text("
        + "json.dumps({'argv':sys.argv,'service':"
        + "Path(f['--source'],'jarvis_core/service.py').read_text()}))\n"
    )
    destination = tmp_path / "release"
    package.package_release(
        source,
        bundle,
        destination,
        "v1",
        {"state": [0, 2], "memory": [0, 2], "schedules": [0, 1]},
        context(),
    )
    installation = tmp_path / "installation"
    installation.mkdir(mode=0o700)
    receipt = install(destination, installation)
    profile = tmp_path / "profile"
    profile.mkdir(mode=0o700)
    (profile / "profile.lock").touch(mode=0o600)
    pin_path = tmp_path / "own-pin.json"
    pin_path.write_bytes(
        canonical(
            replace(
                profile_pin(),
                root_identity=FileIdentity.of(profile.stat()),
                schemas=(("state", 0), ("memory", 0), ("schedules", 0)),
            ).metadata()
        )
    )
    pin_path.chmod(0o600)
    done = subprocess.run(
        [
            sys.executable,
            str(_REPO / "script/launch_local_release.py"),
            "--install-root",
            str(installation),
            "--profile-pin",
            str(pin_path),
            "--expected-current-sha256",
            receipt.current_sha256,
            "--python",
            os.path.realpath(sys.executable),
            "--codex",
            os.path.realpath(sys.executable),
            "--state",
            str(profile),
            "--launch",
        ],
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert done.returncode == 0, done.stderr
    proof = json.loads((profile / "synthetic-launch-proof.json").read_bytes())
    assert proof["argv"][0].startswith(str(installation / "releases/v1/app/"))
    assert proof["argv"][proof["argv"].index("--source") + 1] == str(
        installation / "releases/v1/python"
    )
    assert proof["service"].startswith("raise RuntimeError('MUST NEVER EXECUTE')")
