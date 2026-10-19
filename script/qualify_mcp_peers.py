#!/usr/bin/env python3
"""Opt-in real stdio probes: synthetic files and an empty isolated browser only.

No owner's documents, account credentials, network navigation, microphone,
physical device, or live daemon configuration is used or changed.
"""

from __future__ import annotations

import asyncio
import json
import shutil
import sys
from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from aegis_core.contracts import AgentRole, PolicyDecision, ToolCall
from aegis_core.mcp.client import (
    McpHost,
    McpHostConfiguration,
    StdioMcpClient,
    reviewed_schema_sha256,
)
from aegis_core.mcp.integrations import IntegrationId, IntegrationScope, integration_tools
from aegis_core.mcp.local_peers import local_peer_configuration
from aegis_core.tools.confirmations import OneTimeConfirmationStore
from aegis_core.tools.defaults import build_default_tool_broker, default_policy_context


async def probe(provider: IntegrationId, directory: Path) -> dict:
    packages = Path(__file__).resolve().parents[1] / "integrations/mcp"
    root = directory / provider.value
    root.mkdir(mode=0o700)
    fixture = root / "synthetic.txt"
    fixture.write_text("JARVIS_MCP_SYNTHETIC_OK", encoding="utf-8")
    scope = IntegrationScope(
        provider=provider,
        **(
            {"roots": (root,)}
            if provider is IntegrationId.FILESYSTEM
            else {"browser_origins": {"https://example.com"}}
        ),
    )
    configuration = local_peer_configuration(
        scope,
        node=Path(shutil.which("node") or "/missing/node"),
        packages=packages,
        working_directory=root,
    )
    discovery = StdioMcpClient(configuration)
    try:
        catalogue = await discovery.start()
    finally:
        await discovery.close()
    approvals = {
        t["name"]: reviewed_schema_sha256(t["inputSchema"], scope, t["name"])
        for t in catalogue
        if t["name"] in integration_tools(provider)
    }
    assert set(approvals) == set(integration_tools(provider)), "Curated catalogue changed"
    approved = configuration.model_copy(update={"enabled": True, "approved_tools": approvals})
    host = McpHost(McpHostConfiguration(servers=(approved,)))
    await host.start()
    try:
        definitions = host.tool_definitions()
        assert len(definitions) == len(approvals), host.status()
        remote = "read_text_file" if provider is IntegrationId.FILESYSTEM else "browser_snapshot"
        definition = next(d for d in definitions if d.name.endswith("__" + remote))
        broker = build_default_tool_broker(definitions)
        call = ToolCall(
            call_id="synthetic-mcp-probe",
            tool_name=definition.name,
            arguments={"path": str(fixture)} if provider is IntegrationId.FILESYSTEM else {},
            requested_by=(
                AgentRole.CODE_SECURITY
                if provider is IntegrationId.FILESYSTEM
                else AgentRole.PLANNER
            ),
        )
        context = default_policy_context(root)
        authorization = broker.authorize(call, context)
        assert authorization.decision is PolicyDecision.REQUIRE_CONFIRMATION, (
            authorization.decision,
            authorization.reason_code,
        )
        confirmations = OneTimeConfirmationStore()
        confirmations.issue(call, authorization, approved_by="synthetic-test-harness")
        context = replace(context, confirmation_store=confirmations)
        authorization = broker.authorize(call, context)
        assert authorization.decision is PolicyDecision.ALLOW
        result = await host.handlers()[definition.name](authorization, context)
        assert result.success, result.error_code
        if provider is IntegrationId.FILESYSTEM:
            assert "JARVIS_MCP_SYNTHETIC_OK" in result.output
        assert broker.authorize(call, context).reason_code == "confirmation_replayed"
        return {
            "provider": provider.value,
            "state": "synthetic_probe_passed",
            "admitted_tools": len(definitions),
            "real_tool_calls": 1,
            "user_data_read": False,
            "schema_sha256": approvals,
        }
    finally:
        await host.close()


async def main() -> None:
    with TemporaryDirectory(prefix="jarvis-mcp-probe-") as temporary:
        root = Path(temporary).resolve()
        for provider in (IntegrationId.FILESYSTEM, IntegrationId.PLAYWRIGHT):
            print(json.dumps(await probe(provider, root), sort_keys=True), flush=True)


if __name__ == "__main__":
    asyncio.run(main())
