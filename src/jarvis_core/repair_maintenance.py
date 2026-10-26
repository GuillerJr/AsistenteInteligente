"""Explicit, loss-described repair candidates published as new usable backups.

Repair never edits the selected original. A successful candidate still needs the
ordinary separate restore and activation reviews to become an active profile.
"""
from __future__ import annotations

import time

from jarvis_core.host_profile import freeze_sources
from jarvis_core.maintenance_journal import wire
from jarvis_core.recovery import (
    EXCLUSIONS,
    RecoveryContext,
    RecoveryLimits,
    SourceBundle,
    SqliteImageSource,
    create_backup,
)
from jarvis_core.recovery_extensions import ExtensionOutcome, ExtensionPrepared
from jarvis_core.recovery_fs import RecoveryFsError


class RepairManagement:
    def repair_handlers(self):
        return {"doctor.repair.review": self.repair_review,
                "doctor.repair.apply": self.repair_apply}

    def repair_workers(self):
        return {"doctor.repair.apply": self._repair_effect}

    async def repair_review(self, params):
        from jarvis_core.cold_repair import repair_cold_image
        from jarvis_core.maintenance import _params
        _params(params, {"role", "repair_kind", "destination_parent", "name"},
                {"role", "repair_kind", "destination_parent", "name"})
        role, kind = params["role"], params["repair_kind"]
        if role not in {"state", "memory", "schedules"} or kind not in {
                "wal_valid_prefix", "memory_fts_rebuild"}:
            raise RecoveryFsError("profile_repair_kind_invalid")

        def prepare(cut, operation):
            frozen = freeze_sources(self.service, cut)
            target = self._target(params, frozen.forbidden_roots)
            row = next(row for row in frozen._sql if row.role == role)
            if row.cold is None or row.cold.members[0] is None:
                raise RecoveryFsError("profile_repair_closed_source_required")

            def guard(stage, write=False):
                frozen.assert_current(cut)
                operation.stage = stage
            context = RecoveryContext(operation.operation_id, cut.scope.profile_id,
                                      cut.scope.generation, guard, operation.cancellation,
                                      time.monotonic() + 60)
            candidate = repair_cold_image(row.cold, context, RecoveryLimits(),
                                          lambda: frozen.assert_current(cut), kind)
            # Resolve and validate all other role images before requesting the
            # owner's effect. No WAL reader can survive into the approval wait.
            bundle = frozen.bind(cut, context=context,
                                 repaired_images={role: (candidate.data, candidate.sha256)})
            images = []
            from jarvis_core.recovery import capture_sqlite_snapshot
            for source in bundle.sqlite:
                if isinstance(source, SqliteImageSource):
                    images.append(source)
                else:
                    store = next(row.store for row in frozen._sql if row.role == source.role)
                    with store._mutex:
                        data = capture_sqlite_snapshot(source, context, RecoveryLimits(),
                                                       self.service.directory)
                    import hashlib

                    from jarvis_core.recovery_fs import FileIdentity
                    images.append(SqliteImageSource(source.role, data,
                                                    FileIdentity(*next(
                                                        r.fingerprint[1:3] for r in frozen._sql
                                                        if r.role == source.role)),
                                                    hashlib.sha256(data).hexdigest(),
                                                    lambda: frozen.assert_current(cut)))
            captured = SourceBundle(bundle.profile_id, bundle.generation, bundle.root,
                                    bundle.root_identity, tuple(images), bundle.json,
                                    bundle.absent)
            total = sum(len(source.data) for source in captured.sqlite)
            if total > RecoveryLimits().max_total_bytes:
                raise RecoveryFsError("profile_sqlite_image_limit")
            return ExtensionPrepared(
                "doctor.repair.apply", frozen, target, (candidate, captured),
                {"role": role, "repair_kind": kind, "candidate": candidate.metadata(),
                 "source": wire(frozen.metadata),
                 "destination_parent": str(target.parent), "destination_name": target.name,
                 "destination_identity": wire(target.parent_identity),
                 "excluded": list(EXCLUSIONS), "original_preserved": True,
                 "activation_included": False, "credentials_included": False,
                 "semantics": "new_repaired_backup_requires_separate_restore",
                 "authenticity": "not_established"})
        return self._read_operation("doctor.repair.review", prepare)

    async def repair_apply(self, params):
        return self._extension_start(params, "doctor.repair.apply")

    def _repair_effect(self, review, context, cut, operation):
        from jarvis_core.maintenance import _absent_target
        candidate, bundle = review.payload
        _absent_target(review.target)
        candidate.check_source(context, lambda: review.frozen.assert_current(cut))
        # Publish the reviewed immutable images. Never recompute a repair after
        # approval, and never retry a publication with an uncertain receipt.
        from dataclasses import replace
        def check():
            review.frozen.assert_current(cut)
        bundle = replace(bundle,
                         sqlite=tuple(replace(source, check_current=check)
                                      for source in bundle.sqlite),
                         json=tuple(replace(source, check_current=check) for source in bundle.json))
        try:
            receipt = create_backup(bundle, review.target, context)
        except Exception as error:
            if getattr(error, "receipt", None) is not None:
                error.receipt = replace(error.receipt, purpose=review.kind)
            raise
        receipt = replace(receipt, purpose=review.kind)
        return ExtensionOutcome(receipt, {"repair": candidate.metadata(),
                                          "restoration_required": True,
                                          "original_preserved": True})
