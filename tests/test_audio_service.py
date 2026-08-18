from __future__ import annotations

import tempfile
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from pydantic import ValidationError

from aegis_core.audio import (
    AudioMeterSample,
    AudioTelemetryIpcService,
    AudioTelemetryManager,
    SpeechActivityEvent,
    SpeechEventType,
)
from aegis_core.ipc.client import IpcClient
from aegis_core.ipc.protocol import IpcAuthenticator
from aegis_core.ipc.server import AegisDaemon

AUTHENTICATOR = IpcAuthenticator(bytes.fromhex("77" * 32))


@pytest.fixture
def ipc_root() -> Iterator[Path]:
    with tempfile.TemporaryDirectory(prefix="aa-", dir="/private/tmp") as value:
        root = Path(value)
        root.chmod(0o700)
        yield root


def sample(
    sequence: int = 0,
    *,
    monotonic_nanoseconds: int | None = None,
    rms: float = 0.2,
    voice_active: bool = True,
) -> AudioMeterSample:
    return AudioMeterSample(
        sequence=sequence,
        monotonic_nanoseconds=monotonic_nanoseconds or 123_000_000 + sequence,
        sample_rate_hz=48_000,
        frame_count=2_400,
        rms=rms,
        peak=0.4,
        dbfs=-13.98,
        activity=0.8,
        voice_active=voice_active,
        clipped=False,
    )


def speech_event(
    event: SpeechEventType,
    utterance_id: str,
    sequence: int,
    *,
    duration_milliseconds: int | None = None,
) -> SpeechActivityEvent:
    return SpeechActivityEvent(
        event=event,
        utterance_id=utterance_id,
        sample_sequence=sequence,
        monotonic_nanoseconds=123_000_000 + sequence,
        duration_milliseconds=duration_milliseconds,
    )


def test_audio_sample_rejects_reconstructive_or_invalid_fields() -> None:
    payload = sample().model_dump()
    payload["pcm"] = "AAAA"
    with pytest.raises(ValidationError):
        AudioMeterSample.model_validate(payload)

    payload = sample().model_dump()
    payload["peak"] = 0.1
    with pytest.raises(ValidationError, match="peak"):
        AudioMeterSample.model_validate(payload)


@pytest.mark.asyncio
async def test_audio_manager_retains_only_latest_monotonic_sample() -> None:
    manager = AudioTelemetryManager()
    opened = await manager.open()

    first = await manager.publish(opened.session_id, sample(10))  # type: ignore[arg-type]
    second = await manager.publish(opened.session_id, sample(11))  # type: ignore[arg-type]

    assert first.samples_received == 1
    assert second.samples_received == 2
    assert second.last_sample == sample(11)
    assert not hasattr(second, "pcm")


@pytest.mark.asyncio
async def test_audio_manager_rejects_replayed_sequence() -> None:
    manager = AudioTelemetryManager()
    opened = await manager.open()
    session_id = opened.session_id
    assert session_id is not None
    await manager.publish(session_id, sample(5))

    with pytest.raises(RuntimeError, match="monotonically"):
        await manager.publish(session_id, sample(5))


@pytest.mark.asyncio
async def test_audio_manager_applies_atomic_speech_transitions() -> None:
    manager = AudioTelemetryManager()
    opened = await manager.open()
    session_id = opened.session_id
    assert session_id is not None
    utterance_id = "01234567-89ab-cdef-0123-456789abcdef"

    speaking = await manager.publish(
        session_id,
        sample(10),
        speech_event(SpeechEventType.STARTED, utterance_id, 10),
    )
    idle = await manager.publish(
        session_id,
        sample(11, rms=0.001, voice_active=False),
        speech_event(
            SpeechEventType.ENDED,
            utterance_id,
            11,
            duration_milliseconds=750,
        ),
    )

    assert speaking.speech_state == "speaking"
    assert str(speaking.active_utterance_id) == utterance_id
    assert idle.speech_state == "idle"
    assert idle.active_utterance_id is None
    assert idle.last_speech_event is not None
    assert idle.last_speech_event.duration_milliseconds == 750


@pytest.mark.asyncio
async def test_audio_manager_rejects_unbound_or_invalid_speech_transition() -> None:
    manager = AudioTelemetryManager()
    opened = await manager.open()
    session_id = opened.session_id
    assert session_id is not None
    utterance_id = "01234567-89ab-cdef-0123-456789abcdef"

    with pytest.raises(RuntimeError, match="active utterance"):
        await manager.publish(
            session_id,
            sample(1, rms=0.001, voice_active=False),
            speech_event(
                SpeechEventType.ENDED,
                utterance_id,
                1,
                duration_milliseconds=100,
            ),
        )

    unbound = speech_event(SpeechEventType.STARTED, utterance_id, 3)
    with pytest.raises(RuntimeError, match="not bound"):
        await manager.publish(session_id, sample(2), unbound)


@pytest.mark.asyncio
async def test_audio_status_marks_old_measurement_stale() -> None:
    now = datetime.now(UTC)
    clock_value = [now]
    manager = AudioTelemetryManager(
        stale_after_seconds=0.1,
        clock=lambda: clock_value[0],
    )
    opened = await manager.open()
    session_id = opened.session_id
    assert session_id is not None
    await manager.publish(session_id, sample())
    clock_value[0] = now + timedelta(seconds=1)

    status = await manager.status()

    assert status.stale is True


@pytest.mark.asyncio
async def test_audio_ipc_lifecycle_is_authenticated_and_bounded(ipc_root: Path) -> None:
    socket_path = ipc_root / "aegis.sock"
    service = AudioTelemetryIpcService(AudioTelemetryManager())
    client = IpcClient(socket_path, AUTHENTICATOR)

    async with AegisDaemon(socket_path, AUTHENTICATOR, handlers=service.handlers()):
        opened = await client.call("audio.session.open")
        session_id = opened.payload["session_id"]
        utterance_id = "01234567-89ab-cdef-0123-456789abcdef"
        published = await client.call(
            "audio.meter.publish",
            {
                "session_id": session_id,
                "sample": sample(1).model_dump(mode="json"),
                "speech_event": speech_event(
                    SpeechEventType.STARTED,
                    utterance_id,
                    1,
                ).model_dump(mode="json"),
            },
        )
        status = await client.call("audio.meter.status")
        closed = await client.call("audio.session.close", {"session_id": session_id})
        idle = await client.call("audio.meter.status")

    assert opened.ok is True
    assert published.payload["samples_received"] == 1
    assert published.payload["speech_state"] == "speaking"
    assert published.payload["active_utterance_id"] == utterance_id
    assert status.payload["last_sample"]["sequence"] == 1
    assert closed.payload["status"] == "closed"
    assert idle.payload == {
        "state": "idle",
        "session_id": None,
        "opened_at": None,
        "samples_received": 0,
        "last_received_at": None,
        "last_sample": None,
        "speech_state": "idle",
        "active_utterance_id": None,
        "last_speech_event": None,
        "stale": False,
    }


@pytest.mark.asyncio
async def test_audio_ipc_rejects_parallel_session_and_raw_pcm(ipc_root: Path) -> None:
    socket_path = ipc_root / "aegis.sock"
    service = AudioTelemetryIpcService(AudioTelemetryManager())
    client = IpcClient(socket_path, AUTHENTICATOR)

    async with AegisDaemon(socket_path, AUTHENTICATOR, handlers=service.handlers()):
        opened = await client.call("audio.session.open")
        duplicate = await client.call("audio.session.open")
        raw_sample = sample().model_dump(mode="json")
        raw_sample["pcm"] = "sensitive-audio"
        rejected = await client.call(
            "audio.meter.publish",
            {"session_id": opened.payload["session_id"], "sample": raw_sample},
        )
        invalid_transition = await client.call(
            "audio.meter.publish",
            {
                "session_id": opened.payload["session_id"],
                "sample": sample(1, rms=0.001, voice_active=False).model_dump(mode="json"),
                "speech_event": speech_event(
                    SpeechEventType.ENDED,
                    "01234567-89ab-cdef-0123-456789abcdef",
                    1,
                    duration_milliseconds=100,
                ).model_dump(mode="json"),
            },
        )

    assert duplicate.ok is False
    assert duplicate.error_code == "audio_session_active"
    assert rejected.ok is False
    assert rejected.error_code == "invalid_payload"
    assert invalid_transition.ok is False
    assert invalid_transition.error_code == "audio_speech_transition_rejected"
