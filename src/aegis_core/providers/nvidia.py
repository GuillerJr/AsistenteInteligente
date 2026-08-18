from __future__ import annotations

import asyncio
import json
from collections.abc import Callable, Mapping, Sequence
from typing import Any

import httpx

from aegis_core.config import Settings
from aegis_core.contracts import AgentResult, AgentRole, ToolCall
from aegis_core.models import model_for


class NvidiaNimError(RuntimeError):
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

        headers = {
            "Authorization": f"Bearer {self._api_key_loader()}",
            "Accept": "application/json",
            "Content-Type": "application/json",
        }

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

    @staticmethod
    def _parse_tool_arguments(value: object) -> dict[str, Any]:
        decoded = json.loads(value) if isinstance(value, str) else value
        if not isinstance(decoded, dict):
            raise ValueError("tool arguments must be an object")
        return decoded
