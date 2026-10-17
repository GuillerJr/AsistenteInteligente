"""Create disabled, owner-only setup candidates without touching the live daemon."""

from __future__ import annotations

import os
import shutil
from pathlib import Path

from aegis_core.contracts import Capability
from aegis_core.mcp.client import McpHostConfiguration, McpServerConfiguration
from aegis_core.mcp.integrations import IntegrationId, IntegrationScope
from aegis_core.mcp.local_peers import local_peer_configuration


def prepare_local_profiles(destination: Path, *, packages: Path, owner_home: Path) -> dict:
    if not destination.is_absolute() or destination.exists() or destination.is_symlink():
        raise ValueError("Use a new absolute directory; existing configurations are never replaced")
    roots = (owner_home / "Documents", owner_home / "Downloads")
    if any(not p.is_dir() or p.resolve() != p for p in roots):
        raise ValueError("Documents and Downloads must be real directories")
    node = shutil.which("node")
    if node is None:
        raise ValueError("Install the pinned Node dependencies before preparing profiles")
    destination.mkdir(mode=0o700)
    browser_working = destination / "browser-workspace"
    browser_working.mkdir(mode=0o700)
    candidates = []
    for provider in (IntegrationId.FILESYSTEM, IntegrationId.PLAYWRIGHT):
        scope = IntegrationScope(
            provider=provider, roots=roots if provider is IntegrationId.FILESYSTEM else ()
        )
        candidates.append(
            local_peer_configuration(
                scope,
                node=Path(node),
                packages=packages,
                working_directory=browser_working,
            )
        )
    for provider in (IntegrationId.GITHUB, IntegrationId.GOOGLE, IntegrationId.HOME_ASSISTANT):
        secrets = (
            {"GITHUB_PERSONAL_ACCESS_TOKEN": "read-only-token"}
            if provider is IntegrationId.GITHUB
            else {"GOOGLE_OAUTH_CLIENT_SECRET": "oauth-client"}
            if provider is IntegrationId.GOOGLE
            else {}
        )
        candidates.append(
            McpServerConfiguration(
                server_id=provider.value,
                # Explicit non-executable placeholder for uninstalled/unconfigured peers.
                command=Path("/usr/bin/false"),
                capability=(
                    Capability.SYSTEM_READ
                    if provider is IntegrationId.HOME_ASSISTANT
                    else Capability.WEB_READ
                ),
                read_only=provider is not IntegrationId.GOOGLE,
                integration=IntegrationScope(provider=provider),
                secret_environment=secrets,
                allow_explicit_requests=True,
            )
        )
    files = []
    for candidate in candidates:
        path = destination / f"{candidate.server_id}.json"
        raw = McpHostConfiguration(servers=(candidate,)).model_dump_json(indent=2).encode()
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(raw)
        files.append(str(path))
    return {
        "status": "disabled_candidates_created",
        "files": files,
        "live_configuration_changed": False,
        "accounts_connected": 0,
        "next_step": "Review launcher, scopes and discovered schemas before activation",
    }
