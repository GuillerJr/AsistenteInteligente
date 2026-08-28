from __future__ import annotations

import json
import math
import os
import re
import sqlite3
import stat
import struct
import threading
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import NoReturn
from urllib.parse import quote
from uuid import UUID

try:
    import sqlite_vec
except ImportError:  # pragma: no cover - supported deployment installs the accelerator
    sqlite_vec = None

from aegis_core.memory.contracts import (
    MAX_MEMORY_EXCERPT_BYTES,
    NAMESPACE_PATTERN,
    TAG_PATTERN,
    ConversationRecord,
    ConversationRole,
    ConversationTurn,
    MemoryEvidence,
    MemoryKind,
    MemoryRecord,
    MemorySearchHit,
)
from aegis_core.memory.crypto import MemoryRowCipher, RowAuthenticationError
from aegis_core.secrets import contains_likely_secret_material

SCHEMA_VERSION = 5
APPLICATION_ID = 0x41454749
PYTHON_VECTOR_FALLBACK_LIMIT = 2_000
MAX_MEMORY_VECTORS = 2_000


class MemoryStoreError(RuntimeError):
    """Base error for persistent memory operations."""


class MemorySecurityError(MemoryStoreError):
    pass


class DecryptionAuthError(MemorySecurityError):
    """Fatal signal that an encrypted memory row or its namespace was tampered with."""


class MemoryCapacityError(MemoryStoreError):
    pass


class MemoryNotFoundError(MemoryStoreError):
    pass


class MemoryQueryError(MemoryStoreError):
    pass


class SecretMaterialError(MemoryStoreError):
    pass


class ConversationCapacityError(MemoryStoreError):
    pass


class _VectorAccelerationUnavailable(RuntimeError):
    pass


class SQLiteMemoryStore:
    def __init__(
        self,
        path: Path,
        *,
        max_entries: int = 50_000,
        max_vectors: int = MAX_MEMORY_VECTORS,
        expected_uid: int | None = None,
        encryption_secret: bytes,
        on_auth_failure: Callable[[str], None] | None = None,
    ) -> None:
        if max_entries < 1:
            raise ValueError("memory capacity must be positive")
        if not 1 <= max_vectors <= MAX_MEMORY_VECTORS:
            raise ValueError("memory vector capacity is out of range")
        self._path = path
        self._max_entries = max_entries
        self._max_vectors = max_vectors
        self._expected_uid = os.getuid() if expected_uid is None else expected_uid
        self._cipher = MemoryRowCipher(encryption_secret)
        self._on_auth_failure = on_auth_failure
        self._lock = threading.RLock()
        self._initialized = False
        self._compromised = False
        self._directory_identity: tuple[int, int] | None = None
        self._database_identity: tuple[int, int] | None = None

    @property
    def path(self) -> Path:
        return self._path

    @classmethod
    def encryption_key_initialized(cls, path: Path) -> bool:
        if not path.exists():
            return False
        encoded_path = quote(path.absolute().as_posix(), safe="/")
        try:
            connection = sqlite3.connect(f"file:{encoded_path}?mode=ro", uri=True, timeout=1.0)
            try:
                version = int(connection.execute("PRAGMA user_version").fetchone()[0])
                if version < 5:
                    return False
                row = connection.execute(
                    """
                    SELECT name FROM sqlite_master
                    WHERE type = 'table' AND name = 'memory_security'
                    """
                ).fetchone()
                return row is not None
            finally:
                connection.close()
        except (OSError, sqlite3.Error, TypeError, ValueError):
            return False

    @classmethod
    def vector_acceleration_available(cls) -> bool:
        connection = sqlite3.connect(":memory:")
        try:
            connection.execute("PRAGMA trusted_schema = OFF")
            connection.execute("PRAGMA query_only = ON")
            cls._load_vector_extension(connection)
        except (OSError, sqlite3.Error, _VectorAccelerationUnavailable):
            return False
        finally:
            connection.close()
        return True

    def initialize(self) -> None:
        with self._lock:
            self._prepare_private_directory()
            self._prepare_database_file()
            with self._connect() as connection:
                version = int(connection.execute("PRAGMA user_version").fetchone()[0])
                application_id = int(connection.execute("PRAGMA application_id").fetchone()[0])
                if version not in {0, 1, 2, 3, 4, SCHEMA_VERSION}:
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
                    self._migrate_v2_to_v3(connection)
                    self._migrate_v3_to_v4(connection)
                    self._migrate_v4_to_v5(connection)
                elif version == 2:
                    self._migrate_v2_to_v3(connection)
                    self._migrate_v3_to_v4(connection)
                    self._migrate_v4_to_v5(connection)
                elif version == 3:
                    self._migrate_v3_to_v4(connection)
                    self._migrate_v4_to_v5(connection)
                elif version == 4:
                    self._migrate_v4_to_v5(connection)
                self._verify_schema(connection)
                self._verify_encryption_key(connection)
                connection.execute("BEGIN IMMEDIATE")
                self._prune_embeddings(connection)
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
        confidence: float = 1.0,
        evidence: MemoryEvidence = MemoryEvidence.EXPLICIT_TEXT,
        expires_at: datetime | None = None,
        last_confirmed_at: datetime | None = None,
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
            confidence=confidence,
            evidence=evidence,
            expires_at=expires_at,
            last_confirmed_at=last_confirmed_at,
        )
        nonce, ciphertext, source_digest, tag_digests, blind_content = self._seal_record(record)
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
                    , confidence, evidence, expires_at, last_confirmed_at,
                    nonce, ciphertext, source_digest, tags_digest_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    str(record.memory_id),
                    record.namespace,
                    record.kind.value,
                    "",
                    None,
                    "[]",
                    record.created_at.isoformat(),
                    record.updated_at.isoformat(),
                    record.content_sha256,
                    record.confidence,
                    record.evidence.value,
                    record.expires_at.isoformat() if record.expires_at else None,
                    record.last_confirmed_at.isoformat() if record.last_confirmed_at else None,
                    nonce,
                    ciphertext,
                    source_digest,
                    tag_digests,
                ),
            )
            connection.execute(
                "INSERT INTO memory_fts(rowid, content) VALUES (?, ?)",
                (cursor.lastrowid, blind_content),
            )
        self._secure_database_files()
        return record

    def upsert_by_source(
        self,
        *,
        namespace: str,
        kind: MemoryKind,
        content: str,
        source: str,
        tags: tuple[str, ...] = (),
        confidence: float = 1.0,
        evidence: MemoryEvidence = MemoryEvidence.EXPLICIT_TEXT,
        expires_at: datetime | None = None,
        last_confirmed_at: datetime | None = None,
    ) -> MemoryRecord:
        """Replace one stable, locally-owned memory slot without creating duplicates."""
        self._require_initialized()
        self._validate_namespace(namespace)
        self._reject_secret_material(content)
        now = datetime.now(UTC)
        source_digest = self._cipher.blind_exact(source)
        with self._lock, self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            existing = connection.execute(
                """
                SELECT row_id, memory_id, namespace, kind, content, source, tags_json,
                       created_at, updated_at, content_sha256, confidence, evidence,
                       expires_at, last_confirmed_at, nonce, ciphertext,
                       source_digest, tags_digest_json
                FROM memory_items
                WHERE namespace = ? AND source_digest = ?
                ORDER BY updated_at DESC, memory_id ASC
                LIMIT 1
                """,
                (namespace, source_digest),
            ).fetchone()
            if existing is None:
                count = int(connection.execute("SELECT COUNT(*) FROM memory_items").fetchone()[0])
                if count >= self._max_entries:
                    raise MemoryCapacityError("memory capacity reached")
                record = MemoryRecord(
                    namespace=namespace,
                    kind=kind,
                    content=content,
                    source=source,
                    tags=tags,
                    created_at=now,
                    updated_at=now,
                    content_sha256=MemoryRecord.digest_content(content),
                    confidence=confidence,
                    evidence=evidence,
                    expires_at=expires_at,
                    last_confirmed_at=last_confirmed_at,
                )
                nonce, ciphertext, source_digest, tag_digests, blind_content = (
                    self._seal_record(record)
                )
                cursor = connection.execute(
                    """
                    INSERT INTO memory_items (
                        memory_id, namespace, kind, content, source, tags_json,
                        created_at, updated_at, content_sha256
                        , confidence, evidence, expires_at, last_confirmed_at,
                        nonce, ciphertext, source_digest, tags_digest_json
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        str(record.memory_id),
                        record.namespace,
                        record.kind.value,
                        "",
                        None,
                        "[]",
                        record.created_at.isoformat(),
                        record.updated_at.isoformat(),
                        record.content_sha256,
                        record.confidence,
                        record.evidence.value,
                        record.expires_at.isoformat() if record.expires_at else None,
                        record.last_confirmed_at.isoformat() if record.last_confirmed_at else None,
                        nonce,
                        ciphertext,
                        source_digest,
                        tag_digests,
                    ),
                )
                row_id = cursor.lastrowid
            else:
                previous = self._record_from_row(existing)
                record = MemoryRecord(
                    memory_id=existing["memory_id"],
                    namespace=namespace,
                    kind=kind,
                    content=content,
                    source=source,
                    tags=tags,
                    created_at=previous.created_at,
                    updated_at=now,
                    content_sha256=MemoryRecord.digest_content(content),
                    confidence=max(previous.confidence, confidence)
                    if previous.content == content
                    else confidence,
                    evidence=evidence,
                    expires_at=expires_at,
                    last_confirmed_at=last_confirmed_at,
                )
                row_id = int(existing["row_id"])
                nonce, ciphertext, source_digest, tag_digests, blind_content = (
                    self._seal_record(record)
                )
                connection.execute("DELETE FROM memory_fts WHERE rowid = ?", (row_id,))
                connection.execute(
                    """
                    UPDATE memory_items SET
                        kind = ?, content = '', source = NULL, tags_json = '[]',
                        updated_at = ?, content_sha256 = ?, confidence = ?, evidence = ?,
                        expires_at = ?, last_confirmed_at = ?, nonce = ?, ciphertext = ?,
                        source_digest = ?, tags_digest_json = ?
                    WHERE row_id = ?
                    """,
                    (
                        record.kind.value,
                        record.updated_at.isoformat(),
                        record.content_sha256,
                        record.confidence,
                        record.evidence.value,
                        record.expires_at.isoformat() if record.expires_at else None,
                        record.last_confirmed_at.isoformat() if record.last_confirmed_at else None,
                        nonce,
                        ciphertext,
                        source_digest,
                        tag_digests,
                        row_id,
                    ),
                )
                connection.execute(
                    "DELETE FROM memory_embeddings WHERE memory_id = ?",
                    (str(record.memory_id),),
                )
            connection.execute(
                "INSERT INTO memory_fts(rowid, content) VALUES (?, ?)",
                (row_id, blind_content),
            )
        self._secure_database_files()
        return record

    def list_by_tag(
        self,
        *,
        namespace: str,
        tag: str,
        limit: int = 10,
    ) -> tuple[MemorySearchHit, ...]:
        self._require_initialized()
        self._validate_namespace(namespace)
        if not re.fullmatch(TAG_PATTERN, tag) or not 1 <= limit <= 100:
            raise MemoryQueryError("invalid tagged memory query")
        tag_digest = self._cipher.blind_exact(tag)
        with self._lock, self._connect(read_only=True) as connection:
            rows = connection.execute(
                """
                SELECT m.memory_id, m.namespace, m.kind, m.content, m.source,
                       m.tags_json, m.created_at, m.updated_at, m.content_sha256,
                       m.confidence, m.evidence, m.expires_at, m.last_confirmed_at,
                       m.nonce, m.ciphertext, m.source_digest, m.tags_digest_json
                FROM memory_items AS m
                WHERE m.namespace = ?
                  AND (m.expires_at IS NULL OR m.expires_at > ?)
                  AND EXISTS (
                      SELECT 1 FROM json_each(m.tags_digest_json) WHERE value = ?
                  )
                ORDER BY m.updated_at DESC, m.memory_id ASC
                LIMIT ?
                """,
                (namespace, datetime.now(UTC).isoformat(), tag_digest, limit),
            ).fetchall()
        return tuple(self._hit_from_row(row, score=1.0) for row in rows)

    def delete_by_source(self, *, namespace: str, source: str) -> bool:
        self._require_initialized()
        self._validate_namespace(namespace)
        if not source or len(source) > 256:
            raise MemoryQueryError("invalid memory source")
        source_digest = self._cipher.blind_exact(source)
        with self._lock, self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                """
                SELECT row_id FROM memory_items
                WHERE namespace = ? AND source_digest = ?
                ORDER BY updated_at DESC, memory_id ASC
                LIMIT 1
                """,
                (namespace, source_digest),
            ).fetchone()
            if row is None:
                return False
            connection.execute("DELETE FROM memory_fts WHERE rowid = ?", (row["row_id"],))
            connection.execute("DELETE FROM memory_items WHERE row_id = ?", (row["row_id"],))
        self._secure_database_files()
        return True

    def delete_by_tag(self, *, namespace: str, tag: str) -> int:
        self._require_initialized()
        self._validate_namespace(namespace)
        if not re.fullmatch(TAG_PATTERN, tag):
            raise MemoryQueryError("invalid memory tag")
        tag_digest = self._cipher.blind_exact(tag)
        with self._lock, self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            rows = connection.execute(
                """
                SELECT m.row_id
                FROM memory_items AS m
                WHERE m.namespace = ?
                  AND EXISTS (
                      SELECT 1 FROM json_each(m.tags_digest_json) WHERE value = ?
                  )
                """,
                (namespace, tag_digest),
            ).fetchall()
            for row in rows:
                connection.execute("DELETE FROM memory_fts WHERE rowid = ?", (row["row_id"],))
                connection.execute(
                    "DELETE FROM memory_items WHERE row_id = ?",
                    (row["row_id"],),
                )
        self._secure_database_files()
        return len(rows)

    def get(self, *, namespace: str, memory_id: UUID) -> MemoryRecord:
        self._require_initialized()
        self._validate_namespace(namespace)
        with self._lock, self._connect(read_only=True) as connection:
            row = connection.execute(
                """
                SELECT memory_id, namespace, kind, content, source, tags_json,
                       created_at, updated_at, content_sha256, confidence, evidence,
                       expires_at, last_confirmed_at, nonce, ciphertext,
                       source_digest, tags_digest_json
                FROM memory_items
                WHERE namespace = ? AND memory_id = ?
                """,
                (namespace, str(memory_id)),
            ).fetchone()
        if row is None:
            raise MemoryNotFoundError("memory does not exist")
        return self._record_from_row(row)

    def list_missing_embeddings(
        self,
        *,
        namespace: str,
        model_id: str,
        limit: int = 500,
    ) -> tuple[MemoryRecord, ...]:
        self._require_initialized()
        self._validate_namespace(namespace)
        if not model_id or len(model_id) > 256 or not 1 <= limit <= 2_000:
            raise MemoryQueryError("invalid embedding backfill query")
        with self._lock, self._connect(read_only=True) as connection:
            rows = connection.execute(
                """
                SELECT m.memory_id, m.namespace, m.kind, m.content, m.source,
                       m.tags_json, m.created_at, m.updated_at, m.content_sha256,
                       m.confidence, m.evidence, m.expires_at, m.last_confirmed_at,
                       m.nonce, m.ciphertext, m.source_digest, m.tags_digest_json
                FROM memory_items AS m
                LEFT JOIN memory_embeddings AS e
                  ON e.memory_id = m.memory_id
                 AND e.model_id = ?
                 AND e.content_sha256 = m.content_sha256
                WHERE m.namespace = ? AND e.memory_id IS NULL
                  AND (m.expires_at IS NULL OR m.expires_at > ?)
                ORDER BY m.updated_at DESC, m.memory_id ASC
                LIMIT ?
                """,
                (model_id, namespace, datetime.now(UTC).isoformat(), limit),
            ).fetchall()
        return tuple(self._record_from_row(row) for row in rows)

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
                       m.tags_json, m.created_at, m.updated_at, m.content_sha256,
                       m.confidence, m.evidence, m.expires_at, m.last_confirmed_at,
                       m.nonce, m.ciphertext, m.source_digest, m.tags_digest_json,
                       bm25(memory_fts) AS rank
                FROM memory_fts
                JOIN memory_items AS m ON m.row_id = memory_fts.rowid
                WHERE memory_fts MATCH ? AND m.namespace = ?
                  AND (m.expires_at IS NULL OR m.expires_at > ?)
                ORDER BY rank ASC, m.updated_at DESC, m.memory_id ASC
                LIMIT ?
                """,
                (fts_query, namespace, datetime.now(UTC).isoformat(), limit),
            ).fetchall()
        return tuple(self._hit_from_row(row) for row in rows)

    def delete(self, *, namespace: str, memory_id: UUID) -> None:
        self._require_initialized()
        self._validate_namespace(namespace)
        with self._lock, self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                """
                SELECT row_id FROM memory_items
                WHERE namespace = ? AND memory_id = ?
                """,
                (namespace, str(memory_id)),
            ).fetchone()
            if row is None:
                raise MemoryNotFoundError("memory does not exist")
            connection.execute("DELETE FROM memory_fts WHERE rowid = ?", (row["row_id"],))
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
            self._prune_embeddings(connection)
        self._secure_database_files()

    def vector_search(
        self,
        *,
        namespace: str,
        model_id: str,
        query_vector: tuple[float, ...],
        limit: int = 5,
        scan_limit: int = MAX_MEMORY_VECTORS,
    ) -> tuple[MemorySearchHit, ...]:
        self._require_initialized()
        self._validate_namespace(namespace)
        if not 1 <= limit <= 10 or not 10 <= scan_limit <= MAX_MEMORY_VECTORS:
            raise MemoryQueryError("vector search limits are out of range")
        normalized_query = self._normalize_vector(query_vector)
        now = datetime.now(UTC).isoformat()
        try:
            rows = self._accelerated_vector_search_rows(
                namespace=namespace,
                model_id=model_id,
                query_vector=self._encode_vector(normalized_query)[0],
                limit=limit,
                scan_limit=scan_limit,
                now=now,
            )
        except (OSError, sqlite3.Error, _VectorAccelerationUnavailable):
            rows = self._python_vector_search_rows(
                namespace=namespace,
                model_id=model_id,
                scan_limit=min(scan_limit, PYTHON_VECTOR_FALLBACK_LIMIT),
                now=now,
            )
            return self._rank_vector_rows(rows, normalized_query=normalized_query, limit=limit)
        return tuple(self._accelerated_hit_from_row(row) for row in rows)

    def _prune_embeddings(self, connection: sqlite3.Connection) -> None:
        connection.execute(
            """
            DELETE FROM memory_embeddings
            WHERE memory_id IN (
                SELECT e.memory_id
                FROM memory_embeddings AS e
                JOIN memory_items AS m ON m.memory_id = e.memory_id
                ORDER BY m.updated_at DESC, m.memory_id ASC
                LIMIT -1 OFFSET ?
            )
            """,
            (self._max_vectors,),
        )

    def _accelerated_vector_search_rows(
        self,
        *,
        namespace: str,
        model_id: str,
        query_vector: bytes,
        limit: int,
        scan_limit: int,
        now: str,
    ) -> list[sqlite3.Row]:
        with self._lock, self._connect(read_only=True, load_vector_extension=True) as connection:
            return connection.execute(
                """
                WITH candidates AS (
                    SELECT m.memory_id, m.updated_at, e.vector
                    FROM memory_embeddings AS e
                    JOIN memory_items AS m ON m.memory_id = e.memory_id
                    WHERE m.namespace = ? AND e.model_id = ?
                      AND e.content_sha256 = m.content_sha256
                      AND (m.expires_at IS NULL OR m.expires_at > ?)
                    ORDER BY m.updated_at DESC, m.memory_id ASC
                    LIMIT ?
                ), matches AS (
                    SELECT memory_id, updated_at,
                           1.0 - vec_distance_cosine(vector, ?) AS similarity
                    FROM candidates
                    ORDER BY similarity DESC, updated_at DESC, memory_id ASC
                    LIMIT ?
                )
                SELECT m.memory_id, m.namespace, m.kind, m.content, m.source,
                       m.tags_json, m.created_at, m.updated_at, m.content_sha256,
                       m.confidence, m.evidence, m.expires_at, m.last_confirmed_at,
                       m.nonce, m.ciphertext, m.source_digest, m.tags_digest_json,
                       matches.similarity
                FROM matches
                JOIN memory_items AS m ON m.memory_id = matches.memory_id
                ORDER BY matches.similarity DESC, m.updated_at DESC, m.memory_id ASC
                """,
                (namespace, model_id, now, scan_limit, query_vector, limit),
            ).fetchall()

    def _python_vector_search_rows(
        self,
        *,
        namespace: str,
        model_id: str,
        scan_limit: int,
        now: str,
    ) -> list[sqlite3.Row]:
        with self._lock, self._connect(read_only=True) as connection:
            return connection.execute(
                """
                SELECT m.memory_id, m.namespace, m.kind, m.content, m.source,
                       m.tags_json, m.created_at, m.updated_at, m.content_sha256,
                       m.confidence, m.evidence, m.expires_at, m.last_confirmed_at,
                       m.nonce, m.ciphertext, m.source_digest, m.tags_digest_json,
                       e.dimensions, e.vector
                FROM memory_embeddings AS e
                JOIN memory_items AS m ON m.memory_id = e.memory_id
                WHERE m.namespace = ? AND e.model_id = ?
                  AND e.content_sha256 = m.content_sha256
                  AND (m.expires_at IS NULL OR m.expires_at > ?)
                ORDER BY m.updated_at DESC, m.memory_id ASC
                LIMIT ?
                """,
                (namespace, model_id, now, scan_limit),
            ).fetchall()

    def _rank_vector_rows(
        self,
        rows: list[sqlite3.Row],
        *,
        normalized_query: tuple[float, ...],
        limit: int,
    ) -> tuple[MemorySearchHit, ...]:
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

    def _accelerated_hit_from_row(self, row: sqlite3.Row) -> MemorySearchHit:
        try:
            similarity = float(row["similarity"])
        except (KeyError, TypeError, ValueError) as error:
            raise MemoryStoreError("accelerated vector score is invalid") from error
        if not math.isfinite(similarity):
            raise MemoryStoreError("accelerated vector score is invalid")
        return self._hit_from_row(row, score=max(0.0, min(1.0, similarity)))

    def create_conversation(
        self,
        *,
        namespace: str,
        title: str | None = None,
        max_conversations: int = 1_000,
    ) -> ConversationRecord:
        self._require_initialized()
        if not 1 <= max_conversations <= 100_000:
            raise MemoryQueryError("conversation capacity is out of range")
        now = datetime.now(UTC)
        record = ConversationRecord(
            namespace=namespace,
            title=title,
            created_at=now,
            updated_at=now,
        )
        with self._lock, self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            count = int(
                connection.execute(
                    "SELECT COUNT(*) FROM conversations WHERE namespace = ?",
                    (namespace,),
                ).fetchone()[0]
            )
            if count >= max_conversations:
                raise ConversationCapacityError("conversation capacity reached")
            connection.execute(
                """
                INSERT INTO conversations (
                    conversation_id, namespace, title, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?)
                """,
                (
                    str(record.conversation_id),
                    record.namespace,
                    record.title,
                    record.created_at.isoformat(),
                    record.updated_at.isoformat(),
                ),
            )
        self._secure_database_files()
        return record

    def get_conversation(
        self,
        *,
        namespace: str,
        conversation_id: UUID,
    ) -> ConversationRecord:
        self._require_initialized()
        self._validate_namespace(namespace)
        with self._lock, self._connect(read_only=True) as connection:
            row = connection.execute(
                """
                SELECT conversation_id, namespace, title, created_at, updated_at
                FROM conversations
                WHERE namespace = ? AND conversation_id = ?
                """,
                (namespace, str(conversation_id)),
            ).fetchone()
        if row is None:
            raise MemoryNotFoundError("conversation does not exist")
        return self._conversation_from_row(row)

    def conversation_history(
        self,
        *,
        namespace: str,
        conversation_id: UUID,
        limit: int = 12,
    ) -> tuple[ConversationTurn, ...]:
        self.get_conversation(namespace=namespace, conversation_id=conversation_id)
        if not 1 <= limit <= 50:
            raise MemoryQueryError("conversation history limit is out of range")
        with self._lock, self._connect(read_only=True) as connection:
            rows = connection.execute(
                """
                SELECT turn_id, conversation_id, sequence, role, content,
                       created_at, content_sha256
                FROM conversation_turns
                WHERE conversation_id = ?
                ORDER BY sequence DESC
                LIMIT ?
                """,
                (str(conversation_id), limit),
            ).fetchall()
        return tuple(self._turn_from_row(row) for row in reversed(rows))

    def append_conversation_exchange(
        self,
        *,
        namespace: str,
        conversation_id: UUID,
        user_content: str,
        assistant_content: str,
        max_turns: int = 1_000,
    ) -> tuple[ConversationTurn, ConversationTurn]:
        self._require_initialized()
        self._validate_namespace(namespace)
        self._reject_secret_material(user_content)
        self._reject_secret_material(assistant_content)
        if not 2 <= max_turns <= 10_000:
            raise MemoryQueryError("conversation turn capacity is out of range")
        now = datetime.now(UTC)
        with self._lock, self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            conversation = connection.execute(
                """
                SELECT conversation_id FROM conversations
                WHERE namespace = ? AND conversation_id = ?
                """,
                (namespace, str(conversation_id)),
            ).fetchone()
            if conversation is None:
                raise MemoryNotFoundError("conversation does not exist")
            count, last_sequence = connection.execute(
                """
                SELECT COUNT(*), COALESCE(MAX(sequence), 0)
                FROM conversation_turns
                WHERE conversation_id = ?
                """,
                (str(conversation_id),),
            ).fetchone()
            if int(count) + 2 > max_turns:
                raise ConversationCapacityError("conversation turn capacity reached")
            user_turn = ConversationTurn(
                conversation_id=conversation_id,
                sequence=int(last_sequence) + 1,
                role=ConversationRole.USER,
                content=user_content,
                created_at=now,
                content_sha256=ConversationTurn.digest_content(user_content),
            )
            assistant_turn = ConversationTurn(
                conversation_id=conversation_id,
                sequence=int(last_sequence) + 2,
                role=ConversationRole.ASSISTANT,
                content=assistant_content,
                created_at=now,
                content_sha256=ConversationTurn.digest_content(assistant_content),
            )
            for turn in (user_turn, assistant_turn):
                connection.execute(
                    """
                    INSERT INTO conversation_turns (
                        turn_id, conversation_id, sequence, role, content,
                        created_at, content_sha256
                    ) VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        str(turn.turn_id),
                        str(turn.conversation_id),
                        turn.sequence,
                        turn.role.value,
                        turn.content,
                        turn.created_at.isoformat(),
                        turn.content_sha256,
                    ),
                )
            connection.execute(
                "UPDATE conversations SET updated_at = ? WHERE conversation_id = ?",
                (now.isoformat(), str(conversation_id)),
            )
        self._secure_database_files()
        return user_turn, assistant_turn

    def delete_conversation(self, *, namespace: str, conversation_id: UUID) -> None:
        self._require_initialized()
        self._validate_namespace(namespace)
        with self._lock, self._connect() as connection:
            cursor = connection.execute(
                "DELETE FROM conversations WHERE namespace = ? AND conversation_id = ?",
                (namespace, str(conversation_id)),
            )
            if cursor.rowcount != 1:
                raise MemoryNotFoundError("conversation does not exist")
        self._secure_database_files()

    def _connect(
        self,
        *,
        read_only: bool = False,
        load_vector_extension: bool = False,
    ) -> sqlite3.Connection:
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
            connection.execute("PRAGMA foreign_keys = ON")
            connection.execute("PRAGMA busy_timeout = 5000")
            connection.execute("PRAGMA trusted_schema = OFF")
            if read_only:
                connection.execute("PRAGMA query_only = ON")
            else:
                connection.execute("PRAGMA secure_delete = ON")
            if load_vector_extension:
                self._load_vector_extension(connection)
            self._verify_private_directory()
            self._verify_database_identity()
            self._verify_database_sidecars()
        except Exception:
            connection.close()
            raise
        return connection

    @staticmethod
    def _load_vector_extension(connection: sqlite3.Connection) -> None:
        if sqlite_vec is None or not hasattr(connection, "enable_load_extension"):
            raise _VectorAccelerationUnavailable("sqlite vector acceleration is unavailable")
        connection.enable_load_extension(True)
        try:
            sqlite_vec.load(connection)
        except Exception as error:
            raise _VectorAccelerationUnavailable(
                "sqlite vector acceleration could not be loaded"
            ) from error
        finally:
            connection.enable_load_extension(False)
        version = connection.execute("SELECT vec_version()").fetchone()
        if version is None or not isinstance(version[0], str) or not version[0]:
            raise _VectorAccelerationUnavailable("sqlite vector acceleration is invalid")

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
                , confidence REAL NOT NULL CHECK(confidence >= 0.0 AND confidence <= 1.0)
                , evidence TEXT NOT NULL
                , expires_at TEXT
                , last_confirmed_at TEXT
                , nonce BLOB NOT NULL CHECK(length(nonce) = 12)
                , ciphertext BLOB NOT NULL CHECK(length(ciphertext) >= 17)
                , source_digest TEXT
                , tags_digest_json TEXT NOT NULL
            );
            CREATE INDEX memory_items_namespace_updated
                ON memory_items(namespace, updated_at DESC);
            CREATE INDEX memory_items_namespace_source_digest
                ON memory_items(namespace, source_digest);
            CREATE VIRTUAL TABLE memory_fts USING fts5(
                content,
                tokenize='ascii'
            );
            CREATE TABLE memory_security (
                singleton INTEGER PRIMARY KEY CHECK(singleton = 1),
                key_identifier TEXT NOT NULL CHECK(length(key_identifier) = 64)
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
            CREATE TABLE conversations (
                conversation_id TEXT PRIMARY KEY,
                namespace TEXT NOT NULL,
                title TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE INDEX conversations_namespace_updated
                ON conversations(namespace, updated_at DESC);
            CREATE TABLE conversation_turns (
                turn_id TEXT PRIMARY KEY,
                conversation_id TEXT NOT NULL
                    REFERENCES conversations(conversation_id) ON DELETE CASCADE,
                sequence INTEGER NOT NULL,
                role TEXT NOT NULL,
                content TEXT NOT NULL,
                created_at TEXT NOT NULL,
                content_sha256 TEXT NOT NULL,
                UNIQUE(conversation_id, sequence)
            );
            PRAGMA application_id = 1095059273;
            PRAGMA user_version = 5;
            COMMIT;
            """
        )
        connection.execute(
            "INSERT INTO memory_security(singleton, key_identifier) VALUES (1, ?)",
            (self._cipher.key_identifier,),
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
    def _migrate_v2_to_v3(connection: sqlite3.Connection) -> None:
        connection.executescript(
            """
            BEGIN IMMEDIATE;
            CREATE TABLE conversations (
                conversation_id TEXT PRIMARY KEY,
                namespace TEXT NOT NULL,
                title TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE INDEX conversations_namespace_updated
                ON conversations(namespace, updated_at DESC);
            CREATE TABLE conversation_turns (
                turn_id TEXT PRIMARY KEY,
                conversation_id TEXT NOT NULL
                    REFERENCES conversations(conversation_id) ON DELETE CASCADE,
                sequence INTEGER NOT NULL,
                role TEXT NOT NULL,
                content TEXT NOT NULL,
                created_at TEXT NOT NULL,
                content_sha256 TEXT NOT NULL,
                UNIQUE(conversation_id, sequence)
            );
            PRAGMA user_version = 3;
            COMMIT;
            """
        )

    @staticmethod
    def _migrate_v3_to_v4(connection: sqlite3.Connection) -> None:
        columns = {str(row[1]) for row in connection.execute("PRAGMA table_info(memory_items)")}
        connection.execute("BEGIN IMMEDIATE")
        try:
            additions = {
                "confidence": (
                    "ALTER TABLE memory_items ADD COLUMN confidence REAL NOT NULL DEFAULT 1.0 "
                    "CHECK(confidence >= 0.0 AND confidence <= 1.0)"
                ),
                "evidence": (
                    "ALTER TABLE memory_items ADD COLUMN evidence TEXT NOT NULL DEFAULT 'imported'"
                ),
                "expires_at": "ALTER TABLE memory_items ADD COLUMN expires_at TEXT",
                "last_confirmed_at": ("ALTER TABLE memory_items ADD COLUMN last_confirmed_at TEXT"),
            }
            for name, statement in additions.items():
                if name not in columns:
                    connection.execute(statement)
            connection.execute("PRAGMA user_version = 4")
            connection.execute("COMMIT")
        except Exception:
            connection.execute("ROLLBACK")
            raise

    def _migrate_v4_to_v5(self, connection: sqlite3.Connection) -> None:
        columns = {str(row[1]) for row in connection.execute("PRAGMA table_info(memory_items)")}
        encryption_columns = {
            "nonce",
            "ciphertext",
            "source_digest",
            "tags_digest_json",
        }
        if columns & encryption_columns:
            if not encryption_columns.issubset(columns):
                raise MemoryStoreError("memory encryption migration is incomplete")
            security_table = connection.execute(
                """
                SELECT 1 FROM sqlite_master
                WHERE type = 'table' AND name = 'memory_security'
                """
            ).fetchone()
            if security_table is None:
                raise MemoryStoreError("memory encryption metadata is missing")
            connection.execute("PRAGMA user_version = 5")
            return
        connection.execute("BEGIN IMMEDIATE")
        try:
            connection.execute("ALTER TABLE memory_items ADD COLUMN nonce BLOB")
            connection.execute("ALTER TABLE memory_items ADD COLUMN ciphertext BLOB")
            connection.execute("ALTER TABLE memory_items ADD COLUMN source_digest TEXT")
            connection.execute(
                "ALTER TABLE memory_items ADD COLUMN tags_digest_json TEXT NOT NULL DEFAULT '[]'"
            )
            connection.execute(
                """
                CREATE INDEX memory_items_namespace_source_digest
                ON memory_items(namespace, source_digest)
                """
            )
            connection.execute("DROP TABLE memory_fts")
            connection.execute(
                "CREATE VIRTUAL TABLE memory_fts USING fts5(content, tokenize='ascii')"
            )
            connection.execute(
                """
                CREATE TABLE memory_security (
                    singleton INTEGER PRIMARY KEY CHECK(singleton = 1),
                    key_identifier TEXT NOT NULL CHECK(length(key_identifier) = 64)
                )
                """
            )

            rows = connection.execute(
                """
                SELECT row_id, memory_id, namespace, kind, content, source, tags_json,
                       created_at, updated_at, content_sha256, confidence, evidence,
                       expires_at, last_confirmed_at
                FROM memory_items
                ORDER BY row_id ASC
                """
            ).fetchall()
            for row in rows:
                record = self._plaintext_record_from_migration(row)
                nonce, ciphertext, source_digest, tag_digests, blind_content = (
                    self._seal_record(record)
                )
                connection.execute(
                    """
                    UPDATE memory_items SET
                        content = '', source = NULL, tags_json = '[]', nonce = ?,
                        ciphertext = ?, source_digest = ?, tags_digest_json = ?
                    WHERE row_id = ?
                    """,
                    (
                        nonce,
                        ciphertext,
                        source_digest,
                        tag_digests,
                        row["row_id"],
                    ),
                )
                connection.execute(
                    "INSERT INTO memory_fts(rowid, content) VALUES (?, ?)",
                    (row["row_id"], blind_content),
                )
            connection.execute(
                "INSERT INTO memory_security(singleton, key_identifier) VALUES (1, ?)",
                (self._cipher.key_identifier,),
            )
            connection.execute("PRAGMA user_version = 5")
            connection.execute("COMMIT")
        except Exception:
            connection.execute("ROLLBACK")
            raise

    @staticmethod
    def _verify_schema(connection: sqlite3.Connection) -> None:
        names = {
            str(row[0])
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type IN ('table', 'view')"
            )
        }
        if not {
            "memory_items",
            "memory_fts",
            "memory_embeddings",
            "memory_security",
            "conversations",
            "conversation_turns",
        }.issubset(names):
            raise MemoryStoreError("memory schema is incomplete")
        columns = {str(row[1]) for row in connection.execute("PRAGMA table_info(memory_items)")}
        if not {
            "confidence",
            "evidence",
            "expires_at",
            "last_confirmed_at",
            "nonce",
            "ciphertext",
            "source_digest",
            "tags_digest_json",
        }.issubset(columns):
            raise MemoryStoreError("memory evolution schema is incomplete")

    def _verify_encryption_key(self, connection: sqlite3.Connection) -> None:
        row = connection.execute(
            "SELECT key_identifier FROM memory_security WHERE singleton = 1"
        ).fetchone()
        if row is None or row["key_identifier"] != self._cipher.key_identifier:
            self._mark_compromised("memory_encryption_key_mismatch")

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

    def _secure_database_files(self) -> None:
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

    def _require_initialized(self) -> None:
        if self._compromised:
            raise DecryptionAuthError("memory subsystem is locked after authentication failure")
        if not self._initialized:
            raise MemoryStoreError("memory store is not initialized")

    @staticmethod
    def _reject_secret_material(content: str) -> None:
        if contains_likely_secret_material(content):
            raise SecretMaterialError("credential-like material belongs in Keychain")

    @staticmethod
    def _validate_namespace(namespace: str) -> None:
        if not re.fullmatch(NAMESPACE_PATTERN, namespace):
            raise MemoryQueryError("invalid memory namespace")

    def _fts_query(self, query: str) -> str:
        try:
            return self._cipher.blind_query(query)
        except ValueError as error:
            raise MemoryQueryError("memory query has no searchable terms") from error

    def _seal_record(
        self,
        record: MemoryRecord,
    ) -> tuple[bytes, bytes, str | None, str, str]:
        document = {
            "content": record.content,
            "source": record.source,
            "tags": list(record.tags),
            "kind": record.kind.value,
            "created_at": record.created_at.isoformat(),
            "updated_at": record.updated_at.isoformat(),
            "content_sha256": record.content_sha256,
            "confidence": record.confidence,
            "evidence": record.evidence.value,
            "expires_at": record.expires_at.isoformat() if record.expires_at else None,
            "last_confirmed_at": (
                record.last_confirmed_at.isoformat() if record.last_confirmed_at else None
            ),
        }
        sealed = self._cipher.seal(
            namespace=record.namespace,
            memory_id=str(record.memory_id),
            document=document,
        )
        source_digest = (
            self._cipher.blind_exact(record.source) if record.source is not None else None
        )
        tag_digests = json.dumps(
            [self._cipher.blind_exact(tag) for tag in record.tags],
            separators=(",", ":"),
        )
        return (
            sealed.nonce,
            sealed.ciphertext,
            source_digest,
            tag_digests,
            self._cipher.blind_search_document(record.content),
        )

    def _record_from_row(self, row: sqlite3.Row) -> MemoryRecord:
        memory_id = row["memory_id"]
        namespace = row["namespace"]
        if not isinstance(memory_id, str) or not isinstance(namespace, str):
            self._mark_compromised("memory_row_identity_invalid")
        try:
            document = self._cipher.open(
                namespace=namespace,
                memory_id=memory_id,
                nonce=row["nonce"],
                ciphertext=row["ciphertext"],
            )
        except RowAuthenticationError:
            self._mark_compromised("memory_row_aead_authentication_failed")

        expected_keys = {
            "content",
            "source",
            "tags",
            "kind",
            "created_at",
            "updated_at",
            "content_sha256",
            "confidence",
            "evidence",
            "expires_at",
            "last_confirmed_at",
        }
        if set(document) != expected_keys:
            self._mark_compromised("memory_row_document_shape_invalid")
        try:
            content = self._verified_content(document["content"], document["content_sha256"])
            tags = tuple(document["tags"])
            record = MemoryRecord(
                memory_id=memory_id,
                namespace=namespace,
                kind=document["kind"],
                content=content,
                source=document["source"],
                tags=tags,
                created_at=datetime.fromisoformat(document["created_at"]),
                updated_at=datetime.fromisoformat(document["updated_at"]),
                content_sha256=document["content_sha256"],
                confidence=document["confidence"],
                evidence=document["evidence"],
                expires_at=(
                    datetime.fromisoformat(document["expires_at"])
                    if document["expires_at"]
                    else None
                ),
                last_confirmed_at=(
                    datetime.fromisoformat(document["last_confirmed_at"])
                    if document["last_confirmed_at"]
                    else None
                ),
            )
            structural = {
                "kind": row["kind"],
                "created_at": row["created_at"],
                "updated_at": row["updated_at"],
                "content_sha256": row["content_sha256"],
                "confidence": row["confidence"],
                "evidence": row["evidence"],
                "expires_at": row["expires_at"],
                "last_confirmed_at": row["last_confirmed_at"],
            }
            if any(document[name] != value for name, value in structural.items()):
                self._mark_compromised("memory_row_metadata_mismatch")
            source_digest = (
                self._cipher.blind_exact(record.source) if record.source is not None else None
            )
            tag_digests = json.dumps(
                [self._cipher.blind_exact(tag) for tag in record.tags],
                separators=(",", ":"),
            )
            if (
                row["content"] != ""
                or row["source"] is not None
                or row["tags_json"] != "[]"
                or row["source_digest"] != source_digest
                or row["tags_digest_json"] != tag_digests
            ):
                self._mark_compromised("memory_row_lookup_metadata_mismatch")
            return record
        except DecryptionAuthError:
            raise
        except (KeyError, MemoryStoreError, TypeError, ValueError) as error:
            self._mark_compromised("memory_row_decrypted_payload_invalid", cause=error)

    @staticmethod
    def _plaintext_record_from_migration(row: sqlite3.Row) -> MemoryRecord:
        content = SQLiteMemoryStore._verified_content(row["content"], row["content_sha256"])
        try:
            return MemoryRecord(
                memory_id=row["memory_id"],
                namespace=row["namespace"],
                kind=row["kind"],
                content=content,
                source=row["source"],
                tags=tuple(json.loads(row["tags_json"])),
                created_at=datetime.fromisoformat(row["created_at"]),
                updated_at=datetime.fromisoformat(row["updated_at"]),
                content_sha256=row["content_sha256"],
                confidence=row["confidence"],
                evidence=row["evidence"],
                expires_at=datetime.fromisoformat(row["expires_at"])
                if row["expires_at"]
                else None,
                last_confirmed_at=datetime.fromisoformat(row["last_confirmed_at"])
                if row["last_confirmed_at"]
                else None,
            )
        except (TypeError, ValueError) as error:
            raise MemoryStoreError("stored migration record is invalid") from error

    @staticmethod
    def _conversation_from_row(row: sqlite3.Row) -> ConversationRecord:
        return ConversationRecord(
            conversation_id=row["conversation_id"],
            namespace=row["namespace"],
            title=row["title"],
            created_at=datetime.fromisoformat(row["created_at"]),
            updated_at=datetime.fromisoformat(row["updated_at"]),
        )

    @staticmethod
    def _turn_from_row(row: sqlite3.Row) -> ConversationTurn:
        content = SQLiteMemoryStore._verified_content(row["content"], row["content_sha256"])
        try:
            return ConversationTurn(
                turn_id=row["turn_id"],
                conversation_id=row["conversation_id"],
                sequence=row["sequence"],
                role=row["role"],
                content=content,
                created_at=datetime.fromisoformat(row["created_at"]),
                content_sha256=row["content_sha256"],
            )
        except (TypeError, ValueError) as error:
            raise MemoryStoreError("stored conversation turn is invalid") from error

    def _hit_from_row(
        self,
        row: sqlite3.Row,
        *,
        score: float | None = None,
    ) -> MemorySearchHit:
        record = self._record_from_row(row)
        encoded = record.content.encode("utf-8")[:MAX_MEMORY_EXCERPT_BYTES]
        excerpt = encoded.decode("utf-8", errors="ignore")
        try:
            return MemorySearchHit(
                memory_id=record.memory_id,
                namespace=record.namespace,
                kind=record.kind,
                excerpt=excerpt,
                source=record.source,
                tags=record.tags,
                updated_at=record.updated_at,
                content_sha256=record.content_sha256,
                score=max(0.0, -float(row["rank"])) if score is None else score,
                confidence=record.confidence,
                evidence=record.evidence,
                expires_at=record.expires_at,
                last_confirmed_at=record.last_confirmed_at,
            )
        except (TypeError, ValueError) as error:
            raise MemoryStoreError("stored memory search row is invalid") from error

    def _mark_compromised(
        self,
        reason: str,
        *,
        cause: Exception | None = None,
    ) -> NoReturn:
        self._compromised = True
        if self._on_auth_failure is not None:
            try:
                self._on_auth_failure(reason)
            except Exception:
                pass
        error = DecryptionAuthError("memory authentication failed; subsystem locked")
        if cause is not None:
            raise error from cause
        raise error

    @staticmethod
    def _verified_content(content: object, expected_sha256: object) -> str:
        if (
            not isinstance(content, str)
            or not isinstance(expected_sha256, str)
            or MemoryRecord.digest_content(content) != expected_sha256
        ):
            raise MemoryStoreError("stored content hash is invalid")
        return content

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
