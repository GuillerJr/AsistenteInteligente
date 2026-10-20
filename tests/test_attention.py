from datetime import UTC, datetime, timedelta
from uuid import uuid4

from aegis_core.attention import AttentionList
from aegis_core.goals import Goal, GoalStatus, GoalStore
from aegis_core.scheduling import Schedule, ScheduledTask, ScheduledTaskStore, ScheduleKind


def _stores(tmp_path):
    tmp_path.chmod(0o700)
    return (ScheduledTaskStore(tmp_path / "tasks.json"), GoalStore(tmp_path / "goals.json"))


def test_lists_tasks_and_goals_that_need_the_owner_and_snoozes(tmp_path):
    tasks, goals = _stores(tmp_path)
    now = datetime.now(UTC)
    blocked = ScheduledTask(task_id=uuid4(), title="Correos", prompt="Resume",
                            schedule=Schedule(kind=ScheduleKind.DAILY, time="08:00"),
                            created_at=now, last_status="needs_approval", last_run_at=now)
    fine = blocked.model_copy(update={"task_id": uuid4(), "title": "Bien",
                                      "last_status": "completed"})
    tasks.add(blocked)
    tasks.add(fine)
    goals.save(Goal(goal_id=uuid4(), title="Informe", objective="x", created_at=now,
                    updated_at=now, status=GoalStatus.BUDGET_LIMITED))
    goals.save(Goal(goal_id=uuid4(), title="Listo", objective="x", created_at=now,
                    updated_at=now, status=GoalStatus.COMPLETE))
    clock = [now]
    attention = AttentionList(tasks, goals, clock=lambda: clock[0])
    assert {item.title for item in attention.items()} == {"Correos", "Informe"}
    assert "Requiere tu atención" in attention.text()
    attention.snooze(f"task:{blocked.task_id}")
    assert {item.title for item in attention.items()} == {"Informe"}
    clock[0] = now + timedelta(hours=2)
    assert {item.title for item in attention.items()} == {"Correos", "Informe"}


def test_empty_list_says_so(tmp_path):
    assert "No hay nada pendiente" in AttentionList(*_stores(tmp_path)).text()
