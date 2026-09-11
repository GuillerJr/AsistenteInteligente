from __future__ import annotations

from datetime import datetime


def memory_is_live(expires_at: str | None, as_of: str) -> bool:
    """Compare aware instants exactly, including offsets and microseconds."""
    if expires_at is None:
        return True
    expiry, now = datetime.fromisoformat(expires_at), datetime.fromisoformat(as_of)
    if expiry.utcoffset() is None or now.utcoffset() is None:
        raise ValueError("memory expiry timestamps must be timezone-aware")
    return expiry > now


# Prepend to graph queries and bind namespace/as_of FIRST. These are ephemeral
# projections, not persistent SQL views that could bypass trusted_schema=OFF.
# Shared entities survive only with live provenance (or as standalone nodes).
GRAPH_VISIBILITY_CTE = """
WITH scope AS (SELECT ? AS namespace, ? AS as_of),
live_memories AS MATERIALIZED (
    SELECT m.memory_id FROM memory_items m, scope
    WHERE m.namespace = scope.namespace AND aegis_memory_live(m.expires_at, scope.as_of)
),
base_nodes AS MATERIALIZED (
    SELECT n.* FROM nodes n, scope WHERE n.namespace = scope.namespace
    AND (n.memory_id IS NULL OR n.memory_id IN (SELECT memory_id FROM live_memories))
),
live_edges AS MATERIALIZED (
    SELECT e.* FROM edges e, scope
    WHERE e.namespace = scope.namespace
    AND (e.memory_id IS NULL OR e.memory_id IN (SELECT memory_id FROM live_memories))
    AND e.source_id IN (SELECT node_id FROM base_nodes)
    AND e.target_id IN (SELECT node_id FROM base_nodes)
),
live_nodes AS (
    SELECT n.* FROM base_nodes n
    WHERE n.memory_id IS NOT NULL
    OR EXISTS (SELECT 1 FROM live_edges e WHERE e.source_id = n.node_id OR e.target_id = n.node_id)
    OR NOT EXISTS (
        SELECT 1 FROM edges e WHERE e.namespace = n.namespace
        AND (e.source_id = n.node_id OR e.target_id = n.node_id)
    )
)
"""
