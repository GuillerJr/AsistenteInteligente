"""Regression boundaries use encrypted temporary state and synthetic clients only."""

import asyncio
import base64
import os
import secrets
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock

import pytest
from test_telegram import OWNER, FakeClient, ipc, spin_until, update
from test_telegram_experience import experience as encrypted_experience
from test_telegram_inbox import restart

from aegis_core.job_contracts import JobStatus
from aegis_core.memory.crypto import RowAuthenticationError
from aegis_core.memory.errors import MemoryStoreError
from aegis_core.settings_store import write_private_json
from aegis_core.telegram.client import TelegramAPIError
from aegis_core.telegram.journal import Delivery, MessageReference
from aegis_core.telegram.media import MediaError
from aegis_core.telegram.service import Decision

experience = encrypted_experience


async def conversation(service):
    record = await service._conversations.create()
    service._save(conversation_id=record.conversation_id)
    service._ensure_transport_binding()
    return record.conversation_id


async def test_new_result_keeps_pending_outbox_references_at_full_reference_capacity(experience):
    identifier = await conversation(experience)
    references = tuple(
        MessageReference(
            token=secrets.token_urlsafe(18),
            conversation_id=identifier,
            content=f"Earlier answer {index}",
            created=experience._clock(),
        )
        for index in range(16)
    )
    experience._transport_update(references=references)
    experience._client.send = AsyncMock(side_effect=TelegramAPIError("telegram_unavailable"))
    await experience._enqueue_result("Durable answer A")
    first = experience._transport.deliveries[0].token
    experience._remember(
        MessageReference(
            token=secrets.token_urlsafe(18),
            conversation_id=identifier,
            content="New request B",
            created=experience._clock(),
        )
    )
    await experience._enqueue_result("Durable answer B")
    state = experience._journal.load()
    assert len(state.references) == 16 and len(state.deliveries) == 2
    assert first in {r.token for r in state.references}
    assert {d.token for d in state.deliveries} <= {r.token for r in state.references}
    assert b"Durable answer" not in experience._journal.path.read_bytes()
    revived = restart(experience)
    try:
        await revived._recover_transport()
        assert len(revived._client.sent) == 2
        assert "Durable answer A" in revived._client.sent[0][1]
        assert "Durable answer B" in revived._client.sent[1][1]
        assert not revived._transport.deliveries and not revived._jobs.requests
        revived._jobs.approve_from_telegram.assert_not_awaited()
    finally:
        await revived.close()


async def test_older_orphaned_delivery_recovers_text_without_resource_authority(experience):
    identifier = await conversation(experience)
    delivery = Delivery(
        token="legacy-orphan",
        conversation_id=identifier,
        text="Authenticated answer from an older version",
        created=experience._clock(),
    )
    experience._transport_update(deliveries=(delivery,), references=())
    await experience._flush_deliveries()
    assert not experience._transport.deliveries
    reference = experience._transport.references[-1]
    assert reference.content == delivery.text and not reference.resources
    assert reference.resource_mode is None and not reference.question
    assert experience._client.sent[-1][1] == delivery.text
    assert not experience._jobs.requests
    experience._jobs.approve_from_telegram.assert_not_awaited()


@pytest.mark.parametrize("encrypted", [False, True])
async def test_deep_json_journal_is_blocked_without_overwrite_or_authentication_bypass(
    experience,
    encrypted,
):
    path = experience._journal.path
    nested = b"[" * 1000 + b"0" + b"]" * 1000
    if encrypted:
        cipher = experience._journal.cipher
        nonce = os.urandom(12)
        ciphertext = cipher._aead.encrypt(
            nonce,
            b'{"depth":' + nested + b"}",
            cipher._associated_data(namespace="telegram-transport-v1", memory_id="state"),
        )
        write_private_json(
            path,
            {
                "nonce": base64.b64encode(nonce).decode(),
                "ciphertext": base64.b64encode(ciphertext).decode(),
            },
        )
    else:
        path.write_bytes(nested)
        path.chmod(0o600)
    before = path.read_bytes()
    revived = restart(experience)
    try:
        assert revived.status()["state"] == "blocked"
        assert revived.status()["error"] == "telegram_journal_invalid"
        assert path.read_bytes() == before
        assert not revived._jobs.requests
        if encrypted:
            document = {
                "nonce": base64.b64encode(nonce).decode(),
                "ciphertext": base64.b64encode(
                    ciphertext[:-1] + bytes([ciphertext[-1] ^ 1])
                ).decode(),
            }
            write_private_json(path, document)
            with pytest.raises(RowAuthenticationError):
                experience._journal.load()
    finally:
        await revived.close()


async def test_oversized_correction_cancels_old_proposal_without_truncating_a_new_request(
    experience,
):
    experience._batch_seconds = 60
    text = ("Revisa mis correos y prepara una lista. " * 220)[:8100]
    await experience.process_update(update(experience._config.offset, text[:4050], message_id=11))
    await experience.process_update(update(experience._config.offset, text[4050:], message_id=12))
    await experience._submit_incoming()
    old = experience._active
    experience._jobs.snapshots[old].status = JobStatus.AWAITING_CONFIRMATION
    experience._decision = Decision(
        "old-confirmation",
        old,
        "a" * 64,
        33,
        datetime.now(UTC) + timedelta(minutes=5),
    )
    experience._batch_seconds = 0
    correction = "solo revisa de hoy. " * 12 + "EXCLUYE a MARTA y jamás modifiques ese correo."
    await experience.process_update(update(experience._config.offset, correction))
    experience._jobs.cancel.assert_awaited_once_with(old)
    assert len(experience._jobs.requests) == 1 and not experience._busy
    assert experience._decision is None and experience._transport.interrupted is None
    assert not experience._transport.inbox
    assert "solicitud nueva" in experience._client.sent[-1][1]
    assert "No recorté" in experience._client.sent[-1][1]
    experience._jobs.approve_from_telegram.assert_not_awaited()


@pytest.mark.parametrize("kind", ["voice", "document", "photo"])
async def test_failed_media_keeps_controls_and_durable_notice_for_queued_text(
    experience,
    monkeypatch,
    kind,
):
    from aegis_core.telegram import experience as module

    experience._batch_seconds = 0.01
    entered, release = asyncio.Event(), asyncio.Event()

    async def download(*args, **kwargs):
        entered.set()
        await release.wait()
        return b"synthetic invalid media"

    experience._client.download = download
    experience._transcribe.side_effect = ValueError("synthetic preparation failure")
    monkeypatch.setattr(module, "decode", AsyncMock(side_effect=MediaError("invalid")))
    media = {
        "voice": {"file_id": "voice-audit", "duration": 2},
        "document": {"file_id": "document-audit", "file_name": "audit.txt"},
        "photo": [{"file_id": "photo-audit"}],
    }[kind]
    caption = "Procesa este adjunto sin modificar ningún archivo."
    await experience.process_update(
        update(experience._config.offset, None, caption=caption, **{kind: media})
    )
    await asyncio.wait_for(entered.wait(), timeout=1)
    old_control, old_progress = experience._control, experience._progress_id
    pending = "Extrae solo las fechas del archivo que adjunté."
    await experience.process_update(update(experience._config.offset, pending))
    assert experience._control == old_control and experience._progress_id == old_progress
    experience._client.send = AsyncMock(side_effect=TelegramAPIError("telegram_unavailable"))
    release.set()
    await spin_until(lambda: not experience._busy)
    state = experience._journal.load()
    assert not experience._jobs.requests and not state.inbox and state.interrupted is None
    assert len(state.deliveries) == 1
    assert caption in state.deliveries[0].text
    assert pending in state.deliveries[0].text and "No ejecuté" in state.deliveries[0].text
    assert b"Extrae solo" not in experience._journal.path.read_bytes()
    revived = restart(experience)
    try:
        await revived._recover_transport()
        assert not revived._jobs.requests and not revived._transport.deliveries
        assert pending in revived._client.sent[-1][1]
        assert "No ejecuté" in revived._client.sent[-1][1]
        revived._jobs.approve_from_telegram.assert_not_awaited()
    finally:
        await revived.close()


async def test_media_rejection_save_failure_preserves_entire_batch_for_restart(
    experience, monkeypatch
):
    experience._batch_seconds = 60
    entered, release = asyncio.Event(), asyncio.Event()

    async def download(*args, **kwargs):
        entered.set()
        await release.wait()
        return b"invalid audio"

    experience._client.download = download
    experience._transcribe.side_effect = ValueError("invalid")
    await experience.process_update(
        update(experience._config.offset, None, voice={"file_id": "voice-audit", "duration": 2})
    )
    await asyncio.wait_for(entered.wait(), timeout=1)
    preparing = experience._media_task
    pending = "Resume ese audio y conserva su última restricción."
    await experience.process_update(update(experience._config.offset, pending))
    original = experience._journal.save

    def fail_notice(state):
        if state.deliveries:
            raise OSError("notice was not committed")
        original(state)

    monkeypatch.setattr(experience._journal, "save", fail_notice)
    release.set()
    (error,) = await asyncio.gather(preparing, return_exceptions=True)
    assert isinstance(error, ValueError)
    assert len(experience._journal.load().inbox) == 2
    assert not experience._journal.load().deliveries and not experience._jobs.requests
    monkeypatch.setattr(experience._journal, "save", original)
    revived = restart(experience)
    revived._client.download = AsyncMock(return_value=b"invalid audio")
    revived._transcribe = AsyncMock(side_effect=ValueError("invalid"))
    try:
        await revived._recover_transport()
        await spin_until(lambda: not revived._busy)
        await revived._recover_transport()
        assert not revived._jobs.requests and not revived._transport.inbox
        assert any(pending in text and "No ejecuté" in text for _, text, _ in revived._client.sent)
        revived._jobs.approve_from_telegram.assert_not_awaited()
    finally:
        await revived.close()


async def test_prepared_voice_restart_admits_all_restrictions_before_submission(experience):
    experience._batch_seconds = 60
    transcript = "Prepara una lista de mis correos de hoy."
    experience._transcribe = AsyncMock(return_value=transcript)
    await experience.process_update(
        update(experience._config.offset, None, voice={"file_id": "voice-audit", "duration": 2})
    )
    await spin_until(lambda: bool(experience._incoming))
    restriction = "Solo asuntos y remitentes; excluye publicidad y no modifiques mensajes."
    await experience.process_update(update(experience._config.offset, restriction))
    await experience.close()
    revived = restart(experience)
    revived._client.download = AsyncMock(side_effect=AssertionError("already prepared"))
    revived._transcribe = AsyncMock(side_effect=AssertionError("already prepared"))
    try:
        await revived._recover_transport()
        assert len(revived._jobs.requests) == 1
        assert revived._jobs.requests[0][0].text == transcript + "\n" + restriction
        assert not revived._transport.inbox
        revived._client.download.assert_not_called()
        revived._transcribe.assert_not_called()
        revived._jobs.approve_from_telegram.assert_not_awaited()
    finally:
        await revived.close()


async def test_failed_recovery_admission_retains_media_and_restrictions_for_retry(
    experience, monkeypatch
):
    experience._batch_seconds = 60
    entered = asyncio.Event()

    async def download(*args, **kwargs):
        entered.set()
        await asyncio.Event().wait()

    experience._client.download = download
    await experience.process_update(
        update(experience._config.offset, None, voice={"file_id": "voice-audit", "duration": 2})
    )
    await asyncio.wait_for(entered.wait(), timeout=1)
    restriction = "Conserva todas las fechas y no envíes mensajes."
    await experience.process_update(update(experience._config.offset, restriction))
    await experience.close()
    revived = restart(experience)
    revived._client.download = AsyncMock(return_value=b"synthetic audio")
    revived._transcribe = AsyncMock(return_value="Resume mi nota de voz.")
    original = revived._conversations.ensure_exists
    count = 0

    async def fail_second_read(identifier):
        nonlocal count
        count += 1
        if count == 2:
            await asyncio.sleep(0)  # Media preparation waits at its admission barrier.
            raise MemoryStoreError("temporary synthetic lookup failure")
        await original(identifier)

    monkeypatch.setattr(revived._conversations, "ensure_exists", fail_second_read)
    try:
        with pytest.raises(MemoryStoreError):
            await revived._recover_transport()
        assert len(revived._journal.load().inbox) == 2
        assert not revived._busy and not revived._jobs.requests
        revived._client.download.assert_not_called()
        monkeypatch.setattr(revived._conversations, "ensure_exists", original)
        await revived._recover_transport()
        await spin_until(lambda: revived._active is not None)
        assert len(revived._jobs.requests) == 1
        assert restriction in revived._jobs.requests[0][0].text
        assert "Resume mi nota de voz." in revived._jobs.requests[0][0].text
        assert not revived._transport.inbox
        revived._jobs.approve_from_telegram.assert_not_awaited()
    finally:
        await revived.close()


@pytest.mark.parametrize("outcome", ["success", "stale_error"])
async def test_token_rotation_during_cold_getme_cannot_replace_current_client(experience, outcome):
    entered, release = asyncio.Event(), asyncio.Event()

    class OldCredentialClient(FakeClient):
        async def api(self, method, payload=None):
            if method == "getMe":
                entered.set()
                await release.wait()
                if outcome == "stale_error":
                    raise TelegramAPIError("telegram_credential_invalid", 60)
            return await super().api(method, payload)

    old, fresh = OldCredentialClient(), FakeClient()
    rotated = "123456789:" + "b" * 35
    experience._factory = lambda token: fresh if token == rotated else old
    await experience._client.close()
    experience._client = None
    poller = asyncio.create_task(experience.run())
    try:
        await asyncio.wait_for(entered.wait(), timeout=1)
        await experience._configure(rotated)
        release.set()
        await spin_until(lambda: old.closed)
        assert experience._client is fresh and experience._keychain.value == rotated
        assert not fresh.closed and experience._error is None
        assert not any(args[0] == "getUpdates" for args, _ in old.call.call_args_list)
        assert not experience._jobs.requests
        assert (await ipc(experience, "telegram.pair.begin")).ok
        # Real updates stay monotonic across rotations of the same bot token.
        identifier = max(experience._config.offset, experience._transport.acknowledged_offset)
        await experience.process_update(update(identifier, f"/start {experience._pair.nonce}"))
        assert (
            await ipc(
                experience,
                "telegram.pair.approve",
                candidate_id=OWNER,
                confirm=True,
            )
        ).ok
        assert experience.status()["state"] == "connected"
    finally:
        release.set()
        poller.cancel()
        await asyncio.gather(poller, return_exceptions=True)


async def test_cancel_during_cold_client_adoption_closes_unadopted_client(experience):
    entered, release, verified = asyncio.Event(), asyncio.Event(), asyncio.Event()

    class ColdClient(FakeClient):
        async def api(self, method, payload=None):
            if method == "getMe":
                entered.set()
                await release.wait()
                result = await super().api(method, payload)
                verified.set()
                return result
            return await super().api(method, payload)

    candidate = ColdClient()
    experience._factory = lambda token: candidate
    await experience._client.close()
    experience._client = None
    poller = asyncio.create_task(experience.run())
    try:
        await asyncio.wait_for(entered.wait(), timeout=1)
        async with experience._lock:
            release.set()
            await asyncio.wait_for(verified.wait(), timeout=1)
            poller.cancel()
            await asyncio.gather(poller, return_exceptions=True)
        assert candidate.closed and experience._client is None
        assert not any(args[0] == "getUpdates" for args, _ in candidate.call.call_args_list)
        assert not experience._jobs.requests
    finally:
        release.set()
        poller.cancel()
        await asyncio.gather(poller, return_exceptions=True)
