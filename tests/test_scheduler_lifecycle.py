"""Synthetic jobs prove cancellation, admission and delivery independently of NVIDIA."""

import asyncio
from datetime import UTC, datetime, timedelta
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
    StepVerdict,
)
from aegis_core.job_contracts import JobStatus
from aegis_core.scheduling import (
    DeliveryInbox,
    NotifyPolicy,
    Schedule,
    ScheduledTask,
    ScheduledTaskStore,
    ScheduleIpcService,
    ScheduleToolHandlers,
    TaskScheduler,
)
from aegis_core.settings_store import write_private_json


def request(method, **payload):
    return SimpleNamespace(method=method, payload=payload)


def task(*, once=False, **changes):
    now = datetime.now(UTC)
    values = dict(
        task_id=uuid4(), title="Prueba sintética", prompt="Acción de prueba",
        created_at=now, next_run_at=now - timedelta(seconds=1),
        schedule=Schedule(kind="once", at=now) if once else
        Schedule(kind="interval", every_minutes=30),
    )
    return ScheduledTask(**(values | changes))


class Jobs:
    def __init__(self, *, pending=False, completed=False):
        self.pending = pending
        self.completed = completed
        self.submitted = []
        self.cancelled = []
        self.waiting = asyncio.Event()
        self.cancelling = asyncio.Event()
        self.cancel_release = asyncio.Event()
        self.cancel_release.set()

    async def submit(self, request, **kwargs):
        self.submitted.append(request)
        return SimpleNamespace(
            job_id=uuid4(), status=JobStatus.COMPLETED if self.completed else
            JobStatus.AWAITING_CONFIRMATION if self.pending else JobStatus.RUNNING,
            result="Resultado sintético", error_code=None, stream_version=1,
            confirmation=SimpleNamespace(call_digest="a" * 64,
                                         expires_at=datetime.now(UTC) + timedelta(minutes=2))
            if self.pending else None,
        )

    async def wait_for_change(self, *args, **kwargs):
        self.waiting.set()
        await asyncio.Event().wait()

    async def cancel(self, job_id):
        self.cancelling.set()
        await self.cancel_release.wait()
        self.cancelled.append(job_id)


async def delivered(_task, _text):
    pass


@pytest.mark.asyncio
@pytest.mark.parametrize("pending", [False, True])
async def test_delete_settles_job_and_pending_approval_before_success(tmp_path, pending):
    store = ScheduledTaskStore(tmp_path / "tasks.json")
    scheduled = store.add(task())
    jobs = Jobs(pending=pending)
    jobs.cancel_release.clear()
    scheduler = TaskScheduler(store, jobs=jobs, conversations=None, deliver=(delivered,))
    service = ScheduleIpcService(store, scheduler, DeliveryInbox())
    assert scheduler.start_now(scheduled.task_id) == "started"
    await jobs.waiting.wait()
    deletion = asyncio.create_task(service.handle(request(
        "schedules.delete", task_id=str(scheduled.task_id))))
    await jobs.cancelling.wait()
    assert not deletion.done()
    assert scheduler.start_now(scheduled.task_id) == "unavailable"
    jobs.cancel_release.set()
    response = await deletion
    assert response.ok and response.payload == {"deleted": True}
    assert len(jobs.cancelled) == 1 and len(jobs.submitted) == 1
    assert store.get(scheduled.task_id) is None and scheduler.running == ()


@pytest.mark.asyncio
async def test_chat_delete_uses_same_cancellation_path(tmp_path):
    store = ScheduledTaskStore(tmp_path / "tasks.json")
    scheduled = store.add(task())
    jobs = Jobs(pending=True)
    scheduler = TaskScheduler(store, jobs=jobs, conversations=None, deliver=(delivered,))
    handlers = ScheduleToolHandlers(store)
    handlers.bind_scheduler(scheduler)
    scheduler.start_now(scheduled.task_id)
    await jobs.waiting.wait()
    authorization = ToolAuthorization.model_construct(
        call_id="fixture", tool_name="jarvis_schedule_delete",
        normalized_arguments={"title": "sintética"})
    result = await handlers.handlers()["jarvis_schedule_delete"](authorization, None)
    assert result.success and len(jobs.cancelled) == 1
    assert not scheduler.running and not store.tasks()


@pytest.mark.asyncio
async def test_delete_during_admission_owns_late_job_identity(tmp_path):
    store = ScheduledTaskStore(tmp_path / "tasks.json")
    scheduled = store.add(task())
    entered, release = asyncio.Event(), asyncio.Event()

    class SlowAdmission(Jobs):
        async def submit(self, request, **kwargs):
            snapshot = await super().submit(request, **kwargs)
            entered.set()
            await release.wait()
            return snapshot

    jobs = SlowAdmission(pending=True)
    scheduler = TaskScheduler(store, jobs=jobs, conversations=None, deliver=(delivered,))
    scheduler.start_now(scheduled.task_id)
    await entered.wait()
    deletion = asyncio.create_task(scheduler.delete(scheduled.task_id))
    await asyncio.sleep(0)
    assert not deletion.done()
    release.set()
    assert await deletion is not None
    assert len(jobs.cancelled) == 1 and len(jobs.submitted) == 1


@pytest.mark.asyncio
async def test_failed_job_cancellation_does_not_acknowledge_deletion(tmp_path):
    store = ScheduledTaskStore(tmp_path / "tasks.json")
    scheduled = store.add(task())

    class FailedCancellation(Jobs):
        async def cancel(self, job_id):
            raise OSError("synthetic cancellation unavailable")

    jobs = FailedCancellation()
    scheduler = TaskScheduler(store, jobs=jobs, conversations=None, deliver=())
    scheduler.start_now(scheduled.task_id)
    await jobs.waiting.wait()
    with pytest.raises(RuntimeError, match="could not be verified"):
        await scheduler.delete(scheduled.task_id)
    assert store.get(scheduled.task_id) is not None
    assert scheduler.start_now(scheduled.task_id) == "execution_uncertain"
    with pytest.raises(RuntimeError, match="could not be verified"):
        await scheduler.delete(scheduled.task_id)
    assert store.get(scheduled.task_id) is not None


@pytest.mark.asyncio
async def test_delete_timeout_reports_pending_and_keeps_job_owned(tmp_path, monkeypatch):
    import aegis_core.scheduling as scheduling

    monkeypatch.setattr(scheduling, "SCHEDULE_CANCEL_WAIT_SECONDS", 0.01)
    store = ScheduledTaskStore(tmp_path / "tasks.json")
    scheduled = store.add(task())
    jobs = Jobs()
    jobs.cancel_release.clear()
    scheduler = TaskScheduler(store, jobs=jobs, conversations=None, deliver=())
    service = ScheduleIpcService(store, scheduler, DeliveryInbox())
    scheduler.start_now(scheduled.task_id)
    await jobs.waiting.wait()
    result = await service.handle(request("schedules.delete", task_id=str(scheduled.task_id)))
    assert not result.ok and result.error_code == "schedule_cancel_pending"
    assert scheduled.task_id in scheduler.running and store.get(scheduled.task_id) is not None
    jobs.cancel_release.set()
    await asyncio.gather(*scheduler._running.values(), return_exceptions=True)
    removed = await service.handle(request("schedules.delete", task_id=str(scheduled.task_id)))
    assert removed.ok and removed.payload["deleted"] is True
    assert not scheduler.running and store.get(scheduled.task_id) is None


@pytest.mark.asyncio
async def test_manual_and_due_runs_share_capacity_and_ipc_reports_actual_start(tmp_path):
    store = ScheduledTaskStore(tmp_path / "tasks.json")
    tasks = [store.add(task()) for _ in range(3)]
    jobs = Jobs()
    scheduler = TaskScheduler(store, jobs=jobs, conversations=None, deliver=())
    service = ScheduleIpcService(store, scheduler, DeliveryInbox())
    first = await service.handle(request("schedules.run_now", task_id=str(tasks[0].task_id)))
    assert first.payload == {"started": True, "start_reason": "started"}
    duplicate = await service.handle(request("schedules.run_now", task_id=str(tasks[0].task_id)))
    assert duplicate.payload == {"started": False, "start_reason": "already_running"}
    await scheduler.run_due()
    assert len(scheduler.running) == 2
    rejected = await service.handle(request("schedules.run_now", task_id=str(tasks[2].task_id)))
    assert rejected.payload == {"started": False, "start_reason": "capacity"}
    await jobs.waiting.wait()
    await scheduler.close()
    assert 1 <= len(jobs.submitted) <= 2
    assert len(jobs.cancelled) == len(jobs.submitted)


@pytest.mark.asyncio
async def test_delivery_failure_survives_restart_without_reexecuting_action(tmp_path):
    path = tmp_path / "tasks.json"
    store = ScheduledTaskStore(path)
    scheduled = store.add(task(once=True, notify=NotifyPolicy.WHEN_NEW))
    jobs = Jobs(completed=True)

    async def offline(_task, _text):
        raise OSError("synthetic delivery failure")

    scheduler = TaskScheduler(store, jobs=jobs, conversations=None, deliver=(offline,))
    await scheduler.run_now(scheduled.task_id)
    current = store.get(scheduled.task_id)
    assert current.pending_delivery == "Resultado sintético"
    assert current.last_delivered_digest is None and current.last_delivered_excerpt is None
    assert current.last_status == "delivery_failed" and current.enabled
    assert path.stat().st_mode & 0o777 == 0o600
    await scheduler.close()

    received = []

    async def online(_task, text):
        received.append(text)

    restarted_store = ScheduledTaskStore(path)
    restarted_jobs = Jobs(completed=True)
    restarted = TaskScheduler(restarted_store, jobs=restarted_jobs, conversations=None,
                              deliver=(online,))
    await restarted.run_now(scheduled.task_id)
    restored = restarted_store.get(scheduled.task_id)
    assert len(jobs.submitted) == 1 and restarted_jobs.submitted == []
    assert received == ["⏰ Prueba sintética\n\nResultado sintético"]
    assert restored.pending_delivery is None and restored.last_delivered_digest
    assert restored.last_status == "completed" and not restored.enabled


@pytest.mark.asyncio
async def test_one_working_transport_acknowledges_result_without_duplicate_delivery(tmp_path):
    store = ScheduledTaskStore(tmp_path / "tasks.json")
    scheduled = store.add(task(notify=NotifyPolicy.WHEN_NEW))
    received = []

    async def offline(_task, _text):
        raise OSError("offline")

    async def online(_task, text):
        received.append(text)

    scheduler = TaskScheduler(store, jobs=Jobs(completed=True), conversations=None,
                              deliver=(offline, online))
    await scheduler.run_now(scheduled.task_id)
    await scheduler.run_now(scheduled.task_id)
    assert len(received) == 1 and store.get(scheduled.task_id).pending_delivery is None


@pytest.mark.asyncio
async def test_restart_after_outbox_commit_recovers_automatically_without_a_due_time(tmp_path):
    path = tmp_path / "tasks.json"
    store = ScheduledTaskStore(path)
    scheduled = store.add(task(once=True, next_run_at=None, run_in_progress=True,
                               pending_delivery="Resultado antes del cierre",
                               pending_delivery_status="completed",
                               pending_delivery_started_at=datetime.now(UTC)))
    received = []

    async def online(_task, text):
        received.append(text)

    jobs = Jobs(completed=True)
    restarted = TaskScheduler(ScheduledTaskStore(path), jobs=jobs, conversations=None,
                              deliver=(online,))
    await restarted.run_due()
    await asyncio.gather(*restarted._running.values())
    assert jobs.submitted == [] and received == [
        "⏰ Prueba sintética\n\nResultado antes del cierre"]
    assert ScheduledTaskStore(path).get(scheduled.task_id).pending_delivery is None


@pytest.mark.asyncio
async def test_outbox_write_failure_after_action_cannot_repeat_it_after_restart(
    tmp_path, monkeypatch,
):
    import aegis_core.scheduling as scheduling

    path = tmp_path / "tasks.json"
    store = ScheduledTaskStore(path)
    scheduled = store.add(task())
    original = scheduling.write_private_json

    def fail_outbox(path, value, **kwargs):
        if any(item.get("pending_delivery") for item in value["tasks"]):
            raise OSError("synthetic full disk after action")
        return original(path, value, **kwargs)

    monkeypatch.setattr(scheduling, "write_private_json", fail_outbox)
    jobs = Jobs(completed=True)
    scheduler = TaskScheduler(store, jobs=jobs, conversations=None, deliver=(delivered,))
    with pytest.raises(OSError):
        await scheduler.run_now(scheduled.task_id)
    restarted_store = ScheduledTaskStore(path)
    restarted = TaskScheduler(restarted_store, jobs=Jobs(completed=True), conversations=None,
                              deliver=(delivered,))
    assert len(jobs.submitted) == 1
    assert restarted.start_now(scheduled.task_id) == "execution_uncertain"
    await restarted.run_due()
    assert not restarted.running and restarted_store.get(scheduled.task_id).next_run_at is None


@pytest.mark.asyncio
async def test_owner_edits_and_pause_survive_inflight_delivery(tmp_path):
    store = ScheduledTaskStore(tmp_path / "tasks.json")
    scheduled = store.add(task())
    entered, release = asyncio.Event(), asyncio.Event()

    async def delivery(_task, _text):
        entered.set()
        await release.wait()

    scheduler = TaskScheduler(store, jobs=Jobs(completed=True), conversations=None,
                              deliver=(delivery,))
    service = ScheduleIpcService(store, scheduler, DeliveryInbox())
    runner = asyncio.create_task(scheduler.run_now(scheduled.task_id))
    await entered.wait()
    edited = await service.handle(request(
        "schedules.save", task_id=str(scheduled.task_id), title="Nuevo título",
        prompt="Nuevo trabajo", kind="interval", every_minutes=90))
    assert edited.ok and store.get(scheduled.task_id).pending_delivery == "Resultado sintético"
    paused = await service.handle(request(
        "schedules.set_enabled", task_id=str(scheduled.task_id), enabled=False))
    assert paused.ok
    release.set()
    await runner
    current = store.get(scheduled.task_id)
    assert current.title == "Nuevo título" and current.prompt == "Nuevo trabajo"
    assert current.schedule.every_minutes == 90 and not current.enabled
    assert current.pending_delivery is None and current.last_delivered_digest


@pytest.mark.asyncio
async def test_false_delivery_receipt_is_not_acknowledged(tmp_path):
    store = ScheduledTaskStore(tmp_path / "tasks.json")
    scheduled = store.add(task(once=True))

    async def refused(_task, _text):
        return False

    scheduler = TaskScheduler(store, jobs=Jobs(completed=True), conversations=None,
                              deliver=(refused,))
    await scheduler.run_now(scheduled.task_id)
    current = store.get(scheduled.task_id)
    assert current.last_status == "delivery_failed" and current.last_delivered_digest is None
    assert current.pending_delivery == "Resultado sintético"


@pytest.mark.asyncio
async def test_edit_to_future_once_during_run_preserves_new_schedule(tmp_path):
    store = ScheduledTaskStore(tmp_path / "tasks.json")
    scheduled = store.add(task())
    entered, release = asyncio.Event(), asyncio.Event()

    async def delivery(_task, _text):
        entered.set()
        await release.wait()

    scheduler = TaskScheduler(store, jobs=Jobs(completed=True), conversations=None,
                              deliver=(delivery,))
    service = ScheduleIpcService(store, scheduler, DeliveryInbox())
    runner = asyncio.create_task(scheduler.run_now(scheduled.task_id))
    await entered.wait()
    when = datetime.now(UTC) + timedelta(days=2)
    edited = await service.handle(request(
        "schedules.save", task_id=str(scheduled.task_id), title="Mañana",
        prompt="Otra acción", kind="once", at=when.isoformat()))
    assert edited.ok
    release.set()
    await runner
    current = store.get(scheduled.task_id)
    assert current.enabled and current.next_run_at == when


@pytest.mark.asyncio
async def test_outbox_retry_after_restart_preserves_edited_future_once(tmp_path):
    path = tmp_path / "tasks.json"
    store = ScheduledTaskStore(path)
    scheduled = store.add(task())
    entered, release = asyncio.Event(), asyncio.Event()

    async def offline(_task, _text):
        entered.set()
        await release.wait()
        return False

    jobs = Jobs(completed=True)
    scheduler = TaskScheduler(store, jobs=jobs, conversations=None, deliver=(offline,))
    service = ScheduleIpcService(store, scheduler, DeliveryInbox())
    runner = asyncio.create_task(scheduler.run_now(scheduled.task_id))
    await entered.wait()
    when = datetime.now(UTC) + timedelta(days=2)
    assert (await service.handle(request(
        "schedules.save", task_id=str(scheduled.task_id), title="Nuevo horario",
        prompt="Nueva acción", kind="once", at=when.isoformat()))).ok
    release.set()
    await runner
    restarted_store = ScheduledTaskStore(path)
    restarted_jobs = Jobs(completed=True)
    restarted = TaskScheduler(restarted_store, jobs=restarted_jobs, conversations=None,
                              deliver=(delivered,))
    await restarted.run_now(scheduled.task_id)
    current = restarted_store.get(scheduled.task_id)
    assert len(jobs.submitted) == 1 and restarted_jobs.submitted == []
    assert current.enabled and current.next_run_at == when
    assert current.title == "Nuevo horario" and current.pending_delivery is None


@pytest.mark.parametrize("key,store_type", [("tasks", ScheduledTaskStore), ("goals", GoalStore)])
@pytest.mark.parametrize("malformed", [None, {}, 1, True])
def test_invalid_collection_does_not_crash_background_runner(tmp_path, key, store_type, malformed):
    path = tmp_path / "state.json"
    write_private_json(path, {"version": 1, key: malformed})
    store = store_type(path)
    assert getattr(store, key)() == ()


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [GoalStatus.ACTIVE, GoalStatus.BUDGET_LIMITED])
async def test_goal_at_absolute_budget_never_submits_an_extra_step_or_disappears(tmp_path, status):
    path = tmp_path / "goals.json"
    store = GoalStore(path)
    now = datetime.now(UTC)
    goal = Goal(goal_id=uuid4(), title="Tope", objective="Fixture", status=status,
                max_steps=20, steps_used=20, created_at=now, updated_at=now)
    store.save(goal)
    jobs = Jobs(completed=True)

    async def judge(_goal, _text):
        pytest.fail("An exhausted goal must not reach inference")

    runner = GoalRunner(store, jobs=jobs, conversations=None, judge=judge, deliver=())
    resumed = await GoalIpcService(store, runner).handle(request(
        "goals.resume", goal_id=str(goal.goal_id)))
    assert not resumed.ok and resumed.error_code == "goal_budget_exhausted"
    await runner.step(goal.goal_id)
    assert jobs.submitted == []
    reloaded = GoalStore(path).get(goal.goal_id)
    assert reloaded is not None and reloaded.steps_used == 20
    assert reloaded.status is GoalStatus.BUDGET_LIMITED


@pytest.mark.asyncio
async def test_goal_resume_below_absolute_limit_adds_only_available_budget(tmp_path):
    store = GoalStore(tmp_path / "goals.json")
    now = datetime.now(UTC)
    goal = Goal(goal_id=uuid4(), title="Tope", objective="Fixture",
                status=GoalStatus.BUDGET_LIMITED, max_steps=18, steps_used=18,
                created_at=now, updated_at=now)
    store.save(goal)

    async def judge(_goal, _text):
        return StepVerdict(status="continue", note="Prueba")

    runner = GoalRunner(store, jobs=Jobs(completed=True), conversations=None,
                        judge=judge, deliver=())
    result = await GoalIpcService(store, runner).handle(request(
        "goals.resume", goal_id=str(goal.goal_id)))
    assert result.ok and result.payload["goal"]["max_steps"] == 20


def test_full_unicode_outbox_capacity_survives_reload_update_and_delete(tmp_path):
    from aegis_core.scheduling import MAX_SCHEDULE_STORE_BYTES, MAX_SCHEDULED_TASKS
    from aegis_core.settings_store import DEFAULT_PRIVATE_JSON_BYTES

    path = tmp_path / "tasks.json"
    store = ScheduledTaskStore(path)
    result = "🧪" * 32_768
    tasks = [task(
        title="🧪" * 80, prompt="🧪" * 1_000,
        last_delivered_excerpt="🧪" * 2_000,
        pending_delivery=result, pending_delivery_status="completed",
        pending_delivery_started_at=datetime.now(UTC),
        pending_delivery_schedule=Schedule(kind="interval", every_minutes=30),
    ) for _ in range(MAX_SCHEDULED_TASKS)]
    for scheduled in tasks:
        store.add(scheduled)
    assert DEFAULT_PRIVATE_JSON_BYTES < path.stat().st_size <= MAX_SCHEDULE_STORE_BYTES
    reloaded = ScheduledTaskStore(path)
    assert len(reloaded.tasks()) == MAX_SCHEDULED_TASKS
    assert all(item.pending_delivery == result for item in reloaded.tasks())
    updated = reloaded.get(tasks[0].task_id)
    updated.title = "Título editado"
    reloaded.update(updated)
    assert reloaded.delete(tasks[-1].task_id) is not None
    again = ScheduledTaskStore(path)
    assert len(again.tasks()) == MAX_SCHEDULED_TASKS - 1
    assert again.get(tasks[0].task_id).title == "Título editado"
    assert again.get(tasks[0].task_id).pending_delivery == result
    assert path.stat().st_mode & 0o777 == 0o600
