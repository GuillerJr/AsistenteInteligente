from __future__ import annotations

import json
import subprocess
from pathlib import Path
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from aegis_core.brain.agent_graph import PlanExecuteReflectRunner, PlanStepStatus
from aegis_core.brain.nodes.reflector import VisualReflectionNode
from aegis_core.contracts import (
    AgentResult,
    AgentRole,
    PolicyDecision,
    ToolAuthorization,
    ToolCall,
    ToolExecutionResult,
    UserRequest,
)
from aegis_core.job_completion import ApprovedToolContext, JobCompletionCoordinator
from aegis_core.job_execution import JobExecutionPipeline
from aegis_core.job_graph import JobGraphInvoker
from aegis_core.job_tool_presenter import JobToolPresenter
from aegis_core.job_tool_runtime import ApprovedToolOutcome
from aegis_core.orchestration.graph import build_nvidia_swarm_graph
from aegis_core.orchestration.response_rendering import _deterministic_native_control_response
from aegis_core.tools.defaults import default_policy_context
from aegis_core.tools.execution import ReadOnlyToolExecutor
from aegis_core.tools.native_receipts import (
    NATIVE_RECEIPT_SCOPE,
    NATIVE_RECEIPT_SOURCES,
    native_receipt_is_valid,
)
from aegis_core.tools.verification import result_goal_is_verified, result_is_verified

# All identities, messages and app data in this file are synthetic. The subprocess
# and JXA boundaries are replaced; no app, Apple Event or real user record is read.
CASES = {
    "mail_send_message": (
        {"recipients": ["synthetic@example.com"], "subject": "Prueba", "body": "Texto"},
        {"sent": True, "recipient_count": 1},
    ),
    "calendar_create_event": (
        {"title": "Prueba", "calendar_name": "Pruebas", "start_at": "2026-10-22T10:00:00Z",
         "end_at": "2026-10-22T11:00:00Z"},
        {"created": True, "title": "Prueba", "calendar": "Pruebas"},
    ),
    "reminder_create": (
        {"title": "Prueba", "list_name": "Pruebas"},
        {"created": True, "title": "Prueba", "list": "Pruebas"},
    ),
    "reminder_complete": (
        {"title": "Prueba", "list_name": "Pruebas"},
        {"completed": True, "title": "Prueba", "list": "Pruebas"},
    ),
    "contact_create": (
        {"first_name": "Ada", "last_name": "Prueba", "email": "synthetic@example.com"},
        {"created": True, "name": "Prueba Ada"},
    ),
    "application_open": (
        {"bundle_identifier": "com.apple.TextEdit"},
        {"opened": True, "bundle_identifier": "com.apple.TextEdit"},
    ),
    "browser_open_url": (
        {"url": "https://example.com/synthetic"},
        {"opened": True, "url": "https://example.com/synthetic"},
    ),
    "browser_search": (
        {"query": "prueba sintética", "browser": "safari"},
        {"opened": True, "query": "prueba sintética", "browser": "safari"},
    ),
    "shortcut_run": (
        {"name": "Prueba sintética"}, {"completed": True, "name": "Prueba sintética"},
    ),
    "media_control": (
        {"action": "next"}, {"action": "next", "bundle_identifier": "com.apple.Music"},
    ),
}
JXA_TOOLS = tuple(tool for tool in CASES if NATIVE_RECEIPT_SOURCES[tool]
                  not in {"macos_open", "macos_shortcuts"})


def authorization(tool: str) -> ToolAuthorization:
    return ToolAuthorization(
        call_id="synthetic-native", tool_name=tool, call_digest="a" * 64,
        decision=PolicyDecision.ALLOW, reason_code="confirmation_consumed",
        normalized_arguments=CASES[tool][0],
    )


def accepted(tool: str) -> ToolExecutionResult:
    return ReadOnlyToolExecutor._json_result(
        authorization(tool), CASES[tool][1], NATIVE_RECEIPT_SOURCES[tool]
    )


@pytest.mark.parametrize("tool", CASES)
def test_valid_receipts_verify_only_the_native_acknowledgement(tool: str) -> None:
    result = accepted(tool)
    assert result.success and result_is_verified(result)
    assert result.metadata["verification_scope"] == NATIVE_RECEIPT_SCOPE
    assert result.metadata["goal_verified"] is False
    assert not result_goal_is_verified(result)
    summary = JobToolPresenter.format_result(result, authorization(tool))
    outcome = ApprovedToolOutcome(result, summary)
    assert outcome.verified and not outcome.goal_verified
    assert "No he comprobado" in outcome.rendered_result


@pytest.mark.parametrize("tool", CASES)
@pytest.mark.parametrize("payload", [{}, [], None, False, "false", {"success": True}])
def test_empty_or_unrelated_output_cannot_be_promoted_to_verified(tool: str, payload) -> None:
    result = accepted(tool).model_copy(update={"output": json.dumps(payload)})
    assert not result_is_verified(result)
    with pytest.raises(ValueError, match="acknowledgement"):
        JobToolPresenter.format_result(result, authorization(tool))
    executed = ReadOnlyToolExecutor._json_result(
        authorization(tool), payload, NATIVE_RECEIPT_SOURCES[tool]
    )
    assert not executed.success and executed.error_code == "automation_invalid_response"
    assert executed.output == "" and executed.metadata["verified"] is False


@pytest.mark.parametrize("tool", CASES)
def test_receipt_requires_all_fields_exactly_once_and_no_extra_private_data(tool: str) -> None:
    _, payload = CASES[tool]
    for key in payload:
        missing = {field: value for field, value in payload.items() if field != key}
        assert not native_receipt_is_valid(tool, missing)
    assert not native_receipt_is_valid(tool, {**payload, "private": "not allowed"})
    encoded = json.dumps(payload)
    first_key = next(iter(payload))
    duplicate = '{' + json.dumps(first_key) + ':false,' + encoded[1:]
    assert not result_is_verified(accepted(tool).model_copy(update={"output": duplicate}))


@pytest.mark.parametrize("false_value", [False, "false", "true", 1, 0, None, [], {}])
def test_success_flags_and_counts_do_not_coerce_truthy_values(false_value) -> None:
    for tool, (_, payload) in CASES.items():
        for key, value in payload.items():
            if value is True:
                assert not native_receipt_is_valid(tool, {**payload, key: false_value})
    assert not native_receipt_is_valid("mail_send_message", {"sent": True, "recipient_count": True})


@pytest.mark.parametrize("metadata_change", [
    {"verified": "true"}, {"verified": False}, {"goal_verified": True},
    {"goal_verified": "false"}, {"verification_scope": "external_delivery"},
    {"source": "untrusted_adapter"}, {"status": "partial"},
])
def test_forged_receipt_metadata_never_proves_an_outcome(metadata_change) -> None:
    result = accepted("mail_send_message")
    forged = result.model_copy(update={"metadata": {**result.metadata, **metadata_change}})
    assert not result_is_verified(forged) and not result_goal_is_verified(forged)
    for field in ("verified", "goal_verified", "verification_scope", "source"):
        stripped = {key: value for key, value in result.metadata.items() if key != field}
        assert not result_is_verified(result.model_copy(update={"metadata": stripped}))


@pytest.mark.parametrize(("tool", "key", "wrong"), [
    ("mail_send_message", "recipient_count", 2),
    ("calendar_create_event", "title", "Otro"),
    ("calendar_create_event", "calendar", "Otro"),
    ("reminder_create", "list", "Otra"),
    ("reminder_complete", "title", "Otro"),
    ("application_open", "bundle_identifier", "com.apple.Safari"),
    ("browser_open_url", "url", "https://example.com/other"),
    ("browser_search", "browser", "chrome"),
    ("browser_search", "query", "otra búsqueda"),
    ("shortcut_run", "name", "Otro"),
    ("media_control", "action", "previous"),
])
def test_a_different_valid_receipt_cannot_satisfy_authorized_arguments(tool, key, wrong) -> None:
    payload = {**CASES[tool][1], key: wrong}
    assert native_receipt_is_valid(tool, payload)
    assert not native_receipt_is_valid(tool, payload, CASES[tool][0])
    result = accepted(tool).model_copy(update={"output": json.dumps(payload)})
    with pytest.raises(ValueError, match="authorization"):
        JobToolPresenter.format_result(result, authorization(tool))


async def test_mismatched_native_receipt_cannot_advance_a_plan() -> None:
    result = accepted("mail_send_message").model_copy(update={
        "output": '{"sent":true,"recipient_count":2}',
    })
    execute = AsyncMock(return_value=result)
    plan = await PlanExecuteReflectRunner(execute).run(
        goal="Acción sintética", authorizations=(authorization("mail_send_message"),),
    )
    assert plan.halted and plan.contract.steps[0].status is PlanStepStatus.FAILED
    assert plan.results[0].error_code == "tool_result_mismatch"
    execute.assert_awaited_once()


@pytest.mark.parametrize(("tool", "key", "wrong"), [
    ("calendar_create_event", "title", "  "),
    ("reminder_complete", "list", "\nprivate"),
    ("contact_create", "name", "x" * 301),
    ("browser_search", "browser", []),
    ("browser_search", "query", "x" * 301),
    ("browser_open_url", "url", "file:///private/synthetic"),
    ("browser_open_url", "url", "https://user:password@example.com"),
    ("application_open", "bundle_identifier", "x / y"),
    ("media_control", "action", "send"),
    ("media_control", "bundle_identifier", "com.apple.Terminal"),
])
def test_malformed_labels_and_targets_do_not_become_receipts(tool, key, wrong) -> None:
    assert not native_receipt_is_valid(tool, {**CASES[tool][1], key: wrong})


@pytest.mark.parametrize("tool", CASES)
def test_real_executor_returns_scoped_receipts_without_observing_apps(
    tool: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = []

    def run(command, **kwargs):
        calls.append(command)
        output = json.dumps(CASES[tool][1]).encode() if tool in JXA_TOOLS else b"private stdout"
        return subprocess.CompletedProcess(command, 0, stdout=output)

    monkeypatch.setattr("aegis_core.tools.execution.subprocess.run", run)
    monkeypatch.setattr("aegis_core.tools.execution.validate_public_https_url", lambda value: value)
    result = ReadOnlyToolExecutor().execute(authorization(tool), default_policy_context(tmp_path))
    assert result.success and result_is_verified(result) and not result_goal_is_verified(result)
    assert json.loads(result.output) == CASES[tool][1]
    assert "private stdout" not in result.model_dump_json()
    assert len(calls) == 1


@pytest.mark.parametrize("tool", JXA_TOOLS)
@pytest.mark.parametrize("output", [
    b"{}", b'{"completed":"false"}', b'[]',
    b'{"sent":false,"sent":true,"recipient_count":1}',
    b'{"sent":true,"recipient_count":NaN}',
])
async def test_uncertain_post_mutation_output_halts_without_repeating(
    tool: str, output: bytes, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    effects = []

    def run(command, **kwargs):
        effects.append(command)  # Model an effect that happened before the invalid reply.
        return subprocess.CompletedProcess(command, 0, stdout=output)

    monkeypatch.setattr("aegis_core.tools.execution.subprocess.run", run)
    executor = ReadOnlyToolExecutor()

    async def execute(auth):
        return executor.execute(auth, default_policy_context(tmp_path))

    correct = AsyncMock(return_value=True)
    outcome = await PlanExecuteReflectRunner(execute, ui_corrector=correct).run(
        goal="Acción sintética", authorizations=(authorization(tool),), historical_context="",
    )
    assert outcome.halted and outcome.contract.steps[0].status is PlanStepStatus.FAILED
    assert outcome.results[0].error_code == "automation_invalid_response"
    assert len(effects) == 1
    correct.assert_not_awaited()


async def test_acknowledgement_completes_once_without_claiming_an_observed_external_goal(
    tmp_path: Path,
) -> None:
    tool = "mail_send_message"
    auth, result = authorization(tool), accepted(tool)
    reflection = await VisualReflectionNode().evaluate(auth, result)
    assert reflection.reason == "command_acknowledged"
    execute = AsyncMock(return_value=result)
    plan = await PlanExecuteReflectRunner(execute).run(
        goal="Enviar solicitud sintética", authorizations=(auth,), historical_context="",
    )
    assert not plan.halted and plan.contract.steps[0].status is PlanStepStatus.SUCCEEDED
    execute.assert_awaited_once()

    persistence = AsyncMock()
    persistence.persist_exchange.return_value = True
    coordinator = JobCompletionCoordinator(
        persistence, owner_profile=None, social_memory=None, capability_learning=None,
    )
    outcome = ApprovedToolOutcome(result, JobToolPresenter.format_result(result, auth))
    completion = await coordinator.from_approved_tool(
        ApprovedToolContext(uuid4(), "Solicitud sintética", uuid4()), outcome,
    )
    assert completion.succeeded and not completion.action_verified
    assert "No he comprobado la entrega" in completion.result
    persistence.persist_exchange.assert_awaited_once()

    graph = AsyncMock()
    graph.ainvoke.return_value = {
        "specialist_result": AgentResult(
            role=AgentRole.PLANNER, model_id="synthetic", content="",
            tool_calls=(ToolCall(call_id=auth.call_id, tool_name=tool,
                                 arguments=CASES[tool][0], requested_by=AgentRole.PLANNER),),
        ),
        "tool_results": (result,),
        "final_result": AgentResult(role=AgentRole.SYNTHESIZER, model_id="synthetic",
                                    content=outcome.rendered_result),
    }
    invocation = await JobGraphInvoker(graph).invoke(
        UserRequest(text="Solicitud sintética"), (), stream_callback=lambda _: None,
    )
    assert not invocation.action_verified

    conversations = AsyncMock()
    conversations.recovery.return_value = None
    pipeline = JobExecutionPipeline(graph, execution_timeout_seconds=1, conversations=conversations)
    await pipeline.record_recovery(
        uuid4(), request_id=uuid4(), request_text="Solicitud sintética", tool_name=tool,
        call_digest=auth.call_digest, status="observed", result=result,
    )
    recovery = conversations.record_recovery.call_args.args[1]
    assert recovery.observation is not None  # Keep evidence; never repeat just to verify delivery.
    assert recovery.observation.tool_name == tool
    retained = json.loads(recovery.observation.excerpt)
    assert retained == {"verification_scope": NATIVE_RECEIPT_SCOPE,
                        "goal_verified": False, "receipt": CASES[tool][1]}

    provider, executor = AsyncMock(), AsyncMock()
    resumed = build_nvidia_swarm_graph(
        provider, tool_executor=executor, policy_context=default_policy_context(tmp_path),
    )
    state = await resumed.ainvoke({"request": UserRequest(
        text="Continúa", metadata={"task_recovery": recovery.model_dump(mode="json")},
    )})
    response = state["final_result"].content
    assert "No la he repetido" in response and '"goal_verified": false' in response
    assert NATIVE_RECEIPT_SCOPE in response and "Correo enviado" not in response
    provider.complete.assert_not_awaited()
    provider.complete_nvidia_only.assert_not_awaited()
    executor.execute_async.assert_not_awaited()


@pytest.mark.parametrize("tool", ["application_open", "browser_open_url", "browser_search",
                                  "media_control"])
def test_direct_response_rejects_empty_mismatched_and_unscoped_evidence(tool: str) -> None:
    auth, result = authorization(tool), accepted(tool)
    call = ToolCall(call_id=auth.call_id, tool_name=tool, arguments=CASES[tool][0],
                    requested_by=AgentRole.PLANNER)
    response = _deterministic_native_control_response(call, (result,))
    assert response is not None and "No he comprobado" in response
    for change in ({"output": "{}"}, {"metadata": {}}, {"call_id": "other"}):
        response = _deterministic_native_control_response(call, (result.model_copy(update=change),))
        assert "Revisa si se aplicó algún cambio" in response


@pytest.mark.parametrize("state", ["true", "command_sent", "key_sent", "wake_packet_sent"])
async def test_tv_command_acknowledgement_does_not_claim_the_appliance_state(state: str) -> None:
    auth = authorization("media_control").model_copy(update={
        "tool_name": "smart_tv_control", "normalized_arguments": {"device_id": "synthetic-tv"},
    })
    result = ToolExecutionResult(
        call_id=auth.call_id, tool_name=auth.tool_name, success=True,
        output=json.dumps({"device_id": "synthetic-tv", "state": state}),
        metadata={"verified": True, "goal_verified": False, "device_id": "synthetic-tv",
                  "device_state": state, "verification_scope": "smart_tv_command_acknowledgement"},
    )
    assert JobToolPresenter.format_result(result, auth) == (
        "Envié la orden al televisor. No he comprobado su estado final.")
    assert not result_goal_is_verified(result)
    assert (await VisualReflectionNode().evaluate(auth, result)).reason == "command_acknowledged"
    for payload in ({}, {"device_id": "other", "state": state},
                    {"device_id": "synthetic-tv", "state": "false"}):
        with pytest.raises(ValueError, match="TV"):
            JobToolPresenter.format_result(result.model_copy(update={
                "output": json.dumps(payload),
            }), auth)
