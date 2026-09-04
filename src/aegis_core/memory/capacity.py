from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from typing import NoReturn

from aegis_core.memory.errors import DatabaseCapacityError
from aegis_core.memory.graph_repository import GraphRepository


class MemoryCapacityManager:
    """Enforce bounded storage inside caller-owned SQLite connections."""

    def __init__(
        self,
        *,
        max_namespace_entries: int,
        graph_repository: GraphRepository,
    ) -> None:
        self._max_namespace_entries = max_namespace_entries
        self._graph_repository = graph_repository

    @staticmethod
    def begin(connection: sqlite3.Connection) -> None:
        if connection.in_transaction:
            raise DatabaseCapacityError("memory capacity transaction is already active")
        connection.execute("PRAGMA secure_delete = ON")
        secure_delete = connection.execute("PRAGMA secure_delete").fetchone()
        if secure_delete is None or int(secure_delete[0]) != 1:
            raise DatabaseCapacityError("SQLite secure deletion is unavailable")
        connection.execute("BEGIN IMMEDIATE")

    @contextmanager
    def transaction(self, connection: sqlite3.Connection) -> Iterator[None]:
        try:
            self.begin(connection)
            yield
            connection.commit()
        except (sqlite3.Error, DatabaseCapacityError) as error:
            self.rollback(connection, error)
        except BaseException:
            if connection.in_transaction:
                connection.rollback()
            raise

    @staticmethod
    def rollback(
        connection: sqlite3.Connection,
        error: sqlite3.Error | DatabaseCapacityError,
    ) -> NoReturn:
        if connection.in_transaction:
            connection.rollback()
        if isinstance(error, DatabaseCapacityError):
            raise error
        raise DatabaseCapacityError("atomic memory capacity write failed") from error

    def evict_namespace_fifo(
        self,
        connection: sqlite3.Connection,
        *,
        namespace: str,
        reserve_slot: bool,
    ) -> tuple[str, ...]:
        """Evict complete oldest rows, including graph and lexical indexes."""
        count = int(
            connection.execute(
                "SELECT COUNT(*) FROM memory_items WHERE namespace = ?",
                (namespace,),
            ).fetchone()[0]
        )
        retained = self._max_namespace_entries - (1 if reserve_slot else 0)
        eviction_count = max(0, count - retained)
        if eviction_count == 0:
            return ()
        rows = connection.execute(
            """
            SELECT row_id, memory_id
            FROM memory_items
            WHERE namespace = ?
            ORDER BY row_id ASC, created_at ASC, memory_id ASC
            LIMIT ?
            """,
            (namespace, eviction_count),
        ).fetchall()
        if len(rows) != eviction_count:
            raise DatabaseCapacityError("namespace FIFO selection was incomplete")
        for row in rows:
            self._graph_repository.delete_memory(connection, str(row["memory_id"]))
            lexical_cursor = connection.execute(
                "DELETE FROM memory_fts WHERE rowid = ?",
                (row["row_id"],),
            )
            memory_cursor = connection.execute(
                "DELETE FROM memory_items WHERE row_id = ? AND namespace = ?",
                (row["row_id"], namespace),
            )
            if lexical_cursor.rowcount != 1 or memory_cursor.rowcount != 1:
                raise DatabaseCapacityError("namespace FIFO eviction lost transactional ownership")
        return tuple(str(row["memory_id"]) for row in rows)
