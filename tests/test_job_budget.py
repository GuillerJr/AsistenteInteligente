import asyncio
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from pydantic import ValidationError
from test_jobs import ImmediateGraph, PendingNetworkGraph, _awaiting_confirmation, _terminal

from aegis_core.contracts import UserRequest
from aegis_core.job_contracts import JobConfirmationError, JobSnapshot, JobStatus
from aegis_core.job_state import MutableJob
from aegis_core.jobs import SwarmJobManager
from aegis_core.memory.conversations import ConversationCoordinator
from aegis_core.memory.sqlite import SQLiteMemoryStore
from aegis_core.tools.broker import PolicyContext
from aegis_core.tools.confirmations import OneTimeConfirmationStore
from aegis_core.tools.defaults import build_default_tool_broker, default_policy_context


def test_active_budget_is_monotonic_and_excludes_confirmation_wait():
    now = datetime.now(UTC)
    job = MutableJob(job_id=uuid4(), request_id=uuid4(), authorization_request=None,
                     request_text="Prueba", conversation_id=None, status=JobStatus.RUNNING,
                     created_at=now, updated_at=now, started_monotonic=0,
                     execution_budget_ms=120_000)
    assert job.remaining_execution_ms(7) == 113_000
    job.confirmation_started_monotonic = 7
    assert job.remaining_execution_ms(77) == 113_000
    job.finish_confirmation_wait(lambda: 77)
    assert job.remaining_execution_ms(87) == 103_000
    assert job.remaining_execution_ms(900) == 0


def test_snapshot_budget_requires_consistent_bounded_pair():
    now = datetime.now(UTC)
    fields = dict(job_id=uuid4(), request_id=uuid4(), status=JobStatus.RUNNING,
                  created_at=now, updated_at=now)
    assert JobSnapshot(**fields).execution_budget_ms is None
    for extra in ({"execution_budget_ms": 120_000},
                  {"remaining_execution_ms": 100},
                  {"execution_budget_ms": 120_000, "remaining_execution_ms": 120_001}):
        with pytest.raises(ValidationError):
            JobSnapshot(**fields, **extra)


async def test_context_preparation_is_inside_daemon_budget(tmp_path):
    class SlowContext(ConversationCoordinator):
        async def prepare_context(self, *args):
            await asyncio.sleep(10)
    store = SQLiteMemoryStore(tmp_path / "memory.db", encryption_secret=b"x" * 32)
    store.initialize()
    coordinator = SlowContext(store, namespace="owner")
    conversation = await coordinator.create()
    graph = ImmediateGraph()
    jobs = SwarmJobManager(graph, conversations=coordinator, execution_timeout_seconds=0.05)
    try:
        submitted = await jobs.submit(UserRequest(text="Prueba sintética"),
                                      conversation_id=conversation.conversation_id)
        failed = await _terminal(jobs, submitted.job_id)
        assert failed.error_code == "swarm_execution_timeout"
        assert not graph.inputs
        assert failed.execution_budget_ms == 50
        assert failed.remaining_execution_ms == 0
    finally:
        await jobs.close()


async def test_final_observers_are_inside_daemon_budget():
    async def observe(_):
        await asyncio.sleep(10)
    profile = SimpleNamespace(observe=AsyncMock(side_effect=observe))
    jobs = SwarmJobManager(ImmediateGraph(), owner_profile=profile,
                           execution_timeout_seconds=0.05)
    try:
        submitted = await jobs.submit(UserRequest(text="Prueba"))
        failed = await _terminal(jobs, submitted.job_id)
        assert failed.error_code == "swarm_execution_timeout"
        profile.observe.assert_awaited_once()
    finally:
        await jobs.close()


async def test_approved_tool_uses_remaining_budget_and_is_never_replayed(tmp_path):
    broker = build_default_tool_broker()
    base = default_policy_context(tmp_path)
    grants = OneTimeConfirmationStore()
    context = PolicyContext(workspace_root=tmp_path, network_scopes=base.network_scopes,
                            confirmation_store=grants)
    async def execute(*_):
        await asyncio.sleep(10)
    executor = type("Executor", (), {"execute_async": AsyncMock(side_effect=execute)})()
    jobs = SwarmJobManager(PendingNetworkGraph(broker, context), tool_broker=broker,
                           policy_context=context, confirmation_store=grants,
                           tool_executor=executor, execution_timeout_seconds=0.08)
    try:
        submitted = await jobs.submit(UserRequest(text="Consulta sintética"))
        pending = await _awaiting_confirmation(jobs, submitted.job_id)
        resumed = await jobs.approve(submitted.job_id, pending.confirmation.call_digest)
        assert 0 < resumed.remaining_execution_ms <= pending.remaining_execution_ms
        failed = await _terminal(jobs, submitted.job_id)
        assert failed.error_code == "swarm_execution_timeout"
        executor.execute_async.assert_awaited_once()
        with pytest.raises(JobConfirmationError):
            await jobs.approve(submitted.job_id, pending.confirmation.call_digest)
        executor.execute_async.assert_awaited_once()
    finally:
        await jobs.close()


async def test_expired_budget_joins_final_persistence_without_publishing_success(tmp_path):
    class SlowPersistence(ConversationCoordinator):
        async def record_exchange(self, *args, **kwargs):
            await asyncio.sleep(0.2)
            return await super().record_exchange(*args, **kwargs)

    store = SQLiteMemoryStore(tmp_path / "memory.db", encryption_secret=b"x" * 32)
    store.initialize()
    coordinator = SlowPersistence(store, namespace="owner")
    conversation = await coordinator.create()
    jobs = SwarmJobManager(ImmediateGraph(), conversations=coordinator,
                           execution_timeout_seconds=0.05)
    try:
        submitted = await jobs.submit(UserRequest(text="Prueba sintética"),
                                      conversation_id=conversation.conversation_id)
        failed = await _terminal(jobs, submitted.job_id)
        assert failed.status is JobStatus.FAILED
        assert failed.error_code == "swarm_execution_timeout"
        assert failed.result is None
        assert failed.remaining_execution_ms == 0
        # The transaction is joined safely; its successful commit is not an
        # authorization to publish a completed job after the execution deadline.
        assert len(await coordinator.history(conversation.conversation_id)) == 2
    finally:
        await jobs.close()


async def test_close_joins_final_persistence_before_releasing_ownership(tmp_path):
    class GatedPersistence(ConversationCoordinator):
        def __init__(self, store):
            super().__init__(store, namespace="owner")
            self.started = asyncio.Event()
            self.release = asyncio.Event()
            self.finished = False

        async def record_exchange(self, *args, **kwargs):
            self.started.set()
            await self.release.wait()
            result = await super().record_exchange(*args, **kwargs)
            self.finished = True
            return result

    store = SQLiteMemoryStore(tmp_path / "memory.db", encryption_secret=b"x" * 32)
    store.initialize()
    coordinator = GatedPersistence(store)
    conversation = await coordinator.create()
    jobs = SwarmJobManager(ImmediateGraph(), conversations=coordinator,
                           execution_timeout_seconds=0.05)
    closing = None
    try:
        submitted = await jobs.submit(UserRequest(text="Prueba sintética"),
                                      conversation_id=conversation.conversation_id)
        await coordinator.started.wait()
        closing = asyncio.create_task(jobs.close())
        await asyncio.sleep(0.08)  # The execution timeout also cancels the writer's owner.
        assert not closing.done()
        assert not coordinator.finished
        coordinator.release.set()
        await closing
        assert coordinator.finished
        assert (await jobs.status(submitted.job_id)).status is JobStatus.CANCELLED
        assert len(await coordinator.history(conversation.conversation_id)) == 2
    finally:
        coordinator.release.set()
        await jobs.close()
        if closing is not None:
            await closing
