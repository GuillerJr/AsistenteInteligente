"""Adversarial private profile JSON tests with temporary, synthetic settings."""

import fcntl
import json
import os
import stat

import pytest

from jarvis_core.config import ConfigError, ProfileConfig
from jarvis_core.state import StateError

SESSION = "a" * 32
OTHER = "b" * 32
DIGEST = "c" * 64


@pytest.fixture
def profile(tmp_path):
    directory = tmp_path.resolve() / "profile"
    return ProfileConfig(directory)


def valid_data(workspace=None):
    return {"version": 1, "workspace": workspace, "bindings": {}}


def write_private(path, value):
    path.write_text(json.dumps(value))
    path.chmod(0o600)


def test_roundtrip_permissions_and_fingerprint_binding(profile, tmp_path):
    assert profile.workspace is None and not profile.binding_matches(SESSION, DIGEST)
    workspace = tmp_path.resolve() / "workspace"
    workspace.mkdir(mode=0o700)
    profile.set_workspace(workspace)
    profile.bind(SESSION, DIGEST)
    assert profile.binding_matches(SESSION, DIGEST)
    assert not profile.binding_matches(OTHER, DIGEST)
    assert not profile.binding_matches(SESSION, "d" * 64)
    assert stat.S_IMODE(profile.directory.stat().st_mode) == 0o700
    assert stat.S_IMODE(profile.path.stat().st_mode) == 0o600
    restored = ProfileConfig(profile.directory)
    assert restored.workspace == str(workspace) and restored.binding_matches(SESSION, DIGEST)
    restored.forget(SESSION)
    assert not ProfileConfig(profile.directory).binding_matches(SESSION, DIGEST)


def test_changing_workspace_invalidates_old_provider_bindings(profile, tmp_path):
    first, second = (tmp_path.resolve() / name for name in ("first", "second"))
    first.mkdir(mode=0o700)
    second.mkdir(mode=0o700)
    profile.set_workspace(first)
    profile.bind(SESSION, DIGEST)
    profile.set_workspace(second)
    assert profile.workspace == str(second) and not profile.binding_matches(SESSION, DIGEST)
    assert not ProfileConfig(profile.directory).binding_matches(SESSION, DIGEST)


@pytest.mark.parametrize("value", [
    {"version": True, "workspace": None, "bindings": {}},
    {"version": 2, "workspace": None, "bindings": {}},
    {"version": 1, "workspace": "relative", "bindings": {}},
    {"version": 1, "workspace": "/tmp/../other", "bindings": {}},
    {"version": 1, "workspace": None, "bindings": {"invalid": DIGEST}},
    {"version": 1, "workspace": None, "bindings": {SESSION: "invalid"}},
    {"version": 1, "workspace": None, "bindings": {}, "untrusted": True},
])
def test_invalid_json_schema_is_rejected(profile, value):
    write_private(profile.path, value)
    with pytest.raises(ConfigError):
        ProfileConfig(profile.directory)


def test_oversized_or_corrupt_json_never_becomes_settings(profile):
    profile.path.write_bytes(b"x" * 131073)
    profile.path.chmod(0o600)
    with pytest.raises(ConfigError):
        ProfileConfig(profile.directory)
    profile.path.write_bytes(b"{not-json}")
    with pytest.raises(ConfigError):
        ProfileConfig(profile.directory)


def test_duplicate_json_keys_are_rejected(profile):
    profile.path.write_text('{"version":2,"version":1,"workspace":null,"bindings":{}}')
    profile.path.chmod(0o600)
    with pytest.raises(ConfigError):
        ProfileConfig(profile.directory)


@pytest.mark.parametrize("kind", ["symlink", "hardlink", "fifo", "public_mode"])
def test_unsafe_settings_file_rejected_without_following(profile, tmp_path, kind):
    outside = tmp_path / "outside.json"
    write_private(outside, valid_data())
    if kind == "symlink":
        profile.path.symlink_to(outside)
    elif kind == "hardlink":
        os.link(outside, profile.path)
    elif kind == "fifo":
        os.mkfifo(profile.path)
    else:
        write_private(profile.path, valid_data())
        profile.path.chmod(0o644)
    with pytest.raises(ConfigError):
        ProfileConfig(profile.directory)


def test_symlink_ancestor_is_rejected_before_open(tmp_path):
    root = tmp_path.resolve()
    outside = root / "outside"
    outside.mkdir(mode=0o700)
    link = root / "linked"
    link.symlink_to(outside, target_is_directory=True)
    with pytest.raises((ConfigError, StateError)):
        ProfileConfig(link / "profile")
    assert not (outside / "profile").exists()


def test_failed_atomic_replace_preserves_bytes_memory_and_bindings(profile, tmp_path, monkeypatch):
    workspace = tmp_path.resolve() / "workspace"
    workspace.mkdir(mode=0o700)
    profile.set_workspace(workspace)
    profile.bind(SESSION, DIGEST)
    previous = profile.path.read_bytes()

    def fail(*_, **__):
        raise OSError("synthetic failure")

    monkeypatch.setattr(os, "replace", fail)
    with pytest.raises((ConfigError, OSError)):
        profile.forget(SESSION)
    assert profile.path.read_bytes() == previous
    assert profile.binding_matches(SESSION, DIGEST)
    assert not list(profile.directory.glob(".configuration-*"))


def test_failed_temp_fsync_preserves_previous_configuration(profile, monkeypatch):
    profile.bind(SESSION, DIGEST)
    previous = profile.path.read_bytes()

    def fail(_):
        raise OSError("synthetic failure")

    monkeypatch.setattr(os, "fsync", fail)
    with pytest.raises((ConfigError, OSError)):
        profile.bind(OTHER, DIGEST)
    assert profile.path.read_bytes() == previous
    assert not profile.binding_matches(OTHER, DIGEST)
    assert not list(profile.directory.glob(".configuration-*"))


def test_settings_file_swap_to_symlink_does_not_overwrite_outside(profile, tmp_path):
    profile.bind(SESSION, DIGEST)
    outside = tmp_path / "outside.json"
    write_private(outside, valid_data("/public/fixture"))
    previous = outside.read_bytes()
    profile.path.unlink()
    profile.path.symlink_to(outside)
    with pytest.raises(ConfigError):
        profile.bind(OTHER, DIGEST)
    assert outside.read_bytes() == previous
    with pytest.raises(ConfigError):
        profile.binding_matches(SESSION, DIGEST)


def test_parent_retarget_during_save_cannot_write_outside(profile, tmp_path, monkeypatch):
    profile.bind(SESSION, DIGEST)
    original = profile.path.read_bytes()
    retired = tmp_path / "retired-profile"
    outside = tmp_path / "outside"
    outside.mkdir(mode=0o700)
    original_write = profile._write_temporary
    switched = False

    def change_parent(*args, **kwargs):
        nonlocal switched
        filename = original_write(*args, **kwargs)
        if not switched:
            switched = True
            profile.directory.rename(retired)
            profile.directory.symlink_to(outside, target_is_directory=True)
        return filename

    monkeypatch.setattr(profile, "_write_temporary", change_parent)
    with pytest.raises((ConfigError, StateError, OSError)):
        profile.bind(OTHER, DIGEST)
    assert not list(outside.iterdir())
    assert (retired / "configuration.json").read_bytes() == original
    assert not list(retired.glob(".configuration-*"))


def test_many_bindings_cannot_overflow_configuration_budget(profile):
    bindings = {f"{index:032x}": DIGEST for index in range(1001)}
    write_private(profile.path, {"version": 1, "workspace": None, "bindings": bindings})
    with pytest.raises(ConfigError):
        ProfileConfig(profile.directory)


def test_forget_unknown_binding_does_not_rewrite_file(profile):
    profile.bind(SESSION, DIGEST)
    before = profile.path.stat()
    profile.forget(OTHER)
    after = profile.path.stat()
    assert (before.st_ino, before.st_mtime_ns) == (after.st_ino, after.st_mtime_ns)


def test_two_instances_merge_bindings_without_lost_updates(profile):
    other = ProfileConfig(profile.directory)
    profile.bind(SESSION, DIGEST)
    other.bind(OTHER, "d" * 64)
    assert profile.binding_matches(OTHER, "d" * 64)
    assert other.binding_matches(SESSION, DIGEST)


def test_readers_invalidate_stale_settings_after_corruption(profile):
    profile.bind(SESSION, DIGEST)
    profile.path.write_text('{"invalid":true}')
    with pytest.raises(ConfigError):
        profile.binding_matches(SESSION, DIGEST)
    with pytest.raises(ConfigError):
        _ = profile.workspace


def test_workspace_identity_survives_restart(profile, tmp_path):
    folder = tmp_path.resolve() / "workspace"
    folder.mkdir(mode=0o700)
    info = folder.stat()
    identity = info.st_dev, info.st_ino
    profile.set_workspace(folder, identity=identity)
    assert profile.workspace_identity == identity
    assert ProfileConfig(profile.directory).workspace_identity == identity


def test_busy_configuration_lock_fails_closed_instead_of_overwriting(profile):
    profile.bind(SESSION, DIGEST)
    previous = profile.path.read_bytes()
    descriptor = os.open(profile.directory / "configuration.lock", os.O_RDWR)
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        with pytest.raises(ConfigError):
            profile.bind(OTHER, DIGEST)
    finally:
        os.close(descriptor)
    assert profile.path.read_bytes() == previous


@pytest.mark.parametrize("kind", ["symlink", "hardlink", "fifo", "public_mode"])
def test_unsafe_lock_cannot_be_used_to_write_configuration(profile, tmp_path, kind):
    profile.bind(SESSION, DIGEST)
    previous = profile.path.read_bytes()
    lock = profile.directory / "configuration.lock"
    lock.unlink()
    outside = tmp_path / "outside-lock"
    outside.touch(mode=0o600)
    if kind == "symlink":
        lock.symlink_to(outside)
    elif kind == "hardlink":
        os.link(outside, lock)
    elif kind == "fifo":
        os.mkfifo(lock)
    else:
        lock.touch(mode=0o644)
        lock.chmod(0o644)
    with pytest.raises(ConfigError):
        profile.bind(OTHER, DIGEST)
    assert profile.path.read_bytes() == previous


def test_postcommit_fsync_failure_reports_uncertainty_without_losing_data(profile, monkeypatch):
    profile.bind(SESSION, DIGEST)
    original = os.fsync

    def fsync(descriptor):
        if stat.S_ISDIR(os.fstat(descriptor).st_mode):
            raise OSError("synthetic directory durability failure")
        original(descriptor)

    monkeypatch.setattr(os, "fsync", fsync)
    with pytest.raises(ConfigError, match="uncertain"):
        profile.bind(OTHER, DIGEST)
    restored = ProfileConfig(profile.directory)
    assert restored.binding_matches(SESSION, DIGEST)
    assert restored.binding_matches(OTHER, DIGEST)


def test_mcp_configuration_is_private_persistent_and_does_not_lose_other_writers(profile):
    other = ProfileConfig(profile.directory)
    assert profile.server_specs == ()
    profile.set_server("notes", ("/usr/bin/python3", "/fixture/server.py"), "2025-11-25")
    other.bind(SESSION, DIGEST)
    other.set_server("second", ("/usr/bin/fixture",), "2026-07-28")
    restored = ProfileConfig(profile.directory)
    assert len(restored.server_specs) == 2
    assert restored.server_specs[0]["command"] == ("/usr/bin/python3", "/fixture/server.py")
    assert restored.binding_matches(SESSION, DIGEST)
    snapshot = restored.server_specs
    snapshot[0]["command"] = ("/untrusted/change",)
    assert restored.server_specs[0]["command"][0] == "/usr/bin/python3"
    profile.remove_server("notes")
    assert [row["server_id"] for row in other.server_specs] == ["second"]
    profile.remove_server("missing")
    assert other.binding_matches(SESSION, DIGEST)


@pytest.mark.parametrize("server_id,command,version", [
    ("../escape", ["/usr/bin/python3"], "2025-11-25"),
    ("x" * 25, ["/usr/bin/python3"], "2025-11-25"),
    ("fixture", [], "2025-11-25"),
    ("fixture", ["relative"], "2025-11-25"),
    ("fixture", ["/usr/bin/python3", "bad\0argument"], "2025-11-25"),
    ("fixture", ["/usr/bin/python3", "x" * 8193], "2025-11-25"),
    ("fixture", ["/usr/bin/python3"] * 65, "2025-11-25"),
    ("fixture", ["/usr/bin/python3"] + ["x" * 8192] * 4, "2025-11-25"),
    ("fixture", ["/usr/bin/python3"], "auto"),
    ("fixture", ["/usr/bin/python3"], []),
    ("fixture", ["/usr/bin/python3"], {}),
])
def test_invalid_mcp_settings_never_commit(profile, server_id, command, version):
    with pytest.raises(ConfigError):
        profile.set_server(server_id, tuple(command), version)
    assert profile.server_specs == ()
    assert not profile.path.exists()


def test_invalid_remote_mcp_settings_preserve_the_existing_public_configuration(profile):
    profile.set_server("kept", ("/usr/bin/fixture",), "2025-11-25")
    original = profile.path.read_bytes()
    with pytest.raises(ConfigError, match="invalid_mcp_configuration"):
        profile.set_server("remote", (), "2026-07-28", transport="streamable_http",
                           endpoint="https://example.com/mcp", oauth={
                               "resource": "https://example.com/mcp", "client_id": "own-client",
                               "scopes": [float("nan")],
                           })
    assert profile.path.read_bytes() == original
    assert [spec["server_id"] for spec in profile.server_specs] == ["kept"]


def test_mcp_server_limit_rejects_without_changing_existing_settings(profile):
    for index in range(8):
        profile.set_server(f"server{index}", ("/usr/bin/fixture",), "2025-11-25")
    previous = profile.path.read_bytes()
    with pytest.raises(ConfigError):
        profile.set_server("ninth", ("/usr/bin/fixture",), "2025-11-25")
    assert profile.path.read_bytes() == previous
