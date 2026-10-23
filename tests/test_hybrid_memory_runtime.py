"""Pipeline evaluation uses synthetic evidence IDs, not claims about encoder accuracy."""

import asyncio
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import httpx
import pytest

from aegis_core.config import Settings
from aegis_core.contracts import TEMPORARY_CONVERSATION_METADATA, UserRequest
from aegis_core.memory.contracts import MemoryKind
from aegis_core.memory.embedding import NvidiaMemoryEmbeddingClient
from aegis_core.memory.graph_service import GraphRAGService
from aegis_core.memory.records import GraphEmbeddingCandidate
from aegis_core.memory.sqlite import SQLiteMemoryStore
from aegis_core.memory.vector_db import EmbeddingBackfillWorker, HybridMemoryRetriever
from aegis_core.orchestration.memory_nodes import create_recall_memory_node
from aegis_core.providers.base import EmbeddingBatch, EmbeddingInputType, EmbeddingProviderError
from aegis_core.providers.nvidia import NvidiaNimClient


class Encoder:
    model_id = "nvidia/synthetic@memory-v1-384-l2"
    dimensions = 384

    def __init__(self):
        self.calls = []
        self.fail = False

    async def embed(self, texts, *, input_type):
        self.calls.append((tuple(texts), input_type))
        if self.fail:
            raise EmbeddingProviderError("synthetic outage")
        vectors = []
        for text in texts:
            axis = 0 if any(term in text for term in ("felinos", "mascota", "gatos")) else 1
            vectors.append(tuple(1.0 if index == axis else 0.0 for index in range(384)))
        return EmbeddingBatch(self.model_id, tuple(vectors))


@pytest.fixture
def store(tmp_path):
    tmp_path.chmod(0o700)
    result = SQLiteMemoryStore(tmp_path / "memory.sqlite3", encryption_secret=b"s" * 32)
    result.initialize()
    return result


def recall_node(retriever):
    return create_recall_memory_node(
        capability_learning=None, memory_limit=1, memory_namespace="user.default",
        memory_retriever=retriever, owner_profile=None, owner_profile_limit=1, social_memory=None,
    )


@pytest.mark.asyncio
async def test_production_recall_fuses_once_and_ranks_evidence_above_distractors(store):
    encoder = Encoder()
    retriever = HybridMemoryRetriever(store, embedding_provider=encoder)
    expected = store.put(namespace="user.default", kind=MemoryKind.PREFERENCE,
                         content="Le agradan los felinos domésticos.")
    distractor = store.put(namespace="user.default", kind=MemoryKind.SEMANTIC,
                           content="La red usa segmentación local.")
    lexical_hits = await retriever.retrieve_local(
        namespace="user.default", query="Qué mascota prefiere", limit=1
    )
    assert expected.memory_id not in [hit.memory_id for hit in lexical_hits]
    await retriever.backfill(namespace="user.default")
    encoder.calls.clear()
    state = await recall_node(retriever)({"request": UserRequest(text="Qué mascota prefiere")})
    ids = [hit.memory_id for hit in state["memory_hits"]]
    # Deterministic Recall@1 and reciprocal rank on the fixture's evidence IDs.
    assert ids == [expected.memory_id]
    assert distractor.memory_id not in ids
    assert "felinos" in state["graph_memory_context"]
    assert [kind for _, kind in encoder.calls] == [EmbeddingInputType.QUERY]
    status = await retriever.status("user.default")
    assert status["state"] == "hybrid_ready"
    assert status["indexed_nodes"] >= 2
    assert status["retrieval_total_ms"] >= status["embedding_ms"] >= 0


@pytest.mark.asyncio
async def test_temporary_chat_never_embeds_or_recalls_private_context(store):
    encoder = Encoder()
    retriever = HybridMemoryRetriever(store, embedding_provider=encoder)
    state = await recall_node(retriever)({"request": UserRequest(
        text="Qué mascota le agrada", metadata={TEMPORARY_CONVERSATION_METADATA: True}
    )})
    assert encoder.calls == []
    assert state["memory_hits"] == ()
    assert state["graph_memory_context"] == ""


@pytest.mark.asyncio
async def test_runtime_states_distinguish_empty_pending_degraded_and_recovered(store):
    encoder = Encoder()
    retriever = HybridMemoryRetriever(store, embedding_provider=encoder)
    assert (await retriever.status("user.default"))["state"] == "lexical_only"
    record = store.put(namespace="user.default", kind=MemoryKind.PREFERENCE,
                       content="Prefiere gatos.")
    assert (await retriever.status("user.default"))["state"] == "indexing"
    encoder.fail = True
    hits, graph = await retriever.retrieve_context(namespace="user.default", query="gatos", limit=2)
    assert [hit.memory_id for hit in hits] == [record.memory_id] and graph is None
    assert (await retriever.status("user.default"))["state"] == "degraded"
    encoder.fail = False
    await retriever.backfill(namespace="user.default")
    assert (await retriever.status("user.default"))["state"] == "hybrid_ready"
    store.delete(namespace="user.default", memory_id=record.memory_id)
    assert (await retriever.status("user.default"))["state"] == "lexical_only"


@pytest.mark.asyncio
async def test_backfill_and_retrieval_preserve_namespace_expiry_and_empty_evidence(store):
    encoder = Encoder()
    retriever = HybridMemoryRetriever(store, embedding_provider=encoder)
    store.put(namespace="private.other", kind=MemoryKind.PREFERENCE, content="Le agradan felinos.")
    store.put(namespace="user.default", kind=MemoryKind.EPISODIC, content="Gatos caducados.",
              expires_at=datetime.now(UTC) - timedelta(seconds=1))
    assert await retriever.backfill(namespace="user.default") == 0
    assert encoder.calls == []
    hits, graph = await retriever.retrieve_context(
        namespace="user.default", query="mascota", limit=2
    )
    assert hits == ()
    assert graph is not None and graph.ranked_node_ids == ()
    assert (await retriever.status("user.default"))["indexed_nodes"] == 0


@pytest.mark.asyncio
async def test_backfill_resumes_after_restart_and_does_not_reuse_old_model_space(store):
    encoder = Encoder()
    store.put(namespace="user.default", kind=MemoryKind.PREFERENCE, content="Prefiere gatos.")
    first = HybridMemoryRetriever(store, embedding_provider=encoder)
    assert await first.backfill(namespace="user.default") > 0
    restarted = HybridMemoryRetriever(store, embedding_provider=encoder)
    assert await restarted.backfill(namespace="user.default") == 0
    encoder.model_id = "nvidia/synthetic@memory-v2-384-l2"
    assert (await restarted.status("user.default"))["state"] == "indexing"
    assert await restarted.backfill(namespace="user.default") > 0
    assert (await restarted.status("user.default"))["state"] == "hybrid_ready"


@pytest.mark.asyncio
async def test_background_cycle_indexes_memories_added_after_initial_coverage(store):
    encoder = Encoder()
    retriever = HybridMemoryRetriever(store, embedding_provider=encoder)
    worker = EmbeddingBackfillWorker(retriever, namespace="user.default", limit=10)
    worker.arm()
    assert await worker.run() == 0
    store.put(namespace="user.default", kind=MemoryKind.PREFERENCE, content="Prefiere gatos.")
    assert await worker.run() > 0
    assert (await retriever.status("user.default"))["state"] == "hybrid_ready"


def test_embedding_projection_omits_network_identifiers_and_api_credentials():
    node = GraphEmbeddingCandidate(uuid4(), "user.default", "Mi dispositivo", "device", {
        "ip_address": "192.0.2.55", "mac_address": "aa:bb:cc:dd:ee:ff",
        "api_token": "private-value", "location": "Sala",
    }, "a" * 64)
    text = HybridMemoryRetriever._embedding_text(node)
    assert "Sala" in text
    assert all(value not in text for value in ("192.0.2.55", "aa:bb", "private-value"))


@pytest.mark.asyncio
async def test_forget_boundary_discards_inflight_retrieval_results(store):
    encoder = Encoder()
    entered, release = asyncio.Event(), asyncio.Event()

    class DelayedGraph(GraphRAGService):
        async def search(self, **kwargs):
            result = await super().search(**kwargs)
            entered.set()
            await release.wait()
            return result

    retriever = HybridMemoryRetriever(store, embedding_provider=encoder,
                                      graph_service=DelayedGraph(store))
    record = store.put(namespace="user.default", kind=MemoryKind.PREFERENCE,
                       content="Prefiere gatos.")
    await retriever.index(record)
    task = asyncio.create_task(retriever.retrieve_context(
        namespace="user.default", query="mascota", limit=1
    ))
    await entered.wait()
    retriever.invalidate()
    store.delete(namespace="user.default", memory_id=record.memory_id)
    release.set()
    assert await task == ((), None)


@pytest.mark.asyncio
async def test_oversized_query_degrades_to_lexical_without_network_or_chat_failure(store):
    calls = []

    async def handler(request):
        calls.append(request)
        return httpx.Response(500)

    async with NvidiaNimClient(
        Settings(), lambda: "synthetic-api-key", transport=httpx.MockTransport(handler)
    ) as client:
        retriever = HybridMemoryRetriever(
            store, embedding_provider=NvidiaMemoryEmbeddingClient(client)
        )
        hits, graph = await retriever.retrieve_context(
            namespace="user.default", query="gatos " * 3000, limit=2
        )
    assert hits == () and graph is None
    assert calls == []
    assert (await retriever.status("user.default"))["state"] == "degraded"
