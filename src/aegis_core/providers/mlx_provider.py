from __future__ import annotations

import asyncio
import hashlib
import json
import math
import os
import re
import stat
import struct
import subprocess
import time
from collections import OrderedDict
from collections.abc import Callable, Mapping, Sequence
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

from aegis_core.contracts import AgentResult, AgentRole
from aegis_core.ipc.protocol import IpcRequest
from aegis_core.ipc.server import IpcHandlerResult, IpcMethodHandler
from aegis_core.tools.audit import AuditSink, NullAuditSink

_PROTOCOL_VERSION = "1.0"
_MAX_REQUEST_BYTES = 65_536
_MAX_RESPONSE_BYTES = 262_144
_MAX_PROMPT_BYTES = 24_576
_MAX_INSTRUCTION_BYTES = 8_192
_MAX_TOKENS = 1_024
_MAX_SESSIONS = 2
_AVAILABILITY_CACHE_SECONDS = 30.0
_MODEL_PATTERN = re.compile(
    r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}/[A-Za-z0-9][A-Za-z0-9._-]{0,127}$"
)
_CONVERSATION_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
WHISPER_VOCABULARY_PROMPT = (
    "Google Chrome, YouTube, Michael Jackson, Man in the Mirror, reproducir, "
    "buscar, clic, abrir, confirmar, aprobado"
)


class MLXProviderError(RuntimeError):
    """Raised when the bounded native MLX helper fails closed."""


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


@dataclass(frozen=True, slots=True)
class MLXDraftVerification:
    accepted_token_count: int
    correction_token_id: int | None
    verified_token_count: int


@dataclass(slots=True)
class _ConversationState:
    instructions_digest: str
    message_digests: tuple[str, ...]


class MLXProvider:
    def __init__(
        self,
        executable_path: Path,
        *,
        model_id: str = "mlx-community/Qwen2.5-3B-Instruct-4bit",
        compact_model_id: str = "mlx-community/Llama-3.2-1B-Instruct-4bit",
        draft_model_id: str | None = None,
        draft_model_bytes: int = 0,
        timeout_seconds: float = 30.0,
        audit_sink: AuditSink | None = None,
    ) -> None:
        if _MODEL_PATTERN.fullmatch(model_id) is None:
            raise ValueError("MLX model identifier is invalid")
        if _MODEL_PATTERN.fullmatch(compact_model_id) is None:
            raise ValueError("MLX compact model identifier is invalid")
        if draft_model_id is not None and _MODEL_PATTERN.fullmatch(draft_model_id) is None:
            raise ValueError("MLX draft model identifier is invalid")
        if not 0 <= draft_model_bytes <= 8 * 1_024 * 1_024 * 1_024:
            raise ValueError("MLX draft model size estimate is invalid")
        if (draft_model_id is None) != (draft_model_bytes == 0):
            raise ValueError("MLX draft model configuration is incomplete")
        if not 1.0 <= timeout_seconds <= 120.0:
            raise ValueError("MLX provider timeout is invalid")
        self._path = executable_path
        self.model_id = model_id
        self._compact_model_id = compact_model_id
        self._draft_model_id = draft_model_id
        self._draft_model_bytes = draft_model_bytes
        self._timeout_seconds = timeout_seconds
        self._audit = audit_sink or NullAuditSink()
        self._process: asyncio.subprocess.Process | None = None
        self._lock = asyncio.Lock()
        self._conversations: OrderedDict[str, _ConversationState] = OrderedDict()
        self._available_until = 0.0
        self._active_model_id: str | None = None

    @property
    def executable_path(self) -> Path:
        return self._path

    def is_available(self) -> bool:
        if not self._is_private_executable():
            self._available_until = 0.0
            return False
        if time.monotonic() < self._available_until:
            return True
        try:
            completed = subprocess.run(
                (str(self._path), "--status"),
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                timeout=3,
                check=False,
                env=self._environment(),
            )
            payload = json.loads(completed.stdout)
        except (OSError, ValueError):
            return False
        available = (
            completed.returncode == 0
            and isinstance(payload, dict)
            and payload.get("protocol_version") == _PROTOCOL_VERSION
            and payload.get("success") is True
            and payload.get("model_id") in {self.model_id, self._compact_model_id}
            and payload.get("local_execution_allowed") is True
        )
        self._available_until = (
            time.monotonic() + _AVAILABILITY_CACHE_SECONDS if available else 0.0
        )
        return available

    async def aclose(self) -> None:
        async with self._lock:
            await self._terminate_locked()
            self._conversations.clear()

    async def complete(
        self,
        *,
        role: AgentRole,
        messages: Sequence[Mapping[str, Any]],
        max_tokens: int | None = None,
        temperature: float | None = None,
        extra_body: Mapping[str, Any] | None = None,
    ) -> AgentResult:
        if role not in {AgentRole.PLANNER, AgentRole.SYNTHESIZER}:
            raise MLXProviderError("MLX role is unsupported")
        conversation_override = self._conversation_override(extra_body)
        token_budget, sampling_temperature = self._generation_options(
            max_tokens=max_tokens,
            temperature=temperature,
        )
        instructions, normalized = self._normalized_messages(messages)
        conversation_id = conversation_override or self._derived_conversation_id(
            role,
            normalized,
        )
        async with self._lock:
            prompt, cache_expected = await self._prepare_turn_locked(
                conversation_id,
                instructions,
                normalized,
            )
            response = await self._request_locked(
                {
                    "protocol_version": _PROTOCOL_VERSION,
                    "request_id": str(uuid4()),
                    "method": "generate",
                    "conversation_id": conversation_id,
                    "system_instructions": instructions,
                    "prompt": prompt,
                    "maximum_tokens": token_budget,
                    "temperature": sampling_temperature,
                }
            )
            content = response.get("content")
            cache_reused = response.get("cache_reused")
            response_model = response.get("model_id")
            model_swapped = (
                isinstance(response_model, str)
                and self._active_model_id is not None
                and response_model != self._active_model_id
            )
            if (
                not isinstance(content, str)
                or not content.strip()
                or not isinstance(cache_reused, bool)
                or (cache_expected and not cache_reused and not model_swapped)
            ):
                await self._terminate_locked()
                self._conversations.pop(conversation_id, None)
                raise MLXProviderError("MLX response state is inconsistent")
            if model_swapped:
                self._conversations.clear()
            assert isinstance(response_model, str)
            self._active_model_id = response_model
            normalized_content = content.strip()
            current_digests = tuple(self._message_digest(*item) for item in normalized)
            assistant_digest = self._message_digest("assistant", normalized_content)
            self._conversations[conversation_id] = _ConversationState(
                instructions_digest=self._text_digest(instructions),
                message_digests=(*current_digests, assistant_digest),
            )
            self._conversations.move_to_end(conversation_id)
            self._evict_provider_state_locked()
        self._audit.record_system_event(
            UUID(response["request_id"]),
            event_type="mlx_inference_completed",
            component="mlx_provider",
            data={
                "cache_reused": cache_reused,
                "model": response_model,
                "role": role.value,
            },
        )
        return AgentResult(
            role=role,
            model_id=response_model,
            content=normalized_content,
            finish_reason="stop",
        )

    async def complete_stream(
        self,
        *,
        role: AgentRole,
        messages: Sequence[Mapping[str, Any]],
        max_tokens: int | None = None,
        temperature: float | None = None,
        extra_body: Mapping[str, Any] | None = None,
        on_delta: Callable[[str], None] | None,
    ) -> AgentResult:
        result = await self.complete(
            role=role,
            messages=messages,
            max_tokens=max_tokens,
            temperature=temperature,
            extra_body=extra_body,
        )
        if on_delta is not None:
            on_delta(result.content)
        return result

    async def verify_draft(
        self,
        *,
        prompt: str,
        draft_token_ids: Sequence[int],
        system_instructions: str = "",
    ) -> MLXDraftVerification:
        if (
            not prompt
            or len(prompt.encode("utf-8")) > _MAX_PROMPT_BYTES
            or len(system_instructions.encode("utf-8")) > _MAX_INSTRUCTION_BYTES
            or not 1 <= len(draft_token_ids) <= 64
            or any(
                isinstance(item, bool) or not isinstance(item, int) or item < 0
                for item in draft_token_ids
            )
        ):
            raise MLXProviderError("MLX draft request is invalid")
        async with self._lock:
            response = await self._request_locked(
                {
                    "protocol_version": _PROTOCOL_VERSION,
                    "request_id": str(uuid4()),
                    "method": "verify_draft",
                    "system_instructions": system_instructions or None,
                    "prompt": prompt,
                    "draft_token_ids": list(draft_token_ids),
                }
            )
        raw = response.get("verification")
        if not isinstance(raw, dict) or set(raw) != {
            "accepted_token_count",
            "correction_token_id",
            "verified_token_count",
        }:
            raise MLXProviderError("MLX draft verification response is invalid")
        accepted = raw["accepted_token_count"]
        correction = raw["correction_token_id"]
        verified = raw["verified_token_count"]
        if (
            isinstance(accepted, bool)
            or not isinstance(accepted, int)
            or not 0 <= accepted <= len(draft_token_ids)
            or (
                correction is not None
                and (
                    isinstance(correction, bool)
                    or not isinstance(correction, int)
                    or correction < 0
                )
            )
            or isinstance(verified, bool)
            or not isinstance(verified, int)
            or verified != len(draft_token_ids)
        ):
            raise MLXProviderError("MLX draft verification values are invalid")
        return MLXDraftVerification(accepted, correction, verified)

    async def _prepare_turn_locked(
        self,
        conversation_id: str,
        instructions: str,
        normalized: tuple[tuple[str, str], ...],
    ) -> tuple[str, bool]:
        current = tuple(self._message_digest(*item) for item in normalized)
        prior = self._conversations.get(conversation_id)
        if (
            prior is not None
            and prior.instructions_digest == self._text_digest(instructions)
            and len(current) > len(prior.message_digests)
            and current[: len(prior.message_digests)] == prior.message_digests
        ):
            delta = normalized[len(prior.message_digests) :]
            return self._render_messages(delta), True
        if prior is not None:
            await self._request_locked(
                {
                    "protocol_version": _PROTOCOL_VERSION,
                    "request_id": str(uuid4()),
                    "method": "reset_conversation",
                    "conversation_id": conversation_id,
                }
            )
            self._conversations.pop(conversation_id, None)
        return self._render_messages(normalized), False

    async def _request_locked(self, payload: dict[str, object]) -> dict[str, object]:
        if not self._is_private_executable():
            raise MLXProviderError("MLX helper is unavailable")
        encoded = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode()
        if not encoded or len(encoded) > _MAX_REQUEST_BYTES:
            raise MLXProviderError("MLX request exceeds its size limit")
        process = await self._ensure_process_locked()
        assert process.stdin is not None
        assert process.stdout is not None
        frame = struct.pack(">I", len(encoded)) + encoded
        try:
            async with asyncio.timeout(self._timeout_seconds):
                process.stdin.write(frame)
                await process.stdin.drain()
                header = await process.stdout.readexactly(4)
                length = struct.unpack(">I", header)[0]
                if not 1 <= length <= _MAX_RESPONSE_BYTES:
                    raise MLXProviderError("MLX response exceeds its size limit")
                raw = await process.stdout.readexactly(length)
        except (OSError, TimeoutError, asyncio.IncompleteReadError) as error:
            await self._terminate_locked()
            self._audit.record_system_event(
                uuid4(),
                event_type="mlx_provider_failure",
                component="mlx_provider",
                data={"reason": type(error).__name__},
            )
            raise MLXProviderError("MLX helper transport failed") from error
        except MLXProviderError:
            await self._terminate_locked()
            self._audit.record_system_event(
                uuid4(),
                event_type="mlx_provider_failure",
                component="mlx_provider",
                data={"reason": "protocol_limit"},
            )
            raise
        try:
            response = json.loads(raw)
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            await self._terminate_locked()
            raise MLXProviderError("MLX helper response is not JSON") from error
        error_code = response.get("error_code") if isinstance(response, dict) else None
        if (
            not isinstance(response, dict)
            or response.get("protocol_version") != _PROTOCOL_VERSION
            or response.get("request_id") != payload.get("request_id")
            or response.get("model_id") not in {self.model_id, self._compact_model_id}
            or response.get("success") is not True
            or error_code is not None
        ):
            await self._terminate_locked()
            if error_code in {
                "cloud_required",
                "runtime_policy_changed",
                "thermal_swap_deadline_exceeded",
            }:
                raise MLXProviderError(str(error_code))
            raise MLXProviderError("MLX helper rejected the request")
        return response

    async def _ensure_process_locked(self) -> asyncio.subprocess.Process:
        if self._process is not None and self._process.returncode is None:
            return self._process
        try:
            process = await asyncio.create_subprocess_exec(
                str(self._path),
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,
                env=self._environment(),
            )
        except OSError as error:
            raise MLXProviderError("MLX helper launch failed") from error
        if process.stdin is None or process.stdout is None:
            process.kill()
            await process.wait()
            raise MLXProviderError("MLX helper pipes are unavailable")
        self._process = process
        self._available_until = time.monotonic() + _AVAILABILITY_CACHE_SECONDS
        self._audit.record_system_event(
            uuid4(),
            event_type="mlx_provider_started",
            component="mlx_provider",
            data={"model": self.model_id, "sessions": _MAX_SESSIONS},
        )
        return process

    async def _terminate_locked(self) -> None:
        process = self._process
        self._process = None
        self._available_until = 0.0
        if process is None or process.returncode is not None:
            return
        with suppress(ProcessLookupError):
            process.terminate()
        try:
            await asyncio.wait_for(process.wait(), timeout=1.0)
        except TimeoutError:
            with suppress(ProcessLookupError):
                process.kill()
            await process.wait()

    def _environment(self) -> dict[str, str]:
        environment = {
            "AEGIS_MLX_MODEL_ID": self.model_id,
            "AEGIS_MLX_COMPACT_MODEL_ID": self._compact_model_id,
            "AEGIS_MLX_DRAFT_MODEL_BYTES": str(self._draft_model_bytes),
            "HOME": str(Path.home()),
            "LC_ALL": "C",
            "PATH": "/usr/bin:/bin:/usr/sbin:/sbin",
        }
        if self._draft_model_id is not None:
            environment["AEGIS_MLX_DRAFT_MODEL_ID"] = self._draft_model_id
        return environment

    def _is_private_executable(self) -> bool:
        try:
            status = self._path.stat(follow_symlinks=False)
        except OSError:
            return False
        return (
            stat.S_ISREG(status.st_mode)
            and status.st_uid == os.getuid()
            and bool(status.st_mode & stat.S_IXUSR)
            and not bool(status.st_mode & 0o022)
        )

    @staticmethod
    def _generation_options(
        *,
        max_tokens: int | None,
        temperature: float | None,
    ) -> tuple[int, float]:
        token_budget = 512 if max_tokens is None else max_tokens
        sampling_temperature = 0.2 if temperature is None else temperature
        if (
            isinstance(token_budget, bool)
            or not isinstance(token_budget, int)
            or not 1 <= token_budget <= _MAX_TOKENS
            or isinstance(sampling_temperature, bool)
            or not isinstance(sampling_temperature, (int, float))
            or not math.isfinite(sampling_temperature)
            or not 0.0 <= sampling_temperature <= 2.0
        ):
            raise MLXProviderError("MLX generation options are invalid")
        return token_budget, float(sampling_temperature)

    @staticmethod
    def _normalized_messages(
        messages: Sequence[Mapping[str, Any]],
    ) -> tuple[str, tuple[tuple[str, str], ...]]:
        instructions: list[str] = []
        normalized: list[tuple[str, str]] = []
        for message in messages:
            if set(message) - {"role", "content", "name"}:
                raise MLXProviderError("MLX messages contain unsupported fields")
            role = message.get("role")
            content = message.get("content")
            if role not in {"system", "user", "assistant", "tool"} or not isinstance(content, str):
                raise MLXProviderError("MLX accepts text messages only")
            if role == "system":
                instructions.append(content)
            else:
                normalized.append((role, content))
        instruction_text = "\n".join(instructions)
        if (
            not instruction_text
            or len(instruction_text.encode()) > _MAX_INSTRUCTION_BYTES
            or not normalized
            or not any(role == "user" for role, _ in normalized)
        ):
            raise MLXProviderError("MLX prompt is incomplete")
        return instruction_text, tuple(normalized)

    @staticmethod
    def _render_messages(messages: Sequence[tuple[str, str]]) -> str:
        rendered = "\n".join(f"{role}: {content}" for role, content in messages)
        if not rendered or len(rendered.encode()) > _MAX_PROMPT_BYTES:
            raise MLXProviderError("MLX prompt exceeds its size limit")
        return rendered

    @staticmethod
    def _conversation_override(extra_body: Mapping[str, Any] | None) -> str | None:
        if extra_body is None:
            return None
        if set(extra_body) != {"aegis_conversation_id"}:
            raise MLXProviderError("MLX tool payloads are unsupported")
        value = extra_body.get("aegis_conversation_id")
        if not isinstance(value, str) or _CONVERSATION_PATTERN.fullmatch(value) is None:
            raise MLXProviderError("MLX conversation identifier is invalid")
        return value

    @classmethod
    def _derived_conversation_id(
        cls,
        role: AgentRole,
        messages: tuple[tuple[str, str], ...],
    ) -> str:
        first_user = next(content for item_role, content in messages if item_role == "user")
        digest = hashlib.sha256(f"{role.value}\0{first_user}".encode()).hexdigest()[:32]
        return f"local-{digest}"

    @staticmethod
    def _text_digest(value: str) -> str:
        return hashlib.sha256(value.encode()).hexdigest()

    @classmethod
    def _message_digest(cls, role: str, content: str) -> str:
        return cls._text_digest(f"{role}\0{content}")

    def _evict_provider_state_locked(self) -> None:
        while len(self._conversations) > _MAX_SESSIONS:
            self._conversations.popitem(last=False)


class MLXVerificationIpcService:
    METHOD = "mlx.verify_draft"

    def __init__(self, provider: MLXProvider, audit_sink: AuditSink | None = None) -> None:
        self._provider = provider
        self._audit = audit_sink or NullAuditSink()

    def handlers(self) -> dict[str, IpcMethodHandler]:
        return {self.METHOD: self.handle}

    async def handle(self, request: IpcRequest) -> IpcHandlerResult:
        if request.method != self.METHOD:
            return IpcHandlerResult(ok=False, error_code="method_not_found")
        if set(request.payload) - {"prompt", "draft_token_ids", "system_instructions"}:
            return IpcHandlerResult(ok=False, error_code="invalid_payload")
        prompt = request.payload.get("prompt")
        draft_token_ids = request.payload.get("draft_token_ids")
        instructions = request.payload.get("system_instructions", "")
        if (
            not isinstance(prompt, str)
            or not isinstance(draft_token_ids, list)
            or not isinstance(instructions, str)
        ):
            return IpcHandlerResult(ok=False, error_code="invalid_payload")
        try:
            verification = await self._provider.verify_draft(
                prompt=prompt,
                draft_token_ids=draft_token_ids,
                system_instructions=instructions,
            )
        except MLXProviderError:
            self._audit.record_system_event(
                request.request_id,
                event_type="mlx_verification_failed",
                component="mlx_provider",
                data={"draft_tokens": min(len(draft_token_ids), 65)},
            )
            return IpcHandlerResult(ok=False, error_code="mlx_verification_failed")
        self._audit.record_system_event(
            request.request_id,
            event_type="mlx_verification_completed",
            component="mlx_provider",
            data={
                "accepted_tokens": verification.accepted_token_count,
                "verified_tokens": verification.verified_token_count,
            },
        )
        return IpcHandlerResult(
            ok=True,
            payload={
                "accepted_token_count": verification.accepted_token_count,
                "correction_token_id": verification.correction_token_id,
                "verified_token_count": verification.verified_token_count,
            },
        )
