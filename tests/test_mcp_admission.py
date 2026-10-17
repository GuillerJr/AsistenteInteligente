from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path

import pytest
from pydantic import ValidationError
from test_mcp_host import STATUS_SCHEMA, server_configuration

from aegis_core.contracts import AgentRole, Capability, PolicyDecision, ToolCall
from aegis_core.mcp.client import (
    McpConfigurationError,
    McpExecutionError,
    McpHost,
    McpHostConfiguration,
    McpProtocolError,
    McpServerConfiguration,
    StdioMcpClient,
    _sanitize_call_result,
    load_mcp_configuration,
    mcp_schema_sha256,
)
from aegis_core.mcp.review import configuration_report, review_input_schema
from aegis_core.tools.defaults import build_default_tool_broker, default_policy_context


def config_for_mode(mode: str, **kwargs) -> McpServerConfiguration:
    normal = server_configuration()
    return server_configuration(arguments=(*normal.arguments, mode), **kwargs)


@pytest.mark.parametrize(
    "mode", ["schema-drift", "mutating", "missing", "duplicate", "cursor-loop"]
)
async def test_bad_peer_is_quarantined_without_disabling_good_peer(mode: str) -> None:
    host = McpHost(
        McpHostConfiguration(
            servers=(config_for_mode(mode), server_configuration(server_id="good-server"))
        )
    )
    await host.start()
    try:
        assert [d.name for d in host.tool_definitions()] == ["mcp_good_server__read_status"]
        states = host.status()["servers"]
        assert states[0]["state"] == "quarantined"
        assert states[0]["available_tools"] == 0
        assert states[1]["state"] == "active"
    finally:
        await host.close()


async def test_unreviewed_extra_tool_is_never_registered() -> None:
    host = McpHost(McpHostConfiguration(servers=(config_for_mode("extra"),)))
    await host.start()
    try:
        assert set(host.handlers()) == {"mcp_test_server__read_status"}
    finally:
        await host.close()


@pytest.mark.parametrize(
    "fields", [{"enabled": False}, {"approved_tools": {}}, {"application_bundle_ids": []}]
)
async def test_unapproved_or_disabled_servers_never_start(monkeypatch, fields) -> None:
    async def forbidden(*args, **kwargs):
        pytest.fail("unapproved process started")

    monkeypatch.setattr(asyncio, "create_subprocess_exec", forbidden)
    host = McpHost(McpHostConfiguration(servers=(server_configuration(**fields),)))
    await host.start()
    assert host.tool_definitions() == ()
    assert host.status()["servers"][0]["state"] in {"disabled", "quarantined"}
    await host.close()


async def test_invalid_private_configuration_isolated_from_daemon(tmp_path: Path) -> None:
    path = tmp_path / "mcp.json"
    path.write_text('{"servers": ["SECRET-value"]}')
    path.chmod(0o600)
    host = McpHost.from_file(path)
    await host.start()
    assert host.tool_definitions() == ()
    assert host.status()["configuration_valid"] is False
    assert "SECRET-value" not in json.dumps(host.status())
    await host.close()


async def test_cancellation_during_start_reaps_child(monkeypatch) -> None:
    processes = []
    original = asyncio.create_subprocess_exec

    async def track(*args, **kwargs):
        process = await original(*args, **kwargs)
        processes.append(process)
        return process

    monkeypatch.setattr(asyncio, "create_subprocess_exec", track)
    host = McpHost(McpHostConfiguration(servers=(config_for_mode("hang-start"),)))
    task = asyncio.create_task(host.start())
    async with asyncio.timeout(3):
        while not processes:
            await asyncio.sleep(0.01)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(task, 3)
    assert processes[0].returncode is not None
    assert host.handlers() == {}


async def test_cancellation_during_call_reaps_child() -> None:
    client = StdioMcpClient(config_for_mode("hang-call"))
    await client.start()
    process = client._process
    task = asyncio.create_task(client.call_tool("read.status", {"scope": "test"}))
    await asyncio.sleep(0.02)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(task, 3)
    assert process.returncode is not None
    assert not client.available


async def test_catalogue_change_revokes_visibility_and_cached_handler(tmp_path: Path) -> None:
    host = McpHost(McpHostConfiguration(servers=(config_for_mode("catalogue-change"),)))
    await host.start()
    definition = host.tool_definitions()[0]
    handler = host.handlers()[definition.name]
    try:
        client = host._clients["test-server"]
        with pytest.raises(McpExecutionError):
            await client.call_tool("read.status", {"scope": "synthetic"})
        assert host.tool_definitions() == ()
        assert not host.tool_names_for_application("com.apple.Terminal")
        assert host.status()["servers"][0]["reason_code"] == "catalogue_changed"
        authorization = build_default_tool_broker((definition,)).authorize(
            ToolCall(
                call_id="cached",
                tool_name=definition.name,
                requested_by=AgentRole.PLANNER,
                arguments={"scope": "synthetic"},
            ),
            default_policy_context(tmp_path),
        )
        authorization = authorization.model_copy(
            update={"decision": PolicyDecision.ALLOW, "reason_code": "confirmation_consumed"}
        )
        result = await handler(authorization, default_policy_context(tmp_path))
        assert result.error_code == "mcp_server_unavailable"
    finally:
        await host.close()


@pytest.mark.parametrize("flag", ["false", 0, 1, None, {}, []])
def test_error_flag_must_be_boolean(flag) -> None:
    with pytest.raises(McpProtocolError):
        _sanitize_call_result({"content": [], "isError": flag})


@pytest.mark.parametrize(
    "name",
    [
        "PATH",
        "HOME",
        "PYTHONPATH",
        "DYLD_INSERT_LIBRARIES",
        "LD_PRELOAD",
        "NODE_OPTIONS",
        "ACCESS_TOKEN",
    ],
)
def test_peer_environment_cannot_override_runtime_or_supply_secrets(name: str) -> None:
    with pytest.raises(ValidationError):
        server_configuration(environment={name: "private-value"})


def test_read_only_never_admits_a_write_capability() -> None:
    with pytest.raises(ValidationError):
        server_configuration(capability=Capability.MAIL_WRITE)
    raw = server_configuration().model_dump()
    raw.pop("enabled")
    assert McpServerConfiguration.model_validate(raw).enabled is False
    for fields in (
        {"enabled": "true"},
        {"read_only": 1},
        {"approved_tools": {"*": "0" * 64}},
        {"application_bundle_ids": ["*"]},
    ):
        with pytest.raises(ValidationError):
            server_configuration(**fields)


def test_configuration_dangling_symlink_and_fifo_are_not_treated_as_absent(tmp_path: Path) -> None:
    link = tmp_path / "link"
    link.symlink_to(tmp_path / "absent")
    fifo = tmp_path / "fifo"
    os.mkfifo(fifo, 0o600)
    for path in (link, fifo):
        with pytest.raises(McpConfigurationError):
            load_mcp_configuration(path)


def test_offline_check_does_not_execute_and_does_not_leak_arguments(tmp_path, monkeypatch) -> None:
    def forbidden(*args, **kwargs):
        pytest.fail("offline review attempted execution")

    monkeypatch.setattr(asyncio, "create_subprocess_exec", forbidden)
    path = tmp_path / "config.json"
    configuration = server_configuration(arguments=("sensitive-private-path",))
    path.write_text(McpHostConfiguration(servers=(configuration,)).model_dump_json())
    path.chmod(0o600)
    report = configuration_report(path)
    assert report["servers"][0]["state"] == "awaiting_connection"
    assert report["external_certification"] is False
    assert report["network_requests"] == report["processes_started"] == 0
    assert "sensitive-private-path" not in json.dumps(report)


def test_schema_fingerprint_is_reproducible_and_not_an_approval(tmp_path) -> None:
    path = tmp_path / "schema.json"
    path.write_text(json.dumps(STATUS_SCHEMA, indent=2))
    report = review_input_schema(path)
    assert report["status"] == "fingerprinted_not_approved"
    assert report["input_schema_sha256"] == mcp_schema_sha256(STATUS_SCHEMA)
    path.chmod(0o666)
    with pytest.raises(McpConfigurationError):
        review_input_schema(path)


async def test_oversized_stdout_does_not_deadlock_cleanup() -> None:
    host = McpHost(McpHostConfiguration(servers=(config_for_mode("stdout-flood"),)))
    try:
        await asyncio.wait_for(host.start(), timeout=4)
        assert host.status()["servers"][0]["state"] == "quarantined"
        assert host.tool_definitions() == ()
    finally:
        await asyncio.wait_for(host.close(), timeout=4)


async def test_close_is_idempotent_and_joins_cleanup() -> None:
    client = StdioMcpClient(server_configuration())
    await client.start()
    process = client._process
    await asyncio.wait_for(asyncio.gather(client.close(), client.close()), timeout=3)
    assert process.returncode is not None
    assert client._close_task.done()
    assert not client.available


async def test_server_timeout_is_local_to_that_server() -> None:
    host = McpHost(
        McpHostConfiguration(
            servers=(
                config_for_mode("hang-start", start_timeout_seconds=1),
                server_configuration(server_id="healthy-peer"),
            )
        )
    )
    await asyncio.wait_for(host.start(), 4)
    try:
        assert host.status()["servers"][0]["state"] == "quarantined"
        assert host.status()["servers"][1]["state"] == "active"
    finally:
        await host.close()
