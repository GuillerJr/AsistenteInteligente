from __future__ import annotations

import json
import os
import re
import sqlite3
import stat
import threading
import unicodedata
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import quote
from uuid import UUID

from aegis_core.memory.contracts import (
    MAX_MEMORY_EXCERPT_BYTES,
    NAMESPACE_PATTERN,
    MemoryKind,
    MemoryRecord,
    MemorySearchHit,
)

SCHEMA_VERSION = 1
APPLICATION_ID = 0x41454749
MAX_SEARCH_TERMS = 24


class MemoryStoreError(RuntimeError):
    """Base error for persistent memory operations."""


class MemorySecurityError(MemoryStoreError):
    pass


class MemoryCapacityError(MemoryStoreError):
    pass


class MemoryNotFoundError(MemoryStoreError):
    pass


class MemoryQueryError(MemoryStoreError):
    pass


class SecretMaterialError(MemoryStoreError):
    pass


class SQLiteMemoryStore:
    def __init__(
        self,
        path: Path,
        *,
        max_entries: int = 50_000,
        expected_uid: int | None = None,
    ) -> None:
        if max_entries < 1:
            raise ValueError("memory capacity must be positive")
        self._path = path
        self._max_entries = max_entries
        self._expected_uid = os.getuid() if expected_uid is None else expected_uid
        self._lock = threading.RLock()
        self._initialized = False
        self._database_identity: tuple[int, int] | None = None

    @property
    def path(self) -> Path:
        return self._path

    def initialize(self) -> None:
        with self._lock:
            self._prepare_private_directory()
            self._prepare_database_file()
            with self._connect() as connection:
                version = int(connection.execute("PRAGMA user_version").fetchone()[0])
                application_id = int(connection.execute("PRAGMA application_id").fetchone()[0])
                if version not in {0, SCHEMA_VERSION}:
                    raise MemoryStoreError("unsupported memory schema version")
                if version == 0:
                    existing_objects = connection.execute(
                        """
                        SELECT COUNT(*) FROM sqlite_master
                        WHERE name NOT LIKE 'sqlite_%'
                        """
                    ).fetchone()[0]
                    if application_id != 0 or existing_objects:
                        raise MemoryStoreError("refusing to modify an unrelated database")
                    self._create_schema(connection)
                elif application_id != APPLICATION_ID:
                    raise MemoryStoreError("memory database identity is invalid")
                self._verify_schema(connection)
                if connection.execute("PRAGMA quick_check").fetchone()[0] != "ok":
                    raise MemoryStoreError("memory database integrity check failed")
            self._secure_database_files()
            self._initialized = True

    def put(
        self,
        *,
        namespace: str,
        kind: MemoryKind,
        content: str,
        source: str | None = None,
        tags: tuple[str, ...] = (),
    ) -> MemoryRecord:
        self._require_initialized()
        self._reject_secret_material(content)
        now = datetime.now(UTC)
        record = MemoryRecord(
            namespace=namespace,
            kind=kind,
            content=content,
            source=source,
            tags=tags,
            created_at=now,
            updated_at=now,
            content_sha256=MemoryRecord.digest_content(content),
        )
        with self._lock, self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            count = int(connection.execute("SELECT COUNT(*) FROM memory_items").fetchone()[0])
            if count >= self._max_entries:
                raise MemoryCapacityError("memory capacity reached")
            cursor = connection.execute(
                """
                INSERT INTO memory_items (
                    memory_id, namespace, kind, content, source, tags_json,
                    created_at, updated_at, content_sha256
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    str(record.memory_id),
                    record.namespace,
                    record.kind.value,
                    record.content,
                    record.source,
                    json.dumps(record.tags, separators=(",", ":")),
                    record.created_at.isoformat(),
                    record.updated_at.isoformat(),
                    record.content_sha256,
                ),
            )
            connection.execute(
                "INSERT INTO memory_fts(rowid, content) VALUES (?, ?)",
                (cursor.lastrowid, record.content),
            )
        self._secure_database_files()
        return record

    def get(self, *, namespace: str, memory_id: UUID) -> MemoryRecord:
        self._require_initialized()
        self._validate_namespace(namespace)
        with self._lock, self._connect(read_only=True) as connection:
            row = connection.execute(
                """
                SELECT memory_id, namespace, kind, content, source, tags_json,
                       created_at, updated_at, content_sha256
                FROM memory_items
                WHERE namespace = ? AND memory_id = ?
                """,
                (namespace, str(memory_id)),
            ).fetchone()
        if row is None:
            raise MemoryNotFoundError("memory does not exist")
        return self._record_from_row(row)

    def search(
        self,
        *,
        namespace: str,
        query: str,
        limit: int = 5,
    ) -> tuple[MemorySearchHit, ...]:
        self._require_initialized()
        self._validate_namespace(namespace)
        if not 1 <= limit <= 10:
            raise MemoryQueryError("memory search limit is out of range")
        fts_query = self._fts_query(query)
        with self._lock, self._connect(read_only=True) as connection:
            rows = connection.execute(
                """
                SELECT m.memory_id, m.namespace, m.kind, m.content, m.source,
                       m.tags_json, m.updated_at, m.content_sha256,
                       bm25(memory_fts) AS rank
                FROM memory_fts
                JOIN memory_items AS m ON m.row_id = memory_fts.rowid
                WHERE memory_fts MATCH ? AND m.namespace = ?
                ORDER BY rank ASC, m.updated_at DESC, m.memory_id ASC
                LIMIT ?
                """,
                (fts_query, namespace, limit),
            ).fetchall()
        return tuple(self._hit_from_row(row) for row in rows)

    def delete(self, *, namespace: str, memory_id: UUID) -> None:
        self._require_initialized()
        self._validate_namespace(namespace)
        with self._lock, self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                """
                SELECT row_id, content FROM memory_items
                WHERE namespace = ? AND memory_id = ?
                """,
                (namespace, str(memory_id)),
            ).fetchone()
            if row is None:
                raise MemoryNotFoundError("memory does not exist")
            connection.execute(
                """
                INSERT INTO memory_fts(memory_fts, rowid, content)
                VALUES ('delete', ?, ?)
                """,
                (row["row_id"], row["content"]),
            )
            connection.execute(
                "DELETE FROM memory_items WHERE row_id = ?",
                (row["row_id"],),
            )
        self._secure_database_files()

    def _connect(self, *, read_only: bool = False) -> sqlite3.Connection:
        self._verify_database_identity()
        mode = "ro" if read_only else "rw"
        encoded_path = quote(self._path.absolute().as_posix(), safe="/")
        connection = sqlite3.connect(
            f"file:{encoded_path}?mode={mode}",
            uri=True,
            timeout=5.0,
            isolation_level=None,
        )
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 5000")
        connection.execute("PRAGMA trusted_schema = OFF")
        if read_only:
            connection.execute("PRAGMA query_only = ON")
        else:
            connection.execute("PRAGMA secure_delete = ON")
        return connection

    def _create_schema(self, connection: sqlite3.Connection) -> None:
        connection.executescript(
            """
            BEGIN IMMEDIATE;
            CREATE TABLE memory_items (
                row_id INTEGER PRIMARY KEY AUTOINCREMENT,
                memory_id TEXT NOT NULL UNIQUE,
                namespace TEXT NOT NULL,
                kind TEXT NOT NULL,
                content TEXT NOT NULL,
                source TEXT,
                tags_json TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                content_sha256 TEXT NOT NULL
            );
            CREATE INDEX memory_items_namespace_updated
                ON memory_items(namespace, updated_at DESC);
            CREATE VIRTUAL TABLE memory_fts USING fts5(
                content,
                content='memory_items',
                content_rowid='row_id',
                tokenize='unicode61 remove_diacritics 2'
            );
            PRAGMA application_id = 1095059273;
            PRAGMA user_version = 1;
            COMMIT;
            """
        )

    @staticmethod
    def _verify_schema(connection: sqlite3.Connection) -> None:
        names = {
            str(row[0])
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type IN ('table', 'view')"
            )
        }
        if not {"memory_items", "memory_fts"}.issubset(names):
            raise MemoryStoreError("memory schema is incomplete")

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

    def _secure_database_files(self) -> None:
        for path in (self._path, Path(f"{self._path}-wal"), Path(f"{self._path}-shm")):
            try:
                status = path.lstat()
            except FileNotFoundError:
                continue
            if not stat.S_ISREG(status.st_mode) or status.st_uid != self._expected_uid:
                raise MemorySecurityError("unsafe memory database sidecar")
            path.chmod(0o600)

    def _require_initialized(self) -> None:
        if not self._initialized:
            raise MemoryStoreError("memory store is not initialized")

    @staticmethod
    def _reject_secret_material(content: str) -> None:
        normalized = content.casefold()
        markers = (
            "-----begin private key-----",
            "-----begin rsa private key-----",
            "-----begin openssh private key-----",
            "nvapi-",
            "sk-proj-",
        )
        if any(marker in normalized for marker in markers):
            raise SecretMaterialError("credential-like material belongs in Keychain")

    @staticmethod
    def _validate_namespace(namespace: str) -> None:
        if not re.fullmatch(NAMESPACE_PATTERN, namespace):
            raise MemoryQueryError("invalid memory namespace")

    @staticmethod
    def _fts_query(query: str) -> str:
        normalized = unicodedata.normalize("NFKC", query).casefold()
        terms: list[str] = []
        for term in re.findall(r"\w+", normalized, flags=re.UNICODE):
            if term not in terms:
                terms.append(term[:64])
            if len(terms) == MAX_SEARCH_TERMS:
                break
        if not terms:
            raise MemoryQueryError("memory query has no searchable terms")
        return " OR ".join(f'"{term}"' for term in terms)

    @staticmethod
    def _record_from_row(row: sqlite3.Row) -> MemoryRecord:
        return MemoryRecord(
            memory_id=row["memory_id"],
            namespace=row["namespace"],
            kind=row["kind"],
            content=row["content"],
            source=row["source"],
            tags=tuple(json.loads(row["tags_json"])),
            created_at=datetime.fromisoformat(row["created_at"]),
            updated_at=datetime.fromisoformat(row["updated_at"]),
            content_sha256=row["content_sha256"],
        )

    @staticmethod
    def _hit_from_row(row: sqlite3.Row) -> MemorySearchHit:
        content = str(row["content"])
        encoded = content.encode("utf-8")[:MAX_MEMORY_EXCERPT_BYTES]
        excerpt = encoded.decode("utf-8", errors="ignore")
        return MemorySearchHit(
            memory_id=row["memory_id"],
            namespace=row["namespace"],
            kind=row["kind"],
            excerpt=excerpt,
            source=row["source"],
            tags=tuple(json.loads(row["tags_json"])),
            updated_at=datetime.fromisoformat(row["updated_at"]),
            content_sha256=row["content_sha256"],
            score=max(0.0, -float(row["rank"])),
        )
