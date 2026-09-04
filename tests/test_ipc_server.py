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
from aegis_core.ipc.framing import MAX_ACCUMULATED_MESSAGE_BYTES, encode_stream
from aegis_core.ipc.protocol import IpcAuthenticator, IpcRequest, ProtocolError
from aegis_core.ipc.server import (
    AegisDaemon,
    DaemonSecurityError,
    IpcHandlerResult,
    peer_uid,
)
from aegis_core.job_ipc import SwarmIpcService
from aegis_core.jobs import JobStatus, SwarmJobManager
from aegis_core.memory import (
    ConversationCoordinator,
    ConversationIpcService,
    MemoryIpcService,
    SQLiteMemoryStore,
)
from aegis_core.tools.audit import HashChainAuditLog

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


class BlockingGraph:
    def __init__(self) -> None:
        self.release = asyncio.Event()

    async def ainvoke(self, input: dict[str, object]) -> dict[str, object]:
        del input
        await self.release.wait()
        return {
            "final_result": AgentResult(
                role=AgentRole.SYNTHESIZER,
                model_id="fake/synthesizer",
                content="respuesta:carga",
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
        assert response.payload["build_revision"] == "development"
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
async def test_daemon_exposes_bounded_runtime_metrics(ipc_root: Path) -> None:
    socket_path = ipc_root / "aegis.sock"

    async with AegisDaemon(socket_path, AUTHENTICATOR):
        response = await IpcClient(socket_path, AUTHENTICATOR).call("runtime.metrics")

    assert response.ok is True
    assert set(response.payload) == {
        "uptime_seconds",
        "cpu_seconds",
        "peak_rss_bytes",
        "runtime_state",
    }
    assert response.payload["runtime_state"] == "active"
    assert response.payload["uptime_seconds"] >= 0
    assert response.payload["cpu_seconds"] >= 0
    assert response.payload["peak_rss_bytes"] > 0


@pytest.mark.asyncio
async def test_daemon_multiplexes_large_authenticated_request_and_response(
    ipc_root: Path,
) -> None:
    socket_path = ipc_root / "aegis.sock"
    padding = "x" * 90_000

    async def echo_handler(request: IpcRequest) -> IpcHandlerResult:
        assert request.payload == {"padding": padding}
        return IpcHandlerResult(ok=True, payload={"padding": padding})

    async with AegisDaemon(
        socket_path,
        AUTHENTICATOR,
        handlers={"swarm.submit": echo_handler},
    ):
        response = await IpcClient(socket_path, AUTHENTICATOR).call(
            "swarm.submit",
            {"padding": padding},
        )

    assert response.ok is True
    assert response.payload == {"padding": padding}


@pytest.mark.asyncio
async def test_daemon_aborts_oversized_stream_and_audits_critical_alert(
    ipc_root: Path,
) -> None:
    socket_path = ipc_root / "aegis.sock"
    audit = HashChainAuditLog(ipc_root / "audit.jsonl")
    frames = encode_stream(
        b"x" * (MAX_ACCUMULATED_MESSAGE_BYTES + 1),
        AUTHENTICATOR,
    )

    async with AegisDaemon(socket_path, AUTHENTICATOR, audit_sink=audit):
        reader, writer = await asyncio.open_unix_connection(socket_path)
        try:
            for frame in frames:
                writer.write(frame)
                await writer.drain()
            assert await asyncio.wait_for(reader.read(), timeout=1) == b""
        finally:
            writer.close()
            try:
                await writer.wait_closed()
            except (ConnectionError, OSError):
                pass

    records = audit.verify()
    assert records[-1].event_type == "ipc_protocol_violation"
    assert records[-1].data == {
        "severity": "critical",
        "reason": "accumulated payload size exceeds safety ceiling",
        "suppressed_since_last": 0,
    }


@pytest.mark.asyncio
async def test_daemon_runs_response_hook_only_after_successful_response(
    ipc_root: Path,
) -> None:
    socket_path = ipc_root / "aegis.sock"
    response_sent = asyncio.Event()

    async def preflight(request: IpcRequest) -> IpcHandlerResult:
        del request
        return IpcHandlerResult(ok=True, payload={"status": "ok"})

    async with AegisDaemon(
        socket_path,
        AUTHENTICATOR,
        handlers={"runtime.preflight": preflight},
        response_sent_hooks={"runtime.preflight": response_sent.set},
    ):
        response = await IpcClient(socket_path, AUTHENTICATOR).call("runtime.preflight")

    assert response.ok is True
    assert response.payload == {"status": "ok"}
    assert response_sent.is_set()


@pytest.mark.asyncio
async def test_client_rejects_request_larger_than_message_limit(ipc_root: Path) -> None:
    socket_path = ipc_root / "aegis.sock"

    async with AegisDaemon(
        socket_path,
        AUTHENTICATOR,
        max_frame_bytes=4_096,
        max_message_bytes=4_096,
    ):
        with pytest.raises(ProtocolError, match="message limit"):
            await IpcClient(
                socket_path,
                AUTHENTICATOR,
                max_frame_bytes=4_096,
                max_message_bytes=4_096,
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
async def test_daemon_job_wait_wakes_when_blocked_job_completes(ipc_root: Path) -> None:
    socket_path = ipc_root / "aegis.sock"
    graph = BlockingGraph()
    jobs = SwarmJobManager(graph)
    service = SwarmIpcService(jobs)
    client = IpcClient(socket_path, AUTHENTICATOR)

    try:
        async with AegisDaemon(
            socket_path,
            AUTHENTICATOR,
            handlers=service.handlers(),
            handler_timeout_overrides={service.WAIT_METHOD: service.MAX_WAIT_SECONDS + 2},
        ):
            submitted = await client.call("swarm.submit", {"text": "espera"})
            waiting = asyncio.create_task(
                client.call(
                    "jobs.wait",
                    {
                        "job_id": submitted.payload["job_id"],
                        "after_stream_version": 0,
                        "timeout_milliseconds": 20_000,
                    },
                )
            )
            await asyncio.sleep(0.01)
            assert waiting.done() is False
            graph.release.set()
            response = await asyncio.wait_for(waiting, timeout=1)
    finally:
        graph.release.set()
        await jobs.close()

    assert response.ok is True
    assert response.payload["status"] == JobStatus.COMPLETED


@pytest.mark.asyncio
async def test_daemon_job_burst_fails_closed_at_exact_capacity(ipc_root: Path) -> None:
    socket_path = ipc_root / "aegis.sock"
    graph = BlockingGraph()
    jobs = SwarmJobManager(graph, max_jobs=8)
    service = SwarmIpcService(jobs)
    client = IpcClient(socket_path, AUTHENTICATOR)

    try:
        async with AegisDaemon(
            socket_path,
            AUTHENTICATOR,
            max_clients=32,
            handlers=service.handlers(),
        ):
            responses = await asyncio.gather(
                *(
                    client.call("swarm.submit", {"text": f"carga-{index}"})
                    for index in range(24)
                )
            )
            accepted = [response for response in responses if response.ok]
            rejected = [response for response in responses if not response.ok]

            assert len(accepted) == 8
            assert len({response.payload["job_id"] for response in accepted}) == 8
            assert len(rejected) == 16
            assert {response.error_code for response in rejected} == {
                "job_capacity_reached"
            }

            graph.release.set()

            async def wait_for_completion(job_id: str) -> str:
                for _ in range(100):
                    status = await client.call("jobs.status", {"job_id": job_id})
                    if status.payload["status"] == JobStatus.COMPLETED:
                        return str(status.payload["result"])
                    await asyncio.sleep(0.001)
                raise AssertionError("accepted burst job did not complete")

            results = await asyncio.gather(
                *(wait_for_completion(response.payload["job_id"]) for response in accepted)
            )
    finally:
        graph.release.set()
        await jobs.close()

    assert results == ["respuesta:carga"] * 8


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
async def test_daemon_cancels_timed_out_handler_and_releases_capacity(
    ipc_root: Path,
) -> None:
    socket_path = ipc_root / "aegis.sock"
    cancelled = asyncio.Event()

    async def hanging_handler(request: IpcRequest) -> IpcHandlerResult:
        del request
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.set()

    async with AegisDaemon(
        socket_path,
        AUTHENTICATOR,
        max_clients=1,
        handler_timeout_seconds=0.01,
        handlers={"swarm.submit": hanging_handler},
    ):
        timed_out = await IpcClient(socket_path, AUTHENTICATOR).call("swarm.submit")
        healthy = await IpcClient(socket_path, AUTHENTICATOR).call("health")

    assert timed_out.ok is False
    assert timed_out.error_code == "handler_timeout"
    assert cancelled.is_set()
    assert healthy.ok is True


def test_daemon_rejects_non_positive_handler_timeout(ipc_root: Path) -> None:
    with pytest.raises(ValueError, match="timeout must be positive"):
        AegisDaemon(
            ipc_root / "aegis.sock",
            AUTHENTICATOR,
            handler_timeout_seconds=0,
        )


@pytest.mark.asyncio
async def test_daemon_applies_timeout_override_only_to_selected_handler(
    ipc_root: Path,
) -> None:
    socket_path = ipc_root / "aegis.sock"

    async def delayed_handler(request: IpcRequest) -> IpcHandlerResult:
        del request
        await asyncio.sleep(0.03)
        return IpcHandlerResult(ok=True, payload={"changed": False})

    async with AegisDaemon(
        socket_path,
        AUTHENTICATOR,
        handler_timeout_seconds=0.01,
        handlers={"swarm.wait": delayed_handler},
        handler_timeout_overrides={"swarm.wait": 0.1},
    ):
        response = await IpcClient(socket_path, AUTHENTICATOR).call("swarm.wait")

    assert response.ok is True
    assert response.payload == {"changed": False}


def test_daemon_rejects_invalid_handler_timeout_overrides(ipc_root: Path) -> None:
    async def handler(request: IpcRequest) -> IpcHandlerResult:
        del request
        return IpcHandlerResult(ok=True)

    with pytest.raises(ValueError, match="requires a custom handler"):
        AegisDaemon(
            ipc_root / "aegis.sock",
            AUTHENTICATOR,
            handlers={"swarm.wait": handler},
            handler_timeout_overrides={"missing.wait": 1},
        )
    with pytest.raises(ValueError, match="positive and finite"):
        AegisDaemon(
            ipc_root / "aegis.sock",
            AUTHENTICATOR,
            handlers={"swarm.wait": handler},
            handler_timeout_overrides={"swarm.wait": float("inf")},
        )


@pytest.mark.asyncio
async def test_daemon_rejects_connections_beyond_hard_client_cap(ipc_root: Path) -> None:
    socket_path = ipc_root / "aegis.sock"
    admitted = asyncio.Event()
    peer_checks = 0

    def resolve_peer(_: object) -> int:
        nonlocal peer_checks
        peer_checks += 1
        admitted.set()
        return os.getuid()

    async with AegisDaemon(
        socket_path,
        AUTHENTICATOR,
        max_clients=1,
        peer_uid_resolver=resolve_peer,
    ):
        slow_reader, slow_writer = await asyncio.open_unix_connection(socket_path)
        await asyncio.wait_for(admitted.wait(), timeout=1)

        with pytest.raises((ProtocolError, ConnectionError, OSError)):
            await IpcClient(socket_path, AUTHENTICATOR).call("health")
        assert peer_checks == 1

        slow_writer.write(b"\n")
        await slow_writer.drain()
        assert await slow_reader.read() == b""
        slow_writer.close()
        await slow_writer.wait_closed()

        healthy = await IpcClient(socket_path, AUTHENTICATOR).call("health")

    assert healthy.ok is True
    assert peer_checks == 2


def test_daemon_rejects_non_positive_client_capacity(ipc_root: Path) -> None:
    with pytest.raises(ValueError, match="max_clients must be positive"):
        AegisDaemon(ipc_root / "aegis.sock", AUTHENTICATOR, max_clients=0)


@pytest.mark.asyncio
@pytest.mark.parametrize("prefix", [b"", b"{"])
async def test_daemon_releases_incomplete_frame_after_read_timeout(
    ipc_root: Path,
    prefix: bytes,
) -> None:
    socket_path = ipc_root / "aegis.sock"

    async with AegisDaemon(
        socket_path,
        AUTHENTICATOR,
        max_clients=1,
        read_timeout_seconds=0.01,
    ):
        idle_reader, idle_writer = await asyncio.open_unix_connection(socket_path)
        if prefix:
            idle_writer.write(prefix)
            await idle_writer.drain()

        assert await asyncio.wait_for(idle_reader.read(), timeout=1) == b""
        healthy = await IpcClient(socket_path, AUTHENTICATOR).call("health")
        idle_writer.close()
        await idle_writer.wait_closed()

    assert healthy.ok is True


def test_daemon_rejects_non_positive_read_timeout(ipc_root: Path) -> None:
    with pytest.raises(ValueError, match="read timeout must be positive"):
        AegisDaemon(
            ipc_root / "aegis.sock",
            AUTHENTICATOR,
            read_timeout_seconds=0,
        )


@pytest.mark.asyncio
async def test_daemon_replaces_oversized_handler_response_with_signed_error(
    ipc_root: Path,
) -> None:
    socket_path = ipc_root / "aegis.sock"

    async def oversized_handler(request: IpcRequest) -> IpcHandlerResult:
        del request
        return IpcHandlerResult(ok=True, payload={"padding": "x" * 5_000})

    async with AegisDaemon(
        socket_path,
        AUTHENTICATOR,
        max_frame_bytes=4_096,
        max_message_bytes=4_096,
        handlers={"swarm.submit": oversized_handler},
    ):
        client = IpcClient(
            socket_path,
            AUTHENTICATOR,
            max_frame_bytes=4_096,
            max_message_bytes=4_096,
        )
        oversized = await client.call("swarm.submit")
        healthy = await client.call("health")

    assert oversized.ok is False
    assert oversized.error_code == "response_too_large"
    assert healthy.ok is True


@pytest.mark.asyncio
async def test_daemon_throttles_nonessential_waits_while_runtime_is_suspended(
    ipc_root: Path,
) -> None:
    socket_path = ipc_root / "aegis.sock"
    called = False

    async def wait_handler(request: IpcRequest) -> IpcHandlerResult:
        nonlocal called
        del request
        called = True
        return IpcHandlerResult(ok=True, payload={"changed": False})

    async with AegisDaemon(
        socket_path,
        AUTHENTICATOR,
        handlers={"swarm.wait": wait_handler},
        runtime_suspended=lambda: True,
    ):
        client = IpcClient(socket_path, AUTHENTICATOR)
        blocked = await client.call("swarm.wait")
        health = await client.call("health")

    assert blocked.ok is False
    assert blocked.error_code == "runtime_suspended"
    assert called is False
    assert health.payload["runtime_state"] == "suspended"


@pytest.mark.asyncio
async def test_daemon_bounds_response_write_time(ipc_root: Path) -> None:
    class BlockingWriter:
        def write(self, frame: bytes) -> None:
            assert frame == b"{}\n"

        async def drain(self) -> None:
            await asyncio.Event().wait()

    daemon = AegisDaemon(
        ipc_root / "aegis.sock",
        AUTHENTICATOR,
        write_timeout_seconds=0.01,
    )

    with pytest.raises(TimeoutError):
        await daemon._write_response(BlockingWriter(), b"{}")  # type: ignore[arg-type]


def test_daemon_rejects_non_positive_write_timeout(ipc_root: Path) -> None:
    with pytest.raises(ValueError, match="write timeout must be positive"):
        AegisDaemon(
            ipc_root / "aegis.sock",
            AUTHENTICATOR,
            write_timeout_seconds=0,
        )


@pytest.mark.asyncio
async def test_daemon_persists_and_searches_memory_over_authenticated_ipc(
    ipc_root: Path,
) -> None:
    socket_path = ipc_root / "aegis.sock"
    store = SQLiteMemoryStore(ipc_root / "memory.sqlite3", encryption_secret=b"m" * 32)
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
    store = SQLiteMemoryStore(ipc_root / "memory.sqlite3", encryption_secret=b"m" * 32)
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
