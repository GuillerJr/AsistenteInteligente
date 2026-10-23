"""A spoken approval may only stand in for the button on actions that read.

Nearby speech once approved a Gmail draft on its own (22-10-2026): the app opened the
microphone for the approval and its two-class voice model took another voice for the
owner. Writes, sends, deletes and computer use must need the button or Touch ID.
"""

import dataclasses
from types import SimpleNamespace

import pytest
from test_jobs import _approval_jobs, _awaiting_confirmation, _terminal

from aegis_core.contracts import Capability, UserRequest
from aegis_core.job_contracts import JobConfirmationError, JobStatus


class _ReadingBroker:
    """The default broker, with its network tool presented as a mail read."""

    def __init__(self, broker) -> None:
        self._broker = broker

    def definition(self, name):
        definition = self._broker.definition(name)
        return dataclasses.replace(definition, capability=Capability.MAIL_READ)

    def __getattr__(self, name):
        return getattr(self._broker, name)


class _Verifier:
    def __init__(self) -> None:
        self.calls = 0

    async def verify(self, *args, **kwargs):
        self.calls += 1
        return SimpleNamespace(authorized=True, speaker_verified=True, semantic_verified=True)


async def _approve_by_voice(jobs, job_id, digest):
    return await jobs.approve_by_voice(
        job_id=job_id, call_digest=digest, pcm=bytes(8000),
        speaker_identifier="owner", speaker_confidence=0.99, owner_profile_match=True,
        transcript="sí",
    )


@pytest.mark.asyncio
async def test_voice_cannot_approve_an_action_that_changes_data(tmp_path):
    jobs, audit = _approval_jobs(tmp_path)
    verifier = _Verifier()
    jobs._authorization._voice_verifier = verifier
    try:
        queued = await jobs.submit(UserRequest(text="Escanea loopback"))
        pending = await _awaiting_confirmation(jobs, queued.job_id)
        assert pending.confirmation.voice_approval_available is False
        with pytest.raises(JobConfirmationError):
            await _approve_by_voice(jobs, queued.job_id, pending.confirmation.call_digest)
        assert verifier.calls == 0  # Refused before any audio is examined.
        still = await jobs.status(queued.job_id)
        assert still.status is JobStatus.AWAITING_CONFIRMATION  # The button still works.
        events = [record.event_type for record in audit.verify()]
        assert "voice_confirmation_refused" in events
        assert "tool_execution" not in events
        await jobs.approve(queued.job_id, pending.confirmation.call_digest)
        assert (await _terminal(jobs, queued.job_id)).status is JobStatus.COMPLETED
    finally:
        await jobs.close()


@pytest.mark.asyncio
async def test_voice_can_still_approve_an_action_that_only_reads(tmp_path):
    jobs, _ = _approval_jobs(tmp_path)
    jobs._workflow_broker = _ReadingBroker(jobs._workflow_broker)
    verifier = _Verifier()
    jobs._authorization._voice_verifier = verifier
    try:
        queued = await jobs.submit(UserRequest(text="Escanea loopback"))
        pending = await _awaiting_confirmation(jobs, queued.job_id)
        assert pending.confirmation.voice_approval_available is True
        await _approve_by_voice(jobs, queued.job_id, pending.confirmation.call_digest)
        assert verifier.calls == 1
        assert (await _terminal(jobs, queued.job_id)).status is JobStatus.COMPLETED
    finally:
        await jobs.close()
