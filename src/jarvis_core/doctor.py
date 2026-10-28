"""Host-scoped, non-mutating inspection; never opens a production store.

The host supplies the selected inventory, physical identity and an already-held
coherence barrier. This module creates no lease, permission, directory, journal,
snapshot or repair. All SQLite inspection consumes bounded bytes from a pinned
descriptor, through the shared pure recovery inspector, not a filesystem VFS.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import stat
import time
from collections.abc import Callable, Mapping
from dataclasses import asdict, dataclass, field
from pathlib import Path
from types import MappingProxyType
from typing import Protocol
from uuid import uuid4

from jarvis_core.automation_flows import validate_automation_document
from jarvis_core.plugins import _validate as validate_plugins
from jarvis_core.profiles import _validate as validate_agents
from jarvis_core.skills import _validate as validate_skills
from jarvis_core.telegram import _journal_validate as validate_telegram

_MAX_SAFE = (1 << 52) - 1
_SQLITE = {
    "state": (0x4A525653, 2),
    "memory": (0x4A524D4D, 1),
    "schedules": (0x4A525343, 2),
}
_PATHS = {
    "state": ("jarvis.sqlite3",),
    "memory": ("memory.sqlite3",),
    "schedules": ("schedules.sqlite3",),
    "agents": ("profiles", "agents.json"),
    "skills": ("profiles", "skills.json"),
    "plugins": ("profiles", "plugins.json"),
    "telegram": ("telegram", "telegram.json"),
    "automations": ("automation.json",),
}
_ROLES = (*_PATHS, "configuration_portable", "restore_record")
_COUNTS = frozenset(
    {
        "sessions",
        "runs",
        "messages",
        "events",
        "scopes",
        "documents",
        "chunks",
        "metadata",
        "memory_scope_copies",
        "memory_scope_copy_documents",
        "schedules",
        "occurrences",
        "wake_events",
        "automation_hooks",
        "enabled_automation_hooks",
        "automation_flows",
        "paused_automation_flows",
        "unsettled_automation_flows",
        "unsettled_runs",
        "unsettled_occurrences",
    }
)
_BLOCKERS = frozenset(
    {
        "runs_active",
        "cleanup_pending",
        "scheduler_running",
        "telegram_running",
        "pairing_running",
        "processes_active",
        "pending_effects",
        "profile_busy",
        "generation_changed",
        "inventory_incomplete",
    }
)


class Cancellation(Protocol):
    def is_set(self) -> bool: ...


class DoctorError(ValueError):
    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


class _Stop(DoctorError):
    pass


class _ImagesRejected(DoctorError):
    pass


def _scope_id(value):
    return (
        isinstance(value, str)
        and re.fullmatch(r"[A-Za-z0-9_-]{1,128}", value) is not None
    )


def _physical_identity(value):
    return (
        isinstance(value, tuple)
        and len(value) == 2
        and all(type(item) is int and 0 <= item < (1 << 64) for item in value)
    )


@dataclass(frozen=True)
class DoctorSqliteImage:
    """Host-only RAM value; never a file path, command, authorization or RPC payload."""

    role: str
    disposition: str
    source_identity: tuple[int, int] | None = None
    data: bytes | None = field(default=None, repr=False)
    sha256: str | None = None

    def __post_init__(self):
        if not isinstance(self.role, str) or self.role not in _SQLITE:
            raise DoctorError("doctor_image_invalid")
        if self.disposition == "image":
            valid = (
                _physical_identity(self.source_identity)
                and type(self.data) is bytes
                and isinstance(self.sha256, str)
                and re.fullmatch(r"[a-f0-9]{64}", self.sha256) is not None
            )
        elif self.disposition == "absent":
            valid = (
                self.source_identity is None
                and self.data is None
                and self.sha256 is None
            )
        elif self.disposition == "unavailable":
            valid = (
                _physical_identity(self.source_identity)
                and self.data is None
                and self.sha256 is None
            )
        else:
            valid = False
        if not valid:
            raise DoctorError("doctor_image_invalid")


@dataclass(frozen=True)
class DoctorSqliteImages:
    profile_id: str
    generation: int
    profile_identity: tuple[int, int]
    cut_id: str
    images: tuple[DoctorSqliteImage, ...]

    def __post_init__(self):
        if (
            not _scope_id(self.profile_id)
            or type(self.generation) is not int
            or not 0 <= self.generation <= _MAX_SAFE
            or not _physical_identity(self.profile_identity)
            or not _scope_id(self.cut_id)
            or not isinstance(self.images, tuple)
            or len(self.images) > len(_SQLITE)
            or any(type(image) is not DoctorSqliteImage for image in self.images)
            or len({image.role for image in self.images}) != len(self.images)
        ):
            raise DoctorError("doctor_images_invalid")


@dataclass(frozen=True)
class DoctorLimits:
    sqlite_bytes: int = 64 * 1024 * 1024
    json_bytes: int = 4 * 1024 * 1024
    total_bytes: int = 128 * 1024 * 1024
    timeout_seconds: float = 20

    def __post_init__(self):
        for value, maximum in (
            (self.sqlite_bytes, 64 * 1024 * 1024),
            (self.json_bytes, 4 * 1024 * 1024),
            (self.total_bytes, 128 * 1024 * 1024),
        ):
            if type(value) is not int or not 100 <= value <= maximum:
                raise DoctorError("doctor_limits_invalid")
        if (
            type(self.timeout_seconds) not in (int, float)
            or not math.isfinite(self.timeout_seconds)
            or not 0 < self.timeout_seconds <= 25
        ):
            raise DoctorError("doctor_limits_invalid")


@dataclass(frozen=True)
class DoctorInventory:
    profile_id: str
    generation: int
    root: Path
    profile_identity: tuple[int, int] | None
    coherence: str
    roles: tuple[str, ...] = _ROLES[:-1]
    blockers: tuple[str, ...] = ()
    configuration_metadata: Mapping[str, object] | None = field(
        default=None, repr=False
    )
    cut_id: str | None = None

    def __post_init__(self):
        if self.cut_id is not None and not _scope_id(self.cut_id):
            raise DoctorError("doctor_inventory_invalid")
        if (
            not isinstance(self.profile_id, str)
            or re.fullmatch(r"[A-Za-z0-9_-]{1,128}", self.profile_id) is None
        ):
            raise DoctorError("doctor_inventory_invalid")
        if type(self.generation) is not int or not 0 <= self.generation <= _MAX_SAFE:
            raise DoctorError("doctor_inventory_invalid")
        if (
            not isinstance(self.root, Path)
            or not self.root.is_absolute()
            or ".." in self.root.parts
            or "\0" in str(self.root)
        ):
            raise DoctorError("doctor_inventory_invalid")
        if self.profile_identity is not None and (
            not isinstance(self.profile_identity, tuple)
            or len(self.profile_identity) != 2
            or any(
                type(v) is not int or not 0 <= v < (1 << 64)
                for v in self.profile_identity
            )
        ):
            raise DoctorError("doctor_inventory_invalid")
        if not isinstance(self.coherence, str) or self.coherence not in {
            "coherent_snapshot",
            "offline_quiescent",
            "unverified",
        }:
            raise DoctorError("doctor_inventory_invalid")
        if (
            not isinstance(self.roles, tuple)
            or not self.roles
            or len(self.roles) > 16
            or any(
                not isinstance(role, str) or role not in _ROLES for role in self.roles
            )
            or len(set(self.roles)) != len(self.roles)
        ):
            raise DoctorError("doctor_inventory_invalid")
        if (
            not isinstance(self.blockers, tuple)
            or len(self.blockers) > 16
            or any(
                not isinstance(value, str) or len(value) > 128
                for value in self.blockers
            )
        ):
            raise DoctorError("doctor_inventory_invalid")
        if self.configuration_metadata is not None:
            if (
                not isinstance(self.configuration_metadata, Mapping)
                or len(self.configuration_metadata) > 8
            ):
                raise DoctorError("doctor_inventory_invalid")
            object.__setattr__(
                self,
                "configuration_metadata",
                MappingProxyType(dict(self.configuration_metadata)),
            )


class DoctorContext:
    def __init__(
        self,
        check_current: Callable[..., None],
        cancellation: Cancellation,
        *,
        limits: DoctorLimits | None = None,
        deadline: float | None = None,
        clock: Callable[[], float] = time.monotonic,
    ):
        self.limits = limits or DoctorLimits()
        if not callable(check_current) or not callable(
            getattr(cancellation, "is_set", None)
        ):
            raise DoctorError("doctor_context_invalid")
        self._guard, self._cancellation, self._clock = (
            check_current,
            cancellation,
            clock,
        )
        now = clock()
        if type(now) not in (int, float) or not math.isfinite(now):
            raise DoctorError("doctor_context_invalid")
        if deadline is not None and (
            type(deadline) not in (int, float) or not math.isfinite(deadline)
        ):
            raise DoctorError("doctor_context_invalid")
        self.deadline = (
            min(now + self.limits.timeout_seconds, deadline)
            if deadline is not None
            else now + self.limits.timeout_seconds
        )
        self.stop_reason: str | None = None
        self.bytes_read = 0

    def checkpoint(self, stage: str) -> None:
        if self.stop_reason is not None:
            raise _Stop(self.stop_reason)
        if self._cancellation.is_set():
            self.stop_reason = "doctor_cancelled"
        elif self._clock() >= self.deadline:
            self.stop_reason = "doctor_deadline"
        if self.stop_reason is not None:
            raise _Stop(self.stop_reason)
        try:
            result = self._guard(stage, write=False)
            if result is not None:
                raise _Stop("doctor_barrier_changed")
        except Exception:
            self.stop_reason = "doctor_barrier_changed"
            raise _Stop(self.stop_reason) from None
        if self._cancellation.is_set() or self._clock() >= self.deadline:
            self.stop_reason = (
                "doctor_cancelled" if self._cancellation.is_set() else "doctor_deadline"
            )
            raise _Stop(self.stop_reason)

    def admit_bytes(self, count: int):
        self.checkpoint("read_budget")
        if self.bytes_read + count > self.limits.total_bytes:
            raise DoctorError("doctor_byte_budget")
        self.bytes_read += count


@dataclass(frozen=True)
class DoctorCheck:
    role: str
    status: str
    code: str | None = None
    identity: tuple[str, str] | None = None
    permissions: str | None = None
    byte_length: int | None = None
    format_version: int | None = None
    application_id: int | None = None
    schema_version: int | None = None
    integrity: str = "not_checked"
    counts: tuple[tuple[str, int], ...] = ()
    inspection_source: str = "metadata"
    snapshot_byte_length: int | None = None
    snapshot_sha256: str | None = None


@dataclass(frozen=True)
class DoctorReport:
    report_id: str
    profile_id: str
    generation: int
    status: str
    coherence: str
    profile_identity: tuple[str, str] | None
    checks: tuple[DoctorCheck, ...]
    blockers: tuple[str, ...]
    suggestions: tuple[str, ...]
    supported_repair_actions: tuple[str, ...] = ()
    effect: str = "read_only_diagnostic"

    def to_dict(self) -> dict:
        return asdict(self)


def _identity(info):
    return info.st_dev, info.st_ino


def _fingerprint(info):
    return (
        info.st_dev,
        info.st_ino,
        info.st_size,
        info.st_mtime_ns,
        info.st_ctime_ns,
        stat.S_IMODE(info.st_mode),
        info.st_uid,
        info.st_nlink,
    )


def _wire_identity(info):
    return str(info.st_dev), str(info.st_ino)


def _safe_directory(info):
    return (
        stat.S_ISDIR(info.st_mode)
        and info.st_uid == os.getuid()
        and stat.S_IMODE(info.st_mode) == 0o700
    )


def _open_root(path: Path) -> int:
    descriptor = os.open(path.anchor, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        for part in path.parts[1:]:
            child = os.open(
                part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=descriptor
            )
            os.close(descriptor)
            descriptor = child
        if not _safe_directory(os.fstat(descriptor)):
            raise DoctorError("doctor_profile_unsafe")
        return descriptor
    except BaseException:
        os.close(descriptor)
        raise


def _assert_root(inventory, root, context):
    context.checkpoint("profile_identity")
    try:
        current = _open_root(inventory.root)
    except (OSError, DoctorError):
        raise _Stop("doctor_profile_changed") from None
    try:
        if (
            _identity(os.fstat(current)) != inventory.profile_identity
            or _identity(os.fstat(root)) != inventory.profile_identity
        ):
            raise _Stop("doctor_profile_changed")
    finally:
        os.close(current)


def _open_parent(root, parts):
    descriptor = os.dup(root)
    try:
        for part in parts:
            child = os.open(
                part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=descriptor
            )
            os.close(descriptor)
            descriptor = child
            if not _safe_directory(os.fstat(descriptor)):
                raise DoctorError("doctor_directory_unsafe")
        return descriptor
    except BaseException:
        os.close(descriptor)
        raise


def _assert_parent(root, parent, parts):
    # Reopen the fixed, selected relative directory: the pinned old directory
    # alone cannot prove that a rename did not retarget profiles/ or telegram/.
    try:
        current = _open_parent(root, parts)
    except (OSError, DoctorError):
        raise _Stop("doctor_resource_changed") from None
    try:
        if _identity(os.fstat(current)) != _identity(os.fstat(parent)):
            raise _Stop("doctor_resource_changed")
    finally:
        os.close(current)


def _sidecars(parent, name):
    found = False
    for suffix in ("-wal", "-shm", "-journal"):
        try:
            info = os.stat(name + suffix, dir_fd=parent, follow_symlinks=False)
        except FileNotFoundError:
            continue
        if (
            not stat.S_ISREG(info.st_mode)
            or info.st_uid != os.getuid()
            or stat.S_IMODE(info.st_mode) != 0o600
            or info.st_nlink != 1
        ):
            raise DoctorError("doctor_sidecar_unsafe")
        found = True
    return found


def _object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise DoctorError("doctor_json_invalid")
        result[key] = value
    return result


def _json_check(role, data, context):
    context.checkpoint("json_validation")
    try:
        value = json.loads(
            data,
            object_pairs_hook=_object,
            parse_constant=lambda _: (_ for _ in ()).throw(
                DoctorError("doctor_json_invalid")
            ),
        )
        {
            "agents": validate_agents,
            "skills": validate_skills,
            "plugins": validate_plugins,
            "telegram": validate_telegram,
            "automations": validate_automation_document,
        }[role](value)
    except (ValueError, UnicodeError, RecursionError, KeyError, TypeError):
        raise DoctorError("doctor_json_invalid") from None
    context.checkpoint("json_validated")
    if role == "agents":
        counts = (("agents", len(value["agents"])),)
    elif role == "skills":
        counts = (
            ("skills", len(value["skills"])),
            ("enabled_skills", sum(row["enabled"] for row in value["skills"].values())),
        )
    elif role == "plugins":
        counts = (("plugins", len(value["plugins"])),
                  ("enabled_plugins", sum(row["enabled"] for row in value["plugins"].values())))
    elif role == "automations":
        counts = (
            ("automation_hooks", len(value["hooks"])),
            ("enabled_automation_hooks", sum(row["enabled"] for row in value["hooks"])),
            ("automation_flows", len(value["flows"])),
            ("paused_automation_flows", sum(row["status"] == "paused" for row in value["flows"])),
            ("unsettled_automation_flows", sum(row["status"] in {"queued", "running"}
                                               for row in value["flows"])),
        )
    else:
        counts = (
            ("routes", len(value["routes"])),
            ("events", len(value["events"])),
            ("control_messages", len(value.get("control_messages", {}))),
            (
                "unsettled_telegram_events",
                sum(
                    row["status"] in {"claimed", "dispatching", "send_pending"}
                    for row in value["events"].values()
                ),
            ),
        )
    if role == "agents":
        references = frozenset(value["agents"])
    elif role == "skills":
        references = frozenset(row["agent_id"] for row in value["skills"].values())
    elif role == "plugins":
        references = frozenset(row["agent_id"] for row in value["plugins"].values())
    elif role == "automations":
        references = frozenset(row["agent_id"] for row in value["hooks"] + value["flows"])
    else:
        references = tuple(
            (row["agent_id"], row["session_id"]) for row in value["routes"].values()
        )
    return (1 if role == "plugins" else value["version"]), tuple(sorted(counts)), references


def _sqlite_check(role, data, context, inspector):
    if len(data) < 100 or data[:16] != b"SQLite format 3\x00":
        raise DoctorError("doctor_sqlite_corrupt")
    if data[18] != 1 or data[19] != 1:
        raise DoctorError("doctor_needs_quiescent_snapshot")
    application_id, version = (
        int.from_bytes(data[68:72], "big"),
        int.from_bytes(data[60:64], "big"),
    )
    expected_app, expected_version = _SQLITE[role]
    if application_id == 0 and version == 0:
        # A foreign/default SQLite header does not prove an empty Jarvis
        # resource. Only a genuinely empty fixed role is uninitialized.
        raise DoctorError("doctor_sqlite_identity_missing")
    if application_id != expected_app:
        raise DoctorError("doctor_sqlite_identity_invalid")
    accepted_versions = {1, 2} if role in {"memory", "schedules"} else {expected_version}
    if version not in accepted_versions:
        raise DoctorError("doctor_schema_unsupported")
    if inspector is None:
        try:
            from jarvis_core.recovery_sqlite import inspect_sqlite_snapshot
        except ImportError:
            raise DoctorError("doctor_inspector_unavailable") from None
        inspector = inspect_sqlite_snapshot
    try:
        result = inspector(
            role, data, check_current=lambda: context.checkpoint("sqlite_validation")
        )
    except DoctorError:
        raise
    except ValueError as error:
        mapping = {
            "snapshot_schema_unsupported": "doctor_schema_invalid",
            "snapshot_image_not_standalone": "doctor_needs_quiescent_snapshot",
            "snapshot_work_limit": "doctor_sqlite_budget",
            "snapshot_row_limit": "doctor_sqlite_budget",
            "snapshot_cell_limit": "doctor_sqlite_budget",
            "snapshot_size_invalid": "doctor_sqlite_budget",
            "snapshot_integrity_failed": "doctor_sqlite_corrupt",
            "snapshot_foreign_key_failed": "doctor_sqlite_corrupt",
            "snapshot_memory_content_invalid": "doctor_sqlite_corrupt",
            "snapshot_memory_history_invalid": "doctor_sqlite_corrupt",
            "snapshot_memory_history_limit": "doctor_sqlite_budget",
            "snapshot_data_invalid": "doctor_sqlite_corrupt",
            "snapshot_invalid": "doctor_sqlite_corrupt",
            "snapshot_sqlite_capability_unavailable": "doctor_inspector_unavailable",
        }
        raise DoctorError(
            mapping.get(getattr(error, "code", None), "doctor_sqlite_unknown")
        ) from None
    context.checkpoint("sqlite_validated")
    if (
        result.application_id != expected_app
        or result.user_version != version
        or result.integrity != "ok"
    ):
        raise DoctorError("doctor_sqlite_identity_invalid")
    counts = tuple((key, count) for key, count in result.row_counts if key in _COUNTS)
    if any(
        type(count) is not int or not 0 <= count <= _MAX_SAFE for _, count in counts
    ):
        raise DoctorError("doctor_count_limit")
    if (
        sum(
            len(getattr(result, key, ()))
            for key in ("agent_ids", "session_ids", "memory_scopes", "session_agents")
        )
        > 8192
    ):
        raise DoctorError("doctor_reference_limit")
    return application_id, version, tuple(sorted(counts)), result


def _state(code):
    if code in {
        "doctor_file_unsafe",
        "doctor_directory_unsafe",
        "doctor_sidecar_unsafe",
        "doctor_sqlite_identity_invalid",
        "doctor_schema_invalid",
    }:
        return "unsafe"
    if code in {
        "doctor_sqlite_corrupt",
        "doctor_json_invalid",
        "doctor_snapshot_hash_mismatch",
        "snapshot_sqlite_corrupt",
        "snapshot_integrity_failed",
        "snapshot_foreign_key_failed",
    }:
        return "corrupt"
    if code == "doctor_sqlite_uninitialized":
        return "uninitialized"
    if code in {
        "doctor_needs_quiescent_snapshot",
        "doctor_byte_budget",
        "doctor_resource_limit",
        "doctor_count_limit",
        "doctor_reference_limit",
        "doctor_sqlite_budget",
        "doctor_snapshot_source_unavailable",
    }:
        return "blocked"
    return "unknown"


def _projection(inventory):
    value = inventory.configuration_metadata
    if value is None:
        return DoctorCheck(
            "configuration_portable",
            "unknown",
            "doctor_configuration_projection_missing",
        )
    if (
        not isinstance(value, Mapping)
        or set(value)
        != {"version", "workspace_selected", "binding_count", "mcp_server_count"}
        or type(value["version"]) is not int
        or value["version"] != 1
        or type(value["workspace_selected"]) is not bool
        or type(value["binding_count"]) is not int
        or not 0 <= value["binding_count"] <= 1000
        or type(value["mcp_server_count"]) is not int
        or not 0 <= value["mcp_server_count"] <= 8
    ):
        return DoctorCheck(
            "configuration_portable",
            "unknown",
            "doctor_configuration_projection_invalid",
        )
    return DoctorCheck(
        "configuration_portable",
        "ok",
        "doctor_host_projection_only",
        format_version=1,
        counts=(
            ("bindings_excluded", value["binding_count"]),
            ("mcp_commands_excluded", value["mcp_server_count"]),
        ),
    )


def _select_images(inventory, context, bundle):
    if bundle is None:
        return {}
    context.checkpoint("images_scope")
    if type(bundle) is not DoctorSqliteImages:
        raise _ImagesRejected("doctor_images_invalid")
    if inventory.coherence != "coherent_snapshot" or inventory.cut_id is None:
        raise _ImagesRejected("doctor_snapshot_mode_invalid")
    if (
        bundle.profile_id != inventory.profile_id
        or bundle.generation != inventory.generation
        or bundle.profile_identity != inventory.profile_identity
        or bundle.cut_id != inventory.cut_id
    ):
        raise _ImagesRejected("doctor_snapshot_scope_mismatch")
    images = {image.role: image for image in bundle.images}
    if set(images) != set(inventory.roles).intersection(_SQLITE):
        raise _ImagesRejected("doctor_snapshot_roles_mismatch")
    context.checkpoint("images_scoped")
    return images


def _host_image(image, context):
    context.checkpoint("image_budget")
    if len(image.data) > context.limits.sqlite_bytes:
        raise DoctorError("doctor_resource_limit")
    context.admit_bytes(len(image.data))
    digest = hashlib.sha256()
    view = memoryview(image.data)
    for start in range(0, len(view), 65536):
        context.checkpoint("image_hash")
        digest.update(view[start : start + 65536])
    if digest.hexdigest() != image.sha256:
        raise DoctorError("doctor_snapshot_hash_mismatch")
    context.checkpoint("image_hashed")
    return image.data


def _resource(inventory, root, role, context, inspector, facts, images):
    if role == "configuration_portable":
        return _projection(inventory)
    if role == "restore_record":
        from jarvis_core.activation import ActivationError, validate_bootstrap_admission
        try:
            context.checkpoint("restore_admission")
            admission = validate_bootstrap_admission(
                inventory.root, check_current=lambda: context.checkpoint("restore_admission"))
            return DoctorCheck(role, "ok" if admission is not None else "absent",
                               "doctor_local_admission_valid" if admission else None)
        except ActivationError as error:
            # The validator preserves callback codes in its safe wrapper. A
            # latched doctor cancellation/deadline/barrier failure stays a stop,
            # never a diagnosis that the admission itself is invalid.
            context.checkpoint("restore_admission_failed")
            code = ("doctor_activation_required" if error.code == "profile_activation_required"
                    else "doctor_restore_admission_invalid")
            return DoctorCheck(role, "blocked", code)
    parent = descriptor = None
    base = {}
    image = images.get(role)
    try:
        context.checkpoint("resource_metadata")
        parts = _PATHS[role]
        parent = _open_parent(root, parts[:-1])
        name = parts[-1]
        descriptor = os.open(
            name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent
        )
        info = os.fstat(descriptor)
        if (
            not stat.S_ISREG(info.st_mode)
            or info.st_uid != os.getuid()
            or stat.S_IMODE(info.st_mode) != 0o600
            or info.st_nlink != 1
        ):
            raise DoctorError("doctor_file_unsafe")
        base = {
            "identity": _wire_identity(info),
            "permissions": "0600",
            "byte_length": info.st_size,
        }
        if image is not None and (
            image.disposition == "absent" or image.source_identity != _identity(info)
        ):
            raise _Stop("doctor_snapshot_source_changed")
        limit = (
            context.limits.sqlite_bytes
            if role in _SQLITE
            else min(
                context.limits.json_bytes,
                524288 if role == "agents" else context.limits.json_bytes,
            )
        )
        if info.st_size > limit:
            raise DoctorError("doctor_resource_limit")
        if inventory.coherence == "unverified" or inventory.blockers:
            raise DoctorError("doctor_needs_quiescent_snapshot")
        if image is not None and image.disposition == "unavailable":
            raise DoctorError("doctor_snapshot_source_unavailable")
        if image is None and role in _SQLITE and _sidecars(parent, name):
            raise DoctorError("doctor_needs_quiescent_snapshot")
        if image is not None:
            data = _host_image(image, context)
            base.update(
                inspection_source="host_ram_snapshot",
                snapshot_byte_length=len(data),
                snapshot_sha256=image.sha256,
            )
        elif info.st_size == 0:
            return DoctorCheck(
                role, "uninitialized", "doctor_resource_uninitialized", **base
            )
        else:
            if context.bytes_read + info.st_size > context.limits.total_bytes:
                raise DoctorError("doctor_byte_budget")
            data = bytearray()
            while len(data) < info.st_size:
                context.checkpoint("resource_read")
                part = os.read(descriptor, min(65536, info.st_size - len(data)))
                if not part:
                    break
                context.admit_bytes(len(part))
                data.extend(part)
                if len(data) > limit:
                    raise DoctorError("doctor_resource_limit")
            if len(data) != info.st_size:
                raise _Stop("doctor_resource_changed")
            base["inspection_source"] = (
                "standalone_file" if role in _SQLITE else "selected_json"
            )
        if _fingerprint(os.fstat(descriptor)) != _fingerprint(info):
            raise _Stop("doctor_resource_changed")
        if role in _SQLITE:
            if len(data) >= 100 and data[:16] == b"SQLite format 3\x00":
                base.update(
                    application_id=int.from_bytes(data[68:72], "big"),
                    schema_version=int.from_bytes(data[60:64], "big"),
                )
            _app, _version, counts, references = _sqlite_check(
                role, bytes(data), context, inspector
            )
            check = DoctorCheck(
                role,
                "ok",
                integrity="ok",
                counts=counts,
                **base,
            )
        else:
            version, counts, references = _json_check(role, bytes(data), context)
            check = DoctorCheck(
                role, "ok", format_version=version, counts=counts, **base
            )
        context.checkpoint("resource_revalidated")
        _assert_parent(root, parent, parts[:-1])
        if _fingerprint(os.fstat(descriptor)) != _fingerprint(info) or _fingerprint(
            os.stat(name, dir_fd=parent, follow_symlinks=False)
        ) != _fingerprint(info):
            raise _Stop("doctor_resource_changed")
        if image is None and role in _SQLITE and _sidecars(parent, name):
            raise _Stop("doctor_resource_changed")
        facts[role] = references
        if any(key.startswith("unsettled_") and count for key, count in check.counts):
            return DoctorCheck(
                role,
                "blocked",
                "doctor_unsettled_state",
                **{
                    k: v
                    for k, v in asdict(check).items()
                    if k not in {"role", "status", "code"}
                },
            )
        return check
    except FileNotFoundError:
        if image is not None and image.disposition != "absent":
            raise _Stop("doctor_snapshot_source_changed") from None
        if descriptor is not None:
            raise _Stop("doctor_resource_changed") from None
        return DoctorCheck(role, "absent", "doctor_resource_absent")
    except _Stop:
        raise
    except DoctorError as error:
        return DoctorCheck(role, _state(error.code), error.code, **base)
    except OSError:
        return DoctorCheck(role, "unsafe", "doctor_resource_unavailable", **base)
    except Exception:
        # Inspector diagnostics and JSON content never become public error text.
        if context.stop_reason:
            raise _Stop(context.stop_reason) from None
        return DoctorCheck(role, "unknown", "doctor_resource_inspection_failed", **base)
    finally:
        if descriptor is not None:
            os.close(descriptor)
        if parent is not None:
            os.close(parent)


def _cross_references(facts):
    if not facts:
        return None
    agents = facts.get("agents")
    required = set(facts.get("skills", ()))
    required.update(facts.get("plugins", ()))
    required.update(facts.get("automations", ()))
    state = facts.get("state")
    if state is not None:
        required.update(state.agent_ids)
    schedules = facts.get("schedules")
    if schedules is not None:
        # The shared inspector exposes definition agents, not historical
        # occurrences: deleting their former agent can be valid own state.
        required.update(schedules.agent_ids)
    memory = facts.get("memory")
    if memory is not None:
        required.update(memory.agent_ids)
    routes = facts.get("telegram", ())
    required.update(agent for agent, _ in routes)
    if required and agents is None:
        return DoctorCheck(
            "cross_references", "unknown", "doctor_reference_sources_missing"
        )
    if agents is not None and not required.issubset(agents):
        return DoctorCheck("cross_references", "corrupt", "doctor_references_invalid")
    if routes:
        if state is None or not hasattr(state, "session_agents"):
            return DoctorCheck(
                "cross_references", "unknown", "doctor_reference_sources_missing"
            )
        sessions = dict(state.session_agents)
        if any(sessions.get(session) != agent for agent, session in routes):
            return DoctorCheck(
                "cross_references", "corrupt", "doctor_references_invalid"
            )
    # Parent session IDs and MemoryScope.session_id are provenance, not FKs.
    # They are never rewritten or rejected merely because a session was deleted.
    return DoctorCheck(
        "cross_references",
        "ok",
        counts=(("agent_references", len(required)), ("route_bindings", len(routes))),
    )


def inspect_report(
    inventory: DoctorInventory,
    context: DoctorContext,
    *,
    sqlite_images: DoctorSqliteImages | None = None,
    sqlite_inspector=None,
) -> DoctorReport:
    """Synchronous worker entry. The host retains its barrier until this settles."""
    checks = []
    facts = {}
    root = None
    identity = None
    blockers = tuple(
        sorted(
            {
                code if code in _BLOCKERS else "host_blocked"
                for code in inventory.blockers
            }
        )
    )
    status = "unknown"
    try:
        context.checkpoint("doctor_start")
        images = _select_images(inventory, context, sqlite_images)
        root = _open_root(inventory.root)
        info = os.fstat(root)
        identity = _wire_identity(info)
        if (
            inventory.profile_identity is None
            or _identity(info) != inventory.profile_identity
        ):
            raise _Stop("doctor_profile_changed")
        for role in inventory.roles:
            _assert_root(inventory, root, context)
            checks.append(
                _resource(
                    inventory, root, role, context, sqlite_inspector, facts, images
                )
            )
        cross = _cross_references(facts)
        if cross is not None:
            checks.append(cross)
        _assert_root(inventory, root, context)
        if any(check.status == "unsafe" for check in checks):
            status = "unsafe"
        elif any(check.status == "corrupt" for check in checks):
            status = "corrupt"
        elif blockers or any(check.status == "blocked" for check in checks):
            status = "blocked"
        elif all(check.status == "ok" for check in checks):
            status = "ok"
        elif all(check.status in {"absent", "uninitialized"} for check in checks):
            status = "uninitialized"
        else:
            status = "partial"
    except _ImagesRejected as error:
        status = "blocked"
        blockers = tuple(sorted(set(blockers) | {error.code}))
        checks = [
            DoctorCheck(role, "blocked", error.code)
            for role in inventory.roles
            if role in _SQLITE
        ]
    except FileNotFoundError:
        if root is None and sqlite_images is None:
            status = "absent"
            checks = [
                DoctorCheck(role, "absent", "doctor_resource_absent")
                for role in inventory.roles
            ]
        else:
            status = "unknown"
            blockers = tuple(sorted(set(blockers) | {"doctor_profile_changed"}))
            checks = [
                DoctorCheck(
                    check.role,
                    "unknown",
                    "doctor_snapshot_invalidated",
                    identity=check.identity,
                )
                for check in checks
            ]
    except _Stop as error:
        status = "cancelled" if error.code == "doctor_cancelled" else "unknown"
        blockers = tuple(sorted(set(blockers) | {error.code}))
        if error.code in {
            "doctor_barrier_changed",
            "doctor_profile_changed",
            "doctor_resource_changed",
            "doctor_snapshot_source_changed",
        }:
            checks = [
                DoctorCheck(
                    check.role,
                    "unknown",
                    "doctor_snapshot_invalidated",
                    identity=check.identity,
                )
                for check in checks
            ]
    except (DoctorError, OSError):
        status = "unsafe"
        blockers = tuple(sorted(set(blockers) | {"doctor_profile_unsafe"}))
    finally:
        if root is not None:
            os.close(root)
    suggestions = set()
    for check in checks:
        if check.code == "doctor_needs_quiescent_snapshot":
            suggestions.add("requires_quiescent_snapshot")
        if check.status == "corrupt":
            suggestions.add("preserve_corrupt_resource")
        if check.code == "doctor_schema_unsupported":
            suggestions.add("requires_implemented_migration")
        if check.code == "doctor_unsettled_state":
            suggestions.add("requires_owner_cleanup_review")
    return DoctorReport(
        uuid4().hex,
        inventory.profile_id,
        inventory.generation,
        status,
        inventory.coherence if status == "ok" else "unverified",
        identity,
        tuple(checks),
        blockers,
        tuple(sorted(suggestions)),
    )
