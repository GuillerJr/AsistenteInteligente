"""Explicit administrative discovery. Never enables a server or calls its tools."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from aegis_core.mcp.client import (
    McpConfigurationError,
    StdioMcpClient,
    load_mcp_configuration,
    reviewed_schema_sha256,
)
from aegis_core.mcp.integrations import IntegrationId, integration_tools


async def discover_configuration(path: Path) -> dict[str, Any]:
    configuration = load_mcp_configuration(path)
    if len(configuration.servers) != 1:
        raise McpConfigurationError("Discover one explicitly chosen peer at a time")
    server = configuration.servers[0]
    if server.integration is None:
        raise McpConfigurationError("Discovery requires a curated integration")
    # Catalogue inspection never needs the Google OAuth client or an OAuth session.
    # Keep the reviewed profile unchanged and suppress both client settings here.
    discovery_server = (
        server.model_copy(update={
            "secret_environment": {},
            "environment": {
                name: value for name, value in server.environment.items()
                if name != "GOOGLE_OAUTH_CLIENT_ID"
            },
        })
        if server.integration.provider is IntegrationId.GOOGLE and not server.enabled
        else server
    )
    client = StdioMcpClient(discovery_server)
    if server.home_assistant is not None:
        from aegis_core.mcp.home_assistant import HomeAssistantMcpClient

        client = HomeAssistantMcpClient(server)
    try:
        tools = await client.start()
        reviewed, incompatible = {}, []
        allowed = integration_tools(server.integration.provider)
        for tool in tools:
            name = tool["name"]
            if name not in allowed:
                continue
            if server.browser_session is not None and name == "browser_close":
                continue
            try:
                reviewed[name] = reviewed_schema_sha256(
                    tool["inputSchema"],
                    server.integration,
                    name,
                )
            except (ValueError, RuntimeError, KeyError, TypeError):
                incompatible.append(name)
        return {
            "server_id": server.server_id,
            "status": "discovered_not_approved",
            "candidate_schema_sha256": reviewed,
            "incompatible_tools": incompatible,
            "tools_called": 0,
            "configuration_changed": False,
            "warning": "Executable and upstream defaults still require local review",
        }
    finally:
        await client.close()
