"""Prepare an inert, pinned Google Workspace MCP candidate for one account."""

from __future__ import annotations

import os
import re
import stat
from pathlib import Path

from aegis_core.contracts import Capability
from aegis_core.mcp.client import (
    McpConfigurationError,
    McpHostConfiguration,
    McpServerConfiguration,
    artifact_sha256,
)
from aegis_core.mcp.integrations import IntegrationId, IntegrationScope

GOOGLE_WORKSPACE_VERSION = "1.26.1"
GOOGLE_WORKSPACE_ARGUMENTS = (
    "--transport", "stdio", "--permissions", "gmail:drafts", "calendar:full",
    "drive:full", "--tool-tier", "extended",
)
GOOGLE_WORKSPACE_OAUTH_PORT = 18731
GOOGLE_WORKSPACE_CALL_TIMEOUT_SECONDS = 45.0
GOOGLE_WORKSPACE_REDIRECT_URI = (
    f"http://127.0.0.1:{GOOGLE_WORKSPACE_OAUTH_PORT}/oauth2callback"
)
# Discovered from the installed v1.26.1 stdio peer with the exact launcher above.
# Hashes include excluded fields and upstream defaults, not only Jarvis's projection.
GOOGLE_WORKSPACE_REVIEWED_SCHEMAS = {
    "create_drive_file": "faa30a1b0cc6a24ffc389eca0e94ea9cf71fd2861ca6ff32a9ed9a56e088ba58",
    "create_drive_folder": "692bddd9d00896e676cc94fca53f035d7cf4cd907285e3c2f87490c587399dee",
    "draft_gmail_message": "15fe51ff5c10b10adf856f83a74af01c8cac2169e149403faf76de144e5eb831",
    "get_drive_file_content": "fb86b21b9074855bf5df6444e52d5a99cc221c0047be89a3a9930f2f41428918",
    "get_events": "5a2a1b000d8d3ca66014727d73f324532a53c12e74da2d09c8125c1d48248155",
    "get_gmail_message_content": "7b5736962ca160c2c762e3d3c9bc79853895ade6145bbdd791fa67dab1d35668",
    "get_gmail_messages_content_batch": (
        "038c517c2ce6aaeaa5d4e4436f965371c58f9cc012a55eddd597750b71498c1e"
    ),
    "list_calendars": "f6767819419d9c5e9fb5a3d4b17421eaeaf49fcd510f374a0c18a0b55990a0fb",
    "list_drive_items": "f25ef3014bb4784a1489c2dc7432380e57864e0c80705e7660979468de45109a",
    "manage_event": "ccb0259e353ccde21ad347c314811942b8a6b97d2d95f090cfd268c4449541b4",
    "search_drive_files": "dee58691e327456269c3083d80aef25e1044bff0bb2a192861f84aa5b0233fb8",
    "search_gmail_messages": "0c9aae39e83d2e6a6b545c6d9eec4e71e390a480af82aea1f86d9c7f023c2372",
    "update_drive_file": "6feb459295f0de4ab44431bcbf1b79456cf46172e9eeb8e5e0bbdea622a50d67",
}
_CLIENT_ID = re.compile(r"^[A-Za-z0-9_-]+\.apps\.googleusercontent\.com$")
_PINNED_MODULES = (
    "main.py",
    "auth/credential_store.py",
    "auth/google_auth.py",
    "auth/permissions.py",
    "auth/scopes.py",
    "gcalendar/calendar_tools.py",
    "gdrive/drive_tools.py",
    "gmail/gmail_tools.py",
)


def installed_peer_artifacts(project: Path) -> dict[str, str]:
    """Pin the executable, selected policy-bearing modules and dependency lock."""
    project = project.resolve(strict=True)
    executable = project / ".venv/bin/workspace-mcp"
    locations = tuple((project / ".venv/lib").glob("python*/site-packages"))
    if len(locations) != 1:
        raise McpConfigurationError("Google Workspace peer must have one installed Python runtime")
    package = locations[0]
    metadata = package / f"workspace_mcp-{GOOGLE_WORKSPACE_VERSION}.dist-info/METADATA"
    if not metadata.is_file():
        raise McpConfigurationError("Pinned Google Workspace MCP version is not installed")
    paths = (executable, project / "uv.lock", metadata, *(package / p for p in _PINNED_MODULES))
    return {str(path): artifact_sha256(path) for path in paths}


def prepare_google_workspace_profile(
    destination: Path,
    *,
    project: Path,
    account: str,
    client_id: str | None = None,
) -> dict[str, object]:
    """Create a private disabled profile; never authenticates or edits the live daemon."""
    if not destination.is_absolute() or destination.exists() or destination.is_symlink():
        raise ValueError("Use a new absolute directory for the Google profile")
    scope = IntegrationScope(provider=IntegrationId.GOOGLE, google_accounts={account})
    if client_id is not None and _CLIENT_ID.fullmatch(client_id) is None:
        raise ValueError("Google OAuth client ID has an invalid format")
    project = project.resolve(strict=True)
    pins = installed_peer_artifacts(project)
    executable = project / ".venv/bin/workspace-mcp"
    if not os.access(executable, os.X_OK):
        raise McpConfigurationError("Google Workspace MCP executable is unavailable")

    destination.mkdir(mode=0o700)
    credential_directory = destination / "credentials"
    log_directory = destination / "logs"
    credential_directory.mkdir(mode=0o700)
    log_directory.mkdir(mode=0o700)
    environment = {
        "WORKSPACE_MCP_CREDENTIALS_DIR": str(credential_directory),
        "WORKSPACE_MCP_LOG_DIR": str(log_directory),
        "WORKSPACE_MCP_PORT": str(GOOGLE_WORKSPACE_OAUTH_PORT),
        "WORKSPACE_MCP_PORT_FALLBACK_COUNT": "0",
        "WORKSPACE_MCP_HOST": "127.0.0.1",
        "GOOGLE_OAUTH_REDIRECT_URI": GOOGLE_WORKSPACE_REDIRECT_URI,
        "OAUTHLIB_INSECURE_TRANSPORT": "1",
        "WORKSPACE_MCP_MAX_FILE_BYTES": "1048576",
    }
    if client_id is not None:
        environment["GOOGLE_OAUTH_CLIENT_ID"] = client_id
    server = McpServerConfiguration(
        server_id="google-workspace",
        command=executable,
        arguments=GOOGLE_WORKSPACE_ARGUMENTS,
        working_directory=project,
        environment=environment,
        capability=Capability.WEB_READ,
        read_only=False,
        integration=scope,
        approved_tools=GOOGLE_WORKSPACE_REVIEWED_SCHEMAS,
        secret_environment={"GOOGLE_OAUTH_CLIENT_SECRET": "oauth-client"},
        allow_explicit_requests=True,
        artifact_sha256=pins,
        start_timeout_seconds=30,
        # A token refresh plus a batched header fetch can exceed 15 s on a slow link.
        call_timeout_seconds=GOOGLE_WORKSPACE_CALL_TIMEOUT_SECONDS,
    )
    path = destination / "google-workspace.json"
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(descriptor, "wb") as handle:
        handle.write(McpHostConfiguration(servers=(server,)).model_dump_json(indent=2).encode())
    if not stat.S_ISREG(path.stat().st_mode):
        raise McpConfigurationError("Google Workspace profile is not a regular file")
    return {
        "status": "disabled_candidate_created",
        "profile": str(path),
        "account": account,
        "oauth_client_configured": client_id is not None,
        "approved_tools": len(GOOGLE_WORKSPACE_REVIEWED_SCHEMAS),
        "live_configuration_changed": False,
        "accounts_connected": 0,
    }
