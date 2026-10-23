"""Explicit native app destinations cannot become unrelated connected-service writes."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from pydantic import BaseModel, ConfigDict

from aegis_core.contracts import (
    PLAN_MODE_METADATA,
    UNSIGNED_PEER_METADATA,
    AgentResult,
    AgentRole,
    Capability,
    PolicyDecision,
    RiskLevel,
    ToolCall,
    UserRequest,
)
from aegis_core.memory.contracts import ConversationRole, ConversationTurn
from aegis_core.orchestration.graph import build_nvidia_swarm_graph
from aegis_core.orchestration.routing import _route_request, _tool_names_for_request
from aegis_core.orchestration.semantic_tools import select_tools
from aegis_core.providers.base import IncompleteModelResponseError
from aegis_core.request_intent import is_explicit_native_ui_request
from aegis_core.skills import SkillDraft, SkillRegistry, SkillStore
from aegis_core.tools.broker import ToolDefinition
from aegis_core.tools.computer_workflow import computer_destination_matches_request
from aegis_core.tools.defaults import build_default_tool_broker, default_policy_context
from aegis_core.tools.execution import ReadOnlyToolExecutor

REQUEST = (
    "Controla la app TextEdit para escribir únicamente «PRUEBA JARVIS A» "
    "en el documento nuevo vacío abierto. No abras otros documentos ni guardes."
)
DOCUMENT_REQUEST = (
    "Escribe exactamente «PRUEBA JARVIS SIEMPRE A» en el documento nuevo y vacío de TextEdit. "
    "No guardes ni cierres el documento."
)
DRIVE_TOOL = "mcp_google_workspace__create_drive_file"
CATALOGUE = ((DRIVE_TOOL, "Crea un archivo en Google Drive"),)


class DriveArguments(BaseModel):
    model_config = ConfigDict(extra="forbid")
    content: str
    file_name: str
    mime_type: str = "text/plain"


class UiProvider:
    def __init__(self, tool_name="computer_use", objective=REQUEST, literal="PRUEBA JARVIS A"):
        self.requests = []
        arguments = (
            {"content": "PRUEBA JARVIS A", "file_name": "PRUEBA JARVIS A.txt"}
            if tool_name == DRIVE_TOOL else {
                "application_bundle_identifier": "com.apple.TextEdit", "objective": objective,
                "max_steps": 3,
                "postconditions": [{"kind": "field_contains", "value": literal}],
            }
        )
        self.call = ToolCall(call_id="synthetic-proposal", tool_name=tool_name,
                             requested_by=AgentRole.PLANNER, arguments=arguments)

    async def complete_nvidia_only(self, **kwargs):
        self.requests.append(kwargs)
        role = kwargs["role"]
        return AgentResult(
            role=role, model_id="synthetic/nvidia",
            content=json.dumps({"intent": "accion", "tools": [DRIVE_TOOL]})
            if role is AgentRole.ROUTER else "Acción pendiente de autorización",
            tool_calls=(self.call,) if role is AgentRole.PLANNER else (),
        )


class NoExecution(ReadOnlyToolExecutor):
    async def execute_async(self, authorization, context):
        pytest.fail("No UI, cloud service or other tool may execute without owner approval")


def make_graph(tmp_path, provider, *, dynamic=False, skill=False):
    broker = build_default_tool_broker((ToolDefinition(
        name=DRIVE_TOOL, description="Create a synthetic cloud file",
        arguments_model=DriveArguments,
        capability=Capability.APPLICATION_CONTROL, risk=RiskLevel.HIGH,
        allowed_roles=frozenset({AgentRole.PLANNER}), requires_confirmation=True,
    ),))
    registry = None
    if skill:
        registry = SkillRegistry(broker, SkillStore(tmp_path / "synthetic-skills"))
        registry.learn(SkillDraft(
            skill_id="synthetic-cloud-starter", name="Propuesta sintética",
            description="Prueba de un starter ajeno al destino actual.",
            role=AgentRole.PLANNER, trigger_phrases=("controla la app", "escribe exactamente"),
            trigger_terms=frozenset({"controla", "textedit"}), priority=95,
            instructions=("Prepara la operación usando el servicio conectado.",),
            allowed_tools=frozenset({DRIVE_TOOL}), starter_tools=frozenset({DRIVE_TOOL}),
        ))
    return build_nvidia_swarm_graph(
        provider, tool_broker=broker, policy_context=default_policy_context(tmp_path),
        tool_executor=NoExecution(), skill_registry=registry,
        dynamic_tool_names=lambda request: frozenset({DRIVE_TOOL}) if dynamic else frozenset(),
        semantic_tool_catalogue=lambda: CATALOGUE,
    )


@pytest.mark.parametrize("text", [
    REQUEST,
    "Controla la aplicación Pages para escribir «Hola» en el documento abierto",
    "Interactúa con la app Numbers para seleccionar la celda A1",
    "Maneja la ventana de Vista Previa para seleccionar el texto visible",
    "Escribe «Crea un archivo en Google Drive» en la app TextEdit",
    "Controla la app TextEdit para escribir «Hola». No uses Google Drive ni envíes correo",
    "Controla visualmente: en TextEdit comprueba el texto; después en Safari abre la página",
    "¿Puedes controlar mi computadora y usar Safari?",
])
def test_native_ui_intent_binds_to_visual_tool_without_authorizing_it(text):
    request = UserRequest(text=text)
    assert is_explicit_native_ui_request(text) is True
    assert _route_request(request).role is AgentRole.PLANNER
    assert _tool_names_for_request(request) == frozenset({"computer_use"})


@pytest.mark.parametrize("text", [
    "Crea un archivo en Google Drive con el texto PRUEBA JARVIS A",
    "Busca un tutorial sobre cómo controlar la app TextEdit",
    "No controles la app TextEdit",
    "No escribas en la app TextEdit",
    'Explica «Controla la app TextEdit para escribir Hola»',
    'Traduce: "Interactúa con la app Numbers"',
    "Controla la app TextEdit para escribir Hola y súbelo a Drive",
    "Controla la app TextEdit para escribir Hola; luego envía el texto por correo",
    "Controla la app TextEdit y crea un archivo en Google Drive",
    "Controla la app TextEdit y busca el documento en Drive",
    "Controla mi Mac en Chrome: abre https://example.com y selecciona Informes",
    "Controla la app Chrome: abre https://example.com y selecciona Informes",
])
def test_services_examples_negations_and_compound_requests_are_not_reclassified(text):
    assert is_explicit_native_ui_request(text) is False


async def test_semantic_service_selector_cannot_substitute_a_native_app():
    complete = AsyncMock(return_value=json.dumps({"intent": "accion", "tools": [DRIVE_TOOL]}))

    assert await select_tools(REQUEST, (), CATALOGUE, complete) == frozenset()
    complete.assert_not_awaited()

    assert await select_tools("Crea un archivo en Drive", (), CATALOGUE, complete) == frozenset({
        DRIVE_TOOL,
    })
    complete.assert_awaited_once()


@pytest.mark.parametrize(("dynamic", "skill"), [(False, False), (True, False), (True, True)])
async def test_exact_request_offers_only_gui_and_keeps_owner_confirmation(tmp_path, dynamic, skill):
    provider = UiProvider()

    state = await make_graph(tmp_path, provider, dynamic=dynamic, skill=skill).ainvoke({
        "request": UserRequest(text=REQUEST),
    })

    planning = next(item for item in provider.requests if item["role"] is AgentRole.PLANNER)
    assert [tool["function"]["name"] for tool in planning["extra_body"]["tools"]] == [
        "computer_use",
    ]
    assert planning["extra_body"]["tool_choice"] == {
        "type": "function", "function": {"name": "computer_use"},
    }
    instruction = planning["messages"][0]["content"]
    assert "already open, new, unsaved document" in instruction
    assert "field_contains with the exact literal and omit target" in instruction
    assert json.dumps({"kind": "field_contains", "value": "PRUEBA JARVIS A"}) in instruction
    assert "Never use it to open, read, save or change existing files" in instruction
    authorization, = state["tool_authorizations"]
    assert authorization.tool_name == "computer_use"
    assert authorization.decision is PolicyDecision.REQUIRE_CONFIRMATION
    assert authorization.normalized_arguments["objective"] == REQUEST
    assert not state.get("tool_results")
    if skill:
        assert any(s.manifest.skill_id == "synthetic-cloud-starter" for s in state["skills"])


@pytest.mark.parametrize("condition", [
    {"kind": "text_present", "value": "PRUEBA JARVIS A"},
    {"kind": "field_contains", "target": "PRUEBA JARVIS A", "value": True},
])
async def test_literal_write_example_cannot_relax_postcondition_validation(tmp_path, condition):
    provider = UiProvider()
    provider.call = provider.call.model_copy(update={"arguments": {
        **provider.call.arguments, "postconditions": [condition],
    }})

    state = await make_graph(tmp_path, provider, dynamic=True).ainvoke({
        "request": UserRequest(text=REQUEST),
    })

    authorization, = state["tool_authorizations"]
    assert authorization.decision is PolicyDecision.DENY
    assert authorization.reason_code == "invalid_arguments"
    assert not state.get("tool_results")


class TextOnlyUiProvider(UiProvider):
    async def complete_nvidia_only(self, **kwargs):
        self.requests.append(kwargs)
        return AgentResult(
            role=kwargs["role"], model_id="synthetic/nvidia",
            content="Aquí estoy. Voy a escribir PRUEBA JARVIS A.\n"
                    "Opciones:\n1. Escribir y listo\n2. Cancelar\n¿Qué prefieres?",
        )


async def test_text_only_gui_approval_imitation_is_incomplete_and_never_executed(tmp_path):
    provider = TextOnlyUiProvider()
    chunks = []

    with pytest.raises(IncompleteModelResponseError, match="requested action has no tool proposal"):
        await make_graph(tmp_path, provider).ainvoke({
            "request": UserRequest(text=REQUEST), "stream_callback": chunks.append,
        })

    assert [item["role"] for item in provider.requests] == [AgentRole.PLANNER]
    assert provider.requests[0]["extra_body"]["tool_choice"] == {
        "type": "function", "function": {"name": "computer_use"},
    }
    assert chunks == []


@pytest.mark.parametrize("metadata", [{PLAN_MODE_METADATA: True}, {UNSIGNED_PEER_METADATA: True}])
async def test_text_only_response_does_not_force_unavailable_gui_tools(tmp_path, metadata):
    provider = TextOnlyUiProvider()

    state = await make_graph(tmp_path, provider).ainvoke({
        "request": UserRequest(text=REQUEST, metadata=metadata),
    })

    assert all(not (item.get("extra_body") or {}).get("tools") for item in provider.requests)
    assert not state.get("tool_authorizations")
    assert not state.get("tool_results")


@pytest.mark.parametrize(("dynamic", "skill"), [(False, False), (True, False), (True, True)])
async def test_unexpected_drive_write_is_denied_before_owner_approval(tmp_path, dynamic, skill):
    provider = UiProvider(DRIVE_TOOL)
    conversation_id = uuid4()
    prior_text = "Antes creamos un archivo en Google Drive"
    history = (ConversationTurn(
        conversation_id=conversation_id, sequence=1, role=ConversationRole.USER,
        content=prior_text, content_sha256=hashlib.sha256(prior_text.encode()).hexdigest(),
        created_at=datetime.now(UTC),
    ),)

    state = await make_graph(tmp_path, provider, dynamic=dynamic, skill=skill).ainvoke({
        "request": UserRequest(text=REQUEST), "conversation_history": history,
    })

    authorization, = state["tool_authorizations"]
    assert authorization.tool_name == DRIVE_TOOL
    assert authorization.decision is PolicyDecision.DENY
    assert authorization.reason_code == "tool_not_offered"
    assert not state.get("tool_results")


@pytest.mark.parametrize("metadata", [{PLAN_MODE_METADATA: True}, {UNSIGNED_PEER_METADATA: True}])
async def test_ui_scope_does_not_bypass_plan_or_unsigned_restrictions(tmp_path, metadata):
    provider = UiProvider()

    state = await make_graph(tmp_path, provider, dynamic=True).ainvoke({
        "request": UserRequest(text=REQUEST, metadata=metadata),
    })

    planning = next(item for item in provider.requests if item["role"] is AgentRole.PLANNER)
    assert not (planning.get("extra_body") or {}).get("tools")
    assert state["tool_authorizations"][0].decision is PolicyDecision.DENY
    assert not state.get("tool_results")


async def test_capability_gap_cannot_replace_the_installed_visual_tool(tmp_path, monkeypatch):
    monkeypatch.setattr("aegis_core.orchestration.routing_nodes.is_capability_gap_request",
                        lambda *args, **kwargs: True)
    provider = UiProvider()

    state = await make_graph(tmp_path, provider).ainvoke({"request": UserRequest(text=REQUEST)})

    assert state["capability_gap"] is True
    planning = next(item for item in provider.requests if item["role"] is AgentRole.PLANNER)
    assert [tool["function"]["name"] for tool in planning["extra_body"]["tools"]] == [
        "computer_use",
    ]
    assert "The requested operation has no installed executor" not in planning["messages"][0][
        "content"
    ]
    assert state["tool_authorizations"][0].decision is PolicyDecision.REQUIRE_CONFIRMATION
    assert not state.get("tool_results")


async def test_an_explicit_drive_request_still_proposes_its_confirmed_service_write(tmp_path):
    provider = UiProvider(DRIVE_TOOL)

    state = await make_graph(tmp_path, provider, dynamic=True).ainvoke({
        "request": UserRequest(text="Crea un archivo en Google Drive con PRUEBA JARVIS A"),
    })

    authorization, = state["tool_authorizations"]
    assert authorization.tool_name == DRIVE_TOOL
    assert authorization.decision is PolicyDecision.REQUIRE_CONFIRMATION
    assert not state.get("tool_results")


@pytest.mark.parametrize(("text", "bundle", "matches"), [
    (REQUEST, "com.apple.TextEdit", True),
    (REQUEST, "com.apple.Safari", False),
    ("Controla la app Vista Previa para seleccionar el texto", "com.apple.Preview", True),
    ("Maneja la ventana de Vista Previa para seleccionar el texto", "com.apple.Safari", False),
    ("Interactúa con la aplicación Google Chrome para hacer clic", "com.apple.Safari", False),
    ("Controla la app com.example.Editor para escribir Hola", "com.example.Editor", True),
    ("Controla la app com.example.Editor para escribir Hola", "com.apple.TextEdit", False),
    ("Escribe «Safari» en la app TextEdit. No controles Safari", "com.apple.Safari", False),
    ("Escribe «Safari» en la app TextEdit. No controles Safari", "com.apple.TextEdit", True),
    ("Controla la app TextEdit para escribir «com.apple.Safari»", "com.apple.Safari", False),
    ("Controla la app Editor Desconocido. No controles Safari", "com.example.Unknown", True),
    ("No controles la app TextEdit", "com.apple.Safari", True),
    ('Explica «Controla la app TextEdit»', "com.apple.Safari", True),
    (REQUEST, None, False),
])
def test_known_destination_binding_ignores_payload_and_later_mentions(text, bundle, matches):
    assert computer_destination_matches_request(text, bundle) is matches


@pytest.mark.parametrize("browser_destination", [True, False])
async def test_browser_destination_keeps_its_gate_but_a_url_in_textedit_is_data(
    tmp_path, browser_destination,
):
    from test_browser_task_graph import BrowserProvider, SyntheticBrowser

    browser = SyntheticBrowser()
    text = (
        "Controla la app Chrome: abre https://example.com y selecciona Informes"
        if browser_destination else
        "Controla la app TextEdit para escribir «https://example.com» "
        "en el documento nuevo vacío abierto. No guardes."
    )
    if browser_destination:
        provider = BrowserProvider(browser)
    else:
        provider = UiProvider(objective=text)
        provider.call = provider.call.model_copy(update={"arguments": {
            **provider.call.arguments,
            "postconditions": [{"kind": "field_contains", "value": "https://example.com"}],
        }})
    graph = build_nvidia_swarm_graph(
        provider, tool_broker=build_default_tool_broker(browser.definitions()),
        policy_context=default_policy_context(tmp_path), tool_executor=NoExecution(),
        dynamic_tool_names=browser.names_for_request,
        browser_task_context=browser.context_for_request,
    )

    state = await graph.ainvoke({"request": UserRequest(text=text)})

    planning = next(item for item in provider.requests if item["role"] is AgentRole.PLANNER)
    expected = "browser_task_open" if browser_destination else "computer_use"
    assert [tool["function"]["name"] for tool in planning["extra_body"]["tools"]] == [expected]
    authorization, = state["tool_authorizations"]
    assert authorization.tool_name == expected
    assert authorization.decision is PolicyDecision.REQUIRE_CONFIRMATION
    assert not state.get("tool_results") and not browser.calls


@pytest.mark.parametrize("skill", [False, True])
@pytest.mark.parametrize("proposed_tool", ["computer_use", DRIVE_TOOL])
async def test_textedit_document_destination_survives_dynamic_catalogue_and_skill(
    tmp_path, skill, proposed_tool,
):
    provider = UiProvider(
        proposed_tool, objective=DOCUMENT_REQUEST, literal="PRUEBA JARVIS SIEMPRE A",
    )
    state = await make_graph(tmp_path, provider, dynamic=True, skill=skill).ainvoke({
        "request": UserRequest(text=DOCUMENT_REQUEST),
    })
    planning = next(item for item in provider.requests if item["role"] is AgentRole.PLANNER)
    assert [tool["function"]["name"] for tool in planning["extra_body"]["tools"]] == [
        "computer_use",
    ]
    if skill:
        assert any(item.manifest.skill_id == "synthetic-cloud-starter" for item in state["skills"])
    authorization, = state["tool_authorizations"]
    if proposed_tool == "computer_use":
        assert authorization.decision is PolicyDecision.REQUIRE_CONFIRMATION
        assert authorization.normalized_arguments["objective"] == DOCUMENT_REQUEST
        assert authorization.normalized_arguments["application_bundle_identifier"] == (
            "com.apple.TextEdit"
        )
    else:
        assert authorization.decision is PolicyDecision.DENY
        assert authorization.reason_code == "tool_not_offered"
    assert not state.get("tool_results")


async def test_textedit_document_destination_rejects_another_app_bundle(tmp_path):
    provider = UiProvider(objective=DOCUMENT_REQUEST, literal="PRUEBA JARVIS SIEMPRE A")
    provider.call = provider.call.model_copy(update={"arguments": {
        **provider.call.arguments, "application_bundle_identifier": "com.apple.Safari",
    }})
    state = await make_graph(tmp_path, provider, dynamic=True).ainvoke({
        "request": UserRequest(text=DOCUMENT_REQUEST),
    })
    authorization, = state["tool_authorizations"]
    assert authorization.decision is PolicyDecision.DENY
    assert authorization.reason_code == "computer_destination_not_requested"
    assert not state.get("tool_results")
