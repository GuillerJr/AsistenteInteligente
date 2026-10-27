"""Strict unified text patches against exact workspace snapshots.

Headers and hunk coordinates are data, never shell commands. Publication is atomic
per ordinary file, not as a multi-file transaction. A partial operation is retained
as uncertain and is never automatically repeated or rolled back over newer data.
"""
from __future__ import annotations

import asyncio
import hashlib
import os
import re
from collections.abc import Mapping
from contextlib import ExitStack
from dataclasses import dataclass
from uuid import uuid4

from .runtime import ToolResult
from .tools import ToolError, check_tool_authority, revalidate_committed_tool_authority
from .workspace import WorkspaceError, _fingerprint, _json, _parts

_HEADER = re.compile(r"@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@(?:[^\r\n]*)\n?\Z")
_HASH = re.compile(r"[a-f0-9]{64}\Z")


@dataclass(frozen=True)
class FilePatch:
    source: str | None
    destination: str | None
    hunks: tuple[tuple[int, int, int, int, tuple[str, ...]], ...]


def _path(line: str, prefix: str) -> str | None:
    if not line.startswith(prefix):
        raise WorkspaceError("workspace_patch_header_invalid")
    name = line[len(prefix):].rstrip("\r\n").split("\t", 1)[0]
    if name == "/dev/null":
        return None
    if name.startswith(("a/", "b/")):
        name = name[2:]
    if any(ord(char) < 32 for char in name):
        raise WorkspaceError("workspace_patch_path_invalid")
    _parts(name)
    return name


def parse_patch(text: str) -> tuple[FilePatch, ...]:
    if not isinstance(text, str) or not 1 <= len(text.encode()) <= 96000 or "\0" in text:
        raise WorkspaceError("workspace_patch_limit")
    lines = text.splitlines(keepends=True)
    result, offset, used = [], 0, set()
    while offset < len(lines):
        if lines[offset].startswith("diff --git "):
            offset += 1  # Only ---/+++ paths and exact hashes confer authority.
        if offset + 1 >= len(lines):
            raise WorkspaceError("workspace_patch_header_invalid")
        source, destination = _path(lines[offset], "--- "), _path(lines[offset + 1], "+++ ")
        offset += 2
        paths = {p for p in (source, destination) if p is not None}
        if not paths or used & paths:
            raise WorkspaceError("workspace_patch_path_conflict")
        used.update(paths)
        if sum(len(path.encode()) for path in used) > 8192:
            raise WorkspaceError("workspace_patch_limit")
        hunks = []
        while offset < len(lines) and lines[offset].startswith("@@ "):
            match = _HEADER.fullmatch(lines[offset])
            if match is None:
                raise WorkspaceError("workspace_patch_hunk_invalid")
            a, ac, b, bc = (int(value) if value is not None else 1 for value in match.groups())
            if min(a, ac, b, bc) < 0 or max(a, ac, b, bc) > 96000:
                raise WorkspaceError("workspace_patch_hunk_invalid")
            offset += 1
            body, old_count, new_count = [], 0, 0
            while offset < len(lines):
                line = lines[offset]
                if line.startswith("\\ No newline at end of file"):
                    if not body or not body[-1].endswith("\n"):
                        raise WorkspaceError("workspace_patch_hunk_invalid")
                    body[-1] = body[-1][:-1]
                    offset += 1
                    continue
                if old_count == ac and new_count == bc:
                    break
                if not line or line[0] not in " +-":
                    raise WorkspaceError("workspace_patch_hunk_invalid")
                old_count += line[0] in " -"
                new_count += line[0] in " +"
                if old_count > ac or new_count > bc:
                    raise WorkspaceError("workspace_patch_hunk_invalid")
                body.append(line)
                offset += 1
            if old_count != ac or new_count != bc or not body:
                raise WorkspaceError("workspace_patch_hunk_invalid")
            hunks.append((a, ac, b, bc, tuple(body)))
        if not hunks and source == destination:
            raise WorkspaceError("workspace_patch_empty")
        result.append(FilePatch(source, destination, tuple(hunks)))
        if len(result) > 16:
            raise WorkspaceError("workspace_patch_limit")
    return tuple(result)


def validate_patch(arguments: Mapping) -> tuple[FilePatch, ...]:
    if not isinstance(arguments, Mapping) or set(arguments) != {"patch", "expected_sha256"}:
        raise WorkspaceError("workspace_patch_arguments_invalid")
    patches = parse_patch(arguments["patch"])
    expected = arguments["expected_sha256"]
    paths = {p for patch in patches for p in (patch.source, patch.destination) if p is not None}
    if not isinstance(expected, Mapping) or set(expected) != paths:
        raise WorkspaceError("workspace_patch_versions_required")
    for patch in patches:
        if patch.source is not None and (
            not isinstance(expected[patch.source], str)
            or not _HASH.fullmatch(expected[patch.source])
        ):
            raise WorkspaceError("workspace_patch_versions_required")
        if patch.destination is not None and patch.destination != patch.source:
            if expected[patch.destination] is not None:
                raise WorkspaceError("workspace_patch_destination_must_be_new")
    return patches


def apply_hunks(original: bytes, patch: FilePatch, limit: int) -> bytes:
    lines = original.decode("utf-8").splitlines(keepends=True)
    output, cursor = [], 0
    for a, ac, b, _bc, body in patch.hunks:
        start = a - 1 if ac else a
        if start < cursor or start > len(lines):
            raise WorkspaceError("workspace_patch_context_mismatch")
        output.extend(lines[cursor:start])
        new_start = b - 1 if _bc else b
        if new_start != len(output):
            raise WorkspaceError("workspace_patch_coordinates_mismatch")
        cursor = start
        for line in body:
            kind, content = line[0], line[1:]
            if kind in " -":
                if cursor >= len(lines) or lines[cursor] != content:
                    raise WorkspaceError("workspace_patch_context_mismatch")
                cursor += 1
            if kind in " +":
                output.append(content)
    output.extend(lines[cursor:])
    result = "".join(output).encode()
    if len(result) > limit:
        raise WorkspaceError("workspace_file_limit")
    if patch.destination is None and result:
        raise WorkspaceError("workspace_patch_delete_not_empty")
    return result


def execute_patch(workspace, arguments: Mapping, cancellation: asyncio.Event) -> ToolResult:
    name, changed, completed = "workspace_patch", False, []
    current_file = None
    staged = []
    stack = ExitStack()
    cleanup_uncertain = False
    try:
        patches = validate_patch(arguments)
        check_tool_authority(name, arguments)
        if cancellation.is_set():
            raise WorkspaceError("workspace_cancelled")
        plans = []
        for patch in patches:
            check_tool_authority(name, arguments)
            if cancellation.is_set():
                raise WorkspaceError("workspace_cancelled")
            source = destination = None
            original, before, mode = b"", None, 0o600
            if patch.source:
                parts = _parts(patch.source)
                parent = stack.enter_context(workspace._parent(parts[:-1]))
                workspace._target(parent, parts[-1], arguments["expected_sha256"][patch.source])
                original, before = workspace._read(parent, parts[-1])
                mode = before.st_mode & 0o777
                source = parts, parent
            if patch.destination:
                parts = _parts(patch.destination)
                parent = stack.enter_context(workspace._parent(parts[:-1]))
                workspace._target(parent, parts[-1],
                                  arguments["expected_sha256"][patch.destination])
                destination = parts, parent
            result = apply_hunks(original, patch, workspace.max_file_bytes)
            stage = identity = None
            if destination:
                stage = ".jarvis-tmp-" + uuid4().hex
                fd = os.open(stage, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                             0o600, dir_fd=destination[1])
                staged.append((destination[1], stage))
                try:
                    view = memoryview(result)
                    while view:
                        count = os.write(fd, view)
                        if count <= 0:
                            raise OSError("short patch stage write")
                        view = view[count:]
                    os.fchmod(fd, mode)
                    os.fsync(fd)
                    identity = os.fstat(fd)
                finally:
                    os.close(fd)
            plans.append((patch, source, destination, original, before, result, stage, identity))
        # Reject every stale snapshot before the first external publication.
        def verify(plan):
            patch, source, destination, original, before, *_ = plan
            for path, endpoint in ((patch.source, source), (patch.destination, destination)):
                if endpoint:
                    parts, parent = endpoint
                    workspace._same_parent(parts[:-1], parent)
                    workspace._target(parent, parts[-1], arguments["expected_sha256"][path])
            if source:
                actual, current = workspace._read(source[1], source[0][-1])
                if actual != original or _fingerprint(current) != _fingerprint(before):
                    raise WorkspaceError("workspace_version_conflict")
        for plan in plans:
            check_tool_authority(name, arguments)
            verify(plan)
        for plan in plans:
            patch, source, destination, original, _before, result, stage, identity = plan
            if changed:
                revalidate_committed_tool_authority(name, arguments)
            else:
                check_tool_authority(name, arguments)
            verify(plan)
            if cancellation.is_set():
                raise WorkspaceError("workspace_cancelled")
            if changed:
                revalidate_committed_tool_authority(name, arguments)
            else:
                check_tool_authority(name, arguments, commit=True)
            current_file = {"source": patch.source, "destination": patch.destination,
                            "phase": "publication_started"}
            changed = True  # A failing syscall can still leave an uncertain effect.
            if destination:
                parent, leaf = destination[1], destination[0][-1]
                if patch.source == patch.destination:
                    os.replace(stage, leaf, src_dir_fd=parent, dst_dir_fd=parent)
                else:
                    os.link(stage, leaf, src_dir_fd=parent, dst_dir_fd=parent,
                            follow_symlinks=False)
                    os.unlink(stage, dir_fd=parent)
                staged.remove((parent, stage))
                os.fsync(parent)
                actual, info = workspace._read(parent, leaf)
                if actual != result or (info.st_dev, info.st_ino) != (
                    identity.st_dev, identity.st_ino
                ):
                    raise WorkspaceError("workspace_patch_verification_failed")
                current_file["phase"] = "destination_verified"
            if source and patch.source != patch.destination:
                revalidate_committed_tool_authority(name, arguments)
                if cancellation.is_set():
                    raise WorkspaceError("workspace_cancelled")
                parts, parent = source
                workspace._same_parent(parts[:-1], parent)
                current, info = workspace._read(parent, parts[-1])
                if current != original or _fingerprint(info) != _fingerprint(_before):
                    raise WorkspaceError("workspace_version_conflict")
                current_file["phase"] = "source_removal_started"
                os.unlink(parts[-1], dir_fd=parent)
                os.fsync(parent)
                try:
                    os.stat(parts[-1], dir_fd=parent, follow_symlinks=False)
                except FileNotFoundError:
                    pass
                else:
                    raise WorkspaceError("workspace_patch_verification_failed")
            completed.append({
                "source": patch.source, "destination": patch.destination,
                "bytes": len(result) if destination else 0,
                "sha256": hashlib.sha256(result).hexdigest() if destination else None,
            })
            current_file = None
        outcome = ToolResult(name, True, True, output=_json({
            "files": completed, "publication": "per_file", "permits_replay": False,
        }))
    except (WorkspaceError, ToolError, OSError, UnicodeError) as error:
        code = (str(error) if isinstance(error, (WorkspaceError, ToolError))
                else "workspace_unavailable")
        outcome = ToolResult(name, False, False, output=_json({
            "completed_files": completed, "publication": "per_file", "permits_replay": False,
            "uncertain_file": current_file,
        }), error=code, effect_status="unknown" if changed or cleanup_uncertain else "known")
    finally:
        # Cleanup owns every stage and descriptor until their outcome is known.
        for parent, stage in staged:
            try:
                os.unlink(stage, dir_fd=parent)
            except OSError:
                cleanup_uncertain = True
        stack.close()
    if cleanup_uncertain:
        outcome = ToolResult(name, False, False, error="workspace_patch_cleanup_uncertain",
                             output=_json({"completed_files": completed, "permits_replay": False}),
                             effect_status="unknown")
    return outcome
