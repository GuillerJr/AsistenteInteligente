"""Explicit selected workspace/artifact archives through native host approval."""
from __future__ import annotations

import time
from dataclasses import asdict

from jarvis_core.host_profile import freeze_sources
from jarvis_core.maintenance_journal import wire
from jarvis_core.recovery import RecoveryContext, RecoveryReceipt
from jarvis_core.recovery_extensions import ExtensionOutcome, ExtensionPrepared
from jarvis_core.recovery_fs import Directory, RecoveryFsError


class WorkspaceMaintenance:
    def workspace_handlers(self):
        return {"workspace.backup.review": self.workspace_backup_review,
                "workspace.backup.create": self.workspace_backup_create,
                "workspace.restore.review": self.workspace_restore_review,
                "workspace.restore.stage": self.workspace_restore_stage}

    def workspace_workers(self):
        return {"workspace.backup.create": self._workspace_effect,
                "workspace.restore.stage": self._workspace_effect}

    async def workspace_backup_review(self, params):
        from jarvis_core.maintenance import _params
        from jarvis_core.workspace_recovery import (
            SelectedWorkspaceEntry,
            WorkspaceSelection,
            capture_workspace,
            validate_workspace_snapshot,
        )
        _params(params, {"entries", "destination_parent", "name"},
                {"entries", "destination_parent", "name"})
        rows = params["entries"]
        if type(rows) is not list or not 1 <= len(rows) <= 256:
            raise RecoveryFsError("workspace_recovery_selection_invalid")
        entries = []
        for row in rows:
            _params(row, {"path", "role", "kind"}, {"path", "role", "kind"})
            if any(type(row[key]) is not str for key in row):
                raise RecoveryFsError("workspace_recovery_selection_invalid")
            entries.append(SelectedWorkspaceEntry(**row))
        entries = tuple(entries)
        self.service._assert_workspace_current()
        root = self.service._workspace_path
        if root is None:
            raise RecoveryFsError("workspace_recovery_workspace_required")
        with Directory(root, private=False) as directory:
            identity = directory.identity

        def prepare(cut, operation):
            self.service._assert_workspace_current()
            if self.service._workspace_path != root:
                raise RecoveryFsError("workspace_recovery_source_changed")
            frozen = freeze_sources(self.service, cut)
            target = self._target(params, (*frozen.forbidden_roots, root))
            context = self._workspace_read_context(cut, frozen, operation)
            # The built-in workspace is a dedicated child of the profile. Its
            # descriptor boundary confines the exact selection; denying the
            # entire ancestor here would make every default workspace unusable.
            forbidden = (() if root == self.service.directory / "workspace"
                         else (self.service.directory,))
            selection = WorkspaceSelection(root, identity, entries, forbidden)
            snapshot = capture_workspace(selection, context.checkpoint)
            summary = validate_workspace_snapshot(snapshot)
            return ExtensionPrepared(
                "workspace.backup.create", frozen, target, snapshot,
                {"source_path": str(root), "source_identity": wire(identity),
                 "destination_parent": str(target.parent), "destination_name": target.name,
                 "destination_identity": wire(target.parent_identity),
                 "summary": asdict(summary), "selection": [asdict(row) for row in entries],
                 "semantics": "selected_paths_only_no_directory_recursion",
                 "credentials_included": False, "activation_included": False,
                 "authenticity": "not_established",
                 "content_warning": "selected_content_may_contain_secrets"})
        return self._read_operation("workspace.backup.review", prepare)

    async def workspace_restore_review(self, params):
        from jarvis_core.maintenance import _HASH, _params, _path
        from jarvis_core.workspace_recovery import (
            read_workspace_archive,
            validate_workspace_snapshot,
        )
        _params(params, {"archive_path", "expected_manifest_sha256", "destination_parent", "name"},
                {"archive_path", "destination_parent", "name"})
        path = _path(params["archive_path"])
        digest = params.get("expected_manifest_sha256")
        if digest is not None and (type(digest) is not str or not _HASH.fullmatch(digest)):
            raise RecoveryFsError("workspace_recovery_manifest_invalid")

        def prepare(cut, operation):
            frozen = freeze_sources(self.service, cut)
            target = self._target(params, (*frozen.forbidden_roots, path))
            context = self._workspace_read_context(cut, frozen, operation)
            snapshot = read_workspace_archive(path, digest, context.checkpoint)
            summary = validate_workspace_snapshot(snapshot)
            return ExtensionPrepared(
                "workspace.restore.stage", frozen, target, snapshot,
                {"source_path": str(path),
                 "source_identity": wire(snapshot.archive_binding.root_identity),
                 "destination_parent": str(target.parent), "destination_name": target.name,
                 "destination_identity": wire(target.parent_identity),
                 "summary": asdict(summary), "selection": [],
                 "semantics": "new_unselected_workspace_no_execution",
                 "credentials_included": False, "activation_included": False,
                 "authenticity": "not_established",
                 "content_warning": "selected_content_may_contain_secrets"})
        return self._read_operation("workspace.restore.review", prepare)

    def _workspace_read_context(self, cut, frozen, operation):
        def guard(stage, write=False):
            frozen.assert_current(cut)
            operation.stage = stage
        return RecoveryContext(operation.operation_id, cut.scope.profile_id,
                               cut.scope.generation, guard, operation.cancellation,
                               time.monotonic() + 60)

    async def workspace_backup_create(self, params):
        return self._extension_start(params, "workspace.backup.create")

    async def workspace_restore_stage(self, params):
        return self._extension_start(params, "workspace.restore.stage")

    def _workspace_effect(self, review, context, cut, operation):
        from jarvis_core.maintenance import _absent_target
        from jarvis_core.workspace_recovery import (
            WorkspaceTarget,
            check_workspace_archive_source,
            check_workspace_source,
            restore_workspace_new,
            write_workspace_archive_new,
        )
        snapshot = review.payload
        _absent_target(review.target)
        if snapshot.source_binding is not None:
            check_workspace_source(snapshot, context.checkpoint)
        if snapshot.archive_binding is not None:
            check_workspace_archive_source(snapshot, context.checkpoint)
        target = WorkspaceTarget(review.target.parent, review.target.parent_identity,
                                 review.target.name, review.target.forbidden_roots)
        context.begin()
        callback = (write_workspace_archive_new if review.kind == "workspace.backup.create"
                    else restore_workspace_new)

        def checkpoint(stage, write=False):
            context.checkpoint(stage, write)
            if stage in {"workspace_archive_before_publish", "workspace_restore_before_publish"}:
                if snapshot.source_binding is not None:
                    check_workspace_source(snapshot, context.checkpoint)
                if snapshot.archive_binding is not None:
                    check_workspace_archive_source(snapshot, context.checkpoint)

        result = callback(snapshot, target, operation.operation_id, checkpoint)
        receipt = RecoveryReceipt(
            operation.operation_id, review.kind, "completed", "published_verified_bytes",
            target.name, artifact_id=operation.operation_id,
            manifest_sha256=result.manifest_sha256, target_identity=result.target_identity)
        return ExtensionOutcome(receipt, {"workspace": wire(result),
                                          "association_changed": False})
