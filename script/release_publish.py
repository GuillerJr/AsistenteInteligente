#!/usr/bin/env python3
"""Promote a verified staged release locally, keeping a recoverable previous set.

Readers authenticate all hashes and therefore fail closed during replacement.
The manifest is the last file installed. Nothing is uploaded or installed here.
"""

from __future__ import annotations

import argparse
import os
import shutil
import stat
import tempfile
from pathlib import Path

import release_evidence as evidence
from release_paths import is_release_directory

ARTIFACT_NAMES = (
    "Jarvis.zip",
    "Jarvis.spdx.json",
    "Jarvis.notarization.json",
    "Jarvis.update.json",
    "Jarvis.release.json",
)
REQUIRED = frozenset({"Jarvis.zip", "Jarvis.spdx.json", "Jarvis.release.json"})


def promote(root: Path, staging: Path, profile: str) -> Path:
    destination = root / "dist"
    destination.mkdir(mode=0o700, exist_ok=True)
    if (
        staging == destination
        or not is_release_directory(root, staging)
        or not is_release_directory(root, destination)
    ):
        raise evidence.ReleaseEvidenceError("unsafe release promotion directory")
    lock = destination / ".release-publish.lock"
    try:
        lock.mkdir(mode=0o700)
    except FileExistsError as error:
        raise evidence.ReleaseEvidenceError("another release promotion is active") from error
    history: Path | None = None
    published: list[str] = []
    saved: list[str] = []
    try:
        evidence.verify(
            root,
            staging / "Jarvis.zip",
            staging / "Jarvis.release.json",
            staging / "Jarvis.spdx.json",
            profile=profile,
        )
        present = {name for name in ARTIFACT_NAMES if (staging / name).exists()}
        if not REQUIRED.issubset(present):
            raise evidence.ReleaseEvidenceError("staged release is incomplete")
        for directory in (staging, destination):
            for name in ARTIFACT_NAMES:
                path = directory / name
                if path.is_symlink() or (
                    path.exists()
                    and (
                        not path.is_file()
                        or path.stat().st_uid != os.getuid()
                        or stat.S_IMODE(path.stat().st_mode) & 0o077
                    )
                ):
                    raise evidence.ReleaseEvidenceError("unsafe release artifact")
        # Stage on the destination filesystem before changing any live file.
        history = Path(tempfile.mkdtemp(prefix=".release-previous.", dir=destination))
        incoming = history / "incoming"
        incoming.mkdir(mode=0o700)
        for name in present:
            shutil.copyfile(staging / name, incoming / name)
            (incoming / name).chmod(0o600)
            with (incoming / name).open("rb") as source:
                os.fsync(source.fileno())
        evidence.verify(
            root,
            incoming / "Jarvis.zip",
            incoming / "Jarvis.release.json",
            incoming / "Jarvis.spdx.json",
            profile=profile,
        )
        # Save the complete former set, including stale channel/notary evidence.
        for name in ARTIFACT_NAMES:
            if (destination / name).exists():
                os.replace(destination / name, history / name)
                saved.append(name)
        for name in ARTIFACT_NAMES:
            if name in present:
                os.replace(incoming / name, destination / name)
                published.append(name)
        incoming.rmdir()
        return history
    except BaseException:
        if history is not None:
            for name in reversed(published):
                (destination / name).unlink(missing_ok=True)
            for name in reversed(saved):
                os.replace(history / name, destination / name)
        raise
    finally:
        lock.rmdir()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-root", type=Path, required=True)
    parser.add_argument("--staging", type=Path, required=True)
    parser.add_argument("--profile", choices=sorted(evidence.PROFILES), required=True)
    options = parser.parse_args()
    try:
        previous = promote(options.project_root.resolve(), options.staging, options.profile)
    except (OSError, evidence.ReleaseEvidenceError):
        print("status=error reason=release_promotion_failed")
        return 1
    print(f"status=ok action=promoted previous_artifacts={previous}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
