"""Explicit local admission of a prepared restore, without starting that profile.

All profile reads are fixed-path descriptor reads and bounded RAM SQLite images.
The host supplies the sole authorization/cut guard through RecoveryContext. A
local admission is configuration tied to one physical root, never a tool grant,
provider login, durable proof of an earlier ACK, or same-UID authenticity claim.
"""

from __future__ import annotations

import fcntl
import os
import re
import sqlite3
import stat
import threading
import time
from contextlib import ExitStack
from dataclasses import asdict, dataclass, field
from pathlib import Path

from jarvis_core.recovery import (
    DISARMS,
    EXCLUSIONS,
    RecoveryContext,
    RecoveryLimits,
    _cross_references,
    _decode,
    _json,
    canonical,
)
from jarvis_core.recovery_fs import (
    Directory,
    FileIdentity,
    PinnedFile,
    RecoveryFsError,
    digest,
    directory_entries,
    no_replace,
    private_file,
)
from jarvis_core.recovery_sqlite import deny_extension_loading
from jarvis_core.tools import ToolError

MARKER_NAME = "restore_record.json"
ADMISSION_NAME = "restore_admission.json"
ADMISSION_FORMAT = "jarvis-restore-admission/1"
_PURPOSE = "profile.restore.activate"
_UUID = re.compile(r"[a-f0-9]{32}\Z")
_HASH = re.compile(r"[a-f0-9]{64}\Z")
_OPAQUE = re.compile(r"[A-Za-z0-9_-]{1,128}\Z")
_ROLES = {
    "state": (".", "jarvis.sqlite3", 64 * 1024 * 1024),
    "memory": (".", "memory.sqlite3", 64 * 1024 * 1024),
    "schedules": (".", "schedules.sqlite3", 64 * 1024 * 1024),
    "agents": ("profiles", "agents.json", 524_288),
    "skills": ("profiles", "skills.json", 4_194_304),
    "plugins": ("profiles", "plugins.json", 4_194_304),
    "automations": (".", "automation.json", 4_194_304),
    "telegram": ("telegram", "telegram.json", 1_048_576),
    "configuration_portable": (".", "configuration.json", 1024),
}
_SQL = frozenset({"state", "memory", "schedules"})


class ActivationError(ValueError):
    def __init__(self, code, *, receipt=None, owner=None):
        super().__init__(code)
        self.code, self.receipt, self.owner = code, receipt, owner


def _code(error):
    code = getattr(error, "code", None)
    if code is None and isinstance(error, ToolError) and len(error.args) == 1:
        code = error.args[0]
    return (code if isinstance(code, str) and re.fullmatch(r"[a-z][a-z0-9_]{0,127}", code)
            else "activation_failed")


def _fail(code):
    raise ActivationError(code)


def _identity(value):
    return (type(value) is FileIdentity and type(value.device) is int and value.device >= 0
            and type(value.inode) is int and value.inode >= 0)


def _physical(value):
    return {"device": str(value.device), "inode": str(value.inode)}


def _metadata(info):
    return (info.st_dev, info.st_ino, info.st_uid, info.st_mode, info.st_nlink,
            info.st_size, info.st_mtime_ns, info.st_ctime_ns)


def _exists(directory, name):
    try:
        return os.stat(name, dir_fd=directory.fd, follow_symlinks=False)
    except FileNotFoundError:
        return None


@dataclass(frozen=True)
class ActivationLimits:
    max_sqlite_bytes: int = 64 * 1024 * 1024
    max_total_bytes: int = 128 * 1024 * 1024
    max_rows: int = 100_000
    max_vm_steps: int = 20_000_000
    max_control_bytes: int = 16_384

    def __post_init__(self):
        maxima = (64 * 1024 * 1024, 128 * 1024 * 1024, 100_000, 20_000_000, 16_384)
        if any(type(value) is not int or not 1 <= value <= maximum
               for value, maximum in zip(vars(self).values(), maxima, strict=True)):
            _fail("activation_limits_invalid")

    def recovery(self):
        return RecoveryLimits(max_sqlite_bytes=self.max_sqlite_bytes,
                              max_total_bytes=self.max_total_bytes,
                              max_rows=self.max_rows, max_vm_steps=self.max_vm_steps)


@dataclass(frozen=True)
class ActivationResource:
    role: str
    present: bool
    sha256: str | None
    byte_length: int
    counts: tuple[tuple[str, int], ...] = ()


@dataclass(frozen=True)
class ActivationReview:
    root: Path
    root_identity: FileIdentity
    marker_sha256: str
    restored_profile_id: str
    source_manifest_sha256: str
    resource_fingerprint: str
    resources: tuple[ActivationResource, ...]
    lock_state: tuple | None = field(repr=False)
    physical_state: tuple = field(repr=False)

    def metadata(self):
        return {
            "kind": _PURPOSE, "root": str(self.root),
            "root_identity": _physical(self.root_identity),
            "marker_sha256": self.marker_sha256, "restored_profile_id": self.restored_profile_id,
            "source_manifest_sha256": self.source_manifest_sha256,
            "resource_fingerprint": self.resource_fingerprint,
            "resources": [asdict(resource) for resource in self.resources],
            "policy": "local_admission_only_no_automatic_effects",
        }


@dataclass(frozen=True)
class LocalAdmission:
    root_identity: FileIdentity
    marker_sha256: str
    restored_profile_id: str
    source_manifest_sha256: str
    admission_sha256: str
    operation_id: str
    initial_review_fingerprint: str
    state: str = field(default="local_present_valid", init=False)
    authenticity: str = field(default="not_established", init=False)
    prior_operation_durability: str = field(default="not_established", init=False)
    grants_restored: bool = field(default=False, init=False)


@dataclass(frozen=True)
class ActivationReceipt:
    operation_id: str
    purpose: str
    status: str
    effect_status: str
    target_identity: FileIdentity | None
    marker_sha256: str | None
    admission_sha256: str | None
    error_code: str | None = None
    cleanup_pending: bool = False
    cleanup_error_code: str | None = None
    services_started: bool = field(default=False, init=False)
    grants_restored: bool = field(default=False, init=False)


def _marker(data, maximum):
    value = _decode(data, maximum)
    keys = {"format", "profile_id", "source_artifact_id", "source_manifest_sha256",
            "source_profile_id", "source_generation", "activation_required", "replay_allowed",
            "disarms", "excluded"}
    if (not isinstance(value, dict) or set(value) != keys
            or value["format"] != "jarvis-restore-record/1"
            or not isinstance(value["profile_id"], str) or not _UUID.fullmatch(value["profile_id"])
            or not isinstance(value["source_artifact_id"], str)
            or not _UUID.fullmatch(value["source_artifact_id"])
            or not isinstance(value["source_manifest_sha256"], str)
            or not _HASH.fullmatch(value["source_manifest_sha256"])
            or not isinstance(value["source_profile_id"], str)
            or not _OPAQUE.fullmatch(value["source_profile_id"])
            or type(value["source_generation"]) is not int
            or not 0 <= value["source_generation"] <= (1 << 52) - 1
            or value["activation_required"] is not True or value["replay_allowed"] is not False
            or value["disarms"] not in (
                list(DISARMS), [item for item in DISARMS
                                if item != "automation_hooks_disabled_and_checkpoints_disarmed"])
            or value["excluded"] != list(EXCLUSIONS)
            or canonical(value) != data):
        _fail("activation_restore_record_invalid")
    return value


def _admission(data, root_identity, marker, marker_hash, maximum):
    value = _decode(data, maximum)
    keys = {"format", "root_identity", "marker_sha256", "restored_profile_id",
            "source_manifest_sha256", "initial_review_fingerprint", "operation_id", "created_ns",
            "policy"}
    if (not isinstance(value, dict) or set(value) != keys
            or value["format"] != ADMISSION_FORMAT
            or value["root_identity"] != _physical(root_identity)
            or value["marker_sha256"] != marker_hash
            or value["restored_profile_id"] != marker["profile_id"]
            or value["source_manifest_sha256"] != marker["source_manifest_sha256"]
            or not isinstance(value["initial_review_fingerprint"], str)
            or not _HASH.fullmatch(value["initial_review_fingerprint"])
            or not isinstance(value["operation_id"], str)
            or not _UUID.fullmatch(value["operation_id"])
            or type(value["created_ns"]) is not int or value["created_ns"] < 0
            or value["policy"] != "local_admission_only_no_automatic_effects"
            or canonical(value) != data):
        _fail("activation_local_admission_invalid")
    return LocalAdmission(root_identity, marker_hash, marker["profile_id"],
                          marker["source_manifest_sha256"], digest(data), value["operation_id"],
                          value["initial_review_fingerprint"])


def validate_bootstrap_admission(root, *, limits=None, check_current=lambda: None):
    """Read current local admission only; do not certify an earlier operation/ACK.

    None means an ordinary profile with neither marker nor admission. A marker
    without a valid local admission fails closed. Legitimate mutable stores are
    deliberately not opened, fingerprinted or required to retain initial hashes.
    The caller owns the subsequent lifetime lease and must recheck admission at
    the bootstrap boundary, never use this value as a tool/provider permission.
    """
    limits = limits or ActivationLimits()
    owned = not isinstance(root, Directory)
    directory = None
    try:
        check_current()
        directory = Directory(root) if owned else root
        directory.check()
        marker_info = _exists(directory, MARKER_NAME)
        admission_info = _exists(directory, ADMISSION_NAME)
        if marker_info is None:
            if admission_info is not None:
                _fail("activation_local_admission_orphaned")
            check_current()
            directory.check()
            if (_exists(directory, MARKER_NAME) is not None
                    or _exists(directory, ADMISSION_NAME) is not None):
                _fail("activation_bootstrap_changed")
            return None
        if admission_info is None:
            _fail("profile_activation_required")
        with PinnedFile(directory, MARKER_NAME, limits.max_control_bytes) as marker_file:
            raw = marker_file.read(check_current)
            marker = _marker(raw, limits.max_control_bytes)
            with PinnedFile(directory, ADMISSION_NAME, limits.max_control_bytes) as admission_file:
                data = admission_file.read(check_current)
                result = _admission(data, directory.identity, marker, digest(raw),
                                    limits.max_control_bytes)
                check_current()
                marker_file.check()
                admission_file.check()
                directory.check()
                return result
    except BaseException as error:
        if isinstance(error, ActivationError):
            raise
        raise ActivationError(_code(error)) from None
    finally:
        if owned and directory is not None:
            directory.close()


def _sql_disarms(images, context, limits):
    for role in ("state", "schedules"):
        if role not in images:
            continue
        connection = sqlite3.connect(":memory:")
        stopped, steps = [], 0

        def progress(stopped=stopped):
            nonlocal steps
            steps += 1000
            try:
                context.checkpoint("activation_disarms_progress")
                if steps > limits.max_vm_steps:
                    _fail("activation_work_limit")
            except BaseException as error:
                stopped.append(error)
                return 1
            return 0

        try:
            deny_extension_loading(connection)
            connection.deserialize(images[role])
            connection.execute("PRAGMA trusted_schema=OFF")
            connection.execute("PRAGMA query_only=ON")
            connection.set_progress_handler(progress, 1000)
            query = ("SELECT 1 FROM sessions WHERE provider_thread_id IS NOT NULL LIMIT 1"
                     if role == "state" else
                     "SELECT 1 FROM schedules WHERE enabled!=0 OR next_at IS NOT NULL LIMIT 1")
            if connection.execute(query).fetchone() is not None:
                _fail("activation_profile_not_disarmed")
        except sqlite3.Error:
            if stopped:
                raise stopped[0] from None
            _fail("activation_snapshot_invalid")
        finally:
            connection.close()
        context.checkpoint("activation_disarms_checked")


def _read_prepared(directory, context, limits, *, owner=None):
    context.checkpoint("activation_review_begin")
    directory.check()
    root_entries = directory_entries(directory)
    known = {MARKER_NAME, "profile.lock", "profiles", "telegram", "configuration.json",
             "jarvis.sqlite3", "memory.sqlite3", "schedules.sqlite3", "automation.json"}
    if owner is not None:
        owner.assert_current()
        known.add(owner.pending_name)
    if ADMISSION_NAME in root_entries:
        _fail("activation_admission_already_exists")
    if any(name.endswith(("-wal", "-shm", "-journal")) for name in root_entries):
        _fail("activation_cold_sqlite_unavailable")
    if root_entries - known or not {MARKER_NAME, "configuration.json", "profiles"} <= root_entries:
        _fail("activation_profile_entries_invalid")
    lock_info = _exists(directory, "profile.lock")
    if lock_info is not None:
        private_file(lock_info, 0)
    lock_state = None if lock_info is None else _metadata(lock_info)
    resources, images, documents, state, total = [], {}, {}, [], 0
    root_names = sorted(root_entries - {"profile.lock"} - (
        {owner.pending_name} if owner is not None else set()))
    state.append(("root", (directory.identity.device, directory.identity.inode),
                  tuple(root_names)))
    with ExitStack() as handles:
        parents = {".": directory}
        for name, allowed in (("profiles", {"agents.json", "skills.json", "plugins.json"}),
                              ("telegram", {"telegram.json"})):
            if name in root_entries:
                parent = handles.enter_context(Directory(directory.path / name))
                names = directory_entries(parent)
                if names - allowed or (name == "profiles" and "agents.json" not in names):
                    _fail("activation_profile_entries_invalid")
                parents[name] = parent
                state.append(("dir:" + name, (parent.identity.device, parent.identity.inode),
                              tuple(sorted(names))))
        marker_file = handles.enter_context(
            PinnedFile(directory, MARKER_NAME, limits.max_control_bytes))
        pinned = [marker_file]
        marker_raw = marker_file.read(lambda: context.checkpoint("activation_marker_read"))
        marker = _marker(marker_raw, limits.max_control_bytes)
        total += len(marker_raw)
        state.append((MARKER_NAME, _metadata(marker_file.info), digest(marker_raw)))
        for role, (subdir, name, maximum) in _ROLES.items():
            context.checkpoint("activation_resource_begin")
            parent = parents.get(subdir)
            if parent is None or _exists(parent, name) is None:
                resources.append(ActivationResource(role, False, None, 0))
                state.append((role, "absent"))
                continue
            if role in _SQL:
                maximum = limits.max_sqlite_bytes
            source = handles.enter_context(PinnedFile(parent, name, maximum))
            pinned.append(source)
            if total + source.info.st_size > limits.max_total_bytes:
                _fail("activation_total_bytes_limit")
            data = source.read(lambda: context.checkpoint("activation_resource_read"))
            total += len(data)
            state.append((role, _metadata(source.info), digest(data)))
            if role in _SQL:
                images[role] = data
            else:
                documents[role] = _json(role, data)
            resources.append(ActivationResource(role, True, digest(data), len(data)))
        inspections = _cross_references(images, documents, context, limits.recovery(), settled=True)
        _sql_disarms(images, context, limits)
        if (any(row["enabled"] for row in documents.get("skills", {}).get("skills", {}).values())
                or any(row["enabled"]
                       for row in documents.get("plugins", {}).get("plugins", {}).values())
                or any(row["enabled"]
                       for row in documents.get("automations", {}).get("hooks", []))
                or any(row["status"] in {"queued", "running", "paused"}
                       for row in documents.get("automations", {}).get("flows", []))
                or any(row["enabled"] for row in documents.get("telegram", {}).get("routes", {})
                       .values())):
            _fail("activation_profile_not_disarmed")
        resources = tuple(ActivationResource(resource.role, resource.present, resource.sha256,
                                             resource.byte_length,
                                             inspections[resource.role].row_counts
                                             if resource.role in inspections else ())
                          for resource in resources)
        context.checkpoint("activation_review_complete")
        for source in pinned:
            source.check()
        for name, parent in parents.items():
            expected_names = root_names if name == "." else next(
                row[2] for row in state if row[0] == "dir:" + name)
            observed = directory_entries(parent)
            if name == ".":
                observed -= {"profile.lock"}
                if owner is not None:
                    observed -= {owner.pending_name}
            if tuple(sorted(observed)) != tuple(expected_names):
                _fail("activation_profile_changed")
        if owner is not None:
            owner.assert_current()
        return ActivationReview(directory.path, directory.identity, digest(marker_raw),
                                marker["profile_id"], marker["source_manifest_sha256"],
                                digest(canonical(state)), resources, lock_state, tuple(state))


def _quick_current(directory, review, owner):
    """Last metadata/CAS check after the host guard and immediately before effect."""
    directory.check()
    for tag, *fields in review.physical_state:
        if tag == "root":
            names = directory_entries(directory) - {"profile.lock", owner.pending_name}
            if tuple(sorted(names)) != fields[1]:
                _fail("activation_review_changed")
        elif tag in {"dir:profiles", "dir:telegram"}:
            with Directory(directory.path / tag[4:]) as parent:
                if ((parent.identity.device, parent.identity.inode) != fields[0]
                        or tuple(sorted(directory_entries(parent))) != fields[1]):
                    _fail("activation_review_changed")
        else:
            subdir, name = ((".", MARKER_NAME) if tag == MARKER_NAME else _ROLES[tag][:2])
            if subdir != "." and _exists(directory, subdir) is None and fields == ["absent"]:
                continue
            parent = directory if subdir == "." else Directory(directory.path / subdir)
            try:
                observed = _exists(parent, name)
                if fields == ["absent"]:
                    if observed is not None:
                        _fail("activation_review_changed")
                elif observed is None or _metadata(observed) != fields[0]:
                    _fail("activation_review_changed")
            finally:
                if parent is not directory:
                    parent.close()
    directory.check()


def review_prepared_profile(root, context: RecoveryContext, *, limits=None):
    """One-shot bounded, read-only review of a selected prepared restore."""
    if not isinstance(context, RecoveryContext):
        _fail("activation_context_invalid")
    try:
        context.begin()
        with Directory(root) as directory:
            return _read_prepared(directory, context, limits or ActivationLimits())
    except BaseException as error:
        if isinstance(error, ActivationError):
            raise
        raise ActivationError(_code(error)) from None


class ActivationOwner:
    """Own only one target lock and temporary; never unlink a published admission.

    Kept on ActivationError when publication or cleanup is uncertain. cleanup()
    is an explicit cleanup/reconciliation attempt, not a second commit, and never
    upgrades the operation's immutable unknown receipt or opens a service.
    """

    def __init__(self, directory, operation_id):
        self.directory = directory
        self.pending_name = ".jarvis-activation-" + operation_id + ".pending"
        self.lock_fd = -1
        self.lock_identity = None
        self.pending_fd = -1
        self.pending_identity = None
        self._pending_created = False
        self._pending_unlinked = False
        self.admission_sha256 = None
        self.publication_attempted = False
        self.closed = False
        self.namespace_dirty = False
        self._mutex = threading.RLock()

    def assert_current(self):
        self.directory.check()
        if self.lock_fd >= 0:
            opened = os.fstat(self.lock_fd)
            named = _exists(self.directory, "profile.lock")
            private_file(opened, 0)
            if named is None:
                _fail("activation_lock_identity_changed")
            private_file(named, 0)
            if (FileIdentity.of(opened) != self.lock_identity
                    or FileIdentity.of(named) != self.lock_identity):
                _fail("activation_lock_identity_changed")
        if self.pending_fd >= 0:
            opened = os.fstat(self.pending_fd)
            named = _exists(self.directory, self.pending_name)
            if self.pending_identity is None:
                # Recover metadata only from the still-owned descriptor returned
                # by our successful exclusive creation, never from the name alone.
                if (not self._pending_created or self.publication_attempted
                        or self._pending_unlinked or named is None):
                    _fail("activation_pending_metadata_unavailable")
                private_file(opened, 16_384)
                private_file(named, 16_384)
                if FileIdentity.of(opened) != FileIdentity.of(named):
                    _fail("activation_pending_identity_changed")
                self.pending_identity = FileIdentity.of(opened)
            if self._pending_unlinked:
                # Only our successful unlink grants this cleanup-only state.
                # An external unlink or a reappeared name never qualifies.
                if (not stat.S_ISREG(opened.st_mode) or opened.st_uid != os.getuid()
                        or stat.S_IMODE(opened.st_mode) != 0o600 or opened.st_nlink != 0
                        or opened.st_size > 16_384 or named is not None
                        or FileIdentity.of(opened) != self.pending_identity):
                    _fail("activation_pending_identity_changed")
            else:
                private_file(opened, 16_384)
                if FileIdentity.of(opened) != self.pending_identity:
                    _fail("activation_pending_identity_changed")
                if self.publication_attempted and named is None:
                    named = _exists(self.directory, ADMISSION_NAME)
                if named is None or FileIdentity.of(named) != self.pending_identity:
                    _fail("activation_pending_identity_changed")

    def acquire_lock(self, context, review):
        self.directory.check()
        context.checkpoint("activation_lock_open", True)
        _quick_current(self.directory, review, self)
        self.directory.check()
        existing = _exists(self.directory, "profile.lock")
        if (None if existing is None else _metadata(existing)) != review.lock_state:
            _fail("activation_review_changed")
        flags = os.O_RDWR | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC
        if existing is None:
            flags |= os.O_CREAT | os.O_EXCL
        self.lock_fd = os.open("profile.lock", flags, 0o600, dir_fd=self.directory.fd)
        if existing is None:
            self.namespace_dirty = True
        try:
            opened = os.fstat(self.lock_fd)
            private_file(opened, 0)
            if existing is not None and _metadata(opened) != review.lock_state:
                _fail("activation_review_changed")
            self.lock_identity = FileIdentity.of(opened)
            fcntl.flock(self.lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BaseException as error:
            os.close(self.lock_fd)
            self.lock_fd = -1
            if isinstance(error, BlockingIOError):
                _fail("activation_profile_busy")
            raise
        self.assert_current()

    def write_pending(self, data, context, review):
        self.assert_current()
        context.checkpoint("activation_pending_create", True)
        _quick_current(self.directory, review, self)
        self.assert_current()
        self.pending_fd = os.open(self.pending_name, os.O_WRONLY | os.O_CREAT | os.O_EXCL |
                                  os.O_NOFOLLOW | os.O_CLOEXEC, 0o600,
                                  dir_fd=self.directory.fd)
        # Creation provenance and dirty namespace precede fallible metadata IO.
        self._pending_created = True
        self.namespace_dirty = True
        self.pending_identity = FileIdentity.of(os.fstat(self.pending_fd))
        position = 0
        while position < len(data):
            context.checkpoint("activation_pending_write", True)
            _quick_current(self.directory, review, self)
            self.assert_current()
            written = os.write(self.pending_fd, data[position:])
            if written <= 0:
                _fail("activation_write_failed")
            position += written
        context.checkpoint("activation_pending_fsync", True)
        _quick_current(self.directory, review, self)
        self.assert_current()
        os.fsync(self.pending_fd)
        with PinnedFile(self.directory, self.pending_name, 16_384) as source:
            if source.identity != self.pending_identity:
                _fail("activation_pending_identity_changed")
            actual = source.read(lambda: context.checkpoint("activation_pending_verify"))
            if actual != data:
                _fail("activation_pending_bytes_changed")
        self.admission_sha256 = digest(data)

    def cleanup(self):
        """Release only provably owned entries/FDs; preserve unknown names/owners."""
        with self._mutex:
            if self.closed:
                return
            self.assert_current()
            pending = _exists(self.directory, self.pending_name)
            final = _exists(self.directory, ADMISSION_NAME)
            if self.publication_attempted:
                if final is not None:
                    private_file(final, 16_384)
                    if (self.pending_identity is None
                            or FileIdentity.of(final) != self.pending_identity
                            or pending is not None):
                        _fail("activation_cleanup_identity_changed")
                    with PinnedFile(self.directory, ADMISSION_NAME, 16_384) as source:
                        if digest(source.read(lambda: None)) != self.admission_sha256:
                            _fail("activation_cleanup_bytes_changed")
                elif pending is None and not self._pending_unlinked:
                    _fail("activation_cleanup_publication_unknown")
            if pending is not None and self.pending_identity is not None:
                private_file(pending, 16_384)
                if FileIdentity.of(pending) != self.pending_identity:
                    _fail("activation_cleanup_identity_changed")
                os.unlink(self.pending_name, dir_fd=self.directory.fd)
                self._pending_unlinked = True
            # Sync current cleanup/publication state, without claiming the old ACK.
            if self.namespace_dirty:
                os.fsync(self.directory.fd)
            self._release()

    def _release(self):
        for name in ("pending_fd", "lock_fd"):
            fd = getattr(self, name)
            if fd >= 0:
                os.close(fd)
                setattr(self, name, -1)
        self.directory.close()
        self.closed = True


def _validate_review(review):
    if (type(review) is not ActivationReview or not isinstance(review.root, Path)
            or not review.root.is_absolute() or ".." in review.root.parts
            or not _identity(review.root_identity)
            or any(not isinstance(value, str) or not _HASH.fullmatch(value)
                   for value in (review.marker_sha256, review.source_manifest_sha256,
                                 review.resource_fingerprint))
            or not isinstance(review.restored_profile_id, str)
            or not _UUID.fullmatch(review.restored_profile_id)
            or digest(canonical(review.physical_state)) != review.resource_fingerprint):
        _fail("activation_review_invalid")


def activate_prepared_profile(review, context: RecoveryContext, *, limits=None, publish=no_replace):
    """Publish one local admission. No service/workspace/provider/channel starts.

    The host binds context.check_current to its central authority's exact review
    metadata and purpose, committing on the first write=True checkpoint and
    revalidating that same lease thereafter. A bare context is not authorization.
    """
    if not isinstance(context, RecoveryContext):
        _fail("activation_context_invalid")
    owner, admission_hash, directory = None, None, None
    try:
        context.begin()
        _validate_review(review)
        limits = limits or ActivationLimits()
        directory = Directory(review.root, expected=review.root_identity)
        owner = ActivationOwner(directory, context.operation_id)
        fresh = _read_prepared(directory, context, limits)
        if fresh != review:
            _fail("activation_review_changed")
        owner.acquire_lock(context, review)
        # Lock creation is the sole allowed difference in this second inspection.
        fresh = _read_prepared(directory, context, limits, owner=owner)
        if (fresh.resource_fingerprint != review.resource_fingerprint
                or fresh.marker_sha256 != review.marker_sha256):
            _fail("activation_review_changed")
        data = canonical({
            "format": ADMISSION_FORMAT, "root_identity": _physical(review.root_identity),
            "marker_sha256": review.marker_sha256,
            "restored_profile_id": review.restored_profile_id,
            "source_manifest_sha256": review.source_manifest_sha256,
            "initial_review_fingerprint": review.resource_fingerprint,
            "operation_id": context.operation_id, "created_ns": time.time_ns(),
            "policy": "local_admission_only_no_automatic_effects",
        })
        if len(data) > limits.max_control_bytes:
            _fail("activation_control_bytes_limit")
        owner.write_pending(data, context, review)
        fresh = _read_prepared(directory, context, limits, owner=owner)
        if fresh.resource_fingerprint != review.resource_fingerprint:
            _fail("activation_review_changed")
        context.checkpoint("activation_publish", True)
        _quick_current(directory, review, owner)
        owner.assert_current()
        owner.publication_attempted = True
        try:
            publish(directory, owner.pending_name, ADMISSION_NAME)
        except RecoveryFsError as error:
            if error.code in {"recovery_destination_exists",
                              "recovery_exclusive_publish_unavailable"}:
                owner.publication_attempted = False
            raise
        context.checkpoint("activation_publication_verify")
        admission = validate_bootstrap_admission(
            directory, limits=limits,
            check_current=lambda: context.checkpoint("activation_publication_read"))
        if admission is None or admission.admission_sha256 != digest(data):
            _fail("activation_publication_changed")
        final = _exists(directory, ADMISSION_NAME)
        if final is None or FileIdentity.of(final) != owner.pending_identity:
            _fail("activation_publication_changed")
        with (PinnedFile(directory, MARKER_NAME, limits.max_control_bytes) as marker_file,
              PinnedFile(directory, ADMISSION_NAME, limits.max_control_bytes) as final_file):
            current = validate_bootstrap_admission(directory, limits=limits)
            if current is None or current.admission_sha256 != digest(data):
                _fail("activation_publication_changed")
            context.checkpoint("activation_publication_fsync", True)
            marker_file.check()
            final_file.check()
            owner.assert_current()
            os.fsync(directory.fd)
            admission_hash = digest(data)
            context.checkpoint("activation_completed")
            marker_file.check()
            final_file.check()
            owner.assert_current()
            owner._release()
        return ActivationReceipt(context.operation_id, _PURPOSE, "completed",
                                 "local_admission_published", review.root_identity,
                                 review.marker_sha256, admission_hash)
    except BaseException as error:
        code = _code(error)
        if code == "recovery_operation_already_used":
            raise ActivationError(code) from None
        unknown = owner is not None and owner.publication_attempted
        cleanup_error = None
        if owner is not None and not unknown:
            if owner._pending_created and owner.pending_identity is None:
                # Preserve the known FD owner for an explicit metadata/cleanup
                # attempt, instead of guessing identity after this IO failure.
                cleanup_error = "activation_pending_metadata_unavailable"
            else:
                try:
                    owner.cleanup()
                except BaseException as cleanup:
                    cleanup_error = _code(cleanup)
        if owner is None and directory is not None:
            directory.close()
        retained = owner is not None and not owner.closed
        status = ("unknown" if unknown or cleanup_error is not None else "cancelled"
                  if code in {"recovery_cancelled", "recovery_deadline_exceeded"} else "failed")
        receipt = ActivationReceipt(
            context.operation_id, _PURPOSE, status,
            "publication_unknown" if unknown else "not_activated",
            getattr(review, "root_identity", None), getattr(review, "marker_sha256", None),
            admission_hash or (owner.admission_sha256 if owner is not None else None),
            code, retained, cleanup_error)
        raise ActivationError(code, receipt=receipt, owner=owner if retained else None) from None
