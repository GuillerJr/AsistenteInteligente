import os
import socket
import stat
import tempfile
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from aegis_core.ipc.client import IpcClient
from aegis_core.ipc.protocol import IpcAuthenticator, ProtocolError
from aegis_core.ipc.server import AegisDaemon, DaemonSecurityError, peer_uid

AUTHENTICATOR = IpcAuthenticator(bytes.fromhex("33" * 32))


@pytest.fixture
def ipc_root() -> Iterator[Path]:
    with tempfile.TemporaryDirectory(prefix="ag-", dir="/private/tmp") as value:
        root = Path(value)
        root.chmod(0o700)
        yield root


def test_peer_uid_reads_local_socket_credentials() -> None:
    first, second = socket.socketpair()
    try:
        assert peer_uid(first) == os.getuid()
        assert peer_uid(second) == os.getuid()
    finally:
        first.close()
        second.close()


@pytest.mark.asyncio
async def test_daemon_health_is_authenticated_and_socket_is_private(ipc_root: Path) -> None:
    socket_path = ipc_root / "aegis.sock"
    daemon = AegisDaemon(socket_path, AUTHENTICATOR)

    async with daemon:
        response = await IpcClient(socket_path, AUTHENTICATOR).call("health")
        mode = stat.S_IMODE(socket_path.lstat().st_mode)

        assert response.ok is True
        assert response.payload["status"] == "ok"
        assert response.payload["protocol_version"] == "1.0"
        assert mode == 0o600

    assert socket_path.exists() is False


@pytest.mark.asyncio
async def test_daemon_rejects_wrong_hmac_secret(ipc_root: Path) -> None:
    socket_path = ipc_root / "aegis.sock"
    wrong = IpcAuthenticator(bytes.fromhex("44" * 32))

    async with AegisDaemon(socket_path, AUTHENTICATOR):
        with pytest.raises((ProtocolError, ConnectionError)):
            await IpcClient(socket_path, wrong).call("health")


@pytest.mark.asyncio
async def test_daemon_rejects_nonce_replay(ipc_root: Path) -> None:
    socket_path = ipc_root / "aegis.sock"
    nonce = "5" * 32
    client = IpcClient(socket_path, AUTHENTICATOR)

    async with AegisDaemon(socket_path, AUTHENTICATOR):
        first = await client.call("health", nonce=nonce)
        replay = await client.call("health", nonce=nonce)

    assert first.ok is True
    assert replay.ok is False
    assert replay.error_code == "request_replayed"


@pytest.mark.asyncio
async def test_daemon_returns_signed_error_for_stale_request(ipc_root: Path) -> None:
    socket_path = ipc_root / "aegis.sock"
    stale_time = datetime.now(UTC) - timedelta(minutes=1)

    async with AegisDaemon(socket_path, AUTHENTICATOR):
        response = await IpcClient(socket_path, AUTHENTICATOR).call("health", now=stale_time)

    assert response.ok is False
    assert response.error_code == "request_stale"


@pytest.mark.asyncio
async def test_daemon_rejects_unknown_method_without_dispatch(ipc_root: Path) -> None:
    socket_path = ipc_root / "aegis.sock"

    async with AegisDaemon(socket_path, AUTHENTICATOR):
        response = await IpcClient(socket_path, AUTHENTICATOR).call("terminal.execute")

    assert response.ok is False
    assert response.error_code == "method_not_found"


@pytest.mark.asyncio
async def test_daemon_exposes_bounded_runtime_metadata(ipc_root: Path) -> None:
    socket_path = ipc_root / "aegis.sock"

    async with AegisDaemon(socket_path, AUTHENTICATOR):
        response = await IpcClient(socket_path, AUTHENTICATOR).call("runtime.info")

    assert response.ok is True
    assert set(response.payload) == {
        "architecture",
        "operating_system",
        "os_release",
        "python",
    }


@pytest.mark.asyncio
async def test_client_rejects_request_larger_than_frame_limit(ipc_root: Path) -> None:
    socket_path = ipc_root / "aegis.sock"

    async with AegisDaemon(socket_path, AUTHENTICATOR, max_frame_bytes=4_096):
        with pytest.raises(ProtocolError, match="frame limit"):
            await IpcClient(
                socket_path,
                AUTHENTICATOR,
                max_frame_bytes=4_096,
            ).call("health", {"padding": "x" * 5_000})


@pytest.mark.asyncio
async def test_client_rejects_socket_with_broad_permissions(ipc_root: Path) -> None:
    socket_path = ipc_root / "aegis.sock"

    async with AegisDaemon(socket_path, AUTHENTICATOR):
        socket_path.chmod(0o666)
        with pytest.raises(ProtocolError, match="permissions"):
            await IpcClient(socket_path, AUTHENTICATOR).call("health")


@pytest.mark.asyncio
async def test_daemon_rejects_peer_uid_mismatch(ipc_root: Path) -> None:
    socket_path = ipc_root / "aegis.sock"
    daemon = AegisDaemon(
        socket_path,
        AUTHENTICATOR,
        peer_uid_resolver=lambda _: os.getuid() + 1,
    )

    async with daemon:
        with pytest.raises((ProtocolError, ConnectionError)):
            await IpcClient(socket_path, AUTHENTICATOR).call("health")


@pytest.mark.asyncio
async def test_daemon_refuses_broad_directory_permissions(ipc_root: Path) -> None:
    directory = ipc_root / "public"
    directory.mkdir(mode=0o755)
    directory.chmod(0o755)
    daemon = AegisDaemon(directory / "aegis.sock", AUTHENTICATOR)

    with pytest.raises(DaemonSecurityError, match="owner-only"):
        await daemon.start()


@pytest.mark.asyncio
async def test_second_daemon_cannot_replace_active_socket(ipc_root: Path) -> None:
    socket_path = ipc_root / "aegis.sock"

    async with AegisDaemon(socket_path, AUTHENTICATOR):
        with pytest.raises(DaemonSecurityError, match="already listening"):
            await AegisDaemon(socket_path, AUTHENTICATOR).start()


@pytest.mark.asyncio
async def test_daemon_replaces_only_owned_stale_socket(ipc_root: Path) -> None:
    socket_path = ipc_root / "aegis.sock"
    stale = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    stale.bind(str(socket_path))
    stale.close()

    async with AegisDaemon(socket_path, AUTHENTICATOR):
        response = await IpcClient(socket_path, AUTHENTICATOR).call("health")

    assert response.ok is True
