"""Provider migration retains privacy, completion and approval boundaries."""

import asyncio
import json
from types import SimpleNamespace

import pytest

from aegis_core.activity import SwarmActivityTracker
from aegis_core.brain.errors import (
    RemoteAuthenticationError,
    RemoteProviderUnavailableError,
    RemoteRateLimitedError,
)
from aegis_core.brain.inference_client import InferenceBrainClient
from aegis_core.computer_app_grants import ComputerAppGrantStore
from aegis_core.contracts import AgentResult, AgentRole, ToolCall, UserRequest
from aegis_core.orchestration.graph import build_swarm_graph
from aegis_core.orchestration.inference import create_completion
from aegis_core.providers.base import IncompleteModelResponseError
from aegis_core.providers.errors import ProviderError, ProviderRateLimitedError
from aegis_core.tools.defaults import default_policy_context


class SyntheticCodexTransport:
    provider_id = "codex"

    def __init__(self, *results):
        self.results = iter(results)
        self.requests = []

    async def complete(self, **kwargs):
        self.requests.append(kwargs)
        result = next(self.results)
        if isinstance(result, BaseException):
            raise result
        return result

    async def complete_stream(self, *, on_delta, **kwargs):
        result = await self.complete(**kwargs)
        if on_delta is not None:
            on_delta(result.content)
        return result

    async def complete_dialogue_review(self, *, review_role, draft_model_id, **kwargs):
        return await self.complete(role=review_role, **kwargs)


def answer(content="Respuesta sintética", *, finish="stop", calls=(), role=AgentRole.PLANNER):
    return AgentResult(role=role, model_id="synthetic-codex", content=content,
                       finish_reason=finish, tool_calls=calls)


async def test_configured_brain_redacts_secrets_and_emits_only_public_metrics():
    class Audit:
        def __init__(self):
            self.events = []

        def record_system_event(self, request_id, **event):
            self.events.append(event)

    audit = Audit()
    secret = "nvapi-" + "x" * 64
    transport = SyntheticCodexTransport(answer())
    brain = InferenceBrainClient(transport, audit_sink=audit)
    emitted = []
    complete = create_completion(brain, SwarmActivityTracker())

    result = await complete(
        AgentRole.PLANNER, stream_callback=emitted.append,
        messages=[{"role": "user", "content": f"La credencial es {secret}"}],
    )

    assert result.model_id == "synthetic-codex" and emitted == [result.content]
    assert secret not in json.dumps(transport.requests)
    event, = audit.events
    assert event["event_type"] == "provider_inference"
    assert event["data"]["provider"] == "codex"
    assert event["data"]["outcome"] == "completed"
    assert all(value not in json.dumps(event) for value in (secret, result.content))


@pytest.mark.parametrize(("error", "expected"), [
    (ProviderError("synthetic private error", status_code=401), RemoteAuthenticationError),
    (ProviderError("synthetic private error", status_code=403), RemoteAuthenticationError),
    (ProviderError("synthetic private error", status_code=503), RemoteProviderUnavailableError),
    (ProviderError("synthetic private error", status_code=429), RemoteRateLimitedError),
    (ProviderRateLimitedError(), RemoteRateLimitedError),
])
async def test_configured_transport_failures_keep_stable_public_error_categories(error, expected):
    transport = SyntheticCodexTransport(error)
    with pytest.raises(expected) as raised:
        await InferenceBrainClient(transport, rate_limit_retries=0).complete_inference(
            role=AgentRole.PLANNER, messages=[{"role": "user", "content": "Prueba"}],
        )
    assert "synthetic private error" not in str(raised.value)
    assert len(transport.requests) == 1


async def test_configured_completion_preserves_cancellation_without_retry():
    transport = SyntheticCodexTransport(asyncio.CancelledError())
    with pytest.raises(asyncio.CancelledError):
        await InferenceBrainClient(transport).complete_inference(
            role=AgentRole.PLANNER, messages=[],
        )
    assert len(transport.requests) == 1


@pytest.mark.parametrize("finish", ["length", "content_filter"])
async def test_structured_incomplete_response_is_never_published_or_authorized(finish):
    transport = SyntheticCodexTransport(answer('{"action":"type"}', finish=finish))
    emitted = []
    with pytest.raises(IncompleteModelResponseError):
        await InferenceBrainClient(transport).complete_inference(
            role=AgentRole.PLANNER, messages=[], on_delta=emitted.append,
            extra_body={"response_format": {"type": "json_object"}},
        )
    assert emitted == [] and len(transport.requests) == 1


async def test_configured_provider_cannot_substitute_a_required_named_tool():
    call = ToolCall(call_id="synthetic", tool_name="filesystem_read_text",
                    arguments={"path": "example.txt"}, requested_by=AgentRole.PLANNER)
    transport = SyntheticCodexTransport(answer("", calls=(call,)))
    with pytest.raises(IncompleteModelResponseError, match="named tool choice"):
        await InferenceBrainClient(transport).complete_inference(
            role=AgentRole.PLANNER, messages=[], extra_body={
                "tools": [], "tool_choice": {
                    "type": "function", "function": {"name": "computer_use"},
                }, "response_format": {"type": "json_object"},
            },
        )


async def test_configured_graph_reads_only_authorized_source_without_any_nvidia(tmp_path):
    source = "def suma(a, b):\n    return a + b\n"
    (tmp_path / "example.py").write_text(source)
    read = ToolCall(call_id="synthetic-read", tool_name="filesystem_read_text",
                    arguments={"path": "example.py", "max_bytes": 4096},
                    requested_by=AgentRole.CODE_SECURITY)
    transport = SyntheticCodexTransport(
        answer("", calls=(read,), role=AgentRole.CODE_SECURITY),
        answer("La función devuelve a + b.", role=AgentRole.CODE_SECURITY),
    )
    graph = build_swarm_graph(
        InferenceBrainClient(transport), policy_context=default_policy_context(tmp_path),
    )
    state = await graph.ainvoke({"request": UserRequest(
        text="Lee example.py y explica su función", metadata={
            "interaction_surface": "engineering_cli", "engineering_workspace": ".",
            "engineering_inference_policy": "configured",
            "engineering_research_policy": "offline",
        },
    )})

    assert state["tool_results"][0].success
    assert state["final_result"].model_id == "synthetic-codex"
    assert source in "".join(
        message["content"].replace("\\n", "\n")
        for request in transport.requests for message in request["messages"]
        if isinstance(message.get("content"), str)
    )
    assert (tmp_path / "example.py").read_text() == source


def test_codex_runtime_does_not_reuse_or_modify_previous_provider_app_grants(tmp_path):
    from aegis_core.runtime.daemon import _computer_app_grants

    old_path = tmp_path / "computer-app-grants.json"
    old = ComputerAppGrantStore(old_path)
    old.grant("com.apple.TextEdit", conversation_id=None)
    before = old_path.read_bytes()
    selected = _computer_app_grants(SimpleNamespace(computer_app_grants_path=old_path))

    assert old.allows("com.apple.TextEdit", None)
    assert not selected.allows("com.apple.TextEdit", None)
    assert old_path.read_bytes() == before
    selected.grant("com.apple.TextEdit", conversation_id=None)
    assert selected.allows("com.apple.TextEdit", None)
    assert old_path.read_bytes() == before


@pytest.mark.parametrize(("choice", "expected"), [("auto", "auto"),
    ({"type": "function", "function": {"name": "private_tool_name"}}, "named")])
async def test_tool_diagnostics_record_counts_without_private_names_or_arguments(choice, expected):
    class Audit:
        def __init__(self):
            self.events = []

        def record_system_event(self, request_id, **event):
            self.events.append(event)

    call = ToolCall(call_id="synthetic", tool_name="private_tool_name",
                    arguments={"secret": "PRIVATE_ARGUMENT"}, requested_by=AgentRole.PLANNER)
    transport = SyntheticCodexTransport(answer("", calls=(call,)))
    audit = Audit()
    await InferenceBrainClient(transport, audit_sink=audit).complete_inference(
        role=AgentRole.PLANNER, messages=[{"role": "user", "content": "PRIVATE_PROMPT"}],
        extra_body={"tools": [{"type": "function", "function": {"name": "private_tool_name"}}],
                    "tool_choice": choice},
    )
    data = audit.events[0]["data"]
    assert data["offered_tools_count"] == 1
    assert data["proposed_tools_count"] == 1
    assert data["tool_choice"] == expected
    assert "PRIVATE" not in json.dumps(audit.events)
    assert "private_tool_name" not in json.dumps(audit.events)


async def test_tool_diagnostics_distinguish_optional_decline_from_no_catalogue():
    class Audit:
        def __init__(self):
            self.events = []

        def record_system_event(self, request_id, **event):
            self.events.append(event)

    audit = Audit()
    transport = SyntheticCodexTransport(answer("No tengo disponible esa lectura."), answer())
    brain = InferenceBrainClient(transport, audit_sink=audit)
    await brain.complete_inference(
        role=AgentRole.PLANNER, messages=[], extra_body={
            "tools": [{"type": "function", "function": {"name": "synthetic_status"}}],
            "tool_choice": "auto",
        },
    )
    await brain.complete_inference(role=AgentRole.PLANNER, messages=[])
    assert [(e["data"]["offered_tools_count"], e["data"]["proposed_tools_count"])
            for e in audit.events] == [(1, 0), (0, 0)]
