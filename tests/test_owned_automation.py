from __future__ import annotations

import asyncio
import contextvars
import json
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from aegis_core.async_tasks import run_blocking_owned
from aegis_core.contracts import (
    AgentResult,
    AgentRole,
    PolicyDecision,
    ToolAuthorization,
    ToolCall,
    ToolExecutionResult,
    UserRequest,
)
from aegis_core.job_graph import JobGraphInvoker
from aegis_core.job_tool_presenter import JobToolPresenter
from aegis_core.job_tool_runtime import ApprovedToolOutcome
from aegis_core.tools.background_automation import QuietActionVerification
from aegis_core.tools.computer import ComputerUseError, NativeComputerBridge
from aegis_core.tools.defaults import default_policy_context
from aegis_core.tools.execution import ReadOnlyToolExecutor


def authorized(reason: str = "confirmation_consumed") -> ToolAuthorization:
    return ToolAuthorization(
        call_id="call-1",
        tool_name="computer_use",
        call_digest="a" * 64,
        decision=PolicyDecision.ALLOW,
        reason_code=reason,
        normalized_arguments={
            "objective": "Haz clic en Documentación",
            "application_bundle_identifier": "ai.aegis.fixture",
            "max_steps": 3,
        },
    )


@pytest.mark.asyncio
async def test_owned_worker_propagates_context_result_and_original_failure() -> None:
    context = contextvars.ContextVar("automation_test", default="unset")
    token = context.set("request-context")
    try:
        assert await run_blocking_owned(context.get) == "request-context"
        with pytest.raises(ValueError, match="invalid integer"):
            await run_blocking_owned(int, "invalid integer")
    finally:
        context.reset(token)


@pytest.mark.asyncio
async def test_cancelled_queued_worker_never_executes_its_action() -> None:
    # An isolated one-worker pool makes the queued-vs-running boundary deterministic.
    # Never replace pytest's running event loop's default executor.
    def scenario() -> None:
        async def run() -> None:
            loop = asyncio.get_running_loop()
            started = asyncio.Event()
            release = threading.Event()
            calls: list[str] = []
            loop.set_default_executor(ThreadPoolExecutor(max_workers=1))

            def occupy() -> None:
                loop.call_soon_threadsafe(started.set)
                assert release.wait(3)

            occupying = asyncio.create_task(asyncio.to_thread(occupy))
            cancelled = None
            try:
                await asyncio.wait_for(started.wait(), 1)
                cancelled = asyncio.create_task(run_blocking_owned(calls.append, "late action"))
                await asyncio.sleep(0)
                await asyncio.sleep(0)
                cancelled.cancel()
                await asyncio.sleep(0)
            finally:
                release.set()
                await occupying
                if cancelled is not None:
                    with pytest.raises(asyncio.CancelledError):
                        await cancelled
            assert calls == []

        asyncio.run(run())

    await asyncio.to_thread(scenario)


@pytest.mark.asyncio
async def test_sync_tool_cancellation_drains_late_failure(tmp_path: Path) -> None:
    loop = asyncio.get_running_loop()
    started = asyncio.Event()
    release = threading.Event()
    finished = threading.Event()

    def blocking(*_: object) -> ToolExecutionResult:
        loop.call_soon_threadsafe(started.set)
        try:
            assert release.wait(3)
            raise ValueError("private late failure")
        finally:
            finished.set()

    executor = ReadOnlyToolExecutor(extra_handlers={"fixture_action": blocking})
    grant = authorized().model_copy(update={"tool_name": "fixture_action"})
    task = asyncio.create_task(executor.execute_async(grant, default_policy_context(tmp_path)))
    try:
        await asyncio.wait_for(started.wait(), 1)
        task.cancel()
        await asyncio.sleep(0)
        task.cancel()
        await asyncio.sleep(0)
        assert not task.done()
    finally:
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await task
    assert finished.is_set()


@pytest.mark.parametrize(
    "changes",
    [
        {"verified": "true"},
        {"verified": 1},
        {"state_changed": 1},
        {"after_sha256": "a" * 64},
        {"state_changed": False},
    ],
)
def test_quiet_evidence_rejects_coercion_and_inconsistent_hashes(changes: dict) -> None:
    with pytest.raises(ValueError):
        QuietActionVerification.model_validate(
            {
                "pathway": "accessibility",
                "before_sha256": "a" * 64,
                "after_sha256": "b" * 64,
                "state_changed": True,
                "verified": True,
                **changes,
            }
        )


def test_target_identity_takes_precedence_over_foreground_identity() -> None:
    with pytest.raises(ComputerUseError, match="frontmost_application_mismatch"):
        NativeComputerBridge._require_target(
            {
                "target_bundle_identifier": "ai.aegis.unrelated",
                "frontmost_bundle_identifier": "ai.aegis.fixture",
            },
            "ai.aegis.fixture",
        )
    NativeComputerBridge._require_target(
        {
            "target_bundle_identifier": "ai.aegis.fixture",
            "frontmost_bundle_identifier": "ai.aegis.owner",
        },
        "ai.aegis.fixture",
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("method", ["reload_ui", "reevaluate_ui"])
async def test_recovery_requires_consumed_confirmation(method: str) -> None:
    controller = AsyncMock()
    controller.reload_application.return_value = True
    controller.reevaluate_application.return_value = True
    executor = ReadOnlyToolExecutor(computer_controller=controller)
    assert not await getattr(executor, method)(authorized("policy_allowed"))
    assert controller.mock_calls == []
    assert await getattr(executor, method)(authorized())
    assert len(controller.mock_calls) == 1


@pytest.mark.asyncio
async def test_modal_correction_rejects_unrelated_result(tmp_path: Path) -> None:
    controller = AsyncMock()
    executor = ReadOnlyToolExecutor(computer_controller=controller)
    foreign = ToolExecutionResult(
        call_id="another-call",
        tool_name="computer_use",
        success=True,
        metadata={
            "verified": False,
            "status": "blocked",
            "reason_code": "uncertain_state",
            "steps": 0,
        },
    )
    assert not await executor.correct_ui_block(
        authorized(), foreign, default_policy_context(tmp_path)
    )
    for invalid_evidence in ({"steps": False}, {"verified": True}):
        invalid = foreign.model_copy(
            update={
                "call_id": "call-1",
                "metadata": {**foreign.metadata, **invalid_evidence},
            }
        )
        assert not await executor.correct_ui_block(
            authorized(),
            invalid,
            default_policy_context(tmp_path),
        )
    assert controller.mock_calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "changes",
    [
        {"metadata": {}},
        {"metadata": {"verified": True, "status": "blocked"}},
        {"metadata": {"verified": 1, "status": "completed"}},
        {"error_code": "computer_helper_failed"},
    ],
)
async def test_approved_runtime_and_job_graph_share_strict_ui_evidence(changes: dict) -> None:
    call = ToolCall(
        call_id="call-1",
        tool_name="computer_use",
        arguments={},
        requested_by=AgentRole.CODE_SECURITY,
    )
    result = ToolExecutionResult(
        call_id="call-1",
        tool_name="computer_use",
        success=True,
        metadata={"verified": True, "status": "completed"},
    ).model_copy(update=changes)
    assert not ApprovedToolOutcome(result=result, rendered_result=None).verified
    claimed_completion = result.model_copy(
        update={
            "output": json.dumps(
                {
                    "application_bundle_identifier": "ai.aegis.fixture",
                    "reason_code": "objective_complete",
                    "status": "completed",
                    "steps": 1,
                }
            )
        }
    )
    with pytest.raises(ValueError, match="unverified"):
        JobToolPresenter.format_result(claimed_completion, authorized())
    graph = AsyncMock()
    graph.ainvoke.return_value = {
        "specialist_result": AgentResult(
            role=AgentRole.CODE_SECURITY,
            model_id="fixture/model",
            content="",
            tool_calls=(call,),
        ),
        "tool_results": (result,),
        "final_result": AgentResult(
            role=AgentRole.SYNTHESIZER,
            model_id="fixture/model",
            content="Unverified",
        ),
    }
    invocation = await JobGraphInvoker(graph).invoke(
        UserRequest(text="Synthetic action"),
        (),
        stream_callback=lambda _: None,
    )
    assert not invocation.action_verified


@pytest.mark.asyncio
async def test_job_graph_does_not_attribute_another_calls_success() -> None:
    graph = AsyncMock()
    graph.ainvoke.return_value = {
        "specialist_result": AgentResult(
            role=AgentRole.CODE_SECURITY,
            model_id="fixture/model",
            content="",
            tool_calls=(
                ToolCall(
                    call_id="call-1",
                    tool_name="computer_use",
                    arguments={},
                    requested_by=AgentRole.CODE_SECURITY,
                ),
            ),
        ),
        "tool_results": (
            ToolExecutionResult(
                call_id="foreign",
                tool_name="computer_use",
                success=True,
                metadata={"verified": True, "status": "completed"},
            ),
        ),
        "final_result": AgentResult(
            role=AgentRole.SYNTHESIZER,
            model_id="fixture/model",
            content="Unverified",
        ),
    }
    invocation = await JobGraphInvoker(graph).invoke(
        UserRequest(text="Synthetic action"),
        (),
        stream_callback=lambda _: None,
    )
    assert not invocation.action_verified
