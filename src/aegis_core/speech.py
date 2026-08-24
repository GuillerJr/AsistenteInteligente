from __future__ import annotations

import asyncio
import hashlib
import os
import re
import secrets
import stat
import threading
import time
from collections.abc import Awaitable, Callable
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


class ReleaseSpeechPayload(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    token: str = Field(pattern=r"^[0-9a-f]{32}$")


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

    def __init__(
        self,
        synthesizer: Callable[[str], Awaitable[bytes]],
        store: SpeechArtifactStore,
    ) -> None:
        self._synthesize = synthesizer
        self._store = store

    def handlers(self) -> dict[str, IpcMethodHandler]:
        return {
            self.SYNTHESIZE_METHOD: self.handle,
            self.RELEASE_METHOD: self.handle,
        }

    async def handle(self, request: IpcRequest) -> IpcHandlerResult:
        try:
            if request.method == self.SYNTHESIZE_METHOD:
                payload = SynthesizeSpeechPayload.model_validate(request.payload)
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
        except (ValidationError, ValueError):
            return IpcHandlerResult(ok=False, error_code="invalid_payload")
        except NvidiaNimError:
            return IpcHandlerResult(ok=False, error_code="speech_provider_unavailable")
        except (OSError, SpeechArtifactError):
            return IpcHandlerResult(ok=False, error_code="speech_artifact_unavailable")
        return IpcHandlerResult(ok=False, error_code="method_not_found")

    async def close(self) -> None:
        await asyncio.to_thread(self._store.close)
