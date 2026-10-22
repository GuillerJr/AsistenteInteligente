from __future__ import annotations

import json
import socket
from collections import Counter
from pathlib import Path

import pytest

from aegis_core import cli
from aegis_core import production_workflows as production
from aegis_core.contracts import AgentResult, AgentRole
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
from aegis_core.tools.background_automation import QuietActionVerification
from aegis_core.tools.native_receipts import NATIVE_RECEIPT_SCOPE
from aegis_core.tools.verification import result_goal_is_verified, result_is_verified


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
        Path(__file__).resolve().parents[1] / "docs/quality/P6_PRODUCTION_FLOW_MATRIX.md"
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
        result.checkpoints_passed == result.checkpoints_expected for result in report.results
    )
    serialized = report.private_json().casefold()
    assert "hola jarvis" not in serialized
    assert "owner@example.com" not in serialized
    assert "nvapi-" not in serialized
    assert str(tmp_path).casefold() not in serialized


@pytest.mark.asyncio
async def test_production_native_fixtures_do_not_turn_acknowledgements_into_verified_goals(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    original = JarvisProductionWorkflowBenchmark._execution
    receipts = []

    def recording(authorization, **kwargs):
        result = original(authorization, **kwargs)
        if result.success and authorization.tool_name in {"application_open", "browser_search"}:
            receipts.append(result)
        return result

    monkeypatch.setattr(JarvisProductionWorkflowBenchmark, "_execution", staticmethod(recording))
    report = await JarvisProductionWorkflowBenchmark(temporary_root=tmp_path).run()
    assert report.gate_passed and len(receipts) == 3
    assert all(result_is_verified(receipt) and not result_goal_is_verified(receipt)
               for receipt in receipts)
    assert all(receipt.metadata["verification_scope"] == NATIVE_RECEIPT_SCOPE
               and receipt.metadata["goal_verified"] is False for receipt in receipts)


@pytest.mark.asyncio
@pytest.mark.parametrize("tampering", ["empty_receipt", "different_target", "invented_goal"])
async def test_production_gate_rejects_invalid_or_overclaimed_native_receipts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, tampering: str,
) -> None:
    original = JarvisProductionWorkflowBenchmark._execution

    def corrupted(authorization, **kwargs):
        result = original(authorization, **kwargs)
        if not result.success or authorization.tool_name != "application_open":
            return result
        changes = {
            "empty_receipt": {"output": "{}"},
            "different_target": {"output": json.dumps({
                "opened": True, "bundle_identifier": "ai.aegis.other",
            })},
            "invented_goal": {"metadata": {**result.metadata, "goal_verified": True}},
        }
        return result.model_copy(update=changes[tampering])

    monkeypatch.setattr(JarvisProductionWorkflowBenchmark, "_execution", staticmethod(corrupted))
    report = await JarvisProductionWorkflowBenchmark(temporary_root=tmp_path).run()
    failed = {result.workflow_id: result for result in report.results if not result.passed}
    assert not report.gate_passed
    assert set(failed) == {"orchestration.three_step_contract", "orchestration.denied_step_pruned"}
    assert all(result.failure_code == "expectation_failed" for result in failed.values())


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


@pytest.mark.asyncio
async def test_modal_fixture_rejects_model_done_with_incidental_title(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def incidental_done(self, **kwargs):
        del self, kwargs
        return AgentResult(
            role=AgentRole.VISION,
            model_id="synthetic/incidental-title",
            content='{"action":"done","evidence":"Chrome"}',
        )

    monkeypatch.setattr(production._ProductionComputerProvider, "complete", incidental_done)
    report = await JarvisProductionWorkflowBenchmark(temporary_root=tmp_path).run()
    failed = next(
        result
        for result in report.results
        if result.workflow_id == "orchestration.modal_self_correction"
    )
    assert not report.gate_passed and not failed.passed
    assert failed.failure_code == "expectation_failed"
    assert failed.checkpoints_passed < failed.checkpoints_expected


@pytest.mark.asyncio
async def test_modal_fixture_rejects_ack_without_observed_selection(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original = production._ProductionComputerBridge.act

    def acknowledged_without_effect(self, action, bundle, visual, input_counter):
        if action.action != "click":
            return original(self, action, bundle, visual, input_counter)
        # Deliberately inconsistent native evidence: the final AX observation,
        # rather than the acknowledgement or a model statement, must reject it.
        self.actions.append(action)
        return QuietActionVerification(
            pathway="accessibility",
            before_sha256="a" * 64,
            after_sha256="b" * 64,
            state_changed=True,
            verified=True,
        )

    monkeypatch.setattr(production._ProductionComputerBridge, "act", acknowledged_without_effect)
    report = await JarvisProductionWorkflowBenchmark(temporary_root=tmp_path).run()
    failed = next(
        result
        for result in report.results
        if result.workflow_id == "orchestration.modal_self_correction"
    )
    assert not report.gate_passed and not failed.passed
    assert failed.failure_code == "expectation_failed"
    assert failed.checkpoints_passed == 2
    assert failed.checkpoints_expected == 3
