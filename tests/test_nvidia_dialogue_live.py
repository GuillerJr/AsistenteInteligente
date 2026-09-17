"""Opt-in NVIDIA checks using synthetic conversation; no Google tool is executed."""

from __future__ import annotations

import asyncio
import os

import pytest
from test_nvidia_dialogue import OFFER, REQUEST, TOOL, exchange

from aegis_core.brain.nvidia_client import NvidiaBrainClient
from aegis_core.config import Settings
from aegis_core.contracts import (
    AgentRole,
    Capability,
    PolicyDecision,
    RiskLevel,
    UserRequest,
)
from aegis_core.mcp.client import McpArguments
from aegis_core.orchestration.graph import build_swarm_graph
from aegis_core.providers.nvidia import NvidiaNimClient
from aegis_core.request_intent import google_request_tools
from aegis_core.secrets import MacOSKeychain
from aegis_core.tools.broker import ToolDefinition
from aegis_core.tools.defaults import build_default_tool_broker

pytestmark = pytest.mark.skipif(
    os.environ.get("AEGIS_RUN_NVIDIA_DIALOGUE_PROBE") != "1",
    reason="explicit opt-in required for NVIDIA synthetic dialogue",
)


@pytest.mark.asyncio
@pytest.mark.parametrize("followup", [False, True])
async def test_nvidia_proposes_mail_read_with_headers_and_actual_approval(followup):
    settings = Settings()
    key = await asyncio.to_thread(MacOSKeychain(
        settings.nvidia_keychain_service, settings.nvidia_keychain_account,
    ).get)
    definition = ToolDefinition(
        name=TOOL, description="Search Gmail messages", arguments_model=McpArguments,
        capability=Capability.MAIL_READ, risk=RiskLevel.HIGH,
        allowed_roles=frozenset({AgentRole.PLANNER}), requires_confirmation=True,
        parameters_schema={"type": "object", "additionalProperties": False, "properties": {
            "user_google_email": {"type": "string", "enum": ["owner@example.com"]},
            "query": {"type": "string"}, "include_headers": {"type": "boolean"},
            "page_size": {"type": "integer", "minimum": 1, "maximum": 50},
        }, "required": ["user_google_email", "query"]},
    )
    async with NvidiaNimClient(settings, lambda: key) as nim:
        graph = build_swarm_graph(
            NvidiaBrainClient(nim), inference_nvidia_only=True,
            tool_broker=build_default_tool_broker((definition,)),
            dynamic_tool_names=lambda request: frozenset({TOOL})
            if "search_gmail_messages" in google_request_tools(request.text) else frozenset(),
        )
        state = await asyncio.wait_for(graph.ainvoke({
            "request": UserRequest(text="sí" if followup else REQUEST),
            "conversation_history": exchange() if followup else (),
        }), timeout=120)
    authorization, = state["tool_authorizations"]
    assert authorization.decision is PolicyDecision.REQUIRE_CONFIRMATION
    assert authorization.tool_name == TOOL
    assert authorization.normalized_arguments["include_headers"] is True
    assert "is:unread" in authorization.normalized_arguments["query"]
    assert state.get("tool_results", ()) == ()
    assert OFFER not in state["specialist_result"].content
    print("scenario=mail_followup" if followup else "scenario=mail_request",
          "model=" + state["specialist_result"].model_id, "status=approval_required")


@pytest.mark.asyncio
async def test_nvidia_uses_recent_context_for_non_google_followup():
    settings = Settings()
    key = await asyncio.to_thread(MacOSKeychain(
        settings.nvidia_keychain_service, settings.nvidia_keychain_account,
    ).get)
    history = exchange(
        "Dame dos opciones para una web: primera React, segunda HTML y CSS sin framework.",
    )
    content = "Primera: React. Segunda: HTML y CSS sin framework."
    history = (history[0], history[1].model_copy(update={"content": content}))
    async with NvidiaNimClient(settings, lambda: key) as nim:
        graph = build_swarm_graph(NvidiaBrainClient(nim), inference_nvidia_only=True)
        state = await asyncio.wait_for(graph.ainvoke({
            "request": UserRequest(text="Elijo la segunda. Dime qué elegí en una frase."),
            "conversation_history": history,
        }), timeout=120)
    reply = state["final_result"].content.casefold()
    assert "html" in reply and "css" in reply
    assert "react" not in reply
    print("scenario=context_choice model=" + state["final_result"].model_id, "status=passed")
