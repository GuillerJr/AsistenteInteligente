"""Exact, bounded workspace selections and new private workspace candidates.

The host owns selection, approval, its mutator barrier and the checkpoint's
deadline. This module never discovers a workspace, scans HOME, follows a link,
activates a profile, runs an artifact, or restores deleted/temporary content.
Selecting a directory preserves that directory, not its unselected children.
Portable bytes carry integrity, not authenticity or permission to execute them.
Known private names and supplied forbidden roots are denied; selected regular
file contents are not classified by a secret scanner or isolated from same-UID
hostile code. The host must bind and review the exact selection.
"""

from __future__ import annotations

import errno
import hashlib
import json
import os
import re
import stat
import threading
import unicodedata
from dataclasses import dataclass, replace
from pathlib import Path

from jarvis_core.recovery_fs import FileIdentity, OwnedStage, RecoveryFsError, no_replace

FORMAT = "jarvis-workspace/1"
EXCLUSIONS = (
    "credentials_and_private_state", "unselected_paths", "temporary_and_deleted_content",
)
_ROLES = frozenset({"workspace_file", "artifact"})
_PRIVATE = frozenset({
    ".git", ".ssh", ".aws", ".codex", ".gnupg", ".jarvis", ".config", ".local",
    ".venv", ".venv.nosync", "node_modules", "library", "keychains", "credentials",
    "auth.json", "credentials.json", "cookies", "login data", ".env", ".netrc", ".npmrc",
    ".git-credentials", ".zsh_history", ".bash_history", ".python_history", "history",
    "history.json", "chat_history.json", "conversations.json", "id_rsa", "id_ed25519",
    "jarvis.sqlite3", "memory.sqlite3", "schedules.sqlite3", "configuration.json",
    "transport.token", "restore_record.json", "maintenance.sqlite3", ".pending", ".trash",
    ".ds_store", ".fseventsd", ".spotlight-v100", ".documentrevisions-v100",
})
_WINDOWS = frozenset({"con", "prn", "aux", "nul", *(f"com{i}" for i in range(1, 10)),
                      *(f"lpt{i}" for i in range(1, 10))})
_HASH = re.compile(r"[a-f0-9]{64}\Z")
_OPERATION = re.compile(r"[a-f0-9]{32}\Z")
_BLOCK = 65_536


class WorkspaceRecoveryError(ValueError):
    """Content-free failure; an owner means cleanup has not been proved complete."""

    def __init__(self, code, owner=None, *, cleanup_error_code=None, effect_status="none"):
        super().__init__(code)
        self.code, self.owner = code, owner
        self.cleanup_error_code, self.effect_status = cleanup_error_code, effect_status
        self.publication_unknown = effect_status == "unknown"


@dataclass(frozen=True)
class WorkspaceLimits:
    max_files: int = 256
    max_directories: int = 128
    max_file_bytes: int = 8 * 1024 * 1024
    max_total_bytes: int = 32 * 1024 * 1024
    max_manifest_bytes: int = 262_144
    max_depth: int = 16
    max_path_bytes: int = 1024
    max_work_bytes: int = 256 * 1024 * 1024

    def __post_init__(self):
        if any(type(value) is not int or value <= 0 for value in vars(self).values()):
            raise ValueError("workspace_recovery_limits_invalid")


@dataclass(frozen=True)
class SelectedWorkspaceEntry:
    path: str
    role: str = "workspace_file"
    kind: str = "file"


@dataclass(frozen=True)
class WorkspaceSelection:
    root: Path
    root_identity: FileIdentity
    entries: tuple[SelectedWorkspaceEntry, ...]
    forbidden_roots: tuple[Path, ...] = ()


@dataclass(frozen=True)
class WorkspaceFile:
    path: str
    data: bytes


@dataclass(frozen=True)
class WorkspaceSourceBinding:
    """RAM-only identity inventory. Never include this in a portable manifest."""

    root: Path
    root_identity: FileIdentity
    directories: tuple[tuple[str, FileIdentity], ...]
    files: tuple[tuple[str, tuple], ...]


@dataclass(frozen=True)
class WorkspaceSnapshot:
    manifest: bytes
    files: tuple[WorkspaceFile, ...]
    source_binding: WorkspaceSourceBinding | None = None
    archive_binding: WorkspaceArchiveBinding | None = None


@dataclass(frozen=True)
class WorkspaceArchiveBinding:
    """RAM-only archive identity; a digest alone cannot detect path replacement."""

    root: Path
    root_identity: FileIdentity
    manifest_sha256: str
    data_identity: FileIdentity
    files: tuple[tuple[str, tuple], ...]


@dataclass(frozen=True)
class WorkspaceSummary:
    manifest_sha256: str
    file_count: int
    directory_count: int
    artifact_count: int
    total_bytes: int


@dataclass(frozen=True)
class WorkspaceTarget:
    parent: Path
    parent_identity: FileIdentity
    name: str
    forbidden_roots: tuple[Path, ...]


@dataclass(frozen=True)
class WorkspaceRestoreResult:
    target_identity: FileIdentity
    manifest_sha256: str
    file_count: int
    directory_count: int
    total_bytes: int
    effect_status: str = "new_unselected_workspace"
    activation_required: bool = True
    replay_allowed: bool = False


def _noop(stage, write=False):
    pass


def _fail(code):
    raise WorkspaceRecoveryError("workspace_recovery_" + code)


def _code(error):
    value = getattr(error, "code", None)
    if not value and error.args and isinstance(error.args[0], str):
        value = error.args[0]
    return (value if isinstance(value, str) and re.fullmatch(r"[a-z][a-z0-9_]{0,127}", value)
            else "workspace_recovery_unavailable")


def _canonical(value):
    try:
        return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
                          allow_nan=False).encode("utf-8")
    except (ValueError, TypeError, UnicodeError, RecursionError):
        _fail("manifest_invalid")


def _component(value):
    if (not value or value in {".", ".."} or len(value.encode("utf-8")) > 255
            or value[-1:] in {".", " "} or any(char in value for char in ':<>"|?*')
            or any(unicodedata.category(char)[0] == "C" for char in value)
            or unicodedata.normalize("NFC", value) != value
            or unicodedata.normalize("NFKC", value) != value
            or value.split(".")[0].casefold() in _WINDOWS):
        _fail("path_invalid")
    lowered = value.casefold()
    if (lowered in _PRIVATE or lowered.startswith((".env.", ".jarvis-tmp-", ".jarvis-stage-",
                                                   ".configuration-"))
            or lowered.endswith((".p12", ".pfx", ".key"))
            or any(lowered == name + suffix for name in (
                "jarvis.sqlite3", "memory.sqlite3", "schedules.sqlite3", "maintenance.sqlite3"
            ) for suffix in ("-wal", "-shm", "-journal"))):
        _fail("private_path")


def _parts(path, limits):
    try:
        if (not isinstance(path, str) or not path or "\\" in path
                or len(path.encode("utf-8")) > limits.max_path_bytes):
            _fail("path_invalid")
        parts = tuple(path.split("/"))
        if len(parts) > limits.max_depth:
            _fail("depth_limit")
        for part in parts:
            _component(part)
        return parts
    except UnicodeError:
        _fail("path_invalid")


def _absolute(path):
    if not isinstance(path, (str, Path)):
        _fail("root_invalid")
    result = Path(path)
    if not result.is_absolute() or ".." in result.parts or len(result.parts) > 64:
        _fail("root_invalid")
    return result


def _overlap(first, second):
    # Conservative on case-sensitive hosts too: a portable selection must not
    # change meaning when restored onto a case-insensitive filesystem.
    first_parts = tuple(part.casefold() for part in first.parts)
    second_parts = tuple(part.casefold() for part in second.parts)
    return (first_parts[:len(second_parts)] == second_parts
            or second_parts[:len(first_parts)] == first_parts)


def _fingerprint(info):
    return (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns,
            info.st_mode, info.st_uid, info.st_nlink)


class _Work:
    def __init__(self, limits, checkpoint):
        self.remaining, self.checkpoint = limits.max_work_bytes, checkpoint

    def step(self, stage, amount=0, *, write=False):
        self.checkpoint(stage, write)
        self.remaining -= amount
        if self.remaining < 0:
            _fail("work_limit")


class _Handle:
    """Never retry an ambiguous close on a possibly reused descriptor number."""

    def __init__(self, fd):
        self.fd, self.identity, self.uncertain = fd, None, False

    def close(self):
        if self.fd < 0:
            return
        try:
            info = os.fstat(self.fd)
        except OSError as error:
            if error.errno == errno.EBADF:
                self.fd = -1
                return
            raise
        if self.uncertain or (self.identity is not None and FileIdentity.of(info) != self.identity):
            _fail("cleanup_pending")
        self.identity = FileIdentity.of(info)
        try:
            os.close(self.fd)
        except BaseException:
            self.uncertain = True
            raise
        self.fd = -1


class _DirectoryPin:
    def __init__(self, path, *, expected=None, private=True, owned=True):
        self.path, self.expected, self.private, self.owned = path, expected, private, owned
        self.handles, self.names = [], []
        self.fd, self.identity = -1, None

    def open(self):
        for index, name in enumerate(self.path.parts):
            handle = _Handle(os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                                     dir_fd=None if index == 0 else self.handles[-1].fd))
            self.handles.append(handle)
            self.names.append(name)
            handle.identity = FileIdentity.of(os.fstat(handle.fd))
        self.fd, self.identity = self.handles[-1].fd, self.handles[-1].identity
        if self.expected is not None and self.expected != self.identity:
            _fail("source_changed")
        self.check()
        return self

    def check(self):
        if self.fd < 0:
            _fail("source_changed")
        for parent, name, child in zip(
            self.handles, self.names[1:], self.handles[1:], strict=False
        ):
            info = os.stat(name, dir_fd=parent.fd, follow_symlinks=False)
            if (not stat.S_ISDIR(info.st_mode) or FileIdentity.of(info) != child.identity
                    or FileIdentity.of(os.fstat(child.fd)) != child.identity):
                _fail("source_changed")
        info = os.fstat(self.fd)
        if (FileIdentity.of(info) != self.identity
                or (self.owned and (info.st_uid != os.getuid()
                    or stat.S_IMODE(info.st_mode) & 0o022
                    or (self.private and stat.S_IMODE(info.st_mode) != 0o700)))):
            _fail("directory_unsafe")

    def close(self):
        while self.handles:
            self.handles[-1].close()
            self.handles.pop()
            self.names.pop()
        self.fd = -1


class WorkspaceRecoveryOwner:
    """Exact custody after failure, without recapturing, replaying or adopting paths.

    cleanup() is serialized, independent of an expired approval, and only deletes
    identity-bound staging entries. A publication with a lost acknowledgement
    remains unknown: cleanup closes descriptors but never deletes its candidate.
    """

    def __init__(self):
        self.pins, self.handles = [], []
        self.parent, self.stage = None, None
        self.creation_unknown, self.publication_attempted = False, False
        self.publication_unknown = False
        self.directories, self.files = {}, {}
        self.root_removed, self.removal_synced, self.closed = False, False, False
        self._lock = threading.RLock()

    def pin(self, path, **kwargs):
        pin = _DirectoryPin(path, **kwargs)
        self.pins.append(pin)  # Register before open/stat can fail.
        return pin.open()

    def file(self, directory, name, flags, mode=0o600):
        handle = _Handle(os.open(name, flags, mode, dir_fd=directory.fd))
        self.handles.append(handle)
        info = os.fstat(handle.fd)
        handle.identity = FileIdentity.of(info)
        return handle, info

    def allocate(self, parent, operation_id, work):
        self.parent = parent
        # OwnedStage is registered before its constructor performs mkdir/stat.
        work.step("workspace_restore_stage", write=True)
        parent.check()
        self.stage = OwnedStage.__new__(OwnedStage)
        self.creation_unknown = True
        try:
            OwnedStage.__init__(self.stage, parent, operation_id, lambda: None)
        except FileExistsError:
            self.creation_unknown = False
            self.stage = None
            raise
        self.creation_unknown = False
        # Use the same retained descriptor semantics as source pins for its root.
        old_root = self.stage.root
        pin = _DirectoryPin(old_root.path, expected=self.stage.identity)
        pin.handles = [_Handle(fd) for fd in old_root._fds]
        pin.names = [old_root.path.anchor, *old_root._names]
        pin.fd, pin.identity = old_root.fd, old_root.identity
        old_root._fds, old_root.fd = [], -1
        self.stage.root = pin
        self.pins.append(pin)
        for handle in pin.handles:
            handle.identity = FileIdentity.of(os.fstat(handle.fd))

    def _cleanup_stage(self):
        if self.creation_unknown:
            _fail("cleanup_pending")
        if self.stage is None or self.root_removed:
            return
        if self.publication_attempted:
            # Historical effect uncertainty is separate from live capabilities.
            # Leave both possible names intact; never retry the rename.
            self.publication_unknown = True
            return
        root = self.stage.root
        self.parent.check()
        root.check()
        if FileIdentity.of(os.stat(self.stage.name, dir_fd=self.parent.fd,
                                   follow_symlinks=False)) != self.stage.identity:
            _fail("cleanup_pending")
        # Fresh pins are registered before opening. No recursive deletion and no
        # inference of ownership from a name or from the current directory tree.
        for path, identity in reversed(tuple(self.files.items())):
            parts = path.split("/")
            parent = root if len(parts) == 1 else self.pin(
                root.path.joinpath(*parts[:-1]), expected=self.directories["/".join(parts[:-1])])
            try:
                info = os.stat(parts[-1], dir_fd=parent.fd, follow_symlinks=False)
            except FileNotFoundError:
                self.files.pop(path)
                continue
            if FileIdentity.of(info) != identity or not stat.S_ISREG(info.st_mode):
                _fail("cleanup_pending")
            os.unlink(parts[-1], dir_fd=parent.fd)
            self.files.pop(path)
        for path, identity in sorted(self.directories.items(),
                                     key=lambda row: (-row[0].count("/"), row[0])):
            parts = path.split("/")
            parent = root if len(parts) == 1 else self.pin(
                root.path.joinpath(*parts[:-1]), expected=self.directories["/".join(parts[:-1])])
            try:
                info = os.stat(parts[-1], dir_fd=parent.fd, follow_symlinks=False)
            except FileNotFoundError:
                self.directories.pop(path)
                continue
            if FileIdentity.of(info) != identity or not stat.S_ISDIR(info.st_mode):
                _fail("cleanup_pending")
            os.rmdir(parts[-1], dir_fd=parent.fd)
            self.directories.pop(path)
        os.rmdir(self.stage.name, dir_fd=self.parent.fd)
        self.root_removed = True
        os.fsync(self.parent.fd)
        self.removal_synced = True

    def cleanup(self):
        with self._lock:
            if self.closed:
                return
            error = None
            for handle in reversed(self.handles):
                try:
                    handle.close()
                except BaseException as caught:
                    error = error or caught
            try:
                self._cleanup_stage()
                if self.root_removed and not self.removal_synced:
                    self.parent.check()
                    os.fsync(self.parent.fd)
                    self.removal_synced = True
            except BaseException as caught:
                error = error or caught
            # A failed staging cleanup keeps its path/parent capabilities alive.
            if error is None:
                for pin in reversed(self.pins):
                    try:
                        pin.close()
                    except BaseException as caught:
                        error = error or caught
            if error is not None:
                raise WorkspaceRecoveryError("workspace_recovery_cleanup_pending", self,
                                             cleanup_error_code=_code(error)) from None
            self.closed = True


def _settle(owner, error=None, *, published=False):
    try:
        owner.cleanup()
    except BaseException as cleanup:
        raise WorkspaceRecoveryError(
            _code(error) if error is not None else "workspace_recovery_cleanup_pending", owner,
            cleanup_error_code=_code(cleanup),
            effect_status="unknown" if published else "no_published_effect",
        ) from None
    if error is not None:
        raise WorkspaceRecoveryError(
            _code(error), effect_status="unknown" if published else "no_published_effect"
        ) from None


def _inventory(entries, limits):
    if not isinstance(entries, tuple) or len(entries) > limits.max_files + limits.max_directories:
        _fail("entry_limit")
    directories, files, spellings, explicit = set(), {}, {}, set()
    for entry in entries:
        if not isinstance(entry, SelectedWorkspaceEntry):
            _fail("selection_invalid")
        parts = _parts(entry.path, limits)
        if (not isinstance(entry.kind, str) or entry.kind not in {"file", "directory"}
                or not isinstance(entry.role, str) or entry.role not in _ROLES):
            _fail("selection_invalid")
        if entry.path in explicit:
            _fail("path_collision")
        explicit.add(entry.path)
        if entry.kind == "file":
            if entry.path in files or entry.path in directories:
                _fail("path_collision")
            files[entry.path] = entry.role
        else:
            if entry.path in files:
                _fail("path_collision")
            directories.add(entry.path)
        for length in range(1, len(parts) + 1):
            path = "/".join(parts[:length])
            key = path.casefold()
            if key in spellings and spellings[key] != path:
                _fail("path_collision")
            spellings[key] = path
        directories.update("/".join(parts[:length]) for length in range(1, len(parts)))
    if directories.intersection(files):
        _fail("path_collision")
    if len(files) > limits.max_files or len(directories) > limits.max_directories:
        _fail("entry_limit")
    return sorted(directories), dict(sorted(files.items()))


def _safe_source(selection, limits):
    if not isinstance(selection, WorkspaceSelection) or not isinstance(
        selection.root_identity, FileIdentity
    ):
        _fail("selection_invalid")
    root = _absolute(selection.root)
    if root == Path(root.anchor):
        _fail("root_invalid")
    for part in root.parts[1:]:
        _component(part)
    if not isinstance(selection.forbidden_roots, tuple):
        _fail("selection_invalid")
    for forbidden in selection.forbidden_roots:
        if _overlap(root, _absolute(forbidden)):
            _fail("source_forbidden")
    return root, _inventory(selection.entries, limits)


def _deny_physical_overlap(owner, directory, forbidden_roots, *, source=False):
    """Inspect only ancestor metadata, including aliases the lexical guard missed."""
    ancestors = {handle.identity for handle in directory.handles}
    for path in forbidden_roots:
        try:
            forbidden = owner.pin(_absolute(path), private=False, owned=False)
        except FileNotFoundError:
            continue
        if (forbidden.identity in ancestors
                or (source and directory.identity in {
                    handle.identity for handle in forbidden.handles
                })):
            _fail("source_forbidden" if source else "destination_overlaps_source")
        forbidden.close()
    directory.check()


def _source_file(owner, root, path, directories, work, limits, expected=None, remaining=None):
    parts = path.split("/")
    parent = root if len(parts) == 1 else owner.pin(
        root.path.joinpath(*parts[:-1]), expected=directories["/".join(parts[:-1])],
        private=root.private)
    root.check()
    work.step("workspace_source_open")
    handle, info = owner.file(parent, parts[-1], os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_nlink != 1
            or stat.S_IMODE(info.st_mode) & 0o022
            or (root.private and stat.S_IMODE(info.st_mode) != 0o600)
            or info.st_dev != root.identity.device):
        _fail("file_unsafe")
    if info.st_size > limits.max_file_bytes:
        _fail("file_limit")
    if remaining is not None and info.st_size > remaining:
        _fail("total_limit")
    fingerprint = _fingerprint(info)
    if expected is not None and fingerprint != expected:
        _fail("source_changed")
    result = bytearray()
    while len(result) < info.st_size:
        amount = min(_BLOCK, info.st_size - len(result))
        work.step("workspace_source_read", amount)
        chunk = os.pread(handle.fd, amount, len(result))
        if len(chunk) != amount:
            _fail("source_changed")
        result.extend(chunk)
    work.step("workspace_source_read_done")
    if (_fingerprint(os.fstat(handle.fd)) != fingerprint
            or _fingerprint(os.stat(parts[-1], dir_fd=parent.fd,
                                    follow_symlinks=False)) != fingerprint):
        _fail("source_changed")
    root.check()
    parent.check()
    handle.close()
    if parent is not root:
        parent.close()
    return bytes(result), fingerprint


def capture_workspace(selection, checkpoint=_noop, *, limits=None):
    """Double-read exactly selected, already authorized local paths into RAM."""
    limits = limits or WorkspaceLimits()
    root_path, (directory_paths, file_roles) = _safe_source(selection, limits)
    work, owner = _Work(limits, checkpoint), WorkspaceRecoveryOwner()
    try:
        work.step("workspace_capture_begin")
        root = owner.pin(root_path, expected=selection.root_identity, private=False)
        _deny_physical_overlap(owner, root, selection.forbidden_roots, source=True)
        directories, fingerprints, files, records, total = {}, {}, [], [], 0
        for path in directory_paths:
            work.step("workspace_source_directory")
            pin = owner.pin(root.path / path, private=False)
            if pin.identity.device != root.identity.device:
                _fail("directory_unsafe")
            directories[path] = pin.identity
            records.append({"path": path, "kind": "directory", "role": "directory"})
            pin.close()
        for path, role in file_roles.items():
            data, fingerprint = _source_file(
                owner, root, path, directories, work, limits,
                remaining=limits.max_total_bytes - total,
            )
            total += len(data)
            if total > limits.max_total_bytes:
                _fail("total_limit")
            fingerprints[path] = fingerprint
            files.append(WorkspaceFile(path, data))
            records.append({"path": path, "kind": "file", "role": role,
                            "size": len(data), "sha256": hashlib.sha256(data).hexdigest()})
        for file in files:
            work.step("workspace_capture_second_pass")
            data, _ = _source_file(owner, root, file.path, directories, work, limits,
                                   fingerprints[file.path])
            if hashlib.sha256(data).digest() != hashlib.sha256(file.data).digest():
                _fail("source_changed")
        work.step("workspace_capture_final_check")
        _check_binding(owner, WorkspaceSourceBinding(root_path, root.identity,
                       tuple(directories.items()), tuple(fingerprints.items())), work.checkpoint)
        manifest = _canonical({
            "format": FORMAT, "entries": sorted(records, key=lambda r: r["path"]),
            "total_bytes": total, "exclusions": list(EXCLUSIONS),
        })
        snapshot = WorkspaceSnapshot(manifest, tuple(files), WorkspaceSourceBinding(
            root_path, root.identity, tuple(directories.items()), tuple(fingerprints.items())))
        validate_workspace_snapshot(snapshot, limits=limits)
    except BaseException as error:
        _settle(owner, error)
    _settle(owner)
    return snapshot


def _check_binding(owner, binding, checkpoint):
    root = owner.pin(binding.root, expected=binding.root_identity, private=False)
    for path, identity in binding.directories:
        checkpoint("workspace_source_revalidate_directory", False)
        pin = owner.pin(binding.root / path, expected=identity, private=False)
        pin.close()
    directory_ids = dict(binding.directories)
    for path, fingerprint in binding.files:
        checkpoint("workspace_source_revalidate_file", False)
        parts = path.split("/")
        parent = root if len(parts) == 1 else owner.pin(
            binding.root.joinpath(*parts[:-1]), expected=directory_ids["/".join(parts[:-1])],
            private=False)
        if _fingerprint(os.stat(parts[-1], dir_fd=parent.fd,
                                follow_symlinks=False)) != fingerprint:
            _fail("source_changed")
        if parent is not root:
            parent.close()
    root.check()
    root.close()


def check_workspace_source(snapshot, checkpoint=_noop, *, limits=None):
    """Revalidate a captured RAM binding; not an approval or a new snapshot."""
    if not isinstance(snapshot, WorkspaceSnapshot) or snapshot.source_binding is None:
        _fail("source_binding_required")
    limits = limits or WorkspaceLimits()
    manifest = _manifest(snapshot, limits)
    binding = snapshot.source_binding
    if (not isinstance(binding, WorkspaceSourceBinding)
            or not isinstance(binding.directories, tuple) or not isinstance(binding.files, tuple)
            or len(binding.directories) != sum(
                r["kind"] == "directory" for r in manifest["entries"])
            or len(binding.files) != len(snapshot.files)):
        _fail("source_binding_invalid")
    expected_directories = [r["path"] for r in manifest["entries"] if r["kind"] == "directory"]
    expected_files = [r["path"] for r in manifest["entries"] if r["kind"] == "file"]
    try:
        if (list(dict(binding.directories)) != expected_directories
                or list(dict(binding.files)) != expected_files
                or any(not isinstance(value, FileIdentity) for _, value in binding.directories)
                or any(not isinstance(value, tuple) or len(value) != 8
                       or any(type(item) is not int for item in value)
                       for _, value in binding.files)):
            _fail("source_binding_invalid")
    except (TypeError, ValueError):
        _fail("source_binding_invalid")
    # Reuse the pre-I/O root/path/privacy checks without enumerating anything.
    _safe_source(WorkspaceSelection(binding.root, binding.root_identity, tuple(
        SelectedWorkspaceEntry(row["path"], kind=row["kind"])
        for row in manifest["entries"]
    )), limits)
    owner = WorkspaceRecoveryOwner()
    try:
        checkpoint("workspace_source_revalidate", False)
        _check_binding(owner, binding, checkpoint)
    except BaseException as error:
        _settle(owner, error)
    _settle(owner)


def _manifest(snapshot, limits, *, payload=True):
    if (not isinstance(snapshot, WorkspaceSnapshot) or type(snapshot.manifest) is not bytes
            or len(snapshot.manifest) > limits.max_manifest_bytes
            or not isinstance(snapshot.files, tuple)):
        _fail("manifest_limit")

    def pairs(rows):
        result = {}
        for key, value in rows:
            if key in result:
                _fail("manifest_invalid")
            result[key] = value
        return result

    try:
        value = json.loads(snapshot.manifest, object_pairs_hook=pairs)
    except (ValueError, UnicodeError, RecursionError):
        _fail("manifest_invalid")
    if (not isinstance(value, dict) or set(value) != {"format", "entries", "total_bytes",
                                                    "exclusions"}
            or value["format"] != FORMAT or value["exclusions"] != list(EXCLUSIONS)
            or type(value["total_bytes"]) is not int or value["total_bytes"] < 0
            or not isinstance(value["entries"], list)
            or len(value["entries"]) > limits.max_files + limits.max_directories
            or _canonical(value) != snapshot.manifest):
        _fail("manifest_invalid")
    selections, paths, sizes, total = [], [], {}, 0
    for record in value["entries"]:
        if (not isinstance(record, dict) or not isinstance(record.get("kind"), str)
                or record["kind"] not in {"file", "directory"}):
            _fail("manifest_invalid")
        path, kind, role = record.get("path"), record["kind"], record.get("role")
        _parts(path, limits)
        if kind == "directory":
            if set(record) != {"path", "kind", "role"} or role != "directory":
                _fail("manifest_invalid")
            selections.append(SelectedWorkspaceEntry(path, kind="directory"))
        else:
            if (set(record) != {"path", "kind", "role", "size", "sha256"}
                    or not isinstance(role, str) or role not in _ROLES
                    or type(record["size"]) is not int
                    or not 0 <= record["size"] <= limits.max_file_bytes
                    or not isinstance(record["sha256"], str)
                    or not _HASH.fullmatch(record["sha256"])):
                _fail("manifest_invalid")
            selections.append(SelectedWorkspaceEntry(path, role))
            sizes[path] = record
            total += record["size"]
        paths.append(path)
    directory_paths, file_roles = _inventory(tuple(selections), limits)
    if (paths != sorted(set(paths)) or set(directory_paths) != {
        row["path"] for row in value["entries"] if row["kind"] == "directory"
    } or total != value["total_bytes"] or total > limits.max_total_bytes
            or (payload and len(snapshot.files) != len(file_roles))):
        _fail("manifest_invalid")
    if payload:
        for file, expected_path in zip(snapshot.files, file_roles, strict=True):
            if (not isinstance(file, WorkspaceFile) or file.path != expected_path
                    or type(file.data) is not bytes or len(file.data) != sizes[file.path]["size"]
                    or hashlib.sha256(file.data).hexdigest() != sizes[file.path]["sha256"]):
                _fail("payload_invalid")
    return value


def validate_workspace_snapshot(snapshot, *, limits=None):
    limits = limits or WorkspaceLimits()
    manifest = _manifest(snapshot, limits)
    files = [row for row in manifest["entries"] if row["kind"] == "file"]
    return WorkspaceSummary(hashlib.sha256(snapshot.manifest).hexdigest(), len(files),
                            len(manifest["entries"]) - len(files),
                            sum(row["role"] == "artifact" for row in files),
                            manifest["total_bytes"])


def _verify_tree(owner, root, manifest, work, limits):
    records = {row["path"]: row for row in manifest["entries"]}
    expected = {"": set()}
    for path, row in records.items():
        parts = path.split("/")
        expected.setdefault("/".join(parts[:-1]), set()).add(parts[-1])
        if row["kind"] == "directory":
            expected.setdefault(path, set())
    identities, fingerprints = {}, {}
    for path, names in expected.items():
        work.step("workspace_restore_verify_directory")
        pin = root if not path else owner.pin(root.path / path)
        if path and pin.identity != owner.directories[path]:
            _fail("verification_failed")
        actual = set()
        with os.scandir(pin.fd) as rows:
            for entry in rows:
                actual.add(entry.name)
                if len(actual) > limits.max_files + limits.max_directories:
                    _fail("entry_limit")
        if actual != names:
            _fail("verification_failed")
        if path:
            identities[path] = pin.identity
            pin.close()
    for path, row in records.items():
        if row["kind"] == "file":
            data, fingerprint = _source_file(owner, root, path, identities, work, limits)
            if (FileIdentity(fingerprint[0], fingerprint[1]) != owner.files[path]
                    or len(data) != row["size"]
                    or hashlib.sha256(data).hexdigest() != row["sha256"]):
                _fail("verification_failed")
            fingerprints[path] = fingerprint
    # No callback follows this final boundary before the final observations.
    # Check all earlier bytes by their identity/ctime fingerprint, and repeat
    # directory inventory so a later read cannot hide an added sibling.
    work.step("workspace_restore_verify_final")
    for path, names in expected.items():
        pin = root if not path else owner.pin(root.path / path, expected=identities[path])
        actual = set()
        with os.scandir(pin.fd) as rows:
            for entry in rows:
                actual.add(entry.name)
                if len(actual) > limits.max_files + limits.max_directories:
                    _fail("entry_limit")
        if actual != names:
            _fail("verification_failed")
        for name in names:
            relative = path + "/" + name if path else name
            if relative in fingerprints and _fingerprint(os.stat(
                name, dir_fd=pin.fd, follow_symlinks=False
            )) != fingerprints[relative]:
                _fail("verification_failed")
        if pin is not root:
            pin.close()
    root.check()


def restore_workspace_new(snapshot, target, operation_id, checkpoint=_noop, *, limits=None,
                          publish=no_replace):
    """Publish one new, verified private tree. Never select it in configuration."""
    limits = limits or WorkspaceLimits()
    manifest = _manifest(snapshot, limits)
    summary = validate_workspace_snapshot(snapshot, limits=limits)
    if (not isinstance(target, WorkspaceTarget)
            or not isinstance(target.parent_identity, FileIdentity)
            or not isinstance(operation_id, str) or not _OPERATION.fullmatch(operation_id)
            or not isinstance(target.forbidden_roots, tuple) or not target.forbidden_roots):
        _fail("target_invalid")
    parts = _parts(target.name, limits)
    if len(parts) != 1:
        _fail("target_invalid")
    parent_path = _absolute(target.parent)
    for part in parent_path.parts[1:]:
        _component(part)
    destination = parent_path / target.name
    forbidden = list(target.forbidden_roots)
    if snapshot.source_binding is not None:
        forbidden.append(snapshot.source_binding.root)
    if snapshot.archive_binding is not None:
        forbidden.append(snapshot.archive_binding.root)
    if any(_overlap(destination, _absolute(path)) for path in forbidden):
        _fail("destination_overlaps_source")
    owner, work = WorkspaceRecoveryOwner(), _Work(limits, checkpoint)
    try:
        work.step("workspace_restore_begin")
        parent = owner.pin(parent_path, expected=target.parent_identity, private=False)
        _deny_physical_overlap(owner, parent, forbidden)
        # The exclusive rename is authoritative; this check avoids writing a
        # candidate at all for an already existing destination.
        try:
            os.stat(target.name, dir_fd=parent.fd, follow_symlinks=False)
        except FileNotFoundError:
            pass
        else:
            _fail("destination_exists")
        owner.allocate(parent, operation_id, work)
        root = owner.stage.root
        for row in manifest["entries"]:
            path = row["path"]
            if row["kind"] != "directory":
                continue
            pieces = path.split("/")
            directory = root if len(pieces) == 1 else owner.pin(
                root.path.joinpath(*pieces[:-1]), expected=owner.directories["/".join(pieces[:-1])])
            work.step("workspace_restore_mkdir", write=True)
            root.check()
            directory.check()
            # Unacknowledged creation must not adopt whatever later owns the name.
            owner.creation_unknown = True
            try:
                os.mkdir(pieces[-1], 0o700, dir_fd=directory.fd)
            except FileExistsError:
                owner.creation_unknown = False
                raise
            owner.directories[path] = FileIdentity.of(os.stat(
                pieces[-1], dir_fd=directory.fd, follow_symlinks=False))
            owner.creation_unknown = False
            if directory is not root:
                directory.close()
        for file in snapshot.files:
            pieces = file.path.split("/")
            directory = root if len(pieces) == 1 else owner.pin(
                root.path.joinpath(*pieces[:-1]), expected=owner.directories["/".join(pieces[:-1])])
            work.step("workspace_restore_write", write=True)
            root.check()
            directory.check()
            owner.creation_unknown = True
            try:
                handle, info = owner.file(directory, pieces[-1], os.O_WRONLY | os.O_CREAT
                                          | os.O_EXCL | os.O_NOFOLLOW)
            except FileExistsError:
                owner.creation_unknown = False
                raise
            owner.files[file.path] = FileIdentity.of(info)
            owner.creation_unknown = False
            position = 0
            while position < len(file.data):
                work.step("workspace_restore_write_chunk", min(_BLOCK, len(file.data) - position),
                          write=True)
                wrote = os.write(handle.fd, file.data[position:position + _BLOCK])
                if wrote <= 0:
                    _fail("write_failed")
                position += wrote
            os.fsync(handle.fd)
            handle.close()
            if directory is not root:
                directory.close()
        _verify_tree(owner, root, manifest, work, limits)
        for path in sorted(owner.directories, key=lambda p: -p.count("/")):
            directory = owner.pin(root.path / path, expected=owner.directories[path])
            os.fsync(directory.fd)
            directory.close()
        os.fsync(root.fd)
        work.step("workspace_restore_before_publish", write=True)
        parent.check()
        root.check()
        owner.publication_attempted = True
        try:
            publish(parent, owner.stage.name, target.name)
        except RecoveryFsError as error:
            if error.code in {
                "recovery_destination_exists", "recovery_exclusive_publish_unavailable"
            }:
                owner.publication_attempted = False
            raise
        os.fsync(parent.fd)
        published = owner.pin(destination, expected=owner.stage.identity)
        work.step("workspace_restore_confirm", write=True)
        _verify_tree(owner, published, manifest, work, limits)
        published.check()
        # A confirmed tree is no longer a cleanup resource, and is never deleted.
        owner.stage, owner.publication_attempted = None, False
        result = WorkspaceRestoreResult(published.identity, summary.manifest_sha256,
                                        summary.file_count, summary.directory_count,
                                        summary.total_bytes)
    except BaseException as error:
        _settle(owner, error, published=owner.publication_attempted)
    _settle(owner, published=True)
    return result


def _archive_files(manifest):
    return [(f"data/{index:08d}.bin", row) for index, row in enumerate(
        row for row in manifest["entries"] if row["kind"] == "file"
    )]


def _archive_inventory(owner, root, data, manifest, checkpoint):
    """Enumerate only the selected private artifact, with a known exact inventory."""
    checkpoint("workspace_archive_inventory", False)
    for directory, expected in (
        (root, {"manifest.json", "data"}),
        (data, {path.split("/")[-1] for path, _ in _archive_files(manifest)}),
    ):
        directory.check()
        names = set()
        with os.scandir(directory.fd) as entries:
            for entry in entries:
                names.add(entry.name)
                if len(names) > len(expected):
                    _fail("archive_inventory_invalid")
        if names != expected:
            _fail("archive_inventory_invalid")
        directory.check()


def _archive_manifest_file(owner, root, work, limits, expected=None):
    manifest_limits = replace(limits, max_file_bytes=limits.max_manifest_bytes)
    return _source_file(owner, root, "manifest.json", {}, work, manifest_limits, expected)


def write_workspace_archive_new(snapshot, target, operation_id, checkpoint=_noop, *, limits=None,
                                publish=no_replace):
    """Write a separate portable directory, never alter a profile-v1 artifact.

    Exact layout: manifest.json and data/00000000.bin ... . Original relative
    paths and roles live only in the canonical manifest; no archive extraction,
    execution permission or activation is inferred from them.
    """
    limits = limits or WorkspaceLimits()
    manifest = _manifest(snapshot, limits)
    files = [WorkspaceFile("manifest.json", snapshot.manifest)]
    for (archive_path, _), source in zip(_archive_files(manifest), snapshot.files, strict=True):
        files.append(WorkspaceFile(archive_path, source.data))
    files.sort(key=lambda file: file.path)
    records = [{"path": "data", "kind": "directory", "role": "directory"}]
    records.extend({"path": file.path, "kind": "file", "role": "artifact", "size": len(file.data),
                    "sha256": hashlib.sha256(file.data).hexdigest()} for file in files)
    records.sort(key=lambda record: record["path"])
    wrapper = WorkspaceSnapshot(_canonical({
        "format": FORMAT, "entries": records, "total_bytes": sum(len(file.data) for file in files),
        "exclusions": list(EXCLUSIONS),
    }), tuple(files), snapshot.source_binding, snapshot.archive_binding)
    # Envelope metadata is admitted separately from the user's payload budgets.
    envelope_limits = replace(
        limits, max_files=limits.max_files + 1, max_directories=max(1, limits.max_directories),
        max_file_bytes=max(limits.max_file_bytes, limits.max_manifest_bytes),
        max_total_bytes=limits.max_total_bytes + limits.max_manifest_bytes,
        max_manifest_bytes=max(limits.max_manifest_bytes, (limits.max_files + 2) * 512),
        max_depth=max(2, limits.max_depth), max_path_bytes=max(17, limits.max_path_bytes),
        max_work_bytes=limits.max_work_bytes + 3 * limits.max_manifest_bytes,
    )
    result = restore_workspace_new(wrapper, target, operation_id, checkpoint,
                                   limits=envelope_limits, publish=publish)
    summary = validate_workspace_snapshot(snapshot, limits=limits)
    return replace(result, manifest_sha256=summary.manifest_sha256, file_count=summary.file_count,
                   directory_count=summary.directory_count, total_bytes=summary.total_bytes,
                   effect_status="new_workspace_archive", activation_required=False)


def read_workspace_archive(path, expected_manifest_sha=None, checkpoint=_noop, *, limits=None,
                           expected_identity=None):
    """Read one selected archive, twice; None expected hash is review-only.

    A host dispatch must bind the returned archive identity and manifest digest
    to its reviewed operation. A same-bytes path replacement is not equivalent.
    No original workspace, profile or provider credentials are consulted.
    """
    limits = limits or WorkspaceLimits()
    root_path = _absolute(path)
    for part in root_path.parts[1:]:
        _component(part)
    if ((expected_manifest_sha is not None and (
        not isinstance(expected_manifest_sha, str) or not _HASH.fullmatch(expected_manifest_sha)
    )) or (expected_identity is not None and not isinstance(expected_identity, FileIdentity))):
        _fail("archive_expectation_invalid")
    owner, work = WorkspaceRecoveryOwner(), _Work(limits, checkpoint)
    try:
        work.step("workspace_archive_read_begin")
        root = owner.pin(root_path, expected=expected_identity)
        raw, manifest_fingerprint = _archive_manifest_file(owner, root, work, limits)
        manifest_sha = hashlib.sha256(raw).hexdigest()
        if expected_manifest_sha is not None and manifest_sha != expected_manifest_sha:
            _fail("archive_review_changed")
        manifest = _manifest(WorkspaceSnapshot(raw, ()), limits, payload=False)
        data = owner.pin(root.path / "data")
        if data.identity.device != root.identity.device:
            _fail("directory_unsafe")
        _archive_inventory(owner, root, data, manifest, checkpoint)
        fingerprints, files = {"manifest.json": manifest_fingerprint}, []
        for archive_path, record in _archive_files(manifest):
            value, fingerprint = _source_file(owner, root, archive_path, {"data": data.identity},
                                              work, limits, remaining=record["size"])
            if (len(value) != record["size"]
                    or hashlib.sha256(value).hexdigest() != record["sha256"]):
                _fail("archive_payload_invalid")
            fingerprints[archive_path] = fingerprint
            files.append(WorkspaceFile(record["path"], value))
        work.step("workspace_archive_second_pass")
        second, _ = _archive_manifest_file(owner, root, work, limits, manifest_fingerprint)
        if second != raw:
            _fail("source_changed")
        for archive_path, record in _archive_files(manifest):
            value, _ = _source_file(owner, root, archive_path, {"data": data.identity},
                                    work, limits, fingerprints[archive_path])
            if (len(value) != record["size"]
                    or hashlib.sha256(value).hexdigest() != record["sha256"]):
                _fail("source_changed")
        binding = WorkspaceArchiveBinding(root_path, root.identity, manifest_sha, data.identity,
                                           tuple(fingerprints.items()))
        _check_archive_metadata(owner, root, data, manifest, binding, checkpoint)
        snapshot = WorkspaceSnapshot(raw, tuple(files), archive_binding=binding)
        validate_workspace_snapshot(snapshot, limits=limits)
    except BaseException as error:
        _settle(owner, error)
    _settle(owner)
    return snapshot


def _check_archive_metadata(owner, root, data, manifest, binding, checkpoint):
    _archive_inventory(owner, root, data, manifest, checkpoint)
    checkpoint("workspace_archive_final_check", False)
    root.check()
    data.check()
    for path, fingerprint in binding.files:
        pieces = path.split("/")
        directory = root if len(pieces) == 1 else data
        if _fingerprint(os.stat(pieces[-1], dir_fd=directory.fd,
                                follow_symlinks=False)) != fingerprint:
            _fail("source_changed")
    # Repeat inventory after the final guard; do not run another callback after
    # observing it. Contents are checked through their just-verified ctime IDs.
    _archive_inventory(owner, root, data, manifest, _noop)


def check_workspace_archive_source(snapshot, checkpoint=_noop, *, limits=None):
    """Revalidate a reviewed archive against cached bytes, without recapturing."""
    limits = limits or WorkspaceLimits()
    manifest = _manifest(snapshot, limits)
    binding = snapshot.archive_binding
    if (not isinstance(binding, WorkspaceArchiveBinding)
            or not isinstance(binding.root_identity, FileIdentity)
            or not isinstance(binding.data_identity, FileIdentity)
            or binding.manifest_sha256 != hashlib.sha256(snapshot.manifest).hexdigest()
            or not isinstance(binding.files, tuple)):
        _fail("archive_binding_invalid")
    expected_paths = ["manifest.json", *(path for path, _ in _archive_files(manifest))]
    try:
        if (list(dict(binding.files)) != expected_paths or len(binding.files) != len(expected_paths)
                or any(not isinstance(value, tuple) or len(value) != 8
                       or any(type(item) is not int for item in value)
                       for _, value in binding.files)):
            _fail("archive_binding_invalid")
    except (TypeError, ValueError):
        _fail("archive_binding_invalid")
    root_path = _absolute(binding.root)
    for part in root_path.parts[1:]:
        _component(part)
    owner, work = WorkspaceRecoveryOwner(), _Work(limits, checkpoint)
    fingerprints = dict(binding.files)
    try:
        work.step("workspace_archive_revalidate_begin")
        root = owner.pin(root_path, expected=binding.root_identity)
        data = owner.pin(root.path / "data", expected=binding.data_identity)
        raw, _ = _archive_manifest_file(owner, root, work, limits, fingerprints["manifest.json"])
        if raw != snapshot.manifest:
            _fail("archive_review_changed")
        _archive_inventory(owner, root, data, manifest, checkpoint)
        for archive_path, record in _archive_files(manifest):
            value, _ = _source_file(owner, root, archive_path, {"data": data.identity},
                                    work, limits, fingerprints[archive_path])
            if (len(value) != record["size"]
                    or hashlib.sha256(value).hexdigest() != record["sha256"]):
                _fail("archive_review_changed")
        _check_archive_metadata(owner, root, data, manifest, binding, checkpoint)
    except BaseException as error:
        _settle(owner, error)
    _settle(owner)
