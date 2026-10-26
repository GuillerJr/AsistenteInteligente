"""Small descriptor-confined JSON transactions for explicit private registries.

Only host-selected directories and fixed filenames are accepted. There is no HOME
discovery, credential loading, file migration or retry of an uncertain commit.
"""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
import re
import stat
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4

from jarvis_core.state import StateError, ensure_private_directory


class PrivateJsonError(ValueError):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class PrivateJsonStaging:
    document: object
    name: str
    identity: tuple


class PrivateJsonPendingOwner:
    """Exact staging/descriptor custody; cleanup never publishes or replays."""

    def __init__(self, document, parent=None):
        self.document, self.parent = document, parent
        self.name, self.identity = None, None
        self.descriptors = {}
        self.lock = None
        self.runner = None
        self.staging_descriptor = None
        self.closed = False
        if parent is not None:
            self.own(parent)

    def own(self, descriptor):
        # Register acquisition before its first fallible observation. A later
        # close failure keeps the pin, so a reused descriptor is never closed
        # merely because it once had this integer value.
        self.descriptors[descriptor] = None
        info = os.fstat(descriptor)
        self.descriptors[descriptor] = (info.st_dev, info.st_ino, info.st_mode, info.st_uid)
        return descriptor

    def close_descriptor(self, descriptor):
        if descriptor not in self.descriptors:
            return
        try:
            info = os.fstat(descriptor)
        except OSError as error:
            if error.errno == 9:
                self.descriptors.pop(descriptor)
                return
            raise
        actual = (info.st_dev, info.st_ino, info.st_mode, info.st_uid)
        if self.descriptors[descriptor] is None:
            # This newly acquired descriptor has never had a close attempted.
            self.descriptors[descriptor] = actual
        if actual != self.descriptors[descriptor]:
            raise PrivateJsonError("registry_cleanup_descriptor_changed")
        os.close(descriptor)
        self.descriptors.pop(descriptor)

    def staging(self, data):
        self.document._check_parent(self.parent)
        descriptor = self.own(os.open(
            self.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=self.parent))
        try:
            self.document._regular(descriptor, self.document.max_bytes)
            opened = os.fstat(descriptor)
            identity = (opened.st_dev, opened.st_ino, stat.S_IMODE(opened.st_mode),
                        opened.st_uid, opened.st_nlink, opened.st_size)
            if identity[:5] != self.identity or opened.st_size != len(data):
                raise PrivateJsonError("registry_staging_changed")
            raw = bytearray()
            while part := os.read(descriptor, min(8192, len(data) + 1 - len(raw))):
                raw.extend(part)
                if len(raw) > len(data):
                    raise PrivateJsonError("registry_staging_changed")
            if hashlib.sha256(raw).digest() != hashlib.sha256(data).digest():
                raise PrivateJsonError("registry_staging_changed")
            linked = os.stat(self.name, dir_fd=self.parent, follow_symlinks=False)
            if (linked.st_dev, linked.st_ino, stat.S_IMODE(linked.st_mode), linked.st_uid,
                    linked.st_nlink, linked.st_size) != identity:
                raise PrivateJsonError("registry_staging_changed")
            return PrivateJsonStaging(self.document, self.name, identity)
        finally:
            self.close_descriptor(descriptor)

    def cleanup(self):
        if self.closed:
            return
        if self.name is not None:
            self.document._check_parent(self.parent)
            if self.identity is None:
                # Preserve the O_EXCL result through a first-fstat failure.
                # Derive its pin only while that newly acquired fd is still ours.
                descriptor = self.staging_descriptor
                if descriptor not in self.descriptors:
                    raise PrivateJsonError("registry_cleanup_staging_unobserved")
                info = os.fstat(descriptor)
                self.document._regular(descriptor, self.document.max_bytes)
                self.identity = (info.st_dev, info.st_ino, stat.S_IMODE(info.st_mode),
                                 info.st_uid, info.st_nlink)
            try:
                info = os.stat(self.name, dir_fd=self.parent, follow_symlinks=False)
            except FileNotFoundError:
                self.name = None
            else:
                current = (info.st_dev, info.st_ino, stat.S_IMODE(info.st_mode),
                           info.st_uid, info.st_nlink)
                if current != self.identity:
                    raise PrivateJsonError("registry_cleanup_staging_changed")
                os.unlink(self.name, dir_fd=self.parent)
                self.name = None
        # The parent and configuration flock remain held until every other
        # owned descriptor really closes. Repeated cleanup cannot republish.
        for descriptor in tuple(self.descriptors):
            if descriptor not in {self.parent, self.lock, self.runner}:
                self.close_descriptor(descriptor)
        if self.lock is not None:
            self.close_descriptor(self.lock)
            self.lock = None
        if self.runner is not None:
            self.close_descriptor(self.runner)
            self.runner = None
        self.close_descriptor(self.parent)
        self.closed = True


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

    def _open_parent(self, *, owner=None) -> int:
        if owner is not None:
            # The reviewed owner exists before opening even the first ancestor.
            # Keep each acquired directory until its close actually returns.
            ensure_private_directory(self.directory)
            parent = owner.own(os.open(
                self.directory.anchor, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW))
            for component in self.directory.parts[1:]:
                child = owner.own(os.open(
                    component, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent))
                owner.close_descriptor(parent)
                parent = child
            self._check_parent(parent)
            owner.parent = parent
            return parent
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
            return parent
        except BaseException:
            os.close(parent)
            raise

    @contextmanager
    def _parent(self) -> Iterator[int]:
        parent = self._open_parent()
        try:
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

    def _load(self, parent: int, *, owner=None) -> dict:
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
        if owner is not None:
            owner.own(descriptor)
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
            if owner is not None:
                owner.close_descriptor(descriptor)
            else:
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
        owner = getattr(self, "_reviewed_owner", None)
        if owner is not None and owner.parent != parent:
            owner = None
        if owner is not None:
            owner.name = name
            owner.staging_descriptor = descriptor
            owner.own(descriptor)
            info = os.fstat(descriptor)
            owner.identity = (info.st_dev, info.st_ino, stat.S_IMODE(info.st_mode),
                              info.st_uid, info.st_nlink)
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
            if owner is None:
                os.unlink(name, dir_fd=parent)
            raise
        finally:
            if owner is not None:
                owner.close_descriptor(descriptor)
            else:
                os.close(descriptor)
        return name

    def _reviewed_update(self, change, before_publish, owned_descriptors, runner_lock_name):
        """Reviewed path retains the exact owner when cleanup cannot settle."""
        owner = PrivateJsonPendingOwner(self)
        failure = None
        result = None
        try:
            # Transfer every externally acquired descriptor before observing any
            # of them. Failure on the first must not strand the remaining lease.
            owner.descriptors.update((descriptor, None) for descriptor in owned_descriptors)
            for descriptor in owned_descriptors:
                owner.own(descriptor)
            self._open_parent(owner=owner)
            if runner_lock_name is not None:
                owner.runner = owner.own(os.open(
                    runner_lock_name, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_NONBLOCK,
                    0o600, dir_fd=owner.parent))
                self._regular(owner.runner, 0)
                try:
                    fcntl.flock(owner.runner, fcntl.LOCK_EX | fcntl.LOCK_NB)
                except BlockingIOError:
                    raise PrivateJsonError("registry_runner_busy") from None
            owner.lock = owner.own(os.open(
                self.lock_name, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_NONBLOCK,
                0o600, dir_fd=owner.parent))
            self._regular(owner.lock, 0)
            try:
                fcntl.flock(owner.lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise PrivateJsonError("registry_busy") from None
            self._reviewed_owner = owner
            current = self._load(owner.parent, owner=owner)
            result = change(json.loads(json.dumps(current)))
            self.validate(result)
            if result != current:
                data = json.dumps(result, ensure_ascii=False, sort_keys=True,
                                  separators=(",", ":"), allow_nan=False).encode()
                if len(data) > self.max_bytes:
                    raise PrivateJsonError("registry_size_limit")
                self._write_temporary(owner.parent, data)
                if self._load(owner.parent, owner=owner) != current:
                    raise PrivateJsonError("registry_changed")
                self._check_parent(owner.parent)
                for lock_name, descriptor in (
                        (self.lock_name, owner.lock), (runner_lock_name, owner.runner)):
                    if descriptor is None:
                        continue
                    lock_path = os.stat(lock_name, dir_fd=owner.parent, follow_symlinks=False)
                    self._regular(descriptor, 0)
                    lock_open = os.fstat(descriptor)
                    if ((lock_path.st_dev, lock_path.st_ino)
                            != (lock_open.st_dev, lock_open.st_ino)):
                        raise PrivateJsonError("registry_lock_changed")
                staging = owner.staging(data)
                before_publish(staging)
                # Re-read staged bytes after the host callback too. A guard
                # cannot accidentally authorize a replaced staging inode/body.
                if owner.staging(data) != staging:
                    raise PrivateJsonError("registry_staging_changed")
                before_publish(staging)
                os.replace(owner.name, self.name, src_dir_fd=owner.parent, dst_dir_fd=owner.parent)
                self._has_seen_file = True
                try:
                    os.fsync(owner.parent)
                except OSError:
                    raise PrivateJsonError("registry_durability_uncertain") from None
        except BaseException as error:
            failure = error
        finally:
            if getattr(self, "_reviewed_owner", None) is owner:
                self._reviewed_owner = None
            try:
                owner.cleanup()
            except BaseException:
                pending = PrivateJsonError("registry_cleanup_pending")
                pending.owner = owner
                pending.original_error = failure
                failure = pending
        if failure is not None:
            if isinstance(failure, (OSError, UnicodeError)):
                failure = PrivateJsonError("registry_unavailable")
            raise failure
        return result

    def update(
        self, change: Callable[[dict], dict], *,
        before_publish: Callable[[PrivateJsonStaging], None] | None = None,
        _owned_descriptors: tuple[int, ...] = (),
        _runner_lock_name: str | None = None,
    ) -> dict:
        if before_publish is not None and not callable(before_publish):
            raise PrivateJsonError("registry_configuration_invalid")
        if (type(_owned_descriptors) is not tuple
                or any(type(value) is not int or value < 0 for value in _owned_descriptors)
                or len(set(_owned_descriptors)) != len(_owned_descriptors)
                or (_owned_descriptors and before_publish is None)):
            raise PrivateJsonError("registry_configuration_invalid")
        if (_runner_lock_name is not None and (
                self.name != "telegram.json" or _runner_lock_name != "telegram.runner.lock"
                or before_publish is None)):
            raise PrivateJsonError("registry_configuration_invalid")
        if before_publish is not None:
            return self._reviewed_update(change, before_publish, _owned_descriptors,
                                         _runner_lock_name)
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
