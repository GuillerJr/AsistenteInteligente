"""Build an explicitly selected own development artifact without executing it."""

from __future__ import annotations

import os
import plistlib
import stat
from pathlib import Path

from jarvis_core import local_release_update as core
from jarvis_core.recovery import canonical
from jarvis_core.recovery_fs import FileIdentity


def package_release(
    python_source, native_bundle, destination, version_key, schemas, context, *, limits=None
):
    """Copy source .py files and a Jarvis-branded bundle into one new artifact.

    Branding and hashes do not authenticate the author. No build hooks, signing
    commands, Python imports from the selected source, or launch are performed.
    """
    limits = limits or core.ReleaseLimits()
    owner = core.ReleaseOwner()
    context.begin()
    try:
        source = core._Dir(python_source, owner).child("jarvis_core")
        bundle = core._Dir(native_bundle, owner)
        contents = bundle.child("Contents")
        raw, _ = core._read(
            contents,
            "Info.plist",
            limits.max_manifest_bytes,
            lambda: context.checkpoint("release.package_branding"),
        )
        try:
            info = plistlib.loads(raw)
        except (ValueError, plistlib.InvalidFileException):
            raise core.ReleaseError("release_bundle_branding_invalid") from None
        if (
            type(info) is not dict
            or info.get("CFBundleName") != "Jarvis"
            or info.get("CFBundleExecutable") != "Jarvis"
            or info.get("CFBundleIdentifier") != "com.jarvis.desktop.baseline"
        ):
            raise core.ReleaseError("release_bundle_branding_invalid")
        target_path = Path(destination)
        parent = core._Dir(target_path.parent, owner)
        if (
            not target_path.is_absolute()
            or not core._KEY.fullmatch(target_path.name)
            or not isinstance(version_key, str)
            or not core._KEY.fullmatch(version_key)
        ):
            raise core.ReleaseError("release_package_target_invalid")
        for root in (source.path, bundle.path):
            if (
                target_path == root
                or target_path.is_relative_to(root)
                or root.is_relative_to(target_path)
            ):
                raise core.ReleaseError("release_package_paths_overlap")
        target = core._mkdir(parent, target_path.name, owner)
        outputs = {"": target}
        rows, total, directories = [], 0, 0
        maintenance_purposes = None

        def put(relative, data, executable):
            nonlocal total, maintenance_purposes
            core._path(relative)
            total += len(data)
            if total > limits.max_total_bytes or len(rows) >= limits.max_files:
                raise core.ReleaseError("release_payload_limit")
            parts = relative.split("/")
            for index in range(1, len(parts)):
                prefix, previous = "/".join(parts[:index]), "/".join(parts[: index - 1])
                if prefix not in outputs:
                    outputs[prefix] = core._mkdir(outputs[previous], parts[index - 1], owner)
            core._write(
                outputs["/".join(parts[:-1])],
                parts[-1],
                data,
                owner,
                context,
                executable=executable,
            )
            rows.append(
                {
                    "path": relative,
                    "size": len(data),
                    "sha256": core._sha(data),
                    "executable": executable,
                }
            )
            if relative == core._JOURNAL_SOURCE:
                maintenance_purposes = core.source_maintenance_purposes(data)

        def collect(directory, prefix, *, python_only=False):
            nonlocal directories
            for name in sorted(core._entries(directory, limits.max_files + limits.max_directories)):
                context.checkpoint("release.package_read")
                if python_only and name == "__pycache__":
                    continue
                info = os.stat(name, dir_fd=directory.fd, follow_symlinks=False)
                relative = prefix + name
                if stat.S_ISDIR(info.st_mode):
                    core._path(relative + "/placeholder.py")
                    directories += 1
                    if directories > limits.max_directories:
                        raise core.ReleaseError("release_directories_limit")
                    collect(directory.child(name), relative + "/", python_only=python_only)
                else:
                    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                        raise core.ReleaseError("release_file_unsafe")
                    if python_only and (not name.endswith(".py") or name.endswith(" 2.py")):
                        continue
                    core._path(relative)
                    data, metadata = core._read(
                        directory,
                        name,
                        min(limits.max_file_bytes, core._MAINTENANCE_SOURCE_LIMIT)
                        if relative == core._JOURNAL_SOURCE
                        else limits.max_file_bytes,
                        lambda: context.checkpoint("release.package_read"),
                    )
                    put(relative, data, bool(metadata[2] & 0o111))

        collect(source, "python/jarvis_core/", python_only=True)
        collect(bundle, "app/Jarvis.app/")
        if maintenance_purposes is None:
            raise core.ReleaseError("release_maintenance_source_missing")
        manifest = {
            "format": core.FORMAT,
            "product": "Jarvis",
            "channel": "development",
            "version_key": version_key,
            "entrypoints": core._ENTRYPOINTS,
            "external_dependencies": core._DEPENDENCIES,
            "schemas": schemas,
            "maintenance_purposes": list(maintenance_purposes),
            "files": sorted(rows, key=lambda row: row["path"]),
        }
        data = canonical(manifest)
        if len(data) > limits.max_manifest_bytes:
            raise core.ReleaseError("release_manifest_limit")
        core._manifest(data, limits)
        core._write(target, "release.json", data, owner, context)
        pin = core.ProfilePin(
            context.profile_id,
            context.generation,
            FileIdentity(0, 0),
            "0" * 64,
            tuple(sorted((k, v[0]) for k, v in schemas.items())),
            "0" * 64,
        )
        core._artifact(target, context, limits, pin)
        for directory in outputs.values():
            os.fsync(directory.fd)
        os.fsync(parent.fd)
        result = {
            "artifact_path": str(target.path),
            "version_key": version_key,
            "manifest_sha256": core._sha(data),
            "files": len(rows),
            "bytes": total,
            "channel": "development",
            "release_authenticity": "not_established",
            "code_executed": False,
            "signing_performed": False,
            "external_dependencies": core._DEPENDENCIES,
        }
        owner.keep.update((directory.path, name) for directory, name, _, _ in owner.created)
        owner.cleanup()
        return result
    except BaseException as error:
        try:
            owner.cleanup()
        except BaseException:
            pass
        raise core._wrap(error, owner) from None
