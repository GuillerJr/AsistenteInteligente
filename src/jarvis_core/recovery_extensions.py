"""Reviewed recovery extensions sharing the native host authority and exact cut.

Drivers prepare immutable bounded values in a worker. Only metadata crosses IPC;
approval never captures a new source, renews a grant or replays an unknown effect.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
from dataclasses import dataclass, replace
from uuid import uuid4

from jarvis_core.cold_capture_cut import CapturedProfileCut
from jarvis_core.protocol import RpcError
from jarvis_core.recovery import RecoveryContext, RecoveryReceipt, canonical
from jarvis_core.tools import (
    HostApprovalRequest,
    HostPurpose,
    canonical_host_arguments,
    host_operation_digest,
)


@dataclass(frozen=True)
class ExtensionPrepared:
    kind: str
    frozen: object
    target: object
    payload: object
    metadata: dict


@dataclass(frozen=True)
class ExtensionOutcome:
    receipt: RecoveryReceipt
    result: dict


@dataclass(frozen=True)
class ExtensionReview:
    review_id: str
    kind: str
    digest: str
    scope: object
    fingerprint: str
    frozen: object
    target: object
    payload: object
    metadata_json: bytes
    expires: float

    @property
    def metadata(self):
        return json.loads(self.metadata_json)


class RecoveryExtensions:
    def _publish_extension_review(self, prepared):
        # Retain at most one extension payload (32 MiB workspace / bounded SQLite
        # candidate). Expired reviews are never usable, even if a UI retained one.
        for key, value in tuple(self._reviews.items()):
            if isinstance(value, ExtensionReview):
                self._reviews.pop(key)
        metadata = {**prepared.metadata, "kind": prepared.kind,
                    "profile_id": prepared.frozen.scope.profile_id,
                    "generation": prepared.frozen.scope.generation}
        encoded = canonical(metadata)
        # Exactly the same bounded canonical form will be in the host request.
        canonical_host_arguments({"review_id": "0" * 32, "review_digest": "0" * 64,
                                  "review": metadata})
        review = ExtensionReview(
            uuid4().hex, prepared.kind, hashlib.sha256(encoded).hexdigest(),
            prepared.frozen.scope, prepared.frozen.fingerprint, prepared.frozen,
            prepared.target, prepared.payload, encoded, self.clock() + 120)
        self._reviews[review.review_id] = review
        return {"review_id": review.review_id, "review_digest": review.digest,
                "expires_in_seconds": 120, "metadata": metadata}

    def _extension_workers(self):
        return {**self.workspace_workers(), **self.repair_workers(), **self.release_workers()}

    def _extension_start(self, params, kind):
        from jarvis_core.maintenance import _Operation
        review, old = self._exact_review(params, kind)
        if old is not None:
            return old.snapshot()
        self._capacity()
        if not isinstance(review, ExtensionReview) or review.scope != self.gate.lease.scope:
            raise RpcError(-32040, "El perfil o la revisión cambiaron.")
        with self.gate.exclusive(expected_scope=review.scope,
                                 expected_fingerprint=review.fingerprint) as cut:
            review.frozen.assert_current(cut, full=True)
        operation = _Operation(uuid4().hex, kind, review.scope, phase="awaiting_owner",
                               review_id=review.review_id, review_digest=review.digest)
        self.journal.record(operation.operation_id, kind, review.digest, operation.phase)
        self._reviews.pop(review.review_id, None)
        self._operations[operation.operation_id] = operation
        operation.task = asyncio.create_task(self._extension_effect(operation, review))
        return operation.snapshot()

    async def _extension_effect(self, operation, review):
        from jarvis_core.maintenance import _code, _receipt_phase
        capture = None
        try:
            request = HostApprovalRequest(
                review.scope, HostPurpose(review.kind), operation.operation_id,
                canonical_host_arguments({"review_id": review.review_id,
                                          "review_digest": review.digest,
                                          "review": review.metadata}), "")
            request = replace(request, digest=host_operation_digest(request))
            grant = await self.authority.request_host(request, operation.cancellation)
            if grant is None:
                operation.phase = "cancelled" if operation.cancellation.is_set() else "denied"
                return
            with self.authority.execution_host(request, grant):
                capture = CapturedProfileCut(self.gate, expected_scope=review.scope,
                                             expected_fingerprint=review.fingerprint)
                operation.capture_cut = capture
                cut, committed = capture.cut, False

                def guard(stage, write=False):
                    nonlocal committed
                    review.frozen.assert_current(cut)
                    if committed:
                        self.authority.revalidate_committed_host(request)
                    else:
                        self.authority.check_host(request, commit=write)
                        committed = write
                    operation.stage = stage

                context = RecoveryContext(
                    operation.operation_id, review.scope.profile_id, review.scope.generation,
                    guard, operation.cancellation, min(self.clock() + 90, review.expires),
                    clock=self.clock)
                operation.phase = "working"
                self.journal.record(operation.operation_id, operation.kind,
                                    review.digest, operation.phase)
                worker = self._extension_workers()[review.kind]
                outcome = await self.gate.run_worker(
                    cut, lambda: worker(review, context, cut, operation), operation.cancellation)
                if outcome.error is not None:
                    owner = getattr(outcome.error, "owner", None)
                    if owner is not None and not owner.closed:
                        capture.retain(owner)
                    raise outcome.error
                if not isinstance(outcome.value, ExtensionOutcome):
                    raise ValueError("maintenance_extension_result_invalid")
                operation.receipt = outcome.value.receipt
                operation.result = outcome.value.result
                operation.phase = _receipt_phase(operation.receipt)
        except asyncio.CancelledError:
            operation.cancellation.set()
            operation.phase = "cancelled"
        except Exception as error:
            operation.error_code = _code(error)
            receipt = getattr(error, "receipt", None)
            # A domain receipt may have a different schema. Only the common DTO
            # enters the durable maintenance journal and native status parser.
            if isinstance(receipt, RecoveryReceipt):
                operation.receipt = receipt
            else:
                owner = getattr(error, "owner", None)
                unknown = (getattr(error, "effect_status", None) in {
                    "unknown", "publication_unknown"}
                    or getattr(owner, "publication_attempted", False)
                    or getattr(owner, "publication_unknown", False)
                    or getattr(receipt, "status", None) == "unknown")
                pending = owner is not None and not owner.closed
                operation.receipt = RecoveryReceipt(
                    operation.operation_id, review.kind,
                    "unknown" if unknown else "cancelled"
                    if operation.cancellation.is_set() else "failed",
                    "unknown" if unknown else "no_published_effect",
                    review.target.name if hasattr(review.target, "name") else "jarvis-release",
                    error_code=operation.error_code, cleanup_pending=pending,
                    cleanup_error_code="maintenance_cleanup_pending" if pending else None)
            operation.phase = _receipt_phase(operation.receipt)
        finally:
            self._finish_capture(operation, capture)
            self._save_effect(operation)
