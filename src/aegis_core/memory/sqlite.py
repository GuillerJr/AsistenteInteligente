from __future__ import annotations

import json
import math
import os
import re
import sqlite3
import stat
import struct
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

SCHEMA_VERSION = 2
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
                if version not in {0, 1, SCHEMA_VERSION}:
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
                elif version == 1:
                    self._migrate_v1_to_v2(connection)
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

    def put_embedding(
        self,
        *,
        namespace: str,
        memory_id: UUID,
        model_id: str,
        vector: tuple[float, ...],
        content_sha256: str,
    ) -> None:
        self._require_initialized()
        self._validate_namespace(namespace)
        if not model_id or len(model_id) > 256:
            raise MemoryQueryError("invalid embedding model id")
        encoded_vector, dimensions = self._encode_vector(vector)
        with self._lock, self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                """
                SELECT content_sha256 FROM memory_items
                WHERE namespace = ? AND memory_id = ?
                """,
                (namespace, str(memory_id)),
            ).fetchone()
            if row is None:
                raise MemoryNotFoundError("memory does not exist")
            if row["content_sha256"] != content_sha256:
                raise MemoryStoreError("memory content changed before embedding")
            connection.execute(
                """
                INSERT INTO memory_embeddings (
                    memory_id, model_id, dimensions, vector,
                    content_sha256, created_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(memory_id) DO UPDATE SET
                    model_id = excluded.model_id,
                    dimensions = excluded.dimensions,
                    vector = excluded.vector,
                    content_sha256 = excluded.content_sha256,
                    created_at = excluded.created_at
                """,
                (
                    str(memory_id),
                    model_id,
                    dimensions,
                    encoded_vector,
                    content_sha256,
                    datetime.now(UTC).isoformat(),
                ),
            )
        self._secure_database_files()

    def vector_search(
        self,
        *,
        namespace: str,
        model_id: str,
        query_vector: tuple[float, ...],
        limit: int = 5,
        scan_limit: int = 2_000,
    ) -> tuple[MemorySearchHit, ...]:
        self._require_initialized()
        self._validate_namespace(namespace)
        if not 1 <= limit <= 10 or not 10 <= scan_limit <= 50_000:
            raise MemoryQueryError("vector search limits are out of range")
        normalized_query = self._normalize_vector(query_vector)
        with self._lock, self._connect(read_only=True) as connection:
            rows = connection.execute(
                """
                SELECT m.memory_id, m.namespace, m.kind, m.content, m.source,
                       m.tags_json, m.updated_at, m.content_sha256,
                       e.dimensions, e.vector
                FROM memory_embeddings AS e
                JOIN memory_items AS m ON m.memory_id = e.memory_id
                WHERE m.namespace = ? AND e.model_id = ?
                  AND e.content_sha256 = m.content_sha256
                ORDER BY m.updated_at DESC, m.memory_id ASC
                LIMIT ?
                """,
                (namespace, model_id, scan_limit),
            ).fetchall()
        hits: list[MemorySearchHit] = []
        for row in rows:
            vector = self._decode_vector(row["vector"], int(row["dimensions"]))
            if len(vector) != len(normalized_query):
                raise MemoryStoreError("embedding dimensions do not match query")
            similarity = math.fsum(
                left * right for left, right in zip(vector, normalized_query, strict=True)
            )
            hits.append(self._hit_from_row(row, score=max(0.0, min(1.0, similarity))))
        hits.sort(
            key=lambda hit: (
                -hit.score,
                -hit.updated_at.timestamp(),
                str(hit.memory_id),
            )
        )
        return tuple(hits[:limit])

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
            CREATE TABLE memory_embeddings (
                memory_id TEXT PRIMARY KEY
                    REFERENCES memory_items(memory_id) ON DELETE CASCADE,
                model_id TEXT NOT NULL,
                dimensions INTEGER NOT NULL,
                vector BLOB NOT NULL,
                content_sha256 TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
            CREATE INDEX memory_embeddings_model
                ON memory_embeddings(model_id);
            PRAGMA application_id = 1095059273;
            PRAGMA user_version = 2;
            COMMIT;
            """
        )

    @staticmethod
    def _migrate_v1_to_v2(connection: sqlite3.Connection) -> None:
        connection.executescript(
            """
            BEGIN IMMEDIATE;
            CREATE TABLE memory_embeddings (
                memory_id TEXT PRIMARY KEY
                    REFERENCES memory_items(memory_id) ON DELETE CASCADE,
                model_id TEXT NOT NULL,
                dimensions INTEGER NOT NULL,
                vector BLOB NOT NULL,
                content_sha256 TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
            CREATE INDEX memory_embeddings_model
                ON memory_embeddings(model_id);
            PRAGMA user_version = 2;
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
        if not {"memory_items", "memory_fts", "memory_embeddings"}.issubset(names):
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
    def _hit_from_row(
        row: sqlite3.Row,
        *,
        score: float | None = None,
    ) -> MemorySearchHit:
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
            score=max(0.0, -float(row["rank"])) if score is None else score,
        )

    @staticmethod
    def _normalize_vector(vector: tuple[float, ...]) -> tuple[float, ...]:
        if not 1 <= len(vector) <= 8_192 or any(
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not math.isfinite(value)
            for value in vector
        ):
            raise MemoryQueryError("embedding vector is invalid")
        normalized_values = tuple(float(value) for value in vector)
        norm = math.sqrt(math.fsum(value * value for value in normalized_values))
        if norm <= 0.0:
            raise MemoryQueryError("embedding vector norm must be positive")
        return tuple(value / norm for value in normalized_values)

    @classmethod
    def _encode_vector(cls, vector: tuple[float, ...]) -> tuple[bytes, int]:
        normalized = cls._normalize_vector(vector)
        try:
            return struct.pack(f"<{len(normalized)}f", *normalized), len(normalized)
        except (OverflowError, struct.error) as error:
            raise MemoryQueryError("embedding vector cannot be encoded") from error

    @staticmethod
    def _decode_vector(raw: object, dimensions: int) -> tuple[float, ...]:
        if not isinstance(raw, bytes) or not 1 <= dimensions <= 8_192 or len(raw) != dimensions * 4:
            raise MemoryStoreError("stored embedding vector is invalid")
        try:
            return tuple(struct.unpack(f"<{dimensions}f", raw))
        except struct.error as error:
            raise MemoryStoreError("stored embedding vector cannot be decoded") from error
