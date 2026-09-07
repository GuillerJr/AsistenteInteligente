from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path

import pytest

from aegis_core.long_horizon_reliability import (
    JarvisLongHorizonReliabilityBenchmark,
)
from aegis_core.production_workflows import JarvisProductionWorkflowBenchmark


def _benchmark_factory(root: Path):
    return lambda: JarvisProductionWorkflowBenchmark(temporary_root=root)


@pytest.mark.asyncio
async def test_long_horizon_gate_repeats_all_workflows_without_voice_or_network(
    tmp_path: Path,
) -> None:
    report = await JarvisLongHorizonReliabilityBenchmark(
        cycles=5,
        benchmark_factory=_benchmark_factory(tmp_path),
    ).run()
    payload = json.loads(report.private_json())

    assert report.gate_passed
    assert report.score == 100
    assert report.passed_cycles == 5
    assert report.workflow_runs == 100
    assert report.checkpoints_passed == 290
    assert report.checkpoints_expected == 290
    assert payload["privacy"] == {
        "contains_audio": False,
        "contains_images": False,
        "contains_prompt_text": False,
        "contains_target_urls": False,
        "contains_transcripts": False,
        "network_attempts": 0,
        "requires_owner_voice": False,
    }
    serialized = report.private_json().casefold()
    assert str(tmp_path).casefold() not in serialized
    assert "owner@example.com" not in serialized


@pytest.mark.asyncio
async def test_long_horizon_gate_fails_when_maximum_rss_growth_crosses_budget(
    tmp_path: Path,
) -> None:
    rss_values: Iterator[int] = iter(
        (100_000, 100_000, 100_000, 100_000, 100_000, 200_000, 200_000)
    )
    report = await JarvisLongHorizonReliabilityBenchmark(
        cycles=5,
        maximum_rss_growth_bytes=50_000,
        benchmark_factory=_benchmark_factory(tmp_path),
        rss_probe=lambda: next(rss_values),
    ).run()

    assert not report.gate_passed
    assert not report.checks["resources_bounded"]
    assert report.rss_growth_bytes == 100_000


@pytest.mark.asyncio
async def test_long_horizon_gate_detects_async_task_leaks(tmp_path: Path) -> None:
    task_snapshots: Iterator[set[int]] = iter(({1, 2}, {1, 2, 3}))
    report = await JarvisLongHorizonReliabilityBenchmark(
        cycles=5,
        benchmark_factory=_benchmark_factory(tmp_path),
        task_probe=lambda: next(task_snapshots),
    ).run()

    assert not report.gate_passed
    assert report.leaked_tasks == 1
    assert not report.checks["resources_bounded"]


@pytest.mark.parametrize("cycles", [0, 4, 101])
def test_long_horizon_gate_rejects_unbounded_cycle_counts(cycles: int) -> None:
    with pytest.raises(ValueError, match="cycle count"):
        JarvisLongHorizonReliabilityBenchmark(cycles=cycles)
