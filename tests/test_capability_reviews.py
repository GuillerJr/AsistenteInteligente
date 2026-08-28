from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from aegis_core.capability_learning import CapabilityLearningStore
from aegis_core.capability_reviews import (
    CapabilityArtifactKind,
    CapabilityReviewDossier,
    CapabilityReviewGate,
    build_capability_review_dossier,
)
from aegis_core.contracts import ToolExecutionResult


def _research_result() -> ToolExecutionResult:
    return ToolExecutionResult(
        call_id="research-review",
        tool_name="web_research",
        success=True,
        output=json.dumps(
            {
                "results": [
                    {
                        "url": "https://developer.apple.com/documentation/appintents",
                        "title": "App Intents",
                        "content": "Documentación pública para integrar acciones de aplicaciones.",
                    }
                ]
            }
        ),
    )


@pytest.mark.parametrize(
    ("goal", "artifact", "gate", "required_key"),
    [
        (
            "Abre Photoshop y prepara el lienzo",
            CapabilityArtifactKind.CORE_TOOL_DESIGN,
            CapabilityReviewGate.OWNER_REVIEW,
            "native_framework",
        ),
        (
            "Organiza y respalda estas descargas",
            CapabilityArtifactKind.SHORTCUT_CONTRACT,
            CapabilityReviewGate.OWNER_REVIEW,
            "exact_shortcut_name",
        ),
        (
            "Sincroniza fotos con mi servidor",
            CapabilityArtifactKind.CAPABILITY_PACK_DRAFT,
            CapabilityReviewGate.OWNER_REVIEW,
            "public_https_endpoint",
        ),
        (
            "Sube y publica este informe",
            CapabilityArtifactKind.CAPABILITY_PACK_DRAFT,
            CapabilityReviewGate.SECURITY_REVIEW,
            "public_https_endpoint",
        ),
    ],
)
def test_researched_capability_produces_a_non_executable_review_dossier(
    goal: str,
    artifact: CapabilityArtifactKind,
    gate: CapabilityReviewGate,
    required_key: str,
    tmp_path: Path,
) -> None:
    record = CapabilityLearningStore(tmp_path / goal.split()[0]).record_research(
        goal,
        _research_result(),
    )
    assert record is not None

    dossier = build_capability_review_dossier(record)

    assert dossier.artifact_kind is artifact
    assert dossier.review_gate is gate
    assert dossier.execution_allowed is False
    assert required_key in {item.key for item in dossier.required_inputs}
    assert dossier.evidence[0].title == "App Intents"
    assert not hasattr(dossier.evidence[0], "excerpt")


def test_unresearched_capability_is_blocked_at_research_gate(tmp_path: Path) -> None:
    record = CapabilityLearningStore(tmp_path / "capabilities").observe(
        "Configura una aplicación desconocida"
    )
    assert record is not None

    dossier = build_capability_review_dossier(record)

    assert dossier.review_gate is CapabilityReviewGate.RESEARCH_REQUIRED
    assert dossier.evidence == ()
    assert dossier.execution_allowed is False


def test_unresearched_sensitive_capability_still_requires_research_first(tmp_path: Path) -> None:
    record = CapabilityLearningStore(tmp_path / "capabilities").observe(
        "Instala y publica esta aplicación"
    )
    assert record is not None

    dossier = build_capability_review_dossier(record)

    assert dossier.review_gate is CapabilityReviewGate.RESEARCH_REQUIRED
    assert dossier.risk.value == "critical"
    assert dossier.execution_allowed is False


def test_review_dossier_is_deterministic_and_detects_tampering(tmp_path: Path) -> None:
    record = CapabilityLearningStore(tmp_path / "capabilities").record_research(
        "Sincroniza fotos con mi servidor",
        _research_result(),
    )
    assert record is not None
    first = build_capability_review_dossier(record)
    second = build_capability_review_dossier(record)

    assert first.integrity_sha256 == second.integrity_sha256
    payload = first.model_dump(mode="json")
    payload["objective"] = "Objetivo alterado"

    with pytest.raises(ValidationError, match="integrity"):
        CapabilityReviewDossier.model_validate(payload)
