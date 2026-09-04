from __future__ import annotations

import math
import sqlite3
from datetime import UTC, datetime
from uuid import UUID

from aegis_core.memory.capacity import MemoryCapacityManager
from aegis_core.memory.errors import (
    DatabaseCapacityError,
    MemoryNotFoundError,
    MemoryStoreError,
)
from aegis_core.memory.graph_repository import GraphRepository
from aegis_core.memory.records import GraphEmbeddingCandidate, GraphSeedRecord


class GraphEmbeddingRepository:
    """Maintain and query the bounded sqlite-vec projection of graph nodes."""

    def __init__(
        self,
        *,
        graph_repository: GraphRepository,
        capacity: MemoryCapacityManager,
        max_embeddings: int,
    ) -> None:
        self._graph_repository = graph_repository
        self._capacity = capacity
        self._max_embeddings = max_embeddings

    def list_missing(
        self,
        connection: sqlite3.Connection,
        *,
        namespace: str,
        model_id: str,
        limit: int,
    ) -> tuple[GraphEmbeddingCandidate, ...]:
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

    def put(
        self,
        connection: sqlite3.Connection,
        *,
        namespace: str,
        node_id: UUID,
        model_id: str,
        vector: tuple[float, ...],
        content_sha256: str,
        created_at: datetime | None = None,
    ) -> None:
        encoded_vector = self._graph_repository.encode_vector(vector)
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
                    (created_at or datetime.now(UTC)).isoformat(),
                ),
            )
            self._graph_repository.prune_embeddings(connection)
            connection.commit()
        except (sqlite3.Error, DatabaseCapacityError) as error:
            self._capacity.rollback(connection, error)

    def seed_search(
        self,
        connection: sqlite3.Connection,
        *,
        namespace: str,
        model_id: str,
        query_vector: tuple[float, ...],
        limit: int,
    ) -> tuple[GraphSeedRecord, ...]:
        encoded = self._graph_repository.encode_vector(query_vector)
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
                min(self._max_embeddings, max(32, limit * 8)),
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
