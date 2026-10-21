from datetime import UTC, datetime, timedelta

import pytest

from aegis_core.scheduling import (
    DeliveryInbox,
    ScheduledTaskStore,
    ScheduleIpcService,
    TaskScheduler,
)


class Request:
    def __init__(self, method, payload=None):
        self.method, self.payload = method, payload or {}


class Jobs:
    async def submit(self, request, **_):
        raise RuntimeError("synthetic")


def _service(tmp_path):
    tmp_path.chmod(0o700)
    store = ScheduledTaskStore(tmp_path / "tasks.json")
    scheduler = TaskScheduler(store, jobs=Jobs(), conversations=None, deliver=())
    return ScheduleIpcService(store, scheduler, DeliveryInbox()), store


@pytest.mark.asyncio
async def test_the_app_creates_and_edits_a_task_like_the_chat_does(tmp_path):
    service, store = _service(tmp_path)
    created = await service.handle(Request("schedules.save", {
        "title": "Correos", "prompt": "Resume mis correos sin leer", "kind": "daily",
        "time": "08:00", "weekdays": ["lunes", "martes", "miércoles", "jueves", "viernes"]}))
    assert created.ok
    task = created.payload["task"]
    assert task["schedule"] == "de lunes a viernes a las 08:00"
    assert task["spec"]["kind"] == "daily" and task["spec"]["weekdays"][0] == "lunes"
    edited = await service.handle(Request("schedules.save", {
        "task_id": task["task_id"], "title": "Novedades de Ana", "prompt": "Busca correos de Ana",
        "kind": "interval", "every_minutes": 120, "notify": "when_new"}))
    assert edited.ok and edited.payload["task"]["schedule"] == "cada 2 horas"
    assert edited.payload["task"]["notify"] == "when_new"
    assert len(store.tasks()) == 1


@pytest.mark.asyncio
async def test_invalid_or_past_schedules_are_refused(tmp_path):
    service, _ = _service(tmp_path)
    incomplete = await service.handle(Request("schedules.save", {
        "title": "x", "prompt": "y", "kind": "daily"}))
    assert not incomplete.ok and incomplete.error_code == "invalid_payload"
    past = (datetime.now(UTC) - timedelta(days=1)).strftime("%Y-%m-%dT%H:%M")
    once = await service.handle(Request("schedules.save", {
        "title": "Ayer", "prompt": "Nada", "kind": "once", "at": past}))
    assert once.ok  # A one-time task keeps its exact time, as the chat tool does.
    missing = await service.handle(Request("schedules.save", {
        "task_id": "00000000-0000-4000-8000-000000000000", "title": "x", "prompt": "y",
        "kind": "interval", "every_minutes": 60}))
    assert not missing.ok and missing.error_code == "schedule_not_found"
