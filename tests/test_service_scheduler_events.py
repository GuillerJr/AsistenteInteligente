"""RPC regressions: timer admission never replaces exact action approval."""

import asyncio

import pytest
from test_service_management import management as management
from test_service_management import (
    occurrence,
    pending,
    schedule,
    start_scheduler,
    synthetic_decision,
)

from jarvis_core.protocol import RpcError


async def approval_for_occurrence(service, client, identity):
    async with asyncio.timeout(5):
        while identity not in service._refs:
            await asyncio.sleep(0.002)
    ref = service._refs[identity]
    wire = {"session_id": ref.session_id, "temporary": ref.temporary}
    return wire, await pending(client, wire, identity)


async def test_consumed_once_rule_keeps_exact_running_action_authorized_after_user_decision(
    management,
):
    service, client, peer, _root = management
    peer.plans["prueba once guard"] = [
        ("workspace_write", {"path": "once-guard.txt", "text": "ONCE GUARD 7319"})
    ]
    job = await schedule(client, prompt="prueba once guard", enabled=True)
    await start_scheduler(client)
    active = await occurrence(client, job["schedule_id"], final=False)
    wire, decision = await approval_for_occurrence(service, client, active["occurrence_id"])
    record = service.schedule_store.get(job["schedule_id"])
    assert not record.enabled and record.next_at is None
    assert record.revision == active["schedule_revision"]
    assert not (service._workspace_path / "once-guard.txt").exists()
    assert (await synthetic_decision(client, wire, decision))["accepted"]
    done = await occurrence(client, job["schedule_id"])
    assert done["status"] == "completed" and done["outcome"] == "known"
    assert (service._workspace_path / "once-guard.txt").read_bytes() == b"ONCE GUARD 7319"
    await client.call("scheduler.stop")
    await start_scheduler(client)
    await asyncio.sleep(0.02)
    assert len(peer.calls) == 1
    assert len(service.schedule_store.history(job["schedule_id"])) == 1


async def test_event_wake_requires_active_runner_exact_tool_approval_and_deduplicates_denial(
    management,
):
    service, client, peer, _root = management
    peer.plans["prueba wake sin permiso"] = [
        ("workspace_write", {"path": "wake-denied.txt", "text": "NO ESCRIBIR"})
    ]
    job = (await client.call("schedules.create", {
        "title": "Wake sintético", "prompt": "prueba wake sin permiso", "agent_id": "jarvis",
        "enabled": True, "spec": {"kind": "event"},
    }))["schedule"]
    request = {"schedule_id": job["schedule_id"], "expected_revision": job["revision"],
               "event_id": "synthetic-wake-1"}
    with pytest.raises(RpcError):
        await client.call("schedules.wake", request)
    assert not peer.calls and not service.schedule_store.history(job["schedule_id"])
    await start_scheduler(client)
    admitted = (await client.call("schedules.wake", request))["occurrence"]
    wire, decision = await approval_for_occurrence(service, client, admitted["occurrence_id"])
    assert not (service._workspace_path / "wake-denied.txt").exists()
    assert (await synthetic_decision(client, wire, decision, allow=False))["accepted"]
    receipt = await occurrence(client, job["schedule_id"])
    assert receipt["status"] in {"failed", "cancelled"}
    duplicate = (await client.call("schedules.wake", request))["occurrence"]
    assert duplicate["occurrence_id"] == receipt["occurrence_id"]
    assert duplicate["status"] == receipt["status"]
    await asyncio.sleep(0.02)
    assert len(peer.calls) == 1 and len(service.schedule_store.history(job["schedule_id"])) == 1
    assert not (service._workspace_path / "wake-denied.txt").exists()


async def test_suspend_invalidates_pending_action_approval_without_replay(management):
    service, client, peer, _root = management
    peer.plans["prueba suspensión"] = [
        ("workspace_write", {"path": "suspended.txt", "text": "NO ESCRIBIR"})
    ]
    job = await schedule(client, prompt="prueba suspensión", enabled=True)
    await start_scheduler(client)
    active = await occurrence(client, job["schedule_id"], final=False)
    wire, decision = await approval_for_occurrence(service, client, active["occurrence_id"])
    result = await client.call("scheduler.suspend")
    assert result["suspended"]
    assert not (await synthetic_decision(client, wire, decision))["accepted"]
    receipt = await occurrence(client, job["schedule_id"])
    assert receipt["status"] == "cancelled" and receipt["outcome"] == "unknown"
    assert not (await client.call("scheduler.resume"))["suspended"]
    await asyncio.sleep(0.02)
    assert len(peer.calls) == 1
    assert not (service._workspace_path / "suspended.txt").exists()
