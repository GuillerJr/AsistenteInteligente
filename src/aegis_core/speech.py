from __future__ import annotations

import asyncio
import base64
import hashlib
import os
import re
import secrets
import stat
import threading
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    ValidationError,
    ValidationInfo,
    field_validator,
)

from aegis_core.ipc.protocol import IpcRequest
from aegis_core.ipc.server import IpcHandlerResult, IpcMethodHandler
from aegis_core.providers.nvidia import NvidiaNimError

_TOKEN_PATTERN = re.compile(r"^[0-9a-f]{32}$")
_FILE_PATTERN = re.compile(r"^jarvis-tts-([0-9a-f]{32})\.wav$")


class SpeechArtifactError(RuntimeError):
    pass


class SpeechArtifact(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    token: str = Field(pattern=r"^[0-9a-f]{32}$")
    file_name: str = Field(pattern=r"^jarvis-tts-[0-9a-f]{32}\.wav$")
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    byte_count: int = Field(ge=44, le=8_388_608)

    @field_validator("file_name")
    @classmethod
    def file_name_must_match_token(cls, value: str, info: ValidationInfo) -> str:
        data = info.data
        if data.get("token") and value != f"jarvis-tts-{data['token']}.wav":
            raise ValueError("speech artifact file does not match token")
        return value


class SynthesizeSpeechPayload(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    text: str = Field(min_length=1, max_length=2_000)

    @field_validator("text")
    @classmethod
    def text_must_be_bounded(cls, value: str) -> str:
        normalized = " ".join(value.split())
        if not normalized or len(normalized.encode("utf-8")) > 8_192:
            raise ValueError("speech text is out of range")
        return normalized


class OpenSpeechStreamPayload(SynthesizeSpeechPayload):
    group_token: str = Field(pattern=r"^[0-9a-f]{32}$")


class ReleaseSpeechPayload(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    token: str = Field(pattern=r"^[0-9a-f]{32}$")


class PullSpeechStreamPayload(ReleaseSpeechPayload):
    after_sequence: int = Field(ge=1, le=1_000_000)


class CancelSpeechStreamsPayload(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    group_token: str = Field(pattern=r"^[0-9a-f]{32}$")


class SpeechStreamError(RuntimeError):
    pass


class SpeechStreamConflict(SpeechStreamError):
    pass


class SpeechStreamCapacityError(SpeechStreamError):
    pass


@dataclass(slots=True)
class _SpeechStreamSession:
    iterator: AsyncIterator[bytes]
    group_token: str
    last_access: float
    pending: bytearray = field(default_factory=bytearray)
    sequence: int = 0
    total_bytes: int = 0
    provider_done: bool = False
    cancelled: bool = False
    active_pull: asyncio.Task[object] | None = None
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)


class SpeechStreamManager:
    SAMPLE_RATE_HZ = 22_050
    CHANNELS = 1
    SAMPLE_WIDTH_BYTES = 2
    # A 4 KiB PCM block is about 92.9 ms at 22.05 kHz/16-bit/mono. The
    # manager may return a smaller provider fragment immediately, but never a
    # larger block, so the native player can meet its 150 ms playout budget.
    CHUNK_BYTES = 4_096
    MAX_AUDIO_BYTES = 8_388_608

    def __init__(
        self,
        synthesizer: Callable[[str], AsyncIterator[bytes]],
        *,
        ttl_seconds: float = 45,
        max_sessions: int = 4,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if ttl_seconds <= 0 or not 1 <= max_sessions <= 16:
            raise ValueError("speech stream limits are invalid")
        self._synthesize = synthesizer
        self._ttl_seconds = ttl_seconds
        self._max_sessions = max_sessions
        self._clock = clock
        self._sessions: dict[str, _SpeechStreamSession] = {}
        self._lock = asyncio.Lock()

    async def open(self, text: str, *, group_token: str) -> dict[str, object]:
        if not _TOKEN_PATTERN.fullmatch(group_token):
            raise ValueError("speech stream group token is invalid")
        await self._cleanup_expired()
        async with self._lock:
            if len(self._sessions) >= self._max_sessions:
                raise SpeechStreamCapacityError("speech stream capacity reached")
            iterator = self._synthesize(text)
            if not hasattr(iterator, "__anext__"):
                raise SpeechStreamError("speech stream provider is invalid")
            token = secrets.token_hex(16)
            session = _SpeechStreamSession(
                iterator=iterator,
                group_token=group_token,
                last_access=self._clock(),
            )
            self._sessions[token] = session
        try:
            return await self.pull(token, after_sequence=0)
        except Exception:
            await self.close(token)
            raise

    async def pull(self, token: str, *, after_sequence: int) -> dict[str, object]:
        async with self._lock:
            session = self._sessions.get(token)
        if session is None:
            raise SpeechStreamError("speech stream is unavailable")
        async with session.lock:
            if session.cancelled:
                raise SpeechStreamError("speech stream was cancelled")
            current = asyncio.current_task()
            session.active_pull = current
            try:
                if after_sequence != session.sequence:
                    raise SpeechStreamConflict("speech stream sequence conflict")
                session.last_access = self._clock()
                # Do not wait to fill an entire block: upstream fragments are
                # already time ordered and emitting the first complete sample
                # immediately is what removes whole-file TTS latency.
                while len(session.pending) < self.SAMPLE_WIDTH_BYTES and not session.provider_done:
                    try:
                        fragment = await anext(session.iterator)
                    except StopAsyncIteration:
                        session.provider_done = True
                        break
                    if not isinstance(fragment, bytes) or not fragment:
                        continue
                    session.total_bytes += len(fragment)
                    if session.total_bytes > self.MAX_AUDIO_BYTES:
                        raise SpeechStreamError("speech stream exceeded its audio limit")
                    session.pending.extend(fragment)
                if session.provider_done and len(session.pending) % self.SAMPLE_WIDTH_BYTES:
                    raise SpeechStreamError("speech stream returned incomplete PCM")
                byte_count = min(self.CHUNK_BYTES, len(session.pending))
                byte_count -= byte_count % self.SAMPLE_WIDTH_BYTES
                pcm = bytes(session.pending[:byte_count])
                del session.pending[:byte_count]
                session.sequence += 1
                done = session.provider_done and not session.pending
                return {
                    "token": token,
                    "sequence": session.sequence,
                    "pcm_base64": base64.b64encode(pcm).decode("ascii"),
                    "done": done,
                    "sample_rate_hz": self.SAMPLE_RATE_HZ,
                    "channels": self.CHANNELS,
                    "sample_width_bytes": self.SAMPLE_WIDTH_BYTES,
                }
            finally:
                if session.active_pull is current:
                    session.active_pull = None

    async def close(self, token: str) -> bool:
        async with self._lock:
            session = self._sessions.pop(token, None)
        if session is None:
            return False
        session.cancelled = True
        await self._cancel_session(session)
        return True

    async def cancel_group(self, group_token: str) -> int:
        if not _TOKEN_PATTERN.fullmatch(group_token):
            raise ValueError("speech stream group token is invalid")
        async with self._lock:
            sessions = tuple(
                session
                for session in self._sessions.values()
                if session.group_token == group_token
            )
            self._sessions = {
                token: session
                for token, session in self._sessions.items()
                if session.group_token != group_token
            }
            for session in sessions:
                session.cancelled = True
        await asyncio.gather(*(self._cancel_session(session) for session in sessions))
        return len(sessions)

    async def close_all(self) -> None:
        async with self._lock:
            sessions = tuple(self._sessions.values())
            self._sessions.clear()
            for session in sessions:
                session.cancelled = True
        await asyncio.gather(*(self._cancel_session(session) for session in sessions))

    async def _cancel_session(self, session: _SpeechStreamSession) -> None:
        active_pull = session.active_pull
        current = asyncio.current_task()
        if active_pull is not None and active_pull is not current and not active_pull.done():
            active_pull.cancel()
            await asyncio.gather(active_pull, return_exceptions=True)
        async with session.lock:
            await self._close_iterator(session.iterator)

    async def _close_expired(self, sessions: tuple[_SpeechStreamSession, ...]) -> None:
        for session in sessions:
            session.cancelled = True
        await asyncio.gather(*(self._cancel_session(session) for session in sessions))

    async def _cleanup_expired(self) -> None:
        cutoff = self._clock() - self._ttl_seconds
        async with self._lock:
            expired = [
                (token, session)
                for token, session in self._sessions.items()
                if session.last_access <= cutoff and not session.lock.locked()
            ]
            for token, _ in expired:
                self._sessions.pop(token, None)
        await self._close_expired(tuple(session for _, session in expired))

    @staticmethod
    async def _close_iterator(iterator: AsyncIterator[bytes]) -> None:
        close = getattr(iterator, "aclose", None)
        if close is not None:
            try:
                await close()
            except Exception:
                pass


class SpeechArtifactStore:
    def __init__(
        self,
        directory: Path,
        *,
        ttl_seconds: float = 60,
        max_artifacts: int = 4,
        clock: Callable[[], float] = time.time,
    ) -> None:
        if ttl_seconds <= 0 or not 1 <= max_artifacts <= 32:
            raise ValueError("speech artifact limits are invalid")
        self._directory = directory
        self._ttl_seconds = ttl_seconds
        self._max_artifacts = max_artifacts
        self._clock = clock
        self._lock = threading.Lock()
        self._owned_tokens: set[str] = set()
        self._prepare_directory()

    def create(self, audio: bytes) -> SpeechArtifact:
        if (
            not isinstance(audio, bytes)
            or not 44 <= len(audio) <= 8_388_608
            or audio[:4] != b"RIFF"
            or audio[8:12] != b"WAVE"
        ):
            raise SpeechArtifactError("speech audio is invalid")
        with self._lock:
            self._verify_directory()
            remaining = self._cleanup_expired()
            if remaining >= self._max_artifacts:
                raise SpeechArtifactError("speech artifact capacity reached")
            token = secrets.token_hex(16)
            file_name = f"jarvis-tts-{token}.wav"
            path = self._directory / file_name
            descriptor = os.open(
                path,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                0o600,
            )
            try:
                view = memoryview(audio)
                offset = 0
                while offset < len(view):
                    written = os.write(descriptor, view[offset:])
                    if written <= 0:
                        raise OSError("speech artifact write failed")
                    offset += written
                os.fchmod(descriptor, 0o600)
            except Exception:
                os.close(descriptor)
                path.unlink(missing_ok=True)
                raise
            os.close(descriptor)
            self._owned_tokens.add(token)
            return SpeechArtifact(
                token=token,
                file_name=file_name,
                sha256=hashlib.sha256(audio).hexdigest(),
                byte_count=len(audio),
            )

    def release(self, token: str) -> bool:
        if not _TOKEN_PATTERN.fullmatch(token):
            raise SpeechArtifactError("speech artifact token is invalid")
        with self._lock:
            self._verify_directory()
            path = self._directory / f"jarvis-tts-{token}.wav"
            try:
                metadata = path.lstat()
            except FileNotFoundError:
                self._owned_tokens.discard(token)
                return False
            self._verify_artifact_metadata(metadata)
            path.unlink()
            self._owned_tokens.discard(token)
            return True

    def close(self) -> None:
        with self._lock:
            self._verify_directory()
            for token in tuple(self._owned_tokens):
                path = self._directory / f"jarvis-tts-{token}.wav"
                try:
                    metadata = path.lstat()
                except FileNotFoundError:
                    self._owned_tokens.discard(token)
                    continue
                self._verify_artifact_metadata(metadata)
                path.unlink()
                self._owned_tokens.discard(token)

    def _prepare_directory(self) -> None:
        try:
            metadata = self._directory.lstat()
        except FileNotFoundError:
            self._directory.mkdir(parents=True, mode=0o700)
        else:
            if not stat.S_ISDIR(metadata.st_mode) or metadata.st_uid != os.getuid():
                raise SpeechArtifactError("speech artifact directory is unsafe")
        self._verify_directory()

    def _verify_directory(self) -> None:
        metadata = self._directory.lstat()
        if (
            not stat.S_ISDIR(metadata.st_mode)
            or metadata.st_uid != os.getuid()
            or stat.S_IMODE(metadata.st_mode) & 0o077
        ):
            raise SpeechArtifactError("speech artifact directory is unsafe")

    def _cleanup_expired(self) -> int:
        remaining = 0
        cutoff = self._clock() - self._ttl_seconds
        for path in self._directory.iterdir():
            match = _FILE_PATTERN.fullmatch(path.name)
            if match is None:
                continue
            metadata = path.lstat()
            self._verify_artifact_metadata(metadata)
            if metadata.st_mtime <= cutoff:
                path.unlink()
                self._owned_tokens.discard(match.group(1))
            else:
                remaining += 1
        return remaining

    @staticmethod
    def _verify_artifact_metadata(metadata: os.stat_result) -> None:
        if (
            not stat.S_ISREG(metadata.st_mode)
            or metadata.st_uid != os.getuid()
            or stat.S_IMODE(metadata.st_mode) & 0o077
            or not 44 <= metadata.st_size <= 8_388_608
        ):
            raise SpeechArtifactError("speech artifact metadata is unsafe")


class SpeechSynthesisIpcService:
    SYNTHESIZE_METHOD = "speech.synthesize"
    RELEASE_METHOD = "speech.release"
    STREAM_OPEN_METHOD = "speech.stream.open"
    STREAM_NEXT_METHOD = "speech.stream.next"
    STREAM_CLOSE_METHOD = "speech.stream.close"
    STREAM_CANCEL_METHOD = "speech.stream.cancel"

    def __init__(
        self,
        synthesizer: Callable[[str], Awaitable[bytes]],
        store: SpeechArtifactStore,
        stream_synthesizer: Callable[[str], AsyncIterator[bytes]] | None = None,
    ) -> None:
        self._synthesize = synthesizer
        self._store = store
        self._streams = (
            SpeechStreamManager(stream_synthesizer) if stream_synthesizer is not None else None
        )

    def handlers(self) -> dict[str, IpcMethodHandler]:
        handlers = {
            self.SYNTHESIZE_METHOD: self.handle,
            self.RELEASE_METHOD: self.handle,
        }
        if self._streams is not None:
            handlers.update(
                {
                    self.STREAM_OPEN_METHOD: self.handle,
                    self.STREAM_NEXT_METHOD: self.handle,
                    self.STREAM_CLOSE_METHOD: self.handle,
                    self.STREAM_CANCEL_METHOD: self.handle,
                }
            )
        return handlers

    async def handle(self, request: IpcRequest) -> IpcHandlerResult:
        try:
            if request.method == self.SYNTHESIZE_METHOD:
                payload = SynthesizeSpeechPayload.model_validate(request.payload)
                if self._streams is not None:
                    # Production synthesis is PCM-only and never touches disk.
                    # Keep the method as a compatibility entry point while
                    # returning the same first event as speech.stream.open.
                    group_token = secrets.token_hex(16)
                    event = await self._streams.open(
                        payload.text,
                        group_token=group_token,
                    )
                    return IpcHandlerResult(
                        ok=True,
                        payload={**event, "group_token": group_token},
                    )
                audio = await self._synthesize(payload.text)
                artifact = await asyncio.to_thread(self._store.create, audio)
                return IpcHandlerResult(
                    ok=True,
                    payload=artifact.model_dump(mode="json"),
                )
            if request.method == self.RELEASE_METHOD:
                payload = ReleaseSpeechPayload.model_validate(request.payload)
                released = await asyncio.to_thread(self._store.release, payload.token)
                return IpcHandlerResult(ok=True, payload={"released": released})
            if request.method == self.STREAM_OPEN_METHOD and self._streams is not None:
                payload = OpenSpeechStreamPayload.model_validate(request.payload)
                return IpcHandlerResult(
                    ok=True,
                    payload=await self._streams.open(
                        payload.text,
                        group_token=payload.group_token,
                    ),
                )
            if request.method == self.STREAM_NEXT_METHOD and self._streams is not None:
                payload = PullSpeechStreamPayload.model_validate(request.payload)
                return IpcHandlerResult(
                    ok=True,
                    payload=await self._streams.pull(
                        payload.token,
                        after_sequence=payload.after_sequence,
                    ),
                )
            if request.method == self.STREAM_CLOSE_METHOD and self._streams is not None:
                payload = ReleaseSpeechPayload.model_validate(request.payload)
                return IpcHandlerResult(
                    ok=True,
                    payload={"closed": await self._streams.close(payload.token)},
                )
            if request.method == self.STREAM_CANCEL_METHOD and self._streams is not None:
                payload = CancelSpeechStreamsPayload.model_validate(request.payload)
                return IpcHandlerResult(
                    ok=True,
                    payload={"cancelled": await self._streams.cancel_group(payload.group_token)},
                )
        except (ValidationError, ValueError):
            return IpcHandlerResult(ok=False, error_code="invalid_payload")
        except SpeechStreamConflict:
            return IpcHandlerResult(ok=False, error_code="speech_stream_conflict")
        except SpeechStreamCapacityError:
            return IpcHandlerResult(ok=False, error_code="speech_stream_capacity")
        except NvidiaNimError:
            return IpcHandlerResult(ok=False, error_code="speech_provider_unavailable")
        except SpeechStreamError:
            return IpcHandlerResult(ok=False, error_code="speech_stream_unavailable")
        except (OSError, SpeechArtifactError):
            return IpcHandlerResult(ok=False, error_code="speech_artifact_unavailable")
        return IpcHandlerResult(ok=False, error_code="method_not_found")

    async def close(self) -> None:
        if self._streams is not None:
            await self._streams.close_all()
        await asyncio.to_thread(self._store.close)
