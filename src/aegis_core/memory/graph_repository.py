from __future__ import annotations

import hashlib
import json
import math
import sqlite3
import struct
from collections.abc import Callable
from datetime import datetime
from typing import NoReturn
from uuid import UUID, uuid4

from aegis_core.memory.contracts import MemoryRecord
from aegis_core.memory.crypto import MemoryRowCipher, RowAuthenticationError
from aegis_core.memory.errors import (
    DatabaseCapacityError,
    DecryptionAuthError,
    MemoryQueryError,
    MemoryStoreError,
)
from aegis_core.memory.graph_extractor import DeterministicGraphExtractor, ExtractedEntity
from aegis_core.memory.records import (
    GraphEdgeRecord,
    GraphEmbeddingCandidate,
    GraphNodeRecord,
)

GRAPH_EMBEDDING_DIMENSIONS = 384
_ENCRYPTED_PROPERTIES_MARKER = '{"encrypted":true,"schema":1}'
_SENSITIVE_PROPERTIES = ("ip_address", "mac_address", "api_token")
CompromiseHandler = Callable[[str, Exception | None], NoReturn]


class GraphRepository:
    """Persist the encrypted graph while callers retain transaction ownership."""

    def __init__(
        self,
        cipher: MemoryRowCipher,
        *,
        max_embeddings: int,
        on_compromised: CompromiseHandler,
        extractor: DeterministicGraphExtractor | None = None,
    ) -> None:
        self._cipher = cipher
        self._max_embeddings = max_embeddings
        self._on_compromised = on_compromised
        self._extractor = extractor or DeterministicGraphExtractor()

    def upsert_record(
        self,
        connection: sqlite3.Connection,
        record: MemoryRecord,
    ) -> None:
        extraction = self._extractor.extract(record.content)
        document_name = record.source or self._document_name(record.content)
        document_id = uuid4()
        self._insert_node(
            connection,
            node_id=document_id,
            namespace=record.namespace,
            name=document_name,
            node_type="document",
            properties={
                "evidence": record.evidence.value,
                "kind": record.kind.value,
            },
            memory_id=record.memory_id,
            created_at=record.created_at,
            updated_at=record.updated_at,
        )

        entity_ids: dict[tuple[str, str], UUID] = {}
        for entity in extraction.entities:
            node_id = self._upsert_entity(
                connection,
                namespace=record.namespace,
                entity=entity,
                timestamp=record.updated_at,
            )
            entity_ids[(entity.type, entity.name.casefold())] = node_id
            self._insert_edge(
                connection,
                namespace=record.namespace,
                source_id=document_id,
                target_id=node_id,
                edge_type="MENTIONS",
                weight=1.0,
                memory_id=record.memory_id,
                created_at=record.updated_at,
            )
        for relationship in extraction.relationships:
            source_id = entity_ids.get(
                (relationship.subject.type, relationship.subject.name.casefold())
            )
            target_id = entity_ids.get(
                (relationship.object.type, relationship.object.name.casefold())
            )
            if source_id is None or target_id is None or source_id == target_id:
                continue
            self._insert_edge(
                connection,
                namespace=record.namespace,
                source_id=source_id,
                target_id=target_id,
                edge_type=relationship.type,
                weight=relationship.weight,
                memory_id=record.memory_id,
                created_at=record.updated_at,
            )

    def delete_memory(self, connection: sqlite3.Connection, memory_id: str) -> None:
        shared_rows = connection.execute(
            """
            SELECT DISTINCT n.node_id
            FROM edges AS e
            JOIN nodes AS n
              ON (n.node_id = e.source_id OR n.node_id = e.target_id)
            WHERE e.memory_id = ? AND n.memory_id IS NULL
            ORDER BY n.node_id ASC
            """,
            (memory_id,),
        ).fetchall()
        document_rows = connection.execute(
            "SELECT node_id FROM nodes WHERE memory_id = ?",
            (memory_id,),
        ).fetchall()
        connection.execute("DELETE FROM edges WHERE memory_id = ?", (memory_id,))
        for row in document_rows:
            self.delete_node_embedding(connection, str(row["node_id"]))
        connection.execute("DELETE FROM nodes WHERE memory_id = ?", (memory_id,))
        for row in shared_rows:
            node_id = str(row["node_id"])
            remains_connected = connection.execute(
                """
                SELECT 1 FROM edges
                WHERE source_id = ? OR target_id = ?
                LIMIT 1
                """,
                (node_id, node_id),
            ).fetchone()
            if remains_connected is not None:
                continue
            self.delete_node_embedding(connection, node_id)
            connection.execute(
                "DELETE FROM nodes WHERE node_id = ? AND memory_id IS NULL",
                (node_id,),
            )

    def replace_property_indices(
        self,
        connection: sqlite3.Connection,
        *,
        node_id: UUID,
        namespace: str,
        properties: dict[str, object],
    ) -> None:
        exists = connection.execute(
            """
            SELECT 1 FROM sqlite_master
            WHERE type = 'table' AND name = 'node_property_index'
            """
        ).fetchone()
        if exists is None:
            return
        connection.execute(
            "DELETE FROM node_property_index WHERE node_id = ?",
            (str(node_id),),
        )
        for property_name in _SENSITIVE_PROPERTIES:
            value = properties.get(property_name)
            if value is None:
                continue
            if not isinstance(value, str) or not value or len(value) > 512:
                raise MemoryStoreError("sensitive graph property is invalid")
            connection.execute(
                """
                INSERT INTO node_property_index(
                    node_id, namespace, property_name, value_digest
                ) VALUES (?, ?, ?, ?)
                """,
                (
                    str(node_id),
                    namespace,
                    property_name,
                    self.property_digest(property_name, value),
                ),
            )

    def property_digest(self, property_name: str, value: str) -> str:
        return self._cipher.blind_exact(f"{property_name}\0{value}")

    def embedding_candidate(
        self,
        row: sqlite3.Row | dict[str, object],
    ) -> GraphEmbeddingCandidate:
        node = self.node_from_row(row)
        return GraphEmbeddingCandidate(
            node_id=node.node_id,
            namespace=node.namespace,
            name=node.name,
            type=node.type,
            properties=node.properties,
            content_sha256=node.content_sha256,
        )

    def node_from_row(
        self,
        row: sqlite3.Row | dict[str, object],
    ) -> GraphNodeRecord:
        node_id = str(row["node_id"])
        namespace = str(row["namespace"])
        try:
            document = self._cipher.open(
                namespace=namespace,
                memory_id=node_id,
                nonce=row["nonce"],
                ciphertext=row["ciphertext"],
            )
        except RowAuthenticationError:
            self._fail("graph_node_aead_authentication_failed")
        if set(document) != {"content_sha256", "memory_id", "name", "properties", "type"}:
            self._fail("graph_node_document_shape_invalid")
        try:
            memory_id = UUID(document["memory_id"]) if document["memory_id"] else None
            expected_digest = self.node_digest(
                name=document["name"],
                node_type=document["type"],
                properties=document["properties"],
                memory_id=memory_id,
            )
            if (
                expected_digest != document["content_sha256"]
                or expected_digest != row["content_sha256"]
                or document["type"] != row["type"]
                or (str(memory_id) if memory_id else None) != row["memory_id"]
                or self._cipher.blind_exact(document["name"]) != row["name"]
                or row["properties_json"] != _ENCRYPTED_PROPERTIES_MARKER
            ):
                self._fail("graph_node_metadata_mismatch")
            return GraphNodeRecord(
                node_id=UUID(node_id),
                namespace=namespace,
                name=document["name"],
                type=document["type"],
                properties=document["properties"],
                memory_id=memory_id,
                content_sha256=expected_digest,
                created_at=datetime.fromisoformat(str(row["created_at"])),
                updated_at=datetime.fromisoformat(str(row["updated_at"])),
            )
        except DecryptionAuthError:
            raise
        except (KeyError, TypeError, ValueError) as error:
            self._fail("graph_node_decrypted_payload_invalid", cause=error)

    @staticmethod
    def edge_from_row(row: sqlite3.Row) -> GraphEdgeRecord:
        try:
            weight = float(row["weight"])
            if not math.isfinite(weight) or not 0.0 < weight <= 10.0:
                raise ValueError("invalid graph edge weight")
            return GraphEdgeRecord(
                edge_id=UUID(str(row["edge_id"])),
                namespace=str(row["namespace"]),
                source_id=UUID(str(row["source_id"])),
                target_id=UUID(str(row["target_id"])),
                type=str(row["type"]),
                weight=weight,
                memory_id=UUID(str(row["memory_id"])) if row["memory_id"] else None,
            )
        except (TypeError, ValueError) as error:
            raise MemoryStoreError("stored graph edge is invalid") from error

    @staticmethod
    def node_digest(
        *,
        name: str,
        node_type: str,
        properties: dict[str, object],
        memory_id: UUID | None,
    ) -> str:
        payload = json.dumps(
            {
                "memory_id": str(memory_id) if memory_id else None,
                "name": name,
                "properties": properties,
                "type": node_type,
            },
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
        return hashlib.sha256(payload).hexdigest()

    def namespace_partition_key(self, namespace: str) -> int:
        return int(self._cipher.blind_exact(namespace)[:15], 16)

    @classmethod
    def encode_vector(cls, vector: tuple[float, ...]) -> bytes:
        normalized = cls._normalize_vector(vector)
        projected = [0.0] * GRAPH_EMBEDDING_DIMENSIONS
        for index, value in enumerate(normalized):
            bucket = index % GRAPH_EMBEDDING_DIMENSIONS
            sign = 1.0 if (index // GRAPH_EMBEDDING_DIMENSIONS) % 2 == 0 else -1.0
            projected[bucket] += sign * value
        norm = math.sqrt(math.fsum(value * value for value in projected))
        if norm <= 0.0:
            raise MemoryQueryError("projected graph vector norm must be positive")
        projected = [value / norm for value in projected]
        try:
            return struct.pack(f"<{GRAPH_EMBEDDING_DIMENSIONS}f", *projected)
        except (OverflowError, struct.error) as error:
            raise MemoryQueryError("graph embedding vector cannot be encoded") from error

    def prune_embeddings(self, connection: sqlite3.Connection) -> None:
        namespaces = connection.execute(
            """
            SELECT namespace
            FROM node_embedding_metadata
            GROUP BY namespace
            HAVING COUNT(*) > ?
            ORDER BY namespace ASC
            """,
            (self._max_embeddings,),
        ).fetchall()
        for row in namespaces:
            self.evict_embeddings_fifo(
                connection,
                namespace=str(row["namespace"]),
                reserve_slot=False,
            )

    def evict_embeddings_fifo(
        self,
        connection: sqlite3.Connection,
        *,
        namespace: str,
        reserve_slot: bool,
    ) -> tuple[str, ...]:
        count = int(
            connection.execute(
                "SELECT COUNT(*) FROM node_embedding_metadata WHERE namespace = ?",
                (namespace,),
            ).fetchone()[0]
        )
        retained = self._max_embeddings - (1 if reserve_slot else 0)
        eviction_count = max(0, count - retained)
        if eviction_count == 0:
            return ()
        rows = connection.execute(
            """
            SELECT node_id FROM node_embedding_metadata
            WHERE namespace = ?
            ORDER BY created_at ASC, node_id ASC
            LIMIT ?
            """,
            (namespace, eviction_count),
        ).fetchall()
        if len(rows) != eviction_count:
            raise DatabaseCapacityError("graph embedding FIFO selection was incomplete")
        for row in rows:
            vector_cursor = connection.execute(
                "DELETE FROM node_embeddings WHERE node_id = ?",
                (row["node_id"],),
            )
            metadata_cursor = connection.execute(
                "DELETE FROM node_embedding_metadata WHERE node_id = ? AND namespace = ?",
                (row["node_id"], namespace),
            )
            if vector_cursor.rowcount != 1 or metadata_cursor.rowcount != 1:
                raise DatabaseCapacityError("graph embedding FIFO lost transactional ownership")
        return tuple(str(row["node_id"]) for row in rows)

    @staticmethod
    def delete_node_embedding(connection: sqlite3.Connection, node_id: str) -> None:
        connection.execute("DELETE FROM node_embeddings WHERE node_id = ?", (node_id,))
        connection.execute("DELETE FROM node_embedding_metadata WHERE node_id = ?", (node_id,))

    def _upsert_entity(
        self,
        connection: sqlite3.Connection,
        *,
        namespace: str,
        entity: ExtractedEntity,
        timestamp: datetime,
    ) -> UUID:
        row = connection.execute(
            """
            SELECT node_id, namespace, name, type, properties_json, memory_id,
                   content_sha256, nonce, ciphertext, created_at, updated_at
            FROM nodes
            WHERE namespace = ? AND type = ? AND name = ? AND memory_id IS NULL
            """,
            (namespace, entity.type, self._cipher.blind_exact(entity.name)),
        ).fetchone()
        if row is not None:
            existing = self.node_from_row(row)
            merged_properties = dict(existing.properties)
            merged_properties.update(entity.properties)
            merged_properties["extractor"] = "deterministic-v2"
            if merged_properties != existing.properties:
                self._update_node_properties(
                    connection,
                    existing,
                    properties=merged_properties,
                    updated_at=timestamp,
                )
            return existing.node_id
        node_id = uuid4()
        self._insert_node(
            connection,
            node_id=node_id,
            namespace=namespace,
            name=entity.name,
            node_type=entity.type,
            properties={"extractor": "deterministic-v2", **dict(entity.properties)},
            memory_id=None,
            created_at=timestamp,
            updated_at=timestamp,
        )
        return node_id

    def _insert_node(
        self,
        connection: sqlite3.Connection,
        *,
        node_id: UUID,
        namespace: str,
        name: str,
        node_type: str,
        properties: dict[str, object],
        memory_id: UUID | None,
        created_at: datetime,
        updated_at: datetime,
    ) -> None:
        content_sha256 = self.node_digest(
            name=name,
            node_type=node_type,
            properties=properties,
            memory_id=memory_id,
        )
        sealed = self._cipher.seal(
            namespace=namespace,
            memory_id=str(node_id),
            document={
                "content_sha256": content_sha256,
                "memory_id": str(memory_id) if memory_id is not None else None,
                "name": name,
                "properties": properties,
                "type": node_type,
            },
        )
        connection.execute(
            """
            INSERT INTO nodes(
                node_id, namespace, name, type, properties_json, memory_id,
                content_sha256, nonce, ciphertext, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                str(node_id),
                namespace,
                self._cipher.blind_exact(name),
                node_type,
                _ENCRYPTED_PROPERTIES_MARKER,
                str(memory_id) if memory_id is not None else None,
                content_sha256,
                sealed.nonce,
                sealed.ciphertext,
                created_at.isoformat(),
                updated_at.isoformat(),
            ),
        )
        self.replace_property_indices(
            connection,
            node_id=node_id,
            namespace=namespace,
            properties=properties,
        )

    def _update_node_properties(
        self,
        connection: sqlite3.Connection,
        node: GraphNodeRecord,
        *,
        properties: dict[str, object],
        updated_at: datetime,
    ) -> None:
        content_sha256 = self.node_digest(
            name=node.name,
            node_type=node.type,
            properties=properties,
            memory_id=node.memory_id,
        )
        sealed = self._cipher.seal(
            namespace=node.namespace,
            memory_id=str(node.node_id),
            document={
                "content_sha256": content_sha256,
                "memory_id": str(node.memory_id) if node.memory_id is not None else None,
                "name": node.name,
                "properties": properties,
                "type": node.type,
            },
        )
        cursor = connection.execute(
            """
            UPDATE nodes SET content_sha256 = ?, nonce = ?, ciphertext = ?, updated_at = ?
            WHERE node_id = ? AND namespace = ?
            """,
            (
                content_sha256,
                sealed.nonce,
                sealed.ciphertext,
                updated_at.isoformat(),
                str(node.node_id),
                node.namespace,
            ),
        )
        if cursor.rowcount != 1:
            raise MemoryStoreError("graph node property update lost transactional ownership")
        self.delete_node_embedding(connection, str(node.node_id))
        self.replace_property_indices(
            connection,
            node_id=node.node_id,
            namespace=node.namespace,
            properties=properties,
        )

    @staticmethod
    def _insert_edge(
        connection: sqlite3.Connection,
        *,
        namespace: str,
        source_id: UUID,
        target_id: UUID,
        edge_type: str,
        weight: float,
        memory_id: UUID,
        created_at: datetime,
    ) -> None:
        connection.execute(
            """
            INSERT OR IGNORE INTO edges(
                edge_id, namespace, source_id, target_id, type, weight,
                memory_id, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                str(uuid4()),
                namespace,
                str(source_id),
                str(target_id),
                edge_type,
                weight,
                str(memory_id),
                created_at.isoformat(),
            ),
        )

    @staticmethod
    def _document_name(content: str) -> str:
        bounded = " ".join(content.split())[:128].strip()
        return bounded or "Memory document"

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

    def _fail(self, reason: str, *, cause: Exception | None = None) -> NoReturn:
        self._on_compromised(reason, cause)
