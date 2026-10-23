"""Decision helpers use the owner's router selection, including observable fallback."""

import json
from datetime import UTC, datetime
from uuid import uuid4

import httpx
import pytest

from aegis_core.brain.nvidia_client import NvidiaBrainClient
from aegis_core.config import Settings
from aegis_core.contracts import AgentRole
from aegis_core.goals import Goal, nvidia_step_judge
from aegis_core.memory.semantic_profile import nvidia_memory_interpreter
from aegis_core.orchestration.semantic_tools import nvidia_tool_chooser
from aegis_core.providers.inference_metrics import begin_metrics, end_metrics
from aegis_core.providers.nvidia import NvidiaNimClient, NvidiaNimError
from aegis_core.scheduling import nvidia_novelty_judge
from aegis_core.settings_store import default_model_preferences, write_private_json

PRIMARY, FALLBACK = "fixture/owner-router", "fixture/owner-backup"


def settings_for(tmp_path):
    tmp_path.chmod(0o700)
    path = tmp_path / "model-preferences.json"
    value = default_model_preferences().model_dump(mode="json")
    value["models"] += [
        {"model_id": name, "images": False, "tools": True} for name in (PRIMARY, FALLBACK)
    ]
    value["roles"]["router"] = {"primary": PRIMARY, "fallback": FALLBACK}
    write_private_json(path, value)
    return Settings(model_preferences_path=path)


@pytest.mark.parametrize("helper", ["tools", "memory", "novelty", "goal"])
async def test_decision_helpers_send_the_configured_router_model(tmp_path, helper):
    responses = {
        "tools": {"intent": "datos", "tools": ["mcp_fixture__search_mail"]},
        "memory": {"facts": []}, "novelty": {"notify": True},
        "goal": {"status": "continue", "note": "Siguiente paso", "report": None},
    }
    requests = []

    async def handler(request):
        payload = json.loads(request.content)
        requests.append(payload)
        return httpx.Response(200, json={"choices": [{"finish_reason": "stop", "message": {
            "content": json.dumps(responses[helper])}}]})

    async with NvidiaNimClient(settings_for(tmp_path), lambda: "synthetic-key",
                               transport=httpx.MockTransport(handler)) as provider:
        brain = NvidiaBrainClient(provider)
        if helper == "tools":
            choose = nvidia_tool_chooser(brain, lambda: (("mcp_fixture__search_mail", "Correo"),))
            assert await choose("Resúmeme mis correos") == {"mcp_fixture__search_mail"}
        elif helper == "memory":
            await nvidia_memory_interpreter(brain)([{"role": "user", "content": "Prueba"}])
        elif helper == "novelty":
            assert await nvidia_novelty_judge(brain)("Anterior", "Nuevo")
        else:
            now = datetime.now(UTC)
            goal = Goal(goal_id=uuid4(), title="Prueba", objective="Objetivo sintético",
                        created_at=now, updated_at=now)
            assert (await nvidia_step_judge(brain)(goal, "Resultado")).status == "continue"
        assert provider.fallback_status() is None
    assert [item["model"] for item in requests] == [PRIMARY]


@pytest.mark.parametrize("stream", [False, True])
async def test_fallback_reports_selected_models_and_cached_unavailability(tmp_path, stream):
    sent = []

    async def handler(request):
        model = json.loads(request.content)["model"]
        sent.append(model)
        if model == PRIMARY:
            return httpx.Response(404, text="synthetic private error")
        if stream:
            event = {"choices": [{"delta": {"content": "Bien"}, "finish_reason": "stop"}]}
            return httpx.Response(200, text="data: " + json.dumps(event) + "\n\ndata: [DONE]\n\n")
        return httpx.Response(200, json={"choices": [{"finish_reason": "stop", "message": {
            "content": "Bien"}}]})

    async with NvidiaNimClient(settings_for(tmp_path), lambda: "synthetic-key",
                               transport=httpx.MockTransport(handler)) as provider:
        operation = provider.complete_stream if stream else provider.complete
        options = {"on_delta": lambda value: None} if stream else {}
        metrics, token = begin_metrics()
        try:
            await operation(role=AgentRole.ROUTER, messages=[], **options)
            first = provider.fallback_status()
            assert first == {"role": "router", "requested_model_id": PRIMARY,
                             "actual_model_id": FALLBACK, "reason": "http_404"}
            first["reason"] = "external mutation"
            assert provider.fallback_status()["reason"] == "http_404"
            await operation(role=AgentRole.ROUTER, messages=[], **options)
            assert provider.fallback_status()["reason"] == "model_unavailable"
        finally:
            end_metrics(token)
    assert sent == [PRIMARY, FALLBACK, FALLBACK]
    assert metrics.public_dict()["fallback_count"] == 2
    evidence = json.dumps(metrics.public_dict())
    assert "synthetic private error" not in evidence and "synthetic-key" not in evidence


async def test_failed_fallback_does_not_claim_successful_substitution(tmp_path):
    async def handler(request):
        return httpx.Response(503)

    async with NvidiaNimClient(settings_for(tmp_path), lambda: "synthetic-key",
                               transport=httpx.MockTransport(handler)) as provider:
        with pytest.raises(NvidiaNimError):
            await provider.complete(role=AgentRole.ROUTER, messages=[])
        assert provider.fallback_status() is None
