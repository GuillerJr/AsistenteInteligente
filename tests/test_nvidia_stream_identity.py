"""Remote stream envelopes must not mix replies or completion choices."""

import json

import httpx
import pytest

from aegis_core.config import Settings
from aegis_core.contracts import AgentRole
from aegis_core.providers.nvidia import NvidiaNimClient, NvidiaNimError


def event(content, *, reply_id="reply-a", model="model-a", index=0, finish=None):
    return {
        "id": reply_id,
        "model": model,
        "choices": [{"index": index, "delta": {"content": content}, "finish_reason": finish}],
    }


async def run_stream(events, published, requests):
    def handler(request):
        requests.append(request)
        body = "".join("data: " + json.dumps(item) + "\n\n" for item in events)
        return httpx.Response(200, text=body + "data: [DONE]\n\n")

    async with NvidiaNimClient(
        Settings(), lambda: "synthetic-key", transport=httpx.MockTransport(handler)
    ) as client:
        return await client.complete_stream(
            role=AgentRole.PLANNER,
            messages=[{"role": "user", "content": "synthetic request"}],
            on_delta=published.append,
        )


@pytest.mark.parametrize("changed", [
    {"reply_id": "reply-b"}, {"model": "model-b"}, {"index": 1}, {"index": True},
    {"reply_id": ""}, {"model": []},
])
async def test_stream_rejects_identity_change_before_publishing_foreign_delta(changed):
    published, requests = [], []
    with pytest.raises(NvidiaNimError, match=r"identity|choice"):
        await run_stream(
            [event("Primera parte"), event("contenido ajeno", finish="stop", **changed)],
            published, requests,
        )
    assert published == ["Primera parte"]
    assert len(requests) == 1  # Visible content must never be retried with another model.


async def test_usage_only_event_cannot_rebind_finished_stream():
    published, requests = [], []
    usage = {"id": "reply-b", "model": "model-a", "choices": [], "usage": {"total_tokens": 8}}
    with pytest.raises(NvidiaNimError, match="identity"):
        await run_stream([event("Respuesta", finish="stop"), usage], published, requests)
    assert published == ["Respuesta"]
    assert len(requests) == 1


@pytest.mark.parametrize("include_metadata", [False, True])
async def test_stable_identity_and_legacy_missing_metadata_remain_supported(include_metadata):
    published, requests = [], []
    events = [event("Respuesta"), event(" completa", finish="stop")]
    if not include_metadata:
        for item in events:
            item.pop("id")
            item.pop("model")
            item["choices"][0].pop("index")
    result = await run_stream(events, published, requests)
    assert result.content == "Respuesta completa"
    assert published == ["Respuesta", " completa"]
    assert len(requests) == 1


async def test_optional_identity_may_arrive_late_without_losing_its_binding():
    published, requests = [], []
    first = event("Respuesta")
    first.pop("id")
    result = await run_stream([first, event(" completa", finish="stop")], published, requests)
    assert result.content == "Respuesta completa"
    assert len(requests) == 1
