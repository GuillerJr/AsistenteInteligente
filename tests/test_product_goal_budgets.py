"""Signed host/runtime goal budgets, provider isolation and cleanup custody.

All peers, accounts, client keys, workspaces and notes are synthetic. The tests
are intended for CI; no real model, Git process or external account is involved.
"""

import asyncio
from decimal import Decimal
from uuid import uuid4

import pytest
from test_product_service import (
    coordinate,
    decide,
    initialize,
    new_session,
    pending_approval,
    settle,
)
from test_product_service import product_connection as product_connection

from jarvis_core.protocol import RpcError


async def profiles(client, count):
    return [(await client.call("agents.create", {"name": f"Agente sintético {index}"}))["agent"]
            for index in range(count)]


def specification(agents, messages):
    return {"objective": "Objetivo con contabilidad inclusiva",
            "max_steps": len(agents) * 3, "timeout_seconds": 20,
            "children": [{"agent_id": agent["agent_id"],
                          "expected_revision": agent["revision"], "max_steps": 3,
                          "message": message}
                         for agent, message in zip(agents, messages, strict=True)]}


async def goal_snapshot(client, ref, started):
    return (await client.call("delegations.get", {
        **ref, "parent_run_id": started["parent_run_id"], "goal_id": started["goal_id"],
    }))["goal"]


async def budget(client, ref):
    return (await client.call("product.snapshot", ref))["budget"]


async def test_manual_parent_envelope_refunds_only_known_child_usage_and_isolates_agent_memory(
        product_connection):
    service, client, peer, _, _, _ = product_connection
    agents = await profiles(client, 2)
    workspace = (await initialize(client))["workspaces"][0]
    ref = await new_session(client, workspace_id=workspace["workspace_id"])
    await coordinate(client, ref, "budget.configure", {"token_limit": 10, "expected_revision": 0})
    original = peer.chat
    observations = {}

    async def known_usage(message, **options):
        scope = service._active_memory_scope()
        observations[message] = {
            "scope": scope, "workspace": service.workspace_tools.root,
            "registry_id": service._current_product_workspace().registry_id,
        }
        options["on_event"]("usage.final", {"run_total_tokens": 2})
        return await original(message, **options)

    peer.chat = known_usage
    started = (await client.call("delegations.start", {
        **ref, "request_id": uuid4().hex,
        **specification(agents, ["hijo-a", "hijo-b"]),
    }))["goal"]
    receipt = await settle(client, ref, started["parent_run_id"])
    goal = await goal_snapshot(client, ref, started)
    assert receipt["status"] == goal["status"] == "completed"
    assert receipt["cleanup_confirmed"] and goal["settled"]
    assert len(peer.calls) == 2  # The host parent never called a billable model.
    assert {child["agent_id"] for child in goal["children"]} == {
        agent["agent_id"] for agent in agents}
    assert len({child["session_id"] for child in goal["children"]}) == 2
    assert {row["registry_id"] for row in observations.values()} == {workspace["workspace_id"]}
    assert len({row["workspace"] for row in observations.values()}) == 1
    scopes = [observations[message]["scope"] for message in ("hijo-a", "hijo-b")]
    assert scopes[0].workspace_id == scopes[1].workspace_id
    assert scopes[0].agent_id != scopes[1].agent_id
    service.memory.upsert(scopes[0], "privada", "MARCADOR_PRIVADO_AGENTE_A_731",
                          source_uri="jarvis://fixture/goal-budget")
    assert service.memory.search(scopes[0], "MARCADOR_PRIVADO_AGENTE_A_731", limit=5).hits
    assert service.memory.search(scopes[1], "MARCADOR_PRIVADO_AGENTE_A_731", limit=5).hits == ()
    summary = await budget(client, ref)
    assert summary["committed_tokens"] == summary["actual_tokens"] == 4
    assert summary["actual_cost"] is None and summary["cost_known"] is False
    parent = summary["reservations"][0]
    assert parent["run_id"] == started["parent_run_id"]
    assert parent["reserved_tokens"] == 10 and parent["actual_tokens"] == 0
    for child in goal["children"]:
        child_ref = {"session_id": child["session_id"], "temporary": False}
        child_budget = await budget(client, child_ref)
        allocation = child_budget["reservations"][0]
        assert allocation["parent_reservation_id"] == parent["reservation_id"]
        assert allocation["reserved_tokens"] == 5 and allocation["actual_tokens"] == 2
        assert allocation["actual_cost"] is None and not allocation["cost_known"]
    assert started["parent_run_id"] not in service.product.non_model_runs


async def test_parent_cancel_waits_for_both_child_cleanups_and_preserves_unknown_budget(
        product_connection):
    service, client, peer, _, _, _ = product_connection
    agents = await profiles(client, 2)
    await initialize(client)
    ref = await new_session(client)
    await coordinate(client, ref, "budget.configure", {"token_limit": 10, "expected_revision": 0})
    original = peer.chat
    entered, cleaning = set(), set()
    both_entered, both_cleaning, cleanup_release = asyncio.Event(), asyncio.Event(), asyncio.Event()
    never = asyncio.Event()

    async def controlled_cleanup(message, **options):
        reply = await original(message, **options)
        entered.add(message)
        if len(entered) == 2:
            both_entered.set()
        try:
            await never.wait()
        finally:
            cleaning.add(message)
            if len(cleaning) == 2:
                both_cleaning.set()
            await cleanup_release.wait()
        return reply

    peer.chat = controlled_cleanup
    started = (await client.call("delegations.start", {
        **ref, "request_id": uuid4().hex,
        **specification(agents, ["cancel-a", "cancel-b"]),
    }))["goal"]
    try:
        await asyncio.wait_for(both_entered.wait(), 5)
        request = {**ref, "parent_run_id": started["parent_run_id"], "goal_id": started["goal_id"]}
        cancelled = await client.call("delegations.cancel", request)
        assert cancelled["accepted"] and not cancelled["settled"]
        await asyncio.wait_for(both_cleaning.wait(), 5)
        active = await goal_snapshot(client, ref, started)
        assert not active["parent_settled"]
        assert service._handles[started["parent_run_id"]].phase.value != "settled"
        held = await budget(client, ref)
        assert held["committed_tokens"] == 10
        assert held["actual_tokens"] is None and held["actual_cost"] is None
        with pytest.raises(RpcError):
            await client.call("runs.start", {**ref, "message": "sin eludir limpieza"})
    finally:
        cleanup_release.set()
    receipt = await settle(client, ref, started["parent_run_id"])
    goal = await goal_snapshot(client, ref, started)
    assert receipt["status"] == "cancelled" and receipt["cleanup_confirmed"]
    assert all(child["settled"] for child in goal["children"])
    for child in goal["children"]:
        child_ref = {"session_id": child["session_id"], "temporary": False}
        row = (await client.call("runs.get", {**child_ref, "run_id": child["run_id"]}))["run"]
        assert row["status"] == "cancelled" and row["cleanup_confirmed"]
        allocation = (await budget(client, child_ref))["reservations"][0]
        assert allocation["status"] == allocation["effect_status"] == "unknown"
        assert allocation["actual_tokens"] is None and allocation["actual_cost"] is None
    held = await budget(client, ref)
    assert held["committed_tokens"] == 10 and not held["usage_known"]
    assert held["actual_cost"] is None and not held["cost_known"]
    with pytest.raises(RpcError):
        await client.call("runs.start", {**ref, "message": "no repetir consumo incierto",
                                        "reservation_tokens": 1})


async def test_host_only_product_action_cannot_turn_the_next_model_run_into_a_budget_bypass(
        product_connection):
    _, client, peer, _, _, _ = product_connection
    agents = await profiles(client, 1)
    await initialize(client)
    ref = await new_session(client)
    await coordinate(client, ref, "budget.configure", {"token_limit": 1, "expected_revision": 0})
    consumed = await client.call("runs.start", {**ref, "message": "usage_missing",
                                               "reservation_tokens": 1})
    assert (await settle(client, ref, consumed["run_id"]))["status"] == "completed"
    before = await budget(client, ref)
    operation = await client.call("product.tool.start", {
        **ref, "tool": "report_presence", "arguments": {"state": "idle", "expected_revision": 0},
        "request_id": uuid4().hex,
    })
    approval = await pending_approval(client, ref, operation["run_id"])
    await decide(client, ref, approval)
    assert (await settle(client, ref, operation["run_id"]))["status"] == "completed"
    after = await budget(client, ref)
    assert after["reservations"] == before["reservations"]
    assert after["committed_tokens"] == 1 and after["actual_tokens"] is None
    assert len(peer.calls) == 1
    with pytest.raises(RpcError):
        await client.call("runs.start", {**ref, "message": "no omitir contabilidad",
                                        "reservation_tokens": 1})
    with pytest.raises(RpcError):
        await client.call("delegations.start", {**ref, "request_id": uuid4().hex,
            **specification(agents, ["no iniciar hijo sin reserva padre"])})
    assert len(peer.calls) == 1


async def test_three_child_cost_slices_are_exact_decimals_and_unknown_billing_stays_unknown(
        product_connection):
    _, client, peer, _, _, _ = product_connection
    agents = await profiles(client, 3)
    await initialize(client)
    ref = await new_session(client)
    await coordinate(client, ref, "budget.configure", {
        "token_limit": 12, "cost_limit": "1", "expected_revision": 0,
    })
    original = peer.chat

    async def known_tokens_only(message, **options):
        options["on_event"]("usage.final", {"run_total_tokens": 2})
        return await original(message, **options)

    peer.chat = known_tokens_only
    started = (await client.call("delegations.start", {
        **ref, "request_id": uuid4().hex,
        **specification(agents, ["coste-a", "coste-b", "coste-c"]),
    }))["goal"]
    assert (await settle(client, ref, started["parent_run_id"]))["status"] == "completed"
    goal = await goal_snapshot(client, ref, started)
    slices = []
    for child in goal["children"]:
        child_ref = {"session_id": child["session_id"], "temporary": False}
        allocation = (await budget(client, child_ref))["reservations"][0]
        slices.append(Decimal(allocation["reserved_cost"]))
        assert allocation["reserved_tokens"] == 4 and allocation["actual_tokens"] == 2
        assert allocation["actual_cost"] is None and not allocation["cost_known"]
    assert slices == [Decimal("0.333333333333")] * 3
    assert sum(slices) <= Decimal("1")
    summary = await budget(client, ref)
    assert summary["committed_tokens"] == summary["actual_tokens"] == 6
    assert summary["committed_cost"] == "1"
    assert summary["actual_cost"] is None and not summary["cost_known"]
    assert len(peer.calls) == 3


async def test_model_parent_and_child_usage_at_shared_envelope_cancels_the_entire_tree(
        product_connection):
    _, client, peer, _, _, _ = product_connection
    agents = await profiles(client, 2)
    await initialize(client)
    ref = await new_session(client)
    await coordinate(client, ref, "budget.configure", {"token_limit": 10, "expected_revision": 0})
    original = peer.chat
    release_second_usage, never_finish_parent = asyncio.Event(), asyncio.Event()
    spec = specification(agents, ["shared-child-a", "shared-child-b"])

    async def parent_with_delegated_usage(message, **options):
        reply = await original(message, **options)
        if message == "shared-model-parent":
            options["on_event"]("usage.update", {"run_total_tokens": 4})
            await options["on_tool_call"]("agents_delegate", spec)
            await never_finish_parent.wait()
        elif message == "shared-child-a":
            options["on_event"]("usage.final", {"run_total_tokens": 3})
        elif message == "shared-child-b":
            await release_second_usage.wait()
            options["on_event"]("usage.final", {"run_total_tokens": 3})
        return reply

    peer.chat = parent_with_delegated_usage
    started = await client.call("runs.start", {**ref, "message": "shared-model-parent",
        "request_id": uuid4().hex, "reservation_tokens": 10})
    try:
        approval = await pending_approval(client, ref, started["run_id"])
        assert approval["tool_name"] == "agents_delegate"
        assert len(peer.calls) == 1
        await decide(client, ref, approval)
        # The first child reaches its own exact cap while the shared tree is at
        # seven. Wait for its real durable completion before the second reaches
        # ten; this distinguishes own-final settlement from ancestor exhaustion.
        async with asyncio.timeout(5):
            while True:
                goals = (await client.call("delegations.list", ref))["goals"]
                if goals and len(goals[0]["children"]) == 2:
                    goal = goals[0]
                    first = next(row for row in goal["children"]
                                 if row["agent_id"] == agents[0]["agent_id"])
                    if first["status"] == "completed":
                        break
                await asyncio.sleep(0.005)
        first_ref = {"session_id": first["session_id"], "temporary": False}
        first_allocation = (await budget(client, first_ref))["reservations"][0]
        assert first_allocation["reserved_tokens"] == first_allocation["actual_tokens"] == 3
        active_parent = (await client.call("runs.get", {**ref, "run_id": started["run_id"]}))["run"]
        assert active_parent["status"] not in {"completed", "failed", "cancelled", "interrupted"}
        release_second_usage.set()
        receipt = await settle(client, ref, started["run_id"])
    finally:
        release_second_usage.set()
    assert receipt["status"] == "cancelled" and receipt["cleanup_confirmed"]
    finished = await goal_snapshot(client, ref, goal)
    assert finished["settled"] and all(child["settled"] for child in finished["children"])
    for child in finished["children"]:
        child_ref = {"session_id": child["session_id"], "temporary": False}
        child_run = (await client.call("runs.get", {**child_ref, "run_id": child["run_id"]}))["run"]
        assert child_run["cleanup_confirmed"]
        allocation = (await budget(client, child_ref))["reservations"][0]
        assert allocation["reserved_tokens"] == allocation["actual_tokens"] == 3
        assert allocation["actual_cost"] is None
    summary = await budget(client, ref)
    assert summary["committed_tokens"] == 10
    assert summary["actual_tokens"] is None and summary["actual_cost"] is None
    assert summary["reservations"][0]["actual_tokens"] is None  # Update was not final billing.
    assert len(peer.calls) == 3
