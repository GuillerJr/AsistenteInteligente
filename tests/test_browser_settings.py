from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from aegis_core.browser_preferences import (
    BrowserAutomationPreferences,
    browser_automation_path,
    load_browser_automation_preferences,
    save_browser_automation_preferences,
)
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
        Settings(
            mcp_configuration_path=tmp_path / "mcp.json",
            model_preferences_path=tmp_path / "models.json",
            ipc_socket_path=tmp_path / "aegis.sock",
        ),
        Host(),
        Keychain(),
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


def test_absent_automation_preferences_are_enabled_defaults_without_creating_file(tmp_path):
    path = tmp_path / "new-state" / "browser_automation.json"
    preferences = load_browser_automation_preferences(path)
    assert preferences == BrowserAutomationPreferences(enabled=True, mode="extension")
    assert not path.parent.exists()


@pytest.mark.asyncio
async def test_settings_status_exposes_preference_without_connection_or_access(service):
    result = await service.handle(AUTH.create_request("settings.status"))
    assert result.ok
    assert result.payload["browser_automation"] == {
        "enabled": True,
        "mode": "extension",
        "connection": "ready",
    }
    assert not browser_automation_path(service.settings).exists()
    assert not service.settings.mcp_configuration_path.exists()
    assert service.task is None
    service.host.suspend_for_settings.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["extension", "chrome", "isolated"])
@pytest.mark.parametrize("enabled", [True, False])
async def test_browser_defaults_save_privately_and_apply_without_restart(
    service, monkeypatch, mode, enabled
):
    discovery = AsyncMock()
    prepare = AsyncMock()
    monkeypatch.setattr("aegis_core.settings_service.discover_configuration", discovery)
    monkeypatch.setattr("aegis_core.settings_service.prepare_browser_profile", prepare)
    result = await service.handle(
        AUTH.create_request(
            "settings.browser.save", {"enabled": enabled, "mode": mode, "confirm": True}
        )
    )
    assert result.ok and result.payload["restart_required"] is False
    assert service.restart_required is False and service.task is None
    path = browser_automation_path(service.settings)
    assert path == service.settings.ipc_socket_path.parent / "browser_automation.json"
    assert os.stat(path).st_mode & 0o777 == 0o600
    assert json.loads(path.read_text()) == {
        "schema_version": 1,
        "enabled": enabled,
        "mode": mode,
    }
    # A separate reader sees the new value; no in-memory preference/grant cache is needed.
    assert load_browser_automation_preferences(path) == BrowserAutomationPreferences(
        enabled=enabled, mode=mode
    )
    restarted = SettingsIpcService(service.settings, Host(), Keychain())
    assert (await restarted.status())["browser_automation"] == {
        "enabled": enabled,
        "mode": mode,
        "connection": "ready" if enabled else "disabled",
    }
    assert not service.settings.mcp_configuration_path.exists()
    discovery.assert_not_called()
    prepare.assert_not_called()
    service.host.suspend_for_settings.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "candidate",
    [
        {"enabled": True, "mode": "extension"},
        {"enabled": True, "mode": "extension", "confirm": False},
        {"enabled": True, "mode": "extension", "confirm": "yes"},
        {"enabled": "true", "mode": "extension", "confirm": True},
        {"enabled": 1, "mode": "extension", "confirm": True},
        {"mode": "extension", "confirm": True},
        {"enabled": True, "confirm": True},
        {"enabled": True, "mode": "cdp", "confirm": True},
        {"enabled": True, "mode": "other", "confirm": True},
        {"enabled": True, "mode": "extension", "confirm": True, "url": "https://x.test"},
        {"enabled": True, "mode": "extension", "confirm": True, "selected_tab_index": 0},
        {"enabled": True, "mode": "extension", "confirm": True, "origins": ["https://x.test"]},
        {"enabled": True, "mode": "extension", "confirm": True, "remote_content_allowed": True},
        {"enabled": True, "mode": "extension", "confirm": True, "approved_tools": {}},
        {"enabled": True, "mode": "extension", "confirm": True, "schema_version": 2},
    ],
)
async def test_preferences_reject_implicit_permissions_and_invalid_choices(service, candidate):
    path = browser_automation_path(service.settings)
    save_browser_automation_preferences(path, BrowserAutomationPreferences(enabled=False))
    original = path.read_bytes()
    result = await service.handle(AUTH.create_request("settings.browser.save", candidate))
    assert not result.ok and result.error_code == "settings_invalid_change"
    assert path.read_bytes() == original
    assert service.task is None and not service.restart_required
    assert not service.settings.mcp_configuration_path.exists()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "document",
    [
        {"schema_version": 2},
        {"enabled": "true"},
        {"enabled": 1},
        {"mode": "cdp"},
        {"remote_content_allowed": True},
        {"approved_tools": {"browser_click": "a" * 64}},
    ],
)
async def test_invalid_persistent_preferences_fail_closed_without_hiding_status(service, document):
    path = browser_automation_path(service.settings)
    write_private_json(path, document)
    with pytest.raises(ValueError):
        load_browser_automation_preferences(path)
    result = await service.handle(AUTH.create_request("settings.status"))
    assert result.ok and result.payload["browser_automation"] == {
        "enabled": False,
        "mode": "extension",
        "connection": "invalid",
    }
    assert service.task is None
    assert not service.settings.mcp_configuration_path.exists()


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["public", "symlink", "broken_symlink", "malformed"])
async def test_unsafe_preference_file_cannot_be_used_or_overwritten(service, kind):
    path = browser_automation_path(service.settings)
    if kind == "broken_symlink":
        path.symlink_to(path.parent / "missing.json")
    elif kind == "symlink":
        target = path.parent / "other.json"
        save_browser_automation_preferences(target, BrowserAutomationPreferences())
        path.symlink_to(target)
    elif kind == "malformed":
        path.write_text('{"enabled":')
        path.chmod(0o600)
    else:
        save_browser_automation_preferences(path, BrowserAutomationPreferences())
        path.chmod(0o644)
    with pytest.raises((ValueError, OSError)):
        load_browser_automation_preferences(path)
    result = await service.handle(AUTH.create_request("settings.status"))
    assert result.ok and result.payload["browser_automation"]["connection"] == "invalid"
    result = await service.handle(
        AUTH.create_request(
            "settings.browser.save", {"enabled": True, "mode": "isolated", "confirm": True}
        )
    )
    assert not result.ok and service.task is None
    assert not service.restart_required


def test_symlinked_state_directory_is_rejected_even_without_a_file(tmp_path):
    original = tmp_path / "real"
    original.mkdir()
    linked = tmp_path / "linked"
    linked.symlink_to(original, target_is_directory=True)
    path = linked / "browser_automation.json"
    with pytest.raises(ValueError):
        load_browser_automation_preferences(path)
    with pytest.raises(ValueError):
        save_browser_automation_preferences(path, BrowserAutomationPreferences())
    assert not (original / path.name).exists()


def test_saving_revalidates_unchecked_model_copy_without_creating_file(tmp_path):
    path = tmp_path / "browser_automation.json"
    invalid = BrowserAutomationPreferences().model_copy(update={"mode": "cdp"})
    with pytest.raises(ValueError):
        save_browser_automation_preferences(path, invalid)
    assert not path.exists()


@pytest.mark.asyncio
async def test_save_does_not_reset_pending_restart_or_mutate_advanced_profile(service):
    server = McpServerConfiguration(
        server_id="playwright",
        command="/usr/bin/false",
        capability="web_read",
        read_only=False,
        integration=IntegrationScope(
            provider=IntegrationId.PLAYWRIGHT, browser_origins={"https://fixture.example"}
        ),
        browser_session=BrowserSessionConfiguration(mode="isolated", remote_content_allowed=False),
    )
    write_private_json(
        service.settings.mcp_configuration_path,
        McpHostConfiguration(servers=(server,)).model_dump(mode="json"),
    )
    original = service.settings.mcp_configuration_path.read_bytes()
    service.restart_required = True
    result = await service.handle(
        AUTH.create_request(
            "settings.browser.save", {"enabled": True, "mode": "extension", "confirm": True}
        )
    )
    assert result.ok and result.payload["restart_required"] is False
    assert service.restart_required
    assert service.settings.mcp_configuration_path.read_bytes() == original
    assert not service._server("playwright").enabled
    assert not service._server("playwright").browser_session.remote_content_allowed
    service.host.suspend_for_settings.assert_not_called()
