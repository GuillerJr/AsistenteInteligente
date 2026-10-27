"""Resolve both native/Python entrypoints from one fully verified local CURRENT.

Resolution never starts a service or admits a restored profile. The caller owns
the current profile/recovery-copy pin and a separate explicit launch decision.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from jarvis_core import local_release_update as core
from jarvis_core.cold_sqlite import inspect_cold_family
from jarvis_core.recovery import RecoveryLimits


@dataclass(frozen=True)
class LaunchPlan:
    install_root: Path
    release_root: Path
    version_key: str
    manifest_sha256: str
    current_sha256: str
    native_executable: Path
    python_source: Path
    profile_pin: core.ProfilePin
    maintenance_purposes: tuple[str, ...]

    def metadata(self):
        return {
            "install_root": str(self.install_root),
            "release_root": str(self.release_root),
            "version_key": self.version_key,
            "manifest_sha256": self.manifest_sha256,
            "current_sha256": self.current_sha256,
            "native_executable": str(self.native_executable),
            "python_source": str(self.python_source),
            "profile_pin": self.profile_pin.metadata(),
            "maintenance_purposes": list(self.maintenance_purposes),
            "payload_verified": True,
            "same_release_source_and_native": True,
            "release_authenticity": "not_established",
            "channel": "development",
            "external_python_required": True,
            "external_official_codex_required": True,
            "launch_performed": False,
            "restored_profile_activation_requires_owner": True,
        }

    def arguments(self, *, python, codex, state, dependencies=None):
        values = [Path(value) for value in (python, codex, state)]
        if dependencies is not None:
            values.append(Path(dependencies))
        if any(
            not path.is_absolute() or ".." in path.parts or "\0" in str(path) for path in values
        ):
            raise core.ReleaseError("release_launch_arguments_invalid")
        arguments = [
            str(self.native_executable),
            "--python",
            str(values[0]),
            "--source",
            str(self.python_source),
            "--state",
            str(values[2]),
            "--codex",
            str(values[1]),
        ]
        if dependencies is not None:
            arguments.extend(("--dependencies", str(values[3])))
        return arguments


def read_closed_maintenance_journal(profile_root, context, lease):
    """Only the fixed bounded private journal; no startup, mutation or replay."""
    from jarvis_core.maintenance_journal import _valid

    owner = core.ReleaseOwner()
    try:
        lease.assert_current()
        directory = core._Dir(profile_root, owner, private=True)
        if directory.identity != lease.identity:
            raise core.ReleaseError("release_profile_pin_changed")
        try:
            data, metadata = core._read(
                directory,
                "maintenance.json",
                1048576,
                lambda: context.checkpoint("release.launch_journal"),
            )
        except FileNotFoundError:
            result = {"maintenance_purposes": (), "fingerprint": None}
        else:
            if metadata[2] & 0o7777 != 0o600:
                raise core.ReleaseError("release_launch_journal_unsafe")
            value = core._decode(data)
            _valid(value)
            result = {
                "maintenance_purposes": tuple(
                    sorted({row["purpose"] for row in value["operations"]})
                ),
                "fingerprint": (metadata, core._sha(data)),
            }
        lease.assert_current()
        owner.cleanup()
        return result
    except BaseException as error:
        try:
            owner.cleanup()
        except BaseException:
            pass
        raise core._wrap(error, owner) from None


def validate_profile_for_launch(
    profile_root, context, profile_pin, lease, *, maintenance_purposes=None
):
    """Validate the three fixed cold schema images while the selected lease holds.

    The returned families must be checked again immediately before handoff. No
    message content is returned, no original SQLite is opened, and no store is
    initialized merely to discover its schema. WAL/journal uses private capture.
    A capture failure retains its original cleanup owner in the raised error.
    """
    context.begin()
    lease.assert_current()
    if lease.identity != profile_pin.root_identity or Path(profile_root) != lease.root:
        raise core.ReleaseError("release_profile_pin_changed")
    required = read_closed_maintenance_journal(profile_root, context, lease)["maintenance_purposes"]
    if not set(required) <= set(profile_pin.maintenance_purposes):
        raise core.ReleaseError("release_launch_maintenance_purposes_changed")
    if maintenance_purposes is not None and not set(profile_pin.maintenance_purposes) <= set(
        maintenance_purposes
    ):
        raise core.ReleaseError("release_maintenance_purposes_incompatible")
    limits = RecoveryLimits()
    families = tuple(
        inspect_cold_family(Path(profile_root) / name, role)
        for role, name in (
            ("state", "jarvis.sqlite3"),
            ("memory", "memory.sqlite3"),
            ("schedules", "schedules.sqlite3"),
        )
    )
    if sum(family.physical_bytes for family in families) > limits.max_total_bytes or any(
        family.physical_bytes > limits.max_sqlite_bytes for family in families
    ):
        raise core.ReleaseError("release_launch_profile_size_limit")
    schemas = {}
    for family in families:
        context.checkpoint("release.launch_profile_schema")
        lease.assert_current()
        if family.members[0] is None:
            schemas[family.role] = 0
        else:
            data, _ = family.capture_image(context, limits, lease.assert_current)
            schemas[family.role] = int.from_bytes(data[60:64], "big")
        family.assert_current()
    if schemas != dict(profile_pin.schemas):
        raise core.ReleaseError("release_launch_profile_schema_changed")
    lease.assert_current()
    for family in families:
        family.assert_current()
    return families


def resolve_launch(
    install_root, context, profile_pin, *, expected_current_sha256=None, limits=None
):
    """Read only: full payload check, not merely a pointer or manifest SHA check."""
    limits = limits or core.ReleaseLimits()
    owner = core.ReleaseOwner()
    context.begin()
    try:
        root = core._Dir(install_root, owner, private=True)
        if root.identity == profile_pin.root_identity:
            raise core.ReleaseError("release_install_root_is_profile")
        state = core._installation(root, context, limits)
        if state["pending"] is not None:
            raise core.ReleaseError("release_pending_requires_reconciliation")
        if expected_current_sha256 is not None and (
            not core._hash(expected_current_sha256)
            or state["current_sha256"] != expected_current_sha256
        ):
            raise core.ReleaseError("release_current_changed")
        current = state["current"]
        if current is None:
            raise core.ReleaseError("release_no_current_version")
        artifact = root.child("releases").child(current["release_key"])
        raw, manifest, _ = core._artifact(artifact, context, limits, profile_pin)
        if (
            core._sha(raw) != current["manifest_sha256"]
            or manifest["version_key"] != current["release_key"]
        ):
            raise core.ReleaseError("release_current_payload_changed")
        context.checkpoint("release.launch_plan_complete")
        if core._control(root, "CURRENT", context)[1] != state["current_sha256"]:
            raise core.ReleaseError("release_current_changed")
        plan = LaunchPlan(
            root.path,
            artifact.path,
            manifest["version_key"],
            core._sha(raw),
            state["current_sha256"],
            artifact.path / core._ENTRYPOINTS["native"],
            artifact.path / "python",
            profile_pin,
            tuple(manifest["maintenance_purposes"]),
        )
        owner.cleanup()
        return plan
    except BaseException as error:
        try:
            owner.cleanup()
        except BaseException:
            pass
        raise core._wrap(error, owner) from None
