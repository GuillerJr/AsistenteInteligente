from __future__ import annotations

import hashlib
import json
import math
import re
import sqlite3
import threading
from collections.abc import Callable, Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import NoReturn
from urllib.parse import quote
from uuid import UUID

try:
    import sqlite_vec
except ImportError:  # pragma: no cover - supported deployment installs the accelerator
    sqlite_vec = None

from aegis_core.memory.capacity import MemoryCapacityManager
from aegis_core.memory.codec import MemoryRowCodec
from aegis_core.memory.contracts import (
    NAMESPACE_PATTERN,
    TAG_PATTERN,
    ConversationRecord,
    ConversationTurn,
    MemoryEvidence,
    MemoryKind,
    MemoryRecord,
    MemorySearchHit,
)
from aegis_core.memory.conversation_repository import ConversationRepository
from aegis_core.memory.crypto import MemoryRowCipher
from aegis_core.memory.errors import (
    ConversationCapacityError,
    DatabaseCapacityError,
    DecryptionAuthError,
    MemoryCapacityError,
    MemoryNotFoundError,
    MemoryQueryError,
    MemorySecurityError,
    MemoryStoreError,
    SecretMaterialError,
)
from aegis_core.memory.graph_repository import (
    GRAPH_EMBEDDING_DIMENSIONS,
    GraphRepository,
)
from aegis_core.memory.migrations import EncryptedMemoryMigrator
from aegis_core.memory.records import (
    GraphEdgeRecord,
    GraphEmbeddingCandidate,
    GraphNodeRecord,
    GraphSeedRecord,
    MemoryStorageMetrics,
    SpotlightGraphRecord,
)
from aegis_core.memory.schema import (
    APPLICATION_ID,
    SCHEMA_VERSION,
    ensure_current_schema,
)
from aegis_core.memory.storage import SQLiteStorageGuard
from aegis_core.secrets import contains_likely_secret_material

__all__ = [
    "APPLICATION_ID",
    "DECAY_EVICTION_THRESHOLD",
    "EPISODIC_DECAY_LAMBDA",
    "MAX_HYBRID_MEMORY_PAYLOAD_BYTES",
    "MEMORY_DECAY_LAMBDAS",
    "RRF_RANK_CONSTANT",
    "SCHEMA_VERSION",
    "ConversationCapacityError",
    "DatabaseCapacityError",
    "DecryptionAuthError",
    "GraphEdgeRecord",
    "GraphEmbeddingCandidate",
    "GraphNodeRecord",
    "GraphSeedRecord",
    "MemoryCapacityError",
    "MemoryNotFoundError",
    "MemoryQueryError",
    "MemorySecurityError",
    "MemoryStorageMetrics",
    "MemoryStoreError",
    "SQLiteMemoryStore",
    "SecretMaterialError",
    "SpotlightGraphRecord",
    "calculate_decayed_confidence",
]

MAX_NODE_EMBEDDINGS = 2_000
MAX_NAMESPACE_MEMORIES = 2_000
RRF_RANK_CONSTANT = 60.0
MAX_HYBRID_MEMORY_PAYLOAD_BYTES = 4_096
DECAY_EVICTION_THRESHOLD = 0.35
EPISODIC_DECAY_LAMBDA = 1.15e-6
# ES: λ expresa cuánto “envejece” una clase de recuerdo por segundo. Con 1.15e-6,
# la semivida de un episodio es ln(2)/λ ≈ 7 días; una preferencia usa λ=0 porque
# debe cambiar por evidencia explícita del dueño, no por el simple paso del tiempo.
# EN: λ is the per-second forgetting rate. Episodic memories have an approximately
# seven-day half-life, while stable preferences remain unchanged until new evidence arrives.
MEMORY_DECAY_LAMBDAS: dict[str, float] = {
    MemoryKind.EPISODIC.value: EPISODIC_DECAY_LAMBDA,
    MemoryKind.PREFERENCE.value: 0.0,
    MemoryKind.SEMANTIC.value: 0.0,
    MemoryKind.SUMMARY.value: 0.0,
}


def calculate_decayed_confidence(
    initial_confidence: float,
    kind: str,
    reference_timestamp: str,
    as_of_timestamp: str,
) -> float:
    """Return the exponential forgetting curve C(t)=C₀·e^(-λΔt).

    ES: C₀ es la confianza autenticada al crear o confirmar el recuerdo, Δt son
    los segundos transcurridos y λ pertenece a su clase. Cuando λ=0, e⁰=1 y la
    confianza no decae. Todos los timestamps deben incluir zona horaria para que
    dos procesos no calculen edades distintas.

    EN: C₀ is the authenticated confidence, Δt is elapsed time in seconds, and λ
    is the memory-kind decay rate. A zero λ keeps stable preferences unchanged.
    """
    if (
        isinstance(initial_confidence, bool)
        or not isinstance(initial_confidence, (int, float))
        or not math.isfinite(initial_confidence)
        or not 0.0 <= float(initial_confidence) <= 1.0
        or kind not in MEMORY_DECAY_LAMBDAS
    ):
        raise ValueError("memory decay inputs are invalid")
    reference = datetime.fromisoformat(reference_timestamp)
    as_of = datetime.fromisoformat(as_of_timestamp)
    if reference.tzinfo is None or as_of.tzinfo is None:
        raise ValueError("memory decay timestamps must be timezone-aware")
    elapsed_seconds = max(0.0, (as_of - reference).total_seconds())
    return float(initial_confidence) * math.exp(-MEMORY_DECAY_LAMBDAS[kind] * elapsed_seconds)


class _VectorAccelerationUnavailable(RuntimeError):
    pass


class SQLiteMemoryStore:
    def __init__(
        self,
        path: Path,
        *,
        max_entries: int = 50_000,
        max_namespace_entries: int | None = None,
        max_node_embeddings: int = MAX_NODE_EMBEDDINGS,
        expected_uid: int | None = None,
        encryption_secret: bytes,
        on_auth_failure: Callable[[str], None] | None = None,
    ) -> None:
        if max_entries < 1:
            raise ValueError("memory capacity must be positive")
        namespace_capacity = (
            min(max_entries, MAX_NAMESPACE_MEMORIES)
            if max_namespace_entries is None
            else max_namespace_entries
        )
        if not 1 <= namespace_capacity <= min(max_entries, 50_000):
            raise ValueError("memory namespace capacity is out of range")
        if not 1 <= max_node_embeddings <= MAX_NODE_EMBEDDINGS:
            raise ValueError("graph embedding capacity is out of range")
        self._path = path
        self._max_entries = max_entries
        self._max_namespace_entries = namespace_capacity
        self._max_node_embeddings = max_node_embeddings
        self._storage = SQLiteStorageGuard(path, expected_uid=expected_uid)
        self._cipher = MemoryRowCipher(encryption_secret)
        self._on_auth_failure = on_auth_failure
        self._row_codec = MemoryRowCodec(
            self._cipher,
            on_compromised=lambda reason, cause: self._mark_compromised(
                reason,
                cause=cause,
            ),
        )
        self._graph_repository = GraphRepository(
            self._cipher,
            max_embeddings=max_node_embeddings,
            on_compromised=lambda reason, cause: self._mark_compromised(
                reason,
                cause=cause,
            ),
        )
        self._migrator = EncryptedMemoryMigrator(
            row_codec=self._row_codec,
            graph_repository=self._graph_repository,
            key_identifier=self._cipher.key_identifier,
        )
        self._conversation_repository = ConversationRepository(self._row_codec)
        self._capacity = MemoryCapacityManager(
            max_namespace_entries=namespace_capacity,
            graph_repository=self._graph_repository,
        )
        self._graph_change_listener: Callable[[str], None] | None = None
        self._lock = threading.RLock()
        self._initialized = False
        self._compromised = False

    @property
    def path(self) -> Path:
        return self._path

    def set_graph_change_listener(self, listener: Callable[[str], None] | None) -> None:
        with self._lock:
            self._graph_change_listener = listener

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
            self._storage.prepare()
            with self._connect(load_vector_extension=True) as connection:
                ensure_current_schema(
                    connection,
                    key_identifier=self._cipher.key_identifier,
                    stateful_migrations={
                        4: self._migrator.migrate_v4_to_v5,
                        5: self._migrator.migrate_v5_to_v6,
                        6: self._migrator.migrate_v6_to_v7,
                    },
                )
                self._verify_encryption_key(connection)
                connection.execute("BEGIN IMMEDIATE")
                self._graph_repository.prune_embeddings(connection)
                if connection.execute("PRAGMA quick_check").fetchone()[0] != "ok":
                    raise MemoryStoreError("memory database integrity check failed")
            self._secure_database_files()
            self._initialized = True

    def performance_metrics(self, *, namespace: str) -> MemoryStorageMetrics:
        """Return content-free counters for the bounded graph vector index."""
        self._require_initialized()
        self._validate_namespace(namespace)
        with (
            self._lock,
            self._connect(
                read_only=True,
                load_vector_extension=True,
            ) as connection,
        ):
            row = connection.execute(
                """
                SELECT
                    (SELECT COUNT(*) FROM memory_items) AS memory_items,
                    (SELECT COUNT(*) FROM memory_items WHERE namespace = ?) AS namespace_items,
                    (
                        SELECT COUNT(*) FROM node_embedding_metadata
                        WHERE namespace = ?
                    ) AS namespace_node_embeddings
                """,
                (namespace, namespace),
            ).fetchone()
        if row is None:
            raise MemoryStoreError("memory performance counters are unavailable")
        embedding_count = int(row["namespace_node_embeddings"])
        return MemoryStorageMetrics(
            memory_items=int(row["memory_items"]),
            memory_capacity=self._max_entries,
            namespace_memory_items=int(row["namespace_items"]),
            namespace_memory_capacity=self._max_namespace_entries,
            namespace_node_embeddings=embedding_count,
            namespace_node_embedding_capacity=self._max_node_embeddings,
            graph_index_bytes=embedding_count * GRAPH_EMBEDDING_DIMENSIONS * 4,
            sqlite_vec_loaded=True,
        )

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
        nonce, ciphertext, source_digest, tag_digests, blind_content = self._row_codec.seal_record(
            record
        )
        with self._lock, self._connect(load_vector_extension=True) as connection:
            try:
                self._capacity.begin(connection)
                self._capacity.evict_namespace_fifo(
                    connection,
                    namespace=record.namespace,
                    reserve_slot=True,
                )
                count = int(connection.execute("SELECT COUNT(*) FROM memory_items").fetchone()[0])
                if count >= self._max_entries:
                    raise DatabaseCapacityError("global memory capacity reached")
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
                self._graph_repository.upsert_record(connection, record)
                connection.commit()
            except (sqlite3.Error, DatabaseCapacityError) as error:
                self._capacity.rollback(connection, error)
        self._secure_database_files()
        self._notify_graph_changed(record.namespace)
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
        with (
            self._lock,
            self._connect(load_vector_extension=True) as connection,
            self._capacity.transaction(connection),
        ):
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
                self._capacity.evict_namespace_fifo(
                    connection,
                    namespace=namespace,
                    reserve_slot=True,
                )
                count = int(connection.execute("SELECT COUNT(*) FROM memory_items").fetchone()[0])
                if count >= self._max_entries:
                    raise DatabaseCapacityError("global memory capacity reached")
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
                    self._row_codec.seal_record(record)
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
                previous = self._row_codec.record_from_row(existing)
                self._graph_repository.delete_memory(connection, str(previous.memory_id))
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
                    self._row_codec.seal_record(record)
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
                "INSERT INTO memory_fts(rowid, content) VALUES (?, ?)",
                (row_id, blind_content),
            )
            self._graph_repository.upsert_record(connection, record)
        self._secure_database_files()
        self._notify_graph_changed(namespace)
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
        as_of = datetime.now(UTC).isoformat()
        with self._lock, self._connect(read_only=True) as connection:
            rows = connection.execute(
                """
                SELECT m.memory_id, m.namespace, m.kind, m.content, m.source,
                       m.tags_json, m.created_at, m.updated_at, m.content_sha256,
                       m.confidence, m.evidence, m.expires_at, m.last_confirmed_at,
                       m.nonce, m.ciphertext, m.source_digest, m.tags_digest_json,
                       aegis_decay(
                           m.confidence,
                           m.kind,
                           COALESCE(m.last_confirmed_at, m.updated_at),
                           ?
                       ) AS decayed_confidence
                FROM memory_items AS m
                WHERE m.namespace = ?
                  AND (m.expires_at IS NULL OR m.expires_at > ?)
                  AND EXISTS (
                      SELECT 1 FROM json_each(m.tags_digest_json) WHERE value = ?
                  )
                ORDER BY m.updated_at DESC, m.memory_id ASC
                LIMIT ?
                """,
                (as_of, namespace, as_of, tag_digest, limit),
            ).fetchall()
        return tuple(self._row_codec.hit_from_row(row, score=1.0) for row in rows)

    def delete_by_source(self, *, namespace: str, source: str) -> bool:
        self._require_initialized()
        self._validate_namespace(namespace)
        if not source or len(source) > 256:
            raise MemoryQueryError("invalid memory source")
        source_digest = self._cipher.blind_exact(source)
        with self._lock, self._connect(load_vector_extension=True) as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                """
                SELECT row_id, memory_id FROM memory_items
                WHERE namespace = ? AND source_digest = ?
                ORDER BY updated_at DESC, memory_id ASC
                LIMIT 1
                """,
                (namespace, source_digest),
            ).fetchone()
            if row is None:
                return False
            self._graph_repository.delete_memory(connection, str(row["memory_id"]))
            connection.execute("DELETE FROM memory_fts WHERE rowid = ?", (row["row_id"],))
            connection.execute("DELETE FROM memory_items WHERE row_id = ?", (row["row_id"],))
        self._secure_database_files()
        self._notify_graph_changed(namespace)
        return True

    def delete_by_tag(self, *, namespace: str, tag: str) -> int:
        self._require_initialized()
        self._validate_namespace(namespace)
        if not re.fullmatch(TAG_PATTERN, tag):
            raise MemoryQueryError("invalid memory tag")
        tag_digest = self._cipher.blind_exact(tag)
        with self._lock, self._connect(load_vector_extension=True) as connection:
            connection.execute("BEGIN IMMEDIATE")
            rows = connection.execute(
                """
                SELECT m.row_id, m.memory_id
                FROM memory_items AS m
                WHERE m.namespace = ?
                  AND EXISTS (
                      SELECT 1 FROM json_each(m.tags_digest_json) WHERE value = ?
                  )
                """,
                (namespace, tag_digest),
            ).fetchall()
            for row in rows:
                self._graph_repository.delete_memory(connection, str(row["memory_id"]))
                connection.execute("DELETE FROM memory_fts WHERE rowid = ?", (row["row_id"],))
                connection.execute(
                    "DELETE FROM memory_items WHERE row_id = ?",
                    (row["row_id"],),
                )
        self._secure_database_files()
        if rows:
            self._notify_graph_changed(namespace)
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
        return self._row_codec.record_from_row(row)

    def graph_nodes_for_memory(
        self,
        *,
        namespace: str,
        memory_id: UUID,
    ) -> tuple[GraphEmbeddingCandidate, ...]:
        self._require_initialized()
        self._validate_namespace(namespace)
        with self._lock, self._connect(read_only=True) as connection:
            rows = connection.execute(
                """
                SELECT DISTINCT n.node_id, n.namespace, n.name, n.type,
                       n.properties_json, n.memory_id, n.content_sha256,
                       n.nonce, n.ciphertext, n.created_at, n.updated_at
                FROM nodes AS n
                LEFT JOIN edges AS e
                  ON e.memory_id = ?
                 AND (e.source_id = n.node_id OR e.target_id = n.node_id)
                WHERE n.namespace = ? AND (n.memory_id = ? OR e.edge_id IS NOT NULL)
                ORDER BY n.updated_at DESC, n.node_id ASC
                """,
                (str(memory_id), namespace, str(memory_id)),
            ).fetchall()
        return tuple(self._graph_repository.embedding_candidate(row) for row in rows)

    def spotlight_graph_records(
        self,
        *,
        namespace: str,
        after_node_id: str | None = None,
        limit: int = 100,
    ) -> tuple[SpotlightGraphRecord, ...]:
        """Decrypt only bounded graph labels for an explicitly enabled native index sync."""
        self._require_initialized()
        self._validate_namespace(namespace)
        if not 1 <= limit <= 100:
            raise MemoryQueryError("Spotlight graph batch limit is out of range")
        if after_node_id is not None:
            try:
                UUID(after_node_id)
            except ValueError as error:
                raise MemoryQueryError("Spotlight graph cursor is invalid") from error
        with self._lock, self._connect(read_only=True) as connection:
            rows = connection.execute(
                """
                SELECT node_id, namespace, name, type, properties_json, memory_id,
                       content_sha256, nonce, ciphertext, created_at, updated_at
                FROM nodes
                WHERE namespace = ? AND (? IS NULL OR node_id > ?)
                ORDER BY node_id ASC
                LIMIT ?
                """,
                (namespace, after_node_id, after_node_id, limit),
            ).fetchall()
            records: list[SpotlightGraphRecord] = []
            for row in rows:
                node = self._graph_repository.node_from_row(row)
                edge_rows = connection.execute(
                    """
                    SELECT e.type, e.source_id, e.target_id,
                           n.node_id, n.namespace, n.name, n.type AS node_type,
                           n.properties_json, n.memory_id, n.content_sha256,
                           n.nonce, n.ciphertext, n.created_at, n.updated_at
                    FROM edges AS e
                    JOIN nodes AS n
                      ON n.node_id = CASE
                          WHEN e.source_id = ? THEN e.target_id
                          ELSE e.source_id
                      END
                    WHERE e.namespace = ? AND (e.source_id = ? OR e.target_id = ?)
                    ORDER BY e.type ASC, n.node_id ASC
                    LIMIT 32
                    """,
                    (str(node.node_id), namespace, str(node.node_id), str(node.node_id)),
                ).fetchall()
                relationships: list[tuple[str, str, str]] = []
                for edge in edge_rows:
                    neighbor = self._graph_repository.node_from_row(
                        {
                            "node_id": edge["node_id"],
                            "namespace": edge["namespace"],
                            "name": edge["name"],
                            "type": edge["node_type"],
                            "properties_json": edge["properties_json"],
                            "memory_id": edge["memory_id"],
                            "content_sha256": edge["content_sha256"],
                            "nonce": edge["nonce"],
                            "ciphertext": edge["ciphertext"],
                            "created_at": edge["created_at"],
                            "updated_at": edge["updated_at"],
                        }
                    )
                    direction = "outgoing" if edge["source_id"] == str(node.node_id) else "incoming"
                    relationships.append((direction, str(edge["type"]), neighbor.name))
                records.append(
                    SpotlightGraphRecord(
                        node_id=node.node_id,
                        name=node.name,
                        type=node.type,
                        content_sha256=node.content_sha256,
                        relationships=tuple(relationships),
                    )
                )
        return tuple(records)

    def list_missing_graph_embeddings(
        self,
        *,
        namespace: str,
        model_id: str,
        limit: int = 500,
    ) -> tuple[GraphEmbeddingCandidate, ...]:
        self._require_initialized()
        self._validate_namespace(namespace)
        if not model_id or len(model_id) > 256 or not 1 <= limit <= MAX_NODE_EMBEDDINGS:
            raise MemoryQueryError("invalid graph embedding backfill query")
        with self._lock, self._connect(read_only=True) as connection:
            rows = connection.execute(
                """
                SELECT n.node_id, n.namespace, n.name, n.type, n.properties_json,
                       n.memory_id, n.content_sha256, n.nonce, n.ciphertext,
                       n.created_at, n.updated_at
                FROM nodes AS n
                LEFT JOIN node_embedding_metadata AS e
                  ON e.node_id = n.node_id
                 AND e.model_id = ?
                 AND e.content_sha256 = n.content_sha256
                WHERE n.namespace = ? AND e.node_id IS NULL
                ORDER BY n.updated_at DESC, n.node_id ASC
                LIMIT ?
                """,
                (model_id, namespace, limit),
            ).fetchall()
        return tuple(self._graph_repository.embedding_candidate(row) for row in rows)

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
        as_of = datetime.now(UTC).isoformat()
        with self._lock, self._connect(read_only=True) as connection:
            rows = connection.execute(
                """
                SELECT m.memory_id, m.namespace, m.kind, m.content, m.source,
                       m.tags_json, m.created_at, m.updated_at, m.content_sha256,
                       m.confidence, m.evidence, m.expires_at, m.last_confirmed_at,
                       m.nonce, m.ciphertext, m.source_digest, m.tags_digest_json,
                       aegis_decay(
                           m.confidence,
                           m.kind,
                           COALESCE(m.last_confirmed_at, m.updated_at),
                           ?
                       ) AS decayed_confidence,
                       bm25(memory_fts) AS rank
                FROM memory_fts
                JOIN memory_items AS m ON m.row_id = memory_fts.rowid
                WHERE memory_fts MATCH ? AND m.namespace = ?
                  AND (m.expires_at IS NULL OR m.expires_at > ?)
                ORDER BY rank ASC, m.updated_at DESC, m.memory_id ASC
                LIMIT ?
                """,
                (as_of, fts_query, namespace, as_of, limit),
            ).fetchall()
        return tuple(self._row_codec.hit_from_row(row) for row in rows)

    def hybrid_rrf_search(
        self,
        *,
        namespace: str,
        query: str,
        model_id: str,
        query_vector: tuple[float, ...],
        limit: int = 5,
    ) -> tuple[MemorySearchHit, ...]:
        """Fuse FTS5 and cosine ranks in one SQLite snapshot using formal RRF."""
        self._require_initialized()
        self._validate_namespace(namespace)
        if not model_id or len(model_id) > 256 or not 1 <= limit <= 10:
            raise MemoryQueryError("hybrid memory search arguments are invalid")
        fts_query = self._fts_query(query)
        encoded_vector = self._graph_repository.encode_vector(query_vector)
        as_of = datetime.now(UTC).isoformat()
        candidate_limit = min(self._max_node_embeddings, max(32, limit * 8))
        # ES: RRF no mezcla las escalas incompatibles de BM25 y distancia coseno;
        # mezcla sus *posiciones*. Cada lista aporta 1/(k+rango), o cero cuando el
        # recuerdo no aparece. k=60 suaviza la ventaja del primer puesto para que
        # una coincidencia literal de FTS5 no aplaste por sí sola una coincidencia
        # semántica de sqlite-vec (y viceversa). La suma se calcula dentro de esta
        # única instantánea SQLite, sin copiar vectores a un bucle Python.
        # EN: RRF fuses ranks rather than incomparable raw scores. The k=60
        # smoothing constant prevents either lexical or semantic rank one from
        # dominating the other source, and the complete fusion stays in SQLite.
        #
        # RRF = 1/(60 + lexical_rank) + 1/(60 + semantic_rank)
        sql = """
            WITH
            lexical_candidates AS MATERIALIZED (
                SELECT m.memory_id, bm25(memory_fts) AS lexical_score
                FROM memory_fts
                JOIN memory_items AS m ON m.row_id = memory_fts.rowid
                WHERE memory_fts MATCH ? AND m.namespace = ?
                  AND (m.expires_at IS NULL OR m.expires_at > ?)
                ORDER BY lexical_score ASC, m.updated_at DESC, m.memory_id ASC
                LIMIT ?
            ),
            lexical_ranked AS (
                SELECT memory_id,
                       ROW_NUMBER() OVER (
                           ORDER BY lexical_score ASC, memory_id ASC
                       ) AS lexical_rank
                FROM lexical_candidates
            ),
            semantic_distances AS MATERIALIZED (
                SELECT n.memory_id,
                       MIN(vec_distance_cosine(e.embedding, ?)) AS semantic_distance
                FROM node_embeddings AS e
                JOIN node_embedding_metadata AS metadata
                  ON metadata.node_id = e.node_id
                JOIN nodes AS n ON n.node_id = e.node_id
                JOIN memory_items AS m ON m.memory_id = n.memory_id
                WHERE e.namespace_key = ?
                  AND metadata.namespace = ?
                  AND metadata.model_id = ?
                  AND metadata.content_sha256 = n.content_sha256
                  AND n.namespace = ?
                  AND n.memory_id IS NOT NULL
                  AND (m.expires_at IS NULL OR m.expires_at > ?)
                GROUP BY n.memory_id
                ORDER BY semantic_distance ASC, n.memory_id ASC
                LIMIT ?
            ),
            semantic_ranked AS (
                SELECT memory_id,
                       ROW_NUMBER() OVER (
                           ORDER BY semantic_distance ASC, memory_id ASC
                       ) AS semantic_rank
                FROM semantic_distances
            ),
            candidate_ids AS (
                SELECT memory_id FROM lexical_ranked
                UNION
                SELECT memory_id FROM semantic_ranked
            ),
            fused AS (
                SELECT candidates.memory_id,
                       COALESCE(
                           1.0 / (? + lexical_ranked.lexical_rank),
                           0.0
                       ) + COALESCE(
                           1.0 / (? + semantic_ranked.semantic_rank),
                           0.0
                       ) AS rrf_score
                FROM candidate_ids AS candidates
                LEFT JOIN lexical_ranked
                  ON lexical_ranked.memory_id = candidates.memory_id
                LEFT JOIN semantic_ranked
                  ON semantic_ranked.memory_id = candidates.memory_id
            )
            SELECT m.memory_id, m.namespace, m.kind, m.content, m.source,
                   m.tags_json, m.created_at, m.updated_at, m.content_sha256,
                   m.confidence, m.evidence, m.expires_at, m.last_confirmed_at,
                   m.nonce, m.ciphertext, m.source_digest, m.tags_digest_json,
                   fused.rrf_score,
                   -fused.rrf_score AS rank,
                   aegis_decay(
                       m.confidence,
                       m.kind,
                       COALESCE(m.last_confirmed_at, m.updated_at),
                       ?
                   ) AS decayed_confidence
            FROM fused
            JOIN memory_items AS m ON m.memory_id = fused.memory_id
            ORDER BY fused.rrf_score DESC,
                     decayed_confidence DESC,
                     m.updated_at DESC,
                     m.memory_id ASC
            LIMIT ?
        """
        parameters = (
            fts_query,
            namespace,
            as_of,
            candidate_limit,
            encoded_vector,
            self._graph_repository.namespace_partition_key(namespace),
            namespace,
            model_id,
            namespace,
            as_of,
            candidate_limit,
            RRF_RANK_CONSTANT,
            RRF_RANK_CONSTANT,
            as_of,
            limit,
        )
        with (
            self._lock,
            self._connect(
                read_only=True,
                load_vector_extension=True,
            ) as connection,
        ):
            try:
                connection.execute("BEGIN")
                rows = connection.execute(sql, parameters).fetchall()
                connection.commit()
            except sqlite3.Error as error:
                if connection.in_transaction:
                    connection.rollback()
                raise MemoryQueryError("hybrid memory search failed closed") from error
        hits: list[MemorySearchHit] = []
        payload_bytes = 2
        for row in rows:
            hit = self._row_codec.hit_from_row(row, score=float(row["rrf_score"]))
            encoded_hit = json.dumps(
                hit.model_dump(mode="json"),
                ensure_ascii=False,
                separators=(",", ":"),
            ).encode("utf-8")
            separator_bytes = 1 if hits else 0
            if payload_bytes + separator_bytes + len(encoded_hit) > MAX_HYBRID_MEMORY_PAYLOAD_BYTES:
                break
            hits.append(hit)
            payload_bytes += separator_bytes + len(encoded_hit)
        return tuple(hits)

    def reinforce(
        self,
        *,
        namespace: str,
        memory_id: UUID,
        delta_boost: float = 0.15,
        confirmed_at: datetime | None = None,
    ) -> MemoryRecord:
        """Apply C_new=tanh(C_old+Δ) and authenticate the updated row atomically."""
        self._require_initialized()
        self._validate_namespace(namespace)
        if (
            isinstance(delta_boost, bool)
            or not isinstance(delta_boost, (int, float))
            or not math.isfinite(delta_boost)
            or not 0.0 < float(delta_boost) <= 1.0
        ):
            raise ValueError("memory reinforcement boost is invalid")
        timestamp = confirmed_at or datetime.now(UTC)
        if timestamp.tzinfo is None or timestamp.utcoffset() is None:
            raise ValueError("memory reinforcement timestamp must be timezone-aware")
        with self._lock, self._connect(load_vector_extension=True) as connection:
            try:
                connection.execute("BEGIN IMMEDIATE")
                row = connection.execute(
                    """
                    SELECT row_id, memory_id, namespace, kind, content, source, tags_json,
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
                previous = self._row_codec.record_from_row(row)
                reinforced = previous.model_copy(
                    update={
                        "confidence": math.tanh(previous.confidence + float(delta_boost)),
                        "updated_at": timestamp,
                        "last_confirmed_at": timestamp,
                    }
                )
                nonce, ciphertext, _, _, _ = self._row_codec.seal_record(reinforced)
                connection.execute(
                    """
                    UPDATE memory_items
                    SET updated_at = ?, confidence = ?, last_confirmed_at = ?,
                        nonce = ?, ciphertext = ?
                    WHERE row_id = ? AND namespace = ?
                    """,
                    (
                        timestamp.isoformat(),
                        reinforced.confidence,
                        timestamp.isoformat(),
                        nonce,
                        ciphertext,
                        row["row_id"],
                        namespace,
                    ),
                )
                connection.commit()
            except (sqlite3.Error, MemoryStoreError):
                if connection.in_transaction:
                    connection.rollback()
                raise
        self._secure_database_files()
        return reinforced

    def evict_decayed(
        self,
        *,
        namespace: str,
        threshold: float = DECAY_EVICTION_THRESHOLD,
        as_of: datetime | None = None,
        limit: int = 64,
    ) -> int:
        """Securely evict expired or weak non-stable memories in one transaction."""
        self._require_initialized()
        self._validate_namespace(namespace)
        if (
            isinstance(threshold, bool)
            or not isinstance(threshold, (int, float))
            or not math.isfinite(threshold)
            or not 0.0 < float(threshold) < 1.0
            or not 1 <= limit <= 256
        ):
            raise ValueError("memory decay eviction policy is invalid")
        timestamp = as_of or datetime.now(UTC)
        if timestamp.tzinfo is None or timestamp.utcoffset() is None:
            raise ValueError("memory decay timestamp must be timezone-aware")
        encoded_timestamp = timestamp.isoformat()
        with self._lock, self._connect(load_vector_extension=True) as connection:
            try:
                # ES: secure_delete pide a SQLite rellenar con ceros el contenido
                # liberado dentro de sus páginas antes del commit. Reduce restos
                # recuperables del archivo lógico; no promete borrado físico de
                # cada celda NAND, porque un SSD puede aplicar wear levelling.
                # AES-GCM sigue siendo la defensa de confidencialidad principal.
                # EN: SQLite overwrites freed database-page content with zeros.
                # SSD wear levelling means this is not a physical NAND-erasure
                # guarantee, so authenticated encryption remains mandatory.
                connection.execute("PRAGMA secure_delete = ON")
                connection.execute("BEGIN IMMEDIATE")
                rows = connection.execute(
                    """
                    SELECT row_id, memory_id
                    FROM memory_items
                    WHERE namespace = ?
                      AND (
                          (expires_at IS NOT NULL AND expires_at <= ?)
                          OR (
                              kind != ?
                              AND aegis_decay(
                                  confidence,
                                  kind,
                                  COALESCE(last_confirmed_at, updated_at),
                                  ?
                              ) < ?
                          )
                      )
                    ORDER BY updated_at ASC, row_id ASC
                    LIMIT ?
                    """,
                    (
                        namespace,
                        encoded_timestamp,
                        MemoryKind.PREFERENCE.value,
                        encoded_timestamp,
                        float(threshold),
                        limit,
                    ),
                ).fetchall()
                for row in rows:
                    self._graph_repository.delete_memory(connection, str(row["memory_id"]))
                    connection.execute(
                        "DELETE FROM memory_fts WHERE rowid = ?",
                        (row["row_id"],),
                    )
                    connection.execute(
                        "DELETE FROM memory_items WHERE row_id = ? AND namespace = ?",
                        (row["row_id"], namespace),
                    )
                connection.commit()
            except sqlite3.Error as error:
                if connection.in_transaction:
                    connection.rollback()
                raise MemoryStoreError("memory decay eviction failed closed") from error
        if rows:
            self._secure_database_files()
            self._notify_graph_changed(namespace)
        return len(rows)

    def delete(self, *, namespace: str, memory_id: UUID) -> None:
        self._require_initialized()
        self._validate_namespace(namespace)
        with self._lock, self._connect(load_vector_extension=True) as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                """
                SELECT row_id, memory_id FROM memory_items
                WHERE namespace = ? AND memory_id = ?
                """,
                (namespace, str(memory_id)),
            ).fetchone()
            if row is None:
                raise MemoryNotFoundError("memory does not exist")
            self._graph_repository.delete_memory(connection, str(row["memory_id"]))
            connection.execute("DELETE FROM memory_fts WHERE rowid = ?", (row["row_id"],))
            connection.execute(
                "DELETE FROM memory_items WHERE row_id = ?",
                (row["row_id"],),
            )
        self._secure_database_files()
        self._notify_graph_changed(namespace)

    def put_node_embedding(
        self,
        *,
        namespace: str,
        node_id: UUID,
        model_id: str,
        vector: tuple[float, ...],
        content_sha256: str,
    ) -> None:
        self._require_initialized()
        self._validate_namespace(namespace)
        if not model_id or len(model_id) > 256:
            raise MemoryQueryError("invalid graph embedding model id")
        encoded_vector = self._graph_repository.encode_vector(vector)
        with self._lock, self._connect(load_vector_extension=True) as connection:
            try:
                self._capacity.begin(connection)
                existing_embedding = connection.execute(
                    "SELECT 1 FROM node_embedding_metadata WHERE node_id = ?",
                    (str(node_id),),
                ).fetchone()
                if existing_embedding is None:
                    self._graph_repository.evict_embeddings_fifo(
                        connection,
                        namespace=namespace,
                        reserve_slot=True,
                    )
                row = connection.execute(
                    """
                    SELECT content_sha256 FROM nodes
                    WHERE namespace = ? AND node_id = ?
                    """,
                    (namespace, str(node_id)),
                ).fetchone()
                if row is None:
                    raise MemoryNotFoundError("graph node does not exist")
                if row["content_sha256"] != content_sha256:
                    raise MemoryStoreError("graph node changed before embedding")
                connection.execute(
                    "DELETE FROM node_embeddings WHERE node_id = ?",
                    (str(node_id),),
                )
                connection.execute(
                    """
                    INSERT INTO node_embeddings(
                        node_id, namespace_key, embedding
                    ) VALUES (?, ?, ?)
                    """,
                    (
                        str(node_id),
                        self._graph_repository.namespace_partition_key(namespace),
                        encoded_vector,
                    ),
                )
                connection.execute(
                    """
                    INSERT INTO node_embedding_metadata(
                        node_id, namespace, model_id, content_sha256, created_at
                    ) VALUES (?, ?, ?, ?, ?)
                    ON CONFLICT(node_id) DO UPDATE SET
                        namespace = excluded.namespace,
                        model_id = excluded.model_id,
                        content_sha256 = excluded.content_sha256,
                        created_at = excluded.created_at
                    """,
                    (
                        str(node_id),
                        namespace,
                        model_id,
                        content_sha256,
                        datetime.now(UTC).isoformat(),
                    ),
                )
                self._graph_repository.prune_embeddings(connection)
                connection.commit()
            except (sqlite3.Error, DatabaseCapacityError) as error:
                self._capacity.rollback(connection, error)
        self._secure_database_files()

    def graph_seed_search(
        self,
        *,
        namespace: str,
        model_id: str,
        query_vector: tuple[float, ...],
        limit: int = 5,
    ) -> tuple[GraphSeedRecord, ...]:
        self._require_initialized()
        self._validate_namespace(namespace)
        if not 1 <= limit <= 5:
            raise MemoryQueryError("graph seed limit is out of range")
        encoded = self._graph_repository.encode_vector(query_vector)
        with (
            self._lock,
            self._connect(
                read_only=True,
                load_vector_extension=True,
            ) as connection,
        ):
            rows = connection.execute(
                """
                WITH matches AS (
                    SELECT node_id, distance
                    FROM node_embeddings
                    WHERE embedding MATCH ? AND k = ? AND namespace_key = ?
                    ORDER BY distance ASC
                )
                SELECT matches.node_id, matches.distance
                FROM matches
                JOIN node_embedding_metadata AS metadata
                  ON metadata.node_id = matches.node_id
                JOIN nodes ON nodes.node_id = matches.node_id
                WHERE metadata.namespace = ? AND metadata.model_id = ?
                  AND metadata.content_sha256 = nodes.content_sha256
                ORDER BY matches.distance ASC
                LIMIT ?
                """,
                (
                    encoded,
                    min(self._max_node_embeddings, max(32, limit * 8)),
                    self._graph_repository.namespace_partition_key(namespace),
                    namespace,
                    model_id,
                    limit,
                ),
            ).fetchall()
        seeds: list[GraphSeedRecord] = []
        for row in rows:
            distance = float(row["distance"])
            if not math.isfinite(distance):
                raise MemoryStoreError("graph vector distance is invalid")
            seeds.append(
                GraphSeedRecord(
                    node_id=UUID(str(row["node_id"])),
                    score=math.exp(-4.0 * max(0.0, distance)),
                )
            )
        return tuple(seeds)

    def find_graph_nodes_by_property(
        self,
        *,
        namespace: str,
        property_name: str,
        value: str,
        limit: int = 8,
    ) -> tuple[GraphNodeRecord, ...]:
        """Resolve a sensitive device property through its namespace-isolated blind index."""

        self._require_initialized()
        self._validate_namespace(namespace)
        if property_name not in {"ip_address", "mac_address", "api_token"}:
            raise MemoryQueryError("unsupported graph property lookup")
        if not value or len(value) > 512 or not 1 <= limit <= 16:
            raise MemoryQueryError("graph property lookup is out of range")
        digest = self._graph_repository.property_digest(property_name, value)
        with self._lock, self._connect(read_only=True) as connection:
            rows = connection.execute(
                """
                SELECT n.node_id, n.namespace, n.name, n.type, n.properties_json,
                       n.memory_id, n.content_sha256, n.nonce, n.ciphertext,
                       n.created_at, n.updated_at
                FROM node_property_index AS property
                JOIN nodes AS n ON n.node_id = property.node_id
                WHERE property.namespace = ? AND property.property_name = ?
                  AND property.value_digest = ?
                ORDER BY n.updated_at DESC, n.node_id ASC
                LIMIT ?
                """,
                (namespace, property_name, digest, limit),
            ).fetchall()
        nodes = tuple(self._graph_repository.node_from_row(row) for row in rows)
        if any(node.properties.get(property_name) != value for node in nodes):
            self._mark_compromised("graph_property_blind_index_mismatch")
        return nodes

    def load_graph_neighborhood(
        self,
        *,
        namespace: str,
        seed_ids: tuple[UUID, ...],
        max_nodes: int = 4_096,
        max_edges: int = 16_384,
        depth: int = 3,
    ) -> tuple[tuple[GraphNodeRecord, ...], tuple[GraphEdgeRecord, ...]]:
        self._require_initialized()
        self._validate_namespace(namespace)
        if not seed_ids or len(seed_ids) > 5:
            raise MemoryQueryError("graph seeds are out of range")
        if not 1 <= depth <= 3 or not 1 <= max_nodes <= 4_096 or not 1 <= max_edges <= 16_384:
            raise MemoryQueryError("graph traversal bounds are invalid")
        frontier = {str(item) for item in seed_ids}
        visited = set(frontier)
        edge_rows: dict[str, sqlite3.Row] = {}
        with self._lock, self._connect(read_only=True) as connection:
            for _ in range(depth):
                if not frontier or len(visited) >= max_nodes or len(edge_rows) >= max_edges:
                    break
                placeholders = ",".join("?" for _ in frontier)
                rows = connection.execute(
                    f"""
                    SELECT edge_id, namespace, source_id, target_id, type, weight, memory_id
                    FROM edges
                    WHERE namespace = ?
                      AND (source_id IN ({placeholders}) OR target_id IN ({placeholders}))
                    ORDER BY edge_id ASC
                    LIMIT ?
                    """,
                    (namespace, *frontier, *frontier, max_edges - len(edge_rows)),
                ).fetchall()
                next_frontier: set[str] = set()
                for row in rows:
                    edge_rows[str(row["edge_id"])] = row
                    for key in ("source_id", "target_id"):
                        candidate = str(row[key])
                        if candidate not in visited and len(visited) < max_nodes:
                            visited.add(candidate)
                            next_frontier.add(candidate)
                frontier = next_frontier
            placeholders = ",".join("?" for _ in visited)
            node_rows = connection.execute(
                f"""
                SELECT node_id, namespace, name, type, properties_json, memory_id,
                       content_sha256, nonce, ciphertext, created_at, updated_at
                FROM nodes WHERE namespace = ? AND node_id IN ({placeholders})
                ORDER BY node_id ASC
                """,
                (namespace, *visited),
            ).fetchall()
        nodes = tuple(self._graph_repository.node_from_row(row) for row in node_rows)
        edges = tuple(self._graph_repository.edge_from_row(row) for row in edge_rows.values())
        return nodes, edges

    def create_conversation(
        self,
        *,
        namespace: str,
        title: str | None = None,
        max_conversations: int = 1_000,
    ) -> ConversationRecord:
        self._require_initialized()
        with self._lock, self._connect() as connection:
            record = self._conversation_repository.create(
                connection,
                namespace=namespace,
                title=title,
                max_conversations=max_conversations,
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
            return self._conversation_repository.get(
                connection,
                namespace=namespace,
                conversation_id=conversation_id,
            )

    def conversation_history(
        self,
        *,
        namespace: str,
        conversation_id: UUID,
        limit: int = 12,
    ) -> tuple[ConversationTurn, ...]:
        self._require_initialized()
        self._validate_namespace(namespace)
        with self._lock, self._connect(read_only=True) as connection:
            return self._conversation_repository.history(
                connection,
                namespace=namespace,
                conversation_id=conversation_id,
                limit=limit,
            )

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
        with self._lock, self._connect() as connection:
            turns = self._conversation_repository.append_exchange(
                connection,
                namespace=namespace,
                conversation_id=conversation_id,
                user_content=user_content,
                assistant_content=assistant_content,
                max_turns=max_turns,
            )
        self._secure_database_files()
        return turns

    def delete_conversation(self, *, namespace: str, conversation_id: UUID) -> None:
        self._require_initialized()
        self._validate_namespace(namespace)
        with self._lock, self._connect() as connection:
            self._conversation_repository.delete(
                connection,
                namespace=namespace,
                conversation_id=conversation_id,
            )
        self._secure_database_files()

    def purge_session(self, *, namespace: str, session_id: UUID) -> tuple[bool, int]:
        """Atomically remove one conversation and only memories explicitly bound to it."""
        self._require_initialized()
        self._validate_namespace(namespace)
        source_digests = tuple(
            self._cipher.blind_exact(value)
            for value in (f"conversation:{session_id}", f"session:{session_id}")
        )
        session_tag = f"session.{hashlib.sha256(str(session_id).encode()).hexdigest()[:16]}"
        tag_digest = self._cipher.blind_exact(session_tag)
        with self._lock, self._connect(load_vector_extension=True) as connection:
            connection.execute("PRAGMA secure_delete = ON")
            connection.execute("BEGIN IMMEDIATE")
            try:
                conversation_deleted = self._conversation_repository.delete_optional(
                    connection,
                    namespace=namespace,
                    conversation_id=session_id,
                )
                rows = connection.execute(
                    """
                    SELECT row_id, memory_id FROM memory_items
                    WHERE namespace = ? AND (
                        source_digest IN (?, ?)
                        OR EXISTS (
                            SELECT 1 FROM json_each(tags_digest_json) WHERE value = ?
                        )
                    )
                    ORDER BY row_id ASC
                    """,
                    (namespace, *source_digests, tag_digest),
                ).fetchall()
                for row in rows:
                    self._graph_repository.delete_memory(connection, str(row["memory_id"]))
                    connection.execute(
                        "DELETE FROM memory_fts WHERE rowid = ?",
                        (row["row_id"],),
                    )
                    cursor = connection.execute(
                        "DELETE FROM memory_items WHERE row_id = ? AND namespace = ?",
                        (row["row_id"], namespace),
                    )
                    if cursor.rowcount != 1:
                        raise MemoryStoreError("session purge lost transactional ownership")
                connection.commit()
            except (sqlite3.Error, MemoryStoreError):
                connection.rollback()
                raise
        self._secure_database_files()
        if rows:
            self._notify_graph_changed(namespace)
        return conversation_deleted, len(rows)

    def _connect(
        self,
        *,
        read_only: bool = False,
        load_vector_extension: bool = False,
    ) -> Iterator[sqlite3.Connection]:
        return self._storage.connect(
            read_only=read_only,
            load_vector_extension=load_vector_extension,
            decay_function=calculate_decayed_confidence,
            vector_extension_loader=self._load_vector_extension,
        )

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

    def _verify_encryption_key(self, connection: sqlite3.Connection) -> None:
        row = connection.execute(
            "SELECT key_identifier FROM memory_security WHERE singleton = 1"
        ).fetchone()
        if row is None or row["key_identifier"] != self._cipher.key_identifier:
            self._mark_compromised("memory_encryption_key_mismatch")

    def _secure_database_files(self) -> None:
        self._storage.secure_files()

    def _require_initialized(self) -> None:
        if self._compromised:
            raise DecryptionAuthError("memory subsystem is locked after authentication failure")
        if not self._initialized:
            raise MemoryStoreError("memory store is not initialized")

    def _notify_graph_changed(self, namespace: str) -> None:
        listener = self._graph_change_listener
        if listener is None:
            return
        try:
            listener(namespace)
        except Exception:
            # A secondary native index must not invalidate a committed encrypted record.
            return

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
