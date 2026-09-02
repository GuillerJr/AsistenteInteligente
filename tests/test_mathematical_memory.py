from __future__ import annotations

import json
import math
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from uuid import UUID

import pytest

from aegis_core.memory.contracts import MemoryKind
from aegis_core.memory.graph_service import GraphRAGService, MemoryDecayWorker
from aegis_core.memory.sqlite import (
    DECAY_EVICTION_THRESHOLD,
    EPISODIC_DECAY_LAMBDA,
    MAX_HYBRID_MEMORY_PAYLOAD_BYTES,
    RRF_RANK_CONSTANT,
    MemoryNotFoundError,
    SQLiteMemoryStore,
    calculate_decayed_confidence,
)


def _store(tmp_path: Path) -> SQLiteMemoryStore:
    tmp_path.chmod(0o700)
    store = SQLiteMemoryStore(tmp_path / "memory.sqlite3", encryption_secret=b"r" * 32)
    store.initialize()
    return store


def _unit_vector(index: int) -> tuple[float, ...]:
    return tuple(1.0 if position == index else 0.0 for position in range(384))


def _index_document(
    store: SQLiteMemoryStore,
    *,
    memory_id: UUID,
    vector: tuple[float, ...],
) -> None:
    document = next(
        candidate
        for candidate in store.graph_nodes_for_memory(
            namespace="user.default",
            memory_id=memory_id,
        )
        if candidate.type == "document"
    )
    store.put_node_embedding(
        namespace=document.namespace,
        node_id=document.node_id,
        model_id="test/embed",
        vector=vector,
        content_sha256=document.content_sha256,
    )


def test_rrf_lexical_only_perfect_match_has_first_reciprocal_rank(tmp_path: Path) -> None:
    store = _store(tmp_path)
    lexical = store.put(
        namespace="user.default",
        kind=MemoryKind.PREFERENCE,
        content="El propietario prefiere la palabra heliotropo.",
    )

    hits = store.hybrid_rrf_search(
        namespace="user.default",
        query="heliotropo",
        model_id="test/embed",
        query_vector=_unit_vector(0),
        limit=5,
    )

    assert [hit.memory_id for hit in hits] == [lexical.memory_id]
    assert hits[0].score == pytest.approx(1.0 / (RRF_RANK_CONSTANT + 1.0))


def test_rrf_vector_only_perfect_match_has_first_reciprocal_rank(tmp_path: Path) -> None:
    store = _store(tmp_path)
    semantic = store.put(
        namespace="user.default",
        kind=MemoryKind.PREFERENCE,
        content="El felino doméstico duerme junto a la ventana.",
    )
    _index_document(store, memory_id=semantic.memory_id, vector=_unit_vector(7))

    hits = store.hybrid_rrf_search(
        namespace="user.default",
        query="automóvil",
        model_id="test/embed",
        query_vector=_unit_vector(7),
        limit=5,
    )

    assert [hit.memory_id for hit in hits] == [semantic.memory_id]
    assert hits[0].score == pytest.approx(1.0 / (RRF_RANK_CONSTANT + 1.0))


def test_rrf_combines_lexical_and_semantic_rank_inside_payload_budget(tmp_path: Path) -> None:
    store = _store(tmp_path)
    combined = store.put(
        namespace="user.default",
        kind=MemoryKind.PREFERENCE,
        content="El proyecto Orion utiliza memoria matemática local.",
    )
    _index_document(store, memory_id=combined.memory_id, vector=_unit_vector(3))

    hits = store.hybrid_rrf_search(
        namespace="user.default",
        query="Orion",
        model_id="test/embed",
        query_vector=_unit_vector(3),
        limit=10,
    )
    encoded = json.dumps(
        [hit.model_dump(mode="json") for hit in hits],
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")

    assert hits[0].memory_id == combined.memory_id
    assert hits[0].score == pytest.approx(2.0 / (RRF_RANK_CONSTANT + 1.0))
    assert len(encoded) <= MAX_HYBRID_MEMORY_PAYLOAD_BYTES


def test_fourteen_day_episodic_decay_is_evicted_but_preference_is_stable(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path)
    episodic = store.put(
        namespace="user.default",
        kind=MemoryKind.EPISODIC,
        content="La conversación temporal trató sobre una entrega.",
    )
    preference = store.put(
        namespace="user.default",
        kind=MemoryKind.PREFERENCE,
        content="El propietario prefiere respuestas directas.",
    )
    as_of = episodic.updated_at + timedelta(days=14)
    decayed = calculate_decayed_confidence(
        episodic.confidence,
        episodic.kind.value,
        episodic.updated_at.isoformat(),
        as_of.isoformat(),
    )

    assert decayed == pytest.approx(math.exp(-EPISODIC_DECAY_LAMBDA * 14 * 86_400))
    assert decayed < DECAY_EVICTION_THRESHOLD
    assert store.evict_decayed(namespace="user.default", as_of=as_of) == 1
    with pytest.raises(MemoryNotFoundError):
        store.get(namespace="user.default", memory_id=episodic.memory_id)
    assert store.get(namespace="user.default", memory_id=preference.memory_id) == preference


def test_hyperbolic_reinforcement_updates_authenticated_row(tmp_path: Path) -> None:
    store = _store(tmp_path)
    memory = store.put(
        namespace="user.default",
        kind=MemoryKind.EPISODIC,
        content="El propietario confirmó esta observación.",
        confidence=0.4,
    )
    confirmed_at = datetime.now(UTC) + timedelta(seconds=1)

    reinforced = store.reinforce(
        namespace="user.default",
        memory_id=memory.memory_id,
        delta_boost=0.2,
        confirmed_at=confirmed_at,
    )
    loaded = store.get(namespace="user.default", memory_id=memory.memory_id)

    assert reinforced.confidence == pytest.approx(math.tanh(0.6))
    assert loaded == reinforced
    assert loaded.last_confirmed_at == confirmed_at


@pytest.mark.asyncio
async def test_decay_worker_runs_only_under_nominal_thermal_state(tmp_path: Path) -> None:
    store = _store(tmp_path)
    expired = store.put(
        namespace="user.default",
        kind=MemoryKind.EPISODIC,
        content="Esta observación temporal ya expiró.",
        expires_at=datetime.now(UTC) - timedelta(seconds=1),
    )
    snapshot = SimpleNamespace(thermal_state="serious", low_power_mode=False)
    worker = MemoryDecayWorker(
        GraphRAGService(store),
        namespace="user.default",
        runtime_probe=lambda: snapshot,
    )

    skipped = await worker.run_once()
    assert skipped.skipped_for_thermal_state is True
    assert store.get(namespace="user.default", memory_id=expired.memory_id) == expired

    snapshot.thermal_state = "nominal"
    completed = await worker.run_once()
    assert completed.skipped_for_thermal_state is False
    assert completed.evicted == 1
    with pytest.raises(MemoryNotFoundError):
        store.get(namespace="user.default", memory_id=expired.memory_id)
