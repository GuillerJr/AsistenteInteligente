"""Owned synthetic jobs exercise stop/delete boundaries without live applications."""

import asyncio
import json
from datetime import UTC, datetime
from threading import Event
from types import SimpleNamespace
from uuid import uuid4

import pytest

from aegis_core.contracts import ToolAuthorization
from aegis_core.goals import (
    Goal,
    GoalIpcService,
    GoalRunner,
    GoalStatus,
    GoalStore,
    GoalToolHandlers,
    StepVerdict,
)
from aegis_core.job_contracts import JobStatus


def request(method, goal_id):
    return SimpleNamespace(method=method, payload={"goal_id": str(goal_id)})


class Jobs:
    def __init__(self, *, pending=False, admission_paused=False):
        self.pending = pending
        self.submitted = []
        self.cancelled = []
        self.admitted = asyncio.Event()
        self.release_admission = asyncio.Event()
        if not admission_paused:
            self.release_admission.set()
        self.waiting = asyncio.Event()
        self.cancelling = asyncio.Event()
        self.release_cancel = asyncio.Event()
        self.release_cancel.set()
        self.cancel_fails = False

    async def submit(self, value, **_):
        self.submitted.append(value)
        self.admitted.set()
        await self.release_admission.wait()
        return SimpleNamespace(
            job_id=uuid4(), status=JobStatus.AWAITING_CONFIRMATION if self.pending
            else JobStatus.RUNNING, result=None, error_code=None, stream_version=1,
            confirmation=SimpleNamespace(tool_name="fixture"),
        )

    async def wait_for_change(self, *_args, **_kwargs):
        self.waiting.set()
        await asyncio.Event().wait()

    async def cancel(self, job_id):
        self.cancelling.set()
        await self.release_cancel.wait()
        if self.cancel_fails:
            raise OSError("synthetic cancellation failure")
        self.cancelled.append(job_id)


async def judge(_goal, _text):
    return StepVerdict(status="continue", note="Progreso sintético.")


def setup(tmp_path, jobs=None):
    tmp_path.chmod(0o700)
    store = GoalStore(tmp_path / "goals.json")
    now = datetime.now(UTC)
    goal = Goal(goal_id=uuid4(), title="Objetivo sintético", objective="Trabajo de prueba",
                created_at=now, updated_at=now)
    store.save(goal)
    jobs = jobs or Jobs()
    runner = GoalRunner(store, jobs=jobs, conversations=None, judge=judge, deliver=())
    return store, goal, jobs, runner, GoalIpcService(store, runner)


@pytest.mark.asyncio
@pytest.mark.parametrize("method", ["goals.stop", "goals.delete"])
@pytest.mark.parametrize("pending", [False, True])
async def test_stop_and_delete_drain_job_before_acknowledging(tmp_path, method, pending):
    jobs = Jobs(pending=pending)
    jobs.release_cancel.clear()
    store, goal, _, runner, service = setup(tmp_path, jobs)
    runner.start_due()
    await asyncio.wait_for((jobs.cancelling if pending else jobs.waiting).wait(), 1)
    operation = asyncio.create_task(service.handle(request(method, goal.goal_id)))
    await asyncio.wait_for(jobs.cancelling.wait(), 1)
    await asyncio.sleep(0)
    assert not operation.done()
    assert store.get(goal.goal_id).status is GoalStatus.PAUSED
    assert not runner.can_resume(goal.goal_id)
    jobs.release_cancel.set()
    response = await asyncio.wait_for(operation, 1)
    assert response.ok and len(jobs.cancelled) == 1
    assert runner.running == () and not runner._active_jobs
    if method == "goals.delete":
        assert response.payload == {"deleted": True}
        assert store.get(goal.goal_id) is None
    else:
        assert response.payload["goal"]["status"] == "paused"
        assert store.get(goal.goal_id).status is GoalStatus.PAUSED
    runner.start_due()
    assert runner.running == () and len(jobs.submitted) == 1


@pytest.mark.asyncio
async def test_chat_stop_uses_the_same_owned_cancellation(tmp_path):
    store, goal, jobs, runner, _ = setup(tmp_path)
    handlers = GoalToolHandlers(store)
    handlers.bind_runner(runner)
    runner.start_due()
    await asyncio.wait_for(jobs.waiting.wait(), 1)
    authorization = ToolAuthorization.model_construct(
        call_id="fixture", tool_name="jarvis_goal_stop",
        normalized_arguments={"title": "sintético"},
    )
    result = await handlers.handlers()["jarvis_goal_stop"](authorization, None)
    assert result.success and json.loads(result.output)["stopped"]
    assert len(jobs.cancelled) == 1 and not runner.running
    assert store.get(goal.goal_id).status is GoalStatus.PAUSED


@pytest.mark.asyncio
async def test_failed_cancellation_cannot_delete_or_resume_the_owned_job(tmp_path):
    store, goal, jobs, runner, service = setup(tmp_path)
    jobs.cancel_fails = True
    runner.start_due()
    await asyncio.wait_for(jobs.waiting.wait(), 1)
    for method in ("goals.stop", "goals.resume", "goals.delete"):
        result = await service.handle(request(method, goal.goal_id))
        assert not result.ok and result.error_code == "goal_cancellation_failed"
        assert store.get(goal.goal_id).status is GoalStatus.PAUSED
    assert len(jobs.submitted) == 1 and not jobs.cancelled
    jobs.cancel_fails = False
    removed = await service.handle(request("goals.delete", goal.goal_id))
    assert removed.ok and removed.payload == {"deleted": True}
    assert len(jobs.cancelled) == 1 and store.get(goal.goal_id) is None


@pytest.mark.asyncio
async def test_stop_owns_admission_until_the_late_job_id_can_be_cancelled(tmp_path):
    jobs = Jobs(admission_paused=True)
    _, goal, _, runner, service = setup(tmp_path, jobs)
    runner.start_due()
    await asyncio.wait_for(jobs.admitted.wait(), 1)
    operation = asyncio.create_task(service.handle(request("goals.stop", goal.goal_id)))
    await asyncio.sleep(0.02)
    assert not operation.done() and not jobs.cancelled
    jobs.release_admission.set()
    response = await asyncio.wait_for(operation, 1)
    assert response.ok and len(jobs.cancelled) == 1
    assert not jobs.waiting.is_set() and not runner.running


@pytest.mark.asyncio
async def test_slow_cancellation_returns_error_and_keeps_cleanup_owned(tmp_path, monkeypatch):
    monkeypatch.setattr("aegis_core.goals.GOAL_CANCELLATION_WAIT_SECONDS", 0.01)
    store, goal, jobs, runner, service = setup(tmp_path)
    jobs.release_cancel.clear()
    runner.start_due()
    await asyncio.wait_for(jobs.waiting.wait(), 1)
    response = await asyncio.wait_for(service.handle(request("goals.stop", goal.goal_id)), 1)
    assert not response.ok and response.error_code == "goal_cancellation_failed"
    assert store.get(goal.goal_id).status is GoalStatus.PAUSED and runner._drains
    resumed = await service.handle(request("goals.resume", goal.goal_id))
    assert not resumed.ok
    jobs.release_cancel.set()
    response = await asyncio.wait_for(service.handle(request("goals.stop", goal.goal_id)), 1)
    assert response.ok and len(jobs.cancelled) == 1
    assert not runner.running and not runner._drains


@pytest.mark.asyncio
async def test_concurrent_control_cannot_release_another_operation_barrier(tmp_path):
    store, goal, jobs, runner, service = setup(tmp_path)
    jobs.release_cancel.clear()
    runner.start_due()
    await asyncio.wait_for(jobs.waiting.wait(), 1)
    stopping = asyncio.create_task(service.handle(request("goals.stop", goal.goal_id)))
    await asyncio.wait_for(jobs.cancelling.wait(), 1)
    for method in ("goals.delete", "goals.resume", "goals.stop"):
        response = await service.handle(request(method, goal.goal_id))
        assert not response.ok and response.error_code == "goal_cancellation_failed"
        assert goal.goal_id in runner._stopping
    jobs.release_cancel.set()
    assert (await stopping).ok
    assert store.get(goal.goal_id).status is GoalStatus.PAUSED
    assert len(jobs.submitted) == 1 and len(jobs.cancelled) == 1


@pytest.mark.asyncio
async def test_finished_background_cancellation_allows_explicit_resume(tmp_path, monkeypatch):
    monkeypatch.setattr("aegis_core.goals.GOAL_CANCELLATION_WAIT_SECONDS", 0.01)
    _, goal, jobs, runner, service = setup(tmp_path)
    jobs.release_cancel.clear()
    runner.start_due()
    await asyncio.wait_for(jobs.waiting.wait(), 1)
    response = await service.handle(request("goals.stop", goal.goal_id))
    assert not response.ok
    cleanups = tuple(runner._drains.values())
    jobs.release_cancel.set()
    await asyncio.gather(*cleanups)
    assert not runner._drains and runner.can_resume(goal.goal_id)
    resumed = await service.handle(request("goals.resume", goal.goal_id))
    assert resumed.ok and resumed.payload["goal"]["status"] == "active"
    assert len(jobs.submitted) == 1


@pytest.mark.asyncio
async def test_storage_failure_during_stop_still_cancels_owned_job(tmp_path, monkeypatch):
    store, goal, jobs, runner, service = setup(tmp_path)
    runner.start_due()
    await asyncio.wait_for(jobs.waiting.wait(), 1)

    def failed_write(_goals):
        raise OSError("synthetic storage failure")

    original_write = store._write
    monkeypatch.setattr(store, "_write", failed_write)
    response = await service.handle(request("goals.stop", goal.goal_id))
    assert not response.ok and response.error_code == "goal_cancellation_failed"
    assert len(jobs.cancelled) == 1 and not runner._active_jobs and not runner.running
    assert store.get(goal.goal_id).status is GoalStatus.ACTIVE
    runner.start_due()
    assert not runner.running and await runner.step(goal.goal_id) is None
    assert len(jobs.submitted) == 1
    with pytest.raises(OSError):
        await service.handle(request("goals.resume", goal.goal_id))
    runner.start_due()
    assert not runner.running and await runner.step(goal.goal_id) is None
    monkeypatch.setattr(store, "_write", original_write)
    assert (await service.handle(request("goals.resume", goal.goal_id))).ok
    assert runner.stop_veto_token(goal.goal_id) is None
    runner.start_due()
    await asyncio.sleep(0.02)
    assert len(jobs.submitted) == 2
    await runner.close()


@pytest.mark.asyncio
async def test_older_resume_cannot_clear_newer_stop_veto(tmp_path, monkeypatch):
    store, goal, jobs, runner, service = setup(tmp_path)
    assert (await service.handle(request("goals.stop", goal.goal_id))).ok
    first_veto = runner.stop_veto_token(goal.goal_id)
    saved, release = Event(), Event()
    original = store.modify

    def delay_resume_response(goal_id, change):
        result = original(goal_id, change)
        if change.__name__ == "resume":
            saved.set()
            assert release.wait(2)
        return result

    monkeypatch.setattr(store, "modify", delay_resume_response)
    resuming = asyncio.create_task(service.handle(request("goals.resume", goal.goal_id)))
    assert await asyncio.to_thread(saved.wait, 1)
    assert (await service.handle(request("goals.stop", goal.goal_id))).ok
    newer_veto = runner.stop_veto_token(goal.goal_id)
    assert newer_veto is not first_veto
    release.set()
    assert (await resuming).ok
    assert runner.stop_veto_token(goal.goal_id) is newer_veto
    runner.start_due()
    assert not runner.running and not jobs.submitted
    assert store.get(goal.goal_id).status is GoalStatus.PAUSED


@pytest.mark.asyncio
@pytest.mark.parametrize("owner_change", ["delete", "pause"])
async def test_budget_transition_cannot_resurrect_or_unpause_goal(
    tmp_path, monkeypatch, owner_change,
):
    store, goal, jobs, runner, _ = setup(tmp_path)
    goal.steps_used = goal.max_steps
    store.save(goal)
    original = store.stop_at_budget

    def changed(goal_id, *, now):
        if owner_change == "delete":
            store.delete(goal_id)
        else:
            store.modify(goal_id, lambda current: setattr(current, "status", GoalStatus.PAUSED))
        return original(goal_id, now=now)

    monkeypatch.setattr(store, "stop_at_budget", changed)
    status = await runner.step(goal.goal_id)
    assert not jobs.submitted
    if owner_change == "delete":
        assert status is None and store.get(goal.goal_id) is None
    else:
        assert status is GoalStatus.PAUSED and store.get(goal.goal_id).status is GoalStatus.PAUSED


@pytest.mark.asyncio
async def test_direct_steps_share_capacity_and_shutdown_prevents_restart(tmp_path):
    store, goal, jobs, runner, _ = setup(tmp_path)
    step = asyncio.create_task(runner.step(goal.goal_id))
    await asyncio.wait_for(jobs.waiting.wait(), 1)
    assert await runner.step(goal.goal_id) is None
    await runner.close()
    with pytest.raises(asyncio.CancelledError):
        await step
    assert store.get(goal.goal_id).status is GoalStatus.PAUSED
    assert await runner.step(goal.goal_id) is None
    runner.start_due()
    assert not runner.running and len(jobs.submitted) == 1 and len(jobs.cancelled) == 1


def test_goal_store_does_not_expose_mutable_cached_state(tmp_path):
    store, goal, *_ = setup(tmp_path)
    goal.notes.append("No guardado")
    store.goals()[0].notes.append("Cambio externo")
    store.get(goal.goal_id).status = GoalStatus.COMPLETE
    current = store.get(goal.goal_id)
    assert not current.notes and current.status is GoalStatus.ACTIVE
