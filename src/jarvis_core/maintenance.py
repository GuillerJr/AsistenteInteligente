"""Native host maintenance with exact reviews and the central approval authority.

Requests return a job identity promptly. The coordinator owns approval waits,
workers and their cleanup; neither an IPC timeout nor closing a panel abandons
them. Explicit local admission never starts another profile or grants model tools.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
import time
from dataclasses import dataclass, field, replace
from pathlib import Path
from uuid import uuid4

from jarvis_core.activation_management import ActivationManagement, _Prepared
from jarvis_core.cold_capture_cut import CapturedProfileCut
from jarvis_core.doctor import DoctorContext, DoctorInventory, inspect_report
from jarvis_core.host_profile import freeze_sources
from jarvis_core.maintenance_journal import MaintenanceJournal, wire
from jarvis_core.memory_management import MemoryManagement, _MemoryRead
from jarvis_core.protocol import RpcError
from jarvis_core.recovery import (
    DISARMS,
    EXCLUSIONS,
    NewTarget,
    RecoveryContext,
    RecoveryError,
    RecoveryLimits,
    RecoveryReceipt,
    canonical,
    create_backup,
    restore_new,
    verify_backup,
)
from jarvis_core.recovery_fs import Directory, RecoveryFsError, basename
from jarvis_core.tools import (
    HostApprovalRequest,
    HostCancellation,
    HostPurpose,
    canonical_host_arguments,
    host_operation_digest,
)

_ID = re.compile(r"[a-f0-9]{32}\Z")
_HASH = re.compile(r"[a-f0-9]{64}\Z")
def _absent_target(target):
    with Directory(target.parent, private=False, expected=target.parent_identity) as directory:
        try:
            os.stat(target.name, dir_fd=directory.fd, follow_symlinks=False)
        except FileNotFoundError:
            return
        raise RecoveryFsError("recovery_destination_exists")


def _params(value, allowed, required=()):
    if type(value) is not dict or set(value) - set(allowed) or set(required) - set(value):
        raise RpcError(-32602, "Los parámetros de mantenimiento no son válidos.")


def _id(value):
    if type(value) is not str or _ID.fullmatch(value) is None:
        raise RpcError(-32602, "La identidad de mantenimiento no es válida.")
    return value


def _path(value):
    try:
        valid = type(value) is str and len(value.encode()) <= 4096 and "\0" not in value
    except UnicodeError:
        valid = False
    if not valid:
        raise RpcError(-32602, "Selecciona una carpeta absoluta válida.")
    result = Path(value)
    if not result.is_absolute() or ".." in result.parts or len(result.parts) > 64:
        raise RpcError(-32602, "Selecciona una carpeta absoluta válida.")
    return result


def _code(error):
    # Never return exception text: it can include a path, SQL or external data.
    code = getattr(error, "code", None)
    if type(code) is str and re.fullmatch(
        r"(?:memory|activation|recovery|snapshot|doctor|host|profile|maintenance)_[a-z0-9_]{1,100}",
        code
    ):
        return code
    return "maintenance_failed"


@dataclass(frozen=True)
class _Review:
    review_id: str
    kind: str
    digest: str
    scope: object
    fingerprint: object
    frozen: object
    target: NewTarget
    metadata_json: bytes
    expires: float
    verification: object = None
    backup_path: Path | None = None

    @property
    def metadata(self):
        # Approval metadata is immutable in the review; callers receive copies.
        return json.loads(self.metadata_json)


@dataclass
class _Operation:
    operation_id: str
    kind: str
    scope: object
    phase: str = "quiescing"
    stage: str | None = None
    cancellation: HostCancellation = field(default_factory=HostCancellation)
    task: asyncio.Task | None = None
    result: dict | None = None
    error_code: str | None = None
    receipt: object = None
    receipt_durable: bool = False
    review_id: str | None = None
    review_digest: str | None = None
    cleanup_owner: object = field(default=None, repr=False)
    memory_cut: object = field(default=None, repr=False)
    capture_cut: object = field(default=None, repr=False)

    def snapshot(self):
        return {"operation_id": self.operation_id, "kind": self.kind,
                "scope": wire(self.scope), "review_id": self.review_id,
                "review_digest": self.review_digest,
                "phase": self.phase, "stage": self.stage, "result": self.result,
                "error_code": self.error_code, "receipt": wire(self.receipt),
                "receipt_durable": self.receipt_durable,
                "worker_pending": self.task is not None and not self.task.done(),
                "cancellation_requested": self.cancellation.is_set(),
                "cleanup_owner_pending": self.cleanup_owner is not None}


@dataclass(frozen=True)
class _Verified:
    path: Path
    verification: object


def _receipt_phase(receipt):
    if receipt.cleanup_pending:
        return "cleanup_pending"
    if receipt.status == "unknown":
        return "publication_unknown"
    if receipt.effect_status == "local_admission_published":
        return "activated_not_opened"
    if receipt.status == "completed":
        return "staged_disarmed" if receipt.effect_status == "new_disarmed_profile" else "complete"
    return receipt.status if receipt.status in {"cancelled", "failed"} else "publication_unknown"


class MaintenanceCoordinator(MemoryManagement, ActivationManagement):
    def __init__(self, service, gate, *, clock=time.monotonic):
        self.service, self.gate, self.clock = service, gate, clock
        self.authority = service.authority
        self.journal = MaintenanceJournal(service.directory)
        self._reviews: dict[str, _Review] = {}
        self._operations: dict[str, _Operation] = {}
        self._verification: dict[str, tuple[Path, object]] = {}
        self._closing = False
        self._initialize_memory_limits()

    def handlers(self):
        methods = {"maintenance.status": self.status, "maintenance.pending": self.pending,
                   "maintenance.decide": self.decide, "maintenance.cancel": self.cancel,
                   "backup.review": self.review_backup, "backup.create": self.backup,
                   "backup.verify": self.verify, "restore.review": self.review_restore,
                   "restore.stage": self.restore, "doctor.report": self.doctor}
        methods.update(self.activation_handlers())
        methods.update(self.memory_handlers())

        def safe(callback):
            async def invoke(params):
                try:
                    return await callback(params)
                except RpcError:
                    raise
                except Exception as error:
                    raise RpcError(-32040, _code(error)) from None

            return invoke

        return {name: safe(callback) for name, callback in methods.items()}

    def _current(self):
        if self._closing or self.service._closing:
            raise RpcError(-32040, "Jarvis está cerrando el mantenimiento.")
        self.gate.lease.assert_current()
        scope = self.gate.lease.scope
        self.authority.bind_host_scope(scope)
        return scope

    def _capacity(self, *, allow_cleanup=False):
        self._current()
        if any(row.task is not None and not row.task.done()
               for row in self._operations.values()):
            raise RpcError(-32040, "Espera a que termine el mantenimiento actual.")
        if not allow_cleanup and any(row.cleanup_owner is not None
                                     for row in self._operations.values()):
            raise RpcError(-32040, "Confirma primero la limpieza del mantenimiento anterior.")
        while len(self._operations) >= 16:
            key = next((key for key, row in self._operations.items()
                        if row.cleanup_owner is None
                        and row.phase not in {"publication_unknown", "cleanup_pending",
                                             "interrupted_unknown"}
                        and getattr(row.receipt, "status", None) != "unknown"), None)
            if key is None:
                raise RpcError(-32040, "Se alcanzó el límite de operaciones sin resolver.")
            self._operations.pop(key)
        now = self.clock()
        self._reviews = {key: row for key, row in self._reviews.items() if row.expires > now}
        if len(self._reviews) >= 16:
            self._reviews.pop(next(iter(self._reviews)))

    def _target(self, params, forbidden):
        parent = _path(params["destination_parent"])
        name = basename(params["name"])
        destination = parent / name
        for root in forbidden:
            if (destination == root or destination.is_relative_to(root)
                    or Path(root).is_relative_to(destination)):
                raise RecoveryFsError("recovery_destination_overlaps_source")
        with Directory(parent, private=False) as directory:
            # Do not enumerate its siblings or read an existing destination.
            try:
                os.stat(name, dir_fd=directory.fd, follow_symlinks=False)
            except FileNotFoundError:
                pass
            else:
                raise RecoveryFsError("recovery_destination_exists")
            return NewTarget(parent, directory.identity, name, tuple(forbidden))

    def _make_review(self, kind, frozen, target, *, verification=None, backup_path=None):
        metadata = {
            "kind": kind, "profile_id": frozen.scope.profile_id,
            "generation": frozen.scope.generation,
            "source": wire(frozen.metadata),
            "destination_parent": str(target.parent), "destination_name": target.name,
            "destination_identity": wire(target.parent_identity),
            "excluded": list(EXCLUSIONS),
            "disarms": list(DISARMS) if kind == "restore.stage" else [],
            "workspace_files_included": False, "credentials_included": False,
            "activation_included": False, "authenticity": "not_established",
            "content_warning": "user_content_may_contain_secrets",
        }
        if verification is not None:
            metadata["manifest_sha256"] = verification.manifest_sha256
            metadata["artifact_id"] = verification.artifact_id
            metadata["source_artifact_identity"] = wire(verification.root_identity)
        if kind == "backup.create" and frozen.sqlite_recovery:
            metadata["sqlite_recovery"] = wire(frozen.sqlite_recovery)
        encoded = canonical(metadata)
        digest = hashlib.sha256(encoded).hexdigest()
        row = _Review(uuid4().hex, kind, digest, frozen.scope, frozen.fingerprint,
                      frozen, target, encoded, self.clock() + 120,
                      verification, backup_path)
        self._reviews[row.review_id] = row
        return {"review_id": row.review_id, "review_digest": row.digest,
                "expires_in_seconds": 120, "metadata": metadata}

    async def review_backup(self, params):
        _params(params, {"destination_parent", "name"}, {"destination_parent", "name"})
        self._capacity()
        with self.gate.exclusive() as cut:
            frozen = freeze_sources(self.service, cut)
            if any(row["disposition"] == "unavailable" for row in frozen.metadata):
                raise RecoveryFsError("profile_lazy_source_unavailable")
            # A review stays metadata-only, but must not offer a physically
            # oversized family that the native DTO or worker cannot admit.
            frozen._check_image_budget(RecoveryLimits(), metadata_only=True)
            target = self._target(params, frozen.forbidden_roots)
            return self._make_review("backup.create", frozen, target)

    async def review_restore(self, params):
        _params(params, {"verification_id", "destination_parent", "name"},
                {"verification_id", "destination_parent", "name"})
        self._capacity()
        item = self._verification.get(_id(params["verification_id"]))
        if item is None:
            raise RpcError(-32040, "Verifica primero el respaldo que quieres restaurar.")
        path, verified = item
        with self.gate.exclusive() as cut:
            frozen = freeze_sources(self.service, cut)
            target = self._target(params, (*frozen.forbidden_roots, path))
            return self._make_review("restore.stage", frozen, target,
                                     verification=verified, backup_path=path)

    def _exact_review(self, params, kind):
        _params(params, {"review_id", "review_digest"}, {"review_id", "review_digest"})
        identity = _id(params["review_id"])
        digest = params["review_digest"]
        if type(digest) is not str or _HASH.fullmatch(digest) is None:
            raise RpcError(-32602, "La revisión no es válida.")
        for row in self._operations.values():
            if row.review_id == identity:
                if row.review_digest != digest or row.kind != kind:
                    raise RpcError(-32040, "La revisión cambió. Vuelve a prepararla.")
                return None, row
        review = self._reviews.get(identity)
        if (review is None or review.kind != kind or review.digest != digest
                or review.expires <= self.clock()):
            raise RpcError(-32040, "La revisión cambió o caducó. Vuelve a prepararla.")
        return review, None

    async def backup(self, params):
        review, old = self._exact_review(params, "backup.create")
        if old is not None:
            return old.snapshot()
        return self._start_effect(review).snapshot()

    async def restore(self, params):
        review, old = self._exact_review(params, "restore.stage")
        if old is not None:
            return old.snapshot()
        return self._start_effect(review).snapshot()

    def _start_effect(self, review):
        self._capacity()
        if review.scope != self.gate.lease.scope:
            raise RpcError(-32040, "El perfil cambió. Vuelve a preparar la revisión.")
        with self.gate.exclusive(expected_scope=review.scope,
                                 expected_fingerprint=review.fingerprint) as cut:
            review.frozen.assert_current(cut, full=True)
            _absent_target(review.target)
        operation = _Operation(uuid4().hex, review.kind, review.scope, phase="awaiting_owner",
                               review_id=review.review_id, review_digest=review.digest)
        # Consumption is permanent: eviction/expiry can cause a missing review,
        # never another execution. Bounded idempotent responses live in operations.
        self.journal.record(operation.operation_id, operation.kind, review.digest,
                            operation.phase)
        self._reviews.pop(review.review_id, None)
        self._operations[operation.operation_id] = operation
        operation.task = asyncio.create_task(self._effect(operation, review))
        return operation

    async def _effect(self, operation, review):
        capture = None
        try:
            request = HostApprovalRequest(
                review.scope, HostPurpose(review.kind), operation.operation_id,
                canonical_host_arguments({"review_id": review.review_id,
                                          "review_digest": review.digest,
                                          "review": review.metadata}), "",
            )
            request = replace(request, digest=host_operation_digest(request))
            grant = await self.authority.request_host(request, operation.cancellation)
            if grant is None:
                if operation.cancellation.is_set():
                    operation.phase = "cancelled"
                elif self.clock() >= review.expires:
                    operation.phase = "failed"
                    operation.error_code = "maintenance_review_expired"
                elif self.gate.lease.scope != review.scope:
                    operation.phase = "failed"
                    operation.error_code = "maintenance_review_changed"
                else:
                    operation.phase = "denied"
                return
            operation.phase = "quiescing"
            # The ContextVar always exits in this Task. The ordinary cut may
            # outlive it if a private WAL reader cannot confirm cleanup.
            with self.authority.execution_host(request, grant):
                capture = CapturedProfileCut(
                    self.gate, expected_scope=review.scope,
                    expected_fingerprint=review.fingerprint)
                operation.capture_cut = capture
                cut = capture.cut
                # Restore validates only its selected artifact. It must not
                # initialize or require unrelated cold stores of the active host.
                if review.kind != "backup.create":
                    review.frozen.assert_current(cut, full=True)
                committed = False

                def guard(stage, write=False):
                    nonlocal committed
                    cut.assert_current()
                    if write:
                        review.frozen.assert_current(cut)
                    if committed:
                        self.authority.revalidate_committed_host(request)
                    else:
                        self.authority.check_host(request, commit=write)
                        if write:
                            committed = True
                    operation.stage = stage

                context = RecoveryContext(
                    operation.operation_id, review.scope.profile_id, review.scope.generation,
                    guard, operation.cancellation, min(self.clock() + 20, review.expires),
                    clock=self.clock,
                )
                operation.phase = "working"
                self.journal.record(operation.operation_id, operation.kind, review.digest,
                                    operation.phase)
                def callback():
                    if review.kind == "backup.create":
                        # Cold images are read/validated by the owned worker,
                        # never loaded in full on the RPC event loop.
                        try:
                            bundle = review.frozen.bind(cut, context=context)
                        except Exception as error:
                            # No backup target has been published. Private WAL
                            # staging is distinct and may still require custody.
                            code = _code(error)
                            owner = getattr(error, "owner", None)
                            pending = owner is not None and not owner.closed
                            receipt = RecoveryReceipt(
                                operation.operation_id, review.kind,
                                "cancelled" if code in {"recovery_cancelled",
                                                       "recovery_deadline_exceeded"} else "failed",
                                "no_published_effect", review.target.name, error_code=code,
                                cleanup_pending=pending,
                                cleanup_error_code="profile_wal_cleanup_pending"
                                if pending else None,
                            )
                            failure = RecoveryError(code, receipt)
                            failure.owner = owner
                            raise failure from None
                        return create_backup(bundle, review.target, context)
                    return restore_new(review.backup_path, review.verification,
                                       review.target, context)

                outcome = await self.gate.run_worker(
                    cut, callback, operation.cancellation
                )
                if outcome.error is not None:
                    owner = getattr(outcome.error, "owner", None)
                    if owner is not None:
                        capture.retain(owner)
                    raise outcome.error
                operation.receipt = outcome.value
                operation.phase = _receipt_phase(operation.receipt)
        except asyncio.CancelledError:
            operation.cancellation.set()
            operation.phase = "cancelled"
        except Exception as error:
            operation.receipt = getattr(error, "receipt", None)
            operation.error_code = _code(error)
            if operation.receipt is not None:
                operation.phase = _receipt_phase(operation.receipt)
            else:
                operation.phase = "failed"
        finally:
            self._finish_capture(operation, capture)
            self._save_effect(operation)

    def _save_effect(self, operation):
        try:
            self.journal.record(operation.operation_id, operation.kind, operation.review_digest,
                                operation.phase, operation.receipt)
            operation.receipt_durable = True
        except Exception:
            operation.receipt_durable = False
            operation.error_code = "maintenance_receipt_unconfirmed"
            operation.phase = ("cleanup_pending" if operation.cleanup_owner is not None else
                               "publication_unknown" if operation.receipt else "failed")

    def _finish_capture(self, operation, capture):
        if capture is None or capture.released:
            return
        if capture.owner is not None and not capture.owner.closed:
            operation.cleanup_owner = capture
            operation.phase = "cleanup_pending"
            return
        try:
            capture.release()
            operation.capture_cut = None
        except Exception as error:
            operation.cleanup_owner = capture
            operation.phase = "cleanup_pending"
            operation.error_code = _code(error)

    async def cleanup(self, params):
        _params(params, {"operation_id"}, {"operation_id"})
        operation = self._operations.get(_id(params["operation_id"]))
        if operation is None or operation.capture_cut is None:
            return await super().cleanup(params)
        self._current()
        if operation.task is not None and not operation.task.done():
            return operation.snapshot()
        if operation.cleanup_owner is not None:
            operation.task = asyncio.create_task(self._cleanup_capture(operation))
        return operation.snapshot()

    async def _cleanup_owner(self, operation):
        # Shutdown routes the same retained reader without acquiring a second
        # admission, cut, authority ContextVar or one-use operation grant.
        if operation.capture_cut is not None:
            return await self._cleanup_capture(operation)
        return await super()._cleanup_owner(operation)

    async def _cleanup_capture(self, operation):
        try:
            capture = operation.capture_cut
            if capture is None or operation.cleanup_owner is not capture:
                raise RecoveryFsError("profile_wal_owner_mismatch")
            operation.stage = "cold_wal_cleanup"
            outcome = await self.gate.run_worker(capture.cut, capture.cleanup,
                                                 operation.cancellation)
            if outcome.error is not None:
                raise outcome.error
            capture.release()
            operation.cleanup_owner = None
            operation.capture_cut = None
            # Preserve the original receipt, including its historical cleanup
            # flag. Retiring a private reader does not retry a backup or read.
            status = getattr(operation.receipt, "status", None)
            operation.phase = (status if status in {"failed", "cancelled"} else
                               "cancelled" if operation.cancellation.is_set() else "failed")
        except Exception as error:
            operation.error_code = _code(error)
            operation.phase = "cleanup_pending"
        finally:
            if operation.kind in {"backup.create", "restore.stage"}:
                self._save_effect(operation)

    def _read_operation(self, kind, callback, *, allow_cleanup=False):
        self._capacity(allow_cleanup=allow_cleanup)
        operation = _Operation(uuid4().hex, kind, self.gate.lease.scope)
        self._operations[operation.operation_id] = operation
        operation.task = asyncio.create_task(self._read(operation, callback))
        return operation.snapshot()

    async def _read(self, operation, callback):
        capture = None
        try:
            capture = CapturedProfileCut(self.gate)
            operation.capture_cut = capture
            cut = capture.cut
            operation.scope = cut.scope
            operation.phase = "working"
            outcome = await self.gate.run_worker(
                cut, lambda: callback(cut, operation), operation.cancellation
            )
            if outcome.error is not None:
                owner = getattr(outcome.error, "owner", None)
                if owner is not None:
                    capture.retain(owner)
                raise outcome.error
            result = outcome.value
            if isinstance(result, _MemoryRead):
                if operation.cancellation.is_set():
                    raise RecoveryFsError("recovery_cancelled")
                operation.result = self._publish_memory_result(result)
            elif isinstance(result, _Prepared):
                if operation.cancellation.is_set():
                    raise RecoveryFsError("recovery_cancelled")
                operation.result = {"review": self._publish_activation_review(result)}
            elif isinstance(result, _Verified):
                identity = uuid4().hex
                while len(self._verification) >= 8:
                    self._verification.pop(next(iter(self._verification)))
                self._verification[identity] = (result.path, result.verification)
                operation.result = {"verification_id": identity,
                                    "verification": wire(result.verification)}
            else:
                operation.result = result
            operation.phase = "complete"
        except asyncio.CancelledError:
            operation.cancellation.set()
            operation.phase = "cancelled"
        except Exception as error:
            operation.error_code = _code(error)
            operation.phase = ("cancelled" if operation.cancellation.is_set()
                               or operation.error_code in {"recovery_cancelled",
                                                           "recovery_deadline_exceeded"}
                               else "failed")
        finally:
            self._finish_capture(operation, capture)

    async def verify(self, params):
        _params(params, {"backup_path", "expected_manifest_sha256"}, {"backup_path"})
        path = _path(params["backup_path"])
        digest = params.get("expected_manifest_sha256")
        if digest is not None and (type(digest) is not str or _HASH.fullmatch(digest) is None):
            raise RpcError(-32602, "El hash del manifiesto no es válido.")

        def check(cut, operation):
            context = RecoveryContext(
                operation.operation_id, cut.scope.profile_id, cut.scope.generation,
                lambda stage, write=False: cut.assert_current(), operation.cancellation,
                time.monotonic() + 20,
            )
            verified = verify_backup(path, context, expected_manifest_sha256=digest)
            # This worker produces immutable values; publish the cache on the
            # owning loop only, after the gate has confirmed worker settlement.
            return _Verified(path, verified)

        return self._read_operation("backup.verify", check)

    async def doctor(self, params):
        _params(params, set())
        requested = self.clock()

        def check(cut, operation):
            frozen = freeze_sources(self.service, cut)
            context = DoctorContext(lambda stage, write=False: frozen.assert_current(cut),
                                    operation.cancellation, deadline=requested + 20,
                                    clock=self.clock)
            images = frozen.doctor_images(cut, context, RecoveryLimits())
            inventory = DoctorInventory(
                frozen.scope.profile_id, frozen.scope.generation, self.service.directory,
                (self.gate.lease.identity.device, self.gate.lease.identity.inode),
                "coherent_snapshot", configuration_metadata=frozen.configuration_metadata,
                roles=tuple(row["role"] for row in frozen.metadata)
                + ("configuration_portable",)
                + (("restore_record",) if self.gate.lease.admission is not None else ()),
                cut_id=cut.cut_id,
            )
            return {"report": inspect_report(inventory, context,
                                              sqlite_images=images).to_dict()}

        return self._read_operation("doctor.report", check)

    async def status(self, params):
        _params(params, {"operation_id"})
        self.gate.lease.assert_current()
        if "operation_id" in params:
            operation = self._operations.get(_id(params["operation_id"]))
            if operation is None:
                raise RpcError(-32040, "No existe esa operación en este servicio.")
            return operation.snapshot()
        return {"operations": [row.snapshot() for row in self._operations.values()],
                "previous_operations": list(self.journal.previous),
                "profile_id": self.gate.lease.scope.profile_id,
                "generation": self.gate.lease.scope.generation,
                "blockers": list(self.gate.blockers()),
                "closing": self._closing}

    async def pending(self, params):
        _params(params, set())
        scope = self._current()
        return {"pending": [wire(row) for row in self.authority.pending_host(scope)],
                "profile_id": scope.profile_id, "generation": scope.generation}

    async def decide(self, params):
        _params(params, {"approval_id", "digest", "allow", "profile_id", "generation"},
                {"approval_id", "digest", "allow", "profile_id", "generation"})
        scope = self._current()
        if (type(params["generation"]) is not int or type(params["profile_id"]) is not str
                or params["profile_id"] != scope.profile_id
                or params["generation"] != scope.generation):
            raise RpcError(-32040, "El perfil cambió. La aprobación ya no es válida.")
        accepted = self.authority.decide_host(
            params["approval_id"], scope=scope, digest=params["digest"], allow=params["allow"]
        )
        return {"accepted": accepted}

    async def cancel(self, params):
        _params(params, {"operation_id"}, {"operation_id"})
        row = self._operations.get(_id(params["operation_id"]))
        if row is None:
            raise RpcError(-32040, "No existe esa operación en este servicio.")
        if row.task is not None and not row.task.done():
            row.cancellation.set()
        return row.snapshot()

    async def aclose(self):
        self._closing = True
        tasks = []
        for row in self._operations.values():
            if row.task is not None and not row.task.done():
                row.cancellation.set()
                tasks.append(row.task)
        if tasks:
            _, pending = await asyncio.wait(tasks, timeout=25)
            if pending:
                raise TimeoutError("maintenance_cleanup_incomplete")
        for row in self._operations.values():
            if row.cleanup_owner is not None:
                callback = (self._cleanup_memory if row.kind == "memory.restore.copy_scope"
                            else self._cleanup_owner)
                row.task = asyncio.create_task(callback(row))
                _, pending = await asyncio.wait((row.task,), timeout=25)
                if pending or row.cleanup_owner is not None:
                    raise TimeoutError("maintenance_cleanup_incomplete")
