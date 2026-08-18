import httpx
import pytest

from aegis_core.config import Settings
from aegis_core.contracts import AgentRole
from aegis_core.providers.nvidia import NvidiaNimClient, NvidiaNimRateLimited


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
