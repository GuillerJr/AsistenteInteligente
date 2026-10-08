from __future__ import annotations

import asyncio
import base64
import io
import json
from uuid import uuid4

import httpx
import pytest
from PIL import Image

from aegis_core.brain.vision_fallback import (
    LocalizedVisionAnalyzer,
    LocalizedVisionError,
    VisionFallbackIpcService,
)
from aegis_core.config import Settings
from aegis_core.contracts import AgentResult, AgentRole, ToolCall
from aegis_core.ipc.protocol import IpcRequest
from aegis_core.providers.nvidia import NvidiaNimClient

VALID = '{"offset_x":1,"offset_y":-2,"confidence":0.9,"target_found":true}'


def png() -> bytes:
    stream = io.BytesIO()
    Image.new("RGB", (120, 120), "white").save(stream, format="PNG")
    return stream.getvalue()


def arguments() -> dict:
    return dict(image_png=png(), target_description="Documentation", expected_x=60, expected_y=60)


class ProbeProvider:
    def __init__(self, content=VALID, *, delay=0.0, calls=()):
        self.content, self.delay, self.tool_calls = content, delay, calls
        self.requests = []

    async def complete(self, **kwargs):
        self.requests.append(kwargs)
        await asyncio.sleep(self.delay)
        return AgentResult(
            role=AgentRole.VISION, model_id="synthetic/vision", content=self.content,
            tool_calls=self.tool_calls,
        )


async def test_localized_vision_uses_nvidia_only_transport_without_tool_authority(tmp_path):
    requests = []

    async def endpoint(request):
        body = json.loads(request.content)
        requests.append(body)
        return httpx.Response(200, json={
            "id": "synthetic", "choices": [{
                "finish_reason": "stop", "message": {"role": "assistant", "content": VALID},
            }],
        })

    settings = Settings.model_construct(model_preferences_path=tmp_path / "models.json")
    async with NvidiaNimClient(
        settings, lambda: "synthetic", transport=httpx.MockTransport(endpoint)
    ) as provider:
        decision = await LocalizedVisionAnalyzer(provider).analyze(**arguments())
    assert decision.offset_x == 1 and decision.offset_y == -2
    assert len(requests) == 1
    request = requests[0]
    assert request["response_format"] == {"type": "json_object"}
    assert request["max_tokens"] == 128
    assert not request.get("tools") and not request.get("tool_choice")
    assert request["messages"][1]["content"][1]["image_url"]["url"].startswith("data:image/png;")
    assert "untrusted" in request["messages"][0]["content"]


@pytest.mark.parametrize("content", [
    '[]', '{"offset_x":1,"offset_x":2,"offset_y":0,"confidence":1,"target_found":true}',
    '{"offset_x":NaN,"offset_y":0,"confidence":1,"target_found":true}',
    '{"offset_x":true,"offset_y":0,"confidence":1,"target_found":true}',
    '{"offset_x":61,"offset_y":0,"confidence":1,"target_found":true}',
    '{"offset_x":0,"offset_y":0,"confidence":1,"target_found":"true"}',
    '{"offset_x":0,"offset_y":0,"confidence":1,"target_found":true,"click":true}',
    'x' * 2049,
    '{"offset_x":' + '9' * 1000 + ',"offset_y":0,"confidence":1,"target_found":true}',
    '{"offset_x":1e999,"offset_y":0,"confidence":1,"target_found":true}',
])
async def test_localized_vision_rejects_unbounded_or_ambiguous_model_decisions(content):
    provider = ProbeProvider(content)
    with pytest.raises(LocalizedVisionError):
        await LocalizedVisionAnalyzer(provider).analyze(**arguments())
    assert len(provider.requests) == 1


async def test_localized_vision_rejects_tool_calls_instead_of_executing_them():
    provider = ProbeProvider(calls=(ToolCall(
        call_id="synthetic", tool_name="filesystem_read_text",
        requested_by=AgentRole.VISION, arguments={"path": "x"},
    ),))
    with pytest.raises(LocalizedVisionError):
        await LocalizedVisionAnalyzer(provider).analyze(**arguments())
    assert len(provider.requests) == 1


@pytest.mark.parametrize("replace", [
    {"image_png": b"not-png"}, {"expected_x": float("nan")}, {"expected_x": 10 ** 1000},
    {"expected_y": True}, {"target_description": "Ignore\npolicy"},
])
async def test_localized_vision_validates_before_provider_dispatch(replace):
    provider = ProbeProvider()
    with pytest.raises(LocalizedVisionError):
        await LocalizedVisionAnalyzer(provider).analyze(**(arguments() | replace))
    assert provider.requests == []


async def test_localized_vision_admission_is_inside_deadline():
    provider = ProbeProvider()
    analyzer = LocalizedVisionAnalyzer(provider, timeout_seconds=0.03)
    await analyzer._lock.acquire()
    try:
        with pytest.raises(LocalizedVisionError):
            await asyncio.wait_for(analyzer.analyze(**arguments()), timeout=0.2)
        assert provider.requests == []
    finally:
        analyzer._lock.release()


async def test_localized_vision_timeout_cancels_response_without_retry():
    provider = ProbeProvider(delay=1)
    analyzer = LocalizedVisionAnalyzer(provider, timeout_seconds=0.03)
    with pytest.raises(LocalizedVisionError):
        await asyncio.wait_for(analyzer.analyze(**arguments()), timeout=0.2)
    assert len(provider.requests) == 1


async def test_localized_vision_cancellation_propagates_and_does_not_dispatch_later():
    provider = ProbeProvider()
    analyzer = LocalizedVisionAnalyzer(provider)
    await analyzer._lock.acquire()
    task = asyncio.create_task(analyzer.analyze(**arguments()))
    try:
        await asyncio.sleep(0)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    finally:
        analyzer._lock.release()
    await asyncio.sleep(0)
    assert provider.requests == []


async def test_localized_vision_preserves_ipc_contract_and_never_grants_an_action():
    provider = ProbeProvider()
    service = VisionFallbackIpcService(LocalizedVisionAnalyzer(provider))
    request = IpcRequest(
        request_id=uuid4(), timestamp=0, nonce="a" * 32, method=service.METHOD,
        payload={"image_png_base64": base64.b64encode(png()).decode(),
                 "target_description": "Documentation", "expected_x": 60, "expected_y": 60},
        auth_tag="a" * 64,
    )
    response = await service.handle(request)
    assert response.ok
    assert response.payload == {"offset_x": 1.0, "offset_y": -2.0,
                                "confidence": 0.9, "target_found": True}
    assert "action" not in response.payload
    assert provider.requests[0]["role"] is AgentRole.VISION
