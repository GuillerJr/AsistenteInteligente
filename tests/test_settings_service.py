from __future__ import annotations

import asyncio
import json
import os
import threading
from unittest.mock import AsyncMock

import httpx
import pytest
import respx
from aiohttp.test_utils import make_mocked_request

from aegis_core.config import Settings
from aegis_core.contracts import AgentRole
from aegis_core.google_account_settings import GOOGLE_SCOPES, GoogleAccountAuthorization
from aegis_core.ipc.protocol import IpcAuthenticator
from aegis_core.mcp.client import McpHostConfiguration, McpServerConfiguration
from aegis_core.mcp.integrations import IntegrationId, IntegrationScope
from aegis_core.providers.nvidia import NvidiaNimClient
from aegis_core.settings_service import SettingsIpcService
from aegis_core.settings_store import (
    default_model_preferences,
    load_model_preferences,
    read_private_json,
    write_private_json,
)

AUTH = IpcAuthenticator(b"s" * 32)


class FakeHost:
    def __init__(self):
        self.suspend_for_settings = AsyncMock()

    def status(self):
        return {"servers": []}


class FakeKeychain:
    def __init__(self):
        self.value = "private-test-key"

    def is_configured(self):
        return True

    def get(self):
        return self.value

    def set(self, value):
        self.value = value


@pytest.fixture
def service(tmp_path):
    settings = Settings(
        ipc_socket_path=tmp_path / "aegis.sock",
        model_preferences_path=tmp_path / "models.json",
        mcp_configuration_path=tmp_path / "mcp.json",
    )
    return SettingsIpcService(settings, FakeHost(), FakeKeychain())


def google_server(tmp_path):
    return McpServerConfiguration(
        server_id="google-workspace",
        command="/usr/bin/false",
        capability="web_read",
        integration=IntegrationScope(
            provider=IntegrationId.GOOGLE, google_accounts={"old@example.com"}
        ),
        environment={
            "WORKSPACE_MCP_CREDENTIALS_DIR": str(tmp_path / "credentials"),
            "GOOGLE_OAUTH_CLIENT_ID": "test.apps.googleusercontent.com",
        },
        secret_environment={"GOOGLE_OAUTH_CLIENT_SECRET": "oauth-client"},
        allow_explicit_requests=True,
    )


@pytest.mark.asyncio
async def test_settings_status_never_returns_credentials(service):
    result = await service.handle(AUTH.create_request("settings.status"))
    assert result.ok
    assert result.payload["credential"] == "configured"
    assert "private-test-key" not in json.dumps(result.payload)
    assert "nvidia_tts_voice" not in result.payload


@pytest.mark.asyncio
async def test_model_selection_survives_restart_and_is_used_by_provider(service):
    preferences = default_model_preferences().model_dump(mode="json")
    preferences["roles"]["planner"]["primary"] = "nvidia/nemotron-3-ultra-550b-a55b"
    result = await service.handle(AUTH.create_request("settings.models.save", preferences))
    assert result.ok and result.payload["restart_required"]
    assert os.stat(service.settings.model_preferences_path).st_mode & 0o777 == 0o600
    new_service = SettingsIpcService(service.settings, FakeHost(), FakeKeychain())
    assert (await new_service.status())["models"]["roles"]["planner"][
        "primary"
    ] == "nvidia/nemotron-3-ultra-550b-a55b"
    async with NvidiaNimClient(service.settings, lambda: "unused") as client:
        assert client._models[AgentRole.PLANNER].model_id == "nvidia/nemotron-3-ultra-550b-a55b"


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["missing", "tools", "images", "extra", "voice"])
async def test_invalid_model_changes_leave_existing_file_untouched(service, change):
    original = default_model_preferences().model_dump(mode="json")
    write_private_json(service.settings.model_preferences_path, original)
    candidate = json.loads(json.dumps(original))
    if change == "missing":
        candidate["models"] = candidate["models"][1:]
    elif change == "tools":
        candidate["roles"]["planner"]["primary"] = "openai/gpt-oss-20b"
    elif change == "images":
        candidate["roles"]["vision"]["primary"] = "openai/gpt-oss-20b"
    elif change == "extra":
        candidate["roles"]["unknown"] = candidate["roles"]["planner"]
    else:
        candidate["nvidia_tts_voice"] = "OtherVoice"
    result = await service.handle(AUTH.create_request("settings.models.save", candidate))
    assert not result.ok
    assert read_private_json(service.settings.model_preferences_path) == original


def test_preferences_reject_symlinks_and_shared_files(tmp_path):
    real = tmp_path / "real.json"
    write_private_json(real, default_model_preferences().model_dump(mode="json"))
    link = tmp_path / "link.json"
    link.symlink_to(real)
    with pytest.raises(OSError):
        load_model_preferences(link)
    real.chmod(0o644)
    with pytest.raises(ValueError):
        load_model_preferences(real)


@pytest.mark.asyncio
async def test_google_disconnect_requires_confirmation_and_disables_access(
    service, tmp_path, monkeypatch
):
    server = google_server(tmp_path).model_copy(update={"enabled": True})
    write_private_json(
        service.settings.mcp_configuration_path,
        McpHostConfiguration(servers=(server,)).model_dump(mode="json"),
    )
    denied = await service.handle(
        AUTH.create_request("settings.google.disconnect", {"server_id": server.server_id})
    )
    assert not denied.ok
    service.host.suspend_for_settings.assert_not_called()
    revoke = AsyncMock()
    monkeypatch.setattr("aegis_core.settings_service.revoke_google_account", revoke)
    accepted = await service.handle(
        AUTH.create_request(
            "settings.google.disconnect", {"server_id": server.server_id, "confirm": True}
        )
    )
    assert accepted.ok
    await service.task
    service.host.suspend_for_settings.assert_awaited_once_with(server.server_id)
    revoke.assert_awaited_once_with(server, "old@example.com")
    assert not (await service.status())["servers"][0]["enabled"]


@pytest.mark.asyncio
async def test_busy_operation_rejects_changes_and_cancellation_joins_work(service):
    blocker = asyncio.Event()
    service._launch_operation(lambda progress: blocker.wait())
    rejected = await service.handle(
        AUTH.create_request(
            "settings.models.save", default_model_preferences().model_dump(mode="json")
        )
    )
    assert rejected.error_code == "settings_operation_busy"
    assert not service.settings.model_preferences_path.exists()
    result = await service.handle(AUTH.create_request("settings.cancel"))
    assert result.ok and service.task.done()


@pytest.mark.asyncio
async def test_cancelled_credential_write_is_joined_before_accepting_a_new_key(service):
    entered, release, finished = threading.Event(), threading.Event(), threading.Event()
    writes = []

    def slow_write(value):
        if value == "synthetic-first":
            entered.set()
            assert release.wait(5)
        service.keychain.value = value
        writes.append(value)
        finished.set()

    service.keychain.set = slow_write
    cancellation = None
    try:
        first = await service.handle(AUTH.create_request(
            "settings.credential.set", {"secret": "synthetic-first"},
        ))
        assert first.ok
        assert await asyncio.to_thread(entered.wait, 2)
        cancellation = asyncio.create_task(service.handle(AUTH.create_request("settings.cancel")))
        for _ in range(10):
            await asyncio.sleep(0)
        assert not cancellation.done()
        denied = await service.handle(AUTH.create_request(
            "settings.credential.set", {"secret": "synthetic-second"},
        ))
        assert denied.error_code == "settings_operation_busy"
        assert not finished.is_set()
        release.set()
        assert (await cancellation).ok
        assert finished.is_set() and service.task.done()
        assert service.operation["state"] == "cancelled"
        second = await service.handle(AUTH.create_request(
            "settings.credential.set", {"secret": "synthetic-second"},
        ))
        assert second.ok
        await service.task
        assert service.operation["state"] == "done"
        assert service.keychain.value == "synthetic-second"
        assert writes == ["synthetic-first", "synthetic-second"]
    finally:
        release.set()
        if cancellation is not None:
            await asyncio.gather(cancellation, return_exceptions=True)
        await service.close()


@pytest.mark.asyncio
async def test_google_callback_rejects_wrong_state_before_token_exchange(tmp_path):
    authorization = GoogleAccountAuthorization(
        google_server(tmp_path), "new@example.com", {}, lambda _: None
    )
    request = make_mocked_request("GET", "/oauth2callback?state=wrong&code=private-code")
    result = await authorization.callback(request)
    assert result.status == 400
    assert not authorization.claimed


@pytest.mark.asyncio
@respx.mock
@pytest.mark.parametrize(
    "email,expected",
    [("other@example.com", "error"), ("old@example.com", "error"), ("new@example.com", "done")],
)
async def test_google_callback_checks_identity_before_committing(tmp_path, email, expected):
    committed = []
    progress = {}
    authorization = GoogleAccountAuthorization(
        google_server(tmp_path), "new@example.com", progress, committed.append
    )
    authorization.client_id = "test.apps.googleusercontent.com"
    authorization.client_secret = "private-secret"
    authorization.path = tmp_path / "credentials" / "new@example.com.json"
    respx.post("https://oauth2.googleapis.com/token").mock(
        return_value=httpx.Response(
            200,
            json={
                "access_token": "private-access",
                "refresh_token": "private-refresh",
                "scope": " ".join(GOOGLE_SCOPES),
                "expires_in": 3600,
            },
        )
    )
    respx.get("https://www.googleapis.com/oauth2/v2/userinfo").mock(
        return_value=httpx.Response(200, json={"email": email, "verified_email": True})
    )
    revoke = respx.post("https://oauth2.googleapis.com/revoke").mock(
        return_value=httpx.Response(200)
    )
    request = make_mocked_request(
        "GET",
        f"/oauth2callback?state={authorization.state}&code=private-code",
        transport=type(
            "Transport",
            (),
            {
                "get_extra_info": lambda self, key, default=None: (
                    ("127.0.0.1", 1) if key == "peername" else default
                )
            },
        )(),
    )
    await authorization.callback(request)
    assert progress["state"] == expected
    assert "private" not in json.dumps(progress)
    if expected == "done":
        assert committed == ["new@example.com"]
        assert read_private_json(authorization.path)["refresh_token"] == "private-refresh"
        assert not revoke.called
    else:
        assert not committed and not authorization.path.exists()
        assert revoke.called == (email != "old@example.com")
    duplicate = await authorization.callback(request)
    assert duplicate.status == 400
