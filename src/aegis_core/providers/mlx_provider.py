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


class MLXProviderError(RuntimeError):
    """Raised when the bounded native MLX helper fails closed."""


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
        draft_model_id: str | None = None,
        draft_model_bytes: int = 0,
        timeout_seconds: float = 30.0,
        audit_sink: AuditSink | None = None,
    ) -> None:
        if _MODEL_PATTERN.fullmatch(model_id) is None:
            raise ValueError("MLX model identifier is invalid")
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
        self._draft_model_id = draft_model_id
        self._draft_model_bytes = draft_model_bytes
        self._timeout_seconds = timeout_seconds
        self._audit = audit_sink or NullAuditSink()
        self._process: asyncio.subprocess.Process | None = None
        self._lock = asyncio.Lock()
        self._conversations: OrderedDict[str, _ConversationState] = OrderedDict()
        self._available_until = 0.0

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
            and payload.get("model_id") == self.model_id
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
            if (
                not isinstance(content, str)
                or not content.strip()
                or not isinstance(cache_reused, bool)
                or (cache_expected and not cache_reused)
            ):
                await self._terminate_locked()
                self._conversations.pop(conversation_id, None)
                raise MLXProviderError("MLX response state is inconsistent")
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
                "model": self.model_id,
                "role": role.value,
            },
        )
        return AgentResult(
            role=role,
            model_id=self.model_id,
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
        if (
            not isinstance(response, dict)
            or response.get("protocol_version") != _PROTOCOL_VERSION
            or response.get("request_id") != payload.get("request_id")
            or response.get("model_id") != self.model_id
            or response.get("success") is not True
            or response.get("error_code") is not None
        ):
            await self._terminate_locked()
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
