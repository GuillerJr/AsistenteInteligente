"""Signed desktop worker queue for the one admitted native Jarvis client.

Model tools enqueue only under the common Runtime authority. The native worker
must claim the exact digest immediately before dispatch; lost replies are receipts
to reconcile, never permission to execute again. No automatic app/tab discovery.
"""

from __future__ import annotations

import asyncio
import contextvars
import hashlib
import json
import re
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from uuid import uuid4

from jarvis_core.protocol import RpcError, authenticated_client
from jarvis_core.tools import (
    ToolError,
    canonical_arguments,
    check_tool_authority,
    current_tool_operation,
    revalidate_committed_tool_authority,
)


def _id(value):
    return isinstance(value, str) and re.fullmatch(r"[a-f0-9]{32}", value) is not None


def _client():
    row = authenticated_client.get()
    if not isinstance(row, str) or re.fullmatch(r"[a-f0-9]{64}", row) is None:
        raise RpcError(-32003, "Se requiere un cliente de gestión admitido.")
    return row


@dataclass
class _NativeWork:
    operation_id: str
    nonce: str
    tool: str
    command: str
    arguments: dict
    original: Mapping
    digest: str
    session_id: str
    run_id: str
    context: contextvars.Context
    cancellation: asyncio.Event
    future: asyncio.Future
    deadline: float
    guard: Callable
    claimed: bool = False
    cancelled: bool = False
    unresolved: bool = False
    result_digest: str | None = None


class NativeAutomationBroker:
    def __init__(self, *, timeout: float = 24, clock=time.monotonic, assert_owner=lambda _: None):
        self.timeout, self.clock = timeout, clock
        self.assert_owner = assert_owner
        self.host_client = self.host_id = None
        self.capabilities: set[str] = set()
        self.work: dict[str, _NativeWork] = {}
        self.last_seen = 0.0
        self.closed = False
        self.closing = False

    @property
    def blockers(self):
        return tuple(
            "native_automation_active"
            for row in self.work.values()
            if not row.future.done() or row.unresolved
        )

    def assert_settled(self, session_id):
        if any(
            row.session_id == session_id and (not row.future.done() or row.unresolved)
            for row in self.work.values()
        ):
            raise ToolError("native_automation_cleanup_pending")

    def handlers(self):
        return {
            "automation.attach": self.attach,
            "automation.poll": self.poll,
            "automation.claim": self.claim,
            "automation.complete": self.complete,
            "automation.check": self.check,
            "automation.detach": self.detach,
            "automation.status": self.status,
        }

    def _params(self, value, required, optional=frozenset()):
        if (
            not isinstance(value, dict)
            or required - value.keys()
            or value.keys() - required - optional
        ):
            raise RpcError(-32602, "Los parámetros de automatización no son válidos.")

    def _host(self, value):
        owner = _client()
        self.assert_owner(owner)
        if owner != self.host_client or value.get("host_id") != self.host_id:
            raise RpcError(-32003, "El cliente no posee este trabajador nativo.")
        self.last_seen = self.clock()

    async def attach(self, value):
        self._params(value, {"host_id", "capabilities"})
        owner = _client()
        self.assert_owner(owner)
        if (
            not _id(value["host_id"])
            or not isinstance(value["capabilities"], list)
            or not value["capabilities"]
            or len(value["capabilities"]) > 3
            or set(value["capabilities"]) - {"desktop", "browser", "media"}
            or len(set(value["capabilities"])) != len(value["capabilities"])
        ):
            raise RpcError(-32602, "El trabajador nativo no es válido.")
        if self.closed or self.closing or (
            self.host_id is not None
            and (owner, value["host_id"]) != (self.host_client, self.host_id)
        ):
            raise RpcError(-32042, "Cierra primero el trabajador nativo anterior.")
        self.host_client, self.host_id = owner, value["host_id"]
        self.capabilities = set(value["capabilities"])
        self.last_seen = self.clock()
        return {"attached": True, "host_id": self.host_id, "permits_replay": False}

    async def status(self, value):
        self._params(value, set())
        return {
            "connected": self.host_id is not None and not self.closed,
            "capabilities": sorted(self.capabilities),
            "active": sum(not x.future.done() for x in self.work.values()),
            "unresolved": sum(x.unresolved for x in self.work.values()),
            "permits_replay": False,
        }

    async def poll(self, value):
        self._params(value, {"host_id"})
        self._host(value)
        tasks, cancellations = [], []
        for row in self.work.values():
            if row.cancellation.is_set() or self.clock() >= row.deadline:
                self._cancel(row)
            if row.cancelled and row.claimed and not row.future.done():
                cancellations.append(row.operation_id)
            elif not row.claimed and not row.future.done():
                tasks.append(
                    {
                        "operation_id": row.operation_id,
                        "nonce": row.nonce,
                        "command": row.command,
                        "arguments": row.arguments,
                        "digest": row.digest,
                        "payload_json": canonical_arguments(
                            {"command": row.command, "arguments": row.arguments}
                        ),
                        "remaining_seconds": max(0, row.deadline - self.clock()),
                    }
                )
        return {"host_id": self.host_id, "tasks": tasks, "cancelled": cancellations}

    def _row(self, value):
        self._host(value)
        row = self.work.get(value["operation_id"])
        if row is None or row.nonce != value["nonce"] or row.digest != value["digest"]:
            raise RpcError(-32042, "La operación nativa no coincide con la revisión.")
        return row

    async def claim(self, value):
        self._params(value, {"host_id", "operation_id", "nonce", "digest"})
        row = self._row(value)
        if row.claimed or row.cancelled or row.future.done() or self.clock() >= row.deadline:
            raise RpcError(-32042, "La operación ya se despachó o perdió su autorización.")
        try:
            row.context.run(row.guard)
            row.context.run(check_tool_authority, row.tool, row.original, commit=True)
        except (ToolError, ValueError, asyncio.CancelledError):
            self._cancel(row)
            raise RpcError(-32042, "La autorización nativa ya no es válida.") from None
        row.claimed = True
        return {
            "claimed": True,
            "operation_id": row.operation_id,
            "digest": row.digest,
            "remaining_seconds": max(0, row.deadline - self.clock()),
            "permits_replay": False,
        }

    async def complete(self, value):
        self._params(value, {"host_id", "operation_id", "nonce", "digest", "result"})
        row = self._row(value)
        result = value["result"]
        if not row.claimed or not isinstance(result, dict):
            raise RpcError(-32042, "No hay un despacho nativo que completar.")
        raw = canonical_arguments(result)
        if (
            len(raw.encode()) > 131072
            or type(result.get("succeeded")) is not bool
            or type(result.get("verified")) is not bool
            or result.get("effect_status") not in {"known", "unknown"}
            or (not result["succeeded"] and result["verified"])
            or (
                result.get("effect_status") == "unknown"
                and (result["succeeded"] or result["verified"])
            )
            or (
                result.get("error") is not None
                and (not isinstance(result["error"], str) or len(result["error"].encode()) > 256)
            )
        ):
            raise RpcError(-32602, "El recibo nativo no es válido.")
        digest = hashlib.sha256(raw.encode()).hexdigest()
        if row.result_digest is not None and row.result_digest != digest:
            raise RpcError(-32042, "El recibo nativo ya tiene otro resultado.")
        row.result_digest, row.unresolved = digest, False
        if not row.future.done():
            row.future.set_result(result)
        return {
            "accepted": True,
            "operation_id": row.operation_id,
            "result_sha256": digest,
            "permits_replay": False,
        }

    async def check(self, value):
        self._params(value, {"host_id", "operation_id", "nonce", "digest"})
        row = self._row(value)
        if (
            not row.claimed
            or row.cancelled
            or row.future.done()
            or row.cancellation.is_set()
            or self.clock() >= row.deadline
        ):
            raise RpcError(-32042, "La operación nativa perdió su autorización.")
        try:
            row.context.run(row.guard)
            row.context.run(revalidate_committed_tool_authority, row.tool, row.original)
        except (ValueError, asyncio.CancelledError):
            self._cancel(row)
            raise RpcError(-32042, "La autorización fue revocada antes del efecto.") from None
        return {
            "checked": True,
            "operation_id": row.operation_id,
            "digest": row.digest,
            "permits_replay": False,
        }

    async def detach(self, value):
        self._params(value, {"host_id", "cleanup_confirmed"})
        self._host(value)
        if value["cleanup_confirmed"] is not True:
            raise RpcError(-32042, "El cierre nativo debe estar comprobado.")
        # The admitted worker acknowledges its own executors have reset and all
        # effects finished. No model, timer or cancellation ACK supplies this.
        for row in self.work.values():
            self._cancel(row)
            if not row.future.done():
                row.future.set_result(self._unknown("native_worker_closed"))
            row.unresolved = False
        self.host_client = self.host_id = None
        self.capabilities.clear()
        return {"detached": True, "permits_replay": False}

    @staticmethod
    def _unknown(error):
        return {"succeeded": False, "verified": False, "effect_status": "unknown", "error": error}

    def _cancel(self, row):
        row.cancelled = True
        if not row.claimed and not row.future.done():
            row.future.set_result(
                {
                    "succeeded": False,
                    "verified": False,
                    "effect_status": "known",
                    "error": "native_not_dispatched",
                }
            )

    def cancel_all(self):
        for row in self.work.values():
            self._cancel(row)

    async def execute(
        self, tool, command, arguments, original, cancellation, *, session_id, run_id, guard,
        timeout=None,
    ):
        if timeout is not None and (command != "media.inspect" or type(timeout) not in {int, float}
                                    or not 0 < timeout <= 180):
            raise ToolError("native_timeout_invalid")
        family = command.split(".", 1)[0].split("_", 1)[0]
        if self.closed or self.closing or self.host_id is None or family not in self.capabilities:
            return {
                "succeeded": False,
                "verified": False,
                "effect_status": "known",
                "error": "native_worker_unavailable",
            }
        self.assert_settled(session_id)
        for key, prior in tuple(self.work.items()):
            if len(self.work) < 64:
                break
            if prior.future.done() and not prior.unresolved:
                self.work.pop(key)
        if len(self.blockers) >= 8 or len(self.work) >= 128:
            raise ToolError("native_automation_capacity")
        guard()
        check_tool_authority(tool, original)
        operation_id = current_tool_operation().operation_id
        args = {**json.loads(canonical_arguments(arguments)), "operation_id": operation_id}
        digest = hashlib.sha256(
            canonical_arguments({"command": command, "arguments": args}).encode()
        ).hexdigest()
        row = _NativeWork(
            operation_id,
            uuid4().hex,
            tool,
            command,
            args,
            original,
            digest,
            session_id,
            run_id,
            contextvars.copy_context(),
            cancellation,
            asyncio.get_running_loop().create_future(),
            self.clock() + (self.timeout if timeout is None else timeout),
            guard,
        )
        self.work[operation_id] = row
        cancelled = asyncio.create_task(cancellation.wait())
        try:
            while not row.future.done():
                try:
                    await asyncio.wait(
                        (row.future, cancelled), timeout=0.25, return_when=asyncio.FIRST_COMPLETED
                    )
                except asyncio.CancelledError:
                    cancellation.set()
                if row.future.done():
                    break
                if cancellation.is_set() or self.clock() >= row.deadline:
                    self._cancel(row)
                if self.host_id is None or self.clock() - self.last_seen > 8:
                    row.unresolved = row.claimed
                    if not row.future.done():
                        row.future.set_result(self._unknown("native_worker_connection_lost"))
                if cancelled.done() and not row.future.done():
                    # Await the same executor's cleanup, not a fresh dispatch.
                    await asyncio.sleep(0.03)
            result = row.future.result()
            if not cancellation.is_set() and row.claimed:
                row.context.run(row.guard)
                row.context.run(revalidate_committed_tool_authority, tool, original)
            return result
        finally:
            cancelled.cancel()
            await asyncio.gather(cancelled, return_exceptions=True)
            # Keep a bounded exact receipt for an ACK lost on complete(). A
            # duplicate receipt may be acknowledged; its claim never repeats.

    async def aclose(self, *, drain=False):
        self.closing = True
        self.cancel_all()
        pending = [x.future for x in self.work.values() if not x.future.done()]
        if pending and drain:
            await asyncio.wait(pending, timeout=self.timeout)
        if self.blockers:
            raise TimeoutError("service_cleanup_incomplete")
        self.closed = True
