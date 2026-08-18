from __future__ import annotations

import asyncio
import json
import math
from collections.abc import Callable, Mapping, Sequence
from typing import Any

import httpx

from aegis_core.config import Settings
from aegis_core.contracts import AgentResult, AgentRole, ToolCall
from aegis_core.models import model_for
from aegis_core.providers.base import (
    EmbeddingBatch,
    EmbeddingInputType,
    EmbeddingProviderError,
)


class NvidiaNimError(EmbeddingProviderError):
    pass


class NvidiaNimRateLimited(NvidiaNimError):
    pass


class NvidiaNimClient:
    def __init__(
        self,
        settings: Settings,
        api_key_loader: Callable[[], str],
        *,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._settings = settings
        self._api_key_loader = api_key_loader
        self._semaphore = asyncio.Semaphore(settings.max_concurrency)
        self._client = httpx.AsyncClient(
            base_url=str(settings.nvidia_base_url).rstrip("/"),
            timeout=settings.request_timeout_seconds,
            transport=transport,
        )

    async def __aenter__(self) -> NvidiaNimClient:
        return self

    async def __aexit__(self, *_: object) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        await self._client.aclose()

    async def complete(
        self,
        *,
        role: AgentRole,
        messages: Sequence[Mapping[str, Any]],
        max_tokens: int | None = None,
        temperature: float | None = None,
        extra_body: Mapping[str, Any] | None = None,
    ) -> AgentResult:
        spec = model_for(role)
        payload: dict[str, Any] = {
            "model": spec.model_id,
            "messages": list(messages),
            "max_tokens": max_tokens or self._settings.max_output_tokens,
            "stream": False,
        }
        if temperature is not None:
            payload["temperature"] = temperature
        if extra_body:
            payload.update(extra_body)

        headers = self._headers()

        async with self._semaphore:
            try:
                response = await self._client.post(
                    "/chat/completions", headers=headers, json=payload
                )
            except httpx.HTTPError as error:
                raise NvidiaNimError("NVIDIA NIM request failed") from error

        if response.status_code == 429:
            raise NvidiaNimRateLimited("NVIDIA NIM rate limit reached")
        if response.is_error:
            raise NvidiaNimError(f"NVIDIA NIM returned HTTP {response.status_code}")

        try:
            data = response.json()
            choice = data["choices"][0]
            message = choice["message"]
        except (ValueError, KeyError, IndexError, TypeError) as error:
            raise NvidiaNimError("NVIDIA NIM returned an invalid response") from error

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
                for raw_call in (message.get("tool_calls") or [])
            )
        except (KeyError, TypeError, ValueError) as error:
            raise NvidiaNimError("NVIDIA NIM returned an invalid tool call") from error

        usage = data.get("usage") or {}
        return AgentResult(
            role=role,
            model_id=spec.model_id,
            content=message.get("content") or "",
            finish_reason=choice.get("finish_reason"),
            raw_usage={key: int(value) for key, value in usage.items() if isinstance(value, int)},
            tool_calls=tool_calls,
        )

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
            try:
                response = await self._client.post(
                    "/embeddings",
                    headers=self._headers(),
                    json=payload,
                )
            except httpx.HTTPError as error:
                raise NvidiaNimError("NVIDIA NIM embedding request failed") from error

        if response.status_code == 429:
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

    def _headers(self) -> dict[str, str]:
        try:
            api_key = self._api_key_loader()
        except (OSError, RuntimeError) as error:
            raise NvidiaNimError("NVIDIA NIM credential is unavailable") from error
        return {
            "Authorization": f"Bearer {api_key}",
            "Accept": "application/json",
            "Content-Type": "application/json",
        }

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
