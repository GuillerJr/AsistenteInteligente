from __future__ import annotations

import json
from pathlib import Path

import pytest

from aegis_core.capability_blueprints import (
    BlueprintReadiness,
    CapabilityIntegrationPath,
    build_capability_blueprint,
)
from aegis_core.capability_learning import CapabilityLearningStore
from aegis_core.contracts import RiskLevel, ToolExecutionResult


def _research_result() -> ToolExecutionResult:
    return ToolExecutionResult(
        call_id="research-1",
        tool_name="web_research",
        success=True,
        output=json.dumps(
            {
                "query": "documentación oficial",
                "results": [
                    {
                        "url": "https://developer.apple.com/documentation/appkit",
                        "title": "AppKit",
                        "content": "Documentación pública de APIs nativas para macOS.",
                    }
                ],
            }
        ),
    )


def test_observed_gap_produces_non_executable_research_blueprint(tmp_path: Path) -> None:
    record = CapabilityLearningStore(tmp_path / "capabilities").observe(
        "Abre Photoshop y prepara el lienzo"
    )
    assert record is not None

    blueprint = build_capability_blueprint(record)

    assert blueprint.readiness is BlueprintReadiness.NEEDS_RESEARCH
    assert blueprint.integration_path is CapabilityIntegrationPath.NATIVE_ADAPTER
    assert blueprint.risk is RiskLevel.HIGH
    assert blueprint.priority_score == 45
    assert blueprint.evidence == ()
    assert all("ejecut" not in domain for domain in blueprint.review_domains)


@pytest.mark.parametrize(
    ("goal", "path", "readiness", "risk"),
    [
        (
            "Sincroniza fotos con mi servidor",
            CapabilityIntegrationPath.CAPABILITY_PACK,
            BlueprintReadiness.READY_FOR_REVIEW,
            RiskLevel.HIGH,
        ),
        (
            "Organiza y respalda estas descargas",
            CapabilityIntegrationPath.SHORTCUT_WORKFLOW,
            BlueprintReadiness.READY_FOR_REVIEW,
            RiskLevel.HIGH,
        ),
        (
            "Sube y publica este informe",
            CapabilityIntegrationPath.CAPABILITY_PACK,
            BlueprintReadiness.SENSITIVE_REVIEW,
            RiskLevel.CRITICAL,
        ),
    ],
)
def test_researched_gap_produces_bounded_review_blueprint(
    goal: str,
    path: CapabilityIntegrationPath,
    readiness: BlueprintReadiness,
    risk: RiskLevel,
    tmp_path: Path,
) -> None:
    record = CapabilityLearningStore(tmp_path / goal.split()[0]).record_research(
        goal,
        _research_result(),
    )
    assert record is not None

    blueprint = build_capability_blueprint(record)

    assert blueprint.integration_path is path
    assert blueprint.readiness is readiness
    assert blueprint.risk is risk
    assert blueprint.priority_score == 70
    assert len(blueprint.evidence) == 1
    assert len(blueprint.safety_constraints) >= 5
    assert len(blueprint.acceptance_tests) == 5


def test_repeated_need_increases_priority_without_changing_authority(tmp_path: Path) -> None:
    store = CapabilityLearningStore(tmp_path / "capabilities")
    researched = store.record_research("Sincroniza fotos con mi servidor", _research_result())
    assert researched is not None
    repeated = store.observe("Sincroniza fotos con mi servidor")
    assert repeated is not None

    blueprint = build_capability_blueprint(repeated)

    assert blueprint.priority_score == 80
    assert blueprint.readiness is BlueprintReadiness.READY_FOR_REVIEW
    assert "El Tool Broker conserva toda la autoridad de ejecución." in (
        blueprint.safety_constraints
    )
