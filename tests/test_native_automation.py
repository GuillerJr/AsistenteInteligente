"""Broker authority, exact dispatch and uncertainty; native peers here are synthetic."""
import asyncio
import base64
import hashlib
import json
from contextlib import contextmanager
from types import SimpleNamespace
from uuid import uuid4

import pytest

from jarvis_core.automation_permissions import AutomationPermissions
from jarvis_core.native_automation import NativeAutomationBroker
from jarvis_core.native_tools import NativeAutomationTools
from jarvis_core.protocol import RpcError, authenticated_client
from jarvis_core.tools import ApprovalAuthority, ToolCatalog, ToolError, current_tool_operation
from jarvis_core.workspace import WorkspaceTools

OWNER = "a" * 64
OTHER = "b" * 64
HOST = "c" * 32
BROWSER = {"url": "https://example.com/fixture"}


@contextmanager
def client(identity=OWNER):
    token = authenticated_client.set(identity)
    try:
        yield
    finally:
        authenticated_client.reset(token)


@pytest.fixture
async def native(tmp_path):
    profile = tmp_path.resolve() / "profile"
    profile.mkdir(mode=0o700)
    root = tmp_path.resolve() / "workspace"
    root.mkdir(mode=0o700)
    owners, active, clock = {OWNER}, [True], [100.0]
    def owner(identity):
        if identity not in owners:
            raise RpcError(-32003, "synthetic owner denied")
    def guard(_run):
        if not active[0]:
            raise ToolError("synthetic_run_revoked")
    broker = NativeAutomationBroker(clock=lambda: clock[0], assert_owner=owner)
    permissions = AutomationPermissions(profile)
    workspace = WorkspaceTools(root)
    tools = NativeAutomationTools(broker, scope=lambda: (
        current_tool_operation().session_id, current_tool_operation().run_id,
    ), guard=guard, permissions=permissions, workspace=lambda: workspace)
    authority = ApprovalAuthority(ttl=4)
    catalog = ToolCatalog(tools.definitions(), tools.specs(), authority=authority)
    with client():
        await broker.attach({"host_id": HOST, "capabilities": ["desktop", "browser"]})
    value = SimpleNamespace(broker=broker, permissions=permissions, workspace=workspace,
        tools=tools, authority=authority, catalog=catalog, owners=owners, active=active,
        clock=clock, root=root, pending=[])
    try:
        yield value
    finally:
        for row in broker.work.values():
            row.cancellation.set()
            row.unresolved = False
            if not row.future.done():
                row.future.set_result(broker._unknown("synthetic_fixture_cleanup"))
        await asyncio.gather(*value.pending, return_exceptions=True)
        await authority.close()
        await broker.aclose()


async def begin(native, name="browser_open", args=None, *, allow=True, session="native-chat"):
    cancellation = asyncio.Event()
    task = asyncio.create_task(native.catalog.execute(name, args or BROWSER,
        session_id=session, run_id=uuid4().hex, cancellation=cancellation))
    native.pending.append(task)
    async with asyncio.timeout(3):
        while not task.done() and not native.authority.list_pending():
            await asyncio.sleep(0)
        if native.authority.list_pending():
            view = native.authority.list_pending()[0]
            native.authority.decide(view.approval_id, allow)
        if allow:
            while not task.done() and not any(not row.future.done()
                                              for row in native.broker.work.values()):
                await asyncio.sleep(0)
    with client():
        polled = await native.broker.poll({"host_id": HOST})
    work = next((row for row in polled["tasks"] if
                 native.broker.work[row["operation_id"]].session_id == session), None)
    return task, cancellation, work


def request(work):
    return {"host_id": HOST, **{k: work[k] for k in ("operation_id", "nonce", "digest")}}


async def claim(native, work):
    with client():
        return await native.broker.claim(request(work))


async def finish(native, work, result=None):
    result = result if result is not None else {
        "succeeded": True, "verified": False, "effect_status": "known", "tab_id": "fixture-tab",
    }
    with client():
        return await native.broker.complete({**request(work), "result": result})


async def test_approval_is_required_and_direct_callback_cannot_enqueue(native):
    task, _, work = await begin(native, allow=False)
    assert (await task).error == "approval_denied" and work is None
    with pytest.raises(ToolError, match="authority_required"):
        await native.tools.execute("browser_open", BROWSER, asyncio.Event())
    assert not native.broker.work


async def test_exact_claim_check_receipt_and_one_shot_dispatch(native):
    task, _, work = await begin(native)
    payload = json.loads(work["payload_json"])
    assert payload == {"command": "browser_open", "arguments": work["arguments"]}
    assert hashlib.sha256(work["payload_json"].encode()).hexdigest() == work["digest"]
    assert work["arguments"]["operation_id"] == work["operation_id"]
    assert (await claim(native, work))["claimed"] is True
    with client():
        assert (await native.broker.check(request(work)))["checked"] is True
        with pytest.raises(RpcError):
            await native.broker.claim(request(work))
    completed = await finish(native, work)
    result = await task
    assert result.succeeded and result.operation_id == work["operation_id"]
    assert completed["permits_replay"] is False and not native.broker.blockers
    assert (await finish(native, work))["result_sha256"] == completed["result_sha256"]
    with client():
        with pytest.raises(RpcError):
            await native.broker.complete({**request(work), "result": {
                "succeeded": False, "verified": False, "effect_status": "unknown",
                "error": "changed",
            }})


@pytest.mark.parametrize("field", ["host_id", "nonce", "digest", "operation_id"])
async def test_mismatched_identity_cannot_claim_or_check(native, field):
    task, _, work = await begin(native)
    bad = {**request(work), field: "f" * len(request(work)[field])}
    with client():
        with pytest.raises(RpcError):
            await native.broker.claim(bad)
    assert not native.broker.work[work["operation_id"]].claimed
    await claim(native, work)
    with client():
        with pytest.raises(RpcError):
            await native.broker.check(bad)
    await finish(native, work)
    assert (await task).succeeded


async def test_another_owner_cannot_take_or_complete_this_workers_operation(native):
    native.owners.add(OTHER)
    task, _, work = await begin(native)
    with client(OTHER):
        for handler, args in [
            (native.broker.poll, {"host_id": HOST}),
            (native.broker.claim, request(work)),
            (native.broker.attach, {"host_id": "d" * 32, "capabilities": ["browser"]}),
        ]:
            with pytest.raises(RpcError):
                await handler(args)
    await claim(native, work)
    with client(OTHER):
        with pytest.raises(RpcError):
            await native.broker.complete({**request(work), "result": {
                "succeeded": True, "verified": False, "effect_status": "known",
            }})
    await finish(native, work)
    assert (await task).succeeded


async def test_client_revocation_is_checked_again_before_native_effect(native):
    task, _, work = await begin(native)
    await claim(native, work)
    native.owners.remove(OWNER)
    with client():
        with pytest.raises(RpcError):
            await native.broker.check(request(work))
    native.owners.add(OWNER)
    native.active[0] = False
    with client():
        with pytest.raises(RpcError):
            await native.broker.check(request(work))
    assert native.broker.work[work["operation_id"]].cancelled
    await finish(native, work, native.broker._unknown("cancelled_before_effect"))
    assert not (await task).succeeded


async def test_exact_authority_revoke_prevents_dispatch_after_worker_claim(native):
    task, _, work = await begin(native)
    await claim(native, work)
    native.authority.revoke("native-chat")
    with client():
        with pytest.raises(RpcError):
            await native.broker.check(request(work))
    await finish(native, work, native.broker._unknown("revoked_cleanup"))
    reply = await task
    assert not reply.succeeded and reply.effect_status == "unknown"


async def test_cancel_before_claim_returns_no_dispatch_and_disallows_late_claim(native):
    task, cancellation, work = await begin(native)
    cancellation.set()
    with client():
        await native.broker.poll({"host_id": HOST})
        with pytest.raises(RpcError):
            await native.broker.claim(request(work))
    with pytest.raises(asyncio.CancelledError):
        await task
    assert not native.broker.blockers


async def test_claimed_cancel_retains_owner_until_same_worker_confirms_cleanup(native):
    task, cancellation, work = await begin(native)
    await claim(native, work)
    cancellation.set()
    with client():
        poll = await native.broker.poll({"host_id": HOST})
    assert poll["cancelled"] == [work["operation_id"]]
    with pytest.raises(ToolError, match="cleanup_pending"):
        native.broker.assert_settled("native-chat")
    assert not task.done()
    await finish(native, work, native.broker._unknown("cancelled_after_dispatch"))
    with pytest.raises(asyncio.CancelledError):
        await task
    native.broker.assert_settled("native-chat")


async def test_deadline_before_claim_does_not_authorize_an_effect(native):
    task, _, work = await begin(native)
    native.clock[0] += 25
    with client():
        assert (await native.broker.poll({"host_id": HOST}))["tasks"] == []
        with pytest.raises(RpcError):
            await native.broker.claim(request(work))
    reply = await task
    assert not reply.succeeded and reply.error == "native_not_dispatched"
    assert reply.effect_status == "known"


async def test_worker_loss_is_unknown_and_late_receipt_is_cleanup_not_replay(native):
    task, _, work = await begin(native)
    await claim(native, work)
    native.clock[0] += 9
    reply = await asyncio.wait_for(task, 2)
    assert not reply.succeeded and reply.effect_status == "unknown"
    assert native.broker.blockers
    with pytest.raises(ToolError):
        native.broker.assert_settled("native-chat")
    with client():
        await native.broker.attach({"host_id": HOST, "capabilities": ["browser", "desktop"]})
        assert (await native.broker.poll({"host_id": HOST}))["tasks"] == []
    await finish(native, work, native.broker._unknown("late_cleanup_confirmation"))
    native.broker.assert_settled("native-chat")
    with client():
        with pytest.raises(RpcError):
            await native.broker.claim(request(work))
    assert reply.effect_status == "unknown"  # A late acknowledgement cannot rewrite history.


async def test_detach_needs_own_worker_cleanup_not_a_cancel_ack(native):
    task, _, work = await begin(native)
    await claim(native, work)
    with pytest.raises(TimeoutError, match="cleanup_incomplete"):
        await native.broker.aclose()
    assert not native.broker.closed
    with client():
        with pytest.raises(RpcError):
            await native.broker.detach({"host_id": HOST, "cleanup_confirmed": False})
        detached = await native.broker.detach({"host_id": HOST, "cleanup_confirmed": True})
        assert detached["detached"]
    reply = await task
    assert not reply.succeeded and not native.broker.blockers


@pytest.mark.parametrize("result", [
    {"succeeded": True, "verified": True, "effect_status": "unknown"},
    {"succeeded": True, "verified": False, "effect_status": "unknown"},
    {"succeeded": False, "verified": True, "effect_status": "known"},
    {"succeeded": False, "verified": False, "effect_status": "known", "error": {"private": "data"}},
])
async def test_malformed_or_contradictory_native_receipt_is_not_accepted(native, result):
    task, _, work = await begin(native)
    await claim(native, work)
    with client():
        with pytest.raises(RpcError) as error:
            await native.broker.complete({**request(work), "result": result})
    assert error.value.code == -32602 and not task.done()
    await finish(native, work)
    assert (await task).succeeded


async def test_ephemeral_frame_is_hash_bound_once_and_not_put_into_transcript(native):
    args = {"target": {"bundle_id": "com.jarvis.fixture", "pid": 7319,
                       "launch_id": "synthetic-launch"}, "window_ref": "synthetic-window",
            "include_frame": True}
    task, _, work = await begin(native, "desktop_observe", args)
    await claim(native, work)
    raw = b"\xff\xd8\xffsynthetic-frame-carrier\xff\xd9"
    data_url = "data:image/jpeg;base64," + base64.b64encode(raw).decode()
    await finish(native, work, {"succeeded": True, "verified": False, "effect_status": "known",
        "schema": "jarvis.desktop.v1", "frame": {"data_url": data_url, "bytes": len(raw),
                  "sha256": hashlib.sha256(raw).hexdigest(),
                  "storage": "memory_only"}})
    reply = await task
    assert reply.succeeded and "data_url" not in reply.output and data_url not in reply.output
    assert native.tools.take_frame(reply) == data_url
    assert native.tools.take_frame(reply) is None


async def test_unexpected_frame_after_action_cannot_turn_uncertain_effect_into_known_failure(
    native,
):
    args = {"target": {"bundle_id": "com.jarvis.fixture", "pid": 7319,
                       "launch_id": "synthetic-launch"}, "window_ref": "synthetic-window",
            "snapshot_id": "synthetic-snapshot", "ref": "synthetic-ref",
            "action": "set_value", "value": "synthetic value"}
    task, _, work = await begin(native, "desktop_action", args)
    await claim(native, work)
    await finish(native, work, {"succeeded": True, "verified": True, "effect_status": "known",
                               "frame": {"data_url": "corrupted"}})
    reply = await task
    assert not reply.succeeded and reply.effect_status == "unknown"
    assert "corrupted" not in reply.output and not native.tools._frames


async def test_download_copies_only_original_reviewed_path_and_verifies_workspace_bytes(native):
    args = {"tab_id": "synthetic-tab", "expected_origin": "https://example.com",
            "url": "https://example.com/fixture.bin", "path": "download.bin"}
    task, _, work = await begin(native, "browser_download", args)
    assert "path" not in work["arguments"]
    await claim(native, work)
    payload = b"OWN DOWNLOAD 7319"
    await finish(native, work, {"succeeded": True, "verified": False, "effect_status": "known",
        "bytes_base64": base64.b64encode(payload).decode(), "bytes": len(payload),
        "sha256": hashlib.sha256(payload).hexdigest(), "written_to_workspace": False})
    reply = await task
    assert reply.succeeded and reply.verified
    assert (native.root / "download.bin").read_bytes() == payload
    assert "bytes_base64" not in reply.output
    result = json.loads(reply.output)
    assert result["written_to_workspace"] is True
    assert result["file"] == {"path": "download.bin", "bytes": len(payload),
                              "sha256": hashlib.sha256(payload).hexdigest()}
    assert base64.b64encode(payload).decode() not in reply.output


async def test_failed_native_download_cannot_claim_a_workspace_write_or_preserve_bytes(native):
    args = {"tab_id": "synthetic-tab", "expected_origin": "https://example.com",
            "url": "https://example.com/fixture.bin", "path": "never.bin"}
    task, _, work = await begin(native, "browser_download", args)
    await claim(native, work)
    payload = b"OWN FAILED DOWNLOAD 7319"
    await finish(native, work, {"succeeded": False, "verified": False, "effect_status": "known",
        "error": "synthetic_fetch_denied", "written_to_workspace": True,
        "file": {"path": "never.bin"}, "bytes_base64": base64.b64encode(payload).decode()})
    reply = await task
    result = json.loads(reply.output)
    assert not reply.succeeded and not reply.verified
    assert reply.error == "synthetic_fetch_denied" and not (native.root / "never.bin").exists()
    assert result["written_to_workspace"] is False and "file" not in result
    assert "bytes_base64" not in reply.output
    assert base64.b64encode(payload).decode() not in reply.output


async def test_download_publication_failure_preserves_existing_file_without_success_flag(native):
    args = {"tab_id": "synthetic-tab", "expected_origin": "https://example.com",
            "url": "https://example.com/fixture.bin", "path": "existing.bin"}
    original = b"DO NOT REPLACE SYNTHETIC DATA"
    path = native.root / "existing.bin"
    path.write_bytes(original)
    path.chmod(0o600)
    task, _, work = await begin(native, "browser_download", args)
    await claim(native, work)
    payload = b"OWN NEW DOWNLOAD 7319"
    await finish(native, work, {"succeeded": True, "verified": False, "effect_status": "known",
        "written_to_workspace": True, "bytes_base64": base64.b64encode(payload).decode(),
        "bytes": len(payload), "sha256": hashlib.sha256(payload).hexdigest()})
    reply = await task
    assert not reply.succeeded and not reply.verified
    assert reply.error == "download_publication_failed" and reply.effect_status == "known"
    assert reply.output == "" and path.read_bytes() == original
    assert {entry.name for entry in native.root.iterdir()} == {"existing.bin"}


@pytest.mark.parametrize("corruption", ["hash", "size", "bytes"])
async def test_download_corruption_never_publishes_a_file(native, corruption):
    args = {"tab_id": "synthetic-tab", "expected_origin": "https://example.com",
            "url": "https://example.com/fixture.bin", "path": "never.bin"}
    task, _, work = await begin(native, "browser_download", args)
    await claim(native, work)
    payload = b"OWN DOWNLOAD 7319"
    result = {"succeeded": True, "verified": False, "effect_status": "known",
        "bytes_base64": base64.b64encode(payload).decode(), "bytes": len(payload),
        "sha256": hashlib.sha256(payload).hexdigest()}
    if corruption == "hash":
        result["sha256"] = "0" * 64
    elif corruption == "size":
        result["bytes"] += 1
    else:
        result["bytes_base64"] = "not base64"
    await finish(native, work, result)
    reply = await task
    assert not reply.succeeded and not (native.root / "never.bin").exists()


async def test_signed_unix_worker_reconnects_after_lost_acks_without_repeating_effects():
    """Real signatures/sockets and dropped ACKs; the effect is a local synthetic counter."""
    import sys
    import tempfile
    from pathlib import Path

    from jarvis_core.client_identity import ClientAuthority, ClientIdentity, ClientIdentityError
    from jarvis_core.protocol import LocalRpcClient, LocalRpcServer

    base = "/private/tmp" if sys.platform == "darwin" else tempfile.gettempdir()
    with tempfile.TemporaryDirectory(prefix="jnwire-", dir=base) as name:
        root = Path(name).resolve()
        profile, workspace = root / "profile", root / "workspace"
        profile.mkdir(mode=0o700)
        workspace.mkdir(mode=0o700)
        admin, worker, reader = (ClientIdentity.generate() for _ in range(3))
        registry = ClientAuthority(profile, bootstrap_public_key=admin.public_key)
        for identity, scope in [(worker, "owner"), (reader, "read")]:
            review = registry.review(admin.client_id, {"public_key": identity.public_key,
                "scope": scope, "expected_revision": registry.status(admin.client_id)["revision"]})
            registry.admit(admin.client_id, {k: review[k] for k in ("review_id", "review_digest")})
        def owner(identity):
            try:
                registry.lookup(identity, scope="owner")
            except ClientIdentityError:
                raise RpcError(-32003, "synthetic worker scope denied") from None
        broker = NativeAutomationBroker(assert_owner=owner)
        permissions = AutomationPermissions(profile)
        tools = NativeAutomationTools(broker, scope=lambda: (
            current_tool_operation().session_id, current_tool_operation().run_id,
        ), guard=lambda _: registry.lookup(admin.client_id, scope="owner"),
            permissions=permissions, workspace=lambda: WorkspaceTools(workspace))
        authority = ApprovalAuthority(ttl=4)
        catalog = ToolCatalog(tools.definitions(), tools.specs(), authority=authority)
        claim_seen, release_claim = asyncio.Event(), asyncio.Event()
        complete_seen, release_complete = asyncio.Event(), asyncio.Event()
        effects, tasks = [], []
        async def lost_claim(value):
            result = await broker.claim(value)
            claim_seen.set()
            await release_claim.wait()
            return result
        async def lost_complete(value):
            result = await broker.complete(value)
            complete_seen.set()
            await release_complete.wait()
            return result
        handlers = {**broker.handlers(), "automation.claim_lost": lost_claim,
                    "automation.complete_lost": lost_complete}
        try:
            async with LocalRpcServer(profile, handlers, client_authority=registry,
                                      read_methods=frozenset({"automation.status"})):
                async with LocalRpcClient(profile, identity=reader) as connection:
                    assert not (await connection.call("automation.status"))["connected"]
                    with pytest.raises(RpcError) as denied:
                        await connection.call("automation.attach", {
                            "host_id": HOST, "capabilities": ["browser"],
                        })
                    assert denied.value.code == -32003
                async with LocalRpcClient(profile, identity=worker) as connection:
                    await connection.call("automation.attach", {
                        "host_id": HOST, "capabilities": ["browser"],
                    })
                    task = asyncio.create_task(catalog.execute("browser_open", BROWSER,
                        session_id="wire-session", run_id=uuid4().hex))
                    tasks.append(task)
                    async with asyncio.timeout(3):
                        while not authority.list_pending():
                            await asyncio.sleep(0)
                        authority.decide(authority.list_pending()[0].approval_id, True)
                        while not broker.work:
                            await asyncio.sleep(0)
                    work = (await connection.call("automation.poll", {"host_id": HOST}))["tasks"][0]
                    body = request(work)
                    lost = asyncio.create_task(connection.call("automation.claim_lost", body))
                    await asyncio.wait_for(claim_seen.wait(), 2)
                    lost.cancel()
                    with pytest.raises(asyncio.CancelledError):
                        await lost
                    release_claim.set()
                async with LocalRpcClient(profile, identity=admin) as connection:
                    with pytest.raises(RpcError):
                        await connection.call("automation.poll", {"host_id": HOST})
                async with LocalRpcClient(profile, identity=worker) as connection:
                    with pytest.raises(RpcError):
                        await connection.call("automation.claim", body)
                    assert (await connection.call("automation.check", body))["checked"]
                    effects.append("SYNTHETIC EFFECT 7319")
                    payload = {**body, "result": {"succeeded": True, "verified": False,
                        "effect_status": "known", "tab_id": "synthetic-wire-tab"}}
                    lost = asyncio.create_task(connection.call("automation.complete_lost", payload))
                    await asyncio.wait_for(complete_seen.wait(), 2)
                    lost.cancel()
                    with pytest.raises(asyncio.CancelledError):
                        await lost
                    release_complete.set()
                result = await asyncio.wait_for(task, 2)
                assert result.succeeded and result.operation_id == work["operation_id"]
                async with LocalRpcClient(profile, identity=worker) as connection:
                    assert (await connection.call("automation.complete", payload))["accepted"]
                    observed = await connection.call("automation.poll", {"host_id": HOST})
                    assert observed["tasks"] == []
                    with pytest.raises(RpcError):
                        await connection.call("automation.claim", body)
                    assert (await connection.call("automation.detach", {
                        "host_id": HOST, "cleanup_confirmed": True,
                    }))["detached"]
                assert effects == ["SYNTHETIC EFFECT 7319"]
                assert not broker.blockers
        finally:
            release_claim.set()
            release_complete.set()
            for row in broker.work.values():
                row.unresolved = False
                if not row.future.done():
                    row.future.set_result(broker._unknown("fixture_cleanup"))
            await asyncio.gather(*tasks, return_exceptions=True)
            await broker.aclose()
            await authority.close()
