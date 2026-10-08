from __future__ import annotations

import json
from uuid import uuid4

import pytest

from aegis_core.contracts import ToolAuthorization, ToolExecutionResult
from aegis_core.job_interactions import confirmation_preview
from aegis_core.job_tool_presenter import JobToolPresenter
from aegis_core.tools.verification import result_is_verified


def access_authorization(mode=None):
    return ToolAuthorization(
        call_id="browser-access", tool_name="browser_task_open", call_digest="a" * 64,
        decision="allow", reason_code="confirmation_consumed",
        normalized_arguments={
            "url": "https://example.com/form", "objective": "Abre el formulario en mi navegador",
            "mode": mode,
        },
    )


def access_result(**changes):
    data = {
        "session_id": str(uuid4()), "expected_url": "https://example.com/form",
        "snapshot_sha256": "b" * 64, "verified": True, "goal_verified": False,
        "verification_scope": "browser_task_access",
    } | changes
    return ToolExecutionResult(
        call_id="browser-access", tool_name="browser_task_open", success=True,
        output=json.dumps({"structuredContent": data}),
        metadata={"verified": True, "goal_verified": False},
    )


@pytest.mark.parametrize("mode", [None, "extension", "isolated", "chrome"])
def test_access_preview_discloses_remote_processing_and_exact_destination(mode):
    authorization = access_authorization(mode)
    presenter = JobToolPresenter(None)
    assert presenter.supports_confirmation("browser_task_open")
    summary = presenter.confirmation_summary(authorization)
    assert "NVIDIA" in summary and "24 acciones" in summary and "10 min" in summary
    fields = confirmation_preview(authorization).fields
    assert fields[0].label == "Dirección"
    assert fields[0].value == authorization.normalized_arguments["url"]
    assert any(field.label == "Objetivo" for field in fields)
    if mode == "chrome":
        assert "Chrome directo (conexión CDP)" in summary
        assert any(field.label == "Tipo de navegador"
                   and field.value == "Chrome directo (conexión CDP)" for field in fields)


@pytest.mark.parametrize("mode", [None, "chrome"])
def test_access_verified_is_not_presented_as_completed_objective(mode):
    result = access_result()
    assert result_is_verified(result)
    rendered = JobToolPresenter.format_result(result, access_authorization(mode))
    assert "permiso temporal" in rendered
    assert "Completé" not in rendered
    if mode == "chrome":
        assert "Chrome directo mediante CDP" in rendered


@pytest.mark.parametrize("changes", [
    {"verified": 1}, {"goal_verified": True}, {"verification_scope": "objective_complete"},
    {"session_id": "invalid"}, {"expected_url": "file:///secret"},
    {"snapshot_sha256": "invalid"},
])
def test_access_completion_claim_requires_valid_bound_evidence(changes):
    result = access_result(**changes)
    assert not result_is_verified(result)
    with pytest.raises(ValueError, match="unverified"):
        JobToolPresenter.format_result(result, access_authorization())


@pytest.mark.parametrize("output", ["{}", "[]", "null", "invalid", '{"structuredContent": 1}'])
def test_malformed_access_evidence_cannot_certify_a_task(output):
    assert not result_is_verified(access_result().model_copy(update={"output": output}))


def test_partial_browser_action_does_not_claim_completion():
    result = ToolExecutionResult(
        call_id="browser-action", tool_name="browser_task_action", success=True,
        output=json.dumps({"structuredContent": {"verified": False}}),
        metadata={"verified": False, "goal_verified": False, "status": "partial"},
    )
    assert not result_is_verified(result)
    assert "sin verificar" in JobToolPresenter.format_result(result)


@pytest.mark.parametrize("goal_verified", [False, True, 0, None])
def test_disconnect_only_certifies_revoked_access(goal_verified):
    result = ToolExecutionResult(
        call_id="browser-close", tool_name="browser_task_close", success=True,
        output=json.dumps({"structuredContent": {
            "session_id": str(uuid4()), "verified": True, "closed": True,
            "goal_verified": goal_verified, "verification_scope": "browser_task_disconnect",
        }}),
        metadata={"verified": True, "goal_verified": False},
    )
    assert result_is_verified(result) is (goal_verified is False)


def test_chrome_preview_retains_entire_destination_and_objective_beyond_summary_cap():
    url = "https://example.com/" + "review-section/" * 130
    objective = "Abre el sitio con Chrome directo y comprueba el formulario. " * 120
    authorization = access_authorization("chrome").model_copy(update={
        "normalized_arguments": {"url": url, "objective": objective, "mode": "chrome"},
    })
    summary = JobToolPresenter(None).confirmation_summary(authorization)
    assert len(summary) == 512
    assert summary.index("NVIDIA") < summary.index("Chrome directo") < summary.index("Destino")
    assert "conexión CDP" in summary and "10 min" in summary and "24 acciones" in summary
    fields = {field.label: field.value for field in confirmation_preview(authorization).fields}
    assert fields["Dirección"] == url and fields["Objetivo"] == objective
    assert fields["Tipo de navegador"] == "Chrome directo (conexión CDP)"
    assert authorization.normalized_arguments["mode"] == "chrome"


@pytest.mark.parametrize("changes", [
    {"goal_verified": True}, {"verification_scope": "objective_complete"}, {"verified": False},
])
def test_chrome_connection_label_cannot_promote_unverified_or_goal_claimed_access(changes):
    with pytest.raises(ValueError, match="unverified"):
        JobToolPresenter.format_result(access_result(**changes), access_authorization("chrome"))


@pytest.mark.parametrize("mode", ["cdp", "auto", "owner", "remote"])
def test_browser_approval_rejects_unknown_connection_mode(mode):
    with pytest.raises(ValueError, match="arguments are invalid"):
        JobToolPresenter(None).confirmation_summary(access_authorization(mode))
