"""Owned no-fork peers; denied group signals never imply completed cleanup."""

import asyncio
import errno
import json
import os
import signal
import sys

import pytest

from jarvis_core.mcp import LEGACY_VERSION, McpClient, McpError
from jarvis_core.mcp_registry import McpRegistry, McpRegistryError, ServerSpec

PEER = r"""
import json, signal, sys, time
mode, log_path = sys.argv[1:]
signal.signal(signal.SIGTERM, lambda *_: None)
def record(value):
    with open(log_path, 'a') as stream:
        stream.write(json.dumps(value) + '\n')
def emit(request, result):
    print(json.dumps({'jsonrpc':'2.0','id':request['id'],'result':result}), flush=True)
for line in sys.stdin:
    request = json.loads(line)
    record(request)
    method = request.get('method')
    if method == 'initialize':
        emit(request, {
            'protocolVersion':'1900-01-01' if mode == 'bad-init' else '2025-11-25',
            'capabilities':{'tools':{}}, 'serverInfo':{'name':'synthetic nofork','version':'1'},
        })
    elif method == 'tools/list':
        emit(request, {'tools':[{'name':'echo','inputSchema':{'type':'object'}}]})
    elif method == 'tools/call':
        if mode == 'hang-call':
            while True: time.sleep(.02)
        emit(request, {'content':[{'type':'text','text':'synthetic report'}]})
if mode != 'exit-on-eof':
    while True: time.sleep(.02)
"""


def wire(path):
    return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []


async def eventually(predicate):
    async with asyncio.timeout(3):
        while not predicate():
            await asyncio.sleep(0.002)


@pytest.fixture
async def owned_peers(tmp_path):
    script = tmp_path / "nofork_peer.py"
    script.write_text(PEER)
    clients, registries = [], []

    def client(mode="ignore-eof"):
        index = len(clients)
        log = tmp_path / f"client-{index}.jsonl"
        value = McpClient(
            (sys.executable, str(script), mode, str(log)),
            tmp_path.resolve() / f"private-{index}",
            request_timeout=3,
            shutdown_timeout=0.1,
            authorize=lambda _name, _args: asyncio.sleep(0, result=True),
        )
        clients.append(value)
        return value, log

    def registry():
        value = McpRegistry(
            tmp_path.resolve() / f"registry-{len(registries)}",
            request_timeout=3,
            shutdown_timeout=0.1,
        )
        registries.append(value)
        return value

    def spec(identity, mode="ignore-eof"):
        log = tmp_path / f"{identity}.jsonl"
        command = (sys.executable, str(script), mode, str(log))
        return ServerSpec(identity, command, LEGACY_VERSION), log

    yield client, registry, spec
    for value in registries:
        await value.aclose()
    for value in clients:
        await value.aclose()


@pytest.mark.parametrize("denied_signal", [signal.SIGTERM, signal.SIGKILL])
async def test_denied_live_group_retains_owners_and_explicit_close_retry_never_replays(
    owned_peers,
    monkeypatch,
    denied_signal,
):
    client, log = owned_peers[0]()
    await client.start()
    await client.list_tools()
    await client.call_tool("echo", {})
    process, home, reader, stderr = client._process, client._home, client._reader, client._stderr
    original = os.killpg
    attempts = []

    def deny(pid, sig):
        if pid == process.pid:
            attempts.append(sig)
            if sig == denied_signal:
                raise PermissionError(errno.EPERM, "private synthetic signal diagnostic")
        return original(pid, sig)

    with monkeypatch.context() as patch:
        patch.setattr(os, "killpg", deny)
        with pytest.raises(McpError, match=r"^cleanup_incomplete$") as error:
            await client.aclose()
        assert error.value.effect_status == "unknown" and process.returncode is None
        failed = client._closing
        assert home.exists() and client._process is process
        assert client._reader is reader and not reader.done()
        assert client._stderr is stderr and not stderr.done()
        assert not client._ready and not client._tools
        with pytest.raises(McpError, match=r"^client_closed$"):
            await client.start()
        with pytest.raises(McpError):
            await client.call_tool("echo", {})
        with pytest.raises(McpError, match=r"^cleanup_incomplete$"):
            await client.aclose()
        sequence = (
            [signal.SIGTERM]
            if denied_signal == signal.SIGTERM
            else [signal.SIGTERM, signal.SIGKILL]
        )
        assert client._closing is not failed and attempts == sequence * 2
    await client.aclose()
    assert process.returncode == -signal.SIGKILL and not home.exists()
    assert reader.done() and stderr.done()
    with pytest.raises(ProcessLookupError):
        original(process.pid, 0)
    assert sum(row.get("method") == "initialize" for row in wire(log)) == 1
    assert sum(row.get("method") == "tools/call" for row in wire(log)) == 1


async def test_reaped_parent_is_not_group_absence_when_probe_is_denied(owned_peers, monkeypatch):
    client, _log = owned_peers[0]("exit-on-eof")
    await client.start()
    process, home, reader, stderr = client._process, client._home, client._reader, client._stderr
    original, probes = os.killpg, []

    def deny_probe(pid, sig):
        if pid == process.pid and sig == 0:
            probes.append(sig)
            raise PermissionError(errno.EPERM, "never expose peer-specific text")
        return original(pid, sig)

    with monkeypatch.context() as patch:
        patch.setattr(os, "killpg", deny_probe)
        with pytest.raises(McpError, match=r"^cleanup_incomplete$"):
            await client.aclose()
        assert process.returncode == 0 and home.exists() and probes
        assert client._reader is reader and client._stderr is stderr
    await client.aclose()
    assert not home.exists() and reader.done() and stderr.done()


async def test_final_group_sweep_denial_is_stable_and_preserves_home(owned_peers, monkeypatch):
    client, _log = owned_peers[0]("exit-on-eof")
    await client.start()
    process, home = client._process, client._home
    original, attempts = os.killpg, []

    def final_denial(pid, sig):
        if pid == process.pid and process.returncode is not None:
            attempts.append(sig)
            if sig == 0:
                return None  # Group state cannot be certified absent.
            raise PermissionError(errno.EPERM, "private final signal diagnostic")
        return original(pid, sig)

    with monkeypatch.context() as patch:
        patch.setattr(os, "killpg", final_denial)
        with pytest.raises(McpError, match=r"^cleanup_incomplete$"):
            await client.aclose()
        assert process.returncode == 0 and home.exists()
        assert attempts == [0, signal.SIGTERM]
    await client.aclose()
    assert not home.exists()


async def test_pending_call_is_released_without_replay_when_cleanup_signal_is_denied(
    owned_peers,
    monkeypatch,
):
    client, log = owned_peers[0]("hang-call")
    await client.start()
    await client.list_tools()
    calling = asyncio.create_task(client.call_tool("echo", {}))
    await eventually(lambda: any(row.get("method") == "tools/call" for row in wire(log)))
    pending = next(iter(client._pending.values()))
    original, process = os.killpg, client._process

    def deny(pid, sig):
        if pid == process.pid:
            raise PermissionError(errno.EPERM, "private diagnostic")
        return original(pid, sig)

    with monkeypatch.context() as patch:
        patch.setattr(os, "killpg", deny)
        with pytest.raises(McpError, match=r"^cleanup_incomplete$"):
            await client.aclose()
        assert pending.future.done() and pending.future.exception().code == "client_closed"
        with pytest.raises(McpError, match=r"^cleanup_incomplete$") as error:
            await asyncio.wait_for(calling, 1)
        assert error.value.effect_status == "unknown"
        assert not client._pending and client._home.exists()
    await client.aclose()
    assert sum(row.get("method") == "tools/call" for row in wire(log)) == 1


async def test_registry_closes_other_entries_preserves_incomplete_entry_and_retries(
    owned_peers,
    monkeypatch,
):
    registry = owned_peers[1]()
    bad_spec, log = owned_peers[2]("blocked")
    good_spec, _ = owned_peers[2]("cooperative", "exit-on-eof")
    await registry.connect(bad_spec)
    await registry.connect(good_spec)
    bad, good = registry._entries["blocked"].client, registry._entries["cooperative"].client
    bad_home, good_home = bad._home, good._home
    original = os.killpg

    def deny(pid, sig):
        if pid == bad._process.pid:
            raise PermissionError(errno.EPERM, "private diagnostic")
        return original(pid, sig)

    with monkeypatch.context() as patch:
        patch.setattr(os, "killpg", deny)
        with pytest.raises(McpRegistryError, match=r"^mcp_cleanup_incomplete$"):
            await registry.aclose()
        assert registry._closed and not registry._bindings
        assert len(registry._entries) == 2 and bad_home.exists() and bad._process.returncode is None
        assert good._process.returncode == 0 and not good_home.exists()
        statuses = {row.server_id: row for row in await registry.list_status()}
        assert statuses["blocked"].error == "mcp_cleanup_incomplete"
        assert not statuses["blocked"].connected and statuses["blocked"].tool_count == 0
        with pytest.raises(McpRegistryError, match=r"^mcp_cleanup_incomplete$"):
            await registry.forget("blocked")
        assert registry._entries["blocked"].client is bad
    await registry.aclose()
    assert bad._process.returncode == -signal.SIGKILL and not bad_home.exists()
    assert sum(row.get("method") == "initialize" for row in wire(log)) == 1
    assert not registry._bindings and registry._entries["blocked"].client is bad


async def test_failed_connect_retains_incomplete_owner_and_never_reconnects_before_cleanup(
    owned_peers,
    monkeypatch,
):
    registry = owned_peers[1]()
    spec, log = owned_peers[2]("invalid", "bad-init")
    original = os.killpg

    def deny(pid, sig):
        entry = registry._entries.get("invalid")
        if (
            entry is not None
            and entry.client._process is not None
            and pid == entry.client._process.pid
        ):
            raise PermissionError(errno.EPERM, "secret-like private diagnostic")
        return original(pid, sig)

    with monkeypatch.context() as patch:
        patch.setattr(os, "killpg", deny)
        with pytest.raises(McpRegistryError, match=r"^mcp_cleanup_incomplete$"):
            await registry.connect(spec)
        owner = registry._entries["invalid"].client
        assert owner._home.exists() and not registry._bindings
        with pytest.raises(McpRegistryError, match=r"^mcp_cleanup_incomplete$"):
            await registry.connect(spec)
        assert registry._entries["invalid"].client is owner
        assert sum(row.get("method") == "initialize" for row in wire(log)) == 1
    await registry.disconnect("invalid")
    assert not owner._home.exists() and owner._process.returncode == -signal.SIGKILL
