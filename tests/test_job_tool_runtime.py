from __future__ import annotations

from pathlib import Path
from uuid import uuid4

import pytest

from aegis_core.contracts import PolicyDecision, ToolAuthorization, ToolExecutionResult
from aegis_core.job_tool_presenter import JobToolPresenter
from aegis_core.job_tool_runtime import ApprovedToolExecutionError, JobToolRuntime
from aegis_core.tools.audit import NullAuditSink
from aegis_core.tools.broker import PolicyContext
from aegis_core.tools.confirmations import OneTimeConfirmationStore
from aegis_core.tools.defaults import build_default_tool_broker, default_policy_context


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
