"""NVIDIA-only inference: no local model, speculative draft or local fallback."""

from __future__ import annotations

import asyncio
import time
from collections.abc import Callable, Mapping, Sequence
from typing import Any
from uuid import UUID, uuid4

from aegis_core.brain.errors import (
    RemoteAuthenticationError,
    RemoteProviderUnavailableError,
    RemoteRateLimitedError,
)
from aegis_core.contracts import AgentResult, AgentRole
from aegis_core.privacy import redact_secrets_for_remote
from aegis_core.providers.base import IncompleteModelResponseError, require_complete_response
from aegis_core.providers.nvidia import NvidiaNimError, NvidiaNimRateLimited
from aegis_core.tools.audit import AuditSink, NullAuditSink


class NvidiaBrainClient:
    def __init__(self, provider: Any, *, audit_sink: AuditSink | None = None) -> None:
        self._provider = provider
        self._audit = audit_sink or NullAuditSink()

    async def complete_nvidia_only(
        self, *, role: AgentRole, messages: Sequence[Mapping[str, Any]],
        max_tokens: int | None = None, temperature: float | None = None,
        extra_body: Mapping[str, Any] | None = None, request_id: UUID | None = None,
        on_delta: Callable[[str], None] | None = None,
    ) -> AgentResult:
        started = time.perf_counter_ns()
        outcome = "failed"
        model_id: str | None = None
        sanitized = []
        for message in messages:
            item = dict(message)
            content = item.get("content")
            if isinstance(content, str):
                item["content"] = redact_secrets_for_remote(content).text
            elif isinstance(content, list):
                item["content"] = [
                    {**part, "text": redact_secrets_for_remote(part["text"]).text}
                    if isinstance(part, dict) and isinstance(part.get("text"), str) else part
                    for part in content
                ]
            sanitized.append(item)
        try:
            if extra_body and extra_body.get("tools"):
                # Never stream tool arguments or an offer to act as a completed answer.
                result = await self._provider.complete(
                    role=role, messages=sanitized, max_tokens=max_tokens,
                    temperature=temperature, extra_body=extra_body,
                )
                choice = extra_body.get("tool_choice", "auto")
                required = choice == "required" or isinstance(choice, Mapping)
                if required and not result.tool_calls:
                    retry_messages = [*sanitized, {
                        "role": "system",
                        "content": "La consulta ya fue solicitada. Devuelve una llamada a la "
                        "herramienta ofrecida; no pidas otra confirmación conversacional ni "
                        "inventes resultados. El broker gestiona cualquier aprobación real.",
                    }]
                    result = await self._provider.complete(
                        role=role, messages=retry_messages, max_tokens=max_tokens,
                        temperature=0.0, extra_body=extra_body,
                    )
                    if not result.tool_calls:
                        raise IncompleteModelResponseError("required tool was not proposed")
                if isinstance(choice, Mapping) and any(
                    call.tool_name != choice.get("function", {}).get("name")
                    for call in result.tool_calls
                ):
                    raise IncompleteModelResponseError("named tool choice was not honored")
            else:
                result = await self._provider.complete_stream(
                    role=role, messages=sanitized, max_tokens=max_tokens,
                    temperature=temperature, extra_body=extra_body, on_delta=on_delta,
                )
            result = require_complete_response(result)
            outcome = "completed"
            model_id = result.model_id
            return result
        except asyncio.CancelledError:
            outcome = "cancelled"
            raise
        except IncompleteModelResponseError:
            outcome = "incomplete"
            raise
        except NvidiaNimRateLimited as error:
            outcome = "rate_limited"
            raise RemoteRateLimitedError("NVIDIA cooldown active") from error
        except NvidiaNimError as error:
            if error.status_code in {401, 403}:
                outcome = "authentication_failed"
                raise RemoteAuthenticationError("NVIDIA credential rejected") from error
            raise RemoteProviderUnavailableError("NVIDIA inference unavailable") from error
        finally:
            self._audit.record_system_event(
                request_id or uuid4(), event_type="nvidia_inference", component="nvidia_brain",
                data={"outcome": outcome, "role": role.value, "model_id": model_id,
                      "latency_ms": (time.perf_counter_ns() - started) // 1_000_000},
            )
