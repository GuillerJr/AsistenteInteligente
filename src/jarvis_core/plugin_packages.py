"""Bounded data-only Jarvis extension archives; never extract or execute them."""

from __future__ import annotations

import base64
import hashlib
import io
import json
import math
import os
import re
import stat
import tarfile
import time
import urllib.parse
import zipfile
from dataclasses import dataclass
from pathlib import Path

from jarvis_core.skills import SkillError, SkillsStore, _parse, _resource, _text

MAX_ARCHIVE_BYTES = 4 * 1024 * 1024
MAX_CONTENT_BYTES = 512 * 1024
MAX_FILE_BYTES = 128 * 1024
MAX_FILES = 64
MANIFEST_NAME = "jarvis-extension.json"
EVENTS = frozenset({"install", "update", "rollback", "enable", "disable", "remove",
                    "session_start", "session_end", "before_turn", "after_turn"})
_IDENTITY = re.compile(r"[a-z][a-z0-9_.-]{0,63}\Z")
_HASH = re.compile(r"[a-f0-9]{64}\Z")
_VERSION = re.compile(r"(?:0|[1-9][0-9]{0,5})(?:\.(?:0|[1-9][0-9]{0,5})){2}"
                      r"(?:-[a-zA-Z0-9]+(?:[.-][a-zA-Z0-9]+)*)?\Z")


class PluginError(ValueError):
    def __init__(self, code, *, receipt=None):
        self.code, self.receipt = code, receipt
        super().__init__(code)


def canonical(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False,
                      separators=(",", ":"), allow_nan=False).encode()


def checkpoint(cancellation=None, deadline=None, assert_current=lambda: None):
    if cancellation is not None and cancellation.is_set():
        raise PluginError("plugin_cancelled")
    if deadline is not None and (type(deadline) not in {int, float}
            or not math.isfinite(deadline) or time.monotonic() >= deadline):
        raise PluginError("plugin_deadline")
    assert_current()


def _object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise PluginError("plugin_duplicate_key")
        result[key] = value
    return result


def identity(value):
    if not isinstance(value, str) or not _IDENTITY.fullmatch(value):
        raise PluginError("plugin_identity_invalid")
    return value


def version(value):
    if not isinstance(value, str) or len(value) > 80 or not _VERSION.fullmatch(value):
        raise PluginError("plugin_version_invalid")
    return value


def digest(value):
    if not isinstance(value, str) or not _HASH.fullmatch(value):
        raise PluginError("plugin_digest_invalid")
    return value


def _keys(value, expected):
    if not isinstance(value, dict) or set(value) != set(expected):
        raise PluginError("plugin_manifest_invalid")


def _path(value):
    try:
        return _resource(value)
    except SkillError:
        raise PluginError("plugin_path_invalid") from None


def _public_uri(value):
    if not isinstance(value, str) or len(value.encode()) > 2048:
        raise PluginError("plugin_provenance_invalid")
    parsed = urllib.parse.urlsplit(value)
    if (parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password
            or parsed.query or parsed.fragment or any(c.isspace() for c in value)):
        raise PluginError("plugin_provenance_invalid")
    return value


def _mcp_descriptor(row):
    if (not isinstance(row, dict) or not isinstance(row.get("transport"), str)
            or row["transport"] not in {"stdio", "streamable_http", "sse"}):
        raise PluginError("plugin_mcp_invalid")
    common = {"server_id", "transport", "protocol_version"}
    expected = common | ({"command"} if row["transport"] == "stdio"
                         else {"endpoint", "allow_loopback"})
    if "oauth" in row and row["transport"] != "stdio":
        expected.add("oauth")
    _keys(row, expected)
    if (not isinstance(row["server_id"], str)
            or not re.fullmatch(r"[a-z][a-z0-9_]{0,23}", row["server_id"])
            or not isinstance(row["protocol_version"], str)
            or row["protocol_version"] not in {"2025-11-25", "2026-07-28", "2024-11-05"}):
        raise PluginError("plugin_mcp_invalid")
    if row["transport"] == "stdio":
        command = row["command"]
        if (not isinstance(command, list) or not 1 <= len(command) <= 32
                or any(not isinstance(arg, str) or not arg or "\0" in arg
                       or len(arg.encode()) > 1024 for arg in command)
                or not Path(command[0]).is_absolute()
                or Path(command[0]).name in {"sh", "bash", "zsh", "dash", "fish", "csh"}):
            raise PluginError("plugin_mcp_invalid")
    else:
        _public_uri(row["endpoint"])
        if row["allow_loopback"] is not False:
            raise PluginError("plugin_mcp_invalid")
        if "oauth" in row:
            oauth = row["oauth"]
            _keys(oauth, {"resource", "client_id", "scopes"})
            _public_uri(oauth["resource"])
            if (not isinstance(oauth["client_id"], str) or not oauth["client_id"]
                    or len(oauth["client_id"].encode()) > 1024
                    or not isinstance(oauth["scopes"], list) or len(oauth["scopes"]) > 16
                    or any(not isinstance(scope, str) or not scope or len(scope) > 128
                           or not re.fullmatch(r"[A-Za-z0-9_:.\-/]+", scope)
                           for scope in oauth["scopes"])
                    or len(set(oauth["scopes"])) != len(oauth["scopes"])):
                raise PluginError("plugin_mcp_invalid")


def _hook_arguments(value, depth=0, budget=None):
    if budget is None:
        budget = [0]
    budget[0] += 1
    if depth > 6 or budget[0] > 256:
        raise PluginError("plugin_hook_invalid")
    if value is None or type(value) is bool:
        return
    if type(value) in {int, float}:
        if not math.isfinite(value) or abs(value) > 2**53:
            raise PluginError("plugin_hook_invalid")
        return
    if isinstance(value, str):
        if "\0" in value or len(value.encode()) > 2048:
            raise PluginError("plugin_hook_invalid")
        return
    if isinstance(value, list) and len(value) <= 32:
        for item in value:
            _hook_arguments(item, depth + 1, budget)
        return
    if isinstance(value, dict) and len(value) <= 32:
        for key, item in value.items():
            if not isinstance(key, str) or not key or len(key.encode()) > 128:
                raise PluginError("plugin_hook_invalid")
            _hook_arguments(item, depth + 1, budget)
        return
    raise PluginError("plugin_hook_invalid")


def validate_manifest(value):
    _keys(value, {"format", "package_id", "name", "description", "version", "publisher",
                  "source_uri", "files", "skills", "prompts", "hooks", "mcp"})
    if value["format"] != "jarvis-extension/1":
        raise PluginError("plugin_format_invalid")
    identity(value["package_id"])
    version(value["version"])
    try:
        for key, limit in (("name", 256), ("description", 2048), ("publisher", 256)):
            _text(value[key], limit)
    except SkillError:
        raise PluginError("plugin_manifest_invalid") from None
    _public_uri(value["source_uri"])
    files = value["files"]
    if not isinstance(files, list) or not 1 <= len(files) <= MAX_FILES:
        raise PluginError("plugin_file_limit")
    seen, total = set(), 0
    for row in files:
        _keys(row, {"path", "bytes", "sha256", "media_type"})
        path = _path(row["path"])
        if (path in seen or path.casefold() in {x.casefold() for x in seen}
                or path == MANIFEST_NAME or type(row["bytes"]) is not int
                or not 0 <= row["bytes"] <= MAX_FILE_BYTES
                or not isinstance(row["media_type"], str)
                or row["media_type"] not in {"text/plain", "text/markdown", "application/json",
                                               "application/octet-stream"}):
            raise PluginError("plugin_file_invalid")
        digest(row["sha256"])
        seen.add(path)
        total += row["bytes"]
    if total > MAX_CONTENT_BYTES:
        raise PluginError("plugin_content_limit")
    for kind, key, limit in (("skills", "skill_id", 16), ("prompts", "prompt_id", 16)):
        entries = value[kind]
        if not isinstance(entries, list) or len(entries) > limit:
            raise PluginError("plugin_contribution_limit")
        ids = set()
        for row in entries:
            _keys(row, {key, "path"})
            identity(row[key])
            if row[key] in ids or not isinstance(row["path"], str) or row["path"] not in seen:
                raise PluginError("plugin_contribution_invalid")
            if kind == "skills" and not row["path"].endswith("/SKILL.md"):
                raise PluginError("plugin_contribution_invalid")
            ids.add(row[key])
    if not isinstance(value["hooks"], list) or len(value["hooks"]) > 16:
        raise PluginError("plugin_hook_invalid")
    prompts = {row["prompt_id"] for row in value["prompts"]}
    descriptors = set()
    if isinstance(value["mcp"], list):
        descriptors = {row["server_id"] for row in value["mcp"] if isinstance(row, dict)
                       and isinstance(row.get("server_id"), str)}
    hook_ids = set()
    for row in value["hooks"]:
        if not isinstance(row, dict):
            raise PluginError("plugin_hook_invalid")
        if "prompt_id" in row:
            _keys(row, {"event", "prompt_id"})
            if not isinstance(row["prompt_id"], str) or row["prompt_id"] not in prompts:
                raise PluginError("plugin_hook_invalid")
        else:
            _keys(row, {"event", "mcp_server_id", "tool", "arguments"})
            if (not isinstance(row["mcp_server_id"], str) or row["mcp_server_id"] not in descriptors
                    or not isinstance(row["tool"], str)
                    or not re.fullmatch(r"[A-Za-z][A-Za-z0-9_.-]{0,63}", row["tool"])
                    or not isinstance(row["arguments"], dict)):
                raise PluginError("plugin_hook_invalid")
            _hook_arguments(row["arguments"])
        pair = canonical(row)
        if not isinstance(row["event"], str) or row["event"] not in EVENTS or pair in hook_ids:
            raise PluginError("plugin_hook_invalid")
        hook_ids.add(pair)
    if not isinstance(value["mcp"], list) or len(value["mcp"]) > 8:
        raise PluginError("plugin_mcp_invalid")
    mcp_ids = set()
    for row in value["mcp"]:
        _mcp_descriptor(row)
        if row["server_id"] in mcp_ids:
            raise PluginError("plugin_mcp_invalid")
        mcp_ids.add(row["server_id"])


@dataclass(frozen=True)
class PackagePreview:
    manifest_json: str
    files: tuple[tuple[str, str], ...]
    sha256: str
    snapshot_sha256: str
    source_path: str
    source_identity: tuple[int, int]
    archive_bytes: int

    def snapshot(self):
        return {"manifest": json.loads(self.manifest_json), "files": dict(self.files),
                "sha256": self.sha256, "snapshot_sha256": self.snapshot_sha256,
                "source_path": self.source_path, "archive_bytes": self.archive_bytes}

    def public(self):
        manifest = json.loads(self.manifest_json)
        return {**{key: manifest[key] for key in ("package_id", "name", "description", "version",
                                                 "publisher", "source_uri", "files", "skills",
                                                 "prompts", "hooks", "mcp")},
                "sha256": self.sha256, "snapshot_sha256": self.snapshot_sha256,
                "archive_bytes": self.archive_bytes, "source_verified": False}


def validate_snapshot(value):
    _keys(value, {"manifest", "files", "sha256", "snapshot_sha256", "source_path",
                  "archive_bytes"})
    validate_manifest(value["manifest"])
    digest(value["sha256"])
    digest(value["snapshot_sha256"])
    if (type(value["archive_bytes"]) is not int
            or not 1 <= value["archive_bytes"] <= MAX_ARCHIVE_BYTES
            or not isinstance(value["source_path"], str)
            or not Path(value["source_path"]).is_absolute()
            or len(value["source_path"].encode()) > 4096
            or not isinstance(value["files"], dict)
            or set(value["files"]) != {row["path"] for row in value["manifest"]["files"]}):
        raise PluginError("plugin_snapshot_invalid")
    for row in value["manifest"]["files"]:
        try:
            raw = base64.b64decode(value["files"][row["path"]], validate=True)
        except (ValueError, TypeError):
            raise PluginError("plugin_snapshot_invalid") from None
        if len(raw) != row["bytes"] or hashlib.sha256(raw).hexdigest() != row["sha256"]:
            raise PluginError("plugin_content_changed")
        if row["media_type"].startswith("text/") or row["media_type"] == "application/json":
            try:
                text = raw.decode("utf-8")
                if "\0" in text:
                    raise ValueError
            except (ValueError, UnicodeError):
                raise PluginError("plugin_text_invalid") from None
    actual = hashlib.sha256(canonical({"manifest": value["manifest"],
                                      "files": value["files"]})).hexdigest()
    if actual != value["snapshot_sha256"]:
        raise PluginError("plugin_snapshot_changed")
    for row in value["manifest"]["skills"]:
        try:
            raw = base64.b64decode(value["files"][row["path"]], validate=True)
            _, _, _, resources = _parse(raw.decode())
        except (SkillError, UnicodeError, ValueError):
            raise PluginError("plugin_skill_invalid") from None
        parent = row["path"].rsplit("/", 1)[0]
        if any(parent + "/" + path not in value["files"] for path in resources):
            raise PluginError("plugin_skill_resource_missing")
    for row in value["manifest"]["prompts"]:
        try:
            raw = base64.b64decode(value["files"][row["path"]], validate=True)
            _text(raw.decode(), 8192)
        except (SkillError, UnicodeError, ValueError):
            raise PluginError("plugin_prompt_invalid") from None


def _archive_entries(raw, cancellation, deadline):
    entries, total = {}, 0

    def accept(name, size, compressed, mode, read):
        nonlocal total
        checkpoint(cancellation, deadline)
        _path(name)
        if (len(entries) >= MAX_FILES + 1 or name in entries
                or name.casefold() in {x.casefold() for x in entries}
                or not 0 <= size <= MAX_FILE_BYTES or size > max(compressed, 1) * 100
                or (mode and not stat.S_ISREG(mode)) or mode & 0o111):
            raise PluginError("plugin_archive_entry_invalid")
        total += size
        if total > MAX_CONTENT_BYTES + 32768:
            raise PluginError("plugin_content_limit")
        data = read(size + 1)
        if len(data) != size:
            raise PluginError("plugin_archive_changed")
        entries[name] = data

    stream = io.BytesIO(raw)
    is_zip = zipfile.is_zipfile(stream)
    # is_zipfile probes the central directory and leaves this shared BytesIO
    # at its probe offset. TAR must start at byte zero of the reviewed archive.
    stream.seek(0)
    if is_zip:
        with zipfile.ZipFile(stream) as archive:
            if len(archive.infolist()) > MAX_FILES + 1:
                raise PluginError("plugin_file_limit")
            for entry in archive.infolist():
                if (entry.is_dir() or entry.flag_bits & 1
                        or entry.compress_type not in {zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED}):
                    raise PluginError("plugin_archive_entry_invalid")
                mode = entry.external_attr >> 16
                # Some ZIP writers store permissions without the regular type.
                if mode and stat.S_IFMT(mode) == 0:
                    mode |= stat.S_IFREG
                with archive.open(entry) as source:
                    accept(entry.filename, entry.file_size, entry.compress_size, mode, source.read)
    else:
        # Compressed tar is deliberately rejected: ZIP supplies bounded archive
        # compression. Plain tar's entire metadata stream already fits our cap.
        with tarfile.open(fileobj=stream, mode="r:") as archive:
            for entry in archive:
                if not entry.isfile() or entry.pax_headers or entry.mode & 0o111:
                    raise PluginError("plugin_archive_entry_invalid")
                source = archive.extractfile(entry)
                if source is None:
                    raise PluginError("plugin_archive_entry_invalid")
                with source:
                    accept(entry.name, entry.size, len(raw), stat.S_IFREG | entry.mode, source.read)
        if total > max(len(raw), 1) * 100:
            raise PluginError("plugin_content_limit")
    return entries


def inspect_package(path, *, cancellation=None, deadline=None):
    checkpoint(cancellation, deadline)
    try:
        path = Path(path)
        if not path.is_absolute() or ".." in path.parts or len(str(path).encode()) > 4096:
            raise PluginError("plugin_source_invalid")
        _path(path.name)
        parent = SkillsStore._open_source_directory(path.parent)
        try:
            descriptor = os.open(path.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK,
                                 dir_fd=parent)
            try:
                opened = os.fstat(descriptor)
                if (not stat.S_ISREG(opened.st_mode) or opened.st_nlink != 1
                        or opened.st_uid != os.getuid() or opened.st_mode & 0o022
                        or not 1 <= opened.st_size <= MAX_ARCHIVE_BYTES):
                    raise PluginError("plugin_source_invalid")
                raw = bytearray()
                while part := os.read(descriptor, min(65536, MAX_ARCHIVE_BYTES + 1 - len(raw))):
                    checkpoint(cancellation, deadline)
                    raw.extend(part)
                    if len(raw) > MAX_ARCHIVE_BYTES:
                        raise PluginError("plugin_content_limit")
                after = os.fstat(descriptor)
                linked = os.stat(path.name, dir_fd=parent, follow_symlinks=False)
                def fields(s):
                    return s.st_dev, s.st_ino, s.st_size, s.st_mtime_ns, s.st_ctime_ns

                if fields(opened) != fields(after) or fields(after) != fields(linked):
                    raise PluginError("plugin_source_changed")
                reopened = SkillsStore._open_source_directory(path.parent)
                try:
                    old_parent, new_parent = os.fstat(parent), os.fstat(reopened)
                    if ((old_parent.st_dev, old_parent.st_ino)
                            != (new_parent.st_dev, new_parent.st_ino)):
                        raise PluginError("plugin_source_changed")
                finally:
                    os.close(reopened)
            finally:
                os.close(descriptor)
        finally:
            os.close(parent)
        entries = _archive_entries(bytes(raw), cancellation, deadline)
        manifest_raw = entries.pop(MANIFEST_NAME, None)
        if manifest_raw is None or len(manifest_raw) > 32768:
            raise PluginError("plugin_manifest_missing")
        manifest = json.loads(manifest_raw, object_pairs_hook=_object)
        files = {key: base64.b64encode(item).decode() for key, item in entries.items()}
        snapshot_hash = hashlib.sha256(canonical({"manifest": manifest,
                                                  "files": files})).hexdigest()
        preview = PackagePreview(canonical(manifest).decode(), tuple(sorted(files.items())),
                                 hashlib.sha256(raw).hexdigest(), snapshot_hash, str(path),
                                 (opened.st_dev, opened.st_ino), len(raw))
        validate_snapshot(preview.snapshot())
        checkpoint(cancellation, deadline)
        return preview
    except PluginError:
        raise
    except (OSError, SkillError, ValueError, TypeError, UnicodeError, RecursionError,
            tarfile.TarError, zipfile.BadZipFile, RuntimeError):
        raise PluginError("plugin_package_invalid") from None
