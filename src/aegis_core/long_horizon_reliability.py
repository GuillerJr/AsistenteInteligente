from __future__ import annotations

import asyncio
import gc
import json
import os
import time
from collections.abc import Callable
from dataclasses import dataclass

import psutil

from aegis_core.production_workflows import (
    EXPECTED_WORKFLOWS,
    JarvisProductionWorkflowBenchmark,
)

SCHEMA_VERSION = "1.0"
DEFAULT_CYCLES = 20
MINIMUM_CYCLES = 5
MAXIMUM_CYCLES = 100
MAXIMUM_P95_CYCLE_MS = 2_000.0
MAXIMUM_RSS_GROWTH_BYTES = 8 * 1_024 * 1_024


@dataclass(frozen=True, slots=True)
class ReliabilityCycleResult:
    cycle: int
    passed: bool
    workflow_runs: int
    checkpoints_passed: int
    checkpoints_expected: int
    latency_ms: int
    failure_code: str | None = None

    def private_dict(self) -> dict[str, int | bool | str | None]:
        return {
            "cycle": self.cycle,
            "passed": self.passed,
            "workflow_runs": self.workflow_runs,
            "checkpoints_passed": self.checkpoints_passed,
            "checkpoints_expected": self.checkpoints_expected,
            "latency_ms": self.latency_ms,
            "failure_code": self.failure_code,
        }


@dataclass(frozen=True, slots=True)
class LongHorizonReliabilityReport:
    cycles_requested: int
    cycles: tuple[ReliabilityCycleResult, ...]
    manifest_sha256: str
    duration_ms: int
    p95_cycle_ms: float
    maximum_cycle_ms: int
    maximum_p95_cycle_ms: float
    rss_growth_bytes: int
    maximum_rss_growth_bytes: int
    leaked_tasks: int
    network_attempts: int

    @property
    def workflow_runs(self) -> int:
        return sum(cycle.workflow_runs for cycle in self.cycles)

    @property
    def checkpoints_passed(self) -> int:
        return sum(cycle.checkpoints_passed for cycle in self.cycles)

    @property
    def checkpoints_expected(self) -> int:
        return sum(cycle.checkpoints_expected for cycle in self.cycles)

    @property
    def passed_cycles(self) -> int:
        return sum(cycle.passed for cycle in self.cycles)

    @property
    def checks(self) -> dict[str, bool]:
        expected_workflows = self.cycles_requested * EXPECTED_WORKFLOWS
        return {
            "cycles_complete": (
                len(self.cycles) == self.cycles_requested
                and self.passed_cycles == self.cycles_requested
                and self.workflow_runs == expected_workflows
            ),
            "checkpoints_complete": (
                self.checkpoints_expected > 0
                and self.checkpoints_passed == self.checkpoints_expected
            ),
            "network_isolated": self.network_attempts == 0,
            "latency_bounded": self.p95_cycle_ms <= self.maximum_p95_cycle_ms,
            "resources_bounded": (
                self.rss_growth_bytes <= self.maximum_rss_growth_bytes
                and self.leaked_tasks == 0
            ),
        }

    @property
    def score(self) -> int:
        passed = sum(self.checks.values())
        return round((passed / len(self.checks)) * 100)

    @property
    def gate_passed(self) -> bool:
        return all(self.checks.values())

    def private_dict(self) -> dict[str, object]:
        return {
            "schema_version": SCHEMA_VERSION,
            "profile": "local_long_horizon_reliability",
            "manifest_sha256": self.manifest_sha256,
            "score": self.score,
            "gate_passed": self.gate_passed,
            "checks": self.checks,
            "cycles_requested": self.cycles_requested,
            "cycles_passed": self.passed_cycles,
            "workflow_runs": self.workflow_runs,
            "checkpoints_passed": self.checkpoints_passed,
            "checkpoints_expected": self.checkpoints_expected,
            "duration_ms": self.duration_ms,
            "latency_ms": {
                "p95_cycle": round(self.p95_cycle_ms, 2),
                "maximum_cycle": self.maximum_cycle_ms,
                "budget_p95": round(self.maximum_p95_cycle_ms, 2),
            },
            "resources": {
                "rss_growth_bytes": self.rss_growth_bytes,
                "rss_growth_budget_bytes": self.maximum_rss_growth_bytes,
                "leaked_async_tasks": self.leaked_tasks,
            },
            "cycles": [cycle.private_dict() for cycle in self.cycles],
            "privacy": {
                "contains_prompt_text": False,
                "contains_target_urls": False,
                "contains_transcripts": False,
                "contains_audio": False,
                "contains_images": False,
                "requires_owner_voice": False,
                "network_attempts": self.network_attempts,
            },
        }

    def private_json(self) -> str:
        return json.dumps(
            self.private_dict(),
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        )


class JarvisLongHorizonReliabilityBenchmark:
    """Repeat P6 as a bounded soak to expose cumulative leaks and state drift."""

    def __init__(
        self,
        *,
        cycles: int = DEFAULT_CYCLES,
        maximum_p95_cycle_ms: float = MAXIMUM_P95_CYCLE_MS,
        maximum_rss_growth_bytes: int = MAXIMUM_RSS_GROWTH_BYTES,
        benchmark_factory: Callable[[], JarvisProductionWorkflowBenchmark] = (
            JarvisProductionWorkflowBenchmark
        ),
        rss_probe: Callable[[], int] | None = None,
        task_probe: Callable[[], set[int]] | None = None,
    ) -> None:
        if not MINIMUM_CYCLES <= cycles <= MAXIMUM_CYCLES:
            raise ValueError("reliability cycle count is out of range")
        if not 100.0 <= maximum_p95_cycle_ms <= 10_000.0:
            raise ValueError("reliability latency budget is out of range")
        if not 0 <= maximum_rss_growth_bytes <= 1_024 * 1_024 * 1_024:
            raise ValueError("reliability memory budget is out of range")
        self._cycles = cycles
        self._maximum_p95_cycle_ms = maximum_p95_cycle_ms
        self._maximum_rss_growth_bytes = maximum_rss_growth_bytes
        self._benchmark_factory = benchmark_factory
        process = psutil.Process(os.getpid())
        self._rss_probe = rss_probe or (lambda: process.memory_info().rss)
        self._task_probe = task_probe or self._pending_task_ids

    async def run(self) -> LongHorizonReliabilityReport:
        benchmark = self._benchmark_factory()
        expected_checkpoints = sum(
            len(workflow.checkpoints) for workflow in benchmark.manifest
        )
        started = time.monotonic_ns()
        warmup = await benchmark.run()
        if not warmup.gate_passed:
            raise RuntimeError("reliability warm-up failed")
        gc.collect()
        initial_rss = self._validated_rss()
        maximum_rss = initial_rss
        initial_tasks = self._task_probe()
        results: list[ReliabilityCycleResult] = []
        network_attempts = warmup.network_attempts

        for cycle_number in range(1, self._cycles + 1):
            cycle_started = time.monotonic_ns()
            failure_code: str | None = None
            workflow_runs = 0
            checkpoints_passed = 0
            checkpoints_required = expected_checkpoints
            try:
                report = await benchmark.run()
                network_attempts += report.network_attempts
                workflow_runs = report.total
                checkpoints_passed = sum(
                    result.checkpoints_passed for result in report.results
                )
                checkpoints_required = sum(
                    result.checkpoints_expected for result in report.results
                )
                if report.manifest_sha256 != benchmark.manifest_sha256:
                    failure_code = "manifest_changed"
                elif not report.gate_passed:
                    failure_code = "workflow_gate_failed"
                elif checkpoints_required != expected_checkpoints:
                    failure_code = "checkpoint_count_changed"
            except Exception:
                failure_code = "internal_error"
            latency_ms = max(
                0,
                (time.monotonic_ns() - cycle_started) // 1_000_000,
            )
            results.append(
                ReliabilityCycleResult(
                    cycle=cycle_number,
                    passed=failure_code is None,
                    workflow_runs=workflow_runs,
                    checkpoints_passed=checkpoints_passed,
                    checkpoints_expected=checkpoints_required,
                    latency_ms=min(latency_ms, 2_147_483_647),
                    failure_code=failure_code,
                )
            )
            if cycle_number % 5 == 0:
                gc.collect()
            maximum_rss = max(maximum_rss, self._validated_rss())
            await asyncio.sleep(0)
            if failure_code is not None:
                break

        gc.collect()
        await asyncio.sleep(0)
        maximum_rss = max(maximum_rss, self._validated_rss())
        leaked_tasks = len(self._task_probe() - initial_tasks)
        latencies = sorted(cycle.latency_ms for cycle in results)
        p95_index = max(0, (95 * len(latencies) + 99) // 100 - 1)
        duration_ms = max(0, (time.monotonic_ns() - started) // 1_000_000)
        return LongHorizonReliabilityReport(
            cycles_requested=self._cycles,
            cycles=tuple(results),
            manifest_sha256=benchmark.manifest_sha256,
            duration_ms=min(duration_ms, 2_147_483_647),
            p95_cycle_ms=float(latencies[p95_index]) if latencies else float("inf"),
            maximum_cycle_ms=latencies[-1] if latencies else 2_147_483_647,
            maximum_p95_cycle_ms=self._maximum_p95_cycle_ms,
            rss_growth_bytes=max(0, maximum_rss - initial_rss),
            maximum_rss_growth_bytes=self._maximum_rss_growth_bytes,
            leaked_tasks=leaked_tasks,
            network_attempts=network_attempts,
        )

    def _validated_rss(self) -> int:
        value = self._rss_probe()
        if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
            raise RuntimeError("reliability RSS probe is invalid")
        return value

    @staticmethod
    def _pending_task_ids() -> set[int]:
        current = asyncio.current_task()
        return {
            id(task)
            for task in asyncio.all_tasks()
            if task is not current and not task.done()
        }
