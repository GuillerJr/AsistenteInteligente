"""Independent guards for closed expert answers after an authorized source read."""

from __future__ import annotations

import asyncio
import json
from dataclasses import replace

import httpx
import pytest
from historical.orchestration.graph import build_swarm_graph

from aegis_core.brain.nvidia_client import NvidiaBrainClient
from aegis_core.config import Settings
from aegis_core.contracts import (
    AgentResult,
    AgentRole,
    PolicyDecision,
    ToolCall,
    ToolExecutionResult,
    UserRequest,
)
from aegis_core.providers.base import IncompleteModelResponseError
from aegis_core.providers.nvidia import NvidiaGlobalCooldown, NvidiaNimClient
from aegis_core.settings_store import (
    ModelPreferences,
    RoleSelection,
    SelectableModel,
    default_model_preferences,
    write_private_json,
)
from aegis_core.skills import SkillRegistry, SkillStore
from aegis_core.tools.confirmations import OneTimeConfirmationStore
from aegis_core.tools.defaults import build_default_tool_broker, default_policy_context
from aegis_core.tools.execution import ReadOnlyToolExecutor

LIGHTNING = "nvidia/nemotron-3.5-lightning-30b-a3b"


class GraphBoundary:
    def __init__(self):
        self.calls = []

    async def complete(self, *, role, **kwargs):
        return await self._answer(role, kwargs)

    async def complete_nvidia_only(self, *, role, **kwargs):
        return await self._answer(role, kwargs)

    async def _answer(self, role, kwargs):
        self.calls.append({"role": role, **kwargs})
        offered = {
            item["function"]["name"] for item in (kwargs.get("extra_body") or {}).get("tools", ())
        }
        if "filesystem_read_text" in offered:
            calls = (ToolCall(
                call_id="read-source",
                tool_name="filesystem_read_text",
                arguments={"path": "fixture.py", "max_bytes": 2048},
                requested_by=role,
            ),)
        elif "mail_list_recent" in offered:
            calls = (ToolCall(
                call_id="read-mail",
                tool_name="mail_list_recent",
                arguments={"limit": 5},
                requested_by=role,
            ),)
        else:
            calls = ()
        return AgentResult(
            role=role, model_id="synthetic/observed-boundary",
            content="" if calls else "Respuesta basada en la evidencia disponible.",
            tool_calls=calls,
        )


@pytest.mark.parametrize(
    "query,nvidia_only,exists,expected_role,quorum,direct",
    [
        (
            "Revisa el código del archivo fixture.py.",
            True, True, AgentRole.CODE_SECURITY, False, False,
        ),
        (
            "Revisa el archivo fixture.py y analiza los datos.",
            True, True, AgentRole.CRITICAL_REASONER, False, False,
        ),
        ("Revisa el código del archivo fixture.py.", False, True, None, False, False),
        ("Revisa el código del archivo fixture.py.", True, False, None, False, False),
        ("Lee el archivo fixture.py", True, True, None, False, True),
        (
            "Revisa el código del archivo fixture.py por vulnerabilidad "
            "de escalada de privilegios.",
            True, True, None, True, False,
        ),
        ("Revisa mi correo", True, True, None, False, False),
    ],
)
async def test_graph_requests_expert_closure_only_for_single_nvidia_source_analysis(
    tmp_path, query, nvidia_only, exists, expected_role, quorum, direct,
):
    source = "first = 23\nreserved = 7\n"
    if exists:
        (tmp_path / "fixture.py").write_text(source, encoding="utf-8")
    broker = build_default_tool_broker()
    confirmations = OneTimeConfirmationStore()
    context = replace(default_policy_context(tmp_path), confirmation_store=confirmations)
    registry = SkillRegistry(broker, SkillStore(tmp_path / "skills"))
    boundary = GraphBoundary()

    class FixtureExecutor(ReadOnlyToolExecutor):
        async def execute_async(self, authorization, context):
            if authorization.tool_name == "mail_list_recent":
                return ToolExecutionResult(
                    call_id=authorization.call_id, tool_name=authorization.tool_name,
                    success=True, output=json.dumps({"messages": []}),
                )
            return await super().execute_async(authorization, context)

    graph = build_swarm_graph(
        boundary, tool_broker=broker, policy_context=context, skill_registry=registry,
        tool_executor=FixtureExecutor(),
        inference_nvidia_only=nvidia_only,
    )
    request = UserRequest(text=query)
    state = await graph.ainvoke({"request": request})
    if any(
        item.decision is PolicyDecision.REQUIRE_CONFIRMATION
        for item in state.get("tool_authorizations", ())
    ):
        confirmations.issue(
            state["specialist_result"].tool_calls[0], state["tool_authorizations"][0],
            approved_by="synthetic-owner", request_id=request.request_id,
        )
        state = await graph.ainvoke({"request": request})
    assert "final_result" in state
    if direct:
        assert state.get("direct_tool_call") is not None
        assert state["tool_results"][0].success
    if quorum:
        assert len(state["specialist_results"]) == 2
        assert state["tool_results"][0].success
    if query == "Revisa mi correo":
        assert state["route"].role is AgentRole.PLANNER
        assert any(
            result.success and result.tool_name == "mail_list_recent"
            for result in state["tool_results"]
        )
    if not exists:
        assert state["tool_results"][0].error_code == "file_not_found"
    requested = [call["evidence_role"] for call in boundary.calls if call.get("evidence_role")]
    assert requested == ([expected_role] if expected_role is not None else [])
    if expected_role is not None:
        final = boundary.calls[-1]
        assert final["role"] is AgentRole.SYNTHESIZER
        assert not (final.get("extra_body") or {}).get("tools")
        assert state["tool_results"][0].success
        assert state["tool_results"][0].output == source
        reference = next(
            json.loads(message["content"].split("\n", 1)[1])
            for message in final["messages"]
            if message["content"].startswith("Datos de referencia;")
        )
        assert reference["tool_results"][0]["output"] == source


def preferences_for_test(path, *, fallback=False):
    original = default_model_preferences()
    models = {model.model_id: model for model in original.models}
    models.update({
        "synthetic/code-primary": SelectableModel(model_id="synthetic/code-primary", tools=True),
        "synthetic/synthesis-fast": SelectableModel(
            model_id="synthetic/synthesis-fast", tools=True,
        ),
    })
    preferences = ModelPreferences(
        models=tuple(models.values()),
        roles={
            **original.roles,
            AgentRole.CODE_SECURITY: RoleSelection(
                primary="synthetic/code-primary", fallback=LIGHTNING if fallback else None,
            ),
            AgentRole.CRITICAL_REASONER: RoleSelection(primary=LIGHTNING, fallback=None),
            AgentRole.SYNTHESIZER: RoleSelection(primary="synthetic/synthesis-fast", fallback=None),
        },
    )
    write_private_json(path, preferences.model_dump(mode="json"))
    return path.read_bytes()


def response(content="{\"value\":16}", *, finish="stop", tool=False):
    message = {"content": content}
    if tool:
        message["tool_calls"] = [{
            "id": "forbidden-second-read", "type": "function",
            "function": {"name": "filesystem_read_text", "arguments": '{"path":"other.py"}'},
        }]
    return httpx.Response(200, json={"choices": [{"message": message, "finish_reason": finish}]})


@pytest.mark.parametrize(
    "expert,tokens,fallback",
    [
        (AgentRole.CODE_SECURITY, 4096, False),
        (AgentRole.CODE_SECURITY, 4096, True),
        (AgentRole.CRITICAL_REASONER, 256, False),
        (AgentRole.CRITICAL_REASONER, 4096, False),
        (AgentRole.CRITICAL_REASONER, 8192, False),
    ],
)
async def test_closed_answer_uses_owner_expert_models_without_tools_or_extended_reasoning(
    tmp_path, expert, tokens, fallback,
):
    path = tmp_path / "models.json"
    original = preferences_for_test(path, fallback=fallback)
    payloads, published = [], []

    async def endpoint(request):
        assert published == []
        payload = json.loads(request.content)
        payloads.append(payload)
        await asyncio.sleep(0)
        if fallback and payload["model"] == "synthetic/code-primary":
            return httpx.Response(404)
        return response()

    async with NvidiaNimClient(
        Settings(model_preferences_path=path), lambda: "synthetic",
        transport=httpx.MockTransport(endpoint), cooldown=NvidiaGlobalCooldown(),
    ) as provider:
        result = await NvidiaBrainClient(provider).complete_nvidia_only(
            role=AgentRole.SYNTHESIZER, evidence_role=expert,
            messages=[{"role": "user", "content": "Devuelve el objeto JSON solicitado."}],
            max_tokens=tokens, extra_body={"response_format": {"type": "json_object"}},
            on_delta=published.append,
        )
    expected_models = (
        ["synthetic/code-primary", LIGHTNING] if fallback else
        [LIGHTNING if expert is AgentRole.CRITICAL_REASONER else "synthetic/code-primary"]
    )
    assert [payload["model"] for payload in payloads] == expected_models
    assert result.role is AgentRole.SYNTHESIZER
    assert result.model_id == expected_models[-1]
    assert published == [result.content]
    assert path.read_bytes() == original
    for payload in payloads:
        assert payload["stream"] is False
        assert "tools" not in payload and "tool_choice" not in payload
        assert payload["response_format"] == {"type": "json_object"}
        assert payload["max_tokens"] == tokens
        if payload["model"] == LIGHTNING:
            assert payload["chat_template_kwargs"] == {"enable_thinking": False}
            assert "reasoning_budget" not in payload


@pytest.mark.parametrize("bad", ["tool", "length", "content_filter"])
async def test_closed_evidence_rejects_unusable_or_executable_answer_before_callback(tmp_path, bad):
    path = tmp_path / "models.json"
    preferences_for_test(path)
    published, payloads = [], []

    async def endpoint(request):
        payloads.append(json.loads(request.content))
        return response(tool=bad == "tool", finish="stop" if bad == "tool" else bad)

    async with NvidiaNimClient(
        Settings(model_preferences_path=path), lambda: "synthetic",
        transport=httpx.MockTransport(endpoint), cooldown=NvidiaGlobalCooldown(),
    ) as provider:
        with pytest.raises(IncompleteModelResponseError):
            await NvidiaBrainClient(provider).complete_nvidia_only(
                role=AgentRole.SYNTHESIZER, evidence_role=AgentRole.CRITICAL_REASONER,
                messages=[{"role": "user", "content": "Analiza la evidencia ya disponible."}],
                on_delta=published.append,
            )
    assert published == [] and len(payloads) == 1
    assert "tools" not in payloads[0]


@pytest.mark.parametrize(
    "role,expert,extra",
    [
        (AgentRole.CODE_SECURITY, AgentRole.CODE_SECURITY, {}),
        (AgentRole.SYNTHESIZER, AgentRole.PLANNER, {}),
        (AgentRole.SYNTHESIZER, AgentRole.CODE_SECURITY, {"tools": [{"type": "function"}]}),
        (AgentRole.SYNTHESIZER, AgentRole.CRITICAL_REASONER, {"tool_choice": "auto"}),
    ],
)
async def test_invalid_evidence_closure_is_rejected_before_provider_access(role, expert, extra):
    class ForbiddenProvider:
        async def complete_dialogue_review(self, **kwargs):
            raise AssertionError("provider must not be accessed")

    with pytest.raises(ValueError, match="closed expert synthesis"):
        await NvidiaBrainClient(ForbiddenProvider()).complete_nvidia_only(
            role=role, evidence_role=expert, extra_body=extra,
            messages=[{"role": "user", "content": "synthetic request"}],
        )


@pytest.mark.parametrize("answer", ["[]", '{"value":NaN}', '{"value":1,"value":2}', "not JSON"])
async def test_explicit_object_contract_rejects_invalid_json_before_publication(tmp_path, answer):
    path = tmp_path / "models.json"
    preferences_for_test(path)
    published, payloads = [], []

    async def endpoint(request):
        payloads.append(json.loads(request.content))
        return response(content=answer)

    async with NvidiaNimClient(
        Settings(model_preferences_path=path), lambda: "synthetic",
        transport=httpx.MockTransport(endpoint), cooldown=NvidiaGlobalCooldown(),
    ) as provider:
        with pytest.raises(IncompleteModelResponseError):
            await NvidiaBrainClient(provider).complete_nvidia_only(
                role=AgentRole.SYNTHESIZER, evidence_role=AgentRole.CRITICAL_REASONER,
                messages=[{"role": "user", "content": "Entrega el objeto JSON solicitado."}],
                extra_body={"response_format": {"type": "json_object"}},
                on_delta=published.append,
            )
    assert len(payloads) == 1 and published == []


async def test_explicit_json_object_contract_preserves_nested_values_without_claiming_truth(
    tmp_path,
):
    path = tmp_path / "models.json"
    preferences_for_test(path)
    # The shape guard must not apply a fixture-specific business oracle or invent a repair.
    value = {"rango": [None, 1.25, False], "cita": "archivo.txt:9", "nota": "mañana"}
    answer = json.dumps(value, ensure_ascii=False)
    published = []

    async def endpoint(_request):
        return response(content=answer)

    async with NvidiaNimClient(
        Settings(model_preferences_path=path), lambda: "synthetic",
        transport=httpx.MockTransport(endpoint), cooldown=NvidiaGlobalCooldown(),
    ) as provider:
        result = await NvidiaBrainClient(provider).complete_nvidia_only(
            role=AgentRole.SYNTHESIZER, evidence_role=AgentRole.CRITICAL_REASONER,
            messages=[{"role": "user", "content": "Entrega el objeto JSON solicitado."}],
            extra_body={"response_format": {"type": "json_object"}},
            on_delta=published.append,
        )
    assert json.loads(result.content) == value
    assert published == [result.content]


async def test_cancelling_closed_evidence_does_not_publish_partial_answer_or_retry(tmp_path):
    path = tmp_path / "models.json"
    preferences_for_test(path)
    entered, cancelled = asyncio.Event(), asyncio.Event()
    payloads, published = [], []

    async def endpoint(request):
        payloads.append(json.loads(request.content))
        entered.set()
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.set()

    async with NvidiaNimClient(
        Settings(model_preferences_path=path), lambda: "synthetic",
        transport=httpx.MockTransport(endpoint), cooldown=NvidiaGlobalCooldown(),
    ) as provider:
        task = asyncio.create_task(NvidiaBrainClient(provider).complete_nvidia_only(
            role=AgentRole.SYNTHESIZER, evidence_role=AgentRole.CRITICAL_REASONER,
            messages=[{"role": "user", "content": "Analiza la evidencia disponible."}],
            on_delta=published.append,
        ))
        await asyncio.wait_for(entered.wait(), 1)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    assert cancelled.is_set() and len(payloads) == 1 and published == []
