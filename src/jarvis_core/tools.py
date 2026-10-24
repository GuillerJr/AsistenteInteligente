"""Original tool catalogue and one-shot, contextual human approval authority.

Approval tickets are process-local capabilities. The local RPC host must bind its
human decision endpoint to an authenticated client; these objects do not claim
macOS code-signing attestation. External descriptions never grant authority.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import math
import re
import time
from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, replace
from types import MappingProxyType
from typing import Any
from uuid import uuid4

from .runtime import ApprovalGrant, ApprovalRequest, ToolDefinition, ToolResult


class ToolError(ValueError):
    """Stable public errors exclude arguments, filesystem paths and peer text."""


class ToolCancelled(asyncio.CancelledError):
    def __init__(self, receipt: ToolResult) -> None:
        super().__init__("tool_cancelled")
        self.receipt = receipt


def _data(value: Any, depth: int = 0, budget: list[int] | None = None) -> Any:
    budget = [10000] if budget is None else budget
    budget[0] -= 1
    if depth > 24 or budget[0] < 0:
        raise ToolError("tool_data_limit")
    if isinstance(value, Mapping):
        if any(type(key) is not str for key in value):
            raise ToolError("invalid_tool_arguments")
        return {key: _data(item, depth + 1, budget) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_data(item, depth + 1, budget) for item in value]
    if value is None or type(value) in {str, bool, int}:
        return value
    if type(value) is float and math.isfinite(value):
        return value
    raise ToolError("invalid_tool_arguments")


def canonical_arguments(arguments: Mapping[str, Any]) -> str:
    if not isinstance(arguments, Mapping):
        raise ToolError("invalid_tool_arguments")
    data = json.dumps(_data(arguments), sort_keys=True, ensure_ascii=False,
                      separators=(",", ":"), allow_nan=False)
    if len(data.encode()) > 131072:
        raise ToolError("tool_data_limit")
    return data


def _freeze(value: Any) -> Any:
    if isinstance(value, dict):
        return MappingProxyType({key: _freeze(item) for key, item in value.items()})
    if isinstance(value, list):
        return tuple(_freeze(item) for item in value)
    return value


def _identity(value: str) -> str:
    if not isinstance(value, str) or not value or len(value.encode()) > 256:
        raise ToolError("invalid_tool_identity")
    return value


def operation_digest(operation: ApprovalRequest) -> str:
    for value in (operation.session_id, operation.run_id, operation.operation_id,
                  operation.tool_name):
        _identity(value)
    if (not isinstance(operation.arguments_json, str)
            or len(operation.arguments_json.encode()) > 131072):
        raise ToolError("tool_data_limit")
    try:
        arguments = json.loads(operation.arguments_json)
    except (ValueError, TypeError, RecursionError):
        raise ToolError("invalid_tool_arguments") from None
    if canonical_arguments(arguments) != operation.arguments_json:
        raise ToolError("noncanonical_tool_arguments")
    identity = json.dumps({"run": operation.run_id, "session": operation.session_id,
                           "operation": operation.operation_id, "tool": operation.tool_name,
                           "arguments_json": operation.arguments_json}, sort_keys=True,
                          ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(identity.encode()).hexdigest()


@dataclass(frozen=True)
class PendingApproval:
    approval_id: str
    session_id: str
    run_id: str
    operation_id: str
    tool_name: str
    arguments_json: str
    digest: str
    expires_at_ns: int


@dataclass
class _Pending:
    view: PendingApproval
    operation: ApprovalRequest
    cancellation: asyncio.Event
    future: asyncio.Future
    version: int
    deadline: float


@dataclass
class _Issued:
    operation: ApprovalRequest
    grant: ApprovalGrant
    cancellation: asyncio.Event
    version: int
    deadline: float


@dataclass
class _Lease:
    authority: ApprovalAuthority
    issued: _Issued
    committed: bool = False
    active: bool = True

    def check(self, tool_name: str, arguments: Mapping[str, Any], commit: bool) -> None:
        if (not self.active or self.committed or self.issued.operation.tool_name != tool_name
                or self.issued.operation.arguments_json != canonical_arguments(arguments)):
            raise ToolError("tool_authority_mismatch")
        self.authority._check_issued(self.issued)
        if commit:
            self.committed = True


_LEASE: ContextVar[_Lease | None] = ContextVar("jarvis_tool_authority", default=None)


def check_tool_authority(
    tool_name: str, arguments: Mapping[str, Any], *, commit: bool = False,
) -> None:
    lease = _LEASE.get()
    if lease is None:
        raise ToolError("tool_authority_required")
    lease.check(tool_name, arguments, commit)


class ApprovalAuthority:
    def __init__(self, *, ttl: float = 120, max_pending: int = 32) -> None:
        if (type(ttl) not in {float, int} or not math.isfinite(ttl) or not 0 < ttl <= 600
                or type(max_pending) is not int or not 1 <= max_pending <= 256):
            raise ValueError("invalid_approval_budget")
        self.ttl, self.max_pending = ttl, max_pending
        self._pending: dict[str, _Pending] = {}
        self._issued: dict[int, _Issued] = {}
        self._versions: dict[str, int] = {}
        self._seen_operations: set[tuple[str, str, str]] = set()
        self._closed = False
        self._loop: asyncio.AbstractEventLoop | None = None

    def _on_loop(self) -> None:
        loop = asyncio.get_running_loop()
        if self._loop is None:
            self._loop = loop
        if self._loop is not loop:
            raise ToolError("approval_loop_mismatch")

    def _check_issued(self, issued: _Issued) -> None:
        self._on_loop()
        if (self._closed or issued.cancellation.is_set() or time.monotonic() >= issued.deadline
                or self._versions.get(issued.operation.session_id, 0) != issued.version):
            raise ToolError("tool_authority_revoked")

    def _sweep(self) -> None:
        for key, entry in tuple(self._pending.items()):
            if (entry.cancellation.is_set() or time.monotonic() >= entry.deadline
                    or self._closed):
                if not entry.future.done():
                    entry.future.set_result(None)
                self._pending.pop(key, None)
        for key, issued in tuple(self._issued.items()):
            try:
                self._check_issued(issued)
            except ToolError:
                self._issued.pop(key, None)

    async def request(
        self, operation: ApprovalRequest, cancellation: asyncio.Event,
    ) -> ApprovalGrant | None:
        self._on_loop()
        if (not isinstance(operation, ApprovalRequest)
                or not isinstance(cancellation, asyncio.Event)):
            raise ToolError("invalid_approval_request")
        if operation.digest != operation_digest(operation):
            raise ToolError("approval_digest_mismatch")
        self._sweep()
        if self._closed or cancellation.is_set():
            return None
        identity = operation.session_id, operation.run_id, operation.operation_id
        if identity in self._seen_operations:
            raise ToolError("approval_operation_replayed")
        if len(self._seen_operations) >= 65536:
            raise ToolError("approval_operation_capacity")
        if len(self._pending) + len(self._issued) >= self.max_pending:
            raise ToolError("approval_capacity")
        self._seen_operations.add(identity)
        approval_id = uuid4().hex
        future = asyncio.get_running_loop().create_future()
        view = PendingApproval(approval_id, operation.session_id, operation.run_id,
                               operation.operation_id, operation.tool_name,
                               operation.arguments_json, operation.digest,
                               time.time_ns() + int(self.ttl * 1_000_000_000))
        entry = _Pending(view, operation, cancellation, future,
                         self._versions.get(operation.session_id, 0),
                         time.monotonic() + self.ttl)
        self._pending[approval_id] = entry
        cancelled = asyncio.create_task(cancellation.wait())
        delivered = False
        try:
            done, _ = await asyncio.wait((future, cancelled), timeout=self.ttl,
                                         return_when=asyncio.FIRST_COMPLETED)
            if cancelled in done or cancellation.is_set() or future not in done:
                return None
            grant = future.result()
            if grant is not None:
                issued = self._issued.get(id(grant))
                if issued is None:
                    return None
                self._check_issued(issued)
                delivered = True
            return grant
        finally:
            self._pending.pop(approval_id, None)
            cancelled.cancel()
            if not delivered:
                for key, issued in tuple(self._issued.items()):
                    if issued.operation.operation_id == operation.operation_id:
                        self._issued.pop(key, None)
            await asyncio.gather(cancelled, return_exceptions=True)

    def list_pending(self, session_id: str | None = None) -> tuple[PendingApproval, ...]:
        self._on_loop()
        self._sweep()
        return tuple(entry.view for entry in self._pending.values()
                     if not entry.future.done()
                     and (session_id is None or entry.operation.session_id == session_id))

    def decide(self, approval_id: str, allow: bool) -> bool:
        self._on_loop()
        if type(allow) is not bool or not isinstance(approval_id, str):
            raise ToolError("invalid_approval_decision")
        self._sweep()
        entry = self._pending.get(approval_id)
        if entry is None or entry.future.done():
            return False
        grant = ApprovalGrant(entry.operation.digest) if allow else None
        if grant is not None:
            self._issued[id(grant)] = _Issued(entry.operation, grant, entry.cancellation,
                                              entry.version, entry.deadline)
        entry.future.set_result(grant)
        return True

    def revoke(self, session_id: str) -> int:
        self._on_loop()
        _identity(session_id)
        self._versions[session_id] = self._versions.get(session_id, 0) + 1
        count = 0
        for key, entry in tuple(self._pending.items()):
            if entry.operation.session_id == session_id:
                if not entry.future.done():
                    entry.future.set_result(None)
                self._pending.pop(key, None)
                count += 1
        for key, issued in tuple(self._issued.items()):
            if issued.operation.session_id == session_id:
                self._issued.pop(key, None)
                count += 1
        return count

    def _consume(self, operation: ApprovalRequest, grant: ApprovalGrant) -> _Issued:
        self._on_loop()
        issued = self._issued.pop(id(grant), None)
        if (issued is None or issued.grant is not grant or issued.operation != operation
                or operation.digest != operation_digest(operation)):
            raise ToolError("tool_authority_invalid")
        self._check_issued(issued)
        return issued

    def consume(self, operation: ApprovalRequest, grant: ApprovalGrant) -> None:
        self._consume(operation, grant)

    @contextmanager
    def execution_scope(self, operation: ApprovalRequest, grant: ApprovalGrant) -> Iterator[None]:
        issued = self._consume(operation, grant)
        lease = _Lease(self, issued)
        token = _LEASE.set(lease)
        try:
            yield
        finally:
            lease.active = False
            _LEASE.reset(token)

    async def close(self) -> None:
        self._on_loop()
        self._closed = True
        self._sweep()
        self._issued.clear()


@dataclass(frozen=True)
class ToolSpec:
    name: str
    description: str
    input_schema: Mapping[str, Any]

    def __post_init__(self) -> None:
        if (not isinstance(self.name, str) or re.fullmatch(r"[a-z][a-z0-9_]{2,63}", self.name)
                is None or not isinstance(self.description, str)
                or not 1 <= len(self.description.encode()) <= 4096):
            raise ToolError("invalid_tool_spec")
        schema = _data(self.input_schema)
        if not isinstance(schema, dict) or schema.get("type") != "object":
            raise ToolError("invalid_tool_schema")
        canonical_arguments(schema)
        object.__setattr__(self, "input_schema", _freeze(schema))


class ToolCatalog:
    def __init__(self, definitions: Sequence[ToolDefinition], specs: Sequence[ToolSpec], *,
                 authority: ApprovalAuthority) -> None:
        self.authority = authority
        self._definitions = {tool.name: tool for tool in definitions}
        self._specs = {spec.name: spec for spec in specs}
        if (len(self._definitions) != len(definitions) or len(self._specs) != len(specs)
                or self._definitions.keys() != self._specs.keys()):
            raise ToolError("tool_catalog_mismatch")

    def definitions(self) -> tuple[ToolDefinition, ...]:
        return tuple(self._definitions.values())

    def specs(self) -> tuple[dict[str, Any], ...]:
        return tuple({"type": "function", "name": spec.name, "description": spec.description,
                      "inputSchema": _data(spec.input_schema)} for spec in self._specs.values())

    async def execute(
        self, tool_name: str, arguments: Mapping[str, Any], *, session_id: str, run_id: str,
        cancellation: asyncio.Event | None = None, offered_tools: Sequence[str] | None = None,
    ) -> ToolResult:
        cancellation = cancellation or asyncio.Event()
        _identity(session_id)
        _identity(run_id)
        tool = self._definitions.get(tool_name)
        if tool is None or (offered_tools is not None and tool_name not in offered_tools):
            return ToolResult(tool_name, False, False, error="tool_not_offered")
        operation_id = uuid4().hex
        try:
            snapshot = canonical_arguments(arguments)
            data = json.loads(snapshot)
            tool.validate(data)
        except Exception:
            return ToolResult(tool_name, False, False, error="invalid_tool_arguments",
                              operation_id=operation_id)
        if cancellation.is_set():
            return ToolResult(tool_name, False, False, error="execution_cancelled",
                              operation_id=operation_id)
        operation = ApprovalRequest(run_id, session_id, operation_id, tool_name, snapshot, "")
        operation = replace(operation, digest=operation_digest(operation))
        grant = None
        if tool.requires_confirmation:
            try:
                grant = await self.authority.request(operation, cancellation)
            except ToolError as error:
                return ToolResult(tool_name, False, False, error=str(error),
                                  operation_id=operation_id)
            if grant is None:
                return ToolResult(tool_name, False, False, error="approval_denied",
                                  operation_id=operation_id)
        dispatched = False
        try:
            tool.validate(data)
            if canonical_arguments(arguments) != snapshot or canonical_arguments(data) != snapshot:
                raise ToolError("tool_arguments_changed")
            if cancellation.is_set():
                raise ToolError("execution_cancelled")
            if grant is not None:
                with self.authority.execution_scope(operation, grant):
                    dispatched = True
                    result = await tool.execute(_freeze(data), cancellation)
            else:
                dispatched = True
                result = await tool.execute(_freeze(data), cancellation)
        except ToolError as error:
            return ToolResult(tool_name, False, False, error=str(error), operation_id=operation_id,
                              effect_status="unknown" if dispatched else "known")
        except asyncio.CancelledError:
            raise ToolCancelled(ToolResult(
                tool_name, False, False, error="execution_cancelled", operation_id=operation_id,
                effect_status="unknown" if dispatched else "known",
            )) from None
        except Exception:
            return ToolResult(tool_name, False, False, error="tool_execution_failed",
                              effect_status="unknown", operation_id=operation_id)
        if (not isinstance(result, ToolResult) or result.tool_name != tool_name
                or result.operation_id not in {None, operation_id} or cancellation.is_set()):
            return ToolResult(tool_name, False, False, error="invalid_tool_result",
                              effect_status="unknown", operation_id=operation_id)
        return replace(result, operation_id=operation_id)
