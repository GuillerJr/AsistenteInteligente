"""Regression scenarios for task continuity; all Google data is synthetic."""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from historical.orchestration.graph import build_swarm_graph
from test_nvidia_dialogue import OFFER, REQUEST, TOOL, Provider, answer, exchange

from aegis_core.brain.nvidia_client import NvidiaBrainClient
from aegis_core.brain.nvidia_goal import NvidiaGoalEvaluator
from aegis_core.brain.nvidia_summary import NvidiaToolResultSummarizer
from aegis_core.contracts import (
    AgentRole,
    Capability,
    InputModality,
    PolicyDecision,
    RiskLevel,
    ToolCall,
    ToolExecutionResult,
    UserRequest,
)
from aegis_core.ipc.protocol import IpcAuthenticator
from aegis_core.job_completion import ApprovedToolContext
from aegis_core.job_execution import JobExecutionPipeline
from aegis_core.job_tool_runtime import ApprovedToolOutcome
from aegis_core.jobs import JobStatus, SwarmJobManager
from aegis_core.mcp.client import McpArguments
from aegis_core.memory.conversations import ConversationCoordinator, ConversationIpcService
from aegis_core.memory.sqlite import SQLiteMemoryStore
from aegis_core.orchestration.task_progress import (
    advance_checkpoint,
    reject_duplicate_calls,
    reject_repeated_completed_step,
    requested_task_steps,
    resolve_task_reference,
    task_reference_context,
    verified_task_result,
)
from aegis_core.providers.base import IncompleteModelResponseError
from aegis_core.request_intent import google_request_tools
from aegis_core.tools.broker import PolicyContext, ToolDefinition
from aegis_core.tools.confirmations import OneTimeConfirmationStore
from aegis_core.tools.defaults import build_default_tool_broker, default_policy_context

DETAIL = "mcp_google_workspace__get_gmail_message_content"


def test_draft_payload_cannot_add_a_calendar_domain_to_explicit_workflow():
    request = "Busca correos y prepara un borrador de correo a ana@example.com " \
        "con asunto Reunión y cuerpo Hola Ana"
    steps = requested_task_steps(request)
    assert len(steps) == 2
    assert steps[1].tool_suffixes == ("draft_gmail_message",)
    assert "Reunión" in steps[1].instruction


def result(*, name=TOOL, payload=None, metadata=None, success=True, call_id="search-1"):
    return ToolExecutionResult(
        call_id=call_id, tool_name=name, success=success,
        output=json.dumps(payload if payload is not None else {"messages": [
            {"id": "abc123", "subject": "Reunión"},
            {"id": "def456", "subject": "Factura"},
        ]}), metadata=metadata if metadata is not None else {
            "verified": True, "integration": "google-workspace",
            "server_id": "synthetic-google", "source": "mcp_2025_11_25_stdio",
            "operation_arguments_json": json.dumps(
                {"subject": "Reunión", "body": "Hola Ana", "to": "ana@example.com"}
                if name.endswith("__draft_gmail_message") else {"query": "is:unread"}
                if name == TOOL else {"message_id": "def456"} if name == DETAIL else {},
            ),
        },
    )


def checkpoint(**kwargs):
    return advance_checkpoint(None, request=REQUEST, response="Hay dos correos.",
                              sequence=2, tool_results=(result(**kwargs),))


@pytest.mark.parametrize("wrapped", [False, True])
def test_deep_unparseable_result_keeps_observation_without_fabricating_read_references(wrapped):
    payload = "[" * 1000 + "0" + "]" * 1000
    output = json.dumps({"content": [{"type": "text", "text": payload}]}) if wrapped else payload
    malformed = result().model_copy(update={"output": output})
    observation = verified_task_result(malformed)
    assert observation is not None and observation.references == ()
    assert observation.excerpt and observation.call_id == malformed.call_id
    progress = advance_checkpoint(None, request=REQUEST, response="Resultado observado.",
                                  sequence=2, tool_results=(malformed,))
    resolution = resolve_task_reference("Lee el segundo correo", progress)
    assert resolution.clarification and resolution.identifier is None
    assert resolution.tool_suffix is None


@pytest.mark.asyncio
async def test_deep_json_cannot_leave_observed_operation_stuck_in_executing_after_restart(tmp_path):
    store = SQLiteMemoryStore(tmp_path / "memory.db", encryption_secret=b"x" * 32)
    store.initialize()
    coordinator = ConversationCoordinator(store, namespace="owner")
    conversation = await coordinator.create("Synthetic")
    pipeline = JobExecutionPipeline(object(), execution_timeout_seconds=10,
                                    conversations=coordinator)
    args = {"request_id": uuid4(), "request_text": REQUEST,
            "tool_name": TOOL, "call_digest": "a" * 64}
    await pipeline.record_recovery(conversation.conversation_id, **args, status="executing")
    payload = "[" * 1000 + "0" + "]" * 1000
    malformed = result(payload={"content": [{"type": "text", "text": payload}]})
    await pipeline.record_recovery(conversation.conversation_id, **args, status="observed",
                                   result=malformed)
    restarted = ConversationCoordinator(store, namespace="owner")
    recovery = await restarted.recovery(conversation.conversation_id)
    assert recovery.status == "observed" and recovery.observation is not None
    assert recovery.observation.references == ()
    assert await restarted.history(conversation.conversation_id) == ()


def definition(name, capability=Capability.MAIL_READ):
    fields = ({"message_id": {"type": "string"}} if name == DETAIL
              else {"query": {"type": "string"}})
    return ToolDefinition(
        name=name, description="Synthetic Gmail", arguments_model=McpArguments,
        capability=capability, risk=RiskLevel.HIGH,
        allowed_roles=frozenset({AgentRole.PLANNER}), requires_confirmation=True,
        provider_label="MCP: Synthetic Google", external_destination="stdio:synthetic-google",
        parameters_schema={"type": "object", "additionalProperties": False,
                           "properties": fields, "required": list(fields)},
    )


def synthetic_graph(provider, *, broker=None, policy=None):
    def names(request):
        remote = google_request_tools(request.text)
        return frozenset(name for name in (TOOL, DETAIL)
                         if name.rsplit("__", 1)[-1] in remote)

    return build_swarm_graph(
        NvidiaBrainClient(provider), inference_nvidia_only=True,
        tool_broker=broker or build_default_tool_broker((definition(TOOL), definition(DETAIL))),
        policy_context=policy, dynamic_tool_names=names,
    )


@pytest.mark.parametrize("prompt", ["Abre el segundo", "Lee el segundo correo",
                                    "Puedes mostrar el 2 por favor?", "abre la segunda"])
def test_ordinal_reference_uses_verified_id_not_assistant_prose(prompt):
    resolution = resolve_task_reference(prompt, checkpoint())
    assert resolution.identifier == "def456"
    assert resolution.tool_suffix == "get_gmail_message_content"
    assert "def456" in resolution.scope_text


@pytest.mark.parametrize("prompt", ["No abras el segundo", 'Explica "abre el segundo"',
                                    "Lee 2 páginas", "Abre el segundo correo y bórralo",
                                    "Corrige: no era el segundo", "Cancela lo anterior"])
def test_negations_examples_and_compound_commands_never_become_ordinal_actions(prompt):
    assert resolve_task_reference(prompt, checkpoint()).scope_text is None


@pytest.mark.parametrize("overrides", [
    {"success": False}, {"metadata": {"verified": False, "integration": "google-workspace"}},
    {"metadata": {"verified": "true", "integration": "google-workspace"}},
    {"metadata": {"verified": True, "integration": "github"}}, {"metadata": {}},
])
def test_failed_or_unverified_evidence_cannot_supply_reference_ids(overrides):
    assert verified_task_result(result(**overrides)) is None
    assert resolve_task_reference("Abre el segundo", checkpoint(**overrides)).identifier is None


def test_out_of_range_missing_id_stale_list_and_multiple_domains_ask_one_clarification():
    assert resolve_task_reference("Abre el segundo correo", None).clarification
    assert resolve_task_reference("Abre el tercero", checkpoint()).clarification
    missing = checkpoint(payload={"messages": [{"id": "abc123"}, {"subject": "No ID"}]})
    assert resolve_task_reference("Lee el segundo correo", missing).clarification
    state = checkpoint()
    old = state.model_copy(update={"results": (state.results[0].model_copy(update={
        "observed_at": datetime.now(UTC) - timedelta(minutes=31),
    }),)})
    assert resolve_task_reference("Lee el segundo correo", old).clarification
    file_result = result(name="mcp_google_workspace__search_drive_files",
                         payload={"files": [{"id": "file-1"}, {"id": "file-2"}]})
    mixed = advance_checkpoint(state, request="Busca archivos en Drive", response="Dos archivos.",
                               sequence=4, tool_results=(file_result,))
    assert resolve_task_reference("Abre el segundo", mixed).clarification
    assert resolve_task_reference("Abre el segundo correo", mixed).identifier == "def456"
    assert resolve_task_reference("Abre el segundo archivo", mixed).identifier == "file-2"


def test_new_empty_list_supersedes_old_list_and_cancellation_clears_pending_work():
    empty = advance_checkpoint(checkpoint(), request=REQUEST, response="No hay coincidencias.",
                               sequence=4, tool_results=(result(payload={"messages": []}),))
    assert resolve_task_reference("Abre el segundo correo", empty).clarification
    cancelled = advance_checkpoint(checkpoint(), request="Cancela lo anterior",
                                   response="Cancelado.", sequence=4)
    assert cancelled.status == "cancelled" and not cancelled.results
    assert resolve_task_reference("hazlo", cancelled).scope_text is None


def test_calendar_reference_requires_both_event_id_and_calendar_provenance():
    missing = checkpoint(name="mcp_google_workspace__get_events",
                         payload={"events": [{"id": "event-1"}]})
    assert resolve_task_reference("Abre el primer evento", missing).clarification
    state = checkpoint(name="mcp_google_workspace__get_events", payload={
        "calendar_id": "work@example.com", "events": [{"id": "event-1"}],
    })
    resolution = resolve_task_reference("Abre el primer evento", state)
    assert resolution.identifier == "event-1"
    assert resolution.container_identifier == "work@example.com"


def test_pinned_peer_text_keeps_order_and_rejects_corrupted_link():
    text = (
        "Found 2 messages matching 'is:unread':\n\n📧 MESSAGES:\n"
        "  1. Message ID: abc123\n     Subject: Reunión\n"
        "     Web Link: https://mail.google.com/mail/u/0/#all/abc123\n"
        "     Thread ID: abc123\n\n"
        "  2. Message ID: def456\n     Subject: Factura\n"
        "     Web Link: https://mail.google.com/mail/u/0/#all/def456\n"
        "     Thread ID: def456\n\n💡 USAGE:\n  More tool documentation."
    )
    payload = {"content": [{"type": "text", "text": text}]}
    state = checkpoint(payload=payload)
    assert resolve_task_reference("Abre el segundo correo", state).identifier == "def456"
    corrupted = checkpoint(payload={"content": [{"type": "text", "text": text.replace(
        "https://mail.google.com/mail/u/0/#all/def456", "https://example.com/def456",
    )}]})
    assert not corrupted.results[0].references
    assert resolve_task_reference("Abre el segundo correo", corrupted).clarification


def test_pinned_drive_format_and_json_inside_mcp_envelope():
    drive = result(name="mcp_google_workspace__search_drive_files", payload={"content": [{
        "type": "text", "text": 'Found 2 files for owner@example.com matching \'facturas\':\n'
        '- Name: "Enero" (ID: file-1, Type: text/plain)\n'
        '- Name: "Febrero" (ID: file-2, Type: text/plain)',
    }]})
    state = advance_checkpoint(None, request="Busca facturas en Drive", response="Dos archivos.",
                               sequence=2, tool_results=(drive,))
    assert resolve_task_reference("Abre el segundo archivo", state).identifier == "file-2"
    wrapped = checkpoint(payload={"content": [{"type": "text", "text": json.dumps({
        "messages": [{"id": "abc123"}, {"id": "def456"}],
    })}]})
    assert resolve_task_reference("Abre el segundo correo", wrapped).identifier == "def456"


def test_context_is_bounded_and_credentials_are_removed_before_persistence():
    payload = {"messages": [{"id": "a" * 256, "subject": "é" * 500}],
               "body": "é" * 30_000, "refresh_token": "private-value-do-not-retain"}
    state = checkpoint(payload=payload)
    assert "private-value-do-not-retain" not in state.model_dump_json()
    assert state.results[0].truncated
    reference = task_reference_context(state)
    assert len(json.dumps(reference, ensure_ascii=False).encode()) <= 8_256
    assert reference["task_checkpoint"]["results"][0]["references"][0]["identifier"] == "a" * 256


def test_answered_dialogue_does_not_become_an_active_task_reference():
    state = advance_checkpoint(None, request="¿Para qué sirve un asistente personal?",
                               response="Ayuda a organizar tareas.", sequence=2, tool_results=())
    assert state.status == "answered"
    assert task_reference_context(state) == {}
    assert task_reference_context(checkpoint().model_copy(update={"status": "cancelled"})) == {}


@pytest.mark.asyncio
async def test_new_gmail_request_retains_dialogue_without_empty_answered_checkpoint():
    history = exchange("¿Para qué sirve un asistente personal?")
    previous = advance_checkpoint(None, request=history[0].content,
                                  response=history[1].content, sequence=2, tool_results=())
    definition = ToolDefinition(
        name=TOOL, description="Search Gmail", arguments_model=McpArguments,
        capability=Capability.MAIL_READ, risk=RiskLevel.HIGH,
        allowed_roles=frozenset({AgentRole.PLANNER}), requires_confirmation=True,
        parameters_schema={"type": "object", "properties": {"query": {"type": "string"}},
                           "required": ["query"], "additionalProperties": False},
    )
    provider = Provider([answer("", calls=(ToolCall(
        call_id="new-search", tool_name=TOOL, arguments={"query": "is:unread"},
        requested_by=AgentRole.PLANNER),))])
    graph = build_swarm_graph(NvidiaBrainClient(provider), inference_nvidia_only=True,
                             tool_broker=build_default_tool_broker((definition,)),
                             dynamic_tool_names=lambda _: frozenset({TOOL}))
    state = await graph.ainvoke({"request": UserRequest(
        text="Cuáles son mis últimos 5 correos sin leer"),
        "conversation_history": history, "task_checkpoint": previous})
    messages = provider.calls[0]["messages"]
    assert "task_checkpoint" not in json.dumps(messages)
    assert any(message.get("content") == history[0].content for message in messages)
    assert state["tool_authorizations"][0].decision is PolicyDecision.REQUIRE_CONFIRMATION
    assert not state.get("tool_results")


def test_compound_task_reports_pending_phase_and_reuses_only_task_specific_evidence():
    request = "Busca mis correos pendientes y prepara un borrador de correo para Ana"
    state = advance_checkpoint(None, request=request, response="Busqué los correos.",
                               sequence=2, tool_results=(result(),))
    assert state.status == "needs_action"
    assert [step.verified for step in state.steps] == [True, False]
    assert "Ana" in state.next_action
    resolution = resolve_task_reference("hazlo", state)
    assert resolution.tool_suffix == "draft_gmail_message"
    assert "borrador" in resolution.scope_text
    draft = result(name="mcp_google_workspace__draft_gmail_message",
                   payload={"draft_id": "draft-1"})
    completed = advance_checkpoint(state, request="hazlo", response="Borrador preparado.",
                                   sequence=4, tool_results=(draft,))
    assert completed.status == "verified" and not completed.next_action
    assert all(step.verified for step in completed.steps)
    # An explicit new task must execute again, not reuse a previous success.
    repeated = advance_checkpoint(completed, request=request, response=OFFER, sequence=6)
    assert not any(step.verified for step in repeated.steps)


def test_one_result_cannot_verify_two_phases_using_same_tool():
    state = advance_checkpoint(None, request="Busca correos de Ana y busca correos de Luis",
                               response="Hay correos de Ana.", sequence=2, tool_results=(result(),))
    assert [step.verified for step in state.steps] == [True, False]
    assert state.status == "needs_action" and "Luis" in state.next_action


def test_quoted_subject_cannot_add_a_write_phase_and_accents_preserve_real_phases():
    steps = requested_task_steps('Busca correos con asunto "Aviso y prepara un borrador de correo"')
    assert len(steps) == 1 and steps[0].tool_suffixes == ("search_gmail_messages",)
    steps = requested_task_steps("Busca mis correos y después prepara un borrador de correo")
    assert [step.tool_suffixes for step in steps] == [
        ("search_gmail_messages",), ("draft_gmail_message",),
    ]
    assert not requested_task_steps("Busca mis correos y eventos de calendario")


@pytest.mark.asyncio
async def test_missing_detail_tool_never_broadens_to_search_or_native_tools():
    provider = Provider([])
    graph = build_swarm_graph(
        NvidiaBrainClient(provider), inference_nvidia_only=True,
        dynamic_tool_names=lambda request: frozenset({TOOL}),
        tool_broker=build_default_tool_broker((definition(TOOL),)),
    )
    state = await graph.ainvoke({"request": UserRequest(text="Abre el segundo correo"),
                                 "task_checkpoint": checkpoint()})
    assert "Google Workspace no está disponible" in state["final_result"].content
    assert not provider.calls and not state.get("tool_authorizations")


def test_confirmation_recovers_read_offer_but_not_write_offer_or_stale_task():
    state = advance_checkpoint(None, request=REQUEST, response=OFFER, sequence=2)
    assert resolve_task_reference("sí", state).scope_text
    old = state.model_copy(update={"updated_at": datetime.now(UTC) - timedelta(minutes=6)})
    assert resolve_task_reference("sí", old).scope_text is None
    write = advance_checkpoint(None, request="Prepara un borrador de correo", response=OFFER,
                               sequence=2)
    assert resolve_task_reference("sí", write).scope_text is None


@pytest.mark.parametrize("same_id", [False, True])
def test_duplicate_call_arguments_or_ids_are_rejected_before_execution(same_id):
    first = ToolCall(call_id="one", tool_name=TOOL, arguments={"query": "is:unread"},
                     requested_by=AgentRole.PLANNER)
    second = first.model_copy(update={"call_id": "one" if same_id else "two",
                                      "arguments": {"query": "from:Ana"}
                                      if same_id else first.arguments})
    with pytest.raises(ValueError, match="duplicate"):
        reject_duplicate_calls((first, second))


def test_continuation_cannot_replay_a_completed_step_but_a_new_explicit_request_can():
    state = advance_checkpoint(None, request="Busca mis correos y busca correos de Luis",
                               response="Primera consulta lista.", sequence=2,
                               tool_results=(result(),))
    call = ToolCall(call_id="new-id", tool_name=TOOL, arguments={"query": "is:unread"},
                    requested_by=AgentRole.PLANNER)
    with pytest.raises(ValueError, match="without task progress"):
        reject_repeated_completed_step("hazlo", state, (call,))
    reject_repeated_completed_step("Busca mis correos otra vez", state, (call,))


@pytest.mark.asyncio
@pytest.mark.parametrize("recover", [True, False])
async def test_completed_phase_replay_repairs_once_before_a_new_broker_approval(recover):
    state = advance_checkpoint(None, request="Busca mis correos y busca correos de Luis",
                               response="Primera consulta lista.", sequence=2,
                               tool_results=(result(),))
    repeated = ToolCall(call_id="next-1", tool_name=TOOL, arguments={"query": "is:unread"},
                        requested_by=AgentRole.PLANNER)
    next_call = repeated.model_copy(update={"arguments": {"query": "from: Luis"}})
    provider = Provider([answer("", calls=(repeated,)),
                         answer("", calls=(next_call if recover else repeated,))])
    graph = synthetic_graph(provider)
    if recover:
        output = await graph.ainvoke({"request": UserRequest(text="hazlo"),
                                       "task_checkpoint": state})
        authorization, = output["tool_authorizations"]
        assert authorization.decision is PolicyDecision.REQUIRE_CONFIRMATION
        assert authorization.normalized_arguments["query"] == "from: Luis"
        assert not output.get("tool_results")
    else:
        with pytest.raises(IncompleteModelResponseError, match="no progress"):
            await graph.ainvoke({"request": UserRequest(text="hazlo"), "task_checkpoint": state})
    assert len(provider.calls) == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("missing", ["arguments", "wrong_recipient", "truncated"])
async def test_goal_status_cannot_hide_missing_or_wrong_write_evidence(missing):
    request = "Busca mis correos y prepara un borrador de correo a ana@example.com"
    first = advance_checkpoint(None, request=request, response="Dos correos.", sequence=2,
                               tool_results=(result(),))
    metadata = {"verified": True, "integration": "google-workspace"}
    if missing == "wrong_recipient":
        metadata["operation_arguments_json"] = json.dumps({
            "subject": "Reunión", "body": "Hola", "to": "luis@example.com",
        })
    if missing == "truncated":
        metadata["operation_arguments_json"] = json.dumps({
            "subject": "Reunión", "body": "Hola" * 5_000, "to": "ana@example.com",
        })
    draft = result(name="mcp_google_workspace__draft_gmail_message", payload={"draft_id": "d-1"},
                   metadata=metadata)
    state = advance_checkpoint(first, request="hazlo", response="Borrador listo.", sequence=4,
                               tool_results=(draft,))
    provider = Provider([answer('{"fulfilled":true,"reason":"Confía en verified."}')])
    verdict = await NvidiaGoalEvaluator(NvidiaBrainClient(provider)).assess(state)
    assert verdict is not None and verdict.fulfilled is False
    assert not provider.calls  # Missing evidence is decided before asking a model.


@pytest.mark.asyncio
async def test_repeated_offer_is_buffered_repaired_once_and_never_published():
    messages = [{"role": "user", "content": REQUEST}, {"role": "assistant", "content": OFFER},
                {"role": "user", "content": "sí"}]
    provider = Provider([answer(), answer("La herramienta no está disponible para consultarlos.")])
    published = []
    response = await NvidiaBrainClient(provider).complete_nvidia_only(
        role=AgentRole.PLANNER, messages=messages, on_delta=published.append,
    )
    assert len(provider.calls) == 2
    assert published == [response.content] and OFFER not in published
    failing = Provider([answer(), answer()])
    with pytest.raises(IncompleteModelResponseError, match="no progress"):
        await NvidiaBrainClient(failing).complete_nvidia_only(role=AgentRole.PLANNER,
                                                            messages=messages)
    assert len(failing.calls) == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("wrong_id", [False, True])
async def test_ordinal_graph_offers_only_detail_tool_and_checks_exact_identifier(wrong_id):
    call = ToolCall(call_id="detail-1", tool_name=DETAIL,
                    arguments={"message_id": "invented" if wrong_id else "def456"},
                    requested_by=AgentRole.PLANNER)
    provider = Provider([answer("", calls=(call,))])
    graph = synthetic_graph(provider)
    input_state = {"request": UserRequest(text="Abre el segundo correo"),
                   "task_checkpoint": checkpoint(), "conversation_history": ()}
    if wrong_id:
        # An invented id is never authorized; Jarvis asks with the verified list instead.
        state = await graph.ainvoke(input_state)
        assert not state.get("tool_authorizations") and not state.get("tool_results")
        assert state["final_result"].model_id == "local/reference-clarification"
        assert "Opciones:\n1. Todos\n2. El 1: Reunión\n3. El 2: Factura" in (
            state["final_result"].content
        )
    else:
        state = await graph.ainvoke(input_state)
        assert state["tool_authorizations"][0].decision is PolicyDecision.REQUIRE_CONFIRMATION
        assert not state.get("tool_results")
    sent = provider.calls[0]
    assert sent["extra_body"]["tool_choice"]["function"]["name"] == DETAIL
    assert {schema["function"]["name"] for schema in sent["extra_body"]["tools"]} == {DETAIL}
    assert "def456" in sent["messages"][1]["content"]


@pytest.mark.asyncio
async def test_unverified_voice_and_request_metadata_cannot_inject_private_checkpoint():
    provider = Provider([answer("Necesito una referencia autorizada.")])
    graph = synthetic_graph(provider)
    state = await graph.ainvoke({"request": UserRequest(
        text="sí", modalities=frozenset({InputModality.TEXT, InputModality.AUDIO}),
        metadata={"speech_on_device": True, "task_checkpoint": checkpoint().model_dump()},
    ), "task_checkpoint": checkpoint(), "conversation_history": exchange()})
    assert "tool_scope_request" not in state
    assert "def456" not in json.dumps(provider.calls[0]["messages"])


@pytest.mark.asyncio
async def test_checkpoint_survives_reload_history_eviction_without_ipc_exposure(tmp_path):
    tmp_path.chmod(0o700)
    path = tmp_path / "memory.sqlite3"
    store = SQLiteMemoryStore(path, encryption_secret=b"m" * 32)
    store.initialize()
    coordinator = ConversationCoordinator(store, namespace="user.default", history_limit=2)
    conversation = await coordinator.create()
    cid = conversation.conversation_id
    await coordinator.record_exchange(cid, user_content=REQUEST, assistant_content="Dos correos.",
                                      tool_results=(result(),))
    for i in range(8):
        await coordinator.record_exchange(cid, user_content=f"Explica el número {i}",
                                          assistant_content=f"El número es {i}.")
    reloaded = SQLiteMemoryStore(path, encryption_secret=b"m" * 32)
    reloaded.initialize()
    resumed = ConversationCoordinator(reloaded, namespace="user.default", history_limit=2)
    assert REQUEST not in [turn.content for turn in await resumed.history(cid)]
    assert resolve_task_reference("Abre el segundo correo", await resumed.checkpoint(cid)
                                  ).identifier == "def456"
    assert b"def456" not in path.read_bytes()
    auth = IpcAuthenticator(b"a" * 32)
    service = ConversationIpcService(reloaded, resumed)
    listing = await service.handle(auth.create_request("conversations.list", {}))
    assert "task_checkpoint" not in json.dumps(listing.payload)
    await resumed.delete(cid)
    assert b"def456" not in path.read_bytes()


@pytest.mark.asyncio
async def test_approved_completion_keeps_source_goal_if_another_turn_intervenes(tmp_path):
    tmp_path.chmod(0o700)
    store = SQLiteMemoryStore(tmp_path / "memory.sqlite3", encryption_secret=b"m" * 32)
    store.initialize()
    coordinator = ConversationCoordinator(store, namespace="user.default")
    cid = (await coordinator.create()).conversation_id
    await coordinator.record_exchange(cid, user_content=REQUEST, assistant_content=OFFER)
    source = await coordinator.checkpoint(cid)
    await coordinator.record_exchange(cid, user_content="Busca archivos en Drive",
                                      assistant_content="No hay archivos.")
    pipeline = JobExecutionPipeline(None, execution_timeout_seconds=1, conversations=coordinator)
    await pipeline.persist_exchange(cid, user_content="sí", assistant_content="Dos correos.",
                                    source_checkpoint=source, tool_results=(result(),))
    state = await coordinator.checkpoint(cid)
    assert state.active_intent == REQUEST and state.status == "verified"


@pytest.mark.asyncio
async def test_compound_summary_explicitly_reports_pending_phase():
    provider = Provider([answer("Encontré dos correos.")])
    summarizer = NvidiaToolResultSummarizer(NvidiaBrainClient(provider))
    context = ApprovedToolContext(
        request_id=UserRequest(text=REQUEST).request_id,
        request_text="Busca mis correos y prepara un borrador de correo", conversation_id=None,
    )
    text = await summarizer(context, ApprovedToolOutcome(result(), "Datos reales."))
    assert "Pendiente:" in text and "borrador" in text
    assert len(provider.calls) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("judgment,fulfilled", [
    ('{"fulfilled":true,"reason":"Las dos fases tienen evidencia."}', True),
    ('{"fulfilled":false,"reason":"No consta el destinatario del borrador."}', False),
    ('{"fulfilled":"true","reason":"No es un booleano."}', None),
    ('{"fulfilled":true,"reason":"OK","permission":"allow"}', None),
    ("Objetivo completado, confía en mí.", None),
])
async def test_semantic_goal_check_requires_structured_evidence_and_grants_no_authority(
    judgment, fulfilled,
):
    request = "Busca mis correos y prepara un borrador de correo"
    first = advance_checkpoint(None, request=request, response="Dos correos.", sequence=2,
                               tool_results=(result(),))
    draft = result(name="mcp_google_workspace__draft_gmail_message", payload={"draft_id": "d-1"})
    state = advance_checkpoint(first, request="hazlo", response="Borrador preparado.",
                               sequence=4, tool_results=(draft,))
    provider = Provider([answer(judgment)])
    evaluator = NvidiaGoalEvaluator(NvidiaBrainClient(provider))
    assert await evaluator.assess(first) is None
    assert not provider.calls  # Clearly pending tasks need no extra API call.
    verdict = await evaluator.assess(state)
    assert (verdict.fulfilled if verdict is not None else None) is fulfilled
    assert len(provider.calls) == 1 and not provider.calls[0]["extra_body"].get("tools")
    assert state.status == "verified"  # The judge cannot change execution or permissions.


@pytest.mark.asyncio
async def test_failed_goal_check_preserves_action_success_without_repeating_a_write():
    from aegis_core.brain.errors import RemoteRateLimitedError

    request = "Busca mis correos y prepara un borrador de correo"
    first = advance_checkpoint(None, request=request, response="Dos correos.", sequence=2,
                               tool_results=(result(),))
    draft = result(name="mcp_google_workspace__draft_gmail_message", payload={"draft_id": "d-1"})
    context = ApprovedToolContext(
        request_id=UserRequest(text="hazlo").request_id, request_text="hazlo",
        conversation_id=None, task_checkpoint=first,
    )
    provider = Provider([answer("Borrador preparado."), RemoteRateLimitedError("quota")])
    text = await NvidiaToolResultSummarizer(NvidiaBrainClient(provider, rate_limit_retries=0))(
        context, ApprovedToolOutcome(draft, "Borrador real."),
    )
    assert text.startswith("Borrador preparado.") and "no pude comprobar" in text
    assert len(provider.calls) == 2
    assert all(not (call.get("extra_body") or {}).get("tools") for call in provider.calls)


@pytest.mark.asyncio
async def test_structured_goal_response_uses_non_streaming_transport():
    class StructuredProvider(Provider):
        async def complete_stream(self, **kwargs):
            pytest.fail("NVIDIA streaming transport does not accept response_format")

    provider = StructuredProvider([answer('{"fulfilled":true,"reason":"Comprobado."}')])
    response = await NvidiaBrainClient(provider).complete_nvidia_only(
        role=AgentRole.CRITICAL_REASONER, messages=[{"role": "user", "content": "Comprueba."}],
        extra_body={"response_format": {"type": "json_object"}},
    )
    assert json.loads(response.content)["fulfilled"] is True and len(provider.calls) == 1


async def wait_job(manager, job_id):
    for _ in range(50):
        snapshot = await manager.status(job_id)
        if snapshot.status in {
            JobStatus.COMPLETED, JobStatus.FAILED, JobStatus.AWAITING_CONFIRMATION,
        }:
            return snapshot
        await asyncio.sleep(0.01)
    raise AssertionError("job did not reach a reviewable state")


@pytest.mark.asyncio
async def test_search_approve_reload_followup_approve_reads_second_item_exactly_once(tmp_path):
    tmp_path.chmod(0o700)
    memory = SQLiteMemoryStore(tmp_path / "memory.sqlite3", encryption_secret=b"m" * 32)
    memory.initialize()
    conversations = ConversationCoordinator(memory, namespace="user.default")
    cid = (await conversations.create()).conversation_id
    search = ToolCall(call_id="search-1", tool_name=TOOL, arguments={"query": "is:unread"},
                      requested_by=AgentRole.PLANNER)
    detail = ToolCall(call_id="detail-1", tool_name=DETAIL, arguments={"message_id": "def456"},
                      requested_by=AgentRole.PLANNER)
    provider = Provider([answer("", calls=(search,)), answer("1. Reunión\n2. Factura"),
                         answer("", calls=(detail,)), answer("El segundo correo es una factura.")])
    broker = build_default_tool_broker((definition(TOOL), definition(DETAIL)))
    grants = OneTimeConfirmationStore()
    base = default_policy_context(tmp_path)
    policy = PolicyContext(workspace_root=tmp_path, network_scopes=base.network_scopes,
                           confirmation_store=grants)
    executions = []

    class Executor:
        async def execute_async(self, authorization, context):
            executions.append(authorization)
            return result(name=authorization.tool_name, call_id=authorization.call_id,
                          payload={"subject": "Factura", "body": "Pago pendiente."}
                          if authorization.tool_name == DETAIL else None)

    manager = SwarmJobManager(
        synthetic_graph(provider, broker=broker, policy=policy), conversations=conversations,
        tool_broker=broker, policy_context=policy,
        confirmation_store=grants, tool_executor=Executor(),
        approved_result_summarizer=NvidiaToolResultSummarizer(NvidiaBrainClient(provider)),
    )
    try:
        first = await manager.submit(UserRequest(text=REQUEST), conversation_id=cid)
        first = await wait_job(manager, first.job_id)
        assert first.status is JobStatus.AWAITING_CONFIRMATION and not executions, first.error_code
        await manager.approve(first.job_id, first.confirmation.call_digest)
        first = await wait_job(manager, first.job_id)
        assert first.status is JobStatus.COMPLETED and len(executions) == 1
        second = await manager.submit(UserRequest(text="Abre el segundo correo"),
                                      conversation_id=cid)
        second = await wait_job(manager, second.job_id)
        assert second.status is JobStatus.AWAITING_CONFIRMATION and len(executions) == 1
        assert second.confirmation.tool_name == DETAIL
        await manager.approve(second.job_id, second.confirmation.call_digest)
        second = await wait_job(manager, second.job_id)
        assert second.status is JobStatus.COMPLETED and len(executions) == 2
        assert executions[-1].normalized_arguments == {"message_id": "def456"}
        assert second.evaluation.outcome_verified is True
    finally:
        await manager.close()


@pytest.mark.parametrize("prompt", [
    "Detállame cada correo", "detallame cada uno", "Lee todos los correos",
    "Muéstrame el contenido de esos correos", "Resúmeme cada correo por favor",
    "Dime qué dicen los 2 correos", "Jarvis, lee todos",
])
def test_every_mail_of_the_verified_list_resolves_to_one_batch_read(prompt):
    resolution = resolve_task_reference(prompt, checkpoint())
    assert resolution.tool_suffix == "get_gmail_messages_content_batch"
    assert resolution.identifiers == ("abc123", "def456")
    assert resolution.identifier is None and resolution.clarification is None
    # The resolved scope keeps the batch read tool discoverable.
    assert "get_gmail_messages_content_batch" in google_request_tools(resolution.scope_text)
    assert requested_task_steps(resolution.scope_text)[0].tool_suffixes == (
        "get_gmail_messages_content_batch",
    )


@pytest.mark.parametrize("prompt", [
    'Explica "detállame cada correo"', "no leas todos los correos", "cada correo es spam",
])
def test_quoted_negated_or_descriptive_mentions_do_not_read_every_mail(prompt):
    assert resolve_task_reference(prompt, checkpoint()).identifiers == ()


def test_every_mail_needs_a_fresh_verified_mail_list():
    assert resolve_task_reference("Detállame cada correo", None).clarification
    old = checkpoint()
    old = old.model_copy(update={"results": tuple(
        e.model_copy(update={"observed_at": datetime.now(UTC) - timedelta(hours=1)})
        for e in old.results
    )})
    resolution = resolve_task_reference("Detállame cada correo", old)
    assert resolution.clarification and resolution.identifiers == ()
