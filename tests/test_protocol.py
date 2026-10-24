"""Real Unix-socket transport tests with isolated, synthetic state only."""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import stat
import sys
import tempfile
from pathlib import Path

import pytest

from jarvis_core.protocol import LocalRpcClient, LocalRpcServer, RpcError, TransportSecurityError


@pytest.fixture
def state_directory():
    # Keep sockaddr_un below macOS's 104-byte maximum; no owner paths are read.
    temporary_root = "/private/tmp" if sys.platform == "darwin" else tempfile.gettempdir()
    with tempfile.TemporaryDirectory(prefix="jarvis-rpc-", dir=temporary_root) as path:
        yield Path(path).resolve()


async def echo(params):
    return params


async def raw_call(server, request):
    reader, writer = await asyncio.open_unix_connection(server.socket_path)
    try:
        writer.write(json.dumps(request).encode() + b"\n")
        await writer.drain()
        return json.loads(await asyncio.wait_for(reader.readline(), 1))
    finally:
        writer.close()
        await writer.wait_closed()


def envelope(server, **changes):
    request = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "health",
        "params": {},
        "auth": server.token_path.read_text(),
    }
    request.update(changes)
    return request


async def test_real_client_health_echo_private_modes_and_cleanup(state_directory):
    server = LocalRpcServer(state_directory, {"echo": echo})
    async with server:
        assert server.is_running
        assert stat.S_IMODE(state_directory.stat().st_mode) == 0o700
        assert stat.S_IMODE(server.token_path.stat().st_mode) == 0o600
        assert stat.S_IMODE(server.socket_path.stat().st_mode) == 0o600
        token = server.token_path.read_text()
        assert len(token) == 64
        async with LocalRpcClient(state_directory) as client:
            assert await client.call("health") == {
                "service": "jarvis",
                "protocol": "jarvis-local-rpc",
                "version": 1,
            }
            assert await client.call("echo", {"texto": "Hola", "n": 3}) == {"texto": "Hola", "n": 3}
    assert not server.is_running
    assert not server.socket_path.exists()
    assert server.active_connections == 0
    # A stable credential survives orderly restarts; no silent reconnect/retry.
    async with server:
        assert server.token_path.read_text() == token
    await server.aclose()


async def test_two_independent_clients_are_actually_concurrent(state_directory):
    started = asyncio.Event()
    release = asyncio.Event()
    count = 0

    async def rendezvous(params):
        nonlocal count
        count += 1
        if count == 2:
            started.set()
        await release.wait()
        return params["client"]

    async with LocalRpcServer(state_directory, {"wait": rendezvous}):
        async with LocalRpcClient(state_directory) as one, LocalRpcClient(state_directory) as two:
            tasks = [
                asyncio.create_task(one.call("wait", {"client": 1})),
                asyncio.create_task(two.call("wait", {"client": 2})),
            ]
            try:
                await asyncio.wait_for(started.wait(), 1)
            finally:
                release.set()
            assert await asyncio.gather(*tasks) == [1, 2]


@pytest.mark.parametrize("auth", [None, "", "bad", "f" * 64, "ñ" * 64, "\ud800" * 64])
async def test_health_requires_authentication_and_reveals_no_catalog(state_directory, auth):
    async with LocalRpcServer(state_directory, {"private.method": echo}) as server:
        response = await raw_call(server, envelope(server, auth=auth))
        assert response["error"]["code"] == -32001
        assert "private.method" not in json.dumps(response)
        assert "result" not in response


@pytest.mark.parametrize("request_id", [None, True, False, 1.1, [], {}, "", 2**53, "x" * 129])
async def test_invalid_request_ids_fail_closed(state_directory, request_id):
    async with LocalRpcServer(state_directory, {}) as server:
        response = await raw_call(server, envelope(server, id=request_id))
        assert response["id"] is None
        assert response["error"]["code"] == -32600


@pytest.mark.parametrize("request_id", [0, -1, 2**53 - 1, "native-1"])
async def test_valid_ids_roundtrip(state_directory, request_id):
    async with LocalRpcServer(state_directory, {}) as server:
        response = await raw_call(server, envelope(server, id=request_id))
        assert response["id"] == request_id
        assert "result" in response


@pytest.mark.parametrize(
    "changes, code",
    [
        ({"jsonrpc": "1.0"}, -32600),
        ({"extra": 1}, -32600),
        ({"method": "__import__"}, -32600),
        ({"method": "unregistered"}, -32601),
        ({"params": []}, -32602),
    ],
)
async def test_only_catalog_methods_and_object_params(state_directory, changes, code):
    async with LocalRpcServer(state_directory, {}) as server:
        assert (await raw_call(server, envelope(server, **changes)))["error"]["code"] == code


@pytest.mark.parametrize(
    "frame", [b"{\n", b"[]\n", b'{"id":1,"id":2}\n', b'{"n":NaN}\n', b"\xff\n", b"[" * 1100 + b"\n"]
)
async def test_malformed_json_is_controlled_and_connection_remains_usable(state_directory, frame):
    async with LocalRpcServer(state_directory, {}) as server:
        reader, writer = await asyncio.open_unix_connection(server.socket_path)
        try:
            writer.write(frame)
            await writer.drain()
            response = json.loads(await reader.readline())
            assert response["error"]["code"] in {-32700, -32600}
            writer.write(json.dumps(envelope(server)).encode() + b"\n")
            await writer.drain()
            assert "result" in json.loads(await reader.readline())
        finally:
            writer.close()
            await writer.wait_closed()


async def test_oversized_frame_closes_connection_without_dispatch(state_directory):
    calls = []

    async def handler(params):
        calls.append(params)

    async with LocalRpcServer(state_directory, {"echo": handler}, max_frame_bytes=512) as server:
        reader, writer = await asyncio.open_unix_connection(server.socket_path)
        try:
            writer.write(b"x" * 513 + b"\n")
            await writer.drain()
            assert json.loads(await reader.readline())["error"]["code"] == -32004
            assert await asyncio.wait_for(reader.read(), 1) == b""
            assert not calls
        finally:
            writer.close()
            await writer.wait_closed()


async def test_output_budget_and_safe_internal_errors(state_directory):
    async def large(_):
        return "x" * 1000

    async def fail(_):
        raise RuntimeError("PRIVATE-TEST-SENTINEL")

    async def invalid_params(_):
        raise RpcError(-32602, "Falta el campo sesión.")

    async with LocalRpcServer(
        state_directory,
        {"large": large, "fail": fail, "invalid": invalid_params},
        max_frame_bytes=512,
    ):
        async with LocalRpcClient(state_directory, max_frame_bytes=512) as client:
            for method, code in [("large", -32004), ("fail", -32603), ("invalid", -32602)]:
                with pytest.raises(RpcError) as error:
                    await client.call(method)
                assert error.value.code == code
                assert "PRIVATE-TEST-SENTINEL" not in str(error.value)
            assert await client.call("health")


async def test_idle_and_incomplete_disconnect_have_no_resource_leak(state_directory):
    async with LocalRpcServer(state_directory, {}, idle_timeout=0.03) as server:
        reader, writer = await asyncio.open_unix_connection(server.socket_path)
        assert await asyncio.wait_for(reader.read(), 1) == b""
        writer.close()
        await writer.wait_closed()
        reader, writer = await asyncio.open_unix_connection(server.socket_path)
        writer.write(b'{"partial":')
        await writer.drain()
        writer.close()
        await writer.wait_closed()
        async with LocalRpcClient(state_directory) as client:
            assert await client.call("health")
    assert server.active_connections == 0


async def test_handler_timeout_cancels_and_client_can_continue(state_directory):
    cancelled = asyncio.Event()

    async def wait(_):
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.set()

    async with LocalRpcServer(state_directory, {"wait": wait}, request_timeout=0.03):
        async with LocalRpcClient(state_directory) as client:
            with pytest.raises(RpcError) as error:
                await client.call("wait")
            assert error.value.code == -32002
            assert cancelled.is_set()
            assert await client.call("health")


async def test_client_timeout_closes_and_does_not_retry_mutation(state_directory):
    started = asyncio.Event()
    cleanup = asyncio.Event()
    calls = 0

    async def wait(_):
        nonlocal calls
        calls += 1
        started.set()
        try:
            await asyncio.Event().wait()
        finally:
            cleanup.set()

    server = LocalRpcServer(state_directory, {"wait": wait})
    async with server:
        client = await LocalRpcClient(state_directory, timeout=0.03).connect()
        with pytest.raises(TimeoutError):
            await client.call("wait")
        assert started.is_set()
        assert calls == 1
        with pytest.raises(RuntimeError, match="no está conectado"):
            await client.call("health")
    assert cleanup.is_set()
    assert server.active_connections == 0


async def test_shutdown_cancels_active_handler_before_unlink(state_directory):
    started, cleaned = asyncio.Event(), asyncio.Event()

    async def wait(_):
        started.set()
        try:
            await asyncio.Event().wait()
        finally:
            await asyncio.sleep(0)
            cleaned.set()

    server = await LocalRpcServer(state_directory, {"wait": wait}).start()
    async with LocalRpcClient(state_directory) as client:
        task = asyncio.create_task(client.call("wait"))
        await asyncio.wait_for(started.wait(), 1)
        await server.aclose()
        assert cleaned.is_set()
        assert not server.socket_path.exists()
        with pytest.raises((ConnectionError, asyncio.IncompleteReadError)):
            await task
    assert server.active_connections == 0


async def test_existing_socket_is_never_replaced(state_directory):
    async with LocalRpcServer(state_directory, {}) as original:
        identity = original.socket_path.stat().st_ino
        with pytest.raises(TransportSecurityError, match="no se sustituirá"):
            await LocalRpcServer(state_directory, {}).start()
        assert original.socket_path.stat().st_ino == identity
        async with LocalRpcClient(state_directory) as client:
            assert await client.call("health")


async def test_shutdown_does_not_remove_replacement_file(state_directory):
    server = await LocalRpcServer(state_directory, {}).start()
    server.socket_path.unlink()
    server.socket_path.write_text("replacement")
    await server.aclose()
    assert server.socket_path.read_text() == "replacement"


@pytest.mark.parametrize("mode", [0o755, 0o770, 0o777])
async def test_insecure_directory_rejected_without_permission_changes(state_directory, mode):
    state_directory.chmod(mode)
    with pytest.raises(TransportSecurityError, match="0700"):
        await LocalRpcServer(state_directory, {}).start()
    assert stat.S_IMODE(state_directory.stat().st_mode) == mode


async def test_symlink_state_directory_and_ancestor_rejected(state_directory):
    real = state_directory / "real"
    real.mkdir(mode=0o700)
    link = state_directory / "linked"
    link.symlink_to(real, target_is_directory=True)
    for directory in [link, link / "child"]:
        with pytest.raises(TransportSecurityError, match="enlace"):
            await LocalRpcServer(directory, {}).start()
    assert not (real / "child").exists()


async def test_token_symlink_and_hardlink_rejected(state_directory):
    target = state_directory / "sentinel"
    target.write_text("a" * 64)
    target.chmod(0o600)
    token = state_directory / "transport.token"
    token.symlink_to(target)
    with pytest.raises(TransportSecurityError):
        await LocalRpcServer(state_directory, {}).start()
    assert target.read_text() == "a" * 64
    token.unlink()
    os.link(target, token)
    with pytest.raises(TransportSecurityError):
        await LocalRpcServer(state_directory, {}).start()


@pytest.mark.parametrize(
    "contents, mode", [("a" * 64, 0o644), ("a" * 63, 0o600), ("z" * 64, 0o600)]
)
async def test_insecure_or_invalid_token_is_never_overwritten(state_directory, contents, mode):
    token = state_directory / "transport.token"
    token.write_text(contents)
    token.chmod(mode)
    with pytest.raises(TransportSecurityError):
        await LocalRpcServer(state_directory, {}).start()
    assert token.read_text() == contents
    assert stat.S_IMODE(token.stat().st_mode) == mode


async def test_token_revocation_applies_to_connected_client(state_directory):
    async with LocalRpcServer(state_directory, {}) as server:
        async with LocalRpcClient(state_directory) as old:
            assert await old.call("health")
            server.token_path.write_text("b" * 64)
            with pytest.raises(RpcError) as error:
                await old.call("health")
            assert error.value.code == -32001
        async with LocalRpcClient(state_directory) as new:
            assert await new.call("health")
        server.token_path.unlink()
        assert (
            await raw_call(
                server, {"jsonrpc": "2.0", "id": 1, "auth": "b" * 64, "method": "health"}
            )
        )["error"]["code"] == -32001


async def test_client_refuses_socket_symlink(state_directory):
    async with LocalRpcServer(state_directory, {}) as server:
        renamed = state_directory / "renamed.sock"
        server.socket_path.rename(renamed)
        server.socket_path.symlink_to(renamed)
        with pytest.raises(TransportSecurityError):
            await LocalRpcClient(state_directory).connect()
    assert server.socket_path.is_symlink()


async def test_client_calls_share_a_connection_without_interleaved_responses(state_directory):
    async with LocalRpcServer(state_directory, {"echo": echo}):
        async with LocalRpcClient(state_directory) as client:
            results = await asyncio.gather(*(client.call("echo", {"i": n}) for n in range(20)))
            assert results == [{"i": n} for n in range(20)]


async def test_connection_budget_rejects_extra_connections(state_directory):
    async with LocalRpcServer(state_directory, {}, max_connections=1) as server:
        async with LocalRpcClient(state_directory) as first:
            assert await first.call("health")
            reader, writer = await asyncio.open_unix_connection(server.socket_path)
            try:
                assert await asyncio.wait_for(reader.read(), 1) == b""
                assert server.active_connections == 1
            finally:
                writer.close()
                await writer.wait_closed()


def test_constructor_rejects_unsafe_catalog_and_budgets(state_directory):
    for handlers in [{"health": echo}, {"import(os)": echo}, {"echo": None}]:
        with pytest.raises(ValueError):
            LocalRpcServer(state_directory, handlers)
    for kwargs in [
        {"max_frame_bytes": True},
        {"max_frame_bytes": 10},
        {"idle_timeout": 0},
        {"request_timeout": float("nan")},
        {"shutdown_timeout": -1},
        {"max_connections": 0},
    ]:
        with pytest.raises(ValueError):
            LocalRpcServer(state_directory, {}, **kwargs)


async def test_explicit_absolute_directory_only_and_private_directory_creation(state_directory):
    with pytest.raises(TransportSecurityError, match="absoluta"):
        await LocalRpcServer(Path("relative"), {}).start()
    child = state_directory / "created"
    async with LocalRpcServer(child, {}):
        assert stat.S_IMODE(child.stat().st_mode) == 0o700


async def test_local_oversize_request_is_rejected_before_dispatch(state_directory):
    async with LocalRpcServer(state_directory, {"echo": echo}, max_frame_bytes=512):
        async with LocalRpcClient(state_directory, max_frame_bytes=512) as client:
            with pytest.raises(RpcError) as error:
                await client.call("echo", {"text": "x" * 513})
            assert error.value.code == -32004
            assert await client.call("health")


async def test_cancelled_client_does_not_leave_reusable_connection(state_directory):
    started = asyncio.Event()

    async def wait(_):
        started.set()
        await asyncio.Event().wait()

    async with LocalRpcServer(state_directory, {"wait": wait}):
        async with LocalRpcClient(state_directory) as client:
            task = asyncio.create_task(client.call("wait"))
            await asyncio.wait_for(started.wait(), 1)
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
            with pytest.raises(RuntimeError, match="no está conectado"):
                await client.call("health")
