from __future__ import annotations

import asyncio
import errno
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
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from aegis_core.ipc.protocol import (
    PROTOCOL_VERSION,
    FreshnessStatus,
    IpcAuthenticator,
    IpcRequest,
    NonceWindow,
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
    def __init__(
        self,
        socket_path: Path,
        authenticator: IpcAuthenticator,
        *,
        max_frame_bytes: int = 65_536,
        clock_skew_seconds: int = 30,
        max_clients: int = 16,
        expected_uid: int | None = None,
        peer_uid_resolver: Callable[[Any], int] = peer_uid,
        handlers: Mapping[str, IpcMethodHandler] | None = None,
    ) -> None:
        self._path = socket_path
        self._authenticator = authenticator
        self._max_frame_bytes = max_frame_bytes
        self._expected_uid = os.getuid() if expected_uid is None else expected_uid
        self._peer_uid_resolver = peer_uid_resolver
        self._handlers = dict(handlers or {})
        if {"health", "runtime.info", "runtime.metrics"} & self._handlers.keys():
            raise ValueError("custom handlers cannot replace built-in IPC methods")
        self._nonce_window = NonceWindow(clock_skew=timedelta(seconds=clock_skew_seconds))
        self._semaphore = asyncio.Semaphore(max_clients)
        self._server: asyncio.AbstractServer | None = None
        self._socket_identity: tuple[int, int] | None = None
        self._started_at = time.monotonic()

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
        async with self._semaphore:
            try:
                peer_socket = writer.get_extra_info("socket")
                if (
                    peer_socket is None
                    or self._peer_uid_resolver(peer_socket) != self._expected_uid
                ):
                    return
                raw_frame = await asyncio.wait_for(reader.readline(), timeout=5)
                if (
                    not raw_frame
                    or not raw_frame.endswith(b"\n")
                    or len(raw_frame) > self._max_frame_bytes
                ):
                    return
                try:
                    request = IpcRequest.model_validate_json(raw_frame)
                except (ValidationError, ValueError):
                    return
                if not self._authenticator.verify_request(request):
                    return
                freshness = self._nonce_window.accept(request)
                if freshness is not FreshnessStatus.ACCEPTED:
                    await self._send_error(writer, request, f"request_{freshness.value}")
                    return
                await self._dispatch(writer, request)
            except (TimeoutError, ConnectionError, OSError, ValueError):
                return
            finally:
                writer.close()
                try:
                    await writer.wait_closed()
                except (ConnectionError, OSError):
                    pass

    async def _dispatch(self, writer: asyncio.StreamWriter, request: IpcRequest) -> None:
        if request.method == "health":
            if request.payload:
                await self._send_error(writer, request, "invalid_payload")
                return
            payload = {
                "status": "ok",
                "protocol_version": PROTOCOL_VERSION,
                "architecture": platform.machine(),
                "pid": os.getpid(),
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
            }
        else:
            handler = self._handlers.get(request.method)
            if handler is None:
                await self._send_error(writer, request, "method_not_found")
                return
            try:
                result = IpcHandlerResult.model_validate(await handler(request))
            except Exception:
                await self._send_error(writer, request, "handler_failed")
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
        await self._write_response(writer, response.model_dump_json().encode("utf-8") + b"\n")

    async def _send_error(
        self, writer: asyncio.StreamWriter, request: IpcRequest, error_code: str
    ) -> None:
        response = self._authenticator.create_response(
            request,
            ok=False,
            error_code=error_code,
        )
        await self._write_response(writer, response.model_dump_json().encode("utf-8") + b"\n")

    async def _write_response(self, writer: asyncio.StreamWriter, frame: bytes) -> None:
        if len(frame) > self._max_frame_bytes:
            raise DaemonSecurityError("IPC response exceeds frame limit")
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
