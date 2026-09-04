from __future__ import annotations

import json
import re
import sqlite3
from datetime import UTC, datetime

from aegis_core.memory.codec import MemoryRowCodec
from aegis_core.memory.contracts import TAG_PATTERN, MemorySearchHit
from aegis_core.memory.crypto import MemoryRowCipher
from aegis_core.memory.errors import MemoryQueryError
from aegis_core.memory.graph_repository import GraphRepository

RRF_RANK_CONSTANT = 60.0
MAX_HYBRID_MEMORY_PAYLOAD_BYTES = 4_096


class MemorySearchRepository:
    """Run bounded lexical and vector retrieval on caller-owned snapshots."""

    def __init__(
        self,
        *,
        cipher: MemoryRowCipher,
        row_codec: MemoryRowCodec,
        graph_repository: GraphRepository,
        max_node_embeddings: int,
    ) -> None:
        self._cipher = cipher
        self._row_codec = row_codec
        self._graph_repository = graph_repository
        self._max_node_embeddings = max_node_embeddings

    def list_by_tag(
        self,
        connection: sqlite3.Connection,
        *,
        namespace: str,
        tag: str,
        limit: int,
    ) -> tuple[MemorySearchHit, ...]:
        if not re.fullmatch(TAG_PATTERN, tag) or not 1 <= limit <= 100:
            raise MemoryQueryError("invalid tagged memory query")
        tag_digest = self._cipher.blind_exact(tag)
        as_of = datetime.now(UTC).isoformat()
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

    def lexical_search(
        self,
        connection: sqlite3.Connection,
        *,
        namespace: str,
        query: str,
        limit: int,
    ) -> tuple[MemorySearchHit, ...]:
        if not 1 <= limit <= 10:
            raise MemoryQueryError("memory search limit is out of range")
        fts_query = self._fts_query(query)
        as_of = datetime.now(UTC).isoformat()
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
        connection: sqlite3.Connection,
        *,
        namespace: str,
        query: str,
        model_id: str,
        query_vector: tuple[float, ...],
        limit: int,
    ) -> tuple[MemorySearchHit, ...]:
        """Fuse FTS5 and cosine ranks in one SQLite snapshot using formal RRF."""
        if not model_id or len(model_id) > 256 or not 1 <= limit <= 10:
            raise MemoryQueryError("hybrid memory search arguments are invalid")
        fts_query = self._fts_query(query)
        encoded_vector = self._graph_repository.encode_vector(query_vector)
        as_of = datetime.now(UTC).isoformat()
        candidate_limit = min(self._max_node_embeddings, max(32, limit * 8))
        # ES: RRF mezcla posiciones, no escalas incompatibles. Cada lista aporta
        # 1/(k+rango); k=60 suaviza el primer puesto para equilibrar coincidencia
        # literal (FTS5) y significado (sqlite-vec) dentro de una sola instantánea.
        # EN: RRF combines ranks instead of incomparable raw scores. The k=60
        # smoothing constant balances lexical and semantic retrieval in SQLite.
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

    def _fts_query(self, query: str) -> str:
        try:
            return self._cipher.blind_query(query)
        except ValueError as error:
            raise MemoryQueryError("memory query has no searchable terms") from error
