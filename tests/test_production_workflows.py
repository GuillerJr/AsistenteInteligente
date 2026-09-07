from __future__ import annotations

import json
import socket
from collections import Counter
from pathlib import Path

import pytest

from aegis_core import cli
from aegis_core.production_workflows import (
    EXPECTED_WORKFLOWS,
    WORKFLOWS_PER_CATEGORY,
    JarvisProductionWorkflowBenchmark,
    OfflineBoundaryError,
    WorkflowCategory,
    WorkflowContext,
    WorkflowDefinition,
    WorkflowProbe,
)


def test_manifest_has_four_unique_multi_checkpoint_flows_per_category(
    tmp_path: Path,
) -> None:
    benchmark = JarvisProductionWorkflowBenchmark(temporary_root=tmp_path)
    manifest = benchmark.manifest

    assert len(manifest) == EXPECTED_WORKFLOWS
    assert len({workflow.workflow_id for workflow in manifest}) == EXPECTED_WORKFLOWS
    assert Counter(workflow.category for workflow in manifest) == {
        category: WORKFLOWS_PER_CATEGORY for category in WorkflowCategory
    }
    assert all(len(workflow.checkpoints) >= 2 for workflow in manifest)
    assert len(benchmark.manifest_sha256) == 64


def test_documented_matrix_traces_every_executable_workflow(tmp_path: Path) -> None:
    benchmark = JarvisProductionWorkflowBenchmark(temporary_root=tmp_path)
    matrix = (
        Path(__file__).resolve().parents[1]
        / "docs/quality/P6_PRODUCTION_FLOW_MATRIX.md"
    ).read_text(encoding="utf-8")

    assert benchmark.manifest_sha256 in matrix
    assert all(f"`{workflow.workflow_id}`" in matrix for workflow in benchmark.manifest)
    assert "No afirmadas por P6" in matrix


@pytest.mark.asyncio
async def test_production_gate_proves_every_checkpoint_without_network_or_user_data(
    tmp_path: Path,
) -> None:
    report = await JarvisProductionWorkflowBenchmark(temporary_root=tmp_path).run()
    payload = json.loads(report.private_json())

    assert report.gate_passed
    assert report.score == 100
    assert report.passed == EXPECTED_WORKFLOWS
    assert payload["privacy"] == {
        "contains_prompt_text": False,
        "contains_target_urls": False,
        "contains_transcripts": False,
        "contains_absolute_paths": False,
        "network_attempts": 0,
    }
    assert all(
        result.checkpoints_passed == result.checkpoints_expected
        for result in report.results
    )
    serialized = report.private_json().casefold()
    assert "hola jarvis" not in serialized
    assert "owner@example.com" not in serialized
    assert "nvapi-" not in serialized
    assert str(tmp_path).casefold() not in serialized


@pytest.mark.asyncio
async def test_incomplete_runner_cannot_produce_a_false_positive(tmp_path: Path) -> None:
    benchmark = JarvisProductionWorkflowBenchmark(temporary_root=tmp_path)

    async def incomplete(_: WorkflowContext, probe: WorkflowProbe) -> None:
        probe.confirm("first", True)

    benchmark._workflows = (
        WorkflowDefinition(
            "fixture.incomplete",
            WorkflowCategory.SECURITY,
            ("first", "second"),
            incomplete,
        ),
    )
    report = await benchmark.run()

    assert not report.gate_passed
    assert report.results[0].failure_code == "expectation_failed"
    assert report.results[0].checkpoints_passed == 1
    assert report.results[0].checkpoints_expected == 2


@pytest.mark.asyncio
async def test_caught_network_attempt_still_fails_the_global_gate(tmp_path: Path) -> None:
    benchmark = JarvisProductionWorkflowBenchmark(temporary_root=tmp_path)

    async def attempts_network(_: WorkflowContext, probe: WorkflowProbe) -> None:
        active_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            try:
                active_socket.connect(("127.0.0.1", 9))
            except OfflineBoundaryError:
                pass
        finally:
            active_socket.close()
        probe.confirm("attempt_caught", True)

    benchmark._workflows = (
        WorkflowDefinition(
            "fixture.network",
            WorkflowCategory.SECURITY,
            ("attempt_caught",),
            attempts_network,
        ),
    )
    report = await benchmark.run()

    assert report.results[0].passed
    assert report.network_attempts == 1
    assert not report.gate_passed
    assert "127.0.0.1" not in report.private_json()


@pytest.mark.asyncio
async def test_internal_failure_is_bounded_and_does_not_leak_details(tmp_path: Path) -> None:
    benchmark = JarvisProductionWorkflowBenchmark(temporary_root=tmp_path)

    async def failure(_: WorkflowContext, __: WorkflowProbe) -> None:
        raise RuntimeError("private prompt at https://private.invalid")

    first = benchmark._workflows[0]
    benchmark._workflows = (
        WorkflowDefinition(first.workflow_id, first.category, first.checkpoints, failure),
        *benchmark._workflows[1:],
    )
    report = await benchmark.run()
    serialized = report.private_json()

    assert not report.gate_passed
    assert report.results[0].failure_code == "internal_error"
    assert "private prompt" not in serialized
    assert "private.invalid" not in serialized


@pytest.mark.asyncio
async def test_cli_emits_one_private_production_report(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(
        cli,
        "JarvisProductionWorkflowBenchmark",
        lambda: JarvisProductionWorkflowBenchmark(temporary_root=tmp_path),
    )

    status = await cli.production_workflows()
    payload = json.loads(capsys.readouterr().out)

    assert status == 0
    assert payload["gate_passed"] is True
    assert payload["score"] == 100
    assert payload["total"] == EXPECTED_WORKFLOWS
