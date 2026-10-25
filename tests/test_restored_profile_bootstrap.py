"""Restored profiles never bootstrap; existing host ownership still cleans up."""

import argparse
import fcntl
import os
import stat
import sys
import tempfile
from pathlib import Path

import pytest
from test_service import ModelPeer

import jarvis_core.host_profile as host_module
import jarvis_core.service as service_module
from jarvis_core.host_profile import HostProfileError, ProfileLease
from jarvis_core.service import JarvisService


def _snapshot(root):
    """Fixture-owned metadata and bytes, no symlink following or atime assertion."""
    result = {}
    for path in [root, *sorted(root.rglob("*"))]:
        info = path.lstat()
        value = None
        if stat.S_ISREG(info.st_mode):
            value = path.read_bytes()
        elif stat.S_ISLNK(info.st_mode):
            value = os.readlink(path)
        result[str(path.relative_to(root))] = (
            info.st_dev, info.st_ino, info.st_mode, info.st_uid, info.st_nlink,
            info.st_size, info.st_mtime_ns, info.st_ctime_ns, value,
        )
    return result


def _marker(root, kind):
    path = root / "restore_record.json"
    if kind == "invalid_bytes":
        path.write_bytes(b"\xff{not JSON, never parse}")
        path.chmod(0o600)
    elif kind == "directory":
        path.mkdir(mode=0o700)
    elif kind == "dangling_symlink":
        path.symlink_to(root / "absent-target")
    elif kind == "symlink":
        target = root / "sentinel"
        target.write_bytes(b"DO NOT FOLLOW")
        target.chmod(0o600)
        path.symlink_to(target)
    elif kind == "fifo":
        os.mkfifo(path, mode=0o600)
    else:
        raise AssertionError(kind)
    return path


@pytest.mark.parametrize("kind", [
    "invalid_bytes", "directory", "dangling_symlink", "symlink", "fifo",
])
@pytest.mark.parametrize("existing_lock", [False, True])
def test_any_marker_blocks_before_lock_open_and_preserves_fixture(
    tmp_path, monkeypatch, kind, existing_lock,
):
    root = tmp_path / "profile"
    root.mkdir(mode=0o700)
    _marker(root, kind)
    if existing_lock:
        # Unknown pre-existing bytes must not even be validated/truncated.
        (root / "profile.lock").write_bytes(b"UNKNOWN LOCK CONTENT")
        (root / "profile.lock").chmod(0o600)
    before = _snapshot(root)
    real_open = os.open

    def guarded_open(path, flags, mode=0o777, *, dir_fd=None):
        if os.fspath(path) in {"profile.lock", "restore_record.json"}:
            pytest.fail("bootstrap opened a forbidden fixture file")
        return real_open(path, flags, mode, dir_fd=dir_fd)

    monkeypatch.setattr(host_module.os, "open", guarded_open)
    with pytest.raises(HostProfileError, match=r"^profile_activation_required$"):
        ProfileLease.acquire(root)
    assert _snapshot(root) == before


async def test_serve_rejects_marker_before_provider_service_or_ipc(tmp_path, monkeypatch):
    root = tmp_path / "profile"
    root.mkdir(mode=0o700)
    _marker(root, "invalid_bytes")
    before = _snapshot(root)
    calls = []

    def forbidden(*args, **kwargs):
        calls.append((args, kwargs))
        pytest.fail("restored staging reached a productive constructor")

    monkeypatch.setattr(service_module, "CodexClient", forbidden)
    monkeypatch.setattr(service_module, "JarvisService", forbidden)
    args = argparse.Namespace(state=root, codex=tmp_path / "unused",
                              provider_state=tmp_path / "provider-never-created")
    with pytest.raises(HostProfileError, match=r"^profile_activation_required$"):
        await service_module._serve(args)
    assert not calls and not args.provider_state.exists()
    assert _snapshot(root) == before


def test_marker_after_acquire_rejects_supplied_lease_before_initialize(tmp_path, monkeypatch):
    root = tmp_path / "profile"
    lease = ProfileLease.acquire(root)
    _marker(root, "invalid_bytes")
    before = _snapshot(root)
    calls = []

    def forbidden(*args, **kwargs):
        calls.append((args, kwargs))
        pytest.fail("supplied lease bypassed bootstrap admission")

    monkeypatch.setattr(JarvisService, "_initialize", forbidden)
    with pytest.raises(HostProfileError, match=r"^profile_activation_required$"):
        JarvisService(root, ModelPeer(), profile_lease=lease)
    assert not calls and _snapshot(root) == before
    assert lease._fd == -1 and lease._directory.fd == -1
    assert not (root / "workspace").exists()


def test_unreadable_marker_metadata_fails_closed_before_lock(tmp_path, monkeypatch):
    root = tmp_path / "profile"
    root.mkdir(mode=0o700)
    before = _snapshot(root)
    real_stat = os.stat

    def denied(path, *args, **kwargs):
        if os.fspath(path) == "restore_record.json" and kwargs.get("dir_fd") is not None:
            raise PermissionError("synthetic metadata denial")
        return real_stat(path, *args, **kwargs)

    monkeypatch.setattr(host_module.os, "stat", denied)
    with pytest.raises(HostProfileError, match=r"^profile_bootstrap_unavailable$"):
        ProfileLease.acquire(root)
    assert _snapshot(root) == before


@pytest.fixture
def short_root():
    directory = "/private/tmp" if sys.platform == "darwin" else tempfile.gettempdir()
    with tempfile.TemporaryDirectory(prefix="jvs-boot-", dir=directory) as folder:
        yield Path(folder).resolve()


async def test_existing_host_can_close_after_marker_without_removing_it(short_root):
    root = short_root
    peer = ModelPeer()
    service = JarvisService(root, peer)
    try:
        await service.start()
        await service.handlers()["sessions.list"]({})
        _marker(root, "invalid_bytes")
        marker_before = _snapshot(root)["restore_record.json"]
        lock_before = _snapshot(root)["profile.lock"]
        # This is bootstrap admission, not a global runtime revocation/cleanup gate.
        service.profile_lease.assert_current()
        with pytest.raises(HostProfileError, match=r"^profile_activation_required$"):
            service.profile_lease.check_bootstrap_ready()
        await service.aclose()
        assert peer.closed and service.profile_lease._fd == -1
        assert _snapshot(root)["restore_record.json"] == marker_before
        assert _snapshot(root)["profile.lock"] == lock_before
        assert not (root / "service.sock").exists()
        fd = os.open(root / "profile.lock", os.O_RDWR | os.O_NOFOLLOW)
        try:
            # Confirm the actual cooperative lock is released; presence alone is no proof.
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        finally:
            os.close(fd)
        with pytest.raises(HostProfileError, match=r"^profile_activation_required$"):
            ProfileLease.acquire(root)
    finally:
        await service.aclose()


def test_marker_does_not_weaken_existing_identity_guard(tmp_path):
    root = tmp_path / "profile"
    lease = ProfileLease.acquire(root)
    marker = _marker(root, "directory")
    saved = root / "owned-lock-saved"
    (root / "profile.lock").rename(saved)
    (root / "profile.lock").touch(mode=0o600)
    try:
        with pytest.raises(HostProfileError, match=r"^profile_lock_changed$"):
            lease.check_bootstrap_ready()
        with pytest.raises(HostProfileError, match=r"^profile_lock_changed$"):
            lease.close()
        assert lease._fd >= 0 and marker.is_dir()
    finally:
        (root / "profile.lock").unlink()
        saved.rename(root / "profile.lock")
        lease.close()
