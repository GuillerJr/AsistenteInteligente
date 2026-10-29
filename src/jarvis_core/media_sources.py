"""Descriptor-only reads of one explicitly chosen workspace media source."""

from __future__ import annotations

import hashlib
import os
import re
import stat
from dataclasses import dataclass, field

from .media_documents import MediaError, MediaLimits, MediaType, checkpoint, sniff_media
from .workspace import WorkspaceTools, _fingerprint, _parts


@dataclass(frozen=True)
class MediaSource:
    name: str
    relative_path: str
    sha256: str
    media_type: MediaType
    payload: bytes = field(repr=False)


def read_media_source(
    workspace, path, expected_sha256, *, limits=None, cancellation=None, deadline=None, guard=None
):
    if not isinstance(workspace, WorkspaceTools):
        raise MediaError("media_workspace_invalid")
    if (
        not isinstance(expected_sha256, str)
        or re.fullmatch(r"[a-f0-9]{64}", expected_sha256) is None
    ):
        raise MediaError("media_digest_invalid")
    limits = limits or MediaLimits()
    parts = _parts(path)
    if any(p.casefold() == ".jarvis-attachments" for p in parts):
        raise MediaError("media_source_private")

    def current():
        checkpoint(cancellation, deadline)
        if guard is not None:
            guard()

    current()
    with workspace._parent(parts[:-1]) as parent:
        fd = os.open(parts[-1], os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent)
        try:
            before = os.fstat(fd)
            if (
                not stat.S_ISREG(before.st_mode)
                or before.st_uid != os.getuid()
                or before.st_nlink != 1
            ):
                raise MediaError("media_source_unsafe")
            if not 1 <= before.st_size <= limits.max_file_bytes:
                raise MediaError("media_file_limit")
            chunks, size, digest = [], 0, hashlib.sha256()
            while True:
                current()
                block = os.read(fd, min(65536, limits.max_file_bytes + 1 - size))
                if not block:
                    break
                size += len(block)
                if size > limits.max_file_bytes:
                    raise MediaError("media_file_limit")
                chunks.append(block)
                digest.update(block)
            after = os.fstat(fd)
            final = os.stat(parts[-1], dir_fd=parent, follow_symlinks=False)
            workspace._same_parent(parts[:-1], parent)
            if _fingerprint(before) != _fingerprint(after) or _fingerprint(after) != _fingerprint(
                final
            ):
                raise MediaError("media_source_changed")
            if digest.hexdigest() != expected_sha256:
                raise MediaError("media_source_changed")
            current()
            payload = b"".join(chunks)
            media_type = sniff_media(payload, parts[-1], limits=limits)
            return MediaSource(parts[-1], path, digest.hexdigest(), media_type, payload)
        finally:
            os.close(fd)
