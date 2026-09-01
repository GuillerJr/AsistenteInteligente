from __future__ import annotations

import asyncio
from dataclasses import dataclass
from uuid import UUID

from aegis_core.memory.graph_search import GraphSearchResult, PersonalizedPageRankSearch
from aegis_core.memory.sqlite import SQLiteMemoryStore


@dataclass(frozen=True, slots=True)
class SessionResetResult:
    conversation_deleted: bool
    memories_deleted: int
    cached_seed_sets_cleared: int


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
        session_id: UUID,
        model_id: str,
        query_vector: tuple[float, ...],
        limit: int = 8,
    ) -> GraphSearchResult:
        result = await asyncio.to_thread(
            self._search.search,
            namespace=namespace,
            model_id=model_id,
            query_vector=query_vector,
            result_limit=limit,
        )
        async with self._lock:
            self._seed_cache[(namespace, session_id)] = result.ranked_node_ids[:5]
        return result

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
