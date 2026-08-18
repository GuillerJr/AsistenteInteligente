from __future__ import annotations

import json
from typing import Any, TypedDict

from langgraph.graph import END, START, StateGraph

from aegis_core.contracts import (
    AgentResult,
    AgentRole,
    InputModality,
    RiskLevel,
    RouteDecision,
    UserRequest,
)
from aegis_core.providers.base import ChatProvider


class SwarmState(TypedDict, total=False):
    request: UserRequest
    route: RouteDecision
    specialist_result: AgentResult
    final_result: AgentResult
    errors: list[str]


ROUTER_SYSTEM_PROMPT = """You are the routing controller of a policy-gated agent swarm.
Return exactly one JSON object with keys: role, risk, reason, requires_confirmation.
Allowed roles: planner, critical_reasoner, code_security, vision, omni.
Allowed risk values: low, medium, high, critical.
Route code, terminal, network and security analysis to code_security.
Route image-only work to vision; audio or video to omni.
Use critical_reasoner only for high-impact decisions. Never authorize a tool execution."""


def _fallback_route(request: UserRequest) -> RouteDecision:
    modalities = request.modalities
    lowered = request.text.casefold()
    if InputModality.AUDIO in modalities or InputModality.VIDEO in modalities:
        role = AgentRole.OMNI
    elif InputModality.IMAGE in modalities:
        role = AgentRole.VISION
    elif any(
        token in lowered
        for token in ("código", "code", "terminal", "red", "network", "puerto", "security")
    ):
        role = AgentRole.CODE_SECURITY
    else:
        role = AgentRole.PLANNER
    return RouteDecision(role=role, risk=RiskLevel.MEDIUM, reason="deterministic fallback")


def _parse_route(content: str, request: UserRequest) -> RouteDecision:
    try:
        start = content.index("{")
        end = content.rindex("}") + 1
        return RouteDecision.model_validate(json.loads(content[start:end]))
    except (ValueError, json.JSONDecodeError):
        return _fallback_route(request)


def build_swarm_graph(provider: ChatProvider) -> Any:
    async def route_node(state: SwarmState) -> dict[str, Any]:
        request = state["request"]
        result = await provider.complete(
            role=AgentRole.ROUTER,
            messages=[
                {"role": "system", "content": ROUTER_SYSTEM_PROMPT},
                {
                    "role": "user",
                    "content": json.dumps(
                        {
                            "text": request.text,
                            "modalities": sorted(item.value for item in request.modalities),
                        }
                    ),
                },
            ],
            temperature=0.0,
            extra_body={"response_format": {"type": "json_object"}},
        )
        return {"route": _parse_route(result.content, request)}

    async def specialist_node(state: SwarmState) -> dict[str, Any]:
        request = state["request"]
        route = state["route"]
        result = await provider.complete(
            role=route.role,
            messages=[
                {
                    "role": "system",
                    "content": (
                        "Analyze the request. Do not execute tools. Clearly separate observations, "
                        "assumptions and recommendations."
                    ),
                },
                {"role": "user", "content": request.text},
            ],
            temperature=0.2,
        )
        return {"specialist_result": result}

    async def synthesize_node(state: SwarmState) -> dict[str, Any]:
        specialist = state["specialist_result"]
        result = await provider.complete(
            role=AgentRole.SYNTHESIZER,
            messages=[
                {"role": "system", "content": "Produce a concise Spanish response."},
                {"role": "user", "content": specialist.content},
            ],
            temperature=0.2,
        )
        return {"final_result": result}

    builder = StateGraph(SwarmState)
    builder.add_node("route", route_node)
    builder.add_node("specialist", specialist_node)
    builder.add_node("synthesize", synthesize_node)
    builder.add_edge(START, "route")
    builder.add_edge("route", "specialist")
    builder.add_edge("specialist", "synthesize")
    builder.add_edge("synthesize", END)
    return builder.compile()
