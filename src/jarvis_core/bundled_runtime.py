"""Own bounded, read-only verification of a sealed relocatable Jarvis runtime.

The native host verifies the manifest pin before starting this interpreter.
This second check does not bootstrap trust, sign code, download, or launch it.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import stat
import struct
import unicodedata
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from urllib.parse import urlsplit

SCHEMA = "jarvis.bundled-runtime.v1"
MANIFEST_NAME = "runtime-manifest.json"
MAX_MANIFEST_BYTES = 8 * 1024 * 1024
MAX_FILES = 32768
MAX_FILE_BYTES = 512 * 1024 * 1024
MAX_TREE_BYTES = 2 * 1024 * 1024 * 1024
ENTRYPOINTS = {
    "python": "python/bin/python3.11",
    "source": "core",
    "dependencies": "site-packages",
    "codex": "codex/bin/codex",
}
MODEL_ENTRYPOINT = "models/minilm"
CA_CERTIFICATES_PATH = "certs/cacert.pem"
_SHA = re.compile(r"[a-f0-9]{64}\Z")
_COMMIT = re.compile(r"[a-f0-9]{40}\Z")
_TOP = {"schema", "source_commit", "target", "minimum_macos", "python_version",
        "codex_version", "entrypoints", "files", "components"}


class BundledRuntimeError(ValueError):
    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


def _require(condition: bool, code: str) -> None:
    if not condition:
        raise BundledRuntimeError(code)


def relative_path(value: object) -> str:
    _require(isinstance(value, str), "runtime_path_invalid")
    assert isinstance(value, str)
    _require(not any(0xD800 <= ord(c) <= 0xDFFF for c in value), "runtime_path_invalid")
    _require(0 < len(value.encode("utf-8")) <= 1024 and "\\" not in value
             and not any(ord(c) < 32 or ord(c) == 127 for c in value), "runtime_path_invalid")
    parts = value.split("/")
    _require(all(part not in {"", ".", ".."} for part in parts)
             and not PurePosixPath(value).is_absolute()
             and unicodedata.normalize("NFC", value) == value, "runtime_path_invalid")
    return value


def _decode(raw: bytes) -> dict:
    _require(0 < len(raw) <= MAX_MANIFEST_BYTES, "runtime_manifest_limit")

    def pairs(items):
        result = {}
        for key, value in items:
            _require(key not in result, "runtime_manifest_duplicate_key")
            result[key] = value
        return result

    try:
        value = json.loads(raw, object_pairs_hook=pairs,
                           parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
    except (UnicodeError, json.JSONDecodeError, ValueError, RecursionError):
        raise BundledRuntimeError("runtime_manifest_invalid") from None
    _require(type(value) is dict, "runtime_manifest_invalid")
    return value


def parse_manifest(raw: bytes, *, expected_sha256: str,
                   expected_source_commit: str | None = None) -> dict:
    _require(isinstance(raw, bytes) and 0 < len(raw) <= MAX_MANIFEST_BYTES,
             "runtime_manifest_limit")
    _require(isinstance(expected_sha256, str)
             and _SHA.fullmatch(expected_sha256) is not None
             and hashlib.sha256(raw).hexdigest() == expected_sha256, "runtime_manifest_hash")
    value = _decode(raw)
    try:
        normalized = json.dumps(value, ensure_ascii=False, sort_keys=True,
                                separators=(",", ":"), allow_nan=False).encode("utf-8")
    except (UnicodeError, ValueError):
        raise BundledRuntimeError("runtime_manifest_invalid") from None
    _require(normalized == raw, "runtime_manifest_noncanonical")
    _require(set(value) == _TOP and value["schema"] == SCHEMA,
             "runtime_manifest_shape")
    _require(value["target"] == "aarch64-apple-darwin"
             and value["minimum_macos"] == "14.0"
             and value["python_version"] == "3.11.18"
             and value["codex_version"] == "0.163.0", "runtime_version_unsupported")
    commit = value["source_commit"]
    _require(isinstance(commit, str) and _COMMIT.fullmatch(commit) is not None
             and (expected_source_commit is None or commit == expected_source_commit),
             "runtime_source_commit")
    entries = value["entrypoints"]
    _require(type(entries) is dict and set(entries) in (set(ENTRYPOINTS),
             set(ENTRYPOINTS) | {"model"}) and all(entries.get(k) == v for k, v in
             ENTRYPOINTS.items()) and ("model" not in entries
             or entries["model"] == MODEL_ENTRYPOINT), "runtime_entrypoints_invalid")
    files = value["files"]
    _require(type(files) is list and 1 <= len(files) <= MAX_FILES, "runtime_file_limit")
    paths, folded, total = set(), set(), 0
    for row in files:
        _require(type(row) is dict and set(row) == {"path", "bytes", "sha256", "executable"},
                 "runtime_file_shape")
        name = relative_path(row["path"])
        _require(name != MANIFEST_NAME and name not in paths and name.casefold() not in folded,
                 "runtime_file_duplicate")
        paths.add(name)
        folded.add(name.casefold())
        _require(type(row["bytes"]) is int and 0 <= row["bytes"] <= MAX_FILE_BYTES
                 and type(row["executable"]) is bool and isinstance(row["sha256"], str)
                 and _SHA.fullmatch(row["sha256"]) is not None, "runtime_file_shape")
        total += row["bytes"]
    _require(total <= MAX_TREE_BYTES and [r["path"] for r in files] == sorted(paths),
             "runtime_tree_limit_or_order")
    _require({entries["python"], entries["codex"], CA_CERTIFICATES_PATH,
              "core/jarvis_core/__init__.py"} <= paths, "runtime_required_file_missing")
    by_path = {row["path"]: row for row in files}
    _require(all(by_path[entries[k]]["executable"] for k in ("python", "codex")),
             "runtime_entrypoint_not_executable")
    _require(by_path[CA_CERTIFICATES_PATH]["bytes"] > 0, "runtime_certificates_empty")
    _require(any(p.startswith("site-packages/") for p in paths), "runtime_dependencies_missing")
    if "model" in entries:
        _require({MODEL_ENTRYPOINT + "/manifest.json", MODEL_ENTRYPOINT + "/tokenizer.json",
                  MODEL_ENTRYPOINT + "/onnx/model_qint8_arm64.onnx",
                  MODEL_ENTRYPOINT + "/LICENSE.Apache-2.0.txt"} <= paths,
                 "runtime_model_incomplete")
    components = value["components"]
    _require(type(components) is list and 3 <= len(components) <= 128,
             "runtime_component_limit")
    ids = set()
    for component in components:
        _require(type(component) is dict and set(component) == {
            "id", "version", "origin", "archive_sha256", "license_paths"},
            "runtime_component_shape")
        identifier, version, origin = (component[k] for k in ("id", "version", "origin"))
        _require(isinstance(identifier, str)
                 and re.fullmatch(r"[a-z0-9][a-z0-9._-]{0,95}", identifier) is not None
                 and identifier not in ids and isinstance(version, str)
                 and 0 < len(version) <= 128, "runtime_component_identity")
        ids.add(identifier)
        _require(isinstance(origin, str) and len(origin) <= 2048, "runtime_component_origin")
        try:
            parsed = urlsplit(origin)
        except ValueError:
            raise BundledRuntimeError("runtime_component_origin") from None
        _require(parsed.scheme == "https" and bool(parsed.hostname) and not parsed.username
                 and not parsed.password and not parsed.fragment and not parsed.query,
                 "runtime_component_origin")
        _require(isinstance(component["archive_sha256"], str)
                 and _SHA.fullmatch(component["archive_sha256"]) is not None,
                 "runtime_component_hash")
        licenses = component["license_paths"]
        _require(type(licenses) is list and 1 <= len(licenses) <= 256
                 and all(isinstance(p, str) for p in licenses)
                 and len(set(licenses)) == len(licenses)
                 and all(relative_path(p) in paths and by_path[p]["bytes"] > 0
                         for p in licenses), "runtime_license_missing")
    _require({"python", "codex", "jarvis-core"} <= ids, "runtime_component_missing")
    return value


def _identity(info: os.stat_result) -> tuple:
    return (info.st_dev, info.st_ino, info.st_mode, info.st_size, info.st_nlink,
            info.st_uid, info.st_mtime_ns, info.st_ctime_ns)


@dataclass(frozen=True)
class _Read:
    payload: bytes
    digest: str
    size: int
    mode: int
    prefix: bytes


def _read_at(root: int, name: str, *, limit: int, capture: bool = False) -> _Read:
    parts = relative_path(name).split("/")
    parent = os.dup(root)
    try:
        for part in parts[:-1]:
            child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent)
            os.close(parent)
            parent = child
        fd = os.open(parts[-1], os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent)
        try:
            before = os.fstat(fd)
            _require(stat.S_ISREG(before.st_mode) and before.st_nlink == 1
                     and before.st_size <= limit and not before.st_mode & 0o022,
                     "runtime_file_unsafe")
            data, prefix, digest, size = bytearray(), bytearray(), hashlib.sha256(), 0
            while True:
                block = os.read(fd, min(512 * 1024, limit + 1 - size))
                if not block:
                    break
                size += len(block)
                _require(size <= limit, "runtime_file_limit")
                digest.update(block)
                if capture:
                    data.extend(block)
                if len(prefix) < 16:
                    prefix.extend(block[:16 - len(prefix)])
            named = os.stat(parts[-1], dir_fd=parent, follow_symlinks=False)
            _require(_identity(before) == _identity(os.fstat(fd)) == _identity(named),
                     "runtime_file_changed")
            return _Read(bytes(data), digest.hexdigest(), size, before.st_mode, bytes(prefix))
        finally:
            os.close(fd)
    finally:
        os.close(parent)


@dataclass(frozen=True)
class VerifiedBundledRuntime:
    root: Path
    manifest_sha256: str
    source_commit: str
    files: int
    bytes: int
    entrypoints: tuple[tuple[str, str], ...]

    def path(self, role: str) -> Path:
        try:
            relative = dict(self.entrypoints)[role]
        except KeyError:
            raise BundledRuntimeError("runtime_entrypoint_missing") from None
        return self.root / relative

    def read_file(self, relative: str, *, limit: int) -> bytes:
        """Re-pin the manifest and exact regular member immediately before use."""
        relative = relative_path(relative)
        _require(type(limit) is int and 0 < limit <= MAX_FILE_BYTES,
                 "runtime_read_limit")
        try:
            _require(self.root.resolve(strict=True) == self.root, "runtime_root_invalid")
            descriptor = os.open(self.root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
            try:
                raw = _read_at(descriptor, MANIFEST_NAME, limit=MAX_MANIFEST_BYTES,
                               capture=True).payload
                manifest = parse_manifest(raw, expected_sha256=self.manifest_sha256,
                                          expected_source_commit=self.source_commit)
                row = next((row for row in manifest["files"] if row["path"] == relative), None)
                _require(row is not None and row["bytes"] <= limit, "runtime_read_limit")
                result = _read_at(descriptor, relative, limit=row["bytes"], capture=True)
                _require(result.size == row["bytes"] and result.digest == row["sha256"],
                         "runtime_file_hash")
                return result.payload
            finally:
                os.close(descriptor)
        except BundledRuntimeError:
            raise
        except (OSError, ValueError, TypeError):
            raise BundledRuntimeError("runtime_tree_unavailable") from None


def verify_runtime_tree(root: Path, *, expected_manifest_sha256: str,
                        expected_source_commit: str | None = None) -> VerifiedBundledRuntime:
    """Verify only this explicit tree. No search, execution, repair, or fallback."""
    root = Path(root)
    try:
        _require(root.is_absolute() and root.resolve(strict=True) == root,
                 "runtime_root_invalid")
        before = root.lstat()
        _require(stat.S_ISDIR(before.st_mode) and not before.st_mode & 0o022,
                 "runtime_root_invalid")
        fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            raw = _read_at(fd, MANIFEST_NAME, limit=MAX_MANIFEST_BYTES, capture=True).payload
            manifest = parse_manifest(raw, expected_sha256=expected_manifest_sha256,
                                      expected_source_commit=expected_source_commit)
            expected = {row["path"] for row in manifest["files"]} | {MANIFEST_NAME}
            actual, actual_directories, count = set(), set(), 0
            for parent, directories, files in os.walk(root, followlinks=False):
                count += len(directories) + len(files)
                _require(count <= MAX_FILES * 4, "runtime_tree_limit")
                for name in directories:
                    directory = Path(parent) / name
                    current = directory.lstat()
                    _require(stat.S_ISDIR(current.st_mode) and not current.st_mode & 0o022,
                             "runtime_directory_unsafe")
                    actual_directories.add(directory.relative_to(root).as_posix())
                actual.update((Path(parent) / name).relative_to(root).as_posix() for name in files)
            _require(actual == expected, "runtime_tree_mismatch")
            expected_directories = {parent.as_posix() for name in expected
                                    for parent in PurePosixPath(name).parents
                                    if parent != PurePosixPath(".")}
            _require(actual_directories == expected_directories, "runtime_tree_mismatch")
            total = 0
            for row in manifest["files"]:
                read = _read_at(fd, row["path"], limit=row["bytes"])
                _require(read.size == row["bytes"] and read.digest == row["sha256"],
                         "runtime_file_hash")
                _require(bool(read.mode & 0o111) == row["executable"], "runtime_file_mode")
                if row["path"] in (ENTRYPOINTS["python"], ENTRYPOINTS["codex"]):
                    _require(len(read.prefix) >= 16
                             and struct.unpack("<IIII", read.prefix) ==
                             (0xFEEDFACF, 0x0100000C, 0, 2), "runtime_executable_architecture")
                total += read.size
            _require(_identity(before) == _identity(root.lstat()), "runtime_root_changed")
            return VerifiedBundledRuntime(root, expected_manifest_sha256,
                                          manifest["source_commit"], len(manifest["files"]), total,
                                          tuple(sorted(manifest["entrypoints"].items())))
        finally:
            os.close(fd)
    except BundledRuntimeError:
        raise
    except (OSError, ValueError, TypeError):
        raise BundledRuntimeError("runtime_tree_unavailable") from None
