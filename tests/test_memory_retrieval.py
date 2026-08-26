from collections.abc import Sequence
from pathlib import Path

import pytest

from aegis_core.memory.contracts import MemoryKind
from aegis_core.memory.retrieval import EmbeddingIndexStatus, HybridMemoryRetriever
from aegis_core.memory.sqlite import SQLiteMemoryStore
from aegis_core.providers.base import (
    EmbeddingBatch,
    EmbeddingInputType,
    EmbeddingProviderError,
)


class FakeEmbeddingProvider:
    model_id = "test/embed"

    def __init__(self) -> None:
        self.calls: list[EmbeddingInputType] = []

    async def embed(
        self,
        texts: Sequence[str],
        *,
        input_type: EmbeddingInputType,
    ) -> EmbeddingBatch:
        self.calls.append(input_type)
        vectors = []
        for text in texts:
            if input_type is EmbeddingInputType.QUERY or "felinos" in text:
                vectors.append((1.0, 0.0))
            else:
                vectors.append((0.0, 1.0))
        return EmbeddingBatch(model_id=self.model_id, vectors=tuple(vectors))


class OfflineEmbeddingProvider:
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
    store = SQLiteMemoryStore(tmp_path / "memory.sqlite3")
    store.initialize()
    return store


@pytest.mark.asyncio
async def test_hybrid_retrieval_finds_semantic_match_without_shared_terms(
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

    hits = await retriever.retrieve(
        namespace="user.default",
        query="¿Qué mascota prefiere?",
        limit=2,
    )

    assert hits[0].memory_id == cats.memory_id
    assert {hit.memory_id for hit in hits} == {cats.memory_id, network.memory_id}


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
async def test_remote_embedding_is_disabled_by_default(tmp_path: Path) -> None:
    store = _store(tmp_path)
    record = store.put(
        namespace="user.default",
        kind=MemoryKind.EPISODIC,
        content="Una interacción local.",
    )
    retriever = HybridMemoryRetriever(store)

    assert await retriever.index(record) is EmbeddingIndexStatus.DISABLED
    assert retriever.remote_embeddings_enabled is False


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
