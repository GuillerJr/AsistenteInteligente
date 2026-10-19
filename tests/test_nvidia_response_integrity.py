"""A delivered fragment is not proof that a model completed its answer."""

import asyncio
import json

import httpx
import pytest

from aegis_core.config import Settings
from aegis_core.contracts import AgentRole
from aegis_core.providers.base import IncompleteModelResponseError
from aegis_core.providers.nvidia import NvidiaNimClient, NvidiaNimError


@pytest.mark.parametrize("streaming", [False, True])
async def test_stalled_primary_leaves_time_for_remote_fallback(streaming):
    requested = []
    primary_cancelled = asyncio.Event()

    async def handler(request):
        requested.append(json.loads(request.content)["model"])
        if len(requested) == 1:
            try:
                await asyncio.Event().wait()
            finally:
                primary_cancelled.set()
        if streaming:
            return httpx.Response(200, text=sse("Respuesta alternativa verificada."))
        return httpx.Response(200, json={"choices": [{
            "message": {"content": "Respuesta alternativa verificada."}, "finish_reason": "stop",
        }]})

    async with NvidiaNimClient(
        Settings.model_construct(request_timeout_seconds=0.2), lambda: "test",
        transport=httpx.MockTransport(handler),
    ) as client:
        options = {"role": AgentRole.PLANNER, "messages": [{"role": "user", "content": "test"}]}
        if streaming:
            result = await asyncio.wait_for(client.complete_stream(**options, on_delta=None), 0.5)
        else:
            result = await asyncio.wait_for(client.complete(**options), 0.5)
    assert primary_cancelled.is_set()
    assert len(requested) == 2
    assert result.model_id == requested[1]
    assert result.content == "Respuesta alternativa verificada."


async def test_timeout_after_visible_output_does_not_switch_or_mix_models():
    requested = []
    published = []

    class InterruptedBody(httpx.AsyncByteStream):
        async def __aiter__(self):
            yield sse("Fragmento de la primera respuesta", finish=None, done=False).encode()
            await asyncio.Event().wait()

    async def handler(request):
        requested.append(request)
        return httpx.Response(200, stream=InterruptedBody())

    async with NvidiaNimClient(
        Settings.model_construct(request_timeout_seconds=0.2), lambda: "test",
        transport=httpx.MockTransport(handler),
    ) as client:
        with pytest.raises(NvidiaNimError, match="timed out"):
            await client.complete_stream(
                role=AgentRole.PLANNER, messages=[{"role": "user", "content": "test"}],
                on_delta=published.append,
            )
    assert len(requested) == 1
    assert published == ["Fragmento de la primera respuesta"]


@pytest.mark.parametrize("role,override,expected", [
    (AgentRole.PLANNER, None, {"enable_thinking": False}),
    (AgentRole.SYNTHESIZER, None, {"enable_thinking": False}),
    (AgentRole.PLANNER, {"enable_thinking": True}, {"enable_thinking": True}),
    (AgentRole.CRITICAL_REASONER, None, None),
])
def test_direct_response_tuning_respects_role_and_explicit_configuration(role, override, expected):
    payload = {"messages": [{"role": "user", "content": "test"}]}
    if override is not None:
        payload["chat_template_kwargs"] = override
    tuned = NvidiaNimClient._attempt_payload(
        payload, role, "nvidia/nemotron-3.5-lightning-30b-a3b",
    )
    assert tuned.get("chat_template_kwargs") == expected
    custom = NvidiaNimClient._attempt_payload(payload, role, "custom/provider-model")
    assert custom.get("chat_template_kwargs") == override
    assert "model" not in payload


def sse(content="respuesta", finish="stop", *, done=True):
    event = {"choices": [{"delta": {"content": content}, "finish_reason": finish}]}
    return "data: " + json.dumps(event) + "\n\n" + ("data: [DONE]\n\n" if done else "")


@pytest.mark.parametrize(
    "finish,done",
    [(None, False), ("stop", False), ("length", True), ("content_filter", True), (None, True)],
)
async def test_stream_requires_a_complete_answer_without_retrying_fragments(finish, done):
    calls = []
    chunks = []

    def handler(request):
        calls.append(request)
        return httpx.Response(200, text=sse(finish=finish, done=done))

    async with NvidiaNimClient(
        Settings(), lambda: "test", transport=httpx.MockTransport(handler)
    ) as client:
        with pytest.raises(IncompleteModelResponseError, match="incomplete"):
            await client.complete_stream(
                role=AgentRole.CODE_SECURITY,
                messages=[{"role": "user", "content": "test"}],
                on_delta=chunks.append,
            )
    assert len(calls) == 1


@pytest.mark.parametrize("finish", ["length", "content_filter"])
async def test_buffered_incomplete_response_never_reaches_tools_or_fallback(finish):
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "content": "Voy a leer el archivo",
                            "tool_calls": [
                                {
                                    "id": "read1",
                                    "function": {
                                        "name": "filesystem_read_text",
                                        "arguments": '{"path":"a.py"}',
                                    },
                                }
                            ],
                        },
                        "finish_reason": finish,
                    }
                ]
            },
        )

    async with NvidiaNimClient(
        Settings(), lambda: "test", transport=httpx.MockTransport(handler)
    ) as client:
        with pytest.raises(IncompleteModelResponseError, match="incomplete"):
            await client.complete(
                role=AgentRole.CODE_SECURITY,
                messages=[{"role": "user", "content": "test"}],
            )
    assert len(calls) == 1


async def test_empty_attempt_does_not_pollute_successful_fallback_usage():
    calls = []

    def handler(request):
        calls.append(request)
        if len(calls) == 1:
            return httpx.Response(
                200, text='data: {"choices":[],"usage":{"prompt_tokens":999}}\n\ndata: [DONE]\n\n'
            )
        return httpx.Response(200, text=sse())

    async with NvidiaNimClient(
        Settings(), lambda: "test", transport=httpx.MockTransport(handler)
    ) as client:
        result = await client.complete_stream(
            role=AgentRole.CODE_SECURITY,
            messages=[{"role": "user", "content": "test"}],
            on_delta=None,
        )
    assert result.content == "respuesta"
    assert result.raw_usage == {}
    assert len(calls) == 2


async def test_no_text_is_accepted_after_the_terminal_choice():
    chunks = []
    body = sse("válido", done=False) + sse(" añadido tardío")
    async with NvidiaNimClient(
        Settings(),
        lambda: "test",
        transport=httpx.MockTransport(lambda _: httpx.Response(200, text=body)),
    ) as client:
        with pytest.raises(NvidiaNimError, match="after stream finish"):
            await client.complete_stream(
                role=AgentRole.CODE_SECURITY,
                messages=[{"role": "user", "content": "test"}],
                on_delta=chunks.append,
            )
    assert chunks == ["válido"]


@pytest.mark.parametrize("finish", [[], {}, 3, True])
async def test_invalid_finish_reason_is_a_typed_provider_error(finish):
    async with NvidiaNimClient(
        Settings(),
        lambda: "test",
        transport=httpx.MockTransport(
            lambda _: httpx.Response(
                200,
                json={
                    "choices": [
                        {
                            "message": {"content": "respuesta"},
                            "finish_reason": finish,
                        }
                    ]
                },
            )
        ),
    ) as client:
        with pytest.raises(NvidiaNimError, match="invalid finish reason"):
            await client.complete(
                role=AgentRole.PLANNER,
                messages=[{"role": "user", "content": "test"}],
            )
