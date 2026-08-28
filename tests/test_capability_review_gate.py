from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from aegis_core.capability_learning import CapabilityLearningStore
from aegis_core.capability_review_gate import (
    CapabilityReviewDecision,
    CapabilityReviewError,
    CapabilityReviewStatus,
    CapabilityReviewSubmission,
    evaluate_capability_review,
    load_capability_review_submission,
)
from aegis_core.capability_reviews import build_capability_review_dossier
from aegis_core.contracts import ToolExecutionResult


def _research_result() -> ToolExecutionResult:
    return ToolExecutionResult(
        call_id="research-gate",
        tool_name="web_research",
        success=True,
        output=json.dumps(
            {
                "results": [
                    {
                        "url": "https://modelcontextprotocol.io/specification",
                        "title": "MCP specification",
                        "content": "Especificación pública del protocolo y sus contratos.",
                    }
                ]
            }
        ),
    )


def _pack_inputs() -> dict[str, str]:
    return {
        "public_https_endpoint": "https://api.example.com/mcp",
        "auth_mode": "none",
        "declared_capabilities": "web_read",
        "closed_tool_schemas": (
            '{"sync_photos":{"type":"object","properties":{},"required":[],'
            '"additionalProperties":false}}'
        ),
        "verification_probe": "Read the resulting remote record using its public identifier.",
    }


def _shortcut_inputs() -> dict[str, str]:
    empty_schema = '{"type":"object","properties":{},"required":[],"additionalProperties":false}'
    return {
        "exact_shortcut_name": "Organizar descargas",
        "closed_input_schema": empty_schema,
        "bounded_output_schema": empty_schema,
        "verification_probe": "List the destination folder and verify the expected file count.",
    }


def _submission(
    dossier_sha256: str,
    gap_id: str,
    gate: str,
    inputs: dict[str, str],
    *,
    security_reference: str | None = None,
) -> CapabilityReviewSubmission:
    return CapabilityReviewSubmission.model_validate(
        {
            "gap_id": gap_id,
            "dossier_sha256": dossier_sha256,
            "decision": CapabilityReviewDecision.ACCEPT_DESIGN,
            "review_gate": gate,
            "completed_inputs": inputs,
            "accepted_security_checks": (0, 1, 2, 3, 4),
            "passed_acceptance_tests": (0, 1, 2, 3, 4),
            "test_evidence_sha256": "a" * 64,
            "security_review_reference": security_reference,
            "rationale": "The bounded design and its tests were reviewed locally.",
            "acknowledges_no_execution": True,
        }
    )


def test_complete_owner_review_is_ready_only_for_manual_implementation(tmp_path: Path) -> None:
    record = CapabilityLearningStore(tmp_path / "capabilities").record_research(
        "Sincroniza fotos con mi servidor",
        _research_result(),
    )
    assert record is not None
    dossier = build_capability_review_dossier(record)
    submission = _submission(
        dossier.integrity_sha256,
        dossier.gap_id,
        dossier.review_gate,
        _pack_inputs(),
    )

    verdict = evaluate_capability_review(record, submission)

    assert verdict.status is CapabilityReviewStatus.READY_FOR_MANUAL_IMPLEMENTATION
    assert verdict.execution_allowed is False
    assert verdict.reviewed_input_keys == tuple(sorted(_pack_inputs()))


def test_shortcut_review_reuses_closed_runtime_schemas(tmp_path: Path) -> None:
    record = CapabilityLearningStore(tmp_path / "capabilities").record_research(
        "Organiza y respalda estas descargas",
        _research_result(),
    )
    assert record is not None
    dossier = build_capability_review_dossier(record)
    submission = _submission(
        dossier.integrity_sha256,
        dossier.gap_id,
        dossier.review_gate,
        _shortcut_inputs(),
    )

    assert (
        evaluate_capability_review(record, submission).status
        is CapabilityReviewStatus.READY_FOR_MANUAL_IMPLEMENTATION
    )

    unsafe_inputs = _shortcut_inputs()
    unsafe_inputs["exact_shortcut_name"] = "../Organizar"
    unsafe = _submission(
        dossier.integrity_sha256,
        dossier.gap_id,
        dossier.review_gate,
        unsafe_inputs,
    )
    with pytest.raises(CapabilityReviewError, match="shortcut name"):
        evaluate_capability_review(record, unsafe)


def test_critical_review_requires_a_security_reference(tmp_path: Path) -> None:
    record = CapabilityLearningStore(tmp_path / "capabilities").record_research(
        "Sube y publica este informe",
        _research_result(),
    )
    assert record is not None
    dossier = build_capability_review_dossier(record)
    missing = _submission(
        dossier.integrity_sha256,
        dossier.gap_id,
        dossier.review_gate,
        _pack_inputs(),
    )

    with pytest.raises(CapabilityReviewError, match="security review evidence"):
        evaluate_capability_review(record, missing)

    complete = _submission(
        dossier.integrity_sha256,
        dossier.gap_id,
        dossier.review_gate,
        _pack_inputs(),
        security_reference="Security review SR-2026-0042 completed.",
    )
    assert (
        evaluate_capability_review(record, complete).status
        is CapabilityReviewStatus.READY_FOR_MANUAL_IMPLEMENTATION
    )


def test_research_gate_cannot_accept_a_design(tmp_path: Path) -> None:
    record = CapabilityLearningStore(tmp_path / "capabilities").observe(
        "Sincroniza fotos con mi servidor"
    )
    assert record is not None
    dossier = build_capability_review_dossier(record)
    submission = _submission(
        dossier.integrity_sha256,
        dossier.gap_id,
        dossier.review_gate,
        _pack_inputs(),
    )

    with pytest.raises(CapabilityReviewError, match="requires research"):
        evaluate_capability_review(record, submission)


def test_stale_dossier_and_incomplete_tests_fail_closed(tmp_path: Path) -> None:
    store = CapabilityLearningStore(tmp_path / "capabilities")
    record = store.record_research("Sincroniza fotos con mi servidor", _research_result())
    assert record is not None
    dossier = build_capability_review_dossier(record)
    incomplete = _submission(
        dossier.integrity_sha256,
        dossier.gap_id,
        dossier.review_gate,
        _pack_inputs(),
    ).model_copy(update={"passed_acceptance_tests": (0, 1, 2, 3)})

    with pytest.raises(CapabilityReviewError, match="acceptance tests"):
        evaluate_capability_review(record, incomplete)

    no_evidence = _submission(
        dossier.integrity_sha256,
        dossier.gap_id,
        dossier.review_gate,
        _pack_inputs(),
    ).model_copy(update={"test_evidence_sha256": None})
    with pytest.raises(CapabilityReviewError, match="test evidence"):
        evaluate_capability_review(record, no_evidence)

    changed = store.observe("Sincroniza fotos con mi servidor")
    assert changed is not None
    current = _submission(
        dossier.integrity_sha256,
        dossier.gap_id,
        dossier.review_gate,
        _pack_inputs(),
    )
    with pytest.raises(CapabilityReviewError, match="current dossier"):
        evaluate_capability_review(changed, current)


def test_rejection_records_no_false_validation_claims(tmp_path: Path) -> None:
    record = CapabilityLearningStore(tmp_path / "capabilities").observe(
        "Configura una aplicación desconocida"
    )
    assert record is not None
    dossier = build_capability_review_dossier(record)
    submission = CapabilityReviewSubmission(
        gap_id=dossier.gap_id,
        dossier_sha256=dossier.integrity_sha256,
        decision=CapabilityReviewDecision.REJECT_DESIGN,
        review_gate=dossier.review_gate,
        rationale="The proposed design does not meet the owner's requirements.",
        acknowledges_no_execution=True,
    )

    verdict = evaluate_capability_review(record, submission)

    assert verdict.status is CapabilityReviewStatus.REJECTED
    assert verdict.reviewed_input_keys == ()
    assert verdict.execution_allowed is False


@pytest.mark.parametrize(
    ("key", "value", "message"),
    [
        ("auth_mode", "api_key", "auth mode"),
        ("declared_capabilities", "process_execution", "declaration"),
        ("closed_tool_schemas", '{"sync":{"type":"object"}}', "tool schemas"),
        ("public_https_endpoint", "http://localhost/mcp", "public HTTPS"),
        ("public_https_endpoint", "https://127.0.0.1/mcp", "IP literal"),
    ],
)
def test_special_inputs_reject_unsafe_values(
    key: str,
    value: str,
    message: str,
    tmp_path: Path,
) -> None:
    record = CapabilityLearningStore(tmp_path / key).record_research(
        "Sincroniza fotos con mi servidor",
        _research_result(),
    )
    assert record is not None
    dossier = build_capability_review_dossier(record)
    inputs = _pack_inputs()
    inputs[key] = value
    submission = _submission(
        dossier.integrity_sha256,
        dossier.gap_id,
        dossier.review_gate,
        inputs,
    )

    with pytest.raises(CapabilityReviewError, match=message):
        evaluate_capability_review(record, submission)


def test_submission_rejects_secrets_and_safe_loader_rejects_symlinks(tmp_path: Path) -> None:
    record = CapabilityLearningStore(tmp_path / "capabilities").record_research(
        "Sincroniza fotos con mi servidor",
        _research_result(),
    )
    assert record is not None
    dossier = build_capability_review_dossier(record)
    payload = _submission(
        dossier.integrity_sha256,
        dossier.gap_id,
        dossier.review_gate,
        _pack_inputs(),
    ).model_dump(mode="json")
    payload["completed_inputs"]["verification_probe"] = "Never expose nvapi-secret-example"

    with pytest.raises(ValidationError, match="unsafe"):
        CapabilityReviewSubmission.model_validate_json(json.dumps(payload))

    source = tmp_path / "review.json"
    source.write_text(
        json.dumps(
            _submission(
                dossier.integrity_sha256,
                dossier.gap_id,
                dossier.review_gate,
                _pack_inputs(),
            ).model_dump(mode="json")
        )
    )
    loaded = load_capability_review_submission(source)
    assert loaded.gap_id == dossier.gap_id

    link = tmp_path / "review-link.json"
    link.symlink_to(source)
    with pytest.raises(CapabilityReviewError, match="regular file"):
        load_capability_review_submission(link)
