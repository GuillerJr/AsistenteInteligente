from pathlib import Path

import pytest

from aegis_core.memory.contracts import MemoryKind
from aegis_core.memory.graph_search import PersonalizedPageRankSearch
from aegis_core.memory.sqlite import SQLiteMemoryStore


def _store(tmp_path: Path) -> SQLiteMemoryStore:
    tmp_path.chmod(0o700)
    store = SQLiteMemoryStore(tmp_path / "memory.sqlite3", encryption_secret=b"g" * 32)
    store.initialize()
    return store


def test_ppr_connects_multi_hop_concepts_within_bounded_runtime(tmp_path: Path) -> None:
    store = _store(tmp_path)
    record = store.put(
        namespace="user.default",
        kind=MemoryKind.SEMANTIC,
        content="El proyecto Jarvis usa Python. Python requiere SQLite.",
    )
    candidates = store.graph_nodes_for_memory(
        namespace=record.namespace,
        memory_id=record.memory_id,
    )
    for candidate in candidates:
        vector = (1.0, 0.0) if candidate.name == "Jarvis" else (0.0, 1.0)
        store.put_node_embedding(
            namespace=candidate.namespace,
            node_id=candidate.node_id,
            model_id="test/embed",
            vector=vector,
            content_sha256=candidate.content_sha256,
        )

    result = PersonalizedPageRankSearch(store).search(
        namespace="user.default",
        model_id="test/embed",
        query_vector=(1.0, 0.0),
        seed_limit=3,
    )

    assert result.markdown.startswith("# Local Knowledge Graph")
    assert "Jarvis" in result.markdown
    assert "USES" in result.markdown
    assert "Python" in result.markdown
    assert result.ppr_compute_ms == result.traversal_ms
    assert result.end_to_end_ms >= result.traversal_ms >= 0
    assert result.within_latency_budget == (result.end_to_end_ms <= 15.0)


@pytest.mark.parametrize("iterations", [4, 11])
def test_ppr_enforces_fixed_safe_iteration_bounds(tmp_path: Path, iterations: int) -> None:
    with pytest.raises(ValueError, match="between five and ten"):
        PersonalizedPageRankSearch(_store(tmp_path), iterations=iterations)


@pytest.mark.parametrize("has_seeds", [False, True])
def test_latency_budget_includes_sql_and_formatting(
    monkeypatch: pytest.MonkeyPatch, has_seeds: bool,
) -> None:
    from types import SimpleNamespace
    from uuid import uuid4

    clock = [0]
    node_id = uuid4()
    node = SimpleNamespace(node_id=node_id, type="project", name="Orion")

    class SlowStore:
        def graph_seed_search(self, **kwargs):
            clock[0] += 20_000_000
            return (SimpleNamespace(node_id=node_id, score=1.0),) if has_seeds else ()

        def load_graph_neighborhood(self, **kwargs):
            clock[0] += 10_000_000
            return (node,), ()

    def rank(**kwargs):
        clock[0] += 2_000_000
        return {node_id: 1.0}

    def format_context(**kwargs):
        clock[0] += 3_000_000
        return "Evidencia de prueba"

    monkeypatch.setattr("aegis_core.memory.graph_search.time.perf_counter_ns", lambda: clock[0])
    search = PersonalizedPageRankSearch(SlowStore())
    monkeypatch.setattr(search, "_ppr", rank)
    monkeypatch.setattr(search, "_format_context", format_context)
    result = search.search(namespace="user.default", model_id="test/embed", query_vector=(1.0,))

    assert result.seed_ms == 20.0
    assert result.within_latency_budget is False
    assert result.end_to_end_ms == (35.0 if has_seeds else 20.0)
    assert result.neighborhood_ms == (10.0 if has_seeds else 0.0)
    assert result.ppr_compute_ms == (2.0 if has_seeds else 0.0)
    assert result.format_ms == (3.0 if has_seeds else 0.0)
    assert result.end_to_end_ms == sum((
        result.seed_ms, result.neighborhood_ms, result.ppr_compute_ms, result.format_ms,
    ))  # These four spans are serial, disjoint and cover this synchronous search.
