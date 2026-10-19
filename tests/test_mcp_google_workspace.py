from pathlib import Path

import pytest

from aegis_core.mcp.client import (
    McpConfigurationError,
    load_mcp_configuration,
    validate_integration_process,
)
from aegis_core.mcp.google_workspace import (
    _PINNED_MODULES,
    GOOGLE_WORKSPACE_REVIEWED_SCHEMAS,
    GOOGLE_WORKSPACE_VERSION,
    prepare_google_workspace_profile,
)
from aegis_core.mcp.review import configuration_report


def installed_fixture(tmp_path: Path) -> Path:
    project = tmp_path / "installed-peer"
    package = project / ".venv/lib/python3.11/site-packages"
    executable = project / ".venv/bin/workspace-mcp"
    executable.parent.mkdir(parents=True)
    executable.write_text("#!/bin/sh\nexit 0\n")
    executable.chmod(0o700)
    (project / "uv.lock").write_text("pinned-lock")
    metadata = package / f"workspace_mcp-{GOOGLE_WORKSPACE_VERSION}.dist-info/METADATA"
    metadata.parent.mkdir(parents=True)
    metadata.write_text(f"Name: workspace-mcp\nVersion: {GOOGLE_WORKSPACE_VERSION}\n")
    for name in _PINNED_MODULES:
        module = package / name
        module.parent.mkdir(parents=True, exist_ok=True)
        module.write_text(f"# {name}\n")
    return project


def test_google_candidate_is_private_scoped_reviewed_and_disabled(tmp_path):
    project = installed_fixture(tmp_path)
    destination = tmp_path / "private"
    result = prepare_google_workspace_profile(
        destination, project=project, account="owner@example.com"
    )
    assert result["accounts_connected"] == 0
    assert result["approved_tools"] == len(GOOGLE_WORKSPACE_REVIEWED_SCHEMAS)
    assert destination.stat().st_mode & 0o077 == 0
    path = destination / "google-workspace.json"
    assert path.stat().st_mode & 0o077 == 0
    (server,) = load_mcp_configuration(path).servers
    assert not server.enabled
    assert server.integration.google_accounts == {"owner@example.com"}
    assert server.approved_tools == GOOGLE_WORKSPACE_REVIEWED_SCHEMAS
    assert "send_gmail_message" not in server.approved_tools
    assert "delete_event" not in server.approved_tools
    assert "GOOGLE_OAUTH_CLIENT_SECRET" not in server.environment
    validate_integration_process(server)
    report = configuration_report(path)["servers"][0]
    assert report["state"] == "disabled"
    assert "google_oauth_client_id_required" in report["blockers"]


def test_google_profile_rejects_package_drift_and_unreviewed_launcher(tmp_path):
    project = installed_fixture(tmp_path)
    destination = tmp_path / "private"
    prepare_google_workspace_profile(destination, project=project, account="owner@example.com")
    (server,) = load_mcp_configuration(destination / "google-workspace.json").servers
    changed = server.model_copy(update={"arguments": (*server.arguments, "--read-only")})
    with pytest.raises(McpConfigurationError):
        validate_integration_process(changed)
    module = project / ".venv/lib/python3.11/site-packages/gdrive/drive_tools.py"
    module.write_text("# changed upstream code\n")
    with pytest.raises(McpConfigurationError):
        validate_integration_process(server)


def test_google_profile_requires_exact_client_id_and_new_directory(tmp_path):
    project = installed_fixture(tmp_path)
    destination = tmp_path / "private"
    with pytest.raises(ValueError):
        prepare_google_workspace_profile(
            destination, project=project, account="owner@example.com", client_id="placeholder"
        )
    client_id = "synthetic-id.apps.googleusercontent.com"
    prepare_google_workspace_profile(
        destination, project=project, account="owner@example.com", client_id=client_id
    )
    (server,) = load_mcp_configuration(destination / "google-workspace.json").servers
    assert server.environment["GOOGLE_OAUTH_CLIENT_ID"] == client_id
    assert "google_oauth_client_id_required" not in configuration_report(
        destination / "google-workspace.json"
    )["servers"][0]["blockers"]
    with pytest.raises(ValueError):
        prepare_google_workspace_profile(
            destination, project=project, account="owner@example.com", client_id=client_id
        )
