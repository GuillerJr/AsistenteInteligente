import json

import httpx
import pytest

from aegis_core.config import Settings
from aegis_core.contracts import AgentRole
from aegis_core.providers.base import EmbeddingInputType
from aegis_core.providers.nvidia import NvidiaNimClient, NvidiaNimError, NvidiaNimRateLimited


@pytest.mark.asyncio
async def test_complete_does_not_expose_api_key_in_result() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["Authorization"] == "Bearer secret-value"
        return httpx.Response(
            200,
            json={
                "choices": [{"message": {"content": "ok"}, "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 3, "completion_tokens": 1},
            },
        )

    client = NvidiaNimClient(
        Settings(),
        lambda: "secret-value",
        transport=httpx.MockTransport(handler),
    )
    async with client:
        result = await client.complete(
            role=AgentRole.ROUTER,
            messages=[{"role": "user", "content": "hola"}],
        )
    assert result.content == "ok"
    assert "secret-value" not in repr(result)


@pytest.mark.asyncio
async def test_rate_limit_has_a_typed_error() -> None:
    async def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(429, json={"detail": "slow down"})

    client = NvidiaNimClient(
        Settings(),
        lambda: "secret-value",
        transport=httpx.MockTransport(handler),
    )
    async with client:
        with pytest.raises(NvidiaNimRateLimited):
            await client.complete(
                role=AgentRole.ROUTER,
                messages=[{"role": "user", "content": "hola"}],
            )


@pytest.mark.asyncio
async def test_transport_failure_has_a_typed_error() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("offline", request=request)

    client = NvidiaNimClient(
        Settings(),
        lambda: "secret-value",
        transport=httpx.MockTransport(handler),
    )
    async with client:
        with pytest.raises(NvidiaNimError, match="request failed"):
            await client.complete(
                role=AgentRole.ROUTER,
                messages=[{"role": "user", "content": "hola"}],
            )


@pytest.mark.asyncio
async def test_complete_parses_nvidia_tool_calls() -> None:
    async def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "content": None,
                            "tool_calls": [
                                {
                                    "id": "call-1",
                                    "type": "function",
                                    "function": {
                                        "name": "filesystem_read_text",
                                        "arguments": '{"path":"README.md"}',
                                    },
                                }
                            ],
                        },
                        "finish_reason": "tool_calls",
                    }
                ]
            },
        )

    client = NvidiaNimClient(
        Settings(), lambda: "secret-value", transport=httpx.MockTransport(handler)
    )
    async with client:
        result = await client.complete(
            role=AgentRole.CODE_SECURITY,
            messages=[{"role": "user", "content": "lee el archivo"}],
        )

    assert result.tool_calls[0].tool_name == "filesystem_read_text"
    assert result.tool_calls[0].arguments == {"path": "README.md"}
    assert result.tool_calls[0].requested_by is AgentRole.CODE_SECURITY


@pytest.mark.asyncio
async def test_invalid_tool_arguments_have_a_typed_error() -> None:
    async def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "tool_calls": [
                                {
                                    "id": "call-1",
                                    "function": {
                                        "name": "filesystem_read_text",
                                        "arguments": "not-json",
                                    },
                                }
                            ]
                        },
                        "finish_reason": "tool_calls",
                    }
                ]
            },
        )

    client = NvidiaNimClient(
        Settings(), lambda: "secret-value", transport=httpx.MockTransport(handler)
    )
    async with client:
        with pytest.raises(NvidiaNimError, match="invalid tool call"):
            await client.complete(
                role=AgentRole.CODE_SECURITY,
                messages=[{"role": "user", "content": "lee el archivo"}],
            )


@pytest.mark.asyncio
async def test_embed_uses_nvidia_endpoint_modes_and_normalizes_vectors() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        assert request.url.path == "/v1/embeddings"
        assert payload == {
            "model": "nvidia/nemotron-3-embed-1b",
            "input": ["consulta", "documento"],
            "input_type": "query",
            "encoding_format": "float",
            "truncate": "END",
        }
        return httpx.Response(
            200,
            json={
                "data": [
                    {"index": 1, "embedding": [0.0, 5.0]},
                    {"index": 0, "embedding": [3.0, 4.0]},
                ]
            },
        )

    client = NvidiaNimClient(
        Settings(), lambda: "secret-value", transport=httpx.MockTransport(handler)
    )
    async with client:
        batch = await client.embed(
            ["consulta", "documento"],
            input_type=EmbeddingInputType.QUERY,
        )

    assert batch.model_id == "nvidia/nemotron-3-embed-1b"
    assert batch.dimensions == 2
    assert batch.vectors[0] == pytest.approx((0.6, 0.8))
    assert batch.vectors[1] == pytest.approx((0.0, 1.0))


@pytest.mark.asyncio
async def test_embed_rejects_invalid_provider_vectors() -> None:
    async def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={"data": [{"index": 0, "embedding": [0.0, 0.0]}]},
        )

    client = NvidiaNimClient(
        Settings(), lambda: "secret-value", transport=httpx.MockTransport(handler)
    )
    async with client:
        with pytest.raises(NvidiaNimError, match="invalid embeddings"):
            await client.embed(["consulta"], input_type=EmbeddingInputType.QUERY)
