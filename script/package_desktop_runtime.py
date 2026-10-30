#!/usr/bin/env python3
"""Assemble the sealed arm64 runtime in an admitted remote CI build only.

No third-party install hooks are run. Archives are authenticated before reading,
their internal links become bounded regular copies, and nested Mach-O signatures
precede the manifest. The caller signs the containing app after this program.
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
import plistlib
import re
import ssl
import stat
import subprocess
import sys
import tarfile
import time
import tomllib
import urllib.parse
import urllib.request
import zipfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from urllib.error import HTTPError, URLError

from packaging.markers import Marker
from packaging.tags import compatible_tags, cpython_tags, mac_platforms
from packaging.utils import canonicalize_name, parse_wheel_filename

from jarvis_core.bundled_runtime import (
    CA_CERTIFICATES_PATH,
    ENTRYPOINTS,
    MANIFEST_NAME,
    MAX_FILE_BYTES,
    MAX_FILES,
    MAX_TREE_BYTES,
    MODEL_ENTRYPOINT,
    SCHEMA,
    BundledRuntimeError,
    relative_path,
    verify_runtime_tree,
)

REPO = Path(__file__).resolve().parents[1]
MAX_STAGE_BYTES = 3 * 1024**3
MACHO_MAGICS = {b"\xcf\xfa\xed\xfe", b"\xfe\xed\xfa\xcf", b"\xca\xfe\xba\xbe",
                b"\xbe\xba\xfe\xca", b"\xca\xfe\xba\xbf", b"\xbf\xba\xfe\xca"}
_ALLOWED_DOWNLOAD_HOSTS = {"github.com", "release-assets.githubusercontent.com",
                           "objects.githubusercontent.com", "raw.githubusercontent.com",
                           "files.pythonhosted.org"}
TARGET_ENVIRONMENT = {
    "implementation_name": "cpython", "implementation_version": "3.11.18",
    "os_name": "posix", "platform_machine": "arm64", "platform_release": "23.0.0",
    "platform_system": "Darwin", "platform_version": "Darwin Kernel Version 23.0.0",
    "platform_python_implementation": "CPython", "python_full_version": "3.11.18",
    "python_version": "3.11", "sys_platform": "darwin", "extra": "",
}


class PackageError(ValueError):
    pass


@dataclass
class BuildDiagnostics:
    phase: str = "admission"

    def enter(self, stage, component=None):
        require(stage in {"admission", "setup", "pins", "download", "extract", "wheellegal",
                          "wheels", "certs", "source", "model", "sign", "manifest", "info",
                          "receipt"}, "runtime_diagnostic_phase_invalid")
        require(component is None or (isinstance(component, str) and re.fullmatch(
            r"[A-Za-z0-9_.-]{1,96}", component) is not None), "runtime_diagnostic_phase_invalid")
        self.phase = stage + (":" + component if component is not None else "")


def failure_diagnostic(error, diagnostic):
    """No exception messages, URL queries, paths, response bodies or headers."""
    code = "runtime_build_failed"
    if isinstance(error, PackageError):
        candidate = error.args[0] if error.args else None
    elif isinstance(error, BundledRuntimeError):
        candidate = error.code
    else:
        candidate = None
    if isinstance(candidate, str) and re.fullmatch(r"[a-z0-9_.:-]{1,192}", candidate):
        code = candidate
    kind = type(error).__name__
    phase = diagnostic.phase
    value = {"status": "failed", "error": code,
             "phase": phase if isinstance(phase, str) and re.fullmatch(
                 r"[a-z]+(?::[A-Za-z0-9_.-]{1,96})?", phase) else "unknown",
             "exception_type": kind if re.fullmatch(r"[A-Za-z0-9_]{1,96}", kind)
             else "Exception"}
    if isinstance(error, HTTPError) and type(error.code) is int and 100 <= error.code <= 599:
        value["http_status"] = error.code
    reason = error.reason if isinstance(error, URLError) else error
    number = getattr(reason, "errno", None)
    if type(number) is int and -4096 <= number <= 4096:
        value["errno"] = number
    if reason is not error and isinstance(reason, BaseException):
        kind = type(reason).__name__
        if re.fullmatch(r"[A-Za-z0-9_]{1,96}", kind):
            value["cause_type"] = kind
    return value


def require(condition, code):
    if not condition:
        raise PackageError(code)


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
                      allow_nan=False).encode("utf-8")


def sha256(payload):
    return hashlib.sha256(payload).hexdigest()


class _ArchiveDigest:
    """Hash an actual deterministic generated tar stream without retaining it."""

    def __init__(self):
        self.digest, self.bytes = hashlib.sha256(), 0

    def write(self, payload):
        self.digest.update(payload)
        self.bytes += len(payload)
        return len(payload)


def _archive_name(name):
    # A conventional archive-leading './' is not a filesystem authority. Strip
    # it here only; finished runtime paths use the stricter shared validator.
    while name.startswith("./"):
        name = name[2:]
    return relative_path(name.rstrip("/"))


def _link_target(name, target, *, hardlink=False):
    require(isinstance(target, str) and not target.startswith("/") and "\\" not in target,
            "archive_link_unsafe")
    parts = [] if hardlink else name.split("/")[:-1]
    for component in target.split("/"):
        if component in {"", "."}:
            continue
        if component == "..":
            require(bool(parts), "archive_link_escape")
            parts.pop()
        else:
            parts.append(component)
    return relative_path("/".join(parts))


@dataclass
class Budget:
    deadline: float
    staged_bytes: int = 0
    maximum: int = MAX_STAGE_BYTES

    def check(self):
        require(time.monotonic() < self.deadline, "runtime_build_deadline")

    def reserve(self, size):
        self.check()
        require(type(size) is int and size >= 0
                and self.staged_bytes + size <= self.maximum, "runtime_stage_limit")
        self.staged_bytes += size


class TreeWriter:
    """Only new physical directories/files; never overwrite a prior component."""

    def __init__(self, root, budget):
        self.root, self.budget = root, budget
        self.names, self.folded, self.total = set(), set(), 0

    def write(self, name, stream, size, *, executable=False):
        name = relative_path(name)
        require(name not in self.names and name.casefold() not in self.folded,
                "runtime_output_collision")
        require(0 <= size <= MAX_FILE_BYTES and self.total + size <= MAX_TREE_BYTES
                and len(self.names) < MAX_FILES, "runtime_output_limit")
        self.budget.reserve(size)
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o755)
        for ancestor in (path.parent, *path.parent.parents):
            if ancestor == self.root.parent:
                break
            info = ancestor.lstat()
            require(stat.S_ISDIR(info.st_mode) and not info.st_mode & 0o022,
                    "runtime_output_directory_unsafe")
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        written = 0
        with os.fdopen(descriptor, "wb") as output:
            while block := stream.read(min(512 * 1024, size + 1 - written)):
                self.budget.check()
                written += len(block)
                require(written <= size, "archive_entry_grew")
                output.write(block)
            require(written == size, "archive_entry_truncated")
            output.flush()
            os.fchmod(output.fileno(), 0o755 if executable else 0o644)
        self.names.add(name)
        self.folded.add(name.casefold())
        self.total += size

    def bytes(self, name, payload, *, executable=False):
        self.write(name, io.BytesIO(payload), len(payload), executable=executable)


def extract_tar(archive_path, writer, destination, *, anchor):
    """Materialize only one anchored archive tree, including safe internal links."""
    with tarfile.open(archive_path, "r:gz") as archive:
        members, aliases = {}, set()
        for item in archive:
            writer.budget.check()
            if item.name.rstrip("/") in {"", "."} and item.isdir():
                continue
            name = _archive_name(item.name)
            require(name not in members and name.casefold() not in aliases
                    and len(members) < MAX_FILES, "archive_duplicate_or_limit")
            require(item.isfile() or item.isdir() or item.issym() or item.islnk(),
                    "archive_special_file")
            require(0 <= item.size <= MAX_FILE_BYTES, "archive_file_limit")
            members[name] = item
            aliases.add(name.casefold())
        anchors = [name for name in members if name == anchor or name.endswith("/" + anchor)]
        require(len(anchors) == 1, "archive_layout_unknown")
        prefix = anchors[0][:-len(anchor)]
        require(all(name.startswith(prefix) or prefix.rstrip("/").startswith(name + "/")
                    for name in members), "archive_multiple_roots")

        def resolve(name, seen):
            require(name in members and name not in seen and len(seen) <= 64,
                    "archive_link_cycle_or_missing")
            item = members[name]
            if item.isfile():
                return item
            require(item.issym() or item.islnk(), "archive_link_not_file")
            target = _link_target(name, item.linkname, hardlink=item.islnk())
            require(target.startswith(prefix), "archive_link_escape")
            return resolve(target, {*seen, name})

        for name in sorted(members):
            item = members[name]
            if item.isdir() or name == prefix.rstrip("/"):
                continue
            require(name.startswith(prefix), "archive_multiple_roots")
            target = resolve(name, set())
            with archive.extractfile(target) as stream:
                writer.write(destination + "/" + name[len(prefix):], stream, target.size,
                             executable=bool(target.mode & 0o111))


def target_wheels(lock):
    """Resolve only locked production dependencies for the explicit target."""
    packages = {}
    for package in lock["package"]:
        name = canonicalize_name(package["name"])
        require(name not in packages, "lock_ambiguous_package")
        packages[name] = package
    require("jarvis-core" in packages, "lock_core_missing")
    platforms = list(mac_platforms(version=(14, 0), arch="arm64"))
    tags = list(cpython_tags((3, 11), abis=["cp311"], platforms=platforms))
    tags += list(compatible_tags((3, 11), interpreter="cp311", platforms=platforms))
    rank = {tag: index for index, tag in reversed(list(enumerate(tags)))}
    pending, visited, result = ["jarvis-core"], set(), []
    while pending:
        name = pending.pop()
        if name in visited:
            continue
        visited.add(name)
        require(name in packages, "lock_dependency_missing")
        package = packages[name]
        for dependency in package.get("dependencies", []):
            if not dependency.get("marker") or Marker(dependency["marker"]).evaluate(
                    TARGET_ENVIRONMENT):
                require(not dependency.get("extra"), "lock_extra_unreviewed")
                pending.append(canonicalize_name(dependency["name"]))
        if name == "jarvis-core":
            continue
        require(package.get("source") == {"registry": "https://pypi.org/simple"},
                "lock_nonregistry_dependency")
        choices = []
        for wheel in package.get("wheels", []):
            filename = urllib.parse.unquote(PurePosixPath(
                urllib.parse.urlsplit(wheel["url"]).path).name)
            parsed_name, version, _, wheel_tags = parse_wheel_filename(filename)
            require(canonicalize_name(parsed_name) == name
                    and str(version) == package["version"], "lock_wheel_identity")
            matches = [rank[t] for t in wheel_tags if t in rank]
            if matches:
                require(wheel["hash"].startswith("sha256:"), "lock_wheel_hash")
                choices.append((min(matches), filename, wheel))
        require(bool(choices), "lock_target_wheel_missing:" + name)
        _, filename, wheel = min(choices, key=lambda row: (row[0], row[1]))
        result.append({"name": name, "version": package["version"], "filename": filename,
                       "url": wheel["url"], "bytes": wheel["size"],
                       "sha256": wheel["hash"].removeprefix("sha256:")})
    return sorted(result, key=lambda row: row["name"])


def reviewed_license_fallbacks(pins, *, name, version):
    """An explicit source-reviewed exception for one exact upstream version."""
    rows = pins.get("wheel_legal", [])
    require(type(rows) is list and len(rows) <= 32, "wheel_legal_pin_shape")
    matches = [row for row in rows if row.get("package") == name and row.get("version") == version]
    require(len(matches) <= 1, "wheel_legal_pin_duplicate")
    if not matches:
        return []
    row = matches[0]
    require(set(row) == {"package", "version", "files"} and type(row["files"]) is list
            and 1 <= len(row["files"]) <= 8, "wheel_legal_pin_shape")
    names = set()
    for pin in row["files"]:
        require(type(pin) is dict and set(pin) == {"name", "url", "bytes", "sha256"}
                and pin["name"] in {"LICENSE", "NOTICE", "COPYING"}
                and pin["name"] not in names and 0 < pin["bytes"] <= 128 * 1024,
                "wheel_legal_pin_shape")
        names.add(pin["name"])
        validate_download_url(pin["url"])
    return row["files"]


def reviewed_embedded_licenses(pins, *, name, version):
    rows = pins.get("wheel_embedded_legal", [])
    require(type(rows) is list and len(rows) <= 32, "wheel_legal_pin_shape")
    matches = [row for row in rows if row.get("package") == name and row.get("version") == version]
    require(len(matches) <= 1, "wheel_legal_pin_duplicate")
    if not matches:
        return []
    row = matches[0]
    require(set(row) == {"package", "version", "paths"} and type(row["paths"]) is list
            and 1 <= len(row["paths"]) <= 8
            and len(set(row["paths"])) == len(row["paths"]), "wheel_legal_pin_shape")
    return [relative_path(path) for path in row["paths"]]


def extract_wheel(path, writer, *, name, version, license_fallbacks=(), embedded_legal=()):
    with zipfile.ZipFile(path) as archive:
        entries, folded, licenses = [], set(), []
        for item in archive.infolist():
            writer.budget.check()
            original = _archive_name(item.filename)
            require(original.casefold() not in folded and len(folded) < MAX_FILES,
                    "wheel_duplicate_or_limit")
            folded.add(original.casefold())
            mode = item.external_attr >> 16
            require(not item.flag_bits & 1 and not stat.S_ISLNK(mode)
                    and (stat.S_IFMT(mode) in {0, stat.S_IFREG, stat.S_IFDIR}),
                    "wheel_special_file")
            if item.is_dir():
                continue
            parts = original.split("/")
            if parts[0].endswith(".data"):
                require(len(parts) > 2 and parts[1] in {"purelib", "platlib"},
                        "wheel_install_hook_or_external_data")
                relative = "/".join(parts[2:])
            else:
                relative = original
            destination = "site-packages/" + relative
            if any(p.endswith(".dist-info") for p in parts) and any(
                    token in parts[-1].upper()
                    for token in ("LICENSE", "LICENCE", "COPYING", "NOTICE")):
                licenses.append(destination)
            entries.append((item, destination))
        metadata = [i for i, _ in entries if i.filename.endswith(".dist-info/METADATA")]
        require(len(metadata) == 1 and metadata[0].file_size <= 1024 * 1024,
                "wheel_metadata_missing_or_limit")
        raw = archive.read(metadata[0])
        require(len(raw) <= 1024 * 1024, "wheel_metadata_limit")
        from email.parser import BytesParser
        message = BytesParser().parsebytes(raw, headersonly=True)
        require(canonicalize_name(message.get("Name", "")) == name
                and message.get("Version") == version, "wheel_metadata_identity")
        destinations = {destination: item for item, destination in entries}
        for relative in embedded_legal:
            member = "site-packages/" + relative_path(relative)
            require(member in destinations and destinations[member].file_size > 0,
                    "wheel_embedded_license_missing")
            licenses.append(member)
        for relative in license_fallbacks:
            require(relative_path(relative) in writer.names
                    and (writer.root / relative).stat().st_size > 0,
                    "wheel_license_fallback_unavailable")
        licenses.extend(license_fallbacks)
        require(bool(licenses), "wheel_license_missing:" + name)
        for item, destination in entries:
            with archive.open(item) as stream:
                writer.write(destination, stream, item.file_size, executable=False)
        return sorted(licenses)


class _PinnedRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, message, headers, newurl):
        validate_download_url(newurl)
        return super().redirect_request(request, fp, code, message, headers, newurl)


def validate_download_url(url):
    parsed = urllib.parse.urlsplit(url)
    require(parsed.scheme == "https" and parsed.hostname in _ALLOWED_DOWNLOAD_HOSTS
            and not parsed.username and not parsed.password and not parsed.fragment,
            "artifact_download_origin")


def download(pin, destination, budget, *, opener=None):
    validate_download_url(pin["url"])
    require(type(pin["bytes"]) is int and 0 < pin["bytes"] <= MAX_FILE_BYTES
            and re.fullmatch(r"[a-f0-9]{64}", pin["sha256"]) is not None,
            "artifact_pin_invalid")
    budget.reserve(pin["bytes"])
    opener = opener or urllib.request.build_opener(
        urllib.request.HTTPSHandler(context=ssl.create_default_context()), _PinnedRedirect())
    request = urllib.request.Request(pin["url"], headers={"User-Agent": "Jarvis-CI-runtime/1"})
    digest, size = hashlib.sha256(), 0
    with opener.open(request, timeout=min(45, max(1, budget.deadline - time.monotonic()))) as reply:
        require(reply.status == 200, "artifact_download_status")
        with destination.open("xb") as output:
            while block := reply.read(min(512 * 1024, pin["bytes"] + 1 - size)):
                budget.check()
                size += len(block)
                require(size <= pin["bytes"], "artifact_download_limit")
                output.write(block)
                digest.update(block)
    require(size == pin["bytes"] and digest.hexdigest() == pin["sha256"],
            "artifact_download_hash")
    return destination


def _run(arguments, *, timeout=60, cwd=None):
    result = subprocess.run(arguments, check=False, capture_output=True, timeout=timeout, cwd=cwd)
    require(result.returncode == 0, "runtime_build_command_failed:" + Path(arguments[0]).name)
    return result.stdout


def _license_paths(writer, prefix, *, required=True):
    names = sorted(name for name in writer.names if name.startswith(prefix + "/")
                   and any(token in PurePosixPath(name).name.upper()
                           for token in ("LICENSE", "LICENCE", "COPYING", "NOTICE")))
    require(bool(names) or not required, "component_license_missing:" + prefix)
    return names


def _macho(path):
    with path.open("rb") as stream:
        return stream.read(4) in MACHO_MAGICS


def _macho_dependencies(output):
    lines = output.decode("utf-8", errors="strict").splitlines()[1:]
    return [line.strip().split(" (compatibility version", 1)[0]
            for line in lines if line[:1].isspace()]


def check_linked_dependency(dependency, *, path, runtime, rpaths):
    """Allow OS frameworks or a dependency that resolves inside this runtime."""
    if dependency.startswith(("/System/Library/", "/usr/lib/")):
        return
    candidates = []
    if dependency.startswith("@loader_path/"):
        candidates.append(path.parent / dependency.removeprefix("@loader_path/"))
    elif dependency.startswith("@executable_path/"):
        # Libraries from the Python distribution are loaded by its pinned binary.
        candidates.append(runtime / "python/bin" / dependency.removeprefix("@executable_path/"))
    elif dependency.startswith("@rpath/"):
        suffix = dependency.removeprefix("@rpath/")
        for item in rpaths:
            if item.startswith("@loader_path"):
                candidates.append(Path(str(path.parent)
                                       + item.removeprefix("@loader_path")) / suffix)
            elif item.startswith("@executable_path"):
                candidates.append(Path(str(runtime / "python/bin")
                                       + item.removeprefix("@executable_path")) / suffix)
            elif item.startswith(("/System/Library/", "/usr/lib/")):
                return
    require(any(candidate.resolve().is_relative_to(runtime) and candidate.is_file()
                for candidate in candidates), "runtime_external_linkage")


def sign_nested(runtime, identity, budget, *, runner=_run):
    machos = sorted((p for p in runtime.rglob("*") if p.is_file() and _macho(p)),
                    key=lambda p: (-len(p.parts), str(p)))
    require(bool(machos), "runtime_no_native_code")
    for path in machos:
        budget.check()
        load_commands = runner(["/usr/bin/otool", "-l", str(path)]).decode()
        rpaths = re.findall(r"cmd LC_RPATH\n(?:[^\n]*\n){1,2}\s*path (.*?) \(offset", load_commands)
        dependencies = _macho_dependencies(runner(["/usr/bin/otool", "-L", str(path)]))
        # LC_ID_DYLIB is the image's own install name, not a load request.
        install_names = runner(["/usr/bin/otool", "-D", str(path)]).decode().splitlines()[1:]
        for dependency in dependencies:
            if dependency in install_names:
                continue
            check_linked_dependency(dependency, path=path, runtime=runtime, rpaths=rpaths)
        runner(["/usr/bin/codesign", "--force", "--sign", identity, "--timestamp=none", str(path)])
        runner(["/usr/bin/codesign", "--verify", "--strict", str(path)])
    return len(machos)


def file_inventory(root):
    rows = []
    for path in sorted(root.rglob("*")):
        if path.is_dir():
            require(not path.is_symlink(), "runtime_output_link")
            continue
        info = path.lstat()
        require(stat.S_ISREG(info.st_mode) and info.st_nlink == 1, "runtime_output_link")
        name = relative_path(path.relative_to(root).as_posix())
        require(name != MANIFEST_NAME, "runtime_manifest_already_present")
        digest = hashlib.sha256()
        with path.open("rb") as stream:
            while block := stream.read(512 * 1024):
                digest.update(block)
        rows.append({"path": name, "bytes": info.st_size, "sha256": digest.hexdigest(),
                     "executable": bool(info.st_mode & 0o111)})
    return rows


def _admission(app, scratch, commit):
    require(sys.platform == "darwin" and os.environ.get("GITHUB_ACTIONS") == "true"
            and os.environ.get("GITHUB_SHA") == commit,
            "runtime_build_remote_ci_only")
    run_root = os.environ.get("JARVIS_TEST_GUARD_RUN_ROOT")
    nonce = os.environ.get("JARVIS_TEST_GUARD_NONCE")
    require(run_root and nonce and os.environ.get("JARVIS_TEST_GUARD_CHILD_CHECKED") == nonce
            and os.environ.get("JARVIS_TEST_GUARD_ADMISSION"), "runtime_build_not_admitted")
    run_root = Path(run_root).resolve(strict=True)
    require(app.is_absolute() and app.resolve(strict=True) == app
            and scratch.is_absolute() and not scratch.exists()
            and app.is_relative_to(run_root) and scratch.is_relative_to(run_root)
            and scratch.parent.resolve(strict=True) == scratch.parent,
            "runtime_build_destination")
    require(re.fullmatch(r"[a-f0-9]{40}", commit) is not None
            and _run(["git", "-C", str(REPO), "rev-parse", "HEAD"]).decode().strip() == commit
            and not _run(["git", "-C", str(REPO), "status", "--porcelain", "--untracked-files=no"]),
            "runtime_build_source_identity")


def assemble(app, scratch, commit, model_assets, *, identity="-", deadline_seconds=900,
             diagnostic=None):
    diagnostic = diagnostic or BuildDiagnostics()
    diagnostic.enter("admission")
    _admission(app, scratch, commit)
    require(identity == "-", "runtime_signing_identity_not_selected")
    runtime = app / "Contents/Resources/JarvisRuntime"
    require(not runtime.exists() and not runtime.is_symlink(), "runtime_already_present")
    diagnostic.enter("setup")
    scratch.mkdir(mode=0o700)
    runtime.mkdir(parents=True, mode=0o755)
    budget = Budget(time.monotonic() + deadline_seconds)
    writer, components = TreeWriter(runtime, budget), []
    diagnostic.enter("pins")
    pins = json.loads((REPO / "script/runtime_artifacts.json").read_bytes())
    require(pins["schema"] == "jarvis.runtime-artifact-pins.v1"
            and pins["target"] == "aarch64-apple-darwin", "runtime_pin_schema")
    for key, anchor in (("python", "bin/python3.11"), ("codex", "bin/codex")):
        pin = pins[key]
        diagnostic.enter("download", key)
        archive = download(pin, scratch / (key + ".tar.gz"), budget)
        diagnostic.enter("extract", key)
        extract_tar(archive, writer, key, anchor=anchor)
        components.append({"id": key, "version": pin["version"], "origin": pin["url"],
                           "archive_sha256": pin["sha256"], "license_paths": []})
    for pin in pins["codex_legal"]:
        diagnostic.enter("download", "codex-" + pin["name"].lower())
        path = download(pin, scratch / ("codex-" + pin["name"]), budget)
        diagnostic.enter("extract", "codex-legal")
        with path.open("rb") as stream:
            writer.write("licenses/codex/" + pin["name"], stream, pin["bytes"])
    diagnostic.enter("extract", "legal-inventory")
    components[0]["license_paths"] = _license_paths(writer, "python")
    components[1]["license_paths"] = sorted(set(_license_paths(writer, "codex", required=False)
                                                + _license_paths(writer, "licenses/codex")))
    diagnostic.enter("extract", "codex-metadata")
    package_info = json.loads((runtime / "codex/codex-package.json").read_bytes())
    require(package_info.get("version") == "0.163.0"
            and package_info.get("target") == "aarch64-apple-darwin"
            and package_info.get("entrypoint") == "bin/codex"
            and {"codex/bin/codex-code-mode-host"} <= writer.names,
            "codex_package_incomplete")

    diagnostic.enter("wheels", "resolve")
    lock = tomllib.loads((REPO / "uv.lock").read_text())
    for wheel in target_wheels(lock):
        diagnostic.enter("download", wheel["name"])
        path = download(wheel, scratch / wheel["filename"], budget)
        fallbacks = []
        diagnostic.enter("wheellegal", wheel["name"])
        for pin in reviewed_license_fallbacks(pins, name=wheel["name"], version=wheel["version"]):
            diagnostic.enter("download", wheel["name"] + "-" + pin["name"].lower())
            legal = download(pin, scratch / (wheel["name"] + "-" + pin["name"]), budget)
            diagnostic.enter("wheellegal", wheel["name"])
            relative = "licenses/wheels/" + wheel["name"] + "/" + pin["name"]
            with legal.open("rb") as stream:
                writer.write(relative, stream, pin["bytes"])
            fallbacks.append(relative)
            components.append({"id": wheel["name"] + "-" + pin["name"].lower(),
                               "version": wheel["version"], "origin": pin["url"],
                               "archive_sha256": pin["sha256"], "license_paths": [relative]})
        diagnostic.enter("extract", wheel["name"])
        licenses = extract_wheel(path, writer, name=wheel["name"], version=wheel["version"],
                                 license_fallbacks=fallbacks,
                                 embedded_legal=reviewed_embedded_licenses(
                                     pins, name=wheel["name"], version=wheel["version"]))
        components.append({"id": wheel["name"], "version": wheel["version"],
                           "origin": wheel["url"], "archive_sha256": wheel["sha256"],
                           "license_paths": licenses})
    diagnostic.enter("certs")
    with (runtime / "site-packages/certifi/cacert.pem").open("rb") as stream:
        writer.write(CA_CERTIFICATES_PATH, stream,
                     (runtime / "site-packages/certifi/cacert.pem").stat().st_size)

    diagnostic.enter("source")
    source_archive = _run(["git", "-C", str(REPO), "archive", "--format=tar", commit,
                           "src/jarvis_core", "LICENSE"])
    require(len(source_archive) <= 32 * 1024 * 1024, "runtime_source_limit")
    with tarfile.open(fileobj=io.BytesIO(source_archive), mode="r:") as archive:
        for item in archive:
            require(item.isfile() or item.isdir(), "runtime_source_special")
            if item.isfile():
                name = relative_path(item.name)
                destination = ("licenses/jarvis-core/LICENSE" if name == "LICENSE" else
                               "core/" + name.removeprefix("src/"))
                with archive.extractfile(item) as stream:
                    writer.write(destination, stream, item.size)
    version = tomllib.loads((REPO / "pyproject.toml").read_text())["project"]["version"]
    components.append({"id": "jarvis-core", "version": version,
                       "origin": ("https://github.com/GuillerJr/AsistenteInteligente/tree/"
                                  + commit),
                       "archive_sha256": sha256(source_archive),
                       "license_paths": ["licenses/jarvis-core/LICENSE"]})

    diagnostic.enter("model")
    from jarvis_core.model_artifacts import MODEL_SPEC, REVISION, inspect_model
    inspect_model(model_assets)
    model_archive = _ArchiveDigest()
    with tarfile.open(fileobj=model_archive, mode="w|") as archive:
        for relative in sorted(["manifest.json", *(row["path"] for row in MODEL_SPEC["files"])]):
            source = model_assets / relative
            with source.open("rb") as stream:
                writer.write(MODEL_ENTRYPOINT + "/" + relative, stream, source.stat().st_size)
            info = tarfile.TarInfo(relative)
            info.size, info.mode, info.mtime = source.stat().st_size, 0o644, 0
            with source.open("rb") as stream:
                archive.addfile(info, stream)
    components.append({"id": "minilm", "version": REVISION,
                       "origin": "https://huggingface.co/" + MODEL_SPEC["repository"]
                                 + "/tree/" + REVISION,
                       "archive_sha256": model_archive.digest.hexdigest(),
                       "license_paths": [MODEL_ENTRYPOINT + "/LICENSE.Apache-2.0.txt"]})

    diagnostic.enter("sign")
    signed = sign_nested(runtime, identity, budget)
    diagnostic.enter("manifest")
    manifest = {"schema": SCHEMA, "source_commit": commit, "target": pins["target"],
                "minimum_macos": pins["minimum_macos"],
                "python_version": pins["python"]["version"],
                "codex_version": pins["codex"]["version"],
                "entrypoints": {**ENTRYPOINTS, "model": MODEL_ENTRYPOINT},
                "files": file_inventory(runtime),
                "components": sorted(components, key=lambda row: row["id"])}
    raw = canonical(manifest)
    writer.bytes(MANIFEST_NAME, raw)
    pin = sha256(raw)
    verified = verify_runtime_tree(runtime, expected_manifest_sha256=pin,
                                   expected_source_commit=commit)
    diagnostic.enter("info")
    info_path = app / "Contents/Info.plist"
    info = plistlib.loads(info_path.read_bytes())
    info["JarvisRuntimeManifestSHA256"] = pin
    info["JarvisDistributionChannel"] = "self-contained"
    info_path.write_bytes(plistlib.dumps(info, sort_keys=True))
    diagnostic.enter("receipt")
    receipt = {"schema": "jarvis.runtime-build.v1", "source_commit": commit,
               "manifest_sha256": pin, "files": verified.files, "bytes": verified.bytes,
               "nested_macho_signed": signed, "staging_reserved_bytes": budget.staged_bytes,
               "self_contained": True, "outer_app_signature_pending": True,
               "acquisition": "pinned_official_artifacts_only", "model_included": True}
    (scratch / "runtime-build-receipt.json").write_bytes(canonical(receipt))
    return receipt


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--app", type=Path, required=True)
    parser.add_argument("--scratch", type=Path, required=True)
    parser.add_argument("--source-commit", required=True)
    parser.add_argument("--model-assets", type=Path, required=True)
    parser.add_argument("--identity", default="-")
    parser.add_argument("--deadline", type=int, default=900)
    args = parser.parse_args()
    diagnostic = BuildDiagnostics()
    try:
        require(1 <= args.deadline <= 1200, "runtime_build_deadline_invalid")
        receipt = assemble(args.app, args.scratch, args.source_commit, args.model_assets,
                           identity=args.identity, deadline_seconds=args.deadline,
                           diagnostic=diagnostic)
    except Exception as error:
        print(json.dumps(failure_diagnostic(error, diagnostic)))
        return 1
    print(json.dumps({"status": "ready", **receipt}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
