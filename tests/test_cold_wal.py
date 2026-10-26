"""Real stopped SQLite WAL families; approvals, accounts and owner data absent."""

import hashlib
import os
import sqlite3
import stat
import struct
import time
from dataclasses import replace
from uuid import uuid4

import pytest
from wal_fixture import NAMES, TABLES, make_wal, oracle

from jarvis_core import cold_wal
from jarvis_core.cold_sqlite import inspect_cold_family
from jarvis_core.cold_wal import ColdWalError, WalCaptureOwner, read_wal_image
from jarvis_core.recovery import RecoveryContext, RecoveryLimits
from jarvis_core.tools import HostCancellation


@pytest.fixture
def scratch(tmp_path, monkeypatch):
    path = tmp_path / "scratch"
    path.mkdir(mode=0o700)
    monkeypatch.setattr(cold_wal, "_TEMP_ROOT", path)
    return path


def capture(root, role, *, limits=None, callback=None, cancellation=None):
    family = inspect_cold_family(root / NAMES[role], role)
    cancellation = cancellation or HostCancellation()

    def guard(stage, write=False):
        assert not write
        if callback:
            callback(stage)

    context = RecoveryContext(uuid4().hex, "wal-fixture", 0, guard,
                              cancellation, time.monotonic() + 20)
    return read_wal_image(family, context, limits or RecoveryLimits(), family.assert_current)


def title(data, role):
    with sqlite3.connect(":memory:") as db:
        db.deserialize(data)
        return db.execute("SELECT title FROM " + TABLES[role]).fetchone()[0]


@pytest.mark.parametrize("role", NAMES)
@pytest.mark.parametrize("with_shm", [False, True])
def test_recovers_last_of_three_real_commits_without_opening_original(
    tmp_path, scratch, monkeypatch, role, with_shm,
):
    root = tmp_path / "profile"
    markers = make_wal(root, (role,), shm=with_shm)
    assert markers[role]["committed"].encode() not in (root / NAMES[role]).read_bytes()
    before = oracle(root)
    connection, opened = sqlite3.connect, []

    def only_private(path, *args, **kwargs):
        value = str(path)
        assert value == ":memory:" or value.startswith(scratch.as_uri() + "/jarvis-cold-wal-")
        opened.append(value)
        return connection(path, *args, **kwargs)

    monkeypatch.setattr(sqlite3, "connect", only_private)
    data, digest = capture(root, role)
    assert data[18:20] == b"\1\1"
    assert digest == hashlib.sha256(data).hexdigest()
    assert title(data, role) == markers[role]["committed"]
    assert oracle(root) == before
    assert list(scratch.iterdir()) == []
    assert len([x for x in opened if x != ":memory:"]) == 1


def checksum(raw, state, endian):
    """Independent integer-at-offset format oracle, not a product helper."""
    left, right = state
    for offset in range(0, len(raw), 8):
        left = (left + int.from_bytes(raw[offset:offset + 4], endian) + right) % (2**32)
        right = (right + int.from_bytes(raw[offset + 4:offset + 8], endian) + left) % (2**32)
    return left, right


def rewrite_checksums(raw, *, big=False, commits=True):
    raw = bytearray(raw)
    raw[:4] = (0x377F0683 if big else 0x377F0682).to_bytes(4, "big")
    order = "big" if big else "little"
    pair = checksum(raw[:24], (0, 0), order)
    raw[24:32] = struct.pack(">2I", *pair)
    page_size = int.from_bytes(raw[8:12], "big")
    for start in range(32, len(raw), page_size + 24):
        if not commits:
            raw[start + 4:start + 8] = b"\0" * 4
        pair = checksum(raw[start:start + 8], pair, order)
        pair = checksum(raw[start + 24:start + 24 + page_size], pair, order)
        raw[start + 16:start + 24] = struct.pack(">2I", *pair)
    return bytes(raw)


@pytest.mark.parametrize("big", [False, True])
def test_both_documented_checksum_byte_orders_are_recovered_by_sqlite(
    tmp_path, scratch, big,
):
    root = tmp_path / "profile"
    markers = make_wal(root, ("state",))
    wal = root / (NAMES["state"] + "-wal")
    wal.write_bytes(rewrite_checksums(wal.read_bytes(), big=big))
    before = oracle(root)
    data, _ = capture(root, "state")
    assert title(data, "state") == markers["state"]["committed"]
    assert oracle(root) == before and list(scratch.iterdir()) == []


@pytest.mark.parametrize("role", NAMES)
def test_complete_valid_uncommitted_tail_is_not_replayed(tmp_path, scratch, role):
    root = tmp_path / "profile"
    markers = make_wal(root, (role,))
    wal = root / (NAMES[role] + "-wal")
    raw = wal.read_bytes()
    page_size = int.from_bytes(raw[8:12], "big")
    marker = markers[role]["committed"].encode()
    frames = [raw[x:x + 24 + page_size] for x in range(32, len(raw), page_size + 24)]
    frame = bytearray(next(x for x in reversed(frames) if marker in x))
    replacement = b"X" * len(marker)
    frame[24:] = frame[24:].replace(marker, replacement)
    frame[4:8] = b"\0" * 4
    extended = raw + frame
    extended = rewrite_checksums(extended, big=int.from_bytes(raw[:4], "big") == 0x377F0683)
    wal.write_bytes(extended)
    before = oracle(root)
    data, _ = capture(root, role)
    assert title(data, role) == markers[role]["committed"]
    assert replacement not in data
    assert oracle(root) == before and list(scratch.iterdir()) == []


@pytest.mark.parametrize("mutation", ["magic", "version", "page_size", "header_checksum",
                                     "frame_salt", "payload_checksum", "truncated", "old_tail"])
def test_corrupt_or_ambiguous_wal_is_rejected_without_prefix_success(
    tmp_path, scratch, mutation,
):
    root = tmp_path / "profile"
    make_wal(root, ("state",))
    wal = root / (NAMES["state"] + "-wal")
    raw = bytearray(wal.read_bytes())
    offsets = {"magic": 0, "version": 4, "page_size": 8, "header_checksum": 24,
               "frame_salt": 40, "payload_checksum": 32 + 24 + 20}
    if mutation in offsets:
        raw[offsets[mutation]] ^= 0x40
    elif mutation == "truncated":
        raw = raw[:-1]
    else:
        frame = bytearray(raw[-(int.from_bytes(raw[8:12], "big") + 24):])
        frame[8] ^= 0x40
        raw += frame
    wal.write_bytes(raw)
    before = oracle(root)
    with pytest.raises(ColdWalError, match="profile_cold_wal_invalid") as error:
        capture(root, "state")
    assert error.value.owner is None
    assert oracle(root) == before and list(scratch.iterdir()) == []


def test_valid_wal_without_any_commit_uses_only_main_state(tmp_path, scratch):
    root = tmp_path / "profile"
    markers = make_wal(root, ("state",))
    wal = root / (NAMES["state"] + "-wal")
    wal.write_bytes(rewrite_checksums(wal.read_bytes(), commits=False))
    before = oracle(root)
    data, _ = capture(root, "state")
    assert title(data, "state") == markers["state"]["base"]
    assert oracle(root) == before and list(scratch.iterdir()) == []


@pytest.mark.parametrize("suffix,payload", [("-journal", b""), ("-journal", b"journal")])
def test_journal_is_never_recovered_or_deleted(tmp_path, scratch, suffix, payload):
    root = tmp_path / "profile"
    make_wal(root, ("state",))
    path = root / (NAMES["state"] + suffix)
    path.write_bytes(payload)
    path.chmod(0o600)
    before = oracle(root)
    with pytest.raises(ColdWalError, match="profile_cold_wal_unavailable"):
        capture(root, "state")
    assert oracle(root) == before and list(scratch.iterdir()) == []


@pytest.mark.parametrize("stage", ["cold_wal_preflight", "cold_wal_open_source", "cold_wal_staging",
                                   "cold_wal_copy", "cold_wal_verify_source",
                                   "cold_wal_verify_copy",
                                   "cold_wal_validate", "cold_wal_open_private", "cold_wal_backup",
                                   "cold_wal_inspect", "cold_wal_complete"])
def test_cancellation_at_each_boundary_settles_before_return(tmp_path, scratch, stage):
    root = tmp_path / "profile"
    make_wal(root, ("state",))
    before, cancellation = oracle(root), HostCancellation()

    def cancel(current):
        if current == stage:
            cancellation.set()

    with pytest.raises(Exception, match="recovery_cancelled") as error:
        capture(root, "state", callback=cancel, cancellation=cancellation)
    assert getattr(error.value, "owner", None) is None
    assert oracle(root) == before and list(scratch.iterdir()) == []


@pytest.mark.parametrize("which", ["family", "logical", "work"])
def test_family_logical_expansion_and_work_have_independent_bounds(tmp_path, scratch, which):
    root = tmp_path / "profile"
    make_wal(root, ("state",))
    before = oracle(root)
    if which == "family":
        limits = replace(RecoveryLimits(), max_sqlite_bytes=4096)
    elif which == "logical":
        wal = root / (NAMES["state"] + "-wal")
        raw = bytearray(wal.read_bytes())
        page_size = int.from_bytes(raw[8:12], "big")
        raw[-(24 + page_size) + 4:-(24 + page_size) + 8] = (1_000_000).to_bytes(4, "big")
        wal.write_bytes(rewrite_checksums(raw))
        before = oracle(root)
        limits = RecoveryLimits()
    else:
        limits = replace(RecoveryLimits(), max_vm_steps=1)
    with pytest.raises(ColdWalError, match=r"profile_cold_wal_(size|work)_limit"):
        capture(root, "state", limits=limits)
    assert oracle(root) == before and list(scratch.iterdir()) == []


@pytest.mark.parametrize("kind", ["native_close", "unlink", "parent_sync"])
def test_cleanup_failure_retains_exact_owner_then_only_cleans_same_resources(
    tmp_path, scratch, monkeypatch, kind,
):
    root = tmp_path / "profile"
    make_wal(root, ("state",))
    before, allow, checkpoints = oracle(root), False, []
    if kind == "native_close":
        original = cold_wal._close_connection

        def fail(owner, attribute):
            if attribute == "source" and not allow:
                raise OSError("synthetic close failure")
            return original(owner, attribute)

        monkeypatch.setattr(cold_wal, "_close_connection", fail)
    elif kind == "unlink":
        original = cold_wal._unlink_owned

        def fail(owner, name):
            if name == "database.sqlite3" and not allow:
                raise OSError("synthetic unlink failure")
            return original(owner, name)

        monkeypatch.setattr(cold_wal, "_unlink_owned", fail)
    else:
        original = os.fsync

        def fail(fd):
            info = os.fstat(fd)
            if stat.S_ISDIR(info.st_mode) and info.st_ino == scratch.stat().st_ino and not allow:
                raise OSError("synthetic sync failure")
            return original(fd)

        monkeypatch.setattr(os, "fsync", fail)
    with pytest.raises(ColdWalError) as error:
        capture(root, "state", callback=checkpoints.append)
    owner = error.value.owner
    assert type(owner) is WalCaptureOwner and not owner.closed
    if kind == "native_close":
        assert "source" in owner.connections
    if kind == "parent_sync":
        assert owner.root_removed and not owner.removal_synced
    previous = tuple(checkpoints)
    allow = True
    owner.cleanup()
    owner.cleanup()
    assert owner.closed and tuple(checkpoints) == previous
    assert oracle(root) == before and list(scratch.iterdir()) == []


def test_replaced_staging_file_is_never_unlinked_as_owned(tmp_path, scratch, monkeypatch):
    root = tmp_path / "profile"
    make_wal(root, ("state",))
    before = oracle(root)
    original, changed, saved = cold_wal._unlink_owned, False, None

    def replace_before_unlink(owner, name):
        nonlocal changed, saved
        if name == "database.sqlite3" and not changed:
            changed = True
            path = owner.root_path / name
            saved = path.read_bytes(), path.stat().st_ino
            path.rename(owner.root_path / "original")
            path.write_bytes(b"FOREIGN SYNTHETIC FILE")
            path.chmod(0o600)
        return original(owner, name)

    monkeypatch.setattr(cold_wal, "_unlink_owned", replace_before_unlink)
    with pytest.raises(ColdWalError) as error:
        capture(root, "state")
    owner = error.value.owner
    assert owner is not None and not owner.closed
    path = owner.root_path / "database.sqlite3"
    assert path.read_bytes() == b"FOREIGN SYNTHETIC FILE"
    # The fixture, not cleanup, restores its own substituted resource.
    path.unlink()
    (owner.root_path / "original").rename(path)
    assert path.stat().st_ino == saved[1]
    owner.cleanup()
    assert owner.closed and oracle(root) == before and list(scratch.iterdir()) == []


def test_mkdir_with_unknown_acknowledgement_retains_owner_and_never_adopts_path(
    tmp_path, scratch, monkeypatch,
):
    root = tmp_path / "profile"
    make_wal(root, ("state",))
    before, original = oracle(root), os.mkdir

    def mkdir_then_fail(name, *args, **kwargs):
        original(name, *args, **kwargs)
        if str(name).startswith("jarvis-cold-wal-"):
            raise OSError("synthetic lost mkdir acknowledgement")

    monkeypatch.setattr(os, "mkdir", mkdir_then_fail)
    with pytest.raises(ColdWalError) as error:
        capture(root, "state")
    owner = error.value.owner
    assert owner is not None and owner.creation_unknown and not owner.created
    assert owner.root_path.is_dir() and not owner.closed
    with pytest.raises(ColdWalError, match="profile_cold_wal_cleanup_pending"):
        owner.cleanup()
    assert owner.root_path.is_dir() and oracle(root) == before
    # Only the test knows that its injected mkdir created this empty path.
    owner.root_path.rmdir()
    owner.cleanup()
    assert owner.closed and list(scratch.iterdir()) == []


def test_lost_close_ack_never_closes_a_reused_foreign_descriptor(tmp_path, monkeypatch):
    original_path, foreign_path = tmp_path / "own", tmp_path / "foreign"
    original_path.write_bytes(b"OWN")
    foreign_path.write_bytes(b"FOREIGN")
    original = os.close
    fd = os.open(original_path, os.O_RDONLY)
    handle = cold_wal._FileHandle(fd, cold_wal.FileIdentity.of(os.fstat(fd)))
    other = None

    def close_then_fail(value):
        if value == fd:
            original(value)
            raise OSError("synthetic lost close acknowledgement")
        return original(value)

    monkeypatch.setattr(os, "close", close_then_fail)
    with pytest.raises(OSError):
        handle.close()
    other = os.open(foreign_path, os.O_RDONLY)
    assert other == fd
    with pytest.raises(ColdWalError, match="profile_cold_wal_cleanup_pending"):
        handle.close()
    assert os.read(other, 7) == b"FOREIGN"
    original(other)
    # Independent EBADF can retire the stale number, without issuing close.
    handle.close()
    assert handle.fd == -1


def test_native_shm_and_ram_reserve_are_checked_before_sqlite_open(
    tmp_path, scratch, monkeypatch,
):
    root = tmp_path / "profile"
    make_wal(root, ("state",), shm=False)
    before = oracle(root)
    family = inspect_cold_family(root / NAMES["state"], "state")
    wal = (root / (NAMES["state"] + "-wal")).read_bytes()
    page_size = int.from_bytes(wal[8:12], "big")
    committed = int.from_bytes(wal[-(24 + page_size) + 4:-(24 + page_size) + 8], "big")
    physical = sum(row[5] for row in family.members if row)
    limits = replace(RecoveryLimits(), max_total_bytes=physical + committed * page_size + 32_768)

    def forbidden(*args, **kwargs):
        pytest.fail("native open occurred without space for its reconstructed WAL-index")

    monkeypatch.setattr(sqlite3, "connect", forbidden)
    with pytest.raises(ColdWalError, match="profile_cold_wal_size_limit"):
        capture(root, "state", limits=limits)
    assert oracle(root) == before and list(scratch.iterdir()) == []


def test_directory_close_with_lost_ack_preserves_reused_foreign_fd(tmp_path, monkeypatch):
    pin = cold_wal._DirectoryPin(tmp_path)
    pin.open()
    original, fd = os.close, pin.fd
    foreign = tmp_path / "foreign"
    foreign.write_bytes(b"FOREIGN DIRECTORY FD TEST")

    def close_then_fail(value):
        if value == fd:
            original(value)
            raise OSError("synthetic lost directory close acknowledgement")
        return original(value)

    monkeypatch.setattr(os, "close", close_then_fail)
    with pytest.raises(OSError):
        pin.close()
    other = os.open(foreign, os.O_RDONLY)
    assert other == fd
    with pytest.raises(ColdWalError, match="profile_cold_wal_cleanup_pending"):
        pin.close()
    assert os.read(other, 7) == b"FOREIGN"
    original(other)
    pin.close()
    assert pin.fd == -1 and pin.handles == []


def test_failed_descriptor_stat_cannot_adopt_a_replacement_by_path(
    tmp_path, scratch, monkeypatch,
):
    root = tmp_path / "profile"
    make_wal(root, ("state",))
    before = oracle(root)
    original_open, original_stat = os.open, os.fstat
    stage_fd, failed, saved = None, False, None

    def track_open(name, flags, *args, **kwargs):
        nonlocal stage_fd
        fd = original_open(name, flags, *args, **kwargs)
        if name == "database.sqlite3" and flags & os.O_CREAT:
            stage_fd = fd
        return fd

    def fail_stat(fd):
        nonlocal failed, saved
        if fd == stage_fd and not failed:
            failed = True
            folder, = scratch.iterdir()
            path = folder / "database.sqlite3"
            saved = folder / "original-held-by-fixture"
            path.rename(saved)
            path.write_bytes(b"FOREIGN STAT FAILURE TEST")
            path.chmod(0o600)
            raise OSError("synthetic initial fstat failure")
        return original_stat(fd)

    monkeypatch.setattr(os, "open", track_open)
    monkeypatch.setattr(os, "fstat", fail_stat)
    with pytest.raises(ColdWalError) as error:
        capture(root, "state")
    owner = error.value.owner
    assert owner is not None and not owner.closed
    path = owner.root_path / "database.sqlite3"
    assert path.read_bytes() == b"FOREIGN STAT FAILURE TEST"
    assert "database.sqlite3" not in owner.owned_files
    # Fixture restores its own actual FD-bound object. A later cleanup may
    # establish that descriptor's identity; it never adopts the foreign path.
    path.unlink()
    saved.rename(path)
    owner.cleanup()
    assert owner.closed and oracle(root) == before and list(scratch.iterdir()) == []


@pytest.mark.parametrize("variant", ["absent", "empty", "header"])
@pytest.mark.parametrize("role", NAMES)
def test_checkpointed_main_without_frames_has_valid_private_image(
    tmp_path, scratch, role, variant,
):
    root = tmp_path / "profile"
    markers = make_wal(root, (role,))
    wal = root / (NAMES[role] + "-wal")
    valid_header = wal.read_bytes()[:32]
    # Fixture preparation on our synthetic database, before the cold cut.
    with sqlite3.connect(root / NAMES[role]) as connection:
        connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    connection.close()
    for suffix in ("-wal", "-shm"):
        (root / (NAMES[role] + suffix)).unlink(missing_ok=True)
    if variant != "absent":
        wal.write_bytes(b"" if variant == "empty" else valid_header)
        wal.chmod(0o600)
    family = inspect_cold_family(root / NAMES[role], role)
    assert not family.eligible
    assert family.capture_mode == {"absent": "cold_wal_main_only", "empty": "cold_wal_empty",
                                   "header": "cold_wal_header"}[variant]
    before = oracle(root)
    data, _ = capture(root, role)
    assert title(data, role) == markers[role]["committed"]
    assert oracle(root) == before and list(scratch.iterdir()) == []
