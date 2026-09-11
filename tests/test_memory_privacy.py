"""Regression checks use only disposable, synthetic memory databases."""

import asyncio
import sqlite3
import threading
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path
from uuid import UUID, uuid4

import pytest

from aegis_core.memory.contracts import ConversationTurn, MemoryKind
from aegis_core.memory.conversations import ConversationCoordinator
from aegis_core.memory.errors import DecryptionAuthError, MemoryNotFoundError
from aegis_core.memory.graph_search import GraphSearchResult
from aegis_core.memory.graph_service import MAX_CACHED_GRAPH_SESSIONS, GraphRAGService
from aegis_core.memory.sqlite import SQLiteMemoryStore
from aegis_core.memory.visibility import memory_is_live

NS = "test.private"


@pytest.fixture
def store(tmp_path: Path) -> SQLiteMemoryStore:
    tmp_path.chmod(0o700)
    result = SQLiteMemoryStore(tmp_path / "memory.sqlite3", encryption_secret=b"m" * 32)
    result.initialize()
    return result


def test_conversation_payloads_are_not_plaintext(store: SQLiteMemoryStore) -> None:
    conversation = store.create_conversation(namespace=NS, title="synthetic-private-title")
    turns = store.append_conversation_exchange(
        namespace=NS,
        conversation_id=conversation.conversation_id,
        user_content="synthetic-private-question",
        assistant_content="synthetic-private-answer",
    )
    assert (
        store.conversation_history(namespace=NS, conversation_id=conversation.conversation_id)
        == turns
    )
    assert (
        store.get_conversation(namespace=NS, conversation_id=conversation.conversation_id).title
        == conversation.title
    )
    for path in store.path.parent.glob("memory.sqlite3*"):
        contents = path.read_bytes()
        for text in (conversation.title, *(turn.content for turn in turns)):
            assert text.encode() not in contents


@pytest.mark.parametrize(
    "field,value", [("role", "user"), ("sequence", 9), ("created_at", "2020-01-01T00:00:00+00:00")]
)
def test_conversation_turn_metadata_is_authenticated(
    store: SQLiteMemoryStore, field: str, value: object
) -> None:
    conversation = store.create_conversation(namespace=NS)
    turns = store.append_conversation_exchange(
        namespace=NS,
        conversation_id=conversation.conversation_id,
        user_content="question",
        assistant_content="answer",
    )
    with sqlite3.connect(store.path) as connection:
        connection.execute(
            f"UPDATE conversation_turns SET {field} = ? WHERE turn_id = ?",
            (value, str(turns[1].turn_id)),
        )
    with pytest.raises(DecryptionAuthError):
        store.conversation_history(namespace=NS, conversation_id=conversation.conversation_id)
    with pytest.raises(DecryptionAuthError):
        store.create_conversation(namespace=NS)


def test_recomputed_plaintext_hash_cannot_forge_a_turn(store: SQLiteMemoryStore) -> None:
    conversation = store.create_conversation(namespace=NS)
    store.append_conversation_exchange(
        namespace=NS,
        conversation_id=conversation.conversation_id,
        user_content="question",
        assistant_content="answer",
    )
    with sqlite3.connect(store.path) as connection:
        connection.execute(
            "UPDATE conversation_turns SET content = ?, content_sha256 = ?",
            ("forged", ConversationTurn.digest_content("forged")),
        )
    with pytest.raises(DecryptionAuthError):
        store.conversation_history(namespace=NS, conversation_id=conversation.conversation_id)


def test_title_tampering_prevents_append(store: SQLiteMemoryStore) -> None:
    conversation = store.create_conversation(namespace=NS, title="original")
    with sqlite3.connect(store.path) as connection:
        connection.execute("UPDATE conversations SET title = 'forged'")
    with pytest.raises(DecryptionAuthError):
        store.append_conversation_exchange(
            namespace=NS,
            conversation_id=conversation.conversation_id,
            user_content="question",
            assistant_content="answer",
        )
    with sqlite3.connect(store.path) as connection:
        assert connection.execute("SELECT COUNT(*) FROM conversation_turns").fetchone()[0] == 0


@pytest.mark.parametrize("expired", [False, True])
def test_expiry_compares_instants_not_text(store: SQLiteMemoryStore, expired: bool) -> None:
    now = datetime.now(UTC)
    expiry = (now + timedelta(hours=-1 if expired else 1)).astimezone(
        timezone(timedelta(hours=12 if expired else -12))
    )
    record = store.put(
        namespace=NS,
        kind=MemoryKind.PREFERENCE,
        content="synthetic timezone memory",
        tags=("clock",),
        expires_at=expiry,
    )
    assert bool(store.search(namespace=NS, query="timezone")) is not expired
    assert bool(store.list_by_tag(namespace=NS, tag="clock")) is not expired
    assert store.evict_decayed(namespace=NS, as_of=now) == int(expired)
    if not expired:
        assert store.get(namespace=NS, memory_id=record.memory_id) == record


def test_expired_graph_is_invisible_before_physical_eviction(store: SQLiteMemoryStore) -> None:
    record = store.put(
        namespace=NS,
        kind=MemoryKind.PREFERENCE,
        content="Alice usa Python en el proyecto Atlas.",
        expires_at=datetime.now(UTC) - timedelta(hours=1),
    )
    assert store.graph_nodes_for_memory(namespace=NS, memory_id=record.memory_id) == ()
    assert store.list_missing_graph_embeddings(namespace=NS, model_id="test/embed") == ()
    assert store.spotlight_graph_records(namespace=NS) == ()
    with sqlite3.connect(store.path) as connection:
        ids = [UUID(row[0]) for row in connection.execute("SELECT node_id FROM nodes")]
    for node_id in ids:
        assert store.load_graph_neighborhood(namespace=NS, seed_ids=(node_id,)) == ((), ())
    # Retrieval must not implement expiry by mutating the user's database.
    assert store.get(namespace=NS, memory_id=record.memory_id) == record


@pytest.mark.asyncio
async def test_missing_conversations_do_not_leak_locks(store: SQLiteMemoryStore) -> None:
    coordinator = ConversationCoordinator(store, namespace=NS)
    for _ in range(20):
        with pytest.raises(MemoryNotFoundError):
            async with coordinator.serialized(uuid4()):
                pytest.fail("missing conversation accepted")
    assert len(coordinator._locks) == 0


@pytest.mark.asyncio
async def test_cancelled_exchange_keeps_lock_until_database_worker_finishes(
    store: SQLiteMemoryStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    coordinator = ConversationCoordinator(store, namespace=NS)
    conversation = await coordinator.create()
    started = threading.Event()
    release = threading.Event()
    finished = threading.Event()
    original = store.append_conversation_exchange

    def delayed(**kwargs):
        started.set()
        assert release.wait(5)
        try:
            return original(**kwargs)
        finally:
            finished.set()

    monkeypatch.setattr(store, "append_conversation_exchange", delayed)

    async def exchange():
        async with coordinator.serialized(conversation.conversation_id):
            await coordinator.record_exchange(
                conversation.conversation_id, user_content="question", assistant_content="answer"
            )

    task = asyncio.create_task(exchange())
    try:
        assert await asyncio.to_thread(started.wait, 2)
        task.cancel()
        await asyncio.sleep(0.02)
        assert not task.done()
        assert coordinator._locks[conversation.conversation_id].locked()
    finally:
        release.set()
        await asyncio.gather(task, return_exceptions=True)
        await asyncio.to_thread(finished.wait, 2)
    assert len(coordinator._locks) == 0


@pytest.mark.parametrize("offset", [-12, 0, 14])
def test_expiry_boundary_preserves_microseconds(offset: int) -> None:
    now = datetime(2026, 9, 11, 14, 0, 0, 123456, tzinfo=UTC)
    zone = timezone(timedelta(hours=offset))
    assert memory_is_live(
        (now + timedelta(microseconds=1)).astimezone(zone).isoformat(), now.isoformat()
    )
    assert not memory_is_live(now.astimezone(zone).isoformat(), now.isoformat())
    assert not memory_is_live(
        (now - timedelta(microseconds=1)).astimezone(zone).isoformat(), now.isoformat()
    )


@pytest.mark.parametrize("target", ["namespace", "conversation", "turn"])
def test_history_cannot_be_transplanted(store: SQLiteMemoryStore, target: str) -> None:
    header = store.create_conversation(namespace=NS)
    other = store.create_conversation(namespace=NS)
    turns = store.append_conversation_exchange(
        namespace=NS,
        conversation_id=header.conversation_id,
        user_content="question",
        assistant_content="answer",
    )
    with sqlite3.connect(store.path) as connection:
        if target == "namespace":
            connection.execute(
                "UPDATE conversations SET namespace = 'test.other' WHERE conversation_id = ?",
                (str(header.conversation_id),),
            )
        elif target == "conversation":
            connection.execute(
                "UPDATE conversation_turns SET conversation_id = ?", (str(other.conversation_id),)
            )
        else:
            connection.execute(
                "UPDATE conversation_turns SET turn_id = ? WHERE turn_id = ?",
                (str(uuid4()), str(turns[0].turn_id)),
            )
    with pytest.raises(DecryptionAuthError):
        store.conversation_history(
            namespace="test.other" if target == "namespace" else NS,
            conversation_id=header.conversation_id,
        )


def test_truncated_history_is_detected_before_read_or_append(store: SQLiteMemoryStore) -> None:
    header = store.create_conversation(namespace=NS)
    store.append_conversation_exchange(
        namespace=NS,
        conversation_id=header.conversation_id,
        user_content="question",
        assistant_content="answer",
    )
    with sqlite3.connect(store.path) as connection:
        connection.execute("DELETE FROM conversation_turns WHERE sequence = 2")
    with pytest.raises(DecryptionAuthError):
        store.conversation_history(namespace=NS, conversation_id=header.conversation_id, limit=1)


@pytest.mark.asyncio
async def test_cancelled_waiter_cannot_split_conversation_lock(store: SQLiteMemoryStore) -> None:
    coordinator = ConversationCoordinator(store, namespace=NS)
    header = await coordinator.create()
    entered = asyncio.Event()

    async def waiter():
        async with coordinator.serialized(header.conversation_id):
            entered.set()

    async with coordinator.serialized(header.conversation_id):
        first = asyncio.create_task(waiter())
        await asyncio.sleep(0)
        first.cancel()
        await asyncio.gather(first, return_exceptions=True)
        second = asyncio.create_task(waiter())
        await asyncio.sleep(0)
        assert not entered.is_set()
        assert coordinator._lock_users[header.conversation_id] == 2
    await second
    assert entered.is_set()
    assert coordinator._locks == coordinator._lock_users == {}


def test_expired_graph_does_not_leak_through_live_shared_entity(store: SQLiteMemoryStore) -> None:
    expired = store.put(
        namespace=NS,
        kind=MemoryKind.SEMANTIC,
        content="El proyecto Antiguo usa Python.",
        expires_at=datetime.now(UTC) - timedelta(hours=1),
    )
    live = store.put(
        namespace=NS, kind=MemoryKind.SEMANTIC, content="El proyecto Vigente usa Python."
    )
    foreign = store.put(
        namespace="test.other", kind=MemoryKind.SEMANTIC, content="El proyecto Ajeno usa Python."
    )
    seeds = store.graph_nodes_for_memory(namespace=NS, memory_id=live.memory_id)
    nodes, edges = store.load_graph_neighborhood(namespace=NS, seed_ids=(seeds[0].node_id,))
    assert any(node.memory_id == live.memory_id for node in nodes)
    assert not any(node.memory_id in {expired.memory_id, foreign.memory_id} for node in nodes)
    assert all(edge.memory_id == live.memory_id for edge in edges)
    assert all(
        edge.source_id in {n.node_id for n in nodes}
        and edge.target_id in {n.node_id for n in nodes}
        for edge in edges
    )
    spotlight = store.spotlight_graph_records(namespace=NS)
    assert "Antiguo" not in repr(spotlight)
    assert "Ajeno" not in repr(spotlight)


def test_graph_node_budget_never_returns_dangling_edges(store: SQLiteMemoryStore) -> None:
    record = store.put(
        namespace=NS, kind=MemoryKind.SEMANTIC, content="El proyecto Atlas usa Python y SQLite."
    )
    seeds = store.graph_nodes_for_memory(namespace=NS, memory_id=record.memory_id)
    nodes, edges = store.load_graph_neighborhood(
        namespace=NS, seed_ids=tuple(seed.node_id for seed in seeds[:3]), max_nodes=1
    )
    assert len(nodes) == 1
    assert edges == ()


@pytest.mark.parametrize("forget", [False, True])
def test_device_properties_require_current_evidence_even_with_live_neighbors(
    store: SQLiteMemoryStore, forget: bool
) -> None:
    live = store.put(
        namespace=NS,
        kind=MemoryKind.SEMANTIC,
        content="El televisor está en la sala y su IP es 192.0.2.20.",
    )
    old = store.put(
        namespace=NS,
        kind=MemoryKind.SEMANTIC,
        content="El televisor está en la oficina y su IP es 192.0.2.10.",
        expires_at=datetime.now(UTC) - timedelta(hours=1),
    )
    if forget:
        store.delete(namespace=NS, memory_id=old.memory_id)
    assert (
        store.find_graph_nodes_by_property(
            namespace=NS, property_name="ip_address", value="192.0.2.10"
        )
        == ()
    )
    nodes = store.find_graph_nodes_by_property(
        namespace=NS, property_name="ip_address", value="192.0.2.20"
    )
    assert len(nodes) == 1
    assert nodes[0].properties["ip_address"] == "192.0.2.20"
    projected, _ = store.load_graph_neighborhood(namespace=NS, seed_ids=(nodes[0].node_id,))
    assert "192.0.2.10" not in repr(projected)
    candidates = store.graph_nodes_for_memory(namespace=NS, memory_id=live.memory_id)
    assert "192.0.2.10" not in repr(candidates)


@pytest.mark.asyncio
async def test_graph_session_cache_is_bounded(
    store: SQLiteMemoryStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    service = GraphRAGService(store)
    result = GraphSearchResult("synthetic", (uuid4(),), (1.0,), 0.0, True)
    monkeypatch.setattr(service._search, "search", lambda **kwargs: result)
    first = uuid4()
    await service.search(
        namespace=NS, session_id=first, model_id="test/embed", query_vector=(1.0, 0.0)
    )
    for _ in range(MAX_CACHED_GRAPH_SESSIONS):
        await service.search(
            namespace=NS, session_id=uuid4(), model_id="test/embed", query_vector=(1.0, 0.0)
        )
    assert len(service._seed_cache) == MAX_CACHED_GRAPH_SESSIONS
    assert await service.cached_seeds(namespace=NS, session_id=first) == ()


@pytest.mark.asyncio
async def test_inflight_graph_search_cannot_resurrect_reset_session(
    store: SQLiteMemoryStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    service = GraphRAGService(store)
    session = uuid4()
    started, release = threading.Event(), threading.Event()

    def search(**kwargs):
        started.set()
        assert release.wait(5)
        return GraphSearchResult("must be discarded", (uuid4(),), (1.0,), 0.0, True)

    monkeypatch.setattr(service._search, "search", search)
    task = asyncio.create_task(
        service.search(
            namespace=NS, session_id=session, model_id="test/embed", query_vector=(1.0, 0.0)
        )
    )
    try:
        assert await asyncio.to_thread(started.wait, 2)
        await service.reset_session(namespace=NS, session_id=session)
    finally:
        release.set()
        result = await task
    assert result.markdown == ""
    assert await service.cached_seeds(namespace=NS, session_id=session) == ()


def test_expired_nearest_vectors_cannot_starve_live_seeds(
    store: SQLiteMemoryStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    import aegis_core.memory.graph_embedding_repository as embeddings

    expiry = datetime.now(UTC) + timedelta(hours=1)
    for index in range(41):
        record = store.put(
            namespace=NS,
            kind=MemoryKind.SEMANTIC,
            content=f"syntheticvector{index}",
            expires_at=expiry if index < 40 else None,
        )
        node = store.graph_nodes_for_memory(namespace=NS, memory_id=record.memory_id)[0]
        store.put_node_embedding(
            namespace=NS,
            node_id=node.node_id,
            model_id="test/embed",
            vector=(1.0, 0.0) if index < 40 else (0.8, 0.2),
            content_sha256=node.content_sha256,
        )
        live_node = node.node_id

    class FutureClock:
        @staticmethod
        def now(zone):
            return expiry + timedelta(seconds=1)

    monkeypatch.setattr(embeddings, "datetime", FutureClock)
    seeds = store.graph_seed_search(
        namespace=NS, model_id="test/embed", query_vector=(1.0, 0.0), limit=1
    )
    assert [seed.node_id for seed in seeds] == [live_node]


def test_hybrid_expiry_uses_the_same_instant_contract(store: SQLiteMemoryStore) -> None:
    now = datetime.now(UTC)
    visible = store.put(
        namespace=NS,
        kind=MemoryKind.SEMANTIC,
        content="synthetic offset visible",
        expires_at=(now + timedelta(hours=1)).astimezone(timezone(timedelta(hours=-12))),
    )
    store.put(
        namespace=NS,
        kind=MemoryKind.SEMANTIC,
        content="synthetic offset expired",
        expires_at=(now - timedelta(hours=1)).astimezone(timezone(timedelta(hours=12))),
    )
    hits = store.hybrid_rrf_search(
        namespace=NS, query="synthetic offset", model_id="test/embed", query_vector=(1.0, 0.0)
    )
    assert [hit.memory_id for hit in hits] == [visible.memory_id]
