"""Offline MCP admission checks. These functions never start a peer or access secrets."""

from __future__ import annotations

import json
import os
import stat
from pathlib import Path
from typing import Any

from aegis_core.mcp.client import (
    MAX_MCP_CONFIG_BYTES,
    McpConfigurationError,
    _validate_executable,
    load_mcp_configuration,
    mcp_schema_sha256,
    validate_integration_process,
)
from aegis_core.mcp.integrations import IntegrationId


def configuration_report(path: Path) -> dict[str, Any]:
    report: dict[str, Any] = {
        "scope": "stdio_configuration_only",
        "configuration_valid": False,
        "servers": [],
        "processes_started": 0,
        "network_requests": 0,
        "external_certification": False,
        "contains_user_content": False,
    }
    try:
        configuration = load_mcp_configuration(path)
    except (OSError, McpConfigurationError):
        report["reason_code"] = "invalid_configuration"
        return report
    report["configuration_valid"] = True
    for server in configuration.servers:
        blockers = []
        if not server.approved_tools:
            blockers.append("tool_review_required")
        if not server.application_bundle_ids and not server.allow_explicit_requests:
            blockers.append("application_scope_required")
        if server.integration is not None:
            blockers.extend(server.integration.blockers())
            if (server.integration.provider is IntegrationId.HOME_ASSISTANT
                    and server.home_assistant is None):
                blockers.append("home_assistant_endpoint_required")
        if server.home_assistant is None:
            try:
                _validate_executable(server)
            except McpConfigurationError:
                blockers.append("executable_or_working_directory_unavailable")
            if server.integration is not None:
                try:
                    validate_integration_process(server)
                except McpConfigurationError:
                    blockers.append("launch_artifacts_or_isolation_require_review")
        report["servers"].append(
            {
                "server_id": server.server_id,
                "enabled": server.enabled,
                "read_only": server.read_only,
                "approved_tools": len(server.approved_tools),
                "application_scopes": len(server.application_bundle_ids),
                "integration": server.integration.provider.value if server.integration else None,
                "explicit_requests": server.allow_explicit_requests,
                "credential_references": len(server.secret_environment),
                "blockers": blockers,
                "state": (
                    "disabled"
                    if not server.enabled
                    else "blocked"
                    if blockers
                    else "awaiting_connection"
                ),
            }
        )
    return report


def review_input_schema(path: Path) -> dict[str, str]:
    """Fingerprint a manually reviewed inputSchema JSON; not an approval or installation."""
    try:
        info = path.lstat()
        if (
            not stat.S_ISREG(info.st_mode)
            or info.st_uid != os.getuid()
            or info.st_mode & 0o022
            or not 0 < info.st_size <= MAX_MCP_CONFIG_BYTES
        ):
            raise McpConfigurationError("MCP schema file is unsafe")
        descriptor = os.open(path, os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW | os.O_CLOEXEC)
        try:
            current = os.fstat(descriptor)
            if (current.st_dev, current.st_ino, current.st_mode, current.st_uid) != (
                info.st_dev,
                info.st_ino,
                info.st_mode,
                info.st_uid,
            ):
                raise McpConfigurationError("MCP schema file changed")
            with os.fdopen(descriptor, "rb", closefd=False) as source:
                raw = source.read(MAX_MCP_CONFIG_BYTES + 1)
        finally:
            os.close(descriptor)
        if len(raw) > MAX_MCP_CONFIG_BYTES:
            raise McpConfigurationError("MCP schema is too large")
        schema = json.loads(raw)
        if not isinstance(schema, dict):
            raise McpConfigurationError("MCP inputSchema must be an object")
        return {
            "input_schema_sha256": mcp_schema_sha256(schema),
            "status": "fingerprinted_not_approved",
        }
    except (OSError, ValueError, RecursionError) as error:
        raise McpConfigurationError("MCP inputSchema could not be reviewed") from error
