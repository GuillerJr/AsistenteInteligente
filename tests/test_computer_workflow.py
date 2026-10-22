from __future__ import annotations

import asyncio
import hashlib
from pathlib import Path
from uuid import uuid4

import pytest
from historical.orchestration.graph import build_swarm_graph
from pydantic import ValidationError

from aegis_core.contracts import (
    AgentResult,
    AgentRole,
    Capability,
    InputModality,
    PolicyDecision,
    RiskLevel,
    ToolAuthorization,
    ToolCall,
    ToolExecutionResult,
    UserRequest,
)
from aegis_core.jobs import JobConfirmationError, JobStatus, SwarmJobManager
from aegis_core.mcp.client import McpArguments
from aegis_core.memory.conversations import ConversationCoordinator
from aegis_core.memory.sqlite import SQLiteMemoryStore
from aegis_core.memory.task_checkpoint import TaskRecovery
from aegis_core.tools.broker import PolicyContext, ToolDefinition
from aegis_core.tools.computer import ComputerUseReport
from aegis_core.tools.computer_postconditions import ComputerPostconditionCheck
from aegis_core.tools.computer_workflow import (
    ComputerWorkflowSpec,
    ComputerWorkflowStep,
    begin_workflow_step,
    create_workflow,
    next_pending_step,
    observe_workflow_step,
    recover_interrupted_workflow,
    validate_workflow_request,
    workflow_step_call,
)
from aegis_core.tools.confirmations import OneTimeConfirmationStore
from aegis_core.tools.defaults import build_default_tool_broker, default_policy_context

REQUEST = (
    "Controla visualmente: en TextEdit comprueba texto visible Informe; "
    "después en Safari abre https://example.org/informe"
)


def specification(text: str = REQUEST) -> ComputerWorkflowSpec:
    return ComputerWorkflowSpec(
        goal_sha256=hashlib.sha256(text.encode()).hexdigest(),
        steps=(
            ComputerWorkflowStep(
                ordinal=1,
                application_bundle_identifier="com.apple.TextEdit",
                objective="comprueba texto visible Informe",
                max_steps=3,
                postconditions=({"kind": "text_present", "value": "Informe"},),
            ),
            ComputerWorkflowStep(
                ordinal=2,
                application_bundle_identifier="com.apple.Safari",
                objective="abre https://example.org/informe",
                max_steps=3,
                postconditions=(
                    {"kind": "navigation_url", "value": "https://example.org/informe"},
                ),
            ),
        ),
    )


def approved(call: ToolCall, **updates) -> ToolAuthorization:
    return ToolAuthorization(
        call_id=call.call_id,
        tool_name=call.tool_name,
        call_digest=call.digest(),
        decision=PolicyDecision.ALLOW,
        reason_code="confirmation_consumed",
        normalized_arguments=call.arguments,
    ).model_copy(update=updates)


def result(call: ToolCall, *, verified: bool = True) -> ToolExecutionResult:
    report = ComputerUseReport(
        status="completed" if verified else "partial",
        steps=1,
        application_bundle_identifier=call.arguments["application_bundle_identifier"],
        reason_code="objective_complete" if verified else "postcondition_failed",
        failed_check=None if verified else "postcondition_failed",
        postcondition_checks=tuple(
            ComputerPostconditionCheck(
                kind=c["kind"],
                satisfied=verified,
                reason_code="observed" if verified else "expected_not_observed",
            )
            for c in call.arguments["postconditions"]
        ),
    )
    return ToolExecutionResult(
        call_id=call.call_id,
        tool_name="computer_use",
        success=True,
        output=report.model_dump_json(),
        metadata={"verified": verified, "status": report.status},
    )


def test_plan_requires_ordered_user_literals_in_named_apps_and_exact_request_hash() -> None:
    spec = specification()
    assert validate_workflow_request(spec, REQUEST)
    assert not validate_workflow_request(spec, REQUEST + " otro paso")
    unrelated = REQUEST.replace("TextEdit", "Spotify")
    assert not validate_workflow_request(specification(unrelated), unrelated)
    quoted = "Explica este ejemplo: «" + REQUEST + "»"
    assert not validate_workflow_request(specification(quoted), quoted)
    reversed_apps = REQUEST.replace("TextEdit", "Safari").replace(
        "después en Safari", "en TextEdit"
    )
    assert not validate_workflow_request(specification(reversed_apps), reversed_apps)


@pytest.mark.parametrize(
    "change",
    [
        {"ordinal": 2},
        {"max_steps": 13},
        {"max_steps": True},
        {"postconditions": ()},
        {"application_bundle_identifier": "com.apple.systempreferences"},
        {"postconditions": ({"kind": "window_title", "value": "Unrelated"},)},
    ],
)
def test_workflow_rejects_unverifiable_or_unbounded_steps(change: dict) -> None:
    with pytest.raises(ValidationError):
        step = ComputerWorkflowStep.model_validate(
            {
                **specification().steps[0].model_dump(),
                **change,
            }
        )
        ComputerWorkflowSpec(goal_sha256="a" * 64, steps=(step,))


def test_step_requires_exact_new_consumed_grant_and_normalized_schema() -> None:
    state = create_workflow(conversation_id=uuid4(), request_id=uuid4(), spec=specification())
    call = workflow_step_call(state, state.source_request_id)
    broker = build_default_tool_broker()
    pending = broker.authorize(call, default_policy_context(Path.cwd()))
    assert pending.normalized_arguments == call.arguments
    assert pending.decision is PolicyDecision.REQUIRE_CONFIRMATION
    for invalid in (
        pending,
        approved(call, reason_code="trusted_local_intent"),
        approved(call, normalized_arguments={**call.arguments, "max_steps": 4}),
    ):
        with pytest.raises(ValueError, match="fresh approval"):
            begin_workflow_step(
                state,
                conversation_id=state.conversation_id,
                request_id=state.source_request_id,
                authorization=invalid,
            )
    with pytest.raises(ValueError, match="conversation"):
        begin_workflow_step(
            state, conversation_id=uuid4(), request_id=uuid4(), authorization=approved(call)
        )
    running = begin_workflow_step(
        state,
        conversation_id=state.conversation_id,
        request_id=state.source_request_id,
        authorization=approved(call),
    )
    assert next_pending_step(running) is None
    completed = observe_workflow_step(running, result(call))
    assert next_pending_step(completed).ordinal == 2
    next_call = workflow_step_call(completed, state.source_request_id)
    assert next_call.call_id != call.call_id and next_call.digest() != call.digest()
    with pytest.raises(ValueError, match="fresh approval"):
        begin_workflow_step(
            completed,
            conversation_id=state.conversation_id,
            request_id=state.source_request_id,
            authorization=approved(call),
        )


@pytest.mark.parametrize("fault", ["partial", "foreign_id", "foreign_app", "invalid", "crash"])
def test_unknown_or_partial_step_never_proposes_following_action(fault: str) -> None:
    state = create_workflow(conversation_id=uuid4(), request_id=uuid4(), spec=specification())
    call = workflow_step_call(state, state.source_request_id)
    running = begin_workflow_step(
        state,
        conversation_id=state.conversation_id,
        request_id=state.source_request_id,
        authorization=approved(call),
    )
    observed = result(call, verified=fault != "partial")
    if fault == "foreign_id":
        observed = observed.model_copy(update={"call_id": "foreign"})
    elif fault == "foreign_app":
        observed = observed.model_copy(
            update={"output": observed.output.replace("com.apple.TextEdit", "com.apple.Safari")}
        )
    elif fault == "invalid":
        observed = observed.model_copy(update={"output": "{}"})
    stopped = (
        recover_interrupted_workflow(running)
        if fault == "crash"
        else observe_workflow_step(running, observed)
    )
    assert stopped.progress[0].status in {"partial", "intervention_required"}
    assert next_pending_step(stopped) is None
    assert next_pending_step(recover_interrupted_workflow(stopped)) is None


class PlanProvider:
    def __init__(self) -> None:
        self.calls = 0
        self.propose = True

    async def complete(self, **kwargs) -> AgentResult:
        self.calls += 1
        role = kwargs["role"]
        return AgentResult(
            role=role,
            model_id="synthetic/plan",
            content="Plan verificable",
            tool_calls=(
                ToolCall(
                    call_id="outer-plan",
                    tool_name="computer_use",
                    requested_by=AgentRole.PLANNER,
                    arguments={
                        **specification().steps[0].arguments(),
                        "workflow_steps": [
                            step.model_dump(mode="json") for step in specification().steps
                        ],
                    },
                ),
            )
            if role is AgentRole.PLANNER and self.propose
            else (),
        )


class ObservedExecutor:
    def __init__(self, conversations, conversation_id, *, partial=False, stall=False):
        self.calls = []
        self.conversations, self.conversation_id = conversations, conversation_id
        self.partial, self.stall = partial, stall
        self.started = asyncio.Event()

    async def execute_async(self, authorization, policy_context):
        del policy_context
        recovery = await self.conversations.recovery(self.conversation_id)
        assert recovery.computer_workflow.progress[len(self.calls)].status == "executing"
        assert (
            recovery.computer_workflow.progress[len(self.calls)].call_digest
            == authorization.call_digest
        )
        call = ToolCall(
            call_id=authorization.call_id,
            tool_name=authorization.tool_name,
            requested_by=AgentRole.PLANNER,
            arguments=authorization.normalized_arguments,
        )
        self.calls.append(call)
        self.started.set()
        if self.stall:
            await asyncio.Event().wait()
        return result(call, verified=not self.partial)


async def wait_status(jobs, job_id, expected, *, different_digest=None):
    for _ in range(1000):
        snapshot = await jobs.status(job_id)
        if snapshot.status in expected and (
            different_digest is None
            or snapshot.confirmation is None
            or snapshot.confirmation.call_digest != different_digest
        ):
            return snapshot
        await asyncio.sleep(0.002)
    raise AssertionError(f"job remained {snapshot.status}")


async def setup(tmp_path: Path, *, partial=False, stall=False):
    tmp_path.chmod(0o700)
    store = SQLiteMemoryStore(tmp_path / "memory.sqlite3", encryption_secret=b"s" * 32)
    store.initialize()
    conversations = ConversationCoordinator(store, namespace="user.default")
    conversation_id = (await conversations.create()).conversation_id
    broker, confirmations = build_default_tool_broker(), OneTimeConfirmationStore()
    base = default_policy_context(tmp_path)
    context = PolicyContext(
        workspace_root=tmp_path,
        network_scopes=base.network_scopes,
        confirmation_store=confirmations,
    )
    provider = PlanProvider()
    graph = build_swarm_graph(provider, tool_broker=broker, policy_context=context)
    executor = ObservedExecutor(conversations, conversation_id, partial=partial, stall=stall)
    jobs = SwarmJobManager(
        graph,
        conversations=conversations,
        tool_broker=broker,
        policy_context=context,
        confirmation_store=confirmations,
        tool_executor=executor,
    )
    return jobs, conversations, conversation_id, executor, provider, graph, store


@pytest.mark.asyncio
async def test_actual_graph_two_apps_requires_two_decisions_and_encrypted_progress(
    tmp_path,
) -> None:
    jobs, conversations, conversation_id, executor, provider, _, _ = await setup(tmp_path)
    try:
        submitted = await jobs.submit(UserRequest(text=REQUEST), conversation_id=conversation_id)
        first = await wait_status(jobs, submitted.job_id, {JobStatus.AWAITING_CONFIRMATION})
        assert first.confirmation.summary.startswith("Paso 1 de 2")
        assert executor.calls == []
        await jobs.approve(submitted.job_id, first.confirmation.call_digest)
        second = await wait_status(
            jobs,
            submitted.job_id,
            {JobStatus.AWAITING_CONFIRMATION},
            different_digest=first.confirmation.call_digest,
        )
        assert second.confirmation.summary.startswith("Paso 2 de 2")
        assert len(executor.calls) == 1
        with pytest.raises(JobConfirmationError):
            await jobs.approve(submitted.job_id, first.confirmation.call_digest)
        # A mismatched decision must not execute step two; it also must not clear
        # the current valid pending review (existing broker contract).
        assert len(executor.calls) == 1
        await jobs.approve(submitted.job_id, second.confirmation.call_digest)
        completed = await wait_status(
            jobs, submitted.job_id, {JobStatus.COMPLETED, JobStatus.FAILED}
        )
        assert completed.status is JobStatus.COMPLETED
        assert [call.arguments["application_bundle_identifier"] for call in executor.calls] == [
            "com.apple.TextEdit",
            "com.apple.Safari",
        ]
        journal = await conversations.recovery(conversation_id)
        assert [p.status for p in journal.computer_workflow.progress] == ["verified", "verified"]
        assert journal.status == "completed"
        assert "Informe" not in (tmp_path / "memory.sqlite3").read_bytes().decode(errors="ignore")
        assert provider.calls <= 3
    finally:
        await jobs.close()


@pytest.mark.asyncio
async def test_partial_write_halts_workflow_and_continue_cannot_replay(tmp_path) -> None:
    jobs, conversations, conversation_id, executor, provider, _, _ = await setup(
        tmp_path, partial=True
    )
    try:
        submitted = await jobs.submit(UserRequest(text=REQUEST), conversation_id=conversation_id)
        pending = await wait_status(jobs, submitted.job_id, {JobStatus.AWAITING_CONFIRMATION})
        await jobs.approve(submitted.job_id, pending.confirmation.call_digest)
        await wait_status(jobs, submitted.job_id, {JobStatus.COMPLETED, JobStatus.FAILED})
        assert len(executor.calls) == 1
        journal = await conversations.recovery(conversation_id)
        assert journal.computer_workflow.progress[0].status == "partial"
        before_calls = provider.calls
        continued = await jobs.submit(UserRequest(text="continúa"), conversation_id=conversation_id)
        done = await wait_status(jobs, continued.job_id, {JobStatus.COMPLETED, JobStatus.FAILED})
        assert done.status is JobStatus.COMPLETED and "detenido" in done.result
        assert len(executor.calls) == 1 and provider.calls == before_calls
    finally:
        await jobs.close()


@pytest.mark.asyncio
async def test_cancel_while_executing_records_intervention_and_never_replays(tmp_path) -> None:
    jobs, conversations, conversation_id, executor, _, _, _ = await setup(tmp_path, stall=True)
    try:
        submitted = await jobs.submit(UserRequest(text=REQUEST), conversation_id=conversation_id)
        pending = await wait_status(jobs, submitted.job_id, {JobStatus.AWAITING_CONFIRMATION})
        await jobs.approve(submitted.job_id, pending.confirmation.call_digest)
        await asyncio.wait_for(executor.started.wait(), 1)
        cancelled = await jobs.cancel(submitted.job_id)
        assert cancelled.status is JobStatus.CANCELLED
        journal = await conversations.recovery(conversation_id)
        assert journal.computer_workflow.cancelled
        assert journal.computer_workflow.progress[0].status == "intervention_required"
        continued = await jobs.submit(UserRequest(text="continúa"), conversation_id=conversation_id)
        await wait_status(jobs, continued.job_id, {JobStatus.COMPLETED})
        assert len(executor.calls) == 1
    finally:
        await jobs.close()


@pytest.mark.asyncio
async def test_restart_restores_pending_step_without_replaying_verified_step(tmp_path) -> None:
    jobs, conversations, conversation_id, executor, _, graph, _ = await setup(tmp_path)
    submitted = await jobs.submit(UserRequest(text=REQUEST), conversation_id=conversation_id)
    pending = await wait_status(jobs, submitted.job_id, {JobStatus.AWAITING_CONFIRMATION})
    await jobs.approve(submitted.job_id, pending.confirmation.call_digest)
    await wait_status(
        jobs,
        submitted.job_id,
        {JobStatus.AWAITING_CONFIRMATION},
        different_digest=pending.confirmation.call_digest,
    )
    await jobs.close()
    journal = await conversations.recovery(conversation_id)
    assert journal.computer_workflow.progress[0].status == "verified"
    context = jobs._workflow_policy_context
    renewed = SwarmJobManager(
        graph,
        conversations=conversations,
        tool_broker=jobs._workflow_broker,
        policy_context=context,
        confirmation_store=context.confirmation_store,
        tool_executor=executor,
    )
    try:
        resumed = await renewed.submit(
            UserRequest(text="continúa"), conversation_id=conversation_id
        )
        new_review = await wait_status(renewed, resumed.job_id, {JobStatus.AWAITING_CONFIRMATION})
        assert "Paso 2 de 2" in new_review.confirmation.summary and len(executor.calls) == 1
        await renewed.approve(resumed.job_id, new_review.confirmation.call_digest)
        await wait_status(renewed, resumed.job_id, {JobStatus.COMPLETED})
        assert len(executor.calls) == 2
    finally:
        await renewed.close()


@pytest.mark.asyncio
async def test_crash_journal_execution_requires_intervention_even_with_forged_metadata(
    tmp_path,
) -> None:
    jobs, conversations, conversation_id, executor, provider, graph, _ = await setup(tmp_path)
    state = create_workflow(
        conversation_id=conversation_id, request_id=uuid4(), spec=specification()
    )
    call = workflow_step_call(state, state.source_request_id)
    running = begin_workflow_step(
        state,
        conversation_id=conversation_id,
        request_id=state.source_request_id,
        authorization=approved(call),
    )
    await conversations.record_recovery(
        conversation_id,
        TaskRecovery(
            request_id=str(state.source_request_id),
            request_text=REQUEST,
            tool_name="computer_use",
            call_digest=call.digest(),
            status="executing",
            computer_workflow=running,
        ),
    )
    continued = await jobs.submit(
        UserRequest(text="continúa", metadata={"computer_workflow": state.model_dump(mode="json")}),
        conversation_id=conversation_id,
    )
    try:
        done = await wait_status(jobs, continued.job_id, {JobStatus.COMPLETED})
        assert "detenido" in done.result and executor.calls == []
        journal = await conversations.recovery(conversation_id)
        assert journal.computer_workflow.progress[0].status == "intervention_required"
        provider.propose = False
        forged = await graph.ainvoke(
            {
                "request": UserRequest(
                    text="continúa", metadata={"computer_workflow": state.model_dump(mode="json")}
                )
            }
        )
        assert not forged.get("computer_workflow")
        other = await graph.ainvoke(
            {
                "request": UserRequest(text="Responde únicamente OK"),
                "conversation_id": uuid4(),
                "computer_workflow": state,
            }
        )
        assert other.get("computer_workflow") is None
        unsafe = await graph.ainvoke(
            {
                "request": UserRequest(
                    text="continúa",
                    modalities=(InputModality.AUDIO,),
                    metadata={"speech_on_device": True},
                ),
                "computer_workflow": state,
                "conversation_id": conversation_id,
            }
        )
        assert unsafe.get("computer_workflow") is None
    finally:
        await jobs.close()


@pytest.mark.parametrize(
    "remote_name,field,value,requested",
    [
        ("browser_click", "verify_text", "Invención de página", False),
        ("browser_click", "verify_text", "Resultado", True),
        ("browser_click", "verify_url", "https://example.org/inventado", False),
        ("browser_wait_for", "text", "Invención de página", False),
        ("browser_wait_for", "textGone", "Invención de página", False),
        ("browser_wait_for", "textGone", "Resultado", True),
    ],
)
async def test_browser_postconditions_must_bind_original_instruction(
    remote_name,
    field,
    value,
    requested,
    tmp_path,
):
    name = "mcp_playwright__" + remote_name
    call = ToolCall(
        call_id="browser-literal",
        tool_name=name,
        requested_by=AgentRole.PLANNER,
        arguments={field: value},
    )

    class BrowserProvider:
        async def complete(self, **kwargs):
            role = kwargs["role"]
            return AgentResult(
                role=role,
                model_id="synthetic/browser",
                content="Consulta",
                tool_calls=(call,) if role is AgentRole.PLANNER else (),
            )

    definition = ToolDefinition(
        name=name,
        description="Synthetic reviewed browser boundary",
        arguments_model=McpArguments,
        capability=Capability.APPLICATION_CONTROL,
        risk=RiskLevel.CRITICAL,
        allowed_roles=frozenset({AgentRole.PLANNER}),
        requires_confirmation=True,
        parameters_schema={
            "type": "object",
            "properties": {field: {"type": "string"}},
            "required": [field],
            "additionalProperties": False,
        },
    )
    graph = build_swarm_graph(
        BrowserProvider(),
        tool_broker=build_default_tool_broker((definition,)),
        policy_context=default_policy_context(tmp_path),
        dynamic_tool_names=lambda request: frozenset({name}),
    )
    state = await graph.ainvoke(
        {
            "request": UserRequest(
                text="Controla el navegador con Playwright y verifica el texto Resultado"
            )
        }
    )
    authorization = state["tool_authorizations"][0]
    assert authorization.decision is (
        PolicyDecision.REQUIRE_CONFIRMATION if requested else PolicyDecision.DENY
    )
    if not requested:
        assert authorization.reason_code == "browser_postcondition_not_requested"
    assert state.get("tool_results", ()) == ()
