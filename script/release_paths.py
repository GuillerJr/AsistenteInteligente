"""Constrained destinations for release evidence and private staging."""

from __future__ import annotations

import os
import stat
from pathlib import Path


def is_release_directory(project_root: Path, directory: Path) -> bool:
    try:
        if directory.is_symlink() or directory.resolve(strict=True) != directory:
            return False
        metadata = directory.stat()
        if not stat.S_ISDIR(metadata.st_mode) or metadata.st_uid != os.getuid():
            return False
        if stat.S_IMODE(metadata.st_mode) & 0o022:
            return False
        if directory == project_root / "dist":
            return True
        # Only a fresh private release workspace, never an arbitrary /tmp path.
        if directory.name != "artifacts" or directory.parent.name != "bundle":
            return False
        workspace = directory.parent.parent
        if workspace.parent != Path("/private/tmp") or not workspace.name.startswith(
            "aegis-distribution."
        ):
            return False
        return all(
            not path.is_symlink()
            and path.stat().st_uid == os.getuid()
            and stat.S_IMODE(path.stat().st_mode) == 0o700
            for path in (directory, directory.parent, workspace)
        )
    except OSError:
        return False
