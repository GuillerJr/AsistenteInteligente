from __future__ import annotations

import asyncio
import math
from collections.abc import Sequence
from enum import StrEnum
from typing import Protocol
from uuid import uuid4

from aegis_core.memory.contracts import MemoryRecord, MemorySearchHit
from aegis_core.memory.sqlite import MAX_MEMORY_VECTORS, MemoryQueryError, SQLiteMemoryStore
from aegis_core.providers.base import (
    EmbeddingBatch,
    EmbeddingInputType,
    EmbeddingProvider,
    EmbeddingProviderError,
)
from aegis_core.tools.audit import AuditSink, NullAuditSink

RRF_K = 60
MAX_BACKFILL_BATCH_TEXT_BYTES = 20_000
MAX_BACKFILL_BATCH_SIZE = 10
DEFAULT_BACKFILL_BATCH_DELAY_SECONDS = 0.5
DEFAULT_BACKFILL_RETRY_DELAY_SECONDS = 5.0


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

    async def backfill(
        self,
        *,
        namespace: str,
        limit: int = 500,
        inter_batch_delay_seconds: float = 0.0,
        raise_provider_errors: bool = False,
    ) -> int:
        provider = self._embedding_provider
        model_id = self.embedding_model_id
        if not math.isfinite(inter_batch_delay_seconds) or inter_batch_delay_seconds < 0:
            raise ValueError("embedding backfill delay cannot be negative")
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
        for batch_index, batch_records in enumerate(batches):
            if batch_index > 0 and inter_batch_delay_seconds > 0:
                await asyncio.sleep(inter_batch_delay_seconds)
            if self._background_gate is not None:
                await self._background_gate.wait_until_active()
            try:
                batch = await provider.embed(
                    [record.content for record in batch_records],
                    input_type=EmbeddingInputType.PASSAGE,
                )
            except EmbeddingProviderError:
                if raise_provider_errors:
                    raise
                break
            if batch.model_id != model_id or len(batch.vectors) != len(batch_records):
                if raise_provider_errors:
                    raise EmbeddingProviderError(
                        "embedding provider returned an inconsistent backfill batch"
                    )
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


class EmbeddingBackfillWorker:
    """Indexes missing vectors only after an authenticated preflight response is sent."""

    def __init__(
        self,
        retriever: HybridMemoryRetriever,
        *,
        namespace: str,
        limit: int,
        audit_sink: AuditSink | None = None,
        batch_size: int = MAX_BACKFILL_BATCH_SIZE,
        batch_delay_seconds: float = DEFAULT_BACKFILL_BATCH_DELAY_SECONDS,
        retry_delay_seconds: float = DEFAULT_BACKFILL_RETRY_DELAY_SECONDS,
    ) -> None:
        if not 0 <= limit <= 2_000:
            raise ValueError("embedding backfill limit is out of range")
        if not 1 <= batch_size <= 20:
            raise ValueError("embedding backfill batch size is out of range")
        if (
            not math.isfinite(batch_delay_seconds)
            or not math.isfinite(retry_delay_seconds)
            or batch_delay_seconds < 0
            or retry_delay_seconds <= 0
        ):
            raise ValueError("embedding backfill worker delays are invalid")
        self._retriever = retriever
        self._namespace = namespace
        self._limit = limit
        self._audit_sink = audit_sink or NullAuditSink()
        self._batch_size = batch_size
        self._batch_delay_seconds = batch_delay_seconds
        self._retry_delay_seconds = retry_delay_seconds
        self._preflight_response_sent = asyncio.Event()

    def arm(self) -> None:
        """Permit background work after the daemon has flushed an OK preflight response."""
        self._preflight_response_sent.set()

    async def run(self) -> int:
        await self._preflight_response_sent.wait()
        remaining = self._limit
        indexed_total = 0
        failure_active = False
        while remaining > 0:
            try:
                indexed = await self._retriever.backfill(
                    namespace=self._namespace,
                    limit=min(self._batch_size, remaining),
                    inter_batch_delay_seconds=self._batch_delay_seconds,
                    raise_provider_errors=True,
                )
            except asyncio.CancelledError:
                raise
            except Exception as error:
                if not failure_active:
                    self._record_event(
                        event_type="embedding_backfill_failed",
                        data={
                            "error_type": type(error).__name__,
                            "retry_delay_milliseconds": int(
                                self._retry_delay_seconds * 1_000
                            ),
                        },
                    )
                failure_active = True
                await asyncio.sleep(self._retry_delay_seconds)
                continue

            if failure_active:
                self._record_event(
                    event_type="embedding_backfill_recovered",
                    data={"indexed_records": indexed},
                )
                failure_active = False
            if indexed == 0:
                break
            indexed_total += indexed
            remaining -= indexed
            if remaining > 0:
                await asyncio.sleep(self._batch_delay_seconds)
        return indexed_total

    def _record_event(
        self,
        *,
        event_type: str,
        data: dict[str, str | int | bool | None],
    ) -> None:
        try:
            self._audit_sink.record_system_event(
                uuid4(),
                event_type=event_type,
                component="semantic_memory",
                data=data,
            )
        except Exception:
            # Indexing remains resumable even if the ledger is temporarily unavailable.
            return
