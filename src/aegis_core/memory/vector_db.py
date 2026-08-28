from __future__ import annotations

import asyncio
from collections.abc import Sequence
from enum import StrEnum
from typing import Protocol

from aegis_core.memory.contracts import MemoryRecord, MemorySearchHit
from aegis_core.memory.sqlite import MAX_MEMORY_VECTORS, MemoryQueryError, SQLiteMemoryStore
from aegis_core.providers.base import (
    EmbeddingBatch,
    EmbeddingInputType,
    EmbeddingProvider,
    EmbeddingProviderError,
)

RRF_K = 60
MAX_BACKFILL_BATCH_TEXT_BYTES = 20_000
MAX_BACKFILL_BATCH_SIZE = 7


class EmbeddingIndexStatus(StrEnum):
    DISABLED = "disabled"
    INDEXED = "indexed"
    UNAVAILABLE = "unavailable"


class MemoryRetriever(Protocol):
    async def retrieve_local(
        self,
        *,
        namespace: str,
        query: str,
        limit: int,
    ) -> tuple[MemorySearchHit, ...]: ...

    async def retrieve(
        self,
        *,
        namespace: str,
        query: str,
        limit: int,
    ) -> tuple[MemorySearchHit, ...]: ...


class BackgroundActivityGate(Protocol):
    async def wait_until_active(self) -> None: ...


class HybridMemoryRetriever:
    """FTS5 plus a bounded sqlite-vec index with lexical failure fallback."""

    def __init__(
        self,
        store: SQLiteMemoryStore,
        *,
        embedding_provider: EmbeddingProvider | None = None,
        vector_scan_limit: int = MAX_MEMORY_VECTORS,
        background_gate: BackgroundActivityGate | None = None,
    ) -> None:
        if not 10 <= vector_scan_limit <= MAX_MEMORY_VECTORS:
            raise ValueError("vector scan limit is out of range")
        self._store = store
        self._embedding_provider = embedding_provider
        self._vector_scan_limit = vector_scan_limit
        self._background_gate = background_gate

    @property
    def semantic_embeddings_enabled(self) -> bool:
        return self._embedding_provider is not None

    @property
    def embedding_model_id(self) -> str | None:
        model_id = getattr(self._embedding_provider, "model_id", None)
        return model_id if isinstance(model_id, str) and model_id else None

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

    async def backfill(self, *, namespace: str, limit: int = 500) -> int:
        provider = self._embedding_provider
        model_id = self.embedding_model_id
        if provider is None or model_id is None or not 1 <= limit <= MAX_MEMORY_VECTORS:
            return 0
        records = await asyncio.to_thread(
            self._store.list_missing_embeddings,
            namespace=namespace,
            model_id=model_id,
            limit=limit,
        )
        indexed = 0
        batches: list[list[MemoryRecord]] = []
        for record in records:
            record_bytes = len(record.content.encode("utf-8"))
            if (
                not batches
                or len(batches[-1]) >= MAX_BACKFILL_BATCH_SIZE
                or sum(len(item.content.encode("utf-8")) for item in batches[-1])
                + record_bytes
                > MAX_BACKFILL_BATCH_TEXT_BYTES
            ):
                batches.append([])
            batches[-1].append(record)
        for batch_records in batches:
            if self._background_gate is not None:
                await self._background_gate.wait_until_active()
            try:
                batch = await provider.embed(
                    [record.content for record in batch_records],
                    input_type=EmbeddingInputType.PASSAGE,
                )
            except EmbeddingProviderError:
                break
            if batch.model_id != model_id or len(batch.vectors) != len(batch_records):
                break
            for record, vector in zip(batch_records, batch.vectors, strict=True):
                if self._background_gate is not None:
                    await self._background_gate.wait_until_active()
                await asyncio.to_thread(
                    self._store.put_embedding,
                    namespace=record.namespace,
                    memory_id=record.memory_id,
                    model_id=batch.model_id,
                    vector=vector,
                    content_sha256=record.content_sha256,
                )
                indexed += 1
        return indexed

    async def retrieve(
        self,
        *,
        namespace: str,
        query: str,
        limit: int,
    ) -> tuple[MemorySearchHit, ...]:
        if self._embedding_provider is None:
            return await self.retrieve_local(namespace=namespace, query=query, limit=limit)
        lexical_result, embedding_result = await asyncio.gather(
            self.retrieve_local(namespace=namespace, query=query, limit=limit),
            self._query_embedding(query),
        )
        if embedding_result is None:
            return lexical_result
        vector_result = await asyncio.to_thread(
            self._store.vector_search,
            namespace=namespace,
            model_id=embedding_result.model_id,
            query_vector=embedding_result.vectors[0],
            limit=limit,
            scan_limit=self._vector_scan_limit,
        )
        return self._reciprocal_rank_fusion((lexical_result, vector_result), limit=limit)

    async def retrieve_local(
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

    async def _query_embedding(self, query: str) -> EmbeddingBatch | None:
        if self._embedding_provider is None:
            return None
        try:
            return await self._embedding_provider.embed(
                [query],
                input_type=EmbeddingInputType.QUERY,
            )
        except EmbeddingProviderError:
            return None

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
