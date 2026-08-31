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
    assert result.traversal_ms < 15.0
    assert result.within_latency_budget is True


@pytest.mark.parametrize("iterations", [4, 11])
def test_ppr_enforces_fixed_safe_iteration_bounds(tmp_path: Path, iterations: int) -> None:
    with pytest.raises(ValueError, match="between five and ten"):
        PersonalizedPageRankSearch(_store(tmp_path), iterations=iterations)
