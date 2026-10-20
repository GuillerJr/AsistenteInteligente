from __future__ import annotations

import asyncio
import copy
import io
import json
import math
import re
import threading
import time
import wave
from collections.abc import AsyncIterator, Callable, Mapping, Sequence
from contextlib import asynccontextmanager
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
    IncompleteModelResponseError,
)
from aegis_core.providers.inference_metrics import record_http_request, record_usage
from aegis_core.settings_store import load_model_preferences, selected_model_registry
from aegis_core.speech_pcm import SpeechPauseSmoother

_ALLOWED_EXTRA_BODY_KEYS = frozenset(
    {"chat_template_kwargs", "response_format", "tool_choice", "tools"}
)
_MAX_TTS_TEXT_BYTES = 8_192
_MAX_TTS_AUDIO_BYTES = 8_388_608
_TOOL_PLANNING_PRIMARY_SECONDS = 12.0


class NvidiaNimError(EmbeddingProviderError):
    def __init__(
        self,
        message: str,
        *,
        status_code: int | None = None,
        terminal: bool = False,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.terminal = terminal


class NvidiaNimRateLimited(NvidiaNimError):
    def __init__(self, message: str) -> None:
        super().__init__(message, status_code=429, terminal=True)


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


def _inference_tool_schemas(tools: Any) -> Any:
    """Prefer omission over nullable default branches in optional tool arguments.

    NIM's constrained decoder can stall on these unions in a conversation. An
    omitted optional argument already receives the same null default downstream.
    Only the inference copy is narrowed; the broker keeps its original schema.
    Required nulls, non-null defaults and unions of several value types survive.
    """
    result = copy.deepcopy(tools)
    if not isinstance(result, list):
        return result
    for tool in result:
        if not isinstance(tool, dict) or not isinstance(tool.get("function"), dict):
            continue
        schema = tool["function"].get("parameters")
        if not isinstance(schema, dict) or not isinstance(schema.get("properties"), dict):
            continue
        required = schema.get("required", [])
        for name, field in schema["properties"].items():
            if (
                name in required
                or not isinstance(field, dict)
                or "default" not in field
                or field["default"] is not None
            ):
                continue
            branches = field.get("anyOf")
            if not isinstance(branches, list) or len(branches) != 2:
                continue
            values = [branch for branch in branches if branch != {"type": "null"}]
            if len(values) != 1 or not isinstance(values[0], dict):
                continue
            field.pop("anyOf")
            field.pop("default")
            field.update(values[0])
    return result


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
        try:
            self._models = selected_model_registry(
                load_model_preferences(settings.model_preferences_path)
            )
        except (OSError, ValueError):
            self._models = {role: model_for(role) for role in AgentRole}
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

    def _attempt_budget(
        self,
        deadline: float,
        model_ids: Sequence[str],
        attempt: int,
        *,
        tool_planning: bool = False,
        primary_timeout_seconds: float | None = None,
    ) -> float:
        remaining = max(0.0, deadline - asyncio.get_running_loop().time())
        available = sum(model not in self._unavailable_model_ids for model in model_ids[attempt:])
        # A stalled primary must leave time for the user's configured remote fallback.
        budget = remaining if available <= 1 else remaining * 2 / (available + 1)
        if attempt == 0 and available > 1 and primary_timeout_seconds is not None:
            budget = min(budget, primary_timeout_seconds)
        if tool_planning and attempt == 0 and available > 1:
            budget = min(budget, _TOOL_PLANNING_PRIMARY_SECONDS)
        return budget

    @staticmethod
    def _attempt_payload(
        payload: Mapping[str, Any],
        role: AgentRole,
        model_id: str,
        *,
        extended_reasoning: bool = False,
        perception_only: bool = False,
    ) -> dict[str, Any]:
        attempt = {**payload, "model": model_id}
        if (
            perception_only
            and role is AgentRole.VISION
            and model_id == "nvidia/nemotron-3-nano-omni-30b-a3b-reasoning"
            and "chat_template_kwargs" not in attempt
        ):
            attempt["chat_template_kwargs"] = {"enable_thinking": False}
        if (
            role in {AgentRole.ROUTER, AgentRole.PLANNER, AgentRole.SYNTHESIZER}
            and "chat_template_kwargs" not in attempt
        ):
            # NVIDIA's spoken-agent recipe recommends direct responses for this path.
            # Complex reasoning and code-security roles retain their model defaults.
            if model_id in {
                "nvidia/nemotron-3.5-lightning-30b-a3b",
                "nvidia/nemotron-3-nano-30b-a3b",
                # Ultra rejects Super's low_effort option (HTTP 400); with its default
                # reasoning a closed call took 5.8 s and returned malformed JSON.
                "nvidia/nemotron-3-ultra-550b-a55b",
            }:
                attempt["chat_template_kwargs"] = {"enable_thinking": False}
            elif role is AgentRole.SYNTHESIZER and model_id == "nvidia/nemotron-3-super-120b-a12b":
                # Closed dialogue keeps enough reasoning to resolve references
                # without inheriting Super's extended default reasoning budget.
                options = {"enable_thinking": True, "low_effort": True}
                limit = payload.get("max_tokens")
                if type(limit) is int and limit > 1:
                    options["reasoning_budget"] = min(
                        2048 if extended_reasoning else 512, limit // 2
                    )
                attempt["chat_template_kwargs"] = options
            elif model_id in {
                "deepseek-ai/deepseek-v4-pro-0813",
                "deepseek-ai/deepseek-v4-flash-0731",
            }:
                attempt["chat_template_kwargs"] = {"thinking": False}
            elif role is AgentRole.SYNTHESIZER and model_id in {
                "openai/gpt-oss-20b",
                "openai/gpt-oss-120b",
            }:
                # Closed comparisons/response assembly need bounded reasoning, not
                # the endpoint's default medium effort (NVIDIA's documented option).
                attempt["reasoning_effort"] = "medium" if extended_reasoning else "low"
        return attempt

    async def __aenter__(self) -> NvidiaNimClient:
        return self

    @property
    def model_id(self) -> str:
        return self._settings.nvidia_embedding_model_id

    def completion_model_id(self, role: AgentRole) -> str:
        """Selected chat model for a role, without exposing credentials or mutating preferences."""
        return self._models[role].model_id

    def completion_model_ids(self, role: AgentRole) -> tuple[str, ...]:
        """Return the loaded owner selection, including its configured fallback."""
        spec = self._models[role]
        return tuple(dict.fromkeys(
            identifier for identifier in (spec.model_id, spec.fallback_model_id) if identifier
        ))

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

    @asynccontextmanager
    async def _request_slot(self, timeout_seconds: float, *, kind: str = "request"):
        # Queueing and credential loading are part of the same request budget as
        # HTTP/fallbacks. Never send a request after its caller's deadline expired.
        deadline = asyncio.get_running_loop().time() + timeout_seconds
        try:
            async with asyncio.timeout_at(deadline), self._semaphore:
                self._raise_if_rate_limited()
                yield deadline
        except TimeoutError as error:
            raise NvidiaNimError(f"NVIDIA NIM {kind} timed out") from error

    async def complete_dialogue_review(
        self,
        *,
        draft_model_id: str,
        messages: Sequence[Mapping[str, Any]],
        max_tokens: int,
        temperature: float,
        extra_body: Mapping[str, Any],
        timeout_seconds: float | None = None,
        review_role: AgentRole = AgentRole.SYNTHESIZER,
        extended_reasoning: bool = False,
    ) -> AgentResult:
        # A fresh closed call keeps configured order. A supplied answer or intent
        # result can select an independent configured peer. Never mutate preferences.
        spec = self._models[review_role]
        candidates = tuple(
            dict.fromkeys(model for model in (spec.model_id, spec.fallback_model_id) if model)
        )
        models = tuple(model for model in candidates if model != draft_model_id)
        models += tuple(model for model in candidates if model == draft_model_id)
        return await self.complete(
            # This is closed response assembly, even when using the configured
            # reasoning models. Keep its bounded synthesis inference options.
            role=AgentRole.SYNTHESIZER,
            messages=messages,
            max_tokens=max_tokens,
            temperature=temperature,
            extra_body=extra_body,
            model_ids=models,
            timeout_seconds=timeout_seconds,
            primary_timeout_seconds=3 if review_role is AgentRole.SYNTHESIZER else None,
            extended_reasoning=extended_reasoning,
        )

    async def complete(
        self,
        *,
        role: AgentRole,
        messages: Sequence[Mapping[str, Any]],
        max_tokens: int | None = None,
        temperature: float | None = None,
        extra_body: Mapping[str, Any] | None = None,
        model_ids: Sequence[str] | None = None,
        timeout_seconds: float | None = None,
        primary_timeout_seconds: float | None = None,
        extended_reasoning: bool = False,
        perception_only: bool = False,
    ) -> AgentResult:
        if primary_timeout_seconds is not None and (
            not math.isfinite(primary_timeout_seconds) or primary_timeout_seconds <= 0
        ):
            raise ValueError("invalid primary NVIDIA time budget")
        if extra_body and not set(extra_body).issubset(_ALLOWED_EXTRA_BODY_KEYS):
            raise ValueError("NVIDIA request contains an unapproved option")
        spec = self._models[role]
        if timeout_seconds is not None and (
            not math.isfinite(timeout_seconds) or timeout_seconds <= 0
        ):
            raise ValueError("invalid NVIDIA completion time budget")
        time_budget = (
            self._settings.request_timeout_seconds
            if timeout_seconds is None
            else min(timeout_seconds, self._settings.request_timeout_seconds)
        )
        payload: dict[str, Any] = {
            "messages": list(messages),
            "max_tokens": max_tokens or self._settings.max_output_tokens,
            "stream": False,
        }
        if temperature is not None:
            payload["temperature"] = temperature
        if extra_body:
            payload.update(extra_body)
        if "tools" in payload:
            payload["tools"] = _inference_tool_schemas(payload["tools"])

        selected_model_ids = self._selected_model_ids(
            spec.model_id,
            spec.fallback_model_id,
            model_ids,
        )

        result: AgentResult | None = None
        async with self._request_slot(time_budget) as deadline:
            self._raise_if_rate_limited()
            headers = await asyncio.to_thread(self._headers)
            self._raise_if_rate_limited()
            try:
                async with asyncio.timeout_at(deadline):
                    for attempt, model_id in enumerate(selected_model_ids):
                        if model_id in self._unavailable_model_ids:
                            continue
                        attempt_payload = self._attempt_payload(
                            payload,
                            role,
                            model_id,
                            extended_reasoning=extended_reasoning,
                            perception_only=perception_only,
                        )
                        has_fallback = any(
                            candidate not in self._unavailable_model_ids
                            for candidate in selected_model_ids[attempt + 1 :]
                        )
                        try:
                            async with asyncio.timeout(
                                self._attempt_budget(
                                    deadline,
                                    selected_model_ids,
                                    attempt,
                                    primary_timeout_seconds=primary_timeout_seconds,
                                    tool_planning=(
                                        role is AgentRole.PLANNER
                                        and isinstance(payload.get("tool_choice"), Mapping)
                                    ),
                                )
                            ):
                                record_http_request()
                                response = await self._client.post(
                                    "/chat/completions", headers=headers, json=attempt_payload
                                )
                        except (TimeoutError, httpx.ReadTimeout) as error:
                            if has_fallback:
                                continue
                            raise NvidiaNimError("NVIDIA NIM request timed out") from error
                        except httpx.HTTPError as error:
                            raise NvidiaNimError("NVIDIA NIM request failed") from error

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
                        if response.status_code == 429:
                            self._open_rate_limit_cooldown(response)
                            raise NvidiaNimRateLimited("NVIDIA NIM rate limit reached")
                        if has_fallback and self._can_fallback(response.status_code):
                            continue
                        raise NvidiaNimError(
                            f"NVIDIA NIM returned HTTP {response.status_code}",
                            status_code=response.status_code,
                            terminal=response.status_code in {404, 410},
                        )
            except TimeoutError as error:
                raise NvidiaNimError("NVIDIA NIM request timed out") from error
        if result is None:
            raise NvidiaNimError(
                "NVIDIA NIM has no available model for this role",
                status_code=410,
                terminal=True,
            )
        record_usage(result.raw_usage)
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
        finish_reason = choice.get("finish_reason")
        if finish_reason is not None and not isinstance(finish_reason, str):
            raise NvidiaNimError("NVIDIA NIM returned an invalid finish reason")
        if finish_reason in {"length", "content_filter"}:
            # Do not expose truncated code or authorize a partially generated call.
            # This is intentionally not a retryable transport failure.
            raise IncompleteModelResponseError("NVIDIA NIM returned an incomplete response")

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
                    key: value for key, value in usage.items() if type(value) is int and value >= 0
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
        if extra_body and not set(extra_body).issubset({"chat_template_kwargs"}):
            raise ValueError("streaming tool calls are not supported")
        spec = self._models[role]
        payload: dict[str, Any] = {
            "messages": list(messages),
            "max_tokens": max_tokens or self._settings.max_output_tokens,
            "stream": True,
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
        content_parts: list[str] = []
        finish_reason: str | None = None
        usage: dict[str, int] = {}
        selected_model = selected_model_ids[0]

        async with self._request_slot(self._settings.request_timeout_seconds) as deadline:
            self._raise_if_rate_limited()
            headers = await asyncio.to_thread(self._headers)
            self._raise_if_rate_limited()
            try:
                async with asyncio.timeout_at(deadline):
                    for attempt, model_id in enumerate(selected_model_ids):
                        if model_id in self._unavailable_model_ids:
                            continue
                        attempt_payload = self._attempt_payload(payload, role, model_id)
                        selected_model = model_id
                        # Metadata belongs to the selected attempt, never an empty
                        # or rejected primary that preceded a successful fallback.
                        content_parts.clear()
                        usage.clear()
                        finish_reason = None
                        stream_done = False
                        has_fallback = any(
                            candidate not in self._unavailable_model_ids
                            for candidate in selected_model_ids[attempt + 1 :]
                        )
                        try:
                            record_http_request()
                            async with (
                                asyncio.timeout(
                                    self._attempt_budget(
                                        deadline,
                                        selected_model_ids,
                                        attempt,
                                    )
                                ) as attempt_timeout,
                                self._client.stream(
                                    "POST",
                                    "/chat/completions",
                                    headers=headers,
                                    json=attempt_payload,
                                ) as response,
                            ):
                                if response.is_error or response.status_code == 202:
                                    if response.status_code in {404, 410}:
                                        self._unavailable_model_ids.add(model_id)
                                    if response.status_code == 429:
                                        self._open_rate_limit_cooldown(response)
                                        raise NvidiaNimRateLimited("NVIDIA NIM rate limit reached")
                                    if has_fallback and (
                                        response.status_code == 202
                                        or self._can_fallback(response.status_code)
                                    ):
                                        continue
                                    raise NvidiaNimError(
                                        f"NVIDIA NIM returned HTTP {response.status_code}",
                                        status_code=response.status_code,
                                        terminal=response.status_code in {404, 410},
                                    )
                                async for line in response.aiter_lines():
                                    if not line.startswith("data:"):
                                        continue
                                    raw_event = line[5:].strip()
                                    if not raw_event:
                                        continue
                                    if raw_event == "[DONE]":
                                        stream_done = True
                                        break
                                    try:
                                        event = json.loads(raw_event)
                                        if not isinstance(event, dict):
                                            raise ValueError("event is not an object")
                                        raw_usage = event.get("usage")
                                        if isinstance(raw_usage, dict):
                                            usage.update(
                                                {
                                                    key: value
                                                    for key, value in raw_usage.items()
                                                    if type(value) is int and value >= 0
                                                }
                                            )
                                        # OpenAI-compatible SSE may report usage separately
                                        # after the last delta, with an empty choices array.
                                        if event.get("choices") == [] and isinstance(
                                            raw_usage, dict
                                        ):
                                            continue
                                        choice = event["choices"][0]
                                        if not isinstance(choice, dict):
                                            raise ValueError("choice is not an object")
                                        delta = choice.get("delta") or {}
                                        if not isinstance(delta, dict):
                                            raise ValueError("delta is not an object")
                                    except (ValueError, KeyError, IndexError, TypeError) as error:
                                        raise NvidiaNimError(
                                            "NVIDIA NIM returned an invalid stream"
                                        ) from error
                                    raw_finish = choice.get("finish_reason")
                                    if raw_finish is not None and not isinstance(raw_finish, str):
                                        raise NvidiaNimError(
                                            "NVIDIA NIM returned an invalid stream"
                                        )
                                    if raw_finish is not None and raw_finish != "stop":
                                        raise IncompleteModelResponseError(
                                            "NVIDIA NIM returned an incomplete stream"
                                        )
                                    chunk = delta.get("content")
                                    if chunk is not None:
                                        if not isinstance(chunk, str):
                                            raise NvidiaNimError(
                                                "NVIDIA NIM returned an invalid stream"
                                            )
                                        if chunk and finish_reason is not None:
                                            raise NvidiaNimError(
                                                "NVIDIA NIM returned content after stream finish"
                                            )
                                        content_parts.append(chunk)
                                        if chunk:
                                            # After visible output, finish this attempt. Switching
                                            # models would mix answers or repeat the response.
                                            attempt_timeout.reschedule(deadline)
                                        if chunk and on_delta is not None:
                                            on_delta(chunk)
                                    if isinstance(raw_finish, str):
                                        finish_reason = raw_finish
                                if any(content_parts):
                                    if not stream_done or finish_reason != "stop":
                                        raise IncompleteModelResponseError(
                                            "NVIDIA NIM returned an incomplete stream"
                                        )
                                    break
                                if not has_fallback:
                                    raise NvidiaNimError("NVIDIA NIM returned an empty stream")
                        except (TimeoutError, httpx.ReadTimeout) as error:
                            if has_fallback and not any(content_parts):
                                continue
                            raise NvidiaNimError("NVIDIA NIM request timed out") from error
                        except httpx.HTTPError as error:
                            raise NvidiaNimError("NVIDIA NIM request failed") from error
            except TimeoutError as error:
                raise NvidiaNimError("NVIDIA NIM request timed out") from error

        content = "".join(content_parts)
        if not content:
            if all(model_id in self._unavailable_model_ids for model_id in selected_model_ids):
                raise NvidiaNimError(
                    "NVIDIA NIM has no available model for this role",
                    status_code=410,
                    terminal=True,
                )
            raise NvidiaNimError("NVIDIA NIM returned an empty stream")
        record_usage(usage)
        return AgentResult(
            role=role,
            model_id=selected_model,
            content=content,
            finish_reason=finish_reason,
            raw_usage=usage,
        )

    @staticmethod
    def _can_fallback(status_code: int) -> bool:
        return status_code in {404, 408, 409, 410, 425} or status_code >= 500

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
        async with self._request_slot(
            self._settings.request_timeout_seconds, kind="embedding request"
        ):
            self._raise_if_rate_limited()
            headers = await asyncio.to_thread(self._headers)
            self._raise_if_rate_limited()
            try:
                response = await self._client.post(
                    "/embeddings",
                    headers=headers,
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

        async with self._request_slot(
            self._settings.nvidia_tts_timeout_seconds, kind="speech request"
        ) as deadline:
            self._raise_if_rate_limited()
            authorization = await asyncio.to_thread(self._authorization_value)
            self._raise_if_rate_limited()
            try:
                async with asyncio.timeout_at(deadline):
                    response = await self._client.post(
                        str(self._settings.nvidia_tts_url),
                        headers={
                            "Authorization": authorization,
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
            raise NvidiaNimError(
                f"NVIDIA NIM speech returned HTTP {response.status_code}",
                status_code=response.status_code,
                terminal=response.status_code in {401, 403},
            )
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
        smoother = SpeechPauseSmoother() if self._settings.nvidia_tts_smooth_pauses else None
        total_bytes = 0
        async with self._request_slot(
            self._settings.nvidia_tts_timeout_seconds, kind="speech request"
        ) as deadline:
            self._raise_if_rate_limited()
            authorization = await asyncio.to_thread(self._authorization_value)
            self._raise_if_rate_limited()
            try:
                async with asyncio.timeout_at(deadline):
                    async with self._client.stream(
                        "POST",
                        str(self._settings.nvidia_tts_stream_url),
                        headers={
                            "Authorization": authorization,
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
                            raise NvidiaNimRateLimited("NVIDIA NIM speech rate limit reached")
                        if response.is_error:
                            raise NvidiaNimError(
                                f"NVIDIA NIM speech returned HTTP {response.status_code}"
                            )
                        async for chunk in response.aiter_bytes():
                            if not chunk:
                                continue
                            total_bytes += len(chunk)
                            if total_bytes > _MAX_TTS_AUDIO_BYTES:
                                raise NvidiaNimError("NVIDIA NIM speech returned invalid audio")
                            if smoother is None:
                                yield chunk
                            else:
                                # Bound CPU work and allocation between async yields. The raw
                                # byte budget above still includes all discarded quiet samples.
                                for offset in range(0, len(chunk), 4_096):
                                    conditioned = smoother.process(chunk[offset : offset + 4_096])
                                    if conditioned:
                                        yield conditioned
                                    else:
                                        await asyncio.sleep(0)
                        if smoother is not None:
                            try:
                                smoother.finish()
                            except ValueError as error:
                                raise NvidiaNimError(
                                    "NVIDIA NIM speech returned incomplete PCM"
                                ) from error
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
            raise NvidiaNimError(
                "NVIDIA NIM credential is unavailable", status_code=401, terminal=True
            ) from error
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
                or re.fullmatch(r"[a-z0-9][a-z0-9._-]*/[a-z0-9][a-z0-9._-]*", model_id) is None
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
