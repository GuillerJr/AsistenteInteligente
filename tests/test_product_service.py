"""Signed product/service integration; synthetic providers and HTTP peers only.

The real private IPC, session store, Runtime, approval authority, workspace
executors and product domains are exercised together. No owner data, installed
app, live inference, GitHub account, notification OS permission or network is used.
"""

import asyncio
import json
import os
import shutil
import sqlite3
import tempfile
from pathlib import Path
from uuid import uuid4

import pytest

from jarvis_core.client_identity import ClientIdentity
from jarvis_core.codex import CodexError
from jarvis_core.product_projects_github import GitHubHTTPS, GitHubResponse
from jarvis_core.protocol import LocalRpcClient, RpcError
from jarvis_core.service import JarvisService
from jarvis_core.state import SessionRef


class ProductPeer:
    def __init__(self):
        self.service = None
        self.calls = []
        self.observations = {}
        self.parallel_entered = 0
        self.parallel_ready = asyncio.Event()
        self.release_parallel = asyncio.Event()
        self.hold_entered = asyncio.Event()
        self.hold_release = asyncio.Event()
        self.question_reply = None
        self.process_reply = None
        self.closed = False

    async def account_status(self):
        return {"connected": True, "mode": "chatgpt"}

    async def login(self):
        return {"url": "https://auth.openai.com/synthetic"}

    async def logout(self):
        return {"connected": False, "mode": None}

    async def chat(self, message, *, thread_id, temporary, on_thread, on_event,
                   on_tool_call, **options):
        self.calls.append({"message": message, "temporary": temporary,
                           "history": options["history"], "thread_id": thread_id})
        if thread_id is None:
            thread_id = "product-synthetic-" + uuid4().hex
            on_thread(thread_id)
        self.calls[-1]["bound_thread_id"] = thread_id
        if message in {"workspace-a", "workspace-b"}:
            before = self.service.workspace_tools.root
            self.parallel_entered += 1
            if self.parallel_entered == 2:
                self.parallel_ready.set()
            await self.release_parallel.wait()
            reply = await on_tool_call("workspace_read", {"path": "marker.txt"})
            receipt = json.loads(reply["contentItems"][0]["text"])
            after = self.service.workspace_tools.root
            self.observations[message] = {"before": before, "after": after,
                                           "receipt": receipt}
            on_event("fixture.workspace", {"workspace": str(after), "request": message})
        elif message == "pregunta":
            self.question_reply = await on_tool_call("request_user_input", {"questions": [{
                "id": "style", "header": "Estilo", "prompt": "¿Qué estilo prefieres?",
                "options": [{"id": "simple", "label": "Simple", "description": "Compacto"},
                            {"id": "expanded", "label": "Amplio", "description": "Detallado"}],
            }]})
        elif message == "hold":
            self.hold_entered.set()
            await self.hold_release.wait()
        elif message == "failure":
            raise CodexError("codex_turn_failed")
        elif message == "usage_known":
            on_event("usage.final", {"run_total_tokens": 2})
        elif message == "usage_envelope":
            on_event("usage.update", {"run_total_tokens": 4})
            self.hold_entered.set()
            await self.hold_release.wait()
        elif message == "background_process":
            self.process_reply = await on_tool_call("process_run", {
                "argv": ["/bin/cat"], "wait_ms": 10, "timeout_seconds": 30,
            })
        elif message == "approval_attribution":
            await on_tool_call("workspace_write", {
                "path": "approved-by-owner.txt", "text": "Efecto sintético aprobado",
            })
        return {"thread_id": thread_id, "answer": "Respuesta sintética terminada"}

    async def aclose(self):
        self.closed = True


@pytest.fixture
async def product_connection():
    # Short Unix socket path; every file is owned by this new private fixture.
    root = Path(tempfile.mkdtemp(prefix="jvp-", dir=tempfile.gettempdir())).resolve()
    profile = root / "p"
    profile.mkdir(mode=0o700)
    identity = ClientIdentity.load(Path(os.environ["JARVIS_RPC_CLIENT_KEY_FILE"]))
    peer = ProductPeer()
    service = JarvisService(profile, peer, bootstrap_public_key=identity.public_key)
    peer.service = service
    clients = [LocalRpcClient(profile, identity=identity, max_frame_bytes=1024 * 1024)]
    await service.start()
    await clients[0].connect()
    try:
        yield service, clients[0], peer, root, identity, clients
    finally:
        peer.release_parallel.set()
        peer.hold_release.set()
        for client in clients:
            await client.aclose()
        await service.aclose()
        assert peer.closed
        shutil.rmtree(root)


async def new_session(client, *, title="Chat sintético", temporary=False, workspace_id=None):
    value = {"title": title, "temporary": temporary}
    if workspace_id is not None:
        value["workspace_id"] = workspace_id
    row = (await client.call("sessions.create", value))["session"]
    return {"session_id": row["session_id"], "temporary": row["temporary"]}


async def settle(client, ref, run_id):
    async with asyncio.timeout(5):
        while True:
            row = (await client.call("runs.get", {**ref, "run_id": run_id}))["run"]
            if row["status"] in {"completed", "failed", "cancelled", "interrupted"}:
                return row
            await asyncio.sleep(0.005)


async def snapshot_until(client, ref, predicate, *, run_id=None):
    async with asyncio.timeout(5):
        while True:
            value = await client.call("product.snapshot", {
                **ref, **({"run_id": run_id} if run_id else {}),
            })
            if predicate(value):
                return value
            await asyncio.sleep(0.005)


async def pending_approval(client, ref, run_id):
    async with asyncio.timeout(5):
        while True:
            rows = (await client.call("approvals.list", {**ref, "run_id": run_id}))["approvals"]
            if rows:
                return rows[0]
            await asyncio.sleep(0.005)


async def decide(client, ref, approval):
    return await client.call("approvals.decide", {**ref,
        "approval_id": approval["approval_id"], "digest": approval["digest"], "allow": True})


async def initialize(client, *, temporary=False):
    return await client.call("product.initialize", {"temporary": temporary})


async def coordinate(client, ref, kind, arguments):
    return (await client.call("product.coordination", {
        **ref, "kind": kind, "arguments": arguments,
    }))["result"]


async def interact(client, ref, kind, arguments, *, run_id=None):
    return (await client.call("product.interaction", {
        **ref, **({"run_id": run_id} if run_id else {}), "kind": kind, "arguments": arguments,
    }))["result"]


async def test_parallel_workspaces_keep_provider_tool_callbacks_and_events_in_original_scope(
        product_connection):
    service, client, peer, root, _, _ = product_connection
    first = (await initialize(client))["workspaces"][0]
    first_path = Path(first["path"])
    (first_path / "marker.txt").write_text("MARCADOR_A", encoding="utf-8")
    ref_a = await new_session(client, workspace_id=first["workspace_id"])
    other_path = root / "selected-b"
    other_path.mkdir(mode=0o700)
    (other_path / "marker.txt").write_text("MARCADOR_B", encoding="utf-8")
    second = await coordinate(client, ref_a, "workspace.register", {
        "path": str(other_path), "name": "Carpeta B",
    })
    ref_b = await new_session(client, workspace_id=second["workspace_id"])
    run_a, run_b = await asyncio.gather(
        client.call("runs.start", {**ref_a, "message": "workspace-a", "request_id": uuid4().hex}),
        client.call("runs.start", {**ref_b, "message": "workspace-b", "request_id": uuid4().hex}),
    )
    await asyncio.wait_for(peer.parallel_ready.wait(), 5)
    peer.release_parallel.set()
    results = await asyncio.gather(settle(client, ref_a, run_a["run_id"]),
                                   settle(client, ref_b, run_b["run_id"]))
    assert all(row["status"] == "completed" and row["cleanup_confirmed"] for row in results)
    for message, path, marker in [("workspace-a", first_path, "MARCADOR_A"),
                                  ("workspace-b", other_path, "MARCADOR_B")]:
        observation = peer.observations[message]
        assert observation["before"] == path == observation["after"]
        assert observation["receipt"]["succeeded"] is True
        assert json.loads(observation["receipt"]["output"])["text"] == marker
    for ref, run, path in [(ref_a, run_a, first_path), (ref_b, run_b, other_path)]:
        events = (await client.call("events.poll", {**ref, "run_id": run["run_id"]}))["events"]
        actual = [row["payload"]["workspace"] for row in events
                  if row["kind"] == "fixture.workspace"]
        assert actual == [str(path)]
    assert service._workspace_path == first_path  # Request context did not mutate global fallback.


async def test_collaboration_read_only_upgrade_and_identity_revocation_use_signed_ipc(
        product_connection):
    service, owner, peer, _, _, clients = product_connection
    await initialize(owner)
    ref = await new_session(owner)
    other_key = ClientIdentity.generate()
    review = await owner.call("clients.review", {"public_key": other_key.public_key,
                                                 "scope": "owner", "expected_revision": 1})
    await owner.call("clients.admit", {key: review[key]
                                       for key in ("review_id", "review_digest")})
    other = LocalRpcClient(service.directory, identity=other_key, max_frame_bytes=1024 * 1024)
    clients.append(other)
    await other.connect()
    assert (await other.call("sessions.list", {"temporary": False}))["sessions"] == []
    with pytest.raises(RpcError):
        await other.call("sessions.messages", ref)
    await coordinate(owner, ref, "session.member", {
        "member_id": other_key.client_id, "role": "read", "expected_revision": 1,
    })
    assert (await other.call("sessions.messages", ref))["messages"] == []
    with pytest.raises(RpcError):
        await other.call("runs.start", {**ref, "message": "no autorizado"})
    assert peer.calls == []
    await coordinate(owner, ref, "session.member", {
        "member_id": other_key.client_id, "role": "collaborate", "expected_revision": 2,
    })
    run = await other.call("runs.start", {**ref, "message": "hold", "request_id": uuid4().hex})
    await asyncio.wait_for(peer.hold_entered.wait(), 5)
    await owner.call("clients.revoke", {"client_id": other_key.client_id, "expected_revision": 2})
    receipt = await settle(owner, ref, run["run_id"])
    assert receipt["status"] == "cancelled" and receipt["cleanup_confirmed"] is True
    assert receipt["outcome"] == "unknown"
    with pytest.raises(RpcError):
        await other.call("sessions.messages", ref)
    assert len(peer.calls) == 1


async def test_approval_events_attribute_signed_requester_and_approver_without_identity_grant(
        product_connection):
    service, owner, _, _, owner_key, clients = product_connection
    workspace = (await initialize(owner))["workspaces"][0]
    ref = await new_session(owner, workspace_id=workspace["workspace_id"])
    requester = ClientIdentity.generate()
    review = await owner.call("clients.review", {"public_key": requester.public_key,
                                                 "scope": "owner", "expected_revision": 1})
    await owner.call("clients.admit", {key: review[key]
                                       for key in ("review_id", "review_digest")})
    other = LocalRpcClient(service.directory, identity=requester, max_frame_bytes=1024 * 1024)
    clients.append(other)
    await other.connect()
    await coordinate(owner, ref, "session.member", {
        "member_id": requester.client_id, "role": "collaborate", "expected_revision": 1,
    })
    started = await other.call("runs.start", {**ref, "message": "approval_attribution",
                                              "request_id": uuid4().hex})
    approval = await pending_approval(owner, ref, started["run_id"])
    assert approval["requester_client_id"] == requester.client_id
    assert approval["requester_client_id"] != owner_key.client_id
    destination = Path(workspace["path"]) / "approved-by-owner.txt"
    assert not destination.exists()
    # A mismatched digest cannot produce an approval event or release the effect.
    stale_digest = "0" * 64 if approval["digest"] != "0" * 64 else "1" * 64
    assert await decide(owner, ref, {**approval, "digest": stale_digest}) == {"accepted": False}
    before = (await owner.call("events.poll", {**ref, "run_id": started["run_id"]}))["events"]
    assert not [row for row in before if row["kind"].startswith("approval.decision_")]
    assert not destination.exists()
    assert await decide(owner, ref, approval) == {"accepted": True}
    receipt = await settle(owner, ref, started["run_id"])
    assert receipt["status"] == "completed" and receipt["cleanup_confirmed"] is True
    assert destination.read_text() == "Efecto sintético aprobado"
    events = (await owner.call("events.poll", {**ref, "run_id": started["run_id"]}))["events"]
    requested = [row["payload"] for row in events if row["kind"] == "approval.decision_requested"]
    recorded = [row["payload"] for row in events if row["kind"] == "approval.decision_recorded"]
    assert requested == [{
        "approval_id": approval["approval_id"], "digest": approval["digest"],
        "requester_client_id": requester.client_id, "approver_client_id": owner_key.client_id,
        "allow": True, "remember": "operation", "grants_model_identity": False,
    }]
    assert recorded == [{"approval_id": approval["approval_id"], "accepted": True,
                         "approver_client_id": owner_key.client_id}]
    assert await decide(owner, ref, approval) == {"accepted": False}
    after = (await owner.call("events.poll", {**ref, "run_id": started["run_id"]}))["events"]
    assert [row for row in after if row["kind"].startswith("approval.decision_")] == [
        row for row in events if row["kind"].startswith("approval.decision_")]


async def test_temporary_product_initialization_and_chat_never_open_durable_chat_databases(
        product_connection):
    service, client, _, _, _, _ = product_connection
    selected = (await initialize(client, temporary=True))["workspaces"][0]
    ref = await new_session(client, temporary=True, workspace_id=selected["workspace_id"])
    run = await client.call("runs.start", {**ref, "message": "Temporal sintético",
                                           "request_id": uuid4().hex})
    assert (await settle(client, ref, run["run_id"]))["status"] == "completed"
    value = await client.call("product.snapshot", ref)
    assert value["scope"]["temporary"] is True
    for name in ("jarvis.sqlite3", "session-coordination.sqlite3", "product-interactions.sqlite3"):
        assert not (service.directory / name).exists(), name
        assert not (service.directory / (name + ".lock")).exists(), name


async def test_question_answer_reaches_only_original_provider_ephemeral_handoff(
        product_connection):
    service, client, peer, _, _, _ = product_connection
    await initialize(client)
    ref = await new_session(client)
    unrelated = await new_session(client, title="Otro chat sintético")
    run = await client.call("runs.start", {**ref, "message": "pregunta", "request_id": uuid4().hex})
    value = await snapshot_until(client, ref, lambda value: any(
        row["kind"] == "question" and row["status"] == "pending"
        for row in value["interactions"]), run_id=run["run_id"])
    question = next(row for row in value["interactions"] if row["kind"] == "question")
    answer = "EPHEMERAL_ONLY_PRODUCT_SERVICE_73091"
    arguments = {"id": question["id"], "revision": question["revision"],
                 "answers": {"style": {"text": answer}}}
    with pytest.raises(RpcError):
        await interact(client, unrelated, "question.answer", arguments, run_id=run["run_id"])
    with pytest.raises(RpcError):
        await interact(client, ref, "question.answer", {**arguments, "revision": 0},
                       run_id=run["run_id"])
    response = await interact(client, ref, "question.answer", arguments, run_id=run["run_id"])
    assert response["grants_tool_approval"] is False
    receipt = await settle(client, ref, run["run_id"])
    assert receipt["status"] == "completed"
    assert answer in json.dumps(peer.question_reply)
    first = json.loads(peer.question_reply["contentItems"][0]["text"])
    assert answer not in json.dumps(first)
    assert json.loads(first["output"])["answers_stored"] is False
    messages = await client.call("sessions.messages", ref)
    events = await client.call("events.poll", {**ref, "run_id": run["run_id"]})
    assert answer not in json.dumps({"receipt": receipt, "messages": messages, "events": events})
    assert service.product.ephemeral == {}
    for name in ("jarvis.sqlite3", "session-coordination.sqlite3", "product-interactions.sqlite3"):
        path = service.directory / name
        if path.exists():
            assert answer.encode() not in path.read_bytes(), name


async def test_panel_internal_source_and_notification_claim_are_bound_to_chat_and_revocation(
        product_connection):
    _, client, _, _, _, _ = product_connection
    await initialize(client)
    ref = await new_session(client, title="Título del chat elegido")
    other = await new_session(client, title="OTRO_CHAT_NO_DEBE_APARECER")
    panel = {"title": "Panel", "layout": "stack",
        "theme": {"appearance": "system", "accent": "accent"},
        "widgets": [{"id": "selected", "type": "text", "title": "Conversación",
                     "source_id": "session"}]}
    rendered = await interact(client, ref, "panel.set", {"revision": 0, "panel": panel})
    assert rendered["payload"]["widgets"][0]["source"]["text"] == "Título del chat elegido"
    assert "OTRO_CHAT_NO_DEBE_APARECER" not in json.dumps(rendered)
    remote = {**panel, "widgets": [{"id": "unsafe", "type": "text", "title": "Ajeno",
                                    "source_id": "https://example.invalid"}]}
    with pytest.raises(RpcError):
        await interact(client, ref, "panel.set", {"revision": 1, "panel": remote})
    await interact(client, ref, "notifications.configure", {
        "revision": 0, "enabled": True, "silenced": False,
    })
    run = await client.call("runs.start", {**ref, "message": "Aviso sintético",
                                           "request_id": uuid4().hex})
    await settle(client, ref, run["run_id"])
    value = await snapshot_until(client, ref, lambda value: any(
        row["kind"] == "notification" for row in value["interactions"]), run_id=run["run_id"])
    notification = next(row for row in value["interactions"] if row["kind"] == "notification")
    claim = await interact(client, ref, "notification.claim", {
        "id": notification["id"], "revision": notification["revision"],
    }, run_id=run["run_id"])
    exact = {"id": claim["id"], "revision": claim["revision"], "claim": claim["claim"]}
    with pytest.raises(RpcError):
        await interact(client, other, "notification.validate", exact, run_id=run["run_id"])
    assert (await interact(client, ref, "notification.validate", exact,
                           run_id=run["run_id"]))["valid"] is True
    await interact(client, ref, "notifications.configure", {
        "revision": 1, "enabled": True, "silenced": True,
    })
    with pytest.raises(RpcError):
        await interact(client, ref, "notification.validate", exact, run_id=run["run_id"])


async def test_budget_admission_reconciles_known_usage_and_retains_unknown_commitments(
        product_connection):
    _, client, peer, _, _, _ = product_connection
    await initialize(client)
    ref = await new_session(client)
    await coordinate(client, ref, "budget.configure", {"token_limit": 10, "expected_revision": 0})
    first = await client.call("runs.start", {**ref, "message": "usage_known",
        "request_id": uuid4().hex, "reservation_tokens": 4})
    assert (await settle(client, ref, first["run_id"]))["outcome"] == "known"
    budget = (await client.call("product.snapshot", ref))["budget"]
    assert budget["actual_tokens"] == 2 and budget["committed_tokens"] == 2
    assert budget["actual_cost"] is None and budget["cost_known"] is False
    missing = await client.call("runs.start", {**ref, "message": "usage_missing",
        "request_id": uuid4().hex, "reservation_tokens": 5})
    assert (await settle(client, ref, missing["run_id"]))["outcome"] == "known"
    budget = (await client.call("product.snapshot", ref))["budget"]
    assert budget["actual_tokens"] is None and budget["committed_tokens"] == 7
    with pytest.raises(RpcError):
        await client.call("runs.start", {**ref, "message": "overflow",
            "request_id": uuid4().hex, "reservation_tokens": 4})
    assert len(peer.calls) == 2
    failed = await client.call("runs.start", {**ref, "message": "failure",
        "request_id": uuid4().hex, "reservation_tokens": 2})
    assert (await settle(client, ref, failed["run_id"]))["outcome"] == "unknown"
    with pytest.raises(RpcError):
        await client.call("runs.start", {**ref, "message": "cannot_reuse_uncertain_custody",
            "request_id": uuid4().hex, "reservation_tokens": 1})
    assert len(peer.calls) == 3


async def test_observed_usage_envelope_cancels_but_never_invents_settled_billing(
        product_connection):
    _, client, peer, _, _, _ = product_connection
    await initialize(client)
    ref = await new_session(client)
    await coordinate(client, ref, "budget.configure", {"token_limit": 10, "expected_revision": 0})
    run = await client.call("runs.start", {**ref, "message": "usage_envelope",
        "request_id": uuid4().hex, "reservation_tokens": 4})
    await asyncio.wait_for(peer.hold_entered.wait(), 5)
    receipt = await settle(client, ref, run["run_id"])
    assert receipt["status"] == "cancelled" and receipt["cleanup_confirmed"] is True
    assert receipt["outcome"] == "unknown" and receipt["answer"] is None
    budget = (await client.call("product.snapshot", ref))["budget"]
    assert budget["committed_tokens"] == 4
    assert budget["actual_tokens"] is None and budget["actual_cost"] is None
    assert len(peer.calls) == 1


async def test_rejected_parent_deletion_preserves_provider_continuation_cache(
        product_connection):
    _, client, provider, _, _, _ = product_connection
    await initialize(client)
    parent = await new_session(client)
    first = await client.call("runs.start", {**parent, "message": "continuity",
        "request_id": uuid4().hex})
    await settle(client, parent, first["run_id"])
    original_thread = provider.calls[-1]["bound_thread_id"]
    revision = (await client.call("sessions.context", parent))["context"]["revision"]
    branch = (await client.call("sessions.fork", {**parent, "title": "Rama sintética",
        "expected_revision": revision, "operation_id": uuid4().hex}))["session"]
    child = {"session_id": branch["session_id"], "temporary": branch["temporary"]}
    await client.call("product.snapshot", child)  # Establish the real parent metadata link.
    with pytest.raises(RpcError):
        await client.call("sessions.delete", parent)
    second = await client.call("runs.start", {**parent, "message": "continuity",
        "request_id": uuid4().hex})
    assert (await settle(client, parent, second["run_id"]))["status"] == "completed"
    assert provider.calls[-1]["thread_id"] == original_thread


async def test_catalog_rebuild_inside_selected_task_keeps_default_fingerprint_and_executor(
        product_connection):
    service, client, provider, root, identity, _ = product_connection
    first = (await initialize(client))["workspaces"][0]
    base = await new_session(client, workspace_id=first["workspace_id"])
    other = root / "catalog-workspace-b"
    other.mkdir(mode=0o700)
    (other / "marker.txt").write_text("CATALOG_B", encoding="utf-8")
    workspace = await coordinate(client, base, "workspace.register", {
        "path": str(other), "name": "Catálogo B",
    })
    ref = await new_session(client, workspace_id=workspace["workspace_id"])
    selected = service.bind_product_workspace(identity.client_id,
        SessionRef(ref["session_id"], ref["temporary"]))
    before = service._catalog_fingerprint
    token = service._product_workspace_context.set(selected)
    try:
        # Exercise the real rebuild entry point under the same ambient task
        # scope as an idle MCP refresh from runs.start; no catalogue is mocked.
        service._configure_tools()
        assert service.workspace_tools.root == other
        assert service._default_workspace_tools.root == Path(first["path"])
        assert service._catalog_fingerprint == before
    finally:
        service._product_workspace_context.reset(token)
    provider.release_parallel.set()
    run = await client.call("runs.start", {**ref, "message": "workspace-b",
        "request_id": uuid4().hex})
    assert (await settle(client, ref, run["run_id"]))["status"] == "completed"
    actual = provider.observations["workspace-b"]
    assert actual["before"] == other == actual["after"]
    assert json.loads(actual["receipt"]["output"])["text"] == "CATALOG_B"


async def test_first_product_initialization_rejects_replaced_default_workspace(
        product_connection):
    service, client, provider, root, _, _ = product_connection
    original = service._default_workspace_tools
    path = original.root
    path.rename(root / "retained-original-default-workspace")
    path.mkdir(mode=0o700)
    (path / "marker.txt").write_text("REPLACEMENT_MUST_NOT_BE_ADMITTED", encoding="utf-8")
    with pytest.raises(RpcError):
        await initialize(client)
    assert service._default_workspace_tools.identity == original.identity
    assert provider.calls == []


async def test_workspace_change_cannot_orphan_background_processes_from_controls(
        product_connection):
    _, client, peer, root, _, _ = product_connection
    selected = (await initialize(client))["workspaces"][0]
    ref = await new_session(client, workspace_id=selected["workspace_id"])
    status = await client.call("processes.list", ref)
    if not status["sandbox"]["available"]:
        pytest.skip("Esta plataforma CI no dispone del sandbox real; no se lanza sin él.")
    run = await client.call("runs.start", {**ref, "message": "background_process",
        "request_id": uuid4().hex})
    approval = await pending_approval(client, ref, run["run_id"])
    assert (await decide(client, ref, approval))["accepted"] is True
    receipt = await settle(client, ref, run["run_id"])
    assert receipt["action_receipts"][0]["succeeded"] is True
    assert peer.process_reply["success"] is True
    processes = (await client.call("processes.list", ref))["processes"]
    assert any(row["running"] for row in processes)
    other = root / "new-process-workspace"
    other.mkdir(mode=0o700)
    with pytest.raises(RpcError):
        await client.call("workspace.set", {**ref, "path": str(other)})
    assert (await client.call("workspace.get", ref))["workspace"]["path"] == selected["path"]
    assert any(row["running"] for row in (await client.call("processes.list", ref))["processes"])


async def test_workspace_round_trip_never_revives_an_old_native_notification_claim(
        product_connection):
    _, client, _, root, _, _ = product_connection
    first = (await initialize(client))["workspaces"][0]
    ref = await new_session(client, workspace_id=first["workspace_id"])
    await interact(client, ref, "notifications.configure", {
        "revision": 0, "enabled": True, "silenced": False,
    })
    run = await client.call("runs.start", {**ref, "message": "old attention",
        "request_id": uuid4().hex})
    await settle(client, ref, run["run_id"])
    snapshot = await snapshot_until(client, ref, lambda value: any(
        row["kind"] == "notification" for row in value["interactions"]), run_id=run["run_id"])
    notification = next(row for row in snapshot["interactions"] if row["kind"] == "notification")
    claim = await interact(client, ref, "notification.claim", {
        "id": notification["id"], "revision": notification["revision"],
    }, run_id=run["run_id"])
    exact = {"id": claim["id"], "revision": claim["revision"], "claim": claim["claim"]}
    # Cosmetic group/pin CAS must preserve the scope's security generation.
    await coordinate(client, ref, "session.bind", {"workspace_id": first["workspace_id"],
        "expected_revision": snapshot["binding"]["revision"], "group": "Grupo", "pinned": True})
    assert (await interact(client, ref, "notification.validate", exact,
                           run_id=run["run_id"]))["valid"] is True
    other = root / "notification-other-workspace"
    other.mkdir(mode=0o700)
    await client.call("workspace.set", {**ref, "path": str(other)})
    await client.call("workspace.set", {**ref, "path": first["path"]})
    current = await client.call("product.snapshot", {**ref, "run_id": run["run_id"]})
    assert current["scope"]["generation"] > snapshot["scope"]["generation"]
    with pytest.raises(RpcError):
        await interact(client, ref, "notification.validate", exact, run_id=run["run_id"])


async def test_failed_state_delete_never_silently_rebinds_live_chat_to_default_workspace(
        product_connection):
    service, client, _, root, _, _ = product_connection
    await initialize(client)
    original = await new_session(client)
    other = root / "delete-selected-workspace"
    other.mkdir(mode=0o700)
    workspace = await coordinate(client, original, "workspace.register", {
        "name": "Conservada", "path": str(other),
    })
    ref = await new_session(client, workspace_id=workspace["workspace_id"])
    before = await client.call("product.snapshot", ref)
    database = service.directory / "jarvis.sqlite3"
    # Actual SQLite abort at the storage boundary, without replacing any domain
    # or authorization function. The trigger only touches this private fixture.
    with sqlite3.connect(database) as connection:
        connection.execute("CREATE TRIGGER synthetic_delete_abort BEFORE DELETE ON sessions "
                           "BEGIN SELECT RAISE(ABORT, 'synthetic_delete_abort'); END")
    try:
        with pytest.raises(RpcError):
            await client.call("sessions.delete", ref)
        try:
            observed = await client.call("product.snapshot", ref)
        except RpcError:
            pass  # Explicit retained retirement is also a safe, reviewable result.
        else:
            assert observed["binding"]["workspace_id"] == workspace["workspace_id"]
            assert observed["binding"]["owner_id"] == before["binding"]["owner_id"]
            assert observed["binding"]["members"] == before["binding"]["members"]
    finally:
        with sqlite3.connect(database) as connection:
            connection.execute("DROP TRIGGER synthetic_delete_abort")


class GitHubProductPeer:
    """External HTTP boundary fixture; real GitHubProjects validation remains active."""

    def __init__(self):
        self.calls = []

    async def request(self, method, path, headers, body):
        assert headers["Authorization"].startswith("Bearer SYNTHETIC_")
        self.calls.append((method, path, body))
        if path == "/user":
            result = {"id": 23, "login": "synthetic-owner"}
        else:
            owner, repository = path.split("/")[2:4]
            repository_id = 41 if repository == "repo-a" else 42
            if method == "POST":
                result = {"number": 7,
                    "html_url": f"https://github.com/{owner}/{repository}/pull/7",
                    "head": {"sha": "a" * 40}, "base": {"repo": {"id": repository_id}}}
            elif "/git/ref/heads/" in path:
                branch = path.rsplit("/", 1)[-1]
                result = {"ref": "refs/heads/" + branch,
                          "object": {"sha": ("a" if branch == "change" else "b") * 40}}
            else:
                result = {"id": repository_id, "full_name": owner + "/" + repository}
        return GitHubResponse(201 if method == "POST" else 200, json.dumps(result).encode())


async def test_github_destination_change_invalidates_captured_operation_and_old_approval(
        product_connection, monkeypatch):
    _, client, provider, _, _, _ = product_connection
    http = GitHubProductPeer()

    async def transport(_self, method, path, headers, body):
        return await http.request(method, path, headers, body)

    monkeypatch.setattr(GitHubHTTPS, "request", transport)
    await initialize(client)
    ref = await new_session(client)

    async def connect(repository):
        return await client.call("product.projects", {**ref, "kind": "github.connect",
            "arguments": {"token": "SYNTHETIC_" + repository, "owner": "jarvis",
                          "repository": repository}})

    arguments = {"title": "Cambio sintético", "body": "Sólo datos de fixture",
                 "head": "change", "base": "main", "expected_head_sha": "a" * 40}
    await connect("repo-a")
    old = await client.call("product.tool.start", {**ref, "tool": "github_publish",
        "arguments": arguments, "request_id": uuid4().hex})
    old_approval = await pending_approval(client, ref, old["run_id"])
    assert old_approval["arguments"]["repository"] == "repo-a"
    assert "SYNTHETIC_" not in old_approval["arguments_json"]
    await connect("repo-b")
    assert (await decide(client, ref, old_approval))["accepted"] is True
    old_receipt = await settle(client, ref, old["run_id"])
    assert old_receipt["action_receipts"][0]["succeeded"] is False
    assert old_receipt["action_receipts"][0]["error"] == "github_connection_changed"
    assert not any(method == "POST" for method, _, _ in http.calls)
    current = await client.call("product.tool.start", {**ref, "tool": "github_publish",
        "arguments": arguments, "request_id": uuid4().hex})
    current_approval = await pending_approval(client, ref, current["run_id"])
    assert current_approval["arguments"]["repository"] == "repo-b"
    assert current_approval["digest"] != old_approval["digest"]
    assert (await decide(client, ref, old_approval))["accepted"] is False
    assert not any(method == "POST" for method, _, _ in http.calls)
    assert (await decide(client, ref, current_approval))["accepted"] is True
    receipt = await settle(client, ref, current["run_id"])
    assert receipt["action_receipts"][0]["succeeded"] is True
    posts = [path for method, path, _ in http.calls if method == "POST"]
    assert posts == ["/repos/jarvis/repo-b/pulls"]
    assert (await decide(client, ref, current_approval))["accepted"] is False
    await client.call("product.tool.start", {**ref, "tool": "github_publish",
        "arguments": arguments, "request_id": current["run_id"]})
    assert [path for method, path, _ in http.calls if method == "POST"] == posts
    assert provider.calls == []  # Host exact-operation Runtime never calls a model.
