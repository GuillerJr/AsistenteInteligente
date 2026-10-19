"""Crash boundaries use encrypted temporary storage and never contact Telegram."""

import asyncio
import os
from unittest.mock import AsyncMock

import pytest
from test_telegram import OWNER, FakeClient, FakeJobs, FakeKeychain, ipc, spin_until, update
from test_telegram_experience import experience as encrypted_experience

from aegis_core.memory.crypto import MemoryRowCipher, RowAuthenticationError
from aegis_core.telegram.client import TelegramAPIError
from aegis_core.telegram.journal import InboxEntry, TelegramJournal
from aegis_core.telegram.service import TelegramService

experience = encrypted_experience


def restart(service, *, batch_seconds=0):
    instance = TelegramService(
        service._store,
        FakeKeychain(),
        FakeJobs(),
        service._conversations,
        blocked=lambda: False,
        journal=service._journal,
        batch_seconds=batch_seconds,
    )
    instance._client = FakeClient()
    return instance


async def test_journal_failure_before_ack_keeps_update_available(experience, monkeypatch):
    identifier = experience._config.offset
    original = experience._journal.save
    monkeypatch.setattr(experience._journal, "save", lambda state: (_ for _ in ()).throw(OSError()))
    with pytest.raises(ValueError):
        await experience.process_update(update(identifier, "Consulta recuperable"))
    assert experience._store.load().offset == identifier
    assert not experience._jobs.requests
    assert experience.status()["error"] == "telegram_journal_invalid"
    monkeypatch.setattr(experience._journal, "save", original)
    revived = restart(experience)
    try:
        await revived.process_update(update(identifier, "Consulta recuperable"))
        assert len(revived._jobs.requests) == 1
    finally:
        await revived.close()


async def test_inbox_and_highwater_survive_configuration_ack_failure(experience, monkeypatch):
    identifier = experience._config.offset
    original = experience._store.save

    def fail_ack(config):
        if config.offset > identifier:
            raise OSError("simulated crash between journal and configuration")
        original(config)

    monkeypatch.setattr(experience._store, "save", fail_ack)
    with pytest.raises(OSError):
        await experience.process_update(
            update(identifier, "Consulta persistida antes de confirmar")
        )
    saved = experience._journal.load()
    assert saved.acknowledged_offset == identifier + 1
    assert len(saved.inbox) == 1 and not saved.inbox[0].claimed
    assert experience._store.load().offset == identifier
    assert not experience._jobs.requests
    assert b"Consulta persistida" not in experience._journal.path.read_bytes()
    monkeypatch.setattr(experience._store, "save", original)
    revived = restart(experience)
    try:
        await revived._recover_transport()
        await revived.process_update(update(identifier, "Duplicate must not replace input"))
        assert len(revived._jobs.requests) == 1
        assert revived._jobs.requests[0][0].text == "Consulta persistida antes de confirmar"
        assert not revived._journal.load().inbox
    finally:
        await revived.close()


@pytest.mark.parametrize("boundary", ["claim_write", "after_claim"])
async def test_claim_boundaries_only_resume_before_submission(experience, monkeypatch, boundary):
    identifier = experience._config.offset
    original_save = experience._journal.save
    original_process = experience._process_acknowledged_update
    if boundary == "claim_write":

        def fail_claim(state):
            if any(entry.claimed for entry in state.inbox):
                raise OSError("crash while claiming input")
            original_save(state)

        monkeypatch.setattr(experience._journal, "save", fail_claim)
        expected = ValueError
    else:
        monkeypatch.setattr(
            experience, "_process_acknowledged_update", AsyncMock(side_effect=OSError("crash"))
        )
        expected = OSError
    with pytest.raises(expected):
        await experience.process_update(update(identifier, "Solo preparación, ninguna acción"))
    assert not experience._jobs.requests
    assert experience._journal.load().inbox
    monkeypatch.setattr(experience._journal, "save", original_save)
    monkeypatch.setattr(experience, "_process_acknowledged_update", original_process)
    revived = restart(experience)
    try:
        await revived._recover_transport()
        assert len(revived._jobs.requests) == 1
        assert revived._jobs.requests[0][0].text == "Solo preparación, ninguna acción"
    finally:
        await revived.close()


async def test_restart_preserves_all_burst_fragments_before_submission(experience):
    experience._batch_seconds = 60
    identifier = experience._config.offset
    await experience.process_update(update(identifier, "Consulta mis correos", message_id=11))
    await experience.process_update(update(identifier + 1, "Solo cinco de hoy", message_id=12))
    saved = experience._journal.load()
    assert len(saved.inbox) == 2
    assert saved.interrupted is not None and not saved.interrupted.submission_started
    assert not experience._jobs.requests
    await experience.close()  # Cancels RAM tasks, leaving the durable shutdown state.
    revived = restart(experience, batch_seconds=0.02)
    try:
        await revived._recover_transport()
        await spin_until(lambda: revived._active is not None)
        assert len(revived._jobs.requests) == 1
        assert revived._jobs.requests[0][0].text == "Consulta mis correos\nSolo cinco de hoy"
        assert revived._journal.load().interrupted.submission_started
    finally:
        await revived.close()


async def test_submission_boundary_preserves_instruction_without_replaying(experience):
    identifier = experience._config.offset
    experience._jobs.submit = AsyncMock(side_effect=OSError("crash at submission boundary"))
    with pytest.raises(OSError):
        await experience.process_update(update(identifier, "Crea el evento solo una vez"))
    saved = experience._journal.load()
    assert not saved.inbox
    assert saved.interrupted.submission_started
    assert saved.interrupted.request_text == "Crea el evento solo una vez"
    revived = restart(experience)
    try:
        await revived._recover_transport()
        assert not revived._jobs.requests
        revived._jobs.approve_from_telegram.assert_not_called()
        assert "No repetí la operación" in revived._client.sent[-1][1]
        assert "Crea el evento solo una vez" in revived._client.sent[-1][1]
        await revived._recover_transport()
        assert len(revived._client.sent) == 1
    finally:
        await revived.close()


async def test_crash_after_job_submission_never_submits_or_approves_twice(experience, monkeypatch):
    identifier = experience._config.offset
    original = experience._journal.save

    def fail_job_marker(state):
        if state.interrupted and state.interrupted.job_id:
            raise OSError("crash after submit returned")
        original(state)

    monkeypatch.setattr(experience._journal, "save", fail_job_marker)
    with pytest.raises(ValueError):
        await experience.process_update(update(identifier, "Operación con frontera incierta"))
    assert len(experience._jobs.requests) == 1
    assert experience._journal.load().interrupted.job_id is None
    monkeypatch.setattr(experience._journal, "save", original)
    revived = restart(experience)
    try:
        await revived._recover_transport()
        assert not revived._jobs.requests
        revived._jobs.approve_from_telegram.assert_not_called()
        assert "Operación con frontera incierta" in revived._client.sent[-1][1]
    finally:
        await revived.close()


async def test_suspension_does_not_acknowledge_or_lose_owner_input(experience):
    identifier = experience._config.offset
    experience._blocked = lambda: True
    await experience.process_update(update(identifier, "Cuando vuelvas revisa mi agenda"))
    assert experience._store.load().offset == identifier
    assert not experience._jobs.requests
    experience._blocked = lambda: False
    await experience.process_update(update(identifier, "Cuando vuelvas revisa mi agenda"))
    assert len(experience._jobs.requests) == 1


async def test_expired_inbox_notifies_owner_without_submitting(experience, monkeypatch):
    identifier = experience._config.offset
    monkeypatch.setattr(experience, "_process_inbox_entry", AsyncMock())
    await experience.process_update(update(identifier, "Solicitud antigua", date=0))
    revived = restart(experience)
    try:
        await revived._recover_transport()
        assert not revived._jobs.requests
        assert "no la ejecuté" in revived._client.sent[-1][1]
        assert not revived._journal.load().inbox
    finally:
        await revived.close()


async def test_owner_message_capacity_stops_before_ack_and_keeps_encrypted_queue(experience):
    experience._ensure_transport_binding()
    identifier = experience._config.offset
    record = await experience._conversations.create()
    experience._save(conversation_id=record.conversation_id)
    entries = tuple(
        InboxEntry(
            update_id=identifier + index,
            message=update(0, f"Mensaje {index}")["message"],
            conversation_id=record.conversation_id,
        )
        for index in range(4)
    )
    experience._transport_update(inbox=entries, acknowledged_offset=identifier + 4)
    experience._save(offset=identifier + 4)
    with pytest.raises(TelegramAPIError, match="telegram_inbox_full"):
        await experience.process_update(update(identifier + 4, "Quinta solicitud"))
    assert experience._store.load().offset == identifier + 4
    assert len(experience._journal.load().inbox) == 4
    assert not experience._jobs.requests
    with pytest.raises(RowAuthenticationError):
        TelegramJournal(experience._journal.path, MemoryRowCipher(b"z" * 32)).load()
    assert os.stat(experience._journal.path).st_mode & 0o777 == 0o600


async def test_changed_owner_binding_cannot_recover_previous_owners_inbox(experience, monkeypatch):
    identifier = experience._config.offset
    monkeypatch.setattr(experience, "_process_inbox_entry", AsyncMock())
    await experience.process_update(update(identifier, "Datos privados del propietario anterior"))
    experience._save(owner_id=OWNER + 1, owner_name="Otra cuenta")
    revived = restart(experience)
    try:
        await revived._recover_transport()
        assert not revived._journal.load().inbox
        assert not revived._jobs.requests and not revived._client.sent
    finally:
        await revived.close()


@pytest.mark.parametrize("kind", ["json", "envelope", "authentication", "permissions"])
async def test_invalid_journal_blocks_only_telegram_without_overwriting(experience, kind):
    path = experience._journal.path
    if kind == "json":
        path.write_bytes(b"private invalid json")
    elif kind == "envelope":
        path.write_bytes(b'{"nonce": 0}')
    elif kind == "authentication":
        TelegramJournal(path, MemoryRowCipher(b"z" * 32)).save(experience._transport)
    else:
        path.chmod(0o644)
    before = path.read_bytes()
    revived = restart(experience)
    try:
        assert revived.status()["state"] == "blocked"
        assert revived.status()["error"] == "telegram_journal_invalid"
        assert (await ipc(revived, "telegram.status")).ok
        assert not (await ipc(revived, "telegram.set_enabled", enabled=True, confirm=True)).ok
        await revived.process_update(update(revived._config.offset, "No ejecutar"))
        poll = asyncio.create_task(revived.run())
        await asyncio.sleep(0.02)
        poll.cancel()
        await asyncio.gather(poll, return_exceptions=True)
        assert not revived._jobs.requests
        revived._client.call.assert_not_called()
        assert path.read_bytes() == before
        # Other IPC-facing objects remain constructed and usable.
        record = await revived._conversations.create(title="Chat sigue disponible")
        await revived._conversations.ensure_exists(record.conversation_id)
    finally:
        await revived.close()
        path.chmod(0o600)


async def test_explicit_recovery_retains_private_backup_and_drops_old_authority(experience):
    path = experience._journal.path
    path.write_bytes(b"private corrupted transport")
    revived = restart(experience)
    try:
        denied = await ipc(revived, "telegram.journal.reset", confirm=False)
        assert not denied.ok and path.read_bytes() == b"private corrupted transport"
        restored = await ipc(revived, "telegram.journal.reset", confirm=True)
        assert restored.ok and not restored.payload["enabled"]
        assert revived._config.owner_id == OWNER
        assert revived._config.discard_backlog
        backups = list(path.parent.glob(f"{path.name}.invalid-*"))
        assert len(backups) == 1 and backups[0].read_bytes() == b"private corrupted transport"
        assert os.stat(backups[0]).st_mode & 0o777 == 0o600
        assert TelegramJournal(path, MemoryRowCipher(b"x" * 32)).load().inbox == ()
        revived._client.call = AsyncMock(return_value=[update(120, "Old action")])
        assert (await ipc(revived, "telegram.set_enabled", enabled=True, confirm=True)).ok
        await revived._discard_backlog()
        assert revived._config.offset == 121 and not revived._config.discard_backlog
        await revived.process_update(update(120, "Old action"))
        assert not revived._jobs.requests
    finally:
        await revived.close()


async def test_failed_backlog_discard_keeps_recovery_barrier(experience):
    experience._save(discard_backlog=True)
    experience._client.call = AsyncMock(side_effect=TelegramAPIError("telegram_unavailable"))
    with pytest.raises(TelegramAPIError):
        await experience._discard_backlog()
    assert experience._store.load().discard_backlog
    assert not experience._jobs.requests


async def test_reset_failure_keeps_disabled_backlog_barrier(experience, monkeypatch):
    experience._journal.path.write_bytes(b"corrupted transport fixture")
    revived = restart(experience)
    monkeypatch.setattr(revived._journal, "reset", lambda state: (_ for _ in ()).throw(OSError()))
    try:
        response = await ipc(revived, "telegram.journal.reset", confirm=True)
        assert not response.ok
        saved = revived._store.load()
        assert not saved.enabled and saved.discard_backlog
        assert revived.status()["state"] == "blocked"
        assert revived._journal.path.read_bytes() == b"corrupted transport fixture"
    finally:
        await revived.close()


async def test_recovery_barrier_refuses_direct_old_update_before_discard(experience):
    identifier = experience._config.offset
    experience._save(discard_backlog=True)
    await experience.process_update(update(identifier, "Old pending request"))
    assert not experience._jobs.requests
    assert experience._store.load().offset == identifier


async def test_deleted_original_chat_drops_inbox_without_retargeting(experience, monkeypatch):
    identifier = experience._config.offset
    monkeypatch.setattr(experience, "_process_inbox_entry", AsyncMock())
    await experience.process_update(update(identifier, "Solicitud del chat borrado"))
    deleted = experience._config.conversation_id
    await experience._conversations.delete(deleted)
    new = await experience._conversations.create(title="Otro chat")
    experience._save(conversation_id=new.conversation_id)
    revived = restart(experience)
    try:
        await revived._recover_transport()
        assert not revived._jobs.requests
        assert not revived._journal.load().inbox
        assert revived._config.conversation_id == new.conversation_id
        assert "fue eliminado" in revived._client.sent[-1][1]
    finally:
        await revived.close()


async def test_shutdown_during_media_preparation_retains_input_for_safe_restart(experience):
    identifier = experience._config.offset
    downloading = asyncio.Event()

    async def unfinished_download(*args):
        downloading.set()
        await asyncio.Event().wait()

    experience._client.download = AsyncMock(side_effect=unfinished_download)
    await experience.process_update(
        update(identifier, None, document={"file_id": "safe-document", "file_name": "notes.txt"})
    )
    await asyncio.wait_for(downloading.wait(), 1)
    await experience.close()
    assert experience._journal.load().inbox
    revived = restart(experience)
    revived._client.download = AsyncMock(return_value=b"Contenido preparado tras el reinicio.")
    try:
        await revived._recover_transport()
        await spin_until(lambda: revived._active is not None)
        assert len(revived._jobs.requests) == 1
        request = revived._jobs.requests[0][0]
        assert request.documents[0].content == "Contenido preparado tras el reinicio."
        assert not revived._journal.load().inbox
    finally:
        await revived.close()


async def test_prepared_voice_over_external_text_limit_survives_restart(experience):
    transcript = "Revisa esta instrucción detallada. " * 170
    assert 4096 < len(transcript.encode("utf-8")) <= 8192
    experience._batch_seconds = 60
    experience._transcribe = AsyncMock(return_value=transcript)
    identifier = experience._config.offset
    await experience.process_update(
        update(identifier, None, voice={"file_id": "voice-source", "duration": 30})
    )
    await spin_until(lambda: bool(experience._incoming))
    saved = experience._journal.load()
    assert saved.inbox[0].message["text"] == ""
    assert saved.inbox[0].prepared_text == transcript
    await experience.close()
    revived = restart(experience)
    revived._client.download = AsyncMock(side_effect=AssertionError("already transcribed"))
    revived._transcribe = AsyncMock(side_effect=AssertionError("already transcribed"))
    try:
        await revived._recover_transport()
        assert len(revived._jobs.requests) == 1
        assert revived._jobs.requests[0][0].text == transcript
        revived._transcribe.assert_not_called()
        revived._client.download.assert_not_called()
    finally:
        await revived.close()


async def test_suspension_during_media_preparation_preserves_request_without_latent_action(
    experience,
):
    identifier = experience._config.offset
    downloading = asyncio.Event()
    release = asyncio.Event()

    async def download(*args):
        downloading.set()
        await release.wait()
        return b"Synthetic document text"

    experience._client.download = AsyncMock(side_effect=download)
    await experience.process_update(
        update(
            identifier, "Revisa este archivo", document={"file_id": "file", "file_name": "n.txt"}
        )
    )
    await asyncio.wait_for(downloading.wait(), 1)
    experience._blocked = lambda: True
    release.set()
    await spin_until(lambda: experience._media_task is None)
    assert experience._journal.load().inbox
    assert not experience._jobs.requests
    experience._blocked = lambda: False
    await experience._recover_transport()
    assert not experience._jobs.requests
    assert "No repetí la operación" in experience._client.sent[-1][1]
    assert "Revisa este archivo" in experience._client.sent[-1][1]
    assert not experience._journal.load().inbox


async def test_callbacks_commands_and_unknown_users_never_enter_replayable_inbox(experience):
    identifier = experience._config.offset
    await experience.process_update({"update_id": identifier, "callback_query": {"data": "a:old"}})
    await experience.process_update(update(identifier + 1, "/estado"))
    await experience.process_update(update(identifier + 2, "Unknown", owner=OWNER + 1))
    assert not experience._journal.load().inbox
    assert not experience._jobs.requests
    experience._jobs.approve_from_telegram.assert_not_called()
    assert experience._journal.load().acknowledged_offset == identifier + 3


async def test_busy_receipt_is_not_attributed_to_or_replayed_with_interrupted_job(
    experience, monkeypatch
):
    identifier = experience._config.offset
    await experience.process_update(update(identifier, "Primera operación"))
    request = experience._jobs.requests[0][0]
    await experience._conversations.record_exchange(
        experience._config.conversation_id,
        user_content=request.text,
        assistant_content="Resultado de la primera operación",
        request_id=request.request_id,
    )
    monkeypatch.setattr(
        experience,
        "_process_acknowledged_update",
        AsyncMock(side_effect=OSError("crash before busy rejection")),
    )
    with pytest.raises(OSError):
        await experience.process_update(update(identifier + 1, "Segunda operación distinta"))
    assert experience._journal.load().inbox[0].received_while_active
    revived = restart(experience)
    try:
        await revived._recover_transport()
        assert not revived._jobs.requests
        texts = [message[1] for message in revived._client.sent]
        assert texts[0] == "Resultado de la primera operación"
        assert "Segunda operación distinta" in texts[1]
        assert "No la ejecuté" in texts[1]
        assert not revived._journal.load().inbox
    finally:
        await revived.close()
