"""Real RPC, SQLite and disposable files with a synthetic dynamic-tool peer.

Every decision below is fixture input, never an approval on the owner's account,
installed app, private files or actual external server. These tests exercise the
single production Service -> Bridge -> Runtime -> Authority path.
"""

import asyncio
import hashlib
import json
import shutil
import sys
import tempfile
from pathlib import Path
from uuid import uuid4

import pytest

from jarvis_core.protocol import LocalRpcClient, RpcError
from jarvis_core.service import JarvisService
from jarvis_core.state import SessionRef


class DynamicPeer:
    def __init__(self):
        self.plans = {}
        self.calls = []
        self.replies = []
        self.pending_backend_closes = False
        self.finish_backend = asyncio.Event()
        self.release_cleanup = asyncio.Event()
        self.release_cleanup.set()
        self.cleaning = asyncio.Event()
        self.tasks = set()

    async def account_status(self):
        return {"connected": True, "mode": "chatgpt"}

    async def chat(self, message, *, on_tool_call, on_event, on_thread, **options):
        self.calls.append((message, options))
        identity = options["thread_id"] or "synthetic_" + uuid4().hex
        if options["thread_id"] is None:
            on_thread(identity)

        async def invoke(name, arguments):
            result = await on_tool_call(name, arguments)
            self.replies.append(result)

        owned_tasks = []
        try:
            for name, arguments in self.plans.get(message, ()):
                if self.pending_backend_closes:
                    task = asyncio.create_task(invoke(name, arguments))
                    owned_tasks.append(task)
                    self.tasks.add(task)
                else:
                    await invoke(name, arguments)
            if self.pending_backend_closes:
                await self.finish_backend.wait()
            answer = "Respuesta sintética: " + message
            on_event("answer.delta", {"text": answer})
            return {"thread_id": identity, "answer": answer}
        except asyncio.CancelledError:
            self.cleaning.set()
            await self.release_cleanup.wait()
            raise
        finally:
            for task in owned_tasks:
                if not task.done():
                    task.cancel()
            await asyncio.gather(*owned_tasks, return_exceptions=True)
            self.tasks.difference_update(owned_tasks)

    async def aclose(self):
        self.release_cleanup.set()
        self.finish_backend.set()


@pytest.fixture
async def capabilities():
    base = "/private/tmp" if sys.platform == "darwin" else tempfile.gettempdir()
    root = Path(tempfile.mkdtemp(prefix="jarvis-capabilities-", dir=base)).resolve()
    peer = DynamicPeer()
    service = JarvisService(root / "profile", peer)
    client = LocalRpcClient(root / "profile", max_frame_bytes=1024 * 1024)
    await service.start()
    await client.connect()
    try:
        yield service, client, peer, root
    finally:
        peer.release_cleanup.set()
        peer.finish_backend.set()
        await client.aclose()
        await service.aclose()
        shutil.rmtree(root)


async def create_session(client, *, temporary=False):
    record = (
        await client.call(
            "sessions.create",
            {
                "title": "Prueba sintética",
                "temporary": temporary,
            },
        )
    )["session"]
    return {"session_id": record["session_id"], "temporary": record["temporary"]}


async def settled(client, ref, run_id):
    async with asyncio.timeout(4):
        while True:
            result = (await client.call("runs.get", {**ref, "run_id": run_id}))["run"]
            if result["status"] in {"completed", "failed", "cancelled", "interrupted"}:
                return result
            await asyncio.sleep(0.002)


async def pending(client, ref, run_id):
    async with asyncio.timeout(4):
        while True:
            tickets = (await client.call("approvals.list", {**ref, "run_id": run_id}))["approvals"]
            if tickets:
                assert len(tickets) == 1
                return tickets[0]
            result = (await client.call("runs.get", {**ref, "run_id": run_id}))["run"]
            assert result["status"] not in {"completed", "failed", "cancelled"}, result
            await asyncio.sleep(0.002)


async def fixture_decision(client, ref, ticket, *, allow=True):
    return await client.call(
        "approvals.decide",
        {
            **ref,
            "approval_id": ticket["approval_id"],
            "digest": ticket["digest"],
            "allow": allow,
        },
    )


async def approved_run(client, ref, message):
    run_id = (await client.call("runs.start", {**ref, "message": message}))["run_id"]
    ticket = await pending(client, ref, run_id)
    assert (await fixture_decision(client, ref, ticket))["accepted"]
    return await settled(client, ref, run_id)


def provider_receipt(peer, offset=-1):
    return json.loads(peer.replies[offset]["contentItems"][0]["text"])


async def test_exact_approval_scope_digest_one_use_and_verified_file_receipt(capabilities):
    service, client, peer, _ = capabilities
    ref = await create_session(client)
    other = await create_session(client)
    arguments = {"path": "nota.txt", "text": "Línea de prueba: áéíóú\nsegunda línea"}
    peer.plans["escribe"] = [("workspace_write", arguments)]
    run_id = (await client.call("runs.start", {**ref, "message": "escribe"}))["run_id"]
    ticket = await pending(client, ref, run_id)
    assert ticket["session_id"] == ref["session_id"] and ticket["run_id"] == run_id
    assert ticket["tool_name"] == "workspace_write"
    assert ticket["arguments"] == json.loads(ticket["arguments_json"]) == arguments
    assert len(ticket["digest"]) == 64
    assert (await client.call("runs.get", {**ref, "run_id": run_id}))["run"][
        "status"
    ] == "waiting_approval"
    assert (await client.call("approvals.list", other))["approvals"] == []
    assert not (await fixture_decision(client, other, ticket))["accepted"]
    assert not (await fixture_decision(client, ref, {**ticket, "digest": "0" * 64}))["accepted"]
    with pytest.raises(RpcError):
        await fixture_decision(client, {**ref, "temporary": True}, ticket)
    with pytest.raises(RpcError):
        await fixture_decision(client, ref, ticket, allow=1)
    assert not (service._workspace_path / "nota.txt").exists()
    assert (await fixture_decision(client, ref, ticket))["accepted"]
    run = await settled(client, ref, run_id)
    assert run["status"] == "completed" and run["answer_source"] == "model"
    assert (service._workspace_path / "nota.txt").read_text() == arguments["text"]
    receipt = run["action_receipts"][0]
    assert receipt["succeeded"] and receipt["verified"] and receipt["effect_status"] == "known"
    assert receipt["operation_id"] and receipt["tool_name"] == "workspace_write"
    evidence = json.loads(receipt["output"])
    assert evidence["sha256"] == hashlib.sha256(arguments["text"].encode()).hexdigest()
    full_receipt = provider_receipt(peer)
    assert full_receipt["operation_id"] == receipt["operation_id"]
    assert receipt["output_sha256"] == hashlib.sha256(full_receipt["output"].encode()).hexdigest()
    assert not (await fixture_decision(client, ref, ticket))["accepted"]
    events = (await client.call("events.poll", {**ref, "run_id": run_id}))["events"]
    reported = [
        event["payload"]["receipt"] for event in events if event["kind"] == "tool.completed"
    ]
    assert reported == [receipt]
    assert not service._handles and not service.authority.list_pending()


@pytest.mark.parametrize("decision", ["deny", "revoke", "cancel"])
async def test_rejected_revoked_or_cancelled_ticket_cannot_write_later(capabilities, decision):
    service, client, peer, _ = capabilities
    ref = await create_session(client)
    peer.plans["escribe"] = [("workspace_write", {"path": "sin-efecto.txt", "text": "NO"})]
    run_id = (await client.call("runs.start", {**ref, "message": "escribe"}))["run_id"]
    ticket = await pending(client, ref, run_id)
    if decision == "deny":
        assert (await fixture_decision(client, ref, ticket, allow=False))["accepted"]
    elif decision == "revoke":
        result = await client.call("approvals.revoke", ref)
        assert result == {"revoked": True, "count": 1}
    else:
        assert (await client.call("runs.cancel", {**ref, "run_id": run_id}))["accepted"]
    run = await settled(client, ref, run_id)
    assert run["status"] == ("cancelled" if decision == "cancel" else "failed")
    assert not run["action_receipts"] and not peer.replies
    assert not (service._workspace_path / "sin-efecto.txt").exists()
    assert not (await fixture_decision(client, ref, ticket))["accepted"]
    assert not (await client.call("approvals.list", ref))["approvals"]


async def test_pending_approval_blocks_workspace_switch_and_delete_until_cleanup(capabilities):
    service, client, peer, root = capabilities
    ref = await create_session(client)
    alternate = root / "otra-carpeta"
    alternate.mkdir(mode=0o700)
    peer.plans["escribe"] = [("workspace_write", {"path": "nunca.txt", "text": "NO"})]
    peer.release_cleanup.clear()
    run_id = (await client.call("runs.start", {**ref, "message": "escribe"}))["run_id"]
    await pending(client, ref, run_id)
    with pytest.raises(RpcError):
        await client.call("workspace.set", {"path": str(alternate)})
    assert (await client.call("runs.cancel", {**ref, "run_id": run_id}))["accepted"]
    await asyncio.wait_for(peer.cleaning.wait(), 2)
    for method, params in (
        ("workspace.set", {"path": str(alternate)}),
        ("sessions.delete", ref),
        ("runs.start", {**ref, "message": "siguiente"}),
    ):
        with pytest.raises(RpcError):
            await client.call(method, params)
    assert service._handles and not list(alternate.iterdir())
    peer.release_cleanup.set()
    await settled(client, ref, run_id)
    assert (await client.call("workspace.set", {"path": str(alternate)}))["workspace"]["available"]
    assert (await client.call("sessions.delete", ref))["deleted"]


async def test_provider_ends_during_approval_invalidates_card_before_any_effect(capabilities):
    service, client, peer, _ = capabilities
    ref = await create_session(client)
    peer.pending_backend_closes = True
    peer.plans["escribe"] = [("workspace_write", {"path": "no-repetir.txt", "text": "NO"})]
    run_id = (await client.call("runs.start", {**ref, "message": "escribe"}))["run_id"]
    ticket = await pending(client, ref, run_id)
    peer.finish_backend.set()
    run = await settled(client, ref, run_id)
    assert run["status"] == "cancelled" and run["error"] == "provider_turn_closed"
    assert not run["action_receipts"] and not peer.replies and not peer.tasks
    assert not (service._workspace_path / "no-repetir.txt").exists()
    assert not (await fixture_decision(client, ref, ticket))["accepted"]
    assert not service._adapter.bridge._turns and not service.authority.list_pending()


async def test_real_read_receipt_is_bounded_but_full_hash_and_model_result_match(capabilities):
    service, client, peer, _ = capabilities
    ref = await create_session(client)
    text = "á" * 1000
    (service._workspace_path / "lectura.txt").write_text(text)
    peer.plans["lee"] = [("workspace_read", {"path": "lectura.txt"})]
    run_id = (await client.call("runs.start", {**ref, "message": "lee"}))["run_id"]
    run = await settled(client, ref, run_id)
    receipt = run["action_receipts"][0]
    full = provider_receipt(peer)
    assert run["status"] == "completed" and receipt["verified"]
    assert json.loads(full["output"])["text"] == text
    assert receipt["output_truncated"] and len(receipt["output"].encode()) <= 512
    assert receipt["output_bytes"] == len(full["output"].encode())
    assert receipt["output_sha256"] == hashlib.sha256(full["output"].encode()).hexdigest()
    assert not service.authority.list_pending()


async def test_post_write_verification_failure_preserves_unknown_receipt_without_replay(
    capabilities,
    monkeypatch,
):
    service, client, peer, _ = capabilities
    ref = await create_session(client)
    original = service.workspace_tools._read

    def changed_read(parent, name):
        data, info = original(parent, name)
        return (b"cambio observado", info) if name == "incierto.txt" else (data, info)

    monkeypatch.setattr(service.workspace_tools, "_read", changed_read)
    peer.plans["escribe"] = [("workspace_write", {"path": "incierto.txt", "text": "aplicado"})]
    run = await approved_run(client, ref, "escribe")
    assert run["status"] == "failed" and run["answer"] is None
    assert run["error"] == "tool_outcome_unknown" and run["outcome"] == "unknown"
    assert (service._workspace_path / "incierto.txt").read_text() == "aplicado"
    assert len(run["action_receipts"]) == 1 and not peer.replies
    receipt = run["action_receipts"][0]
    assert receipt["effect_status"] == "unknown" and not receipt["verified"]
    assert not receipt["succeeded"]
    following = (await client.call("runs.start", {**ref, "message": "continúa"}))["run_id"]
    assert (await settled(client, ref, following))["status"] == "completed"
    assert len(peer.calls) == 2
    assert peer.calls[1][1]["thread_id"] is None and peer.calls[1][1]["history"] == []
    assert (service._workspace_path / "incierto.txt").read_text() == "aplicado"


async def test_memory_is_workspace_shared_temporary_isolated_and_citations_expire(capabilities):
    service, client, peer, _ = capabilities
    a, b = await create_session(client), await create_session(client)
    temporary = await create_session(client, temporary=True)
    other_temporary = await create_session(client, temporary=True)
    peer.plans["guarda"] = [("memory_store", {"key": "nota", "text": "Planeta violeta"})]
    stored = await approved_run(client, a, "guarda")
    assert stored["action_receipts"][0]["verified"]
    notes = (await client.call("memory.list", b))["documents"]
    assert len(notes) == 1 and notes[0]["key"] == "nota" and notes[0]["revision"] == 1
    assert notes[0]["source_uri"] == "jarvis://provided-text" and not notes[0]["source_verified"]
    assert not (await client.call("memory.list", temporary))["documents"]
    result = await client.call("memory.search", {**b, "query": "violeta"})
    assert len(result["matches"]) == 1 and result["mode"] in {"fts5_lexical", "lexical_scan"}
    citation = result["matches"][0]["citation"]
    resolved = await client.call("memory.resolve", {**b, "citation": citation})
    assert resolved["verified"] and not resolved["external_source_verified"]
    assert "violeta" in resolved["excerpt"]
    await approved_run(client, temporary, "guarda")
    assert len((await client.call("memory.list", temporary))["documents"]) == 1
    assert not (await client.call("memory.list", other_temporary))["documents"]
    peer.plans["actualiza"] = [
        (
            "memory_store",
            {
                "key": "nota",
                "text": "Planeta naranja",
                "expected_revision": 1,
            },
        )
    ]
    assert (await approved_run(client, b, "actualiza"))["action_receipts"][0]["verified"]
    with pytest.raises(RpcError, match="cita"):
        await client.call("memory.resolve", {**a, "citation": citation})
    with pytest.raises(RpcError):
        await client.call(
            "memory.search", {**a, "query": "violeta", "generation": result["generation"]}
        )
    with pytest.raises(RpcError):
        await client.call("memory.list", {**a, "workspace_id": "injected"})
    assert (await client.call("sessions.delete", temporary))["deleted"]
    assert (await client.call("sessions.delete", a))["deleted"]
    assert (await client.call("memory.list", b))["documents"][0]["revision"] == 2
    assert service.memory is not None


async def test_temporary_memory_never_opens_disk_and_vanishes_with_its_session(capabilities):
    service, client, peer, _ = capabilities
    ref = await create_session(client, temporary=True)
    peer.plans["temporal"] = [("memory_store", {"key": "nota", "text": "Solo temporal"})]
    assert (await approved_run(client, ref, "temporal"))["status"] == "completed"
    assert len((await client.call("memory.list", ref))["documents"]) == 1
    assert not (service.directory / "jarvis.sqlite3").exists()
    assert not (service.directory / "memory.sqlite3").exists()
    assert not (service.directory / "configuration.json").exists()
    assert (await client.call("sessions.delete", ref))["deleted"]
    replacement = await create_session(client, temporary=True)
    assert not (await client.call("memory.list", replacement))["documents"]


async def test_deleting_temporary_session_clears_all_visited_workspace_scopes(capabilities):
    service, client, peer, root = capabilities
    target = await create_session(client, temporary=True)
    other = await create_session(client, temporary=True)
    workspace_a = service._workspace_path
    peer.plans["guarda"] = [("memory_store", {"key": "nota", "text": "Memoria sintética"})]
    for ref in (target, other):
        assert (await approved_run(client, ref, "guarda"))["status"] == "completed"
        assert len((await client.call("memory.list", ref))["documents"]) == 1
    target_a = service._memory_scope(SessionRef(**target))
    other_a = service._memory_scope(SessionRef(**other))
    assert not (service.directory / "jarvis.sqlite3").exists()
    assert not (service.directory / "memory.sqlite3").exists()
    durable = await create_session(client)
    assert (await approved_run(client, durable, "guarda"))["status"] == "completed"
    durable_a = service._memory_scope(SessionRef(**durable))
    workspace_b = root / "segunda-carpeta"
    workspace_b.mkdir(mode=0o700)
    await client.call("workspace.set", {"path": str(workspace_b)})
    assert (await approved_run(client, target, "guarda"))["status"] == "completed"
    target_b = service._memory_scope(SessionRef(**target))
    assert target_a != target_b
    assert (await client.call("sessions.delete", target))["deleted"]
    assert service.memory.list_documents(target_a) == ()
    assert service.memory.list_documents(target_b) == ()
    assert len(service.memory.list_documents(other_a)) == 1
    assert len(service.memory.list_documents(durable_a)) == 1
    await client.call("workspace.set", {"path": str(workspace_a)})
    assert len((await client.call("memory.list", other))["documents"]) == 1
    assert len((await client.call("memory.list", durable))["documents"]) == 1


async def test_workspace_change_resets_thread_and_restores_only_completed_owned_context(
    capabilities,
):
    service, client, peer, root = capabilities
    ref = await create_session(client)
    other = await create_session(client)
    for current, message in ((ref, "mi contexto"), (other, "otra conversación")):
        run_id = (await client.call("runs.start", {**current, "message": message}))["run_id"]
        assert (await settled(client, current, run_id))["status"] == "completed"
    peer.plans["falló"] = [("workspace_write", {"path": "denegado.txt", "text": "NO"})]
    failed_id = (await client.call("runs.start", {**ref, "message": "falló"}))["run_id"]
    assert (
        await fixture_decision(client, ref, await pending(client, ref, failed_id), allow=False)
    )["accepted"]
    assert (await settled(client, ref, failed_id))["status"] == "failed"
    alternate = root / "carpeta-elegida"
    alternate.mkdir(mode=0o700)
    configured = await client.call("workspace.set", {"path": str(alternate)})
    assert configured["workspace"] == {
        "path": str(alternate),
        "available": True,
        "private_default": False,
        "error": None,
    }
    run_id = (await client.call("runs.start", {**ref, "message": "sigue"}))["run_id"]
    assert (await settled(client, ref, run_id))["status"] == "completed"
    options = peer.calls[-1][1]
    assert options["thread_id"] is None
    history = options["history"]
    assert [item["role"] for item in history] == ["user", "assistant"]
    assert [item["content"][0]["text"] for item in history] == [
        "mi contexto",
        "Respuesta sintética: mi contexto",
    ]
    assert {"process_run", "process_poll", "process_send", "process_stop"} <= {
        item["name"] for item in options["tool_specs"]}
    # Intrinsic MCP catalog/content helpers stay available in every workspace.
    # No owner-configured remote server tool is carried into this new context.
    assert {item["name"] for item in options["tool_specs"]
            if item["name"].startswith("mcp_")} == {
        "mcp_catalog", "mcp_resource_read", "mcp_prompt_get", "mcp_app_resource",
        "mcp_resource_template_read",
    }
    assert not list(alternate.iterdir())
    assert service._workspace_path == alternate
    events = (await client.call("events.poll", {**ref, "run_id": run_id}))["events"]
    assert any(event["kind"] == "context.restored" for event in events)


async def test_workspace_identity_change_fails_closed_until_owner_selects_again(capabilities):
    service, client, peer, root = capabilities
    chosen = root / "elegida"
    chosen.mkdir(mode=0o700)
    await client.call("workspace.set", {"path": str(chosen)})
    chosen.rename(root / "apartada")
    chosen.mkdir(mode=0o700)
    ref = await create_session(client)
    peer.plans["escribe"] = [("workspace_write", {"path": "no.txt", "text": "NO"})]
    identity = (await client.call("runs.start", {**ref, "message": "escribe"}))["run_id"]
    result = await settled(client, ref, identity)
    assert result["status"] == "failed" and result["error"] == "workspace_identity_changed"
    assert result["action_receipts"] == [] and peer.calls == []
    assert not list(chosen.iterdir()) and not list((root / "apartada").iterdir())
    assert (await client.call("workspace.set", {"path": str(chosen)}))["workspace"]["available"]
    assert (await approved_run(client, ref, "escribe"))["action_receipts"][0]["verified"]
    assert (chosen / "no.txt").read_text() == "NO"
    assert service._workspace_path == chosen
