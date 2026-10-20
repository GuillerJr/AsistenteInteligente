import asyncio
import json
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest

from aegis_core.contracts import ToolAuthorization
from aegis_core.job_contracts import JobStatus
from aegis_core.scheduling import (
    NotifyPolicy,
    Schedule,
    ScheduledTask,
    ScheduledTaskStore,
    ScheduleKind,
    ScheduleToolHandlers,
    TaskScheduler,
    schedule_confirmation_summary,
)

ECUADOR = timezone(timedelta(hours=-5))


def _local(*parts):
    """Schedules use the Mac's own wall clock, whatever zone the tests run in."""
    return datetime(*parts).astimezone()


def _store(tmp_path: Path) -> ScheduledTaskStore:
    tmp_path.chmod(0o700)
    return ScheduledTaskStore(tmp_path / "scheduled_tasks.json")


def _task(**changes) -> ScheduledTask:
    values = dict(
        task_id=uuid4(), title="Correos", prompt="Resúmeme los correos sin leer",
        schedule=Schedule(kind=ScheduleKind.DAILY, time="08:00"),
        created_at=datetime.now(UTC), next_run_at=datetime.now(UTC) - timedelta(seconds=1),
    )
    values.update(changes)
    return ScheduledTask(**values)


def test_daily_schedule_respects_time_and_weekdays():
    weekdays = Schedule(kind=ScheduleKind.DAILY, time="08:00", weekdays=(0, 1, 2, 3, 4))
    friday_evening = _local(2026, 10, 23, 20, 0)
    monday = weekdays.next_after(friday_evening)
    assert monday.weekday() == 0 and (monday.hour, monday.minute) == (8, 0)
    assert weekdays.describe() == "de lunes a viernes a las 08:00"
    every_day = Schedule(kind=ScheduleKind.DAILY, time="08:00")
    assert every_day.next_after(_local(2026, 10, 20, 7, 59)).day == 20
    assert every_day.next_after(_local(2026, 10, 20, 8, 0)).day == 21


def test_interval_and_once_schedules():
    interval = Schedule(kind=ScheduleKind.INTERVAL, every_minutes=30)
    now = datetime(2026, 10, 20, 10, 0, tzinfo=UTC)
    assert interval.next_after(now) - now == timedelta(minutes=1)
    assert interval.next_after(now, last_run=now) - now == timedelta(minutes=30)
    assert interval.describe() == "cada 30 minutos"
    at = datetime(2026, 10, 21, 9, 0, tzinfo=ECUADOR)
    once = Schedule(kind=ScheduleKind.ONCE, at=at)
    assert once.next_after(now) == at
    assert once.next_after(now, last_run=at) is None


@pytest.mark.parametrize("bad", [
    {"kind": "daily"},
    {"kind": "daily", "time": "25:00"},
    {"kind": "interval", "every_minutes": 5},
    {"kind": "once", "at": "2026-10-21T09:00:00"},
    {"kind": "daily", "time": "08:00", "weekdays": [7]},
])
def test_invalid_schedules_are_rejected(bad):
    with pytest.raises(ValueError):
        Schedule.model_validate(bad)


def test_store_round_trips_and_ignores_loose_files(tmp_path):
    store = _store(tmp_path)
    task = store.add(_task())
    assert [item.task_id for item in _store(tmp_path).tasks()] == [task.task_id]
    (tmp_path / "scheduled_tasks.json").chmod(0o644)
    assert _store(tmp_path).tasks() == ()


def _authorization(tool_name, arguments):
    return ToolAuthorization.model_construct(
        call_id="call-1", tool_name=tool_name, normalized_arguments=arguments,
    )


@pytest.mark.asyncio
async def test_create_list_delete_tools(tmp_path):
    store = _store(tmp_path)
    changes = []
    tools = ScheduleToolHandlers(store, on_change=lambda: changes.append(1)).handlers()
    created = await tools["jarvis_schedule_create"](_authorization("jarvis_schedule_create", {
        "title": "Correos", "prompt": "Resúmeme los correos sin leer", "kind": "daily",
        "time": "08:00", "weekdays": ["lunes", "martes", "miércoles", "jueves", "viernes"],
        "every_minutes": None, "at": None,
        "notify": "always",
    }), None)
    payload = json.loads(created.output)
    assert created.success and payload["created"] is True
    assert "de lunes a viernes a las 08:00" in payload["message"]
    listed = json.loads((await tools["jarvis_schedule_list"](
        _authorization("jarvis_schedule_list", {}), None)).output)
    assert len(listed["tasks"]) == 1
    missing = json.loads((await tools["jarvis_schedule_delete"](
        _authorization("jarvis_schedule_delete", {"title": "banco"}),
        None)).output)
    assert missing["deleted"] is False and len(store.tasks()) == 1
    deleted = json.loads((await tools["jarvis_schedule_delete"](
        _authorization("jarvis_schedule_delete", {"title": "correos"}),
        None)).output)
    assert deleted["deleted"] is True and store.tasks() == ()
    assert changes == [1, 1]


def test_confirmation_summary_states_schedule_and_policy():
    summary = schedule_confirmation_summary(_authorization("jarvis_schedule_create", {
        "title": "Ana", "prompt": "Busca correos nuevos de Ana", "kind": "interval",
        "time": None, "weekdays": None, "every_minutes": 60, "at": None, "notify": "when_new",
    }))
    assert "cada hora" in summary and "solo cuando haya algo nuevo" in summary


class FakeJobs:
    def __init__(self, outcome):
        self.outcome = outcome
        self.submitted = []
        self.cancelled = []

    async def submit(self, request, *, conversation_id=None, persist_conversation=False):
        self.submitted.append((request, conversation_id))
        return self.outcome

    async def wait_for_change(self, job_id, *, after_stream_version, timeout_seconds):
        return self.outcome

    async def cancel(self, job_id):
        self.cancelled.append(job_id)


def _snapshot(status, *, result=None, confirmation=None):
    return SimpleNamespace(job_id=uuid4(), status=status, result=result, error_code=None,
                           stream_version=1, confirmation=confirmation)


class FakeConversations:
    async def create(self, title=None):
        return SimpleNamespace(conversation_id=uuid4())


@pytest.mark.asyncio
async def test_due_task_runs_as_owner_job_and_delivers(tmp_path):
    store = _store(tmp_path)
    task = store.add(_task())
    jobs = FakeJobs(_snapshot(JobStatus.COMPLETED, result="Tienes 3 correos sin leer."))
    delivered = []

    async def deliver(task, text):
        delivered.append(text)

    scheduler = TaskScheduler(store, jobs=jobs, conversations=FakeConversations(),
                              deliver=(deliver,))
    await scheduler.run_due()
    await asyncio.gather(*scheduler._running.values())

    request, conversation_id = jobs.submitted[0]
    assert request.text.startswith("Resúmeme los correos sin leer")
    assert request.metadata["source_channel"] == "schedule"
    assert conversation_id is not None
    assert delivered == ["⏰ Correos\n\nTienes 3 correos sin leer."]
    updated = store.get(task.task_id)
    assert updated.last_status == "completed" and updated.next_run_at > datetime.now(UTC)
    assert updated.conversation_id == conversation_id


@pytest.mark.asyncio
async def test_pending_approval_is_cancelled_never_approved(tmp_path):
    store = _store(tmp_path)
    store.add(_task())
    confirmation = SimpleNamespace(tool_name="mcp_google_workspace__search_gmail_messages",
                                   standing_grant_available=True)
    jobs = FakeJobs(_snapshot(JobStatus.AWAITING_CONFIRMATION, confirmation=confirmation))
    delivered = []

    async def deliver(task, text):
        delivered.append(text)

    scheduler = TaskScheduler(store, jobs=jobs, conversations=None, deliver=(deliver,))
    await scheduler.run_due()
    await asyncio.gather(*scheduler._running.values())

    assert len(jobs.cancelled) == 1
    assert "Aprobar siempre" in delivered[0]
    assert store.tasks()[0].last_status == "needs_approval"


@pytest.mark.asyncio
async def test_subscription_only_delivers_news(tmp_path):
    store = _store(tmp_path)
    store.add(_task(notify=NotifyPolicy.WHEN_NEW,
                    schedule=Schedule(kind=ScheduleKind.INTERVAL, every_minutes=30)))
    jobs = FakeJobs(_snapshot(JobStatus.COMPLETED, result="Sin correos nuevos de Ana."))
    delivered, judged = [], []

    async def deliver(task, text):
        delivered.append(text)

    async def judge(previous, current):
        judged.append((previous, current))
        return False

    scheduler = TaskScheduler(store, jobs=jobs, conversations=None, deliver=(deliver,),
                              judge_new=judge)
    task = store.tasks()[0]
    # The first run is the baseline: delivered without asking the judge.
    await scheduler.run_now(task.task_id)
    assert len(delivered) == 1 and judged == []
    # Same text again is never re-delivered, still without asking the judge.
    await scheduler.run_now(task.task_id)
    assert len(delivered) == 1 and judged == []
    # A different result the judge considers not new is skipped too.
    jobs.outcome = _snapshot(JobStatus.COMPLETED, result="Revisé otra vez: sin novedades.")
    await scheduler.run_now(task.task_id)
    assert len(delivered) == 1 and len(judged) == 1
    assert judged[0] == ("Sin correos nuevos de Ana.", "Revisé otra vez: sin novedades.")


@pytest.mark.asyncio
async def test_one_time_task_disables_after_running(tmp_path):
    store = _store(tmp_path)
    at = datetime.now(UTC) - timedelta(seconds=1)
    task = store.add(_task(schedule=Schedule(kind=ScheduleKind.ONCE, at=at), next_run_at=at))
    scheduler = TaskScheduler(store, jobs=FakeJobs(_snapshot(JobStatus.COMPLETED, result="ok")),
                              conversations=None, deliver=())
    await scheduler.run_now(task.task_id)
    updated = store.get(task.task_id)
    assert updated.enabled is False and updated.next_run_at is None


def test_incomplete_schedule_is_rejected_as_arguments():
    from pydantic import ValidationError

    from aegis_core.scheduling import ScheduleCreateArguments

    with pytest.raises(ValidationError):
        ScheduleCreateArguments.model_validate({
            "title": "Correos", "prompt": "Revisa mis correos", "kind": "daily",
        })


@pytest.mark.asyncio
async def test_schedule_interpreter_validates_router_answer():
    from aegis_core.scheduling import interpret_schedule_request

    async def answer(payload):
        async def complete(messages):
            assert "martes" in messages[1]["content"] or "ahora" in messages[1]["content"]
            return json.dumps(payload)
        return await interpret_schedule_request(
            "cada mañana a las 8 resúmeme los correos",
            now=datetime(2026, 10, 20, 10, 0, tzinfo=UTC), complete=complete)

    good = await answer({"title": "Correos", "prompt": "Resúmeme los correos",
                         "kind": "daily", "time": "08:00", "weekdays": None,
                         "every_minutes": None, "at": None, "notify": "always"})
    assert good["kind"] == "daily" and good["weekdays"] is None and good["time"] == "08:00"
    named = await answer({"title": "Reuniones", "prompt": "Dime mis reuniones",
                          "kind": "daily", "time": "18:30", "weekdays": ["martes", "jueves"],
                          "notify": None})
    assert named["weekdays"] == ["martes", "jueves"] and named["notify"] == "always"
    assert await answer({"title": "x", "prompt": "y", "kind": "daily"}) is None
    assert await answer({"kind": "weekly", "title": "x", "prompt": "y"}) is None


@pytest.mark.asyncio
async def test_transient_provider_failure_retries_before_reporting(tmp_path):
    store = _store(tmp_path)
    task = store.add(_task())
    failed = _snapshot(JobStatus.FAILED)
    failed.error_code = "remote_rate_limited"
    delivered = []

    async def deliver(task, text):
        delivered.append(text)

    scheduler = TaskScheduler(store, jobs=FakeJobs(failed), conversations=None, deliver=(deliver,))
    for attempt in range(1, 4):
        await scheduler.run_now(task.task_id)
        current = store.get(task.task_id)
        assert delivered == [] and current.retries == attempt
        assert current.next_run_at - current.last_run_at == timedelta(minutes=5)
    await scheduler.run_now(task.task_id)
    assert len(delivered) == 1 and "remote_rate_limited" in delivered[0]
    assert store.get(task.task_id).retries == 0


@pytest.mark.asyncio
async def test_reminder_is_delivered_without_running_a_job(tmp_path):
    store = _store(tmp_path)
    task = store.add(_task(prompt="Pagar el arriendo", reminder=True))
    jobs = FakeJobs(_snapshot(JobStatus.COMPLETED, result="no debería usarse"))
    delivered = []

    async def deliver(task, text):
        delivered.append(text)

    await TaskScheduler(store, jobs=jobs, conversations=None, deliver=(deliver,)).run_now(
        task.task_id)
    assert jobs.submitted == []
    assert delivered == ["⏰ Correos\n\nRecordatorio: Pagar el arriendo"]
