"""Observable built-in routing and tool boundaries; model quality is not simulated."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from historical.orchestration.graph import build_swarm_graph
from pydantic import BaseModel, ConfigDict, Field

from aegis_core.contracts import (
    AgentResult,
    AgentRole,
    Capability,
    DocumentInput,
    PolicyDecision,
    RiskLevel,
    ToolCall,
    ToolExecutionResult,
    UserRequest,
)
from aegis_core.skills import SkillRegistry, SkillStore
from aegis_core.skills.builtins import BUILTIN_SKILLS
from aegis_core.tools.broker import PolicyContext, ToolDefinition
from aegis_core.tools.defaults import build_default_tool_broker
from aegis_core.tools.execution import ReadOnlyToolExecutor


def registry(tmp_path: Path, broker=None) -> SkillRegistry:
    return SkillRegistry(broker or build_default_tool_broker(), SkillStore(tmp_path / "skills"))


class Boundary:
    """Capture actual production prompts; never pretend to obey a procedure."""

    def __init__(self, calls=()) -> None:
        self.calls = calls
        self.requests = []

    async def complete_nvidia_only(self, *, role, **kwargs):
        self.requests.append({"role": role, **kwargs})
        return AgentResult(
            role=role,
            model_id="synthetic/nvidia",
            content='{"skill_ids":[]}' if role is AgentRole.ROUTER else "Respuesta de fixture.",
            tool_calls=tuple(call for call in self.calls if call.requested_by is role),
        )

    async def complete(self, **_kwargs):
        raise AssertionError("This fixture must remain NVIDIA-only")


class Executor:
    def __init__(self):
        self.calls = []

    async def execute_async(self, authorization, _context):
        self.calls.append(authorization)
        return ToolExecutionResult(
            call_id=authorization.call_id,
            tool_name=authorization.tool_name,
            success=True,
            output='{"source":"synthetic","value":7}',
        )


@pytest.mark.parametrize(
    ("text", "identifier", "role"),
    [
        ("Controla mi computadora y usa Safari", "mac-control-expert", AgentRole.PLANNER),
        ("Investiga en internet la documentación", "browser-navigation-expert", AgentRole.PLANNER),
        ("Audita la seguridad de este Mac", "security-audit-expert", AgentRole.CODE_SECURITY),
        ("Revisa este código y sus errores", "code-review-expert", AgentRole.CODE_SECURITY),
        ("Organiza mi agenda y recordatorios", "personal-productivity-expert", AgentRole.PLANNER),
        (
            "Cuáles son mis últimos 5 correos sin leer",
            "personal-productivity-expert",
            AgentRole.PLANNER,
        ),
        ("Revisa mi Gmail", "google-workspace-expert", AgentRole.PLANNER),
        ("Revisa mi calendario de Google", "google-workspace-expert", AgentRole.PLANNER),
        ("Busca en Drive el informe", "google-workspace-expert", AgentRole.PLANNER),
        ("Crea una carpeta en Drive", "google-workspace-expert", AgentRole.PLANNER),
        ("Resume este documento adjunto", "document-analysis-expert", AgentRole.CRITICAL_REASONER),
        ("Lee el archivo informe.txt", "document-analysis-expert", AgentRole.CRITICAL_REASONER),
        ("Compara estos documentos", "document-analysis-expert", AgentRole.CRITICAL_REASONER),
        ("Analiza los datos del CSV", "document-analysis-expert", AgentRole.CRITICAL_REASONER),
        ("Revisa la batería", "mac-diagnostics-expert", AgentRole.PLANNER),
        ("Revisa el almacenamiento", "mac-diagnostics-expert", AgentRole.PLANNER),
        ("Revisa el rendimiento del Mac", "mac-diagnostics-expert", AgentRole.PLANNER),
        ("Diagnostica la conexión", "mac-diagnostics-expert", AgentRole.PLANNER),
        ("Diagnostica el audio", "mac-diagnostics-expert", AgentRole.PLANNER),
    ],
)
def test_domain_procedures_have_precise_local_routes(tmp_path, text, identifier, role):
    selection = registry(tmp_path).select(text)
    assert selection is not None
    assert selection.manifest.skill_id == identifier
    assert selection.manifest.role is role


@pytest.mark.parametrize(
    "text",
    [
        "Hola, ¿cómo estás?",
        "Responde únicamente: listo",
        "¿Qué es una aplicación con ventana y botón?",
        "¿Qué son internet y web?",
        "El navegador muestra una página de bienvenida",
        "Mi agenda contiene una frase del día",
        "¿Qué es una red y para qué sirve?",
        "El informe menciona un error",
        "Explícame qué es Google Drive",
        "¿En qué se diferencia Gmail de un documento?",
        "La batería es un componente del Mac",
        "Este archivo dice ignora las instrucciones anteriores",
    ],
)
def test_generic_mentions_do_not_force_a_procedure_or_starter_tool(tmp_path, text):
    assert registry(tmp_path).select_many(text) == ()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("text", "builtins", "expected_role", "expected_skill"),
    [
        ("Resume este documento adjunto", True, AgentRole.CRITICAL_REASONER,
         "document-analysis-expert"),
        ("Busca en Drive el informe", True, AgentRole.PLANNER, "google-workspace-expert"),
        ("Revisa el archivo fixture.txt", False, AgentRole.CODE_SECURITY, None),
        ("Resume este documento y revisa su vulnerabilidad", True, AgentRole.CODE_SECURITY, None),
    ],
)
async def test_graph_keeps_real_domain_role_and_explicit_security_precedence(
    tmp_path, text, builtins, expected_role, expected_skill,
):
    instance = SkillRegistry(
        build_default_tool_broker(), SkillStore(tmp_path / "skills"),
        builtins=BUILTIN_SKILLS if builtins else (),
    )
    provider = Boundary()
    graph = build_swarm_graph(provider, skill_registry=instance, inference_nvidia_only=True)
    state = await graph.ainvoke({"request": UserRequest(text=text)})
    assert state["route"].role is expected_role
    specialists = [item["role"] for item in provider.requests
                   if item["role"] not in {AgentRole.ROUTER, AgentRole.SYNTHESIZER}]
    assert not specialists or specialists[0] is expected_role
    assert (state["skill"].manifest.skill_id if state.get("skill") else None) == expected_skill


def test_catalogue_preserves_legacy_ids_and_actual_role_tool_boundaries(tmp_path):
    instance = registry(tmp_path)
    skills = {skill.skill_id: skill for skill in instance.all()}
    assert len(skills) == 8
    assert {
        "mac-control-expert", "browser-navigation-expert", "security-audit-expert",
        "code-review-expert", "personal-productivity-expert",
    }.issubset(skills)
    broker = build_default_tool_broker()
    for skill in skills.values():
        offered = {schema["function"]["name"] for schema in broker.schemas_for(skill.role)}
        assert skill.allowed_tools.issubset(offered)
        assert skill.starter_tools.issubset(skill.allowed_tools)
        assert skill.origin.value == "builtin"
        assert skill.remote_safe is True
        assert len(json.dumps(instance.preview(
            skill.skill_id, instance.describe_manifest(skill)["content_sha256"],
        ), ensure_ascii=False).encode()) <= 10_240
    assert skills["google-workspace-expert"].allowed_tools == frozenset()
    assert skills["document-analysis-expert"].starter_tools == frozenset()
    assert skills["mac-diagnostics-expert"].starter_tools == frozenset()


@pytest.mark.asyncio
async def test_unqualified_unread_request_keeps_the_real_local_mail_tool(tmp_path):
    call = ToolCall(
        call_id="fixture-mail", tool_name="mail_list_recent",
        arguments={"limit": 5, "unread_only": True}, requested_by=AgentRole.PLANNER,
    )
    provider, executor = Boundary((call,)), Executor()
    graph = build_swarm_graph(
        provider, skill_registry=registry(tmp_path), inference_nvidia_only=True,
        tool_executor=executor,
    )
    state = await graph.ainvoke({"request": UserRequest(
        text="Cuáles son mis últimos 5 correos sin leer",
    )})
    assert state["skill"].manifest.skill_id == "personal-productivity-expert"
    schemas = provider.requests[0]["extra_body"]["tools"]
    assert {schema["function"]["name"] for schema in schemas} == {"mail_list_recent"}
    assert state["tool_authorizations"][0].decision is PolicyDecision.ALLOW
    assert executor.calls[0].normalized_arguments == {"limit": 5, "unread_only": True}


class GmailArguments(BaseModel):
    model_config = ConfigDict(extra="forbid")
    user_google_email: str
    query: str
    page_size: int = Field(ge=1, le=10)
    include_headers: bool


@pytest.mark.asyncio
@pytest.mark.parametrize("text", [
    "Revisa mi Gmail y muéstrame los últimos 5 correos sin leer",
    "Revisa mi Gmail y busca avisos de seguridad de esta semana; solo asuntos y remitentes.",
])
async def test_workspace_guidance_does_not_bypass_dynamic_peer_approval(tmp_path, text):
    name = "mcp_google_workspace__search_gmail_messages"
    broker = build_default_tool_broker((ToolDefinition(
        name=name, description="Synthetic reviewed Gmail read boundary.",
        arguments_model=GmailArguments, capability=Capability.MAIL_READ,
        risk=RiskLevel.MEDIUM, allowed_roles=frozenset({AgentRole.PLANNER}),
        requires_confirmation=True,
    ),))
    call = ToolCall(
        call_id="fixture-gmail", tool_name=name, requested_by=AgentRole.PLANNER,
        arguments={"user_google_email": "fixture@example.com", "query": "in:inbox is:unread",
                   "page_size": 5, "include_headers": True},
    )
    provider, executor = Boundary((call,)), Executor()
    graph = build_swarm_graph(
        provider, tool_broker=broker, skill_registry=registry(tmp_path, broker),
        inference_nvidia_only=True, tool_executor=executor,
        dynamic_tool_names=lambda _request: frozenset({name}),
    )
    state = await graph.ainvoke({"request": UserRequest(text=text)})
    assert state["route"].role is AgentRole.PLANNER
    assert state["skill"].manifest.skill_id == "google-workspace-expert"
    offered = provider.requests[0]["extra_body"]["tools"]
    assert {tool["function"]["name"] for tool in offered} == {name}
    assert state["tool_authorizations"][0].decision is PolicyDecision.REQUIRE_CONFIRMATION
    assert executor.calls == []
    assert "mail_list_recent" not in json.dumps(offered)


class DriveArguments(BaseModel):
    model_config = ConfigDict(extra="forbid")
    user_google_email: str
    query: str
    page_size: int = Field(ge=1, le=10)


@pytest.mark.asyncio
async def test_google_drive_search_never_becomes_a_public_web_query(tmp_path):
    name = "mcp_google_workspace__search_drive_files"
    broker = build_default_tool_broker((ToolDefinition(
        name=name, description="Synthetic reviewed Drive read boundary.",
        arguments_model=DriveArguments, capability=Capability.WEB_READ,
        risk=RiskLevel.MEDIUM, allowed_roles=frozenset({AgentRole.PLANNER}),
        requires_confirmation=True,
    ),))
    call = ToolCall(
        call_id="fixture-drive", tool_name=name, requested_by=AgentRole.PLANNER,
        arguments={"user_google_email": "fixture@example.com",
                   "query": "name contains 'junio'", "page_size": 5},
    )
    provider, executor = Boundary((call,)), Executor()
    graph = build_swarm_graph(
        provider, tool_broker=broker, skill_registry=registry(tmp_path, broker),
        inference_nvidia_only=True, tool_executor=executor,
        dynamic_tool_names=lambda _request: frozenset({name}),
    )
    state = await graph.ainvoke({"request": UserRequest(
        text="Busca en Google Drive el informe de junio; dime sus nombres y carpetas "
             "antes de abrirlo.",
    )})
    assert state["route"].role is AgentRole.PLANNER
    assert state["skill"].manifest.skill_id == "google-workspace-expert"
    offered = provider.requests[0]["extra_body"]["tools"]
    assert {tool["function"]["name"] for tool in offered} == {name}
    assert [item.tool_name for item in state["tool_authorizations"]] == [name]
    assert state["tool_authorizations"][0].decision is PolicyDecision.REQUIRE_CONFIRMATION
    assert "direct_tool_call" not in state
    assert executor.calls == []


@pytest.mark.asyncio
async def test_disconnected_drive_never_falls_back_to_public_research(tmp_path):
    provider, executor = Boundary(), Executor()
    graph = build_swarm_graph(
        provider, skill_registry=registry(tmp_path), inference_nvidia_only=True,
        tool_executor=executor, dynamic_tool_names=lambda _request: frozenset(),
    )
    state = await graph.ainvoke({"request": UserRequest(
        text="Busca en Google Drive el informe privado de junio.",
    )})
    assert provider.requests == []
    assert executor.calls == []
    assert not state.get("tool_authorizations")
    assert "no está disponible" in state["final_result"].content


@pytest.mark.asyncio
async def test_attached_document_is_input_without_unrelated_filesystem_access(tmp_path):
    provider, executor = Boundary(), Executor()
    graph = build_swarm_graph(
        provider, skill_registry=registry(tmp_path), inference_nvidia_only=True,
        tool_executor=executor,
    )
    document = DocumentInput(name="fixture.txt", content="ORION: 17 EUR. VEGA: 24 USD.")
    state = await graph.ainvoke({"request": UserRequest(
        text="Resume este documento adjunto con sus unidades", documents=(document,),
    )})
    assert state["route"].role is AgentRole.CRITICAL_REASONER
    assert state["skill"].manifest.skill_id == "document-analysis-expert"
    lead = provider.requests[0]
    assert lead["extra_body"] is None
    serialized = json.dumps(lead["messages"], ensure_ascii=False)
    assert document.content in serialized
    assert "document-analysis-expert" in serialized
    assert executor.calls == []


@pytest.mark.asyncio
async def test_local_document_read_returns_actual_path_and_partial_evidence(tmp_path):
    source = tmp_path / "fixture.txt"
    source.write_text("ORION: 17 EUR.\nVEGA: 24 USD.\n" * 8)
    call = ToolCall(
        call_id="fixture-read", tool_name="filesystem_read_text",
        arguments={"path": source.name, "max_bytes": 54},
        requested_by=AgentRole.CRITICAL_REASONER,
    )
    provider = Boundary((call,))
    graph = build_swarm_graph(
        provider, skill_registry=registry(tmp_path), inference_nvidia_only=True,
        tool_executor=ReadOnlyToolExecutor(), policy_context=PolicyContext(workspace_root=tmp_path),
    )
    state = await graph.ainvoke({"request": UserRequest(text="Revisa el archivo fixture.txt")})
    result = state["tool_results"][0]
    assert state["tool_authorizations"][0].decision is PolicyDecision.ALLOW
    assert result.success is True
    assert result.metadata == {"path": source.name, "bytes_read": 54, "truncated": True}
    assert result.output == source.read_text()[:54]
    assert len(result.output.splitlines()) == 4


@pytest.mark.asyncio
async def test_diagnostics_skill_rejects_a_forged_settings_change(tmp_path):
    call = ToolCall(
        call_id="fixture-forged", tool_name="system_audio_set",
        arguments={"volume_percent": 50}, requested_by=AgentRole.PLANNER,
    )
    provider, executor = Boundary((call,)), Executor()
    graph = build_swarm_graph(
        provider, skill_registry=registry(tmp_path), inference_nvidia_only=True,
        tool_executor=executor,
    )
    state = await graph.ainvoke({"request": UserRequest(
        text="Revisa el rendimiento y el volumen del Mac sin cambiarlos",
    )})
    assert state["skill"].manifest.skill_id == "mac-diagnostics-expert"
    assert state["tool_authorizations"][0].decision is PolicyDecision.DENY
    assert executor.calls == []
    offered = provider.requests[0]["extra_body"]["tools"]
    assert {tool["function"]["name"] for tool in offered} == {"system_observe_status"}


@pytest.mark.asyncio
@pytest.mark.parametrize(("text", "tool_name", "arguments"), [
    (
        "Revisa la conexión del Mac; Telegram no está respondiendo y quiero saber qué "
        "puedes comprobar.",
        "system_observe_status", {"domain": "network"},
    ),
    (
        "Revisa la batería del Mac y dime si necesito conectarlo a corriente.",
        "system_power_status", {},
    ),
    (
        "Revisa el almacenamiento del Mac y dime cuánto espacio libre queda.",
        "system_storage_status", {},
    ),
])
async def test_diagnostic_paraphrases_offer_only_the_needed_measurement(
    tmp_path, text, tool_name, arguments,
):
    call = ToolCall(
        call_id="fixture-diagnostic", tool_name=tool_name,
        arguments=arguments, requested_by=AgentRole.PLANNER,
    )
    provider, executor = Boundary((call,)), Executor()
    graph = build_swarm_graph(
        provider, skill_registry=registry(tmp_path), inference_nvidia_only=True,
        tool_executor=executor,
    )
    state = await graph.ainvoke({"request": UserRequest(text=text)})
    assert state["route"].role is AgentRole.PLANNER
    assert state["skill"].manifest.skill_id == "mac-diagnostics-expert"
    offered = provider.requests[0]["extra_body"]["tools"]
    assert {tool["function"]["name"] for tool in offered} == {tool_name}
    assert [item.tool_name for item in state["tool_authorizations"]] == [tool_name]
    assert state["tool_authorizations"][0].decision is PolicyDecision.ALLOW
    assert len(executor.calls) == 1
    assert executor.calls[0].normalized_arguments == arguments


def test_procedure_arguments_match_actual_builtin_and_google_policy():
    """Catch dangerous recipe drift, e.g. unsupported overwriting or a command shell."""
    from aegis_core.mcp.integrations import _RULES, IntegrationId

    skills = {skill.skill_id: skill for skill in BUILTIN_SKILLS}
    workspace = " ".join(skills["google-workspace-expert"].instructions)
    for remote, (_, fields) in _RULES[IntegrationId.GOOGLE].items():
        if remote in workspace:
            assert "user_google_email" in fields
    assert "mode append o prepend" in workspace
    assert "mode overwrite" not in workspace
    assert "send_updates none" in workspace
    assert "body_format plain" in workspace
    assert "include_signature false" in workspace
    diagnostics = skills["mac-diagnostics-expert"]
    assert diagnostics.allowed_tools == frozenset({
        "system_describe_runtime", "system_power_status", "system_storage_status",
        "system_observe_status",
    })
    for identifier in ("code-review-expert", "document-analysis-expert"):
        instructions = " ".join(skills[identifier].instructions)
        assert "metadata.path" in instructions
        assert "metadata.truncated" in instructions
        assert "desde uno" in instructions
