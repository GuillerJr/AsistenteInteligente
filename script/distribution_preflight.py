#!/usr/bin/env python3
"""Prepare and validate an isolated macOS distribution without microphone access.

This is deliberately not a P10/P12/P13/P14 qualification or a notarized release.
It never installs, launches the UI, reads credentials, or publishes a channel.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import plistlib
import re
import stat
import subprocess
import sys
import tempfile
from pathlib import Path

import release_evidence as evidence

ROOT = Path(__file__).resolve().parents[1]
BUILD_INPUTS = ("src", "native", "script", "packaging", "pyproject.toml", "uv.lock", "LICENSE")
MACHO_MAGICS = {
    b"\xfe\xed\xfa\xce",
    b"\xce\xfa\xed\xfe",
    b"\xfe\xed\xfa\xcf",
    b"\xcf\xfa\xed\xfe",
    b"\xca\xfe\xba\xbe",
    b"\xbe\xba\xfe\xca",
    b"\xca\xfe\xba\xbf",
    b"\xbf\xba\xfe\xca",
}


def source_fingerprint(root: Path) -> str:
    """Bind local validation to tracked *and* new build inputs, not just HEAD."""
    paths = evidence._run_git(
        root,
        "ls-files",
        "-z",
        "--cached",
        "--others",
        "--exclude-standard",
        "--",
        *BUILD_INPUTS,
    )
    digest = hashlib.sha256()
    for encoded in sorted(set(paths.split(b"\0")) - {b""}):
        path = root / os.fsdecode(encoded)
        digest.update(encoded + b"\0")
        if path.is_symlink():
            raise evidence.ReleaseEvidenceError("build input is a symbolic link")
        if not path.exists():
            digest.update(b"deleted\0")
            continue
        if not path.is_file():
            raise evidence.ReleaseEvidenceError("build input is not a regular file")
        digest.update(str(stat.S_IMODE(path.stat().st_mode)).encode() + b"\0")
        digest.update(evidence._sha256_file(path).encode() + b"\0")
    return digest.hexdigest()


def _run(*command: str, timeout: int = 60, env: dict[str, str] | None = None) -> bytes:
    completed = subprocess.run(
        command,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        timeout=timeout,
        check=False,
        env=env,
    )
    if completed.returncode:
        # Tool output can contain local paths or signing identity details.
        raise evidence.ReleaseEvidenceError(f"{Path(command[0]).name} check failed")
    return completed.stdout


def preflight(root: Path) -> dict[str, object]:
    clean = True
    try:
        evidence._require_clean_tracked_tree(root)
    except evidence.ReleaseEvidenceError:
        clean = False
    pin = root / "packaging/JarvisUpdatePublicKey.ed25519"
    key_valid = (
        not pin.is_symlink()
        and pin.is_file()
        and pin.stat().st_size <= 256
        and re.fullmatch(rb"ed25519:[A-Za-z0-9+/]{43}=\s*", pin.read_bytes()) is not None
    )
    checks = {
        "macos_arm64": platform.system() == "Darwin" and platform.machine() == "arm64",
        "python_runtime": os.access(root / ".venv/bin/python", os.X_OK),
        "daemon_packager": os.access(root / ".venv/bin/pyinstaller", os.X_OK),
        "pinned_update_key": key_valid,
    }
    for name, tool in (("swift_toolchain", "swift"), ("macos_sdk", "--show-sdk-path")):
        arguments = ("--sdk", "macosx", tool) if tool.startswith("--") else ("--find", tool)
        try:
            checks[name] = bool(_run("/usr/bin/xcrun", *arguments).strip())
        except (OSError, subprocess.SubprocessError, evidence.ReleaseEvidenceError):
            checks[name] = False
    return {
        "schema_version": "1.0",
        "profile": "distribution_without_voice",
        "local_checks_passed": all(checks.values()),
        "checks": checks,
        "source_clean": clean,
        "build_revision": evidence._source_revision(root),
        "build_inputs_sha256": source_fingerprint(root),
        "public_release_qualified": False,
        "deferred": {
            "owner_voice": "awaiting_explicit_user_authorization",
            "live_macos_qualification": "not_run_in_noninteractive_validation",
            "developer_id_notarization": "requires_separate_signed_release",
            "public_channel": "not_published",
        },
        "privacy": {
            "microphone_access": False,
            "owner_recordings_read": False,
            "credentials_read": False,
            "app_installed_or_launched": False,
            "channel_published": False,
        },
    }


def _inspect_native_code(app: Path) -> int:
    count = 0
    for path in sorted(app.rglob("*")):
        if path.is_symlink() or not path.is_file():
            continue
        with path.open("rb") as source:
            if source.read(4) not in MACHO_MAGICS:
                continue
        architectures = _run("/usr/bin/lipo", "-archs", str(path)).decode().split()
        if "arm64" not in architectures:
            raise evidence.ReleaseEvidenceError("nested code does not support arm64")
        _run("/usr/bin/codesign", "--verify", "--strict", str(path))
        libraries = _run("/usr/bin/otool", "-L", str(path)).decode().splitlines()[1:]
        for line in libraries:
            dependency = line.strip().split(" (", 1)[0]
            if not dependency.startswith(
                ("@rpath/", "@loader_path/", "@executable_path/", "/System/Library/", "/usr/lib/")
            ):
                raise evidence.ReleaseEvidenceError("nested code depends on the developer host")
        count += 1
    if not count:
        raise evidence.ReleaseEvidenceError("bundle contains no native code")
    return count


def validate(root: Path) -> tuple[Path, dict[str, object]]:
    report = preflight(root)
    if report["local_checks_passed"] is not True:
        raise evidence.ReleaseEvidenceError("local distribution prerequisites are missing")
    work = Path(tempfile.mkdtemp(prefix="aegis-distribution.", dir="/private/tmp"))
    work.chmod(0o700)
    output = work / "bundle"
    log = work / "build.log"
    # Do not inherit provider tokens or arbitrary build/signing overrides.
    build_env = {key: os.environ[key] for key in ("HOME", "PATH", "TMPDIR") if key in os.environ}
    build_env.update(
        {
            "LANG": "C",
            "LC_ALL": "C",
            "AEGIS_CODESIGN_IDENTITY": "-",
            "AEGIS_BUILD_MLX": "0",
            "AEGIS_INCLUDE_PERSONAL_MODELS": "0",
            "AEGIS_EMIT_ARCHIVE": "0",
            "AEGIS_EMBED_DAEMON": "1",
            "AEGIS_PACKAGE_OUTPUT_ROOT": str(output),
            "AEGIS_DAEMON_BUILD_ROOT": f"/private/tmp/aegis-daemon-{work.name}",
        }
    )
    print(f"status=building profile=local-validation log={log}", flush=True)
    try:
        with log.open("xb") as target:
            log.chmod(0o600)
            result = subprocess.run(
                (str(root / "script/build_and_run.sh"), "package"),
                stdin=subprocess.DEVNULL,
                stdout=target,
                stderr=subprocess.STDOUT,
                env=build_env,
                timeout=1_800,
                check=False,
            )
        if result.returncode:
            raise evidence.ReleaseEvidenceError("isolated bundle build failed")
        app = output / "Jarvis.app"
        archive = work / "Jarvis.zip"
        _run("/usr/bin/codesign", "--verify", "--deep", "--strict", str(app))
        _run(
            "/usr/bin/ditto",
            "-c",
            "-k",
            "--norsrc",
            "--keepParent",
            str(app),
            str(archive),
            timeout=180,
        )
        archive.chmod(0o600)
        inspected = evidence.inspect_archive(
            archive,
            str(report["build_revision"]),
            allow_dirty=True,
        )
        # Smoke-test the exact ZIP after extraction, not the build tree.
        extraction = work / "extracted"
        extraction.mkdir(mode=0o700)
        _run("/usr/bin/ditto", "-x", "-k", str(archive), str(extraction), timeout=180)
        extracted_app = extraction / "Jarvis.app"
        _run("/usr/bin/codesign", "--verify", "--deep", "--strict", str(extracted_app))
        code_count = _inspect_native_code(extracted_app)
        resources = extracted_app / "Contents/Resources"
        if (resources / "JarvisUpdatePublicKey.ed25519").read_bytes() != (
            root / "packaging/JarvisUpdatePublicKey.ed25519"
        ).read_bytes():
            raise evidence.ReleaseEvidenceError("bundle update key differs from the pin")
        isolated_home = work / "self-test-home"
        isolated_home.mkdir(mode=0o700)
        runtime = json.loads(
            _run(
                str(resources / "Daemon/jarvis-daemon"),
                "--self-test",
                timeout=120,
                env={
                    "HOME": str(isolated_home),
                    "PATH": "/usr/bin:/bin:/usr/sbin:/sbin",
                    "LANG": "C",
                    "LC_ALL": "C",
                },
            )
        )
        if (
            runtime.get("profile") != "jarvis_frozen_daemon_self_test"
            or runtime.get("gate_passed") is not True
            or runtime.get("privacy", {}).get("network_calls") != 0
            or runtime.get("privacy", {}).get("persistent_writes") != 0
        ):
            raise evidence.ReleaseEvidenceError("frozen runtime self test failed")
        info = plistlib.loads((extracted_app / "Contents/Info.plist").read_bytes())
        if info.get("AegisBuildDirty") is not (not report["source_clean"]):
            raise evidence.ReleaseEvidenceError("bundle source state is inconsistent")
        if source_fingerprint(root) != report["build_inputs_sha256"]:
            raise evidence.ReleaseEvidenceError("build inputs changed during validation")
        sbom = evidence.build_spdx_document(
            revision=str(report["build_revision"]),
            created_at=evidence._commit_timestamp(root, str(report["build_revision"])),
            version=inspected["bundle_version"],
            components=evidence._locked_components(root),
        )
        evidence._atomic_write(work / "Jarvis.spdx.json", evidence._canonical_bytes(sbom))
        report.update(
            {
                "status": "passed",
                "artifact": {"name": archive.name, **inspected},
                "sbom_sha256": evidence._sha256_file(work / "Jarvis.spdx.json"),
                "nested_native_code_verified": code_count,
                "runtime_self_test": runtime,
                "swift_mlx_helpers_included": False,
                "signature_profile": "ad-hoc-local-validation",
            }
        )
    except (
        OSError,
        ValueError,
        subprocess.SubprocessError,
        evidence.ReleaseEvidenceError,
    ) as error:
        reason = (
            str(error) if isinstance(error, evidence.ReleaseEvidenceError) else type(error).__name__
        )
        report.update({"status": "failed", "local_checks_passed": False, "reason": reason})
        evidence._atomic_write(work / "Jarvis.validation.json", evidence._canonical_bytes(report))
        print(f"status=failed evidence={work / 'Jarvis.validation.json'} log={log}", flush=True)
        raise
    evidence._atomic_write(work / "Jarvis.validation.json", evidence._canonical_bytes(report))
    return work, report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("preflight", "validate"))
    options = parser.parse_args()
    try:
        if options.action == "preflight":
            report = preflight(ROOT)
            print(json.dumps(report, ensure_ascii=False, sort_keys=True))
            return 0 if report["local_checks_passed"] else 2
        work, _ = validate(ROOT)
        print(
            f"status=ok profile=local-validation evidence={work / 'Jarvis.validation.json'} "
            f"artifact={work / 'Jarvis.zip'} owner_voice=deferred public_release=false"
        )
        return 0
    except (OSError, ValueError, subprocess.SubprocessError, evidence.ReleaseEvidenceError):
        print("status=error reason=distribution_validation_failed", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
