from __future__ import annotations

import json
from dataclasses import replace

import pytest

from aegis_core.skill_flow_benchmark import (
    CATALOGUE_CASES,
    EXPECTED_CASES,
    MAX_OUTGOING_PROMPT_BYTES,
    MODES,
    WORKLOADS,
    SkillFlowBenchmark,
    SkillFlowReport,
    _fixture_json_answer,
)


@pytest.mark.asyncio
async def test_complete_offline_skill_graph_flows_have_real_authorized_tool_evidence(
    tmp_path,
) -> None:
    report = await SkillFlowBenchmark(temporary_root=tmp_path).run()
    failures = [(item.case_id, item.failure_code) for item in report.results if not item.passed]
    assert failures == []
    assert report.gate_passed
    assert not SkillFlowReport((report.results[0],) * EXPECTED_CASES).gate_passed
    assert len(report.results) == EXPECTED_CASES
    assert {item.mode for item in report.results} == set(MODES)
    assert set(WORKLOADS).issubset({item.workload for item in report.results})
    assert all(item.maximum_prompt_bytes <= MAX_OUTGOING_PROMPT_BYTES for item in report.results)
    assert all(item.executions == 1 for item in report.results if item.workload == "evidence")
    assert next(item for item in report.results if item.workload == "code_review").executions == 1
    catalogue = [item for item in report.results if item.workload.startswith("catalogue_")]
    assert len(catalogue) == 2 * len(CATALOGUE_CASES)
    assert all(
        item.selection_verified and item.evidence_verified and item.synthesis_guidance_verified
        for item in catalogue
    )
    incomplete_baselines = {
        "baseline.catalogue_scoped_continuity",
        "baseline.catalogue_scoped_continuity_checkpoint",
    }
    assert {item.case_id for item in catalogue if not item.oracle_passed} == incomplete_baselines
    assert all(
        item.task_completed is False and item.expected_task_completion is False
        for item in catalogue
        if item.case_id in incomplete_baselines
    )
    assert all(
        item.executions == 0
        for item in catalogue
        if "wrong_account" in item.case_id or "unoffered_send" in item.case_id
    )
    assert all(
        item.executions == 0
        for item in report.results
        if item.workload
        in {
            "forged_tool",
            "plugin_revoked",
            "topic_change",
        }
    )
    # The report includes neither protocol payloads nor private skill content and
    # explicitly refuses to equate a synthetic flow gate with real model quality.
    payload = report.public_dict()
    assert payload["real_model_quality_measured"] is False
    assert payload["real_network_calls"] == 0
    comparison = payload["catalogue_comparison"]
    assert comparison["baseline"]["expected_incomplete_controls"] == 2
    assert comparison["builtin"]["expected_incomplete_controls"] == 0
    assert comparison["builtin"]["tasks_completed"] == comparison["builtin"]["task_cases"]
    assert comparison["baseline"]["tasks_completed"] == comparison["baseline"]["task_cases"] - 2
    serialized = json.dumps(payload)
    assert "ZEPHYR-SKILL-73" not in serialized
    assert "Datos de referencia" not in serialized
    assert "ATLAS" not in serialized
    assert len(tuple(tmp_path.iterdir())) == 0


def test_partial_flow_report_cannot_pass_the_coverage_gate() -> None:
    assert not SkillFlowReport(()).gate_passed


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "case_id,replacement",
    [
        ("tabular_revision_units", ("stock,23,2,current", "stock,24,2,current")),
        ("owner_correction_attribution", ('"budget_eur":730', '"budget_eur":731')),
        ("code_independent_boundaries", ("    return items[0]", "    pass\n    return items[0]")),
    ],
)
async def test_changed_evidence_fails_fixed_oracle_even_when_execution_succeeds(
    tmp_path, case_id, replacement
):
    case = next(item for item in CATALOGUE_CASES if item.case_id == case_id)
    changed = replace(case, fixture=case.fixture.replace(*replacement, 1))
    result = await SkillFlowBenchmark().run_catalogue_case(tmp_path / "flow", "builtin", changed)
    assert result.evidence_verified and result.selection_verified
    assert result.oracle_passed is False
    assert not result.passed and result.failure_code == "catalogue_oracle"


def test_fixed_oracles_differ_from_skill_guidance_and_reject_injection_and_wrong_source_lines():
    cases = {item.case_id: item for item in CATALOGUE_CASES}
    data = cases["tabular_revision_units"]
    assert _fixture_json_answer(data.fixture, data.filename) == data.expected
    correction = cases["owner_correction_attribution"]
    assert _fixture_json_answer(correction.fixture, correction.filename) == correction.expected
    assert correction.expected["current_eur"] not in {620, 910, 9999, 1200}
    assert (
        _fixture_json_answer(json.dumps(json.loads(correction.fixture)), correction.filename)[
            "citation"
        ]
        != correction.expected["citation"]
    )
