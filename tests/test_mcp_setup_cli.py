from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from aegis_core import cli
from aegis_core.cli_entrypoint import run


@pytest.mark.parametrize(
    "command,handler_name,arguments",
    [
        (["mcp-integrations"], "mcp_integrations", ()),
        (
            ["mcp-discover", "/private/candidate.json"],
            "mcp_discover",
            (Path("/private/candidate.json"),),
        ),
        (["mcp-prepare-local", "/private/new"], "mcp_prepare_local", (Path("/private/new"),)),
        (
            ["mcp-prepare-google", "/private/new", "--account", "owner@example.com"],
            "mcp_prepare_google",
            (Path("/private/new"), "owner@example.com", None),
        ),
        (
            [
                "mcp-credential-set",
                "/private/candidate.json",
                "--connector",
                "HOME_ASSISTANT_TOKEN",
            ],
            "mcp_credential_set",
            (Path("/private/candidate.json"), "HOME_ASSISTANT_TOKEN"),
        ),
    ],
)
def test_cli_routes_mcp_administration(command, handler_name, arguments):
    calls = []

    def handler(*args):
        calls.append(args)
        return 23

    assert run(command, handlers=SimpleNamespace(**{handler_name: handler})) == 23
    assert calls == [arguments]


def test_catalogue_cli_does_not_claim_connections(capsys):
    assert cli.mcp_integrations() == 0
    result = json.loads(capsys.readouterr().out)
    assert result["status"] == "profiles_available_not_connected"


def test_discovery_cli_rejects_missing_file_without_starting_peer(tmp_path, capsys, monkeypatch):
    async def forbidden(*args, **kwargs):
        pytest.fail("invalid configuration started a process")

    monkeypatch.setattr("asyncio.create_subprocess_exec", forbidden)
    assert cli.mcp_discover(tmp_path / "absent.json") == 1
    assert "mcp_discovery_failed" in capsys.readouterr().out


def test_credentials_cannot_be_piped_and_never_echoed(tmp_path, capsys, monkeypatch):
    path = tmp_path / "config.json"
    path.write_text(
        json.dumps(
            {
                "servers": [
                    {
                        "server_id": "github",
                        "command": "/usr/bin/false",
                        "capability": "web_read",
                        "integration": {"provider": "github"},
                        "secret_environment": {"GITHUB_PERSONAL_ACCESS_TOKEN": "read-only-token"},
                    }
                ]
            }
        )
    )
    path.chmod(0o600)
    monkeypatch.setattr("sys.stdin.isatty", lambda: False)
    monkeypatch.setattr("getpass.getpass", lambda *a: pytest.fail("should not prompt"))
    assert cli.mcp_credential_set(path, "GITHUB_PERSONAL_ACCESS_TOKEN") == 1
    assert "interactive_terminal_required" in capsys.readouterr().out


def test_keychain_scope_is_dedicated_to_configured_peer(tmp_path, capsys, monkeypatch):
    path = tmp_path / "config.json"
    path.write_text(
        json.dumps(
            {
                "servers": [
                    {
                        "server_id": "github",
                        "command": "/usr/bin/false",
                        "capability": "web_read",
                        "integration": {"provider": "github"},
                        "secret_environment": {"GITHUB_PERSONAL_ACCESS_TOKEN": "read-only-token"},
                    }
                ]
            }
        )
    )
    path.chmod(0o600)
    saved = []
    monkeypatch.setattr("sys.stdin.isatty", lambda: True)
    monkeypatch.setattr("getpass.getpass", lambda *a: "synthetic-secret-never-printed")
    monkeypatch.setattr(
        "aegis_core.secrets.MacOSGenericSecret.set",
        lambda self, value: saved.append((self.service, self.account, value)),
    )
    assert cli.mcp_credential_set(path, "GITHUB_PERSONAL_ACCESS_TOKEN") == 0
    assert saved == [("ai.aegis.mcp.github", "read-only-token", "synthetic-secret-never-printed")]
    assert "synthetic-secret-never-printed" not in capsys.readouterr().out


def test_credential_cli_reports_invalid_profile_before_prompt(tmp_path, capsys, monkeypatch):
    monkeypatch.setattr("getpass.getpass", lambda *a: pytest.fail("should not prompt"))
    assert cli.mcp_credential_set(tmp_path / "missing.json", "GOOGLE_OAUTH_CLIENT_SECRET") == 1
    assert "reason=mcp_configuration_invalid" in capsys.readouterr().out


def test_credential_cli_reports_keychain_failure_without_echoing_secret(
    tmp_path, capsys, monkeypatch
):
    path = tmp_path / "config.json"
    path.write_text(
        json.dumps(
            {
                "servers": [
                    {
                        "server_id": "google-workspace",
                        "command": "/usr/bin/false",
                        "capability": "web_read",
                        "integration": {
                            "provider": "google-workspace",
                            "google_accounts": ["owner@example.com"],
                        },
                        "secret_environment": {"GOOGLE_OAUTH_CLIENT_SECRET": "oauth-client"},
                    }
                ]
            }
        )
    )
    path.chmod(0o600)
    monkeypatch.setattr("sys.stdin.isatty", lambda: True)
    monkeypatch.setattr("getpass.getpass", lambda *a: "synthetic-secret-never-printed")

    def fail_to_save(self, value):
        raise OSError("synthetic-secret-never-printed")

    monkeypatch.setattr("aegis_core.secrets.MacOSGenericSecret.set", fail_to_save)
    assert cli.mcp_credential_set(path, "GOOGLE_OAUTH_CLIENT_SECRET") == 1
    output = capsys.readouterr().out
    assert "reason=keychain_write_failed" in output
    assert "synthetic-secret-never-printed" not in output
