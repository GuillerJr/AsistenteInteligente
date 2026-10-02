import asyncio
import copy
import json
import logging
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import httpx
import pytest
from pydantic import ValidationError

from aegis_core.ipc.protocol import IpcAuthenticator
from aegis_core.job_authorization import ApprovalChannel, ApprovalGrant, JobAuthorizationCoordinator
from aegis_core.job_contracts import InputRequest, JobStatus, PendingToolConfirmation
from aegis_core.runtime_state import RuntimeSuspensionController
from aegis_core.telegram.client import (
    TelegramAPIError,
    TelegramClient,
    TelegramTokenLogFilter,
    telegram_html,
    text_chunks,
)
from aegis_core.telegram.configuration import TelegramConfiguration, TelegramConfigurationStore
from aegis_core.telegram.presentation import failure_message
from aegis_core.telegram.service import Decision, TelegramService

TOKEN = "123456789:" + "a" * 35
OWNER = 12345
AUTH = IpcAuthenticator(b"s" * 32)


class FakeKeychain:
    def __init__(self):
        self.value = TOKEN

    def get(self):
        return self.value

    def set(self, value):
        self.value = value


class FakeClient:
    def __init__(self):
        self.sent = []
        self.closed = False
        self.webhook = ""
        self.call = AsyncMock(side_effect=self.api)

    async def api(self, method, payload=None):
        if method == "getMe":
            return {"id": 99999, "username": "JarvisTestBot", "is_bot": True}
        if method == "getWebhookInfo":
            return {"url": self.webhook}
        if method == "getUpdates":
            await asyncio.sleep(0.01)
            return []
        return True

    async def send(self, chat_id, text, *, buttons=None):
        self.sent.append((chat_id, text, buttons))
        return len(self.sent)

    async def close(self):
        self.closed = True


class FakeJobs:
    def __init__(self):
        self.snapshots = {}
        self.requests = []
        self.cancel = AsyncMock()
        self.approve_from_telegram = AsyncMock()
        self.wait_for_change = AsyncMock(side_effect=self.wait)

    async def submit(self, request, *, conversation_id):
        snapshot = SimpleNamespace(
            job_id=uuid4(),
            status=JobStatus.RUNNING,
            stream_version=0,
            confirmation=None,
            input_request=None,
            result=None,
            error_code=None,
        )
        self.snapshots[snapshot.job_id] = snapshot
        self.requests.append((request, conversation_id))
        return snapshot

    async def wait(self, job_id, **kwargs):
        await asyncio.sleep(0.01)
        return self.snapshots[job_id]

    async def status(self, job_id):
        return self.snapshots[job_id]


@pytest.fixture
async def service(tmp_path):
    client = FakeClient()
    instance = TelegramService(
        TelegramConfigurationStore(tmp_path.resolve() / "telegram.json"),
        FakeKeychain(),
        FakeJobs(),
        SimpleNamespace(
            create=AsyncMock(side_effect=lambda **kw: SimpleNamespace(conversation_id=uuid4()))
        ),
        blocked=lambda: False,
        client_factory=lambda token: client,
    )
    await instance._configure(TOKEN)
    yield instance
    await instance.close()


async def ipc(service, method, **payload):
    return await service.handlers()[method](AUTH.create_request(method, payload=payload))


def update(identifier, text="Hola", owner=OWNER, **changes):
    message = {
        "date": int(datetime.now(UTC).timestamp()),
        "message_id": 99,
        "from": {"id": owner, "is_bot": False, "first_name": "Guillermo"},
        "chat": {"id": owner, "type": "private"},
        "text": text,
    }
    message.update(changes)
    return {"update_id": identifier, "message": message}


async def bind(service):
    assert (await ipc(service, "telegram.pair.begin")).ok
    nonce = service._pair.nonce
    await service.process_update(update(service._config.offset, f"/start {nonce}"))
    assert (await ipc(service, "telegram.pair.approve", candidate_id=OWNER, confirm=True)).ok


@pytest.mark.parametrize("low_power_before_request", [True, False])
async def test_low_power_mode_keeps_telegram_requests_and_delivery_available(
    service, low_power_before_request,
):
    power = RuntimeSuspensionController()
    source_id = uuid4()
    service._blocked = lambda: power.suspended
    await power.apply(source_id=source_id, sequence=1, cause="low_power_mode",
                      thermal_state="nominal", low_power_mode=low_power_before_request)
    await bind(service)
    await service.process_update(update(service._config.offset, "Responde en una frase"))
    job = service._jobs.snapshots[service._active]
    await power.apply(source_id=source_id, sequence=2, cause="low_power_mode",
                      thermal_state="nominal", low_power_mode=True)
    job.status, job.result = JobStatus.COMPLETED, "Respuesta desde NVIDIA."
    await spin_until(lambda: service._active is None)
    assert service.status()["state"] == "connected"
    assert service._client.sent[-1][1] == "Respuesta desde NVIDIA."
    service._jobs.cancel.assert_not_awaited()
    await power.apply(source_id=source_id, sequence=3, cause="thermal_pause",
                      thermal_state="serious", low_power_mode=True)
    await service.process_update(update(service._config.offset, "Otra consulta"))
    assert len(service._jobs.requests) == 1
    assert service.status()["state"] == "suspended"


async def spin_until(predicate):
    for _ in range(100):
        if predicate():
            return
        await asyncio.sleep(0.01)
    raise AssertionError("background job did not settle")


async def test_pair_requires_local_exact_identity_confirmation(service):
    await ipc(service, "telegram.pair.begin")
    await service.process_update(update(1, f"/start {service._pair.nonce}"))
    assert not service._config.enabled
    assert service.status()["candidate"]["id"] == OWNER
    assert service._client.sent == []
    assert not (
        await ipc(service, "telegram.pair.approve", candidate_id=OWNER + 1, confirm=True)
    ).ok
    assert not (await ipc(service, "telegram.pair.approve", candidate_id=OWNER, confirm=False)).ok
    assert (await ipc(service, "telegram.pair.approve", candidate_id=OWNER, confirm=True)).ok
    assert service._config.enabled
    assert not (await ipc(service, "telegram.pair.approve", candidate_id=OWNER, confirm=True)).ok


@pytest.mark.parametrize(
    "changes",
    [
        {"chat": {"type": "group", "id": OWNER}},
        {"chat": {"type": "private", "id": OWNER + 1}},
        {"from": {"id": OWNER, "is_bot": True}},
        {"from": {"id": True, "is_bot": False}, "chat": {"type": "private", "id": True}},
        {"date": 0},
        {"date": "invalid"},
    ],
)
async def test_pair_ignores_nonprivate_stale_and_spoofed_identity(service, changes):
    await ipc(service, "telegram.pair.begin")
    await service.process_update(update(1, f"/start {service._pair.nonce}", **changes))
    assert service.status()["candidate"] is None
    assert not service._config.enabled


async def test_pair_code_expiry_unicode_and_attempt_limit(service):
    await ipc(service, "telegram.pair.begin")
    await service.process_update(update(1, "/start código"))
    assert service._pair.attempts == 1
    for number in range(2, 21):
        await service.process_update(update(number, "/start wrong"))
    assert service._pair is None
    await ipc(service, "telegram.pair.begin")
    nonce = service._pair.nonce
    service._pair_clock = lambda: 10**12
    await service.process_update(update(21, f"/start {nonce}"))
    assert service._candidate is None


async def test_config_and_status_never_contain_token_and_file_is_private(service):
    assert TOKEN == service._keychain.value
    assert TOKEN not in json.dumps(service.status())
    assert TOKEN not in service._store.path.read_text()
    assert service._store.path.stat().st_mode & 0o777 == 0o600
    assert not (await ipc(service, "telegram.status", token=TOKEN)).ok
    assert not (await ipc(service, "telegram.configure", token=TOKEN, confirm="true")).ok


async def test_configured_webhook_is_preserved_and_binding_not_replaced(service):
    await bind(service)
    service._client.webhook = "https://other.example/webhook"
    result = await ipc(service, "telegram.configure", token=TOKEN, confirm=True)
    assert result.error_code == "telegram_webhook_configured"
    assert service._config.enabled
    assert not any(call.args[0] == "deleteWebhook" for call in service._client.call.call_args_list)


@pytest.mark.parametrize("text", [TOKEN, "nvapi-" + "x" * 40, "á" * 2500])
async def test_secrets_and_oversize_input_never_reach_nvidia(service, text):
    await bind(service)
    await service.process_update(update(service._config.offset, text))
    assert service._jobs.requests == []
    assert service._client.sent
    assert text not in service._client.sent[-1][1]


async def test_unknown_user_group_and_stale_messages_get_no_content(service):
    await bind(service)
    await service.process_update(update(2, owner=OWNER + 1))
    await service.process_update(update(3, chat={"id": OWNER, "type": "group"}))
    await service.process_update(update(4, date=0))
    assert service._jobs.requests == []
    assert service._client.sent == []


async def test_owner_request_uses_separate_persistent_conversation_and_durable_offset(service):
    await bind(service)
    original_submit = service._jobs.submit

    async def check_durable(request, **kwargs):
        assert service._store.load().offset == 3
        return await original_submit(request, **kwargs)

    service._jobs.submit = check_durable
    await service.process_update(update(2, "Revisa mi calendario"))
    request, conversation_id = service._jobs.requests[0]
    assert request.text == "Revisa mi calendario"
    assert request.metadata == {"source_channel": "telegram"}
    assert conversation_id == service._store.load().conversation_id
    await service.process_update(update(2, "duplicate"))
    assert len(service._jobs.requests) == 1
    await service.process_update(update(3, "/cancelar"))
    service._jobs.submit = original_submit
    await service.process_update(update(4, "Otra pregunta"))
    assert service._jobs.requests[1][1] == conversation_id
    await service.process_update(update(5, "/nuevo"))
    await service.process_update(update(6, "Nueva pregunta"))
    assert service._jobs.requests[2][1] != conversation_id


async def test_completed_response_and_clarification_use_real_job_result(service):
    await bind(service)
    await service.process_update(update(2, "Busca mi correo"))
    snapshot = service._jobs.snapshots[service._active]
    snapshot.status = JobStatus.COMPLETED
    snapshot.result = "Respuesta real"
    snapshot.input_request = InputRequest(
        question="¿De quién?",
        request_text="Busca mi correo",
        expires_at=datetime.now(UTC) + timedelta(minutes=1),
    )
    await spin_until(lambda: service._active is None)
    assert service._client.sent[-1][1] == "¿De quién?"
    await service.process_update(update(3, "De María"))
    assert "Busca mi correo" in service._jobs.requests[-1][0].text
    assert "De María" in service._jobs.requests[-1][0].text
    snapshot = service._jobs.snapshots[service._active]
    snapshot.status, snapshot.result = JobStatus.COMPLETED, "**Encontré el mensaje**"
    await spin_until(lambda: service._active is None)
    assert service._client.sent[-1][1] == "**Encontré el mensaje**"


async def pending_decision(service):
    await bind(service)
    await service.process_update(update(2, "Busca mi correo"))
    snapshot = service._jobs.snapshots[service._active]
    snapshot.status = JobStatus.AWAITING_CONFIRMATION
    snapshot.confirmation = PendingToolConfirmation(
        call_digest="a" * 64,
        tool_name="gmail_search",
        summary="Consultar correos no leídos",
        expires_at=datetime.now(UTC) + timedelta(minutes=1),
    )
    await spin_until(lambda: service._decision is not None)
    decision = service._decision
    callback = {
        "id": "callback",
        "from": {"id": OWNER, "is_bot": False},
        "message": {"message_id": decision.message_id, "chat": {"type": "private", "id": OWNER}},
        "data": f"a:{decision.nonce}",
    }
    return decision, callback


@pytest.mark.parametrize("code,expected", [
    ("remote_provider_unavailable", "NVIDIA no pudo responder"),
    ("remote_rate_limited", "límite de uso"),
    ("remote_authentication_failed", "credencial"),
    ("conversation_not_found", "/nuevo"),
    ("model_context_limit", "/nuevo"),
    ("approved_tool_execution_failed", "Comprueba si hubo cambios"),
    ("owner_verification_required", "identidad de voz"),
    (None, "diagnóstico"),
    (TOKEN, "diagnóstico"),
])
async def test_failed_job_reports_safe_cause_and_releases_session(service, code, expected):
    await bind(service)
    await service.process_update(update(2, "Cuales son mis últimos 5 correos sin leer"))
    snapshot = service._jobs.snapshots[service._active]
    snapshot.status, snapshot.error_code = JobStatus.FAILED, code
    await spin_until(lambda: service._active is None)
    delivered = service._client.sent[-1][1]
    assert expected in delivered
    assert delivered == failure_message(code)
    assert TOKEN not in delivered
    assert service._decision is None
    # A retry is an explicit new owner instruction, never an automatic tool replay.
    await service.process_update(update(3, "Inténtalo otra vez"))
    assert len(service._jobs.requests) == 2


@pytest.mark.parametrize(
    "mutation", ["actor", "chat", "message", "nonce", "digest", "expired", "blocked"]
)
async def test_callbacks_bound_to_owner_message_digest_expiry_and_runtime(service, mutation):
    decision, callback = await pending_decision(service)
    callback = copy.deepcopy(callback)
    if mutation == "actor":
        callback["from"]["id"] += 1
    elif mutation == "chat":
        callback["message"]["chat"]["type"] = "group"
    elif mutation == "message":
        callback["message"]["message_id"] += 1
    elif mutation == "nonce":
        callback["data"] = "a:wrong"
    elif mutation == "digest":
        service._jobs.snapshots[decision.job_id].confirmation = service._jobs.snapshots[
            decision.job_id
        ].confirmation.model_copy(update={"call_digest": "b" * 64})
    elif mutation == "expired":
        service._decision = Decision(
            decision.nonce,
            decision.job_id,
            decision.digest,
            decision.message_id,
            datetime.now(UTC) - timedelta(seconds=1),
        )
    else:
        service._blocked = lambda: True
    await service.process_update({"update_id": 3, "callback_query": callback})
    service._jobs.approve_from_telegram.assert_not_called()


async def test_exact_callback_approves_once_with_telegram_identity(service):
    decision, callback = await pending_decision(service)
    await service.process_update({"update_id": 3, "callback_query": callback})
    await service.process_update({"update_id": 4, "callback_query": callback})
    service._jobs.approve_from_telegram.assert_awaited_once_with(
        decision.job_id, decision.digest, owner_id=OWNER
    )
    assert "telegram.approve" not in service.handlers()


async def test_reject_and_disable_cancel_own_job_and_invalidate_old_decisions(service):
    decision, callback = await pending_decision(service)
    callback["data"] = f"r:{decision.nonce}"
    await service.process_update({"update_id": 3, "callback_query": callback})
    service._jobs.cancel.assert_awaited_with(decision.job_id)
    assert (await ipc(service, "telegram.set_enabled", enabled=False, confirm=True)).ok
    assert service._active is None and service._watch is None and service._decision is None
    sent = len(service._client.sent)
    await service.process_update(update(4, "Instrucción después de desactivar"))
    assert len(service._client.sent) == sent


async def test_suspension_and_old_generation_prevent_remote_execution_and_delivery(service):
    await bind(service)
    service._blocked = lambda: True
    await service.process_update(update(2, "No ejecutar"))
    assert not service._jobs.requests
    service._blocked = lambda: False
    await service.process_update(update(3, "Trabaja"))
    old_job, old_epoch = service._active, service._epoch
    await ipc(service, "telegram.unlink", confirm=True)
    service._jobs.snapshots[old_job].status = JobStatus.COMPLETED
    service._jobs.snapshots[old_job].result = "Privado"
    await service._watch_job(old_job, old_epoch)
    assert not service._client.sent
    assert service._config.owner_id is None and service._config.conversation_id is None


async def test_unconfigured_and_disabled_worker_has_no_network(service):
    calls = service._client.call.call_count
    runner = asyncio.create_task(service.run())
    await asyncio.sleep(0.03)
    runner.cancel()
    await asyncio.gather(runner, return_exceptions=True)
    assert service._client.call.call_count == calls


async def test_pair_polling_recognizes_candidate_and_closes_cleanly(service):
    await ipc(service, "telegram.pair.begin")
    service._client.call = AsyncMock(
        side_effect=[
            [update(1, f"/start {service._pair.nonce}")],
            *([[]] * 10),
        ]
    )
    runner = asyncio.create_task(service.run())
    await spin_until(lambda: service._candidate is not None)
    runner.cancel()
    await asyncio.gather(runner, return_exceptions=True)
    await service.close()
    assert service._client is None


@pytest.mark.parametrize("owner", [True, 0, -1, 2**52 + 1, "12345"])
def test_remote_grant_rejects_invalid_identity(owner):
    with pytest.raises(ValueError):
        JobAuthorizationCoordinator.telegram_grant(owner)


def test_remote_grant_does_not_claim_local_or_verified_voice_identity():
    grant = JobAuthorizationCoordinator.telegram_grant(OWNER)
    assert grant.channel is ApprovalChannel.VERIFIED_TELEGRAM
    assert grant.approved_by == f"telegram-user:{OWNER}"
    assert grant.voice_confirmation is None
    with pytest.raises(ValueError):
        ApprovalGrant(ApprovalChannel.VERIFIED_TELEGRAM, "local-menu-bar-user", None)


@pytest.mark.parametrize(
    "data",
    [
        {"enabled": True},
        {"bot_id": True},
        {"owner_id": 0},
        {"token": TOKEN},
        {"owner_name": "Someone"},
        {"offset": True},
    ],
)
def test_invalid_configuration_fails_closed(data):
    with pytest.raises(ValidationError):
        TelegramConfiguration(**data)


def test_chunking_and_html_escape_untrusted_content():
    chunks = text_chunks("😀" * 6000)
    assert "".join(chunks) == "😀" * 6000
    assert all(len(item.encode("utf-16-le")) // 2 <= 3000 for item in chunks)
    assert (
        telegram_html("**Hola** <script>& `token`")
        == "<b>Hola</b> &lt;script&gt;&amp; <code>token</code>"
    )


def test_token_is_redacted_from_http_log_arguments():
    record = logging.LogRecord(
        "httpx",
        logging.INFO,
        __file__,
        1,
        "POST %s",
        (f"https://api.telegram.org/bot{TOKEN}/getUpdates",),
        None,
    )
    TelegramTokenLogFilter().filter(record)
    assert TOKEN not in record.getMessage()


@pytest.mark.parametrize(
    "status,body,expected",
    [
        (401, {}, "telegram_credential_invalid"),
        (409, {}, "telegram_poll_conflict"),
        (429, {"parameters": {"retry_after": 300}}, "telegram_rate_limited"),
        (429, [], "telegram_response_invalid"),
        (200, [], "telegram_response_invalid"),
        (200, {"ok": False, "description": TOKEN}, "telegram_request_failed"),
    ],
)
async def test_api_errors_are_safe_and_bounded(status, body, expected):
    client = TelegramClient(
        TOKEN, transport=httpx.MockTransport(lambda request: httpx.Response(status, json=body))
    )
    try:
        with pytest.raises(TelegramAPIError) as captured:
            await client.call("getMe")
        assert captured.value.code == expected
        assert TOKEN not in str(captured.value)
        assert captured.value.retry_after <= 60
    finally:
        await client.close()


async def test_real_client_formats_chunks_and_buttons_only_on_last_message():
    calls = []

    def response(request):
        calls.append(json.loads(request.content))
        return httpx.Response(200, json={"ok": True, "result": {"message_id": len(calls)}})

    client = TelegramClient(TOKEN, transport=httpx.MockTransport(response))
    try:
        buttons = [[{"text": "Aprobar", "callback_data": "a:opaque"}]]
        assert await client.send(OWNER, "**Bien** <img> " + "😀" * 3000, buttons=buttons) == 3
        assert all(
            call["parse_mode"] == "HTML" and call["link_preview_options"]["is_disabled"]
            for call in calls
        )
        assert "inline_keyboard" not in calls[0].get("reply_markup", {})
        assert calls[-1]["reply_markup"]["inline_keyboard"] == buttons
        assert "<b>Bien</b> &lt;img&gt;" in calls[0]["text"]
    finally:
        await client.close()


async def test_pair_expiry_does_not_extend_after_wall_clock_rollback(service):
    await ipc(service, "telegram.pair.begin")
    nonce, expiry = service._pair.nonce, service._pair.expires
    service._clock = lambda: 1
    service._pair_clock = lambda: expiry + 1
    assert service.status()["pair_url"] is None
    assert not (await ipc(service, "telegram.pair.approve", candidate_id=OWNER, confirm=True)).ok
    assert nonce not in json.dumps(service.status())


async def test_oversize_and_malformed_api_response_does_not_expose_credentials():
    client = TelegramClient(
        TOKEN,
        transport=httpx.MockTransport(
            lambda request: httpx.Response(200, content=b"x" * 1_048_577),
        ),
    )
    try:
        with pytest.raises(TelegramAPIError, match="telegram_response_invalid"):
            await client.call("getMe")
    finally:
        await client.close()


async def test_worker_rejects_keychain_for_another_bot(service):
    await bind(service)
    client = service._client
    service._client = None
    client.call = AsyncMock(return_value={"id": 88888, "is_bot": True})
    runner = asyncio.create_task(service.run())
    await spin_until(lambda: service._error == "telegram_credential_invalid")
    runner.cancel()
    await asyncio.gather(runner, return_exceptions=True)
    assert service._client is None
    assert not service._jobs.requests
    assert not any(call.args[0] == "getUpdates" for call in client.call.call_args_list)


async def test_pairing_displays_suspension_before_waiting_for_a_message(service):
    service._blocked = lambda: True
    assert (await ipc(service, "telegram.pair.begin")).ok
    assert service.status()["state"] == "suspended"
    assert service.status()["candidate"] is None
    service._blocked = lambda: False
    assert service.status()["state"] == "awaiting_pair"
