"""Small descriptor-confined JSON transactions for explicit private registries.

Only host-selected directories and fixed filenames are accepted. There is no HOME
discovery, credential loading, file migration or retry of an uncertain commit.
"""

from __future__ import annotations

import fcntl
import json
import os
import re
import stat
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path
from uuid import uuid4

from jarvis_core.state import StateError, ensure_private_directory


class PrivateJsonError(ValueError):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def _object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise PrivateJsonError("registry_duplicate_key")
        result[key] = value
    return result


class PrivateJsonDocument:
    def __init__(
        self,
        directory: Path,
        name: str,
        *,
        initial: Callable[[], dict],
        validate: Callable[[dict], None],
        max_bytes: int = 1048576,
    ) -> None:
        if (
            not isinstance(name, str)
            or re.fullmatch(r"[a-z][a-z0-9_]{0,40}\.json", name) is None
            or type(max_bytes) is not int
            or not 1024 <= max_bytes <= 4194304
        ):
            raise PrivateJsonError("registry_configuration_invalid")
        self.directory = ensure_private_directory(directory)
        self.name = name
        self.path = self.directory / name
        self.lock_name = name + ".lock"
        self.initial, self.validate, self.max_bytes = initial, validate, max_bytes
        info = self.directory.stat()
        self.identity = info.st_dev, info.st_ino
        self._has_seen_file = False
        self.read()

    def _check_parent(self, parent: int) -> None:
        ensure_private_directory(self.directory)
        current, opened = self.directory.lstat(), os.fstat(parent)
        if (current.st_dev, current.st_ino) != self.identity or (
            opened.st_dev,
            opened.st_ino,
        ) != self.identity:
            raise PrivateJsonError("registry_directory_changed")

    @contextmanager
    def _parent(self) -> Iterator[int]:
        parent = None
        try:
            ensure_private_directory(self.directory)
            parent = os.open(self.directory.anchor, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
            for component in self.directory.parts[1:]:
                child = os.open(
                    component, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent
                )
                os.close(parent)
                parent = child
        except (OSError, StateError):
            if parent is not None:
                os.close(parent)
            raise PrivateJsonError("registry_directory_unavailable") from None
        try:
            self._check_parent(parent)
            yield parent
        finally:
            os.close(parent)

    @staticmethod
    def _regular(descriptor: int, max_bytes: int) -> None:
        info = os.fstat(descriptor)
        if (
            not stat.S_ISREG(info.st_mode)
            or info.st_uid != os.getuid()
            or stat.S_IMODE(info.st_mode) != 0o600
            or info.st_nlink != 1
            or info.st_size > max_bytes
        ):
            raise PrivateJsonError("registry_file_unsafe")

    def _load(self, parent: int) -> dict:
        self._check_parent(parent)
        try:
            descriptor = os.open(
                self.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent
            )
        except FileNotFoundError:
            if self._has_seen_file:
                raise PrivateJsonError("registry_file_missing") from None
            value = self.initial()
            self.validate(value)
            return value
        except OSError:
            raise PrivateJsonError("registry_file_unsafe") from None
        try:
            self._regular(descriptor, self.max_bytes)
            raw = bytearray()
            while part := os.read(descriptor, min(8192, self.max_bytes + 1 - len(raw))):
                raw.extend(part)
                if len(raw) > self.max_bytes:
                    raise PrivateJsonError("registry_size_limit")
            value = json.loads(raw, object_pairs_hook=_object)
            self.validate(value)
            self._has_seen_file = True
            return value
        except (json.JSONDecodeError, UnicodeError, RecursionError):
            raise PrivateJsonError("registry_json_invalid") from None
        finally:
            os.close(descriptor)

    def read(self) -> dict:
        try:
            with self._parent() as parent:
                return self._load(parent)
        except OSError:
            raise PrivateJsonError("registry_unavailable") from None

    def _write_temporary(self, parent: int, data: bytes) -> str:
        name = ".registry-" + uuid4().hex
        descriptor = os.open(
            name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=parent
        )
        try:
            os.fchmod(descriptor, 0o600)
            remaining = memoryview(data)
            while remaining:
                written = os.write(descriptor, remaining)
                if written <= 0:
                    raise OSError("registry_short_write")
                remaining = remaining[written:]
            os.fsync(descriptor)
        except BaseException:
            os.unlink(name, dir_fd=parent)
            raise
        finally:
            os.close(descriptor)
        return name

    def update(self, change: Callable[[dict], dict]) -> dict:
        try:
            with self._parent() as parent:
                try:
                    lock = os.open(
                        self.lock_name,
                        os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_NONBLOCK,
                        0o600,
                        dir_fd=parent,
                    )
                except OSError:
                    raise PrivateJsonError("registry_lock_unsafe") from None
                try:
                    self._regular(lock, 0)
                    try:
                        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    except BlockingIOError:
                        raise PrivateJsonError("registry_busy") from None
                    current = self._load(parent)
                    # The callback receives its own fresh snapshot, never an
                    # object also used for the final external-change comparison.
                    value = change(json.loads(json.dumps(current)))
                    self.validate(value)
                    if value == current:
                        return value
                    data = json.dumps(
                        value,
                        ensure_ascii=False,
                        sort_keys=True,
                        separators=(",", ":"),
                        allow_nan=False,
                    ).encode()
                    if len(data) > self.max_bytes:
                        raise PrivateJsonError("registry_size_limit")
                    name = self._write_temporary(parent, data)
                    try:
                        if self._load(parent) != current:
                            raise PrivateJsonError("registry_changed")
                        self._check_parent(parent)
                        lock_path = os.stat(self.lock_name, dir_fd=parent, follow_symlinks=False)
                        lock_open = os.fstat(lock)
                        if (lock_path.st_dev, lock_path.st_ino) != (
                            lock_open.st_dev,
                            lock_open.st_ino,
                        ):
                            raise PrivateJsonError("registry_lock_changed")
                        os.replace(name, self.name, src_dir_fd=parent, dst_dir_fd=parent)
                        self._has_seen_file = True
                        try:
                            os.fsync(parent)
                        except OSError:
                            raise PrivateJsonError("registry_durability_uncertain") from None
                    finally:
                        try:
                            os.unlink(name, dir_fd=parent)
                        except FileNotFoundError:
                            pass
                    return value
                finally:
                    os.close(lock)
        except (OSError, UnicodeError):
            raise PrivateJsonError("registry_unavailable") from None
