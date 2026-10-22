"""Procedures guide the final evidence answer, with current content consent."""

from __future__ import annotations

import json

import httpx
import pytest
from historical.orchestration.graph import (
    _build_historical_swarm_graph as build_historical_swarm_graph,
)

from aegis_core.brain.nvidia_client import NvidiaBrainClient
from aegis_core.config import Settings
from aegis_core.contracts import AgentResult, AgentRole, PolicyDecision, ToolCall, UserRequest
from aegis_core.engineering import ENGINEERING_SURFACE_METADATA
from aegis_core.orchestration.graph import build_nvidia_swarm_graph
from aegis_core.orchestration.nvidia_dialogue import nvidia_messages
from aegis_core.providers.nvidia import NvidiaGlobalCooldown, NvidiaNimClient
from aegis_core.skills import SkillDraft, SkillRegistry, SkillStore, skill_content_sha256
from aegis_core.tools.defaults import build_default_tool_broker, default_policy_context
from aegis_core.tools.execution import ReadOnlyToolExecutor


class EvidenceProvider:
    def __init__(self, *, echo_resource=False):
        self.requests = []
        self.echo_resource = echo_resource

    async def complete_nvidia_only(self, *, role, **kwargs):
        self.requests.append({"role": role, **kwargs})
        return AgentResult(
            role=role,
            model_id="synthetic/nvidia-evidence",
            content=("Referencia de procedimiento MARIPOSA-341."
                     if self.echo_resource and role is AgentRole.CODE_SECURITY else
                     "Resultado basado en el archivo verificado."),
            tool_calls=(ToolCall(
                call_id="verified-file-read",
                tool_name="filesystem_read_text",
                arguments={"path": "fixture.py"},
                requested_by=role,
            ),) if role is AgentRole.CODE_SECURITY else (),
        )

    async def complete(self, **_kwargs):
        raise AssertionError("Evidence must use NVIDIA exclusively")


def frame(call):
    content = next(
        message["content"] for message in call["messages"]
        if message["role"] == "user"
        and isinstance(message.get("content"), str)
        and message["content"].startswith(("Datos de referencia;", "Contexto de referencia;"))
    )
    return json.loads(content.split("\n", 1)[1])


@pytest.mark.asyncio
@pytest.mark.parametrize("nvidia_only", [True, False], ids=["all-surfaces", "engineering-policy"])
@pytest.mark.parametrize("change", [None, "revoke", "edit", "disable", "remove"])
async def test_synthesis_revalidates_complete_skill_after_real_file_read(
    tmp_path, nvidia_only, change
):
    broker = build_default_tool_broker()
    instance = SkillRegistry(broker, SkillStore(tmp_path / "skills"), builtins=())
    draft = SkillDraft(
        skill_id="evidence-review",
        name="Revisión de evidencia",
        description="Relaciona afirmaciones con líneas de la fuente sintética solicitada.",
        role=AgentRole.CODE_SECURITY,
        trigger_phrases=("revisa este código",),
        instructions=(
            "Conserva los valores vigentes y cita las líneas de la corrección explícita.",
        ),
        resources=("Referencia sintética exclusiva MARIPOSA-341.",),
        allowed_tools=frozenset({"filesystem_read_text"}),
        starter_tools=frozenset({"filesystem_read_text"}),
    )
    manifest = instance.learn(draft)
    digest = skill_content_sha256(manifest)
    instance.set_remote_consent(manifest.skill_id, digest, True)
    source = "previous = 38\ncorrected = 41\n"
    (tmp_path / "fixture.py").write_text(source, encoding="utf-8")

    class Executor(ReadOnlyToolExecutor):
        async def execute_async(self, authorization, context):
            result = await super().execute_async(authorization, context)
            assert result.success
            if change == "revoke":
                instance.set_remote_consent(manifest.skill_id, digest, False)
            elif change == "edit":
                instance.learn(draft.model_copy(update={
                    "instructions": ("La versión nueva aún no está autorizada para NVIDIA.",),
                }))
            elif change == "disable":
                instance.set_enabled(manifest.skill_id, digest, False)
            elif change == "remove":
                instance.forget(manifest.skill_id)
            return result

    provider = EvidenceProvider(echo_resource=change is not None)
    builder = build_nvidia_swarm_graph if nvidia_only else build_historical_swarm_graph
    graph = builder(
        provider, tool_broker=broker, skill_registry=instance,
        policy_context=default_policy_context(tmp_path), tool_executor=Executor(),
        inference_nvidia_only=nvidia_only,
    )
    metadata = {} if nvidia_only else {
        "interaction_surface": ENGINEERING_SURFACE_METADATA,
        "engineering_inference_policy": "nvidia_only",
    }
    state = await graph.ainvoke({"request": UserRequest(
        text="Revisa este código en fixture.py y cita la corrección vigente.", metadata=metadata,
    )})
    assert state["tool_authorizations"][0].decision is PolicyDecision.ALLOW
    assert state["tool_results"][0].output == source
    assert [call["role"] for call in provider.requests] == [
        AgentRole.CODE_SECURITY, AgentRole.SYNTHESIZER,
    ]
    planned = frame(provider.requests[0])["selected_skills"]
    assert planned[0]["resources"] == list(manifest.resources)
    final = provider.requests[-1]
    reference = frame(final)
    assert reference["tool_results"][0]["output"] == source
    assert final.get("extra_body") is None  # Answer generation cannot execute tools.
    if change is None:
        assert reference["selected_skills"][0]["instructions"] == list(manifest.instructions)
        assert reference["selected_skills"][0]["resources"] == list(manifest.resources)
        assert json.dumps(final, ensure_ascii=False, default=str).count("MARIPOSA-341") == 1
    else:
        assert reference["selected_skills"] == []
        assert "MARIPOSA-341" not in json.dumps(final, ensure_ascii=False, default=str)


@pytest.mark.asyncio
async def test_unshared_procedure_does_not_discard_an_advisory_answer(tmp_path):
    broker = build_default_tool_broker()
    instance = SkillRegistry(broker, SkillStore(tmp_path / "skills"), builtins=())
    instance.learn(SkillDraft(
        skill_id="evidence-review",
        name="Revisión de evidencia",
        description="Procedimiento personal aún sin consentimiento remoto.",
        role=AgentRole.CODE_SECURITY,
        trigger_phrases=("revisa este código",),
        instructions=("Referencia privada sintética nunca enviada ZEPHYR-919.",),
        allowed_tools=frozenset({"filesystem_read_text"}),
        starter_tools=frozenset({"filesystem_read_text"}),
    ))
    (tmp_path / "fixture.py").write_text("verified = 41\n", encoding="utf-8")
    provider = EvidenceProvider()
    graph = build_nvidia_swarm_graph(
        provider, tool_broker=broker, skill_registry=instance,
        policy_context=default_policy_context(tmp_path), tool_executor=ReadOnlyToolExecutor(),
        inference_nvidia_only=True,
    )
    await graph.ainvoke({"request": UserRequest(text="Revisa este código en fixture.py.")})
    assert frame(provider.requests[0])["selected_skills"] == []
    final = frame(provider.requests[-1])
    assert final["selected_skills"] == []
    assert final["analyses"] == [{
        "role": "code_security", "content": "Resultado basado en el archivo verificado.",
    }]
    assert "ZEPHYR-919" not in json.dumps(provider.requests, ensure_ascii=False, default=str)


@pytest.mark.asyncio
async def test_nvidia_wire_preserves_evidence_after_redacted_assignment(tmp_path):
    source = "token=synthetic-secret\nobserved=41\n"
    reference = {"tool_results": [{
        "tool_name": "filesystem_read_text", "success": True, "output": source,
        "metadata": {"path": "fixture.txt"},
    }]}
    payloads = []

    async def endpoint(request):
        payloads.append(json.loads(request.content))
        return httpx.Response(200, json={"choices": [{
            "message": {"content": '{"observed":41}'}, "finish_reason": "stop",
        }]})

    async with NvidiaNimClient(
        Settings(model_preferences_path=tmp_path / "models.json"), lambda: "synthetic",
        transport=httpx.MockTransport(endpoint), cooldown=NvidiaGlobalCooldown(),
    ) as provider:
        result = await NvidiaBrainClient(provider).complete_nvidia_only(
            role=AgentRole.SYNTHESIZER, evidence_role=AgentRole.CRITICAL_REASONER,
            extra_body={"response_format": {"type": "json_object"}},
            messages=nvidia_messages(
                instruction="Usa evidencia", request="Devuelve JSON con observed.",
                history=(), reference=reference,
            ),
        )
    assert result.content == '{"observed":41}'
    evidence = frame(payloads[0])["tool_results"][0]
    assert evidence["output"] == "token=[REDACTED_SECRET]\nobserved=41\n"
    assert evidence["source_lines"] == [
        {"line": 1, "text": "token=[REDACTED_SECRET]"},
        {"line": 2, "text": "observed=41"},
    ]
    assert "synthetic-secret" not in json.dumps(payloads)
    assert reference["tool_results"][0]["output"] == source
