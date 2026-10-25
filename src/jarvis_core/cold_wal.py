"""Own bounded recovery of a quiescent Jarvis WAL family on private copies.

SQLite never opens the source. Every physical member (including SHM) is pinned
and read twice; only main and WAL are copied. A strict independent WAL-format
check rejects incomplete, corrupt or older-generation tails. Complete valid
uncommitted frames are retained in the private copy: SQLite's own reader selects
the last committed state. No source checkpoint, repair, retry or WAL truncation.

The caller must keep its profile cut until this function and any retained owner
settle. Deadlines cannot interrupt a blocked OS/native call. The admitted byte
budgets are payload/staging limits, not a claim about total interpreter RSS.
"""

from __future__ import annotations

import errno
import hashlib
import os
import sqlite3
import stat
import struct
import sys
import threading
from pathlib import Path
from uuid import uuid4

from jarvis_core.recovery_fs import FileIdentity, private_file
from jarvis_core.recovery_sqlite import deny_extension_loading, inspect_sqlite_snapshot

_NAMES = {"state": "jarvis.sqlite3", "memory": "memory.sqlite3", "schedules": "schedules.sqlite3"}
_SUFFIXES = ("", "-wal", "-shm", "-journal")
_TEMP_ROOT = Path("/private/tmp" if sys.platform == "darwin" else "/tmp")
_BLOCK = 65_536
_MASK = (1 << 32) - 1


class ColdWalError(ValueError):
    """Public content-free failure and optional exact, still-owned resources."""

    def __init__(self, code, owner=None, *, cleanup_error_code=None):
        super().__init__(code)
        self.code, self.owner = code, owner
        self.cleanup_error_code = cleanup_error_code


def _fingerprint(info):
    return (info.st_dev, info.st_ino, info.st_uid, stat.S_IMODE(info.st_mode), info.st_nlink,
            info.st_size, info.st_mtime_ns, info.st_ctime_ns)


class _DirectoryPin:
    """Constructor performs no I/O: custody is registered before open()."""

    def __init__(self, path, *, expected=None, shared_temp=False):
        self.path, self.expected, self.shared_temp = Path(path), expected, shared_temp
        self.handles = []
        self.fd = -1
        self.identity = None
        self.uncertain_closes = set()

    def open(self):
        if (not self.path.is_absolute() or ".." in self.path.parts
                or len(self.path.parts) > 64 or self.handles):
            raise ColdWalError("profile_cold_wal_unavailable")
        for index, name in enumerate(self.path.parts):
            parent = None if index == 0 else self.handles[-1][1]
            fd = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent)
            # Keep the handle even if the following fstat/check fails.
            self.handles.append((name, fd, None))
            info = os.fstat(fd)
            self.handles[-1] = name, fd, FileIdentity.of(info)
        self.fd = self.handles[-1][1]
        self.identity = FileIdentity.of(os.fstat(self.fd))
        if self.expected is not None and self.identity != self.expected:
            raise ColdWalError("profile_cold_wal_changed")
        self.check()

    def check(self):
        if self.fd < 0 or not self.handles:
            raise ColdWalError("profile_cold_wal_changed")
        for (_, parent, _), (name, child, identity) in zip(
            self.handles, self.handles[1:], strict=False
        ):
            info = os.stat(name, dir_fd=parent, follow_symlinks=False)
            if (not stat.S_ISDIR(info.st_mode) or FileIdentity.of(info) != identity
                    or FileIdentity.of(os.fstat(child)) != identity):
                raise ColdWalError("profile_cold_wal_changed")
        info = os.fstat(self.fd)
        if FileIdentity.of(info) != self.identity:
            raise ColdWalError("profile_cold_wal_changed")
        if self.shared_temp:
            if (info.st_uid not in {0, os.getuid()} or not stat.S_ISDIR(info.st_mode)
                    or (stat.S_IMODE(info.st_mode) & 0o022 and not info.st_mode & stat.S_ISVTX)):
                raise ColdWalError("profile_cold_wal_unavailable")
        elif info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) != 0o700:
            raise ColdWalError("profile_cold_wal_unavailable")

    def close(self):
        # Pop only after close returns; a failed close keeps its exact handle.
        while self.handles:
            _, fd, identity = self.handles[-1]
            try:
                _close_checked(fd, identity, fd in self.uncertain_closes)
            except BaseException:
                self.uncertain_closes.add(fd)
                raise
            if fd == self.fd:
                self.fd = -1
            self.handles.pop()


class _FileHandle:
    def __init__(self, fd, identity=None):
        self.fd, self.identity = fd, identity
        self.close_uncertain = False

    def close(self):
        if self.fd >= 0:
            try:
                if self.identity is None and not self.close_uncertain:
                    self.identity = FileIdentity.of(os.fstat(self.fd))
                _close_checked(self.fd, self.identity, self.close_uncertain)
            except BaseException:
                self.close_uncertain = True
                raise
            self.fd = -1


def _close_checked(fd, identity, uncertain):
    try:
        actual = FileIdentity.of(os.fstat(fd))
    except OSError as error:
        if error.errno == errno.EBADF:
            # Independent observation of absence, not a retry of close().
            return
        raise
    if (identity is not None and actual != identity) or uncertain:
        # Even the same inode might have been reopened at this number after a
        # close with lost acknowledgement. Do not close an ambiguous capability.
        raise ColdWalError("profile_cold_wal_cleanup_pending")
    os.close(fd)


def _close_connection(owner, attribute):
    connection = owner.connections.get(attribute)
    if connection is not None:
        connection.close()
        del owner.connections[attribute]


def _unlink_owned(owner, name):
    expected = owner.owned_files[name]
    try:
        info = os.stat(name, dir_fd=owner.root.fd, follow_symlinks=False)
    except FileNotFoundError:
        return
    private_file(info, owner.maximum)
    if FileIdentity.of(info) != expected:
        raise ColdWalError("profile_cold_wal_cleanup_pending")
    owner.root.check()
    os.unlink(name, dir_fd=owner.root.fd)


class WalCaptureOwner:
    """Retained authority over one private staging directory and native handles.

    cleanup() neither invokes a previous context/approval nor captures again.
    It can run in a later worker: native connections are thread-transferable and
    access is serialized. A failure retains the owner and remaining handles.
    """

    def __init__(self, family, maximum):
        self.family, self.maximum = family, maximum
        self.name = "jarvis-cold-wal-" + uuid4().hex
        self.parent = _DirectoryPin(_TEMP_ROOT, shared_temp=True)
        self.profile = _DirectoryPin(family.root, expected=family.parent_identity)
        self.root = None
        self.root_path = _TEMP_ROOT / self.name
        self.root_identity = None
        self.created = False
        self.creation_unknown = False
        self.root_removed = False
        self.removal_synced = False
        self.closed = False
        self.source_handles = {}
        self.stage_handles = {}
        self.owned_files = {}
        self.file_attempts = set()
        self.native_open_attempted = False
        self.live_image_bytes = 0
        self.connections = {}
        self._lock = threading.RLock()

    def open_profile(self):
        self.profile.open()
        for index, suffix in enumerate(_SUFFIXES):
            if self.family.members[index] is None:
                continue
            fd = os.open(_NAMES[self.family.role] + suffix,
                         os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK,
                         dir_fd=self.profile.fd)
            handle = _FileHandle(fd)
            self.source_handles[suffix] = handle
            info = os.fstat(fd)
            handle.identity = FileIdentity.of(info)
            private_file(info, self.maximum)
            if _fingerprint(info) != self.family.members[index]:
                raise ColdWalError("profile_cold_wal_changed")

    def allocate(self):
        self.parent.open()
        self.parent.check()
        self.creation_unknown = True
        try:
            os.mkdir(self.name, 0o700, dir_fd=self.parent.fd)
        except FileExistsError:
            # Kernel reports another entry, never an owned staging directory.
            self.creation_unknown = False
            raise
        self.created = True
        self.creation_unknown = False
        self.root_identity = FileIdentity.of(
            os.stat(self.name, dir_fd=self.parent.fd, follow_symlinks=False))
        self.root = _DirectoryPin(self.root_path, expected=self.root_identity)
        self.root.open()

    def create_file(self, name):
        self.root.check()
        self.file_attempts.add(name)
        try:
            fd = os.open(name, os.O_RDWR | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                         0o600, dir_fd=self.root.fd)
        except FileExistsError:
            self.file_attempts.discard(name)
            raise
        handle = _FileHandle(fd)
        self.stage_handles[name] = handle
        info = os.fstat(fd)
        handle.identity = FileIdentity.of(info)
        self.owned_files[name] = handle.identity
        private_file(info, self.maximum)
        return fd

    def note_native(self):
        if not self.created or self.root_removed:
            return
        if self.root is None or self.root.fd < 0:
            raise ColdWalError("profile_cold_wal_cleanup_pending")
        self.root.check()
        names = set()
        with os.scandir(self.root.fd) as entries:
            for entry in entries:
                names.add(entry.name)
                if len(names) > 4:
                    raise ColdWalError("profile_cold_wal_cleanup_pending")
        if names - {"database.sqlite3" + suffix for suffix in _SUFFIXES}:
            raise ColdWalError("profile_cold_wal_cleanup_pending")
        total = 0
        for name in names:
            info = os.stat(name, dir_fd=self.root.fd, follow_symlinks=False)
            private_file(info, self.maximum)
            total += info.st_size
            identity = FileIdentity.of(info)
            expected = self.owned_files.get(name)
            manual = name in {"database.sqlite3", "database.sqlite3-wal"}
            if manual and expected is None:
                handle = self.stage_handles.get(name)
                if handle is not None and handle.fd >= 0 and not handle.close_uncertain:
                    # A returned, never-closed descriptor is a retained
                    # capability. Recover its identity from that FD, never
                    # merely from the pathname after an initial fstat failure.
                    opened = os.fstat(handle.fd)
                    private_file(opened, self.maximum)
                    current = FileIdentity.of(opened)
                    if handle.identity is not None and current != handle.identity:
                        raise ColdWalError("profile_cold_wal_cleanup_pending")
                    handle.identity = current
                    expected = current
            if ((manual and expected is None)
                    or (not manual and not self.native_open_attempted)
                    or (expected is not None and expected != identity)):
                raise ColdWalError("profile_cold_wal_cleanup_pending")
            self.owned_files[name] = identity
        if any(name not in self.owned_files for name in self.file_attempts):
            # A successful open without a captured descriptor identity, or an
            # ambiguous creation error, never becomes pathname-only ownership.
            raise ColdWalError("profile_cold_wal_cleanup_pending")
        if total + self.live_image_bytes > self.maximum:
            raise ColdWalError("profile_cold_wal_size_limit")
        self.root.check()

    def cleanup(self):
        with self._lock:
            if self.closed:
                return
            # Record native sidecars before close can remove them. Never drop
            # an open connection merely because path inspection failed.
            note_error = None
            try:
                self.note_native()
            except BaseException as error:
                note_error = error
            for attribute in tuple(self.connections):
                _close_connection(self, attribute)
            if note_error is not None:
                raise note_error
            for handle in (*self.source_handles.values(), *self.stage_handles.values()):
                handle.close()
            self.profile.close()
            if self.creation_unknown:
                # Do not adopt or remove a possible mkdir effect after an
                # unknown acknowledgement; only confirmed absence can settle.
                self.parent.check()
                try:
                    os.stat(self.name, dir_fd=self.parent.fd, follow_symlinks=False)
                except FileNotFoundError:
                    self.creation_unknown = False
                else:
                    raise ColdWalError("profile_cold_wal_cleanup_pending")
            if self.created and not self.root_removed:
                self.note_native()
                for name in self.owned_files:
                    _unlink_owned(self, name)
                self.parent.check()
                named = os.stat(self.name, dir_fd=self.parent.fd, follow_symlinks=False)
                if FileIdentity.of(named) != self.root_identity:
                    raise ColdWalError("profile_cold_wal_cleanup_pending")
                os.rmdir(self.name, dir_fd=self.parent.fd)
                self.root_removed = True
                self.root.close()
            elif self.root is not None:
                self.root.close()
            if self.created and not self.removal_synced:
                self.parent.check()
                os.fsync(self.parent.fd)
                self.removal_synced = True
            self.parent.close()
            self.closed = True


def _read_and_hash(owner, suffix, checkpoint, target=None):
    from jarvis_core.cold_sqlite import _metadata

    index = _SUFFIXES.index(suffix)
    handle = owner.source_handles[suffix]
    expected = owner.family.members[index]
    result = hashlib.sha256()
    position = 0
    while position < expected[5]:
        checkpoint("cold_wal_copy" if target is not None else "cold_wal_verify_source")
        if _metadata(os.fstat(handle.fd)) != expected:
            raise ColdWalError("profile_cold_wal_changed")
        data = os.pread(handle.fd, min(_BLOCK, expected[5] - position), position)
        if not data:
            raise ColdWalError("profile_cold_wal_changed")
        result.update(data)
        if target is not None:
            written = 0
            while written < len(data):
                checkpoint("cold_wal_copy")
                count = os.write(target, data[written:])
                if count <= 0:
                    raise ColdWalError("profile_cold_wal_unavailable")
                written += count
        position += len(data)
    if _metadata(os.fstat(handle.fd)) != expected:
        raise ColdWalError("profile_cold_wal_changed")
    checkpoint("cold_wal_verify_source")
    return result.digest()


def _checksum(data, state, endian):
    first, second = state
    for left, right in struct.iter_unpack(endian + "II", data):
        first = (first + left + second) & _MASK
        second = (second + right + first) & _MASK
    return first, second


def _hash_stage(owner, name, checkpoint):
    handle = owner.stage_handles[name]
    owner.root.check()
    info = os.fstat(handle.fd)
    private_file(info, owner.maximum)
    if FileIdentity.of(info) != owner.owned_files[name]:
        raise ColdWalError("profile_cold_wal_changed")
    sha, position = hashlib.sha256(), 0
    while position < info.st_size:
        checkpoint("cold_wal_verify_copy")
        raw = os.pread(handle.fd, min(_BLOCK, info.st_size - position), position)
        if not raw:
            raise ColdWalError("profile_cold_wal_changed")
        sha.update(raw)
        position += len(raw)
    named = os.stat(name, dir_fd=owner.root.fd, follow_symlinks=False)
    if (_fingerprint(os.fstat(handle.fd)) != _fingerprint(info)
            or _fingerprint(named) != _fingerprint(info)):
        raise ColdWalError("profile_cold_wal_changed")
    owner.root.check()
    return sha.digest()


def _validate_wal(fd, wal_bytes, main_header, main_bytes, limits, checkpoint):
    """Validate ALL physical frames; never truncate at a convenient good prefix.

    Format/checksum definitions: sqlite.org/fileformat.html#walformat. This is
    an own strict preflight, not a replacement SQLite recovery implementation.
    """
    header = os.pread(fd, 32, 0)
    if (len(header) != 32 or wal_bytes < 32 or len(main_header) != 100
            or main_header[:16] != b"SQLite format 3\0" or main_header[18:20] != b"\2\2"):
        raise ColdWalError("profile_cold_wal_invalid")
    words = struct.unpack(">8I", header)
    magic, version, page_size = words[:3]
    main_size = int.from_bytes(main_header[16:18], "big")
    main_size = 65536 if main_size == 1 else main_size
    if (magic not in {0x377F0682, 0x377F0683} or version != 3_007_000
            or page_size < 512 or page_size > 65_536 or page_size & (page_size - 1)
            or page_size != main_size or main_bytes < page_size or main_bytes % page_size
            or (wal_bytes - 32) % (page_size + 24)):
        raise ColdWalError("profile_cold_wal_invalid")
    frame_count = (wal_bytes - 32) // (page_size + 24)
    if (frame_count > limits.max_rows or (wal_bytes + 7) // 8 > limits.max_vm_steps):
        raise ColdWalError("profile_cold_wal_work_limit")
    endian = "<" if magic == 0x377F0682 else ">"
    checksum = _checksum(header[:24], (0, 0), endian)
    if checksum != words[6:8]:
        raise ColdWalError("profile_cold_wal_invalid")
    maximum_pages, committed_pages = limits.max_sqlite_bytes // page_size, 0
    salts = words[4:6]
    for index in range(frame_count):
        checkpoint("cold_wal_validate")
        frame = os.pread(fd, page_size + 24, 32 + index * (page_size + 24))
        if len(frame) != page_size + 24:
            raise ColdWalError("profile_cold_wal_invalid")
        page, commit, salt_one, salt_two, check_one, check_two = struct.unpack(">6I", frame[:24])
        if page == 0 or (salt_one, salt_two) != salts:
            raise ColdWalError("profile_cold_wal_invalid")
        if page > maximum_pages or commit > maximum_pages:
            raise ColdWalError("profile_cold_wal_size_limit")
        checksum = _checksum(frame[:8], checksum, endian)
        checksum = _checksum(memoryview(frame)[24:], checksum, endian)
        if checksum != (check_one, check_two):
            raise ColdWalError("profile_cold_wal_invalid")
        if commit:
            committed_pages = commit
    if not committed_pages:
        raise ColdWalError("profile_cold_wal_no_commit")
    return committed_pages, page_size


def _capture(owner, context, limits, check_current):
    family = owner.family

    def checkpoint(stage):
        context.checkpoint(stage)
        check_current()
        family.assert_current()

    checkpoint("cold_wal_preflight")
    if (family.role not in _NAMES or family.members[0] is None or family.members[1] is None
            or family.members[3] is not None or family.header[18:20] != b"\2\2"
            or family.members[1][5] <= 32):
        raise ColdWalError("profile_cold_wal_unavailable")
    family_bytes = sum(row[5] for row in family.members if row is not None)
    if family_bytes > min(limits.max_sqlite_bytes, limits.max_total_bytes):
        raise ColdWalError("profile_cold_wal_size_limit")
    checkpoint("cold_wal_open_source")
    owner.open_profile()
    checkpoint("cold_wal_staging")
    owner.allocate()
    source_hashes = {}
    for suffix in ("", "-wal"):
        target = owner.create_file("database.sqlite3" + suffix)
        source_hashes[suffix] = _read_and_hash(owner, suffix, checkpoint, target)
        os.fsync(target)
    if family.members[2] is not None:
        source_hashes["-shm"] = _read_and_hash(owner, "-shm", checkpoint)
    for suffix, expected in source_hashes.items():
        if _read_and_hash(owner, suffix, checkpoint) != expected:
            raise ColdWalError("profile_cold_wal_changed")
        if suffix != "-shm" and _hash_stage(
            owner, "database.sqlite3" + suffix, checkpoint
        ) != expected:
            raise ColdWalError("profile_cold_wal_changed")
    wal_fd = owner.stage_handles["database.sqlite3-wal"].fd
    committed_pages, page_size = _validate_wal(
        wal_fd, family.members[1][5], family.header, family.members[0][5], limits, checkpoint)
    logical_bytes = committed_pages * page_size
    frames = (family.members[1][5] - 32) // (page_size + 24)
    # A WAL-index region is 32 KiB; the conservative extra region and divisor
    # leave room for SQLite's header/hash overhead. Validate actual bytes too.
    shm_reserve = 32_768 * (2 + frames // 4000)
    if family_bytes + logical_bytes + shm_reserve > limits.max_total_bytes:
        raise ColdWalError("profile_cold_wal_size_limit")
    owner.live_image_bytes = logical_bytes
    owner.note_native()
    checkpoint("cold_wal_open_private")
    owner.root.check()
    owner.native_open_attempted = True
    source = sqlite3.connect((owner.root_path / "database.sqlite3").as_uri() + "?mode=ro",
                             uri=True, isolation_level=None, check_same_thread=False, timeout=0)
    owner.connections["source"] = source
    owner.note_native()
    deny_extension_loading(source)
    source.execute("PRAGMA trusted_schema=OFF")
    source.execute("PRAGMA query_only=ON")
    source.execute("PRAGMA temp_store=MEMORY")
    source.execute("PRAGMA busy_timeout=0")
    source.execute("PRAGMA cache_size=-2048")
    source.execute("PRAGMA mmap_size=0")
    source.execute("BEGIN")
    source.execute("PRAGMA schema_version").fetchone()
    if (source.execute("PRAGMA journal_mode").fetchone()[0] != "wal"
            or source.execute("PRAGMA page_size").fetchone()[0] != page_size
            or source.execute("PRAGMA page_count").fetchone()[0] != committed_pages):
        raise ColdWalError("profile_cold_wal_invalid")
    ram = sqlite3.connect(":memory:", isolation_level=None, check_same_thread=False)
    owner.connections["ram"] = ram
    deny_extension_loading(ram)
    ram.execute("PRAGMA trusted_schema=OFF")
    ram.execute("PRAGMA temp_store=MEMORY")
    ram.execute(f"PRAGMA page_size={page_size}")
    ram.execute(f"PRAGMA max_page_count={limits.max_sqlite_bytes // page_size}")

    def progress(status, remaining, total):
        checkpoint("cold_wal_backup")
        owner.note_native()
        if status not in {sqlite3.SQLITE_OK, sqlite3.SQLITE_DONE}:
            raise ColdWalError("profile_cold_wal_sqlite_unavailable")
        if total * page_size > limits.max_sqlite_bytes or remaining > total:
            raise ColdWalError("profile_cold_wal_size_limit")

    checkpoint("cold_wal_backup")
    source.backup(ram, pages=128, progress=progress, sleep=0)
    checkpoint("cold_wal_backup")
    data = ram.serialize()
    if len(data) != logical_bytes or len(data) > limits.max_sqlite_bytes:
        raise ColdWalError("profile_cold_wal_size_limit")
    source.execute("ROLLBACK")
    owner.note_native()
    _close_connection(owner, "ram")
    _close_connection(owner, "source")
    # Only SQLite's online-backup output is normalized, never a raw source.
    if data[18:20] == b"\2\2":
        data = data[:18] + b"\1\1" + data[20:]
    checkpoint("cold_wal_inspect")
    inspect_sqlite_snapshot(family.role, data,
                            check_current=lambda: checkpoint("cold_wal_inspect"),
                            limits=limits.sqlite())
    for suffix, expected in source_hashes.items():
        if _read_and_hash(owner, suffix, checkpoint) != expected:
            raise ColdWalError("profile_cold_wal_changed")
    checkpoint("cold_wal_complete")
    return data, hashlib.sha256(data).hexdigest()


def read_wal_image(family, context, limits, check_current):
    """Return a qualified image only after native/staging cleanup has settled."""
    owner = WalCaptureOwner(family, limits.max_total_bytes)
    result, failure = None, None
    try:
        result = _capture(owner, context, limits, check_current)
    except BaseException as error:
        failure = error
    try:
        owner.cleanup()
    except BaseException as cleanup:
        code = getattr(failure, "code", "profile_cold_wal_cleanup_pending")
        raise ColdWalError(code, owner,
                           cleanup_error_code=getattr(cleanup, "code",
                                                      "profile_cold_wal_cleanup_pending")) from None
    if failure is not None:
        if isinstance(failure, (sqlite3.Error, OSError)):
            raise ColdWalError(getattr(failure, "code", "profile_cold_wal_sqlite_unavailable"))
        raise failure
    return result
