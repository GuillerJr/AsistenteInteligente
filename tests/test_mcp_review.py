"""Independent adversarial MCP review using only owned synthetic subprocesses."""

import asyncio
import contextlib
import signal
import sys

import pytest

from jarvis_core.mcp import McpClient, McpError

_PEER = '''
import json, sys, time
mode = sys.argv[1]
def emit(identity, result):
    print(json.dumps({"jsonrpc":"2.0", "id":identity, "result":result}), flush=True)
for line in sys.stdin:
    message = json.loads(line)
    method, identity = message.get("method"), message.get("id")
    if method == "initialize":
        emit(identity, {"protocolVersion":"2025-11-25", "capabilities":{"tools":{}},
                        "serverInfo":{"name":"private-review-fixture", "version":"1"}})
    elif method == "tools/list":
        value_schema = {"type":"object"}
        if mode == "const": value_schema["const"] = {"x":[1]}
        if mode == "enum": value_schema["enum"] = [{"x":[1]}]
        emit(identity, {"tools":[{"name":"fixture", "inputSchema":{
            "type":"object", "properties":{"value":value_schema},
            "required":["value"], "additionalProperties":False}}]})
    elif method == "tools/call":
        if mode == "oversized":
            print("X" * 16384, flush=True)
            time.sleep(30)
        emit(identity, {"content":[{"type":"text", "text":"Reported fixture data"}]})
'''


@pytest.fixture
def peer_script(tmp_path):
    path = tmp_path.resolve() / "review_peer.py"
    path.write_text(_PEER)
    return path


async def _allow_fixture(tool, arguments):
    return True  # Only a synthetic local tool; this never authorizes a product action.


async def test_oversized_reply_after_dispatch_retains_unknown_effect(peer_script):
    client = McpClient((sys.executable, str(peer_script), "oversized"),
                       peer_script.parent / "mcp-private", authorize=_allow_fixture,
                       max_line_bytes=2048, shutdown_timeout=0.05)
    try:
        await client.start()
        await client.list_tools()
        with pytest.raises(McpError, match="message_limit") as caught:
            await client.call_tool("fixture", {"value": {}})
        assert caught.value.effect_status == "unknown"
        assert client._process.returncode is not None
    finally:
        await client.aclose()


async def test_close_during_spawn_owns_and_reaps_the_late_child(peer_script, monkeypatch):
    client = McpClient((sys.executable, str(peer_script), "normal"),
                       peer_script.parent / "mcp-private", shutdown_timeout=0.05)
    created, release = asyncio.Event(), asyncio.Event()
    create = asyncio.create_subprocess_exec
    children = []

    async def delayed_create(*args, **kwargs):
        process = await create(*args, **kwargs)
        children.append(process)
        created.set()
        await release.wait()
        return process

    monkeypatch.setattr(asyncio, "create_subprocess_exec", delayed_create)
    starting = asyncio.create_task(client.start())
    closing = None
    try:
        await asyncio.wait_for(created.wait(), 1)
        closing = asyncio.create_task(client.aclose())
        await asyncio.sleep(0.02)
        release.set()
        with contextlib.suppress(McpError, asyncio.CancelledError):
            await asyncio.wait_for(starting, 2)
        await asyncio.wait_for(closing, 2)
        assert children[0].returncode is not None, "Un hijo tardío quedó fuera del cierre"
        assert not client._ready
        assert not client._home.exists()
        assert client._reader is None or client._reader.done()
    finally:
        release.set()
        with contextlib.suppress(McpError, asyncio.CancelledError):
            await asyncio.wait_for(starting, 2)
        if closing is not None:
            await asyncio.wait_for(closing, 2)
        for child in children:
            if child.returncode is None:
                child.send_signal(signal.SIGTERM)
                await asyncio.wait_for(child.wait(), 2)
        await client.aclose()


@pytest.mark.parametrize("mode", ["const", "enum"])
async def test_supported_structured_schema_values_survive_frozen_catalog(peer_script, mode):
    client = McpClient((sys.executable, str(peer_script), mode),
                       peer_script.parent / "mcp-private", authorize=_allow_fixture)
    try:
        await client.start()
        await client.list_tools()
        result = await client.call_tool("fixture", {"value": {"x": [1]}})
        assert not result.verified
        assert result.payload["content"][0]["text"] == "Reported fixture data"
        with pytest.raises(McpError, match="invalid_tool_arguments"):
            await client.call_tool("fixture", {"value": {"x": [2]}})
    finally:
        await client.aclose()


@pytest.mark.parametrize("mode", ["public", "ancestor_symlink"])
async def test_unsafe_private_parent_is_rejected_before_spawn(peer_script, mode):
    if mode == "public":
        directory = peer_script.parent / "public-parent"
        directory.mkdir(mode=0o777)
        directory.chmod(0o777)
    else:
        actual = peer_script.parent / "actual"
        actual.mkdir(mode=0o700)
        alias = peer_script.parent / "alias"
        alias.symlink_to(actual, target_is_directory=True)
        directory = alias / "nested"
    client = McpClient((sys.executable, str(peer_script), "normal"), directory)
    try:
        with pytest.raises(McpError):
            await client.start()
        assert client._process is None
    finally:
        await client.aclose()
