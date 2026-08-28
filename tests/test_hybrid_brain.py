import json
from collections.abc import Mapping
from typing import Any
from uuid import UUID

import httpx
import pytest

from aegis_core.brain.hybrid_client import (
    HybridBrainClient,
    LocalFoundationResponse,
    MacLocalFoundationClient,
)
from aegis_core.brain.routing import (
    DEEP_REASONING_MODEL_ID,
    FAST_PLANNING_MODEL_ID,
    ConfidenceMetrics,
    TokenConfidence,
    calibrate_token_confidence,
    contains_non_text_content,
    decide_cascade,
)
from aegis_core.contracts import AgentResult, AgentRole
from aegis_core.providers.nvidia import NvidiaNimRateLimited


def _result(content: str, *, model_id: str = "foundation") -> AgentResult:
    return AgentResult(
        role=AgentRole.ROUTER,
        model_id=model_id,
        content=content,
        finish_reason="stop",
    )


class _Local:
    def __init__(self, confidence: float, content: str = "respuesta local") -> None:
        self.response = LocalFoundationResponse(
            result=_result(content),
            confidence=ConfidenceMetrics(
                calibrated_probability=confidence,
                mean_selected_probability=confidence,
                normalized_entropy=1.0 - confidence,
                token_count=3,
            ),
            source="maclocal_api",
        )
        self.calls = 0

    async def complete_with_confidence(self, **_: Any) -> LocalFoundationResponse:
        self.calls += 1
        return self.response


class _Nvidia:
    def __init__(self, *, rate_limited: bool = False) -> None:
        self.rate_limited = rate_limited
        self.model_ids: tuple[str, ...] | None = None
        self.calls = 0

    async def complete(self, **kwargs: Any) -> AgentResult:
        self.calls += 1
        self.model_ids = kwargs.get("model_ids")
        if self.rate_limited:
            raise NvidiaNimRateLimited("rate limited")
        return _result("respuesta remota", model_id=self.model_ids[0])

    async def complete_stream(self, **kwargs: Any) -> AgentResult:
        result = await self.complete(**kwargs)
        callback = kwargs.get("on_delta")
        if callback is not None:
            callback(result.content)
        return result


class _Audit:
    def __init__(self) -> None:
        self.events: list[dict[str, Any]] = []

    def record_system_event(
        self,
        request_id: UUID,
        *,
        event_type: str,
        component: str,
        data: Mapping[str, str | int | bool | None],
        call_id: str | None = None,
    ) -> None:
        self.events.append(
            {
                "request_id": request_id,
                "event_type": event_type,
                "component": component,
                "data": dict(data),
                "call_id": call_id,
            }
        )


def test_token_confidence_penalizes_ambiguous_distributions() -> None:
    confident = calibrate_token_confidence(
        [TokenConfidence(-0.01, (-0.01, -5.0)) for _ in range(4)]
    )
    ambiguous = calibrate_token_confidence(
        [TokenConfidence(-0.55, (-0.55, -0.65, -0.75)) for _ in range(4)]
    )

    assert confident.calibrated_probability > 0.82
    assert ambiguous.calibrated_probability < 0.82
    assert confident.normalized_entropy < ambiguous.normalized_entropy


def test_router_uses_only_user_request_for_explicit_escalation() -> None:
    decision = decide_cascade(
        role=AgentRole.ROUTER,
        messages=[
            {"role": "system", "content": "You route code and cybersecurity requests."},
            {"role": "user", "content": "¿Qué hora es?"},
        ],
        confidence=ConfidenceMetrics(0.95, 0.95, 0.05, 3),
        threshold=0.82,
    )

    assert not decision.escalates


def test_text_only_openai_parts_are_not_misclassified_as_multimodal() -> None:
    assert not contains_non_text_content(
        [{"role": "user", "content": [{"type": "text", "text": "hola"}]}]
    )
    assert contains_non_text_content(
        [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "describe"},
                    {"type": "image_url", "image_url": {"url": "data:image/png;base64,AA=="}},
                ],
            }
        ]
    )


@pytest.mark.asyncio
async def test_maclocal_client_sends_fixed_foundation_request_and_parses_logprobs() -> None:
    requests: list[dict[str, Any]] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        requests.append(json.loads(request.content))
        return httpx.Response(
            200,
            json={
                "model": "foundation",
                "choices": [
                    {
                        "message": {"content": "  listo  "},
                        "finish_reason": "stop",
                        "logprobs": {
                            "content": [
                                {
                                    "token": "listo",
                                    "logprob": -0.01,
                                    "top_logprobs": [
                                        {"token": "listo", "logprob": -0.01},
                                        {"token": "hecho", "logprob": -5.0},
                                    ],
                                }
                            ]
                        },
                    }
                ],
                "usage": {"prompt_tokens": 2, "completion_tokens": 1},
            },
        )

    client = MacLocalFoundationClient(
        "http://127.0.0.1:9999/v1/chat/completions",
        transport=httpx.MockTransport(handler),
    )
    try:
        response = await client.complete_with_confidence(
            role=AgentRole.ROUTER,
            messages=[{"role": "user", "content": "estado"}],
        )
    finally:
        await client.aclose()

    assert requests[0]["model"] == "foundation"
    assert requests[0]["logprobs"] is True
    assert requests[0]["top_logprobs"] == 5
    assert response.result.content == "listo"
    assert response.confidence.calibrated_probability > 0.82


@pytest.mark.asyncio
async def test_high_confidence_stays_local_and_is_audited() -> None:
    local = _Local(0.96)
    nvidia = _Nvidia()
    audit = _Audit()
    client = HybridBrainClient(local, nvidia, audit_sink=audit)  # type: ignore[arg-type]

    result = await client.complete(
        role=AgentRole.ROUTER,
        messages=[{"role": "user", "content": "salúdame"}],
    )

    assert result.content == "respuesta local"
    assert local.calls == 1
    assert nvidia.calls == 0
    assert audit.events[0]["data"]["selected"] == "local"


@pytest.mark.asyncio
async def test_code_request_escalates_to_exact_deep_reasoning_model() -> None:
    local = _Local(0.99)
    nvidia = _Nvidia()
    client = HybridBrainClient(local, nvidia)  # type: ignore[arg-type]

    result = await client.complete(
        role=AgentRole.ROUTER,
        messages=[{"role": "user", "content": "Implementa este código Python"}],
    )

    assert result.content == "respuesta remota"
    assert nvidia.model_ids == (DEEP_REASONING_MODEL_ID,)


@pytest.mark.asyncio
async def test_low_confidence_uses_fast_planner_but_429_returns_local() -> None:
    local = _Local(0.40)
    nvidia = _Nvidia(rate_limited=True)
    audit = _Audit()
    client = HybridBrainClient(local, nvidia, audit_sink=audit)  # type: ignore[arg-type]

    result = await client.complete(
        role=AgentRole.ROUTER,
        messages=[{"role": "user", "content": "organiza la tarea"}],
    )

    assert nvidia.model_ids == (FAST_PLANNING_MODEL_ID,)
    assert result.content == "respuesta local"
    assert audit.events[0]["data"]["selected"] == "local_degraded"
