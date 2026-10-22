"""Resolve application resources without coupling runtime to an installation folder."""

from __future__ import annotations

import sys
from pathlib import Path


def installed_app_path() -> Path:
    if getattr(sys, "frozen", False):
        executable = Path(sys.executable).resolve()
        suffix = ("Contents", "Resources", "Daemon", "jarvis-daemon")
        if executable.parts[-4:] == suffix:
            return executable.parents[3]
    user_app = Path.home() / "Applications/Jarvis.app"
    if user_app.is_dir():
        return user_app
    system_app = Path("/Applications/Jarvis.app")
    if system_app.is_dir():
        return system_app
    return user_app
