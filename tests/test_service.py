"""Coordinator E2E with real private socket/SQLite and a synthetic model peer."""

import asyncio
import shutil
import sys
import tempfile
from pathlib import Path
from uuid import uuid4

import pytest

from jarvis_core.codex import CodexError
from jarvis_core.protocol import LocalRpcClient, RpcError
from jarvis_core.service import JarvisService


class ModelPeer:
    def __init__(self):
        self.calls = []
        self.entered = asyncio.Event()
        self.cleaning = asyncio.Event()
        self.release = asyncio.Event()
        self.closed = False

    async def account_status(self):
        return {"connected": True, "mode": "chatgpt"}

    async def login(self):
        return {"url": "https://auth.openai.com/fixture"}

    async def logout(self):
        return {"connected": False, "mode": None}

    async def chat(self, message, *, thread_id, temporary, on_thread, on_event, **_options):
        self.calls.append((message, thread_id, temporary))
        if thread_id is None:
            on_thread("owned-" + uuid4().hex)
        self.entered.set()
        if message == "fallo":
            raise CodexError("codex_turn_failed")
        if message == "espera":
            try:
                await asyncio.Event().wait()
            finally:
                self.cleaning.set()
                await self.release.wait()
        on_event("answer.delta", {"text": "Respuesta"})
        return {"thread_id": thread_id, "answer": "Respuesta " + message}

    async def aclose(self):
        self.closed = True


@pytest.fixture
async def connection():
    temporary_root = "/private/tmp" if sys.platform == "darwin" else tempfile.gettempdir()
    directory = Path(tempfile.mkdtemp(prefix="jvs-", dir=temporary_root)).resolve()
    provider = ModelPeer()
    service = JarvisService(directory, provider)
    client = LocalRpcClient(directory, max_frame_bytes=1024 * 1024)
    await service.start()
    await client.connect()
    try:
        yield service, client, provider, directory
    finally:
        provider.release.set()
        await client.aclose()
        await service.aclose()
        shutil.rmtree(directory)


async def session(client, *, temporary=False):
    result = await client.call("sessions.create", {"title": "Prueba", "temporary": temporary})
    return {key: result["session"][key] for key in ("session_id", "temporary")}


async def settled(client, ref, run_id):
    async with asyncio.timeout(5):
        while True:
            run = (await client.call("runs.get", {**ref, "run_id": run_id}))["run"]
            if run["status"] in {"completed", "failed", "cancelled", "interrupted"}:
                return run
            await asyncio.sleep(0.005)


async def test_socket_chat_persists_receipt_messages_events_and_reuses_own_thread(connection):
    service, client, provider, directory = connection
    assert (await client.call("service.status"))["capabilities"]["baseline_complete"] is False
    ref = await session(client)
    first = await client.call("runs.start", {**ref, "message": "uno"})
    run = await settled(client, ref, first["run_id"])
    assert run["status"] == "completed" and run["answer"] == "Respuesta uno"
    assert run["answer_source"] == "model" and run["action_receipts"] == []
    second = await client.call("runs.start", {**ref, "message": "dos"})
    await settled(client, ref, second["run_id"])
    assert provider.calls[0][1] is None and provider.calls[1][1].startswith("owned-")
    messages = (await client.call("sessions.messages", ref))["messages"]
    assert [(m["role"], m["text"]) for m in messages] == [
        ("user", "uno"), ("assistant", "Respuesta uno"),
        ("user", "dos"), ("assistant", "Respuesta dos"),
    ]
    events = (await client.call("events.poll", {**ref, "run_id": first["run_id"]}))["events"]
    assert events[-1]["kind"] == "run_settled"
    assert any(event["kind"] == "answer.delta" for event in events)
    assert not service._handles
    assert (directory / "jarvis.sqlite3").is_file()


async def test_idempotent_request_does_not_replay_turn_and_conflicts_fail(connection):
    _, client, provider, _ = connection
    ref, identity = await session(client), uuid4().hex
    params = {**ref, "message": "único", "request_id": identity}
    a = await client.call("runs.start", params)
    b = await client.call("runs.start", params)
    assert a["run_id"] == b["run_id"] == identity
    await settled(client, ref, identity)
    await client.call("runs.start", params)
    assert len(provider.calls) == 1
    with pytest.raises(RpcError):
        await client.call("runs.start", {**params, "message": "diferente"})
    assert len(provider.calls) == 1


async def test_request_identity_collision_cannot_reroute_another_session(connection):
    _, client, provider, _ = connection
    durable, temporary = await session(client), await session(client, temporary=True)
    identity = uuid4().hex
    started = await client.call("runs.start", {
        **durable, "message": "espera", "request_id": identity,
    })
    await provider.entered.wait()
    with pytest.raises(RpcError):
        await client.call("runs.start", {
            **temporary, "message": "otra", "request_id": identity,
        })
    assert len(provider.calls) == 1
    await client.call("runs.cancel", {**durable, "run_id": started["run_id"]})
    provider.release.set()
    await settled(client, durable, identity)
    assert (await client.call("sessions.messages", temporary))["messages"] == []


async def test_cancel_receipt_precedes_settlement_and_blocks_delete(connection):
    _, client, provider, _ = connection
    ref = await session(client)
    result = await client.call("runs.start", {**ref, "message": "espera"})
    await provider.entered.wait()
    params = {**ref, "run_id": result["run_id"]}
    assert (await client.call("runs.cancel", params))["accepted"]
    await provider.cleaning.wait()
    assert (await client.call("runs.get", params))["run"]["status"] == "cancelling"
    with pytest.raises(RpcError):
        await client.call("sessions.delete", ref)
    with pytest.raises(RpcError):
        await client.call("provider.logout")
    assert not (await client.call("runs.cancel", params))["accepted"]
    provider.release.set()
    run = await settled(client, ref, result["run_id"])
    assert run["status"] == "cancelled" and run["answer"] is None
    assert run["outcome"] == "unknown"
    assert (await client.call("sessions.delete", ref))["deleted"]


async def test_provider_failure_is_recorded_as_failure_without_assistant_claim(connection):
    _, client, _, _ = connection
    ref = await session(client)
    started = await client.call("runs.start", {**ref, "message": "fallo"})
    run = await settled(client, ref, started["run_id"])
    assert run["status"] == "failed" and run["error"] == "codex_turn_failed"
    assert run["answer"] is None
    assert len((await client.call("sessions.messages", ref))["messages"]) == 1


async def test_temporary_chat_is_not_in_durable_database_and_disappears_on_restart(connection):
    service, client, provider, directory = connection
    ref = await session(client, temporary=True)
    run = await client.call("runs.start", {**ref, "message": "secreto-sintético"})
    await settled(client, ref, run["run_id"])
    assert provider.calls[0][2] is True
    assert not (directory / "jarvis.sqlite3").exists()
    with pytest.raises(RpcError):
        await client.call("sessions.messages", {**ref, "temporary": False})
    await client.aclose()
    await service.aclose()
    replacement = JarvisService(directory, ModelPeer())
    await replacement.start()
    try:
        async with LocalRpcClient(directory) as new_client:
            with pytest.raises(RpcError):
                await new_client.call("sessions.messages", ref)
    finally:
        await replacement.aclose()


async def test_durable_session_continuation_survives_service_restart(connection):
    service, client, _, directory = connection
    ref = await session(client)
    run = await client.call("runs.start", {**ref, "message": "recordado"})
    await settled(client, ref, run["run_id"])
    await client.aclose()
    await service.aclose()
    model = ModelPeer()
    replacement = JarvisService(directory, model)
    await replacement.start()
    try:
        async with LocalRpcClient(directory) as new_client:
            run2 = await new_client.call("runs.start", {**ref, "message": "continúa"})
            await settled(new_client, ref, run2["run_id"])
            assert model.calls[0][1].startswith("owned-")
    finally:
        await replacement.aclose()


@pytest.mark.parametrize("method,params", [
    ("sessions.create", {"temporary": "true"}),
    ("sessions.list", {"include_archived": 1}),
    ("service.status", {"token": "untrusted"}),
    ("provider.login", {"provider": "nvidia"}),
])
async def test_rejects_unknown_or_mistyped_parameters(connection, method, params):
    _, client, _, _ = connection
    with pytest.raises(RpcError):
        await client.call(method, params)


async def test_shutdown_cancels_then_waits_for_cleanup_before_closing_provider(connection):
    service, client, provider, _ = connection
    ref = await session(client)
    await client.call("runs.start", {**ref, "message": "espera"})
    await provider.entered.wait()
    await client.aclose()
    closing = asyncio.create_task(service.aclose())
    await provider.cleaning.wait()
    assert not closing.done() and not provider.closed
    provider.release.set()
    await closing
    assert provider.closed


async def test_duplicate_cli_start_fails_cleanly_without_replacing_socket(connection):
    _, client, _, directory = connection
    source = str(Path(__file__).resolve().parents[1] / "src")
    process = await asyncio.create_subprocess_exec(
        sys.executable, "-m", "jarvis_core.service", "--state", str(directory),
        "--codex", sys.executable, env={"PYTHONPATH": source},
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
    )
    output, error = await asyncio.wait_for(process.communicate(), 5)
    assert process.returncode == 1 and not output
    assert "No se pudo iniciar" in error.decode()
    assert b"Traceback" not in error
    assert (await client.call("health"))["service"] == "jarvis"
