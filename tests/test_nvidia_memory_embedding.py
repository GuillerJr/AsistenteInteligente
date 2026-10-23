import asyncio
import json

import httpx
import pytest

from aegis_core.config import Settings
from aegis_core.memory.embedding import NvidiaMemoryEmbeddingClient
from aegis_core.providers.base import EmbeddingBatch, EmbeddingInputType, EmbeddingProviderError
from aegis_core.providers.nvidia import NvidiaNimClient


@pytest.mark.asyncio
async def test_memory_embedding_reuses_nvidia_transport_redacts_and_versions_native_dimensions():
    async def handler(request):
        body = json.loads(request.content)
        assert request.url == "https://integrate.api.nvidia.com/v1/embeddings"
        assert body["model"] == "nvidia/nemotron-3.5-embed-1b"
        assert "dimensions" not in body
        assert "nvapi-private-example" not in body["input"][0]
        assert body["input_type"] == "passage"
        return httpx.Response(200, json={"data": [{
            "index": 0, "embedding": [3.0, 4.0] + [0.0] * 382 + [9.0] * 1664,
        }]})

    async with NvidiaNimClient(
        Settings(), lambda: "synthetic-api-key", transport=httpx.MockTransport(handler)
    ) as transport:
        client = NvidiaMemoryEmbeddingClient(transport)
        batch = await client.embed(
            ["La credencial es nvapi-private-example"], input_type=EmbeddingInputType.PASSAGE
        )
    assert batch.dimensions == 384
    assert batch.model_id == client.model_id
    assert batch.model_id != transport.model_id
    assert batch.model_id.endswith("@memory-v1-mrl384-l2")
    assert batch.vectors[0][:2] == pytest.approx((0.6, 0.8))
    assert batch.vectors[0][2:] == (0.0,) * 382


@pytest.mark.asyncio
@pytest.mark.parametrize("dimensions,model_id", [
    (2048, "nvidia/test"), (384, "nvidia/other"), (2, "nvidia/test"),
])
async def test_memory_embedding_rejects_wrong_space_before_vector_folding(dimensions, model_id):
    class Transport:
        model_id = "nvidia/test"

        async def embed(self, texts, *, input_type):
            return EmbeddingBatch(model_id, ((1.0,) + (0.0,) * (dimensions - 1),))

    with pytest.raises(EmbeddingProviderError, match="incompatible"):
        await NvidiaMemoryEmbeddingClient(Transport()).embed(
            ["consulta"], input_type=EmbeddingInputType.QUERY
        )


@pytest.mark.asyncio
async def test_memory_embedding_deadline_cancels_transport_and_cancellation_propagates():
    class Transport:
        model_id = "nvidia/test"

        def __init__(self):
            self.started = asyncio.Event()
            self.cancelled = asyncio.Event()

        async def embed(self, texts, *, input_type):
            self.started.set()
            try:
                await asyncio.Event().wait()
            finally:
                self.cancelled.set()

    transport = Transport()
    with pytest.raises(EmbeddingProviderError, match="deadline"):
        await NvidiaMemoryEmbeddingClient(transport, timeout_seconds=0.01).embed(
            ["consulta"], input_type=EmbeddingInputType.QUERY
        )
    assert transport.cancelled.is_set()
    transport = Transport()
    task = asyncio.create_task(NvidiaMemoryEmbeddingClient(transport).embed(
        ["consulta"], input_type=EmbeddingInputType.QUERY
    ))
    await transport.started.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert transport.cancelled.is_set()


@pytest.mark.asyncio
@pytest.mark.parametrize("status,attempts,success", [(500, 2, True), (503, 2, True),
                                                    (401, 1, False), (400, 1, False),
                                                    (403, 1, False), (429, 1, False)])
async def test_memory_embedding_retries_server_failure_once_but_not_invalid_credentials(
    status, attempts, success
):
    calls = []

    async def handler(request):
        calls.append(request)
        if len(calls) == 1:
            return httpx.Response(status)
        return httpx.Response(200, json={"data": [{
            "index": 0, "embedding": [1.0] + [0.0] * 2047,
        }]})

    async with NvidiaNimClient(
        Settings(), lambda: "synthetic-api-key", transport=httpx.MockTransport(handler)
    ) as transport:
        client = NvidiaMemoryEmbeddingClient(transport)
        if success:
            batch = await client.embed(["consulta"], input_type=EmbeddingInputType.QUERY)
            assert batch.dimensions == 384
        else:
            with pytest.raises(EmbeddingProviderError):
                await client.embed(["consulta"], input_type=EmbeddingInputType.QUERY)
    assert len(calls) == attempts


@pytest.mark.asyncio
@pytest.mark.parametrize("timeout,attempts", [(0.01, 1), (1.0, 2)])
async def test_memory_embedding_retry_shares_deadline_and_stops_after_two_attempts(
    timeout, attempts
):
    calls = []

    async def handler(request):
        calls.append(request)
        return httpx.Response(500)

    async with NvidiaNimClient(
        Settings(), lambda: "synthetic-api-key", transport=httpx.MockTransport(handler)
    ) as transport:
        with pytest.raises(EmbeddingProviderError):
            await NvidiaMemoryEmbeddingClient(transport, timeout_seconds=timeout).embed(
                ["consulta"], input_type=EmbeddingInputType.QUERY
            )
    assert len(calls) == attempts


@pytest.mark.asyncio
async def test_memory_embedding_rejects_zero_matryoshka_prefix():
    class Transport:
        model_id = "nvidia/nemotron-3.5-embed-1b"

        async def embed(self, texts, *, input_type):
            return EmbeddingBatch(self.model_id, ((0.0,) * 384 + (1.0,) * 1664,))

    with pytest.raises(EmbeddingProviderError, match="zero vector"):
        await NvidiaMemoryEmbeddingClient(Transport()).embed(
            ["consulta"], input_type=EmbeddingInputType.QUERY
        )


@pytest.mark.asyncio
async def test_long_passage_uses_utf8_safe_projection_without_mutating_source():
    original = "á" * 12_000
    sources = [original]
    requests = []

    async def handler(request):
        body = json.loads(request.content)
        requests.append(body)
        assert len(body["input"][0].encode("utf-8")) <= 16_384
        assert body["input"][0] == original[:8192]
        return httpx.Response(200, json={"data": [{
            "index": 0, "embedding": [1.0] + [0.0] * 2047,
        }]})

    async with NvidiaNimClient(
        Settings(), lambda: "synthetic-api-key", transport=httpx.MockTransport(handler)
    ) as transport:
        batch = await NvidiaMemoryEmbeddingClient(transport).embed(
            sources, input_type=EmbeddingInputType.PASSAGE
        )
    assert batch.dimensions == 384 and len(requests) == 1
    assert sources == [original]
