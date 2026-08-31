from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

from aegis_core.contracts import AgentRole, Capability, RiskLevel, ToolCall
from aegis_core.mcp.client import (
    McpConfigurationError,
    McpHost,
    McpHostConfiguration,
    McpServerConfiguration,
    load_mcp_configuration,
)
from aegis_core.tools.defaults import build_default_tool_broker, default_policy_context
from aegis_core.tools.execution import ReadOnlyToolExecutor


def server_configuration() -> McpServerConfiguration:
    fixture = Path(__file__).parent / "fixtures/mcp_stdio_server.py"
    return McpServerConfiguration(
        server_id="test-server",
        command=Path(sys.executable).resolve(),
        arguments=(str(fixture),),
        capability=Capability.SYSTEM_READ,
        risk=RiskLevel.MEDIUM,
    )


@pytest.mark.asyncio
async def test_stdio_host_discovers_authorizes_and_relays_tool(tmp_path: Path) -> None:
    host = McpHost(McpHostConfiguration(servers=(server_configuration(),)))
    await host.start()
    try:
        definitions = host.tool_definitions()
        assert len(definitions) == 1
        assert definitions[0].name == "mcp_test_server__read_status"
        broker = build_default_tool_broker(definitions)
        call = ToolCall(
            call_id="mcp-call-1",
            tool_name=definitions[0].name,
            arguments={"scope": "local"},
            requested_by=AgentRole.PLANNER,
        )
        authorization = broker.authorize(call, default_policy_context(tmp_path))
        executor = ReadOnlyToolExecutor(extra_async_handlers=host.handlers())
        result = await executor.execute_async(
            authorization,
            default_policy_context(tmp_path),
        )
        assert result.success
        assert result.metadata["source"] == "mcp_2025_11_25_stdio"
        assert json.loads(result.output)["content"][0]["text"] == "status:local"
    finally:
        await host.close()


@pytest.mark.asyncio
async def test_stdio_host_rejects_arguments_before_server_call(tmp_path: Path) -> None:
    host = McpHost(McpHostConfiguration(servers=(server_configuration(),)))
    await host.start()
    try:
        definition = host.tool_definitions()[0]
        broker = build_default_tool_broker((definition,))
        authorization = broker.authorize(
            ToolCall(
                call_id="mcp-invalid-1",
                tool_name=definition.name,
                arguments={"scope": "local", "unexpected": True},
                requested_by=AgentRole.PLANNER,
            ),
            default_policy_context(tmp_path),
        )
        assert authorization.reason_code == "invalid_arguments"
    finally:
        await host.close()


def test_configuration_file_must_be_private(tmp_path: Path) -> None:
    path = tmp_path / "mcp_servers.json"
    path.write_text('{"servers":[]}', encoding="utf-8")
    os.chmod(path, 0o644)
    with pytest.raises(McpConfigurationError):
        load_mcp_configuration(path)
    os.chmod(path, 0o600)
    assert load_mcp_configuration(path).servers == ()
