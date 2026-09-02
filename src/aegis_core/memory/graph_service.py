from __future__ import annotations

import asyncio
import json
import math
import time
from collections.abc import Callable
from dataclasses import dataclass
from uuid import UUID, uuid4

from aegis_core.memory.contracts import MemoryRecord, MemorySearchHit
from aegis_core.memory.graph_search import GraphSearchResult, PersonalizedPageRankSearch
from aegis_core.memory.sqlite import DECAY_EVICTION_THRESHOLD, SQLiteMemoryStore
from aegis_core.tools.audit import AuditSink, NullAuditSink


@dataclass(frozen=True, slots=True)
class SessionResetResult:
    conversation_deleted: bool
    memories_deleted: int
    cached_seed_sets_cleared: int


@dataclass(frozen=True, slots=True)
class HybridRRFResult:
    hits: tuple[MemorySearchHit, ...]
    query_ms: float
    payload_bytes: int


@dataclass(frozen=True, slots=True)
class MemoryDecayResult:
    evicted: int
    skipped_for_thermal_state: bool


class GraphRAGService:
    """Session-scoped PPR facade with an explicit reset boundary."""

    def __init__(self, store: SQLiteMemoryStore) -> None:
        self._store = store
        self._search = PersonalizedPageRankSearch(store)
        self._seed_cache: dict[tuple[str, UUID], tuple[UUID, ...]] = {}
        self._lock = asyncio.Lock()

    async def search(
        self,
        *,
        namespace: str,
        session_id: UUID | None = None,
        model_id: str,
        query_vector: tuple[float, ...],
        seed_limit: int = 5,
        limit: int = 8,
    ) -> GraphSearchResult:
        result = await asyncio.to_thread(
            self._search.search,
            namespace=namespace,
            model_id=model_id,
            query_vector=query_vector,
            seed_limit=seed_limit,
            result_limit=limit,
        )
        if session_id is not None:
            async with self._lock:
                self._seed_cache[(namespace, session_id)] = result.ranked_node_ids[:5]
        return result

    async def hybrid_search(
        self,
        *,
        namespace: str,
        query: str,
        model_id: str,
        query_vector: tuple[float, ...],
        limit: int = 8,
    ) -> HybridRRFResult:
        started = time.perf_counter_ns()
        hits = await asyncio.to_thread(
            self._store.hybrid_rrf_search,
            namespace=namespace,
            query=query,
            model_id=model_id,
            query_vector=query_vector,
            limit=limit,
        )
        payload_bytes = len(
            json.dumps(
                [hit.model_dump(mode="json") for hit in hits],
                ensure_ascii=False,
                separators=(",", ":"),
            ).encode("utf-8")
        )
        return HybridRRFResult(
            hits=hits,
            query_ms=(time.perf_counter_ns() - started) / 1_000_000.0,
            payload_bytes=payload_bytes,
        )

    async def reinforce(
        self,
        *,
        namespace: str,
        memory_id: UUID,
        delta_boost: float = 0.15,
    ) -> MemoryRecord:
        return await asyncio.to_thread(
            self._store.reinforce,
            namespace=namespace,
            memory_id=memory_id,
            delta_boost=delta_boost,
        )

    async def evict_decayed(
        self,
        *,
        namespace: str,
        threshold: float = DECAY_EVICTION_THRESHOLD,
        limit: int = 64,
    ) -> int:
        return await asyncio.to_thread(
            self._store.evict_decayed,
            namespace=namespace,
            threshold=threshold,
            limit=limit,
        )

    async def reset_session(
        self,
        *,
        namespace: str,
        session_id: UUID,
    ) -> SessionResetResult:
        conversation_deleted, memories_deleted = await asyncio.to_thread(
            self._store.purge_session,
            namespace=namespace,
            session_id=session_id,
        )
        async with self._lock:
            removed = int(self._seed_cache.pop((namespace, session_id), None) is not None)
        return SessionResetResult(
            conversation_deleted=conversation_deleted,
            memories_deleted=memories_deleted,
            cached_seed_sets_cleared=removed,
        )

    async def cached_seeds(self, *, namespace: str, session_id: UUID) -> tuple[UUID, ...]:
        async with self._lock:
            return self._seed_cache.get((namespace, session_id), ())


class MemoryDecayWorker:
    """Low-priority secure eviction, enabled only by a nominal native thermal report."""

    def __init__(
        self,
        service: GraphRAGService,
        *,
        namespace: str,
        runtime_probe: Callable[[], object | None],
        audit_sink: AuditSink | None = None,
        interval_seconds: float = 3_600.0,
        initial_delay_seconds: float = 30.0,
        batch_limit: int = 64,
    ) -> None:
        if (
            not math.isfinite(interval_seconds)
            or not math.isfinite(initial_delay_seconds)
            or interval_seconds < 60.0
            or initial_delay_seconds < 0.0
            or not 1 <= batch_limit <= 256
        ):
            raise ValueError("memory decay worker policy is invalid")
        self._service = service
        self._namespace = namespace
        self._runtime_probe = runtime_probe
        self._audit = audit_sink or NullAuditSink()
        self._interval_seconds = interval_seconds
        self._initial_delay_seconds = initial_delay_seconds
        self._batch_limit = batch_limit

    async def run_once(self) -> MemoryDecayResult:
        snapshot = self._runtime_probe()
        thermal_state = getattr(snapshot, "thermal_state", "unknown")
        low_power_mode = getattr(snapshot, "low_power_mode", True)
        if thermal_state != "nominal" or low_power_mode is not False:
            return MemoryDecayResult(evicted=0, skipped_for_thermal_state=True)
        evicted = await self._service.evict_decayed(
            namespace=self._namespace,
            limit=self._batch_limit,
        )
        if evicted:
            self._record(
                "memory_decay_eviction",
                {"evicted": evicted, "thermal_state": "nominal"},
            )
        return MemoryDecayResult(evicted=evicted, skipped_for_thermal_state=False)

    async def run(self) -> None:
        if self._initial_delay_seconds:
            await asyncio.sleep(self._initial_delay_seconds)
        while True:
            try:
                await self.run_once()
            except asyncio.CancelledError:
                raise
            except Exception as error:
                self._record(
                    "memory_decay_cycle_failed",
                    {"error_type": type(error).__name__},
                )
            await asyncio.sleep(self._interval_seconds)

    def _record(self, event_type: str, data: dict[str, str | int | bool | None]) -> None:
        try:
            self._audit.record_system_event(
                uuid4(),
                event_type=event_type,
                component="graph_memory",
                data=data,
            )
        except Exception:
            return
