"""Cooperative profile ownership and one-loop admission for coherent host cuts.

The lifetime flock is not a same-UID sandbox. Every service entry and owned
background writer must participate; readers with lazy initialization do too.
No profile discovery, recursive scan, credential read, job stop or auto-retry.
"""

from __future__ import annotations

import asyncio
import fcntl
import hashlib
import inspect
import json
import os
import sqlite3
import stat
import threading
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, replace
from pathlib import Path
from uuid import uuid4

from jarvis_core.activation import ActivationError, validate_bootstrap_admission
from jarvis_core.cold_sqlite import ColdFamily, inspect_cold_family
from jarvis_core.recovery import (
    JsonSource,
    RecoveryLimits,
    SourceBundle,
    SqliteImageSource,
    SqliteSource,
)
from jarvis_core.recovery_fs import Directory, FileIdentity
from jarvis_core.state import ensure_private_directory
from jarvis_core.tools import HostScope


class HostProfileError(ValueError):
    def __init__(self, code: str, blockers: tuple[str, ...] = ()):
        super().__init__(code)
        self.code, self.blockers = code, blockers


def _file_info(info) -> tuple[int, ...]:
    return (info.st_dev, info.st_ino, info.st_uid, stat.S_IMODE(info.st_mode), info.st_nlink,
            info.st_size, info.st_mtime_ns, info.st_ctime_ns)


def _regular(info, *, limit=None):
    if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid()
            or stat.S_IMODE(info.st_mode) != 0o600 or info.st_nlink != 1
            or (limit is not None and info.st_size > limit)):
        raise HostProfileError("profile_file_unsafe")


@dataclass(frozen=True)
class ExpectedRestore:
    """Bootstrap restrictions only: never a provider, tool or activation grant."""

    identity: FileIdentity
    marker_sha256: str
    admission_sha256: str
    operation_id: str

    def __post_init__(self):
        import re
        if (type(self.identity) is not FileIdentity
                or any(type(v) is not int or not 0 <= v < 2**64
                       for v in (self.identity.device, self.identity.inode))
                or any(type(v) is not str or re.fullmatch(r"[a-f0-9]{64}", v) is None
                       for v in (self.marker_sha256, self.admission_sha256))
                or type(self.operation_id) is not str
                or re.fullmatch(r"[a-f0-9]{32}", self.operation_id) is None):
            raise HostProfileError("profile_expected_restore_invalid")


def _bootstrap_files(directory: Directory):
    directory.check()
    rows = []
    try:
        for name in ("restore_record.json", "restore_admission.json"):
            try:
                info = os.stat(name, dir_fd=directory.fd, follow_symlinks=False)
            except FileNotFoundError:
                rows.append((name, None))
            else:
                rows.append((name, (*_file_info(info), info.st_mode)))
    except OSError:
        raise HostProfileError("profile_bootstrap_unavailable") from None
    directory.check()
    return tuple(rows)


def assert_restored_namespace(directory: Directory, admission) -> None:
    """Metadata-only refusal of ambiguous current activation leftovers.

    A historical unknown receipt is not a permanent veto. Current entries are
    checked without deleting, acquiring a target lock or reconstructing an owner.
    The real bootstrap flock remains the cross-process ownership boundary.
    """
    if admission is None:
        return
    directory.check()
    if any(name.startswith(".jarvis-activation-") for name in os.listdir(directory.fd)):
        raise HostProfileError("profile_activation_cleanup_unresolved")
    try:
        lock = os.stat("profile.lock", dir_fd=directory.fd, follow_symlinks=False)
    except FileNotFoundError:
        pass
    else:
        _regular(lock, limit=0)
    directory.check()


def _bootstrap_admission(directory: Directory, expected=None):
    if expected is not None and directory.identity != expected.identity:
        raise HostProfileError("profile_expected_restore_mismatch")
    try:
        admission = validate_bootstrap_admission(directory)
    except ActivationError as error:
        raise HostProfileError("profile_bootstrap_unavailable"
                               if error.code == "activation_failed" else error.code) from None
    assert_restored_namespace(directory, admission)
    if expected is not None and (admission is None
            or admission.root_identity != expected.identity
            or admission.marker_sha256 != expected.marker_sha256
            or admission.admission_sha256 != expected.admission_sha256
            or admission.operation_id != expected.operation_id):
        raise HostProfileError("profile_expected_restore_mismatch")
    return admission


class ProfileLease:
    """One explicitly selected physical profile; a fresh host identity per service."""

    def __init__(self, root: Path, *, create: bool = True,
                 expected_restore: ExpectedRestore | None = None):
        self.root = Path(root)
        self._lock = threading.RLock()
        self._directory = None
        self._fd = -1
        self._scope = HostScope(uuid4().hex, 0)
        try:
            if expected_restore is not None and type(expected_restore) is not ExpectedRestore:
                raise HostProfileError("profile_expected_restore_invalid")
            if create and expected_restore is None:
                ensure_private_directory(self.root)
            self._directory = Directory(self.root)
            self.identity = self._directory.identity
            self.expected_restore = expected_restore
            self._admission_state = _bootstrap_files(self._directory)
            self.admission = _bootstrap_admission(self._directory, expected_restore)
            if _bootstrap_files(self._directory) != self._admission_state:
                raise HostProfileError("profile_bootstrap_changed")
            flags = os.O_RDWR | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC
            if create:
                flags |= os.O_CREAT
            self._fd = os.open("profile.lock", flags, 0o600, dir_fd=self._directory.fd)
            _regular(os.fstat(self._fd), limit=0)
            try:
                fcntl.flock(self._fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise HostProfileError("profile_busy") from None
            self.assert_current()
        except BaseException:
            if self._fd >= 0:
                os.close(self._fd)
                self._fd = -1
            if self._directory is not None:
                self._directory.close()
            raise

    @classmethod
    def acquire(cls, root: Path, *, create: bool = True,
                expected_restore: ExpectedRestore | None = None):
        return cls(root, create=create, expected_restore=expected_restore)

    @property
    def scope(self) -> HostScope:
        with self._lock:
            return self._scope

    def assert_current(self, expected_scope: HostScope | None = None) -> None:
        with self._lock:
            if self._fd < 0 or self._directory is None:
                raise HostProfileError("profile_lease_closed")
            try:
                self._directory.check()
                named = os.stat("profile.lock", dir_fd=self._directory.fd, follow_symlinks=False)
                opened = os.fstat(self._fd)
                _regular(named, limit=0)
                _regular(opened, limit=0)
                if (named.st_dev, named.st_ino) != (opened.st_dev, opened.st_ino):
                    raise HostProfileError("profile_lock_changed")
                if expected_scope is not None and expected_scope != self._scope:
                    raise HostProfileError("profile_generation_stale")
            except OSError:
                raise HostProfileError("profile_identity_changed") from None

    def check_bootstrap_ready(self) -> None:
        """Reject restored staging before constructors, preserving cleanup rights."""
        with self._lock:
            self.assert_current()
            current = _bootstrap_admission(self._directory, self.expected_restore)
            self.assert_admission_current()
            if current != self.admission:
                raise HostProfileError("profile_bootstrap_changed")

    def assert_admission_current(self) -> None:
        """Fast immutable-control check for workers after one complete validation."""
        with self._lock:
            self.assert_current()
            if _bootstrap_files(self._directory) != self._admission_state:
                raise HostProfileError("profile_bootstrap_changed")

    def advance_generation(self) -> HostScope:
        with self._lock:
            self.assert_current()
            if self._scope.generation == 2**52 - 1:
                raise HostProfileError("profile_generation_exhausted")
            self._scope = HostScope(self._scope.profile_id, self._scope.generation + 1)
            return self._scope

    def close(self) -> None:
        with self._lock:
            if self._fd < 0:
                return
            # An uncertain owner never drops its lock to pretend cleanup succeeded.
            self.assert_current()
            os.close(self._fd)
            self._fd = -1
            self._directory.close()


class _Admission:
    def __init__(self, gate, label, mutating):
        self.gate, self.label, self.mutating = gate, label, mutating
        self.active = True
        self.before = gate._fingerprint()

    def close(self):
        self.gate._on_loop()
        if not self.active:
            return
        self.active = False
        try:
            changed = self.mutating or self.before != self.gate._fingerprint()
            if changed:
                scope = self.gate.lease.advance_generation()
                if self.gate.on_generation is not None:
                    self.gate.on_generation(scope)
        finally:
            self.gate._admitted.pop(id(self), None)


@dataclass
class ProfileCut:
    gate: ServiceProfileGate
    scope: HostScope
    cut_id: str
    active: bool = True
    worker_count: int = 0

    def assert_current(self) -> None:
        # Safe from a recovery worker; no asyncio or futures accessed here.
        with self.gate._cut_lock:
            if not self.active or self.gate._cut is not self:
                raise HostProfileError("profile_cut_expired")
            self.gate.lease.assert_current(self.scope)


@dataclass(frozen=True)
class WorkerOutcome:
    value: object = None
    error: BaseException | None = None
    awaiter_cancelled: bool = False


class ServiceProfileGate:
    def __init__(self, lease: ProfileLease, *, blockers: Callable[[], tuple[str, ...]],
                 fingerprint: Callable[[], object] = lambda: None,
                 on_generation: Callable[[HostScope], None] | None = None):
        self.lease, self._blockers, self._fingerprint = lease, blockers, fingerprint
        self.on_generation = on_generation
        self._loop = None
        self._admitted: dict[int, _Admission] = {}
        self._cut_lock = threading.RLock()
        self._cut: ProfileCut | None = None
        self._released = asyncio.Event()
        self._released.set()
        self._closing = False

    def _on_loop(self):
        loop = asyncio.get_running_loop()
        if self._loop is None:
            self._loop = loop
        if self._loop is not loop:
            raise HostProfileError("profile_loop_mismatch")

    def blockers(self) -> tuple[str, ...]:
        self._on_loop()
        values = set(self._blockers())
        if self._admitted:
            values.add("profile_handlers_active")
        if self._closing:
            values.add("profile_closing")
        if self._cut is not None:
            values.add("profile_maintenance_active")
        return tuple(sorted(values))

    def hold(self, label: str, *, mutating: bool = False, closing: bool = False) -> _Admission:
        self._on_loop()
        self.lease.assert_current()
        if self._cut is not None or (self._closing and not closing):
            raise HostProfileError("profile_maintenance_busy")
        token = _Admission(self, label, mutating)
        self._admitted[id(token)] = token
        return token

    @contextmanager
    def admit(self, label: str, *, mutating: bool = False,
              closing: bool = False) -> Iterator[_Admission]:
        token = self.hold(label, mutating=mutating, closing=closing)
        try:
            yield token
        finally:
            token.close()

    @contextmanager
    def exclusive(self, *, expected_scope=None, expected_fingerprint=None) -> Iterator[ProfileCut]:
        self._on_loop()
        self.lease.assert_current(expected_scope)
        blockers = self.blockers()
        if blockers:
            raise HostProfileError("profile_not_quiescent", blockers)
        # No await between closing admission and the final blocker/fingerprint check.
        cut = ProfileCut(self, self.lease.scope, uuid4().hex)
        with self._cut_lock:
            self._cut = cut
            self._released.clear()
        try:
            blockers = tuple(self._blockers())
            if blockers or self._admitted:
                raise HostProfileError("profile_not_quiescent", tuple(sorted(set(blockers))))
            if expected_fingerprint is not None:
                current = freeze_sources(self.service, cut).fingerprint
                if current != expected_fingerprint:
                    raise HostProfileError("profile_sources_changed")
            yield cut
        finally:
            with self._cut_lock:
                if cut.worker_count:
                    # A caller must use run_worker; never reopen admission over live I/O.
                    raise HostProfileError("profile_worker_cleanup_pending")
                cut.active = False
                self._cut = None
                self._released.set()

    async def run_worker(self, cut: ProfileCut, function: Callable, cancellation):
        self._on_loop()
        cut.assert_current()
        with self._cut_lock:
            if cut.worker_count:
                raise HostProfileError("profile_worker_active")
            cut.worker_count += 1
        task = asyncio.create_task(asyncio.to_thread(function))
        caller_cancelled = False
        try:
            while not task.done():
                try:
                    await asyncio.shield(task)
                except asyncio.CancelledError:
                    caller_cancelled = True
                    cancellation.set()
                except BaseException:
                    if not task.done():
                        raise
                    break
            try:
                value = task.result()
            except BaseException as error:
                return WorkerOutcome(error=error, awaiter_cancelled=caller_cancelled)
            return WorkerOutcome(value=value, awaiter_cancelled=caller_cancelled)
        finally:
            with self._cut_lock:
                cut.worker_count -= 1

    def begin_close(self):
        self._on_loop()
        self._closing = True

    async def wait_for_cut(self):
        self._on_loop()
        while self._cut is not None:
            await self._released.wait()


@dataclass(frozen=True)
class _SqliteState:
    role: str
    store: object
    connection: sqlite3.Connection | None
    fingerprint: tuple
    cold: ColdFamily | None = None


@dataclass(frozen=True)
class _JsonState:
    role: str
    document: object
    data: bytes | None
    fingerprint: tuple


def _sqlite_state(role, store):
    connection = store._connection if role == "schedules" else store._disk
    path = store.path
    if connection is None:
        family = inspect_cold_family(path, role)
        if family.members[0] is None:
            return _SqliteState(role, store, None, ("absent", family.members), family)
        kind = ("cold_delete" if family.eligible else "cold_wal" if family.wal_eligible
                else "unavailable")
        return _SqliteState(role, store, None,
                            (kind, *family.members[0], family.members, family.header), family)
    try:
        info = path.lstat()
    except FileNotFoundError:
        raise HostProfileError("profile_sqlite_missing") from None
    _regular(info)
    with store._mutex:
        checker = (store._check if role == "schedules" else store._check_disk
                   if role == "memory" else store._assert_disk_identity)
        checker()
        opened = os.fstat(store._file_fd)
        if (info.st_dev, info.st_ino) != (opened.st_dev, opened.st_ino):
            raise HostProfileError("profile_sqlite_changed")
        version = connection.execute("PRAGMA data_version").fetchone()[0]
        return _SqliteState(role, store, connection,
                            ("owned", *_file_info(info), id(connection),
                             connection.total_changes, version))


def _pairs(rows):
    value = {}
    for key, item in rows:
        if key in value:
            raise HostProfileError("profile_json_invalid")
        value[key] = item
    return value


def _json_state(role, document):
    expected = FileIdentity(*document.identity)
    with Directory(document.directory, expected=expected) as parent:
        try:
            fd = os.open(document.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK,
                         dir_fd=parent.fd)
        except FileNotFoundError:
            if document._has_seen_file:
                raise HostProfileError("profile_json_missing") from None
            # The built-in agent logically exists without a registry file.
            data = (json.dumps(document.initial(), sort_keys=True, ensure_ascii=False,
                               separators=(",", ":")).encode() if role == "agents" else None)
            if data is not None:
                document.validate(json.loads(data))
            return _JsonState(role, document, data, ("absent", parent.identity))
        try:
            info = os.fstat(fd)
            _regular(info, limit=document.max_bytes)
            data = bytearray()
            while chunk := os.read(fd, min(8192, document.max_bytes + 1 - len(data))):
                data.extend(chunk)
                if len(data) > document.max_bytes:
                    raise HostProfileError("profile_json_limit")
            value = json.loads(data, object_pairs_hook=_pairs)
            document.validate(value)
            parent.check()
            named = os.stat(document.name, dir_fd=parent.fd, follow_symlinks=False)
            if (_file_info(info) != _file_info(os.fstat(fd))
                    or _file_info(info) != _file_info(named)):
                raise HostProfileError("profile_json_changed")
            return _JsonState(role, document, bytes(data),
                              ("present", parent.identity, *_file_info(info),
                               hashlib.sha256(data).hexdigest()))
        except (UnicodeError, json.JSONDecodeError, RecursionError):
            raise HostProfileError("profile_json_invalid") from None
        finally:
            os.close(fd)


def _inventory(service, *, full_admission=True):
    root_allowed = {"profile.lock", "configuration.json", "configuration.lock",
                    "maintenance.json", "maintenance.json.lock", "transport.token", "service.sock",
                    "provider", "mcp", "workspace", "profiles", "telegram", "auth.json",
                    "restore_record.json", "restore_admission.json", "provider_choice.json"}
    for name in ("jarvis.sqlite3", "memory.sqlite3", "schedules.sqlite3"):
        root_allowed.update(name + suffix for suffix in (
            "", ".lock", ".init.lock", ".runner.lock", "-journal", "-wal", "-shm",
        ))
    entries = set(os.listdir(service.profile_lease._directory.fd))
    if full_admission:
        service.profile_lease.check_bootstrap_ready()
    else:
        service.profile_lease.assert_admission_current()
    if entries - root_allowed:
        raise HostProfileError("profile_inventory_unclassified")
    for document, allowed in (
        (service.agent_profiles.document,
         {"agents.json", "agents.json.lock", "skills.json", "skills.json.lock"}),
        (service._telegram_journal.document,
         {"telegram.json", "telegram.json.lock", "telegram.runner.lock"}),
    ):
        with Directory(document.directory, expected=FileIdentity(*document.identity)) as directory:
            if set(os.listdir(directory.fd)) - allowed:
                raise HostProfileError("profile_inventory_unclassified")


def _states(service):
    _inventory(service)
    sql = tuple(_sqlite_state(role, store) for role, store in (
        ("state", service.store), ("memory", service.memory), ("schedules", service.schedule_store),
    ))
    docs = tuple(_json_state(role, document) for role, document in (
        ("agents", service.agent_profiles.document), ("skills", service.skills.document),
        ("telegram", service._telegram_journal.document),
    ))
    return sql, docs


def _configuration_state(service):
    # Project counts from the host's already-loaded validated configuration.
    # Never serialize argv/bindings/workspace or call server_specs/_read here.
    loaded = service.config._data
    metadata = (1, loaded.get("workspace") is not None,
                len(loaded.get("bindings", {})), len(loaded.get("mcp_servers", {})))
    try:
        info = service.config.path.lstat()
    except FileNotFoundError:
        fingerprint = ("absent",)
    else:
        _regular(info, limit=131072)
        fingerprint = ("present", *_file_info(info))
    return metadata, fingerprint


def _fingerprint(scope, root_identity, sql, docs, configuration):
    identity = [scope.profile_id, scope.generation, root_identity.device, root_identity.inode,
                [(item.role, item.fingerprint) for item in (*sql, *docs)], configuration]
    return hashlib.sha256(repr(identity).encode()).hexdigest()


@dataclass(frozen=True)
class FrozenSources:
    service: object
    scope: HostScope
    cut_id: str
    fingerprint: str
    _metadata: tuple[tuple[str, str, int], ...]
    forbidden_roots: tuple[Path, ...]
    _sql: tuple[_SqliteState, ...]
    _json: tuple[_JsonState, ...]
    _configuration: tuple

    @property
    def metadata(self):
        return tuple({"role": role, "disposition": disposition, "bytes": size}
                     for role, disposition, size in self._metadata)

    @property
    def configuration_metadata(self):
        value = self._configuration[0]
        return {"version": value[0], "workspace_selected": value[1],
                "binding_count": value[2], "mcp_server_count": value[3]}

    @property
    def sqlite_recovery(self):
        return tuple({"role": row.role, "mode": "cold_wal",
                      "semantics": "confirmed_transactions_only"}
                     for row in self._sql if row.fingerprint[0] == "cold_wal")

    @property
    def root_identity(self):
        return self.service.profile_gate.lease.identity

    def assert_current(self, cut, *, full=False):
        return self._assert_current(cut, full=full, memory_mutating=False)

    def _assert_nonmemory_current(self, cut):
        # Only MemoryMutationCut invokes this after consuming an exact grant.
        # The selected MemoryStore mutex/identity/CAS then own that one row.
        return self._assert_current(cut, full=False, memory_mutating=True)

    def _assert_current(self, cut, *, full, memory_mutating):
        cut.assert_current()
        if cut.gate is not self.service.profile_gate or cut.scope != self.scope:
            raise HostProfileError("profile_generation_stale")
        _inventory(self.service, full_admission=full)
        if full:
            sql, docs = _states(self.service)
            current = _fingerprint(self.scope, self.root_identity, sql, docs,
                                   _configuration_state(self.service))
            if current != self.fingerprint:
                raise HostProfileError("profile_sources_changed")
        else:
            for row in self._sql:
                if memory_mutating and row.role == "memory":
                    continue
                if row.cold is not None:
                    row.cold.assert_current()
                    current = (row.store._connection if row.role == "schedules"
                               else row.store._disk)
                    if current is not None:
                        raise HostProfileError("profile_sources_changed")
                else:
                    current = _sqlite_state(row.role, row.store)
                    if current.fingerprint != row.fingerprint:
                        raise HostProfileError("profile_sources_changed")
            for row in self._json:
                directory = row.document.directory.lstat()
                parent_id = row.fingerprint[1]
                if (not stat.S_ISDIR(directory.st_mode)
                        or FileIdentity.of(directory) != parent_id
                        or directory.st_uid != os.getuid()
                        or stat.S_IMODE(directory.st_mode) != 0o700):
                    raise HostProfileError("profile_sources_changed")
                try:
                    current = row.document.path.lstat()
                except FileNotFoundError:
                    if row.fingerprint[0] != "absent":
                        raise HostProfileError("profile_sources_changed") from None
                else:
                    _regular(current, limit=row.document.max_bytes)
                    if (row.fingerprint[0] != "present"
                            or _file_info(current) != row.fingerprint[2:-1]):
                        raise HostProfileError("profile_sources_changed")
        if _configuration_state(self.service) != self._configuration:
            raise HostProfileError("profile_sources_changed")
        cut.assert_current()

    def _revalidate(self, cut):
        cut.assert_current()
        if cut.gate is not self.service.profile_gate or cut.scope != self.scope:
            raise HostProfileError("profile_generation_stale")
        sql, docs = _states(self.service)
        current = _fingerprint(self.scope, self.root_identity, sql, docs,
                               _configuration_state(self.service))
        if current != self.fingerprint:
            raise HostProfileError("profile_sources_changed")
        cut.assert_current()

    def _check_image_budget(self, limits, *, metadata_only=False):
        sizes = [(row.cold.physical_bytes if row.cold is not None
                  else row.fingerprint[6]) if metadata_only
                 else self._image_reservation(row) for row in self._sql
                 if row.fingerprint[0] in {"owned", "cold_delete", "cold_wal"}]
        if (any(size > limits.max_sqlite_bytes for size in sizes)
                or sum(sizes) + sum(len(row.data or b"") for row in self._json)
                > limits.max_total_bytes):
            raise HostProfileError("profile_sqlite_image_limit")

    @staticmethod
    def _image_reservation(row):
        if row.connection is not None:
            # Worker-only: the owned connection may have logical pages in WAL
            # beyond the main file. Never size its snapshot from main alone.
            with row.store._mutex:
                page_size = row.connection.execute("PRAGMA page_size").fetchone()[0]
                pages = row.connection.execute("PRAGMA page_count").fetchone()[0]
            return max(row.fingerprint[6], page_size * pages)
        if row.cold is not None:
            return row.cold.physical_bytes
        return 0

    @staticmethod
    def _remaining_image_limits(limits, used):
        remaining = min(limits.max_sqlite_bytes, limits.max_total_bytes - used)
        if remaining <= 0:
            raise HostProfileError("profile_sqlite_image_limit")
        return replace(limits, max_sqlite_bytes=remaining,
                       max_total_bytes=limits.max_total_bytes - used)

    def bind(self, cut: ProfileCut, context=None, limits=None) -> SourceBundle:
        if context is not None:
            context.checkpoint("source_bind")
        self._revalidate(cut)
        if any(row.fingerprint[0] == "unavailable" for row in self._sql):
            raise HostProfileError("profile_lazy_source_unavailable")

        limits = limits or RecoveryLimits()
        self._check_image_budget(limits)
        if any(row.cold and row.cold.snapshot_eligible for row in self._sql) and context is None:
            raise HostProfileError("profile_cold_capture_context_required")

        def check():
            self.assert_current(cut)

        sources = []
        used = (sum(len(row.data or b"") for row in self._json)
                + sum(self._image_reservation(row) for row in self._sql
                      if row.connection is not None))
        for row in self._sql:
            if row.connection is not None:
                sources.append(SqliteSource(row.role, row.connection, check))
            elif row.cold and row.cold.snapshot_eligible:
                bounded = self._remaining_image_limits(limits, used)
                data, digest = row.cold.capture_image(context, bounded, check)
                used += len(data)
                if used > limits.max_total_bytes:
                    raise HostProfileError("profile_sqlite_image_limit")
                sources.append(SqliteImageSource(row.role, data,
                                                FileIdentity(*row.fingerprint[1:3]), digest, check))
        self.assert_current(cut, full=True)
        return SourceBundle(
            self.scope.profile_id, self.scope.generation, self.service.directory,
            self.root_identity,
            tuple(sources),
            tuple(JsonSource(row.role, row.data, check) for row in self._json
                  if row.data is not None),
            tuple([row.role for row in self._sql if row.fingerprint[0] == "absent"]
                  + [row.role for row in self._json if row.data is None]),
        )

    def doctor_images(self, cut, context, limits):
        from jarvis_core.doctor import DoctorSqliteImage, DoctorSqliteImages
        from jarvis_core.recovery import capture_sqlite_snapshot

        context.checkpoint("doctor_source_images")
        self._revalidate(cut)
        self._check_image_budget(limits)
        images = []
        used = sum(len(row.data or b"") for row in self._json)
        for row in self._sql:
            self.assert_current(cut)
            if row.cold and row.cold.snapshot_eligible:
                bounded = self._remaining_image_limits(limits, used)
                data, digest = row.cold.capture_image(
                    context, bounded, lambda: self.assert_current(cut),
                )
                used += len(data)
                images.append(DoctorSqliteImage(row.role, "image", row.fingerprint[1:3],
                                               data, digest))
                continue
            if row.connection is None:
                disposition = row.fingerprint[0]
                identity = None if disposition == "absent" else row.fingerprint[1:3]
                images.append(DoctorSqliteImage(row.role, disposition, identity))
                continue
            bounded = self._remaining_image_limits(limits, used)
            source = SqliteSource(row.role, row.connection, lambda: self.assert_current(cut))
            with row.store._mutex:
                data = capture_sqlite_snapshot(source, context, bounded, self.service.directory)
            used += len(data)
            images.append(DoctorSqliteImage(row.role, "image", row.fingerprint[1:3], data,
                                           hashlib.sha256(data).hexdigest()))
        self.assert_current(cut, full=True)
        return DoctorSqliteImages(self.scope.profile_id, self.scope.generation,
                                  (self.root_identity.device, self.root_identity.inode),
                                  cut.cut_id, tuple(images))


def freeze_sources(service, cut: ProfileCut) -> FrozenSources:
    cut.assert_current()
    if cut.gate is not service.profile_gate:
        raise HostProfileError("profile_cut_mismatch")
    sql, docs = _states(service)
    configuration = _configuration_state(service)
    fingerprint = _fingerprint(cut.scope, service.profile_gate.lease.identity, sql, docs,
                               configuration)
    cut.assert_current()
    metadata = tuple((row.role,
                      "generated_builtin" if row.role == "agents"
                      and row.fingerprint[0] == "absent" else "present"
                      if row.fingerprint[0] in {"cold_delete", "cold_wal"}
                      else row.fingerprint[0],
                      row.cold.physical_bytes if row.fingerprint[0] == "cold_wal"
                      else row.fingerprint[6] if row.fingerprint[0]
                      in {"owned", "unavailable", "cold_delete"}
                      else (len(row.data) if isinstance(row, _JsonState) and row.data else 0))
                     for row in (*sql, *docs))
    return FrozenSources(service, cut.scope, cut.cut_id, fingerprint, metadata,
                         (service.directory, service._workspace_path), sql, docs, configuration)


class BoundProfileResource:
    """A host-owned synchronous store API checked after every caller await.

    Raw SQLite/document access is reserved to the cut exporter. Normal service,
    model adapters and runners all receive this same wrapper, not a raw alias.
    This is cooperative API discipline, not Python object/capability isolation.
    """

    def __init__(self, resource, lease, gate):
        self._resource, self._lease, self._gate = resource, lease, gate

    def __getattr__(self, name):
        # These report/repeat an already completed close entirely in RAM.
        if name == "_closed":
            return getattr(self._resource, name)
        if name == "close" and getattr(self._resource, "_closed", False):
            return lambda: None
        self._lease.assert_current()
        if (self._gate._cut is not None
                and name in {"workspace", "workspace_identity", "server_specs"}):
            raise HostProfileError("profile_maintenance_busy")
        value = getattr(self._resource, name)
        from jarvis_core.private_json import PrivateJsonDocument

        if isinstance(value, PrivateJsonDocument):
            return BoundProfileResource(value, self._lease, self._gate)
        # sqlite3.Connection itself is callable; it is data, not a store method.
        if not (inspect.ismethod(value) or inspect.isfunction(value) or inspect.isbuiltin(value)):
            return value

        def checked(*args, **kwargs):
            if name == "close" and getattr(self._resource, "_closed", False):
                return None
            self._lease.assert_current()
            # Only fixed, non-initializing physical checks are used by the cut
            # exporter. A method captured before the cut cannot bypass admission.
            pure_document = (isinstance(self._resource, PrivateJsonDocument)
                             and name in {"validate", "initial"})
            if (self._gate._cut is not None and not pure_document
                    and name not in {"_assert_disk_identity", "_check_disk", "_check"}):
                raise HostProfileError("profile_maintenance_busy")
            return value(*args, **kwargs)

        return checked


def service_fingerprint(service):
    rows = []
    for role, attr, connection_name in (
        ("state", "store", "_disk"), ("memory", "memory", "_disk"),
        ("schedules", "schedule_store", "_connection"),
    ):
        store = getattr(service, attr, None)
        connection = getattr(store, connection_name, None)
        rows.append((role, id(connection), connection.total_changes if connection else 0))
    return tuple(rows)


def service_blockers(service):
    codes = set()
    if service._closing:
        codes.add("service_closing")
    if service._maintenance:
        codes.add("configuration_change_active")
    if service._handles or service._refs:
        codes.add("runs_active")
    if any(not item.done() for item in service._observers):
        codes.add("settlement_active")
    if service._retiring_sessions:
        codes.add("session_cleanup_active")
    if service.scheduler._lease is not None or service.scheduler.active_count:
        codes.add("scheduler_active_or_cleanup")
    if service.process_tools and service.process_tools.has_running:
        codes.add("processes_active_or_cleanup")
    channel = service._telegram_channel
    if channel is not None and (channel._lease is not None or channel.running
                                or (channel._worker is not None and not channel._worker.done())
                                or channel._send_tickets):
        codes.add("telegram_active_or_cleanup")
    pairing = service._telegram_pairing
    if pairing is not None and (pairing._lease is not None
                                or (pairing._worker is not None and not pairing._worker.done())
                                or (pairing._expiry is not None and not pairing._expiry.done())):
        codes.add("pairing_active_or_cleanup")
    for entry in service.mcp._entries.values():
        client = entry.client
        process = client._process
        close_task = getattr(client, "_close_task", None)
        if (entry.active or (process is not None and process.returncode is None)
                or (close_task is not None and not close_task.done())
                or entry.error == "mcp_cleanup_incomplete"):
            codes.add("mcp_active_or_cleanup")
    return tuple(sorted(codes))


# Reads may initialize SQLite or provider/pairing state, so ALL handlers are
# admitted. This set only avoids false generation bumps for unchanged polling.
READ_RPCS = frozenset({
    "service.status", "service.local_status", "sessions.list", "sessions.messages",
    "runs.get", "events.poll",
    "runs.list", "tools.list", "workspace.get", "approvals.list", "memory.list",
    "memory.search", "memory.resolve", "mcp.list", "agents.list", "skills.list",
    "skills.inspect", "skills.get", "schedules.list", "schedules.history",
    "processes.list", "processes.poll", "telegram.status", "telegram.history",
    "telegram.pairing.review", "telegram.pairing.status",
})


def profile_callback(label):
    """Synchronous bridge callbacks cannot reopen a writer inside an active cut."""
    def decorate(callback):
        def invoke(service, *args, **kwargs):
            with service.profile_gate.admit(label, mutating=True, closing=True):
                return callback(service, *args, **kwargs)
        return invoke
    return decorate


def profile_background(label):
    """Admission survives every await and cleanup in an owned async callback."""
    def decorate(callback):
        async def invoke(service, *args, **kwargs):
            with service.profile_gate.admit(label, mutating=True, closing=True):
                return await callback(service, *args, **kwargs)
        return invoke
    return decorate
