"""Opt-in NVIDIA checks using synthetic conversation; no Google tool is executed."""

from __future__ import annotations

import asyncio
import json
import os

import pytest
from test_nvidia_dialogue import OFFER, REQUEST, TOOL, exchange
from test_task_progress import DETAIL, checkpoint, result

from aegis_core.brain.nvidia_client import NvidiaBrainClient
from aegis_core.brain.nvidia_goal import NvidiaGoalEvaluator
from aegis_core.brain.nvidia_summary import NvidiaToolResultSummarizer
from aegis_core.config import Settings
from aegis_core.contracts import (
    AgentRole,
    Capability,
    PolicyDecision,
    RiskLevel,
    UserRequest,
)
from aegis_core.job_completion import ApprovedToolContext
from aegis_core.job_tool_runtime import ApprovedToolOutcome
from aegis_core.mcp.client import McpArguments
from aegis_core.orchestration.graph import build_swarm_graph
from aegis_core.orchestration.task_progress import advance_checkpoint
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
@pytest.mark.parametrize("prompt", [REQUEST, "Cuales son mis últimos 5 correos sin leer"])
async def test_nvidia_proposes_mail_read_with_headers_and_actual_approval(followup, prompt):
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
    detail = ToolDefinition(
        name="mcp_google_workspace__get_gmail_message_content", description="Read a known message",
        arguments_model=McpArguments, capability=Capability.MAIL_READ, risk=RiskLevel.HIGH,
        allowed_roles=frozenset({AgentRole.PLANNER}), requires_confirmation=True,
        parameters_schema={"type": "object", "additionalProperties": False, "properties": {
            "user_google_email": {"type": "string", "enum": ["owner@example.com"]},
            "message_id": {"type": "string"},
        }, "required": ["user_google_email", "message_id"]},
    )
    async with NvidiaNimClient(settings, lambda: key) as nim:
        graph = build_swarm_graph(
            NvidiaBrainClient(nim), inference_nvidia_only=True,
            tool_broker=build_default_tool_broker((definition, detail)),
            dynamic_tool_names=lambda request: frozenset({TOOL, detail.name})
            if "search_gmail_messages" in google_request_tools(request.text) else frozenset(),
        )
        state = await asyncio.wait_for(graph.ainvoke({
            "request": UserRequest(text="sí" if followup else prompt,
                                   metadata={"source_channel": "telegram"}),
            "conversation_history": exchange(prompt) if followup else (),
        }), timeout=120)
    authorization, = state["tool_authorizations"]
    assert authorization.decision is PolicyDecision.REQUIRE_CONFIRMATION
    assert authorization.tool_name == TOOL
    assert authorization.normalized_arguments["include_headers"] is True
    assert "is:unread" in authorization.normalized_arguments["query"]
    if "5" in prompt:
        assert authorization.normalized_arguments["page_size"] == 5
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


@pytest.mark.asyncio
@pytest.mark.parametrize("followup", ["Abre el segundo correo", "Lee el segundo",
                                     "Puedes mostrar el 2 por favor?"])
async def test_nvidia_uses_checkpoint_id_for_followup_with_real_broker(followup):
    settings = Settings()
    key = await asyncio.to_thread(MacOSKeychain(
        settings.nvidia_keychain_service, settings.nvidia_keychain_account,
    ).get)
    definition = ToolDefinition(
        name=DETAIL, description="Read one Gmail message", arguments_model=McpArguments,
        capability=Capability.MAIL_READ, risk=RiskLevel.HIGH,
        allowed_roles=frozenset({AgentRole.PLANNER}), requires_confirmation=True,
        parameters_schema={"type": "object", "additionalProperties": False, "properties": {
            "user_google_email": {"type": "string", "enum": ["owner@example.com"]},
            "message_id": {"type": "string"},
            "body_format": {"type": "string", "enum": ["text"]},
        }, "required": ["user_google_email", "message_id"]},
    )
    async with NvidiaNimClient(settings, lambda: key) as nim:
        graph = build_swarm_graph(
            NvidiaBrainClient(nim), inference_nvidia_only=True,
            tool_broker=build_default_tool_broker((definition,)),
            dynamic_tool_names=lambda request: frozenset({DETAIL})
            if "get_gmail_message_content" in google_request_tools(request.text) else frozenset(),
        )
        state = await asyncio.wait_for(graph.ainvoke({
            "request": UserRequest(text=followup), "task_checkpoint": checkpoint(),
            "conversation_history": (),
        }), timeout=120)
    authorization, = state["tool_authorizations"]
    assert authorization.decision is PolicyDecision.REQUIRE_CONFIRMATION
    assert authorization.tool_name == DETAIL
    assert authorization.normalized_arguments["message_id"] == "def456"
    assert state.get("tool_results", ()) == ()
    print("scenario=checkpoint_followup model=" + state["specialist_result"].model_id,
          "status=approval_required tool_executions=0")


@pytest.mark.asyncio
@pytest.mark.parametrize("matches", [True, False])
async def test_nvidia_goal_judge_checks_observed_arguments_without_executing_tools(matches):
    settings = Settings()
    key = await asyncio.to_thread(MacOSKeychain(
        settings.nvidia_keychain_service, settings.nvidia_keychain_account,
    ).get)
    first = advance_checkpoint(None,
                               request="Busca correos y prepara un borrador de correo "
                               "a ana@example.com con asunto Reunión y cuerpo Hola Ana",
                               response="Dos correos.", sequence=2, tool_results=(result(),))
    draft = result(name="mcp_google_workspace__draft_gmail_message",
                   payload={"draft_id": "synthetic-draft"})
    if not matches:
        arguments = json.loads(draft.metadata["operation_arguments_json"])
        arguments["body"] = "Contenido diferente al solicitado"
        draft = draft.model_copy(update={"metadata": {
            **draft.metadata, "operation_arguments_json": json.dumps(arguments),
        }})
    state = advance_checkpoint(first, request="hazlo", response="Borrador preparado.", sequence=4,
                               tool_results=(draft,))
    async with NvidiaNimClient(settings, lambda: key) as nim:
        verdict = await NvidiaGoalEvaluator(NvidiaBrainClient(nim)).assess(state)
    assert verdict is not None and verdict.fulfilled is matches
    print("scenario=goal_evidence status=arguments_checked tool_executions=0")


@pytest.mark.asyncio
async def test_nvidia_mail_summary_preserves_the_numbered_order_of_real_ids():
    settings = Settings()
    key = await asyncio.to_thread(MacOSKeychain(
        settings.nvidia_keychain_service, settings.nvidia_keychain_account,
    ).get)
    context = ApprovedToolContext(UserRequest(text=REQUEST).request_id, REQUEST, None)
    async with NvidiaNimClient(settings, lambda: key) as nim:
        text = await NvidiaToolResultSummarizer(NvidiaBrainClient(nim))(
            context, ApprovedToolOutcome(result(), "Synthetic records."),
        )
    assert text is not None
    assert text.index("Reunión") < text.index("Factura")
    assert "1." in text and "2." in text
    print("scenario=summary_order status=passed tool_executions=0")
