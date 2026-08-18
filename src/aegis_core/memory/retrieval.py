from __future__ import annotations

import asyncio
from collections.abc import Sequence
from enum import StrEnum
from typing import Protocol

from aegis_core.memory.contracts import MemoryRecord, MemorySearchHit
from aegis_core.memory.sqlite import MemoryQueryError, SQLiteMemoryStore
from aegis_core.providers.base import (
    EmbeddingInputType,
    EmbeddingProvider,
    EmbeddingProviderError,
)

RRF_K = 60


class EmbeddingIndexStatus(StrEnum):
    DISABLED = "disabled"
    INDEXED = "indexed"
    UNAVAILABLE = "unavailable"


class MemoryRetriever(Protocol):
    async def retrieve(
        self,
        *,
        namespace: str,
        query: str,
        limit: int,
    ) -> tuple[MemorySearchHit, ...]: ...


class HybridMemoryRetriever:
    def __init__(
        self,
        store: SQLiteMemoryStore,
        *,
        embedding_provider: EmbeddingProvider | None = None,
        vector_scan_limit: int = 2_000,
    ) -> None:
        if not 10 <= vector_scan_limit <= 50_000:
            raise ValueError("vector scan limit is out of range")
        self._store = store
        self._embedding_provider = embedding_provider
        self._vector_scan_limit = vector_scan_limit

    @property
    def remote_embeddings_enabled(self) -> bool:
        return self._embedding_provider is not None

    async def index(self, record: MemoryRecord) -> EmbeddingIndexStatus:
        if self._embedding_provider is None:
            return EmbeddingIndexStatus.DISABLED
        try:
            batch = await self._embedding_provider.embed(
                [record.content],
                input_type=EmbeddingInputType.PASSAGE,
            )
        except EmbeddingProviderError:
            return EmbeddingIndexStatus.UNAVAILABLE
        await asyncio.to_thread(
            self._store.put_embedding,
            namespace=record.namespace,
            memory_id=record.memory_id,
            model_id=batch.model_id,
            vector=batch.vectors[0],
            content_sha256=record.content_sha256,
        )
        return EmbeddingIndexStatus.INDEXED

    async def retrieve(
        self,
        *,
        namespace: str,
        query: str,
        limit: int,
    ) -> tuple[MemorySearchHit, ...]:
        lexical = await self._lexical_search(namespace=namespace, query=query, limit=limit)
        if self._embedding_provider is None:
            return lexical
        try:
            batch = await self._embedding_provider.embed(
                [query],
                input_type=EmbeddingInputType.QUERY,
            )
        except EmbeddingProviderError:
            return lexical
        vector = await asyncio.to_thread(
            self._store.vector_search,
            namespace=namespace,
            model_id=batch.model_id,
            query_vector=batch.vectors[0],
            limit=limit,
            scan_limit=self._vector_scan_limit,
        )
        return self._reciprocal_rank_fusion((lexical, vector), limit=limit)

    async def _lexical_search(
        self,
        *,
        namespace: str,
        query: str,
        limit: int,
    ) -> tuple[MemorySearchHit, ...]:
        try:
            return await asyncio.to_thread(
                self._store.search,
                namespace=namespace,
                query=query,
                limit=limit,
            )
        except MemoryQueryError:
            return ()

    @staticmethod
    def _reciprocal_rank_fusion(
        rankings: Sequence[tuple[MemorySearchHit, ...]],
        *,
        limit: int,
    ) -> tuple[MemorySearchHit, ...]:
        scores: dict[object, float] = {}
        hits: dict[object, MemorySearchHit] = {}
        for ranking in rankings:
            for rank, hit in enumerate(ranking, start=1):
                hits[hit.memory_id] = hit
                scores[hit.memory_id] = scores.get(hit.memory_id, 0.0) + 1.0 / (RRF_K + rank)
        ordered = sorted(
            hits.values(),
            key=lambda hit: (
                -scores[hit.memory_id],
                -hit.updated_at.timestamp(),
                str(hit.memory_id),
            ),
        )
        return tuple(
            hit.model_copy(update={"score": scores[hit.memory_id]}) for hit in ordered[:limit]
        )
