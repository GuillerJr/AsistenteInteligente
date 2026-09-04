from __future__ import annotations

import asyncio
import json
import math
from enum import StrEnum
from typing import Protocol
from uuid import uuid4

from aegis_core.memory.contracts import MemoryRecord, MemorySearchHit
from aegis_core.memory.errors import MemoryQueryError
from aegis_core.memory.graph_search import GraphSearchResult
from aegis_core.memory.graph_service import GraphRAGService
from aegis_core.memory.records import GraphEmbeddingCandidate
from aegis_core.memory.sqlite import MAX_NODE_EMBEDDINGS, SQLiteMemoryStore
from aegis_core.providers.base import EmbeddingInputType, EmbeddingProvider, EmbeddingProviderError
from aegis_core.tools.audit import AuditSink, NullAuditSink

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
        self, *, namespace: str, query: str, limit: int
    ) -> tuple[MemorySearchHit, ...]: ...

    async def retrieve(
        self, *, namespace: str, query: str, limit: int
    ) -> tuple[MemorySearchHit, ...]: ...

    async def retrieve_graph(
        self, *, namespace: str, query: str, limit: int
    ) -> GraphSearchResult | None: ...


class BackgroundActivityGate(Protocol):
    async def wait_until_active(self) -> None: ...


class HybridMemoryRetriever:
    """FTS5 fallback plus bounded sqlite-vec seed search and local PPR."""

    def __init__(
        self,
        store: SQLiteMemoryStore,
        *,
        embedding_provider: EmbeddingProvider | None = None,
        background_gate: BackgroundActivityGate | None = None,
        graph_service: GraphRAGService | None = None,
    ) -> None:
        self._store = store
        self._embedding_provider = embedding_provider
        self._background_gate = background_gate
        self._graph_service = graph_service or GraphRAGService(store)

    @property
    def semantic_embeddings_enabled(self) -> bool:
        return self._embedding_provider is not None

    @property
    def embedding_model_id(self) -> str | None:
        model_id = getattr(self._embedding_provider, "model_id", None)
        return model_id if isinstance(model_id, str) and model_id else None

    async def index(self, record: MemoryRecord) -> EmbeddingIndexStatus:
        provider = self._embedding_provider
        if provider is None:
            return EmbeddingIndexStatus.DISABLED
        candidates = await asyncio.to_thread(
            self._store.graph_nodes_for_memory,
            namespace=record.namespace,
            memory_id=record.memory_id,
        )
        if not candidates:
            return EmbeddingIndexStatus.INDEXED
        try:
            indexed = await self._index_candidates(provider, candidates)
        except EmbeddingProviderError:
            return EmbeddingIndexStatus.UNAVAILABLE
        return (
            EmbeddingIndexStatus.INDEXED
            if indexed == len(candidates)
            else EmbeddingIndexStatus.UNAVAILABLE
        )

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
        if provider is None or model_id is None or not 1 <= limit <= MAX_NODE_EMBEDDINGS:
            return 0
        candidates = await asyncio.to_thread(
            self._store.list_missing_graph_embeddings,
            namespace=namespace,
            model_id=model_id,
            limit=limit,
        )
        indexed = 0
        for batch_index, candidates_batch in enumerate(self._candidate_batches(candidates)):
            if batch_index > 0 and inter_batch_delay_seconds > 0:
                await asyncio.sleep(inter_batch_delay_seconds)
            if self._background_gate is not None:
                await self._background_gate.wait_until_active()
            try:
                indexed += await self._index_candidates(provider, candidates_batch)
            except EmbeddingProviderError:
                if raise_provider_errors:
                    raise
                break
        return indexed

    async def retrieve(
        self, *, namespace: str, query: str, limit: int
    ) -> tuple[MemorySearchHit, ...]:
        hits, _ = await self.retrieve_context(
            namespace=namespace,
            query=query,
            limit=limit,
        )
        return hits

    async def retrieve_local(
        self, *, namespace: str, query: str, limit: int
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

    async def retrieve_graph(
        self, *, namespace: str, query: str, limit: int
    ) -> GraphSearchResult | None:
        provider = self._embedding_provider
        if provider is None:
            return None
        try:
            batch = await provider.embed([query], input_type=EmbeddingInputType.QUERY)
        except EmbeddingProviderError:
            return None
        if len(batch.vectors) != 1:
            return None
        try:
            return await self._graph_service.search(
                namespace=namespace,
                model_id=batch.model_id,
                query_vector=batch.vectors[0],
                seed_limit=5,
                limit=min(limit + 3, 12),
            )
        except MemoryQueryError:
            return None

    async def retrieve_context(
        self,
        *,
        namespace: str,
        query: str,
        limit: int,
    ) -> tuple[tuple[MemorySearchHit, ...], GraphSearchResult | None]:
        """Embed once, then run SQL RRF and bounded PPR concurrently."""
        provider = self._embedding_provider
        if provider is None:
            return await self.retrieve_local(namespace=namespace, query=query, limit=limit), None
        try:
            batch = await provider.embed([query], input_type=EmbeddingInputType.QUERY)
        except EmbeddingProviderError:
            return await self.retrieve_local(namespace=namespace, query=query, limit=limit), None
        if len(batch.vectors) != 1:
            return await self.retrieve_local(namespace=namespace, query=query, limit=limit), None
        vector = batch.vectors[0]
        try:
            hybrid_result, graph_result = await asyncio.gather(
                self._graph_service.hybrid_search(
                    namespace=namespace,
                    query=query,
                    model_id=batch.model_id,
                    query_vector=vector,
                    limit=limit,
                ),
                self._graph_service.search(
                    namespace=namespace,
                    model_id=batch.model_id,
                    query_vector=vector,
                    seed_limit=5,
                    limit=min(limit + 3, 12),
                ),
            )
        except MemoryQueryError:
            return await self.retrieve_local(namespace=namespace, query=query, limit=limit), None
        return hybrid_result.hits, graph_result

    async def _index_candidates(
        self,
        provider: EmbeddingProvider,
        candidates: tuple[GraphEmbeddingCandidate, ...],
    ) -> int:
        if not candidates:
            return 0
        batch = await provider.embed(
            tuple(self._embedding_text(candidate) for candidate in candidates),
            input_type=EmbeddingInputType.PASSAGE,
        )
        if len(batch.vectors) != len(candidates):
            raise EmbeddingProviderError(
                "embedding provider returned an inconsistent graph batch"
            )
        for candidate, vector in zip(candidates, batch.vectors, strict=True):
            if self._background_gate is not None:
                await self._background_gate.wait_until_active()
            await asyncio.to_thread(
                self._store.put_node_embedding,
                namespace=candidate.namespace,
                node_id=candidate.node_id,
                model_id=batch.model_id,
                vector=vector,
                content_sha256=candidate.content_sha256,
            )
        return len(candidates)

    @classmethod
    def _candidate_batches(
        cls,
        candidates: tuple[GraphEmbeddingCandidate, ...],
    ) -> tuple[tuple[GraphEmbeddingCandidate, ...], ...]:
        batches: list[list[GraphEmbeddingCandidate]] = []
        batch_bytes = 0
        for candidate in candidates:
            candidate_bytes = len(cls._embedding_text(candidate).encode("utf-8"))
            if (
                not batches
                or len(batches[-1]) >= MAX_BACKFILL_BATCH_SIZE
                or batch_bytes + candidate_bytes > MAX_BACKFILL_BATCH_TEXT_BYTES
            ):
                batches.append([])
                batch_bytes = 0
            batches[-1].append(candidate)
            batch_bytes += candidate_bytes
        return tuple(tuple(batch) for batch in batches)

    @staticmethod
    def _embedding_text(candidate: GraphEmbeddingCandidate) -> str:
        properties = json.dumps(
            candidate.properties,
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
            sort_keys=True,
        )
        return f"{candidate.type}: {candidate.name}\n{properties}"


class EmbeddingBackfillWorker:
    """Indexes missing graph nodes only after authenticated preflight succeeds."""

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
        if not 0 <= limit <= MAX_NODE_EMBEDDINGS:
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
                        event_type="graph_embedding_backfill_failed",
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
                    event_type="graph_embedding_backfill_recovered",
                    data={"indexed_nodes": indexed},
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
                component="graph_memory",
                data=data,
            )
        except Exception:
            return
