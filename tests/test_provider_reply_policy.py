"""Actual stdio handoff after the host's provider epoch changes."""
import asyncio

import pytest
from test_codex_tools import TOOL, confirmed, tool_replies, wait_until
from test_codex_tools import tool_peer as tool_peer

from jarvis_core.codex import CodexClient
from jarvis_core.service import JarvisService


@pytest.fixture
async def service(tmp_path, tool_peer):
    root = tmp_path / "host"
    root.mkdir(mode=0o700)
    client = CodexClient(tool_peer, root / "provider", timeout=2)
    service = JarvisService(root, client)
    service.provider_choice.select()
    await service.status({})
    try:
        yield service, client
    finally:
        await service.aclose()


async def test_revoked_success_reply_is_not_delivered_after_waiting_on_write_lock(service):
    service, client = service
    entered, release = asyncio.Event(), asyncio.Event()
    async def callback(name, arguments):
        entered.set()
        await release.wait()
        return confirmed("SENTINEL MUST NOT REACH REVOKED PROVIDER")
    token = service._provider_context.set(service.provider_choice.epoch())
    task = asyncio.create_task(client.chat("one", tool_specs=[TOOL], on_tool_call=callback))
    service._provider_context.reset(token)
    try:
        await asyncio.wait_for(entered.wait(), 2)
        async with client._write_lock:
            release.set()
            await asyncio.sleep(0)
            service.provider_choice.disable()
        await asyncio.wait_for(task, 3)
        await wait_until(lambda: bool(tool_replies(client)))
        replies = tool_replies(client)
        assert all(row["result"]["success"] is False for row in replies)
        assert "SENTINEL" not in str(replies)
    finally:
        release.set()
        if not task.done():
            task.cancel()
        await asyncio.gather(task, return_exceptions=True)


async def test_tool_request_uses_current_turn_epoch_not_sdk_readers_old_epoch(service):
    service, client = service
    # SDK reader already exists from revision1; this explicit selection is revision2.
    service.provider_choice.select()
    expected = service.provider_choice.epoch()
    captured = []
    async def callback(name, arguments):
        captured.append(service._provider_context.get())
        return confirmed("CURRENT TURN RECEIPT")
    token = service._provider_context.set(expected)
    try:
        await client.chat("one", tool_specs=[TOOL], on_tool_call=callback)
    finally:
        service._provider_context.reset(token)
    await wait_until(lambda: bool(tool_replies(client)))
    assert captured == [expected]
    assert tool_replies(client)[0]["result"]["success"] is True
