"""Own project registry and reviewed, bounded local Git operations.

Registration is an explicit host selection, never repository discovery. Reviews
are CAS snapshots, not grants. The embedding host supplies its existing single
authority as ``authorize()``; it is called immediately before every effect.
Temporary projects, reviews and bindings never enter the private JSON document.
No operation uses a shell, remote, repository hook, filter or owner credential.
"""

from __future__ import annotations

import asyncio
import base64
import difflib
import hashlib
import json
import os
import re
import signal
import stat
import time
from collections.abc import Callable
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from pathlib import Path
from uuid import uuid4

from .private_json import PrivateJsonDocument
from .workspace import _parts

_DIRECTORY = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
_FILE = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK
_OID = re.compile(r"[a-f0-9]{40}(?:[a-f0-9]{24})?")

@dataclass(frozen=True)
class ProjectLimits:
    max_files: int = 4096
    max_file_bytes: int = 4194304
    max_total_bytes: int = 33554432
    max_depth: int = 32
    max_entries: int = 16384

    def __post_init__(self):
        for name, ceiling in (("max_files", 4096), ("max_file_bytes", 4194304),
                              ("max_total_bytes", 33554432), ("max_depth", 32),
                              ("max_entries", 16384)):
            value = getattr(self, name)
            if type(value) is not int or not 1 <= value <= ceiling:
                raise ProjectError("project_limits_invalid")


class ProjectError(ValueError):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def _text(value, limit=256):
    try:
        if (not isinstance(value, str) or not value.strip() or "\0" in value
                or len(value.encode()) > limit):
            raise ProjectError("project_argument_invalid")
    except UnicodeError:
        raise ProjectError("project_argument_invalid") from None
    return value


def _id(value):
    if not isinstance(value, str) or not re.fullmatch(r"[a-f0-9]{32}", value):
        raise ProjectError("project_identity_invalid")
    return value


def _json(value):
    return json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":"),
                      allow_nan=False)


def _hash(value):
    return hashlib.sha256(_json(value).encode()).hexdigest()


@contextmanager
def _directory(path, expected=None):
    """Walk every component without symlinks, retaining the final descriptor."""
    path = Path(path)
    if not path.is_absolute() or ".." in path.parts or len(str(path).encode()) > 4096:
        raise ProjectError("project_path_invalid")
    descriptor = os.open(path.anchor, _DIRECTORY)
    try:
        for component in path.parts[1:]:
            child = os.open(component, _DIRECTORY, dir_fd=descriptor)
            os.close(descriptor)
            descriptor = child
        info = os.fstat(descriptor)
        identity = (info.st_dev, info.st_ino)
        if ((expected is not None and tuple(expected) != identity)
                or info.st_uid != os.getuid() or info.st_mode & 0o022):
            raise ProjectError("project_directory_changed")
        yield descriptor, identity
    finally:
        os.close(descriptor)


def _read_at(directory, name, limit=4194304):
    descriptor = os.open(name, _FILE, dir_fd=directory)
    try:
        before = os.fstat(descriptor)
        if (not stat.S_ISREG(before.st_mode) or before.st_nlink != 1
                or before.st_size > limit or before.st_uid != os.getuid()):
            raise ProjectError("project_file_unsafe")
        raw = bytearray()
        while chunk := os.read(descriptor, min(8192, limit + 1 - len(raw))):
            raw.extend(chunk)
            if len(raw) > limit:
                raise ProjectError("project_file_limit")
        after = os.fstat(descriptor)
        linked = os.stat(name, dir_fd=directory, follow_symlinks=False)
        def fingerprint(row):
            return (row.st_dev, row.st_ino, row.st_size, row.st_mtime_ns,
                    row.st_ctime_ns, row.st_nlink)
        if fingerprint(before) != fingerprint(after) or fingerprint(after) != fingerprint(linked):
            raise ProjectError("project_file_changed")
        return bytes(raw), stat.S_IMODE(after.st_mode)
    finally:
        os.close(descriptor)


@dataclass(frozen=True)
class RepositoryPin:
    root: str
    root_identity: tuple[int, int]
    git_dir: str
    git_identity: tuple[int, int]
    common_dir: str
    common_identity: tuple[int, int]

    @classmethod
    def capture(cls, path: Path):
        root = Path(path)
        with _directory(root) as (descriptor, root_identity):
            info = os.stat(".git", dir_fd=descriptor, follow_symlinks=False)
            if stat.S_ISDIR(info.st_mode):
                git_dir = root / ".git"
            elif stat.S_ISREG(info.st_mode):
                raw, _ = _read_at(descriptor, ".git", 4096)
                try:
                    text = raw.decode("utf-8").strip()
                except UnicodeError:
                    raise ProjectError("project_git_pointer_invalid") from None
                if not text.startswith("gitdir: ") or "\n" in text:
                    raise ProjectError("project_git_pointer_invalid")
                target = Path(text[8:])
                # abspath normalises '..' lexically; physical traversal still
                # rejects every symlink in _directory, including metadata.
                git_dir = Path(os.path.abspath(root / target))
            else:
                raise ProjectError("project_git_pointer_invalid")
        with _directory(git_dir) as (descriptor, git_identity):
            try:
                raw, _ = _read_at(descriptor, "commondir", 4096)
            except FileNotFoundError:
                common_dir = git_dir
            else:
                try:
                    relative = raw.decode("utf-8").strip()
                except UnicodeError:
                    raise ProjectError("project_git_pointer_invalid") from None
                if not relative or "\n" in relative or "\0" in relative:
                    raise ProjectError("project_git_pointer_invalid")
                common_dir = Path(os.path.abspath(git_dir / relative))
        with _directory(common_dir) as (_, common_identity):
            return cls(str(root), root_identity, str(git_dir), git_identity,
                       str(common_dir), common_identity)

    def assert_current(self):
        if RepositoryPin.capture(Path(self.root)) != self:
            raise ProjectError("project_repository_changed")

    @classmethod
    def from_json(cls, value):
        if not isinstance(value, dict) or set(value) != set(cls.__dataclass_fields__):
            raise ProjectError("project_registry_invalid")
        value = dict(value)
        for key in ("root_identity", "git_identity", "common_identity"):
            identity = value[key]
            if (not isinstance(identity, (list, tuple)) or len(identity) != 2
                    or any(type(item) is not int or item < 0 for item in identity)):
                raise ProjectError("project_registry_invalid")
            value[key] = tuple(identity)
        for key in ("root", "git_dir", "common_dir"):
            _text(value[key], 4096)
            if not Path(value[key]).is_absolute() or ".." in Path(value[key]).parts:
                raise ProjectError("project_registry_invalid")
        return cls(**value)


def _initial():
    return {"version": 1, "projects": {}, "bindings": {}, "operations": {}, "snapshots": {}}


def _validate(value):
    if (not isinstance(value, dict) or set(value) != set(_initial())
            or type(value["version"]) is not int or value["version"] != 1):
        raise ProjectError("project_registry_invalid")
    for key, limit in (("projects", 128), ("bindings", 1024), ("operations", 256),
                       ("snapshots", 32)):
        if not isinstance(value[key], dict) or len(value[key]) > limit:
            raise ProjectError("project_registry_limit")
    for identity, row in value["projects"].items():
        _id(identity)
        if (not isinstance(row, dict) or set(row) != {
                "name", "pin", "revision", "temporary", "owned", "archived", "origin_project_id"}
                or type(row["revision"]) is not int or row["revision"] < 1
                or any(type(row[key]) is not bool for key in ("temporary", "owned", "archived"))):
            raise ProjectError("project_registry_invalid")
        _text(row["name"])
        RepositoryPin.from_json(row["pin"])
        if row["origin_project_id"] is not None:
            _id(row["origin_project_id"])
    for session_id, binding in value["bindings"].items():
        _text(session_id, 128)
        if (not isinstance(binding, dict) or set(binding) != {"project_id", "revision"}
                or binding["project_id"] not in value["projects"]
                or type(binding["revision"]) is not int or binding["revision"] < 1):
            raise ProjectError("project_registry_invalid")
    for operation_id, row in value["operations"].items():
        _id(operation_id)
        if (not isinstance(row, dict) or set(row) != {"digest", "kind", "phase", "receipt"}
                or not re.fullmatch(r"[a-f0-9]{64}", row["digest"])
                or row["kind"] not in {"worktree.create", "worktree.restore", "worktree.archive",
                                        "file.restore"}
                or row["phase"] not in {"dispatched", "completed", "unknown"}
                or not isinstance(row["receipt"], dict)):
            raise ProjectError("project_registry_invalid")
    for snapshot_id, row in value["snapshots"].items():
        _id(snapshot_id)
        if (not isinstance(row, dict) or set(row) != {"project_id", "files", "digest", "head"}
                or row["project_id"] not in value["projects"]
                or not isinstance(row["files"], dict) or len(row["files"]) > 4096
                or _hash(row["files"]) != row["digest"] or not _OID.fullmatch(row["head"])):
            raise ProjectError("project_registry_invalid")
        size = 0
        for path, item in row["files"].items():
            _parts(path)
            if (not isinstance(item, dict) or set(item) != {"bytes", "mode", "sha256"}
                    or item["mode"] not in {0o600, 0o700}
                    or type(item["bytes"]) is not int or not 0 <= item["bytes"] <= 4194304
                    or not isinstance(item["sha256"], str)
                    or not re.fullmatch(r"[a-f0-9]{64}", item["sha256"])):
                raise ProjectError("project_registry_invalid")
            size += item["bytes"]
            if size > 33554432:
                raise ProjectError("project_registry_limit")


class ProjectsStore:
    """Small private registry; ``directory=None`` is entirely process-local."""

    def __init__(self, directory: Path | None = None, *, limits=None):
        self.limits = limits or ProjectLimits()
        if not isinstance(self.limits, ProjectLimits):
            raise ProjectError("project_limits_invalid")
        self.document = None if directory is None else PrivateJsonDocument(
            directory, "projects.json", initial=_initial, validate=_validate, max_bytes=4194304)
        self._memory = _initial()
        self._temporary_bindings = {}
        self._memory_blobs = {}
        self._blob_identity = None

    def _blob_directory(self, authorize=None):
        path = self.document.directory / "project_blobs"
        with _directory(self.document.directory, self.document.identity) as (parent, _):
            try:
                os.stat("project_blobs", dir_fd=parent, follow_symlinks=False)
            except FileNotFoundError:
                if authorize is None:
                    raise ProjectError("project_snapshot_blob_missing") from None
                authorize()
                os.mkdir("project_blobs", 0o700, dir_fd=parent)
                os.fsync(parent)
        with _directory(path, self._blob_identity) as (_, identity):
            self._blob_identity = identity
        return path

    def save_snapshot_files(self, files, *, temporary, authorize):
        """Publish content-addressed private blobs; JSON contains metadata only."""
        result = {}
        for path, item in files.items():
            raw = base64.b64decode(item["base64"], validate=True)
            digest = hashlib.sha256(raw).hexdigest()
            if digest != item["sha256"] or len(raw) > self.limits.max_file_bytes:
                raise ProjectError("project_snapshot_invalid")
            if temporary or self.document is None:
                authorize()
                self._memory_blobs[digest] = raw
            else:
                root = self._blob_directory(authorize)
                with _directory(root, self._blob_identity) as (parent, _):
                    try:
                        existing, _ = _read_at(parent, digest, self.limits.max_file_bytes)
                    except FileNotFoundError:
                        name = ".jarvis-blob-" + uuid4().hex
                        authorize()
                        descriptor = os.open(
                            name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                            0o600, dir_fd=parent)
                        try:
                            with os.fdopen(descriptor, "wb", closefd=False) as output:
                                output.write(raw)
                                output.flush()
                                os.fsync(descriptor)
                            authorize()
                            os.link(name, digest, src_dir_fd=parent, dst_dir_fd=parent,
                                    follow_symlinks=False)
                            authorize()
                            os.unlink(name, dir_fd=parent)
                            os.fsync(parent)
                            existing, _ = _read_at(parent, digest, self.limits.max_file_bytes)
                        finally:
                            os.close(descriptor)
                    if existing != raw:
                        raise ProjectError("project_snapshot_blob_changed")
            result[path] = {"sha256": digest, "mode": item["mode"], "bytes": len(raw)}
        return result

    def load_snapshot_files(self, files, *, temporary):
        result, size = {}, 0
        if len(files) > self.limits.max_files:
            raise ProjectError("project_snapshot_limit")
        for path, item in files.items():
            _parts(path)
            if temporary or self.document is None:
                raw = self._memory_blobs.get(item["sha256"])
                if raw is None:
                    raise ProjectError("project_snapshot_blob_missing")
            else:
                root = self._blob_directory()
                with _directory(root, self._blob_identity) as (parent, _):
                    raw, _ = _read_at(parent, item["sha256"], self.limits.max_file_bytes)
            size += len(raw)
            if size > self.limits.max_total_bytes:
                raise ProjectError("project_snapshot_limit")
            if len(raw) != item["bytes"] or hashlib.sha256(raw).hexdigest() != item["sha256"]:
                raise ProjectError("project_snapshot_blob_changed")
            result[path] = {"sha256": item["sha256"], "mode": item["mode"],
                            "base64": base64.b64encode(raw).decode()}
        return result

    def _read(self, temporary=False):
        return json.loads(_json(self._memory if temporary or self.document is None
                                else self.document.read()))

    def _update(self, callback, temporary=False):
        if temporary or self.document is None:
            value = self._read(True)
            callback(value)
            _validate(value)
            self._memory = value
            return self._read(True)

        def change(value):
            callback(value)
            _validate(value)
            return value
        return self.document.update(change)

    def list(self):
        durable = {} if self.document is None else self._read()["projects"]
        rows = {**durable, **self._read(True)["projects"]}
        return tuple({"project_id": key, **rows[key]} for key in sorted(rows))

    def get(self, project_id):
        _id(project_id)
        for row in self.list():
            if row["project_id"] == project_id:
                return row
        raise ProjectError("project_not_found")

    def register(self, name, pin: RepositoryPin, *, temporary=False, owned=False,
                 project_id=None, origin_project_id=None):
        _text(name)
        if type(temporary) is not bool or type(owned) is not bool:
            raise ProjectError("project_argument_invalid")
        identity = uuid4().hex if project_id is None else _id(project_id)
        if origin_project_id is not None:
            self.get(origin_project_id)
        if any(row["pin"]["root"] == pin.root for row in self.list()):
            raise ProjectError("project_already_registered")
        pin.assert_current()

        def change(value):
            if identity in value["projects"]:
                raise ProjectError("project_already_registered")
            value["projects"][identity] = {
                "name": name, "pin": asdict(pin), "revision": 1,
                "temporary": temporary, "owned": owned, "archived": False,
                "origin_project_id": origin_project_id}
        self._update(change, temporary)
        return self.get(identity)

    def bind_session(self, session_id, project_id, *, expected_revision=0, temporary=False):
        _text(session_id, 128)
        row = self.get(project_id)
        if (type(temporary) is not bool or type(expected_revision) is not int
                or expected_revision < 0 or (row["temporary"] and not temporary)):
            raise ProjectError("project_binding_invalid")
        if temporary:
            old = self._temporary_bindings.get(session_id)
            if (old["revision"] if old else 0) != expected_revision:
                raise ProjectError("project_revision_conflict")
            if len(self._temporary_bindings) >= 1024 and old is None:
                raise ProjectError("project_registry_limit")
            result = {"project_id": project_id, "revision": expected_revision + 1}
            self._temporary_bindings[session_id] = result
            return dict(result)

        def change(value):
            old = value["bindings"].get(session_id)
            if (old["revision"] if old else 0) != expected_revision:
                raise ProjectError("project_revision_conflict")
            value["bindings"][session_id] = {
                "project_id": project_id, "revision": expected_revision + 1}
        result = self._update(change, temporary)
        return result["bindings"][session_id]

    def binding(self, session_id, *, temporary=False):
        _text(session_id, 128)
        if temporary:
            result = self._temporary_bindings.get(session_id)
            return dict(result) if result else None
        return self._read(temporary)["bindings"].get(session_id)

    def operation(self, operation_id, *, temporary=False):
        return self._read(temporary)["operations"].get(_id(operation_id))

    def operations(self, project_id):
        project = self.get(project_id)
        return tuple({"operation_id": identity, **row} for identity, row in
                     self._read(project["temporary"])["operations"].items()
                     if row["receipt"].get("project_id") == project_id)


@dataclass(frozen=True)
class GitOutput:
    returncode: int
    stdout: bytes
    stderr: bytes


class BoundedGitRunner:
    """Explicit system Git, descriptor pins, fixed environment, no PATH search.

    This limits the adapter; it is not an OS sandbox against hostile same-UID
    processes. The host must retain its normal lifecycle/cancellation lane.
    """

    def __init__(self, executable=Path("/usr/bin/git"), *, timeout=10, max_output=4259840,
                 child_custody=None):
        self.executable = Path(executable)
        if (not self.executable.is_absolute() or type(timeout) not in {float, int}
                or not 0 < timeout <= 30 or type(max_output) is not int
                or not 1024 <= max_output <= 16777216):
            raise ProjectError("project_runner_invalid")
        self.timeout, self.max_output = timeout, max_output
        try:
            info = self.executable.stat()
        except OSError:
            raise ProjectError("project_git_unavailable") from None
        if not stat.S_ISREG(info.st_mode) or not os.access(self.executable, os.X_OK):
            raise ProjectError("project_git_unavailable")
        self.identity = (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns)
        self.child_custody = child_custody or (lambda: ())
        self._pending_processes, self._processes, self._spawns = set(), set(), set()
        self._metadata_tree_cache = {}

    def _safe_metadata(self, pin):
        """Reject config includes and link/special-file metadata before Git reads.

        Directory identities/times cache an observed link-free object/ref tree.
        Changed directories force another finite scan. This is filesystem CAS,
        not protection from malicious same-UID code restoring metadata times.
        """
        for path, identity in ((pin.git_dir, pin.git_identity),
                               (pin.common_dir, pin.common_identity)):
            with _directory(path, identity) as (parent, _):
                for name in ("HEAD", "config", "config.worktree", "packed-refs", "index"):
                    try:
                        raw, _ = _read_at(parent, name, 16777216 if name == "index" else 262144)
                    except FileNotFoundError:
                        continue
                    if name in {"config", "config.worktree"} and re.search(
                            rb"(?im)^[ \t]*\[[ \t]*include(?:if)?(?:[\s\]\"])", raw):
                        raise ProjectError("project_git_external_config_forbidden")
        key = (pin.common_dir, pin.common_identity)
        cached = self._metadata_tree_cache.get(key)
        if cached:
            valid = True
            for path, expected in cached:
                try:
                    info = Path(path).lstat()
                except OSError:
                    valid = False
                    break
                if (info.st_dev, info.st_ino, info.st_mtime_ns, info.st_ctime_ns) != expected:
                    valid = False
                    break
            if valid:
                return
        directories, entries = [], [0]

        def walk(path, depth):
            if depth > 64:
                raise ProjectError("project_git_metadata_limit")
            with _directory(path) as (parent, _):
                before = os.fstat(parent)
                with os.scandir(parent) as iterator:
                    for entry in iterator:
                        entries[0] += 1
                        if entries[0] > 65536:
                            raise ProjectError("project_git_metadata_limit")
                        if entry.name in {"alternates", "http-alternates"}:
                            raise ProjectError("project_git_external_objects_forbidden")
                        info = os.stat(entry.name, dir_fd=parent, follow_symlinks=False)
                        if stat.S_ISDIR(info.st_mode):
                            walk(path / entry.name, depth + 1)
                        elif (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1
                              or info.st_uid != os.getuid()):
                            raise ProjectError("project_git_metadata_unsafe")
                after = os.fstat(parent)
                if (before.st_mtime_ns, before.st_ctime_ns) != (
                        after.st_mtime_ns, after.st_ctime_ns):
                    raise ProjectError("project_git_metadata_changed")
                directories.append((str(path), (after.st_dev, after.st_ino,
                                                after.st_mtime_ns, after.st_ctime_ns)))
        for name in ("objects", "refs"):
            path = Path(pin.common_dir) / name
            try:
                path.lstat()
            except FileNotFoundError:
                continue
            walk(path, 0)
        self._metadata_tree_cache[key] = directories

    @property
    def pending_cleanup(self):
        self._pending_processes = {process for process in self._pending_processes
                                   if process.returncode is None}
        return bool(self._pending_processes or self._spawns)

    @property
    def active_children(self):
        return sum(process.returncode is None for process in self._processes) + len(self._spawns)

    async def aclose(self):
        """Terminate only owned children, retaining custody if reaping times out."""
        for process in tuple(self._processes | self._pending_processes):
            if process.returncode is None:
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                try:
                    async with asyncio.timeout(2):
                        await process.wait()
                except TimeoutError:
                    self._pending_processes.add(process)
            if process.returncode is not None:
                self._processes.discard(process)
                self._pending_processes.discard(process)

    def _late_spawn(self, task, launch_fds):
        self._spawns.discard(task)
        for descriptor in launch_fds:
            os.close(descriptor)
        if task.cancelled():
            return
        try:
            process = task.result()
        except Exception:
            return
        self._processes.add(process)
        if process.returncode is None:
            self._pending_processes.add(process)
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass

    async def run(self, pin: RepositoryPin, arguments: tuple[str, ...], *, authorize=None,
                  cancellation=None):
        if self.pending_cleanup:
            raise ProjectError("project_git_cleanup_pending")
        if (not isinstance(arguments, tuple) or not arguments
                or any(not isinstance(arg, str) or "\0" in arg or len(arg) > 4096
                       for arg in arguments)):
            raise ProjectError("project_git_arguments_invalid")
        # Only the backend constructs argv. No public arbitrary Git endpoint.
        pin.assert_current()
        self._safe_metadata(pin)
        with _directory(pin.root, pin.root_identity) as (root, _), \
                _directory(pin.git_dir, pin.git_identity) as (git_dir, _), \
                _directory(pin.common_dir, pin.common_identity) as (common, _):
            info = self.executable.stat()
            if (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns) != self.identity:
                raise ProjectError("project_git_executable_changed")
            env = {"PATH": "/usr/bin:/bin", "LC_ALL": "C", "GIT_CONFIG_NOSYSTEM": "1",
                   "GIT_CONFIG_GLOBAL": "/dev/null", "GIT_TERMINAL_PROMPT": "0",
                   "GIT_OPTIONAL_LOCKS": "0", "GIT_COMMON_DIR": f"/dev/fd/{common}"}
            if arguments[:2] in {("worktree", "add"), ("worktree", "remove")}:
                # Git writes persistent gitdir pointers when adding worktrees;
                # never publish a descriptor alias into those pointers.
                env["GIT_COMMON_DIR"] = pin.common_dir
            argv = (str(self.executable), "--no-pager", "--no-optional-locks",
                    "-c", "core.hooksPath=/dev/null", "-c", "core.fsmonitor=false",
                    "-c", "core.excludesFile=/dev/null", "-c", "diff.external=",
                    "-c", "core.attributesFile=/dev/null", "-C", f"/dev/fd/{root}",
                    f"--git-dir=/dev/fd/{git_dir}", f"--work-tree=/dev/fd/{root}", *arguments)
            if cancellation is not None and cancellation.is_set():
                raise ProjectError("project_cancelled")
            if authorize is not None:
                authorize()
                pin.assert_current()
            custody = tuple(self.child_custody())
            if any(type(descriptor) is not int or descriptor < 0 for descriptor in custody):
                raise ProjectError("project_child_custody_invalid")
            inherited = tuple(dict.fromkeys((root, git_dir, common, *custody)))
            launch_fds = []
            try:
                for descriptor in inherited:
                    launch_fds.append(os.dup(descriptor))
            except BaseException:
                for descriptor in launch_fds:
                    os.close(descriptor)
                raise
            aliases = {f"/dev/fd/{original}": f"/dev/fd/{duplicate}"
                       for original, duplicate in zip(inherited, launch_fds, strict=True)}
            argv = tuple(aliases.get(arg, arg) if "=" not in arg else
                         arg.partition("=")[0] + "=" + aliases.get(
                             arg.partition("=")[2], arg.partition("=")[2]) for arg in argv)
            env["GIT_COMMON_DIR"] = aliases.get(env["GIT_COMMON_DIR"], env["GIT_COMMON_DIR"])
            async def launch():
                if cancellation is not None and cancellation.is_set():
                    raise ProjectError("project_cancelled")
                if authorize is not None:
                    authorize()
                pin.assert_current()
                return await asyncio.create_subprocess_exec(
                    *argv, env=env, stdin=asyncio.subprocess.DEVNULL,
                    stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
                    pass_fds=tuple(launch_fds), start_new_session=True)

            creation = asyncio.create_task(launch())
            try:
                process = await asyncio.shield(creation)
            except asyncio.CancelledError:
                self._spawns.add(creation)
                creation.add_done_callback(lambda task: self._late_spawn(task, launch_fds))
                raise
            except BaseException:
                for descriptor in launch_fds:
                    os.close(descriptor)
                raise
            else:
                for descriptor in launch_fds:
                    os.close(descriptor)
            self._processes.add(process)
            size = [0]

            async def consume(stream):
                parts = []
                while part := await stream.read(8192):
                    size[0] += len(part)
                    if size[0] > self.max_output:
                        raise ProjectError("project_git_output_limit")
                    parts.append(part)
                return b"".join(parts)

            async def collect():
                out, err = await asyncio.gather(consume(process.stdout), consume(process.stderr))
                await process.wait()
                return GitOutput(process.returncode, out, err)

            collection = asyncio.create_task(collect())
            cancellation_task = (asyncio.create_task(cancellation.wait())
                                 if cancellation is not None else None)
            try:
                waiting = {collection} | ({cancellation_task} if cancellation_task else set())
                done, _ = await asyncio.wait(waiting, timeout=self.timeout,
                                             return_when=asyncio.FIRST_COMPLETED)
                if not done:
                    raise ProjectError("project_git_timeout")
                if cancellation_task in done:
                    raise ProjectError("project_cancelled")
                return await collection
            finally:
                if process.returncode is None:
                    try:
                        os.killpg(process.pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
                try:
                    async with asyncio.timeout(2):
                        await process.wait()
                except TimeoutError:
                    self._pending_processes.add(process)
                if process.returncode is not None:
                    self._processes.discard(process)
                for task in (collection, cancellation_task):
                    if task is not None and not task.done():
                        task.cancel()
                await asyncio.gather(*(task for task in (collection, cancellation_task)
                                       if task is not None), return_exceptions=True)


class ProjectsBackend:
    """Callable host API. Every apply requires exact review, session and run.

    review(kind, project_id, arguments, session_id=..., run_id=...) freezes CAS;
    apply(review_id, digest, session_id=..., run_id=..., authorize=callback)
    consumes it once. ``authorize`` must revalidate the host's existing committed
    authority for its exact canonical tool arguments, never return a new grant.
    """

    def __init__(self, store: ProjectsStore, worktrees_root: Path, *, runner=None,
                 clock=time.monotonic):
        self.store, self.clock, self.limits = store, clock, store.limits
        self.runner = runner or BoundedGitRunner(
            max_output=max(1024, self.limits.max_file_bytes + 65536))
        self.root = Path(worktrees_root)
        with _directory(self.root) as (_, identity):
            self.root_identity = identity
        self.reviews = {}
        self._lock = asyncio.Lock()

    @property
    def blockers(self):
        rows = [*self.store._read()["operations"].values(),
                *self.store._read(True)["operations"].values()]
        result = ["project_effect_unconfirmed"] if any(
            row["phase"] != "completed" for row in rows) else []
        if getattr(self.runner, "pending_cleanup", False):
            result.append("project_git_cleanup_pending")
        elif getattr(self.runner, "active_children", 0):
            result.append("project_git_active")
        return tuple(result)

    async def aclose(self):
        if callable(getattr(self.runner, "aclose", None)):
            await self.runner.aclose()

    async def _git(self, pin, *arguments, **options):
        result = await self.runner.run(pin, tuple(arguments), **options)
        if result.returncode:
            # Git output can contain local paths/secrets. Stable public errors
            # carry no raw stderr; explicit diff/log responses remain peer data.
            raise ProjectError("project_git_failed")
        return result.stdout

    async def register(self, path, name, *, temporary=False):
        pin = RepositoryPin.capture(Path(path))
        await self._git(pin, "rev-parse", "--is-inside-work-tree")
        return self.store.register(name, pin, temporary=temporary)

    async def inspect(self, project_id, *, offset=0, limit=50):
        if (type(offset) is not int or not 0 <= offset <= self.limits.max_files * 2
                or type(limit) is not int or not 1 <= limit <= 50):
            raise ProjectError("project_page_invalid")
        row = self.store.get(project_id)
        pin = RepositoryPin.from_json(row["pin"])
        head = (await self._git(pin, "rev-parse", "--verify", "HEAD")).decode().strip()
        if not _OID.fullmatch(head):
            raise ProjectError("project_head_invalid")
        baseline = await self._tree(pin, head)
        index = await self._git(pin, "ls-files", "--stage", "-z")
        others = await self._git(pin, "ls-files", "--others", "--exclude-standard", "-z")
        indexed = {}
        try:
            for entry in index.split(b"\0"):
                if not entry:
                    continue
                metadata, path = entry.split(b"\t", 1)
                mode, oid, stage = metadata.decode("ascii").split(" ")
                path = path.decode("utf-8")
                _parts(path)
                if stage != "0" or mode not in {"100644", "100755"} or not _OID.fullmatch(oid):
                    raise ProjectError("project_index_unsupported")
                indexed[path] = (mode, oid)
            untracked = {path.decode("utf-8") for path in others.split(b"\0") if path}
        except (ValueError, UnicodeError):
            raise ProjectError("project_index_unsupported") from None
        if len(indexed) + len(untracked) > self.limits.max_files * 2:
            raise ProjectError("project_snapshot_limit")
        changes, disk, patches, excluded, observed_bytes = [], {}, [], 0, 0
        diff_truncated, omitted_large = False, 0
        for path in sorted(set(baseline) | set(indexed) | untracked):
            try:
                _parts(path)
            except ValueError:
                if path in untracked:
                    excluded += 1
                    continue
                raise
            try:
                with self._parent(pin, path) as (parent, name):
                    raw, mode = _read_at(parent, name, self.limits.max_file_bytes)
                observed_bytes += len(raw)
                if observed_bytes > self.limits.max_total_bytes:
                    raise ProjectError("project_snapshot_limit")
                item = {"sha256": hashlib.sha256(raw).hexdigest(),
                        "mode": "100755" if mode & 0o111 else "100644"}
            except FileNotFoundError:
                raw, item = b"", None
            disk[path] = item
            before = baseline.get(path)
            before_raw = base64.b64decode(before["base64"]) if before else b""
            before_mode = "100755" if before and before["mode"] == 0o700 else "100644"
            hash_function = hashlib.sha1 if len(head) == 40 else hashlib.sha256
            before_oid = (hash_function(b"blob " + str(len(before_raw)).encode() + b"\0"
                                       + before_raw).hexdigest() if before else None)
            staged = indexed.get(path) != ((before_mode, before_oid) if before else None)
            changed = (item is None or before is None or item["sha256"] != before["sha256"]
                       or item["mode"] != before_mode)
            if staged or changed:
                changes.append(("??" if path in untracked else
                                ("S" if staged else " ") + ("M" if changed else " "))
                               + " " + path)
                if not offset <= len(changes) - 1 < offset + limit:
                    continue
                if len(raw) + len(before_raw) > 262144:
                    patches.append("Diff extenso omitido; hashes disponibles: " + path + "\n")
                    omitted_large += 1
                    continue
                try:
                    old, new = before_raw.decode("utf-8"), raw.decode("utf-8")
                    if "\0" in old or "\0" in new:
                        raise UnicodeError
                    patches.append("".join(difflib.unified_diff(
                        old.splitlines(keepends=True), new.splitlines(keepends=True),
                        fromfile="a/" + path, tofile="b/" + path)))
                except UnicodeError:
                    patches.append("Contenido binario: " + path + "\n")
        diff = "".join(patches).encode()
        pin.assert_current()
        return {"project": row, "head": head,
                "status": "\n".join(changes[offset:offset + limit]),
                "has_changes": bool(changes), "change_count": len(changes),
                "offset": offset, "limit": limit,
                "next_offset": offset + limit if offset + limit < len(changes) else None,
                "diff": diff[:65536].decode("utf-8", "replace"),
                "diff_truncated": diff_truncated or len(diff) > 65536,
                "diff_omitted_large_files": omitted_large, "excluded_private_paths": excluded,
                "state_digest": _hash([row, head, hashlib.sha256(index).hexdigest(), disk,
                                       hashlib.sha256(others).hexdigest()]),
                "content_trust": "repository_data", "comparison": "literal_bytes_no_filters"}

    async def log(self, project_id, *, limit=20):
        if type(limit) is not int or not 1 <= limit <= 50:
            raise ProjectError("project_argument_invalid")
        row = self.store.get(project_id)
        raw = await self._git(RepositoryPin.from_json(row["pin"]), "log", f"-{limit}",
                              "--format=%H%x00%aI%x00%s", "--no-show-signature", "HEAD", "--")
        return {"log": raw.decode("utf-8", "replace"), "content_trust": "repository_data"}

    async def worktrees(self, project_id):
        row = self.store.get(project_id)
        raw = await self._git(RepositoryPin.from_json(row["pin"]), "worktree", "list",
                              "--porcelain", "-z")
        return {"worktrees": raw.decode("utf-8", "replace"), "content_trust": "repository_data"}

    def _files(self, pin):
        """Finite recovery copy; hidden/private paths fail instead of being read."""
        files, size, entries = {}, [0], [0]

        def walk(descriptor, prefix=""):
            if prefix.count("/") > self.limits.max_depth:
                raise ProjectError("project_snapshot_limit")
            with os.scandir(descriptor) as iterator:
                names = []
                for entry in iterator:
                    entries[0] += 1
                    if entries[0] > self.limits.max_entries:
                        raise ProjectError("project_snapshot_limit")
                    names.append(entry.name)
            for name in sorted(names):
                if not prefix and name == ".git":
                    continue
                path = prefix + name
                _parts(path)
                info = os.stat(name, dir_fd=descriptor, follow_symlinks=False)
                if stat.S_ISDIR(info.st_mode):
                    child = os.open(name, _DIRECTORY, dir_fd=descriptor)
                    try:
                        walk(child, path + "/")
                    finally:
                        os.close(child)
                else:
                    raw, mode = _read_at(descriptor, name, self.limits.max_file_bytes)
                    size[0] += len(raw)
                    if len(files) >= self.limits.max_files or size[0] > self.limits.max_total_bytes:
                        raise ProjectError("project_snapshot_limit")
                    files[path] = {"base64": base64.b64encode(raw).decode(),
                                   "mode": 0o700 if mode & 0o111 else 0o600,
                                   "sha256": hashlib.sha256(raw).hexdigest()}
        with _directory(pin.root, pin.root_identity) as (descriptor, _):
            walk(descriptor)
        pin.assert_current()
        return files

    def _metadata(self, pin):
        values = {}
        for label, parent, identity, name in (
                ("head", pin.git_dir, pin.git_identity, "HEAD"),
                ("index", pin.git_dir, pin.git_identity, "index"),
                ("config", pin.common_dir, pin.common_identity, "config"),
                ("packed_refs", pin.common_dir, pin.common_identity, "packed-refs")):
            with _directory(parent, identity) as (descriptor, _):
                try:
                    raw, _ = _read_at(
                        descriptor, name, 16777216 if label == "index" else 262144)
                except FileNotFoundError:
                    raw = None
                values[label] = None if raw is None else hashlib.sha256(raw).hexdigest()
                if label == "head" and raw is not None and raw.startswith(b"ref: "):
                    try:
                        ref = raw[5:].decode("ascii").strip()
                        if not ref.startswith("refs/"):
                            raise ValueError
                        parts = _parts(ref)
                    except (ValueError, UnicodeError):
                        raise ProjectError("project_head_invalid") from None
                    try:
                        with _directory(Path(pin.common_dir).joinpath(*parts[:-1])) as (refs, _):
                            target, _ = _read_at(refs, parts[-1], 1024)
                    except FileNotFoundError:
                        target = None
                    values["head_ref"] = (None if target is None else
                                          hashlib.sha256(target).hexdigest())
        pin.assert_current()
        return values

    async def review(self, kind, project_id, arguments, *, session_id, run_id):
        _text(session_id, 128)
        _text(run_id, 128)
        if (not isinstance(arguments, dict) or kind not in {
                "worktree.create", "worktree.restore", "worktree.archive", "file.restore"}):
            raise ProjectError("project_argument_invalid")
        args = json.loads(_json(arguments))
        state = await self.inspect(project_id)
        row = state["project"]
        for operation in self.store._read(row["temporary"])["operations"].values():
            if (operation["phase"] != "completed"
                    and operation["receipt"].get("project_id") == project_id):
                raise ProjectError("project_previous_effect_unconfirmed")
        if row["archived"] and kind != "file.restore":
            raise ProjectError("project_archived")
        pin = RepositoryPin.from_json(row["pin"])
        extra = {"metadata": self._metadata(pin)}
        if kind in {"worktree.create", "worktree.restore"}:
            required = {"name", "ref"} if kind == "worktree.create" else {"name", "snapshot_id"}
            if set(args) != required:
                raise ProjectError("project_argument_invalid")
            _text(args["name"])
            if kind == "worktree.restore":
                snapshot = self.store._read(row["temporary"])["snapshots"].get(
                    _id(args["snapshot_id"]))
                if (snapshot is None or self.store.get(snapshot["project_id"])[
                        "origin_project_id"] != project_id):
                    raise ProjectError("project_snapshot_invalid")
                extra["tree"] = self.store.load_snapshot_files(
                    snapshot["files"], temporary=row["temporary"])
                extra["ref"] = snapshot["head"]
            else:
                if not _OID.fullmatch(_text(args["ref"], 64)):
                    raise ProjectError("project_argument_invalid")
                extra["ref"] = args["ref"]
                extra["tree"] = await self._tree(pin, args["ref"])
            oid = (await self._git(pin, "rev-parse", "--verify", extra["ref"] + "^{commit}"))
            if oid.decode().strip() != extra["ref"]:
                raise ProjectError("project_ref_changed")
            extra["new_id"] = uuid4().hex
            with _directory(self.root, self.root_identity):
                if (self.root / extra["new_id"]).exists():
                    raise ProjectError("project_target_exists")
        elif kind == "worktree.archive":
            if args or not row["owned"]:
                raise ProjectError("project_archive_requires_owned_worktree")
            extra["files"] = self._files(pin)
            extra["snapshot_id"] = uuid4().hex
        else:
            if set(args) != {"snapshot_id", "path", "expected_sha256"}:
                raise ProjectError("project_argument_invalid")
            _parts(args["path"])
            snapshot = self.store._read(row["temporary"])["snapshots"].get(_id(args["snapshot_id"]))
            if (snapshot is None or snapshot["project_id"] != project_id
                    or args["path"] not in snapshot["files"]
                    or not re.fullmatch(r"[a-f0-9]{64}", args["expected_sha256"])):
                raise ProjectError("project_snapshot_invalid")
            extra["file"] = self.store.load_snapshot_files(
                {args["path"]: snapshot["files"][args["path"]]}, temporary=row["temporary"]
            )[args["path"]]
            self._assert_file(pin, args["path"], args["expected_sha256"])
            with self._parent(pin, args["path"]) as (parent, name):
                previous, mode = _read_at(parent, name, self.limits.max_file_bytes)
            if hashlib.sha256(previous).hexdigest() != args["expected_sha256"]:
                raise ProjectError("project_file_changed")
            extra["preimage"] = {"sha256": args["expected_sha256"],
                                  "mode": 0o700 if mode & 0o111 else 0o600,
                                  "base64": base64.b64encode(previous).decode()}
            extra["snapshot_id"] = uuid4().hex
        if len(self.reviews) >= 32:
            self.reviews = {key: item for key, item in self.reviews.items()
                            if item["expires"] > self.clock()}
        if len(self.reviews) >= 32:
            raise ProjectError("project_review_limit")
        identity = uuid4().hex
        review = {"review_id": identity, "kind": kind, "project_id": project_id,
                  "arguments": args, "state_digest": state["state_digest"],
                  "session_id": session_id, "run_id": run_id, "extra": extra,
                  "expires": self.clock() + 120}
        review["digest"] = _hash(review)
        self.reviews[identity] = review
        return {key: value for key, value in review.items() if key != "extra"}

    async def _tree(self, pin, oid):
        deadline = self.clock() + 30
        raw = await self._git(pin, "ls-tree", "-rz", "--full-tree", oid)
        files, size = {}, 0
        for entry in raw.split(b"\0"):
            if not entry:
                continue
            try:
                metadata, path = entry.split(b"\t", 1)
                mode, kind, object_id = metadata.decode("ascii").split(" ")
                path = path.decode("utf-8")
            except (UnicodeError, ValueError):
                raise ProjectError("project_tree_unsupported") from None
            _parts(path)
            if self.clock() >= deadline:
                raise ProjectError("project_git_timeout")
            if mode not in {"100644", "100755"} or kind != "blob" or not _OID.fullmatch(object_id):
                raise ProjectError("project_tree_unsupported")
            data = await self._git(pin, "cat-file", "blob", object_id)
            size += len(data)
            if (len(files) >= self.limits.max_files or size > self.limits.max_total_bytes
                    or len(data) > self.limits.max_file_bytes):
                raise ProjectError("project_snapshot_limit")
            files[path] = {"base64": base64.b64encode(data).decode(),
                           "mode": 0o700 if mode == "100755" else 0o600,
                           "sha256": hashlib.sha256(data).hexdigest()}
        return files

    @contextmanager
    def _parent(self, pin, path):
        parts = _parts(path)
        with _directory(pin.root, pin.root_identity) as (root, _):
            descriptor = os.dup(root)
            try:
                for name in parts[:-1]:
                    child = os.open(name, _DIRECTORY, dir_fd=descriptor)
                    os.close(descriptor)
                    descriptor = child
                yield descriptor, parts[-1]
            finally:
                os.close(descriptor)

    def _assert_file(self, pin, path, expected):
        with self._parent(pin, path) as (parent, name):
            raw, _ = _read_at(parent, name, self.limits.max_file_bytes)
        if hashlib.sha256(raw).hexdigest() != expected:
            raise ProjectError("project_file_changed")

    async def apply(self, review_id, digest, *, session_id, run_id, authorize: Callable,
                    cancellation=None):
        if not callable(authorize):
            raise ProjectError("project_authority_required")
        async with self._lock:
            review = self.reviews.get(_id(review_id))
            if (review is None or review["digest"] != digest or review["session_id"] != session_id
                    or review["run_id"] != run_id or review["expires"] <= self.clock()):
                raise ProjectError("project_review_changed")
            row = self.store.get(review["project_id"])
            pin = RepositoryPin.from_json(row["pin"])
            state = await self.inspect(row["project_id"])
            if state["state_digest"] != review["state_digest"]:
                raise ProjectError("project_review_changed")
            kind, args, extra = review["kind"], review["arguments"], review["extra"]
            if kind == "worktree.archive" and self._files(pin) != extra["files"]:
                raise ProjectError("project_review_changed")
            if kind == "file.restore":
                self._assert_file(pin, args["path"], args["expected_sha256"])
            if self.store.operation(review_id, temporary=row["temporary"]) is not None:
                raise ProjectError("project_operation_already_dispatched")
            if any(operation["phase"] != "completed" for operation in
                   self.store.operations(row["project_id"])):
                raise ProjectError("project_previous_effect_unconfirmed")

            def guard():
                if cancellation is not None and cancellation.is_set():
                    raise ProjectError("project_cancelled")
                if self.clock() >= review["expires"]:
                    raise ProjectError("project_review_expired")
                with _directory(self.root, self.root_identity):
                    pass
                if not (kind == "worktree.archive" and removed[0]):
                    pin.assert_current()
                else:
                    with _directory(pin.common_dir, pin.common_identity), \
                            _directory(self.root, self.root_identity):
                        pass
                authorize()
                if not (kind == "worktree.archive" and removed[0]):
                    pin.assert_current()
                    if self._metadata(pin) != extra["metadata"]:
                        raise ProjectError("project_review_changed")

            def journal(phase, receipt):
                def change(value):
                    value["operations"][review_id] = {"digest": digest, "kind": kind,
                                                         "phase": phase, "receipt": receipt}
                guard()
                self.store._update(change, row["temporary"])

            removed = [False]
            self.reviews.pop(review_id)
            recovery = {"operation_id": review_id, "project_id": row["project_id"],
                        "effect_status": "unknown"}
            if "new_id" in extra:
                recovery["target_path"] = str(self.root / extra["new_id"])
            if "snapshot_id" in extra:
                recovery["snapshot_id"] = extra["snapshot_id"]
            journal("dispatched", recovery)
            try:
                if kind in {"worktree.create", "worktree.restore"}:
                    result = await self._create(pin, row, args, extra, guard, cancellation)
                elif kind == "worktree.archive":
                    result = await self._archive(
                        pin, row, extra, state, guard, cancellation, removed)
                else:
                    preimage = self.store.save_snapshot_files(
                        {args["path"]: extra["preimage"]}, temporary=row["temporary"],
                        authorize=guard)

                    def preserve(value):
                        value["snapshots"][extra["snapshot_id"]] = {
                            "project_id": row["project_id"], "files": preimage,
                            "digest": _hash(preimage), "head": state["head"]}
                    guard()
                    self.store._update(preserve, row["temporary"])
                    result = self._restore_file(pin, args, extra["file"], guard)
                    result["rollback_snapshot_id"] = extra["snapshot_id"]
                result.update(operation_id=review_id, succeeded=True, verified=True,
                              effect_status="known")
                journal("completed", result)
                return result
            except (Exception, asyncio.CancelledError):
                # A dispatched journal is already a durable uncertainty receipt.
                # No automatic rollback, file deletion, re-authorisation or replay.
                return {**recovery, "succeeded": False, "verified": False,
                        "error": "project_effect_unconfirmed"}

    async def _create(self, pin, row, args, extra, guard, cancellation):
        target = self.root / extra["new_id"]
        with _directory(self.root, self.root_identity) as (parent, _):
            try:
                os.stat(extra["new_id"], dir_fd=parent, follow_symlinks=False)
            except FileNotFoundError:
                pass
            else:
                raise ProjectError("project_target_exists")
            await self._git(pin, "worktree", "add", "--detach", "--no-checkout", "--",
                            str(target), extra["ref"], authorize=guard, cancellation=cancellation)
        new_pin = RepositoryPin.capture(target)
        for path, item in extra["tree"].items():
            parts = _parts(path)
            with _directory(target, new_pin.root_identity) as (root, _):
                descriptor = os.dup(root)
                try:
                    for component in parts[:-1]:
                        try:
                            os.stat(component, dir_fd=descriptor, follow_symlinks=False)
                        except FileNotFoundError:
                            guard()
                            os.mkdir(component, 0o700, dir_fd=descriptor)
                        child = os.open(component, _DIRECTORY, dir_fd=descriptor)
                        os.close(descriptor)
                        descriptor = child
                    guard()
                    new_pin.assert_current()
                    file = os.open(parts[-1], os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                                   item["mode"], dir_fd=descriptor)
                    try:
                        raw = base64.b64decode(item["base64"])
                        with os.fdopen(file, "wb", closefd=False) as output:
                            output.write(raw)
                            output.flush()
                            os.fsync(file)
                    finally:
                        os.close(file)
                finally:
                    os.close(descriptor)
        await self._git(new_pin, "read-tree", "--reset", extra["ref"], authorize=guard,
                        cancellation=cancellation)
        guard()
        registered = self.store.register(args["name"], new_pin, temporary=row["temporary"],
                                          owned=True, project_id=extra["new_id"],
                                          origin_project_id=row["project_id"])
        if self._files(new_pin) != extra["tree"]:
            raise ProjectError("project_restore_unconfirmed")
        return {"project": registered, "head": extra["ref"]}

    async def _archive(self, pin, row, extra, state, guard, cancellation, removal):
        def archive_guard():
            guard()
            if not removal[0] and self._files(pin) != extra["files"]:
                raise ProjectError("project_review_changed")

        files = self.store.save_snapshot_files(extra["files"], temporary=row["temporary"],
                                                authorize=guard)
        snapshot = {"project_id": row["project_id"], "files": files,
                    "digest": _hash(files), "head": state["head"]}

        def save(value):
            value["snapshots"][extra["snapshot_id"]] = snapshot
        archive_guard()
        self.store._update(save, row["temporary"])
        removed = not state["has_changes"]
        if removed:
            # No --force: Git independently rejects dirty/untracked worktrees.
            source = self.store.get(row["origin_project_id"])
            source_pin = RepositoryPin.from_json(source["pin"])
            if source_pin.common_identity != pin.common_identity:
                raise ProjectError("project_repository_changed")
            await self._git(source_pin, "worktree", "remove", "--", pin.root,
                            authorize=archive_guard, cancellation=cancellation)
            removal[0] = True

        def archive(value):
            current = value["projects"][row["project_id"]]
            if current["revision"] != row["revision"]:
                raise ProjectError("project_revision_conflict")
            current["revision"] += 1
            current["archived"] = True
        # The removed root intentionally no longer exists; the original common
        # metadata and host-selected worktree parent remain pinned.
        archive_guard()
        with _directory(pin.common_dir, pin.common_identity), \
                _directory(self.root, self.root_identity):
            self.store._update(archive, row["temporary"])
        return {"snapshot_id": extra["snapshot_id"], "snapshot_digest": snapshot["digest"],
                "removed": removed, "original_preserved": not removed,
                "path": pin.root}

    def _restore_file(self, pin, args, item, guard):
        with self._parent(pin, args["path"]) as (parent, name):
            self._assert_file(pin, args["path"], args["expected_sha256"])
            temporary = ".jarvis-restore-" + uuid4().hex
            guard()
            descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                                 item["mode"], dir_fd=parent)
            try:
                raw = base64.b64decode(item["base64"])
                with os.fdopen(descriptor, "wb", closefd=False) as output:
                    output.write(raw)
                    output.flush()
                    os.fsync(descriptor)
                guard()
                self._assert_file(pin, args["path"], args["expected_sha256"])
                os.replace(temporary, name, src_dir_fd=parent, dst_dir_fd=parent)
                os.fsync(parent)
                observed, _ = _read_at(parent, name)
                if hashlib.sha256(observed).hexdigest() != item["sha256"]:
                    raise ProjectError("project_restore_unconfirmed")
            finally:
                os.close(descriptor)
                # Preserve staging bytes on any uncertainty; never delete an
                # unobserved name or silently overwrite concurrent changes.
        return {"path": args["path"], "sha256": item["sha256"]}
