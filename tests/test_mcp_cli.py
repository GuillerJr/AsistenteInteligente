from __future__ import annotations

import json
from types import SimpleNamespace

from test_mcp_host import STATUS_SCHEMA, server_configuration

from aegis_core import cli
from aegis_core.config import Settings
from aegis_core.mcp.client import McpHostConfiguration


def test_offline_cli_reports_absence_and_invalid_configuration(tmp_path, monkeypatch, capsys):
    path = tmp_path / "mcp.json"
    monkeypatch.setenv("AEGIS_MCP_CONFIGURATION_PATH", str(path))
    assert cli.mcp_check() == 0
    assert json.loads(capsys.readouterr().out)["servers"] == []
    path.write_text("invalid")
    path.chmod(0o600)
    assert cli.mcp_check() == 1
    assert json.loads(capsys.readouterr().out)["reason_code"] == "invalid_configuration"


def test_offline_cli_does_not_claim_live_availability(tmp_path, monkeypatch, capsys):
    path = tmp_path / "mcp.json"
    monkeypatch.setenv("AEGIS_MCP_CONFIGURATION_PATH", str(path))
    path.write_text(McpHostConfiguration(servers=(server_configuration(),)).model_dump_json())
    path.chmod(0o600)
    assert cli.mcp_check() == 0
    report = json.loads(capsys.readouterr().out)
    assert report["servers"][0]["state"] == "awaiting_connection"
    assert report["external_certification"] is False
    assert report["processes_started"] == 0


def test_schema_cli_handles_invalid_and_valid_schema(tmp_path, capsys):
    path = tmp_path / "schema.json"
    assert cli.mcp_schema(path) == 1
    assert "invalid_mcp_schema" in capsys.readouterr().out
    path.write_text(json.dumps(STATUS_SCHEMA))
    assert cli.mcp_schema(path) == 0
    assert json.loads(capsys.readouterr().out)["status"] == "fingerprinted_not_approved"
    path.write_text('{"type":"object","$ref":"https://invalid.example/schema"}')
    assert cli.mcp_schema(path) == 1


async def test_runtime_cli_does_not_create_a_key_or_start_daemon(monkeypatch, capsys):
    calls = []

    def authenticator(settings: Settings, *, create: bool):
        assert create is False
        return object()

    class Client:
        def __init__(self, *args, **kwargs):
            pass

        async def call(self, method):
            calls.append(method)
            return SimpleNamespace(
                ok=True,
                payload={"configuration_valid": True, "servers": [{"state": "quarantined"}]},
            )

    monkeypatch.setattr(cli, "_ipc_authenticator", authenticator)
    monkeypatch.setattr(cli, "IpcClient", Client)
    assert await cli.mcp_status() == 1
    assert calls == ["mcp.status"]
    assert json.loads(capsys.readouterr().out)["servers"][0]["state"] == "quarantined"


async def test_runtime_cli_handles_old_daemon_without_the_status_method(monkeypatch, capsys):
    monkeypatch.setattr(cli, "_ipc_authenticator", lambda *a, **kw: object())

    class Client:
        def __init__(self, *args, **kwargs):
            pass

        async def call(self, method):
            return SimpleNamespace(ok=False, error_code="method_not_found", payload={})

    monkeypatch.setattr(cli, "IpcClient", Client)
    assert await cli.mcp_status() == 1
    assert "mcp_status_unavailable" in capsys.readouterr().out
