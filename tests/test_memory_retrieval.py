import asyncio
from collections.abc import Sequence
from pathlib import Path
from uuid import UUID

import pytest

from aegis_core.memory.contracts import MemoryKind
from aegis_core.memory.retrieval import (
    EmbeddingBackfillWorker,
    EmbeddingIndexStatus,
    HybridMemoryRetriever,
)
from aegis_core.memory.sqlite import SQLiteMemoryStore
from aegis_core.providers.base import (
    EmbeddingBatch,
    EmbeddingInputType,
    EmbeddingProviderError,
)
from aegis_core.runtime_state import RuntimeSuspensionController
from aegis_core.tools.audit import HashChainAuditLog


class FakeEmbeddingProvider:
    model_id = "test/embed"

    def __init__(self) -> None:
        self.calls: list[EmbeddingInputType] = []
        self.batch_sizes: list[int] = []

    async def embed(
        self,
        texts: Sequence[str],
        *,
        input_type: EmbeddingInputType,
    ) -> EmbeddingBatch:
        self.calls.append(input_type)
        self.batch_sizes.append(len(texts))
        vectors = []
        for text in texts:
            if input_type is EmbeddingInputType.QUERY or "felinos" in text:
                vectors.append((1.0, 0.0))
            else:
                vectors.append((0.0, 1.0))
        return EmbeddingBatch(model_id=self.model_id, vectors=tuple(vectors))


class OfflineEmbeddingProvider:
    model_id = "test/offline"

    async def embed(
        self,
        texts: Sequence[str],
        *,
        input_type: EmbeddingInputType,
    ) -> EmbeddingBatch:
        del texts, input_type
        raise EmbeddingProviderError("offline details")


def _store(tmp_path: Path) -> SQLiteMemoryStore:
    tmp_path.chmod(0o700)
    store = SQLiteMemoryStore(tmp_path / "memory.sqlite3", encryption_secret=b"m" * 32)
    store.initialize()
    return store


@pytest.mark.asyncio
async def test_graphrag_retrieval_finds_semantic_match_without_shared_terms(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path)
    cats = store.put(
        namespace="user.default",
        kind=MemoryKind.PREFERENCE,
        content="Le agradan los felinos domésticos.",
    )
    network = store.put(
        namespace="user.default",
        kind=MemoryKind.SEMANTIC,
        content="La red usa segmentación local.",
    )
    retriever = HybridMemoryRetriever(
        store,
        embedding_provider=FakeEmbeddingProvider(),
    )
    assert await retriever.index(cats) is EmbeddingIndexStatus.INDEXED
    assert await retriever.index(network) is EmbeddingIndexStatus.INDEXED

    result = await retriever.retrieve_graph(
        namespace="user.default",
        query="¿Qué mascota prefiere?",
        limit=2,
    )

    assert result is not None
    assert result.markdown.startswith("# Local Knowledge Graph")
    assert result.markdown.index("felinos domésticos") < result.markdown.index(
        "segmentación local"
    )


@pytest.mark.asyncio
async def test_embedding_outage_falls_back_to_local_lexical_search(tmp_path: Path) -> None:
    store = _store(tmp_path)
    record = store.put(
        namespace="user.default",
        kind=MemoryKind.SUMMARY,
        content="El proyecto utiliza LangGraph.",
    )
    retriever = HybridMemoryRetriever(
        store,
        embedding_provider=OfflineEmbeddingProvider(),
    )

    status = await retriever.index(record)
    hits = await retriever.retrieve(
        namespace="user.default",
        query="LangGraph",
        limit=5,
    )

    assert status is EmbeddingIndexStatus.UNAVAILABLE
    assert [hit.memory_id for hit in hits] == [record.memory_id]


@pytest.mark.asyncio
async def test_semantic_embedding_is_disabled_without_a_provider(tmp_path: Path) -> None:
    store = _store(tmp_path)
    record = store.put(
        namespace="user.default",
        kind=MemoryKind.EPISODIC,
        content="Una interacción local.",
    )
    retriever = HybridMemoryRetriever(store)

    assert await retriever.index(record) is EmbeddingIndexStatus.DISABLED
    assert retriever.semantic_embeddings_enabled is False


@pytest.mark.asyncio
async def test_local_retrieval_never_calls_remote_embedding_provider(tmp_path: Path) -> None:
    store = _store(tmp_path)
    record = store.put(
        namespace="user.default",
        kind=MemoryKind.PREFERENCE,
        content="El propietario prefiere respuestas breves.",
    )
    provider = FakeEmbeddingProvider()
    retriever = HybridMemoryRetriever(store, embedding_provider=provider)

    hits = await retriever.retrieve_local(
        namespace="user.default",
        query="respuestas breves",
        limit=5,
    )

    assert [hit.memory_id for hit in hits] == [record.memory_id]
    assert provider.calls == []


@pytest.mark.asyncio
async def test_local_embedding_backfill_indexes_only_missing_records(tmp_path: Path) -> None:
    store = _store(tmp_path)
    first = store.put(
        namespace="user.default",
        kind=MemoryKind.PREFERENCE,
        content="Le agradan los felinos domésticos.",
    )
    store.put(
        namespace="user.default",
        kind=MemoryKind.SEMANTIC,
        content="La red usa segmentación local.",
    )
    provider = FakeEmbeddingProvider()
    retriever = HybridMemoryRetriever(store, embedding_provider=provider)
    assert await retriever.index(first) is EmbeddingIndexStatus.INDEXED

    indexed = await retriever.backfill(namespace="user.default", limit=100)
    missing = store.list_missing_graph_embeddings(
        namespace="user.default",
        model_id=provider.model_id,
        limit=100,
    )

    assert indexed >= 1
    assert missing == ()
    result = await retriever.retrieve_graph(
        namespace="user.default",
        query="red segmentada",
        limit=2,
    )
    assert result is not None
    assert "segmentación local" in result.markdown


@pytest.mark.asyncio
async def test_embedding_backfill_waits_for_native_power_recovery(tmp_path: Path) -> None:
    store = _store(tmp_path)
    store.put(
        namespace="user.default",
        kind=MemoryKind.SEMANTIC,
        content="Memoria pendiente de indexación eficiente.",
    )
    provider = FakeEmbeddingProvider()
    gate = RuntimeSuspensionController()
    source_id = UUID("01234567-89ab-cdef-0123-456789abcdef")
    await gate.apply(
        source_id=source_id,
        sequence=1,
        cause="low_power_mode",
        thermal_state="nominal",
        low_power_mode=True,
    )
    retriever = HybridMemoryRetriever(
        store,
        embedding_provider=provider,
        background_gate=gate,
    )

    task = asyncio.create_task(retriever.backfill(namespace="user.default", limit=100))
    await asyncio.sleep(0)
    assert provider.calls == []
    assert not task.done()

    await gate.apply(
        source_id=source_id,
        sequence=2,
        cause="low_power_disabled",
        thermal_state="nominal",
        low_power_mode=False,
    )

    assert await task == 1
    assert provider.calls == [EmbeddingInputType.PASSAGE]


@pytest.mark.asyncio
async def test_background_backfill_waits_for_preflight_and_uses_small_batches(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path)
    for index in range(21):
        store.put(
            namespace="user.default",
            kind=MemoryKind.SEMANTIC,
            content=f"Memoria pendiente número {index}.",
        )
    provider = FakeEmbeddingProvider()
    worker = EmbeddingBackfillWorker(
        HybridMemoryRetriever(store, embedding_provider=provider),
        namespace="user.default",
        limit=21,
        batch_delay_seconds=0,
        retry_delay_seconds=0.01,
    )

    task = asyncio.create_task(worker.run())
    await asyncio.sleep(0)
    assert provider.calls == []

    worker.arm()
    assert await task == 21
    assert provider.batch_sizes == [10, 10, 1]


@pytest.mark.asyncio
async def test_background_backfill_audits_failure_and_resumes_next_cycle(
    tmp_path: Path,
) -> None:
    class FlakyEmbeddingProvider(FakeEmbeddingProvider):
        async def embed(
            self,
            texts: Sequence[str],
            *,
            input_type: EmbeddingInputType,
        ) -> EmbeddingBatch:
            if not self.calls:
                self.calls.append(input_type)
                self.batch_sizes.append(len(texts))
                raise EmbeddingProviderError("temporary outage")
            return await super().embed(texts, input_type=input_type)

    store = _store(tmp_path)
    store.put(
        namespace="user.default",
        kind=MemoryKind.SEMANTIC,
        content="Memoria que debe reanudarse tras una falla temporal.",
    )
    audit = HashChainAuditLog(tmp_path / "audit" / "audit.jsonl")
    provider = FlakyEmbeddingProvider()
    worker = EmbeddingBackfillWorker(
        HybridMemoryRetriever(store, embedding_provider=provider),
        namespace="user.default",
        limit=1,
        audit_sink=audit,
        batch_delay_seconds=0,
        retry_delay_seconds=0.001,
    )

    worker.arm()
    assert await worker.run() == 1

    assert [record.event_type for record in audit.verify()] == [
        "graph_embedding_backfill_failed",
        "graph_embedding_backfill_recovered",
    ]
