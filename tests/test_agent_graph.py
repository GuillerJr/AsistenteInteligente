from __future__ import annotations

import hashlib

import pytest

from aegis_core.brain.agent_graph import PlanExecuteReflectRunner, PlanStepStatus
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
async def test_reflection_loop_has_hard_ceiling_of_three() -> None:
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
    assert outcome.halt_reason == "reflection_limit"
    assert len(outcome.reflections) == 3
    assert executions == 3
    assert corrections == 2
    assert outcome.contract.steps[0].status is PlanStepStatus.FAILED
