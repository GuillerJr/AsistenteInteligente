#!/usr/bin/env python3
"""Install pinned MCP peers outside the app bundle, with no lifecycle scripts."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--destination", type=Path,
                        default=Path.home() / "Library/Application Support/Aegis/mcp_peers")
    arguments = parser.parse_args()
    source = Path(__file__).resolve().parents[1] / "integrations/mcp"
    destination = arguments.destination
    if (not destination.is_absolute() or destination.is_symlink()
            or not destination.parent.is_dir()
            or destination.parent.resolve() != destination.parent):
        parser.error("Choose an absolute directory under a real existing parent")
    manifest = json.loads((source / "package.json").read_text())
    lock = json.loads((source / "package-lock.json").read_text())
    expected = {"@playwright/mcp": "0.0.80",
                "@modelcontextprotocol/server-filesystem": "2026.8.31"}
    if (manifest.get("dependencies") != expected
            or lock.get("packages", {}).get("", {}).get("dependencies") != expected):
        parser.error("Pinned dependency manifest changed; review before installation")
    if destination.exists() and any(destination.iterdir()):
        parser.error("Destination must be empty; existing peers require explicit migration")
    npm = shutil.which("npm")
    if npm is None:
        parser.error("Node.js and npm are required")
    destination.mkdir(mode=0o700, exist_ok=True)
    os.chmod(destination, 0o700)
    for name in ("package.json", "package-lock.json"):
        shutil.copyfile(source / name, destination / name)
        os.chmod(destination / name, 0o600)
    completed = subprocess.run(
        [npm, "ci", "--ignore-scripts", "--omit=dev", "--no-audit", "--no-fund"],
        cwd=destination, check=False, timeout=180, capture_output=True, text=True,
    )
    if completed.returncode != 0:
        raise SystemExit("Pinned MCP dependency installation failed; no profile was activated")
    print(json.dumps({"status": "pinned_peers_installed", "destination": str(destination),
                      "lifecycle_scripts_executed": False, "browser_launched": False,
                      "configuration_changed": False}))


if __name__ == "__main__":
    main()
