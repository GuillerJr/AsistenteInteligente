from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from aegis_core.config import Settings
from aegis_core.ipc.protocol import IpcAuthenticator
from aegis_core.mcp.client import McpHostConfiguration, McpServerConfiguration
from aegis_core.mcp.integrations import BrowserSessionConfiguration, IntegrationId, IntegrationScope
from aegis_core.settings_service import SettingsIpcService
from aegis_core.settings_store import write_private_json

AUTH = IpcAuthenticator(b"s" * 32)


class Host:
    def __init__(self):
        self.suspend_for_settings = AsyncMock()

    def status(self):
        return {"servers": []}


class Keychain:
    def is_configured(self):
        return False


@pytest.fixture
def service(tmp_path):
    return SettingsIpcService(
        Settings(mcp_configuration_path=tmp_path / "mcp.json"), Host(), Keychain()
    )


def payload(**changes):
    result = {
        "origins": ["https://fixture.example"],
        "confirm": True,
        "session": {
            "mode": "extension",
            "selected_tab_url": "https://fixture.example/work",
            "remote_content_allowed": True,
        },
    }
    return {**result, **changes}


def fake_candidate(destination: Path, *, origins, session, **_):
    destination.mkdir(mode=0o700)
    server = McpServerConfiguration(
        server_id="playwright",
        command="/usr/bin/false",
        capability="web_read",
        integration=IntegrationScope(provider=IntegrationId.PLAYWRIGHT, browser_origins=origins),
        browser_session=session,
        allow_explicit_requests=True,
        read_only=False,
    )
    candidate = destination / "playwright.json"
    write_private_json(candidate, McpHostConfiguration(servers=(server,)).model_dump(mode="json"))
    return {
        "candidate_path": str(candidate),
        "configuration": server.model_dump(mode="json"),
        "requires_tab_selection": session.mode == "extension",
    }


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "change",
    [
        {"confirm": False},
        {"confirm": "yes"},
        {"origins": ["https://another.example"]},
        {"origins": []},
        {"session": {"mode": "extension", "remote_content_allowed": True}},
        {
            "session": {
                "mode": "cdp",
                "selected_tab_url": "https://fixture.example",
                "cdp_endpoint": "http://10.0.0.1:9222",
            }
        },
    ],
)
async def test_invalid_setup_never_starts_discovery(service, monkeypatch, change):
    discover = AsyncMock()
    monkeypatch.setattr("aegis_core.settings_service.discover_configuration", discover)
    result = await service.handle(
        AUTH.create_request("settings.browser.prepare", payload(**change))
    )
    assert not result.ok
    discover.assert_not_called()
    assert not service.settings.mcp_configuration_path.exists()


@pytest.mark.asyncio
async def test_repeated_failed_reviews_do_not_exhaust_profile_capacity(service, monkeypatch):
    monkeypatch.setattr("aegis_core.settings_service.prepare_browser_profile", fake_candidate)
    monkeypatch.setattr(
        "aegis_core.settings_service.discover_configuration",
        AsyncMock(return_value={"candidate_schema_sha256": {}}),
    )
    for _ in range(65):
        result = await service.handle(AUTH.create_request("settings.browser.prepare", payload()))
        assert result.ok
        await service.task
        assert service.operation["reason"] == "browser_schema_incompatible"
    assert (
        list((service.settings.mcp_configuration_path.parent / "browser-profiles").iterdir()) == []
    )


@pytest.mark.asyncio
async def test_profile_review_keeps_google_and_does_not_connect_or_call_page(service, monkeypatch):
    google = McpServerConfiguration(
        server_id="google-workspace",
        command="/usr/bin/false",
        capability="web_read",
        integration=IntegrationScope(
            provider=IntegrationId.GOOGLE, google_accounts={"owner@example.com"}
        ),
    )
    write_private_json(
        service.settings.mcp_configuration_path,
        McpHostConfiguration(servers=(google,)).model_dump(mode="json"),
    )
    monkeypatch.setattr("aegis_core.settings_service.prepare_browser_profile", fake_candidate)
    discovery = AsyncMock(
        return_value={
            "candidate_schema_sha256": {
                "browser_snapshot": "a" * 64,
                "browser_tabs": "b" * 64,
            },
            "tools_called": 0,
        }
    )
    monkeypatch.setattr("aegis_core.settings_service.discover_configuration", discovery)
    admit = []
    monkeypatch.setattr(service, "_admit", lambda server: admit.append(server))
    result = await service.handle(AUTH.create_request("settings.browser.prepare", payload()))
    assert result.ok and result.payload["state"] == "starting"
    await service.task
    assert service.operation["state"] == "done"
    assert service.operation["tools_called"] == 0 and service.operation["connected"] is False
    assert service.operation["requires_tab_selection"] is True
    servers = service._configuration().servers
    assert servers[0] == google and len(servers) == 2
    assert not servers[1].enabled and set(servers[1].approved_tools) == {
        "browser_snapshot",
        "browser_tabs",
    }
    assert servers[1].browser_session.remote_content_allowed
    assert admit == [servers[1]]
    service.host.suspend_for_settings.assert_not_called()
    status = await service.status()
    assert status["servers"][1]["browser_origins"] == ["https://fixture.example"]


@pytest.mark.asyncio
async def test_incompatible_discovery_cannot_modify_live_profile(service, monkeypatch):
    monkeypatch.setattr("aegis_core.settings_service.prepare_browser_profile", fake_candidate)
    monkeypatch.setattr(
        "aegis_core.settings_service.discover_configuration",
        AsyncMock(return_value={"candidate_schema_sha256": {}}),
    )
    result = await service.handle(AUTH.create_request("settings.browser.prepare", payload()))
    assert result.ok
    await service.task
    assert service.operation["reason"] == "browser_schema_incompatible"
    assert not service.settings.mcp_configuration_path.exists()


@pytest.mark.asyncio
async def test_cancelled_discovery_cannot_enable_or_write_profile(service, monkeypatch):
    monkeypatch.setattr("aegis_core.settings_service.prepare_browser_profile", fake_candidate)
    reached = asyncio.Event()

    async def discovery(_):
        reached.set()
        await asyncio.Event().wait()

    monkeypatch.setattr("aegis_core.settings_service.discover_configuration", discovery)
    await service.handle(AUTH.create_request("settings.browser.prepare", payload()))
    await asyncio.wait_for(reached.wait(), 1)
    await service.close()
    assert service.operation["state"] == "cancelled"
    assert not service.settings.mcp_configuration_path.exists()


@pytest.mark.asyncio
async def test_no_browser_activation_without_remote_content_consent(service):
    candidate = fake_candidate(
        service.settings.mcp_configuration_path.parent / "profile",
        origins=frozenset({"https://fixture.example"}),
        session=BrowserSessionConfiguration(mode="isolated", remote_content_allowed=False),
    )
    server = McpServerConfiguration.model_validate(candidate["configuration"])
    write_private_json(
        service.settings.mcp_configuration_path,
        McpHostConfiguration(servers=(server,)).model_dump(mode="json"),
    )
    result = await service.handle(
        AUTH.create_request(
            "settings.mcp.set_enabled",
            {
                "server_id": "playwright",
                "enabled": True,
                "confirm": True,
            },
        )
    )
    assert not result.ok and not service._server("playwright").enabled
