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
from aegis_core.brain.nvidia_dialogue_guard import (
    needs_dialogue_review,
)
from aegis_core.brain.nvidia_dialogue_guard import (
    review_dialogue as validate_dialogue_reply,
)
from aegis_core.contracts import AgentResult, AgentRole
from aegis_core.orchestration.task_progress import (
    CONTINUATIONS,
    reject_duplicate_calls,
    repeated_followup_offer,
)
from aegis_core.privacy import redact_secrets_for_remote
from aegis_core.providers.base import IncompleteModelResponseError, require_complete_response
from aegis_core.providers.inference_metrics import begin_metrics, end_metrics
from aegis_core.providers.nvidia import NvidiaNimError, NvidiaNimRateLimited
from aegis_core.request_intent import folded_request
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
        review_dialogue: bool = False,
    ) -> AgentResult:
        started = time.perf_counter_ns()
        outcome = "failed"
        model_id: str | None = None
        provider_status: int | None = None
        dialogue_review = "not_requested"
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
        metrics, metrics_token = begin_metrics()
        try:
            if extra_body and (extra_body.get("tools") or extra_body.get("response_format")):
                # Tool proposals and structured judgments must be complete before publication.
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
                current = sanitized[-1].get("content", "") if sanitized else ""
                guarded_followup = (isinstance(current, str)
                                    and folded_request(current) in CONTINUATIONS
                                    and any(m.get("role") == "assistant" for m in sanitized))
                check_dialogue = review_dialogue and needs_dialogue_review(sanitized)
                if check_dialogue:
                    # Fresh intent + bounded answer generation replaces the draft
                    # previously discarded by this same guard on independent turns.
                    dialogue_review = "incomplete"
                    result, dialogue_review = await validate_dialogue_reply(
                        self._provider, sanitized, role=role,
                    )
                    if on_delta is not None:
                        on_delta(result.content)
                elif guarded_followup:
                    # Buffer contextual turns so a rejected draft never reaches chat/history/TTS.
                    result = await self._provider.complete(
                        role=role, messages=sanitized, max_tokens=max_tokens,
                        temperature=temperature, extra_body=extra_body,
                    )
                    if guarded_followup and repeated_followup_offer(sanitized, result.content):
                        result = await self._provider.complete(
                            role=role, messages=[*sanitized, {
                                "role": "system", "content": "El usuario ya respondió a la oferta. "
                                "Retoma el objetivo pendiente usando el contexto y las "
                                "herramientas disponibles. No repitas la oferta ni afirmes una "
                                "acción sin evidencia. Si falta un dato esencial, pregunta por ese "
                                "dato concreto; si falta una herramienta, explica la limitación.",
                            }], max_tokens=max_tokens, temperature=0.0, extra_body=extra_body,
                        )
                        if repeated_followup_offer(sanitized, result.content):
                            raise IncompleteModelResponseError("conversation made no progress")
                    result = require_complete_response(result)
                    if on_delta is not None and not result.tool_calls:
                        on_delta(result.content)
                else:
                    result = await self._provider.complete_stream(
                        role=role, messages=sanitized, max_tokens=max_tokens,
                        temperature=temperature, extra_body=extra_body, on_delta=on_delta,
                    )
            result = require_complete_response(result)
            try:
                reject_duplicate_calls(result.tool_calls)
            except ValueError as error:
                raise IncompleteModelResponseError("duplicate tool proposal") from error
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
            provider_status = error.status_code
            if error.status_code in {401, 403}:
                outcome = "authentication_failed"
                raise RemoteAuthenticationError("NVIDIA credential rejected") from error
            raise RemoteProviderUnavailableError("NVIDIA inference unavailable") from error
        finally:
            end_metrics(metrics_token)
            self._audit.record_system_event(
                request_id or uuid4(), event_type="nvidia_inference", component="nvidia_brain",
                data={"outcome": outcome, "role": role.value, "model_id": model_id,
                      "dialogue_review": dialogue_review,
                      "provider_status": provider_status,
                      **metrics.public_dict(),
                      "latency_ms": (time.perf_counter_ns() - started) // 1_000_000},
            )
