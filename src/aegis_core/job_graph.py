from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Protocol

from aegis_core.contracts import (
    AgentResult,
    PolicyDecision,
    ToolAuthorization,
    ToolCall,
    ToolExecutionResult,
    UserRequest,
)
from aegis_core.memory.contracts import ConversationTurn
from aegis_core.orchestration.direct_actions import is_bounded_public_https_url
from aegis_core.providers.base import require_complete_response
from aegis_core.tools.verification import result_is_verified


class SwarmGraph(Protocol):
    async def ainvoke(self, input: dict[str, Any]) -> dict[str, Any]: ...


@dataclass(frozen=True, slots=True)
class GraphInvocation:
    final_result: AgentResult | None
    pending: tuple[tuple[ToolCall, ToolAuthorization], ...]
    model_id: str | None
    tool_name: str | None
    action_verified: bool
    capability_gap: bool
    capability_research: ToolExecutionResult | None
    public_sources: tuple[str, ...] | None


class JobGraphInvoker:
    """Strict adapter between LangGraph's state dictionary and job contracts."""

    def __init__(self, graph: SwarmGraph) -> None:
        self._graph = graph

    async def invoke(
        self,
        request: UserRequest,
        conversation_history: tuple[ConversationTurn, ...],
        *,
        stream_callback: Callable[[str], None],
    ) -> GraphInvocation:
        state = await self._graph.ainvoke(
            {
                "request": request,
                "conversation_history": conversation_history,
                "stream_callback": stream_callback,
            }
        )
        if not isinstance(state, dict):
            raise ValueError("graph did not return valid state")

        authorizations = state.get("tool_authorizations", ())
        if not isinstance(authorizations, tuple):
            raise ValueError("graph did not return valid tool state")
        specialist = state.get("specialist_result")
        if authorizations and not isinstance(specialist, AgentResult):
            raise ValueError("graph did not return valid tool state")
        calls = (
            {call.call_id: call for call in specialist.tool_calls}
            if isinstance(specialist, AgentResult)
            else {}
        )
        tool_name = next(iter(calls.values())).tool_name if len(calls) == 1 else None

        raw_tool_results = state.get("tool_results", ())
        if not isinstance(raw_tool_results, tuple) or not all(
            isinstance(result, ToolExecutionResult) for result in raw_tool_results
        ):
            raise ValueError("graph did not return valid tool results")
        capability_gap = state.get("capability_gap", False)
        if not isinstance(capability_gap, bool):
            raise ValueError("graph did not return valid capability state")
        capability_research = (
            next(
                (
                    result
                    for result in raw_tool_results
                    if result.tool_name == "web_research" and result.success
                ),
                None,
            )
            if capability_gap
            else None
        )
        action_verified = bool(
            tool_name is not None
            and len(raw_tool_results) == 1
            and raw_tool_results[0].tool_name == tool_name
            and raw_tool_results[0].call_id in calls
            and result_is_verified(raw_tool_results[0])
        )

        pending: list[tuple[ToolCall, ToolAuthorization]] = []
        for authorization in authorizations:
            if not isinstance(authorization, ToolAuthorization):
                raise ValueError("graph returned an invalid authorization")
            if authorization.decision is not PolicyDecision.REQUIRE_CONFIRMATION:
                continue
            call = calls.get(authorization.call_id)
            if (
                call is None
                or call.tool_name != authorization.tool_name
                or call.digest() != authorization.call_digest
            ):
                raise ValueError("pending authorization does not match tool call")
            pending.append((call, authorization))

        final_result = state.get("final_result")
        if final_result is not None and not isinstance(final_result, AgentResult):
            raise ValueError("graph returned an invalid final result")
        if final_result is not None:
            require_complete_response(final_result)
        if not pending and final_result is None:
            raise ValueError("graph did not return a final agent result")
        model_id = final_result.model_id if final_result is not None else None
        if model_id is None and isinstance(specialist, AgentResult):
            model_id = specialist.model_id

        return GraphInvocation(
            final_result=final_result,
            pending=tuple(pending),
            model_id=model_id,
            tool_name=tool_name,
            action_verified=action_verified,
            capability_gap=capability_gap,
            capability_research=capability_research,
            public_sources=_public_sources_from_results(raw_tool_results),
        )


def _public_sources_from_results(
    results: tuple[ToolExecutionResult, ...],
) -> tuple[str, ...] | None:
    research_results = tuple(result for result in results if result.tool_name == "web_research")
    if not research_results:
        return None
    if len(research_results) != 1:
        return ()
    research = research_results[0]
    if (
        not research.success
        or research.metadata.get("source") != "public_https"
        or research.metadata.get("verified") is not True
    ):
        return ()
    try:
        payload = json.loads(research.output)
    except (json.JSONDecodeError, TypeError):
        return ()
    if (
        not isinstance(payload, dict)
        or set(payload) != {"query", "results"}
        or not isinstance(payload.get("query"), str)
    ):
        return ()
    raw_sources = payload.get("results")
    if (
        not isinstance(raw_sources, list)
        or len(raw_sources) > 5
        or type(research.metadata.get("results")) is not int
        or research.metadata["results"] != len(raw_sources)
    ):
        return ()
    urls: list[str] = []
    for raw_source in raw_sources:
        if (
            not isinstance(raw_source, dict)
            or set(raw_source) != {"url", "title", "content"}
            or not isinstance(raw_source.get("title"), str)
            or not isinstance(raw_source.get("content"), str)
        ):
            return ()
        url = raw_source.get("url")
        if not isinstance(url, str) or not is_bounded_public_https_url(url):
            return ()
        if url not in urls and len(urls) < 3:
            urls.append(url)
    return tuple(urls)
