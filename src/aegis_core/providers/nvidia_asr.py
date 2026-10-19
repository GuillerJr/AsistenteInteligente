"""Hosted NVIDIA Canary transcription, separate from local voice identity."""

from __future__ import annotations

import io
import wave

from aegis_core.providers.nvidia import NvidiaNimError


async def transcribe(client, audio: bytes) -> str:
    import grpc
    from riva.client.proto import riva_asr_pb2, riva_asr_pb2_grpc, riva_audio_pb2

    if not 1 <= len(audio) <= 8_000_000:
        raise ValueError("audio is out of range")
    if audio.startswith(b"OggS") and b"OpusHead" in audio[:128]:
        encoding, rate = riva_audio_pb2.OGGOPUS, 48000
    elif audio.startswith(b"RIFF") and audio[8:12] == b"WAVE":
        with wave.open(io.BytesIO(audio), "rb") as wav:
            if (
                wav.getnchannels() != 1
                or wav.getsampwidth() != 2
                or not 8000 <= wav.getframerate() <= 48000
                or wav.getnframes() > wav.getframerate() * 120
            ):
                raise ValueError("invalid voice input")
            encoding, rate = riva_audio_pb2.LINEAR_PCM, wav.getframerate()
            audio = wav.readframes(wav.getnframes())
    else:
        raise ValueError("use a Telegram voice note or mono WAV")
    config = riva_asr_pb2.RecognitionConfig(
        encoding=encoding,
        sample_rate_hertz=rate,
        language_code="es-ES",
        max_alternatives=1,
        audio_channel_count=1,
        enable_automatic_punctuation=True,
        custom_configuration={"task": "transcribe", "source_language": "es-ES"},
    )
    metadata = (
        ("function-id", "b0e8b4a5-217c-40b7-9b96-17d84e666317"),
        ("authorization", client._authorization_value()),
    )
    async with client._semaphore:
        client._raise_if_rate_limited()
        try:
            async with grpc.aio.secure_channel(
                "grpc.nvcf.nvidia.com:443",
                grpc.ssl_channel_credentials(),
                options=[
                    ("grpc.max_send_message_length", 8_388_608),
                    ("grpc.max_receive_message_length", 131072),
                ],
            ) as channel:
                response = await riva_asr_pb2_grpc.RivaSpeechRecognitionStub(channel).Recognize(
                    riva_asr_pb2.RecognizeRequest(config=config, audio=audio),
                    metadata=metadata,
                    timeout=40,
                )
        except (grpc.RpcError, TimeoutError) as error:
            raise NvidiaNimError("NVIDIA voice transcription unavailable") from error
    text = " ".join(
        item.alternatives[0].transcript for item in response.results if item.alternatives
    ).strip()
    if not text or len(text.encode("utf-8")) > 4096:
        raise NvidiaNimError("NVIDIA voice transcription incomplete")
    return text
