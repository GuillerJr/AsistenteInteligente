from __future__ import annotations

import sqlite3
from datetime import UTC, datetime
from uuid import UUID

from aegis_core.memory.graph_repository import GraphRepository
from aegis_core.memory.records import (
    GraphEdgeRecord,
    GraphEmbeddingCandidate,
    GraphNodeRecord,
    SpotlightGraphRecord,
)
from aegis_core.memory.visibility import GRAPH_VISIBILITY_CTE


class GraphReadRepository:
    """Read authenticated GraphRAG projections from caller-owned snapshots."""

    def __init__(self, graph_repository: GraphRepository) -> None:
        self._graph_repository = graph_repository

    def nodes_for_memory(
        self,
        connection: sqlite3.Connection,
        *,
        namespace: str,
        memory_id: UUID,
    ) -> tuple[GraphEmbeddingCandidate, ...]:
        connection.execute("BEGIN")
        as_of = datetime.now(UTC).isoformat()
        rows = connection.execute(
            GRAPH_VISIBILITY_CTE
            + """
            SELECT DISTINCT n.node_id, n.namespace, n.name, n.type,
                   n.properties_json, n.memory_id, n.content_sha256,
                   n.nonce, n.ciphertext, n.created_at, n.updated_at
            FROM live_nodes AS n
            LEFT JOIN live_edges AS e
              ON e.memory_id = ?
             AND (e.source_id = n.node_id OR e.target_id = n.node_id)
            WHERE n.namespace = ? AND (n.memory_id = ? OR e.edge_id IS NOT NULL)
            ORDER BY n.updated_at DESC, n.node_id ASC
            """,
            (namespace, as_of, str(memory_id), namespace, str(memory_id)),
        ).fetchall()
        return tuple(
            self._graph_repository.embedding_candidate(row, connection=connection, as_of=as_of)
            for row in rows
        )

    def spotlight_records(
        self,
        connection: sqlite3.Connection,
        *,
        namespace: str,
        after_node_id: str | None,
        limit: int,
    ) -> tuple[SpotlightGraphRecord, ...]:
        connection.execute("BEGIN")
        as_of = datetime.now(UTC).isoformat()
        rows = connection.execute(
            GRAPH_VISIBILITY_CTE
            + """
            SELECT node_id, namespace, name, type, properties_json, memory_id,
                   content_sha256, nonce, ciphertext, created_at, updated_at
            FROM live_nodes
            WHERE namespace = ? AND (? IS NULL OR node_id > ?)
            ORDER BY node_id ASC
            LIMIT ?
            """,
            (namespace, as_of, namespace, after_node_id, after_node_id, limit),
        ).fetchall()
        records: list[SpotlightGraphRecord] = []
        for row in rows:
            node = self._graph_repository.node_from_row(row)
            edge_rows = connection.execute(
                GRAPH_VISIBILITY_CTE
                + """
                SELECT e.type, e.source_id, e.target_id,
                       n.node_id, n.namespace, n.name, n.type AS node_type,
                       n.properties_json, n.memory_id, n.content_sha256,
                       n.nonce, n.ciphertext, n.created_at, n.updated_at
                FROM live_edges AS e
                JOIN live_nodes AS n
                  ON n.node_id = CASE
                      WHEN e.source_id = ? THEN e.target_id
                      ELSE e.source_id
                  END
                WHERE e.namespace = ? AND (e.source_id = ? OR e.target_id = ?)
                ORDER BY e.type ASC, n.node_id ASC
                LIMIT 32
                """,
                (
                    namespace,
                    as_of,
                    str(node.node_id),
                    namespace,
                    str(node.node_id),
                    str(node.node_id),
                ),
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

    def find_nodes_by_property(
        self,
        connection: sqlite3.Connection,
        *,
        namespace: str,
        property_name: str,
        value: str,
        limit: int,
    ) -> tuple[GraphNodeRecord, ...]:
        connection.execute("BEGIN")
        as_of = datetime.now(UTC).isoformat()
        rows = connection.execute(
            GRAPH_VISIBILITY_CTE
            + """
            SELECT n.node_id, n.namespace, n.name, n.type, n.properties_json,
                   n.memory_id, n.content_sha256, n.nonce, n.ciphertext,
                   n.created_at, n.updated_at, property.value_digest AS lookup_digest
            FROM node_property_index AS property
            JOIN live_nodes AS n ON n.node_id = property.node_id
            WHERE property.namespace = ? AND property.property_name = ?
            ORDER BY n.updated_at DESC, n.node_id ASC
            """,
            (namespace, as_of, namespace, property_name),
        )
        matches = []
        for row in rows:
            node = self._graph_repository.node_from_row(row)
            stored = node.properties.get(property_name)
            if not isinstance(stored, str) or row[
                "lookup_digest"
            ] != self._graph_repository.property_digest(property_name, stored):
                self._graph_repository._fail("graph_property_blind_index_mismatch")
            node = self._graph_repository.project_live_properties(connection, node, as_of=as_of)
            if node.properties.get(property_name) == value:
                matches.append(node)
                if len(matches) == limit:
                    break
        return tuple(matches)

    def load_neighborhood(
        self,
        connection: sqlite3.Connection,
        *,
        namespace: str,
        seed_ids: tuple[UUID, ...],
        max_nodes: int,
        max_edges: int,
        depth: int,
    ) -> tuple[tuple[GraphNodeRecord, ...], tuple[GraphEdgeRecord, ...]]:
        connection.execute("BEGIN")
        as_of = datetime.now(UTC).isoformat()
        placeholders = ",".join("?" for _ in seed_ids)
        frontier = {
            str(row[0])
            for row in connection.execute(
                GRAPH_VISIBILITY_CTE
                + f"SELECT node_id FROM live_nodes WHERE node_id IN ({placeholders}) "
                "ORDER BY node_id LIMIT ?",
                (namespace, as_of, *(str(item) for item in seed_ids), max_nodes),
            )
        }
        visited = set(frontier)
        edge_rows: dict[str, sqlite3.Row] = {}
        for _ in range(depth):
            if not frontier or len(visited) >= max_nodes or len(edge_rows) >= max_edges:
                break
            placeholders = ",".join("?" for _ in frontier)
            rows = connection.execute(
                GRAPH_VISIBILITY_CTE
                + f"""
                SELECT edge_id, namespace, source_id, target_id, type, weight, memory_id
                FROM live_edges
                WHERE namespace = ?
                  AND (source_id IN ({placeholders}) OR target_id IN ({placeholders}))
                ORDER BY edge_id ASC
                LIMIT ?
                """,
                (
                    namespace,
                    as_of,
                    namespace,
                    *sorted(frontier),
                    *sorted(frontier),
                    max_edges - len(edge_rows),
                ),
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
            GRAPH_VISIBILITY_CTE
            + f"""
            SELECT node_id, namespace, name, type, properties_json, memory_id,
                   content_sha256, nonce, ciphertext, created_at, updated_at
            FROM live_nodes WHERE namespace = ? AND node_id IN ({placeholders})
            ORDER BY node_id ASC
            """,
            (namespace, as_of, namespace, *sorted(visited)),
        ).fetchall()
        nodes = tuple(
            self._graph_repository.project_live_properties(
                connection,
                self._graph_repository.node_from_row(row),
                as_of=as_of,
            )
            for row in node_rows
        )
        node_ids = {str(node.node_id) for node in nodes}
        edges = tuple(
            self._graph_repository.edge_from_row(row)
            for row in edge_rows.values()
            if row["source_id"] in node_ids and row["target_id"] in node_ids
        )
        return nodes, edges
