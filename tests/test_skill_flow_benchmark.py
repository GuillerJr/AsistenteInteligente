from __future__ import annotations

import json

import pytest

from aegis_core.skill_flow_benchmark import (
    EXPECTED_CASES,
    MAX_OUTGOING_PROMPT_BYTES,
    MODES,
    WORKLOADS,
    SkillFlowBenchmark,
    SkillFlowReport,
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
    serialized = json.dumps(payload)
    assert "ZEPHYR-SKILL-73" not in serialized
    assert "Datos de referencia" not in serialized
    assert "ATLAS" not in serialized
    assert len(tuple(tmp_path.iterdir())) == 0


def test_partial_flow_report_cannot_pass_the_coverage_gate() -> None:
    assert not SkillFlowReport(()).gate_passed
