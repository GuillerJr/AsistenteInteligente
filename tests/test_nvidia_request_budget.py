import asyncio
import threading

import httpx
import pytest

from aegis_core.config import Settings
from aegis_core.contracts import AgentRole
from aegis_core.providers.base import EmbeddingInputType
from aegis_core.providers.nvidia import NvidiaNimClient, NvidiaNimError


async def invoke(provider, method):
    if method == "complete":
        return await provider.complete(
            role=AgentRole.SYNTHESIZER, messages=[], timeout_seconds=0.03
        )
    if method == "complete_stream":
        return await provider.complete_stream(
            role=AgentRole.SYNTHESIZER, messages=[], on_delta=None
        )
    if method == "embed":
        return await provider.embed(["synthetic"], input_type=EmbeddingInputType.QUERY)
    if method == "synthesize_speech":
        return await provider.synthesize_speech("Prueba sintética.")
    return [chunk async for chunk in provider.stream_speech("Prueba sintética.")]


@pytest.mark.parametrize(
    "method", ["complete", "complete_stream", "embed", "synthesize_speech", "stream_speech"]
)
async def test_queue_wait_is_inside_nvidia_budget_and_never_sends_after_expiry(tmp_path, method):
    credentials, requests = [], []

    def loader():
        credentials.append(True)
        return "synthetic"

    async def endpoint(request):
        requests.append(True)
        raise AssertionError("an expired queued request must not reach HTTP")

    settings = Settings.model_construct(
        model_preferences_path=tmp_path / "models.json",
        max_concurrency=1,
        request_timeout_seconds=0.03,
        nvidia_tts_timeout_seconds=0.03,
    )
    async with NvidiaNimClient(
        settings, loader, transport=httpx.MockTransport(endpoint)
    ) as provider:
        await provider._semaphore.acquire()
        try:
            with pytest.raises(NvidiaNimError, match="timed out"):
                await asyncio.wait_for(invoke(provider, method), timeout=0.2)
            assert not credentials and not requests
        finally:
            provider._semaphore.release()


@pytest.mark.parametrize(
    "method", ["complete", "complete_stream", "embed", "synthesize_speech", "stream_speech"]
)
async def test_credential_loading_cannot_reset_nvidia_deadline(tmp_path, method):
    started, release = threading.Event(), threading.Event()
    requests = []

    def loader():
        started.set()
        assert release.wait(1)
        return "synthetic"

    async def endpoint(request):
        requests.append(True)
        raise AssertionError("a request whose credentials exceeded its deadline must not be sent")

    settings = Settings.model_construct(
        model_preferences_path=tmp_path / "models.json",
        request_timeout_seconds=0.03,
        nvidia_tts_timeout_seconds=0.03,
    )
    async with NvidiaNimClient(
        settings, loader, transport=httpx.MockTransport(endpoint)
    ) as provider:
        try:
            with pytest.raises(NvidiaNimError, match="timed out"):
                await asyncio.wait_for(invoke(provider, method), timeout=0.2)
            assert started.is_set() and not requests
        finally:
            release.set()  # Credential reads may finish safely; they cannot dispatch HTTP.


async def test_cancelling_queue_wait_never_reaches_credentials_or_http(tmp_path):
    credentials = []
    settings = Settings.model_construct(
        model_preferences_path=tmp_path / "models.json", max_concurrency=1
    )
    async with NvidiaNimClient(
        settings,
        lambda: credentials.append(True),
        transport=httpx.MockTransport(lambda _: pytest.fail("unexpected HTTP")),
    ) as provider:
        await provider._semaphore.acquire()
        try:
            task = asyncio.create_task(invoke(provider, "complete"))
            await asyncio.sleep(0)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
            assert not credentials
        finally:
            provider._semaphore.release()
