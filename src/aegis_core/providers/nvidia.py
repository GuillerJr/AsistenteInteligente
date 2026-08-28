from __future__ import annotations

import asyncio
import io
import json
import math
import re
import threading
import time
import wave
from collections.abc import AsyncIterator, Callable, Mapping, Sequence
from typing import Any

import httpx

from aegis_core.config import Settings
from aegis_core.contracts import (
    MAX_TOOL_CALLS_PER_RESULT,
    AgentResult,
    AgentRole,
    ToolCall,
)
from aegis_core.models import model_for
from aegis_core.providers.base import (
    EmbeddingBatch,
    EmbeddingInputType,
    EmbeddingProviderError,
)

_ALLOWED_EXTRA_BODY_KEYS = frozenset(
    {"chat_template_kwargs", "response_format", "tool_choice", "tools"}
)
_MAX_TTS_TEXT_BYTES = 8_192
_MAX_TTS_AUDIO_BYTES = 8_388_608


class NvidiaNimError(EmbeddingProviderError):
    pass


class NvidiaNimRateLimited(NvidiaNimError):
    pass


class NvidiaGlobalCooldown:
    """One monotonic cooldown shared by every live NVIDIA client in the process."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._until = 0.0
        self._clients = 0

    def register(self) -> None:
        with self._lock:
            self._clients += 1

    def unregister(self) -> None:
        with self._lock:
            self._clients = max(0, self._clients - 1)
            if self._clients == 0:
                self._until = 0.0

    def open(self, delay_seconds: float) -> None:
        if not math.isfinite(delay_seconds) or delay_seconds <= 0:
            raise ValueError("NVIDIA cooldown delay is invalid")
        with self._lock:
            self._until = max(self._until, time.monotonic() + delay_seconds)

    def active(self) -> bool:
        with self._lock:
            return time.monotonic() < self._until


GLOBAL_NVIDIA_COOLDOWN = NvidiaGlobalCooldown()


class NvidiaNimClient:
    def __init__(
        self,
        settings: Settings,
        api_key_loader: Callable[[], str],
        *,
        transport: httpx.AsyncBaseTransport | None = None,
        cooldown: NvidiaGlobalCooldown = GLOBAL_NVIDIA_COOLDOWN,
    ) -> None:
        self._settings = settings
        self._api_key_loader = api_key_loader
        self._semaphore = asyncio.Semaphore(settings.max_concurrency)
        self._cooldown = cooldown
        self._cooldown.register()
        self._closed = False
        self._unavailable_model_ids: set[str] = set()
        self._client = httpx.AsyncClient(
            base_url=str(settings.nvidia_base_url).rstrip("/"),
            timeout=settings.request_timeout_seconds,
            transport=transport,
        )

    async def __aenter__(self) -> NvidiaNimClient:
        return self

    @property
    def model_id(self) -> str:
        return self._settings.nvidia_embedding_model_id

    async def __aexit__(self, *_: object) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        if self._closed:
            return
        self._closed = True
        try:
            await self._client.aclose()
        finally:
            self._cooldown.unregister()

    async def complete(
        self,
        *,
        role: AgentRole,
        messages: Sequence[Mapping[str, Any]],
        max_tokens: int | None = None,
        temperature: float | None = None,
        extra_body: Mapping[str, Any] | None = None,
        model_ids: Sequence[str] | None = None,
    ) -> AgentResult:
        if extra_body and not set(extra_body).issubset(_ALLOWED_EXTRA_BODY_KEYS):
            raise ValueError("NVIDIA request contains an unapproved option")
        spec = model_for(role)
        payload: dict[str, Any] = {
            "messages": list(messages),
            "max_tokens": max_tokens or self._settings.max_output_tokens,
            "stream": False,
        }
        if temperature is not None:
            payload["temperature"] = temperature
        if extra_body:
            payload.update(extra_body)

        selected_model_ids = self._selected_model_ids(
            spec.model_id,
            spec.fallback_model_id,
            model_ids,
        )

        result: AgentResult | None = None
        async with self._semaphore:
            self._raise_if_rate_limited()
            headers = self._headers()
            try:
                async with asyncio.timeout(self._settings.request_timeout_seconds):
                    for attempt, model_id in enumerate(selected_model_ids):
                        if model_id in self._unavailable_model_ids:
                            continue
                        payload["model"] = model_id
                        try:
                            response = await self._client.post(
                                "/chat/completions", headers=headers, json=payload
                            )
                        except httpx.HTTPError as error:
                            raise NvidiaNimError("NVIDIA NIM request failed") from error

                        has_fallback = attempt + 1 < len(selected_model_ids)
                        if response.status_code == 202:
                            if has_fallback:
                                continue
                            raise NvidiaNimError("NVIDIA NIM returned HTTP 202")
                        if not response.is_error:
                            try:
                                result = self._parse_completion_response(
                                    response,
                                    role=role,
                                    model_id=model_id,
                                )
                            except NvidiaNimError:
                                if has_fallback:
                                    continue
                                raise
                            break
                        if response.status_code in {404, 410}:
                            self._unavailable_model_ids.add(model_id)
                        if has_fallback and self._can_fallback(response.status_code):
                            continue
                        if response.status_code == 429:
                            self._open_rate_limit_cooldown(response)
                            raise NvidiaNimRateLimited("NVIDIA NIM rate limit reached")
                        raise NvidiaNimError(f"NVIDIA NIM returned HTTP {response.status_code}")
            except TimeoutError as error:
                raise NvidiaNimError("NVIDIA NIM request timed out") from error
        if result is None:
            raise NvidiaNimError("NVIDIA NIM has no available model for this role")
        return result

    def _parse_completion_response(
        self,
        response: httpx.Response,
        *,
        role: AgentRole,
        model_id: str,
    ) -> AgentResult:
        try:
            data = response.json()
            choice = data["choices"][0]
            message = choice["message"]
        except (ValueError, KeyError, IndexError, TypeError) as error:
            raise NvidiaNimError("NVIDIA NIM returned an invalid response") from error
        if not all(isinstance(value, dict) for value in (data, choice, message)):
            raise NvidiaNimError("NVIDIA NIM returned an invalid response")

        raw_tool_calls = message.get("tool_calls", [])
        if raw_tool_calls is None:
            raw_tool_calls = []
        if not isinstance(raw_tool_calls, list):
            raise NvidiaNimError("NVIDIA NIM returned an invalid tool call")
        if len(raw_tool_calls) > MAX_TOOL_CALLS_PER_RESULT:
            raise NvidiaNimError("NVIDIA NIM returned too many tool calls")
        try:
            tool_calls = tuple(
                ToolCall(
                    call_id=raw_call["id"],
                    tool_name=raw_call["function"]["name"],
                    arguments=self._parse_tool_arguments(
                        raw_call["function"].get("arguments", "{}")
                    ),
                    requested_by=role,
                )
                for raw_call in raw_tool_calls
            )
        except (KeyError, TypeError, ValueError) as error:
            raise NvidiaNimError("NVIDIA NIM returned an invalid tool call") from error

        content = message.get("content") or ""
        if not isinstance(content, str) or (not content.strip() and not tool_calls):
            raise NvidiaNimError("NVIDIA NIM returned an empty response")
        usage = data.get("usage") or {}
        if not isinstance(usage, dict):
            raise NvidiaNimError("NVIDIA NIM returned an invalid response")
        try:
            return AgentResult(
                role=role,
                model_id=model_id,
                content=content,
                finish_reason=choice.get("finish_reason"),
                raw_usage={
                    key: int(value) for key, value in usage.items() if isinstance(value, int)
                },
                tool_calls=tool_calls,
            )
        except (TypeError, ValueError) as error:
            raise NvidiaNimError("NVIDIA NIM returned an invalid response") from error

    async def complete_stream(
        self,
        *,
        role: AgentRole,
        messages: Sequence[Mapping[str, Any]],
        max_tokens: int | None = None,
        temperature: float | None = None,
        extra_body: Mapping[str, Any] | None = None,
        on_delta: Callable[[str], None] | None,
        model_ids: Sequence[str] | None = None,
    ) -> AgentResult:
        if extra_body:
            raise ValueError("streaming tool calls are not supported")
        spec = model_for(role)
        payload: dict[str, Any] = {
            "messages": list(messages),
            "max_tokens": max_tokens or self._settings.max_output_tokens,
            "stream": True,
        }
        if temperature is not None:
            payload["temperature"] = temperature
        selected_model_ids = self._selected_model_ids(
            spec.model_id,
            spec.fallback_model_id,
            model_ids,
        )
        content_parts: list[str] = []
        finish_reason: str | None = None
        usage: dict[str, int] = {}
        selected_model = selected_model_ids[0]

        async with self._semaphore:
            self._raise_if_rate_limited()
            headers = self._headers()
            try:
                async with asyncio.timeout(self._settings.request_timeout_seconds):
                    for attempt, model_id in enumerate(selected_model_ids):
                        if model_id in self._unavailable_model_ids:
                            continue
                        payload["model"] = model_id
                        selected_model = model_id
                        try:
                            async with self._client.stream(
                                "POST",
                                "/chat/completions",
                                headers=headers,
                                json=payload,
                            ) as response:
                                has_fallback = attempt + 1 < len(selected_model_ids)
                                if response.is_error or response.status_code == 202:
                                    if response.status_code in {404, 410}:
                                        self._unavailable_model_ids.add(model_id)
                                    if has_fallback and (
                                        response.status_code == 202
                                        or self._can_fallback(response.status_code)
                                    ):
                                        continue
                                    if response.status_code == 429:
                                        self._open_rate_limit_cooldown(response)
                                        raise NvidiaNimRateLimited("NVIDIA NIM rate limit reached")
                                    raise NvidiaNimError(
                                        f"NVIDIA NIM returned HTTP {response.status_code}"
                                    )
                                async for line in response.aiter_lines():
                                    if not line.startswith("data:"):
                                        continue
                                    raw_event = line[5:].strip()
                                    if not raw_event or raw_event == "[DONE]":
                                        continue
                                    try:
                                        event = json.loads(raw_event)
                                        choice = event["choices"][0]
                                        delta = choice.get("delta") or {}
                                    except (ValueError, KeyError, IndexError, TypeError) as error:
                                        raise NvidiaNimError(
                                            "NVIDIA NIM returned an invalid stream"
                                        ) from error
                                    if not isinstance(event, dict) or not isinstance(choice, dict):
                                        raise NvidiaNimError(
                                            "NVIDIA NIM returned an invalid stream"
                                        )
                                    chunk = delta.get("content")
                                    if chunk is not None:
                                        if not isinstance(chunk, str):
                                            raise NvidiaNimError(
                                                "NVIDIA NIM returned an invalid stream"
                                            )
                                        content_parts.append(chunk)
                                        if chunk and on_delta is not None:
                                            on_delta(chunk)
                                    raw_finish = choice.get("finish_reason")
                                    if isinstance(raw_finish, str):
                                        finish_reason = raw_finish
                                    raw_usage = event.get("usage") or {}
                                    if isinstance(raw_usage, dict):
                                        usage = {
                                            key: int(value)
                                            for key, value in raw_usage.items()
                                            if isinstance(value, int)
                                        }
                                if content_parts:
                                    break
                                if not has_fallback:
                                    raise NvidiaNimError("NVIDIA NIM returned an empty stream")
                        except httpx.HTTPError as error:
                            raise NvidiaNimError("NVIDIA NIM request failed") from error
            except TimeoutError as error:
                raise NvidiaNimError("NVIDIA NIM request timed out") from error

        content = "".join(content_parts)
        if not content:
            raise NvidiaNimError("NVIDIA NIM returned an empty stream")
        return AgentResult(
            role=role,
            model_id=selected_model,
            content=content,
            finish_reason=finish_reason,
            raw_usage=usage,
        )

    @staticmethod
    def _can_fallback(status_code: int) -> bool:
        return status_code in {404, 408, 409, 410, 425, 429} or status_code >= 500

    async def embed(
        self,
        texts: Sequence[str],
        *,
        input_type: EmbeddingInputType,
    ) -> EmbeddingBatch:
        if not 1 <= len(texts) <= 16:
            raise ValueError("embedding batch size is out of range")
        if any(not isinstance(text, str) for text in texts):
            raise ValueError("embedding inputs must be strings")
        normalized_texts = tuple(texts)
        if any(not text or len(text.encode("utf-8")) > 16_384 for text in normalized_texts):
            raise ValueError("embedding input is empty or too large")
        payload = {
            "model": self._settings.nvidia_embedding_model_id,
            "input": list(normalized_texts),
            "input_type": input_type.value,
            "encoding_format": "float",
            "truncate": "END",
        }
        async with self._semaphore:
            self._raise_if_rate_limited()
            try:
                response = await self._client.post(
                    "/embeddings",
                    headers=self._headers(),
                    json=payload,
                )
            except httpx.HTTPError as error:
                raise NvidiaNimError("NVIDIA NIM embedding request failed") from error
            if response.status_code == 429:
                self._open_rate_limit_cooldown(response)
                raise NvidiaNimRateLimited("NVIDIA NIM embedding rate limit reached")

        if response.is_error:
            raise NvidiaNimError(f"NVIDIA NIM embeddings returned HTTP {response.status_code}")

        try:
            data = response.json()
            raw_vectors = sorted(data["data"], key=lambda item: item["index"])
            indices = [item["index"] for item in raw_vectors]
            if any(isinstance(index, bool) or not isinstance(index, int) for index in indices):
                raise ValueError("embedding indices are invalid")
            if indices != list(range(len(normalized_texts))):
                raise ValueError("embedding indices are invalid")
            vectors = tuple(self._normalize_vector(item["embedding"]) for item in raw_vectors)
            if len(vectors) != len(normalized_texts):
                raise ValueError("embedding count does not match input")
            return EmbeddingBatch(
                model_id=self._settings.nvidia_embedding_model_id,
                vectors=vectors,
            )
        except (KeyError, TypeError, ValueError) as error:
            raise NvidiaNimError("NVIDIA NIM returned invalid embeddings") from error

    async def synthesize_speech(self, text: str) -> bytes:
        normalized = self._normalize_speech_text(text)

        async with self._semaphore:
            self._raise_if_rate_limited()
            try:
                async with asyncio.timeout(self._settings.nvidia_tts_timeout_seconds):
                    response = await self._client.post(
                        str(self._settings.nvidia_tts_url),
                        headers={
                            "Authorization": self._authorization_value(),
                            "Accept": "audio/wav",
                        },
                        files={
                            "text": (None, normalized),
                            "language": (None, self._settings.nvidia_tts_language),
                            "voice": (None, self._settings.nvidia_tts_voice),
                            "encoding": (None, "LINEAR_PCM"),
                            "sample_rate_hz": (None, "44100"),
                        },
                    )
            except (TimeoutError, httpx.TimeoutException) as error:
                raise NvidiaNimError("NVIDIA NIM speech request timed out") from error
            except httpx.HTTPError as error:
                raise NvidiaNimError("NVIDIA NIM speech request failed") from error

        if response.status_code == 429:
            self._open_rate_limit_cooldown(response)
            raise NvidiaNimRateLimited("NVIDIA NIM speech rate limit reached")
        if response.is_error:
            raise NvidiaNimError(f"NVIDIA NIM speech returned HTTP {response.status_code}")
        audio = response.content
        if not 44 <= len(audio) <= _MAX_TTS_AUDIO_BYTES:
            raise NvidiaNimError("NVIDIA NIM speech returned invalid audio")
        try:
            with wave.open(io.BytesIO(audio), "rb") as wav:
                valid = (
                    wav.getnchannels() in {1, 2}
                    and wav.getsampwidth() == 2
                    and 8_000 <= wav.getframerate() <= 48_000
                    and 0 < wav.getnframes() <= wav.getframerate() * 120
                    and wav.getcomptype() == "NONE"
                )
        except (EOFError, wave.Error):
            valid = False
        if not valid:
            raise NvidiaNimError("NVIDIA NIM speech returned invalid audio")
        return audio

    async def stream_speech(self, text: str) -> AsyncIterator[bytes]:
        normalized = self._normalize_speech_text(text)
        total_bytes = 0
        async with self._semaphore:
            self._raise_if_rate_limited()
            try:
                async with asyncio.timeout(self._settings.nvidia_tts_timeout_seconds):
                    async with self._client.stream(
                        "POST",
                        str(self._settings.nvidia_tts_stream_url),
                        headers={
                            "Authorization": self._authorization_value(),
                            "Accept": "application/octet-stream",
                        },
                        files={
                            "text": (None, normalized),
                            "language": (None, self._settings.nvidia_tts_language),
                            "voice": (None, self._settings.nvidia_tts_voice),
                            "encoding": (None, "LINEAR_PCM"),
                            "sample_rate_hz": (None, "22050"),
                        },
                    ) as response:
                        if response.status_code == 429:
                            self._open_rate_limit_cooldown(response)
                            raise NvidiaNimRateLimited(
                                "NVIDIA NIM speech rate limit reached"
                            )
                        if response.is_error:
                            raise NvidiaNimError(
                                f"NVIDIA NIM speech returned HTTP {response.status_code}"
                            )
                        async for chunk in response.aiter_bytes():
                            if not chunk:
                                continue
                            total_bytes += len(chunk)
                            if total_bytes > _MAX_TTS_AUDIO_BYTES:
                                raise NvidiaNimError(
                                    "NVIDIA NIM speech returned invalid audio"
                                )
                            yield chunk
            except (TimeoutError, httpx.TimeoutException) as error:
                raise NvidiaNimError("NVIDIA NIM speech request timed out") from error
            except httpx.HTTPError as error:
                raise NvidiaNimError("NVIDIA NIM speech request failed") from error
        if total_bytes == 0:
            raise NvidiaNimError("NVIDIA NIM speech returned invalid audio")

    @staticmethod
    def _normalize_speech_text(text: str) -> str:
        normalized = " ".join(text.split()) if isinstance(text, str) else ""
        if (
            not normalized
            or len(normalized) > 2_000
            or len(normalized.encode("utf-8")) > _MAX_TTS_TEXT_BYTES
        ):
            raise ValueError("speech synthesis text is out of range")
        return normalized

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": self._authorization_value(),
            "Accept": "application/json",
            "Content-Type": "application/json",
        }

    def _authorization_value(self) -> str:
        try:
            api_key = self._api_key_loader()
        except (OSError, RuntimeError) as error:
            raise NvidiaNimError("NVIDIA NIM credential is unavailable") from error
        return f"Bearer {api_key}"

    def _raise_if_rate_limited(self) -> None:
        if self._cooldown.active():
            raise NvidiaNimRateLimited("NVIDIA NIM rate limit cooldown active")

    def _open_rate_limit_cooldown(self, response: httpx.Response) -> None:
        delay = self._settings.nvidia_rate_limit_cooldown_seconds
        try:
            retry_after = float(response.headers.get("Retry-After", ""))
        except ValueError:
            retry_after = 0.0
        if math.isfinite(retry_after) and retry_after > 0:
            delay = min(60.0, max(delay, retry_after))
        self._cooldown.open(delay)

    @staticmethod
    def _selected_model_ids(
        primary_model_id: str,
        fallback_model_id: str | None,
        requested: Sequence[str] | None,
    ) -> tuple[str, ...]:
        candidates = (
            tuple(requested)
            if requested is not None
            else tuple(
                model_id
                for model_id in (primary_model_id, fallback_model_id)
                if model_id is not None
            )
        )
        if (
            not candidates
            or len(candidates) > 2
            or len(set(candidates)) != len(candidates)
            or any(
                not isinstance(model_id, str)
                or len(model_id) > 256
                or re.fullmatch(r"[a-z0-9][a-z0-9._-]*/[a-z0-9][a-z0-9._-]*", model_id)
                is None
                for model_id in candidates
            )
        ):
            raise ValueError("NVIDIA model override is invalid")
        return candidates

    @staticmethod
    def _normalize_vector(raw_vector: object) -> tuple[float, ...]:
        if not isinstance(raw_vector, list) or not 1 <= len(raw_vector) <= 8_192:
            raise ValueError("embedding vector dimensions are invalid")
        if any(
            isinstance(value, bool) or not isinstance(value, (int, float)) for value in raw_vector
        ):
            raise ValueError("embedding vector values are invalid")
        vector = tuple(float(value) for value in raw_vector)
        if any(not math.isfinite(value) for value in vector):
            raise ValueError("embedding vector values must be finite")
        norm = math.sqrt(math.fsum(value * value for value in vector))
        if norm <= 0.0:
            raise ValueError("embedding vector norm must be positive")
        return tuple(value / norm for value in vector)

    @staticmethod
    def _parse_tool_arguments(value: object) -> dict[str, Any]:
        decoded = json.loads(value) if isinstance(value, str) else value
        if not isinstance(decoded, dict):
            raise ValueError("tool arguments must be an object")
        return decoded
