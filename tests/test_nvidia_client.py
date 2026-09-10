import asyncio
import io
import json
import wave

import httpx
import pytest
from pydantic import ValidationError

from aegis_core.config import Settings
from aegis_core.contracts import AgentRole
from aegis_core.providers.base import EmbeddingInputType
from aegis_core.providers.nvidia import (
    NvidiaGlobalCooldown,
    NvidiaNimClient,
    NvidiaNimError,
    NvidiaNimRateLimited,
)


def _wav_bytes(*, frames: int = 441) -> bytes:
    output = io.BytesIO()
    with wave.open(output, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(44_100)
        wav.writeframes(b"\0\0" * frames)
    return output.getvalue()


def test_tts_configuration_requires_https_and_bounded_identifiers() -> None:
    with pytest.raises(ValidationError, match="must use HTTPS"):
        Settings(nvidia_tts_url="http://example.test/v1/audio/synthesize")
    with pytest.raises(ValidationError, match="must use HTTPS"):
        Settings(nvidia_tts_stream_url="http://example.test/v1/audio/synthesize_online")
    with pytest.raises(ValidationError):
        Settings(nvidia_tts_voice="invalid voice\n")
    with pytest.raises(ValidationError):
        Settings(nvidia_tts_language="../../secret")


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
async def test_complete_stream_publishes_ordered_text_deltas() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        assert payload["stream"] is True
        body = "\n\n".join(
            [
                'data: {"choices":[{"delta":{"content":"Hola"},"finish_reason":null}]}',
                "data: "
                '{"choices":[{"delta":{"content":" mundo"},"finish_reason":"stop"}],'
                '"usage":{"completion_tokens":2}}',
                "data: [DONE]",
            ]
        )
        return httpx.Response(200, text=body)

    chunks: list[str] = []
    client = NvidiaNimClient(
        Settings(), lambda: "secret-value", transport=httpx.MockTransport(handler)
    )
    async with client:
        result = await client.complete_stream(
            role=AgentRole.PLANNER,
            messages=[{"role": "user", "content": "saluda"}],
            on_delta=chunks.append,
        )

    assert chunks == ["Hola", " mundo"]
    assert result.content == "Hola mundo"
    assert result.finish_reason == "stop"
    assert result.raw_usage == {"completion_tokens": 2}


@pytest.mark.asyncio
async def test_synthesize_speech_uses_magpie_multipart_and_validates_wav() -> None:
    audio = _wav_bytes()

    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path.endswith("/v1/audio/synthesize")
        assert request.headers["Authorization"] == "Bearer secret-value"
        assert request.headers["Accept"] == "audio/wav"
        assert request.headers["Content-Type"].startswith("multipart/form-data;")
        body = request.content
        for value in (
            b"Sistemas en linea.",
            b"es-US",
            b"Magpie-Multilingual.ES-US.Diego",
            b"LINEAR_PCM",
            b"44100",
        ):
            assert value in body
        return httpx.Response(200, content=audio)

    client = NvidiaNimClient(
        Settings(),
        lambda: "secret-value",
        transport=httpx.MockTransport(handler),
    )
    async with client:
        result = await client.synthesize_speech("  Sistemas   en linea. ")

    assert result == audio
    assert b"secret-value" not in result


@pytest.mark.asyncio
async def test_stream_speech_uses_online_magpie_pcm_endpoint() -> None:
    pcm = b"\x01\x00" * 12_000

    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path.endswith("/v1/audio/synthesize_online")
        assert request.headers["Authorization"] == "Bearer secret-value"
        assert request.headers["Accept"] == "application/octet-stream"
        assert request.headers["Content-Type"].startswith("multipart/form-data;")
        for value in (
            b"Respuesta inmediata.",
            b"es-US",
            b"Magpie-Multilingual.ES-US.Diego",
            b"LINEAR_PCM",
            b"22050",
        ):
            assert value in request.content
        return httpx.Response(200, content=pcm)

    client = NvidiaNimClient(
        Settings(),
        lambda: "secret-value",
        transport=httpx.MockTransport(handler),
    )
    async with client:
        chunks = [chunk async for chunk in client.stream_speech("  Respuesta  inmediata. ")]

    assert b"".join(chunks) == pcm
    assert b"secret-value" not in b"".join(chunks)


@pytest.mark.asyncio
async def test_synthesize_speech_rejects_text_before_credentials_or_network() -> None:
    credential_reads = 0
    requests = 0

    def load_credential() -> str:
        nonlocal credential_reads
        credential_reads += 1
        return "secret-value"

    async def handler(_: httpx.Request) -> httpx.Response:
        nonlocal requests
        requests += 1
        return httpx.Response(200, content=_wav_bytes())

    client = NvidiaNimClient(
        Settings(),
        load_credential,
        transport=httpx.MockTransport(handler),
    )
    async with client:
        for text in ("", "   ", "x" * 2_001):
            with pytest.raises(ValueError, match="text is out of range"):
                await client.synthesize_speech(text)

    assert credential_reads == 0
    assert requests == 0


@pytest.mark.asyncio
async def test_synthesize_speech_rejects_malformed_audio_and_rate_limits() -> None:
    responses = iter(
        [
            httpx.Response(200, content=b"not-a-wave" * 10),
            httpx.Response(429),
        ]
    )

    async def handler(_: httpx.Request) -> httpx.Response:
        return next(responses)

    client = NvidiaNimClient(
        Settings(),
        lambda: "secret-value",
        transport=httpx.MockTransport(handler),
    )
    async with client:
        with pytest.raises(NvidiaNimError, match="invalid audio"):
            await client.synthesize_speech("Prueba de audio.")
        with pytest.raises(NvidiaNimRateLimited, match="speech rate limit"):
            await client.synthesize_speech("Prueba de limite.")
        with pytest.raises(NvidiaNimRateLimited, match="cooldown active"):
            await client.synthesize_speech("Prueba sin red.")


@pytest.mark.asyncio
async def test_synthesize_speech_has_a_hard_total_timeout() -> None:
    async def handler(_: httpx.Request) -> httpx.Response:
        await asyncio.sleep(0.1)
        return httpx.Response(200, content=_wav_bytes())

    settings = Settings.model_construct(nvidia_tts_timeout_seconds=0.01)
    client = NvidiaNimClient(
        settings,
        lambda: "secret-value",
        transport=httpx.MockTransport(handler),
    )
    async with client:
        with pytest.raises(NvidiaNimError, match="speech request timed out"):
            await client.synthesize_speech("Prueba de timeout.")


@pytest.mark.asyncio
async def test_complete_forwards_multimodal_content_without_exposing_it() -> None:
    content = [
        {"type": "text", "text": "describe"},
        {
            "type": "image_url",
            "image_url": {"url": "data:image/png;base64,iVBORw0KGgo="},
        },
    ]

    async def handler(request: httpx.Request) -> httpx.Response:
        assert json.loads(request.content)["messages"] == [{"role": "user", "content": content}]
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": "imagen"}}]},
        )

    client = NvidiaNimClient(
        Settings(), lambda: "secret-value", transport=httpx.MockTransport(handler)
    )
    async with client:
        result = await client.complete(
            role=AgentRole.VISION,
            messages=[{"role": "user", "content": content}],
        )

    assert result.content == "imagen"
    assert "iVBORw0KGgo=" not in repr(result)


@pytest.mark.asyncio
async def test_complete_rejects_unapproved_options_before_credentials_or_network() -> None:
    credential_reads = 0
    requests = 0

    def load_credential() -> str:
        nonlocal credential_reads
        credential_reads += 1
        return "secret-value"

    async def handler(_: httpx.Request) -> httpx.Response:
        nonlocal requests
        requests += 1
        return httpx.Response(500)

    client = NvidiaNimClient(
        Settings(),
        load_credential,
        transport=httpx.MockTransport(handler),
    )
    async with client:
        for key in ("messages", "model", "stream", "max_tokens", "unknown"):
            with pytest.raises(ValueError, match="unapproved option"):
                await client.complete(
                    role=AgentRole.ROUTER,
                    messages=[{"role": "user", "content": "hola"}],
                    extra_body={key: "override"},
                )

    assert credential_reads == 0
    assert requests == 0


@pytest.mark.asyncio
async def test_complete_forwards_approved_options() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        assert payload["response_format"] == {"type": "json_object"}
        assert payload["chat_template_kwargs"] == {"enable_thinking": False}
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": "ok"}}]},
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
            extra_body={
                "response_format": {"type": "json_object"},
                "chat_template_kwargs": {"enable_thinking": False},
            },
        )

    assert result.content == "ok"


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
async def test_rate_limit_cooldown_fails_locally_and_recovers() -> None:
    credential_reads = 0
    requests = 0

    def load_credential() -> str:
        nonlocal credential_reads
        credential_reads += 1
        return "secret-value"

    async def handler(_: httpx.Request) -> httpx.Response:
        nonlocal requests
        requests += 1
        return httpx.Response(429)

    settings = Settings.model_construct(nvidia_rate_limit_cooldown_seconds=0.02)
    client = NvidiaNimClient(
        settings,
        load_credential,
        transport=httpx.MockTransport(handler),
    )
    async with client:
        with pytest.raises(NvidiaNimRateLimited):
            await client.complete(
                role=AgentRole.ROUTER,
                messages=[{"role": "user", "content": "hola"}],
            )
        with pytest.raises(NvidiaNimRateLimited, match="cooldown active"):
            await client.embed(["consulta"], input_type=EmbeddingInputType.QUERY)
        assert credential_reads == 1
        assert requests == 1  # 429 opens cooldown before trying another model.
        await asyncio.sleep(0.03)
        with pytest.raises(NvidiaNimRateLimited, match="embedding rate limit"):
            await client.embed(["consulta"], input_type=EmbeddingInputType.QUERY)

    assert credential_reads == 2
    assert requests == 2


@pytest.mark.asyncio
async def test_rate_limit_cooldown_is_shared_by_all_network_agents() -> None:
    first_requests = 0
    second_requests = 0

    async def first_handler(_: httpx.Request) -> httpx.Response:
        nonlocal first_requests
        first_requests += 1
        return httpx.Response(429)

    async def second_handler(_: httpx.Request) -> httpx.Response:
        nonlocal second_requests
        second_requests += 1
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": "must not reach network"}}]},
        )

    cooldown = NvidiaGlobalCooldown()
    settings = Settings.model_construct(nvidia_rate_limit_cooldown_seconds=5.0)
    first = NvidiaNimClient(
        settings,
        lambda: "secret-value",
        transport=httpx.MockTransport(first_handler),
        cooldown=cooldown,
    )
    second = NvidiaNimClient(
        settings,
        lambda: "secret-value",
        transport=httpx.MockTransport(second_handler),
        cooldown=cooldown,
    )
    try:
        with pytest.raises(NvidiaNimRateLimited):
            await first.complete(
                role=AgentRole.ROUTER,
                messages=[{"role": "user", "content": "hola"}],
            )
        with pytest.raises(NvidiaNimRateLimited, match="cooldown active"):
            await second.complete(
                role=AgentRole.PLANNER,
                messages=[{"role": "user", "content": "planifica"}],
            )
    finally:
        await first.aclose()
        await second.aclose()

    assert first_requests == 1
    assert second_requests == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("recoverable_status", [202, 410, 503])
async def test_complete_uses_registered_fallback_once(recoverable_status: int) -> None:
    requested_models: list[str] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        model_id = json.loads(request.content)["model"]
        requested_models.append(model_id)
        if len(requested_models) == 1:
            return httpx.Response(recoverable_status, json={"detail": "model unavailable"})
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": "ok"}, "finish_reason": "stop"}]},
        )

    client = NvidiaNimClient(
        Settings(), lambda: "secret-value", transport=httpx.MockTransport(handler)
    )
    async with client:
        result = await client.complete(
            role=AgentRole.ROUTER,
            messages=[{"role": "user", "content": "hola"}],
        )

    assert requested_models == [
        "nvidia/nemotron-3.5-lightning-30b-a3b",
        "nvidia/nemotron-3-nano-30b-a3b",
    ]
    assert result.model_id == "nvidia/nemotron-3-nano-30b-a3b"


@pytest.mark.asyncio
async def test_complete_falls_back_from_an_empty_primary_response() -> None:
    requested_models: list[str] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        model_id = json.loads(request.content)["model"]
        requested_models.append(model_id)
        content = "" if len(requested_models) == 1 else "ok"
        return httpx.Response(200, json={"choices": [{"message": {"content": content}}]})

    client = NvidiaNimClient(
        Settings(), lambda: "secret-value", transport=httpx.MockTransport(handler)
    )
    async with client:
        result = await client.complete(
            role=AgentRole.ROUTER,
            messages=[{"role": "user", "content": "hola"}],
        )

    assert requested_models == [
        "nvidia/nemotron-3.5-lightning-30b-a3b",
        "nvidia/nemotron-3-nano-30b-a3b",
    ]
    assert result.model_id == "nvidia/nemotron-3-nano-30b-a3b"


@pytest.mark.asyncio
async def test_complete_skips_a_retired_primary_for_the_client_lifetime() -> None:
    requested_models: list[str] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        model_id = json.loads(request.content)["model"]
        requested_models.append(model_id)
        if model_id == "nvidia/nemotron-3.5-lightning-30b-a3b":
            return httpx.Response(404, json={"detail": "retired"})
        return httpx.Response(200, json={"choices": [{"message": {"content": "ok"}}]})

    client = NvidiaNimClient(
        Settings(), lambda: "secret-value", transport=httpx.MockTransport(handler)
    )
    async with client:
        for _ in range(2):
            await client.complete(
                role=AgentRole.ROUTER,
                messages=[{"role": "user", "content": "hola"}],
            )

    assert requested_models == [
        "nvidia/nemotron-3.5-lightning-30b-a3b",
        "nvidia/nemotron-3-nano-30b-a3b",
        "nvidia/nemotron-3-nano-30b-a3b",
    ]


@pytest.mark.asyncio
async def test_complete_does_not_fallback_on_authentication_error() -> None:
    requests = 0

    async def handler(_: httpx.Request) -> httpx.Response:
        nonlocal requests
        requests += 1
        return httpx.Response(401, json={"detail": "unauthorized"})

    client = NvidiaNimClient(
        Settings(), lambda: "secret-value", transport=httpx.MockTransport(handler)
    )
    async with client:
        with pytest.raises(NvidiaNimError, match="HTTP 401"):
            await client.complete(
                role=AgentRole.ROUTER,
                messages=[{"role": "user", "content": "hola"}],
            )

    assert requests == 1


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
async def test_complete_bounds_concurrent_nvidia_requests() -> None:
    active = 0
    calls = 0
    peak = 0
    capacity_reached = asyncio.Event()
    release = asyncio.Event()

    async def handler(_: httpx.Request) -> httpx.Response:
        nonlocal active, calls, peak
        calls += 1
        active += 1
        peak = max(peak, active)
        if active == 2:
            capacity_reached.set()
        try:
            await release.wait()
            return httpx.Response(
                200,
                json={"choices": [{"message": {"content": "ok"}}]},
            )
        finally:
            active -= 1

    client = NvidiaNimClient(
        Settings(max_concurrency=2),
        lambda: "secret-value",
        transport=httpx.MockTransport(handler),
    )
    async with client:
        tasks = [
            asyncio.create_task(
                client.complete(
                    role=AgentRole.ROUTER,
                    messages=[{"role": "user", "content": f"carga-{index}"}],
                )
            )
            for index in range(6)
        ]
        await asyncio.wait_for(capacity_reached.wait(), timeout=1)
        await asyncio.sleep(0)
        assert calls == 2
        assert peak == 2
        release.set()
        results = await asyncio.gather(*tasks)

    assert calls == 6
    assert peak == 2
    assert [result.content for result in results] == ["ok"] * 6


@pytest.mark.asyncio
async def test_complete_uses_one_timeout_budget_across_fallbacks() -> None:
    requested_models: list[str] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        model_id = json.loads(request.content)["model"]
        requested_models.append(model_id)
        if len(requested_models) == 1:
            await asyncio.sleep(0.12)
            return httpx.Response(503)
        if len(requested_models) == 2:
            await asyncio.sleep(0.12)
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": "recovered"}}]},
        )

    settings = Settings.model_construct(request_timeout_seconds=0.2)
    client = NvidiaNimClient(
        settings,
        lambda: "secret-value",
        transport=httpx.MockTransport(handler),
    )
    async with client:
        with pytest.raises(NvidiaNimError, match="request timed out"):
            await client.complete(
                role=AgentRole.ROUTER,
                messages=[{"role": "user", "content": "hola"}],
            )
        result = await client.complete(
            role=AgentRole.ROUTER,
            messages=[{"role": "user", "content": "recupera"}],
        )

    assert result.content == "recovered"
    assert requested_models == [
        "nvidia/nemotron-3.5-lightning-30b-a3b",
        "nvidia/nemotron-3-nano-30b-a3b",
        "nvidia/nemotron-3.5-lightning-30b-a3b",
    ]


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
async def test_complete_rejects_multiple_tool_calls_before_authorization() -> None:
    raw_call = {
        "id": "call-1",
        "type": "function",
        "function": {
            "name": "web_research",
            "arguments": '{"query":"NVIDIA NIM"}',
        },
    }

    async def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {"content": None, "tool_calls": [raw_call, raw_call]},
                        "finish_reason": "tool_calls",
                    }
                ]
            },
        )

    client = NvidiaNimClient(
        Settings(), lambda: "secret-value", transport=httpx.MockTransport(handler)
    )
    async with client:
        with pytest.raises(NvidiaNimError, match="too many tool calls"):
            await client.complete(
                role=AgentRole.PLANNER,
                messages=[{"role": "user", "content": "investiga y revisa correo"}],
            )


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
