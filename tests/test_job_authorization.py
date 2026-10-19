from __future__ import annotations

from dataclasses import dataclass

import pytest
from pydantic import ValidationError

from aegis_core.job_authorization import (
    ApprovalChannel,
    ApprovalGrant,
    JobAuthorizationCoordinator,
)
from aegis_core.job_contracts import JobConfirmationError
from aegis_core.job_ipc import VoiceJobApprovalPayload
from aegis_core.job_tool_presenter import JobToolPresenter
from aegis_core.job_tool_runtime import JobToolRuntime
from aegis_core.tools.audit import NullAuditSink
from aegis_core.tools.broker import VoiceConfirmationEvidence


@dataclass(frozen=True, slots=True)
class _VerificationResult:
    speaker_verified: bool
    semantic_verified: bool

    @property
    def authorized(self) -> bool:
        return True


class _Verifier:
    def __init__(self, result: _VerificationResult) -> None:
        self._result = result
        self.calls = 0

    async def verify(
        self,
        pcm: bytes,
        *,
        speaker_identifier: str,
        speaker_confidence: float,
        owner_profile_match: bool,
        transcript: str | None = None,
    ) -> _VerificationResult:
        del pcm, speaker_identifier, speaker_confidence, owner_profile_match, transcript
        self.calls += 1
        return self._result


def _coordinator(verifier: _Verifier) -> JobAuthorizationCoordinator:
    audit = NullAuditSink()
    presenter = JobToolPresenter(None)
    runtime = JobToolRuntime(
        broker=None,
        policy_context=None,
        confirmation_store=None,
        executor=None,
        audit_sink=audit,
        presenter=presenter,
    )
    return JobAuthorizationCoordinator(
        runtime=runtime,
        presenter=presenter,
        voice_verifier=verifier,
        audit_sink=audit,
    )


def test_approval_grant_rejects_evidence_that_does_not_match_channel() -> None:
    with pytest.raises(ValueError, match="verified channel"):
        ApprovalGrant(
            channel=ApprovalChannel.LOCAL_USER,
            approved_by="verified-owner-voice",
            voice_confirmation=VoiceConfirmationEvidence(
                speaker_verified=True,
                semantic_verified=True,
            ),
        )


@pytest.mark.asyncio
async def test_voice_grant_requires_both_security_gates_even_if_verifier_claims_success() -> None:
    verifier = _Verifier(_VerificationResult(True, False))

    with pytest.raises(JobConfirmationError, match="was rejected"):
        await _coordinator(verifier).verified_voice_grant(
            bytes(8_000),
            speaker_identifier="owner",
            speaker_confidence=0.99,
            owner_profile_match=True,
        )

    assert verifier.calls == 1


@pytest.mark.asyncio
async def test_invalid_voice_pcm_is_rejected_before_biometric_processing() -> None:
    verifier = _Verifier(_VerificationResult(True, True))

    with pytest.raises(JobConfirmationError, match="PCM is invalid"):
        await _coordinator(verifier).verified_voice_grant(
            bytes(7_999),
            speaker_identifier="owner",
            speaker_confidence=0.99,
            owner_profile_match=True,
        )

    assert verifier.calls == 0


@pytest.mark.parametrize("channel", ["untrusted", "verified_owner_voice", None])
def test_unknown_or_untyped_approval_channel_cannot_claim_verified_voice(channel) -> None:
    with pytest.raises(ValueError, match="verified channel"):
        ApprovalGrant(
            channel=channel, approved_by="verified-owner-voice",
            voice_confirmation=VoiceConfirmationEvidence(True, True),
        )


@pytest.mark.parametrize("value", [1, "true", "false"])
async def test_truthy_verifier_fields_are_not_security_evidence(value) -> None:
    verifier = _Verifier(_VerificationResult(value, True))
    with pytest.raises(JobConfirmationError):
        await _coordinator(verifier).verified_voice_grant(
            bytes(8000), speaker_identifier="owner", speaker_confidence=0.99,
            owner_profile_match=True,
        )


@pytest.mark.parametrize("changes", [
    {"speaker_confidence": True}, {"speaker_confidence": "0.99"},
    {"owner_profile_match": 1}, {"owner_profile_match": "true"},
])
def test_voice_ipc_rejects_coerced_authority_fields(changes) -> None:
    with pytest.raises(ValidationError):
        VoiceJobApprovalPayload.model_validate({
            "job_id": "11111111-1111-4111-8111-111111111111", "call_digest": "a" * 64,
            "encoding": "pcm_s16le", "sample_rate_hz": 16000, "channels": 1,
            "pcm_base64": "A" * 10668, "speaker_identifier": "owner",
            "speaker_confidence": 0.99, "owner_profile_match": True, **changes,
        })
