"""Signed app grants survive incomplete vision through the real computer controller."""

from __future__ import annotations

import asyncio
import sqlite3
from dataclasses import replace
from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest
from pydantic import ValidationError
from test_computer_app_grants_ipc import BUNDLE, REQUEST, NoUnapprovedExecution
from test_computer_app_grants_ipc import app_grant_ipc as app_grant_ipc
from test_computer_textedit_request import SyntheticTextEditBridge, SyntheticVision

from aegis_core.approval_signing import PinnedApprovalSigner
from aegis_core.computer_app_grants import ComputerAppGrantStore
from aegis_core.contracts import AgentRole, UserRequest
from aegis_core.job_contracts import JobStatus
from aegis_core.job_ipc import SwarmIpcService
from aegis_core.jobs import SwarmJobManager
from aegis_core.memory.conversations import ConversationCoordinator, ConversationIpcService
from aegis_core.memory.errors import MemoryStoreError
from aegis_core.memory.sqlite import SQLiteMemoryStore
from aegis_core.memory.task_checkpoint import TaskRecovery, VerifiedTaskResult
from aegis_core.orchestration.graph import build_nvidia_swarm_graph
from aegis_core.providers.base import IncompleteModelResponseError
from aegis_core.tools.background_automation import QuietActionVerification
from aegis_core.tools.broker import PolicyContext
from aegis_core.tools.computer import ComputerAction, ComputerUseController, ComputerUseError
from aegis_core.tools.confirmations import OneTimeConfirmationStore
from aegis_core.tools.defaults import build_default_tool_broker
from aegis_core.tools.execution import ReadOnlyToolExecutor


class IncompleteFirstVision(SyntheticVision):
    def __init__(self, literal="PRUEBA JARVIS B", *, prefix_wait=False):
        super().__init__({"action": "type", "text": literal})
        self.prefix_wait = prefix_wait

    async def complete(self, **kwargs):
        if not self.requests and self.prefix_wait:
            next_action = self.action
            self.action = {"action": "wait", "duration_ms": 100}
            result = await super().complete(**kwargs)
            self.action = next_action
            return result
        if len(self.requests) == int(self.prefix_wait):
            assert kwargs["role"] is AgentRole.VISION
            self.requests.append(kwargs)
            raise IncompleteModelResponseError("synthetic incomplete vision response")
        return await super().complete(**kwargs)


@pytest.mark.parametrize("same_request,prefix_wait", [(False, False), (True, False), (True, True)])
async def test_signed_chat_grant_survives_incomplete_vision_before_first_action(
    app_grant_ipc, monkeypatch, same_request, prefix_wait,
):
    ipc = app_grant_ipc
    bridge = SyntheticTextEditBridge()
    literal = "PRUEBA JARVIS A" if same_request else "PRUEBA JARVIS B"
    vision = IncompleteFirstVision(literal, prefix_wait=prefix_wait)
    executor = ReadOnlyToolExecutor(computer_controller=ComputerUseController(
        vision, bridge, settle_seconds=0, timeout_seconds=2,
    ))
    # Keep the real graph, signer, authorization runtime and controller. Only AX
    # and NVIDIA responses are synthetic; no app, socket or owner data is accessed.
    monkeypatch.setattr(ipc.executor, "execute_async", executor.execute_async)
    conversation = await ipc.create_temporary()
    first = await ipc.submit(conversation)
    pending = await ipc.status_until(first, JobStatus.AWAITING_CONFIRMATION)
    assert pending.confirmation.app_grant_bundle == BUNDLE
    assert vision.requests == [] and bridge.actions == []

    approved = await ipc.approve(first, pending.confirmation.call_digest, scope="conversation")

    assert approved.ok and approved.payload["status"] == "running"
    failed = await ipc.status_until(first, JobStatus.FAILED)
    assert failed.error_code == "incomplete_model_response"
    assert len(vision.requests) == 1 + int(prefix_wait)
    assert bridge.actions == [] and bridge.value == ""
    assert ipc.grants.allows(BUNDLE, conversation)
    assert ipc.grants.entries() == ()
    recovery = await ipc.coordinator.recovery(conversation)
    assert recovery.status == "not_dispatched" and recovery.observation is None

    ipc.planner.literal = literal
    next_request = REQUEST.replace("PRUEBA JARVIS A", ipc.planner.literal)
    second = await ipc.submit(conversation, next_request)
    completed = await ipc.status_until(second, JobStatus.COMPLETED)
    assert completed.evaluation.outcome_verified
    assert bridge.actions == [ComputerAction(action="type", text=ipc.planner.literal)]
    assert bridge.value == ipc.planner.literal
    assert len(vision.requests) == 2 + int(prefix_wait)
    assert ipc.grants.allows(BUNDLE, conversation)
    assert await ipc.coordinator.is_temporary(conversation)

    other = await ipc.create_temporary()
    third = await ipc.submit(other, next_request)
    awaiting = await ipc.status_until(third, JobStatus.AWAITING_CONFIRMATION)
    assert awaiting.confirmation.app_grant_bundle == BUNDLE
    assert not ipc.grants.allows(BUNDLE, other)
    assert len(vision.requests) == 2 + int(prefix_wait)
    assert bridge.actions == [ComputerAction(action="type", text=ipc.planner.literal)]
    events = [event.event_type for event in ipc.audit.verify()]
    assert events.count("computer_app_grant_created") == 1
    assert events.count("computer_app_grant_used") == 1


class FirstDispatchBridge(SyntheticTextEditBridge):
    def __init__(self, *, stale):
        super().__init__()
        self.stale = stale

    def act(self, action, *args):
        self.actions.append(action)
        if self.stale:
            raise ComputerUseError("computer_observation_changed")
        return QuietActionVerification(
            pathway="accessibility", before_sha256="0" * 64, after_sha256="f" * 64,
            state_changed=True, verified=True,
        )


class IncompleteAfterDispatchVision(SyntheticVision):
    def __init__(self):
        super().__init__({"action": "key", "key": "right", "modifiers": []})

    async def complete(self, **kwargs):
        if not self.requests:
            return await super().complete(**kwargs)
        self.requests.append(kwargs)
        raise IncompleteModelResponseError("synthetic incomplete after dispatch")


def install_controller(ipc, monkeypatch, bridge, vision):
    executor = ReadOnlyToolExecutor(computer_controller=ComputerUseController(
        vision, bridge, settle_seconds=0, timeout_seconds=2,
    ))
    monkeypatch.setattr(ipc.executor, "execute_async", executor.execute_async)


async def signed_start(ipc, conversation):
    job = await ipc.submit(conversation)
    pending = await ipc.status_until(job, JobStatus.AWAITING_CONFIRMATION)
    approved = await ipc.approve(job, pending.confirmation.call_digest, scope="conversation")
    assert approved.ok and approved.payload["status"] == "running"
    return job


@pytest.mark.parametrize("stale", [False, True])
async def test_incomplete_vision_after_any_dispatch_never_allows_same_request_replay(
    app_grant_ipc, monkeypatch, stale,
):
    ipc = app_grant_ipc
    bridge, vision = FirstDispatchBridge(stale=stale), IncompleteAfterDispatchVision()
    install_controller(ipc, monkeypatch, bridge, vision)
    conversation = await ipc.create_temporary()
    first = await signed_start(ipc, conversation)
    await ipc.status_until(first, JobStatus.FAILED if stale else JobStatus.COMPLETED)
    recovery = await ipc.coordinator.recovery(conversation)
    assert recovery.status == ("executing" if stale else "observed")
    assert recovery.observation is None
    assert len(bridge.actions) == 1 and len(vision.requests) == 2

    repeated = await ipc.submit(conversation)
    completed = await ipc.status_until(repeated, JobStatus.COMPLETED)
    assert "Podría haberse ejecutado" in completed.result
    assert len(bridge.actions) == 1 and len(vision.requests) == 2


class WriteWithoutPostconditionEvidence(SyntheticTextEditBridge):
    def capture(self, expected_bundle_identifier):
        observation = super().capture(expected_bundle_identifier)
        if self.actions:
            item = observation.perception.items[0].model_copy(update={
                "text": "First Text View área de texto", "value": None,
            })
            return observation.model_copy(update={
                "perception": observation.perception.model_copy(update={"items": (item,)}),
            })
        return observation


async def test_completed_partial_explanation_cannot_make_an_unverified_write_replayable(
    app_grant_ipc, monkeypatch,
):
    ipc = app_grant_ipc
    bridge = WriteWithoutPostconditionEvidence()
    vision = SyntheticVision({"action": "type", "text": "PRUEBA JARVIS A"})
    install_controller(ipc, monkeypatch, bridge, vision)
    conversation = await ipc.create_temporary()
    first = await signed_start(ipc, conversation)
    completed = await ipc.status_until(first, JobStatus.COMPLETED)
    assert not completed.evaluation.outcome_verified
    assert bridge.value == "PRUEBA JARVIS A"
    assert (await ipc.coordinator.recovery(conversation)).status == "observed"

    repeated = await ipc.submit(conversation)
    stopped = await ipc.status_until(repeated, JobStatus.COMPLETED)
    assert "Podría haberse ejecutado" in stopped.result
    assert bridge.value == "PRUEBA JARVIS A"
    assert len(bridge.actions) == 1 and len(vision.requests) == 1


class CancelledVision(SyntheticVision):
    def __init__(self):
        super().__init__({})
        self.started = asyncio.Event()

    async def complete(self, **kwargs):
        self.requests.append(kwargs)
        self.started.set()
        await asyncio.Event().wait()


async def test_cancellation_before_dispatch_is_not_a_completed_no_dispatch_observation(
    app_grant_ipc, monkeypatch,
):
    ipc = app_grant_ipc
    bridge, vision = SyntheticTextEditBridge(), CancelledVision()
    install_controller(ipc, monkeypatch, bridge, vision)
    conversation = await ipc.create_temporary()
    first = await signed_start(ipc, conversation)
    await asyncio.wait_for(vision.started.wait(), timeout=2)
    cancelled = await ipc.call("jobs.cancel", {"job_id": str(first)})
    assert cancelled.ok and cancelled.payload["status"] == "cancelled"
    assert (await ipc.coordinator.recovery(conversation)).status == "executing"

    repeated = await ipc.submit(conversation)
    completed = await ipc.status_until(repeated, JobStatus.COMPLETED)
    assert "Podría haberse ejecutado" in completed.result
    assert len(vision.requests) == 1 and bridge.actions == []


@pytest.mark.parametrize("error_type", [OSError, sqlite3.OperationalError, MemoryStoreError])
async def test_failed_no_dispatch_persistence_finishes_job_and_keeps_replay_blocked(
    app_grant_ipc, monkeypatch, caplog, error_type,
):
    ipc = app_grant_ipc
    bridge, vision = SyntheticTextEditBridge(), IncompleteFirstVision()
    install_controller(ipc, monkeypatch, bridge, vision)
    record = ipc.coordinator.record_recovery
    private_error = "SYNTHETIC_PRIVATE_PERSISTENCE_DETAIL"

    async def fail_marker(conversation_id, recovery, **kwargs):
        if recovery.status == "not_dispatched":
            raise error_type(private_error)
        await record(conversation_id, recovery, **kwargs)

    monkeypatch.setattr(ipc.coordinator, "record_recovery", fail_marker)
    conversation = await ipc.create_temporary()
    first = await signed_start(ipc, conversation)
    failed = await ipc.status_until(first, JobStatus.FAILED)
    assert failed.error_code == "conversation_unavailable"
    assert ipc.jobs._tasks.tracked_count == 0
    assert (await ipc.coordinator.recovery(conversation)).status == "executing"
    assert bridge.actions == [] and len(vision.requests) == 1
    assert "computer_recovery_persistence_failed" in caplog.text
    assert private_error not in caplog.text

    repeated = await ipc.submit(conversation)
    stopped = await ipc.status_until(repeated, JobStatus.COMPLETED)
    assert "Podría haberse ejecutado" in stopped.result
    assert bridge.actions == [] and len(vision.requests) == 1


@pytest.mark.parametrize("changed", ["request_id", "call_digest", "observed", "completed"])
async def test_late_no_dispatch_evidence_cannot_replace_another_recovery(
    app_grant_ipc, changed,
):
    ipc = app_grant_ipc
    conversation = await ipc.create_temporary()
    original = TaskRecovery(
        request_id=str(uuid4()), request_text=REQUEST, tool_name="computer_use",
        call_digest="a" * 64, status="executing",
    )
    newer = original.model_copy(update=(
        {changed: str(uuid4()) if changed == "request_id" else "b" * 64}
        if changed in {"request_id", "call_digest"} else {"status": changed}
    ))
    await ipc.coordinator.record_recovery(conversation, newer)
    await ipc.coordinator.record_recovery(
        conversation, original.model_copy(update={"status": "not_dispatched"}),
    )
    assert await ipc.coordinator.recovery(conversation) == newer


def restarted_ipc(ipc, tmp_path, executor):
    memory = SQLiteMemoryStore(tmp_path / "memory.db", encryption_secret=b"x" * 32)
    memory.initialize()
    coordinator = ConversationCoordinator(memory, namespace="synthetic")
    broker, confirmations = build_default_tool_broker(), OneTimeConfirmationStore()
    context = PolicyContext(workspace_root=tmp_path, confirmation_store=confirmations)
    grants = ComputerAppGrantStore(tmp_path / "app_grants.json")
    graph = build_nvidia_swarm_graph(
        ipc.planner, tool_broker=broker, policy_context=context,
        tool_executor=NoUnapprovedExecution(),
    )
    jobs = SwarmJobManager(
        graph, conversations=coordinator, tool_broker=broker, policy_context=context,
        confirmation_store=confirmations, tool_executor=executor,
        audit_sink=ipc.audit, app_grants=grants,
    )
    return replace(
        ipc, coordinator=coordinator, conversations=ConversationIpcService(memory, coordinator),
        jobs=jobs, executor=executor, grants=grants,
        service=SwarmIpcService(
            jobs, approval_signer=PinnedApprovalSigner(tmp_path / "signer.pub"),
        ),
    )


@pytest.mark.parametrize("trusted_no_dispatch", [False, True])
async def test_restart_uses_only_persisted_no_dispatch_evidence_and_fresh_approval(
    app_grant_ipc, tmp_path, monkeypatch, trusted_no_dispatch,
):
    ipc = app_grant_ipc
    # Temporary conversations are discarded on restart; use a synthetic saved chat.
    created = await ipc.call("conversations.create", {"title": "Prueba de recuperación"})
    assert created.ok
    conversation = UUID(created.payload["conversation_id"])
    bridge = SyntheticTextEditBridge()
    first_vision = IncompleteFirstVision() if trusted_no_dispatch else CancelledVision()
    install_controller(ipc, monkeypatch, bridge, first_vision)
    first = await signed_start(ipc, conversation)
    if trusted_no_dispatch:
        await ipc.status_until(first, JobStatus.FAILED)
    else:
        await asyncio.wait_for(first_vision.started.wait(), timeout=2)
        assert (await ipc.call("jobs.cancel", {"job_id": str(first)})).ok
    assert bridge.actions == [] and ipc.grants.allows(BUNDLE, conversation)
    persisted = await ipc.coordinator.recovery(conversation)
    await ipc.jobs.close()

    vision = SyntheticVision({"action": "type", "text": "PRUEBA JARVIS A"})
    executor = ReadOnlyToolExecutor(computer_controller=ComputerUseController(
        vision, bridge, settle_seconds=0, timeout_seconds=2,
    ))
    restarted = restarted_ipc(ipc, tmp_path, executor)
    try:
        assert await restarted.coordinator.recovery(conversation) == persisted
        assert not restarted.grants.allows(BUNDLE, conversation)
        response = await restarted.call("swarm.submit", {
            "text": REQUEST, "conversation_id": str(conversation),
            "metadata": {"task_recovery": persisted.model_copy(
                update={"status": "not_dispatched"},
            ).model_dump(mode="json")},
        })
        assert not response.ok and response.error_code == "invalid_payload"
        assert await restarted.coordinator.recovery(conversation) == persisted
        repeated = await restarted.submit(conversation)
        if trusted_no_dispatch:
            awaiting = await restarted.status_until(repeated, JobStatus.AWAITING_CONFIRMATION)
            assert bridge.actions == [] and vision.requests == []
            approved = await restarted.approve(
                repeated, awaiting.confirmation.call_digest, scope="conversation",
            )
            assert approved.ok
            completed = await restarted.status_until(repeated, JobStatus.COMPLETED)
            assert completed.evaluation.outcome_verified
            assert bridge.actions == [ComputerAction(action="type", text="PRUEBA JARVIS A")]
        else:
            completed = await restarted.status_until(repeated, JobStatus.COMPLETED)
            assert "Podría haberse ejecutado" in completed.result
            assert bridge.actions == [] and vision.requests == []
    finally:
        await restarted.jobs.close()


@pytest.mark.parametrize("invalid", ["another_tool", "observation", "workflow"])
def test_no_dispatch_marker_cannot_certify_another_tool_result_or_workflow(invalid):
    from test_computer_workflow import specification

    from aegis_core.tools.computer_workflow import create_workflow

    recovery = {
        "request_id": str(uuid4()), "request_text": REQUEST, "tool_name": "computer_use",
        "call_digest": "a" * 64, "status": "not_dispatched",
    }
    if invalid == "another_tool":
        recovery["tool_name"] = "run_jxa"
    elif invalid == "observation":
        recovery["observation"] = VerifiedTaskResult(
            tool_name="computer_use", call_id="synthetic", observed_at=datetime.now(UTC),
            excerpt="Resultado sintético",
        )
    else:
        recovery["computer_workflow"] = create_workflow(
            spec=specification(), conversation_id=uuid4(), request_id=uuid4(),
        )
    with pytest.raises(ValidationError, match="limited to a single computer action"):
        TaskRecovery.model_validate(recovery)


async def test_single_action_evidence_cannot_restart_a_multi_app_workflow(tmp_path, monkeypatch):
    from test_computer_workflow import REQUEST as WORKFLOW_REQUEST
    from test_computer_workflow import setup, wait_status

    jobs, conversations, conversation_id, proxy, planner, _, _ = await setup(tmp_path)
    bridge, vision = SyntheticTextEditBridge(), IncompleteFirstVision()
    executor = ReadOnlyToolExecutor(computer_controller=ComputerUseController(
        vision, bridge, settle_seconds=0, timeout_seconds=2,
    ))
    monkeypatch.setattr(proxy, "execute_async", executor.execute_async)
    try:
        first = await jobs.submit(UserRequest(text=WORKFLOW_REQUEST),
                                  conversation_id=conversation_id)
        pending = await wait_status(jobs, first.job_id, {JobStatus.AWAITING_CONFIRMATION})
        await jobs.approve(first.job_id, pending.confirmation.call_digest)
        failed = await wait_status(jobs, first.job_id, {JobStatus.FAILED, JobStatus.COMPLETED})
        assert failed.status is JobStatus.FAILED
        assert failed.error_code == "incomplete_model_response"
        journal = await conversations.recovery(conversation_id)
        assert journal.status == "observed" and journal.observation is None
        assert journal.computer_workflow.progress[0].status == "intervention_required"
        assert journal.computer_workflow.progress[1].status == "pending"
        assert bridge.actions == [] and len(vision.requests) == 1
        planned = planner.calls

        repeated = await jobs.submit(UserRequest(text="continúa"), conversation_id=conversation_id)
        stopped = await wait_status(jobs, repeated.job_id, {JobStatus.COMPLETED, JobStatus.FAILED})
        assert stopped.status is JobStatus.COMPLETED and "detenido" in stopped.result
        assert bridge.actions == [] and len(vision.requests) == 1 and planner.calls == planned
    finally:
        await jobs.close()
