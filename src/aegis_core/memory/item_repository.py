from __future__ import annotations

import math
import sqlite3
from datetime import datetime
from uuid import UUID

from aegis_core.memory.capacity import MemoryCapacityManager
from aegis_core.memory.codec import MemoryRowCodec
from aegis_core.memory.contracts import (
    MemoryEvidence,
    MemoryKind,
    MemoryRecord,
)
from aegis_core.memory.crypto import MemoryRowCipher
from aegis_core.memory.errors import (
    DatabaseCapacityError,
    MemoryNotFoundError,
    MemoryStoreError,
)
from aegis_core.memory.graph_repository import GraphRepository


class MemoryItemRepository:
    """Persist authenticated memory items inside caller-owned connections.

    The store facade owns filesystem identity, locking, and connection lifetime.
    This repository owns only the atomic lifecycle of a memory row and its lexical
    and graph projections, so no committed item can outlive one of its indexes.
    """

    def __init__(
        self,
        *,
        cipher: MemoryRowCipher,
        row_codec: MemoryRowCodec,
        graph_repository: GraphRepository,
        capacity: MemoryCapacityManager,
        max_entries: int,
    ) -> None:
        self._cipher = cipher
        self._row_codec = row_codec
        self._graph_repository = graph_repository
        self._capacity = capacity
        self._max_entries = max_entries

    def insert(
        self,
        connection: sqlite3.Connection,
        *,
        record: MemoryRecord,
    ) -> None:
        nonce, ciphertext, source_digest, tag_digests, blind_content = self._row_codec.seal_record(
            record
        )
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
                    created_at, updated_at, content_sha256, confidence, evidence,
                    expires_at, last_confirmed_at, nonce, ciphertext,
                    source_digest, tags_digest_json
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

    def upsert_by_source(
        self,
        connection: sqlite3.Connection,
        *,
        namespace: str,
        kind: MemoryKind,
        content: str,
        source: str,
        tags: tuple[str, ...],
        confidence: float,
        evidence: MemoryEvidence,
        expires_at: datetime | None,
        last_confirmed_at: datetime | None,
        now: datetime,
    ) -> MemoryRecord:
        source_digest = self._cipher.blind_exact(source)
        with self._capacity.transaction(connection):
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
                        created_at, updated_at, content_sha256, confidence, evidence,
                        expires_at, last_confirmed_at, nonce, ciphertext,
                        source_digest, tags_digest_json
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
                row_id = int(cursor.lastrowid)
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
                    confidence=(
                        max(previous.confidence, confidence)
                        if previous.content == content
                        else confidence
                    ),
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
        return record

    def get(
        self,
        connection: sqlite3.Connection,
        *,
        namespace: str,
        memory_id: UUID,
    ) -> MemoryRecord:
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

    def delete_by_source(
        self,
        connection: sqlite3.Connection,
        *,
        namespace: str,
        source: str,
    ) -> bool:
        source_digest = self._cipher.blind_exact(source)
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
        self._delete_row(connection, namespace=namespace, row=row)
        return True

    def delete_by_tag(
        self,
        connection: sqlite3.Connection,
        *,
        namespace: str,
        tag: str,
    ) -> int:
        tag_digest = self._cipher.blind_exact(tag)
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
            self._delete_row(connection, namespace=namespace, row=row)
        return len(rows)

    def reinforce(
        self,
        connection: sqlite3.Connection,
        *,
        namespace: str,
        memory_id: UUID,
        delta_boost: float,
        timestamp: datetime,
    ) -> MemoryRecord:
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
                    "confidence": math.tanh(previous.confidence + delta_boost),
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
        return reinforced

    def evict_decayed(
        self,
        connection: sqlite3.Connection,
        *,
        namespace: str,
        threshold: float,
        timestamp: datetime,
        limit: int,
    ) -> int:
        try:
            # SQLite erases freed logical page content. AES-GCM remains the primary
            # confidentiality control because SSD wear levelling is below SQLite.
            connection.execute("PRAGMA secure_delete = ON")
            connection.execute("BEGIN IMMEDIATE")
            encoded_timestamp = timestamp.isoformat()
            rows = connection.execute(
                """
                SELECT row_id, memory_id
                FROM memory_items
                WHERE namespace = ?
                  AND (
                      NOT aegis_memory_live(expires_at, ?)
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
                    threshold,
                    limit,
                ),
            ).fetchall()
            for row in rows:
                self._delete_row(connection, namespace=namespace, row=row)
            connection.commit()
        except sqlite3.Error as error:
            if connection.in_transaction:
                connection.rollback()
            raise MemoryStoreError("memory decay eviction failed closed") from error
        return len(rows)

    def delete(
        self,
        connection: sqlite3.Connection,
        *,
        namespace: str,
        memory_id: UUID,
    ) -> None:
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
        self._delete_row(connection, namespace=namespace, row=row)

    def _delete_row(
        self,
        connection: sqlite3.Connection,
        *,
        namespace: str,
        row: sqlite3.Row,
    ) -> None:
        self._graph_repository.delete_memory(connection, str(row["memory_id"]))
        connection.execute("DELETE FROM memory_fts WHERE rowid = ?", (row["row_id"],))
        connection.execute(
            "DELETE FROM memory_items WHERE row_id = ? AND namespace = ?",
            (row["row_id"], namespace),
        )
