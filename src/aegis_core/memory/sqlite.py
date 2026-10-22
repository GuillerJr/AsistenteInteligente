from __future__ import annotations

import hashlib
import math
import re
import sqlite3
import threading
from collections.abc import Callable, Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, NoReturn
from urllib.parse import quote
from uuid import UUID

try:
    import sqlite_vec
except ImportError:  # pragma: no cover - supported deployment installs the accelerator
    sqlite_vec = None

from aegis_core.contracts import DocumentInput, ToolExecutionResult
from aegis_core.failure_boundary import record_boundary_error
from aegis_core.feedback import OwnerFeedback
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
from aegis_core.memory.conversation_codec import ConversationRowCodec
from aegis_core.memory.conversation_migration import migrate_conversations_v8_to_v9
from aegis_core.memory.conversation_repository import ConversationRepository
from aegis_core.memory.conversation_search import (
    ConversationSearchPage,
    ConversationSearchPayload,
    ConversationSearchRepository,
    SearchPosition,
)
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
from aegis_core.memory.graph_embedding_repository import GraphEmbeddingRepository
from aegis_core.memory.graph_read_repository import GraphReadRepository
from aegis_core.memory.graph_repository import (
    GRAPH_EMBEDDING_DIMENSIONS,
    GraphRepository,
)
from aegis_core.memory.item_repository import MemoryItemRepository
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
from aegis_core.memory.search_repository import (
    MAX_HYBRID_MEMORY_PAYLOAD_BYTES,
    RRF_RANK_CONSTANT,
    MemorySearchRepository,
)
from aegis_core.memory.storage import SQLiteStorageGuard
from aegis_core.memory.task_checkpoint import TaskCheckpoint, TaskRecovery
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
        self._conversation_codec = ConversationRowCodec(
            self._cipher,
            on_compromised=lambda reason, cause: self._mark_compromised(reason, cause=cause),
        )
        self._conversation_repository = ConversationRepository(self._conversation_codec)
        self._conversation_search = ConversationSearchRepository(self._conversation_codec)
        self._capacity = MemoryCapacityManager(
            max_namespace_entries=namespace_capacity,
            graph_repository=self._graph_repository,
        )
        self._item_repository = MemoryItemRepository(
            cipher=self._cipher,
            row_codec=self._row_codec,
            graph_repository=self._graph_repository,
            capacity=self._capacity,
            max_entries=max_entries,
        )
        self._search_repository = MemorySearchRepository(
            cipher=self._cipher,
            row_codec=self._row_codec,
            graph_repository=self._graph_repository,
            max_node_embeddings=max_node_embeddings,
        )
        self._graph_reads = GraphReadRepository(self._graph_repository)
        self._graph_embeddings = GraphEmbeddingRepository(
            graph_repository=self._graph_repository,
            capacity=self._capacity,
            max_embeddings=max_node_embeddings,
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
            self._initialized = False
            self._storage.prepare()
            with self._connect(load_vector_extension=True) as connection:
                # Never mutate an encrypted database with a different key, even
                # when it still needs a schema upgrade.
                version = int(connection.execute("PRAGMA user_version").fetchone()[0])
                identity = int(connection.execute("PRAGMA application_id").fetchone()[0])
                if identity == APPLICATION_ID and 1 <= version <= SCHEMA_VERSION:
                    security_metadata = connection.execute(
                        "SELECT 1 FROM sqlite_master WHERE type = 'table' "
                        "AND name = 'memory_security'"
                    ).fetchone()
                    if version >= 5 or security_metadata is not None:
                        self._verify_encryption_key(connection)
                ensure_current_schema(
                    connection,
                    key_identifier=self._cipher.key_identifier,
                    stateful_migrations={
                        4: self._migrator.migrate_v4_to_v5,
                        5: self._migrator.migrate_v5_to_v6,
                        6: self._migrator.migrate_v6_to_v7,
                        9: lambda connection: self._migrator.migrate_v9_to_v10(
                            connection, conversation_codec=self._conversation_codec
                        ),
                        8: lambda connection: migrate_conversations_v8_to_v9(
                            connection, codec=self._conversation_codec
                        ),
                    },
                )
                self._verify_encryption_key(connection)
                connection.execute("BEGIN IMMEDIATE")
                # Metadata-only ordering avoids sorting encrypted conversation bodies
                # for every resumable search page. No searchable text is persisted.
                connection.execute(
                    "CREATE INDEX IF NOT EXISTS conversations_namespace_created "
                    "ON conversations(namespace, created_at DESC, conversation_id DESC)"
                )
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
        with self._lock, self._connect(load_vector_extension=True) as connection:
            self._item_repository.insert(connection, record=record)
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
        with self._lock, self._connect(load_vector_extension=True) as connection:
            record = self._item_repository.upsert_by_source(
                connection,
                namespace=namespace,
                kind=kind,
                content=content,
                source=source,
                tags=tags,
                confidence=confidence,
                evidence=evidence,
                expires_at=expires_at,
                last_confirmed_at=last_confirmed_at,
                now=now,
            )
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
        with self._lock, self._connect(read_only=True) as connection:
            return self._search_repository.list_by_tag(
                connection,
                namespace=namespace,
                tag=tag,
                limit=limit,
            )

    def delete_by_source(self, *, namespace: str, source: str) -> bool:
        self._require_initialized()
        self._validate_namespace(namespace)
        if not source or len(source) > 256:
            raise MemoryQueryError("invalid memory source")
        with self._lock, self._connect(load_vector_extension=True) as connection:
            deleted = self._item_repository.delete_by_source(
                connection,
                namespace=namespace,
                source=source,
            )
        if not deleted:
            return False
        self._secure_database_files()
        self._notify_graph_changed(namespace)
        return True

    def delete_by_tag(self, *, namespace: str, tag: str) -> int:
        self._require_initialized()
        self._validate_namespace(namespace)
        if not re.fullmatch(TAG_PATTERN, tag):
            raise MemoryQueryError("invalid memory tag")
        with self._lock, self._connect(load_vector_extension=True) as connection:
            deleted = self._item_repository.delete_by_tag(
                connection,
                namespace=namespace,
                tag=tag,
            )
        self._secure_database_files()
        if deleted:
            self._notify_graph_changed(namespace)
        return deleted

    def get(self, *, namespace: str, memory_id: UUID) -> MemoryRecord:
        self._require_initialized()
        self._validate_namespace(namespace)
        with self._lock, self._connect(read_only=True) as connection:
            return self._item_repository.get(
                connection,
                namespace=namespace,
                memory_id=memory_id,
            )

    def graph_nodes_for_memory(
        self,
        *,
        namespace: str,
        memory_id: UUID,
    ) -> tuple[GraphEmbeddingCandidate, ...]:
        self._require_initialized()
        self._validate_namespace(namespace)
        with self._lock, self._connect(read_only=True) as connection:
            return self._graph_reads.nodes_for_memory(
                connection,
                namespace=namespace,
                memory_id=memory_id,
            )

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
            return self._graph_reads.spotlight_records(
                connection,
                namespace=namespace,
                after_node_id=after_node_id,
                limit=limit,
            )

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
            return self._graph_embeddings.list_missing(
                connection,
                namespace=namespace,
                model_id=model_id,
                limit=limit,
            )

    def search(
        self,
        *,
        namespace: str,
        query: str,
        limit: int = 5,
    ) -> tuple[MemorySearchHit, ...]:
        self._require_initialized()
        self._validate_namespace(namespace)
        with self._lock, self._connect(read_only=True) as connection:
            return self._search_repository.lexical_search(
                connection,
                namespace=namespace,
                query=query,
                limit=limit,
            )

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
        with (
            self._lock,
            self._connect(
                read_only=True,
                load_vector_extension=True,
            ) as connection,
        ):
            return self._search_repository.hybrid_rrf_search(
                connection,
                namespace=namespace,
                query=query,
                model_id=model_id,
                query_vector=query_vector,
                limit=limit,
            )

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
            reinforced = self._item_repository.reinforce(
                connection,
                namespace=namespace,
                memory_id=memory_id,
                delta_boost=float(delta_boost),
                timestamp=timestamp,
            )
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
        with self._lock, self._connect(load_vector_extension=True) as connection:
            deleted = self._item_repository.evict_decayed(
                connection,
                namespace=namespace,
                threshold=float(threshold),
                timestamp=timestamp,
                limit=limit,
            )
        if deleted:
            self._secure_database_files()
            self._notify_graph_changed(namespace)
        return deleted

    def delete(self, *, namespace: str, memory_id: UUID) -> None:
        self._require_initialized()
        self._validate_namespace(namespace)
        with self._lock, self._connect(load_vector_extension=True) as connection:
            self._item_repository.delete(
                connection,
                namespace=namespace,
                memory_id=memory_id,
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
        with self._lock, self._connect(load_vector_extension=True) as connection:
            self._graph_embeddings.put(
                connection,
                namespace=namespace,
                node_id=node_id,
                model_id=model_id,
                vector=vector,
                content_sha256=content_sha256,
            )
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
        with (
            self._lock,
            self._connect(
                read_only=True,
                load_vector_extension=True,
            ) as connection,
        ):
            return self._graph_embeddings.seed_search(
                connection,
                namespace=namespace,
                model_id=model_id,
                query_vector=query_vector,
                limit=limit,
            )

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
        with self._lock, self._connect(read_only=True) as connection:
            nodes = self._graph_reads.find_nodes_by_property(
                connection,
                namespace=namespace,
                property_name=property_name,
                value=value,
                limit=limit,
            )
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
        with self._lock, self._connect(read_only=True) as connection:
            return self._graph_reads.load_neighborhood(
                connection,
                namespace=namespace,
                seed_ids=seed_ids,
                max_nodes=max_nodes,
                max_edges=max_edges,
                depth=depth,
            )

    def create_conversation(
        self,
        *,
        namespace: str,
        title: str | None = None,
        max_conversations: int = 1_000,
    ) -> ConversationRecord:
        self._require_initialized()
        self._validate_namespace(namespace)
        if title is not None:
            self._reject_secret_material(title)
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

    def set_task_recovery(
        self, *, namespace: str, conversation_id: UUID, recovery: TaskRecovery,
        tool_results: tuple[ToolExecutionResult, ...] = (),
    ) -> None:
        from aegis_core.orchestration.task_progress import advance_checkpoint

        self._require_initialized()
        self._validate_namespace(namespace)
        self._reject_secret_material(recovery.request_text)
        with self._lock, self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            record = self._conversation_repository.get(
                connection, namespace=namespace, conversation_id=conversation_id,
            )
            if recovery.status == "not_dispatched":
                previous = record.task_recovery
                if (
                    previous is None
                    or previous.status != "executing"
                    or previous.request_id != recovery.request_id
                    or previous.call_digest != recovery.call_digest
                    or previous.tool_name != "computer_use"
                    or previous.computer_workflow is not None
                ):
                    return  # A late failure must not erase another operation's uncertainty.
            count = connection.execute(
                "SELECT turn_count FROM conversations WHERE conversation_id = ?",
                (str(conversation_id),),
            ).fetchone()[0]
            progress = record.task_checkpoint
            if tool_results:
                try:
                    checkpoint_request_id = UUID(recovery.request_id)
                except ValueError:
                    checkpoint_request_id = None
                progress = advance_checkpoint(
                    progress, request=recovery.request_text, response="Resultado observado.",
                    sequence=max(1, count), tool_results=tool_results,
                    request_id=checkpoint_request_id,
                )
            sealed = self._conversation_codec.seal_conversation(record.model_copy(update={
                "task_recovery": recovery, "task_checkpoint": progress,
            }), turn_count=count)
            connection.execute(
                "UPDATE conversations SET nonce = ?, ciphertext = ? "
                "WHERE namespace = ? AND conversation_id = ?",
                (sealed.nonce, sealed.ciphertext, namespace, str(conversation_id)),
            )
        self._secure_database_files()

    def conversation_context(self, *, namespace: str, conversation_id: UUID) -> dict[str, Any]:
        return self.get_conversation(namespace=namespace,
                                     conversation_id=conversation_id).context_checkpoints

    def mutate_conversation_context(
        self, *, namespace: str, conversation_id: UUID,
        transform: Callable[[dict[str, Any]], dict[str, Any]],
    ) -> None:
        """Atomically update encrypted LangGraph checkpoints with the conversation row.

        No second database, plaintext history or executable object serialization.
        Deleting/trashing a conversation also deletes/disables its checkpoints.
        """
        import json

        self._require_initialized()
        self._validate_namespace(namespace)
        with self._lock, self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            record = self._conversation_repository.get(
                connection, namespace=namespace, conversation_id=conversation_id,
            )
            context = transform(record.context_checkpoints)
            encoded = json.dumps(context, ensure_ascii=False, allow_nan=False)
            if len(encoded.encode("utf-8")) > 131_072:
                raise MemoryQueryError("conversation checkpoints exceed capacity")
            # JSON round-trip prevents storing classes, callbacks or executable serializers.
            context = json.loads(encoded)
            count = connection.execute(
                "SELECT turn_count FROM conversations WHERE conversation_id = ?",
                (str(conversation_id),),
            ).fetchone()[0]
            sealed = self._conversation_codec.seal_conversation(
                record.model_copy(update={"context_checkpoints": context}), turn_count=count,
            )
            connection.execute(
                "UPDATE conversations SET nonce = ?, ciphertext = ? "
                "WHERE namespace = ? AND conversation_id = ?",
                (sealed.nonce, sealed.ciphertext, namespace, str(conversation_id)),
            )
        self._secure_database_files()

    def conversation_history(
        self,
        *,
        namespace: str,
        conversation_id: UUID,
        limit: int = 12,
        before_sequence: int | None = None,
    ) -> tuple[ConversationTurn, ...]:
        self._require_initialized()
        self._validate_namespace(namespace)
        with self._lock, self._connect(read_only=True) as connection:
            return self._conversation_repository.history(
                connection,
                namespace=namespace,
                conversation_id=conversation_id,
                limit=limit,
                before_sequence=before_sequence,
            )

    def append_conversation_exchange(
        self,
        *,
        namespace: str,
        conversation_id: UUID,
        user_content: str,
        assistant_content: str,
        max_turns: int = 1_000,
        documents: tuple[DocumentInput, ...] = (),
        image_name: str | None = None,
        tool_results: tuple[ToolExecutionResult, ...] = (),
        source_checkpoint: TaskCheckpoint | None = None,
        request_id: UUID | None = None,
    ) -> tuple[ConversationTurn, ConversationTurn]:
        self._require_initialized()
        self._validate_namespace(namespace)
        self._reject_secret_material(user_content)
        self._reject_secret_material(assistant_content)
        for document in documents:
            self._reject_secret_material(document.name)
            self._reject_secret_material(document.content)
        if image_name:
            self._reject_secret_material(image_name)
        # Tool observations are sanitized before encryption; credentials are never retained.
        with self._lock, self._connect() as connection:
            turns = self._conversation_repository.append_exchange(
                connection,
                namespace=namespace,
                conversation_id=conversation_id,
                user_content=user_content,
                assistant_content=assistant_content,
                max_turns=max_turns,
                documents=documents,
                image_name=image_name,
                tool_results=tool_results,
                source_checkpoint=source_checkpoint,
                request_id=request_id,
            )
        self._secure_database_files()
        return turns

    def list_conversations(
        self, *, namespace: str, archived: bool = False, query: str = "",
        offset: int = 0, limit: int = 30, filed: bool | None = None,
    ) -> tuple[tuple[ConversationRecord, ...], bool]:
        self._require_initialized()
        self._validate_namespace(namespace)
        with self._lock, self._connect(read_only=True) as connection:
            return self._conversation_repository.list_records(
                connection, namespace=namespace, archived=archived,
                query=query, offset=offset, limit=limit, filed=filed,
            )

    def search_conversations(
        self, *, namespace: str, request: ConversationSearchPayload,
        position: SearchPosition | None = None,
    ) -> ConversationSearchPage:
        self._require_initialized()
        self._validate_namespace(namespace)
        if not self._lock.acquire(timeout=0.5):
            raise MemoryStoreError("conversation search storage is busy")
        try:
            with self._connect(read_only=True, busy_timeout=0.5) as connection:
                # One snapshot keeps authenticated headers and their turns consistent.
                connection.execute("BEGIN")
                return self._conversation_search.search(
                    connection, namespace=namespace, request=request, position=position,
                )
        except sqlite3.Error as error:
            raise MemoryStoreError("conversation search storage is unavailable") from error
        finally:
            self._lock.release()

    def list_project_conversations(
        self, *, namespace: str, project_id: UUID,
        before: tuple[datetime, UUID] | None = None, limit: int = 30,
    ) -> tuple[tuple[ConversationRecord, ...], bool]:
        self._require_initialized()
        self._validate_namespace(namespace)
        with self._lock, self._connect(read_only=True) as connection:
            return self._conversation_repository.list_project_records(
                connection, namespace=namespace, project_id=project_id, before=before, limit=limit,
            )

    def shared_conversation_context(
        self, *, namespace: str, conversation_id: UUID,
        query: str, alternate_queries: tuple[str, ...] = (),
        include_current: bool = False, only_current: bool = False,
        recent_start_sequence: int = 0,
        visible_sequences: tuple[int, ...] | None = None,
    ) -> dict[str, Any]:
        from aegis_core.memory.shared_conversation_context import search_shared_context

        self._require_initialized()
        self._validate_namespace(namespace)
        with self._lock, self._connect(read_only=True) as connection:
            connection.execute("BEGIN")
            self._conversation_repository.get(connection, namespace=namespace,
                                              conversation_id=conversation_id)
            return search_shared_context(connection, self._conversation_codec,
                                         namespace=namespace, current_id=conversation_id,
                                         query=query, alternate_queries=alternate_queries,
                                         include_current=include_current,
                                         only_current=only_current,
                                         recent_start_sequence=recent_start_sequence,
                                         visible_sequences=visible_sequences)

    def archive_conversation(
        self, *, namespace: str, conversation_id: UUID, archived: bool,
    ) -> None:
        self._require_initialized()
        self._validate_namespace(namespace)
        with self._lock, self._connect() as connection:
            self._conversation_repository.archive(
                connection, namespace=namespace, conversation_id=conversation_id, archived=archived,
            )
        self._secure_database_files()

    def conversation_documents(
        self, *, namespace: str, conversation_id: UUID,
    ) -> tuple[DocumentInput, ...]:
        self._require_initialized()
        self._validate_namespace(namespace)
        with self._lock, self._connect(read_only=True) as connection:
            return self._conversation_repository.documents(
                connection, namespace=namespace, conversation_id=conversation_id,
            )

    def rename_conversation(self, *, namespace: str, conversation_id: UUID,
                            title: str) -> ConversationRecord:
        self._require_initialized()
        self._validate_namespace(namespace)
        title = title.strip()
        if not 1 <= len(title) <= 128:
            raise MemoryQueryError("conversation title is out of range")
        self._reject_secret_material(title)
        with self._lock, self._connect() as connection:
            record = self._conversation_repository.rename(
                connection, namespace=namespace, conversation_id=conversation_id, title=title)
        self._secure_database_files()
        return record

    def fork_conversation(self, *, namespace: str, conversation_id: UUID,
                          max_conversations: int = 1_000,
                          before_turn_id: UUID | None = None) -> ConversationRecord:
        self._require_initialized()
        self._validate_namespace(namespace)
        with self._lock, self._connect() as connection:
            record = self._conversation_repository.fork(
                connection, namespace=namespace, conversation_id=conversation_id,
                max_conversations=max_conversations, before_turn_id=before_turn_id)
        self._secure_database_files()
        return record

    def conversation_turn(self, *, namespace: str, conversation_id: UUID,
                          turn_id: UUID) -> ConversationTurn:
        self._require_initialized()
        self._validate_namespace(namespace)
        with self._lock, self._connect(read_only=True) as connection:
            return self._conversation_repository.turn(
                connection, namespace=namespace, conversation_id=conversation_id, turn_id=turn_id)

    def set_conversation_feedback(
        self, *, namespace: str, conversation_id: UUID, turn_id: UUID,
        value: OwnerFeedback | None,
    ) -> ConversationTurn:
        self._require_initialized()
        self._validate_namespace(namespace)
        with self._lock, self._connect() as connection:
            turn = self._conversation_repository.feedback(
                connection, namespace=namespace, conversation_id=conversation_id,
                turn_id=turn_id, value=value)
        self._secure_database_files()
        return turn

    def empty_conversation_trash(self, *, namespace: str) -> int:
        self._require_initialized()
        self._validate_namespace(namespace)
        with self._lock, self._connect() as connection:
            count = self._conversation_repository.empty_trash(connection, namespace=namespace)
        self._secure_database_files()
        return count

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
        busy_timeout: float = 5.0,
    ) -> Iterator[sqlite3.Connection]:
        return self._storage.connect(
            read_only=read_only,
            load_vector_extension=load_vector_extension,
            decay_function=calculate_decayed_confidence,
            vector_extension_loader=self._load_vector_extension,
            busy_timeout=busy_timeout,
        )

    @staticmethod
    def _load_vector_extension(connection: sqlite3.Connection) -> None:
        if sqlite_vec is None or not hasattr(connection, "enable_load_extension"):
            raise _VectorAccelerationUnavailable("sqlite vector acceleration is unavailable")
        connection.enable_load_extension(True)
        try:
            sqlite_vec.load(connection)
        except (OSError, sqlite3.Error) as error:
            raise _VectorAccelerationUnavailable(
                "sqlite vector acceleration could not be loaded"
            ) from error
        finally:
            connection.enable_load_extension(False)
        version = connection.execute("SELECT vec_version()").fetchone()
        if version is None or not isinstance(version[0], str) or not version[0]:
            raise _VectorAccelerationUnavailable("sqlite vector acceleration is invalid")

    def _verify_encryption_key(self, connection: sqlite3.Connection) -> None:
        try:
            row = connection.execute(
                "SELECT key_identifier FROM memory_security WHERE singleton = 1"
            ).fetchone()
        except sqlite3.Error as error:
            self._mark_compromised("memory_encryption_metadata_invalid", cause=error)
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
        except Exception as error:
            # A secondary native index must not invalidate a committed encrypted record.
            record_boundary_error(error)
            return

    @staticmethod
    def _reject_secret_material(content: str) -> None:
        if contains_likely_secret_material(content):
            raise SecretMaterialError("credential-like material belongs in Keychain")

    @staticmethod
    def _validate_namespace(namespace: str) -> None:
        if not re.fullmatch(NAMESPACE_PATTERN, namespace):
            raise MemoryQueryError("invalid memory namespace")

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
            except Exception as error:
                record_boundary_error(error)
                pass
        error = DecryptionAuthError("memory authentication failed; subsystem locked")
        if cause is not None:
            raise error from cause
        raise error
