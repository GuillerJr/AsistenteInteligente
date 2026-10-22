from __future__ import annotations

import asyncio
from typing import Any

from aegis_core.async_tasks import gather_owned
from aegis_core.contracts import (
    TEMPORARY_CONVERSATION_METADATA,
)
from aegis_core.memory.contracts import MemorySearchHit
from aegis_core.memory.errors import DecryptionAuthError, MemoryStoreError
from aegis_core.orchestration.private_context import (
    _request_can_access_private_context,
)
from aegis_core.orchestration.state import (
    SwarmState,
)


def create_recall_memory_node(
    *,
    capability_learning,
    memory_limit,
    memory_namespace,
    memory_retriever,
    owner_profile,
    owner_profile_limit,
    social_memory,
):
    async def recall_memory_node(state: SwarmState) -> dict[str, Any]:
        request = state["request"]
        capability_gap = state.get("capability_gap", False)
        if (
            not _request_can_access_private_context(request)
            or request.metadata.get(TEMPORARY_CONVERSATION_METADATA) is True
        ):
            return {
                "memory_hits": (),
                "graph_memory_context": "",
                "social_memory_hits": (),
            }

        memory_query = request.text
        if len(request.text.split()) <= 4:
            checkpoint = state.get("task_checkpoint")
            if checkpoint is not None and checkpoint.status != "cancelled":
                memory_query = checkpoint.active_intent + " " + request.text

        async def retrieve_memory() -> tuple[tuple[MemorySearchHit, ...], str, bool]:
            if memory_retriever is not None:
                try:
                    lexical_task = memory_retriever.retrieve_local(
                        namespace=memory_namespace,
                        query=memory_query,
                        limit=memory_limit,
                    )
                    graph_method = getattr(memory_retriever, "retrieve_graph", None)
                    graph_task = (
                        graph_method(
                            namespace=memory_namespace,
                            query=memory_query,
                            limit=memory_limit,
                        )
                        if graph_method is not None
                        else asyncio.sleep(0, result=None)
                    )
                    lexical, graph_result = await gather_owned(
                        lexical_task,
                        graph_task,
                    )
                    return (
                        lexical,
                        graph_result.markdown if graph_result is not None else "",
                        False,
                    )
                except DecryptionAuthError:
                    raise
                except MemoryStoreError:
                    return (), "", True
            return (), "", False

        (
            (retrieved, graph_context, retrieval_failed),
            profile_hits,
            social_hits,
            capability_knowledge,
        ) = await gather_owned(
            retrieve_memory(),
            (
                owner_profile.recall(limit=owner_profile_limit)
                if owner_profile is not None
                else asyncio.sleep(0, result=())
            ),
            (social_memory.recall() if social_memory is not None else asyncio.sleep(0, result=())),
            (
                capability_learning.recall(request)
                if capability_gap and capability_learning is not None
                else asyncio.sleep(0, result=None)
            ),
        )
        combined: list[MemorySearchHit] = []
        seen: set[object] = set()
        for hit in (*profile_hits, *retrieved):
            if hit.memory_id in seen:
                continue
            seen.add(hit.memory_id)
            combined.append(hit)
        update: dict[str, Any] = {
            "memory_hits": tuple(combined),
            "graph_memory_context": graph_context,
            "social_memory_hits": tuple(social_hits),
        }
        if capability_knowledge is not None:
            update["capability_knowledge"] = capability_knowledge
        if retrieval_failed:
            update["errors"] = [*state.get("errors", []), "memory_retrieval_failed"]
        return update

    return recall_memory_node
