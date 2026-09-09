import asyncio
import json
import time
from collections.abc import Mapping
from typing import Any
from uuid import UUID, uuid4

import httpx
import pytest

from aegis_core.brain.errors import RemoteProviderUnavailableError
from aegis_core.brain.hybrid_client import (
    HybridBrainClient,
    LocalFoundationCascadeClient,
    LocalFoundationResponse,
    MacLocalFoundationClient,
)
from aegis_core.brain.routing import (
    DEEP_REASONING_MODEL_ID,
    FAST_PLANNING_MODEL_ID,
    ConfidenceMetrics,
    RoutingPolicySnapshot,
    TokenConfidence,
    calibrate_token_confidence,
    contains_non_text_content,
    contains_private_data,
    decide_cascade,
)
from aegis_core.brain.speculative_engine import SpeculativeEngine
from aegis_core.contracts import AgentResult, AgentRole
from aegis_core.providers.nvidia import NvidiaNimError, NvidiaNimRateLimited


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


class _TerminalNvidia(_Nvidia):
    async def complete(self, **kwargs: Any) -> AgentResult:
        self.calls += 1
        self.model_ids = kwargs.get("model_ids")
        raise NvidiaNimError(
            "NVIDIA NIM returned HTTP 410",
            status_code=410,
            terminal=True,
        )


class _ToolAugmentedLocal(_Local):
    def __init__(self) -> None:
        super().__init__(0.20)
        self.tool_calls = 0

    async def complete_tool_augmented(self, **_: Any) -> LocalFoundationResponse:
        self.tool_calls += 1
        return LocalFoundationResponse(
            result=_result("acción local aceptada", model_id="apple/system-language-model"),
            confidence=ConfidenceMetrics.unavailable(),
            source="apple/system-language-model",
        )


class _StreamingNativeLocal:
    model_id = "apple/system-language-model"

    def __init__(self) -> None:
        self.complete_calls = 0
        self.stream_calls = 0

    def is_available(self) -> bool:
        return True

    async def complete(self, **_: Any) -> AgentResult:
        self.complete_calls += 1
        return _result("Hola, Guillermo.", model_id=self.model_id)

    async def complete_stream(self, **kwargs: Any) -> AgentResult:
        self.stream_calls += 1
        callback = kwargs.get("on_delta")
        if callback is not None:
            callback("Hola, ")
            callback("Guillermo.")
        return _result("Hola, Guillermo.", model_id=self.model_id)


class _SlowNvidia(_Nvidia):
    async def complete(self, **kwargs: Any) -> AgentResult:
        await asyncio.sleep(0.2)
        return await super().complete(**kwargs)


class _NoFirstEventNvidia(_Nvidia):
    def __init__(self) -> None:
        super().__init__()
        self.cancelled = False

    async def complete_stream(self, **kwargs: Any) -> AgentResult:
        self.calls += 1
        self.model_ids = kwargs.get("model_ids")
        try:
            await asyncio.sleep(10)
        except asyncio.CancelledError:
            self.cancelled = True
            raise
        return _result("respuesta remota tardía", model_id=self.model_ids[0])


class _NoToolPlanNvidia(_Nvidia):
    def __init__(self) -> None:
        super().__init__()
        self.cancelled = False

    async def complete(self, **kwargs: Any) -> AgentResult:
        self.calls += 1
        self.model_ids = kwargs.get("model_ids")
        try:
            await asyncio.sleep(10)
        except asyncio.CancelledError:
            self.cancelled = True
            raise
        return _result("plan remoto tardío", model_id=self.model_ids[0])


class _ImmediateFirstEventNvidia(_Nvidia):
    async def complete_stream(self, **kwargs: Any) -> AgentResult:
        self.calls += 1
        self.model_ids = kwargs.get("model_ids")
        callback = kwargs.get("on_delta")
        if callback is not None:
            callback("respuesta ")
        await asyncio.sleep(0.02)
        if callback is not None:
            callback("remota")
        return _result("respuesta remota", model_id=self.model_ids[0])


class _FailAfterFirstEventNvidia(_Nvidia):
    async def complete_stream(self, **kwargs: Any) -> AgentResult:
        self.calls += 1
        self.model_ids = kwargs.get("model_ids")
        callback = kwargs.get("on_delta")
        if callback is not None:
            callback("fragmento remoto")
        await asyncio.sleep(0.02)
        raise NvidiaNimError("private provider payload", status_code=503)


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
async def test_native_local_simple_route_streams_before_completion() -> None:
    secondary = _StreamingNativeLocal()
    local = LocalFoundationCascadeClient(None, secondary)  # type: ignore[arg-type]
    nvidia = _Nvidia()
    audit = _Audit()
    client = HybridBrainClient(local, nvidia, audit_sink=audit)  # type: ignore[arg-type]
    chunks: list[str] = []

    result = await client.complete_stream(
        role=AgentRole.ROUTER,
        messages=[{"role": "user", "content": "salúdame"}],
        on_delta=chunks.append,
    )

    assert result.content == "Hola, Guillermo."
    assert chunks == ["Hola, ", "Guillermo."]
    assert secondary.stream_calls == 1
    assert secondary.complete_calls == 0
    assert nvidia.calls == 0
    assert audit.events[0]["data"]["selected"] == "local_stream"


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
async def test_deep_specialist_without_first_event_releases_local_answer() -> None:
    local = _Local(0.99)
    nvidia = _NoFirstEventNvidia()
    audit = _Audit()
    chunks: list[str] = []
    client = HybridBrainClient(
        local,
        nvidia,  # type: ignore[arg-type]
        audit_sink=audit,
        remote_first_event_timeout_seconds=0.25,
    )

    result = await client.complete_stream(
        role=AgentRole.CODE_SECURITY,
        messages=[{"role": "user", "content": "Implementa este código Python"}],
        on_delta=chunks.append,
    )

    assert result.content == "respuesta local"
    assert chunks == ["respuesta local"]
    assert nvidia.cancelled is True
    assert audit.events[-1]["data"]["selected"] == "local_latency_fallback"
    assert audit.events[-1]["data"]["remote_latency_ms"] >= 200
    assert audit.events[-1]["data"]["fallback_error_type"] == "_RemoteFirstEventTimeout"


@pytest.mark.asyncio
async def test_tool_planner_total_wait_is_bounded_when_local_answer_exists() -> None:
    local = _Local(0.99)
    nvidia = _NoToolPlanNvidia()
    audit = _Audit()
    chunks: list[str] = []
    client = HybridBrainClient(
        local,
        nvidia,  # type: ignore[arg-type]
        audit_sink=audit,
        remote_first_event_timeout_seconds=0.25,
    )

    result = await client.complete_stream(
        role=AgentRole.CODE_SECURITY,
        messages=[{"role": "user", "content": "Revisa este archivo Python"}],
        extra_body={
            "tools": [
                {
                    "type": "function",
                    "function": {
                        "name": "filesystem_read_text",
                        "description": "Read one authorized file",
                        "parameters": {"type": "object", "properties": {}},
                    },
                }
            ],
            "tool_choice": "auto",
        },
        on_delta=chunks.append,
    )

    assert result.content == "respuesta local"
    assert chunks == ["respuesta local"]
    assert nvidia.cancelled is True
    assert audit.events[-1]["data"]["selected"] == "local_latency_fallback"
    assert audit.events[-1]["data"]["remote_latency_ms"] >= 200


@pytest.mark.asyncio
async def test_remote_first_event_commits_one_coherent_stream() -> None:
    local = _Local(0.99)
    nvidia = _ImmediateFirstEventNvidia()
    chunks: list[str] = []
    client = HybridBrainClient(
        local,
        nvidia,  # type: ignore[arg-type]
        remote_first_event_timeout_seconds=0.25,
    )

    result = await client.complete_stream(
        role=AgentRole.CODE_SECURITY,
        messages=[{"role": "user", "content": "Implementa este código Python"}],
        on_delta=chunks.append,
    )

    assert result.content == "respuesta remota"
    assert chunks == ["respuesta ", "remota"]


@pytest.mark.asyncio
async def test_committed_remote_failure_never_mixes_in_local_output() -> None:
    local = _Local(0.99)
    nvidia = _FailAfterFirstEventNvidia()
    audit = _Audit()
    chunks: list[str] = []
    client = HybridBrainClient(
        local,
        nvidia,  # type: ignore[arg-type]
        audit_sink=audit,
        remote_first_event_timeout_seconds=0.25,
    )

    with pytest.raises(RemoteProviderUnavailableError):
        await client.complete_stream(
            role=AgentRole.CODE_SECURITY,
            messages=[{"role": "user", "content": "Implementa este código Python"}],
            on_delta=chunks.append,
        )

    assert chunks == ["fragmento remoto"]
    assert "respuesta local" not in chunks
    assert audit.events[-1]["data"]["selected"] == "nvidia_stream_failed"
    assert audit.events[-1]["data"]["fallback_error_type"] == "_CommittedRemoteStreamError"


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


@pytest.mark.asyncio
async def test_terminal_nvidia_failure_hot_swaps_to_native_tool_session() -> None:
    local = _ToolAugmentedLocal()
    nvidia = _TerminalNvidia()
    audit = _Audit()
    client = HybridBrainClient(local, nvidia, audit_sink=audit)  # type: ignore[arg-type]

    result = await client.complete(
        role=AgentRole.CODE_SECURITY,
        messages=[{"role": "user", "content": "Analiza este código complejo"}],
    )

    assert result.content == "acción local aceptada"
    assert result.model_id == "apple/system-language-model"
    assert local.tool_calls == 1
    assert audit.events[-1]["data"]["selected"] == "local_tool_augmented_fallback"


def test_privacy_classifier_detects_structured_pii_without_plain_number_false_positive() -> None:
    assert contains_private_data("Escríbeme a owner@example.com")
    assert contains_private_data("Mi tarjeta es 4111 1111 1111 1111")
    assert contains_private_data("Mi cuenta bancaria necesita una revisión")
    assert not contains_private_data("Resume las 12 tareas del proyecto")


@pytest.mark.asyncio
async def test_private_complex_request_is_forced_local() -> None:
    local = _Local(0.20)
    nvidia = _Nvidia()
    client = HybridBrainClient(local, nvidia)  # type: ignore[arg-type]

    result = await client.complete(
        role=AgentRole.CODE_SECURITY,
        messages=[
            {
                "role": "user",
                "content": "Analiza este código para owner@example.com y refactorízalo",
            }
        ],
    )

    assert result.content == "respuesta local"
    assert nvidia.calls == 0


@pytest.mark.asyncio
async def test_thermal_budget_offloads_long_non_private_prompt() -> None:
    local = _Local(0.99)
    nvidia = _Nvidia()
    client = HybridBrainClient(
        local,
        nvidia,  # type: ignore[arg-type]
        runtime_policy_provider=lambda: RoutingPolicySnapshot(
            thermal_throttled=True,
            cloud_token_threshold=50,
        ),
    )
    prompt = " ".join(f"detalle{index}" for index in range(70))

    result = await client.complete(
        role=AgentRole.ROUTER,
        messages=[{"role": "user", "content": prompt}],
    )

    assert result.content == "respuesta remota"
    assert nvidia.model_ids == (FAST_PLANNING_MODEL_ID,)


@pytest.mark.asyncio
async def test_speculative_engine_accepts_exact_prefix_and_records_metric() -> None:
    local = _result("respuesta local incompleta")
    nvidia = _Nvidia()
    metrics: list[Any] = []
    engine = SpeculativeEngine(
        nvidia,  # type: ignore[arg-type]
        network_deadline_seconds=0.4,
        metric_sink=metrics.append,
    )
    started = time.perf_counter_ns()

    speculative = await engine.verify_draft(
        request_id=uuid4(),
        role=AgentRole.ROUTER,
        messages=[{"role": "user", "content": "mejora la respuesta"}],
        local_result=local,
        request_started_ns=started,
        local_ready_ns=time.perf_counter_ns(),
        max_tokens=128,
        thermal_throttled=False,
    )

    assert speculative.result.content == "respuesta remota"
    assert speculative.used_local_fallback is False
    assert metrics[0].route == "verified_remote"
    assert metrics[0].active_first_partial_ms <= metrics[0].wall_time_ms


@pytest.mark.asyncio
async def test_speculative_engine_cancels_slow_network_and_returns_local() -> None:
    local = _result("respuesta local inmediata")
    metrics: list[Any] = []
    engine = SpeculativeEngine(
        _SlowNvidia(),  # type: ignore[arg-type]
        network_deadline_seconds=0.1,
        metric_sink=metrics.append,
    )
    started = time.perf_counter_ns()

    speculative = await engine.verify_draft(
        request_id=uuid4(),
        role=AgentRole.ROUTER,
        messages=[{"role": "user", "content": "responde"}],
        local_result=local,
        request_started_ns=started,
        local_ready_ns=time.perf_counter_ns(),
        max_tokens=64,
        thermal_throttled=False,
    )

    assert speculative.result == local
    assert speculative.route == "local_timeout"
    assert metrics[0].local_fallback is True
