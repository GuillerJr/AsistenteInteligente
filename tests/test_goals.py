import json
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest

from aegis_core.contracts import ToolAuthorization
from aegis_core.goals import (
    Goal,
    GoalRunner,
    GoalStatus,
    GoalStore,
    GoalToolHandlers,
    StepVerdict,
    goal_confirmation_summary,
)
from aegis_core.job_contracts import JobStatus


def _store(tmp_path: Path) -> GoalStore:
    tmp_path.chmod(0o700)
    return GoalStore(tmp_path / "goals.json")


def _goal(**changes) -> Goal:
    now = datetime.now(UTC)
    values = dict(goal_id=uuid4(), title="Correos", max_steps=3, created_at=now,
                  updated_at=now, next_step_at=now,
                  objective="Revisa mis correos de la semana y dime cuáles necesitan respuesta")
    values.update(changes)
    return Goal(**values)


def _authorization(tool_name, arguments):
    return ToolAuthorization.model_construct(call_id="c", tool_name=tool_name,
                                             normalized_arguments=arguments)


class Jobs:
    def __init__(self, *outcomes):
        self.outcomes = list(outcomes)
        self.requests = []
        self.cancelled = []

    async def submit(self, request, *, conversation_id=None, persist_conversation=False):
        self.requests.append(request)
        return self.outcomes.pop(0)

    async def wait_for_change(self, job_id, **_):
        raise AssertionError("snapshots are terminal")

    async def cancel(self, job_id):
        self.cancelled.append(job_id)


def _done(text):
    return SimpleNamespace(job_id=uuid4(), status=JobStatus.COMPLETED, result=text,
                           error_code=None, stream_version=1, confirmation=None)


def _judge(*verdicts):
    queue = list(verdicts)
    seen = []

    async def judge(goal, result):
        seen.append((list(goal.notes), result))
        return queue.pop(0)

    judge.seen = seen
    return judge


@pytest.mark.asyncio
async def test_create_list_and_stop_tools(tmp_path):
    store = _store(tmp_path)
    tools = GoalToolHandlers(store).handlers()
    created = json.loads((await tools["jarvis_goal_create"](_authorization(
        "jarvis_goal_create", {"title": "Informe", "objective": "Investiga X", "max_steps": 4},
    ), None)).output)
    assert created["created"] and "hasta 4" in created["message"]
    listed = json.loads((await tools["jarvis_goal_list"](
        _authorization("jarvis_goal_list", {}), None)).output)
    assert "«Informe» · en curso · 0/4 pasos" in listed["message"]
    stopped = json.loads((await tools["jarvis_goal_stop"](
        _authorization("jarvis_goal_stop", {"title": "informe"}), None)).output)
    assert stopped["stopped"] and store.goals()[0].status is GoalStatus.PAUSED


def test_confirmation_states_the_budget():
    summary = goal_confirmation_summary(_authorization("jarvis_goal_create", {
        "title": "Informe", "objective": "Investiga X", "max_steps": 6}))
    assert "hasta 6 pasos" in summary


@pytest.mark.asyncio
async def test_steps_carry_progress_until_complete(tmp_path):
    store = _store(tmp_path)
    goal = _goal()
    store.save(goal)
    delivered = []

    async def deliver(goal, text):
        delivered.append(text)

    judge = _judge(
        StepVerdict(status="continue", note="Listé 10 correos."),
        StepVerdict(status="complete", note="Leí los importantes.",
                    report="Necesitan respuesta: 2 y 5."),
    )
    runner = GoalRunner(store, jobs=Jobs(_done("10 correos"), _done("leídos")),
                        conversations=None, judge=judge, deliver=(deliver,))
    assert await runner.step(goal.goal_id) is GoalStatus.ACTIVE
    assert await runner.step(goal.goal_id) is GoalStatus.COMPLETE
    assert judge.seen[1][0] == ["Listé 10 correos."]  # The second step saw the first.
    assert delivered == ["🎯 Correos · completado\n\nNecesitan respuesta: 2 y 5."]
    assert store.get(goal.goal_id).steps_used == 2


@pytest.mark.asyncio
async def test_budget_stops_the_goal_with_its_progress(tmp_path):
    store = _store(tmp_path)
    goal = _goal(max_steps=1)
    store.save(goal)
    delivered = []

    async def deliver(goal, text):
        delivered.append(text)

    runner = GoalRunner(store, jobs=Jobs(_done("parcial")), conversations=None,
                        judge=_judge(StepVerdict(status="continue", note="Avancé algo.")),
                        deliver=(deliver,))
    assert await runner.step(goal.goal_id) is GoalStatus.BUDGET_LIMITED
    assert "Avancé algo." in delivered[0] and "parcial" in delivered[0]


@pytest.mark.asyncio
async def test_a_step_needing_approval_blocks_without_approving(tmp_path):
    store = _store(tmp_path)
    goal = _goal()
    store.save(goal)
    pending = SimpleNamespace(
        job_id=uuid4(), status=JobStatus.AWAITING_CONFIRMATION, result=None, error_code=None,
        stream_version=1, confirmation=SimpleNamespace(tool_name="mail_send_message",
                                                       standing_grant_available=False))
    jobs = Jobs(pending)
    delivered = []

    async def deliver(goal, text):
        delivered.append(text)

    runner = GoalRunner(store, jobs=jobs, conversations=None, judge=_judge(),
                        deliver=(deliver,))
    assert await runner.step(goal.goal_id) is GoalStatus.BLOCKED
    assert len(jobs.cancelled) == 1 and "Revisa la acción en Jarvis" in delivered[0]
    assert jobs.requests[0].metadata["source_channel"] == "goal"


@pytest.mark.asyncio
async def test_owner_stop_during_a_step_wins(tmp_path):
    store = _store(tmp_path)
    goal = _goal()
    store.save(goal)

    async def judge(goal_seen, result):
        paused = store.get(goal.goal_id)
        paused.status = GoalStatus.PAUSED
        store.save(paused)
        return StepVerdict(status="continue", note="Paso hecho.")

    runner = GoalRunner(store, jobs=Jobs(_done("ok")), conversations=None, judge=judge,
                        deliver=())
    await runner.step(goal.goal_id)
    assert store.get(goal.goal_id).status is GoalStatus.PAUSED
