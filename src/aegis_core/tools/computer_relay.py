from __future__ import annotations

import asyncio
import concurrent.futures
import json
import math
from dataclasses import dataclass
from typing import Any
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field, field_validator

from aegis_core.ipc.protocol import IpcRequest
from aegis_core.ipc.server import IpcHandlerResult, IpcMethodHandler
from aegis_core.tools.computer import ComputerUseError, NativeComputerBridge

_MAX_COMMAND_BYTES = 8_192
_MAX_RESPONSE_BYTES = 60_000
_ALLOWED_FAILURES = frozenset(
    {
        "accessibility_permission_required",
        "accessibility_unresponsive",
        "application_unavailable",
        "capture_failed",
        "computer_observation_changed",
        "computer_user_takeover",
        "display_unavailable",
        "frontmost_application_mismatch",
        "screen_capture_permission_required",
        "sensitive_target_blocked",
        "unsafe_target",
        "user_session_inactive",
        "visual_context_changed",
        "window_geometry_invalid",
        "window_unavailable",
    }
)


class ComputerRelayWaitPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    timeout_milliseconds: int = Field(default=20_000, ge=100, le=20_000)


class ComputerRelayCompletePayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    command_id: UUID
    response: dict[str, Any]

    @field_validator("response")
    @classmethod
    def response_must_be_bounded_json(cls, value: dict[str, Any]) -> dict[str, Any]:
        try:
            encoded = json.dumps(
                value,
                separators=(",", ":"),
                sort_keys=True,
                allow_nan=False,
            ).encode("utf-8")
        except (TypeError, ValueError) as error:
            raise ValueError("computer relay response is not JSON") from error
        if not value or len(encoded) > _MAX_RESPONSE_BYTES:
            raise ValueError("computer relay response is out of range")
        return value


@dataclass(frozen=True, slots=True)
class ComputerRelayCommand:
    command_id: UUID
    payload: dict[str, object]
    expires_at: float


class ComputerCommandRelay:
    def __init__(self, loop: asyncio.AbstractEventLoop) -> None:
        self._loop = loop
        self._queued: ComputerRelayCommand | None = None
        self._available = asyncio.Event()
        self._pending: dict[UUID, asyncio.Future[dict[str, Any]]] = {}
        self._delivered: dict[UUID, float] = {}
        self._closed = False

    def request_sync(
        self,
        payload: dict[str, object],
        *,
        timeout_seconds: float,
    ) -> dict[str, Any]:
        if not math.isfinite(timeout_seconds) or not 0 < timeout_seconds <= 22:
            raise ValueError("computer command timeout is out of range")
        encoded = json.dumps(
            payload,
            separators=(",", ":"),
            sort_keys=True,
            allow_nan=False,
        ).encode("utf-8")
        if not payload or len(encoded) > _MAX_COMMAND_BYTES:
            raise ComputerUseError("computer_command_too_large")
        future = asyncio.run_coroutine_threadsafe(
            self._request(payload, timeout_seconds=timeout_seconds),
            self._loop,
        )
        try:
            return future.result(timeout=timeout_seconds + 1)
        except (TimeoutError, concurrent.futures.TimeoutError) as error:
            future.cancel()
            raise ComputerUseError("computer_helper_unavailable") from error
        except ComputerUseError:
            raise
        except Exception as error:
            raise ComputerUseError("computer_helper_unavailable") from error

    async def _request(
        self,
        payload: dict[str, object],
        *,
        timeout_seconds: float,
    ) -> dict[str, Any]:
        if self._closed or self._queued is not None:
            raise ComputerUseError("computer_helper_unavailable")
        command = ComputerRelayCommand(
            command_id=uuid4(),
            payload=payload,
            expires_at=self._loop.time() + timeout_seconds,
        )
        response = self._loop.create_future()
        self._pending[command.command_id] = response
        self._queued = command
        self._available.set()
        try:
            return await asyncio.wait_for(response, timeout=timeout_seconds)
        except TimeoutError as error:
            raise ComputerUseError("computer_helper_unavailable") from error
        finally:
            self._pending.pop(command.command_id, None)
            self._delivered.pop(command.command_id, None)
            if self._queued is command:
                self._queued = None
                if not self._closed:
                    self._available.clear()

    async def next_command(self, timeout_seconds: float) -> ComputerRelayCommand | None:
        if not math.isfinite(timeout_seconds) or not 0 <= timeout_seconds <= 20:
            raise ValueError("computer poll timeout is out of range")
        deadline = self._loop.time() + timeout_seconds
        while not self._closed:
            remaining = deadline - self._loop.time()
            if remaining <= 0:
                return None
            command = self._queued
            if command is not None:
                self._queued = None
                self._available.clear()
                pending = self._pending.get(command.command_id)
                if (
                    pending is not None
                    and not pending.done()
                    and command.expires_at > self._loop.time()
                ):
                    self._delivered[command.command_id] = command.expires_at
                    return command
            try:
                await asyncio.wait_for(self._available.wait(), timeout=remaining)
            except TimeoutError:
                return None
        return None

    def complete(self, command_id: UUID, response: dict[str, Any]) -> bool:
        pending = self._pending.get(command_id)
        if (
            self._delivered.get(command_id, 0) <= self._loop.time()
            or pending is None
            or pending.done()
        ):
            return False
        pending.set_result(response)
        return True

    def close(self) -> None:
        self._closed = True
        self._queued = None
        self._delivered.clear()
        self._available.set()  # Wake every poller, including an empty queue's waiters.
        for pending in self._pending.values():
            if not pending.done():
                pending.set_exception(ComputerUseError("computer_helper_unavailable"))
        self._pending.clear()


class RelayedComputerBridge(NativeComputerBridge):
    def __init__(self, relay: ComputerCommandRelay) -> None:
        self._relay = relay

    def _invoke(
        self,
        payload: dict[str, object],
        *,
        timeout_seconds: float = 8.0,
    ) -> dict[str, Any]:
        response = self._relay.request_sync(payload, timeout_seconds=timeout_seconds)
        if response.get("status") != "ok":
            reason = response.get("reason")
            raise ComputerUseError(
                reason
                if isinstance(reason, str) and reason in _ALLOWED_FAILURES
                else "computer_helper_failed"
            )
        return response


class ComputerRelayIpcService:
    WAIT_METHOD = "computer.wait"
    COMPLETE_METHOD = "computer.complete"
    MAX_WAIT_SECONDS = 20.0

    def __init__(self, relay: ComputerCommandRelay) -> None:
        self._relay = relay

    def handlers(self) -> dict[str, IpcMethodHandler]:
        return {
            self.WAIT_METHOD: self.handle,
            self.COMPLETE_METHOD: self.handle,
        }

    async def handle(self, request: IpcRequest) -> IpcHandlerResult:
        try:
            if request.method == self.WAIT_METHOD:
                payload = ComputerRelayWaitPayload.model_validate(request.payload)
                command = await self._relay.next_command(payload.timeout_milliseconds / 1_000)
                if command is None:
                    return IpcHandlerResult(ok=True, payload={"available": False})
                return IpcHandlerResult(
                    ok=True,
                    payload={
                        "available": True,
                        "command_id": str(command.command_id),
                        "command": command.payload,
                    },
                )
            if request.method == self.COMPLETE_METHOD:
                payload = ComputerRelayCompletePayload.model_validate(request.payload)
                if not self._relay.complete(payload.command_id, payload.response):
                    return IpcHandlerResult(ok=False, error_code="computer_command_unavailable")
                return IpcHandlerResult(ok=True, payload={"accepted": True})
        except ValueError:
            return IpcHandlerResult(ok=False, error_code="invalid_payload")
        return IpcHandlerResult(ok=False, error_code="method_not_found")
