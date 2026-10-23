"""Partial computer actions explain their bounded stop reason without exposing content."""

import json

import pytest

from aegis_core.contracts import PolicyDecision, ToolAuthorization, ToolExecutionResult
from aegis_core.job_tool_presenter import JobToolPresenter


def partial(reason):
    authorization = ToolAuthorization(
        call_id="synthetic-partial", tool_name="computer_use", call_digest="a" * 64,
        decision=PolicyDecision.ALLOW, reason_code="confirmation_consumed",
        normalized_arguments={"application_bundle_identifier": "com.apple.TextEdit",
                              "max_steps": 3},
    )
    result = ToolExecutionResult(
        call_id=authorization.call_id, tool_name="computer_use", success=True,
        output=json.dumps({"status": "partial", "steps": 1,
                           "application_bundle_identifier": "com.apple.TextEdit",
                           "reason_code": reason}),
    )
    return result, authorization


@pytest.mark.parametrize(("reason", "explanation"), [
    ("user_takeover", "retomaste el teclado o el puntero"),
    ("uncertain_state", "no pudo verificar el estado visual"),
    ("postcondition_failed", "el resultado observado no coincide"),
    ("step_limit", "alcanzó el límite de pasos"),
])
def test_partial_result_reports_why_it_stopped_without_claiming_completion(reason, explanation):
    text = JobToolPresenter.format_result(*partial(reason))
    assert explanation in text
    assert "sigue sin verificar" in text and "evitar repetir cambios" in text
    assert "Completé" not in text


@pytest.mark.parametrize("reason", ["objective_complete", "private synthetic content"])
def test_partial_result_cannot_publish_an_inconsistent_or_arbitrary_stop_reason(reason):
    with pytest.raises(ValueError):
        JobToolPresenter.format_result(*partial(reason))
