import asyncio
import io
import wave
from types import SimpleNamespace
from unittest.mock import AsyncMock

import grpc
import pytest
from riva.client.proto import riva_asr_pb2, riva_asr_pb2_grpc, riva_audio_pb2

from aegis_core.providers.nvidia_asr import transcribe


def wav():
    output = io.BytesIO()
    with wave.open(output, "wb") as handle:
        handle.setparams((1, 2, 16000, 0, "NONE", "not compressed"))
        handle.writeframes(b"\0\0" * 1600)
    return output.getvalue()


@pytest.mark.parametrize(
    "audio,encoding",
    [
        (wav(), riva_audio_pb2.LINEAR_PCM),
        (b"OggS" + b"\0" * 24 + b"OpusHead" + b"\0" * 24, riva_audio_pb2.OGGOPUS),
    ],
)
async def test_asr_uses_hosted_canary_spanish_and_owner_key(monkeypatch, audio, encoding):
    recognize = AsyncMock(
        return_value=riva_asr_pb2.RecognizeResponse(
            results=[
                riva_asr_pb2.SpeechRecognitionResult(
                    alternatives=[
                        riva_asr_pb2.SpeechRecognitionAlternative(transcript="Hola Jarvis")
                    ]
                ),
            ]
        )
    )

    class Channel:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

    monkeypatch.setattr(grpc.aio, "secure_channel", lambda *args, **kwargs: Channel())
    monkeypatch.setattr(
        riva_asr_pb2_grpc,
        "RivaSpeechRecognitionStub",
        lambda channel: SimpleNamespace(Recognize=recognize),
    )
    client = SimpleNamespace(
        _semaphore=asyncio.Semaphore(1),
        _authorization_value=lambda: "Bearer TEST-ONLY",
        _raise_if_rate_limited=lambda: None,
    )
    assert await transcribe(client, audio) == "Hola Jarvis"
    request = recognize.call_args.args[0]
    assert request.config.encoding == encoding
    assert request.config.language_code == "es-ES"
    assert ("authorization", "Bearer TEST-ONLY") in recognize.call_args.kwargs["metadata"]
    assert recognize.call_args.kwargs["timeout"] == 40


async def test_asr_rejects_unbounded_or_invalid_audio_before_network():
    client = SimpleNamespace(_semaphore=asyncio.Semaphore(1))
    for audio in (b"", b"not-audio", b"x" * 8000001):
        with pytest.raises(ValueError):
            await transcribe(client, audio)
