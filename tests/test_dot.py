from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from aegis_core.attention import AttentionList
from aegis_core.dot import DotIpcService, DotPause
from aegis_core.goals import Goal, GoalRunner, GoalStatus, GoalStore
from aegis_core.scheduling import (
    DeliveryInbox,
    NotifyPolicy,
    Schedule,
    ScheduledDelivery,
    ScheduledTask,
    ScheduledTaskStore,
    ScheduleKind,
    TaskScheduler,
)


class Request:
    def __init__(self, method, payload=None):
        self.method, self.payload = method, payload or {}


def _setup(tmp_path):
    tmp_path.chmod(0o700)
    now = datetime.now(UTC)
    tasks, goals = ScheduledTaskStore(tmp_path / "tasks.json"), GoalStore(tmp_path / "goals.json")
    due = ScheduledTask(task_id=uuid4(), title="Correos", prompt="Resume mis correos",
                        schedule=Schedule(kind=ScheduleKind.DAILY, time="08:00"),
                        created_at=now, next_run_at=now - timedelta(minutes=1))
    watch = due.model_copy(update={
        "task_id": uuid4(), "title": "Escribe Ana", "notify": NotifyPolicy.WHEN_NEW,
        "next_run_at": now + timedelta(hours=1)})
    off = due.model_copy(update={"task_id": uuid4(), "title": "Pausada", "enabled": False})
    for task in (due, watch, off):
        tasks.add(task)
    goals.save(Goal(goal_id=uuid4(), title="Informe", objective="x", created_at=now,
                    updated_at=now, steps_used=1, max_steps=6))
    goals.save(Goal(goal_id=uuid4(), title="Hecho", objective="x", created_at=now,
                    updated_at=now, status=GoalStatus.COMPLETE))
    inbox = DeliveryInbox()
    inbox.push(ScheduledDelivery(delivery_id=uuid4(), task_id=due.task_id, title="Correos",
                                 text="Tienes   tres correos nuevos " * 30, conversation_id=None,
                                 delivered_at=now))
    pause = DotPause(tmp_path / "dot.json")
    runs = []

    class Jobs:
        async def submit(self, request, **_):
            runs.append(request.text)
            raise RuntimeError("synthetic")

    scheduler = TaskScheduler(tasks, jobs=Jobs(), conversations=None, deliver=(),
                              paused=pause.is_paused)
    async def judge(goal, result):
        raise RuntimeError("unused")
    runner = GoalRunner(goals, jobs=Jobs(), conversations=None, judge=judge, deliver=(),
                        paused=pause.is_paused)
    service = DotIpcService(pause, schedules=tasks, goals=goals, scheduler=scheduler,
                            goal_runner=runner, inbox=inbox,
                            attention=AttentionList(tasks, goals))
    return service, scheduler, runner, pause, runs


@pytest.mark.asyncio
async def test_overview_shows_what_dot_watches_and_delivered(tmp_path):
    service, *_ = _setup(tmp_path)
    result = await service.handle(Request("dot.overview"))
    assert result.ok
    overview = result.payload
    assert overview["paused"] is False and overview["now"] == []
    assert [item["title"] for item in overview["upcoming"]] == ["Correos", "Escribe Ana"]
    assert [item["monitoring"] for item in overview["upcoming"]] == [False, True]
    assert [goal["title"] for goal in overview["goals"]] == ["Informe"]
    assert overview["recent"][0]["title"] == "Correos"
    assert len(overview["recent"][0]["text"]) <= 280 and "  " not in overview["recent"][0]["text"]
    assert overview["counts"] == {"schedules": 2, "goals": 1}
    assert (await service.handle(Request("dot.overview", {"x": 1}))).ok is False


@pytest.mark.asyncio
async def test_pausing_dot_stops_tasks_and_goals_until_resumed_and_survives_restarts(tmp_path):
    service, scheduler, runner, _, _ = _setup(tmp_path)
    paused = await service.handle(Request("dot.pause"))
    assert paused.payload["paused"] is True and paused.payload["paused_since"]
    assert DotPause(tmp_path / "dot.json").is_paused()  # A restart keeps the pause.
    await scheduler.run_due()
    runner.start_due()
    assert scheduler.running == () and runner.running == ()
    resumed = await service.handle(Request("dot.resume"))
    assert resumed.payload["paused"] is False
    await scheduler.run_due()
    assert scheduler.running  # The due task starts once Dot is back.
    await scheduler.close()
    await runner.close()
