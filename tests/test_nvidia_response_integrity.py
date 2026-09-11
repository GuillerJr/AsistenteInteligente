"""A delivered fragment is not proof that a model completed its answer."""

import json

import httpx
import pytest

from aegis_core.config import Settings
from aegis_core.contracts import AgentRole
from aegis_core.providers.base import IncompleteModelResponseError
from aegis_core.providers.nvidia import NvidiaNimClient, NvidiaNimError


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
