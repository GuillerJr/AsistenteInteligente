from __future__ import annotations

import json
import os
import sys
from dataclasses import replace
from pathlib import Path

import pytest

from aegis_core.contracts import AgentRole, Capability, PolicyDecision, RiskLevel, ToolCall
from aegis_core.mcp.client import (
    McpConfigurationError,
    McpHost,
    McpHostConfiguration,
    McpServerConfiguration,
    load_mcp_configuration,
    mcp_schema_sha256,
)
from aegis_core.tools.confirmations import OneTimeConfirmationStore
from aegis_core.tools.defaults import build_default_tool_broker, default_policy_context
from aegis_core.tools.execution import ReadOnlyToolExecutor

STATUS_SCHEMA = {
    "type": "object",
    "properties": {"scope": {"type": "string", "maxLength": 32}},
    "required": ["scope"],
    "additionalProperties": False,
}


def server_configuration(**overrides) -> McpServerConfiguration:
    fixture = Path(__file__).parent / "fixtures/mcp_stdio_server.py"
    values = dict(
        server_id="test-server",
        command=Path(sys.executable).resolve(),
        arguments=(str(fixture),),
        capability=Capability.SYSTEM_READ,
        risk=RiskLevel.MEDIUM,
        enabled=True,
        approved_tools={"read.status": mcp_schema_sha256(STATUS_SCHEMA)},
        application_bundle_ids={"com.apple.Terminal"},
    )
    values.update(overrides)
    return McpServerConfiguration(**values)


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
        assert authorization.decision is PolicyDecision.REQUIRE_CONFIRMATION
        store = OneTimeConfirmationStore()
        store.issue(call, authorization, approved_by="local-user")
        context = replace(default_policy_context(tmp_path), confirmation_store=store)
        authorization = broker.authorize(call, context)
        executor = ReadOnlyToolExecutor(extra_async_handlers=host.handlers())
        result = await executor.execute_async(
            authorization,
            context,
        )
        assert result.success
        assert result.metadata["source"] == "mcp_2025_11_25_stdio"
        assert json.loads(result.output)["content"][0]["text"] == "status:local"
        assert broker.authorize(call, context).reason_code == "confirmation_replayed"
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


class RecordingAuditSink:
    def __init__(self) -> None:
        self.events: list[tuple[str, dict]] = []

    def record_system_event(self, request_id, *, event_type, component, data, call_id=None):
        self.events.append((event_type, dict(data)))

    def __getattr__(self, name):
        return lambda *args, **kwargs: None


async def call_scope(host: McpHost, tmp_path: Path, scope: str):
    definition = host.tool_definitions()[0]
    authorization = build_default_tool_broker((definition,)).authorize(
        ToolCall(
            call_id=f"mcp-{scope}",
            tool_name=definition.name,
            arguments={"scope": scope},
            requested_by=AgentRole.PLANNER,
        ),
        default_policy_context(tmp_path),
    )
    authorization = authorization.model_copy(
        update={"decision": PolicyDecision.ALLOW, "reason_code": "confirmation_consumed"}
    )
    return await host.handlers()[definition.name](authorization, default_policy_context(tmp_path))


def slow_scope_server() -> McpServerConfiguration:
    fixture = Path(__file__).parent / "fixtures/mcp_stdio_server.py"
    return server_configuration(
        arguments=(str(fixture), "hang-slow-scope"), call_timeout_seconds=1.0,
    )


@pytest.mark.asyncio
async def test_timed_out_server_relaunches_on_next_use(tmp_path: Path) -> None:
    audit = RecordingAuditSink()
    host = McpHost(McpHostConfiguration(servers=(slow_scope_server(),)), audit_sink=audit)
    await host.start()
    try:
        failed = await call_scope(host, tmp_path, "slow")
        assert failed.error_code == "mcp_execution_failed"
        first_child = host._clients["test-server"]
        assert not first_child.available
        # The reviewed tools stay offered and the status does not ask for a review.
        assert [d.name for d in host.tool_definitions()] == ["mcp_test_server__read_status"]
        assert host.status()["servers"][0]["state"] == "active"

        result = await call_scope(host, tmp_path, "local")
        assert result.success
        assert json.loads(result.output)["content"][0]["text"] == "status:local"
        assert host._clients["test-server"] is not first_child
        assert ("mcp_server_restarted", {
            "server_id": "test-server", "reason_code": "call_failed",
            "restarts_in_window": 1, "contains_user_content": False,
        }) in audit.events
    finally:
        await host.close()


@pytest.mark.asyncio
async def test_relaunches_stop_after_budget(tmp_path: Path) -> None:
    host = McpHost(McpHostConfiguration(servers=(slow_scope_server(),)))
    await host.start()
    try:
        for _ in range(4):
            assert (await call_scope(host, tmp_path, "slow")).error_code == (
                "mcp_execution_failed"
            )
        assert host.tool_definitions() == ()
        server = host.status()["servers"][0]
        assert (server["state"], server["reason_code"]) == ("quarantined", "call_failed")
    finally:
        await host.close()


@pytest.mark.asyncio
async def test_changed_catalogue_is_never_relaunched(tmp_path: Path) -> None:
    fixture = Path(__file__).parent / "fixtures/mcp_stdio_server.py"
    host = McpHost(McpHostConfiguration(servers=(
        server_configuration(arguments=(str(fixture), "catalogue-change")),
    )))
    await host.start()
    try:
        handler = host.handlers()["mcp_test_server__read_status"]
        definition = host.tool_definitions()[0]
        assert (await call_scope(host, tmp_path, "local")).error_code == "mcp_execution_failed"
        failed = host._clients["test-server"]
        assert host.tool_definitions() == ()
        authorization = build_default_tool_broker((definition,)).authorize(
            ToolCall(call_id="cached", tool_name=definition.name,
                     arguments={"scope": "local"}, requested_by=AgentRole.PLANNER),
            default_policy_context(tmp_path),
        ).model_copy(
            update={"decision": PolicyDecision.ALLOW, "reason_code": "confirmation_consumed"}
        )
        result = await handler(authorization, default_policy_context(tmp_path))
        assert result.error_code == "mcp_server_unavailable"
        assert host._clients["test-server"] is failed
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
