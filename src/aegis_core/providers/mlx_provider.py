"""Private local Whisper transcription; language generation uses NVIDIA exclusively."""

from __future__ import annotations

import asyncio
import json
import os
import stat
import time
from pathlib import Path
from uuid import uuid4

from aegis_core.failure_boundary import record_boundary_error
from aegis_core.tools.audit import AuditSink, NullAuditSink

WHISPER_VOCABULARY_PROMPT = (
    "Google Chrome, YouTube, Michael Jackson, Man in the Mirror, reproducir, "
    "buscar, clic, abrir, confirmar, aprobado"
)


class MLXProviderError(RuntimeError):
    """Raised when bounded local Whisper transcription fails closed."""


class MLXWhisperTranscriber:
    """Loads a private on-disk MLX Whisper model only for bounded local PCM."""

    def __init__(
        self,
        model_path: Path,
        *,
        timeout_seconds: float = 8.0,
        initial_prompt: str = WHISPER_VOCABULARY_PROMPT,
        audit_sink: AuditSink | None = None,
    ) -> None:
        if not 1.0 <= timeout_seconds <= 20.0:
            raise ValueError("Whisper timeout is out of range")
        if not initial_prompt or len(initial_prompt.encode("utf-8")) > 1_024:
            raise ValueError("Whisper initial prompt is invalid")
        self._model_path = model_path
        self._timeout_seconds = timeout_seconds
        self._initial_prompt = initial_prompt
        self._audit = audit_sink or NullAuditSink()
        self._lock = asyncio.Lock()

    async def transcribe_pcm_s16le(
        self,
        pcm: bytes,
        *,
        sample_rate: int = 16_000,
    ) -> str:
        if sample_rate != 16_000 or len(pcm) % 2 or not 8_000 <= len(pcm) <= 96_000:
            raise MLXProviderError("Whisper PCM payload is invalid")
        self._validate_private_model()
        started = time.monotonic()
        async with self._lock:
            try:
                text = await asyncio.wait_for(
                    asyncio.to_thread(self._transcribe_sync, pcm),
                    timeout=self._timeout_seconds,
                )
            except TimeoutError as error:
                raise MLXProviderError("Whisper transcription timed out") from error
            except MLXProviderError:
                raise
            except Exception as error:
                record_boundary_error(error)
                raise MLXProviderError("Whisper transcription failed") from error
        self._audit.record_system_event(
            uuid4(),
            event_type="local_confirmation_transcribed",
            component="mlx_whisper",
            data={
                "audio_milliseconds": len(pcm) * 1_000 // (16_000 * 2),
                "latency_milliseconds": round((time.monotonic() - started) * 1_000),
                "text_recorded": False,
            },
        )
        return text

    def _transcribe_sync(self, pcm: bytes) -> str:
        try:
            import mlx_whisper
            import numpy as np
        except ImportError as error:
            raise MLXProviderError("MLX Whisper runtime is unavailable") from error
        samples = np.frombuffer(pcm, dtype="<i2").astype(np.float32) / 32_768.0
        result = mlx_whisper.transcribe(
            samples,
            path_or_hf_repo=str(self._model_path),
            language="es",
            task="transcribe",
            initial_prompt=self._initial_prompt,
            condition_on_previous_text=False,
            temperature=0.0,
            verbose=None,
        )
        if not isinstance(result, dict) or not isinstance(result.get("text"), str):
            raise MLXProviderError("Whisper response is malformed")
        text = " ".join(result["text"].split())
        if not text or len(text) > 256:
            raise MLXProviderError("Whisper confirmation is empty or oversized")
        return text

    def _validate_private_model(self) -> None:
        try:
            status = self._model_path.stat(follow_symlinks=False)
            resolved = self._model_path.resolve(strict=True)
        except OSError as error:
            raise MLXProviderError("Whisper model is unavailable") from error
        if (
            not stat.S_ISDIR(status.st_mode)
            or self._model_path.is_symlink()
            or resolved != self._model_path.absolute()
            or status.st_uid != os.getuid()
            or status.st_mode & 0o022
        ):
            raise MLXProviderError("Whisper model directory is not private")
        limits = {
            "config.json": (64, 4_096),
            "weights.npz": (1_048_576, 80 * 1_048_576),
        }
        for name, (minimum_bytes, maximum_bytes) in limits.items():
            candidate = self._model_path / name
            try:
                file_status = candidate.stat(follow_symlinks=False)
            except OSError as error:
                raise MLXProviderError("Whisper model directory is incomplete") from error
            if (
                candidate.is_symlink()
                or not stat.S_ISREG(file_status.st_mode)
                or file_status.st_uid != os.getuid()
                or file_status.st_mode & 0o022
                or not minimum_bytes <= file_status.st_size <= maximum_bytes
            ):
                raise MLXProviderError("Whisper model file is unsafe")
        try:
            configuration = json.loads(
                (self._model_path / "config.json").read_text(encoding="utf-8")
            )
        except (OSError, UnicodeError, json.JSONDecodeError) as error:
            raise MLXProviderError("Whisper model configuration is invalid") from error
        if (
            not isinstance(configuration, dict)
            or configuration.get("model_type") != "whisper"
            or configuration.get("n_mels") != 80
            or configuration.get("quantization") != {"bits": 4, "group_size": 64}
        ):
            raise MLXProviderError("Whisper model configuration is unsupported")
