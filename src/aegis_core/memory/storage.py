from __future__ import annotations

import os
import sqlite3
import stat
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path
from urllib.parse import quote

from aegis_core.memory.errors import MemorySecurityError
from aegis_core.memory.visibility import memory_is_live

DecayFunction = Callable[[float, str, str, str], float]
VectorExtensionLoader = Callable[[sqlite3.Connection], None]


class SQLiteStorageGuard:
    """Own the SQLite file identity, permissions, sidecars, and connections."""

    def __init__(self, path: Path, *, expected_uid: int | None = None) -> None:
        self._path = path
        self._expected_uid = os.getuid() if expected_uid is None else expected_uid
        self._directory_identity: tuple[int, int] | None = None
        self._database_identity: tuple[int, int] | None = None

    def prepare(self) -> None:
        self._prepare_private_directory()
        self._prepare_database_file()

    @contextmanager
    def connect(
        self,
        *,
        read_only: bool,
        load_vector_extension: bool,
        decay_function: DecayFunction,
        vector_extension_loader: VectorExtensionLoader,
    ) -> Iterator[sqlite3.Connection]:
        self._verify_private_directory()
        self._verify_database_identity()
        self._verify_database_sidecars()
        mode = "ro" if read_only else "rw"
        encoded_path = quote(self._path.absolute().as_posix(), safe="/")
        connection = sqlite3.connect(
            f"file:{encoded_path}?mode={mode}",
            uri=True,
            timeout=5.0,
            isolation_level=None,
        )
        try:
            connection.row_factory = sqlite3.Row
            connection.create_function(
                "aegis_decay",
                4,
                decay_function,
                deterministic=True,
            )
            connection.execute("PRAGMA foreign_keys = ON")
            connection.create_function("aegis_memory_live", 2, memory_is_live, deterministic=True)
            connection.execute("PRAGMA busy_timeout = 5000")
            connection.execute("PRAGMA trusted_schema = OFF")
            if read_only:
                connection.execute("PRAGMA query_only = ON")
            else:
                connection.execute("PRAGMA secure_delete = ON")
            if load_vector_extension:
                vector_extension_loader(connection)
            self._verify_private_directory()
            self._verify_database_identity()
            self._verify_database_sidecars()
            with connection:
                yield connection
        finally:
            connection.close()

    def secure_files(self) -> None:
        self._verify_private_directory()
        flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
        for path in (self._path, *self._database_sidecars()):
            try:
                descriptor = os.open(path, flags)
            except FileNotFoundError:
                continue
            except OSError as error:
                raise MemorySecurityError("unsafe memory database sidecar") from error
            try:
                status = os.fstat(descriptor)
                if not stat.S_ISREG(status.st_mode) or status.st_uid != self._expected_uid:
                    raise MemorySecurityError("unsafe memory database sidecar")
                if (
                    path == self._path
                    and (
                        status.st_dev,
                        status.st_ino,
                    )
                    != self._database_identity
                ):
                    raise MemorySecurityError("memory database identity changed")
                os.fchmod(descriptor, 0o600)
            finally:
                os.close(descriptor)

    def _prepare_private_directory(self) -> None:
        parent = self._path.parent
        if not parent.exists():
            parent.mkdir(parents=True, mode=0o700)
        status = parent.lstat()
        if (
            not stat.S_ISDIR(status.st_mode)
            or status.st_uid != self._expected_uid
            or stat.S_IMODE(status.st_mode) & 0o077
        ):
            raise MemorySecurityError("memory directory must be owner-only")
        self._directory_identity = (status.st_dev, status.st_ino)

    def _verify_private_directory(self) -> None:
        if self._directory_identity is None:
            raise MemorySecurityError("memory directory identity is unavailable")
        try:
            status = self._path.parent.lstat()
        except FileNotFoundError as error:
            raise MemorySecurityError("memory directory disappeared") from error
        if (
            not stat.S_ISDIR(status.st_mode)
            or status.st_uid != self._expected_uid
            or stat.S_IMODE(status.st_mode) & 0o077
            or (status.st_dev, status.st_ino) != self._directory_identity
        ):
            raise MemorySecurityError("memory directory identity changed")

    def _prepare_database_file(self) -> None:
        flags = os.O_RDWR | os.O_CREAT | os.O_EXCL
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        try:
            descriptor = os.open(self._path, flags, 0o600)
        except FileExistsError:
            pass
        else:
            os.close(descriptor)
        status = self._path.lstat()
        if (
            not stat.S_ISREG(status.st_mode)
            or status.st_uid != self._expected_uid
            or stat.S_IMODE(status.st_mode) & 0o077
        ):
            raise MemorySecurityError("memory database must be an owner-only regular file")
        self._database_identity = (status.st_dev, status.st_ino)

    def _verify_database_identity(self) -> None:
        if self._database_identity is None:
            raise MemorySecurityError("memory database identity is unavailable")
        try:
            status = self._path.lstat()
        except FileNotFoundError as error:
            raise MemorySecurityError("memory database disappeared") from error
        if (
            not stat.S_ISREG(status.st_mode)
            or status.st_uid != self._expected_uid
            or stat.S_IMODE(status.st_mode) & 0o077
            or (status.st_dev, status.st_ino) != self._database_identity
        ):
            raise MemorySecurityError("memory database identity changed")

    def _verify_database_sidecars(self) -> None:
        for path in self._database_sidecars():
            try:
                status = path.lstat()
            except FileNotFoundError:
                continue
            if (
                not stat.S_ISREG(status.st_mode)
                or status.st_uid != self._expected_uid
                or stat.S_IMODE(status.st_mode) & 0o077
            ):
                raise MemorySecurityError("unsafe memory database sidecar")

    def _database_sidecars(self) -> tuple[Path, Path, Path]:
        return (
            Path(f"{self._path}-journal"),
            Path(f"{self._path}-wal"),
            Path(f"{self._path}-shm"),
        )
