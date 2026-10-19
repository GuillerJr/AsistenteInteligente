import asyncio
import json
from uuid import uuid4

import httpx
import pytest

from aegis_core.brain.nvidia_client import NvidiaBrainClient
from aegis_core.config import Settings
from aegis_core.contracts import AgentRole
from aegis_core.providers.nvidia import NvidiaNimClient


class Audit:
    def __init__(self):
        self.events = {}

    def record_system_event(self, request_id, **kwargs):
        self.events[request_id] = kwargs["data"]


def response(tokens=8):
    return httpx.Response(200, json={"choices": [{"finish_reason": "stop", "message": {
        "content": "Respuesta."}}], "usage": {"prompt_tokens": tokens, "completion_tokens": 3}})


async def complete(brain, request_id, text):
    return await brain.complete_nvidia_only(role=AgentRole.PLANNER, request_id=request_id,
        messages=[{"role": "user", "content": text}],
        extra_body={"response_format": {"type": "json_object"}})


@pytest.mark.asyncio
async def test_metrics_count_http_fallbacks_without_counting_unknown_usage_as_known():
    attempts = 0

    async def handler(_request):
        nonlocal attempts
        attempts += 1
        return httpx.Response(410) if attempts == 1 else response()

    audit = Audit()
    request_id = uuid4()
    async with NvidiaNimClient(Settings(), lambda: "test",
                               transport=httpx.MockTransport(handler)) as provider:
        await complete(NvidiaBrainClient(provider, audit_sink=audit), request_id, "Mensaje privado")
    data = audit.events[request_id]
    assert data["http_requests"] == 2 and data["usage_responses"] == 1
    assert data["prompt_tokens"] == 8 and data["completion_tokens"] == 3
    assert "Mensaje privado" not in json.dumps(data)


@pytest.mark.asyncio
async def test_concurrent_inference_metrics_are_isolated_and_cancelled_scope_is_released():
    entered = asyncio.Event()

    async def handler(request):
        text = json.loads(request.content)["messages"][-1]["content"]
        if text == "slow":
            entered.set()
            await asyncio.Event().wait()
        return response(17)

    audit = Audit()
    slow_id, fast_id, next_id = uuid4(), uuid4(), uuid4()
    async with NvidiaNimClient(Settings(), lambda: "test",
                               transport=httpx.MockTransport(handler)) as provider:
        brain = NvidiaBrainClient(provider, audit_sink=audit)
        slow = asyncio.create_task(complete(brain, slow_id, "slow"))
        await entered.wait()
        await complete(brain, fast_id, "fast")
        slow.cancel()
        with pytest.raises(asyncio.CancelledError):
            await slow
        await complete(brain, next_id, "next")
    assert audit.events[slow_id]["outcome"] == "cancelled"
    assert audit.events[slow_id]["http_requests"] == 1
    assert audit.events[slow_id]["usage_responses"] == 0
    for request_id in (fast_id, next_id):
        assert audit.events[request_id]["http_requests"] == 1
        assert audit.events[request_id]["prompt_tokens"] == 17
