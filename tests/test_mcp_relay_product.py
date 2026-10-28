"""External own HTTP client -> real service -> central Runtime authority."""

from __future__ import annotations

import asyncio

import pytest
from test_mcp_product import server
from test_service import connection as connection
from test_service import session, settled

from jarvis_core.mcp import MODERN_VERSION, McpError
from jarvis_core.mcp_http import McpHttpClient
from jarvis_core.protocol import RpcError


async def relay_control(owner, ref, action, *, exports=None):
    review = (
        await owner.call(
            "mcp.relay.review",
            {
                **ref,
                "action": action,
                "exports": (exports or ["workspace_write"]) if action == "start" else [],
                "port": 0,
            },
        )
    )["review"]
    return await owner.call(
        "mcp.relay.decide",
        {"review_id": review["review_id"], "digest": review["digest"], "allow": True},
    )


async def remote_client(owner, ref, directory, *, exports=None):
    result = await relay_control(owner, ref, "start", exports=exports)
    endpoint = result["relay"]["endpoint"]
    token = (await owner.call("mcp.relay.token"))["token"]

    class Lease:
        resource = endpoint
        authorization_header = "Bearer " + token

        def assert_current(self):
            pass

    async def permit_transport(*_):
        return True

    remote = McpHttpClient(
        endpoint,
        directory,
        protocol_version=MODERN_VERSION,
        allow_loopback=True,
        credentials=lambda _: Lease(),
        authorize=permit_transport,
    )
    await remote.start()
    await remote.list_tools()
    return remote, token


async def pending(owner, ref):
    async with asyncio.timeout(5):
        while True:
            rows = (await owner.call("approvals.list", ref))["approvals"]
            if rows:
                return rows[0]
            await asyncio.sleep(0.005)


async def test_external_relay_tool_needs_central_approval_then_catalog_notice_and_stale_stop(
    connection, tmp_path
):
    service, owner, provider, root = connection
    ref = await session(owner)
    remote, token = await remote_client(owner, ref, tmp_path.resolve() / "external-client")
    task = asyncio.create_task(
        remote.call_tool("workspace_write", {"path": "relay-one.txt", "text": "ONE OWN EFFECT"})
    )
    try:
        approval = await pending(owner, ref)
        assert not (root / "workspace" / "relay-one.txt").exists()
        assert approval["arguments"] == {"path": "relay-one.txt", "text": "ONE OWN EFFECT"}
        await owner.call(
            "approvals.decide",
            {
                **ref,
                "approval_id": approval["approval_id"],
                "digest": approval["digest"],
                "allow": True,
            },
        )
        result = await asyncio.wait_for(task, 5)
        run = await settled(owner, ref, approval["run_id"])
        assert not result.is_error and run["status"] == "completed"
        assert run["action_receipts"][0]["verified"] is True
        assert (root / "workspace" / "relay-one.txt").read_text() == "ONE OWN EFFECT"
        assert len(run["action_receipts"]) == 1 and not provider.calls
        assert token not in "\n".join(service.store._disk.iterdump())
        # Rebuilding identical contents must preserve the useful relay view.
        service._configure_tools()
        assert not (await owner.call("mcp.relay.status"))["relay"]["requires_review"]
        # A real owned MCP connection adds a tool without changing the workspace.
        spec = server(root)
        await owner.call("mcp.add", spec)
        await owner.call("mcp.connect", spec)
        async with asyncio.timeout(5):
            while "notifications/tools/list_changed" not in remote.notification_methods:
                await asyncio.sleep(0.005)
        notice = service._network_state()["catalog_notice"]
        assert notice is not None
        await asyncio.shield(notice)
        status = (await owner.call("mcp.relay.status"))["relay"]
        assert status["running"] and status["requires_review"] and not status["token_available"]
        assert service._network_state()["catalog_notice_delivery"]["flushed"] == 1
        assert not remote._tools
        with pytest.raises((McpError, RpcError)):
            await owner.call("mcp.relay.token")
        # Closing remains possible after the reviewed catalog became stale.
        closed = await relay_control(owner, ref, "stop")
        assert not closed["relay"]["running"] and not closed["relay"]["requires_review"]
        assert not service.mcp_network_blockers
    finally:
        if not task.done():
            task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        await remote.aclose()


@pytest.mark.parametrize("action", ["stop", "revoke"])
async def test_owner_control_cancels_only_relay_pending_approval_without_effect(
    connection, tmp_path, action
):
    service, owner, provider, root = connection
    ref = await session(owner)
    remote, _token = await remote_client(owner, ref, tmp_path.resolve() / "remote")
    task = asyncio.create_task(
        remote.call_tool("workspace_write", {"path": "never-created.txt", "text": "NO EFFECT"})
    )
    try:
        approval = await pending(owner, ref)
        assert approval["run_id"] in service._handles
        result = await relay_control(owner, ref, action)
        if action == "stop":
            assert not result["relay"]["running"]
        async with asyncio.timeout(5):
            await asyncio.gather(task, return_exceptions=True)
        receipt = await settled(owner, ref, approval["run_id"])
        assert receipt["status"] in {"cancelled", "failed"}
        assert not (root / "workspace" / "never-created.txt").exists()
        assert all(not item["succeeded"] for item in receipt["action_receipts"])
        assert not provider.calls
        assert not (await owner.call("approvals.list", ref))["approvals"]
        if action == "revoke":
            assert not service._network_state()["relay"].blockers
            await relay_control(owner, ref, "stop")
        assert not service.mcp_network_blockers
    finally:
        if not task.done():
            task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        await remote.aclose()


async def test_exported_readonly_memory_recall_still_waits_for_central_approval(
    connection, tmp_path
):
    service, owner, provider, _root = connection
    ref = await session(owner)
    remote, _token = await remote_client(
        owner, ref, tmp_path.resolve() / "readonly-peer", exports=["memory_recall"]
    )
    task = asyncio.create_task(remote.call_tool("memory_recall", {"query": "marcador propio"}))
    try:
        approval = await pending(owner, ref)
        assert approval["tool_name"] == "memory_recall"
        assert service.memory._disk is None
        assert not task.done() and not provider.calls
        await owner.call(
            "approvals.decide",
            {
                **ref,
                "approval_id": approval["approval_id"],
                "digest": approval["digest"],
                "allow": True,
            },
        )
        result = await asyncio.wait_for(task, 5)
        receipt = await settled(owner, ref, approval["run_id"])
        assert not result.is_error and receipt["status"] == "completed"
        assert len(receipt["action_receipts"]) == 1
        assert service.memory._disk is not None and not provider.calls
        await relay_control(owner, ref, "stop")
    finally:
        if not task.done():
            task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        await remote.aclose()


@pytest.mark.parametrize("action", ["stop", "revoke"])
async def test_relay_control_retains_postdispatch_cleanup_and_preserves_single_effect(
    connection, tmp_path, action
):
    service, owner, provider, root = connection
    ref = await session(owner)
    remote, _token = await remote_client(owner, ref, tmp_path.resolve() / "held-cleanup-peer")
    cleaning, release = asyncio.Event(), asyncio.Event()
    original_cleanup = service.runtime._cleanup

    async def held_cleanup(context):
        await original_cleanup(context)
        cleaning.set()
        await release.wait()

    service.runtime._cleanup = held_cleanup
    request = asyncio.create_task(
        remote.call_tool(
            "workspace_write",
            {"path": "single-before-cancel.txt", "text": "ONE VERIFIED EFFECT BEFORE CLEANUP"},
        )
    )
    closing = None
    try:
        approval = await pending(owner, ref)
        await owner.call(
            "approvals.decide",
            {
                **ref,
                "approval_id": approval["approval_id"],
                "digest": approval["digest"],
                "allow": True,
            },
        )
        await asyncio.wait_for(cleaning.wait(), 5)
        path = root / "workspace" / "single-before-cancel.txt"
        assert path.read_text() == "ONE VERIFIED EFFECT BEFORE CLEANUP"
        modified_ns = path.stat().st_mtime_ns
        closing = asyncio.create_task(relay_control(owner, ref, action))
        async with asyncio.timeout(5):
            while not service._network_state()["transition"]:
                await asyncio.sleep(0.001)
        # Both transport cancellation and callback cancellation must leave the
        # central Runtime's cleanup lane owned until its actual acknowledgement.
        await asyncio.sleep(0.02)
        assert not closing.done()
        assert approval["run_id"] in service._handles
        assert service._network_state()["relay"].blockers
        release.set()
        await asyncio.wait_for(closing, 5)
        await asyncio.wait_for(asyncio.gather(request, return_exceptions=True), 5)
        receipt = await settled(owner, ref, approval["run_id"])
        assert receipt["status"] == "cancelled"
        assert len(receipt["action_receipts"]) == 1
        assert receipt["action_receipts"][0]["succeeded"] is True
        assert receipt["action_receipts"][0]["verified"] is True
        assert path.stat().st_mtime_ns == modified_ns
        assert not provider.calls
        if action == "revoke":
            await relay_control(owner, ref, "stop")
        assert not service.mcp_network_blockers
    finally:
        release.set()
        if closing is not None:
            await asyncio.gather(closing, return_exceptions=True)
        if not request.done():
            request.cancel()
        await asyncio.gather(request, return_exceptions=True)
        await remote.aclose()
