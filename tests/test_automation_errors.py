from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from aegis_core.contracts import PolicyDecision, ToolAuthorization, ToolExecutionResult
from aegis_core.orchestration.response_rendering import _deterministic_automation_error_response
from aegis_core.tools.automation_errors import bounded_jxa_program
from aegis_core.tools.defaults import default_policy_context
from aegis_core.tools.execution import _CALENDAR_CREATE_SCRIPT, ReadOnlyToolExecutor


def authorization() -> ToolAuthorization:
    return ToolAuthorization(
        call_id="synthetic-automation", tool_name="reminder_complete", call_digest="a" * 64,
        decision=PolicyDecision.ALLOW, reason_code="confirmation_consumed",
        normalized_arguments={"title": "Prueba", "list_name": None},
    )


@pytest.mark.parametrize(("stdout", "returncode", "expected"), [
    (b'{"completed":true,"title":"Prueba","list":"Pruebas"}', 0, None),
    (b'{"_aegis_automation_error":"access_denied"}', 0, "access_denied"),
    (b'{"_aegis_automation_error":"execution_timeout"}', 0, "execution_timeout"),
    (b'{"_aegis_automation_error":"automation_target_ambiguous"}', 0,
     "automation_target_ambiguous"),
    (b'{"_aegis_automation_error":"automation_target_unavailable"}', 0,
     "automation_target_unavailable"),
    (b'{"_aegis_automation_error":"private error"}', 0, "automation_invalid_response"),
    (b'{"_aegis_automation_error":[]}', 0, "automation_invalid_response"),
    (b'{"_aegis_automation_error":"access_denied","private":"text"}', 0,
     "automation_invalid_response"),
    (b'"private text"', 0, "automation_invalid_response"),
    (b'\xff', 0, "automation_invalid_response"),
    (b'private malformed JSON', 0, "automation_invalid_response"),
    (b'x' * 65_537, 0, "automation_output_limit"),
    (b'', 1, "automation_failed"),
    (b'{"completed":true}', 1, "automation_failed"),
])
def test_native_errors_are_bounded_and_never_report_success(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    stdout: bytes, returncode: int, expected: str | None,
) -> None:
    def run(command, **kwargs):
        assert kwargs["stderr"] == subprocess.DEVNULL
        assert kwargs["timeout"] == 12
        assert b"try {" in kwargs["input"]
        return subprocess.CompletedProcess(command, returncode, stdout, b"private native error")

    monkeypatch.setattr("aegis_core.tools.execution.subprocess.run", run)
    result = ReadOnlyToolExecutor().execute(authorization(), default_policy_context(tmp_path))
    assert result.success is (expected is None)
    assert result.error_code == expected
    assert "private" not in result.model_dump_json()
    if expected is not None:
        assert result.output == ""


@pytest.mark.parametrize(("failure", "expected"), [
    ("{number:-1743, message:'private'}", "access_denied"),
    ("{errorNumber:-1743, message:'private'}", "access_denied"),
    ("{number:-1712, message:'private'}", "execution_timeout"),
    ("{number:-1728, message:'private'}", "automation_target_unavailable"),
    ("new Error('calendar_unavailable')", "automation_target_unavailable"),
    ("new Error('reminder_list_unavailable')", "automation_target_unavailable"),
    ("new Error('reminder_match_not_unique')", "automation_target_ambiguous"),
    ("new Error('media_application_not_unique')", "automation_target_ambiguous"),
    ("new Error('calendar candidate limit exceeded')", "automation_output_limit"),
    ("new Error('reminder candidate limit exceeded')", "automation_output_limit"),
    ("new Error('contact candidate limit exceeded')", "automation_output_limit"),
    ("new Error('private -1743 misleading text')", "automation_failed"),
    ("{number:'-1743', message:'private'}", "automation_failed"),
    ("new Error('private permission denied')", "automation_failed"),
    ("null", "automation_failed"),
])
def test_program_classifies_only_numeric_codes_and_owned_errors(
    failure: str, expected: str,
) -> None:
    # Run only synthetic JS: no Application, Apple Events, UI, files or user data.
    node = shutil.which("node")
    if node is None:
        pytest.skip("Optional Node runtime for the isolated JavaScript envelope")
    program = bounded_jxa_program("throw " + failure + ";")
    run = subprocess.run(
        [node, "-e", "process.stdout.write(require('vm').runInNewContext("
         "require('fs').readFileSync(0, 'utf8'), {}, {timeout:1000}))"],
        input=program, text=True, capture_output=True, check=True, timeout=5,
    )
    assert json.loads(run.stdout) == {"_aegis_automation_error": expected}
    assert "private" not in run.stdout


def test_program_preserves_success_completion_value() -> None:
    node = shutil.which("node")
    if node is None:
        pytest.skip("Optional Node runtime for the isolated JavaScript envelope")
    run = subprocess.run(
        [node, "-e", "process.stdout.write(require('vm').runInNewContext("
         "require('fs').readFileSync(0, 'utf8'), {}, {timeout:1000}))"],
        input=bounded_jxa_program('JSON.stringify({completed:true});'),
        text=True, capture_output=True, check=True, timeout=5,
    )
    assert json.loads(run.stdout) == {"completed": True}


def test_action_error_response_does_not_invent_permission_denial_or_retry() -> None:
    auth = authorization()
    result = ToolExecutionResult(
        call_id=auth.call_id, tool_name=auth.tool_name, success=False,
        error_code="automation_target_ambiguous",
    )
    response = _deterministic_automation_error_response((auth,), (result,))
    assert response == (
        "No pude comprobar el resultado de la acción en la app. "
        "Revisa si se aplicó algún cambio antes de repetirla."
    )
    assert _deterministic_automation_error_response(
        (auth,), (result.model_copy(update={"call_id": "other-call"}),)
    ) is None
    assert _deterministic_automation_error_response((auth,), (result, result)) is None
    assert _deterministic_automation_error_response(
        (auth,), (result.model_copy(update={"success": True}),)
    ) is None


def test_calendar_readback_failure_does_not_claim_no_action_was_performed() -> None:
    node = shutil.which("node")
    if node is None:
        pytest.skip("Optional Node runtime for the isolated JavaScript envelope")
    source = "const payload = " + json.dumps({
        "calendar_name": None, "title": "Prueba", "location": None, "notes": None,
        "start_at": "2026-10-22T10:00:00Z", "end_at": "2026-10-22T11:00:00Z",
    }) + ";\n" + _CALENDAR_CREATE_SCRIPT
    run = subprocess.run(
        [node, "-e", """
const vm = require('vm');
let mutations = 0;
const calendar = {
    writable: () => true,
    events: {push: () => {mutations += 1;}},
    name: () => {throw {number:-1728, message:'private calendar detail'};}
};
const result = vm.runInNewContext(require('fs').readFileSync(0, 'utf8'), {
    Application: () => ({calendars: () => [calendar], Event: p => p})
}, {timeout:1000});
process.stdout.write(JSON.stringify({mutations, result: JSON.parse(result)}));
"""], input=bounded_jxa_program(source), text=True, capture_output=True,
        check=True, timeout=5,
    )
    data = json.loads(run.stdout)
    assert data == {
        "mutations": 1,
        "result": {"_aegis_automation_error": "automation_target_unavailable"},
    }
    auth = authorization().model_copy(update={"tool_name": "calendar_create_event"})
    result = ToolExecutionResult(call_id=auth.call_id, tool_name=auth.tool_name, success=False,
                                 error_code=data["result"]["_aegis_automation_error"])
    message = _deterministic_automation_error_response((auth,), (result,))
    assert message is not None and "Revisa si se aplicó algún cambio" in message
    assert "No encontré" not in message
    assert "private" not in run.stdout
