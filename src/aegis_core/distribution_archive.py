"""Bounded ZIP validation shared by packaging and the update/install boundary.

Validate the complete namespace before reading payloads or extracting anything.
macOS commonly uses a case-insensitive, Unicode-normalizing filesystem; a ZIP's
case-sensitive names alone are not a safe inventory for that filesystem.
"""

from __future__ import annotations

import stat
import unicodedata
import zipfile
from pathlib import PurePosixPath

MAX_MEMBERS = 4_096
MAX_MEMBER_BYTES = 512 * 1_024 * 1_024
MAX_TOTAL_BYTES = 2 * 1_024 * 1_024 * 1_024
MAX_LINK_BYTES = 4_096
PRIVATE_COMPONENTS = frozenset(
    {
        ".env",
        ".git",
        "credentials.json",
        "aegis.db",
        "JarvisSpeakerIdentity.mlmodelc",
        "JarvisWakeWord.mlmodelc",
    }
)


class DistributionArchiveError(RuntimeError):
    """The archive cannot be extracted unambiguously inside Jarvis.app."""


def safe_member_path(name: str) -> PurePosixPath:
    raw = name[:-1] if name.endswith("/") else name
    parts = raw.split("/")
    if (
        not raw
        or len(name) > 1_024
        or parts[0] != "Jarvis.app"
        or any(part in {"", ".", ".."} for part in parts)
        or any(char in name for char in ("\\", ":"))
        or any(ord(char) < 32 or ord(char) == 127 for char in name)
    ):
        raise DistributionArchiveError("archive contains an unsafe path")
    return PurePosixPath(raw)


def _macos_key(name: str) -> str:
    return unicodedata.normalize("NFD", name).casefold()


def validate_archive_namespace(archive: zipfile.ZipFile) -> None:
    members = archive.infolist()
    if not members or len(members) > MAX_MEMBERS:
        raise DistributionArchiveError("archive member count exceeds the safety budget")
    entries: dict[str, str] = {}
    spellings: dict[str, str] = {}
    directories: set[str] = set()
    link_members: dict[str, zipfile.ZipInfo] = {}
    total = 0
    private = {_macos_key(part) for part in PRIVATE_COMPONENTS}
    for member in members:
        # ZipInfo truncates NUL-containing names; do not accept that normalization.
        if member.orig_filename != member.filename:
            raise DistributionArchiveError("archive contains an unsafe path")
        path = safe_member_path(member.filename)
        name = str(path)
        if any(_macos_key(part) in private for part in path.parts):
            raise DistributionArchiveError("archive contains a private biometric model or data")
        if name in entries:
            raise DistributionArchiveError("archive contains duplicate paths")
        if member.flag_bits & 1:
            raise DistributionArchiveError("archive contains encrypted data")
        if member.compress_type not in {zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED}:
            raise DistributionArchiveError("archive compression is unsupported")
        if member.file_size > MAX_MEMBER_BYTES:
            raise DistributionArchiveError("archive member exceeds the safety budget")
        total += member.file_size
        if total > MAX_TOTAL_BYTES:
            raise DistributionArchiveError("archive expansion exceeds the safety budget")
        mode = member.external_attr >> 16
        kind = stat.S_IFMT(mode)
        if kind not in {0, stat.S_IFREG, stat.S_IFDIR, stat.S_IFLNK} or mode & 0o7000:
            raise DistributionArchiveError("archive contains unsafe file metadata")
        if member.is_dir():
            if kind not in {0, stat.S_IFDIR} or member.file_size:
                raise DistributionArchiveError("archive directory metadata is invalid")
            entries[name] = "directory"
            directories.add(name)
        elif kind == stat.S_IFDIR:
            raise DistributionArchiveError("archive directory metadata is invalid")
        elif kind == stat.S_IFLNK:
            if not 0 < member.file_size <= MAX_LINK_BYTES:
                raise DistributionArchiveError("archive symbolic link exceeds the safety budget")
            entries[name] = "link"
            link_members[name] = member
        else:
            entries[name] = "file"
        for index in range(1, len(path.parts) + 1):
            component = "/".join(path.parts[:index])
            key = _macos_key(component)
            if key in spellings and spellings[key] != component:
                raise DistributionArchiveError("archive paths collide on macOS")
            spellings[key] = component
            if index < len(path.parts):
                directories.add(component)
    # Extraction order must never let a file or link redirect a later member.
    if any(entries.get(name, "directory") != "directory" for name in directories):
        raise DistributionArchiveError("archive member has a non-directory parent")
    if entries.get("Jarvis.app", "directory") != "directory":
        raise DistributionArchiveError("archive root is not a directory")

    links: dict[str, list[str]] = {}
    for name, member in link_members.items():
        try:
            raw = archive.read(member).decode("utf-8")
        except UnicodeError as error:
            raise DistributionArchiveError("archive symbolic link is invalid") from error
        if (
            not raw
            or raw.startswith("/")
            or "\\" in raw
            or ":" in raw
            or any(ord(char) < 32 or ord(char) == 127 for char in raw)
        ):
            raise DistributionArchiveError("archive contains an unsafe symbolic link")
        links[name] = raw.split("/")

    # Resolve component by component, including links in parent directories.
    # Lexical normalization alone misinterprets `link/..` and framework aliases.
    for source, target in links.items():
        resolved = source.split("/")[:-1]
        pending = list(target)
        hops = 0
        while pending:
            part = pending.pop(0)
            if part in {"", "."}:
                continue
            if part == "..":
                if len(resolved) <= 1:
                    raise DistributionArchiveError("archive symbolic link escapes the bundle")
                resolved.pop()
                continue
            resolved.append(part)
            name = "/".join(resolved)
            if name in links:
                hops += 1
                if hops > 40:
                    raise DistributionArchiveError("archive contains a symbolic link cycle")
                resolved.pop()
                pending = links[name] + pending
            elif pending and name not in directories:
                raise DistributionArchiveError("archive symbolic link target is missing")
        name = "/".join(resolved)
        if name not in entries and name not in directories:
            raise DistributionArchiveError("archive symbolic link target is missing")
