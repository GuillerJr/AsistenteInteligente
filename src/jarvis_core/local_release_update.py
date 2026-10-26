"""Own, bounded development releases; no system install, profile mutation or execution.

The host supplies a fresh profile cut, verified recovery-copy pin and central
authority through RecoveryContext. Hashes bind the selected local artifact, not
its author. A native signature is separate evidence, never Python authenticity.
CURRENT is a CAS under the dedicated installation lock. An unresolved PENDING
blocks further effects: inspection is read-only and never retries publication.
"""

from __future__ import annotations

import ast
import errno
import fcntl
import hashlib
import json
import os
import re
import stat
from dataclasses import asdict, dataclass, field
from pathlib import Path, PurePosixPath
from uuid import uuid4

from jarvis_core.recovery import RecoveryContext, canonical
from jarvis_core.recovery_fs import FileIdentity
from jarvis_core.tools import HostPurpose

FORMAT = "jarvis-local-release/1"
INSTALL_FORMAT = "jarvis-local-installation/1"
_HASH = re.compile(r"[a-f0-9]{64}\Z")
_KEY = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}\Z")
_UUID = re.compile(r"[a-f0-9]{32}\Z")
_ROLES = {"state", "memory", "schedules"}
_MAINTENANCE_PURPOSES = frozenset(purpose.value for purpose in HostPurpose)
_JOURNAL_SOURCE = "python/jarvis_core/maintenance_journal.py"
_MAINTENANCE_SOURCE_LIMIT = 262144
_FORBIDDEN = {
    ".git",
    ".codex",
    ".ssh",
    ".env",
    "auth.json",
    "credentials.json",
    "history.jsonl",
    "client",
    "home",
    "provider",
}
_ENTRYPOINTS = {
    "python": "python/jarvis_core/service.py",
    "native": "app/Jarvis.app/Contents/MacOS/Jarvis",
}
_DEPENDENCIES = {"python": "external_required", "codex": "external_official_required"}
_CONTROL = {"installation.json", "releases", "receipts", "CURRENT", "PENDING", "INSTALL.lock"}


class ReleaseError(ValueError):
    def __init__(self, code, *, owner=None, receipt=None):
        super().__init__(code)
        self.code, self.owner, self.receipt = code, owner, receipt


def _fail(code):
    raise ReleaseError(code)


@dataclass(frozen=True)
class ReleaseLimits:
    max_file_bytes: int = 64 * 1024 * 1024
    max_total_bytes: int = 256 * 1024 * 1024
    max_files: int = 2048
    max_directories: int = 256
    max_manifest_bytes: int = 524288
    max_releases: int = 16

    def __post_init__(self):
        maxima = (64 * 1024 * 1024, 256 * 1024 * 1024, 2048, 256, 524288, 16)
        if any(
            type(v) is not int or not 0 < v <= m
            for v, m in zip(vars(self).values(), maxima, strict=True)
        ):
            _fail("release_limits_invalid")


@dataclass(frozen=True)
class ProfilePin:
    profile_id: str
    generation: int
    root_identity: FileIdentity
    source_fingerprint: str
    schemas: tuple[tuple[str, int], ...]
    backup_manifest_sha256: str
    maintenance_purposes: tuple[str, ...] = ()

    def __post_init__(self):
        if (
            not isinstance(self.profile_id, str)
            or not _KEY.fullmatch(self.profile_id)
            or type(self.generation) is not int
            or self.generation < 0
            or type(self.root_identity) is not FileIdentity
            or any(type(v) is not int or v < 0 for v in vars(self.root_identity).values())
            or not _hash(self.source_fingerprint)
            or not _hash(self.backup_manifest_sha256)
            or type(self.schemas) is not tuple
            or len(self.schemas) != 3
            or any(type(row) is not tuple or len(row) != 2 for row in self.schemas)
            or {r for r, _ in self.schemas} != _ROLES
            or any(type(v) is not int or v < 0 for _, v in self.schemas)
            or type(self.maintenance_purposes) is not tuple
            or len(self.maintenance_purposes) > 32
            or any(
                type(v) is not str or v not in _MAINTENANCE_PURPOSES
                for v in self.maintenance_purposes
            )
            or self.maintenance_purposes != tuple(sorted(set(self.maintenance_purposes)))
        ):
            _fail("release_profile_pin_invalid")

    def metadata(self):
        return {
            **asdict(self),
            "schemas": dict(self.schemas),
            "maintenance_purposes": list(self.maintenance_purposes),
            "restored_profile_activation_requires_owner": True,
            "profile_data_modified": False,
            "automatic_replay": False,
        }


@dataclass(frozen=True)
class ReleaseReview:
    kind: str
    artifact_root: Path
    artifact_identity: FileIdentity
    install_root: Path
    install_identity: FileIdentity
    manifest_sha256: str
    version_key: str
    expected_current_sha256: str | None
    expected_installation_sha256: str | None
    previous_key: str | None
    profile_pin: ProfilePin
    inventory: tuple[tuple[str, int, str, bool], ...]
    manifest_bytes: bytes = field(repr=False)
    platform_evidence_json: bytes = field(repr=False)
    digest: str

    def metadata(self):
        return {
            "kind": self.kind,
            "artifact_root": str(self.artifact_root),
            "install_root": str(self.install_root),
            "version_key": self.version_key,
            "manifest_sha256": self.manifest_sha256,
            "expected_current_sha256": self.expected_current_sha256,
            "expected_installation_sha256": self.expected_installation_sha256,
            "previous_key": self.previous_key,
            "profile_pin": self.profile_pin.metadata(),
            "channel": "development",
            "release_authenticity": "not_established",
            "platform_evidence": json.loads(self.platform_evidence_json),
            "external_dependencies": _DEPENDENCIES,
            "entrypoints": _ENTRYPOINTS,
            "files": len(self.inventory),
            "bytes": sum(v[1] for v in self.inventory),
            "launch_performed": False,
            "profile_mutation_performed": False,
            "digest": self.digest,
        }


@dataclass(frozen=True)
class ReleaseReceipt:
    operation_id: str
    kind: str
    status: str
    effect_status: str
    review_digest: str
    version_key: str
    manifest_sha256: str
    previous_key: str | None
    current_sha256: str | None
    profile_pin: dict
    error_code: str | None = None
    cleanup_pending: bool = False
    release_authenticity: str = "not_established"
    launch_performed: bool = False
    profile_data_modified: bool = False
    automatic_replay: bool = False
    restored_profile_activation_requires_owner: bool = True


def _hash(value):
    return isinstance(value, str) and _HASH.fullmatch(value) is not None


def _sha(value):
    return hashlib.sha256(value).hexdigest()


def _identity(info):
    return FileIdentity.of(info)


def _metadata(info):
    return (
        info.st_dev,
        info.st_ino,
        info.st_mode,
        info.st_uid,
        info.st_nlink,
        info.st_size,
        info.st_mtime_ns,
        info.st_ctime_ns,
    )


@dataclass
class _Handle:
    fd: int
    identity: FileIdentity
    uncertain: bool = False

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
        if self.uncertain or _identity(info) != self.identity:
            _fail("release_close_uncertain")
        try:
            os.close(self.fd)
        except BaseException:
            self.uncertain = True
            raise
        self.fd = -1


class ReleaseOwner:
    """Retains descriptors/lock and exact created identities after a failed call.

    cleanup never republishes CURRENT, never touches profiles, and never erases
    a publication-uncertain PENDING. After a crash it cannot recreate ownership.
    """

    def __init__(self):
        self.handles = []
        self.created = []
        self.creation_unknown = []
        self.publication_attempted = False
        self.closed = False
        self.keep = set()

    def add(self, fd):
        try:
            identity = _identity(os.fstat(fd))
        except BaseException:
            # The returned descriptor is retained; it must not be blindly closed
            # or adopted via a pathname if its identity could not be captured.
            self.handles.append(_Handle(fd, FileIdentity(-1, -1), True))
            raise
        value = _Handle(fd, identity)
        self.handles.append(value)
        return value

    def cleanup(self):
        for parent, name in self.creation_unknown:
            parent.check()
            try:
                os.stat(name, dir_fd=parent.fd, follow_symlinks=False)
            except FileNotFoundError:
                continue
            _fail("release_creation_uncertain")
        # A prepublication failure can leave our own staged directories read
        # only. Restore write permission only after exact identity comparison.
        for parent, name, identity, is_dir in self.created:
            if not is_dir or (parent.path, name) in self.keep:
                continue
            parent.check()
            try:
                info = os.stat(name, dir_fd=parent.fd, follow_symlinks=False)
            except FileNotFoundError:
                continue
            if _identity(info) != identity:
                _fail("release_cleanup_identity_changed")
            os.chmod(name, 0o700, dir_fd=parent.fd, follow_symlinks=False)
        for parent, name, identity, is_dir in reversed(self.created):
            if (parent.path, name) in self.keep:
                continue
            parent.check()
            try:
                info = os.stat(name, dir_fd=parent.fd, follow_symlinks=False)
            except FileNotFoundError:
                continue
            if _identity(info) != identity:
                _fail("release_cleanup_identity_changed")
            if is_dir:
                os.rmdir(name, dir_fd=parent.fd)
            else:
                os.unlink(name, dir_fd=parent.fd)
            os.fsync(parent.fd)
        for handle in reversed(self.handles):
            handle.close()
        self.closed = True


class _Dir:
    def __init__(self, path, owner, *, private=False, parent=None, name=None):
        self.path, self.owner, self.parent, self.name = Path(path), owner, parent, name
        if parent is None:
            if not self.path.is_absolute() or ".." in self.path.parts or len(self.path.parts) > 64:
                _fail("release_path_invalid")
            current = os.open(self.path.anchor, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
            chain = [owner.add(current)]
            for part in self.path.parts[1:]:
                current = os.open(
                    part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=current
                )
                chain.append(owner.add(current))
            self.chain = chain
            self.names = self.path.parts[1:]
        else:
            parent.check()
            fd = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent.fd)
            self.chain = [owner.add(fd)]
            self.names = ()
        self.handle = self.chain[-1]
        self.fd = self.handle.fd
        self.identity = self.handle.identity
        self.private = private
        self.check()

    def check(self):
        if self.parent:
            self.parent.check()
            named = os.stat(self.name, dir_fd=self.parent.fd, follow_symlinks=False)
            if not stat.S_ISDIR(named.st_mode) or _identity(named) != self.identity:
                _fail("release_identity_changed")
        else:
            for parent, name, child in zip(
                self.chain[:-1], self.names, self.chain[1:], strict=True
            ):
                info = os.stat(name, dir_fd=parent.fd, follow_symlinks=False)
                if not stat.S_ISDIR(info.st_mode) or _identity(info) != child.identity:
                    _fail("release_identity_changed")
        info = os.fstat(self.fd)
        if (
            _identity(info) != self.identity
            or info.st_uid != os.getuid()
            or stat.S_IMODE(info.st_mode) & 0o022
            or (self.private and stat.S_IMODE(info.st_mode) != 0o700)
        ):
            _fail("release_directory_unsafe")

    def child(self, name):
        return _Dir(self.path / name, self.owner, parent=self, name=name)


def _entries(directory, maximum):
    directory.check()
    result = []
    with os.scandir(directory.fd) as rows:
        for row in rows:
            result.append(row.name)
            if len(result) > maximum:
                _fail("release_entries_limit")
    directory.check()
    return set(result)


def _read(directory, name, maximum, checkpoint):
    directory.check()
    handle = directory.owner.add(
        os.open(name, os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW, dir_fd=directory.fd)
    )
    info = os.fstat(handle.fd)
    if (
        not stat.S_ISREG(info.st_mode)
        or info.st_nlink != 1
        or info.st_uid != os.getuid()
        or stat.S_IMODE(info.st_mode) & 0o022
        or info.st_size > maximum
    ):
        _fail("release_file_unsafe")
    initial = _metadata(info)
    data = bytearray()
    while len(data) < info.st_size:
        checkpoint()
        chunk = os.pread(handle.fd, min(65536, info.st_size - len(data)), len(data))
        if not chunk:
            _fail("release_identity_changed")
        data.extend(chunk)
    if (
        _metadata(os.fstat(handle.fd)) != initial
        or _metadata(os.stat(name, dir_fd=directory.fd, follow_symlinks=False)) != initial
    ):
        _fail("release_identity_changed")
    directory.check()
    handle.close()
    return bytes(data), initial


def _decode(data):
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                _fail("release_json_invalid")
            result[key] = value
        return result

    try:
        return json.loads(
            data, object_pairs_hook=unique, parse_constant=lambda _: _fail("release_json_invalid")
        )
    except (ValueError, TypeError, UnicodeError, RecursionError):
        _fail("release_json_invalid")


def _path(value):
    if (
        not isinstance(value, str)
        or len(value) > 240
        or "\\" in value
        or value != PurePosixPath(value).as_posix()
        or len(value.split("/")) > 12
        or any(
            part in {"", ".", ".."}
            or part.casefold() in _FORBIDDEN
            or part.casefold().startswith(".env.")
            for part in value.split("/")
        )
        or value.startswith("/")
        or not value.startswith(("python/", "app/"))
    ):
        _fail("release_resource_path_invalid")
    return value


def _manifest(data, limits):
    value = _decode(data)
    if (
        type(value) is not dict
        or set(value)
        != {
            "format",
            "product",
            "channel",
            "version_key",
            "entrypoints",
            "external_dependencies",
            "schemas",
            "maintenance_purposes",
            "files",
        }
        or value["format"] != FORMAT
        or value["product"] != "Jarvis"
        or value["channel"] != "development"
        or not isinstance(value["version_key"], str)
        or not _KEY.fullmatch(value["version_key"])
        or value["entrypoints"] != _ENTRYPOINTS
        or value["external_dependencies"] != _DEPENDENCIES
        or type(value["schemas"]) is not dict
        or set(value["schemas"]) != _ROLES
        or type(value["files"]) is not list
        or not 2 <= len(value["files"]) <= limits.max_files
        or type(value["maintenance_purposes"]) is not list
        or len(value["maintenance_purposes"]) > 32
        or any(
            type(v) is not str or v not in _MAINTENANCE_PURPOSES
            for v in value["maintenance_purposes"]
        )
        or value["maintenance_purposes"] != sorted(set(value["maintenance_purposes"]))
    ):
        _fail("release_manifest_invalid")
    for pair in value["schemas"].values():
        if (
            type(pair) is not list
            or len(pair) != 2
            or any(type(v) is not int or not 0 <= v <= 10000 for v in pair)
            or pair[0] > pair[1]
        ):
            _fail("release_schema_contract_invalid")
    rows, names, total = [], set(), 0
    for row in value["files"]:
        if type(row) is not dict or set(row) != {"path", "size", "sha256", "executable"}:
            _fail("release_manifest_invalid")
        name = _path(row["path"])
        if (
            name in names
            or not _hash(row["sha256"])
            or type(row["size"]) is not int
            or not 0 <= row["size"] <= limits.max_file_bytes
            or type(row["executable"]) is not bool
        ):
            _fail("release_manifest_invalid")
        names.add(name)
        if name == _JOURNAL_SOURCE and row["size"] > _MAINTENANCE_SOURCE_LIMIT:
            _fail("release_maintenance_source_invalid")
        total += row["size"]
        rows.append((name, row["size"], row["sha256"], row["executable"]))
    if total + len(data) > limits.max_total_bytes:
        _fail("release_payload_limit")
    if not set(_ENTRYPOINTS.values()) <= names:
        _fail("release_entrypoints_missing")
    if not dict((name, executable) for name, _, _, executable in rows)[_ENTRYPOINTS["native"]]:
        _fail("release_native_not_executable")
    if not any(name == "python/jarvis_core/__init__.py" for name in names):
        _fail("release_python_package_missing")
    if _JOURNAL_SOURCE not in names:
        _fail("release_maintenance_source_missing")
    return value, tuple(sorted(rows))


def _compatible(manifest, pin, context):
    if pin.profile_id != context.profile_id or pin.generation != context.generation:
        _fail("release_profile_pin_changed")
    for role, version in pin.schemas:
        minimum, maximum = manifest["schemas"][role]
        if not minimum <= version <= maximum:
            _fail("release_profile_schema_incompatible")
    required = set(pin.maintenance_purposes) | {"release.update.apply", "release.rollback.apply"}
    if not required <= set(manifest["maintenance_purposes"]):
        _fail("release_maintenance_purposes_incompatible")


def source_maintenance_purposes(data):
    """Conservative declaration check of our own module; never imports payload."""
    if len(data) > _MAINTENANCE_SOURCE_LIMIT:
        _fail("release_maintenance_source_invalid")
    try:
        tree = ast.parse(data)
    except (SyntaxError, UnicodeError, ValueError, RecursionError, MemoryError):
        _fail("release_maintenance_source_invalid")
    assignments = [
        node
        for node in tree.body
        if isinstance(node, ast.Assign)
        and any(
            isinstance(target, ast.Name) and target.id == "_PURPOSES" for target in node.targets
        )
    ]
    if (
        len(assignments) != 1
        or len(assignments[0].targets) != 1
        or not isinstance(assignments[0].value, (ast.Set, ast.List))
    ):
        _fail("release_maintenance_source_invalid")
    elements = assignments[0].value.elts
    if not 1 <= len(elements) <= 32 or any(
        not isinstance(node, ast.Constant)
        or type(node.value) is not str
        or node.value not in _MAINTENANCE_PURPOSES
        for node in elements
    ):
        _fail("release_maintenance_source_invalid")
    values = [node.value for node in elements]
    if len(values) != len(set(values)):
        _fail("release_maintenance_source_invalid")
    for count, node in enumerate(ast.walk(tree)):
        if count > 50000:
            _fail("release_maintenance_source_invalid")
        if (
            isinstance(node, ast.Name)
            and node.id == "_PURPOSES"
            and isinstance(node.ctx, (ast.Store, ast.Del))
            and node is not assignments[0].targets[0]
        ):
            _fail("release_maintenance_source_invalid")
    return tuple(sorted(values))


def _artifact(directory, context, limits, pin):
    raw, _ = _read(
        directory,
        "release.json",
        limits.max_manifest_bytes,
        lambda: context.checkpoint("release.verify"),
    )
    manifest, inventory = _manifest(raw, limits)
    _compatible(manifest, pin, context)
    expected = {row[0]: row for row in inventory}
    seen, directories = set(), 0
    declared_purposes = None

    def visit(current, prefix=""):
        nonlocal directories, declared_purposes
        for name in sorted(_entries(current, limits.max_files + limits.max_directories + 1)):
            context.checkpoint("release.verify")
            if not prefix and name == "release.json":
                continue
            relative = prefix + name
            info = os.stat(name, dir_fd=current.fd, follow_symlinks=False)
            if stat.S_ISDIR(info.st_mode):
                if not any(path.startswith(relative + "/") for path in expected):
                    _fail("release_unlisted_resource")
                directories += 1
                if directories > limits.max_directories:
                    _fail("release_directories_limit")
                visit(current.child(name), relative + "/")
            else:
                if relative not in expected:
                    _fail("release_unlisted_resource")
                row = expected[relative]
                data, metadata = _read(
                    current,
                    name,
                    min(limits.max_file_bytes, _MAINTENANCE_SOURCE_LIMIT)
                    if relative == _JOURNAL_SOURCE
                    else limits.max_file_bytes,
                    lambda: context.checkpoint("release.verify"),
                )
                if (
                    len(data) != row[1]
                    or _sha(data) != row[2]
                    or bool(metadata[2] & 0o111) != row[3]
                ):
                    _fail("release_resource_changed")
                seen.add(relative)
                if relative == _JOURNAL_SOURCE:
                    declared_purposes = source_maintenance_purposes(data)

    visit(directory)
    if seen != set(expected):
        _fail("release_resource_missing")
    if declared_purposes != tuple(manifest["maintenance_purposes"]):
        _fail("release_maintenance_source_mismatch")
    return raw, manifest, inventory


def _control(directory, name, context, *, absent=True):
    try:
        data, _ = _read(directory, name, 65536, lambda: context.checkpoint("release.inspect"))
    except FileNotFoundError:
        if absent:
            return None, None
        raise
    value = _decode(data)
    if type(value) is not dict:
        _fail("release_control_invalid")
    return value, _sha(data)


def _installation(directory, context, limits):
    entries = _entries(directory, limits.max_releases + 8)
    temporary = {name for name in entries if re.fullmatch(r"\.current-[a-f0-9]{32}", name)}
    if entries - _CONTROL - temporary:
        _fail("release_install_root_not_dedicated")
    installation, install_sha = _control(directory, "installation.json", context)
    current, current_sha = _control(directory, "CURRENT", context)
    pending, pending_sha = _control(directory, "PENDING", context)
    if temporary and (
        pending is None or temporary != {".current-" + str(pending.get("operation_id"))}
    ):
        _fail("release_unowned_temporary")
    if installation is None:
        if entries - {"INSTALL.lock"}:
            _fail("release_installation_incomplete")
    elif (
        set(installation) != {"format", "installation_id", "channel"}
        or installation["format"] != INSTALL_FORMAT
        or not isinstance(installation["installation_id"], str)
        or not _UUID.fullmatch(installation["installation_id"])
        or installation["channel"] != "development"
        or not {"releases", "receipts"} <= entries
    ):
        _fail("release_installation_invalid")
    if current is not None:
        if (
            set(current)
            != {
                "format",
                "revision",
                "operation_id",
                "release_key",
                "manifest_sha256",
                "previous",
                "profile_pin",
            }
            or current["format"] != INSTALL_FORMAT
            or not all(
                isinstance(current[k], str) and _UUID.fullmatch(current[k])
                for k in ("revision", "operation_id")
            )
            or not isinstance(current["release_key"], str)
            or not _KEY.fullmatch(current["release_key"])
            or not _hash(current["manifest_sha256"])
        ):
            _fail("release_current_invalid")
        previous = current["previous"]
        if previous is not None and (
            type(previous) is not dict
            or set(previous) != {"release_key", "manifest_sha256"}
            or not isinstance(previous["release_key"], str)
            or not _KEY.fullmatch(previous["release_key"])
            or not _hash(previous["manifest_sha256"])
        ):
            _fail("release_current_invalid")
    if installation is not None:
        releases = directory.child("releases")
        if len(_entries(releases, limits.max_releases)) > limits.max_releases:
            _fail("release_versions_limit")
        directory.child("receipts")
    return {
        "installation": installation,
        "installation_sha256": install_sha,
        "current": current,
        "current_sha256": current_sha,
        "pending": pending,
        "pending_sha256": pending_sha,
        "temporary_current_files": sorted(temporary),
    }


def _evidence(verifier, path, digest):
    value = {"native_signature": "not_checked"} if verifier is None else verifier(path, digest)
    if (
        type(value) is not dict
        or not value
        or len(value) > 8
        or len(canonical(value)) > 4096
        or any(not isinstance(k, str) or not isinstance(v, (str, bool)) for k, v in value.items())
    ):
        _fail("release_platform_evidence_invalid")
    return canonical(value)


def _public_code(error):
    code = getattr(error, "code", None)
    if not isinstance(code, str) or not re.fullmatch(r"[a-z][a-z0-9_]{0,127}", code):
        code = "release_failed"
    return code


def _wrap(error, owner, receipt=None):
    return ReleaseError(_public_code(error), owner=None if owner.closed else owner, receipt=receipt)


def _review(artifact_root, install_root, context, pin, *, rollback, limits, verifier):
    owner = ReleaseOwner()
    context.begin()
    try:
        installation = _Dir(install_root, owner, private=True)
        if installation.identity == pin.root_identity:
            _fail("release_install_root_is_profile")
        state = _installation(installation, context, limits)
        if state["pending"] is not None:
            _fail("release_pending_requires_reconciliation")
        current = state["current"]
        if rollback:
            if current is None or current["previous"] is None:
                _fail("release_no_previous_version")
            target = current["previous"]
            artifact_root = installation.path / "releases" / target["release_key"]
        artifact = _Dir(artifact_root, owner)
        if artifact.identity == pin.root_identity:
            _fail("release_artifact_is_profile")
        if artifact.identity == installation.identity:
            _fail("release_artifact_is_install_root")
        raw, manifest, inventory = _artifact(artifact, context, limits, pin)
        if rollback and (
            manifest["version_key"] != target["release_key"]
            or _sha(raw) != target["manifest_sha256"]
        ):
            _fail("release_previous_version_changed")
        if current is not None:
            if manifest["version_key"] == current["release_key"]:
                _fail("release_version_already_current")
            if not rollback:
                releases = installation.child("releases")
                if manifest["version_key"] in _entries(releases, limits.max_releases):
                    _fail("release_version_key_reused")
        evidence = _evidence(verifier, artifact.path, _sha(raw))
        payload = {
            "kind": "release.rollback" if rollback else "release.update",
            "artifact_root": str(artifact.path),
            "artifact_identity": asdict(artifact.identity),
            "install_root": str(installation.path),
            "install_identity": asdict(installation.identity),
            "manifest_sha256": _sha(raw),
            "version_key": manifest["version_key"],
            "expected_current_sha256": state["current_sha256"],
            "expected_installation_sha256": state["installation_sha256"],
            "previous_key": current["release_key"] if current else None,
            "profile_pin": pin.metadata(),
            "inventory": inventory,
            "platform_evidence": _decode(evidence),
        }
        result = ReleaseReview(
            payload["kind"],
            artifact.path,
            artifact.identity,
            installation.path,
            installation.identity,
            _sha(raw),
            manifest["version_key"],
            state["current_sha256"],
            state["installation_sha256"],
            payload["previous_key"],
            pin,
            inventory,
            raw,
            evidence,
            _sha(canonical(payload)),
        )
        owner.cleanup()
        return result
    except BaseException as error:
        try:
            owner.cleanup()
        except BaseException:
            pass
        raise _wrap(error, owner) from None


def review_release(
    artifact_root,
    install_root,
    context: RecoveryContext,
    profile_pin: ProfilePin,
    *,
    limits=None,
    authenticity_verifier=None,
):
    return _review(
        artifact_root,
        install_root,
        context,
        profile_pin,
        rollback=False,
        limits=limits or ReleaseLimits(),
        verifier=authenticity_verifier,
    )


def review_rollback(
    install_root,
    context: RecoveryContext,
    profile_pin: ProfilePin,
    *,
    limits=None,
    authenticity_verifier=None,
):
    return _review(
        None,
        install_root,
        context,
        profile_pin,
        rollback=True,
        limits=limits or ReleaseLimits(),
        verifier=authenticity_verifier,
    )


def inspect_installation(install_root, context: RecoveryContext, *, limits=None):
    limits = limits or ReleaseLimits()
    owner = ReleaseOwner()
    context.begin()
    try:
        root = _Dir(install_root, owner, private=True)
        result = _installation(root, context, limits)
        result.update(
            root_identity=asdict(root.identity),
            mutation_performed=False,
            prior_publication_durability="not_established",
            automatic_replay=False,
            launch_performed=False,
            release_payload_verification="not_performed_by_control_readback",
        )
        owner.cleanup()
        return result
    except BaseException as error:
        try:
            owner.cleanup()
        except BaseException:
            pass
        raise _wrap(error, owner) from None


def _mkdir(parent, name, owner):
    parent.check()
    owner.creation_unknown.append((parent, name))
    os.mkdir(name, 0o700, dir_fd=parent.fd)
    info = os.stat(name, dir_fd=parent.fd, follow_symlinks=False)
    owner.created.append((parent, name, _identity(info), True))
    owner.creation_unknown.pop()
    return parent.child(name)


def _write(parent, name, data, owner, context, *, executable=False, immutable=False):
    parent.check()
    context.checkpoint("release.write", True)
    owner.creation_unknown.append((parent, name))
    fd = os.open(
        name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=parent.fd
    )
    handle = owner.add(fd)
    owner.created.append((parent, name, handle.identity, False))
    owner.creation_unknown.pop()
    position = 0
    while position < len(data):
        context.checkpoint("release.write", True)
        wrote = os.write(fd, data[position : position + 65536])
        if wrote <= 0:
            _fail("release_write_failed")
        position += wrote
    if immutable:
        os.fchmod(fd, 0o500 if executable else 0o400)
    elif executable:
        os.fchmod(fd, 0o700)
    os.fsync(fd)
    handle.close()
    parent.check()


def _lock(root, owner):
    fd = os.open(
        "INSTALL.lock",
        os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_NONBLOCK,
        0o600,
        dir_fd=root.fd,
    )
    handle = owner.add(fd)
    info = os.fstat(fd)
    if (
        not stat.S_ISREG(info.st_mode)
        or info.st_nlink != 1
        or info.st_uid != os.getuid()
        or stat.S_IMODE(info.st_mode) != 0o600
    ):
        _fail("release_lock_unsafe")
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        _fail("release_installation_busy")
    root.check()
    if _identity(os.stat("INSTALL.lock", dir_fd=root.fd, follow_symlinks=False)) != handle.identity:
        _fail("release_lock_changed")


def _copy_artifact(source, target, owner, context, review, limits):
    _write(target, "release.json", review.manifest_bytes, owner, context, immutable=True)
    destinations = {"": target}
    for relative, size, sha, executable in review.inventory:
        parts, source_dir = relative.split("/"), source
        prefix = ""
        for component in parts[:-1]:
            source_dir = source_dir.child(component)
            child = prefix + component
            if child not in destinations:
                destinations[child] = _mkdir(destinations[prefix], component, owner)
            prefix = child + "/"
            destinations[prefix] = destinations[child]
        data, _ = _read(
            source_dir, parts[-1], limits.max_file_bytes, lambda: context.checkpoint("release.copy")
        )
        if len(data) != size or _sha(data) != sha:
            _fail("release_resource_changed")
        _write(
            destinations[prefix],
            parts[-1],
            data,
            owner,
            context,
            executable=executable,
            immutable=True,
        )
    # Final bytes are re-verified before becoming an immutable release. DAC is
    # protection against accidents, not an authenticity barrier against this UID.
    raw, _, inventory = _artifact(target, context, limits, review.profile_pin)
    if _sha(raw) != review.manifest_sha256 or inventory != review.inventory:
        _fail("release_stage_changed")
    for parent, name, _, is_dir in reversed(owner.created):
        if parent.path == target.path or target.path in parent.path.parents:
            if is_dir:
                os.chmod(name, 0o500, dir_fd=parent.fd, follow_symlinks=False)
    for directory in destinations.values():
        os.fsync(directory.fd)
    os.fchmod(target.fd, 0o500)
    os.fsync(target.fd)


def apply_release(
    review: ReleaseReview,
    context: RecoveryContext,
    *,
    limits=None,
    authenticity_verifier=None,
    fault=None,
):
    """One authorized effect. fault is a host/test callback, never artifact code.

    A pending/unknown publication returns a retained owner and receipt in
    ReleaseError. cleanup can close resources but leaves durable PENDING after
    publication was attempted. Inspect does not establish a lost fsync ACK.
    """
    limits = limits or ReleaseLimits()
    owner = ReleaseOwner()
    context.begin()
    receipt = None
    root = None
    pending_name = None
    try:
        root = _Dir(review.install_root, owner, private=True)
        if root.identity != review.install_identity:
            _fail("release_install_root_changed")
        if root.identity == review.profile_pin.root_identity:
            _fail("release_install_root_is_profile")
        _lock(root, owner)
        state = _installation(root, context, limits)
        if state["pending"] is not None:
            _fail("release_pending_requires_reconciliation")
        if (
            state["current_sha256"] != review.expected_current_sha256
            or state["installation_sha256"] != review.expected_installation_sha256
        ):
            _fail("release_current_changed")
        source = _Dir(review.artifact_root, owner)
        if source.identity != review.artifact_identity:
            _fail("release_artifact_changed")
        raw, _manifest_value, inventory = _artifact(source, context, limits, review.profile_pin)
        if _sha(raw) != review.manifest_sha256 or inventory != review.inventory:
            _fail("release_artifact_changed")
        if (
            _evidence(authenticity_verifier, source.path, _sha(raw))
            != review.platform_evidence_json
        ):
            _fail("release_platform_evidence_changed")
        if state["installation"] is None:
            releases = _mkdir(root, "releases", owner)
            receipts = _mkdir(root, "receipts", owner)
            _write(
                root,
                "installation.json",
                canonical(
                    {
                        "format": INSTALL_FORMAT,
                        "installation_id": uuid4().hex,
                        "channel": "development",
                    }
                ),
                owner,
                context,
            )
            for row in owner.created:
                if row[0] is root:
                    owner.keep.add((row[0].path, row[1]))
            os.fsync(root.fd)
        else:
            releases, receipts = root.child("releases"), root.child("receipts")
        if len(_entries(releases, limits.max_releases)) >= limits.max_releases and (
            review.kind != "release.rollback"
        ):
            _fail("release_versions_limit")
        pending = {
            "format": INSTALL_FORMAT,
            "operation_id": context.operation_id,
            "review_digest": review.digest,
            "kind": review.kind,
            "expected_current_sha256": review.expected_current_sha256,
            "target_key": review.version_key,
            "manifest_sha256": review.manifest_sha256,
            "profile_pin": review.profile_pin.metadata(),
        }
        _write(root, "PENDING", canonical(pending), owner, context)
        os.fsync(root.fd)
        pending_name = "PENDING"
        if fault:
            fault("pending_durable")
        if review.kind == "release.rollback":
            previous = state["current"]["previous"]
            if (
                previous is None
                or previous["release_key"] != review.version_key
                or previous["manifest_sha256"] != review.manifest_sha256
            ):
                _fail("release_previous_version_changed")
        else:
            if review.version_key in _entries(releases, limits.max_releases):
                _fail("release_version_key_reused")
            stage_name = ".stage-" + context.operation_id
            stage = _mkdir(releases, stage_name, owner)
            _copy_artifact(source, stage, owner, context, review, limits)
            context.checkpoint("release.stage_publish", True)
            # Serialized by INSTALL.lock, plus an exclusive native rename.
            from jarvis_core.recovery_fs import no_replace

            no_replace(releases, stage_name, review.version_key)
            # A published release is never recursively cleaned after a lost ACK.
            owner.keep.update(
                (p.path, name)
                for p, name, _, _ in owner.created
                if p.path == stage.path or stage.path in p.path.parents
            )
            owner.keep.add((releases.path, stage_name))
            os.fsync(releases.fd)
            if fault:
                fault("release_staged")
        context.checkpoint("release.current_cas", True)
        current_now, current_hash = _control(root, "CURRENT", context)
        if current_hash != review.expected_current_sha256:
            _fail("release_current_changed")
        current = {
            "format": INSTALL_FORMAT,
            "revision": uuid4().hex,
            "operation_id": context.operation_id,
            "release_key": review.version_key,
            "manifest_sha256": review.manifest_sha256,
            "previous": None
            if current_now is None
            else {
                "release_key": current_now["release_key"],
                "manifest_sha256": current_now["manifest_sha256"],
            },
            "profile_pin": review.profile_pin.metadata(),
        }
        current_bytes = canonical(current)
        temporary = ".current-" + context.operation_id
        _write(root, temporary, current_bytes, owner, context)
        context.checkpoint("release.current_publish", True)
        # Re-check after the final authority boundary as well as under the
        # cooperative installation lock. Never overwrite a newly observed CAS.
        if _control(root, "CURRENT", context)[1] != review.expected_current_sha256:
            _fail("release_current_changed")
        owner.publication_attempted = True
        owner.keep.add((root.path, "PENDING"))
        if fault:
            fault("before_current_replace")
        os.replace(temporary, "CURRENT", src_dir_fd=root.fd, dst_dir_fd=root.fd)
        if fault:
            fault("after_current_replace")
        os.fsync(root.fd)
        receipt = ReleaseReceipt(
            context.operation_id,
            review.kind,
            "completed",
            "current_selected",
            review.digest,
            review.version_key,
            review.manifest_sha256,
            review.previous_key,
            _sha(current_bytes),
            review.profile_pin.metadata(),
        )
        _write(receipts, context.operation_id + ".json", canonical(asdict(receipt)), owner, context)
        owner.keep.add((receipts.path, context.operation_id + ".json"))
        os.fsync(receipts.fd)
        if fault:
            fault("receipt_durable")
        root.check()
        os.unlink("PENDING", dir_fd=root.fd)
        os.fsync(root.fd)
        owner.cleanup()
        return receipt
    except BaseException as error:
        code = _public_code(error)
        uncertain = owner.publication_attempted
        if uncertain:
            if root is not None and pending_name:
                owner.keep.add((root.path, pending_name))
            receipt = ReleaseReceipt(
                context.operation_id,
                review.kind,
                "uncertain",
                "publication_unknown",
                review.digest,
                review.version_key,
                review.manifest_sha256,
                review.previous_key,
                None,
                review.profile_pin.metadata(),
                code,
                True,
            )
        else:
            try:
                owner.cleanup()
            except BaseException:
                pass
            receipt = ReleaseReceipt(
                context.operation_id,
                review.kind,
                "failed",
                "no_current_change",
                review.digest,
                review.version_key,
                review.manifest_sha256,
                review.previous_key,
                review.expected_current_sha256,
                review.profile_pin.metadata(),
                code,
                not owner.closed,
            )
        raise _wrap(error, owner, receipt) from None
