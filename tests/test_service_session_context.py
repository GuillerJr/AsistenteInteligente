"""Native-shaped session APIs through a real local socket and own synthetic peer."""

import json
import shutil
import sys
import tempfile
from pathlib import Path
from uuid import uuid4

import pytest
from test_service_management import ManagementPeer, new_agent, run, session

from jarvis_core.protocol import LocalRpcClient, RpcError
from jarvis_core.service import JarvisService
from jarvis_core.state import SessionRef


@pytest.fixture
async def context_host():
    parent = "/private/tmp" if sys.platform == "darwin" else tempfile.gettempdir()
    root = Path(tempfile.mkdtemp(prefix="jarvis-session-context-", dir=parent)).resolve()
    peer = ManagementPeer()
    service = JarvisService(root / "profile", peer)
    client = LocalRpcClient(root / "profile", max_frame_bytes=1024 * 1024)
    await service.start()
    await client.connect()
    try:
        yield service, client, peer, root
    finally:
        await client.aclose()
        await service.aclose()
        shutil.rmtree(root)


@pytest.mark.parametrize("temporary", [False, True])
async def test_compaction_is_consumed_by_next_turn_with_profile_instructions(
    context_host, temporary
):
    service, client, peer, _ = context_host
    agent = await new_agent(client, instructions="INSTRUCCIÓN EXACTA DEL PERFIL: habla español.")
    ref = await session(client, agent=agent["agent_id"], temporary=temporary)
    await run(client, ref, "MARCADOR INICIAL TOPACIO 4826")
    initial = service.store.list_messages(SessionRef(**ref))
    for index in range(10):
        await run(client, ref, "Mensaje de prueba " + str(index))
    before = (await client.call("sessions.context", ref))["context"]
    op_id = uuid4().hex
    response = await client.call("sessions.compact", {
        **ref, "operation_id": op_id, "expected_revision": before["revision"],
        "retained_decisions": ["No modificar contrato-fase-a.txt"], "keep_recent": 2,
    })
    assert response["session"]["session_id"] == ref["session_id"]
    assert response["context"]["checkpoint"]["receipts_count"] == 11
    assert response["context"]["checkpoint"]["omitted_message_count"] > 0
    await run(client, ref, "¿Cuál era el marcador?")
    _, options = peer.calls[-1]
    assert options["thread_id"] is None
    assert options["instructions"] == "INSTRUCCIÓN EXACTA DEL PERFIL: habla español."
    history = json.dumps(options["history"], ensure_ascii=False)
    assert "MARCADOR INICIAL TOPACIO 4826" in history
    assert "No modificar contrato-fase-a.txt" in history
    assert "permits_replay" in history
    assert service.store.list_messages(SessionRef(**ref))[:len(initial)] == initial
    observed = (
        await client.call("sessions.operation", {**ref, "operation_id": op_id})
    )["operation"]
    assert observed["historical_ack_established"] is False and observed["permits_replay"] is False


async def test_rewind_metadata_branches_without_binding_or_duplicate_effect(context_host):
    service, client, peer, _ = context_host
    ref = await session(client)
    await run(client, ref, "Primera pregunta")
    await run(client, ref, "Pregunta posterior")
    operation = uuid4().hex
    request = {**ref, "operation_id": operation, "expected_revision": 4,
               "until_sequence": 2, "title": "Continuación desde el punto elegido"}
    first = await client.call("sessions.rewind", request)
    second = await client.call("sessions.rewind", request)
    assert first["session"] == second["session"]
    branch = {key: first["session"][key] for key in ("session_id", "temporary")}
    assert first["lineage"]["source_session_id"] == ref["session_id"]
    assert first["lineage"]["effects_reverted"] is False
    assert first["lineage"]["permissions_inherited"] is False
    assert not service.store.list_runs(SessionRef(**branch))
    before_calls = len(peer.calls)
    status = await client.call("sessions.operation", {**ref, "operation_id": operation})
    assert status["operation"]["result"]["session"] == first["session"]
    assert len(peer.calls) == before_calls
    await run(client, branch, "Continúa con una respuesta nueva")
    options = peer.calls[-1][1]
    assert options["thread_id"] is None
    context = json.dumps(options["history"], ensure_ascii=False)
    assert "Primera pregunta" in context and "Pregunta posterior" not in context
    assert "historical_receipts" in context and "effects_reverted" in context
    assert len((await client.call("sessions.list", {}))["sessions"]) == 2


async def test_stale_or_busy_session_mutations_do_not_fall_back(context_host):
    _, client, _, _ = context_host
    ref = await session(client)
    await run(client, ref, "Una pregunta")
    with pytest.raises(RpcError, match="context_revision_changed"):
        await client.call("sessions.compact", {
            **ref, "operation_id": uuid4().hex, "expected_revision": 1,
        })
    for extra in ({"provider_thread_id": "foreign"}, {"temporary": "false"}):
        with pytest.raises(RpcError):
            await client.call("sessions.compact", {
                **ref, "operation_id": uuid4().hex, "expected_revision": 2, **extra,
            })
    with pytest.raises(RpcError):
        await client.call("sessions.rewind", {
            **ref, "expected_revision": 2, "until_sequence": 1,
        })
    with pytest.raises(RpcError):
        await client.call("sessions.rewind", {
            **ref, "expected_revision": 2, "until_sequence": 1,
            "operation_id": uuid4().hex, "title": "ñ" * 257,
        })
    assert (await client.call("sessions.context", ref))["context"]["checkpoint"] is None


async def test_operations_catalog_is_read_only_scoped_and_paginated(context_host):
    _, client, peer, _ = context_host
    ref = await session(client)
    other = await session(client)
    await run(client, ref, "Dato propio")
    identities = []
    for _ in range(2):
        before = (await client.call("sessions.context", ref))["context"]
        identities.append(uuid4().hex)
        await client.call("sessions.compact", {
            **ref, "operation_id": identities[-1], "expected_revision": before["revision"],
        })
    calls = len(peer.calls)
    first = await client.call("sessions.operations", {**ref, "limit": 1})
    assert first["has_more"] is True and first["next_offset"] == 1
    assert first["operations"][0]["operation_id"] == identities[1]
    last = await client.call("sessions.operations", {**ref, "limit": 1, "offset": 1})
    assert last["has_more"] is False and last["next_offset"] is None
    assert last["operations"][0]["operation_id"] == identities[0]
    assert (await client.call("sessions.operations", other))["operations"] == []
    for invalid in ({"limit": 101}, {"offset": -1}, {"limit": True}):
        with pytest.raises(RpcError):
            await client.call("sessions.operations", {**ref, **invalid})
    assert len(peer.calls) == calls


@pytest.mark.parametrize("temporary", [False, True])
async def test_fork_api_preserves_compacted_decisions_and_empty_source_with_exact_identity(
    context_host, temporary
):
    service, client, peer, _ = context_host
    ref = await session(client, temporary=temporary)
    await run(client, ref, "Petición inicial")
    await client.call("sessions.compact", {
        **ref, "expected_revision": 2, "operation_id": uuid4().hex,
        "retained_decisions": ["Decisión literal TOPACIO5192"],
    })
    revision = (await client.call("sessions.context", ref))["context"]["revision"]
    operation = uuid4().hex
    request = {**ref, "expected_revision": revision, "operation_id": operation, "title": "Rama"}
    first = await client.call("sessions.fork", request)
    repeated = await client.call("sessions.fork", request)
    assert first == repeated and first["lineage"]["kind"] == "fork"
    assert first["lineage"]["until_sequence"] == first["lineage"]["source_revision"] == revision
    branch = {key: first["session"][key] for key in ("session_id", "temporary")}
    assert service.store.get_session(SessionRef(**branch)).provider_thread_id is None
    assert not service.store.list_runs(SessionRef(**branch))
    await run(client, branch, "Continuación nueva")
    payload = json.dumps(peer.calls[-1][1]["history"], ensure_ascii=False)
    assert "TOPACIO5192" in payload and "historical_receipts" in payload
    assert peer.calls[-1][1]["thread_id"] is None
    record = (
        await client.call("sessions.operation", {**ref, "operation_id": operation})
    )["operation"]
    assert record["operation_kind"] == "fork" and not record["historical_ack_established"]
    for changed in ({"operation_id": None}, {"operation_id": ""}, {"expected_revision": None},
                    {"expected_revision": True}, {"until_sequence": 1}):
        with pytest.raises(RpcError):
            await client.call("sessions.fork", {**request, **changed})
    empty = await session(client, temporary=temporary)
    empty_operation = uuid4().hex
    empty_branch = await client.call("sessions.fork", {
        **empty, "expected_revision": 0, "operation_id": empty_operation,
    })
    assert empty_branch["lineage"]["source_revision"] == 0
    assert empty_branch["lineage"]["until_sequence"] == 0
    empty_ref = {key: empty_branch["session"][key] for key in ("session_id", "temporary")}
    assert service.store.list_messages(SessionRef(**empty_ref)) == ()
