"""Explicit restored-memory copy through the single native host authority.

Inventory/review/observation use validated images. Scope keys and content remain
private. SQL mutation keeps its exact cut through any retained cleanup owner.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import time
from dataclasses import dataclass, replace
from uuid import uuid4

from jarvis_core.activation_management import ActivationManagement
from jarvis_core.host_profile import HostProfileError, freeze_sources
from jarvis_core.maintenance_journal import wire
from jarvis_core.memory import (
    MemoryCopyContext,
    MemoryCopyLimits,
    MemoryError,
    MemoryScope,
    lookup_memory_scope_copy,
    memory_scope_summaries,
    review_memory_scope_copy,
)
from jarvis_core.memory_host_cut import MemoryMutationCut
from jarvis_core.memory_host_read import read_memory_image, validate_memory_projection
from jarvis_core.protocol import RpcError
from jarvis_core.recovery import RecoveryContext, RecoveryLimits, canonical
from jarvis_core.recovery_fs import FileIdentity
from jarvis_core.tools import (
    HostApprovalRequest,
    HostPurpose,
    canonical_host_arguments,
    host_operation_digest,
)

_PURPOSE = "memory.restore.copy_scope"


@dataclass
class _Inventory:
    inventory_id: str
    scope: object
    fingerprint: str
    frozen: object
    agent_id: str
    agent_revision: int
    workspace_path: str
    workspace_id: str
    workspace_identity: tuple
    image: object
    candidates: tuple
    summaries: tuple
    expires: float
    handles: dict
    cursors: dict


@dataclass(frozen=True)
class _MemoryReview:
    review_id: str
    kind: str
    digest: str
    scope: object
    fingerprint: str
    frozen: object
    core: object
    metadata_json: bytes
    expires: float
    copy_limits: MemoryCopyLimits
    snapshot_limits: object

    @property
    def metadata(self):
        return json.loads(self.metadata_json)


@dataclass(frozen=True)
class _MemoryRead:
    kind: str
    value: object


def _physical(identity):
    if not isinstance(identity, FileIdentity):
        identity = FileIdentity(*identity)
    return {"device": str(identity.device), "inode": str(identity.inode)}


def _receipt(value):
    return {"operation_id": value.operation_id, "purpose": _PURPOSE,
            "request_digest": value.request_digest, "core_review_digest": value.core_review_digest,
            "status": value.status, "effect_status": value.effect_status,
            "document_count": value.document_count, "byte_length": value.byte_length,
            "mapping_sha256": value.mapping_sha256,
            "destination_generation_after": str(value.destination_generation_after)
            if value.destination_generation_after is not None else None,
            "error_code": value.error_code, "cleanup_pending": value.cleanup_pending}


def _memory_phase(receipt):
    if receipt["cleanup_pending"]:
        return "cleanup_pending"
    if receipt["status"] == "unknown":
        return "publication_unknown"
    return "complete" if receipt["status"] == "completed" else receipt["status"]


class MemoryManagement:
    def _initialize_memory_limits(self):
        self._memory_recovery_limits = RecoveryLimits()
        self._memory_copy_limits = MemoryCopyLimits()

    def memory_handlers(self):
        return {"memory.restore.inventory": self.memory_inventory,
                "memory.restore.review": self.memory_review,
                "memory.restore.copy_scope": self.memory_copy,
                "memory.restore.receipt": self.memory_receipt,
                "maintenance.cleanup": self.cleanup}

    def _memory_current(self, *, require_workspace=True):
        scope = self._current()
        self.service.profile_lease.check_bootstrap_ready()
        if self.service.profile_lease.admission is None:
            raise HostProfileError("memory_restore_profile_required")
        if require_workspace:
            self.service._assert_workspace_current()
        return scope

    def _memory_context(self, cut, frozen, operation, requested):
        def guard(stage, write=False):
            frozen.assert_current(cut)
            operation.stage = stage
        context = RecoveryContext(operation.operation_id, cut.scope.profile_id,
                                  cut.scope.generation, guard, operation.cancellation,
                                  requested + 20, clock=self.clock)
        context.begin()
        return context

    def _memory_inventory_cached(self, identity):
        from jarvis_core.maintenance import _id
        inventory = getattr(self, "_memory_inventory", None)
        if (inventory is None or inventory.inventory_id != _id(identity)
                or inventory.expires <= self.clock()
                or inventory.scope != self.gate.lease.scope):
            raise HostProfileError("memory_restore_inventory_stale")
        return inventory

    async def memory_inventory(self, params):
        from jarvis_core.maintenance import _id, _params
        self._memory_current()
        if type(params) is dict and "inventory_id" in params:
            _params(params, {"inventory_id", "cursor"}, {"inventory_id", "cursor"})
            inventory = self._memory_inventory_cached(params["inventory_id"])
            cursor = _id(params["cursor"])
            offset = inventory.cursors.get(cursor)
            if offset is None:
                raise HostProfileError("memory_restore_cursor_invalid")
            def page(cut, operation):
                inventory.frozen.assert_current(cut, full=True)
                return _MemoryRead("page", (inventory, offset))
            return self._read_operation("memory.restore.inventory", page)
        _params(params, {"agent_id"}, {"agent_id"})
        agent = self.service.agent_profiles.get(params["agent_id"])
        if not 1 <= agent.revision < 1 << 63:
            raise HostProfileError("memory_restore_agent_revision_invalid")
        requested = self.clock()
        workspace_path, workspace_id = str(self.service._workspace_path), self.service._workspace_id
        workspace_identity = self.service.workspace_tools.identity
        def read(cut, operation):
            frozen = freeze_sources(self.service, cut)
            context = self._memory_context(cut, frozen, operation, requested)
            image = read_memory_image(
                frozen, cut, context, recovery_limits=self._memory_recovery_limits,
                copy_limits=self._memory_copy_limits)
            summaries = (() if image.data is None else memory_scope_summaries(
                image.data, check_current=lambda: context.checkpoint("memory_inventory"),
                limits=self.service.memory.limits, copy_limits=image.copy_limits))
            if len(summaries) > 10_000:
                raise MemoryError("memory_copy_inventory_capacity")
            destination = MemoryScope(agent.agent_id, workspace_id)
            candidates = tuple(row for row in summaries if row.scope.agent_id == agent.agent_id
                               and row.scope != destination and row.document_count)
            # Retain only frozen metadata. The potentially large image is released.
            descriptor = replace(image, data=None)
            inventory = _Inventory(uuid4().hex, cut.scope, frozen.fingerprint, frozen,
                                   agent.agent_id, agent.revision, workspace_path, workspace_id,
                                   workspace_identity, descriptor, candidates, summaries,
                                   requested + 120, {}, {})
            return _MemoryRead("inventory", inventory)
        return self._read_operation("memory.restore.inventory", read)

    def _memory_page(self, inventory, offset):
        if inventory.expires <= self.clock():
            raise HostProfileError("memory_restore_inventory_stale")
        destination = next((row for row in inventory.summaries
                            if row.scope.agent_id == inventory.agent_id
                            and row.scope.workspace_id == inventory.workspace_id), None)
        empty = destination is None or destination.document_count == 0
        limits = self.service.memory.limits
        rows = []
        for index, row in enumerate(inventory.candidates[offset:offset + 32], offset):
            handle = inventory.handles.setdefault(index, uuid4().hex)
            reason = ("memory_copy_destination_not_empty" if not empty else
                      "memory_copy_document_limit" if row.largest_document_bytes
                      > limits.max_document_bytes else
                      "memory_copy_scope_capacity" if row.document_count
                      > limits.max_documents_per_scope or row.byte_length > limits.max_scope_bytes
                      else None)
            rows.append({"source_handle": handle, "agent_id": row.scope.agent_id,
                         "source_generation": str(row.generation),
                         "documents": row.document_count, "bytes": row.byte_length,
                         "largest_document_bytes": row.largest_document_bytes,
                         "fingerprint": row.fingerprint, "eligible": reason is None,
                         "reason": reason})
        next_offset = offset + len(rows)
        cursor = None
        if next_offset < len(inventory.candidates):
            cursor = next((key for key, value in inventory.cursors.items()
                           if value == next_offset), None)
            if cursor is None:
                cursor = uuid4().hex
                inventory.cursors[cursor] = next_offset
        return {"inventory_id": inventory.inventory_id,
                "expires_in_seconds": max(0, min(120, int(inventory.expires - self.clock()))),
                "scope": wire(inventory.scope),
                "root_identity": _physical(inventory.frozen.root_identity),
                "agent_id": inventory.agent_id, "agent_revision": str(inventory.agent_revision),
                "schema_version": inventory.image.schema_version,
                "source_kind": inventory.image.source_kind,
                "destination": {"workspace_path": inventory.workspace_path,
                                "workspace_identity": _physical(inventory.workspace_identity),
                                "empty": empty, "scope_present": destination is not None,
                                "scope_generation": str(destination.generation)
                                if destination else None},
                "sources": rows, "candidate_count": len(inventory.candidates),
                "session_bound_scopes_excluded": inventory.image.scopes_total
                - len(inventory.summaries), "next_cursor": cursor, "page_size": 32,
                "provider_used": False, "source_workspace_resolved": False}

    async def memory_review(self, params):
        from jarvis_core.maintenance import _id, _params
        _params(params, {"inventory_id", "source_handle"}, {"inventory_id", "source_handle"})
        self._memory_current()
        inventory = self._memory_inventory_cached(params["inventory_id"])
        handle = _id(params["source_handle"])
        index = next((key for key, value in inventory.handles.items() if value == handle), None)
        if index is None:
            raise HostProfileError("memory_restore_handle_invalid")
        selected = inventory.candidates[index]
        requested = self.clock()
        def read(cut, operation):
            inventory.frozen.assert_current(cut, full=True)
            frozen = freeze_sources(self.service, cut)
            context = self._memory_context(cut, frozen, operation, requested)
            image = read_memory_image(
                frozen, cut, context, recovery_limits=self._memory_recovery_limits,
                copy_limits=self._memory_copy_limits)
            if image.data is None:
                raise HostProfileError("memory_restore_source_changed")
            core = review_memory_scope_copy(
                image.data, selected.scope, MemoryScope(inventory.agent_id, inventory.workspace_id),
                database_identity=(image.identity.device, image.identity.inode),
                check_current=lambda: context.checkpoint("memory_review"),
                limits=self.service.memory.limits, copy_limits=image.copy_limits)
            if core.source != selected:
                raise HostProfileError("memory_restore_source_changed")
            snapshot_limits = self._memory_recovery_limits.sqlite()
            validate_memory_projection(image, core, snapshot_limits)
            return _MemoryRead("review", (inventory, handle, frozen, core, image.copy_limits,
                                           snapshot_limits, requested + 120))
        return self._read_operation("memory.restore.review", read)

    def _publish_memory_result(self, prepared):
        if prepared.kind == "inventory":
            inventory = prepared.value
            if inventory.scope != self.gate.lease.scope:
                raise HostProfileError("memory_restore_inventory_stale")
            self._memory_inventory = inventory
            return {"inventory": self._memory_page(inventory, 0)}
        if prepared.kind == "page":
            inventory, offset = prepared.value
            return {"inventory": self._memory_page(inventory, offset)}
        if prepared.kind == "observation":
            value = prepared.value
            observation = None if value is None else {
                "operation_id": value.operation_id, "request_digest": value.request_digest,
                "origin_kind": value.origin_kind, "document_count": value.document_count,
                "byte_length": value.byte_length, "mapping_sha256": value.mapping_sha256,
                "current_matching_documents": value.current_matching_documents,
                "current_destination_generation": str(value.current_destination_generation),
                "current_documents_match": value.current_documents_match,
                "current_scope_generation_matches": value.current_scope_generation_matches,
                "state": value.state, "prior_acknowledgement": value.prior_acknowledgement}
            return {"observation": observation, "permits_replay": False}
        inventory, handle, frozen, core, copy_limits, snapshot_limits, expires = prepared.value
        if expires <= self.clock():
            raise HostProfileError("memory_restore_review_expired")
        limits = self.service.memory.limits
        metadata = {"kind": _PURPOSE, "profile_id": frozen.scope.profile_id,
                    "generation": frozen.scope.generation,
                    "root_identity": _physical(frozen.root_identity),
                    "database_identity": _physical(core.database_identity),
                    "agent_id": inventory.agent_id, "agent_revision": str(inventory.agent_revision),
                    "inventory_id": inventory.inventory_id, "source_handle": handle,
                    "source_generation": str(core.source.generation),
                    "source_fingerprint": core.source.fingerprint,
                    "source_documents": core.source.document_count,
                    "source_bytes": core.source.byte_length,
                    "largest_document_bytes": core.source.largest_document_bytes,
                    "destination_workspace_path": inventory.workspace_path,
                    "destination_workspace_identity": _physical(inventory.workspace_identity),
                    "destination_scope_present": core.destination.exists,
                    "destination_generation": str(core.destination.generation)
                    if core.destination.exists else None,
                    "destination_documents": 0, "core_review_digest": core.digest,
                    "schema_before": core.schema_before, "schema_after": core.schema_after,
                    "migration_required": core.migration_required,
                    "limits": {"document_bytes": limits.max_document_bytes,
                               "scope_documents": limits.max_documents_per_scope,
                               "scope_bytes": limits.max_scope_bytes,
                               "memory_documents": limits.max_documents,
                               "memory_scopes": limits.max_scopes,
                               "memory_text_bytes": limits.max_total_bytes,
                               "chunk_bytes": limits.chunk_bytes,
                               "copy_receipts": copy_limits.max_receipts,
                               "copy_mapping_rows": copy_limits.max_mapping_rows,
                               "copy_metadata_bytes": copy_limits.max_metadata_bytes,
                               "copy_vm_steps": copy_limits.max_vm_steps,
                               "memory_file_bytes": copy_limits.max_sqlite_bytes,
                               "profile_snapshot_bytes":
                               self._memory_recovery_limits.max_total_bytes},
                    "policy": "same_agent_durable_scope_to_empty_current_workspace",
                    "retains_source": True, "new_document_ids": True,
                    "new_document_revision": 1, "source_labels_verified": False,
                    "uri_resources_opened": False, "provider_used": False, "grants_restored": False}
        encoded = canonical(metadata)
        review = _MemoryReview(uuid4().hex, _PURPOSE, hashlib.sha256(encoded).hexdigest(),
                               frozen.scope, frozen.fingerprint, frozen, core, encoded,
                               expires, copy_limits, snapshot_limits)
        self._reviews[review.review_id] = review
        return {"review": {"review_id": review.review_id, "review_digest": review.digest,
                           "expires_in_seconds": max(0, min(120, int(expires - self.clock()))),
                           "metadata": metadata}}

    async def memory_receipt(self, params):
        from jarvis_core.maintenance import _HASH, _id, _params
        _params(params, {"operation_id", "review_digest"}, {"operation_id", "review_digest"})
        self._memory_current(require_workspace=False)
        identity, digest = _id(params["operation_id"]), params["review_digest"]
        if type(digest) is not str or _HASH.fullmatch(digest) is None:
            raise RpcError(-32602, "La huella de la operación no es válida.")
        requested = self.clock()
        def read(cut, operation):
            frozen = freeze_sources(self.service, cut)
            context = self._memory_context(cut, frozen, operation, requested)
            image = read_memory_image(
                frozen, cut, context, recovery_limits=self._memory_recovery_limits,
                copy_limits=self._memory_copy_limits, for_copy=False)
            observation = None if image.data is None else lookup_memory_scope_copy(
                image.data, identity, digest,
                check_current=lambda: context.checkpoint("memory_receipt_read"),
                limits=self.service.memory.limits, copy_limits=image.copy_limits)
            return _MemoryRead("observation", observation)
        return self._read_operation("memory.restore.receipt", read)

    async def memory_copy(self, params):
        from jarvis_core.maintenance import _Operation
        review, old = self._exact_review(params, _PURPOSE)
        if old is not None:
            return old.snapshot()
        self._memory_current()
        self._capacity()
        if review.scope != self.gate.lease.scope:
            raise HostProfileError("memory_restore_review_stale")
        with self.gate.exclusive(expected_scope=review.scope,
                                 expected_fingerprint=review.fingerprint) as cut:
            review.frozen.assert_current(cut, full=True)
        operation = _Operation(uuid4().hex, _PURPOSE, review.scope, phase="awaiting_owner",
                               review_id=review.review_id, review_digest=review.digest)
        self.journal.record(operation.operation_id, operation.kind, review.digest, operation.phase)
        self._reviews.pop(review.review_id, None)
        self._operations[operation.operation_id] = operation
        operation.task = asyncio.create_task(self._memory_effect(operation, review))
        return operation.snapshot()

    def _memory_save(self, operation):
        try:
            self.journal.record(operation.operation_id, operation.kind, operation.review_digest,
                                operation.phase, operation.receipt)
            operation.receipt_durable = True
        except Exception:
            operation.receipt_durable = False
            operation.error_code = "maintenance_receipt_unconfirmed"
            operation.phase = ("cleanup_pending" if operation.cleanup_owner is not None else
                               "publication_unknown" if operation.receipt else "failed")

    async def _memory_effect(self, operation, review):
        from jarvis_core.maintenance import _code
        mutation = None
        try:
            request = HostApprovalRequest(
                review.scope, HostPurpose.MEMORY_RESTORE_COPY_SCOPE, operation.operation_id,
                canonical_host_arguments({"review_id": review.review_id,
                                          "review_digest": review.digest,
                                          "review": review.metadata}), "")
            request = replace(request, digest=host_operation_digest(request))
            grant = await self.authority.request_host(
                request, operation.cancellation,
                deadline=time.monotonic() + max(0, review.expires - self.clock()))
            if grant is None:
                operation.phase = ("cancelled" if operation.cancellation.is_set() else
                                   "failed" if self.clock() >= review.expires else "denied")
                if operation.phase == "failed":
                    operation.error_code = "memory_restore_review_expired"
                return
            operation.phase = "quiescing"
            # This ContextVar manager ALWAYS exits in this original Task. Only
            # the ordinary ProfileCut manager may be retained for another Task.
            with self.authority.execution_host(request, grant):
                mutation = MemoryMutationCut(self.service, review.frozen, request, review.core)
                operation.memory_cut = mutation
                context = MemoryCopyContext(
                    operation.operation_id, review.digest, review.core.digest, mutation.guard,
                    operation.cancellation, min(self.clock() + 20, review.expires),
                    clock=self.clock)
                operation.phase = "working"
                self.journal.record(operation.operation_id, operation.kind, review.digest,
                                    operation.phase)
                outcome = await self.gate.run_worker(
                    mutation.cut, lambda: mutation.execute(context, copy_limits=review.copy_limits),
                    operation.cancellation)
                if outcome.error is not None:
                    owner = getattr(outcome.error, "owner", None)
                    if owner is not None:
                        mutation.retain(owner)
                    raise outcome.error
                operation.receipt = _receipt(outcome.value)
                operation.phase = _memory_phase(operation.receipt)
        except asyncio.CancelledError:
            operation.cancellation.set()
            operation.phase = "cancelled"
        except Exception as error:
            value = getattr(error, "receipt", None)
            if value is not None:
                operation.receipt = _receipt(value)
                operation.phase = _memory_phase(operation.receipt)
            else:
                operation.phase = "failed"
            operation.error_code = _code(error)
        finally:
            if mutation is not None and not mutation.released:
                # SQL cleanup may outlive the worker. Keep this SAME cut and raw
                # owner latched; never substitute an external-target admission.
                if mutation.owner is not None and not mutation.owner.closed:
                    operation.cleanup_owner = mutation
                    operation.phase = "cleanup_pending"
                else:
                    try:
                        mutation.release()
                        operation.memory_cut = None
                    except Exception as error:
                        operation.cleanup_owner = mutation
                        operation.phase = "cleanup_pending"
                        operation.error_code = _code(error)
            self._memory_save(operation)

    async def cleanup(self, params):
        from jarvis_core.maintenance import _id, _params
        _params(params, {"operation_id"}, {"operation_id"})
        identity = _id(params["operation_id"])
        operation = self._operations.get(identity)
        if operation is None or operation.kind != _PURPOSE:
            return await ActivationManagement.cleanup(self, params)
        self._current()
        if operation.task is not None and not operation.task.done():
            return operation.snapshot()
        if operation.cleanup_owner is None:
            return operation.snapshot()
        operation.task = asyncio.create_task(self._cleanup_memory(operation))
        return operation.snapshot()

    async def _cleanup_memory(self, operation):
        from jarvis_core.maintenance import _code
        try:
            mutation = operation.memory_cut
            if mutation is None or operation.cleanup_owner is not mutation:
                raise HostProfileError("profile_memory_owner_mismatch")
            # No new cut, execution_host, CopyContext, grant, COMMIT or retry.
            operation.stage = "memory_copy_cleanup"
            outcome = await self.gate.run_worker(mutation.cut, mutation.cleanup,
                                                 operation.cancellation)
            if outcome.error is not None:
                raise outcome.error
            mutation.release()
            operation.cleanup_owner = None
            operation.memory_cut = None
            operation.phase = (_memory_phase(operation.receipt)
                               if operation.receipt else "publication_unknown")
            # The historical receipt remains unchanged, including its original
            # unknown/cleanup flag. RAM ownership is the separately settled fact.
            if operation.receipt and operation.receipt["status"] == "unknown":
                operation.phase = "publication_unknown"
        except Exception as error:
            operation.error_code = _code(error)
            operation.phase = "cleanup_pending"
        finally:
            self._memory_save(operation)
