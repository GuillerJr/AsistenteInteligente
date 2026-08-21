from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Literal
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, ValidationError

from aegis_core.audio.contracts import (
    AudioMeterSample,
    AudioSessionSnapshot,
    SpeechActivityEvent,
    SpeechEventType,
)
from aegis_core.ipc.protocol import IpcRequest
from aegis_core.ipc.server import IpcHandlerResult, IpcMethodHandler


class AudioSessionError(RuntimeError):
    """Base error for the in-memory audio telemetry boundary."""


class AudioSessionActiveError(AudioSessionError):
    pass


class AudioSessionNotFoundError(AudioSessionError):
    pass


class AudioSequenceError(AudioSessionError):
    pass


class AudioSpeechTransitionError(AudioSessionError):
    pass


@dataclass(slots=True)
class _AudioSession:
    session_id: UUID
    opened_at: datetime
    samples_received: int = 0
    last_received_at: datetime | None = None
    last_sample: AudioMeterSample | None = None
    speech_state: Literal["idle", "speaking"] = "idle"
    active_utterance_id: UUID | None = None
    last_speech_event: SpeechActivityEvent | None = None


class AudioTelemetryManager:
    """Stores only the latest meter sample for one explicitly opened microphone session."""

    def __init__(
        self,
        *,
        stale_after_seconds: float = 1.0,
        lease_timeout_seconds: float = 5.0,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        if stale_after_seconds <= 0:
            raise ValueError("audio stale interval must be positive")
        if lease_timeout_seconds <= stale_after_seconds:
            raise ValueError("audio lease must exceed stale interval")
        self._stale_after = timedelta(seconds=stale_after_seconds)
        self._lease_timeout = timedelta(seconds=lease_timeout_seconds)
        self._clock = clock or (lambda: datetime.now(UTC))
        self._session: _AudioSession | None = None
        self._lock = asyncio.Lock()

    async def open(self) -> AudioSessionSnapshot:
        async with self._lock:
            self._expire_inactive(self._now())
            if self._session is not None:
                raise AudioSessionActiveError("an audio session is already active")
            now = self._now()
            self._session = _AudioSession(session_id=uuid4(), opened_at=now)
            return self._snapshot(self._session, now=now)

    async def publish(
        self,
        session_id: UUID,
        sample: AudioMeterSample,
        speech_event: SpeechActivityEvent | None = None,
    ) -> AudioSessionSnapshot:
        async with self._lock:
            self._expire_inactive(self._now())
            session = self._require_session(session_id)
            if session.last_sample is not None and (
                sample.sequence <= session.last_sample.sequence
                or sample.monotonic_nanoseconds <= session.last_sample.monotonic_nanoseconds
            ):
                raise AudioSequenceError("audio sequence and monotonic time must increase")
            if speech_event is not None:
                self._validate_speech_event(session, sample, speech_event)
            now = self._now()
            session.last_sample = sample
            session.last_received_at = now
            session.samples_received += 1
            if speech_event is not None:
                self._apply_speech_event(session, speech_event)
            return self._snapshot(session, now=now)

    async def status(self) -> AudioSessionSnapshot:
        async with self._lock:
            self._expire_inactive(self._now())
            if self._session is None:
                return AudioSessionSnapshot(state="idle")
            return self._snapshot(self._session, now=self._now())

    async def close(self, session_id: UUID) -> UUID:
        async with self._lock:
            self._expire_inactive(self._now())
            self._require_session(session_id)
            self._session = None
            return session_id

    def _require_session(self, session_id: UUID) -> _AudioSession:
        if self._session is None or self._session.session_id != session_id:
            raise AudioSessionNotFoundError("audio session does not exist")
        return self._session

    def _now(self) -> datetime:
        now = self._clock()
        if now.tzinfo is None or now.utcoffset() is None:
            raise ValueError("audio clock must return a timezone-aware timestamp")
        return now

    def _expire_inactive(self, now: datetime) -> None:
        if self._session is None:
            return
        last_activity = self._session.last_received_at or self._session.opened_at
        if now - last_activity >= self._lease_timeout:
            self._session = None

    @staticmethod
    def _validate_speech_event(
        session: _AudioSession,
        sample: AudioMeterSample,
        event: SpeechActivityEvent,
    ) -> None:
        if (
            event.sample_sequence != sample.sequence
            or event.monotonic_nanoseconds != sample.monotonic_nanoseconds
        ):
            raise AudioSpeechTransitionError("speech event is not bound to its meter sample")
        if session.last_speech_event is not None and (
            event.sample_sequence <= session.last_speech_event.sample_sequence
        ):
            raise AudioSpeechTransitionError("speech event sequence must increase")
        if event.event is SpeechEventType.STARTED:
            if session.speech_state != "idle" or not sample.voice_active:
                raise AudioSpeechTransitionError("speech start does not match session state")
        elif (
            session.speech_state != "speaking"
            or session.active_utterance_id != event.utterance_id
        ):
            raise AudioSpeechTransitionError("speech end does not match active utterance")
        elif (
            session.last_speech_event is None
            or session.last_speech_event.event is not SpeechEventType.STARTED
            or event.duration_milliseconds
            != (
                event.monotonic_nanoseconds
                - session.last_speech_event.monotonic_nanoseconds
            )
            // 1_000_000
        ):
            raise AudioSpeechTransitionError("speech duration does not match monotonic time")

    @staticmethod
    def _apply_speech_event(session: _AudioSession, event: SpeechActivityEvent) -> None:
        if event.event is SpeechEventType.STARTED:
            session.speech_state = "speaking"
            session.active_utterance_id = event.utterance_id
        else:
            session.speech_state = "idle"
            session.active_utterance_id = None
        session.last_speech_event = event

    def _snapshot(self, session: _AudioSession, *, now: datetime) -> AudioSessionSnapshot:
        stale = (
            session.last_received_at is not None
            and now - session.last_received_at >= self._stale_after
        )
        return AudioSessionSnapshot(
            state="active",
            session_id=session.session_id,
            opened_at=session.opened_at,
            samples_received=session.samples_received,
            last_received_at=session.last_received_at,
            last_sample=session.last_sample,
            speech_state=session.speech_state,
            active_utterance_id=session.active_utterance_id,
            last_speech_event=session.last_speech_event,
            stale=stale,
        )


class PublishAudioMeterPayload(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    session_id: UUID
    sample: AudioMeterSample
    speech_event: SpeechActivityEvent | None = None


class CloseAudioSessionPayload(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    session_id: UUID


class AudioTelemetryIpcService:
    METHODS = frozenset(
        {
            "audio.session.open",
            "audio.meter.publish",
            "audio.meter.status",
            "audio.session.close",
        }
    )

    def __init__(self, manager: AudioTelemetryManager) -> None:
        self._manager = manager

    def handlers(self) -> dict[str, IpcMethodHandler]:
        return {method: self.handle for method in self.METHODS}

    async def handle(self, request: IpcRequest) -> IpcHandlerResult:
        try:
            if request.method == "audio.session.open":
                if request.payload:
                    return IpcHandlerResult(ok=False, error_code="invalid_payload")
                snapshot = await self._manager.open()
                response_payload = snapshot.model_dump(mode="json")
            elif request.method == "audio.meter.publish":
                payload = PublishAudioMeterPayload.model_validate(request.payload)
                snapshot = await self._manager.publish(
                    payload.session_id,
                    payload.sample,
                    payload.speech_event,
                )
                response_payload = snapshot.model_dump(mode="json")
            elif request.method == "audio.meter.status":
                if request.payload:
                    return IpcHandlerResult(ok=False, error_code="invalid_payload")
                snapshot = await self._manager.status()
                response_payload = snapshot.model_dump(mode="json")
            elif request.method == "audio.session.close":
                payload = CloseAudioSessionPayload.model_validate(request.payload)
                session_id = await self._manager.close(payload.session_id)
                response_payload = {"session_id": str(session_id), "status": "closed"}
            else:
                return IpcHandlerResult(ok=False, error_code="method_not_found")
        except (ValidationError, ValueError):
            return IpcHandlerResult(ok=False, error_code="invalid_payload")
        except AudioSessionActiveError:
            return IpcHandlerResult(ok=False, error_code="audio_session_active")
        except AudioSessionNotFoundError:
            return IpcHandlerResult(ok=False, error_code="audio_session_not_found")
        except AudioSequenceError:
            return IpcHandlerResult(ok=False, error_code="audio_sequence_rejected")
        except AudioSpeechTransitionError:
            return IpcHandlerResult(ok=False, error_code="audio_speech_transition_rejected")
        return IpcHandlerResult(ok=True, payload=response_payload)
