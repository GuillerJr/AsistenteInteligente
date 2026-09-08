#!/usr/bin/env python3
"""Generate and verify privacy-safe release provenance and an SPDX 2.3 SBOM."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import plistlib
import re
import stat
import subprocess
import tempfile
import tomllib
import zipfile
from collections.abc import Iterable, Sequence
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import Any

SCHEMA_VERSION = "1.0"
PRODUCT_NAME = "Jarvis"
BUNDLE_IDENTIFIER = "ai.aegis.menubar"
MAX_ARCHIVE_MEMBERS = 4_096
MAX_ARCHIVE_UNCOMPRESSED_BYTES = 2 * 1024 * 1024 * 1024
MAX_ARCHIVE_MEMBER_BYTES = 512 * 1024 * 1024
MAX_PLIST_BYTES = 65_536
PROFILES = frozenset(
    {"local-development", "developer-id", "developer-id-notarized"}
)
PRIVATE_MODEL_DIRECTORIES = frozenset(
    {"JarvisSpeakerIdentity.mlmodelc", "JarvisWakeWord.mlmodelc"}
)
REQUIRED_RUNTIME_FILES = frozenset(
    {
        "Jarvis.app/Contents/Info.plist",
        "Jarvis.app/Contents/MacOS/Jarvis",
        "Jarvis.app/Contents/Resources/Daemon/jarvis-daemon",
    }
)


class ReleaseEvidenceError(RuntimeError):
    """The candidate cannot be identified or verified without ambiguity."""


def _run_git(project_root: Path, *arguments: str) -> bytes:
    try:
        completed = subprocess.run(
            ("/usr/bin/git", "-C", str(project_root), *arguments),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            check=False,
            timeout=30,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise ReleaseEvidenceError("git inspection failed") from error
    if completed.returncode != 0:
        raise ReleaseEvidenceError("git inspection failed")
    return completed.stdout


def _source_revision(project_root: Path) -> str:
    revision = _run_git(project_root, "rev-parse", "--verify", "HEAD").decode().strip()
    if re.fullmatch(r"[0-9a-f]{40}", revision) is None:
        raise ReleaseEvidenceError("source revision is invalid")
    return revision


def _require_clean_tracked_tree(project_root: Path) -> None:
    for arguments in (("diff", "--quiet"), ("diff", "--cached", "--quiet")):
        try:
            completed = subprocess.run(
                ("/usr/bin/git", "-C", str(project_root), *arguments),
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=False,
                timeout=30,
            )
        except (OSError, subprocess.TimeoutExpired) as error:
            raise ReleaseEvidenceError("source tree inspection failed") from error
        if completed.returncode != 0:
            raise ReleaseEvidenceError("tracked source tree is dirty")


def _commit_timestamp(project_root: Path, revision: str) -> str:
    raw = _run_git(project_root, "show", "-s", "--format=%cI", revision).decode().strip()
    try:
        parsed = datetime.fromisoformat(raw).astimezone(UTC)
    except ValueError as error:
        raise ReleaseEvidenceError("commit timestamp is invalid") from error
    return parsed.replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _sha256_file(path: Path) -> str:
    if path.is_symlink() or not path.is_file():
        raise ReleaseEvidenceError(f"unsafe regular file: {path.name}")
    digest = hashlib.sha256()
    with path.open("rb") as source:
        while chunk := source.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _source_tree_sha256(project_root: Path) -> str:
    tracked = _run_git(project_root, "ls-files", "-z").split(b"\0")
    digest = hashlib.sha256()
    for encoded_path in sorted(item for item in tracked if item):
        try:
            relative = encoded_path.decode("utf-8")
        except UnicodeDecodeError as error:
            raise ReleaseEvidenceError("tracked path is not UTF-8") from error
        path = project_root / relative
        metadata = path.lstat()
        digest.update(encoded_path)
        digest.update(b"\0")
        if stat.S_ISLNK(metadata.st_mode):
            digest.update(b"symlink\0")
            digest.update(os.readlink(path).encode("utf-8"))
        elif stat.S_ISREG(metadata.st_mode):
            digest.update(b"file\0")
            with path.open("rb") as source:
                while chunk := source.read(1024 * 1024):
                    digest.update(chunk)
        else:
            raise ReleaseEvidenceError(f"unsupported tracked object: {relative}")
        digest.update(b"\0")
    return digest.hexdigest()


def _safe_archive_path(name: str) -> PurePosixPath:
    path = PurePosixPath(name)
    if (
        not name
        or name.startswith("/")
        or "\\" in name
        or any(part in {"", ".", ".."} for part in path.parts)
        or path.parts[0] != f"{PRODUCT_NAME}.app"
    ):
        raise ReleaseEvidenceError("archive contains an unsafe path")
    return path


def _normalized_link_target(link: PurePosixPath, payload: bytes) -> str:
    try:
        raw_target = payload.decode("utf-8")
    except UnicodeDecodeError as error:
        raise ReleaseEvidenceError("archive contains an invalid symbolic link") from error
    target = PurePosixPath(raw_target)
    if not raw_target or raw_target.startswith("/") or "\\" in raw_target:
        raise ReleaseEvidenceError("archive contains an unsafe symbolic link")
    parts = list(link.parent.parts)
    for part in target.parts:
        if part in {"", "."}:
            continue
        if part == "..":
            if len(parts) <= 1:
                raise ReleaseEvidenceError("archive symbolic link escapes the bundle")
            parts.pop()
        else:
            parts.append(part)
    if not parts or parts[0] != f"{PRODUCT_NAME}.app":
        raise ReleaseEvidenceError("archive symbolic link escapes the bundle")
    return "/".join(parts)


def _validate_archive_links(links: dict[str, str], names: set[str]) -> None:
    normalized_names = {name.rstrip("/") for name in names}
    for source, initial_target in links.items():
        target = initial_target
        visited = {source.rstrip("/")}
        while target in links:
            if target in visited:
                raise ReleaseEvidenceError("archive contains a symbolic link cycle")
            visited.add(target)
            target = links[target]
        if target not in normalized_names:
            raise ReleaseEvidenceError("archive symbolic link target is missing")


def _hash_zip_member(archive: zipfile.ZipFile, member: zipfile.ZipInfo) -> str:
    digest = hashlib.sha256()
    with archive.open(member, "r") as source:
        while chunk := source.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def inspect_archive(archive_path: Path, expected_revision: str) -> dict[str, Any]:
    if archive_path.is_symlink() or not archive_path.is_file():
        raise ReleaseEvidenceError("release archive is missing or unsafe")
    try:
        with zipfile.ZipFile(archive_path) as archive:
            members = archive.infolist()
            if not members or len(members) > MAX_ARCHIVE_MEMBERS:
                raise ReleaseEvidenceError("archive member count is outside the safety budget")
            names: set[str] = set()
            files: list[dict[str, Any]] = []
            links: dict[str, str] = {}
            total_size = 0
            info_payload: bytes | None = None
            for member in members:
                member_path = _safe_archive_path(member.filename)
                if not PRIVATE_MODEL_DIRECTORIES.isdisjoint(member_path.parts):
                    raise ReleaseEvidenceError("archive contains a private biometric model")
                if member.filename in names:
                    raise ReleaseEvidenceError("archive contains duplicate paths")
                names.add(member.filename)
                unix_mode = member.external_attr >> 16
                if stat.S_ISLNK(unix_mode):
                    links[member.filename.rstrip("/")] = _normalized_link_target(
                        member_path,
                        archive.read(member),
                    )
                if member.is_dir():
                    continue
                if member.file_size > MAX_ARCHIVE_MEMBER_BYTES:
                    raise ReleaseEvidenceError("archive member exceeds the safety budget")
                total_size += member.file_size
                if total_size > MAX_ARCHIVE_UNCOMPRESSED_BYTES:
                    raise ReleaseEvidenceError("archive expansion exceeds the safety budget")
                if member.filename == f"{PRODUCT_NAME}.app/Contents/Info.plist":
                    if member.file_size > MAX_PLIST_BYTES:
                        raise ReleaseEvidenceError("bundle metadata exceeds the safety budget")
                    info_payload = archive.read(member)
                files.append(
                    {
                        "path": member.filename,
                        "sha256": _hash_zip_member(archive, member),
                        "size": member.file_size,
                    }
                )
            _validate_archive_links(links, names)
    except (OSError, zipfile.BadZipFile, RuntimeError) as error:
        if isinstance(error, ReleaseEvidenceError):
            raise
        raise ReleaseEvidenceError("release archive cannot be inspected") from error
    if info_payload is None:
        raise ReleaseEvidenceError("bundle metadata is missing from the archive")
    if not REQUIRED_RUNTIME_FILES.issubset(names):
        raise ReleaseEvidenceError("bundle omits the self-contained daemon runtime")
    try:
        info = plistlib.loads(info_payload)
    except plistlib.InvalidFileException as error:
        raise ReleaseEvidenceError("bundle metadata is invalid") from error
    required = {
        "CFBundleIdentifier": BUNDLE_IDENTIFIER,
        "CFBundleName": PRODUCT_NAME,
        "CFBundleExecutable": PRODUCT_NAME,
        "AegisBuildRevision": expected_revision,
        "AegisBuildDirty": False,
    }
    if not isinstance(info, dict) or any(info.get(key) != value for key, value in required.items()):
        raise ReleaseEvidenceError("bundle identity does not match the source revision")
    version = info.get("CFBundleShortVersionString")
    build = info.get("CFBundleVersion")
    minimum_macos = info.get("LSMinimumSystemVersion")
    if not all(isinstance(value, str) and value for value in (version, build, minimum_macos)):
        raise ReleaseEvidenceError("bundle version metadata is incomplete")
    files.sort(key=lambda item: item["path"])
    return {
        "archive_sha256": _sha256_file(archive_path),
        "archive_size": archive_path.stat().st_size,
        "bundle_version": version,
        "bundle_build": build,
        "minimum_macos": minimum_macos,
        "uncompressed_size": total_size,
        "files": files,
    }


def _spdx_identifier(ecosystem: str, name: str, version: str) -> str:
    normalized = re.sub(r"[^A-Za-z0-9.-]", "-", f"{ecosystem}-{name}-{version}")
    return f"SPDXRef-Package-{normalized}"


def _locked_components(project_root: Path) -> list[dict[str, str]]:
    components: set[tuple[str, str, str, str]] = set()
    uv_lock = project_root / "uv.lock"
    swift_lock = project_root / "native/AegisAudio/Package.resolved"
    if uv_lock.is_symlink() or not uv_lock.is_file():
        raise ReleaseEvidenceError("Python lockfile is missing or unsafe")
    if swift_lock.is_symlink() or not swift_lock.is_file():
        raise ReleaseEvidenceError("Swift lockfile is missing or unsafe")
    try:
        uv_data = tomllib.loads(uv_lock.read_text(encoding="utf-8"))
        swift_data = json.loads(swift_lock.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, ValueError, tomllib.TOMLDecodeError) as error:
        raise ReleaseEvidenceError("dependency lockfile is invalid") from error
    for package in uv_data.get("package", []):
        if not isinstance(package, dict):
            raise ReleaseEvidenceError("Python lockfile package is invalid")
        name = package.get("name")
        version = package.get("version")
        if name == "aegis-swarm":
            continue
        source = package.get("source")
        location = (
            source.get("registry", "NOASSERTION")
            if isinstance(source, dict)
            else "NOASSERTION"
        )
        if not all(isinstance(value, str) for value in (name, version, location)):
            raise ReleaseEvidenceError("Python lockfile package is incomplete")
        components.add(("pypi", name, version, location))
    pins = swift_data.get("pins") if isinstance(swift_data, dict) else None
    if not isinstance(pins, list):
        raise ReleaseEvidenceError("Swift lockfile pins are invalid")
    for pin in pins:
        if not isinstance(pin, dict) or not isinstance(pin.get("state"), dict):
            raise ReleaseEvidenceError("Swift lockfile pin is invalid")
        name = pin.get("identity")
        location = pin.get("location")
        state = pin["state"]
        version = state.get("version") or state.get("revision")
        if not all(isinstance(value, str) and value for value in (name, version, location)):
            raise ReleaseEvidenceError("Swift lockfile pin is incomplete")
        components.add(("swift", name, version, location))
    return [
        {"ecosystem": ecosystem, "name": name, "version": version, "location": location}
        for ecosystem, name, version, location in sorted(components)
    ]


def build_spdx_document(
    *,
    revision: str,
    created_at: str,
    version: str,
    components: Iterable[dict[str, str]],
) -> dict[str, Any]:
    root_id = "SPDXRef-Package-Jarvis"
    packages: list[dict[str, Any]] = [
        {
            "SPDXID": root_id,
            "name": PRODUCT_NAME,
            "versionInfo": version,
            "downloadLocation": "NOASSERTION",
            "filesAnalyzed": False,
            "licenseConcluded": "LicenseRef-Proprietary",
            "licenseDeclared": "LicenseRef-Proprietary",
            "copyrightText": "Copyright (c) 2026 Guillermo (gzambrano27)",
            "supplier": "Person: Guillermo (gzambrano27)",
        }
    ]
    relationships: list[dict[str, str]] = [
        {
            "spdxElementId": "SPDXRef-DOCUMENT",
            "relationshipType": "DESCRIBES",
            "relatedSpdxElement": root_id,
        }
    ]
    identifiers: set[str] = {root_id}
    for component in components:
        identifier = _spdx_identifier(
            component["ecosystem"], component["name"], component["version"]
        )
        if identifier in identifiers:
            raise ReleaseEvidenceError("dependency identity collision")
        identifiers.add(identifier)
        packages.append(
            {
                "SPDXID": identifier,
                "name": component["name"],
                "versionInfo": component["version"],
                "downloadLocation": component["location"],
                "filesAnalyzed": False,
                "licenseConcluded": "NOASSERTION",
                "licenseDeclared": "NOASSERTION",
                "copyrightText": "NOASSERTION",
                "comment": (
                    f"Locked {component['ecosystem']} dependency; runtime inclusion varies "
                    "by profile."
                ),
            }
        )
        relationships.append(
            {
                "spdxElementId": root_id,
                "relationshipType": "DEPENDS_ON",
                "relatedSpdxElement": identifier,
            }
        )
    return {
        "spdxVersion": "SPDX-2.3",
        "dataLicense": "CC0-1.0",
        "SPDXID": "SPDXRef-DOCUMENT",
        "name": f"Jarvis-{revision[:12]}",
        "documentNamespace": (
            "https://github.com/GuillerJr/AsistenteInteligente/spdx/" + revision
        ),
        "creationInfo": {
            "created": created_at,
            "creators": ["Tool: aegis-release-evidence-1.0"],
        },
        "documentDescribes": [root_id],
        "packages": packages,
        "relationships": relationships,
    }


def _canonical_bytes(value: Any) -> bytes:
    serialized = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return f"{serialized}\n".encode()


def _atomic_write(path: Path, payload: bytes) -> None:
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary = Path(temporary_name)
    try:
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "wb") as target:
            target.write(payload)
            target.flush()
            os.fsync(target.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def build_documents(
    project_root: Path,
    archive_path: Path,
    *,
    profile: str,
    revision: str,
    source_tree_sha256: str,
    created_at: str,
    sbom_name: str,
) -> tuple[dict[str, Any], dict[str, Any]]:
    if profile not in PROFILES:
        raise ReleaseEvidenceError("release profile is invalid")
    inspected = inspect_archive(archive_path, revision)
    sbom = build_spdx_document(
        revision=revision,
        created_at=created_at,
        version=inspected["bundle_version"],
        components=_locked_components(project_root),
    )
    sbom_sha256 = hashlib.sha256(_canonical_bytes(sbom)).hexdigest()
    manifest = {
        "schema_version": SCHEMA_VERSION,
        "product": PRODUCT_NAME,
        "bundle_identifier": BUNDLE_IDENTIFIER,
        "version": inspected["bundle_version"],
        "build": inspected["bundle_build"],
        "build_revision": revision,
        "source_tree_sha256": source_tree_sha256,
        "created_at": created_at,
        "profile": profile,
        "architecture": "arm64",
        "minimum_macos": inspected["minimum_macos"],
        "artifact": {
            "name": archive_path.name,
            "format": "zip",
            "sha256": inspected["archive_sha256"],
            "size": inspected["archive_size"],
            "uncompressed_size": inspected["uncompressed_size"],
        },
        "bundle_files": inspected["files"],
        "sbom": {"name": sbom_name, "format": "SPDX-2.3", "sha256": sbom_sha256},
        "privacy": {
            "contains_prompts": False,
            "contains_transcripts": False,
            "contains_images": False,
            "contains_credentials": False,
            "contains_absolute_paths": False,
        },
    }
    return manifest, sbom


def _resolved_documents(
    project_root: Path,
    archive_path: Path,
    *,
    profile: str,
    sbom_name: str,
    require_clean: bool,
) -> tuple[dict[str, Any], dict[str, Any]]:
    if require_clean:
        _require_clean_tracked_tree(project_root)
    revision = _source_revision(project_root)
    return build_documents(
        project_root,
        archive_path,
        profile=profile,
        revision=revision,
        source_tree_sha256=_source_tree_sha256(project_root),
        created_at=_commit_timestamp(project_root, revision),
        sbom_name=sbom_name,
    )


def generate(
    project_root: Path,
    archive_path: Path,
    manifest_path: Path,
    sbom_path: Path,
    *,
    profile: str,
) -> None:
    manifest, sbom = _resolved_documents(
        project_root,
        archive_path,
        profile=profile,
        sbom_name=sbom_path.name,
        require_clean=True,
    )
    _atomic_write(sbom_path, _canonical_bytes(sbom))
    _atomic_write(manifest_path, _canonical_bytes(manifest))


def validate_documents(
    manifest: Any,
    sbom: Any,
    *,
    expected_manifest: dict[str, Any],
    expected_sbom: dict[str, Any],
) -> None:
    if manifest != expected_manifest or sbom != expected_sbom:
        raise ReleaseEvidenceError("release evidence authentication failed")


def verify(
    project_root: Path,
    archive_path: Path,
    manifest_path: Path,
    sbom_path: Path,
    *,
    profile: str,
) -> None:
    if any(path.is_symlink() or not path.is_file() for path in (manifest_path, sbom_path)):
        raise ReleaseEvidenceError("release evidence is missing or unsafe")
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        sbom = json.loads(sbom_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise ReleaseEvidenceError("release evidence is invalid") from error
    expected_manifest, expected_sbom = _resolved_documents(
        project_root,
        archive_path,
        profile=profile,
        sbom_name=sbom_path.name,
        require_clean=True,
    )
    validate_documents(
        manifest,
        sbom,
        expected_manifest=expected_manifest,
        expected_sbom=expected_sbom,
    )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("generate", "verify"))
    parser.add_argument("--project-root", type=Path, required=True)
    parser.add_argument("--archive", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--sbom", type=Path, required=True)
    parser.add_argument("--profile", choices=sorted(PROFILES), required=True)
    return parser


def _distribution_path(project_root: Path, raw_path: Path, expected_name: str) -> Path:
    candidate = raw_path if raw_path.is_absolute() else project_root / raw_path
    if candidate.name != expected_name or candidate.is_symlink():
        raise ReleaseEvidenceError("release evidence path is invalid")
    try:
        parent = candidate.parent.resolve(strict=True)
        distribution = (project_root / "dist").resolve(strict=True)
    except OSError as error:
        raise ReleaseEvidenceError("distribution directory is unavailable") from error
    if parent != distribution:
        raise ReleaseEvidenceError("release evidence path is outside dist")
    return candidate


def main(arguments: Sequence[str] | None = None) -> int:
    options = _parser().parse_args(arguments)
    try:
        root = options.project_root.resolve(strict=True)
        if not root.is_dir():
            raise ReleaseEvidenceError("project root is invalid")
        archive = _distribution_path(root, options.archive, f"{PRODUCT_NAME}.zip")
        manifest = _distribution_path(
            root, options.manifest, f"{PRODUCT_NAME}.release.json"
        )
        sbom = _distribution_path(root, options.sbom, f"{PRODUCT_NAME}.spdx.json")
        action = generate if options.action == "generate" else verify
        action(
            root,
            archive,
            manifest,
            sbom,
            profile=options.profile,
        )
    except (OSError, ReleaseEvidenceError) as error:
        print(f"status=error reason={str(error).replace(' ', '_')}", file=os.sys.stderr)
        return 1
    print(
        f"status=ok action={options.action} manifest={options.manifest.name} "
        f"sbom={options.sbom.name}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
