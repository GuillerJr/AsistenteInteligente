"""A narrow active-memory mutation under the existing profile cut and authority.

The core owns its mutex, DB descriptors and transactional CAS. This wrapper keeps
all sibling guards and the SAME cut alive while SQL cleanup remains uncertain.
No execution_host ContextVar token crosses into another cleanup Task.
"""
from __future__ import annotations

import hashlib
import json
import os
import threading
from pathlib import Path

from jarvis_core.host_profile import BoundProfileResource, HostProfileError, _file_info, _regular
from jarvis_core.memory import MemoryStore
from jarvis_core.recovery import canonical
from jarvis_core.recovery_fs import FileIdentity
from jarvis_core.tools import HostPurpose


def _named(path):
    try:
        info = path.lstat()
    except FileNotFoundError:
        return None
    _regular(info)
    return _file_info(info)


class MemoryMutationCut:
    """One reviewed memory row may mutate; every other source remains frozen."""

    def __init__(self, service, frozen, request, core_review):
        self.service, self.frozen, self.request = service, frozen, request
        self.gate, self.authority = service.profile_gate, service.authority
        self._lock = threading.RLock()
        self._manager = None
        self.cut = None
        self.authorized = False
        self.released = False
        self.connection = None
        self.owner = None
        self._generation_advanced = False
        wrapped = service.memory
        if (type(wrapped) is not BoundProfileResource or wrapped._lease is not self.gate.lease
                or wrapped._gate is not self.gate or type(wrapped._resource) is not MemoryStore):
            raise HostProfileError("profile_memory_store_mismatch")
        self._store = wrapped._resource  # Never returned to an RPC, model or generic borrow API.
        self._review = core_review
        try:
            arguments = json.loads(request.arguments_json)
            metadata = arguments["review"]
            valid = (request.purpose is HostPurpose.MEMORY_RESTORE_COPY_SCOPE
                     and request.scope == frozen.scope
                     and metadata["core_review_digest"] == core_review.digest
                     and metadata["kind"] == request.purpose.value
                     and arguments["review_digest"]
                     == hashlib.sha256(canonical(metadata)).hexdigest()
                     and metadata["profile_id"] == frozen.scope.profile_id
                     and metadata["generation"] == frozen.scope.generation
                     and metadata["agent_id"] == core_review.source.scope.agent_id
                     == core_review.destination.scope.agent_id
                     and core_review.destination.scope.workspace_id == service._workspace_id
                     and metadata["destination_workspace_path"] == str(service._workspace_path)
                     and metadata["source_generation"] == str(core_review.source.generation)
                     and metadata["source_fingerprint"] == core_review.source.fingerprint
                     and metadata["source_documents"] == core_review.source.document_count
                     and metadata["source_bytes"] == core_review.source.byte_length
                     and metadata["root_identity"] == {
                         "device": str(frozen.root_identity.device),
                         "inode": str(frozen.root_identity.inode)}
                     and metadata["destination_workspace_identity"] == {
                         "device": str(service.workspace_tools.identity[0]),
                         "inode": str(service.workspace_tools.identity[1])}
                     and metadata["destination_scope_present"] is core_review.destination.exists
                     and metadata["destination_generation"] == (
                         str(core_review.destination.generation)
                         if core_review.destination.exists else None)
                     and metadata["schema_before"] == core_review.schema_before
                     and metadata["schema_after"] == core_review.schema_after
                     and metadata["migration_required"] is core_review.migration_required
                     and metadata["database_identity"] == {
                         "device": str(core_review.database_identity[0]),
                         "inode": str(core_review.database_identity[1])})
        except (TypeError, KeyError, ValueError):
            valid = False
        if not valid:
            raise HostProfileError("profile_memory_review_mismatch")
        row = next((row for row in frozen._sql if row.role == "memory"), None)
        if row is None or row.fingerprint[0] not in {"owned", "cold_delete"}:
            raise HostProfileError("profile_memory_source_unavailable")
        self._identity = FileIdentity(*core_review.database_identity)
        if tuple(row.fingerprint[1:3]) != tuple(core_review.database_identity):
            raise HostProfileError("profile_memory_review_mismatch")
        self._initial_connection = row.connection
        self._initial_changes = row.connection.total_changes if row.connection is not None else 0
        self._before_file = _named(self._store.path)
        self._lock_path = self._store.path.with_name(self._store.path.name + ".lock")
        self._before_lock = _named(self._lock_path)
        self._workspace_path = Path(service._workspace_path)
        self._workspace_id = service._workspace_id
        self._agent_id = core_review.source.scope.agent_id
        profile = service.agent_profiles.get(self._agent_id)
        if metadata["agent_revision"] != str(profile.revision):
            raise HostProfileError("profile_memory_review_mismatch")
        # Enter and leave this manager on the owning loop, but it has no ContextVar
        # token and can stay retained between the execute and explicit cleanup RPC.
        manager = self.gate.exclusive(expected_scope=frozen.scope,
                                      expected_fingerprint=frozen.fingerprint)
        self.cut = manager.__enter__()
        self._manager = manager
        try:
            frozen.assert_current(self.cut, full=True)
            self.authority.check_host(request)
        except BaseException:
            manager.__exit__(None, None, None)
            self._manager = None
            self.released = True
            raise

    @property
    def closed(self):
        return self.released

    def _context_current(self):
        if self.released or self.cut is None:
            raise HostProfileError("profile_cut_expired")
        self.cut.assert_current()
        if (Path(self.service._workspace_path) != self._workspace_path
                or self.service._workspace_id != self._workspace_id):
            raise HostProfileError("profile_workspace_changed")
        self.service._assert_workspace_current()
        # The profile document is frozen and checked below. Accessing its public
        # method under a cut would deliberately reject; compare the cached profile
        # fingerprint in the exact frozen JSON instead of bypassing its store.
        if self._agent_id != self._review.destination.scope.agent_id:
            raise HostProfileError("profile_memory_review_mismatch")

    def _bind_current(self, stage):
        connection = self._store._disk
        if connection is None:
            if stage != "memory_copy_open_existing":
                raise HostProfileError("profile_memory_binding_missing")
            return
        if self.connection is None:
            if self._initial_connection is not None and connection is not self._initial_connection:
                raise HostProfileError("profile_memory_connection_changed")
            self._store._check_disk()
            opened = os.fstat(self._store._file_fd)
            if FileIdentity.of(opened) != self._identity:
                raise HostProfileError("profile_memory_identity_changed")
            self.connection = connection
        if self._store._disk is not self.connection:
            raise HostProfileError("profile_memory_connection_changed")
        self._store._check_disk()
        if FileIdentity.of(os.fstat(self._store._file_fd)) != self._identity:
            raise HostProfileError("profile_memory_identity_changed")

    def guard(self, stage, write=False):
        # Called in the core worker, including from SQLite progress callbacks.
        # No asyncio and no SQL on the mutating connection are used here.
        with self._lock:
            self._context_current()
            if not self.authorized:
                self.frozen.assert_current(self.cut, full=write)
                self.authority.check_host(self.request, commit=write)
                if write:
                    self.authorized = True
            else:
                self.frozen._assert_nonmemory_current(self.cut)
                self._bind_current(stage)
                self.authority.revalidate_committed_host(self.request)

    def execute(self, context, *, copy_limits=None):
        expected_digest = json.loads(self.request.arguments_json)["review_digest"]
        if (context.operation_id != self.request.operation_id
                or context.request_digest != expected_digest
                or context.approved_core_review_digest != self._review.digest):
            raise HostProfileError("profile_memory_review_mismatch")
        return self._store.copy_scope(self._review, context, copy_limits=copy_limits)

    def retain(self, owner):
        with self._lock:
            self.cut.assert_current()
            if owner.store is not self._store or owner.connection is not self.connection:
                raise HostProfileError("profile_memory_owner_mismatch")
            self.owner = owner

    def cleanup(self):
        # Owned rollback/policies do not reactivate the expired copy grant or its
        # one-use Context. The caller keeps this exact cut until the worker ends.
        with self._lock:
            self.cut.assert_current()
            owner = self.owner
            if owner is None:
                return
            owner.cleanup()
            if not owner.closed:
                raise HostProfileError("profile_memory_cleanup_pending")

    def release(self):
        """Loop-only after worker settlement and execution_host exit; idempotent."""
        self.gate._on_loop()
        with self._lock:
            if self.released:
                return
            if self.owner is not None and not self.owner.closed:
                raise HostProfileError("profile_memory_cleanup_pending")
            if self.cut.worker_count:
                raise HostProfileError("profile_worker_cleanup_pending")
            changed = (self._store._disk is not self._initial_connection
                       or (self._store._disk is not None
                           and self._store._disk.total_changes != self._initial_changes)
                       or _named(self._store.path) != self._before_file
                       or _named(self._lock_path) != self._before_lock)
            self._manager.__exit__(None, None, None)
            self._manager = None
            self.released = True
            if changed and not self._generation_advanced:
                scope = self.gate.lease.advance_generation()
                self._generation_advanced = True
                if self.gate.on_generation is not None:
                    self.gate.on_generation(scope)
