from __future__ import annotations

import asyncio
import hashlib
import threading
from pathlib import Path

import pytest

from aegis_core.memory.contracts import MemoryKind
from aegis_core.memory.graph_service import GraphRAGService
from aegis_core.memory.sqlite import MemoryNotFoundError, SQLiteMemoryStore


def store(tmp_path: Path) -> SQLiteMemoryStore:
    tmp_path.chmod(0o700)
    result = SQLiteMemoryStore(tmp_path / "memory.sqlite3", encryption_secret=b"g" * 32)
    result.initialize()
    return result


@pytest.mark.asyncio
async def test_session_reset_removes_only_bound_short_term_graph(tmp_path: Path) -> None:
    memory = store(tmp_path)
    session_id = memory.create_conversation(namespace="user.default").conversation_id
    bound = memory.put(
        namespace="user.default",
        kind=MemoryKind.EPISODIC,
        content="Abre el resultado actual.",
        source=f"session:{session_id}",
        tags=(f"session.{hashlib.sha256(str(session_id).encode()).hexdigest()[:16]}",),
    )
    durable = memory.put(
        namespace="user.default",
        kind=MemoryKind.PREFERENCE,
        content="Prefiere respuestas breves.",
        source="owner-profile",
    )

    reset = await GraphRAGService(memory).reset_session(
        namespace="user.default",
        session_id=session_id,
    )

    assert reset.memories_deleted == 1
    assert reset.conversation_deleted
    with pytest.raises(MemoryNotFoundError):
        memory.get(namespace="user.default", memory_id=bound.memory_id)
    assert memory.get(namespace="user.default", memory_id=durable.memory_id) == durable


@pytest.mark.asyncio
async def test_reset_discards_hybrid_results_computed_before_forget(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    memory = store(tmp_path)
    session_id = memory.create_conversation(namespace="user.default").conversation_id
    record = memory.put(
        namespace="user.default", kind=MemoryKind.EPISODIC,
        content="Evidencia heliotropo para esta sesión.", source=f"session:{session_id}",
    )
    service = GraphRAGService(memory)
    computed = threading.Event()
    release = threading.Event()
    original = memory.hybrid_rrf_search

    def delayed(**kwargs):
        hits = original(**kwargs)
        assert hits[0].memory_id == record.memory_id
        computed.set()
        assert release.wait(5)
        return hits

    monkeypatch.setattr(memory, "hybrid_rrf_search", delayed)
    task = asyncio.create_task(service.hybrid_search(
        namespace="user.default", query="heliotropo", model_id="test/embed",
        query_vector=(1.0, 0.0),
    ))
    try:
        assert await asyncio.to_thread(computed.wait, 5)
        await service.reset_session(namespace="user.default", session_id=session_id)
    finally:
        release.set()
    result = await asyncio.wait_for(task, 5)
    assert result.hits == ()
    assert result.payload_bytes == 2
