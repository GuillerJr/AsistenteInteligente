import asyncio
import os
import socket
import stat
import tempfile
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from aegis_core.contracts import AgentResult, AgentRole
from aegis_core.ipc.client import IpcClient
from aegis_core.ipc.protocol import IpcAuthenticator, IpcRequest, ProtocolError
from aegis_core.ipc.server import (
    AegisDaemon,
    DaemonSecurityError,
    IpcHandlerResult,
    peer_uid,
)
from aegis_core.jobs import JobStatus, SwarmIpcService, SwarmJobManager
from aegis_core.memory import (
    ConversationCoordinator,
    ConversationIpcService,
    MemoryIpcService,
    SQLiteMemoryStore,
)

AUTHENTICATOR = IpcAuthenticator(bytes.fromhex("33" * 32))


class ImmediateGraph:
    async def ainvoke(self, input: dict[str, object]) -> dict[str, object]:
        del input
        return {
            "final_result": AgentResult(
                role=AgentRole.SYNTHESIZER,
                model_id="fake/synthesizer",
                content="respuesta:hola",
            )
        }


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


@pytest.mark.asyncio
async def test_daemon_submits_and_reports_swarm_job(ipc_root: Path) -> None:
    socket_path = ipc_root / "aegis.sock"
    jobs = SwarmJobManager(ImmediateGraph())
    service = SwarmIpcService(jobs)
    client = IpcClient(socket_path, AUTHENTICATOR)

    try:
        async with AegisDaemon(
            socket_path,
            AUTHENTICATOR,
            handlers=service.handlers(),
        ):
            submitted = await client.call("swarm.submit", {"text": "hola"})
            assert submitted.ok is True
            job_id = submitted.payload["job_id"]
            for _ in range(20):
                status = await client.call("jobs.status", {"job_id": job_id})
                if status.payload["status"] == JobStatus.COMPLETED:
                    break
                await asyncio.sleep(0)
            else:
                raise AssertionError("IPC job did not complete")
    finally:
        await jobs.close()

    assert status.payload["result"] == "respuesta:hola"


@pytest.mark.asyncio
async def test_daemon_submits_final_local_transcript_as_voice_job(ipc_root: Path) -> None:
    socket_path = ipc_root / "aegis.sock"
    jobs = SwarmJobManager(ImmediateGraph())
    service = SwarmIpcService(jobs)
    client = IpcClient(socket_path, AUTHENTICATOR)

    try:
        async with AegisDaemon(
            socket_path,
            AUTHENTICATOR,
            handlers=service.handlers(),
        ):
            submitted = await client.call(
                "voice.submit",
                {
                    "transcript": {
                        "capture_id": "01234567-89ab-cdef-0123-456789abcdef",
                        "sequence": 1,
                        "text": "revisa el sistema",
                        "locale_identifier": "es-US",
                        "duration_milliseconds": 900,
                        "is_final": True,
                        "on_device": True,
                    }
                },
            )
            assert submitted.ok is True
            job_id = submitted.payload["job_id"]
            for _ in range(20):
                status = await client.call("jobs.status", {"job_id": job_id})
                if status.payload["status"] == JobStatus.COMPLETED:
                    break
                await asyncio.sleep(0)
            else:
                raise AssertionError("voice job did not complete")
    finally:
        await jobs.close()

    assert status.payload["result"] == "respuesta:hola"


@pytest.mark.asyncio
async def test_daemon_hides_custom_handler_exceptions(ipc_root: Path) -> None:
    socket_path = ipc_root / "aegis.sock"

    async def failing_handler(request: IpcRequest) -> IpcHandlerResult:
        del request
        raise RuntimeError("sensitive internal failure")

    async with AegisDaemon(
        socket_path,
        AUTHENTICATOR,
        handlers={"swarm.submit": failing_handler},
    ):
        response = await IpcClient(socket_path, AUTHENTICATOR).call(
            "swarm.submit", {"text": "hola"}
        )

    assert response.ok is False
    assert response.error_code == "handler_failed"
    assert "sensitive" not in response.model_dump_json()


@pytest.mark.asyncio
async def test_daemon_persists_and_searches_memory_over_authenticated_ipc(
    ipc_root: Path,
) -> None:
    socket_path = ipc_root / "aegis.sock"
    store = SQLiteMemoryStore(ipc_root / "memory.sqlite3")
    store.initialize()
    service = MemoryIpcService(store)
    client = IpcClient(socket_path, AUTHENTICATOR)

    async with AegisDaemon(
        socket_path,
        AUTHENTICATOR,
        handlers=service.handlers(),
    ):
        stored = await client.call(
            "memory.put",
            {
                "namespace": "user.default",
                "kind": "preference",
                "content": "El HUD debe permanecer oculto por defecto.",
            },
        )
        found = await client.call(
            "memory.search",
            {"namespace": "user.default", "query": "HUD oculto"},
        )

    assert stored.ok is True
    assert found.ok is True
    assert found.payload["hits"][0]["memory_id"] == stored.payload["memory_id"]


@pytest.mark.asyncio
async def test_daemon_runs_persistent_conversation_over_authenticated_ipc(
    ipc_root: Path,
) -> None:
    socket_path = ipc_root / "aegis.sock"
    store = SQLiteMemoryStore(ipc_root / "memory.sqlite3")
    store.initialize()
    conversations = ConversationCoordinator(store, namespace="user.default")
    conversation_service = ConversationIpcService(store, conversations)
    jobs = SwarmJobManager(ImmediateGraph(), conversations=conversations)
    swarm_service = SwarmIpcService(jobs)
    client = IpcClient(socket_path, AUTHENTICATOR)

    try:
        async with AegisDaemon(
            socket_path,
            AUTHENTICATOR,
            handlers={
                **conversation_service.handlers(),
                **swarm_service.handlers(),
            },
        ):
            created = await client.call(
                "conversations.create",
                {"title": "IPC persistente"},
            )
            submitted = await client.call(
                "swarm.submit",
                {
                    "text": "hola",
                    "conversation_id": created.payload["conversation_id"],
                },
            )
            for _ in range(200):
                status = await client.call(
                    "jobs.status",
                    {"job_id": submitted.payload["job_id"]},
                )
                if status.payload["status"] == JobStatus.COMPLETED:
                    break
                await asyncio.sleep(0.001)
            else:
                raise AssertionError("conversation job did not complete")
            history = await client.call(
                "conversations.history",
                {"conversation_id": created.payload["conversation_id"]},
            )
    finally:
        await jobs.close()

    assert status.payload["conversation_persisted"] is True
    assert [turn["content"] for turn in history.payload["turns"]] == [
        "hola",
        "respuesta:hola",
    ]
