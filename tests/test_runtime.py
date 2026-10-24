"""Synthetic behavioral contracts; no model, owner data, device, IPC or network."""

import asyncio
import json
from dataclasses import asdict

import pytest

from aegis_core import (
    AgentRuntime,
    ApprovalGrant,
    FinalAnswer,
    RunPhase,
    ToolDefinition,
    ToolProposal,
    ToolResult,
)


async def settled(handle):
    return await asyncio.wait_for(handle.await_settled(), timeout=2)


async def observed(event):
    await asyncio.wait_for(event.wait(), timeout=2)


def validate_text(arguments):
    if set(arguments) != {"text"} or not isinstance(arguments["text"], str):
        raise ValueError("expected text")


class ToolThenAnswer:
    def __init__(self, proposal=None, answer="Terminado"):
        self.proposal = proposal or ToolProposal("fixture_read", {"text": "sintético"})
        self.answer = answer

    async def next_step(self, context):
        return FinalAnswer(self.answer) if context.tool_results else self.proposal


async def test_chat_text_does_not_invent_verified_tool_work():
    class Provider:
        async def next_step(self, context):
            return FinalAnswer("He completado y verificado todo")

    result = await settled(AgentRuntime(Provider()).start("temporal", "Hola"))
    assert result.status == "completed"
    assert result.answer == "He completado y verificado todo"
    assert result.answer_source == "model" and result.action_receipts == ()


async def test_tool_success_and_verification_remain_independent_of_final_text():
    async def execute(arguments, cancellation):
        assert arguments["text"] == "sintético" and not cancellation.is_set()
        return ToolResult("fixture_read", succeeded=True, verified=False, output="dato leído")

    runtime = AgentRuntime(ToolThenAnswer(answer="El resultado está verificado"), [
        ToolDefinition("fixture_read", validate_text, execute),
    ])
    result = await settled(runtime.start("test", "Lee"))
    assert result.status == "completed" and result.tool_results[0].succeeded
    assert not result.tool_results[0].verified
    assert result.tool_results[0].output == "dato leído"
    assert result.answer_source == "model" and not result.action_receipts[0].verified


async def test_write_waits_for_an_external_grant_for_the_exact_snapshot():
    waiting, permit = asyncio.Event(), asyncio.Event()
    requests, effects = [], []

    class Gate:
        async def request(self, operation, cancellation):
            requests.append(operation)
            waiting.set()
            await permit.wait()
            return ApprovalGrant(operation.digest)

    async def execute(arguments, cancellation):
        with pytest.raises(TypeError):
            arguments["text"] = "changed"
        effects.append(arguments["text"])
        return ToolResult("fixture_write", succeeded=True, verified=True)

    runtime = AgentRuntime(ToolThenAnswer(ToolProposal("fixture_write", {"text": "A"})), [
        ToolDefinition("fixture_write", validate_text, execute, requires_confirmation=True),
    ], approval_gate=Gate())
    handle = runtime.start("conversation-A", "Escribe A")
    await observed(waiting)
    assert handle.phase is RunPhase.WAITING_APPROVAL and effects == []
    assert requests[0].run_id == handle.run_id and requests[0].session_id == "conversation-A"
    assert json.loads(requests[0].arguments_json) == {"text": "A"}
    assert len(requests[0].digest) == 64
    assert requests[0].operation_id
    permit.set()
    result = await settled(handle)
    assert result.status == "completed" and effects == ["A"]
    assert result.tool_results[0].verified
    assert result.tool_results[0].operation_id == requests[0].operation_id


@pytest.mark.parametrize("mutation", ["proposal", "validated", "grant"])
async def test_mutation_or_mismatched_grant_cannot_authorize_an_effect(mutation):
    original, retained, effects = {"text": "A"}, [], []

    def validate(arguments):
        validate_text(arguments)
        retained.append(arguments)

    class Gate:
        async def request(self, operation, cancellation):
            if mutation == "proposal":
                original["text"] = "B"
            elif mutation == "validated":
                retained[0]["text"] = "B"
            return ApprovalGrant("0" * 64 if mutation == "grant" else operation.digest)

    async def execute(arguments, cancellation):
        effects.append(arguments["text"])
        return ToolResult("fixture_write", True, True)

    runtime = AgentRuntime(ToolThenAnswer(ToolProposal("fixture_write", original)), [
        ToolDefinition("fixture_write", validate, execute, True),
    ], approval_gate=Gate())
    result = await settled(runtime.start("test", "Escribe A"))
    assert result.status == "failed" and effects == []
    assert result.error == ("approval_denied" if mutation == "grant" else "arguments_changed")


async def test_a_grant_from_another_run_is_not_reusable():
    grant = None
    effects = []

    class Gate:
        async def request(self, operation, cancellation):
            nonlocal grant
            if grant is None:
                grant = ApprovalGrant(operation.digest)
            return grant

    async def execute(arguments, cancellation):
        effects.append(arguments["text"])
        return ToolResult("fixture_write", True, True)

    runtime = AgentRuntime(ToolThenAnswer(ToolProposal("fixture_write", {"text": "A"})), [
        ToolDefinition("fixture_write", validate_text, execute, True),
    ], approval_gate=Gate())
    assert (await settled(runtime.start("one", "Escribe A"))).status == "completed"
    second = await settled(runtime.start("two", "Escribe A"))
    assert second.error == "approval_denied" and effects == ["A"]


@pytest.mark.parametrize("replay", [True, False])
async def test_identical_proposals_in_one_run_need_distinct_operation_grants(replay):
    requests, effects = [], []
    first_grant = None

    class Provider:
        async def next_step(self, context):
            if len(context.tool_results) < 2:
                return ToolProposal("fixture_write", {"text": "A"})
            return FinalAnswer("Terminado")

    class Gate:
        async def request(self, operation, cancellation):
            nonlocal first_grant
            requests.append(operation)
            if first_grant is None:
                first_grant = ApprovalGrant(operation.digest)
            return first_grant if replay else ApprovalGrant(operation.digest)

    async def execute(arguments, cancellation):
        effects.append(arguments["text"])
        return ToolResult("fixture_write", True, True)

    runtime = AgentRuntime(Provider(), [
        ToolDefinition("fixture_write", validate_text, execute, True),
    ], approval_gate=Gate())
    result = await settled(runtime.start("one", "Prueba dos operaciones idénticas"))
    assert len(requests) == 2 and requests[0].run_id == requests[1].run_id
    assert requests[0].arguments_json == requests[1].arguments_json
    assert requests[0].operation_id != requests[1].operation_id
    assert requests[0].digest != requests[1].digest
    if replay:
        assert result.error == "approval_denied" and effects == ["A"]
    else:
        assert result.status == "completed" and effects == ["A", "A"]
        assert len({receipt.operation_id for receipt in result.action_receipts}) == 2


async def test_preconditions_are_revalidated_after_owner_wait():
    ready, effects = True, []

    def validate(arguments):
        validate_text(arguments)
        if not ready:
            raise ValueError("synthetic resource is no longer available")

    class Gate:
        async def request(self, operation, cancellation):
            nonlocal ready
            ready = False
            return ApprovalGrant(operation.digest)

    async def execute(arguments, cancellation):
        effects.append(arguments)
        return ToolResult("fixture_write", True, True)

    runtime = AgentRuntime(ToolThenAnswer(ToolProposal("fixture_write", {"text": "A"})), [
        ToolDefinition("fixture_write", validate, execute, True),
    ], approval_gate=Gate())
    assert (await settled(runtime.start("one", "Escribe A"))).error == "invalid_arguments"
    assert effects == []


async def test_missing_gate_never_authorizes_writes():
    effects = []

    async def execute(arguments, cancellation):
        effects.append(arguments)
        return ToolResult("fixture_write", True, True)

    runtime = AgentRuntime(ToolThenAnswer(ToolProposal("fixture_write", {"text": "A"})), [
        ToolDefinition("fixture_write", validate_text, execute, True),
    ])
    result = await settled(runtime.start("one", "Escribe A"))
    assert result.error == "approval_required" and effects == []


async def test_cancellation_retains_session_until_cleanup_and_other_sessions_are_independent():
    first_active, closing, release = asyncio.Event(), asyncio.Event(), asyncio.Event()
    admitted = []

    class Provider:
        async def next_step(self, context):
            admitted.append(context.message)
            if context.message == "first":
                first_active.set()
                await asyncio.Event().wait()
            return FinalAnswer(context.message)

    async def cleanup(context):
        if context.message == "first":
            closing.set()
            await release.wait()

    runtime = AgentRuntime(Provider(), cleanup=cleanup)
    first = runtime.start("same", "first")
    await observed(first_active)
    assert first.cancel().accepted
    await observed(closing)
    assert first.phase is RunPhase.CLEANING and not first.cancel().accepted
    second = runtime.start("same", "second")
    other = runtime.start("different", "other")
    assert (await settled(other)).answer == "other"
    assert admitted == ["first", "other"] and second.phase is RunPhase.QUEUED
    release.set()
    assert (await settled(first)).status == "cancelled"
    assert (await settled(second)).answer == "second"
    assert admitted == ["first", "other", "second"]


async def test_cancel_while_waiting_for_approval_prevents_effect_even_if_gate_ignores_cancel():
    waiting, effects = asyncio.Event(), []

    class Gate:
        async def request(self, operation, cancellation):
            waiting.set()
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                return ApprovalGrant(operation.digest)

    async def execute(arguments, cancellation):
        effects.append(arguments)
        return ToolResult("fixture_write", True, True)

    runtime = AgentRuntime(ToolThenAnswer(ToolProposal("fixture_write", {"text": "A"})), [
        ToolDefinition("fixture_write", validate_text, execute, True),
    ], approval_gate=Gate())
    handle = runtime.start("one", "Escribe A")
    await observed(waiting)
    assert handle.cancel().accepted
    assert (await settled(handle)).status == "cancelled" and effects == []


async def test_cancellation_before_first_schedule_does_not_leave_an_unsettled_handle():
    calls = []

    class Provider:
        async def next_step(self, context):
            calls.append(context)
            return FinalAnswer("unused")

    handle = AgentRuntime(Provider()).start("one", "Hola")
    assert handle.cancel().accepted
    assert (await settled(handle)).status == "cancelled" and calls == []


async def test_cancelling_a_queued_run_does_not_clean_up_the_active_run():
    active, release = asyncio.Event(), asyncio.Event()
    admitted, cleaned = [], []

    class Provider:
        async def next_step(self, context):
            admitted.append(context.message)
            active.set()
            await release.wait()
            return FinalAnswer(context.message)

    async def cleanup(context):
        cleaned.append(context.message)

    runtime = AgentRuntime(Provider(), cleanup=cleanup)
    first = runtime.start("same", "first")
    await observed(active)
    queued = runtime.start("same", "queued")
    await asyncio.sleep(0)
    assert queued.cancel().accepted
    assert (await settled(queued)).status == "cancelled"
    assert admitted == ["first"] and cleaned == []
    release.set()
    assert (await settled(first)).answer == "first" and cleaned == ["first"]


async def test_cancelled_result_observer_does_not_cancel_the_run():
    active, release = asyncio.Event(), asyncio.Event()

    class Provider:
        async def next_step(self, context):
            active.set()
            await release.wait()
            return FinalAnswer("sigue activo")

    handle = AgentRuntime(Provider()).start("one", "Hola")
    await observed(active)
    observer = asyncio.create_task(handle.await_settled())
    await asyncio.sleep(0)
    observer.cancel()
    with pytest.raises(asyncio.CancelledError):
        await observer
    release.set()
    assert (await settled(handle)).answer == "sigue activo"


@pytest.mark.parametrize("offered", [(), ("fixture_read",)])
async def test_unknown_or_unoffered_tools_never_execute(offered):
    effects = []

    async def execute(arguments, cancellation):
        effects.append(arguments)
        return ToolResult("fixture_read", True, True)

    provider = ToolThenAnswer(ToolProposal(
        "fixture_read" if not offered else "not_registered", {"text": "A"},
    ))
    runtime = AgentRuntime(provider, [ToolDefinition("fixture_read", validate_text, execute)])
    result = await settled(runtime.start("one", "Lee", offered_tools=offered))
    assert result.error == "tool_not_offered" and effects == []


@pytest.mark.parametrize("failure", ["invalid_arguments", "provider", "tool", "step_limit"])
async def test_invalid_arguments_errors_and_step_budget_are_bounded(failure):
    effects = []

    class Provider:
        async def next_step(self, context):
            if failure == "provider":
                raise RuntimeError("synthetic detail that must not be published")
            return ToolProposal("fixture_read", {"bad": "A"} if failure == "invalid_arguments"
                                else {"text": "A"})

    async def execute(arguments, cancellation):
        effects.append(arguments["text"])
        if failure == "tool":
            raise RuntimeError("synthetic execution error")
        return ToolResult("fixture_read", True, False)

    runtime = AgentRuntime(Provider(), [ToolDefinition("fixture_read", validate_text, execute)],
                             max_steps=2)
    result = await settled(runtime.start("one", "Lee"))
    assert result.status == "failed" and result.answer is None
    assert result.error == (failure if failure in {"invalid_arguments", "step_limit"}
                            else "tool_execution_failed" if failure == "tool"
                            else "execution_failed")
    assert len(effects) == (2 if failure == "step_limit" else 1 if failure == "tool" else 0)


async def test_cleanup_failure_blocks_further_effects_in_that_session():
    calls = []

    class Provider:
        async def next_step(self, context):
            calls.append(context.message)
            return FinalAnswer("ok")

    async def cleanup(context):
        raise RuntimeError("resource release failed")

    runtime = AgentRuntime(Provider(), cleanup=cleanup)
    assert (await settled(runtime.start("one", "first"))).error == "cleanup_failed"
    assert (await settled(runtime.start("one", "second"))).error == "session_cleanup_failed"
    assert calls == ["first"]


def test_impossible_verification_and_ambiguous_tool_registry_are_rejected():
    with pytest.raises(ValueError, match="verification"):
        ToolResult("fixture_read", succeeded=False, verified=True)
    with pytest.raises(ValueError, match="boolean"):
        ToolResult("fixture_read", succeeded=True, verified="false")
    with pytest.raises(ValueError, match="uncertain"):
        ToolResult("fixture_read", succeeded=True, verified=False, effect_status="unknown")

    async def execute(arguments, cancellation):
        return ToolResult("fixture_read", True, True)

    tool = ToolDefinition("fixture_read", validate_text, execute)
    with pytest.raises(ValueError, match="duplicate"):
        AgentRuntime(ToolThenAnswer(), [tool, tool])


@pytest.mark.parametrize("interruption", [
    "cooperative_cancel", "ignored_cancel", "exception_after_effect", "invalid_result",
])
async def test_interrupted_execution_preserves_uncertain_effect_without_retry(interruption):
    entered, release = asyncio.Event(), asyncio.Event()
    effects, proposals = [], []

    class Provider:
        async def next_step(self, context):
            proposals.append(context)
            return ToolProposal("fixture_effect", {"text": "A"})

    async def execute(arguments, cancellation):
        effects.append(arguments["text"])
        entered.set()
        try:
            await release.wait()
        except asyncio.CancelledError:
            if interruption == "ignored_cancel":
                return ToolResult("fixture_effect", succeeded=True, verified=True)
            raise
        if interruption == "exception_after_effect":
            raise RuntimeError("synthetic failure after the effect")
        return None  # Invalid post-effect output must retain uncertainty too.

    runtime = AgentRuntime(Provider(), [ToolDefinition("fixture_effect", validate_text, execute)])
    handle = runtime.start("one", "Prueba sintética")
    await observed(entered)
    if interruption in {"cooperative_cancel", "ignored_cancel"}:
        assert handle.cancel().accepted
    else:
        release.set()
    result = await settled(handle)
    assert result.status == ("cancelled" if "cancel" in interruption else "failed")
    assert result.answer is None and len(proposals) == 1 and effects == ["A"]
    assert result.answer_source is None
    assert len(result.tool_results) == 1
    recorded = result.tool_results[0]
    assert recorded.effect_status == "unknown" and not recorded.succeeded and not recorded.verified
    assert recorded.tool_name == "fixture_effect"
    assert recorded.operation_id


async def test_executor_reported_uncertainty_is_terminal_before_another_proposal():
    calls, effects = [], []

    class Provider:
        async def next_step(self, context):
            calls.append(context)
            return ToolProposal("fixture_effect", {"text": "A"})

    async def execute(arguments, cancellation):
        effects.append(arguments["text"])
        return ToolResult("fixture_effect", False, False, effect_status="unknown")

    runtime = AgentRuntime(Provider(), [ToolDefinition("fixture_effect", validate_text, execute)])
    result = await settled(runtime.start("one", "Prueba sintética"))
    assert result.error == "tool_outcome_unknown"
    assert len(calls) == len(effects) == 1
    assert result.tool_results[0].effect_status == "unknown"


@pytest.mark.parametrize("boundary", [
    "provider", "initial_validation", "final_validation", "approval", "tool", "cleanup",
])
async def test_error_types_are_preserved_without_exception_contents(boundary):
    validations = 0

    def fail():
        raise RuntimeError("secret-owner-content")

    class Provider:
        async def next_step(self, context):
            if boundary == "provider":
                fail()
            if boundary == "cleanup" or context.tool_results:
                return FinalAnswer("respuesta sintética")
            return ToolProposal("fixture_effect", {"text": "A"})

    class Gate:
        async def request(self, operation, cancellation):
            if boundary == "approval":
                fail()
            return ApprovalGrant(operation.digest)

    def validate(arguments):
        nonlocal validations
        validations += 1
        validate_text(arguments)
        if (
            boundary == "initial_validation"
            or (boundary == "final_validation" and validations == 2)
        ):
            fail()

    async def execute(arguments, cancellation):
        if boundary == "tool":
            fail()
        return ToolResult("fixture_effect", True, True)

    async def cleanup(context):
        if boundary == "cleanup":
            fail()

    runtime = AgentRuntime(Provider(), [
        ToolDefinition("fixture_effect", validate, execute, True),
    ], approval_gate=Gate(), cleanup=cleanup)
    result = await settled(runtime.start("one", "Prueba sintética de errores"))
    assert result.status == "failed" and result.error_type == "RuntimeError"
    assert "secret" not in json.dumps(asdict(result))
    if boundary == "tool":
        assert result.action_receipts[0].error_type == "RuntimeError"
        assert result.action_receipts[0].effect_status == "unknown"
    else:
        assert result.action_receipts == ()


def test_error_type_metadata_rejects_arbitrary_text():
    result = ToolResult("fixture_read", False, False, error_type="RuntimeError\nsecret")
    assert result.error_type == "UnknownError" and "secret" not in repr(result)
