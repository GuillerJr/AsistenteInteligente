"""Explicit local reconciliation of restored Telegram routes.

The historical bot ID is a label, not external account verification. A native
host decision changes one existing route only. Connection, reader activation,
provider choice and tool permissions remain separate; old events never replay.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import time
from dataclasses import dataclass, replace
from uuid import uuid4

from jarvis_core.cold_capture_cut import CapturedProfileCut
from jarvis_core.host_profile import HostProfileError, freeze_sources
from jarvis_core.maintenance_journal import wire
from jarvis_core.protocol import RpcError
from jarvis_core.recovery import canonical
from jarvis_core.state import SessionRef
from jarvis_core.telegram import TelegramError
from jarvis_core.tools import (
    HostApprovalRequest,
    HostPurpose,
    canonical_host_arguments,
    host_operation_digest,
)

_PURPOSE = "telegram.restore.reconcile"
_NO_EFFECTS = {
    "network_started": False,
    "provider_started": False,
    "grants_restored": False,
    "historical_events_replayed": False,
}


@dataclass(frozen=True)
class _ChannelReview:
    review_id: str
    kind: str
    digest: str
    scope: object
    fingerprint: str
    frozen: object
    metadata_json: bytes
    internal_hash: str
    expires: float

    @property
    def metadata(self):
        return json.loads(self.metadata_json)


class _ChannelMutationCut(CapturedProfileCut):
    changed = False

    def release(self):
        was_released = self.released
        super().release()
        if not was_released and self.changed:
            scope = self.gate.lease.advance_generation()
            if self.gate.on_generation is not None:
                self.gate.on_generation(scope)


class ChannelReconciliation:
    def channel_reconciliation_handlers(self):
        return {
            "telegram.restore.review": self.channel_restore_review,
            _PURPOSE: self.channel_restore_reconcile,
            "telegram.restore.inspect": self.channel_restore_inspect,
        }

    def _channel_current(self):
        self._current()
        self.service.profile_lease.check_bootstrap_ready()
        if self.service.profile_lease.admission is None:
            raise TelegramError("telegram_restore_profile_required")
        self.service._assert_workspace_current()
        self.service._telegram_require_stopped()

    def _channel_metadata(self, chat_id, expected_revision, enabled):
        self._channel_current()
        routes, internal_hash, configuration_hash = self.service._telegram_configuration()
        row = next((item for item in routes if item["chat_id"] == chat_id), None)
        bot_id = self.service._telegram_journal.bot_id
        if bot_id is None:
            raise TelegramError("telegram_restore_bot_required")
        if row is None or row["revision"] != expected_revision:
            raise TelegramError("telegram_reconciliation_changed")
        if row["enabled"] is enabled:
            raise TelegramError("telegram_reconciliation_no_change")
        agent = self.service.agent_profiles.get(row["agent_id"])
        record = self.service.store.get_session(SessionRef(row["session_id"]))
        if record.archived or record.agent_id != agent.agent_id:
            raise TelegramError("telegram_route_unavailable")
        admission = self.service.profile_lease.admission
        scope = self.gate.lease.scope
        return {
            "profile_id": scope.profile_id,
            "generation": scope.generation,
            "bot_id": bot_id,
            "chat_id": row["chat_id"],
            "agent_id": agent.agent_id,
            "agent_revision": agent.revision,
            "session_id": row["session_id"],
            "route_revision": row["revision"],
            "enabled_before": row["enabled"],
            "enabled_after": enabled,
            "workspace_id": self.service._workspace_id,
            "workspace_path": str(self.service._workspace_path),
            "configuration_hash": configuration_hash,
            "marker_sha256": admission.marker_sha256,
            "admission_sha256": admission.admission_sha256,
            **_NO_EFFECTS,
            "external_bot_verified": False,
        }, internal_hash

    async def channel_restore_review(self, params):
        from jarvis_core.maintenance import _params

        _params(params, {"chat_id", "expected_revision", "enabled"},
                {"chat_id", "expected_revision", "enabled"})
        if (type(params["chat_id"]) is not int or not 1 <= params["chat_id"] < 1 << 52
                or type(params["expected_revision"]) is not int
                or not 1 <= params["expected_revision"] < 1 << 52
                or type(params["enabled"]) is not bool):
            raise RpcError(-32602, "Selecciona una ruta y un cambio válidos.")
        self._capacity()
        requested = self.clock()
        # These existing, bounded service reads precede the cut. No await occurs
        # before the frozen fingerprint is captured; they do not read messages.
        metadata, internal_hash = self._channel_metadata(**params)
        with self.gate.exclusive() as cut:
            frozen = freeze_sources(self.service, cut)
            frozen.assert_current(cut, full=True)
            encoded = canonical(metadata)
            review = _ChannelReview(
                uuid4().hex, _PURPOSE, hashlib.sha256(encoded).hexdigest(),
                cut.scope, frozen.fingerprint, frozen, encoded, internal_hash,
                requested + 120,
            )
        if self.clock() >= review.expires:
            raise TelegramError("telegram_reconciliation_expired")
        self._reviews[review.review_id] = review
        return {"review_id": review.review_id, "review_digest": review.digest,
                "expires_in_seconds": max(0, min(120, int(review.expires - self.clock()))),
                "metadata": wire(metadata)}

    async def channel_restore_reconcile(self, params):
        from jarvis_core.maintenance import _Operation

        review, old = self._exact_review(params, _PURPOSE)
        if old is not None:
            return old.snapshot()
        self._capacity()
        self._channel_review_current(review)
        with self.gate.exclusive(expected_scope=review.scope,
                                 expected_fingerprint=review.fingerprint) as cut:
            review.frozen.assert_current(cut, full=True)
        operation = _Operation(uuid4().hex, review.kind, review.scope, phase="awaiting_owner",
                               review_id=review.review_id, review_digest=review.digest)
        self.journal.record(operation.operation_id, operation.kind, review.digest, operation.phase)
        self._reviews.pop(review.review_id, None)
        self._operations[operation.operation_id] = operation
        operation.task = asyncio.create_task(self._channel_effect(operation, review))
        return operation.snapshot()

    def _channel_review_current(self, review):
        if self.clock() >= review.expires:
            raise TelegramError("telegram_reconciliation_expired")
        metadata = review.metadata
        actual, internal_hash = self._channel_metadata(
            metadata["chat_id"], metadata["route_revision"], metadata["enabled_after"])
        if actual != metadata or internal_hash != review.internal_hash:
            raise TelegramError("telegram_reconciliation_changed")

    @staticmethod
    def _channel_receipt(operation, review, status, *, error=None, after_hash=None,
                         cleanup_pending=False):
        metadata = review.metadata
        completed = status == "completed"
        return {
            "operation_id": operation.operation_id, "purpose": _PURPOSE,
            "request_digest": review.digest, "status": status,
            "effect_status": ("route_reconciled" if completed else "route_change_unknown"
                              if status == "unknown" else "no_route_change_confirmed"),
            "bot_id": metadata["bot_id"], "chat_id": metadata["chat_id"],
            "route_revision_before": metadata["route_revision"],
            "route_revision_after": metadata["route_revision"] + 1 if completed else None,
            "enabled_before": metadata["enabled_before"],
            "enabled_after": metadata["enabled_after"],
            "configuration_hash_before": metadata["configuration_hash"],
            "configuration_hash_after": after_hash,
            "error_code": error, "cleanup_pending": cleanup_pending, **_NO_EFFECTS,
            "permits_replay": False,
        }

    async def _channel_effect(self, operation, review):
        from jarvis_core.maintenance import _code

        attempted = False
        capture = None
        try:
            request = HostApprovalRequest(
                review.scope, HostPurpose.TELEGRAM_RESTORE_RECONCILE, operation.operation_id,
                canonical_host_arguments({"review_id": review.review_id,
                                          "review_digest": review.digest,
                                          "review": review.metadata}), "")
            request = replace(request, digest=host_operation_digest(request))
            deadline = time.monotonic() + max(0, review.expires - self.clock())
            grant = await self.authority.request_host(request, operation.cancellation,
                                                      deadline=deadline)
            if grant is None:
                operation.phase = ("cancelled" if operation.cancellation.is_set() else
                                   "failed" if self.clock() >= review.expires else "denied")
                if operation.phase == "failed":
                    operation.error_code = "telegram_reconciliation_expired"
                return
            self._channel_review_current(review)
            operation.phase = "quiescing"
            with self.authority.execution_host(request, grant):
                capture = _ChannelMutationCut(
                    self.gate, expected_scope=review.scope,
                    expected_fingerprint=review.fingerprint)
                operation.capture_cut = capture
                cut = capture.cut
                review.frozen.assert_current(cut, full=True)
                # A narrow raw reference is retained only while this exclusive
                # host cut owns its fixed Telegram registry. No generic RPC can
                # borrow it, and ordinary BoundProfileResource calls stay barred.
                journal = self.service._telegram_journal._resource

                def guard(staging):
                    nonlocal attempted
                    # The cut excludes cooperative changes. The registry's
                    # locked CAS rechecks its entire source immediately before
                    # this hook; its own private temporary now exists, so the
                    # frozen inventory must not misclassify that owned staging
                    # file as an unrelated profile entry.
                    review.frozen.assert_json_staging_current(cut, staging)
                    self.service.profile_lease.assert_admission_current()
                    self.service._assert_workspace_current()
                    if operation.cancellation.is_set():
                        raise TelegramError("telegram_reconciliation_cancelled")
                    if self.clock() >= review.expires:
                        raise TelegramError("telegram_reconciliation_expired")
                    if attempted:
                        self.authority.revalidate_committed_host(request)
                    else:
                        self.authority.check_host(request, commit=True)
                    attempted = True
                    capture.changed = True
                    operation.stage = "telegram_route_publish"

                metadata = review.metadata
                operation.phase = "working"
                self.journal.record(operation.operation_id, operation.kind, review.digest,
                                    operation.phase)
                outcome = await self.gate.run_worker(cut, lambda: journal.reconcile_route(
                    metadata["chat_id"], expected_revision=metadata["route_revision"],
                    enabled=metadata["enabled_after"],
                    expected_configuration_hash=review.internal_hash,
                    before_publish=guard), operation.cancellation)
                if outcome.error is not None:
                    raise outcome.error
                cut.assert_current()
                actual_hash = journal.configuration_hash()
                after_hash = hashlib.sha256(json.dumps(
                    [actual_hash, metadata["workspace_id"]], separators=(",", ":")).encode()
                ).hexdigest()
                operation.receipt = self._channel_receipt(
                    operation, review, "completed", after_hash=after_hash)
                operation.phase = "complete"
        except asyncio.CancelledError:
            operation.cancellation.set()
            operation.phase = "publication_unknown" if attempted else "cancelled"
            operation.receipt = self._channel_receipt(
                operation, review, "unknown" if attempted else "cancelled",
                error="telegram_reconciliation_cancelled")
        except Exception as error:
            operation.error_code = _code(error)
            owner = getattr(error, "owner", None)
            if owner is not None and not owner.closed and capture is not None:
                capture.retain(owner)
                capture.changed = True
                operation.cleanup_owner = capture
            pending = operation.cleanup_owner is not None
            operation.phase = ("cleanup_pending" if pending else
                               "publication_unknown" if attempted else "failed")
            operation.receipt = self._channel_receipt(
                operation, review, "unknown" if attempted or pending else "failed",
                error=operation.error_code, cleanup_pending=pending)
        finally:
            if attempted:
                self.service._telegram_restored_pairings.clear()
            self._finish_capture(operation, capture)
            try:
                self.journal.record(operation.operation_id, operation.kind, review.digest,
                                    operation.phase, operation.receipt)
                operation.receipt_durable = True
            except Exception:
                operation.receipt_durable = False
                operation.error_code = "maintenance_receipt_unconfirmed"
                operation.phase = "publication_unknown" if attempted else "failed"

    async def channel_restore_inspect(self, params):
        from jarvis_core.maintenance import _params

        _params(params, {"chat_id"}, {"chat_id"})
        if type(params["chat_id"]) is not int or not 1 <= params["chat_id"] < 1 << 52:
            raise RpcError(-32602, "Selecciona una ruta válida.")
        self._channel_current()
        # Observation does not reconstruct a permit, complete an earlier receipt,
        # or claim whether a previous fsync/ACK succeeded.
        routes, _internal_hash, configuration_hash = self.service._telegram_configuration()
        route = next((row for row in routes if row["chat_id"] == params["chat_id"]), None)
        if route is None:
            raise HostProfileError("telegram_reconciliation_route_absent")
        return {"observation_only": True, "permits_replay": False,
                "prior_acknowledgement": "not_established",
                "bot_id": self.service._telegram_journal.bot_id, "route": route,
                "configuration_hash": configuration_hash,
                "workspace_id": self.service._workspace_id, "network_started": False}
