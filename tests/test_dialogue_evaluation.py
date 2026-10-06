"""Evaluation must preserve failures and avoid claiming unexecuted coverage."""

import importlib
import json
from functools import lru_cache
from itertools import combinations
from pathlib import Path

import httpx
import pytest

from aegis_core.brain.errors import RemoteRateLimitedError
from aegis_core.contracts import AgentResult, AgentRole
from aegis_core.dialogue_challenges import challenge_cases


def test_two_worker_oracle_checks_all_feasible_nonpreemptive_event_schedules():
    durations = (3, 2, 5, 4, 2, 3, 4, 1)
    predecessors = (0, 0, 1, 1, 2, (1 << 2) | (1 << 4), 1 << 3, (1 << 5) | (1 << 6))
    finished = (1 << len(durations)) - 1

    @lru_cache(None)
    def minimum_remaining(done, running):
        if done == finished:
            return 0
        available = [
            task for task in range(len(durations))
            if not done & (1 << task)
            and all(task != active for active, _ in running)
            and predecessors[task] & done == predecessors[task]
        ]
        best = float("inf")
        for count in range(min(2 - len(running), len(available)) + 1):
            for start in combinations(available, count):
                active = (*running, *((task, durations[task]) for task in start))
                if not active:
                    continue
                elapsed = min(left for _, left in active)
                next_done = done
                remaining = []
                for task, left in active:
                    if left == elapsed:
                        next_done |= 1 << task
                    else:
                        remaining.append((task, left - elapsed))
                best = min(best, elapsed + minimum_remaining(next_done, tuple(sorted(remaining))))
        return best

    expected = minimum_remaining(0, ())
    assert expected == 13
    case = next(case for case in challenge_cases() if case.name == "two_worker_schedule")
    assert not case.failures(str(expected))
    assert case.failures("12")  # The unrestricted critical path ignores the worker limit.


@pytest.mark.asyncio
async def test_rate_limit_stops_evaluation_without_claiming_success_or_full_coverage(monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1] / "script"))
    gate = importlib.import_module("dialogue_stability_gate")
    calls = []

    class Provider:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

    class Graph:
        async def ainvoke(self, state):
            calls.append(state)
            raise RemoteRateLimitedError("synthetic limit")

    monkeypatch.setattr(gate.MacOSKeychain, "get", lambda _: "synthetic-not-a-credential")
    monkeypatch.setattr(gate, "NvidiaNimClient", Provider)
    monkeypatch.setattr(gate, "build_swarm_graph", lambda *args, **kwargs: Graph())
    report = await gate.run(2, set(), "all", pause_seconds=0)
    assert len(calls) == 1
    assert report["blocked_by_rate_limit"] and not report["coverage_complete"]
    assert report["total"] == 1 and report["planned_samples"] >= 130
    assert not report["all_passed"] and report["passed"] == 0
    assert report["latency_p50_ms"] is None and report["latency_p95_ms"] is None
    assert report["samples"][0]["failures"] == ["request_failed:RemoteRateLimitedError"]


@pytest.mark.asyncio
async def test_rate_limit_wait_preserves_failure_and_advances_to_next_planned_sample(monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1] / "script"))
    gate = importlib.import_module("dialogue_stability_gate")
    waits, calls = [], []

    class Provider:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

    class Graph:
        async def ainvoke(self, state):
            calls.append(state)
            if len(calls) == 1:
                raise RemoteRateLimitedError("synthetic limit")
            return {"final_result": AgentResult(role=AgentRole.PLANNER, model_id="synthetic",
                content='{"A":232,"B":359}', finish_reason="stop")}

    async def sleep(delay):
        waits.append(delay)

    monkeypatch.setattr(gate.MacOSKeychain, "get", lambda _: "synthetic-not-a-credential")
    monkeypatch.setattr(gate, "NvidiaNimClient", Provider)
    monkeypatch.setattr(gate, "build_swarm_graph", lambda *args, **kwargs: Graph())
    monkeypatch.setattr(gate.asyncio, "sleep", sleep)
    report = await gate.run(2, {"ledger_join_0"}, "challenge", 0, 60)
    assert len(calls) == 2 and waits == [60]
    assert report["coverage_complete"] and not report["blocked_by_rate_limit"]
    assert report["passed"] == 1 and report["rate_limited_samples"] == 1
    assert not report["all_passed"]
    assert [s["repeat"] for s in report["samples"]] == [1, 2]


@pytest.mark.asyncio
async def test_rate_limit_diagnostics_exclude_provider_headers_and_body(monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1] / "script"))
    gate = importlib.import_module("dialogue_stability_gate")
    transport = gate.CountingTransport()
    await transport.inner.aclose()
    transport.inner = httpx.MockTransport(lambda _: httpx.Response(429, headers={
        "Retry-After": "12", "X-Private-Header": "synthetic-private-value",
    }, json={"message": "per minute limit; synthetic-private-value"}))
    response = await transport.handle_async_request(httpx.Request("POST", "https://test.invalid"))
    assert response.status_code == 429 and "synthetic-private-value" in response.text
    assert transport.rate_limit_events == [{"status": 429, "retry_after_seconds": 12.0,
                                            "provider_limit_hint": "minute"}]
    assert "synthetic-private-value" not in json.dumps(transport.rate_limit_events)
    await transport.aclose()
