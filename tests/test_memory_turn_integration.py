"""Signed own-service/SQLite/stdio integration; no account, model or owner data."""

from __future__ import annotations

import asyncio
import json
import tempfile
from pathlib import Path

import pytest
from test_codex_memory_context import memory_wire, write_memory_peer

from jarvis_core.client_identity import ClientIdentity
from jarvis_core.codex import CodexClient
from jarvis_core.protocol import LocalRpcClient
from jarvis_core.service import JarvisService
from jarvis_core.state import SessionRef


@pytest.fixture
async def connected():
    with tempfile.TemporaryDirectory(
        prefix="jmm-", dir=Path(tempfile.gettempdir()).resolve()
    ) as name:
        root = Path(name).resolve()
        provider = CodexClient(write_memory_peer(root / "peer"), root / "p/provider", timeout=2)
        owner = ClientIdentity.generate()
        service = JarvisService(root / "p", provider, bootstrap_public_key=owner.public_key)
        await service.start()
        client = LocalRpcClient(service.directory, identity=owner, max_frame_bytes=1024 * 1024)
        await client.connect()
        try:
            yield service, client, provider, owner
        finally:
            if provider._write_lock.locked():
                provider._write_lock.release()
            await client.aclose()
            await service.aclose()


async def create_session(client):
    session = (await client.call("sessions.create", {"title": "Prueba memoria propia"}))["session"]
    return {key: session[key] for key in ("session_id", "temporary")}


async def enable_recall(client, ref, enabled=True):
    policy = (await client.call("memory.intelligence.status", ref))["intelligence"]["policy"]
    review = (
        await client.call(
            "memory.intelligence.policy.review",
            {
                **ref,
                "settings": {
                    "auto_recall_enabled": enabled,
                    "auto_index_enabled": False,
                    "mode": "lexical",
                    "source_policy": "approved_notes_only",
                },
                "expected_revision": policy["revision"],
                "expected_digest": policy["digest"],
            },
        )
    )["review"]
    return await client.call(
        "memory.intelligence.policy.apply",
        {**ref, "review_id": review["review_id"], "review_digest": review["review_digest"]},
    )


def memory_scope(service, ref):
    return service._memory_scope(SessionRef(ref["session_id"], ref["temporary"]))


def note(service, ref, text="TOPACIO CITA SINTÉTICA ORIGINAL"):
    return service.memory.upsert(
        memory_scope(service, ref),
        "marker",
        text,
        source_uri="jarvis://synthetic/memory-turn",
        title="Prueba aprobada",
    )


async def settled(client, ref, run_id):
    async with asyncio.timeout(8):
        while True:
            run = (await client.call("runs.get", {**ref, "run_id": run_id}))["run"]
            if run["status"] in {"completed", "failed", "cancelled", "interrupted"}:
                return run
            await asyncio.sleep(0.005)


async def send(client, ref, message="TOPACIO"):
    run_id = (await client.call("runs.start", {**ref, "message": message}))["run_id"]
    return run_id, await settled(client, ref, run_id)


async def prepared(service, run_id):
    async with asyncio.timeout(5):
        while run_id not in service._memory_leases or run_id not in service._memory_epochs:
            await asyncio.sleep(0.005)


async def test_signed_off_status_and_turn_keep_cold_memory_unopened(connected):
    service, client, provider, _owner = connected
    ref = await create_session(client)
    assert service.memory._disk is None
    status = (await client.call("memory.intelligence.status", ref))["intelligence"]
    assert status["index"]["state"] == "not_opened"
    _, run = await send(client, ref, "Pregunta de prueba sin memoria")
    assert run["status"] == "completed" and service.memory._disk is None
    assert not (service.directory / "memory.sqlite3").exists()
    turn = [row["params"] for row in memory_wire(provider) if row.get("method") == "turn/start"]
    assert len(turn) == 1 and len(turn[0]["input"]) == 1
    assert service.memory_intelligence._engine is None


async def test_each_service_turn_has_current_citations_without_durable_capsule_messages(connected):
    service, client, provider, _owner = connected
    ref = await create_session(client)
    record = note(service, ref)
    await enable_recall(client, ref)
    first, run = await send(client, ref)
    assert run["status"] == "completed"
    second, run2 = await send(client, ref)
    assert run2["status"] == "completed" and second != first
    rows = memory_wire(provider)
    turns = [row["params"] for row in rows if row.get("method") == "turn/start"]
    assert len(turns) == 2 and turns[0]["threadId"] == turns[1]["threadId"]
    for turn in turns:
        assert len(turn["input"]) == 2
        payload = json.loads(turn["input"][0]["text"].split("\n", 1)[1])
        assert payload["usage"] == "untrusted_context_data" and payload["permissions"] is False
        citation = payload["notes"][0]
        assert (
            citation["document_id"] == record.document_id and citation["source_verified"] is False
        )
        assert citation["excerpt"] == "TOPACIO CITA SINTÉTICA ORIGINAL"
        assert turn["input"][-1]["text"] == "TOPACIO"
    messages = (await client.call("sessions.messages", ref))["messages"]
    assert "CITA SINTÉTICA" not in json.dumps(messages)
    events = (await client.call("events.poll", {**ref, "run_id": second}))["events"]
    assert "CITA SINTÉTICA" not in json.dumps(events)
    assert not service._memory_leases and not service._handles


@pytest.mark.parametrize("mutation", ["policy", "correct", "forget"])
async def test_revocation_correction_and_forget_recheck_after_wait_before_first_byte(
    connected, mutation
):
    service, client, provider, _owner = connected
    ref = await create_session(client)
    record = note(service, ref)
    await enable_recall(client, ref)
    await client.call("service.status")  # Warm only our own stdio peer.
    await provider._write_lock.acquire()
    run_id = (await client.call("runs.start", {**ref, "message": "TOPACIO"}))["run_id"]
    try:
        await prepared(service, run_id)
        if mutation == "policy":
            await enable_recall(client, ref, False)
        elif mutation == "correct":
            service.memory.upsert(
                memory_scope(service, ref),
                "marker",
                "TOPACIO CITA CORREGIDA",
                expected_revision=record.revision,
                source_uri="jarvis://synthetic/memory-turn",
            )
        else:
            service.memory.delete(
                memory_scope(service, ref), "marker", expected_revision=record.revision
            )
    finally:
        provider._write_lock.release()
    run = await settled(client, ref, run_id)
    assert run["status"] == "failed" and run["answer"] is None and not run["action_receipts"]
    rows = memory_wire(provider)
    assert not [row for row in rows if row.get("method") == "turn/start"]
    assert "CITA SINTÉTICA" not in json.dumps(rows) and "CITA CORREGIDA" not in json.dumps(rows)
    assert not service._memory_leases and not service._handles
    if mutation == "forget":
        assert not service.memory.list_documents(memory_scope(service, ref))


async def test_owner_policy_and_recall_banner_do_not_cross_clients_or_chats(connected):
    service, client, provider, owner = connected
    ref, other_chat = await create_session(client), await create_session(client)
    note(service, ref)
    await enable_recall(client, ref)
    first, run = await send(client, ref)
    assert run["status"] == "completed"
    status = (await client.call("memory.intelligence.status", other_chat))["intelligence"]
    assert status["last_recall"] is None
    second_owner = ClientIdentity.generate()
    review = service.client_authority.review(
        owner.client_id,
        {"public_key": second_owner.public_key, "scope": "owner", "expected_revision": 1},
    )
    service.client_authority.admit(
        owner.client_id, {key: review[key] for key in ("review_id", "review_digest")}
    )
    async with LocalRpcClient(service.directory, identity=second_owner) as other:
        status = (await other.call("memory.intelligence.status", ref))["intelligence"]
        assert not status["policy"]["settings"]["auto_recall_enabled"]
        assert status["last_recall"] is None
        second, result = await send(other, ref)
        assert result["status"] == "completed"
        assert (await other.call("memory.intelligence.status", ref))["intelligence"]["last_recall"][
            "run_id"
        ] == second
    own = (await client.call("memory.intelligence.status", ref))["intelligence"]
    assert own["last_recall"]["run_id"] == first
    latest = [row["params"] for row in memory_wire(provider) if row.get("method") == "turn/start"][
        -1
    ]
    assert len(latest["input"]) == 1 and "CITA SINTÉTICA" not in json.dumps(latest)
    assert not [row for row in memory_wire(provider) if row.get("method") == "thread/list"]


async def test_cancel_before_provider_write_settles_without_replaying_capsule(connected):
    service, client, provider, _owner = connected
    ref = await create_session(client)
    note(service, ref)
    await enable_recall(client, ref)
    await client.call("service.status")
    await provider._write_lock.acquire()
    run_id = (await client.call("runs.start", {**ref, "message": "TOPACIO"}))["run_id"]
    try:
        await prepared(service, run_id)
        assert (await client.call("runs.cancel", {**ref, "run_id": run_id}))["accepted"]
    finally:
        provider._write_lock.release()
    run = await settled(client, ref, run_id)
    assert run["status"] == "cancelled" and run["answer"] is None
    assert not [row for row in memory_wire(provider) if row.get("method") == "turn/start"]
    assert not service._handles and not service._memory_leases
    assert not service.memory_intelligence.blockers
    assert not provider._pending and not provider._events
