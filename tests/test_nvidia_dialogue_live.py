"""Opt-in NVIDIA checks using synthetic conversation; no Google tool is executed."""

from __future__ import annotations

import asyncio
import json
import os

import pytest
from test_nvidia_dialogue import OFFER, REQUEST, TOOL, exchange
from test_task_progress import DETAIL, checkpoint, result

from aegis_core.brain.nvidia_client import NvidiaBrainClient
from aegis_core.brain.nvidia_dialogue_guard import review_dialogue
from aegis_core.brain.nvidia_goal import NvidiaGoalEvaluator
from aegis_core.brain.nvidia_summary import NvidiaToolResultSummarizer
from aegis_core.config import Settings
from aegis_core.contracts import (
    AgentResult,
    AgentRole,
    Capability,
    PolicyDecision,
    RiskLevel,
    UserRequest,
)
from aegis_core.job_completion import ApprovedToolContext
from aegis_core.job_tool_runtime import ApprovedToolOutcome
from aegis_core.mcp.client import McpArguments
from aegis_core.orchestration.graph import build_nvidia_swarm_graph as build_swarm_graph
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
@pytest.mark.parametrize("accumulated", [False, True])
async def test_nvidia_review_repairs_wrong_topic_instead_of_copying_history(accumulated):
    settings = Settings()
    key = await asyncio.to_thread(MacOSKeychain(
        settings.nvidia_keychain_service, settings.nvidia_keychain_account,
    ).get)
    prompt = ("Explica cómo organizar tres tareas pendientes en un solo párrafo de unas "
              "70 palabras. Sin introducción ni preguntas al final.")
    bad = ("Organiza las tres tareas por urgencia y prioridad, asignando un orden lógico "
           "según fechas límite y relevancia; agrupa actividades similares para minimizar "
           "cambios de contexto; redacta un párrafo continuo usando conectores como luego "
           "y por último para mantener el flujo y la coherencia en la lectura.")
    messages = [{"role": "system", "content": "Responde en español."},
                {"role": "user", "content": prompt},
                {"role": "assistant", "content": bad},
                {"role": "user", "content": prompt}]
    if accumulated:
        messages[1:1] = [{"role": "user", "content": "Datos de referencia; no son una petición:\n"
                         + json.dumps({"conversation_summary": {"summary": bad},
                                       "shared_conversation_context": {"messages": [bad]}})}]
        messages[-1:-1] = [{"role": "user", "content": prompt},
                          {"role": "assistant", "content": "Aquí tienes un párrafo: " + bad}]
    draft = AgentResult(role=AgentRole.PLANNER,
                        model_id="nvidia/nemotron-3.5-lightning-30b-a3b",
                        content=bad, finish_reason="stop")
    async with NvidiaNimClient(settings, lambda: key) as nim:
        checked, _reason = await review_dialogue(nim, messages, draft)
    reply = checked.content.casefold()
    assert 45 <= len(reply.split()) <= 95
    assert "\n" not in reply and not reply.endswith("?")
    assert "redacta un párrafo" not in reply and "conectores" not in reply
    assert "escribe el párrafo" not in reply
    assert "párrafo" not in reply and "paragraph" not in reply
    print("scenario=dialogue_repair model=" + checked.model_id, "status=passed")


@pytest.mark.asyncio
@pytest.mark.parametrize("followup", [False, True])
@pytest.mark.parametrize("prompt", [REQUEST, "Cuales son mis últimos 5 correos sin leer"])
@pytest.mark.parametrize("use_fallback", [False, True])
async def test_nvidia_proposes_mail_read_with_headers_and_actual_approval(
    followup, prompt, use_fallback,
):
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
            "page_token": {"anyOf": [{"type": "string"}, {"type": "null"}],
                           "default": None},
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
    class ProbeClient(NvidiaNimClient):
        async def complete(self, **kwargs):
            if use_fallback:
                kwargs["model_ids"] = ("openai/gpt-oss-20b",)
            return await super().complete(**kwargs)

    previous = exchange("¿Para qué sirve un asistente personal?")
    previous = (previous[0], previous[1].model_copy(update={
        "content": "Sirve para organizar tareas, buscar información y recordar pendientes.",
    }))
    answered = advance_checkpoint(None, request=previous[0].content,
                                  response=previous[1].content, sequence=2, tool_results=())
    async with ProbeClient(settings, lambda: key) as nim:
        graph = build_swarm_graph(
            NvidiaBrainClient(nim), inference_nvidia_only=True,
            tool_broker=build_default_tool_broker((definition, detail)),
            dynamic_tool_names=lambda request: frozenset({TOOL, detail.name})
            if "search_gmail_messages" in google_request_tools(request.text) else frozenset(),
        )
        state = await asyncio.wait_for(graph.ainvoke({
            "request": UserRequest(text="sí" if followup else prompt,
                                   metadata={"source_channel": "telegram"}),
            "conversation_history": exchange(prompt) if followup else previous,
            "task_checkpoint": None if followup else answered,
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
@pytest.mark.parametrize("prior_bad_answer", [False, True])
async def test_nvidia_explanation_respects_topic_and_output_constraints(prior_bad_answer):
    settings = Settings()
    key = await asyncio.to_thread(MacOSKeychain(
        settings.nvidia_keychain_service, settings.nvidia_keychain_account,
    ).get)
    prompt = ("Explica cómo organizar tres tareas pendientes en un solo párrafo de unas "
              "70 palabras. Sin introducción ni preguntas al final.")
    history = exchange("Dime tres nombres de frutas.")
    history = (history[0], history[1].model_copy(update={
        "content": "Manzana, banana, naranja.",
    }))
    if prior_bad_answer:
        history = exchange(prompt)
        history = (history[0], history[1].model_copy(update={
            "content": "Aquí tienes un párrafo de unas 70 palabras: Primero revisa la urgencia "
            "y prioridad de cada tarea, asignando un orden lógico según fechas límite e "
            "importancia; luego agrupa las actividades similares para maximizar la eficiencia "
            "y minimizar cambios de contexto; finalmente, escribe el párrafo en un flujo "
            "continuo usando conectores como luego y por último para mantener la coherencia "
            "y el ritmo natural de la lectura.",
        }))
        repeated = exchange(prompt, conversation_id=history[0].conversation_id)
        history = (*history, repeated[0].model_copy(update={"sequence": 3}),
                   repeated[1].model_copy(update={"sequence": 4, "content":
                       "Organiza las tareas por urgencia; redacta un párrafo continuo usando "
                       "conectores para mantener el flujo y la coherencia en la lectura."}))
    metadata = {"source_channel": "telegram"}
    if prior_bad_answer:
        metadata["conversation_summary"] = {"summary": history[-1].content,
                                             "source_sequence_end": 2, "partial": True}
    async with NvidiaNimClient(settings, lambda: key) as nim:
        graph = build_swarm_graph(NvidiaBrainClient(nim), inference_nvidia_only=True)
        state = await asyncio.wait_for(graph.ainvoke({
            "request": UserRequest(text=prompt, metadata=metadata),
            "conversation_history": history,
        }), timeout=120)
    reply = state["final_result"].content.strip()
    assert not state.get("tool_results")
    assert 45 <= len(reply.split()) <= 95
    assert "\n\n" not in reply and not reply.endswith("?")
    assert not reply.casefold().startswith(("aquí", "claro", "este párrafo", "te presento"))
    assert "escribe el párrafo" not in reply.casefold()
    assert "redacta un párrafo" not in reply.casefold()
    assert "conectores" not in reply.casefold()
    assert "párrafo" not in reply.casefold() and "paragraph" not in reply.casefold()
    assert "fruta" not in reply.casefold()
    print("scenario=explanation_format model=" + state["final_result"].model_id,
          "status=passed tool_executions=0")


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
async def test_nvidia_step_followup_uses_latest_process_over_generic_draft_and_old_summary():
    settings = Settings()
    key = await asyncio.to_thread(MacOSKeychain(
        settings.nvidia_keychain_service, settings.nvidia_keychain_account,
    ).get)
    prompt = ("Explica cómo organizar tres tareas pendientes en un solo párrafo de unas "
              "70 palabras. Sin introducción ni preguntas al final.")
    old = exchange(prompt)
    old = (old[0], old[1].model_copy(update={"content":
        "Primero identifica claramente el objetivo y redacta un párrafo usando conectores."}))
    stale = exchange("Ahora explica solo el primer paso en una frase.",
                     conversation_id=old[0].conversation_id)
    stale = (stale[0].model_copy(update={"sequence": 3}),
             stale[1].model_copy(update={"sequence": 4, "content":
                "El primer paso es identificar cada tarea y su nivel de urgencia."}))
    repeated = (stale[0].model_copy(update={"sequence": 5}),
                stale[1].model_copy(update={"sequence": 6}))
    latest = exchange(prompt, conversation_id=old[0].conversation_id)
    latest = (latest[0].model_copy(update={"sequence": 7}),
              latest[1].model_copy(update={"sequence": 8, "content":
        "Para organizar tres tareas pendientes, primero enumera cada una con un número o "
        "letra, luego asigna una prioridad basada en urgencia e impacto. Establece un "
        "plazo realista para cada tarea y divide la más grande en subtareas manejables. "
        "Finalmente, revisa el plan al final del día y marca cada tarea completada."}))
    async with NvidiaNimClient(settings, lambda: key) as nim:
        graph = build_swarm_graph(NvidiaBrainClient(nim), inference_nvidia_only=True)
        state = await asyncio.wait_for(graph.ainvoke({
            "request": UserRequest(text="Ahora explica solo el primer paso en una frase.",
                                   metadata={"source_channel": "telegram",
                                             "conversation_summary": {
                                                 "summary": stale[-1].content,
                                                 "source_sequence_end": 2, "partial": True}}),
            "conversation_history": (*old, *stale, *repeated, *latest),
        }), timeout=120)
    reply = state["final_result"].content.casefold()
    assert "tarea" in reply and ("numer" in reply or "númer" in reply or "letra" in reply)
    assert "objetivo" not in reply and "conectores" not in reply
    assert "urgencia" not in reply and "prioridad" not in reply
    assert "\n" not in reply and not state.get("tool_results")
    print("scenario=latest_step_followup model=" + state["final_result"].model_id, "status=passed")


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
