from __future__ import annotations

import asyncio
import json
import math
import socket
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any
from uuid import UUID, uuid4

import httpx

from aegis_core.brain.routing import (
    CascadeDecision,
    ConfidenceMetrics,
    TokenConfidence,
    calibrate_token_confidence,
    contains_non_text_content,
    decide_cascade,
    extract_text,
)
from aegis_core.contracts import AgentResult, AgentRole
from aegis_core.providers.base import ChatProvider
from aegis_core.providers.nvidia import NvidiaNimClient, NvidiaNimError, NvidiaNimRateLimited
from aegis_core.tools.audit import AuditSink, NullAuditSink

MAX_LOCAL_REQUEST_BYTES = 32_768
MAX_LOCAL_RESPONSE_BYTES = 32_768
MAX_LOCAL_RESPONSE_TOKENS = 4_096


class MacLocalFoundationError(RuntimeError):
    """The fixed loopback Apple Foundation Model wrapper failed closed."""


@dataclass(frozen=True, slots=True)
class LocalFoundationResponse:
    result: AgentResult
    confidence: ConfidenceMetrics
    source: str


class MacLocalFoundationClient:
    def __init__(
        self,
        endpoint: str,
        *,
        model_id: str = "foundation",
        timeout_seconds: float = 20.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        url = httpx.URL(endpoint)
        if (
            url.scheme != "http"
            or url.host not in {"127.0.0.1", "localhost"}
            or url.port != 9999
            or url.path != "/v1/chat/completions"
            or url.query
            or url.fragment
            or model_id != "foundation"
            or not 1.0 <= timeout_seconds <= 60.0
        ):
            raise ValueError("local Foundation Model endpoint is invalid")
        self.model_id = model_id
        self._url = url
        self._timeout_seconds = timeout_seconds
        self._client = httpx.AsyncClient(
            timeout=httpx.Timeout(timeout_seconds),
            limits=httpx.Limits(max_connections=4, max_keepalive_connections=2),
            transport=transport,
            trust_env=False,
        )

    async def aclose(self) -> None:
        await self._client.aclose()

    def is_available(self) -> bool:
        try:
            with socket.create_connection(("127.0.0.1", 9999), timeout=0.25):
                return True
        except OSError:
            return False

    async def complete(
        self,
        *,
        role: AgentRole,
        messages: Sequence[Mapping[str, Any]],
        max_tokens: int | None = None,
        temperature: float | None = None,
        extra_body: Mapping[str, Any] | None = None,
    ) -> AgentResult:
        if extra_body:
            raise MacLocalFoundationError("local Foundation Model tools are unsupported")
        return (
            await self.complete_with_confidence(
                role=role,
                messages=messages,
                max_tokens=max_tokens,
                temperature=temperature,
            )
        ).result

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

    async def complete_with_confidence(
        self,
        *,
        role: AgentRole,
        messages: Sequence[Mapping[str, Any]],
        max_tokens: int | None = None,
        temperature: float | None = None,
    ) -> LocalFoundationResponse:
        bounded_messages = self._text_messages(messages)
        token_limit = max_tokens if max_tokens is not None else 512
        if (
            isinstance(token_limit, bool)
            or not isinstance(token_limit, int)
            or not 1 <= token_limit <= MAX_LOCAL_RESPONSE_TOKENS
        ):
            raise ValueError("local Foundation Model token limit is invalid")
        if temperature is not None and (
            isinstance(temperature, bool)
            or not isinstance(temperature, (int, float))
            or not math.isfinite(temperature)
            or not 0.0 <= float(temperature) <= 2.0
        ):
            raise ValueError("local Foundation Model temperature is invalid")
        payload: dict[str, Any] = {
            "model": self.model_id,
            "messages": bounded_messages,
            "max_tokens": token_limit,
            "stream": False,
            "logprobs": True,
            "top_logprobs": 5,
        }
        if temperature is not None:
            payload["temperature"] = float(temperature)
        if len(
            json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        ) > MAX_LOCAL_REQUEST_BYTES:
            raise MacLocalFoundationError("local Foundation Model request is too large")
        response = await self._post(payload)
        if response.status_code == 400:
            payload.pop("logprobs")
            payload.pop("top_logprobs")
            response = await self._post(payload)
        if response.status_code != 200:
            raise MacLocalFoundationError(
                f"local Foundation Model returned HTTP {response.status_code}"
            )
        return self._parse_response(response, role=role)

    async def _post(self, payload: Mapping[str, Any]) -> httpx.Response:
        try:
            async with asyncio.timeout(self._timeout_seconds):
                return await self._client.post(
                    self._url,
                    headers={"Accept": "application/json", "Content-Type": "application/json"},
                    json=payload,
                )
        except (TimeoutError, httpx.HTTPError) as error:
            raise MacLocalFoundationError("local Foundation Model request failed") from error

    def _parse_response(
        self,
        response: httpx.Response,
        *,
        role: AgentRole,
    ) -> LocalFoundationResponse:
        if len(response.content) > MAX_LOCAL_RESPONSE_BYTES:
            raise MacLocalFoundationError("local Foundation Model response is too large")
        try:
            payload = response.json()
            choice = payload["choices"][0]
            message = choice["message"]
            content = message["content"]
        except (ValueError, KeyError, IndexError, TypeError) as error:
            raise MacLocalFoundationError("local Foundation Model response is invalid") from error
        if (
            not isinstance(payload, dict)
            or not isinstance(choice, dict)
            or not isinstance(message, dict)
            or not isinstance(content, str)
            or not content.strip()
            or len(content.encode("utf-8")) > MAX_LOCAL_RESPONSE_BYTES
            or ("model" in payload and payload["model"] != self.model_id)
        ):
            raise MacLocalFoundationError("local Foundation Model response is invalid")
        usage = payload.get("usage") or {}
        if not isinstance(usage, dict):
            raise MacLocalFoundationError("local Foundation Model usage is invalid")
        finish_reason = choice.get("finish_reason")
        if finish_reason is not None and not isinstance(finish_reason, str):
            raise MacLocalFoundationError("local Foundation Model finish reason is invalid")
        confidence = calibrate_token_confidence(self._token_confidence(choice.get("logprobs")))
        return LocalFoundationResponse(
            result=AgentResult(
                role=role,
                model_id=self.model_id,
                content=content.strip(),
                finish_reason=finish_reason,
                raw_usage={
                    key: int(value)
                    for key, value in usage.items()
                    if isinstance(value, int) and not isinstance(value, bool) and value >= 0
                },
            ),
            confidence=confidence,
            source="maclocal_api",
        )

    @staticmethod
    def _text_messages(
        messages: Sequence[Mapping[str, Any]],
    ) -> list[dict[str, str]]:
        normalized: list[dict[str, str]] = []
        for message in messages:
            role = message.get("role")
            content = message.get("content")
            if not isinstance(role, str) or role not in {"system", "user", "assistant"}:
                raise MacLocalFoundationError("local Foundation Model message role is invalid")
            text = content if isinstance(content, str) else extract_text((message,))
            if not text or "\0" in text:
                raise MacLocalFoundationError("local Foundation Model requires text messages")
            normalized.append({"role": role, "content": text})
        if not normalized or not any(item["role"] == "user" for item in normalized):
            raise MacLocalFoundationError("local Foundation Model prompt is incomplete")
        return normalized

    @staticmethod
    def _token_confidence(raw: object) -> tuple[TokenConfidence, ...]:
        if not isinstance(raw, dict) or not isinstance(raw.get("content"), list):
            return ()
        parsed: list[TokenConfidence] = []
        for item in raw["content"]:
            if not isinstance(item, dict):
                return ()
            selected = item.get("logprob")
            top = item.get("top_logprobs")
            if (
                isinstance(selected, bool)
                or not isinstance(selected, (int, float))
                or not isinstance(top, list)
            ):
                return ()
            top_values: list[float] = []
            for candidate in top:
                value = candidate.get("logprob") if isinstance(candidate, dict) else None
                if isinstance(value, bool) or not isinstance(value, (int, float)):
                    return ()
                top_values.append(float(value))
            parsed.append(
                TokenConfidence(
                    selected_log_probability=float(selected),
                    top_log_probabilities=tuple(top_values),
                )
            )
        return tuple(parsed)


class LocalFoundationCascadeClient:
    def __init__(
        self,
        primary: MacLocalFoundationClient,
        secondary: ChatProvider,
    ) -> None:
        self._primary = primary
        self._secondary = secondary

    def is_available(self) -> bool:
        secondary_probe = getattr(self._secondary, "is_available", None)
        return self._primary.is_available() or (
            callable(secondary_probe) and bool(secondary_probe())
        )

    async def aclose(self) -> None:
        await self._primary.aclose()

    async def complete_with_confidence(
        self,
        *,
        role: AgentRole,
        messages: Sequence[Mapping[str, Any]],
        max_tokens: int | None = None,
        temperature: float | None = None,
    ) -> LocalFoundationResponse:
        try:
            return await self._primary.complete_with_confidence(
                role=role,
                messages=messages,
                max_tokens=max_tokens,
                temperature=temperature,
            )
        except (OSError, RuntimeError):
            result = await self._secondary.complete(
                role=role,
                messages=messages,
                max_tokens=max_tokens,
                temperature=temperature,
            )
            return LocalFoundationResponse(
                result=result,
                confidence=ConfidenceMetrics.unavailable(),
                source="native_foundation_helper",
            )

    async def complete(
        self,
        *,
        role: AgentRole,
        messages: Sequence[Mapping[str, Any]],
        max_tokens: int | None = None,
        temperature: float | None = None,
        extra_body: Mapping[str, Any] | None = None,
    ) -> AgentResult:
        if extra_body:
            raise MacLocalFoundationError("local Foundation Model tools are unsupported")
        return (
            await self.complete_with_confidence(
                role=role,
                messages=messages,
                max_tokens=max_tokens,
                temperature=temperature,
            )
        ).result


class HybridBrainClient:
    def __init__(
        self,
        local: LocalFoundationCascadeClient,
        nvidia: NvidiaNimClient,
        *,
        confidence_threshold: float = 0.82,
        audit_sink: AuditSink | None = None,
    ) -> None:
        if not 0.5 <= confidence_threshold <= 0.99:
            raise ValueError("hybrid confidence threshold is invalid")
        self._local = local
        self._nvidia = nvidia
        self._threshold = confidence_threshold
        self._audit = audit_sink or NullAuditSink()

    async def complete(
        self,
        *,
        role: AgentRole,
        messages: Sequence[Mapping[str, Any]],
        max_tokens: int | None = None,
        temperature: float | None = None,
        extra_body: Mapping[str, Any] | None = None,
    ) -> AgentResult:
        return await self.complete_cascade(
            role=role,
            local_messages=messages,
            remote_messages=messages,
            max_tokens=max_tokens,
            temperature=temperature,
            extra_body=extra_body,
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
        return await self.complete_cascade(
            role=role,
            local_messages=messages,
            remote_messages=messages,
            max_tokens=max_tokens,
            temperature=temperature,
            extra_body=extra_body,
            on_delta=on_delta,
        )

    async def complete_cascade(
        self,
        *,
        role: AgentRole,
        local_messages: Sequence[Mapping[str, Any]],
        remote_messages: Sequence[Mapping[str, Any]],
        max_tokens: int | None = None,
        temperature: float | None = None,
        extra_body: Mapping[str, Any] | None = None,
        allow_remote_fallback: bool = True,
        request_id: UUID | None = None,
        on_delta: Callable[[str], None] | None = None,
    ) -> AgentResult:
        audit_request_id = request_id or uuid4()
        local_response: LocalFoundationResponse | None = None
        try:
            local_response = await self._local.complete_with_confidence(
                role=role,
                messages=local_messages,
                max_tokens=max_tokens,
                temperature=temperature,
            )
        except (OSError, RuntimeError):
            local_response = None
        confidence = (
            local_response.confidence
            if local_response is not None
            else ConfidenceMetrics.unavailable()
        )
        decision = decide_cascade(
            role=role,
            messages=local_messages,
            confidence=confidence,
            threshold=self._threshold,
            extra_body=extra_body,
            contains_non_text_input=contains_non_text_content(local_messages),
        )
        if not decision.escalates and local_response is not None:
            self._record_route(audit_request_id, decision, local_response, "local")
            if on_delta is not None:
                on_delta(local_response.result.content)
            return local_response.result
        if not allow_remote_fallback:
            if local_response is None:
                raise MacLocalFoundationError("local-only inference is unavailable")
            self._record_route(audit_request_id, decision, local_response, "local_policy")
            if on_delta is not None:
                on_delta(local_response.result.content)
            return local_response.result
        try:
            if on_delta is not None and not extra_body:
                result = await self._nvidia.complete_stream(
                    role=role,
                    messages=remote_messages,
                    max_tokens=max_tokens,
                    temperature=temperature,
                    on_delta=on_delta,
                    model_ids=decision.model_ids,
                )
            else:
                result = await self._nvidia.complete(
                    role=role,
                    messages=remote_messages,
                    max_tokens=max_tokens,
                    temperature=temperature,
                    extra_body=extra_body,
                    model_ids=decision.model_ids,
                )
                if on_delta is not None and result.content:
                    on_delta(result.content)
        except (NvidiaNimRateLimited, NvidiaNimError):
            if local_response is None:
                raise
            self._record_route(audit_request_id, decision, local_response, "local_degraded")
            if on_delta is not None:
                on_delta(local_response.result.content)
            return local_response.result
        self._record_route(audit_request_id, decision, local_response, "nvidia")
        return result

    def _record_route(
        self,
        request_id: UUID,
        decision: CascadeDecision,
        local_response: LocalFoundationResponse | None,
        selected: str,
    ) -> None:
        confidence = (
            local_response.confidence.calibrated_probability
            if local_response is not None
            else 0.0
        )
        self._audit.record_system_event(
            request_id,
            event_type="brain_route",
            component="hybrid_brain",
            data={
                "selected": selected,
                "decision": decision.target.value,
                "reason": decision.reason,
                "confidence_milli": round(confidence * 1_000),
                "threshold_milli": round(self._threshold * 1_000),
                "local_source": local_response.source if local_response else "unavailable",
            },
        )
