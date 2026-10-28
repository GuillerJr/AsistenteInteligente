"""Own portable backups and *new, disarmed* profile staging.

This is not a service, scanner, permission system or automatic recovery loop.
The host must hold its profile lease and an all-mutator quiescent barrier for the
entire synchronous call (including cleanup), and supply the central authority
guard. No existing profile, dedicated credential or external workspace is read.
"""

from __future__ import annotations

import json
import math
import os
import re
import sqlite3
import threading
import time
from collections.abc import Callable
from contextlib import ExitStack
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol
from uuid import uuid4

from jarvis_core import plugins, profiles, skills, telegram
from jarvis_core.automation_flows import validate_automation_document
from jarvis_core.recovery_fs import (
    Directory,
    FileIdentity,
    OwnedStage,
    PinnedFile,
    RecoveryFsError,
    basename,
    digest,
    directory_entries,
    no_replace,
)
from jarvis_core.recovery_sqlite import (
    SnapshotLimits,
    deny_extension_loading,
    inspect_sqlite_snapshot,
)
from jarvis_core.tools import ToolError

FORMAT = "jarvis-backup/1"
_LEGACY_ROLES = ("state", "memory", "schedules", "agents", "skills", "telegram",
                 "configuration_portable")
_EXTENSION_ROLES = (*_LEGACY_ROLES, "plugins")
_ROLES = (*_EXTENSION_ROLES, "automations")
_SQL = frozenset({"state", "memory", "schedules"})
_JSON_LIMITS = {
    "agents": 524_288,
    "skills": 4_194_304,
    "plugins": 4_194_304,
    "automations": 4_194_304,
    "telegram": 1_048_576,
    "configuration_portable": 1024,
}
_BACKUP_NAMES = {role: role + (".sqlite3" if role in _SQL else ".json") for role in _ROLES}
_PROFILE_NAMES = {
    "state": (".", "jarvis.sqlite3"),
    "memory": (".", "memory.sqlite3"),
    "schedules": (".", "schedules.sqlite3"),
    "agents": ("profiles", "agents.json"),
    "skills": ("profiles", "skills.json"),
    "plugins": ("profiles", "plugins.json"),
    "automations": (".", "automation.json"),
    "telegram": ("telegram", "telegram.json"),
    "configuration_portable": (".", "configuration.json"),
}
EXCLUSIONS = (
    "provider_credentials_and_state",
    "transport_tokens_sockets_and_locks",
    "mcp_programs_arguments_and_runtime_homes",
    "external_and_internal_workspace_files",
    "external_skill_resources",
    "temporary_sessions_and_memory",
    "live_approvals_and_grants",
    "running_processes_audio_and_network_clients",
    "host_maintenance_journal",
)
DISARMS = (
    "provider_threads_and_bindings_cleared",
    "workspace_unselected",
    "mcp_programs_absent",
    "schedules_disabled",
    "telegram_routes_disabled",
    "skills_disabled",
    "automation_hooks_disabled_and_checkpoints_disarmed",
    "unfinished_records_interrupted_unknown_no_replay",
    "fresh_transport_required",
    "fresh_channel_pairing_and_delivery_cutoff_required",
    "memory_workspace_association_requires_separate_review",
)
PORTABLE_CONFIG = {
    "version": 1,
    "workspace": None,
    "workspace_identity": None,
    "bindings": {},
    "mcp_servers": {},
}
_OPAQUE = re.compile(r"[A-Za-z0-9_-]{1,128}\Z")
_UUID = re.compile(r"[a-f0-9]{32}\Z")
_HASH = re.compile(r"[a-f0-9]{64}\Z")


class Cancellation(Protocol):
    def is_set(self) -> bool: ...


@dataclass(frozen=True)
class RecoveryLimits:
    max_sqlite_bytes: int = 64 * 1024 * 1024
    max_total_bytes: int = 128 * 1024 * 1024
    max_manifest_bytes: int = 65_536
    max_rows: int = 100_000
    max_vm_steps: int = 20_000_000

    def __post_init__(self):
        if any(type(value) is not int or value <= 0 for value in vars(self).values()):
            raise ValueError("recovery_limits_invalid")

    def sqlite(self):
        return SnapshotLimits(
            max_bytes=self.max_sqlite_bytes, max_rows=self.max_rows, max_vm_steps=self.max_vm_steps
        )


@dataclass
class RecoveryContext:
    operation_id: str
    profile_id: str
    generation: int
    check_current: Callable[[str, bool], None]
    cancellation: Cancellation
    deadline: float
    clock: Callable[[], float] = time.monotonic
    _used: bool = field(default=False, init=False, repr=False)
    _failure: BaseException | None = field(default=None, init=False, repr=False)
    _lock: threading.Lock = field(default_factory=threading.Lock, init=False, repr=False)

    def __post_init__(self):
        if (
            not isinstance(self.operation_id, str)
            or not _UUID.fullmatch(self.operation_id)
            or not isinstance(self.profile_id, str)
            or not _OPAQUE.fullmatch(self.profile_id)
            or type(self.generation) is not int
            or not 0 <= self.generation <= (1 << 52) - 1
            or type(self.deadline) not in {int, float}
            or not math.isfinite(self.deadline)
            or not callable(self.check_current)
            or not callable(self.clock)
            or not callable(getattr(self.cancellation, "is_set", None))
        ):
            raise ValueError("recovery_context_invalid")
        if self.deadline - self.clock() > 120:
            raise ValueError("recovery_deadline_invalid")

    def begin(self):
        with self._lock:
            if self._used:
                raise RecoveryFsError("recovery_operation_already_used")
            self._used = True
        self.checkpoint("begin")

    def checkpoint(self, stage, write=False):
        if self._failure is not None:
            raise self._failure
        try:
            if self.cancellation.is_set():
                raise RecoveryFsError("recovery_cancelled")
            if self.clock() >= self.deadline:
                raise RecoveryFsError("recovery_deadline_exceeded")
            self.check_current(stage, write)
            if self.cancellation.is_set():
                raise RecoveryFsError("recovery_cancelled")
            if self.clock() >= self.deadline:
                raise RecoveryFsError("recovery_deadline_exceeded")
        except BaseException as error:
            self._failure = error
            raise


@dataclass(frozen=True)
class SqliteSource:
    role: str
    connection: sqlite3.Connection
    check_current: Callable[[], None]


@dataclass(frozen=True)
class SqliteImageSource:
    role: str
    data: bytes
    source_identity: FileIdentity
    sha256: str
    check_current: Callable[[], None]


@dataclass(frozen=True)
class JsonSource:
    role: str
    data: bytes
    check_current: Callable[[], None]


@dataclass(frozen=True)
class SourceBundle:
    profile_id: str
    generation: int
    root: Path
    root_identity: FileIdentity
    sqlite: tuple[SqliteSource | SqliteImageSource, ...]
    json: tuple[JsonSource, ...]
    absent: tuple[str, ...]


@dataclass(frozen=True)
class NewTarget:
    parent: Path
    parent_identity: FileIdentity
    name: str
    forbidden_roots: tuple[Path, ...] = ()


@dataclass(frozen=True)
class RecoveryReceipt:
    operation_id: str
    purpose: str
    status: str
    effect_status: str
    target_name: str
    artifact_id: str | None = None
    manifest_sha256: str | None = None
    target_identity: FileIdentity | None = None
    new_profile_id: str | None = None
    error_code: str | None = None
    cleanup_error_code: str | None = None
    cleanup_pending: bool = False


class RecoveryError(ValueError):
    def __init__(self, code, receipt=None):
        super().__init__(code)
        self.code, self.receipt = code, receipt


@dataclass(frozen=True)
class BackupVerification:
    artifact_id: str
    profile_id: str
    generation: int
    manifest_sha256: str
    root_identity: FileIdentity
    resource_hashes: tuple[tuple[str, str], ...]
    excluded: tuple[str, ...] = EXCLUSIONS
    authenticity: str = "not_established"


def canonical(value):
    try:
        return json.dumps(
            value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
        ).encode()
    except (TypeError, ValueError, UnicodeError, RecursionError):
        raise RecoveryError("recovery_json_invalid") from None


def _decode(data, limit):
    if type(data) is not bytes or len(data) > limit:
        raise RecoveryError("recovery_json_limit")

    def pairs(rows):
        result = {}
        for key, value in rows:
            if key in result:
                raise RecoveryError("recovery_json_duplicate_key")
            result[key] = value
        return result

    try:
        value = json.loads(
            data,
            object_pairs_hook=pairs,
            parse_constant=lambda _: (_ for _ in ()).throw(ValueError()),
        )

        def bounded(item, depth=0):
            if depth > 24:
                raise RecoveryError("recovery_json_depth")
            if type(item) is float and not math.isfinite(item):
                raise RecoveryError("recovery_json_invalid")
            if isinstance(item, (dict, list)):
                if len(item) > 10_000:
                    raise RecoveryError("recovery_json_limit")
                for child in item.values() if isinstance(item, dict) else item:
                    bounded(child, depth + 1)

        bounded(value)
        return value
    except (ValueError, UnicodeError, RecursionError):
        raise RecoveryError("recovery_json_invalid") from None


def _json(role, data):
    value = _decode(data, _JSON_LIMITS[role])
    try:
        if role == "agents":
            profiles._validate(value)
        elif role == "skills":
            skills._validate(value)
        elif role == "plugins":
            plugins._validate(value)
        elif role == "automations":
            validate_automation_document(value)
        elif role == "telegram":
            telegram._journal_validate(value)
            if value["version"] != 2:
                raise RecoveryError("recovery_json_schema_unsupported")
        elif value != PORTABLE_CONFIG:
            raise RecoveryError("recovery_portable_configuration_invalid")
    except (
        ValueError,
        TypeError,
        KeyError,
        profiles.ProfileError,
        skills.SkillError,
        telegram.TelegramError,
    ):
        raise RecoveryError("recovery_json_schema_invalid") from None
    return value


def _cross_references(images, documents, context, limits, *, settled=False):
    inspections = {
        role: inspect_sqlite_snapshot(
            role,
            data,
            check_current=lambda: context.checkpoint("sqlite_inspect"),
            limits=limits.sqlite(),
        )
        for role, data in images.items()
    }
    agents = set(documents.get("agents", {}).get("agents", {}))
    if not agents:
        raise RecoveryError("recovery_agents_required")
    for inspection in inspections.values():
        if set(inspection.agent_ids) - agents:
            raise RecoveryError("recovery_cross_reference_invalid")
        if settled and any(
            inspection.counts.get(key, 0) for key in ("unsettled_runs", "unsettled_occurrences")
        ):
            raise RecoveryError("recovery_source_unsettled")
    for row in documents.get("skills", {}).get("skills", {}).values():
        if row["agent_id"] not in agents:
            raise RecoveryError("recovery_cross_reference_invalid")
    for row in documents.get("plugins", {}).get("plugins", {}).values():
        if row["agent_id"] not in agents:
            raise RecoveryError("recovery_cross_reference_invalid")
    for collection in ("hooks", "flows"):
        for row in documents.get("automations", {}).get(collection, []):
            if row["agent_id"] not in agents:
                raise RecoveryError("recovery_cross_reference_invalid")
            if collection == "flows" and settled and row["status"] in {"queued", "running"}:
                raise RecoveryError("recovery_source_unsettled")
    session_agents = dict(inspections["state"].session_agents) if "state" in inspections else {}
    journal = documents.get("telegram", {})
    for row in journal.get("routes", {}).values():
        if (
            row["agent_id"] not in agents
            or session_agents.get(row["session_id"]) != row["agent_id"]
        ):
            raise RecoveryError("recovery_cross_reference_invalid")
    for row in journal.get("events", {}).values():
        if settled and row["status"] in telegram._ACTIVE:
            raise RecoveryError("recovery_source_unsettled")
    return inspections


def capture_sqlite_snapshot(source, context, limits, root):
    """Return validated RAM bytes without sanitization, recovery or profile writes.

    The source is either an already owned connection or a host-pinned standalone
    cold image. Cold WAL/journal recovery is not performed or guessed here.
    The caller retains its quiescent-cut barrier/guard until this function ends.
    """
    context.checkpoint("sqlite_snapshot")
    source.check_current()
    if isinstance(source, SqliteImageSource):
        if (
            source.role not in _SQL
            or type(source.data) is not bytes
            or not isinstance(source.source_identity, FileIdentity)
            or not isinstance(source.sha256, str)
            or not _HASH.fullmatch(source.sha256)
            or digest(source.data) != source.sha256
        ):
            raise RecoveryError("recovery_source_image_invalid")
        inspect_sqlite_snapshot(
            source.role,
            source.data,
            check_current=lambda: context.checkpoint("sqlite_snapshot_check"),
            limits=limits.sqlite(),
        )
        source.check_current()
        context.checkpoint("sqlite_snapshot_complete")
        return source.data
    if source.role not in _SQL or not isinstance(source.connection, sqlite3.Connection):
        raise RecoveryError("recovery_source_invalid")
    connection = source.connection
    if connection.in_transaction:
        raise RecoveryError("recovery_source_transaction_active")
    databases = connection.execute("PRAGMA database_list").fetchall()
    main = [row for row in databases if row[1] == "main"]
    expected = root / _PROFILE_NAMES[source.role][1]
    if (
        len(main) != 1
        or Path(main[0][2]) != expected
        or any(row[1] not in {"main", "temp"} for row in databases)
    ):
        raise RecoveryError("recovery_source_not_owned_durable")
    page_size = connection.execute("PRAGMA page_size").fetchone()[0]
    pages = connection.execute("PRAGMA page_count").fetchone()[0]
    if pages * page_size > limits.max_sqlite_bytes:
        raise RecoveryError("recovery_source_size_limit")
    copy = sqlite3.connect(":memory:")
    try:

        def progress(status, remaining, total):
            context.checkpoint("sqlite_snapshot_progress")
            source.check_current()
            if total * page_size > limits.max_sqlite_bytes:
                raise RecoveryError("recovery_source_size_limit")

        connection.backup(copy, pages=128, progress=progress, sleep=0)
        source.check_current()
        data = copy.serialize()
        if len(data) > limits.max_sqlite_bytes:
            raise RecoveryError("recovery_source_size_limit")
        # Only a result of SQLite's own online-backup API is normalized here.
        # sqlite.org/c3ref/deserialize.html documents bytes18/19 WAL->rollback.
        if data[18:20] == b"\x02\x02":
            data = data[:18] + b"\x01\x01" + data[20:]
        inspect_sqlite_snapshot(
            source.role,
            data,
            check_current=lambda: context.checkpoint("sqlite_snapshot_check"),
            limits=limits.sqlite(),
        )
        context.checkpoint("sqlite_snapshot_complete")
        return data
    finally:
        copy.close()


def _snapshot(source, context, limits, root):
    data = capture_sqlite_snapshot(source, context, limits, root)
    return _transform_sqlite(source.role, data, context, limits, restoring=False)


def _transform_sqlite(role, data, context, limits, *, restoring):
    context.checkpoint("sqlite_transform")
    c = sqlite3.connect(":memory:", isolation_level=None)
    stopped = []
    steps = 0

    def progress():
        nonlocal steps
        steps += 1000
        try:
            context.checkpoint("sqlite_transform_progress")
            if steps > limits.max_vm_steps:
                raise RecoveryError("recovery_work_limit")
        except BaseException as error:
            stopped.append(error)
            return 1
        return 0

    try:
        deny_extension_loading(c)
        c.deserialize(data)
        c.execute("PRAGMA trusted_schema=OFF")
        c.execute("PRAGMA secure_delete=ON")
        c.set_progress_handler(progress, 1000)
        if role == "state":
            c.execute("UPDATE sessions SET provider_thread_id=NULL")
            if restoring:
                receipt = canonical(
                    {
                        "reason": "backup_restore",
                        "effect_status": "unknown",
                        "replay_allowed": False,
                    }
                ).decode()
                c.execute(
                    "UPDATE runs SET status='interrupted',outcome='unknown',receipt_json=? "
                    "WHERE receipt_json IS NULL",
                    (receipt,),
                )
        elif role == "memory" and restoring and c.execute("PRAGMA user_version").fetchone()[0] == 2:
            # Copy receipts are portable historical data, never local replay or
            # permission. Preserve their maps/digests and mark their new origin.
            c.execute("UPDATE memory_scope_copies SET origin_kind='restored_history'")
        elif role == "schedules" and restoring:
            c.execute("UPDATE schedules SET enabled=0,next_at=NULL")
            c.execute(
                "UPDATE occurrences SET status='interrupted',outcome='unknown',"
                "error_code='restore_interrupted_no_replay' "
                "WHERE status IN ('claimed','running')"
            )
        # VACUUM removes freelist/deleted provider IDs from the portable image.
        # It only mutates a private RAM copy, never the source connection.
        c.execute("VACUUM")
        result = c.serialize()
        inspect_sqlite_snapshot(
            role,
            result,
            check_current=lambda: context.checkpoint("sqlite_transform_check"),
            limits=limits.sqlite(),
        )
        return result
    except sqlite3.Error:
        if stopped:
            raise stopped[0] from None
        raise RecoveryError("recovery_sqlite_transform_failed") from None
    finally:
        c.close()


def _target(target, forbidden=()):
    basename(target.name)
    destination = target.parent / target.name
    for root in (*target.forbidden_roots, *forbidden):
        root = Path(root)
        if not root.is_absolute() or ".." in root.parts:
            raise RecoveryError("recovery_forbidden_root_invalid")
        if (
            destination == root
            or destination.is_relative_to(root)
            or root.is_relative_to(destination)
        ):
            raise RecoveryError("recovery_destination_overlaps_source")
    return Directory(target.parent, private=False, expected=target.parent_identity)


def _manifest(context, artifact_id, resources, absent):
    return {
        "format": FORMAT,
        "artifact_id": artifact_id,
        "profile_id": context.profile_id,
        "generation": context.generation,
        "created_ns": time.time_ns(),
        "consistency": "host_quiescent_cut_individual_sqlite_snapshots",
        "resources": [
            {
                "role": role,
                "path": "data/" + _BACKUP_NAMES[role],
                "size": len(resources[role]),
                "sha256": digest(resources[role]),
            }
            for role in _ROLES
            if role in resources
        ],
        "absent": sorted(absent),
        "excluded": list(EXCLUSIONS),
        "content_warning": "user_content_may_contain_secrets",
        "authenticity": "not_established",
    }


def _validate_manifest(value):
    if (
        not isinstance(value, dict)
        or set(value)
        != {
            "format",
            "artifact_id",
            "profile_id",
            "generation",
            "created_ns",
            "consistency",
            "resources",
            "absent",
            "excluded",
            "content_warning",
            "authenticity",
        }
        or value["format"] != FORMAT
        or not isinstance(value["artifact_id"], str)
        or not _UUID.fullmatch(value["artifact_id"])
        or not isinstance(value["profile_id"], str)
        or not _OPAQUE.fullmatch(value["profile_id"])
        or type(value["generation"]) is not int
        or not 0 <= value["generation"] <= (1 << 52) - 1
        or type(value["created_ns"]) is not int
        or value["created_ns"] < 0
        or value["consistency"] != "host_quiescent_cut_individual_sqlite_snapshots"
        or value["excluded"] != list(EXCLUSIONS)
        or value["content_warning"] != "user_content_may_contain_secrets"
        or value["authenticity"] != "not_established"
        or not isinstance(value["resources"], list)
        or len(value["resources"]) > len(_ROLES)
        or not isinstance(value["absent"], list)
        or len(value["absent"]) > len(_ROLES)
    ):
        raise RecoveryError("recovery_manifest_invalid")
    seen = set()
    for row in value["resources"]:
        if (
            not isinstance(row, dict)
            or set(row) != {"role", "path", "size", "sha256"}
            or not isinstance(row["role"], str)
            or row["role"] not in _ROLES
            or row["role"] in seen
            or row["path"] != "data/" + _BACKUP_NAMES[row["role"]]
            or type(row["size"]) is not int
            or row["size"] < 0
            or not isinstance(row["sha256"], str)
            or not _HASH.fullmatch(row["sha256"])
        ):
            raise RecoveryError("recovery_manifest_invalid")
        seen.add(row["role"])
    absent = value["absent"]
    if (
        any(not isinstance(role, str) or role not in _ROLES for role in absent)
        or len(set(absent)) != len(absent)
        or set(absent) & seen
        or (set(absent) | seen not in (set(_ROLES), set(_EXTENSION_ROLES), set(_LEGACY_ROLES)))
        or not {"agents", "configuration_portable"} <= seen
    ):
        raise RecoveryError("recovery_manifest_inventory_invalid")
    return value


def _verified(directory, context, limits, *, expected_hash=None, allow_pending=False):
    context.checkpoint("verify_begin")
    directory.check()
    entries = directory_entries(directory)
    allowed = {"data", "manifest.json"} | ({".pending"} if allow_pending else set())
    if entries != allowed:
        raise RecoveryError("recovery_artifact_entries_invalid")
    with ExitStack() as handles:
        file = handles.enter_context(
            PinnedFile(directory, "manifest.json", limits.max_manifest_bytes)
        )
        pinned_files = [file]
        raw = file.read(lambda: context.checkpoint("manifest_read"))
        manifest = _validate_manifest(_decode(raw, limits.max_manifest_bytes))
        if expected_hash is not None and digest(raw) != expected_hash:
            raise RecoveryError("recovery_manifest_changed")
        if raw != canonical(manifest):
            raise RecoveryError("recovery_manifest_not_canonical")
        resources, images, documents, total = {}, {}, {}, 0
        data_dir = handles.enter_context(Directory(directory.path / "data"))
        if directory_entries(data_dir) != {
            _BACKUP_NAMES[row["role"]] for row in manifest["resources"]
        }:
            raise RecoveryError("recovery_artifact_entries_invalid")
        for row in manifest["resources"]:
            context.checkpoint("resource_read")
            role = row["role"]
            maximum = limits.max_sqlite_bytes if role in _SQL else _JSON_LIMITS[role]
            if row["size"] > maximum or total + row["size"] > limits.max_total_bytes:
                raise RecoveryError("recovery_resource_limit")
            file = handles.enter_context(PinnedFile(data_dir, _BACKUP_NAMES[role], maximum))
            pinned_files.append(file)
            content = file.read(lambda: context.checkpoint("resource_chunk"))
            if len(content) != row["size"] or digest(content) != row["sha256"]:
                raise RecoveryError("recovery_resource_changed")
            resources[role] = content
            total += len(content)
            if role in _SQL:
                images[role] = content
            else:
                documents[role] = _json(role, content)
        _cross_references(images, documents, context, limits)
        context.checkpoint("verify_complete")
        directory.check()
        for file in pinned_files:
            file.check()
        if directory_entries(directory) != allowed or directory_entries(data_dir) != {
            _BACKUP_NAMES[row["role"]] for row in manifest["resources"]
        }:
            raise RecoveryError("recovery_artifact_entries_invalid")
        result = BackupVerification(
            manifest["artifact_id"],
            manifest["profile_id"],
            manifest["generation"],
            digest(raw),
            directory.identity,
            tuple((row["role"], row["sha256"]) for row in manifest["resources"]),
        )
        return result, manifest, resources


def verify_backup(
    path, context, *, limits=None, expected_manifest_sha256=None, expected_identity=None
):
    """Read-only and explicit: never resolves, repairs or deletes .pending files."""
    context.begin()
    limits = limits or RecoveryLimits()
    if expected_manifest_sha256 is not None and (
        not isinstance(expected_manifest_sha256, str)
        or not _HASH.fullmatch(expected_manifest_sha256)
    ):
        raise RecoveryError("recovery_digest_invalid")
    try:
        with Directory(path, expected=expected_identity) as directory:
            result, _, _ = _verified(
                directory, context, limits, expected_hash=expected_manifest_sha256
            )
            return result
    except (OSError, ValueError, sqlite3.Error) as error:
        if isinstance(error, RecoveryError):
            raise
        raise RecoveryError(_error_code(error)) from None


def _publish(stage, target, context, publish, verify):
    stage.root.check()
    context.checkpoint("publish", True)
    for name in stage._owned_dirs:
        with stage.directory(name) as directory:
            os.fsync(directory.fd)
    os.fsync(stage.root.fd)
    stage.parent.check()
    context.checkpoint("publish_dispatch", True)
    stage.publication_attempted = True
    publish(stage.parent, stage.name, target.name)
    # Until this point .pending remains part of the artifact and verify refuses
    # it. An exception after invoking publish is deliberately not auto-retried.
    stage.root.close()
    stage.name = target.name
    stage.root = Directory(stage.parent.path / target.name, expected=stage.identity)
    os.fsync(stage.parent.fd)
    context.checkpoint("publish_acknowledge", True)
    verify(stage.root, True)
    with PinnedFile(stage.root, ".pending", 1024) as file:
        if file.read(lambda: context.checkpoint("pending_check")) != context.operation_id.encode():
            raise RecoveryError("recovery_pending_changed")
    context.checkpoint("pending_remove", True)
    os.unlink(".pending", dir_fd=stage.root.fd)
    os.fsync(stage.root.fd)
    os.fsync(stage.parent.fd)
    stage.root.check()
    context.checkpoint("publication_durable")
    verify(stage.root, False)


def _error_code(error):
    value = getattr(error, "code", None)
    if value is None and isinstance(error, ToolError) and len(error.args) == 1:
        # ToolError belongs to the same central Jarvis authority and documents
        # stable code-only arguments. Arbitrary exception messages stay hidden.
        value = error.args[0]
    if value is None:
        value = "recovery_operation_failed"
    return (
        value
        if isinstance(value, str) and re.fullmatch(r"[a-z][a-z0-9_]{0,127}", value)
        else "recovery_operation_failed"
    )


def _failed(error, *, context, purpose, target, stage, publish_attempted):
    cleanup_error = getattr(error, "cleanup_error_code", None)
    if stage is not None and not publish_attempted:
        try:
            stage.cleanup()
        except BaseException as cleanup:
            cleanup_error = _error_code(cleanup)
    code = _error_code(error)
    unknown = publish_attempted or cleanup_error is not None or getattr(error, "uncertain", False)
    try:
        safe_name = basename(target.name)
    except (ValueError, OSError):
        safe_name = "unvalidated_target"
    receipt = RecoveryReceipt(
        context.operation_id,
        purpose,
        "unknown"
        if unknown
        else "cancelled"
        if code in {"recovery_cancelled", "recovery_deadline_exceeded"}
        else "failed",
        "unknown" if unknown else "no_published_effect",
        safe_name,
        target_identity=stage.identity if stage else None,
        error_code=code,
        cleanup_error_code=cleanup_error,
        cleanup_pending=unknown,
    )
    return RecoveryError(code, receipt)


def _begin_write(context, purpose, target):
    try:
        context.begin()
    except BaseException as error:
        # Reusing an ID is a protocol violation, not a new attempt with a receipt
        # that could contradict the original operation's already published result.
        if getattr(error, "code", None) == "recovery_operation_already_used":
            raise
        raise _failed(
            error,
            context=context,
            purpose=purpose,
            target=target,
            stage=None,
            publish_attempted=False,
        ) from None


def create_backup(bundle, target, context, *, limits=None, publish=no_replace):
    """Capture only the host's typed, immutable/quiescent allowlisted resources.

    ``publish`` is injectable only for owned synthetic crash/ack tests. Production
    uses native exclusive rename and never a check-then-overwrite fallback.
    """
    _begin_write(context, "backup.create", target)
    limits = limits or RecoveryLimits()
    stage, parent = None, None
    try:
        if bundle.profile_id != context.profile_id or bundle.generation != context.generation:
            raise RecoveryError("recovery_source_generation_changed")
        with Directory(bundle.root, expected=bundle.root_identity) as root:
            root.check()
            source_roles = [source.role for source in (*bundle.sqlite, *bundle.json)]
            if (
                len(source_roles) != len(set(source_roles))
                or any(role not in _SQL for role in [s.role for s in bundle.sqlite])
                or any(
                    role not in {"agents", "skills", "telegram", "plugins", "automations"}
                    for role in [s.role for s in bundle.json]
                )
                or len(set(bundle.absent)) != len(bundle.absent)
                or set(source_roles) & set(bundle.absent)
                or (set(source_roles) | set(bundle.absent)) not in (
                    set(_ROLES) - {"configuration_portable"},
                    set(_EXTENSION_ROLES) - {"configuration_portable"},
                    set(_LEGACY_ROLES) - {"configuration_portable"})
                or "agents" not in source_roles
            ):
                raise RecoveryError("recovery_source_inventory_invalid")
            parent = _target(target, (bundle.root,))
            stage = OwnedStage(
                parent, context.operation_id, lambda: context.checkpoint("stage_create", True)
            )
            stage.write(
                stage.root,
                ".pending",
                context.operation_id.encode(),
                lambda: context.checkpoint("pending_write", True),
            )
            resources = {"configuration_portable": canonical(PORTABLE_CONFIG)}
            documents = {"configuration_portable": PORTABLE_CONFIG}
            images = {}
            total = len(resources["configuration_portable"])
            for source in bundle.sqlite:
                images[source.role] = _snapshot(source, context, limits, bundle.root)
                resources[source.role] = images[source.role]
                total += len(resources[source.role])
                if total > limits.max_total_bytes:
                    raise RecoveryError("recovery_resource_limit")
            for source in bundle.json:
                context.checkpoint("json_snapshot")
                source.check_current()
                value = _json(source.role, source.data)
                documents[source.role] = value
                resources[source.role] = canonical(value)
                total += len(resources[source.role])
                if total > limits.max_total_bytes:
                    raise RecoveryError("recovery_resource_limit")
                source.check_current()
            _cross_references(images, documents, context, limits, settled=True)
            with stage.directory("data") as data_dir:
                for role, data in resources.items():
                    stage.write(
                        data_dir,
                        _BACKUP_NAMES[role],
                        data,
                        lambda: context.checkpoint("resource_write", True),
                    )
            artifact_id = uuid4().hex
            manifest = canonical(_manifest(context, artifact_id, resources, bundle.absent))
            if len(manifest) > limits.max_manifest_bytes:
                raise RecoveryError("recovery_manifest_limit")
            stage.write(
                stage.root,
                "manifest.json",
                manifest,
                lambda: context.checkpoint("manifest_write", True),
            )
            verified, _, _ = _verified(stage.root, context, limits, allow_pending=True)
            for source in (*bundle.sqlite, *bundle.json):
                source.check_current()
            root.check()
            context.checkpoint("cut_validate", True)
            try:
                _publish(
                    stage,
                    target,
                    context,
                    publish,
                    lambda directory, pending: _verified(
                        directory,
                        context,
                        limits,
                        expected_hash=verified.manifest_sha256,
                        allow_pending=pending,
                    ),
                )
            except RecoveryFsError as error:
                # Native EEXIST/unsupported guarantees that publication did not
                # happen; other errors/lost ACK remain unknown, with no retry.
                if error.code in {
                    "recovery_destination_exists",
                    "recovery_exclusive_publish_unavailable",
                }:
                    stage.publication_attempted = False
                raise
            return RecoveryReceipt(
                context.operation_id,
                "backup.create",
                "completed",
                "published_verified_bytes",
                target.name,
                artifact_id,
                verified.manifest_sha256,
                stage.identity,
            )
    except BaseException as error:
        raise _failed(
            error,
            context=context,
            purpose="backup.create",
            target=target,
            stage=stage,
            publish_attempted=stage is not None and stage.publication_attempted,
        ) from None
    finally:
        if stage is not None:
            stage.close()
        if parent is not None:
            parent.close()


def _restore_json(role, data):
    value = _json(role, data)
    if role == "skills":
        for row in value["skills"].values():
            row["enabled"] = False
    elif role == "plugins":
        # Preserve reviewed package/version bytes and provenance; restore no
        # extension context, lifecycle dispatch, MCP connection or permission.
        for row in value["plugins"].values():
            row["enabled"] = False
    elif role == "telegram":
        for row in value["routes"].values():
            row["enabled"] = False
        for row in value["events"].values():
            if row["status"] in telegram._ACTIVE:
                row.update(
                    status="interrupted",
                    outcome="unknown",
                    error_code="restore_interrupted_no_replay",
                )
    elif role == "automations":
        from jarvis_core.automation_flows import disarm_automation_document
        value = disarm_automation_document(value)
    elif role == "configuration_portable":
        value = dict(PORTABLE_CONFIG)
    encoded = canonical(value)
    _json(role, encoded)
    return encoded


def _verify_restored(directory, contents, context, limits, pending):
    """Compare fixed staged paths to the exact disarmed bytes before certification."""
    with ExitStack() as handles:
        directories = {".": directory}
        for subdir, _ in contents:
            if subdir not in directories:
                directories[subdir] = handles.enter_context(Directory(directory.path / subdir))
        expected_entries = {key: set() for key in directories}
        for subdir, name in contents:
            expected_entries[subdir].add(name)
        expected_entries["."].update(set(directories) - {"."})
        if pending:
            expected_entries["."].add(".pending")
        pinned = []
        for key, opened in directories.items():
            if directory_entries(opened) != expected_entries[key]:
                raise RecoveryError("recovery_restore_entries_invalid")
        for (subdir, name), expected in contents.items():
            file = handles.enter_context(
                PinnedFile(directories[subdir], name, max(limits.max_sqlite_bytes, 4_194_304))
            )
            pinned.append(file)
            actual = file.read(lambda: context.checkpoint("restore_verify_chunk"))
            if len(actual) != len(expected) or digest(actual) != digest(expected):
                raise RecoveryError("recovery_restore_bytes_changed")
        context.checkpoint("restore_verify_complete")
        for file in pinned:
            file.check()
        for key, opened in directories.items():
            if directory_entries(opened) != expected_entries[key]:
                raise RecoveryError("recovery_restore_entries_invalid")


def restore_new(
    backup_path, expected: BackupVerification, target, context, *, limits=None, publish=no_replace
):
    """Prepare a new profile; never replace, activate, reconnect or replay it."""
    _begin_write(context, "restore.stage", target)
    limits = limits or RecoveryLimits()
    stage, parent = None, None
    try:
        if not target.forbidden_roots:
            raise RecoveryError("recovery_active_root_inventory_required")
        with Directory(backup_path, expected=expected.root_identity) as source:
            verified, manifest, resources = _verified(
                source, context, limits, expected_hash=expected.manifest_sha256
            )
            if verified != expected:
                raise RecoveryError("recovery_review_changed")
            parent = _target(target, (Path(backup_path),))
            stage = OwnedStage(
                parent,
                context.operation_id,
                lambda: context.checkpoint("restore_stage_create", True),
            )
            stage.write(
                stage.root,
                ".pending",
                context.operation_id.encode(),
                lambda: context.checkpoint("restore_pending_write", True),
            )
            images, documents, staged_contents = {}, {}, {}
            for role, data in resources.items():
                if role in _SQL:
                    transformed = _transform_sqlite(role, data, context, limits, restoring=True)
                    images[role] = transformed
                else:
                    transformed = _restore_json(role, data)
                    documents[role] = _json(role, transformed)
                subdir, name = _PROFILE_NAMES[role]
                staged_contents[(subdir, name)] = transformed
                directory = stage.root if subdir == "." else stage.directory(subdir)
                try:
                    stage.write(
                        directory,
                        name,
                        transformed,
                        lambda: context.checkpoint("restore_resource_write", True),
                    )
                finally:
                    if directory is not stage.root:
                        directory.close()
            _cross_references(images, documents, context, limits, settled=True)
            new_id = uuid4().hex
            record = {
                "format": "jarvis-restore-record/1",
                "profile_id": new_id,
                "source_artifact_id": manifest["artifact_id"],
                "source_manifest_sha256": expected.manifest_sha256,
                "source_profile_id": manifest["profile_id"],
                "source_generation": manifest["generation"],
                "activation_required": True,
                "replay_allowed": False,
                "disarms": list(DISARMS),
                "excluded": list(EXCLUSIONS),
            }
            record_bytes = canonical(record)
            staged_contents[(".", "restore_record.json")] = record_bytes
            stage.write(
                stage.root,
                "restore_record.json",
                record_bytes,
                lambda: context.checkpoint("restore_record_write", True),
            )
            # Recheck the source before publication. Resources used above are
            # immutable bytes whose digests were established under pinned FDs.
            again, _, _ = _verified(source, context, limits, expected_hash=expected.manifest_sha256)
            if again != expected:
                raise RecoveryError("recovery_review_changed")
            try:
                _publish(
                    stage,
                    target,
                    context,
                    publish,
                    lambda directory, pending: _verify_restored(
                        directory, staged_contents, context, limits, pending
                    ),
                )
            except RecoveryFsError as error:
                if error.code in {
                    "recovery_destination_exists",
                    "recovery_exclusive_publish_unavailable",
                }:
                    stage.publication_attempted = False
                raise
            return RecoveryReceipt(
                context.operation_id,
                "restore.stage",
                "completed",
                "new_disarmed_profile",
                target.name,
                manifest["artifact_id"],
                expected.manifest_sha256,
                stage.identity,
                new_profile_id=new_id,
            )
    except BaseException as error:
        raise _failed(
            error,
            context=context,
            purpose="restore.stage",
            target=target,
            stage=stage,
            publish_attempted=stage is not None and stage.publication_attempted,
        ) from None
    finally:
        if stage is not None:
            stage.close()
        if parent is not None:
            parent.close()
