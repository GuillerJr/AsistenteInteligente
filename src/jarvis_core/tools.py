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
import threading
import time
from collections.abc import Callable, Iterator, Mapping, Sequence
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, replace
from enum import StrEnum
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


class HostPurpose(StrEnum):
    """Fixed host effects, never offered to a model or added to ToolCatalog."""

    BACKUP_CREATE = "backup.create"
    RESTORE_STAGE = "restore.stage"
    PROFILE_RESTORE_ACTIVATE = "profile.restore.activate"
    MEMORY_RESTORE_COPY_SCOPE = "memory.restore.copy_scope"
    TELEGRAM_RESTORE_RECONCILE = "telegram.restore.reconcile"
    DOCTOR_REPAIR_APPLY = "doctor.repair.apply"
    WORKSPACE_BACKUP_CREATE = "workspace.backup.create"
    WORKSPACE_RESTORE_STAGE = "workspace.restore.stage"
    RELEASE_UPDATE_APPLY = "release.update.apply"
    RELEASE_ROLLBACK_APPLY = "release.rollback.apply"


@dataclass(frozen=True)
class HostScope:
    profile_id: str
    generation: int

    def __post_init__(self) -> None:
        if (type(self.profile_id) is not str
                or re.fullmatch(r"[A-Za-z0-9_-]{1,128}", self.profile_id) is None
                or type(self.generation) is not int
                or not 0 <= self.generation <= 2**52 - 1):
            raise ToolError("invalid_host_scope")


@dataclass(frozen=True)
class HostApprovalRequest:
    scope: HostScope
    purpose: HostPurpose
    operation_id: str
    arguments_json: str
    digest: str


@dataclass(frozen=True)
class HostApprovalGrant:
    digest: str


@dataclass(frozen=True)
class PendingHostApproval:
    approval_id: str
    scope: HostScope
    purpose: HostPurpose
    operation_id: str
    arguments_json: str
    digest: str
    expires_at_ns: int


def canonical_host_arguments(arguments: Mapping[str, Any]) -> str:
    """Bound metadata only; the trusted purpose handler validates its schema.

    This is not a credential/content scanner. Callers must build metadata from
    approved fields (identities, counts, digests, destination), never raw stores,
    credentials, prompts or other private content.
    """
    try:
        encoded = canonical_arguments(arguments)
    except ToolError:
        raise
    except (ValueError, RecursionError):
        raise ToolError("invalid_host_arguments") from None
    if len(encoded.encode()) > 16384:
        raise ToolError("host_data_limit")
    return encoded


def host_operation_digest(operation: HostApprovalRequest) -> str:
    if (type(operation) is not HostApprovalRequest or type(operation.scope) is not HostScope
            or type(operation.purpose) is not HostPurpose
            or type(operation.operation_id) is not str
            or re.fullmatch(r"[a-f0-9]{32}", operation.operation_id) is None):
        raise ToolError("invalid_host_approval_request")
    # Revalidate the frozen scope even for deliberately constructed objects.
    HostScope(operation.scope.profile_id, operation.scope.generation)
    if type(operation.arguments_json) is not str:
        raise ToolError("invalid_host_arguments")
    try:
        size = len(operation.arguments_json.encode())
    except UnicodeError:
        raise ToolError("invalid_host_arguments") from None
    if size > 16384:
        raise ToolError("host_data_limit")
    try:
        arguments = json.loads(operation.arguments_json)
    except (ValueError, TypeError, RecursionError):
        raise ToolError("invalid_host_arguments") from None
    if canonical_host_arguments(arguments) != operation.arguments_json:
        raise ToolError("noncanonical_host_arguments")
    identity = json.dumps({
        "domain": "jarvis.host.approval/1",
        "profile": operation.scope.profile_id,
        "generation": operation.scope.generation,
        "purpose": operation.purpose.value,
        "operation": operation.operation_id,
        "arguments_json": operation.arguments_json,
    }, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(identity.encode()).hexdigest()


class HostCancellation:
    """An irreversible cancellation latch usable by the owning loop and workers.

    set() fixes the thread event before scheduling waiter notification. There is
    deliberately no clear/rearm API. Workers need only the read-only is_set()
    protocol and never need an asyncio loop.
    """

    def __init__(self) -> None:
        self._event = threading.Event()
        self._lock = threading.Lock()
        self._waiters: set[tuple[asyncio.AbstractEventLoop, asyncio.Future]] = set()

    def is_set(self) -> bool:
        return self._event.is_set()

    def set(self) -> None:
        with self._lock:
            if self._event.is_set():
                return
            self._event.set()
            waiters = tuple(self._waiters)
        for loop, future in waiters:
            try:
                loop.call_soon_threadsafe(self._notify, future)
            except RuntimeError:
                # A closed waiter loop cannot use a grant; the latch stays set.
                pass

    @staticmethod
    def _notify(future: asyncio.Future) -> None:
        if not future.done():
            future.set_result(None)

    async def wait(self) -> None:
        loop = asyncio.get_running_loop()
        future = loop.create_future()
        waiter = loop, future
        with self._lock:
            if self._event.is_set():
                return
            self._waiters.add(waiter)
        try:
            await future
        finally:
            with self._lock:
                self._waiters.discard(waiter)


@dataclass
class _HostPending:
    view: PendingHostApproval
    operation: HostApprovalRequest
    cancellation: HostCancellation
    future: asyncio.Future
    epoch: int
    deadline: float


@dataclass
class _HostIssued:
    operation: HostApprovalRequest
    grant: HostApprovalGrant
    cancellation: HostCancellation
    epoch: int
    deadline: float


@dataclass
class _HostLease:
    authority: ApprovalAuthority
    issued: _HostIssued
    committed: bool = False
    active: bool = True

    def check(self, operation: HostApprovalRequest, *, commit: bool, continuing: bool) -> None:
        # to_thread propagates ContextVars but the same lease object/lock is used.
        # Holding this lock linearizes revoke/close/commit against the last guard.
        with self.authority._host_lock:
            if (not self.active or operation != self.issued.operation
                    or (continuing and not self.committed)
                    or (not continuing and self.committed)):
                raise ToolError("host_authority_mismatch")
            self.authority._check_host_issued_locked(self.issued)
            if commit:
                self.committed = True


_HOST_LEASE: ContextVar[_HostLease | None] = ContextVar("jarvis_host_authority", default=None)


def check_host_authority(operation: HostApprovalRequest, *, commit: bool = False) -> None:
    lease = _HOST_LEASE.get()
    if lease is None:
        raise ToolError("host_authority_required")
    if type(commit) is not bool:
        raise ToolError("invalid_host_commit")
    lease.check(operation, commit=commit, continuing=False)


def revalidate_committed_host_authority(operation: HostApprovalRequest) -> None:
    """Continue the original bounded effect, never commit again or renew TTL."""
    lease = _HOST_LEASE.get()
    if lease is None:
        raise ToolError("host_authority_required")
    lease.check(operation, commit=False, continuing=True)


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


def current_tool_operation() -> ApprovalRequest:
    """Trusted adapter context, never a grant from model-controlled arguments."""
    lease = _LEASE.get()
    if lease is None or not lease.active:
        raise ToolError("tool_authority_required")
    lease.authority._check_issued(lease.issued)
    return lease.issued.operation


def check_tool_authority(
    tool_name: str, arguments: Mapping[str, Any], *, commit: bool = False,
) -> None:
    lease = _LEASE.get()
    if lease is None:
        raise ToolError("tool_authority_required")
    lease.check(tool_name, arguments, commit)


def revalidate_committed_tool_authority(
    tool_name: str, arguments: Mapping[str, Any],
) -> None:
    """Continue one bounded operation without granting or renewing authority.

    This does not allow a second commit. The original committed lease must still
    be active, exact, uncancelled, unexpired and at its original revocation epoch.
    """
    lease = _LEASE.get()
    if lease is None:
        raise ToolError("tool_authority_required")
    if (not lease.active or not lease.committed
            or lease.issued.operation.tool_name != tool_name
            or lease.issued.operation.arguments_json != canonical_arguments(arguments)):
        raise ToolError("tool_authority_mismatch")
    lease.authority._check_issued(lease.issued)


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
        self._host_lock = threading.RLock()
        self._host_scope: HostScope | None = None
        self._host_epoch = 0
        self._host_pending: dict[str, _HostPending] = {}
        self._host_issued: dict[int, _HostIssued] = {}
        self._host_active: dict[int, _HostLease] = {}
        self._host_seen: set[tuple[str, str]] = set()

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

    def _capacity(self) -> int:
        with self._host_lock:
            # A decided ticket and its grant are one request, not two.
            return (sum(not entry.future.done() for entry in self._pending.values())
                    + len(self._issued)
                    + sum(not entry.future.done() for entry in self._host_pending.values())
                    + len(self._host_issued) + len(self._host_active))

    def _sweep(self) -> None:
        self._sweep_host()
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
        *, on_pending: Callable[[PendingApproval], None] | None = None,
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
        if len(self._seen_operations) + len(self._host_seen) >= 65536:
            raise ToolError("approval_operation_capacity")
        if self._capacity() >= self.max_pending:
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
        if on_pending is not None:
            try:
                on_pending(view)
            except BaseException:
                self._pending.pop(approval_id, None)
                future.cancel()
                raise
        cancelled = asyncio.create_task(cancellation.wait())
        delivered = False

        def discard_unacknowledged() -> None:
            # The same operation_id may legitimately exist in another session or
            # run. Only this request's exact future/grant may be discarded.
            if not future.done() or future.cancelled():
                return
            unacknowledged = future.result()
            if unacknowledged is None:
                return
            issued = self._issued.get(id(unacknowledged))
            if (issued is not None and issued.grant is unacknowledged
                    and issued.operation == operation
                    and issued.grant.digest == operation.digest):
                self._issued.pop(id(unacknowledged), None)

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
        except asyncio.CancelledError:
            cancellation.set()
            raise
        finally:
            self._pending.pop(approval_id, None)
            cancelled.cancel()
            if not delivered:
                discard_unacknowledged()
            try:
                await asyncio.gather(cancelled, return_exceptions=True)
            except asyncio.CancelledError:
                # A prepared return is not an acknowledged capability if the
                # caller is cancelled during the waiter's final cleanup.
                cancellation.set()
                discard_unacknowledged()
                raise

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

    def _check_host_issued_locked(self, issued: _HostIssued) -> None:
        # This guard intentionally has no _on_loop/get_running_loop dependency.
        if (self._closed or issued.cancellation.is_set()
                or time.monotonic() >= issued.deadline
                or self._host_scope != issued.operation.scope
                or self._host_epoch != issued.epoch):
            raise ToolError("host_authority_revoked")

    def _sweep_host(self) -> None:
        # Only the owning loop edits futures. Workers only inspect locked leases.
        with self._host_lock:
            for key, entry in tuple(self._host_pending.items()):
                if (self._closed or entry.cancellation.is_set()
                        or time.monotonic() >= entry.deadline
                        or self._host_scope != entry.operation.scope
                        or self._host_epoch != entry.epoch):
                    if not entry.future.done():
                        entry.future.set_result(None)
                    self._host_pending.pop(key, None)
            for key, issued in tuple(self._host_issued.items()):
                try:
                    self._check_host_issued_locked(issued)
                except ToolError:
                    self._host_issued.pop(key, None)

    def bind_host_scope(self, scope: HostScope) -> None:
        """Bind one profile; same scope is idempotent, newer generation revokes."""
        self._on_loop()
        if type(scope) is not HostScope:
            raise ToolError("invalid_host_scope")
        HostScope(scope.profile_id, scope.generation)
        with self._host_lock:
            if self._closed:
                raise ToolError("host_authority_closed")
            current = self._host_scope
            if current is not None:
                if current.profile_id != scope.profile_id:
                    raise ToolError("host_profile_mismatch")
                if current.generation > scope.generation:
                    raise ToolError("host_generation_stale")
                if current == scope:
                    return
            self._host_scope = scope
            self._host_epoch += 1
            self._sweep_host()

    def revoke_host(self, scope: HostScope) -> int:
        self._on_loop()
        if type(scope) is not HostScope:
            raise ToolError("invalid_host_scope")
        with self._host_lock:
            if scope != self._host_scope:
                raise ToolError("host_scope_mismatch")
            count = (len(self._host_pending) + len(self._host_issued)
                     + len(self._host_active))
            self._host_epoch += 1
            self._sweep_host()
            return count

    async def request_host(
        self, operation: HostApprovalRequest, cancellation: HostCancellation,
        *, deadline: float | None = None,
    ) -> HostApprovalGrant | None:
        self._on_loop()
        if type(cancellation) is not HostCancellation:
            raise ToolError("invalid_host_cancellation")
        if deadline is not None and (type(deadline) not in {int, float}
                or not math.isfinite(deadline)):
            raise ToolError("invalid_host_deadline")
        remaining = self.ttl if deadline is None else min(self.ttl, deadline - time.monotonic())
        if remaining <= 0:
            return None
        digest = host_operation_digest(operation)
        if operation.digest != digest:
            raise ToolError("host_approval_digest_mismatch")
        self._sweep()
        with self._host_lock:
            if self._closed or cancellation.is_set():
                return None
            if self._host_scope != operation.scope:
                raise ToolError("host_scope_mismatch")
            identity = operation.scope.profile_id, operation.operation_id
            if identity in self._host_seen:
                raise ToolError("approval_operation_replayed")
            if len(self._seen_operations) + len(self._host_seen) >= 65536:
                raise ToolError("approval_operation_capacity")
            if self._capacity() >= self.max_pending:
                raise ToolError("approval_capacity")
            self._host_seen.add(identity)
            approval_id = uuid4().hex
            future = asyncio.get_running_loop().create_future()
            view = PendingHostApproval(
                approval_id, operation.scope, operation.purpose, operation.operation_id,
                operation.arguments_json, operation.digest,
                time.time_ns() + int(remaining * 1_000_000_000),
            )
            entry = _HostPending(view, operation, cancellation, future,
                                 self._host_epoch, time.monotonic() + remaining)
            self._host_pending[approval_id] = entry
        cancelled = asyncio.create_task(cancellation.wait())
        delivered = False
        try:
            done, _ = await asyncio.wait((future, cancelled), timeout=remaining,
                                         return_when=asyncio.FIRST_COMPLETED)
            if cancelled in done or cancellation.is_set() or future not in done:
                return None
            grant = future.result()
            if grant is not None:
                with self._host_lock:
                    issued = self._host_issued.get(id(grant))
                    if issued is None:
                        return None
                    self._check_host_issued_locked(issued)
                    delivered = True
            return grant
        except asyncio.CancelledError:
            cancellation.set()
            raise
        finally:
            with self._host_lock:
                self._host_pending.pop(approval_id, None)
                if not delivered and future.done() and not future.cancelled():
                    grant = future.result()
                    if grant is not None:
                        self._host_issued.pop(id(grant), None)
            cancelled.cancel()
            try:
                await asyncio.gather(cancelled, return_exceptions=True)
            except asyncio.CancelledError:
                # Cancellation during the final hand-off is still unacknowledged.
                # Revoke the issued object even if the normal return was prepared.
                cancellation.set()
                with self._host_lock:
                    if future.done() and not future.cancelled():
                        unacknowledged = future.result()
                        if unacknowledged is not None:
                            self._host_issued.pop(id(unacknowledged), None)
                raise

    def pending_host(self, scope: HostScope) -> tuple[PendingHostApproval, ...]:
        self._on_loop()
        if type(scope) is not HostScope:
            raise ToolError("invalid_host_scope")
        self._sweep()
        with self._host_lock:
            if scope != self._host_scope:
                return ()
            return tuple(entry.view for entry in self._host_pending.values()
                         if not entry.future.done() and entry.operation.scope == scope)

    def decide_host(
        self, approval_id: str, *, scope: HostScope, digest: str, allow: bool,
    ) -> bool:
        """For the authenticated native owner endpoint; not a model decision."""
        self._on_loop()
        if (type(allow) is not bool or type(scope) is not HostScope
                or type(approval_id) is not str
                or re.fullmatch(r"[a-f0-9]{32}", approval_id) is None
                or type(digest) is not str or re.fullmatch(r"[a-f0-9]{64}", digest) is None):
            raise ToolError("invalid_host_approval_decision")
        self._sweep()
        with self._host_lock:
            entry = self._host_pending.get(approval_id)
            if (entry is None or entry.future.done() or entry.operation.scope != scope
                    or entry.operation.digest != digest):
                return False
            grant = HostApprovalGrant(digest) if allow else None
            if grant is not None:
                self._host_issued[id(grant)] = _HostIssued(
                    entry.operation, grant, entry.cancellation, entry.epoch, entry.deadline,
                )
            entry.future.set_result(grant)
            return True

    @contextmanager
    def execution_host(
        self, operation: HostApprovalRequest, grant: HostApprovalGrant,
    ) -> Iterator[None]:
        self._on_loop()
        with self._host_lock:
            issued = self._host_issued.pop(id(grant), None)
            if (issued is None or issued.grant is not grant or issued.operation != operation
                    or type(grant) is not HostApprovalGrant
                    or operation.digest != host_operation_digest(operation)):
                raise ToolError("host_authority_invalid")
            self._check_host_issued_locked(issued)
            lease = _HostLease(self, issued)
            self._host_active[id(lease)] = lease
        token = _HOST_LEASE.set(lease)
        try:
            yield
        finally:
            with self._host_lock:
                lease.active = False
                self._host_active.pop(id(lease), None)
            _HOST_LEASE.reset(token)

    def check_host(self, operation: HostApprovalRequest, *, commit: bool = False) -> None:
        lease = _HOST_LEASE.get()
        if lease is None or lease.authority is not self:
            raise ToolError("host_authority_required")
        check_host_authority(operation, commit=commit)

    def revalidate_committed_host(self, operation: HostApprovalRequest) -> None:
        lease = _HOST_LEASE.get()
        if lease is None or lease.authority is not self:
            raise ToolError("host_authority_required")
        revalidate_committed_host_authority(operation)

    async def close(self) -> None:
        self._on_loop()
        with self._host_lock:
            self._closed = True
            self._sweep()
            self._issued.clear()
            self._host_issued.clear()


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
                or result.operation_id not in {None, operation_id}
                or (cancellation.is_set() and not (
                    not result.succeeded and result.effect_status == "unknown"))):
            return ToolResult(tool_name, False, False, error="invalid_tool_result",
                              effect_status="unknown", operation_id=operation_id)
        return replace(result, operation_id=operation_id)
