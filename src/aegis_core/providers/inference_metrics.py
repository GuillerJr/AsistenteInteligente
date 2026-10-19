"""Per-inference numerical counters, isolated across concurrent async requests."""

from __future__ import annotations

from collections.abc import Mapping
from contextlib import contextmanager
from contextvars import ContextVar, Token
from dataclasses import dataclass, field
from time import perf_counter_ns


@dataclass
class InferenceMetrics:
    http_requests: int = 0
    usage_responses: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0

    stages: dict[str, dict[str, int]] = field(default_factory=dict)

    def public_dict(self) -> dict[str, int]:
        # HashChainAuditLog accepts scalar values only. A nested stage dictionary
        # would reject the event in the inference finalizer and mask the real result.
        return {"http_requests": self.http_requests, "usage_responses": self.usage_responses,
                "prompt_tokens": self.prompt_tokens, "completion_tokens": self.completion_tokens,
                **{f"{name}_{key}": value for name, values in self.stages.items()
                   for key, value in values.items()}}


_CURRENT: ContextVar[InferenceMetrics | None] = ContextVar("nvidia_inference_metrics", default=None)


def begin_metrics() -> tuple[InferenceMetrics, Token]:
    metrics = InferenceMetrics()
    return metrics, _CURRENT.set(metrics)


def end_metrics(token: Token) -> None:
    _CURRENT.reset(token)


def record_http_request() -> None:
    if (metrics := _CURRENT.get()) is not None:
        metrics.http_requests += 1


def record_usage(usage: Mapping[str, int]) -> None:
    if (metrics := _CURRENT.get()) is None:
        return
    if any(type(usage.get(key)) is int and usage[key] >= 0
           for key in ("prompt_tokens", "completion_tokens")):
        metrics.usage_responses += 1
    for key in ("prompt_tokens", "completion_tokens"):
        value = usage.get(key)
        if type(value) is int and value >= 0:
            setattr(metrics, key, getattr(metrics, key) + value)


@contextmanager
def measure_stage(name: str):
    """Numerical stage evidence includes failed/cancelled attempts, never prompts."""
    if name not in {"intent", "answer", "vision"}:
        raise ValueError("unknown inference stage")
    metrics = _CURRENT.get()
    started = perf_counter_ns()
    before = metrics.http_requests if metrics else 0
    try:
        yield
    finally:
        if metrics is not None:
            stage = metrics.stages.setdefault(name, {"calls": 0, "latency_ms": 0,
                                                       "http_requests": 0})
            stage["calls"] += 1
            stage["latency_ms"] += (perf_counter_ns() - started) // 1_000_000
            stage["http_requests"] += metrics.http_requests - before
