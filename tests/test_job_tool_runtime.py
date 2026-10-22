from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from uuid import uuid4

import pytest

from aegis_core.action_history import ActionHistoryStore
from aegis_core.contracts import (
    AgentRole,
    PolicyDecision,
    ToolAuthorization,
    ToolCall,
    ToolExecutionResult,
)
from aegis_core.job_contracts import JobConfirmationError
from aegis_core.job_tool_presenter import JobToolPresenter
from aegis_core.job_tool_runtime import ApprovedToolExecutionError, JobToolRuntime
from aegis_core.tools.audit import HashChainAuditLog, NullAuditSink
from aegis_core.tools.broker import PolicyContext
from aegis_core.tools.confirmations import ConfirmationStatus, OneTimeConfirmationStore
from aegis_core.tools.defaults import build_default_tool_broker, default_policy_context
from aegis_core.tools.native_receipts import NATIVE_RECEIPT_SCOPE


class RecordingExecutor:
    def __init__(self, result: ToolExecutionResult) -> None:
        self._result = result
        self.calls = 0

    async def execute_async(
        self,
        authorization: ToolAuthorization,
        context: PolicyContext,
    ) -> ToolExecutionResult:
        del authorization, context
        self.calls += 1
        return self._result


def _runtime(
    workspace: Path,
    executor: RecordingExecutor,
) -> JobToolRuntime:
    broker = build_default_tool_broker()
    store = OneTimeConfirmationStore()
    base = default_policy_context(workspace)
    context = PolicyContext(
        workspace_root=workspace,
        network_scopes=base.network_scopes,
        confirmation_store=store,
    )
    return JobToolRuntime(
        broker=broker,
        policy_context=context,
        confirmation_store=store,
        executor=executor,
        audit_sink=NullAuditSink(),
        presenter=JobToolPresenter(broker),
    )


@pytest.mark.asyncio
async def test_approved_runtime_rejects_result_from_another_call(tmp_path: Path) -> None:
    authorization = ToolAuthorization(
        call_id="expected-call",
        tool_name="network_discover_hosts",
        call_digest="a" * 64,
        decision=PolicyDecision.ALLOW,
        reason_code="confirmation_consumed",
        normalized_arguments={
            "target": "127.0.0.1/32",
            "ports": [443],
            "timeout_seconds": 0.25,
        },
    )
    executor = RecordingExecutor(
        ToolExecutionResult(
            call_id="substituted-call",
            tool_name=authorization.tool_name,
            success=True,
            output='{"target":"127.0.0.1/32","hosts":[]}',
        )
    )

    with pytest.raises(ApprovedToolExecutionError, match="does not match"):
        await _runtime(tmp_path, executor).execute(uuid4(), authorization)

    assert executor.calls == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("replace_call,replace_tool", [(True, False), (False, True), (True, True)])
async def test_misattributed_receipt_never_publishes_success_or_another_tool(
    tmp_path: Path, replace_call: bool, replace_tool: bool,
) -> None:
    authorization = ToolAuthorization(
        call_id="authorized-call", tool_name="network_discover_hosts", call_digest="a" * 64,
        decision=PolicyDecision.ALLOW, reason_code="confirmation_consumed",
        normalized_arguments={"target": "127.0.0.1/32", "ports": [443]},
    )
    forged = ToolExecutionResult(
        call_id="unrelated-call" if replace_call else authorization.call_id,
        tool_name="mail_send_message" if replace_tool else authorization.tool_name,
        success=True, output="PRIVATE_SYNTHETIC_RECEIPT", metadata={"verified": True},
    )
    request_id = uuid4()
    history = ActionHistoryStore(tmp_path / "activity.json")
    history.begin_request(request_id, conversation_id=None, temporary=False)
    audit = HashChainAuditLog(tmp_path / "audit.jsonl", activity_observer=history)
    runtime = _runtime(tmp_path, RecordingExecutor(forged))
    runtime._audit = audit
    with pytest.raises(ApprovedToolExecutionError, match="does not match"):
        await runtime.execute(request_id, authorization)
    history.finish_request(request_id)
    records = audit.verify()
    assert len(records) == 2
    assert all(record.call_id == authorization.call_id for record in records)
    assert all(record.tool_name == authorization.tool_name for record in records)
    assert records[-1].data["success"] is False
    assert records[-1].data["error_code"] == "tool_result_mismatch"
    [entry] = history.page()["entries"]
    assert entry["state"] == "failed" and entry["tool_name"] == authorization.tool_name
    assert entry["failure_count"] == entry["execution_count"] == 1
    for path in (tmp_path / "audit.jsonl", history.path):
        assert "PRIVATE_SYNTHETIC_RECEIPT" not in path.read_text()
        assert "unrelated-call" not in path.read_text()
        assert "mail_send_message" not in path.read_text()


@pytest.mark.asyncio
@pytest.mark.parametrize("wrong_target", [False, True])
async def test_native_receipt_is_validated_before_publishing_success(
    tmp_path: Path, wrong_target: bool,
) -> None:
    authorization = ToolAuthorization(
        call_id="authorized-browser-call", tool_name="browser_open_url", call_digest="b" * 64,
        decision=PolicyDecision.ALLOW, reason_code="confirmation_consumed",
        normalized_arguments={"url": "https://example.com/authorized"},
    )
    receipt_url = "https://example.com/substituted" if wrong_target else "https://example.com/authorized"
    result = ToolExecutionResult(
        call_id=authorization.call_id, tool_name=authorization.tool_name, success=True,
        output='{"opened":true,"url":"' + receipt_url + '"}',
        metadata={"verified": True, "goal_verified": False,
                  "verification_scope": NATIVE_RECEIPT_SCOPE, "source": "macos_open"},
    )
    request_id = uuid4()
    history = ActionHistoryStore(tmp_path / "activity.json")
    history.begin_request(request_id, conversation_id=None, temporary=False)
    audit = HashChainAuditLog(tmp_path / "audit.jsonl", activity_observer=history)
    runtime = _runtime(tmp_path, RecordingExecutor(result))
    runtime._audit = audit
    if wrong_target:
        with pytest.raises(ApprovedToolExecutionError, match="evidence is invalid"):
            await runtime.execute(request_id, authorization)
    else:
        outcome = await runtime.execute(request_id, authorization)
        assert outcome.result.success and not outcome.goal_verified
    history.finish_request(request_id)
    [entry] = history.page()["entries"]
    assert entry["state"] == ("failed" if wrong_target else "reported_success")
    execution = audit.verify()[-1]
    assert execution.data["success"] is (not wrong_target)
    assert execution.data["error_code"] == ("tool_result_invalid" if wrong_target else None)
    assert "example.com" not in (tmp_path / "audit.jsonl").read_text()
    assert "example.com" not in history.path.read_text()


@pytest.mark.asyncio
async def test_approved_runtime_never_executes_an_unconsumed_grant(tmp_path: Path) -> None:
    authorization = ToolAuthorization(
        call_id="pending-call",
        tool_name="network_discover_hosts",
        call_digest="b" * 64,
        decision=PolicyDecision.REQUIRE_CONFIRMATION,
        reason_code="confirmation_required",
        normalized_arguments={
            "target": "127.0.0.1/32",
            "ports": [443],
            "timeout_seconds": 0.25,
        },
    )
    executor = RecordingExecutor(
        ToolExecutionResult(
            call_id=authorization.call_id,
            tool_name=authorization.tool_name,
            success=True,
            output='{"target":"127.0.0.1/32","hosts":[]}',
        )
    )

    with pytest.raises(ApprovedToolExecutionError, match="one-time grant"):
        await _runtime(tmp_path, executor).execute(uuid4(), authorization)

    assert executor.calls == 0


def _pending_runtime(tmp_path):
    call = ToolCall(
        call_id="approval-boundary", tool_name="network_discover_hosts",
        arguments={"target": "127.0.0.1", "ports": [443]},
        requested_by=AgentRole.CODE_SECURITY,
    )
    executor = RecordingExecutor(ToolExecutionResult(
        call_id=call.call_id, tool_name=call.tool_name, success=True, output="{}",
    ))
    runtime = _runtime(tmp_path, executor)
    pending = runtime._broker.authorize(call, runtime._policy_context)
    return runtime, call, pending


def _consume(runtime, call, pending, *, digest=None):
    return runtime.consume_confirmation(
        call=call, pending_authorization=pending, authorization_request=None,
        expected_call_digest=digest or call.digest(),
        received_call_digest=digest or call.digest(),
        approved_by="local-menu-bar-user", voice_confirmation=None,
    )


def test_approval_digest_must_match_the_retained_call_not_only_the_echo(tmp_path) -> None:
    runtime, call, pending = _pending_runtime(tmp_path)
    with pytest.raises(JobConfirmationError):
        _consume(runtime, call, pending, digest="a" * 64)
    assert runtime._confirmation_store.consume(
        call.digest(), now=runtime._policy_context.current_time()
    ) is ConfirmationStatus.MISSING


def test_changed_normalized_arguments_cannot_reuse_the_displayed_approval(tmp_path) -> None:
    runtime, call, pending = _pending_runtime(tmp_path)
    displayed = pending.model_copy(update={"normalized_arguments": {
        **pending.normalized_arguments, "ports": [8443],
    }})
    with pytest.raises(JobConfirmationError):
        _consume(runtime, call, displayed)
    assert runtime._confirmation_store.consume(
        call.digest(), now=runtime._policy_context.current_time()
    ) is not ConfirmationStatus.CONSUMED


@pytest.mark.parametrize("failure", ["deny", "raise"])
def test_failed_reauthorization_revokes_an_issued_grant(tmp_path, monkeypatch, failure) -> None:
    runtime, call, pending = _pending_runtime(tmp_path)

    def fail(*args, **kwargs):
        if failure == "raise":
            raise RuntimeError("private provider detail")
        return pending.model_copy(update={
            "decision": PolicyDecision.DENY, "reason_code": "policy_changed",
        })

    monkeypatch.setattr(runtime._broker, "authorize", fail)
    with pytest.raises(JobConfirmationError):
        _consume(runtime, call, pending)
    assert runtime._confirmation_store.consume(
        call.digest(), now=runtime._policy_context.current_time()
    ) is not ConfirmationStatus.CONSUMED


@pytest.mark.parametrize("during_audit", [False, True])
async def test_security_loss_stops_approved_execution_even_with_a_custom_executor(
    tmp_path, monkeypatch, during_audit
) -> None:
    runtime, call, pending = _pending_runtime(tmp_path)
    authorization = _consume(runtime, call, pending)
    compromised = [not during_audit]
    runtime._policy_context = replace(
        runtime._policy_context, security_compromised=lambda: compromised[0]
    )
    if during_audit:
        monkeypatch.setattr(
            runtime._audit, "record_authorization", lambda *args: compromised.__setitem__(0, True)
        )
    with pytest.raises(ApprovedToolExecutionError, match="compromised"):
        await runtime.execute(uuid4(), authorization)
    assert runtime._executor.calls == 0


def test_reauthorization_normalization_is_type_exact(tmp_path) -> None:
    runtime, call, pending = _pending_runtime(tmp_path)
    displayed = pending.model_copy(update={"normalized_arguments": {
        **pending.normalized_arguments, "ports": [True],
    }})
    # Boolean true and numeric one compare equal in Python, but not in an approval.
    call = call.model_copy(update={"arguments": {"target": "127.0.0.1", "ports": [1]}})
    displayed = displayed.model_copy(update={"call_digest": call.digest()})
    with pytest.raises(JobConfirmationError):
        _consume(runtime, call, displayed)


def test_reauthorization_reordering_keys_does_not_change_consent(tmp_path) -> None:
    runtime, call, pending = _pending_runtime(tmp_path)
    displayed = pending.model_copy(update={
        "normalized_arguments": dict(reversed(tuple(pending.normalized_arguments.items())))
    })
    assert _consume(runtime, call, displayed).decision is PolicyDecision.ALLOW
