"""Own finite scheduling contracts, with no account, effect tool or private data."""

import asyncio

import pytest

from jarvis_core.runtime import (
    AgentRuntime,
    FinalAnswer,
    RunBudget,
    ToolDefinition,
    ToolProposal,
    ToolResult,
)


async def test_step_budget_is_individual_and_counts_actual_provider_calls():
    calls = []

    class Provider:
        async def next_step(self, context):
            calls.append(context.session_id)
            return ToolProposal("fixture_read", {})

    async def read(_arguments, _cancellation):
        return ToolResult("fixture_read", True, False, "dato sintético")

    runtime = AgentRuntime(Provider(), [ToolDefinition("fixture_read", lambda _: None, read)])
    one = runtime.start("uno", "lee", budget=RunBudget(1))
    three = runtime.start("tres", "lee", budget=RunBudget(3))
    a, b = await asyncio.gather(one.await_settled(), three.await_settled())
    assert a.error == b.error == "step_limit"
    assert one.provider_steps == calls.count("uno") == 1
    assert three.provider_steps == calls.count("tres") == 3


async def test_deadline_keeps_unknown_effect_and_waits_for_real_cleanup():
    dispatched, cleaning, release = asyncio.Event(), asyncio.Event(), asyncio.Event()

    class Provider:
        async def next_step(self, _context):
            return ToolProposal("fixture_write", {})

    async def write(_arguments, _cancellation):
        dispatched.set()
        await asyncio.Event().wait()

    async def cleanup(_context):
        cleaning.set()
        await release.wait()

    runtime = AgentRuntime(Provider(), [ToolDefinition("fixture_write", lambda _: None, write)],
                           cleanup=cleanup)
    handle = runtime.start("uno", "escribe", budget=RunBudget(3, .02))
    await asyncio.wait_for(dispatched.wait(), 2)
    await asyncio.wait_for(cleaning.wait(), 2)
    assert not handle._settled.done()
    release.set()
    result = await asyncio.wait_for(handle.await_settled(), 2)
    assert result.status == "failed" and result.error == "run_deadline"
    assert len(result.action_receipts) == 1
    assert result.action_receipts[0].effect_status == "unknown"


async def test_suppressed_cancellation_cannot_turn_expired_effect_into_success():
    class Provider:
        async def next_step(self, _context):
            return ToolProposal("fixture_write", {})

    async def write(_arguments, _cancellation):
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            return ToolResult("fixture_write", True, True, "retorno tardío")

    runtime = AgentRuntime(Provider(), [ToolDefinition("fixture_write", lambda _: None, write)])
    result = await asyncio.wait_for(runtime.start("uno", "escribe", budget=RunBudget(2, .02))
                                    .await_settled(), 2)
    assert result.status == "failed" and result.error == "run_deadline"
    assert len(result.action_receipts) == 1
    assert result.action_receipts[0].effect_status == "unknown"
    assert not result.action_receipts[0].verified


async def test_lane_ceiling_preserves_unrelated_session_progress_and_cleanup_custody():
    entered, release, cleaning, cleaned = [], asyncio.Event(), asyncio.Event(), asyncio.Event()

    class Provider:
        async def next_step(self, context):
            entered.append(context.session_id)
            await release.wait()
            return FinalAnswer(context.message)

    async def cleanup(context):
        if context.session_id == "uno":
            cleaning.set()
            await cleaned.wait()

    runtime = AgentRuntime(Provider(), max_concurrent_runs=2, cleanup=cleanup)
    a, b, c = [runtime.start(name, name) for name in ("uno", "dos", "tres")]
    await asyncio.sleep(.01)
    assert entered == ["uno", "dos"]
    release.set()
    await cleaning.wait()
    await b.await_settled()
    await c.await_settled()
    assert entered == ["uno", "dos", "tres"] and not a._settled.done()
    successor = runtime.start("uno", "siguiente", budget=RunBudget(1, .01))
    await asyncio.sleep(.02)
    cleaned.set()
    await a.await_settled()
    assert (await successor.await_settled()).error == "run_deadline"
    assert entered.count("uno") == 1


async def test_failed_preparation_barrier_settles_failed_without_calling_provider():
    calls = []

    class Provider:
        async def next_step(self, context):
            calls.append(context)
            return FinalAnswer("No se debe ejecutar")

    async def before(_context):
        raise ConnectionError("synthetic predecessor receipt was not confirmed")

    handle = AgentRuntime(Provider(), before_start=before).start("uno", "dato")
    result = await handle.await_settled()
    assert result.status == "failed" and result.error == "run_preparation_failed"
    assert result.error_type == "ConnectionError" and result.action_receipts == () and not calls


@pytest.mark.parametrize("arguments", [
    {"max_steps": True}, {"max_steps": 0}, {"max_steps": 101},
    {"timeout_seconds": False}, {"timeout_seconds": 0},
    {"timeout_seconds": float("inf")}, {"timeout_seconds": 3601},
])
def test_invalid_budget_is_rejected_before_admission(arguments):
    with pytest.raises(ValueError):
        RunBudget(**arguments)
