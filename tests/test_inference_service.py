import asyncio
import json
from types import SimpleNamespace

import pytest

from aegis_core.contracts import AgentResult, AgentRole
from aegis_core.inference_service import InferenceIpcService
from aegis_core.inference_settings import (
    InferencePreferences,
    load_inference_preferences,
    save_inference_preferences,
)
from aegis_core.provider_status import ProviderStatusIpcService

MODEL = {"id": "test-model", "display_name": "Modelo de prueba", "images": True,
         "is_default": True}


class Provider:
    def __init__(self):
        self.state = "ready"
        self.calls = []
        self.release = asyncio.Event()
        self.wait = False
        self.reply = "4"
        self.login_url = "https://auth.openai.com/authorize?state=synthetic"

    async def status(self):
        return {"state": self.state, "installed": True, "model_id": MODEL["id"],
                "models": [MODEL], "email": "SYNTHETIC_PRIVATE", "access_token": "PRIVATE_TOKEN"}

    async def list_models(self):
        return [MODEL, {**MODEL, "id": "text-only", "images": False}]

    async def login_start(self):
        self.calls.append("login")
        self.state = "login_pending"
        return {"authUrl": self.login_url, "loginId": "PRIVATE_LOGIN_ID"}

    async def login_cancel(self):
        self.calls.append("cancel")
        if self.state == "login_pending":
            self.state = "signed_out"

    async def logout(self):
        self.calls.append("logout")
        self.state = "signed_out"

    async def complete(self, **kwargs):
        self.calls.append(kwargs)
        if self.wait:
            await self.release.wait()
        return AgentResult(role=AgentRole.SYNTHESIZER, model_id=MODEL["id"],
                           content=self.reply, finish_reason="stop")


def request(method, payload=None):
    return SimpleNamespace(method=f"settings.inference.{method}", payload=payload or {})


def test_connection_preferences_are_private_and_never_replace_legacy_models(tmp_path):
    path = tmp_path / "inference.json"
    assert load_inference_preferences(path) == InferencePreferences()
    save_inference_preferences(path, InferencePreferences(model_id="test-model"))
    assert load_inference_preferences(path).model_id == "test-model"
    assert path.stat().st_mode & 0o077 == 0
    assert json.loads(path.read_text()) == {
        "version": 1, "provider": "codex", "model_id": "test-model",
    }


@pytest.mark.parametrize("payload", [
    {"provider": "nvidia"}, {"secret": "PRIVATE"}, {"model_id": "bad\nmodel"},
])
def test_preferences_reject_credentials_and_unselected_connections(payload):
    with pytest.raises(ValueError):
        InferencePreferences.model_validate(payload)


async def test_status_and_provider_readiness_never_expose_account_details(tmp_path):
    provider = Provider()
    service = InferenceIpcService(provider, tmp_path / "inference.json")
    result = await service.handle(request("status"))
    assert result.ok and result.payload["state"] == "ready"
    assert result.payload["models"] == [MODEL]
    assert "PRIVATE" not in repr(result)
    status = ProviderStatusIpcService(status_probe=service.provider_status_payload)
    assert await status.status_payload() == {
        "provider": "codex", "credential": "configured", "voice_output": "unconfigured",
    }
    provider.state = "signed_out"
    assert await status.status_payload() == {
        "provider": "codex", "credential": "missing", "voice_output": "unconfigured",
    }


@pytest.mark.parametrize("payload", [{}, {"confirm": 1}, {"confirm": False},
                                     {"confirm": True, "extra": True}])
@pytest.mark.parametrize("method", ["connect", "disconnect"])
async def test_connection_mutations_require_exact_owner_confirmation(tmp_path, method, payload):
    provider = Provider()
    result = await InferenceIpcService(provider, tmp_path / "inference.json").handle(
        request(method, payload),
    )
    assert not result.ok and result.error_code == "invalid_payload"
    assert provider.calls == []


async def test_login_url_is_returned_once_and_cancellation_stops_pending_login(tmp_path):
    provider = Provider()
    service = InferenceIpcService(provider, tmp_path / "inference.json")
    started = await service.handle(request("connect", {"confirm": True}))
    assert started.ok and started.payload == {
        "provider": "codex", "state": "login_pending", "installed": True,
        "model_id": MODEL["id"], "models": [MODEL],
        "last_test": {"state": "idle", "error_code": None, "latency_ms": None},
        "authUrl": provider.login_url,
    }
    assert "PRIVATE" not in repr(started)
    status = await service.handle(request("status"))
    assert "authUrl" not in status.payload and "PRIVATE" not in repr(status)
    cancelled = await service.handle(request("cancel"))
    assert cancelled.ok and cancelled.payload["state"] == "signed_out"
    assert provider.calls == ["login", "cancel"]


@pytest.mark.parametrize("url", [
    "https://auth.openai.com.attacker.test/login", "http://auth.openai.com/login",
    "https://user:password@auth.openai.com/login", "https://chatgpt.com:444/login",
    "file:///private/tmp/login", "https://chatgpt.com/login#fragment",
])
async def test_login_receipts_cannot_open_untrusted_destinations(tmp_path, url):
    provider = Provider()
    provider.login_url = url
    result = await InferenceIpcService(provider, tmp_path / "inference.json").handle(
        request("connect", {"confirm": True}),
    )
    assert not result.ok and result.error_code == "invalid_payload"
    assert url not in repr(result)


@pytest.mark.parametrize("identifier", ["unknown", "text-only"])
async def test_model_selection_requires_catalogue_and_visual_capability(tmp_path, identifier):
    path = tmp_path / "inference.json"
    result = await InferenceIpcService(Provider(), path).handle(
        request("save", {"model_id": identifier}),
    )
    assert not result.ok and result.error_code == "inference_model_incompatible"
    assert not path.exists()


async def test_save_persists_an_available_model_without_restart_or_credentials(tmp_path):
    path = tmp_path / "inference.json"
    service = InferenceIpcService(Provider(), path)
    result = await service.handle(request("save", {"model_id": MODEL["id"]}))
    assert result.ok and result.payload["model_id"] == MODEL["id"]
    assert load_inference_preferences(path).model_id == MODEL["id"]


async def test_test_is_background_work_and_cancel_prevents_late_success(tmp_path):
    provider = Provider()
    provider.wait = True
    service = InferenceIpcService(provider, tmp_path / "inference.json")
    started = await service.handle(request("test"))
    assert started.ok and started.payload["last_test"]["state"] == "running"
    busy = await service.handle(request("disconnect", {"confirm": True}))
    assert not busy.ok and busy.error_code == "inference_operation_busy"
    await asyncio.sleep(0)
    cancelled = await service.handle(request("cancel"))
    provider.release.set()
    assert cancelled.ok and cancelled.payload["last_test"]["state"] == "cancelled"
    assert "logout" not in provider.calls


@pytest.mark.parametrize("reply,state", [("4", "passed"), ("PRIVATE_WRONG_REPLY", "failed")])
async def test_probe_verifies_the_actual_response_without_echoing_it(tmp_path, reply, state):
    provider = Provider()
    provider.reply = reply
    service = InferenceIpcService(provider, tmp_path / "inference.json")
    await service.handle(request("test"))
    await service.task
    status = await service.status_payload()
    assert status["last_test"]["state"] == state
    assert status["last_test"]["latency_ms"] >= 0
    assert "PRIVATE" not in repr(status)


async def test_no_probe_inference_before_login(tmp_path):
    provider = Provider()
    provider.state = "signed_out"
    service = InferenceIpcService(provider, tmp_path / "inference.json")
    await service.handle(request("test"))
    await service.task
    assert (await service.status_payload())["last_test"]["error_code"] == "inference_not_connected"
    assert provider.calls == []


@pytest.mark.parametrize("method,payload", [
    ("connect", {"confirm": True}), ("disconnect", {"confirm": True}),
    ("save", {"model_id": MODEL["id"]}), ("test", {}),
])
async def test_model_and_session_cannot_change_during_an_active_job(tmp_path, method, payload):
    provider = Provider()
    service = InferenceIpcService(provider, tmp_path / "inference.json", jobs_busy=lambda: True)
    result = await service.handle(request(method, payload))
    assert not result.ok and result.error_code == "inference_operation_busy"
    assert provider.calls == []
    assert not service.preferences_path.exists()
    assert (await service.handle(request("status"))).ok


async def test_cancel_waits_for_inflight_login_receipt_and_cancels_it(tmp_path):
    provider = Provider()
    entered, release = asyncio.Event(), asyncio.Event()
    login_start = provider.login_start

    async def delayed_login():
        entered.set()
        await release.wait()
        return await login_start()

    provider.login_start = delayed_login
    service = InferenceIpcService(provider, tmp_path / "inference.json")
    connecting = asyncio.create_task(service.handle(request("connect", {"confirm": True})))
    await entered.wait()
    cancelling = asyncio.create_task(service.handle(request("cancel")))
    await asyncio.sleep(0)
    assert not cancelling.done()
    release.set()

    connected, cancelled = await asyncio.gather(connecting, cancelling)
    assert connected.ok
    assert cancelled.ok and cancelled.payload["state"] == "signed_out"
    assert provider.calls == ["login", "cancel"]
    assert "authUrl" not in cancelled.payload
