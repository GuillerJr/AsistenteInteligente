"""NVIDIA-only prompts and local continuity use bounded, content-approved guidance."""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime
from uuid import uuid4

import pytest
from test_skill_policy import approve, draft, registry

from aegis_core.contracts import (
    AgentResult,
    AgentRole,
    DocumentInput,
    InputModality,
    PolicyDecision,
    ToolCall,
    ToolExecutionResult,
    UserRequest,
)
from aegis_core.engineering import ENGINEERING_SURFACE_METADATA
from aegis_core.memory.contracts import ConversationRole, ConversationTurn
from aegis_core.orchestration.graph import build_nvidia_swarm_graph as build_swarm_graph
from aegis_core.skills import SkillError
from aegis_core.skills.contracts import SkillManifest, SkillOrigin


class NvidiaBoundary:
    def __init__(self, semantic='{"skill_ids":["owner-procedure"]}', calls=()):
        self.semantic = semantic
        self.calls = calls
        self.requests = []

    async def complete_nvidia_only(self, *, role, **kwargs):
        self.requests.append({"role": role, **kwargs})
        if role is AgentRole.ROUTER and isinstance(self.semantic, BaseException):
            raise self.semantic
        return AgentResult(
            role=role,
            model_id="synthetic/nvidia",
            content=self.semantic if role is AgentRole.ROUTER else "listo",
            tool_calls=self.calls if role is AgentRole.PLANNER else (),
        )

    async def complete(self, **_kwargs):
        raise AssertionError("NVIDIA-only requests must not use another provider")


def history(*contents):
    conversation_id = uuid4()
    return tuple(
        ConversationTurn(
            conversation_id=conversation_id,
            sequence=index,
            role=ConversationRole.USER,
            content=content,
            created_at=datetime.now(UTC),
            content_sha256=ConversationTurn.digest_content(content),
        )
        for index, content in enumerate(contents, 1)
    )


def reference(call):
    return next(
        json.loads(message["content"].split("\n", 1)[1])
        for message in call["messages"]
        if isinstance(message.get("content"), str)
        and message["content"].startswith("Datos de referencia;")
    )


def test_normalization_is_accent_and_punctuation_safe_without_substring_matches(tmp_path):
    instance = registry(tmp_path, builtins=())
    instance.learn(
        draft(
            trigger_phrases=("revisión profunda",),
            trigger_terms=frozenset({"revisión", "revision"}),
        )
    )
    selected = instance.select("REVISIÓN—PROFUNDA, por favor")
    assert selected is not None
    assert selected.manifest.skill_id == "owner-procedure"
    assert instance.select("previsión superprofunda") is None
    assert instance.select("revisión") is None  # Accent aliases count as one conceptual term.


def test_multiple_skills_are_bounded_and_role_compatible(tmp_path):
    instance = registry(tmp_path, builtins=())
    for index in range(4):
        instance.learn(draft(f"procedure-{index}", priority=70))
    instance.learn(draft("security-procedure", role=AgentRole.CODE_SECURITY, priority=1))
    selected = instance.select_many("procedimiento atlas")
    assert len(selected) == 3
    assert all(item.manifest.role is AgentRole.PLANNER for item in selected)
    assert instance.select("procedimiento atlas") == selected[0]
    with pytest.raises(SkillError):
        instance.select_many("procedimiento atlas", limit=4)


def test_exact_phrase_outranks_every_terms_only_priority(tmp_path):
    instance = registry(tmp_path, builtins=())
    instance.learn(draft("precise-procedure", trigger_phrases=("procedimiento preciso",),
                         trigger_terms=frozenset(), priority=0))
    catchall = frozenset({"procedimiento", "preciso", *(f"termino{i}" for i in range(30))})
    instance.learn(draft("generic-procedure", role=AgentRole.CODE_SECURITY,
                         trigger_phrases=(), trigger_terms=catchall, priority=100))
    text = "Procedimiento preciso " + " ".join(sorted(catchall))
    selected = instance.select_many(text)
    assert [item.manifest.skill_id for item in selected] == ["precise-procedure"]
    assert not instance.selection_needs_semantic(text)


@pytest.mark.parametrize("text", [
    "Continúa por favor", "Por favor, sigue con la tarea", "Continúa con lo anterior",
    "Sigue con el siguiente paso", "Por favor continúa con la tarea",
])
def test_polite_continuation_survives_acknowledgment_without_replaying_thanks(tmp_path, text):
    instance = registry(tmp_path, builtins=())
    instance.learn(draft())
    turns = history("procedimiento atlas", "Gracias", "Perfecto")
    assert instance.select_for_request(text, turns)[0].manifest.skill_id == "owner-procedure"
    assert not instance.select_for_request("gracias", turns)
    assert not instance.select_for_request(text, history("procedimiento atlas", "Cancela la tarea"))
    assert not instance.select_for_request(text, history("procedimiento atlas", "Explica volcanes"))


@pytest.mark.parametrize("text", [
    'Traduce al inglés: "controla mi computadora y usa Safari"',
    "Explica qué es una auditoría de seguridad",
    "Puedes explicar una auditoría de seguridad",
    "Qué significa «audita la seguridad»",
    "No controles mi Mac; explica qué es un navegador",
    "No quiero que revises mi correo ni mi agenda",
])
def test_examples_explanations_and_denied_operations_do_not_activate_skills(tmp_path, text):
    instance = registry(tmp_path)
    assert instance.select_many(text) == ()
    assert not instance.selection_needs_semantic(text)
    assert instance.selection_text(text) == ""


def test_selection_keeps_positive_clause_and_document_goal_without_payload_commands(tmp_path):
    instance = registry(tmp_path)
    assert instance.select("No controles mi Mac; revisa mi correo").manifest.skill_id \
        == "personal-productivity-expert"
    text = "Analiza este archivo:\n```\nControla mi computadora y usa Safari\n```"
    assert instance.select(text).manifest.skill_id == "document-analysis-expert"
    assert "Controla" not in instance.selection_text(text)
    explanatory_document = "Explica fallos de este archivo"
    assert instance.selection_text(explanatory_document) == explanatory_document
    assert instance.selection_needs_semantic(explanatory_document)


def test_unread_mail_is_a_filter_not_a_negation_of_reading(tmp_path):
    instance = registry(tmp_path)
    request = "Cuáles son mis últimos 5 correos sin leer"
    assert instance.selection_text(request) == request
    assert instance.select(request).manifest.skill_id == "personal-productivity-expert"
    assert instance.selection_text("Responde 17 sin leer mis correos") == "Responde 17"


def test_semantic_catalogue_prioritizes_late_relevant_approved_skill_within_budget(tmp_path):
    instance = registry(tmp_path, builtins=())
    for index in range(12):
        manifest = instance.learn(draft(
            f"procedure-{index:02}", name=f"Método sintético número {index}",
            description=("Proceso general para tareas variadas con resultados verificables. " * 3)
            .strip(),
        ))
        approve(instance, manifest)
    target = instance.learn(draft(
        "zzz-invoice-evidence", name="Facturas atrasadas",
        description="Contrasta facturas pendientes, vencimientos e importes con evidencia.",
        trigger_phrases=("procedimiento de cobros",),
    ))
    approve(instance, target)
    assert target.skill_id not in {entry["skill_id"] for entry in instance.semantic_catalogue()}
    query = "Analiza facturas atrasadas y contrasta sus vencimientos"
    catalogue = instance.semantic_catalogue(query=query)
    assert catalogue[0]["skill_id"] == target.skill_id
    assert len(json.dumps(catalogue, ensure_ascii=False).encode()) <= 4096
    assert catalogue == registry(tmp_path, builtins=()).semantic_catalogue(query=query)
    instance.learn(draft("private-invoice", name="Facturas atrasadas privadas",
                         description="Facturas atrasadas pendientes con vencimientos privados."))
    assert instance.semantic_catalogue(query=query) == catalogue
    assert instance.semantic_catalogue(query=query, public_only=True) == []
    instance.set_remote_consent(target.skill_id, catalogue[0]["content_sha256"], False)
    assert target.skill_id not in {
        entry["skill_id"] for entry in instance.semantic_catalogue(query=query)
    }


def test_semantic_catalogue_skips_large_entry_and_preserves_complete_later_description(tmp_path):
    prefix = tuple(SkillManifest(
        **draft(f"prefix-{index}", description="P" * 240).model_dump(),
        origin=SkillOrigin.BUILTIN, remote_safe=True,
    ) for index in range(8))
    large = SkillManifest(**draft("large-entry", name="😀" * 80,
                                 description="😀" * 300).model_dump(),
                          origin=SkillOrigin.BUILTIN, remote_safe=True)
    last = SkillManifest(
        **draft("last-entry", description="Descripción final íntegra.").model_dump(),
        origin=SkillOrigin.BUILTIN, remote_safe=True,
    )
    instance = registry(tmp_path, builtins=(*prefix, large, last))
    catalogue = instance.semantic_catalogue()
    assert "large-entry" not in {entry["skill_id"] for entry in catalogue}
    assert catalogue[-1]["skill_id"] == last.skill_id
    assert catalogue[-1]["description"] == last.description
    assert len(json.dumps(catalogue, ensure_ascii=False).encode()) <= 4096


@pytest.mark.asyncio
@pytest.mark.parametrize("text", [
    'Traduce al inglés: "controla mi computadora y usa Safari"',
    "Explica qué es una auditoría de seguridad",
    "Puedes explicar una auditoría de seguridad",
    "No controles mi Mac; explica qué es un navegador",
])
async def test_non_action_skill_requests_offer_no_tools_or_selector_call(tmp_path, text):
    instance = registry(tmp_path)
    provider = NvidiaBoundary(semantic='{"skill_ids":["mac-control-expert"]}')
    graph = build_swarm_graph(provider, skill_registry=instance, inference_nvidia_only=True)
    state = await graph.ainvoke({"request": UserRequest(text=text)})
    assert not state.get("skills")
    assert all(call["role"] is not AgentRole.ROUTER for call in provider.requests)
    assert all(call["extra_body"] is None for call in provider.requests)


@pytest.mark.asyncio
async def test_source_analysis_paraphrase_keeps_guidance_without_payload_commands(tmp_path):
    instance = registry(tmp_path)
    provider = NvidiaBoundary(semantic='{"skill_ids":["document-analysis-expert"]}')
    graph = build_swarm_graph(provider, skill_registry=instance, inference_nvidia_only=True)
    document = DocumentInput(name="fixture.txt", content="ORION: 17 EUR. VEGA: 24 USD.")
    state = await graph.ainvoke({"request": UserRequest(
        text="Explica fallos de este archivo adjunto", documents=(document,),
    )})
    assert state["skills"][0].manifest.skill_id == "document-analysis-expert"
    assert provider.requests[0]["role"] is AgentRole.ROUTER
    lead = next(call for call in provider.requests if call["role"] is AgentRole.CRITICAL_REASONER)
    assert lead["extra_body"] is None
    assert document.content in json.dumps(lead["messages"], ensure_ascii=False)


def test_continuity_uses_latest_user_task_and_never_older_topic_or_assistant_claim(tmp_path):
    instance = registry(tmp_path, builtins=())
    instance.learn(draft())
    assert instance.select_for_request("continúa", history("procedimiento atlas", "hazlo"))
    assert not instance.select_for_request(
        "continúa", history("procedimiento atlas", "Explica volcanes")
    )
    assert not instance.select_for_request("solo responde hola", history("procedimiento atlas"))
    assert not instance.select_for_request(
        "continúa", ({"role": "assistant", "content": "procedimiento atlas"},)
    )
    assert not instance.select_for_request("continúa", history("procedimiento atlas", *["si"] * 8))
    restarted = registry(tmp_path, builtins=())
    assert restarted.select_for_request("continúa", history("procedimiento atlas"))
    manifest = restarted.all()[0]
    restarted.set_enabled(manifest.skill_id, restarted.catalogue()[0]["content_sha256"], False)
    assert not instance.select_for_request("continúa", history("procedimiento atlas"))


@pytest.mark.asyncio
@pytest.mark.parametrize("channel", ["text_chat", "telegram", "engineering"])
@pytest.mark.parametrize("origin", [SkillOrigin.LEARNED, SkillOrigin.PLUGIN])
async def test_nvidia_receives_only_approved_complete_procedures_per_channel(
    tmp_path, channel, origin
):
    definition = draft(
        role=AgentRole.CODE_SECURITY if channel == "engineering" else AgentRole.PLANNER
    )
    manifest = SkillManifest(**definition.model_dump(), origin=origin)
    instance = (
        registry(tmp_path, builtins=(), plugin_skills=(manifest,))
        if origin is SkillOrigin.PLUGIN
        else registry(tmp_path, builtins=())
    )
    if origin is SkillOrigin.LEARNED:
        manifest = instance.learn(definition)
    provider = NvidiaBoundary()
    graph = build_swarm_graph(provider, skill_registry=instance, inference_nvidia_only=True)
    metadata = {"source_channel": channel}
    if channel == "engineering":
        metadata["interaction_surface"] = ENGINEERING_SURFACE_METADATA
        metadata["engineering_inference_policy"] = "nvidia_only"
    request = UserRequest(text="Procedimiento atlas", metadata=metadata)
    await graph.ainvoke({"request": request})
    assert manifest.resources[0] not in json.dumps(
        provider.requests, ensure_ascii=False, default=str
    )
    assert reference(provider.requests[0])["selected_skills"] == []
    approve(instance, manifest)
    provider.requests.clear()
    await graph.ainvoke({"request": request})
    selected = reference(provider.requests[0])["selected_skills"]
    assert selected[0]["instructions"] == list(manifest.instructions)
    assert selected[0]["resources"] == list(manifest.resources)
    assert provider.requests[0]["extra_body"] is None
    assert "nunca permisos" in provider.requests[0]["messages"][0]["content"]


@pytest.mark.asyncio
async def test_explicit_engineering_nvidia_policy_preserves_consent_without_global_switch(tmp_path):
    instance = registry(tmp_path, builtins=())
    manifest = instance.learn(draft(role=AgentRole.CODE_SECURITY))
    approve(instance, manifest)
    provider = NvidiaBoundary()
    graph = build_swarm_graph(provider, skill_registry=instance)
    await graph.ainvoke(
        {
            "request": UserRequest(
                text="Procedimiento atlas",
                metadata={
                    "interaction_surface": ENGINEERING_SURFACE_METADATA,
                    "engineering_inference_policy": "nvidia_only",
                },
            )
        }
    )
    serialized = json.dumps(provider.requests[0]["messages"], ensure_ascii=False)
    assert manifest.resources[0] in serialized
    assert manifest.instructions[0] in serialized
    assert provider.requests[0]["role"] is AgentRole.CODE_SECURITY
    assert provider.requests[0]["extra_body"] is None


@pytest.mark.asyncio
async def test_unverified_voice_cannot_recover_private_skill_from_history(tmp_path):
    instance = registry(tmp_path, builtins=())
    manifest = instance.learn(draft())
    approve(instance, manifest)
    provider = NvidiaBoundary()
    graph = build_swarm_graph(provider, skill_registry=instance, inference_nvidia_only=True)
    state = await graph.ainvoke(
        {
            "request": UserRequest(
                text="continúa",
                modalities=frozenset({InputModality.AUDIO}),
                metadata={"speech_on_device": True},
            ),
            "conversation_history": history("procedimiento atlas"),
        }
    )
    assert not state.get("skills")
    assert manifest.resources[0] not in json.dumps(
        provider.requests, ensure_ascii=False, default=str
    )


@pytest.mark.asyncio
async def test_fuzzy_selector_sees_only_approved_catalogue_and_redacted_current_request(tmp_path):
    instance = registry(tmp_path, builtins=())
    approved = instance.learn(draft())
    instance.learn(draft("hidden-procedure", name="Nombre privado no consentido"))
    approve(instance, approved)
    provider = NvidiaBoundary()
    graph = build_swarm_graph(provider, skill_registry=instance, inference_nvidia_only=True)
    text = "Analiza las entregas atrasadas para ordenarlas por urgencia"
    request = UserRequest(text=text + " nvapi" + "-synthetic-secret-not-real")
    state = await graph.ainvoke({"request": request})
    router = provider.requests[0]
    assert router["role"] is AgentRole.ROUTER
    assert router["structured_time_budget"] == 1.5
    assert state["skills"][0].manifest.skill_id == approved.skill_id
    serialized = json.dumps(router["messages"], ensure_ascii=False)
    assert "Nombre privado" not in serialized
    assert "synthetic-secret-not-real" not in serialized
    assert approved.resources[0] not in serialized
    assert "resources" not in router["messages"][1]["content"]
    assert "historial privado" not in serialized
    assert len(router["messages"][1]["content"].encode()) < 4600
    provider.requests.clear()
    await graph.ainvoke({"request": request})
    assert all(call["role"] is not AgentRole.ROUTER for call in provider.requests)
    instance.set_remote_consent(approved.skill_id, instance.catalogue()[1]["content_sha256"], False)
    provider.requests.clear()
    state = await graph.ainvoke({"request": request})
    assert not state.get("skills")
    assert all(call["role"] is not AgentRole.ROUTER for call in provider.requests)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "semantic",
    [
        '{"skill_ids":["invented-skill"]}',
        '{"skill_ids":["owner-procedure","owner-procedure"]}',
        '{"skill_ids":["owner-procedure"],"grant":true}',
        '{"skill_ids":true}',
        "[" * 1000 + "0" + "]" * 1000,
        '{"skill_ids":["owner-procedure"]}' + " " * 2048,
        TimeoutError("synthetic deadline"),
    ],
    ids=["unknown", "duplicate", "extra_key", "wrong_type", "deep", "oversize", "timeout"],
)
async def test_malformed_or_timed_out_semantic_selection_fails_closed(tmp_path, semantic):
    instance = registry(tmp_path, builtins=())
    manifest = instance.learn(draft())
    approve(instance, manifest)
    provider = NvidiaBoundary(semantic=semantic)
    graph = build_swarm_graph(provider, skill_registry=instance, inference_nvidia_only=True)
    state = await graph.ainvoke(
        {"request": UserRequest(text="Analiza las entregas atrasadas para ordenarlas por urgencia")}
    )
    assert not state.get("skills")
    assert reference(provider.requests[-1])["selected_skills"] == []
    assert manifest.resources[0] not in json.dumps(
        provider.requests, ensure_ascii=False, default=str
    )


@pytest.mark.asyncio
async def test_clear_match_and_independent_chat_do_not_pay_semantic_call(tmp_path):
    instance = registry(tmp_path, builtins=())
    manifest = instance.learn(draft())
    approve(instance, manifest)
    provider = NvidiaBoundary()
    graph = build_swarm_graph(provider, skill_registry=instance, inference_nvidia_only=True)
    for text in ("Procedimiento atlas", "Qué es un volcán"):
        provider.requests.clear()
        await graph.ainvoke({"request": UserRequest(text=text)})
        assert all(call["role"] is not AgentRole.ROUTER for call in provider.requests)


@pytest.mark.asyncio
async def test_ambiguous_roles_use_closed_semantic_selection_without_expanding_authority(tmp_path):
    instance = registry(tmp_path, builtins=())
    for identifier, role in (
        ("owner-procedure", AgentRole.PLANNER),
        ("security-procedure", AgentRole.CODE_SECURITY),
    ):
        manifest = instance.learn(
            draft(
                identifier,
                role=role,
                trigger_phrases=(),
                trigger_terms=frozenset({"entregas", "atrasadas"}),
            )
        )
        approve(instance, manifest)
    provider = NvidiaBoundary(semantic='{"skill_ids":["owner-procedure","security-procedure"]}')
    graph = build_swarm_graph(provider, skill_registry=instance, inference_nvidia_only=True)
    state = await graph.ainvoke({"request": UserRequest(text="Analiza entregas atrasadas")})
    assert provider.requests[0]["role"] is AgentRole.ROUTER
    assert len(state["skills"]) == 1  # Invalid incompatible selection retains lexical route.
    assert provider.requests[1]["extra_body"] is None


class SyntheticExecutor:
    async def execute_async(self, authorization, _context):
        return ToolExecutionResult(
            call_id=authorization.call_id,
            tool_name=authorization.tool_name,
            success=True,
            output="Synthetic source verified.",
        )


@pytest.mark.asyncio
async def test_multiple_skill_tool_scope_is_used_both_for_proposal_and_authorization(tmp_path):
    instance = registry(tmp_path, builtins=())
    for identifier, tool, priority in (
        ("primary-procedure", "web_research", 90),
        ("secondary-procedure", "web_fetch", 80),
    ):
        instance.learn(
            draft(
                identifier,
                allowed_tools=frozenset({tool}),
                starter_tools=frozenset({tool}),
                priority=priority,
            )
        )
    call = ToolCall(
        call_id="synthetic-fetch",
        tool_name="web_fetch",
        arguments={"url": "https://example.com"},
        requested_by=AgentRole.PLANNER,
    )
    provider = NvidiaBoundary(calls=(call,))
    graph = build_swarm_graph(
        provider,
        skill_registry=instance,
        inference_nvidia_only=True,
        tool_executor=SyntheticExecutor(),
    )
    state = await graph.ainvoke({"request": UserRequest(text="Procedimiento atlas")})
    offered = provider.requests[0]["extra_body"]["tools"]
    assert {tool["function"]["name"] for tool in offered} == {"web_fetch", "web_research"}
    assert state["tool_authorizations"][0].decision is PolicyDecision.ALLOW
    assert state["tool_results"][0].success


@pytest.mark.asyncio
async def test_selector_cancellation_is_not_swallowed(tmp_path):
    instance = registry(tmp_path, builtins=())
    approve(instance, instance.learn(draft()))
    provider = NvidiaBoundary(semantic=asyncio.CancelledError())
    graph = build_swarm_graph(provider, skill_registry=instance, inference_nvidia_only=True)
    with pytest.raises(asyncio.CancelledError):
        await graph.ainvoke(
            {
                "request": UserRequest(
                    text="Analiza las entregas atrasadas para ordenarlas por urgencia"
                )
            }
        )


@pytest.mark.asyncio
async def test_semantic_continuity_reuses_current_chat_query_and_rechecks_after_restart(tmp_path):
    instance = registry(tmp_path, builtins=())
    manifest = instance.learn(draft())
    approve(instance, manifest)
    text = "Analiza las entregas atrasadas para ordenarlas por urgencia"
    provider = NvidiaBoundary()
    graph = build_swarm_graph(provider, skill_registry=instance, inference_nvidia_only=True)
    first = await graph.ainvoke({"request": UserRequest(text=text)})
    assert first["skills"]
    provider.requests.clear()
    next_state = await graph.ainvoke(
        {"request": UserRequest(text="continúa"), "conversation_history": history(text)}
    )
    assert next_state["skills"][0].manifest.skill_id == manifest.skill_id
    assert all(call["role"] is not AgentRole.ROUTER for call in provider.requests)
    assert reference(provider.requests[0])["selected_skills"][0]["resources"] == list(
        manifest.resources
    )
    restarted = build_swarm_graph(
        provider, skill_registry=registry(tmp_path, builtins=()), inference_nvidia_only=True
    )
    provider.requests.clear()
    state = await restarted.ainvoke(
        {"request": UserRequest(text="continúa"), "conversation_history": history(text)}
    )
    assert state["skills"]
    assert provider.requests[0]["role"] is AgentRole.ROUTER
    provider.requests.clear()
    state = await graph.ainvoke(
        {
            "request": UserRequest(text="continúa"),
            "conversation_history": history(text, "Explica los volcanes"),
        }
    )
    assert not state.get("skills")
    assert manifest.resources[0] not in json.dumps(
        provider.requests, ensure_ascii=False, default=str
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "text", ["Procedimiento atlas", "Analiza las entregas atrasadas para ordenarlas por urgencia"]
)
async def test_unknown_voice_cannot_expose_private_skill_via_trigger_or_semantic_catalogue(
    tmp_path, text
):
    instance = registry(tmp_path)
    manifest = instance.learn(draft(name="Nombre privado no consentido a desconocidos"))
    approve(instance, manifest)
    provider = NvidiaBoundary()
    graph = build_swarm_graph(provider, skill_registry=instance, inference_nvidia_only=True)
    state = await graph.ainvoke(
        {
            "request": UserRequest(
                text=text,
                modalities=frozenset({InputModality.AUDIO}),
                metadata={"speech_on_device": True},
            )
        }
    )
    assert not state.get("skills")
    serialized = json.dumps(provider.requests, ensure_ascii=False, default=str)
    assert manifest.resources[0] not in serialized
    assert manifest.name not in serialized
    for call in provider.requests:
        if call["role"] is AgentRole.ROUTER:
            assert manifest.skill_id not in json.dumps(call["messages"])
