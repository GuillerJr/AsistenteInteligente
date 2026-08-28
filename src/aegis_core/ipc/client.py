from __future__ import annotations

import asyncio
import os
import stat
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from aegis_core.ipc.framing import DEFAULT_MAX_MESSAGE_BYTES, encode_message, read_message
from aegis_core.ipc.protocol import IpcAuthenticator, IpcResponse, ProtocolError


class IpcClient:
    def __init__(
        self,
        socket_path: Path,
        authenticator: IpcAuthenticator,
        *,
        max_frame_bytes: int = 65_536,
        max_message_bytes: int = DEFAULT_MAX_MESSAGE_BYTES,
        timeout_seconds: float = 5,
        clock_skew_seconds: int = 30,
    ) -> None:
        self._path = socket_path
        self._authenticator = authenticator
        if max_frame_bytes < 1:
            raise ValueError("IPC legacy frame limit must be positive")
        self._max_frame_bytes = max_frame_bytes
        if max_message_bytes < max_frame_bytes:
            raise ValueError("IPC message limit cannot be smaller than legacy frame limit")
        self._max_message_bytes = max_message_bytes
        self._timeout_seconds = timeout_seconds
        self._clock_skew = timedelta(seconds=clock_skew_seconds)

    async def call(
        self,
        method: str,
        payload: dict[str, Any] | None = None,
        *,
        now: datetime | None = None,
        nonce: str | None = None,
    ) -> IpcResponse:
        self._validate_socket()
        request = self._authenticator.create_request(
            method,
            payload,
            now=now,
            nonce=nonce,
        )
        frame, _ = encode_message(
            request.model_dump_json().encode("utf-8"),
            self._authenticator,
            legacy_frame_bytes=self._max_frame_bytes,
            max_message_bytes=self._max_message_bytes,
        )

        reader, writer = await asyncio.wait_for(
            asyncio.open_unix_connection(self._path, limit=self._max_frame_bytes + 1),
            timeout=self._timeout_seconds,
        )
        try:
            writer.write(frame)
            await writer.drain()
            try:
                (raw_response, _) = await asyncio.wait_for(
                    read_message(
                        reader,
                        self._authenticator,
                        legacy_frame_bytes=self._max_frame_bytes,
                        max_message_bytes=self._max_message_bytes,
                    ),
                    timeout=self._timeout_seconds,
                )
            except (ValueError, asyncio.IncompleteReadError) as error:
                raise ProtocolError("IPC response frame is invalid") from error
            try:
                response = IpcResponse.model_validate_json(raw_response)
            except (ValidationError, ValueError) as error:
                raise ProtocolError("IPC response is malformed") from error
            if response.request_id != request.request_id or response.request_nonce != request.nonce:
                raise ProtocolError("IPC response is not bound to the request")
            if not self._authenticator.verify_response(response):
                raise ProtocolError("IPC response authentication failed")
            current = datetime.now(UTC)
            if abs(current - response.timestamp) >= self._clock_skew:
                raise ProtocolError("IPC response timestamp is stale")
            return response
        finally:
            writer.close()
            try:
                await writer.wait_closed()
            except (ConnectionError, OSError):
                pass

    def _validate_socket(self) -> None:
        try:
            status = self._path.lstat()
        except FileNotFoundError as error:
            raise ProtocolError("daemon socket is unavailable") from error
        if (
            not stat.S_ISSOCK(status.st_mode)
            or status.st_uid != os.getuid()
            or stat.S_IMODE(status.st_mode) & 0o077
        ):
            raise ProtocolError("daemon socket ownership or permissions are invalid")
