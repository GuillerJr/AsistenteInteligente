from __future__ import annotations

import hashlib
from unittest.mock import AsyncMock

import pytest

from aegis_core.brain.agent_graph import PlanExecuteReflectRunner, PlanStepStatus
from aegis_core.brain.nodes.reflector import ReflectionStatus, VisualReflectionNode
from aegis_core.contracts import (
    PolicyDecision,
    ToolAuthorization,
    ToolExecutionResult,
)


def authorization(call_id: str = "call-1") -> ToolAuthorization:
    return ToolAuthorization(
        call_id=call_id,
        tool_name="computer_use",
        call_digest=hashlib.sha256(call_id.encode()).hexdigest(),
        decision=PolicyDecision.ALLOW,
        reason_code="confirmation_consumed",
        normalized_arguments={},
    )


def result(
    *,
    success: bool,
    status: str,
    reason_code: str,
    verified: bool,
) -> ToolExecutionResult:
    return ToolExecutionResult(
        call_id="call-1",
        tool_name="computer_use",
        success=success,
        output="{}",
        metadata={
            "status": status,
            "reason_code": reason_code,
            "steps": 0,
            "verified": verified,
        },
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("wrong_field", ["call_id", "tool_name"])
async def test_unrelated_result_never_advances_plan_or_triggers_ui_recovery(
    wrong_field: str,
) -> None:
    foreign = result(
        success=True, status="completed", reason_code="objective_complete", verified=True
    )
    foreign = foreign.model_copy(update={wrong_field: "unrelated"})
    execute = AsyncMock(return_value=foreign)
    correct = AsyncMock(return_value=True)
    reload_ui = AsyncMock(return_value=True)
    reevaluate = AsyncMock(return_value=True)
    outcome = await PlanExecuteReflectRunner(
        execute,
        ui_corrector=correct,
        ui_reloader=reload_ui,
        ui_reevaluator=reevaluate,
    ).run(
        goal="synthetic automation",
        authorizations=(authorization(), authorization("call-2")),
        historical_context="A modal appeared previously",
    )
    assert outcome.halted
    execute.assert_awaited_once()
    correct.assert_not_awaited()
    reload_ui.assert_not_awaited()
    reevaluate.assert_not_awaited()
    assert outcome.contract.steps[0].status is PlanStepStatus.FAILED
    assert outcome.contract.steps[1].status is PlanStepStatus.PENDING
    assert len(outcome.results) == 1
    assert outcome.results[0].call_id == "call-1"
    assert outcome.results[0].error_code == "tool_result_mismatch"


@pytest.mark.asyncio
async def test_duplicate_plan_call_ids_are_rejected_before_execution() -> None:
    execute = AsyncMock()
    with pytest.raises(ValueError, match="unique"):
        await PlanExecuteReflectRunner(execute).run(
            goal="duplicate action",
            authorizations=(authorization(), authorization()),
        )
    execute.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "proof",
    [
        {},
        {"verified": 0},
        {"verified": 1},
        {"verified": "false"},
        {"verified": True, "status": "running"},
    ],
)
async def test_computer_success_requires_explicit_completed_boolean_proof(proof: dict) -> None:
    unverified = result(
        success=True, status="completed", reason_code="objective_complete", verified=True
    )
    unverified = unverified.model_copy(update={"metadata": proof})
    decision = await VisualReflectionNode().evaluate(authorization(), unverified)
    assert decision.status is not ReflectionStatus.VERIFIED


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "reason,steps", [("user_takeover", 0), ("sensitive_action", 0), ("uncertain_state", 1)]
)
async def test_unsafe_or_partially_executed_ui_action_is_not_retried(
    reason: str, steps: int
) -> None:
    blocked = result(success=True, status="blocked", reason_code=reason, verified=False)
    blocked = blocked.model_copy(update={"metadata": {**blocked.metadata, "steps": steps}})
    execute = AsyncMock(return_value=blocked)
    correct = AsyncMock(return_value=True)
    outcome = await PlanExecuteReflectRunner(execute, ui_corrector=correct).run(
        goal="synthetic automation",
        authorizations=(authorization(),),
        historical_context="A modal appeared previously",
    )
    assert outcome.halted
    execute.assert_awaited_once()
    correct.assert_not_awaited()


@pytest.mark.asyncio
async def test_plan_execute_reflect_finishes_verified_step() -> None:
    async def execute(_: ToolAuthorization) -> ToolExecutionResult:
        return result(
            success=True,
            status="completed",
            reason_code="objective_complete",
            verified=True,
        )

    outcome = await PlanExecuteReflectRunner(execute).run(
        goal="open the authorized window",
        authorizations=(authorization(),),
    )

    assert not outcome.halted
    assert len(outcome.results) == 1
    assert outcome.contract.steps[0].status is PlanStepStatus.SUCCEEDED
    assert "open the authorized window" not in outcome.contract.markdown()


@pytest.mark.asyncio
async def test_plan_self_corrects_transient_modal_before_safe_retry() -> None:
    executions = 0
    corrections = 0

    async def execute(_: ToolAuthorization) -> ToolExecutionResult:
        nonlocal executions
        executions += 1
        if executions == 1:
            return result(
                success=True,
                status="blocked",
                reason_code="uncertain_state",
                verified=False,
            )
        return result(
            success=True,
            status="completed",
            reason_code="objective_complete",
            verified=True,
        )

    async def correct(
        _: ToolAuthorization,
        __: ToolExecutionResult,
    ) -> bool:
        nonlocal corrections
        corrections += 1
        return True

    outcome = await PlanExecuteReflectRunner(execute, ui_corrector=correct).run(
        goal="continue after a modal",
        authorizations=(authorization(),),
    )

    assert executions == 2
    assert corrections == 1
    assert not outcome.halted
    assert len(outcome.attempts) == 2
    assert outcome.contract.steps[0].status is PlanStepStatus.SUCCEEDED


@pytest.mark.asyncio
async def test_behavior_tree_detects_recovery_cycle_and_requests_intervention() -> None:
    executions = 0
    corrections = 0

    async def execute(_: ToolAuthorization) -> ToolExecutionResult:
        nonlocal executions
        executions += 1
        return result(
            success=True,
            status="blocked",
            reason_code="uncertain_state",
            verified=False,
        )

    async def correct(
        _: ToolAuthorization,
        __: ToolExecutionResult,
    ) -> bool:
        nonlocal corrections
        corrections += 1
        return True

    outcome = await PlanExecuteReflectRunner(execute, ui_corrector=correct).run(
        goal="bounded retry",
        authorizations=(authorization(),),
    )

    assert outcome.halted
    assert outcome.halt_reason == "user_intervention_required"
    assert len(outcome.reflections) == 1
    assert executions == 4
    assert corrections == 2
    assert outcome.contract.steps[0].status is PlanStepStatus.FAILED
