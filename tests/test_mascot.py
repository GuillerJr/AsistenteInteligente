import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from aegis_core.ipc.protocol import IpcAuthenticator
from aegis_core.mascot import MascotAutonomyService

AUTH = IpcAuthenticator(b"m" * 32)
PLAN = {"intent": "explore", "targets": [{"x": 0.85, "y": 0.2}],
        "speed": 24, "duration_seconds": 45}


def world():
    return {"revision": str(uuid4()),
            "room": {"width": 280, "height": 208, "body_size": 84,
                     "travel_width": 172, "travel_height": 54, "expanded": True},
            "position": {"x": 0.5, "y": 0.5},
            "pointer": {"x": 0.5, "y": 0.5, "present": False}, "phase": "resting",
            "memory": {"energy": 0.85, "curiosity": 0.7, "affinity": 0.4,
                       "last_intent": "observe", "visits": []}, "event": "started"}


def brain(plan=PLAN):
    return SimpleNamespace(complete_nvidia_only=AsyncMock(return_value=SimpleNamespace(
        content=json.dumps(plan), tool_calls=(), model_id="test/nvidia")))


async def call(service, method, payload):
    return await service.handle(AUTH.create_request("mascot.plan." + method, payload))


async def settled(service, snapshot):
    for _ in range(100):
        response = await call(service, "status", {"revision": snapshot["revision"]})
        if response.payload["status"] != "pending":
            return response.payload
        await asyncio.sleep(0.01)
    raise AssertionError("background inference did not settle")


@pytest.mark.asyncio
async def test_admission_is_nonblocking_with_only_numeric_spatial_context_and_no_tools():
    inference = brain()
    service = MascotAutonomyService(inference)
    snapshot = world()
    response = await call(service, "request", snapshot)
    assert response.payload["status"] == "pending"
    assert not inference.complete_nvidia_only.called  # No inference in the IPC handler.
    result = await settled(service, snapshot)
    assert result["status"] == "ready" and result["provider"] == "nvidia"
    assert result["plan"] == PLAN
    args = inference.complete_nvidia_only.call_args.kwargs
    assert args["extra_body"] == {"response_format": {"type": "json_object"}}
    references = json.loads(args["messages"][1]["content"])
    assert references == {"world": snapshot, "recent_decisions": []}
    assert "tools" not in args and len(args["messages"]) == 2
    await service.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("bad", [
    {**PLAN, "targets": [{"x": 2, "y": 0.5}]},
    {**PLAN, "targets": []}, {**PLAN, "speed": True},
    {**PLAN, "intent": "execute"}, {**PLAN, "shell": "open file"},
    {**PLAN, "duration_seconds": 1_000},
])
async def test_invalid_remote_plans_are_rejected_without_publication(bad):
    service = MascotAutonomyService(brain(bad))
    snapshot = world()
    await call(service, "request", snapshot)
    result = await settled(service, snapshot)
    assert result["status"] == "unavailable" and "plan" not in result
    assert (await call(service, "request", world())).payload["status"] == "cooldown"
    await service.close()


@pytest.mark.asyncio
async def test_private_fields_and_impossible_geometry_never_reach_nvidia():
    inference = brain()
    service = MascotAutonomyService(inference)
    for snapshot in [dict(world(), conversation="private"),
                     dict(world(), room={**world()["room"], "body_size": 200}),
                     dict(world(), memory={**world()["memory"], "token": "private"})]:
        response = await call(service, "request", snapshot)
        assert not response.ok and response.error_code == "invalid_payload"
    inference.complete_nvidia_only.assert_not_called()
    await service.close()


@pytest.mark.asyncio
async def test_foreground_work_cancels_inference_without_requiring_ui_polling():
    started = asyncio.Event()
    cancelled = asyncio.Event()
    busy = False

    async def slow(**kwargs):
        started.set()
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.set()

    service = MascotAutonomyService(SimpleNamespace(complete_nvidia_only=slow),
                                    foreground_busy=lambda: busy)
    snapshot = world()
    await call(service, "request", snapshot)
    await asyncio.wait_for(started.wait(), 1)
    assert (await call(service, "request", world())).payload["status"] == "busy"
    busy = True
    await asyncio.wait_for(cancelled.wait(), 1)
    assert (await settled(service, snapshot))["status"] == "unavailable"
    assert (await call(service, "request", world())).payload["status"] == "unavailable"
    await service.close()


@pytest.mark.asyncio
async def test_exact_ticket_cancellation_timeout_and_close_join_network_tasks():
    cancelled = asyncio.Event()
    async def slow(**kwargs):
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.set()
    service = MascotAutonomyService(
        SimpleNamespace(complete_nvidia_only=slow), timeout_seconds=0.02)
    snapshot = world()
    await call(service, "request", snapshot)
    other = await call(service, "status", {"revision": str(uuid4())})
    assert other.payload["status"] == "expired"
    assert (await settled(service, snapshot))["status"] == "unavailable"
    assert cancelled.is_set()
    await service.close()
    assert (await call(service, "request", world())).payload["status"] == "unavailable"

    service = MascotAutonomyService(SimpleNamespace(complete_nvidia_only=slow))
    snapshot = world()
    await call(service, "request", snapshot)
    await asyncio.sleep(0.01)
    await call(service, "cancel", {"revision": str(uuid4())})
    response = await call(service, "status", {"revision": snapshot["revision"]})
    assert response.payload["status"] == "pending"
    await call(service, "cancel", {"revision": snapshot["revision"]})
    response = await call(service, "status", {"revision": snapshot["revision"]})
    assert response.payload["status"] == "cancelled"
    await service.close()


@pytest.mark.asyncio
async def test_budgets_expiry_and_recent_memory_remain_bounded():
    now = 100.0
    inference = brain()
    service = MascotAutonomyService(inference, clock=lambda: now)
    snapshot = world()
    await call(service, "request", snapshot)
    await settled(service, snapshot)
    assert (await call(service, "request", world())).payload["status"] == "cooldown"
    now += 31
    response = await call(service, "status", {"revision": snapshot["revision"]})
    assert response.payload["status"] == "expired"
    for _ in range(59):
        snapshot = world()
        await call(service, "request", snapshot)
        assert (await settled(service, snapshot))["status"] == "ready"
        now += 31
    assert (await call(service, "request", world())).payload["status"] == "cooldown"
    context = json.loads(inference.complete_nvidia_only.call_args.kwargs["messages"][1]["content"])
    assert len(context["recent_decisions"]) == 3
    now += 3_600
    assert (await call(service, "request", world())).payload["status"] == "pending"
    await service.close()


@pytest.mark.asyncio
async def test_security_and_thermal_gates_invalidate_even_completed_plans():
    blocked = False
    service = MascotAutonomyService(brain(), blocked=lambda: blocked)
    snapshot = world()
    await call(service, "request", snapshot)
    assert (await settled(service, snapshot))["status"] == "ready"
    blocked = True
    result = await call(service, "status", {"revision": snapshot["revision"]})
    assert result.payload["status"] == "unavailable" and "plan" not in result.payload
    assert (await call(service, "request", world())).payload["status"] == "unavailable"
    await service.close()


@pytest.mark.asyncio
async def test_unexpected_tool_call_is_never_executed_or_published():
    inference = brain()
    inference.complete_nvidia_only.return_value.tool_calls = (object(),)
    service = MascotAutonomyService(inference)
    snapshot = world()
    await call(service, "request", snapshot)
    assert (await settled(service, snapshot))["status"] == "unavailable"
    await service.close()


@pytest.mark.asyncio
async def test_actual_job_lifecycle_yields_priority_until_foreground_task_finishes():
    from test_jobs import BlockingGraph, _terminal

    from aegis_core.contracts import UserRequest
    from aegis_core.jobs import SwarmJobManager

    graph = BlockingGraph()
    jobs = SwarmJobManager(graph)
    service = MascotAutonomyService(brain(), foreground_busy=lambda: jobs.has_active_jobs)
    try:
        assert not jobs.has_active_jobs
        queued = await jobs.submit(UserRequest(text="synthetic priority check"))
        assert jobs.has_active_jobs
        await asyncio.wait_for(graph.started.wait(), 1)
        assert (await call(service, "request", world())).payload["status"] == "unavailable"
        graph.release.set()
        await _terminal(jobs, queued.job_id)
        assert not jobs.has_active_jobs
        assert (await call(service, "request", world())).payload["status"] == "pending"
    finally:
        await service.close()
        await jobs.close()
