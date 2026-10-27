"""Real own socket/SQLite coordination with a deterministic synthetic model peer."""

import asyncio
import json
import shutil
import sys
import tempfile
from pathlib import Path
from uuid import uuid4

import pytest

from jarvis_core.protocol import LocalRpcClient, RpcError
from jarvis_core.service import JarvisService


class CoordinatedPeer:
    def __init__(self):
        self.calls = []
        self.holding = asyncio.Event()
        self.release = asyncio.Event()
        self.cleaning = asyncio.Event()
        self.cleanup_release = asyncio.Event()
        self.steers = []
        self.turns = {}
        self.delegate_spec = None

    async def account_status(self):
        return {"connected": True, "mode": "chatgpt"}

    async def aclose(self):
        self.release.set()
        self.cleanup_release.set()

    def active_turn_id(self, thread):
        return self.turns.get(thread)

    async def steer(self, thread, turn, message, *, request_id):
        assert self.turns.get(thread) == turn
        self.steers.append((thread, turn, message, request_id))
        return {"turn_id": turn, "accepted": True}

    async def chat(self, message, *, thread_id, temporary, on_thread, on_event,
                   on_tool_call, tool_specs, history=(), **_options):
        thread = thread_id or "own-" + uuid4().hex
        turn = "turn-" + uuid4().hex
        if thread_id is None:
            on_thread(thread)
        self.turns[thread] = turn
        self.calls.append({"message": message, "thread_id": thread_id, "temporary": temporary,
                           "tool_names": [row["name"] for row in tool_specs],
                           "history": list(history)})
        try:
            if message in {"espera", "limpieza"}:
                self.holding.set()
                try:
                    await self.release.wait()
                finally:
                    if message == "limpieza":
                        self.cleaning.set()
                        await self.cleanup_release.wait()
            if message == "delegar":
                result = await on_tool_call("agents_delegate", self.delegate_spec)
                self.delegate_result = result
            elif message == "catalogo":
                self.catalog_result = await on_tool_call("agents_available", {})
            elif message == "escribe":
                await on_tool_call("workspace_write", {"path": "hijo.txt", "text": "HIJO 7319"})
            on_event("answer.delta", {"text": "Resultado " + message})
            return {"thread_id": thread, "answer": "Resultado " + message}
        finally:
            self.turns.pop(thread, None)


@pytest.fixture
async def coordinated():
    root = "/private/tmp" if sys.platform == "darwin" else tempfile.gettempdir()
    directory = Path(tempfile.mkdtemp(prefix="jvs-coord-", dir=root)).resolve()
    peer = CoordinatedPeer()
    service = JarvisService(directory, peer)
    client = LocalRpcClient(directory, max_frame_bytes=1024 * 1024)
    await service.start()
    await client.connect()
    try:
        yield service, client, peer, directory
    finally:
        peer.release.set()
        peer.cleanup_release.set()
        await client.aclose()
        await service.aclose()
        shutil.rmtree(directory)


async def parent_session(client, temporary=False):
    row = (await client.call("sessions.create", {"title": "Objetivo sintético",
                                                "temporary": temporary}))["session"]
    return {key: row[key] for key in ("session_id", "temporary")}


def spec(*messages, steps=6, timeout=10):
    return {"objective": "Objetivo de prueba", "max_steps": steps,
            "timeout_seconds": timeout, "children": [
                {"agent_id": "jarvis", "expected_revision": 1,
                 "message": message, "max_steps": 3} for message in messages]}


async def terminal(client, ref, run_id):
    async with asyncio.timeout(5):
        while True:
            row = (await client.call("runs.get", {**ref, "run_id": run_id}))["run"]
            if row["status"] in {"completed", "failed", "cancelled", "interrupted"}:
                return row
            await asyncio.sleep(.005)


async def job_snapshot(client, ref, started):
    return (await client.call("delegations.get", {
        **ref, "parent_run_id": started["parent_run_id"], "goal_id": started["goal_id"]}))["goal"]


async def test_two_children_use_distinct_sessions_receipts_and_no_inherited_context(coordinated):
    service, client, peer, _ = coordinated
    ref = await parent_session(client)
    prior = await client.call("runs.start", {**ref, "message": "contexto del padre"})
    await terminal(client, ref, prior["run_id"])
    started = (await client.call("delegations.start", {
        **ref, "request_id": uuid4().hex, **spec("uno", "dos")}))["goal"]
    settled = await terminal(client, ref, started["parent_run_id"])
    job = await job_snapshot(client, ref, started)
    assert settled["status"] == job["status"] == "completed"
    assert settled["answer"] is None and settled["answer_source"] is None
    assert job["settled"] and job["parent_settled"] and job["parent_outcome"] == "known"
    assert job["max_steps"] == job["reserved_steps"] == 6
    assert job["permits_replay"] is False
    assert len({row["run_id"] for row in job["children"]}) == 2
    assert len({row["session_id"] for row in job["children"]}) == 2
    for row in job["children"]:
        child_ref = {"session_id": row["session_id"], "temporary": False}
        child = service.store.get_session(service._ref(child_ref))
        assert child.parent_session_id == ref["session_id"]
        assert row["status"] == "completed" and row["answer_source"] == "model"
        assert row["action_receipts"] == [] and row["settled"]
    for call in peer.calls[1:]:
        assert call["thread_id"] is None and not call["history"]
        assert "agents_delegate" not in call["tool_names"]


async def test_owner_start_idempotence_and_lookup_do_not_replay_even_after_restart(coordinated):
    service, client, peer, directory = coordinated
    ref = await parent_session(client)
    identity = uuid4().hex
    params = {**ref, "request_id": identity, **spec("uno")}
    started = (await client.call("delegations.start", params))["goal"]
    await terminal(client, ref, identity)
    assert len(peer.calls) == 1
    repeat = await client.call("delegations.start", params)
    assert repeat["goal"]["goal_id"] == started["goal_id"]
    assert len(peer.calls) == 1
    await client.aclose()
    await service.aclose()
    second_peer = CoordinatedPeer()
    reopened = JarvisService(directory, second_peer)
    new_client = LocalRpcClient(directory, max_frame_bytes=1024 * 1024)
    await reopened.start()
    await new_client.connect()
    try:
        lookup = await new_client.call("delegations.lookup", {**ref, "request_id": identity})
        assert lookup["goal"]["status"] == "completed"
        assert lookup["historical_ack_established"] is lookup["permits_replay"] is False
        listing = await new_client.call("delegations.list", {**ref, "limit": 1})
        assert len(listing["goals"]) == 1
        assert listing["goals"][0]["goal_id"] == identity
        assert listing["goals"][0]["settled"]
        assert listing["next_offset"] == 1 and listing["has_more"]
        assert (await new_client.call("delegations.list", {**ref, "offset": 1}))["goals"] == []
        await new_client.call("delegations.start", params)
        assert second_peer.calls == []
        with pytest.raises(RpcError):
            await new_client.call("delegations.start", {**params, "objective": "otro"})
        absent = await new_client.call("delegations.lookup", {**ref, "request_id": uuid4().hex})
        assert absent["goal"] is None
    finally:
        await new_client.aclose()
        await reopened.aclose()


async def test_model_delegation_needs_its_own_approval_and_child_effect_needs_another(coordinated):
    service, client, peer, directory = coordinated
    ref = await parent_session(client)
    peer.delegate_spec = spec("escribe")
    started = await client.call("runs.start", {**ref, "message": "delegar"})
    async with asyncio.timeout(3):
        while True:
            pending = (await client.call("approvals.list", ref))["approvals"]
            if pending:
                break
            await asyncio.sleep(.005)
    assert pending[0]["tool_name"] == "agents_delegate" and len(peer.calls) == 1
    assert not (directory / "workspace/hijo.txt").exists()
    await client.call("approvals.decide", {**ref, "approval_id": pending[0]["approval_id"],
                                          "digest": pending[0]["digest"], "allow": True})
    async with asyncio.timeout(3):
        while True:
            active = list(service._active_goals.values())
            if active and active[0].children:
                child_ref = {"session_id": active[0].children[0]["session_id"], "temporary": False}
                pending = (await client.call("approvals.list", child_ref))["approvals"]
                if pending:
                    break
            await asyncio.sleep(.005)
    assert pending[0]["tool_name"] == "workspace_write"
    assert not (directory / "workspace/hijo.txt").exists()
    await client.call("approvals.decide", {**child_ref, "approval_id": pending[0]["approval_id"],
                                          "digest": pending[0]["digest"], "allow": False})
    result = await terminal(client, ref, started["run_id"])
    assert result["status"] == "completed"
    assert result["action_receipts"][0]["tool_name"] == "agents_delegate"
    assert not result["action_receipts"][0]["succeeded"]
    assert not result["action_receipts"][0]["verified"]
    assert not (directory / "workspace/hijo.txt").exists()


async def test_cancel_parent_keeps_custody_until_child_cleanup_and_settlement(coordinated):
    service, client, peer, _ = coordinated
    ref = await parent_session(client)
    started = (await client.call("delegations.start", {
        **ref, "request_id": uuid4().hex, **spec("limpieza", "espera")}))["goal"]
    await asyncio.wait_for(peer.holding.wait(), 2)
    args = {**ref, "parent_run_id": started["parent_run_id"], "goal_id": started["goal_id"]}
    cancelled = await client.call("delegations.cancel", args)
    assert cancelled["accepted"] and not cancelled["settled"]
    await asyncio.wait_for(peer.cleaning.wait(), 2)
    await client.call("delegations.cancel", args)
    job = await job_snapshot(client, ref, started)
    assert job["status"] in {"cancelling", "cancelled"}
    assert service._handles and service._refs
    with pytest.raises(RpcError):
        await client.call("runs.start", {**ref, "message": "siguiente"})
    peer.cleanup_release.set()
    result = await terminal(client, ref, started["parent_run_id"])
    job = await job_snapshot(client, ref, started)
    assert result["status"] == job["status"] == "cancelled"
    assert all(row["settled"] for row in job["children"])
    assert not service._handles and not service._refs


async def test_queue_waits_for_real_cleanup_then_durable_history_and_can_cancel_queued(coordinated):
    service, client, peer, _ = coordinated
    ref = await parent_session(client)
    first = await client.call("runs.start", {**ref, "message": "limpieza"})
    await peer.holding.wait()
    queued = await client.call("runs.enqueue", {**ref, "request_id": uuid4().hex, "message": "dos"})
    retired = await client.call("runs.enqueue", {
        **ref, "request_id": uuid4().hex, "message": "tres"})
    await client.call("runs.cancel", {**ref, "run_id": retired["run_id"]})
    await client.call("runs.cancel", {**ref, "run_id": first["run_id"]})
    await peer.cleaning.wait()
    assert [call["message"] for call in peer.calls] == ["limpieza"]
    peer.cleanup_release.set()
    assert (await terminal(client, ref, first["run_id"]))["status"] == "cancelled"
    blocked = await terminal(client, ref, queued["run_id"])
    assert blocked["status"] == "failed" and blocked["error"] == "run_preparation_failed"
    assert (await terminal(client, ref, retired["run_id"]))["status"] == "cancelled"
    assert [call["message"] for call in peer.calls] == ["limpieza"]
    assert not service._handles


async def test_steer_binds_exact_child_and_request_does_not_repeat_provider_write(coordinated):
    _, client, peer, _ = coordinated
    ref = await parent_session(client)
    started = (await client.call("delegations.start", {
        **ref, "request_id": uuid4().hex, **spec("espera")}))["goal"]
    await peer.holding.wait()
    job = await job_snapshot(client, ref, started)
    identity = uuid4().hex
    args = {**ref, "parent_run_id": started["parent_run_id"], "goal_id": started["goal_id"],
            "child_run_id": job["children"][0]["run_id"], "request_id": identity,
            "message": "Usa únicamente el marcador NUEVO"}
    response = await client.call("delegations.steer", args)
    assert response["status"] == "accepted" and response["permits_replay"] is False
    repeat = await client.call("delegations.steer", args)
    assert repeat["status"] == "unknown" and len(peer.steers) == 1
    with pytest.raises(RpcError):
        await client.call("delegations.steer", {**args, "child_run_id": uuid4().hex})
    with pytest.raises(RpcError):
        await client.call("delegations.steer", {**args, "message": "otro"})
    peer.release.set()
    await terminal(client, ref, started["parent_run_id"])


async def test_parent_route_cannot_read_or_cancel_other_parent_children(coordinated):
    _, client, peer, _ = coordinated
    a, b = await parent_session(client), await parent_session(client)
    started = (await client.call("delegations.start", {
        **a, "request_id": uuid4().hex, **spec("espera")}))["goal"]
    await peer.holding.wait()
    for method in ("delegations.get", "delegations.cancel", "delegations.wait"):
        with pytest.raises(RpcError):
            await client.call(method, {**b, "parent_run_id": started["parent_run_id"],
                                       "goal_id": started["goal_id"]})
    peer.release.set()
    assert (await terminal(client, a, started["parent_run_id"]))["status"] == "completed"


@pytest.mark.parametrize("mutation", ["budget", "revision", "five", "bool", "empty"])
async def test_invalid_or_stale_goal_does_not_start_any_child(coordinated, mutation):
    service, client, peer, _ = coordinated
    ref = await parent_session(client)
    value = spec("uno", "dos")
    if mutation == "budget":
        value["max_steps"] = 5
    elif mutation == "revision":
        value["children"][0]["expected_revision"] = 2
    elif mutation == "five":
        value["children"] *= 3
    elif mutation == "bool":
        value["timeout_seconds"] = True
    elif mutation == "empty":
        value["children"] = []
    with pytest.raises(RpcError):
        await client.call("delegations.start", {**ref, "request_id": uuid4().hex, **value})
    assert peer.calls == [] and not service._active_goals
    assert len(service.store.list_sessions()) == 1


async def test_temporary_parent_and_children_do_not_persist_after_reopen(coordinated):
    service, client, _, directory = coordinated
    ref = await parent_session(client, temporary=True)
    started = (await client.call("delegations.start", {
        **ref, "request_id": uuid4().hex, **spec("uno", "dos")}))["goal"]
    await terminal(client, ref, started["parent_run_id"])
    assert len(service.store.list_sessions(temporary=True)) == 3
    await client.aclose()
    await service.aclose()
    restarted = JarvisService(directory, CoordinatedPeer())
    try:
        assert restarted.store.list_sessions(temporary=True) == ()
        assert restarted.store.list_sessions(temporary=False) == ()
    finally:
        await restarted.aclose()


async def test_wait_timeout_is_observation_not_cancel_or_completion_consumption(coordinated):
    _, client, peer, _ = coordinated
    ref = await parent_session(client)
    started = (await client.call("delegations.start", {
        **ref, "request_id": uuid4().hex, **spec("espera")}))["goal"]
    await peer.holding.wait()
    args = {**ref, "parent_run_id": started["parent_run_id"], "goal_id": started["goal_id"]}
    observed = await client.call("delegations.wait", {**args, "timeout_seconds": 0})
    assert observed["goal"]["status"] == "running"
    peer.release.set()
    await terminal(client, ref, started["parent_run_id"])
    a = await client.call("delegations.wait", args)
    b = await client.call("delegations.wait", args)
    assert a == b and a["goal"]["status"] == "completed"


async def test_goal_deadline_waits_for_child_finalizer_without_renewing_budget(coordinated):
    service, client, peer, _ = coordinated
    ref = await parent_session(client)
    started = (await client.call("delegations.start", {
        **ref, "request_id": uuid4().hex, **spec("limpieza", timeout=1)}))["goal"]
    await asyncio.wait_for(peer.cleaning.wait(), 3)
    held = await job_snapshot(client, ref, started)
    assert held["status"] == "cancelling" and not held["settled"]
    assert service._handles and service._refs
    peer.cleanup_release.set()
    result = await terminal(client, ref, started["parent_run_id"])
    job = await job_snapshot(client, ref, started)
    assert result["status"] == job["status"] == "failed"
    assert job["error_code"] == "coordination_deadline" and job["settled"]
    assert len(peer.calls) == 1


async def test_list_is_read_only_parent_scoped_and_paginates_existing_goals(coordinated):
    _, client, peer, _ = coordinated
    ref, other = await parent_session(client), await parent_session(client)
    ids = []
    for message in ("primero", "segundo", "tercero"):
        row = (await client.call("delegations.start", {
            **ref, "request_id": uuid4().hex, **spec(message)}))["goal"]
        await terminal(client, ref, row["parent_run_id"])
        ids.append(row["goal_id"])
    first = await client.call("delegations.list", {**ref, "limit": 2})
    assert [row["goal_id"] for row in first["goals"]] == list(reversed(ids))[0:2]
    second = await client.call("delegations.list", {**ref, "limit": 2,
                                                   "offset": first["next_offset"]})
    assert [row["goal_id"] for row in second["goals"]] == [ids[0]]
    assert not second["has_more"]
    assert (await client.call("delegations.list", other))["goals"] == []
    assert len(peer.calls) == 3


async def test_goal_preview_bounds_utf8_and_never_promotes_model_text_to_verification(coordinated):
    _, client, peer, _ = coordinated
    original = peer.chat

    async def large_chat(*args, **kwargs):
        result = await original(*args, **kwargs)
        result["answer"] = "ñ" * 14000
        return result

    peer.chat = large_chat
    ref = await parent_session(client)
    started = (await client.call("delegations.start", {
        **ref, "request_id": uuid4().hex, **spec("uno", "dos")}))["goal"]
    await terminal(client, ref, started["parent_run_id"])
    job = await job_snapshot(client, ref, started)
    assert len(json.dumps(job, ensure_ascii=False).encode()) <= 65536
    assert job["snapshot_truncated"]
    for child in job["children"]:
        assert child["answer_truncated"] and child["answer_bytes"] == 28000
        assert len(child["answer"].encode()) == 2048
        assert child["answer_source"] == "model" and child["action_receipts"] == []
        full = await client.call("runs.get", {"session_id": child["session_id"],
                                               "run_id": child["run_id"]})
        assert full["run"]["answer"] == "ñ" * 14000
    listing = (await client.call("delegations.list", ref))["goals"][0]
    assert all(len(child["answer"].encode()) == 256 for child in listing["children"])


async def test_queue_is_bounded_and_duplicate_request_is_not_a_second_turn(coordinated):
    _, client, peer, _ = coordinated
    ref = await parent_session(client)
    first = await client.call("runs.start", {**ref, "message": "espera"})
    await peer.holding.wait()
    queued = {**ref, "request_id": uuid4().hex, "message": "segundo"}
    a = await client.call("runs.enqueue", queued)
    b = await client.call("runs.enqueue", queued)
    assert a == b
    for i in range(3):
        await client.call("runs.enqueue", {**ref, "request_id": uuid4().hex,
                                           "message": "cola " + str(i)})
    with pytest.raises(RpcError):
        await client.call("runs.enqueue", {**ref, "request_id": uuid4().hex, "message": "llena"})
    peer.release.set()
    await terminal(client, ref, first["run_id"])
    await terminal(client, ref, a["run_id"])
    async with asyncio.timeout(3):
        while len(peer.calls) < 5:
            await asyncio.sleep(.005)
    assert [row["message"] for row in peer.calls].count("segundo") == 1


async def test_cross_mode_request_identity_cannot_replace_an_active_run_owner(coordinated):
    service, client, peer, _ = coordinated
    durable, temporary = await parent_session(client), await parent_session(client, temporary=True)
    identity = uuid4().hex
    first = await client.call("runs.start", {
        **durable, "request_id": identity, "message": "espera"})
    await peer.holding.wait()
    with pytest.raises(RpcError):
        await client.call("delegations.start", {**temporary, "request_id": identity, **spec("uno")})
    assert service._refs[identity].session_id == durable["session_id"]
    assert len(peer.calls) == 1 and not service.store.list_runs(service._ref(temporary))
    peer.release.set()
    assert (await terminal(client, durable, first["run_id"]))["status"] == "completed"


async def test_authorized_child_write_has_its_own_exact_receipt_and_independent_bytes(coordinated):
    _, client, _, directory = coordinated
    ref = await parent_session(client)
    started = (await client.call("delegations.start", {
        **ref, "request_id": uuid4().hex, **spec("escribe")}))["goal"]
    async with asyncio.timeout(3):
        while True:
            job = await job_snapshot(client, ref, started)
            if job["children"]:
                child = job["children"][0]
                child_ref = {"session_id": child["session_id"], "temporary": False}
                pending = (await client.call("approvals.list", child_ref))["approvals"]
                if pending:
                    break
            await asyncio.sleep(.005)
    assert not (directory / "workspace/hijo.txt").exists()
    await client.call("approvals.decide", {**child_ref, "approval_id": pending[0]["approval_id"],
                                          "digest": pending[0]["digest"], "allow": True})
    await terminal(client, ref, started["parent_run_id"])
    job = await job_snapshot(client, ref, started)
    assert job["status"] == "completed" and job["settled"]
    assert (directory / "workspace/hijo.txt").read_bytes() == b"HIJO 7319"
    receipt = job["children"][0]["action_receipts"][0]
    assert receipt["tool_name"] == "workspace_write"
    assert receipt["succeeded"] and receipt["verified"] and receipt["effect_status"] == "known"
    assert receipt["operation_id"]


async def test_uncertain_child_effect_stops_parent_without_repeating_delegation(coordinated):
    service, client, peer, directory = coordinated
    ref = await parent_session(client)
    peer.delegate_spec = spec("escribe")
    original = service.workspace_tools._execute

    async def lost_ack(*args, **kwargs):
        await original(*args, **kwargs)
        raise ConnectionError("synthetic lost receipt after real own write")

    service.workspace_tools._execute = lost_ack
    started = await client.call("runs.start", {**ref, "message": "delegar"})
    async with asyncio.timeout(3):
        while True:
            pending = (await client.call("approvals.list", ref))["approvals"]
            if pending:
                break
            await asyncio.sleep(.005)
    await client.call("approvals.decide", {**ref, "approval_id": pending[0]["approval_id"],
                                          "digest": pending[0]["digest"], "allow": True})
    async with asyncio.timeout(3):
        while True:
            active = list(service._active_goals.values())
            if active and active[0].children:
                child_ref = {"session_id": active[0].children[0]["session_id"], "temporary": False}
                pending = (await client.call("approvals.list", child_ref))["approvals"]
                if pending:
                    break
            await asyncio.sleep(.005)
    await client.call("approvals.decide", {**child_ref, "approval_id": pending[0]["approval_id"],
                                          "digest": pending[0]["digest"], "allow": True})
    result = await terminal(client, ref, started["run_id"])
    assert result["status"] == "failed" and result["error"] == "tool_outcome_unknown"
    receipt = result["action_receipts"][0]
    assert receipt["tool_name"] == "agents_delegate" and receipt["effect_status"] == "unknown"
    assert not receipt["succeeded"] and not receipt["verified"]
    assert len(peer.calls) == 2 and (directory / "workspace/hijo.txt").read_bytes() == b"HIJO 7319"


async def test_interrupted_goal_is_observed_after_reopen_without_child_replay(coordinated):
    service, client, _, directory = coordinated
    ref = await parent_session(client)
    parent_ref = service._ref(ref)
    identity = uuid4().hex
    value = spec("no repetir")
    # Construct an admitted-but-interrupted SQL state. No process was killed;
    # this tests recovery of the state the real crash would leave behind.
    service.store.create_run(parent_ref, run_id=identity, user_text=value["objective"])
    service.store.set_run_status(parent_ref, identity, "running")
    goal = service._new_goal(parent_ref, identity, identity, value)
    child = service.store.create_session("Hijo interrumpido", parent_session_id=ref["session_id"])
    child_run = service.store.create_run(child.ref, user_text="no repetir")
    service.store.set_run_status(child.ref, child_run.run_id, "running")
    service.store.append_event(parent_ref, identity, "coordination.child", {
        "goal_id": identity, "index": 0, "agent_id": "jarvis", "agent_revision": 1,
        "session_id": child.ref.session_id, "run_id": child_run.run_id, "max_steps": 3})
    service._active_goals.pop(goal.goal_id)
    await client.aclose()
    await service.aclose()
    peer = CoordinatedPeer()
    restarted = JarvisService(directory, peer)
    new_client = LocalRpcClient(directory, max_frame_bytes=1024 * 1024)
    await restarted.start()
    await new_client.connect()
    try:
        listed = (await new_client.call("delegations.list", ref))["goals"]
        assert len(listed) == 1 and listed[0]["status"] == "interrupted"
        assert listed[0]["parent_outcome"] == "unknown" and listed[0]["settled"]
        child = listed[0]["children"][0]
        assert child["status"] == "interrupted" and child["unknown_effects"]
        assert child["outcome"] == "unknown"
        repeat = await new_client.call("delegations.start", {
            **ref, "request_id": identity, **value})
        assert repeat["goal"]["status"] == "interrupted" and peer.calls == []
    finally:
        await new_client.aclose()
        await restarted.aclose()


async def test_agent_catalog_exposes_current_ids_but_no_instructions_or_grants(coordinated):
    service, client, peer, _ = coordinated
    created = await client.call("agents.create", {
        "name": "Agente de prueba", "instructions": "INSTRUCCIÓN QUE NO SE EXPORTA EN CATÁLOGO"})
    ref = await parent_session(client)
    started = await client.call("runs.start", {**ref, "message": "catalogo"})
    result = await terminal(client, ref, started["run_id"])
    assert result["status"] == "completed"
    assert result["action_receipts"][0]["tool_name"] == "agents_available"
    assert not result["action_receipts"][0]["verified"]
    text = peer.catalog_result["contentItems"][0]["text"]
    receipt = json.loads(text)
    payload = json.loads(receipt["output"])
    profile = next(row for row in payload["agents"]
                   if row["agent_id"] == created["agent"]["agent_id"])
    assert profile["revision"] == 1 and set(profile) == {"agent_id", "name", "revision"}
    assert payload["grants_included"] is payload["instructions_included"] is False
    assert "INSTRUCCIÓN QUE NO SE EXPORTA" not in text
    assert service.authority.list_pending(ref["session_id"]) == ()


async def test_uncertain_predecessor_blocks_queued_successor_before_any_provider_call(coordinated):
    service, client, peer, directory = coordinated
    ref = await parent_session(client)
    original = service.workspace_tools._execute

    async def lost_ack(*args, **kwargs):
        await original(*args, **kwargs)
        raise ConnectionError("synthetic lost acknowledgement")

    service.workspace_tools._execute = lost_ack
    first = await client.call("runs.start", {**ref, "message": "escribe"})
    async with asyncio.timeout(3):
        while True:
            tickets = (await client.call("approvals.list", ref))["approvals"]
            if tickets:
                break
            await asyncio.sleep(.005)
    queued = await client.call("runs.enqueue", {
        **ref, "request_id": uuid4().hex, "message": "No repetir esta escritura"})
    await client.call("approvals.decide", {**ref, "approval_id": tickets[0]["approval_id"],
                                          "digest": tickets[0]["digest"], "allow": True})
    failed = await terminal(client, ref, first["run_id"])
    assert failed["action_receipts"][0]["effect_status"] == "unknown"
    blocked = await terminal(client, ref, queued["run_id"])
    assert blocked["status"] == "failed" and blocked["error"] == "run_preparation_failed"
    assert len(peer.calls) == 1
    assert (directory / "workspace/hijo.txt").read_bytes() == b"HIJO 7319"
    events = service.store.list_events(service._ref(ref), queued["run_id"])
    causal = next(json.loads(row.payload_json) for row in events if row.kind == "queue.blocked")
    assert causal["reason"] == "queued_predecessor_unknown"
    assert causal["predecessor_run_id"] == first["run_id"]
    assert causal["predecessor_receipt_sha256"] and causal["permits_replay"] is False


@pytest.mark.parametrize("acknowledge", [True, False])
async def test_steering_survives_new_binding_compaction_and_rewind(coordinated, acknowledge):
    service, client, peer, _ = coordinated
    ref = await parent_session(client)
    if not acknowledge:
        async def lose_ack(*_args, **_kwargs):
            raise ConnectionError("synthetic lost correction ACK")
        peer.steer = lose_ack
    started = await client.call("runs.start", {**ref, "message": "espera"})
    await peer.holding.wait()
    receipt = await client.call("runs.steer", {
        **ref, "run_id": started["run_id"], "request_id": uuid4().hex,
        "message": "CORRECCIÓN PROPIA 4826; no concede permisos"})
    assert receipt["status"] == ("accepted" if acknowledge else "unknown")
    peer.release.set()
    await terminal(client, ref, started["run_id"])
    source = service._ref(ref)
    service.store.set_provider_thread(source, None)
    follow = await client.call("runs.start", {**ref, "message": "Continuar con contexto propio"})
    await terminal(client, ref, follow["run_id"])
    restored = json.dumps(peer.calls[-1]["history"], ensure_ascii=False)
    assert "CORRECCIÓN PROPIA 4826" in restored
    assert 'historical_ack_established' in restored and 'instructions_current' in restored
    checkpoint = service.store.compact_context(
        source, operation_id=uuid4().hex,
        expected_revision=service.store.context_snapshot(source)["revision"])
    branch, _ = service.store.rewind_session(
        source, expected_revision=checkpoint["revision"], operation_id=uuid4().hex,
        until_sequence=2)
    next_ref = {"session_id": branch.ref.session_id, "temporary": False}
    final = await client.call("runs.start", {**next_ref, "message": "Continuar rama"})
    await terminal(client, next_ref, final["run_id"])
    text = json.dumps(peer.calls[-1]["history"], ensure_ascii=False)
    assert "CORRECCIÓN PROPIA 4826" in text
    assert ('\\"status\\":\\"' + ("accepted" if acknowledge else "unknown") + '\\"') in text
    assert all(item["role"] in {"user", "assistant"} for item in peer.calls[-1]["history"])
