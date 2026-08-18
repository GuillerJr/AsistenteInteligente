from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, ValidationError

from aegis_core.audio.contracts import AudioMeterSample, AudioSessionSnapshot
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


@dataclass(slots=True)
class _AudioSession:
    session_id: UUID
    opened_at: datetime
    samples_received: int = 0
    last_received_at: datetime | None = None
    last_sample: AudioMeterSample | None = None


class AudioTelemetryManager:
    """Stores only the latest meter sample for one explicitly opened microphone session."""

    def __init__(
        self,
        *,
        stale_after_seconds: float = 1.0,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        if stale_after_seconds <= 0:
            raise ValueError("audio stale interval must be positive")
        self._stale_after = timedelta(seconds=stale_after_seconds)
        self._clock = clock or (lambda: datetime.now(UTC))
        self._session: _AudioSession | None = None
        self._lock = asyncio.Lock()

    async def open(self) -> AudioSessionSnapshot:
        async with self._lock:
            if self._session is not None:
                raise AudioSessionActiveError("an audio session is already active")
            now = self._now()
            self._session = _AudioSession(session_id=uuid4(), opened_at=now)
            return self._snapshot(self._session, now=now)

    async def publish(
        self,
        session_id: UUID,
        sample: AudioMeterSample,
    ) -> AudioSessionSnapshot:
        async with self._lock:
            session = self._require_session(session_id)
            if session.last_sample is not None and sample.sequence <= session.last_sample.sequence:
                raise AudioSequenceError("audio sequence must increase monotonically")
            now = self._now()
            session.last_sample = sample
            session.last_received_at = now
            session.samples_received += 1
            return self._snapshot(session, now=now)

    async def status(self) -> AudioSessionSnapshot:
        async with self._lock:
            if self._session is None:
                return AudioSessionSnapshot(state="idle")
            return self._snapshot(self._session, now=self._now())

    async def close(self, session_id: UUID) -> UUID:
        async with self._lock:
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
            stale=stale,
        )


class PublishAudioMeterPayload(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    session_id: UUID
    sample: AudioMeterSample


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
                snapshot = await self._manager.publish(payload.session_id, payload.sample)
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
        return IpcHandlerResult(ok=True, payload=response_payload)
