"""RPC regressions: timer admission never replaces exact action approval."""

import asyncio

import pytest
from test_service_management import management as management
from test_service_management import (
    new_agent,
    occurrence,
    pending,
    schedule,
    start_scheduler,
    synthetic_decision,
)

from jarvis_core.protocol import RpcError
from jarvis_core.provider_choice import ProviderChoiceError
from jarvis_core.scheduler import DispatchResult


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


@pytest.mark.parametrize("error_type", [ValueError, ProviderChoiceError, asyncio.CancelledError])
async def test_schedule_start_failure_removes_origin_and_guard_without_awaiting_nonexistent_runtime(
    management, monkeypatch, error_type,
):
    service, client, peer, _root = management
    job = await schedule(client, prompt="inicio fallido sintético", enabled=True)

    async def unavailable(_request):
        raise error_type("synthetic_start_unavailable")

    monkeypatch.setattr(service, "start_run", unavailable)
    await start_scheduler(client)
    receipt = await occurrence(client, job["schedule_id"])
    assert receipt["status"] in {"failed", "cancelled"}
    assert receipt["outcome"] == "unknown"
    assert receipt["occurrence_id"] not in service._automation_origins
    assert receipt["occurrence_id"] not in service._automation_run_guards
    assert not service._handles and not service._refs and not peer.calls
    await client.call("scheduler.stop")
    await start_scheduler(client)
    await asyncio.sleep(0.02)
    assert len(service.schedule_store.history(job["schedule_id"])) == 1


async def test_partial_rpc_start_failure_cancels_owned_runtime_and_keeps_unknown_receipt(
    management, monkeypatch,
):
    service, client, peer, _root = management
    # Lose the ACK after inference has entered. A consumed once rule correctly
    # refuses provider preparation before its exact run binding is attached;
    # injecting an await inside that binding gap would test a different fault.
    job = (await client.call("schedules.create", {
        "title": "ACK perdido sintético", "prompt": "esperar", "agent_id": "jarvis",
        "enabled": True, "spec": {"kind": "event"},
    }))["schedule"]
    wake = {"schedule_id": job["schedule_id"], "expected_revision": job["revision"],
            "event_id": "synthetic-partial-start-event"}
    original = service.start_run

    async def partial_start(request):
        await original(request)
        await peer.entered.wait()
        raise RpcError(-32020, "synthetic_partial_start_ack_lost")

    monkeypatch.setattr(service, "start_run", partial_start)
    await start_scheduler(client)
    admitted = (await client.call("schedules.wake", wake))["occurrence"]
    receipt = await occurrence(client, job["schedule_id"])
    assert receipt["occurrence_id"] == admitted["occurrence_id"]
    assert receipt["status"] == "interrupted" and receipt["outcome"] == "unknown"
    assert receipt["error_code"] == "scheduled_dispatch_unconfirmed"
    assert receipt["run_id"] == receipt["occurrence_id"]
    async with asyncio.timeout(5):
        while service._handles:
            await asyncio.sleep(0.002)
    assert receipt["occurrence_id"] not in service._automation_origins
    assert receipt["occurrence_id"] not in service._automation_run_guards
    assert len(peer.calls) == 1
    await client.call("scheduler.stop")
    await start_scheduler(client)
    duplicate = (await client.call("schedules.wake", wake))["occurrence"]
    assert duplicate["occurrence_id"] == receipt["occurrence_id"]
    assert duplicate["status"] == "interrupted" and duplicate["outcome"] == "unknown"
    await asyncio.sleep(0.02)
    assert len(peer.calls) == 1


async def test_agent_with_retained_flow_history_cannot_be_orphaned_after_hook_removal(management):
    service, client, peer, _root = management
    agent = await new_agent(client)
    hook = (await client.call("automation.hooks.create", {
        "title": "Historial sintético", "event": "manual", "agent_id": agent["agent_id"],
        "steps": [{"prompt": "No usar herramientas", "pause_after": False}],
    }))["hook"]
    hook = (await client.call("automation.hooks.update", {
        "hook_id": hook["hook_id"], "expected_revision": hook["revision"], "enabled": True,
    }))["hook"]
    flow = (await client.call("automation.hooks.emit", {
        "hook_id": hook["hook_id"], "expected_revision": hook["revision"],
        "event_id": "retained-history-event", "payload": {},
    }))["flows"][0]
    # Finish only a synthetic registry claim, without inference or a session.
    claim = service.automation_flows.claim_next()
    service.automation_flows.finish_step(claim, DispatchResult(None))
    await client.call("automation.hooks.remove", {
        "hook_id": hook["hook_id"], "expected_revision": hook["revision"],
    })
    assert not service.automation_flows.list_hooks()
    assert not any(row.agent_id == agent["agent_id"] for row in service.store.list_sessions())
    before = service.automation_flows.get_flow(flow["flow_id"])
    assert before["status"] == "completed"
    with pytest.raises(RpcError) as error:
        await client.call("agents.delete", {
            "agent_id": agent["agent_id"], "expected_revision": agent["revision"],
        })
    assert error.value.code == -32032
    assert service.agent_profiles.get(agent["agent_id"]).revision == agent["revision"]
    assert service.automation_flows.get_flow(flow["flow_id"]) == before
    assert not peer.calls
