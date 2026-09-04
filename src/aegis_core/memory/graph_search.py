from __future__ import annotations

import math
import time
from dataclasses import dataclass
from uuid import UUID

from aegis_core.memory.records import (
    GraphEdgeRecord,
    GraphNodeRecord,
    GraphSeedRecord,
)
from aegis_core.memory.sqlite import SQLiteMemoryStore

PPR_DAMPING = 0.85
PPR_ITERATIONS = 7
PPR_BUDGET_MILLISECONDS = 15.0
MAX_GRAPH_CONTEXT_BYTES = 4_096


@dataclass(frozen=True, slots=True)
class GraphSearchResult:
    markdown: str
    ranked_node_ids: tuple[UUID, ...]
    scores: tuple[float, ...]
    traversal_ms: float
    within_latency_budget: bool


class PersonalizedPageRankSearch:
    """Bounded local GraphRAG search with deterministic sparse propagation."""

    def __init__(
        self,
        store: SQLiteMemoryStore,
        *,
        damping: float = PPR_DAMPING,
        iterations: int = PPR_ITERATIONS,
    ) -> None:
        if not 0.0 < damping < 1.0:
            raise ValueError("PPR damping factor is invalid")
        if not 5 <= iterations <= 10:
            raise ValueError("PPR iteration count must be between five and ten")
        self._store = store
        self._damping = damping
        self._iterations = iterations

    def search(
        self,
        *,
        namespace: str,
        model_id: str,
        query_vector: tuple[float, ...],
        seed_limit: int = 5,
        result_limit: int = 8,
    ) -> GraphSearchResult:
        if not 3 <= seed_limit <= 5:
            raise ValueError("GraphRAG seed limit must be between three and five")
        if not 1 <= result_limit <= 16:
            raise ValueError("GraphRAG result limit is out of range")
        seeds = self._store.graph_seed_search(
            namespace=namespace,
            model_id=model_id,
            query_vector=query_vector,
            limit=seed_limit,
        )
        if not seeds:
            return GraphSearchResult(
                markdown="",
                ranked_node_ids=(),
                scores=(),
                traversal_ms=0.0,
                within_latency_budget=True,
            )
        nodes, edges = self._store.load_graph_neighborhood(
            namespace=namespace,
            seed_ids=tuple(seed.node_id for seed in seeds),
        )
        started = time.perf_counter_ns()
        scores = self._ppr(nodes=nodes, edges=edges, seeds=seeds)
        ranking_scores = {
            node.node_id: scores.get(node.node_id, 0.0)
            * (1.12 if node.type in {"device", "sensor", "location"} else 1.0)
            for node in nodes
        }
        ranked = sorted(
            nodes,
            key=lambda node: (
                -ranking_scores.get(node.node_id, 0.0),
                node.type,
                node.name.casefold(),
            ),
        )[:result_limit]
        elapsed_ms = (time.perf_counter_ns() - started) / 1_000_000.0
        markdown = self._format_context(
            ranked=ranked,
            edges=edges,
            nodes_by_id={node.node_id: node for node in nodes},
            scores=ranking_scores,
        )
        return GraphSearchResult(
            markdown=markdown,
            ranked_node_ids=tuple(node.node_id for node in ranked),
            scores=tuple(ranking_scores.get(node.node_id, 0.0) for node in ranked),
            traversal_ms=elapsed_ms,
            within_latency_budget=elapsed_ms <= PPR_BUDGET_MILLISECONDS,
        )

    def _ppr(
        self,
        *,
        nodes: tuple[GraphNodeRecord, ...],
        edges: tuple[GraphEdgeRecord, ...],
        seeds: tuple[GraphSeedRecord, ...],
    ) -> dict[UUID, float]:
        node_ids = {node.node_id for node in nodes}
        if not node_ids:
            return {}
        adjacency: dict[UUID, list[tuple[UUID, float]]] = {node_id: [] for node_id in node_ids}
        for edge in edges:
            if edge.source_id not in node_ids or edge.target_id not in node_ids:
                continue
            adjacency[edge.source_id].append((edge.target_id, edge.weight))
            adjacency[edge.target_id].append((edge.source_id, edge.weight))

        raw_personalization = {
            seed.node_id: max(float(seed.score), 1e-6) for seed in seeds if seed.node_id in node_ids
        }
        total_seed_weight = math.fsum(raw_personalization.values())
        if total_seed_weight <= 0.0:
            return {}
        personalization = {
            node_id: weight / total_seed_weight for node_id, weight in raw_personalization.items()
        }
        ranks = {node_id: personalization.get(node_id, 0.0) for node_id in node_ids}
        outbound_totals = {
            node_id: math.fsum(weight for _, weight in neighbors)
            for node_id, neighbors in adjacency.items()
        }
        teleport = 1.0 - self._damping
        for _ in range(self._iterations):
            next_ranks = {
                node_id: teleport * personalization.get(node_id, 0.0) for node_id in node_ids
            }
            dangling_mass = 0.0
            for source_id, source_rank in ranks.items():
                total_weight = outbound_totals[source_id]
                if total_weight <= 0.0:
                    dangling_mass += source_rank
                    continue
                scale = self._damping * source_rank / total_weight
                for target_id, weight in adjacency[source_id]:
                    next_ranks[target_id] += scale * weight
            if dangling_mass:
                redistributed = self._damping * dangling_mass
                for node_id, probability in personalization.items():
                    next_ranks[node_id] += redistributed * probability
            ranks = next_ranks
        return ranks

    @classmethod
    def _format_context(
        cls,
        *,
        ranked: list[GraphNodeRecord],
        edges: tuple[GraphEdgeRecord, ...],
        nodes_by_id: dict[UUID, GraphNodeRecord],
        scores: dict[UUID, float],
    ) -> str:
        lines = ["# Local Knowledge Graph"]
        for node in ranked:
            lines.append(
                f"- [{cls._escape(node.type)}] {cls._escape(node.name)} "
                f"(relevance={scores.get(node.node_id, 0.0):.4f})"
            )
            adjacent = sorted(
                (
                    edge
                    for edge in edges
                    if edge.source_id == node.node_id or edge.target_id == node.node_id
                ),
                key=lambda edge: (-edge.weight, edge.type, str(edge.edge_id)),
            )[:4]
            for edge in adjacent:
                other_id = edge.target_id if edge.source_id == node.node_id else edge.source_id
                other = nodes_by_id.get(other_id)
                if other is None:
                    continue
                direction = "→" if edge.source_id == node.node_id else "←"
                lines.append(
                    f"  - {cls._escape(edge.type)} {direction} "
                    f"[{cls._escape(other.type)}] {cls._escape(other.name)}"
                )
        encoded = "\n".join(lines).encode("utf-8")
        if len(encoded) <= MAX_GRAPH_CONTEXT_BYTES:
            return encoded.decode("utf-8")
        bounded = encoded[:MAX_GRAPH_CONTEXT_BYTES]
        decoded = bounded.decode("utf-8", errors="ignore")
        return decoded.rsplit("\n", maxsplit=1)[0]

    @staticmethod
    def _escape(value: str) -> str:
        return (
            " ".join(value.split())
            .replace("\\", "\\\\")
            .replace("[", "\\[")
            .replace("]", "\\]")
            .replace("`", "\\`")
        )[:160]
