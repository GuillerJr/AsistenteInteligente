"""Native local admission controls on the existing host authority and profile cut.

Readback establishes a current local pair, never the durability of a prior ACK.
No endpoint starts another profile, provider or channel, and cleanup never republishes.
"""
from __future__ import annotations

import asyncio
import hashlib
import time
from dataclasses import dataclass, replace
from uuid import uuid4

from jarvis_core.activation import (
    ActivationError,
    activate_prepared_profile,
    review_prepared_profile,
    validate_bootstrap_admission,
)
from jarvis_core.host_profile import assert_restored_namespace, freeze_sources
from jarvis_core.maintenance_journal import wire
from jarvis_core.protocol import RpcError
from jarvis_core.recovery import RecoveryContext, canonical
from jarvis_core.recovery_fs import Directory
from jarvis_core.tools import (
    HostApprovalRequest,
    HostPurpose,
    canonical_host_arguments,
    host_operation_digest,
)


@dataclass(frozen=True)
class _Prepared:
    core: object
    frozen: object
    expires: float


@dataclass(frozen=True)
class _ActivationReview:
    review_id: str
    kind: str
    digest: str
    scope: object
    fingerprint: str
    frozen: object
    core: object
    metadata_json: bytes
    expires: float

    @property
    def metadata(self):
        import json
        return json.loads(self.metadata_json)


class ActivationManagement:
    def activation_handlers(self):
        return {"profile.restore.review": self.review_activation,
                "profile.restore.activate": self.activate,
                "profile.restore.inspect": self.inspect_activation,
                "maintenance.cleanup": self.cleanup}

    def _publish_activation_review(self, result):
        if self.clock() >= result.expires:
            raise ActivationError("maintenance_review_expired")
        metadata = {
            **result.core.metadata(), "profile_id": result.frozen.scope.profile_id,
            "generation": result.frozen.scope.generation,
            "workspace_policy": "new_private_empty", "credentials_included": False,
            "authenticity": "not_established",
        }
        encoded = canonical(metadata)
        row = _ActivationReview(uuid4().hex, "profile.restore.activate",
                                hashlib.sha256(encoded).hexdigest(), result.frozen.scope,
                                result.frozen.fingerprint, result.frozen, result.core,
                                encoded, result.expires)
        self._reviews[row.review_id] = row
        return {"review_id": row.review_id, "review_digest": row.digest,
                "expires_in_seconds": max(0, min(120, int(row.expires - self.clock()))),
                "metadata": wire(metadata)}

    async def review_activation(self, params):
        from jarvis_core.maintenance import _params, _path
        _params(params, {"profile_path"}, {"profile_path"})
        path, requested = _path(params["profile_path"]), self.clock()

        def read(cut, operation):
            frozen = freeze_sources(self.service, cut)
            context = RecoveryContext(
                operation.operation_id, cut.scope.profile_id, cut.scope.generation,
                lambda stage, write=False: frozen.assert_current(cut),
                operation.cancellation, requested + 20, clock=self.clock,
            )
            # Reject physical reentrancy before target lock, approval or constructors.
            with Directory(path) as directory:
                if directory.identity == self.gate.lease.identity:
                    raise ActivationError("activation_target_is_current_profile")
            core = review_prepared_profile(path, context)
            return _Prepared(core, frozen, requested + 120)

        return self._read_operation("profile.restore.review", read)

    async def activate(self, params):
        from jarvis_core.maintenance import _Operation
        review, old = self._exact_review(params, "profile.restore.activate")
        if old is not None:
            return old.snapshot()
        self._capacity()
        if review.scope != self.gate.lease.scope:
            raise ActivationError("maintenance_review_changed")
        with self.gate.exclusive(expected_scope=review.scope,
                                 expected_fingerprint=review.fingerprint) as cut:
            review.frozen.assert_current(cut, full=True)
            with Directory(review.core.root, expected=review.core.root_identity) as directory:
                if directory.identity == self.gate.lease.identity:
                    raise ActivationError("activation_target_is_current_profile")
        operation = _Operation(uuid4().hex, review.kind, review.scope, phase="awaiting_owner",
                               review_id=review.review_id, review_digest=review.digest)
        self.journal.record(operation.operation_id, operation.kind, review.digest,
                            operation.phase)
        self._reviews.pop(review.review_id, None)
        self._operations[operation.operation_id] = operation
        operation.task = asyncio.create_task(self._activate(operation, review))
        return operation.snapshot()

    async def _activate(self, operation, review):
        from jarvis_core.maintenance import _code, _receipt_phase
        try:
            request = HostApprovalRequest(
                review.scope, HostPurpose.PROFILE_RESTORE_ACTIVATE, operation.operation_id,
                canonical_host_arguments({"review_id": review.review_id,
                                          "review_digest": review.digest,
                                          "review": review.metadata}), "")
            request = replace(request, digest=host_operation_digest(request))
            # Coordinator fake clock and authority real monotonic have distinct
            # domains. Transfer only the remaining restrictive budget, never renew it.
            deadline = time.monotonic() + max(0, review.expires - self.clock())
            grant = await self.authority.request_host(request, operation.cancellation,
                                                      deadline=deadline)
            if grant is None:
                operation.phase = ("cancelled" if operation.cancellation.is_set() else
                                   "failed" if self.clock() >= review.expires else "denied")
                if operation.phase == "failed":
                    operation.error_code = "maintenance_review_expired"
                return
            operation.phase = "quiescing"
            with self.authority.execution_host(request, grant), self.gate.exclusive(
                expected_scope=review.scope, expected_fingerprint=review.fingerprint
            ) as cut:
                review.frozen.assert_current(cut, full=True)
                committed = False

                def guard(stage, write=False):
                    nonlocal committed
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
                outcome = await self.gate.run_worker(
                    cut, lambda: activate_prepared_profile(review.core, context),
                    operation.cancellation)
                if outcome.error is not None:
                    raise outcome.error
                operation.receipt = outcome.value
                operation.phase = _receipt_phase(operation.receipt)
        except asyncio.CancelledError:
            operation.cancellation.set()
            operation.phase = "cancelled"
        except Exception as error:
            operation.receipt = getattr(error, "receipt", None)
            # Own the actual cleanup object before dropping any exception reference.
            operation.cleanup_owner = getattr(error, "owner", None)
            operation.error_code = _code(error)
            if operation.cleanup_owner is not None:
                operation.phase = "cleanup_pending"
            elif operation.receipt is not None:
                operation.phase = _receipt_phase(operation.receipt)
            else:
                operation.phase = "failed"
        finally:
            try:
                self.journal.record(operation.operation_id, operation.kind, review.digest,
                                    operation.phase, operation.receipt)
                operation.receipt_durable = True
            except Exception:
                operation.receipt_durable = False
                operation.error_code = "maintenance_receipt_unconfirmed"
                operation.phase = ("cleanup_pending" if operation.cleanup_owner is not None
                                   else "publication_unknown" if operation.receipt else "failed")

    async def inspect_activation(self, params):
        from jarvis_core.maintenance import _HASH, _params, _path
        _params(params, {"profile_path", "expected_marker_sha256", "expected_admission_sha256"},
                {"profile_path"})
        path = _path(params["profile_path"])
        expected = {key: params[key] for key in
                    ("expected_marker_sha256", "expected_admission_sha256") if key in params}
        if any(type(item) is not str or _HASH.fullmatch(item) is None
               for item in expected.values()):
            raise RpcError(-32602, "La identidad de la admisión no es válida.")

        def read(cut, operation):
            context = RecoveryContext(
                operation.operation_id, cut.scope.profile_id, cut.scope.generation,
                lambda stage, write=False: cut.assert_current(), operation.cancellation,
                requested + 20, clock=self.clock)
            context.begin()
            with Directory(path) as directory:
                identity = directory.identity
                try:
                    admission = validate_bootstrap_admission(
                        directory, check_current=lambda: context.checkpoint("activation_inspect"))
                except ActivationError as error:
                    if error.code != "profile_activation_required" or expected:
                        raise
                    state, admission = "activation_required", None
                else:
                    state = "local_present_valid" if admission else "not_restored_profile"
                assert_restored_namespace(directory, admission)
                for key, item in expected.items():
                    if (admission is None
                            or getattr(admission, key.removeprefix("expected_")) != item):
                        raise ActivationError("activation_expected_admission_mismatch")
                pending = any(row.cleanup_owner is not None for row in self._operations.values())
                context.checkpoint("activation_inspect_complete")
                return {"admission": {"profile_path": str(path), "root_identity": wire(identity),
                                      "state": state, "admission": wire(admission),
                                      "cleanup_owner_pending": pending,
                                      "open_available": admission is not None and not pending
                                      and identity != self.gate.lease.identity}}

        requested = self.clock()
        return self._read_operation("profile.restore.inspect", read, allow_cleanup=True)

    async def cleanup(self, params):
        from jarvis_core.maintenance import _id, _params
        _params(params, {"operation_id"}, {"operation_id"})
        self._current()
        row = self._operations.get(_id(params["operation_id"]))
        if row is None:
            raise RpcError(-32040, "No existe esa operación en este servicio.")
        if row.task is not None and not row.task.done():
            return row.snapshot()
        if row.cleanup_owner is None:
            return row.snapshot()
        # No new grant, no replay, no replacement receipt. Only this retained owner.
        row.task = asyncio.create_task(self._cleanup_owner(row))
        return row.snapshot()

    async def _cleanup_owner(self, operation):
        from jarvis_core.maintenance import _code
        try:
            # Cleanup touches only this owner's target descriptors, never active
            # source stores. A closing admission holds the host lease until its
            # real worker ends, including repeated cancellation of the awaiter.
            with self.gate.admit("activation.cleanup", closing=True):
                owner = operation.cleanup_owner
                worker = asyncio.create_task(asyncio.to_thread(owner.cleanup))
                while not worker.done():
                    try:
                        await asyncio.shield(worker)
                    except asyncio.CancelledError:
                        operation.cancellation.set()
                    except BaseException:
                        if not worker.done():
                            raise
                        break
                worker.result()
                if not owner.closed:
                    raise ActivationError("activation_cleanup_unconfirmed")
                operation.cleanup_owner = None
                # The physical receipt remains unknown, including its original
                # cleanup_pending evidence. RAM ownership is reported separately.
                operation.phase = "publication_unknown"
        except Exception as error:
            operation.error_code = _code(error)
            operation.phase = "cleanup_pending"
        finally:
            try:
                self.journal.record(operation.operation_id, operation.kind,
                                    operation.review_digest, operation.phase, operation.receipt)
                operation.receipt_durable = True
            except Exception:
                operation.receipt_durable = False
                operation.error_code = "maintenance_receipt_unconfirmed"
