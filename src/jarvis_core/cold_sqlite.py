"""Pinned cold families; DELETE reads and explicitly isolated WAL recovery.

Only fixed Jarvis roles and their four-member families are inspected. Physical
checks include absent sidecars: adding one invalidates the captured review.
"""

from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass
from pathlib import Path

from jarvis_core.recovery_fs import Directory, FileIdentity, PinnedFile, private_file
from jarvis_core.recovery_sqlite import inspect_sqlite_snapshot

_NAMES = {"state": "jarvis.sqlite3", "memory": "memory.sqlite3", "schedules": "schedules.sqlite3"}
_SUFFIXES = ("", "-wal", "-shm", "-journal")


class ColdSqliteError(ValueError):
    def __init__(self, code):
        super().__init__(code)
        self.code = code


def _metadata(info):
    private_file(info, (1 << 63) - 1)
    return (info.st_dev, info.st_ino, info.st_uid, info.st_mode & 0o7777, info.st_nlink,
            info.st_size, info.st_mtime_ns, info.st_ctime_ns)


def _members(parent, name):
    result = []
    for suffix in _SUFFIXES:
        try:
            info = os.stat(name + suffix, dir_fd=parent.fd, follow_symlinks=False)
        except FileNotFoundError:
            result.append(None)
        else:
            result.append(_metadata(info))
    return tuple(result)


@dataclass(frozen=True)
class ColdFamily:
    root: Path
    role: str
    parent_identity: FileIdentity
    members: tuple
    header: bytes
    journal_header: bytes = b""

    @property
    def eligible(self):
        return (self.members[0] is not None and not any(self.members[1:])
                and len(self.header) == 100 and self.header.startswith(b"SQLite format 3\0")
                and self.header[18:20] == b"\x01\x01")

    @property
    def wal_eligible(self):
        # Metadata-only candidate classification. Frames, commits and content
        # are validated in the worker before any native private reader opens.
        return (self.members[0] is not None and self.members[3] is None
                and len(self.header) == 100 and self.header.startswith(b"SQLite format 3\0")
                and self.header[18:20] == b"\x02\x02")

    @property
    def journal_eligible(self):
        return (self.members[0] is not None and self.members[3] is not None
                and self.members[1] is None and self.members[2] is None
                and len(self.header) == 100 and self.header.startswith(b"SQLite format 3\0")
                and self.header[18:20] == b"\x01\x01")

    @property
    def capture_mode(self):
        if self.eligible:
            return "cold_delete"
        if self.wal_eligible:
            member = self.members[1]
            return ("cold_wal_main_only" if member is None else
                    "cold_wal_empty" if member[5] == 0 else
                    "cold_wal_header" if member[5] == 32 else "cold_wal")
        if self.journal_eligible:
            if self.journal_header.startswith(b"\xd9\xd5\x05\xf9\x20\xa1\x63\xd7"):
                return "cold_journal_hot"
            if self.members[3][5] == 0 or self.journal_header == bytes(28):
                return "cold_journal_inactive"
            return "cold_journal"
        return "unavailable"

    @property
    def fingerprint(self):
        import json
        raw = [self.role, [self.parent_identity.device, self.parent_identity.inode],
               self.members, self.header.hex(), self.journal_header.hex()]
        return hashlib.sha256(json.dumps(raw, separators=(",", ":")).encode()).hexdigest()

    @property
    def snapshot_eligible(self):
        return self.eligible or self.wal_eligible or self.journal_eligible

    @property
    def physical_bytes(self):
        return sum(member[5] for member in self.members if member is not None)

    def capture_image(self, context, limits, check_current):
        if self.eligible:
            return self.read_image(context, limits, check_current)
        if self.wal_eligible:
            from jarvis_core.cold_wal import read_wal_image
            return read_wal_image(self, context, limits, check_current)
        if self.journal_eligible:
            from jarvis_core.cold_journal import read_journal_image
            return read_journal_image(self, context, limits, check_current)
        raise ColdSqliteError("profile_cold_sqlite_unavailable")

    def assert_current(self):
        with Directory(self.root, expected=self.parent_identity) as parent:
            if _members(parent, _NAMES[self.role]) != self.members:
                raise ColdSqliteError("profile_sqlite_family_changed")
            parent.check()

    def read_image(self, context, limits, check_current):
        if not self.eligible:
            raise ColdSqliteError("profile_cold_sqlite_unavailable")

        def checkpoint():
            context.checkpoint("cold_sqlite_read")
            check_current()
            self.assert_current()

        checkpoint()
        with Directory(self.root, expected=self.parent_identity) as parent:
            with PinnedFile(parent, _NAMES[self.role], limits.max_sqlite_bytes) as source:
                if _metadata(source.info) != self.members[0]:
                    raise ColdSqliteError("profile_sqlite_family_changed")
                checkpoint()
                data = source.read(checkpoint)
                source.check()
                checkpoint()
        # Validate the immutable RAM image; never patch a cold WAL header.
        inspect_sqlite_snapshot(self.role, data, check_current=checkpoint, limits=limits.sqlite())
        checkpoint()
        return data, hashlib.sha256(data).hexdigest()


def inspect_cold_family(path: Path, role: str) -> ColdFamily:
    if role not in _NAMES or path.name != _NAMES[role]:
        raise ColdSqliteError("profile_sqlite_role_invalid")
    with Directory(path.parent) as parent:
        members = _members(parent, path.name)
        header, journal_header = b"", b""
        if members[0] is None:
            if any(members[1:]):
                raise ColdSqliteError("profile_sqlite_family_incomplete")
        else:
            # Review work is bounded to metadata and one hundred bytes, never a
            # complete image or SQLite constructor on the owning event loop.
            with PinnedFile(parent, path.name, (1 << 63) - 1) as source:
                if _metadata(source.info) != members[0]:
                    raise ColdSqliteError("profile_sqlite_family_changed")
                header = os.pread(source.fd, 100, 0)
                source.check()
            if _members(parent, path.name) != members:
                raise ColdSqliteError("profile_sqlite_family_changed")
            if members[3] is not None:
                with PinnedFile(parent, path.name + "-journal", (1 << 63) - 1) as journal:
                    if _metadata(journal.info) != members[3]:
                        raise ColdSqliteError("profile_sqlite_family_changed")
                    journal_header = os.pread(journal.fd, 28, 0)
                    journal.check()
                if _members(parent, path.name) != members:
                    raise ColdSqliteError("profile_sqlite_family_changed")
        parent.check()
        return ColdFamily(path.parent, role, parent.identity, members, header, journal_header)
