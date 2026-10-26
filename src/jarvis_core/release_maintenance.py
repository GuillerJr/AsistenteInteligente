"""Local development releases on the existing host authority and profile cut.

An explicit verified recovery copy must match the present portable sources. No
backup, migration, restart, credential discovery or restored admission is hidden
inside updating a release. All reads/effects remain in the shared worker owner.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, replace

from jarvis_core.host_profile import freeze_sources
from jarvis_core.local_release_update import (
    ProfilePin,
    ReleaseError,
    apply_release,
    inspect_installation,
    review_release,
    review_rollback,
)
from jarvis_core.maintenance_journal import wire
from jarvis_core.recovery import (
    PORTABLE_CONFIG,
    RecoveryContext,
    RecoveryLimits,
    RecoveryReceipt,
    _json,
    _snapshot,
    _verified,
    canonical,
)
from jarvis_core.recovery_fs import Directory, digest
from jarvis_core.recovery_sqlite import deny_extension_loading, inspect_sqlite_snapshot

_UPDATE = "release.update.apply"
_ROLLBACK = "release.rollback.apply"


@dataclass(frozen=True)
class _ReleasePrepared:
    core: object
    backup_path: object
    verification: object


def _child_context(context):
    return RecoveryContext(
        context.operation_id,
        context.profile_id,
        context.generation,
        context.check_current,
        context.cancellation,
        context.deadline,
        clock=context.clock,
    )


def _canonical_ram_hash(role, data, context, limits):
    """Hash every validated byte after the SAME SQLite backup API on both sides.

    Hot online snapshots and cold durable images can have different generated
    schema cookies after restart. canonical_RAM_portable_resource_hashes does
    not patch a header, skip offsets or replace the original artifact hashes.
    """

    def check():
        context.checkpoint("release.canonical_ram_resource")

    inspect_sqlite_snapshot(role, data, check_current=check, limits=limits.sqlite())
    source = sqlite3.connect(":memory:", isolation_level=None)
    target = None
    try:
        deny_extension_loading(source)
        source.deserialize(data)
        source.execute("PRAGMA trusted_schema=OFF")
        page_size = source.execute("PRAGMA page_size").fetchone()[0]
        target = sqlite3.connect(":memory:", isolation_level=None)
        deny_extension_loading(target)

        def progress(_status, _remaining, pages):
            check()
            if pages * page_size > limits.max_sqlite_bytes:
                raise ReleaseError("release_canonical_image_limit")

        check()
        source.backup(target, pages=128, progress=progress, sleep=0)
        result = target.serialize()
        if len(result) > limits.max_sqlite_bytes:
            raise ReleaseError("release_canonical_image_limit")
        inspect_sqlite_snapshot(role, result, check_current=check, limits=limits.sqlite())
        check()
        return digest(result)
    finally:
        if target is not None:
            target.close()
        source.close()


def _pin_current_sources(frozen, cut, context, path, expected, *, maintenance_purposes=()):
    """Canonical RAM resource comparison with exact original artifact integrity.

    The backup's profile/generation records historical host provenance; a lease
    receives another UUID on restart. Current physical identity, generation and
    fingerprint belong to the new pin. All original artifact byte hashes remain
    exact; ALL portable resources additionally match after identical RAM backup
    normalization on both SQLite images (JSON uses its exact canonical hash).
    No source SQL pathname is opened;
    snapshots come solely from the same frozen cut exporter.
    """
    verifying = _child_context(context)
    verifying.begin()
    limits = RecoveryLimits()
    with Directory(path, expected=expected.root_identity) as directory:
        verified, _manifest, resources = _verified(
            directory, verifying, limits, expected_hash=expected.manifest_sha256
        )
    if verified != expected:
        raise ReleaseError("release_recovery_copy_stale")
    reading = _child_context(context)
    reading.begin()
    bundle = frozen.bind(cut, context=reading)
    hashes = {"configuration_portable": digest(canonical(PORTABLE_CONFIG))}
    schemas = {role: 0 for role in ("state", "memory", "schedules")}
    expected_hashes = {}
    for role, data in resources.items():
        expected_hashes[role] = (
            _canonical_ram_hash(role, data, reading, limits) if role in schemas else digest(data)
        )
    for source in bundle.sqlite:
        data = _snapshot(source, reading, limits, bundle.root)
        hashes[source.role] = _canonical_ram_hash(source.role, data, reading, limits)
        schemas[source.role] = int.from_bytes(data[60:64], "big")
    for source in bundle.json:
        source.check_current()
        hashes[source.role] = digest(canonical(_json(source.role, source.data)))
    frozen.assert_current(cut, full=True)
    if hashes != expected_hashes:
        raise ReleaseError("release_recovery_copy_does_not_match_current_profile")
    return ProfilePin(
        frozen.scope.profile_id,
        frozen.scope.generation,
        frozen.root_identity,
        frozen.fingerprint,
        tuple(sorted(schemas.items())),
        verified.manifest_sha256,
        maintenance_purposes,
    )


def _common_receipt(detail, review, context):
    status = "unknown" if detail.status == "uncertain" else detail.status
    if status == "failed" and context.cancellation.is_set():
        status = "cancelled"
    return RecoveryReceipt(
        context.operation_id,
        review.kind,
        status,
        detail.effect_status,
        review.payload.core.version_key,
        manifest_sha256=detail.manifest_sha256,
        target_identity=review.payload.core.install_identity,
        error_code=detail.error_code,
        cleanup_pending=detail.cleanup_pending,
        cleanup_error_code="release_cleanup_pending" if detail.cleanup_pending else None,
    )


class ReleaseManagement:
    def _release_required_purposes(self, cut):
        cut.assert_current()
        values = {row["purpose"] for row in self.journal.document.read()["operations"]}
        values.update((_UPDATE, _ROLLBACK))
        cut.assert_current()
        return tuple(sorted(values))

    def release_handlers(self):
        return {
            "maintenance.release.inspect": self.release_inspect,
            "maintenance.release.review": self.release_review,
            "maintenance.release.apply": self.release_apply,
            "maintenance.release.rollback.review": self.release_rollback_review,
            "maintenance.release.rollback.apply": self.release_rollback_apply,
            "maintenance.release.resolve_launch": self.release_resolve_launch,
        }

    def release_workers(self):
        return {_UPDATE: self._release_effect, _ROLLBACK: self._release_effect}

    def _release_verifier(self):
        # An optional trusted, bounded platform callback is configured by the
        # host, never imported from the selected release or supplied over RPC.
        return getattr(self, "release_authenticity_verifier", None)

    def _release_verified_copy(self, identity):
        from jarvis_core.maintenance import _id
        from jarvis_core.protocol import RpcError

        item = self._verification.get(_id(identity))
        if item is None:
            raise RpcError(-32040, "Verifica primero una copia actual de este perfil.")
        return item

    @staticmethod
    def _release_overlap(paths, forbidden):
        for path in paths:
            if any(
                path == root or path.is_relative_to(root) or root.is_relative_to(path)
                for root in forbidden
            ):
                raise ReleaseError("release_paths_overlap_profile_or_recovery_copy")

    def _release_read_context(self, cut, operation, requested, frozen=None):
        def check(stage, write=False):
            if frozen is None:
                cut.assert_current()
            else:
                frozen.assert_current(cut)
            operation.stage = stage

        return RecoveryContext(
            operation.operation_id,
            cut.scope.profile_id,
            cut.scope.generation,
            check,
            operation.cancellation,
            requested + 90,
            clock=self.clock,
        )

    async def release_review(self, params):
        return self._release_review(params, rollback=False)

    async def release_rollback_review(self, params):
        return self._release_review(params, rollback=True)

    def _release_review(self, params, *, rollback):
        from jarvis_core.maintenance import _params, _path
        from jarvis_core.recovery_extensions import ExtensionPrepared

        required = {"install_root", "verification_id"} | (set() if rollback else {"artifact_path"})
        _params(params, required, required)
        installation = _path(params["install_root"])
        artifact = None if rollback else _path(params["artifact_path"])
        backup_path, verified = self._release_verified_copy(params["verification_id"])
        requested, verifier = self.clock(), self._release_verifier()

        def read(cut, operation):
            frozen = freeze_sources(self.service, cut)
            self._release_overlap(
                (installation,) if rollback else (installation, artifact),
                (*frozen.forbidden_roots, backup_path),
            )
            if artifact is not None:
                self._release_overlap((artifact,), (installation,))
            context = self._release_read_context(cut, operation, requested, frozen)
            pin = _pin_current_sources(
                frozen,
                cut,
                context,
                backup_path,
                verified,
                maintenance_purposes=self._release_required_purposes(cut),
            )
            core = (
                review_rollback(
                    installation, _child_context(context), pin, authenticity_verifier=verifier
                )
                if rollback
                else review_release(
                    artifact,
                    installation,
                    _child_context(context),
                    pin,
                    authenticity_verifier=verifier,
                )
            )
            metadata = {
                **wire(core.metadata()),
                "kind": _ROLLBACK if rollback else _UPDATE,
                "source": wire(frozen.metadata),
                "provider_used": False,
                "backup_created_by_this_operation": False,
                "recovery_copy": {
                    "path": str(backup_path),
                    "manifest_sha256": verified.manifest_sha256,
                    "artifact_id": verified.artifact_id,
                    "root_identity": wire(verified.root_identity),
                },
                "source_profile_and_recovery_copy_match": True,
                "profile_data_modified": False,
                "credentials_included": False,
            }
            return ExtensionPrepared(
                _ROLLBACK if rollback else _UPDATE,
                frozen,
                None,
                _ReleasePrepared(core, backup_path, verified),
                metadata,
            )

        return self._read_operation(
            "release.rollback.review" if rollback else "release.review", read
        )

    async def release_apply(self, params):
        return self._extension_start(params, _UPDATE)

    async def release_rollback_apply(self, params):
        return self._extension_start(params, _ROLLBACK)

    def _release_effect(self, review, context, cut, operation):
        from jarvis_core.recovery_extensions import ExtensionOutcome

        payload = review.payload
        pin = _pin_current_sources(
            review.frozen,
            cut,
            context,
            payload.backup_path,
            payload.verification,
            maintenance_purposes=self._release_required_purposes(cut),
        )
        if pin != payload.core.profile_pin:
            raise ReleaseError("release_profile_pin_changed")
        original_guard = context.check_current

        def guard(stage, write=False):
            # The journal is not part of the portable backup. Its own awaited/
            # working records were anticipated in the required set; a different
            # purpose appearing after pinning must still stop the first effect.
            if write or stage == "begin":
                if self._release_required_purposes(cut) != pin.maintenance_purposes:
                    raise ReleaseError("release_profile_pin_changed")
            original_guard(stage, write)

        context = replace(context, check_current=guard)
        try:
            detail = apply_release(
                payload.core, context, authenticity_verifier=self._release_verifier()
            )
        except ReleaseError as error:
            receipt = (
                None if error.receipt is None else _common_receipt(error.receipt, review, context)
            )
            raise ReleaseError(error.code, owner=error.owner, receipt=receipt) from None
        return ExtensionOutcome(_common_receipt(detail, review, context), {"release": wire(detail)})

    async def release_inspect(self, params):
        from jarvis_core.maintenance import _params, _path

        _params(params, {"install_root"}, {"install_root"})
        root, requested = _path(params["install_root"]), self.clock()

        def read(cut, operation):
            frozen = freeze_sources(self.service, cut)
            self._release_overlap((root,), frozen.forbidden_roots)
            context = self._release_read_context(cut, operation, requested, frozen)
            return {"release_installation": wire(inspect_installation(root, context))}

        return self._read_operation("release.inspect", read, allow_cleanup=True)

    async def release_resolve_launch(self, params):
        from jarvis_core.local_release_launch import resolve_launch
        from jarvis_core.maintenance import _HASH, _params, _path
        from jarvis_core.protocol import RpcError

        _params(
            params,
            {"install_root", "verification_id", "expected_current_sha256"},
            {"install_root", "verification_id"},
        )
        expected = params.get("expected_current_sha256")
        if expected is not None and (type(expected) is not str or not _HASH.fullmatch(expected)):
            raise RpcError(-32602, "El hash de la versión seleccionada no es válido.")
        root, requested = _path(params["install_root"]), self.clock()
        path, verified = self._release_verified_copy(params["verification_id"])

        def read(cut, operation):
            frozen = freeze_sources(self.service, cut)
            self._release_overlap((root,), (*frozen.forbidden_roots, path))
            context = self._release_read_context(cut, operation, requested, frozen)
            pin = _pin_current_sources(
                frozen,
                cut,
                context,
                path,
                verified,
                maintenance_purposes=self._release_required_purposes(cut),
            )
            plan = resolve_launch(
                root, _child_context(context), pin, expected_current_sha256=expected
            )
            return {"release_launch": wire(plan.metadata())}

        return self._read_operation("release.resolve_launch", read)
