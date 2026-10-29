"""Real local RPC/runtime/authority/files; all peers and approval decisions are synthetic."""

import asyncio
import hmac
import json
import sys
import tempfile
import time
from pathlib import Path
from urllib.parse import urlsplit
from uuid import uuid4

import pytest
from test_service_management import (
    ManagementPeer,
    pending,
    run,
    schedule,
    session,
    start_scheduler,
    synthetic_decision,
)
from test_service_management import (
    management as management,
)

from jarvis_core.automation_flows import AutomationFlowError
from jarvis_core.client_identity import ClientIdentity
from jarvis_core.host_profile import HostProfileError
from jarvis_core.protocol import LocalRpcClient, RpcError
from jarvis_core.service import JarvisService


async def create_hook(client, *, event="manual", steps=None):
    row = (await client.call("automation.hooks.create", {
        "title": "Automatización sintética", "event": event, "agent_id": "jarvis",
        "steps": steps or [{"prompt": "Paso sintético", "pause_after": False}],
    }))["hook"]
    assert not row["enabled"]
    return (await client.call("automation.hooks.update", {
        "hook_id": row["hook_id"], "expected_revision": row["revision"], "enabled": True,
    }))["hook"]


async def activate(client):
    snapshot = await client.call("automation.list")
    return await client.call("automation.start", {
        "configuration_hash": snapshot["configuration_hash"]})


async def emit(client, hook, *, identity=None, payload=None):
    return (await client.call("automation.hooks.emit", {
        "hook_id": hook["hook_id"], "expected_revision": hook["revision"],
        "event_id": identity or uuid4().hex, "payload": payload or {},
    }))["flows"][0]


async def flow_until(client, identity, states):
    async with asyncio.timeout(5):
        while True:
            snapshot = await client.call("automation.list")
            row = next(r for r in snapshot["flows"] if r["flow_id"] == identity)
            if row["status"] in states:
                return row
            await asyncio.sleep(0.005)


async def flow_approval(service, client, identity):
    async with asyncio.timeout(5):
        while True:
            snapshot = await client.call("automation.list")
            flow = next(r for r in snapshot["flows"] if r["flow_id"] == identity)
            if flow["status"] == "running" and flow["claim_run_id"] is not None:
                run_id = flow["claim_run_id"]
                ref = service.store.session_for_run(run_id)
                wire = {"session_id": ref.session_id, "temporary": False}
                decision = await pending(client, wire, run_id)
                return wire, decision
            await asyncio.sleep(0.005)


def plan_prefix(monkeypatch, peer, plans):
    original = peer.chat
    async def chat(message, **options):
        for prefix, tools in plans.items():
            if message.startswith(prefix):
                peer.plans[message] = tools
        return await original(message, **options)
    monkeypatch.setattr(peer, "chat", chat)


async def test_hook_rpc_is_disabled_until_review_and_stale_activation_rejected(management):
    _service, client, peer, _root = management
    before = await client.call("automation.list")
    hook = await create_hook(client)
    with pytest.raises(RpcError):
        await client.call("automation.start", {"configuration_hash": before["configuration_hash"]})
    queued = await emit(client, hook, identity="exact_event")
    duplicate = await emit(client, hook, identity="exact_event")
    assert queued["flow_id"] == duplicate["flow_id"] and not peer.calls
    await activate(client)
    final = await flow_until(client, queued["flow_id"], {"completed"})
    assert final["outcome"] == "known" and len(peer.calls) == 1
    assert final["agent_id"] == "jarvis"
    await client.call("automation.stop")


async def test_flow_workspace_write_uses_real_central_approval_and_receipt(management, monkeypatch):
    service, client, peer, _root = management
    plan_prefix(monkeypatch, peer, {"ESCRITURA": [
        ("workspace_write", {"path": "flow.txt", "text": "PRUEBA FLUJO 7319"})]})
    hook = await create_hook(client, steps=[{"prompt": "ESCRITURA", "pause_after": False}])
    queued = await emit(client, hook)
    await activate(client)
    wire, approval = await flow_approval(service, client, queued["flow_id"])
    target = service._workspace_path / "flow.txt"
    assert not target.exists()
    assert (await synthetic_decision(client, wire, approval))["accepted"]
    final = await flow_until(client, queued["flow_id"], {"completed", "failed", "interrupted"})
    assert final["status"] == "completed" and final["outcome"] == "known"
    assert target.read_bytes() == b"PRUEBA FLUJO 7319"
    assert len(final["receipts"]) == len(final["run_ids"]) == 1
    runtime = (await client.call("runs.get", {**wire, "run_id": final["run_ids"][0]}))["run"]
    assert runtime["action_receipts"][0]["verified"]
    await client.call("automation.stop")


async def test_denied_step_is_known_and_never_executes_later_prompt(management, monkeypatch):
    service, client, peer, _root = management
    plan_prefix(monkeypatch, peer, {"DENEGAR": [
        ("workspace_write", {"path": "denied-flow.txt", "text": "NO"})]})
    hook = await create_hook(client, steps=[{"prompt": "DENEGAR", "pause_after": False},
                                            {"prompt": "NO EJECUTAR", "pause_after": False}])
    queued = await emit(client, hook)
    await activate(client)
    wire, approval = await flow_approval(service, client, queued["flow_id"])
    assert (await synthetic_decision(client, wire, approval, allow=False))["accepted"]
    final = await flow_until(client, queued["flow_id"], {"failed", "interrupted"})
    assert (final["status"], final["outcome"], final["error_code"]) == (
        "failed", "known", "automation_step_denied")
    assert len(peer.calls) == 1 and final["cursor"] == 0
    assert not (service._workspace_path / "denied-flow.txt").exists()
    assert not service.authority.list_pending()
    await client.call("automation.stop")
    await activate(client)
    await asyncio.sleep(0.03)
    assert len(peer.calls) == 1
    await client.call("automation.stop")


async def test_checkpoint_pause_resume_new_step_requires_its_own_approval(management, monkeypatch):
    service, client, peer, _root = management
    plan_prefix(monkeypatch, peer, {"SEGUNDO": [
        ("workspace_write", {"path": "second-step.txt", "text": "DOS"})]})
    hook = await create_hook(client, steps=[{"prompt": "PRIMERO", "pause_after": True},
                                            {"prompt": "SEGUNDO", "pause_after": False}])
    queued = await emit(client, hook)
    await activate(client)
    paused = await flow_until(client, queued["flow_id"], {"paused"})
    assert paused["cursor"] == 1 and paused["outcome"] == "known" and len(peer.calls) == 1
    await client.call("automation.flows.resume", {
        "flow_id": paused["flow_id"], "expected_revision": paused["revision"]})
    wire, approval = await flow_approval(service, client, queued["flow_id"])
    assert not (service._workspace_path / "second-step.txt").exists()
    await synthetic_decision(client, wire, approval)
    final = await flow_until(client, queued["flow_id"], {"completed"})
    assert final["cursor"] == 2 and len(final["receipts"]) == 2 and len(peer.calls) == 2
    assert (service._workspace_path / "second-step.txt").read_bytes() == b"DOS"
    await client.call("automation.stop")


@pytest.mark.parametrize("control", ["stop", "pause", "cancel", "disable", "remove"])
async def test_revocation_while_waiting_blocks_effect_and_replay(management, monkeypatch, control):
    service, client, peer, _root = management
    plan_prefix(monkeypatch, peer, {"ESPERAR": [
        ("workspace_write", {"path": "never-flow.txt", "text": "NO"})]})
    hook = await create_hook(client, steps=[{"prompt": "ESPERAR", "pause_after": False}])
    queued = await emit(client, hook)
    await activate(client)
    wire, approval = await flow_approval(service, client, queued["flow_id"])
    current = service.automation_flows.get_flow(queued["flow_id"])
    if control == "stop":
        await client.call("automation.stop")
    elif control == "disable":
        await client.call("automation.hooks.update", {
            "hook_id": hook["hook_id"], "expected_revision": hook["revision"], "enabled": False})
    elif control == "remove":
        await client.call("automation.hooks.remove", {
            "hook_id": hook["hook_id"], "expected_revision": hook["revision"]})
    else:
        await client.call("automation.flows." + control, {
            "flow_id": current["flow_id"], "expected_revision": current["revision"]})
    await flow_until(client, queued["flow_id"], {"interrupted", "cancelled", "failed"})
    await client.call("automation.stop")
    assert not (await synthetic_decision(client, wire, approval))["accepted"]
    assert not (service._workspace_path / "never-flow.txt").exists()
    assert not service.authority.list_pending()
    await activate(client)
    await asyncio.sleep(0.03)
    assert len(peer.calls) == 1
    await client.call("automation.stop")


async def test_run_completed_hook_does_not_recursively_trigger_itself(management):
    _service, client, peer, _root = management
    await create_hook(client, event="run.completed")
    await activate(client)
    ref = await session(client)
    await run(client, ref, "Origen del evento")
    async with asyncio.timeout(5):
        while not (snapshot := await client.call("automation.list"))["flows"]:
            await asyncio.sleep(0.005)
    final = await flow_until(client, snapshot["flows"][0]["flow_id"], {"completed"})
    await asyncio.sleep(0.05)
    assert len((await client.call("automation.list"))["flows"]) == 1
    assert len(peer.calls) == 2 and final["payload"]["session_id"] == ref["session_id"]
    await client.call("automation.stop")


async def test_scheduler_completion_routes_one_durable_event_to_runtime(management):
    _service, client, peer, _root = management
    hook = await create_hook(client, event="scheduler.completed")
    await activate(client)
    await schedule(client, enabled=True)
    await start_scheduler(client)
    async with asyncio.timeout(5):
        while not (snapshot := await client.call("automation.list"))["flows"]:
            await asyncio.sleep(0.005)
    final = await flow_until(client, snapshot["flows"][0]["flow_id"], {"completed"})
    assert final["hook_id"] == hook["hook_id"] and len(peer.calls) == 2
    await client.call("scheduler.stop")
    await client.call("automation.stop")


async def test_authenticated_webhook_via_rpc_enqueues_once_and_uses_same_runtime(management):
    _service, client, peer, _root = management
    hook = await create_hook(client, event="webhook")
    snapshot = await client.call("automation.list")
    secret = "S" * 32
    listener = (await client.call("automation.webhook.start", {
        "hook_id": hook["hook_id"], "expected_revision": hook["revision"],
        "configuration_hash": snapshot["configuration_hash"], "secret": secret,
    }))["listener"]
    url, timestamp, nonce = urlsplit(listener["address"]), str(int(time.time())), uuid4().hex
    body = b'{"marker":"WEBHOOK 8293"}'
    signature = hmac.digest(
        secret.encode(), f"{timestamp}.{nonce}.".encode() + body, "sha256").hex()
    async def post(signature):
        reader, writer = await asyncio.open_connection(url.hostname, url.port)
        try:
            writer.write((f"POST {url.path} HTTP/1.1\r\nHost: {url.netloc}\r\n"
                f"Content-Type: application/json\r\nContent-Length: {len(body)}\r\n"
                f"X-Jarvis-Timestamp: {timestamp}\r\nX-Jarvis-Nonce: {nonce}\r\n"
                f"X-Jarvis-Signature: {signature}\r\n\r\n").encode() + body)
            await writer.drain()
            async with asyncio.timeout(3):
                raw = await reader.read()
            head, data = raw.split(b"\r\n\r\n", 1)
            return int(head.split()[1]), json.loads(data)
        finally:
            writer.close()
            await writer.wait_closed()
    assert (await post("0" * 64))[0] == 401
    first, duplicate = await post(signature), await post(signature)
    assert first == duplicate and first[0] == 202 and not peer.calls
    await activate(client)
    flow = await flow_until(client, first[1]["flow_ids"][0], {"completed"})
    assert flow["payload"] == {"marker": "WEBHOOK 8293"} and len(peer.calls) == 1
    assert "WEBHOOK 8293" in peer.calls[0][0]
    assert secret not in json.dumps(await client.call("automation.list"))
    await client.call("automation.webhook.stop")
    await client.call("automation.stop")
    with pytest.raises(OSError):
        await asyncio.open_connection(url.hostname, url.port)


async def test_revoked_activation_client_cannot_start_another_step_or_effect():
    base = "/private/tmp" if sys.platform == "darwin" else tempfile.gettempdir()
    with tempfile.TemporaryDirectory(prefix="jvs-flow-rev-", dir=base) as value:
        profile = Path(value) / "profile"
        owner, operator = ClientIdentity.generate(), ClientIdentity.generate()
        peer = ManagementPeer()
        service = JarvisService(profile, peer, bootstrap_public_key=owner.public_key)
        review = service.client_authority.review(owner.client_id, {
            "public_key": operator.public_key, "scope": "owner", "expected_revision": 1})
        service.client_authority.admit(owner.client_id, {
            key: review[key] for key in ("review_id", "review_digest")})
        await service.start()
        try:
            async with LocalRpcClient(profile, identity=owner) as admin:
                async with LocalRpcClient(profile, identity=operator) as client:
                    hook = await create_hook(client, steps=[
                        {"prompt": "PRIMERO", "pause_after": True},
                        {"prompt": "SEGUNDO NO AUTORIZADO", "pause_after": False}])
                    queued = await emit(client, hook)
                    await activate(client)
                    paused = await flow_until(admin, queued["flow_id"], {"paused"})
                    await admin.call("clients.revoke", {
                        "client_id": operator.client_id, "expected_revision": 2})
                    assert not (await admin.call("automation.list"))["runner_running"]
                    with pytest.raises(RpcError, match="activa el motor"):
                        await admin.call("automation.flows.resume", {
                            "flow_id": paused["flow_id"], "expected_revision": paused["revision"]})
                    assert service.automation_flows.get_flow(paused["flow_id"]) == {
                        key: value for key, value in paused.items() if key != "sessions"}
                    assert len(peer.calls) == 1
                    # A different owner must explicitly review and activate the
                    # engine before resuming the retained known checkpoint.
                    await activate(admin)
                    await admin.call("automation.flows.resume", {
                        "flow_id": paused["flow_id"], "expected_revision": paused["revision"]})
                    final = await flow_until(admin, queued["flow_id"], {"completed"})
                    assert final["outcome"] == "known" and len(peer.calls) == 2
                    await admin.call("automation.stop")
        finally:
            await service.aclose()


async def test_revoking_webhook_owner_closes_admission_and_does_not_borrow_another_owner():
    base = "/private/tmp" if sys.platform == "darwin" else tempfile.gettempdir()
    with tempfile.TemporaryDirectory(prefix="jvs-hook-rev-", dir=base) as value:
        profile = Path(value) / "profile"
        owner, operator = ClientIdentity.generate(), ClientIdentity.generate()
        peer = ManagementPeer()
        service = JarvisService(profile, peer, bootstrap_public_key=owner.public_key)
        review = service.client_authority.review(owner.client_id, {
            "public_key": operator.public_key, "scope": "owner", "expected_revision": 1})
        service.client_authority.admit(owner.client_id, {
            key: review[key] for key in ("review_id", "review_digest")})
        await service.start()
        try:
            async with LocalRpcClient(profile, identity=owner) as admin:
                async with LocalRpcClient(profile, identity=operator) as client:
                    hook = await create_hook(client, event="webhook")
                    snapshot = await client.call("automation.list")
                    parameters = {"hook_id": hook["hook_id"],
                        "expected_revision": hook["revision"],
                        "configuration_hash": snapshot["configuration_hash"], "secret": "S" * 32}
                    listener = (await client.call(
                        "automation.webhook.start", parameters))["listener"]
                    url = urlsplit(listener["address"])
                    # A failed start from another admitted owner cannot retarget
                    # the already active listener's identity or secret.
                    with pytest.raises(RpcError, match="already_open"):
                        await admin.call("automation.webhook.start", parameters)
                    assert service._automation_webhook_owner == operator.client_id
                    reader, writer = await asyncio.open_connection(url.hostname, url.port)
                    try:
                        writer.write(b"POST /hooks/")
                        await writer.drain()
                        async with asyncio.timeout(3):
                            while service.automation_webhook.active_count != 1:
                                await asyncio.sleep(0.005)
                        await admin.call("clients.revoke", {
                            "client_id": operator.client_id, "expected_revision": 2})
                        status = (await admin.call("automation.webhook.status"))["listener"]
                        assert not status["running"] and status["pending_cleanup"]
                        with pytest.raises(OSError):
                            await asyncio.open_connection(url.hostname, url.port)
                        with pytest.raises(AutomationFlowError, match="owner_revoked"):
                            service._emit_automation_event("webhook", uuid4().hex, {},
                                source="webhook", hook_id=hook["hook_id"],
                                expected_revision=hook["revision"])
                        assert not service.automation_flows.list_flows() and not peer.calls
                        async with asyncio.timeout(2):
                            try:
                                received = await reader.read()
                            except ConnectionResetError:
                                received = b""
                        assert received == b"" or received.startswith(b"HTTP/1.1 400 Jarvis\r\n")
                        await admin.call("automation.webhook.stop")
                        status = (await admin.call("automation.webhook.status"))["listener"]
                        assert not status["pending_cleanup"] and status["active_count"] == 0
                        assert service._automation_webhook_owner is None
                        assert service.automation_webhook._key is None
                    finally:
                        writer.close()
                        try:
                            await writer.wait_closed()
                        except ConnectionResetError:
                            pass
        finally:
            await service.aclose()


async def test_workflow_partial_start_failure_settles_owned_runtime_without_replay(
    management, monkeypatch,
):
    service, client, peer, _root = management
    original_start, original_chat = service.start_run, peer.chat
    async def blocked_peer(message, **options):
        # This synthetic provider enters and remains cancellable inside the real
        # runtime. No tools are proposed and no external account is contacted.
        return await original_chat("esperar", **options)
    async def partial_start(request):
        await original_start(request)
        await peer.entered.wait()
        raise RpcError(-32020, "synthetic_partial_workflow_start_ack_lost")
    monkeypatch.setattr(peer, "chat", blocked_peer)
    monkeypatch.setattr(service, "start_run", partial_start)
    hook = await create_hook(client, steps=[
        {"prompt": "ACK PARCIAL", "pause_after": False},
        {"prompt": "NO EJECUTAR DESPUÉS", "pause_after": False}])
    queued = await emit(client, hook, identity="workflow_lost_ack")
    await activate(client)
    final = await flow_until(client, queued["flow_id"], {"interrupted", "failed"})
    assert final["status"] == "interrupted" and final["outcome"] == "unknown"
    assert final["error_code"] == "automation_dispatch_unconfirmed"
    assert final["run_ids"] == [final["claim_id"]]
    assert final["receipts"][0]["run_id"] == final["claim_id"]
    async with asyncio.timeout(5):
        while service._handles:
            await asyncio.sleep(0.005)
    assert final["claim_id"] not in service._automation_origins
    assert final["claim_id"] not in service._automation_run_guards
    assert not service.authority.list_pending() and len(peer.calls) == 1
    await client.call("automation.stop")
    duplicate = await emit(client, hook, identity="workflow_lost_ack")
    assert duplicate["flow_id"] == final["flow_id"] and duplicate["outcome"] == "unknown"
    await activate(client)
    await asyncio.sleep(0.03)
    assert len(peer.calls) == 1
    await client.call("automation.stop")


async def test_service_close_can_retry_uncertain_webhook_without_releasing_profile_early(
    management,
):
    service, client, _peer, _root = management
    hook = await create_hook(client, event="webhook")
    snapshot = await client.call("automation.list")
    await client.call("automation.webhook.start", {
        "hook_id": hook["hook_id"], "expected_revision": hook["revision"],
        "configuration_hash": snapshot["configuration_hash"], "secret": "S" * 32})
    listener = service.automation_webhook
    class UnconfirmedWriter:
        fails = True
        def close(self):
            pass
        async def wait_closed(self):
            if self.fails:
                raise OSError("synthetic_transport_close_unconfirmed")
    writer = UnconfirmedWriter()
    listener._writers.add(writer)
    with pytest.raises(AutomationFlowError, match="automation_cleanup_pending"):
        await service.aclose()
    first = service._close_task
    assert first.done() and listener.pending_cleanup and listener._key == b"S" * 32
    service.profile_lease.assert_current()
    assert writer in listener._writers
    writer.fails = False
    await service.aclose()
    assert service._close_task is not first
    assert not listener.pending_cleanup and listener._key is None
    assert writer not in listener._writers
    with pytest.raises(HostProfileError, match="lease_closed"):
        service.profile_lease.assert_current()
