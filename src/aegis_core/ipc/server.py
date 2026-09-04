from __future__ import annotations

import asyncio
import errno
import math
import os
import platform
import resource
import socket
import stat
import struct
import time
from collections.abc import Awaitable, Callable, Mapping
from datetime import timedelta
from pathlib import Path
from typing import Any, Protocol
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from aegis_core.build_info import runtime_build_revision
from aegis_core.ipc.framing import DEFAULT_MAX_MESSAGE_BYTES, encode_frames, read_message
from aegis_core.ipc.protocol import (
    PROTOCOL_VERSION,
    FreshnessStatus,
    IpcAuthenticator,
    IpcRequest,
    NonceWindow,
    ProtocolError,
)


class DaemonSecurityError(RuntimeError):
    """Raised when the local daemon cannot establish a secure IPC boundary."""


class IpcHandlerResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    ok: bool
    payload: dict[str, Any] = Field(default_factory=dict)
    error_code: str | None = Field(default=None, pattern=r"^[a-z][a-z0-9_]{2,63}$")

    @model_validator(mode="after")
    def error_must_match_status(self) -> IpcHandlerResult:
        if self.ok and self.error_code is not None:
            raise ValueError("successful handler result cannot contain an error")
        if not self.ok and self.error_code is None:
            raise ValueError("failed handler result requires an error code")
        if not self.ok and self.payload:
            raise ValueError("failed handler result cannot contain a payload")
        return self


IpcMethodHandler = Callable[[IpcRequest], Awaitable[IpcHandlerResult]]
IpcResponseSentHook = Callable[[], None]


class IpcAuditSink(Protocol):
    def record_system_event(
        self,
        request_id: UUID,
        *,
        event_type: str,
        component: str,
        data: Mapping[str, str | int | bool | None],
        call_id: str | None = None,
    ) -> None: ...


def peer_uid(peer_socket: Any) -> int:
    if platform.system() == "Darwin":
        format_string = "@IIh2x16I"
        raw = peer_socket.getsockopt(
            0,
            socket.LOCAL_PEERCRED,
            struct.calcsize(format_string),
        )
        version, uid, _ = struct.unpack(format_string, raw)[:3]
        if version != 0:
            raise DaemonSecurityError("unsupported macOS peer credential version")
        return int(uid)
    if hasattr(socket, "SO_PEERCRED"):
        raw = peer_socket.getsockopt(
            socket.SOL_SOCKET,
            socket.SO_PEERCRED,
            struct.calcsize("3i"),
        )
        _, uid, _ = struct.unpack("3i", raw)
        return int(uid)
    raise DaemonSecurityError("peer credentials are unavailable on this platform")


class AegisDaemon:
    _SUSPENDED_METHODS = frozenset(
        {
            "computer.wait",
            "swarm.activity",
            "swarm.wait",
        }
    )

    def __init__(
        self,
        socket_path: Path,
        authenticator: IpcAuthenticator,
        *,
        max_frame_bytes: int = 65_536,
        max_message_bytes: int = DEFAULT_MAX_MESSAGE_BYTES,
        clock_skew_seconds: int = 30,
        max_clients: int = 16,
        read_timeout_seconds: float = 1.0,
        write_timeout_seconds: float = 1.0,
        handler_timeout_seconds: float = 4.0,
        expected_uid: int | None = None,
        peer_uid_resolver: Callable[[Any], int] = peer_uid,
        handlers: Mapping[str, IpcMethodHandler] | None = None,
        handler_timeout_overrides: Mapping[str, float] | None = None,
        response_sent_hooks: Mapping[str, IpcResponseSentHook] | None = None,
        security_compromised: Callable[[], bool] | None = None,
        runtime_suspended: Callable[[], bool] | None = None,
        audit_sink: IpcAuditSink | None = None,
    ) -> None:
        self._path = socket_path
        self._authenticator = authenticator
        if max_frame_bytes < 1:
            raise ValueError("IPC legacy frame limit must be positive")
        self._max_frame_bytes = max_frame_bytes
        if max_message_bytes < max_frame_bytes:
            raise ValueError("IPC message limit cannot be smaller than legacy frame limit")
        self._max_message_bytes = max_message_bytes
        if read_timeout_seconds <= 0:
            raise ValueError("IPC read timeout must be positive")
        self._read_timeout_seconds = read_timeout_seconds
        if write_timeout_seconds <= 0:
            raise ValueError("IPC write timeout must be positive")
        self._write_timeout_seconds = write_timeout_seconds
        if handler_timeout_seconds <= 0:
            raise ValueError("IPC handler timeout must be positive")
        self._handler_timeout_seconds = handler_timeout_seconds
        self._expected_uid = os.getuid() if expected_uid is None else expected_uid
        self._peer_uid_resolver = peer_uid_resolver
        self._handlers = dict(handlers or {})
        if {"health", "runtime.info", "runtime.metrics"} & self._handlers.keys():
            raise ValueError("custom handlers cannot replace built-in IPC methods")
        self._handler_timeout_overrides = dict(handler_timeout_overrides or {})
        self._response_sent_hooks = dict(response_sent_hooks or {})
        self._security_compromised = security_compromised or (lambda: False)
        self._runtime_suspended = runtime_suspended or (lambda: False)
        self._audit_sink = audit_sink
        if not self._handler_timeout_overrides.keys() <= self._handlers.keys():
            raise ValueError("IPC handler timeout override requires a custom handler")
        if not self._response_sent_hooks.keys() <= self._handlers.keys():
            raise ValueError("IPC response hook requires a custom handler")
        if any(
            not isinstance(timeout, (int, float))
            or isinstance(timeout, bool)
            or not math.isfinite(timeout)
            or timeout <= 0
            for timeout in self._handler_timeout_overrides.values()
        ):
            raise ValueError("IPC handler timeout override must be positive and finite")
        self._nonce_window = NonceWindow(clock_skew=timedelta(seconds=clock_skew_seconds))
        if max_clients < 1:
            raise ValueError("IPC max_clients must be positive")
        self._max_clients = max_clients
        self._active_clients = 0
        self._server: asyncio.AbstractServer | None = None
        self._socket_identity: tuple[int, int] | None = None
        self._started_at = time.monotonic()
        self._last_protocol_alert_at = 0.0
        self._suppressed_protocol_alerts = 0

    async def __aenter__(self) -> AegisDaemon:
        await self.start()
        return self

    async def __aexit__(self, *_: object) -> None:
        await self.close()

    async def start(self) -> None:
        if self._server is not None:
            raise RuntimeError("daemon is already started")
        self._prepare_private_directory()
        self._prepare_socket_path()
        self._server = await asyncio.start_unix_server(
            self._handle_client,
            path=self._path,
            limit=self._max_frame_bytes + 1,
        )
        os.chmod(self._path, 0o600)
        status = self._path.lstat()
        self._socket_identity = (status.st_dev, status.st_ino)
        if (
            not stat.S_ISSOCK(status.st_mode)
            or status.st_uid != self._expected_uid
            or stat.S_IMODE(status.st_mode) & 0o077
        ):
            await self.close()
            raise DaemonSecurityError("daemon socket ownership or permissions are invalid")

    async def serve_forever(self) -> None:
        if self._server is None:
            raise RuntimeError("daemon has not been started")
        await self._server.serve_forever()

    async def close(self) -> None:
        if self._server is not None:
            self._server.close()
            await self._server.wait_closed()
            self._server = None
        self._unlink_owned_socket()

    def _prepare_private_directory(self) -> None:
        if len(os.fsencode(self._path)) > 100:
            raise DaemonSecurityError("daemon socket path is too long")
        parent = self._path.parent
        if not parent.exists():
            parent.mkdir(parents=True, mode=0o700)
        status = parent.lstat()
        if (
            not stat.S_ISDIR(status.st_mode)
            or status.st_uid != self._expected_uid
            or stat.S_IMODE(status.st_mode) & 0o077
        ):
            raise DaemonSecurityError("daemon directory must be owner-only")

    def _prepare_socket_path(self) -> None:
        try:
            status = self._path.lstat()
        except FileNotFoundError:
            return
        if not stat.S_ISSOCK(status.st_mode) or status.st_uid != self._expected_uid:
            raise DaemonSecurityError("refusing to replace an unsafe socket path")

        probe = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            result = probe.connect_ex(str(self._path))
        finally:
            probe.close()
        if result == 0:
            raise DaemonSecurityError("another daemon is already listening")
        if result not in {errno.ECONNREFUSED, errno.ENOENT}:
            raise DaemonSecurityError("cannot verify stale daemon socket")
        self._path.unlink()

    async def _handle_client(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        if self._active_clients >= self._max_clients:
            writer.close()
            try:
                await writer.wait_closed()
            except (ConnectionError, OSError):
                pass
            return
        self._active_clients += 1
        connection_aborted = False
        try:
            peer_socket = writer.get_extra_info("socket")
            if (
                peer_socket is None
                or self._peer_uid_resolver(peer_socket) != self._expected_uid
            ):
                return
            raw_frame, transport_metrics = await asyncio.wait_for(
                read_message(
                    reader,
                    self._authenticator,
                    legacy_frame_bytes=self._max_frame_bytes,
                    max_message_bytes=self._max_message_bytes,
                ),
                timeout=self._read_timeout_seconds,
            )
            try:
                request = IpcRequest.model_validate_json(raw_frame)
            except (ValidationError, ValueError):
                return
            if not self._authenticator.verify_request(request):
                return
            if transport_metrics.framed and self._audit_sink is not None:
                self._audit_sink.record_system_event(
                    request.request_id,
                    event_type="ipc_stream_received",
                    component="ipc_transport",
                    data={
                        "frame_count": transport_metrics.frame_count,
                        "payload_bytes": transport_metrics.payload_bytes,
                    },
                )
            freshness = self._nonce_window.accept(request)
            if freshness is not FreshnessStatus.ACCEPTED:
                await self._send_error(writer, request, f"request_{freshness.value}")
                return
            await self._dispatch(writer, request)
        except ProtocolError as error:
            self._record_protocol_violation(error)
            writer.transport.abort()
            connection_aborted = True
            return
        except (
            TimeoutError,
            ConnectionError,
            OSError,
            ValueError,
            asyncio.IncompleteReadError,
        ):
            return
        finally:
            self._active_clients -= 1
            if not connection_aborted:
                writer.close()
                try:
                    await writer.wait_closed()
                except (ConnectionError, OSError):
                    pass

    async def _dispatch(self, writer: asyncio.StreamWriter, request: IpcRequest) -> None:
        if self._security_compromised() and request.method not in {
            "health",
            "runtime.info",
            "runtime.metrics",
            "security.status",
        }:
            await self._send_error(writer, request, "security_compromised")
            return
        if self._runtime_suspended() and request.method in self._SUSPENDED_METHODS:
            await self._send_error(writer, request, "runtime_suspended")
            return
        if request.method == "health":
            if request.payload:
                await self._send_error(writer, request, "invalid_payload")
                return
            payload = {
                "status": "ok",
                "protocol_version": PROTOCOL_VERSION,
                "architecture": platform.machine(),
                "build_revision": runtime_build_revision(),
                "pid": os.getpid(),
                "runtime_state": (
                    "suspended" if self._runtime_suspended() else "active"
                ),
            }
        elif request.method == "runtime.info":
            if request.payload:
                await self._send_error(writer, request, "invalid_payload")
                return
            payload = {
                "architecture": platform.machine(),
                "operating_system": platform.system(),
                "os_release": platform.release(),
                "python": platform.python_version(),
            }
        elif request.method == "runtime.metrics":
            if request.payload:
                await self._send_error(writer, request, "invalid_payload")
                return
            usage = resource.getrusage(resource.RUSAGE_SELF)
            peak_rss_bytes = int(usage.ru_maxrss)
            if platform.system() != "Darwin":
                peak_rss_bytes *= 1_024
            payload = {
                "uptime_seconds": round(time.monotonic() - self._started_at, 3),
                "cpu_seconds": round(usage.ru_utime + usage.ru_stime, 6),
                "peak_rss_bytes": peak_rss_bytes,
                "runtime_state": (
                    "suspended" if self._runtime_suspended() else "active"
                ),
            }
        else:
            handler = self._handlers.get(request.method)
            if handler is None:
                await self._send_error(writer, request, "method_not_found")
                return
            try:
                raw_result = await asyncio.wait_for(
                    handler(request),
                    timeout=self._handler_timeout_overrides.get(
                        request.method,
                        self._handler_timeout_seconds,
                    ),
                )
                result = IpcHandlerResult.model_validate(raw_result)
            except TimeoutError:
                await self._send_error(writer, request, "handler_timeout")
                return
            except Exception:
                await self._send_error(writer, request, "handler_failed")
                return
            if self._security_compromised():
                await self._send_error(writer, request, "security_compromised")
                return
            if result.ok:
                if result.error_code is not None:
                    await self._send_error(writer, request, "invalid_handler_result")
                    return
                payload = result.payload
            else:
                await self._send_error(
                    writer,
                    request,
                    result.error_code or "handler_failed",
                )
                return
        response = self._authenticator.create_response(request, ok=True, payload=payload)
        frame = response.model_dump_json().encode("utf-8")
        if len(frame) > self._max_message_bytes:
            await self._send_error(writer, request, "response_too_large")
            return
        await self._write_response(writer, frame, request_id=request.request_id)
        response_hook = self._response_sent_hooks.get(request.method)
        if response_hook is not None:
            response_hook()

    async def _send_error(
        self, writer: asyncio.StreamWriter, request: IpcRequest, error_code: str
    ) -> None:
        response = self._authenticator.create_response(
            request,
            ok=False,
            error_code=error_code,
        )
        await self._write_response(
            writer,
            response.model_dump_json().encode("utf-8"),
            request_id=request.request_id,
        )

    async def _write_response(
        self,
        writer: asyncio.StreamWriter,
        payload: bytes,
        *,
        request_id: UUID | None = None,
    ) -> None:
        frames, metrics = encode_frames(
            payload,
            self._authenticator,
            legacy_frame_bytes=self._max_frame_bytes,
            max_message_bytes=self._max_message_bytes,
        )
        if metrics.framed and request_id is not None and self._audit_sink is not None:
            self._audit_sink.record_system_event(
                request_id,
                event_type="ipc_stream_sent",
                component="ipc_transport",
                data={
                    "frame_count": metrics.frame_count,
                    "payload_bytes": metrics.payload_bytes,
                },
            )
        async with asyncio.timeout(self._write_timeout_seconds):
            for frame in frames:
                writer.write(frame)
                await writer.drain()

    def _unlink_owned_socket(self) -> None:
        if self._socket_identity is None:
            return
        try:
            status = self._path.lstat()
        except FileNotFoundError:
            self._socket_identity = None
            return
        if (
            stat.S_ISSOCK(status.st_mode)
            and (status.st_dev, status.st_ino) == self._socket_identity
            and status.st_uid == self._expected_uid
        ):
            self._path.unlink()
        self._socket_identity = None

    def _record_protocol_violation(self, error: ProtocolError) -> None:
        if self._audit_sink is None:
            return
        now = time.monotonic()
        if now - self._last_protocol_alert_at < 5.0:
            self._suppressed_protocol_alerts += 1
            return
        suppressed = self._suppressed_protocol_alerts
        self._suppressed_protocol_alerts = 0
        self._last_protocol_alert_at = now
        try:
            self._audit_sink.record_system_event(
                uuid4(),
                event_type="ipc_protocol_violation",
                component="ipc_transport",
                data={
                    "severity": "critical",
                    "reason": str(error),
                    "suppressed_since_last": suppressed,
                },
            )
        except Exception:
            # The transport still aborts if the append-only ledger is unavailable.
            return
