from __future__ import annotations

import sqlite3
from uuid import UUID

from aegis_core.memory.graph_repository import GraphRepository
from aegis_core.memory.records import (
    GraphEdgeRecord,
    GraphEmbeddingCandidate,
    GraphNodeRecord,
    SpotlightGraphRecord,
)


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

    def spotlight_records(
        self,
        connection: sqlite3.Connection,
        *,
        namespace: str,
        after_node_id: str | None,
        limit: int,
    ) -> tuple[SpotlightGraphRecord, ...]:
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

    def find_nodes_by_property(
        self,
        connection: sqlite3.Connection,
        *,
        namespace: str,
        property_name: str,
        value: str,
        limit: int,
    ) -> tuple[GraphNodeRecord, ...]:
        digest = self._graph_repository.property_digest(property_name, value)
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
        return tuple(self._graph_repository.node_from_row(row) for row in rows)

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
        frontier = {str(item) for item in seed_ids}
        visited = set(frontier)
        edge_rows: dict[str, sqlite3.Row] = {}
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
