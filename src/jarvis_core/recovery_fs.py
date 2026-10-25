"""Descriptor-confined files owned by one recovery operation; no profile discovery."""

from __future__ import annotations

import ctypes
import errno
import hashlib
import os
import re
import stat
import sys
from dataclasses import dataclass
from pathlib import Path


class RecoveryFsError(OSError):
    def __init__(self, code, *, uncertain=False, cleanup_error_code=None):
        super().__init__(code)
        self.code = code
        self.uncertain = uncertain
        self.cleanup_error_code = cleanup_error_code


@dataclass(frozen=True)
class FileIdentity:
    device: int
    inode: int

    @classmethod
    def of(cls, info):
        return cls(info.st_dev, info.st_ino)


def basename(value):
    if not isinstance(value, str) or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,95}", value) is None:
        raise RecoveryFsError("recovery_name_invalid")
    return value


class Directory:
    """Pins every ancestor, plus the selected final directory identity.

    Metadata may be checked for ancestors; their contents are never enumerated.
    A retargeted path is not followed or silently repaired.
    """

    def __init__(self, path, *, private=True, expected=None):
        path = Path(path)
        if not path.is_absolute() or ".." in path.parts or len(path.parts) > 64:
            raise RecoveryFsError("recovery_path_invalid")
        self.private = private
        self.path, self._fds, self._names, self.fd = path, [], [], -1
        try:
            self._fds.append(os.open(path.anchor, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW))
            for part in path.parts[1:]:
                self._names.append(part)
                self._fds.append(
                    os.open(
                        part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=self._fds[-1]
                    )
                )
            self.fd = self._fds[-1]
            info = os.fstat(self.fd)
            if (
                info.st_uid != os.getuid()
                or stat.S_IMODE(info.st_mode) & 0o022
                or (private and stat.S_IMODE(info.st_mode) != 0o700)
            ):
                raise RecoveryFsError("recovery_directory_unsafe")
            self.identity = FileIdentity.of(info)
            if expected is not None and self.identity != expected:
                raise RecoveryFsError("recovery_identity_changed")
            self.check()
        except BaseException:
            self.close()
            raise

    def check(self):
        if self.fd < 0:
            raise RecoveryFsError("recovery_handle_closed")
        for parent, name, child in zip(self._fds, self._names, self._fds[1:], strict=False):
            named = os.stat(name, dir_fd=parent, follow_symlinks=False)
            opened = os.fstat(child)
            if not stat.S_ISDIR(named.st_mode) or FileIdentity.of(named) != FileIdentity.of(opened):
                raise RecoveryFsError("recovery_identity_changed")
        info = os.fstat(self.fd)
        if (
            FileIdentity.of(info) != self.identity
            or info.st_uid != os.getuid()
            or stat.S_IMODE(info.st_mode) & 0o022
            or (self.private and stat.S_IMODE(info.st_mode) != 0o700)
        ):
            raise RecoveryFsError("recovery_identity_changed")

    def close(self):
        while self._fds:
            os.close(self._fds.pop())
        self.fd = -1

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()


def directory_entries(directory):
    directory.check()
    names = set()
    with os.scandir(directory.fd) as rows:
        for row in rows:
            names.add(row.name)
            if len(names) > 16:
                raise RecoveryFsError("recovery_artifact_entries_limit")
    directory.check()
    return names


def _fingerprint(info):
    return (
        info.st_dev,
        info.st_ino,
        info.st_size,
        info.st_mtime_ns,
        info.st_ctime_ns,
        info.st_mode,
        info.st_uid,
        info.st_nlink,
    )


def private_file(info, maximum):
    if (
        not stat.S_ISREG(info.st_mode)
        or info.st_uid != os.getuid()
        or stat.S_IMODE(info.st_mode) != 0o600
        or info.st_nlink != 1
        or info.st_size > maximum
    ):
        raise RecoveryFsError("recovery_file_unsafe")


class PinnedFile:
    def __init__(self, directory, name, maximum):
        if "/" in name or name in {"", ".", ".."}:
            raise RecoveryFsError("recovery_name_invalid")
        self.parent, self.name, self.fd = directory, name, -1
        try:
            directory.check()
            self.fd = os.open(
                name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory.fd
            )
            self.info = os.fstat(self.fd)
            private_file(self.info, maximum)
            self.identity = FileIdentity.of(self.info)
            self.check()
        except BaseException:
            self.close()
            raise

    def check(self):
        self.parent.check()
        named = os.stat(self.name, dir_fd=self.parent.fd, follow_symlinks=False)
        if _fingerprint(os.fstat(self.fd)) != _fingerprint(self.info) or _fingerprint(
            named
        ) != _fingerprint(self.info):
            raise RecoveryFsError("recovery_identity_changed")

    def read(self, checkpoint):
        data = bytearray()
        self.check()
        while len(data) < self.info.st_size:
            checkpoint()
            chunk = os.pread(self.fd, min(65_536, self.info.st_size - len(data)), len(data))
            if not chunk:
                raise RecoveryFsError("recovery_identity_changed")
            data.extend(chunk)
        self.check()
        checkpoint()
        return bytes(data)

    def close(self):
        if self.fd >= 0:
            os.close(self.fd)
            self.fd = -1

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()


class OwnedStage:
    """Only delete entries created by this operation and still matching identity."""

    def __init__(self, parent, operation_id, checkpoint):
        self.parent, self.name = parent, ".jarvis-stage-" + operation_id
        self._owned_files, self._owned_dirs = {}, {}
        self.root = None
        self.publication_attempted = False
        parent.check()
        checkpoint()
        parent.check()
        os.mkdir(self.name, 0o700, dir_fd=parent.fd)
        self.identity = FileIdentity.of(os.stat(self.name, dir_fd=parent.fd, follow_symlinks=False))
        try:
            self.root = Directory(parent.path / self.name, expected=self.identity)
        except BaseException as error:
            # Constructor has no files yet; retain it if its identity changed.
            try:
                named = os.stat(self.name, dir_fd=parent.fd, follow_symlinks=False)
                if FileIdentity.of(named) != self.identity:
                    raise RecoveryFsError("recovery_cleanup_identity_changed")
                os.rmdir(self.name, dir_fd=parent.fd)
                os.fsync(parent.fd)
            except BaseException:
                raise RecoveryFsError(
                    getattr(error, "code", "recovery_stage_initialization_failed"),
                    uncertain=True,
                    cleanup_error_code="recovery_stage_cleanup_incomplete",
                ) from None
            raise

    def directory(self, name):
        if name not in {"data", "profiles", "telegram"}:
            raise RecoveryFsError("recovery_name_invalid")
        self.root.check()
        if name not in self._owned_dirs:
            os.mkdir(name, 0o700, dir_fd=self.root.fd)
            info = os.stat(name, dir_fd=self.root.fd, follow_symlinks=False)
            self._owned_dirs[name] = FileIdentity.of(info)
        return Directory(self.root.path / name, expected=self._owned_dirs[name])

    def write(self, directory, name, data, checkpoint):
        self.root.check()
        directory.check()
        checkpoint()
        self.root.check()
        directory.check()
        fd = os.open(
            name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=directory.fd
        )
        key = (directory.path.relative_to(self.root.path).as_posix(), name)
        self._owned_files[key] = FileIdentity.of(os.fstat(fd))
        try:
            position = 0
            while position < len(data):
                checkpoint()
                wrote = os.write(fd, data[position : position + 65_536])
                if wrote <= 0:
                    raise RecoveryFsError("recovery_write_failed")
                position += wrote
            os.fsync(fd)
        finally:
            os.close(fd)
        directory.check()
        return self._owned_files[key]

    def note_sqlite(self, directory, name, expected):
        # SQLite may create sidecars only in the operation's private directory.
        # Record each exact identity, never recursively delete an arbitrary path.
        for suffix in ("", "-journal", "-wal", "-shm"):
            filename = name + suffix
            try:
                info = os.stat(filename, dir_fd=directory.fd, follow_symlinks=False)
            except FileNotFoundError:
                continue
            private_file(info, 128 * 1024 * 1024)
            if not suffix and FileIdentity.of(info) != expected:
                raise RecoveryFsError("recovery_identity_changed")
            self._owned_files[(directory.path.relative_to(self.root.path).as_posix(), filename)] = (
                FileIdentity.of(info)
            )

    def cleanup(self):
        self.parent.check()
        self.root.check()
        named = os.stat(self.name, dir_fd=self.parent.fd, follow_symlinks=False)
        if FileIdentity.of(named) != self.identity:
            raise RecoveryFsError("recovery_cleanup_identity_changed")
        for (subdir, name), identity in list(self._owned_files.items())[::-1]:
            directory = self.root if subdir == "." else self.directory(subdir)
            try:
                try:
                    info = os.stat(name, dir_fd=directory.fd, follow_symlinks=False)
                except FileNotFoundError:
                    continue
                if FileIdentity.of(info) != identity:
                    raise RecoveryFsError("recovery_cleanup_identity_changed")
                os.unlink(name, dir_fd=directory.fd)
            finally:
                if directory is not self.root:
                    directory.close()
        for name, identity in list(self._owned_dirs.items())[::-1]:
            info = os.stat(name, dir_fd=self.root.fd, follow_symlinks=False)
            if FileIdentity.of(info) != identity:
                raise RecoveryFsError("recovery_cleanup_identity_changed")
            os.rmdir(name, dir_fd=self.root.fd)
        self.root.close()
        os.rmdir(self.name, dir_fd=self.parent.fd)
        os.fsync(self.parent.fd)

    def close(self):
        if self.root is not None:
            self.root.close()


def no_replace(parent, old_name, new_name):
    """One native exclusive rename, with no racy check-then-overwrite fallback."""
    parent.check()
    library = ctypes.CDLL(None, use_errno=True)
    if sys.platform == "darwin":
        function, flag = getattr(library, "renameatx_np", None), 0x00000004  # RENAME_EXCL
    elif sys.platform.startswith("linux"):
        function, flag = getattr(library, "renameat2", None), 1  # RENAME_NOREPLACE
    else:
        function, flag = None, 0
    if function is None:
        raise RecoveryFsError("recovery_exclusive_publish_unavailable")
    function.argtypes = [
        ctypes.c_int,
        ctypes.c_char_p,
        ctypes.c_int,
        ctypes.c_char_p,
        ctypes.c_uint,
    ]
    function.restype = ctypes.c_int
    if function(parent.fd, os.fsencode(old_name), parent.fd, os.fsencode(new_name), flag):
        error = ctypes.get_errno()
        if error in {errno.EEXIST, errno.ENOTEMPTY}:
            raise RecoveryFsError("recovery_destination_exists")
        if error in {errno.ENOSYS, errno.ENOTSUP, errno.EINVAL}:
            raise RecoveryFsError("recovery_exclusive_publish_unavailable")
        raise RecoveryFsError("recovery_publish_failed")


def digest(data):
    return hashlib.sha256(data).hexdigest()
