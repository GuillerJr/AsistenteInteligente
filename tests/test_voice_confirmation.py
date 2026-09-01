from __future__ import annotations

from dataclasses import dataclass

import pytest

from aegis_core.biometric_training_service import VoiceConfirmationVerifier
from aegis_core.providers.mlx_provider import MLXProviderError


@dataclass
class _Transcriber:
    text: str = "Aprobado."
    should_fail: bool = False

    async def transcribe_pcm_s16le(
        self,
        pcm: bytes,
        *,
        sample_rate: int = 16_000,
    ) -> str:
        assert pcm
        assert sample_rate == 16_000
        if self.should_fail:
            raise MLXProviderError("closed")
        return self.text


def _pcm(seed: int = 1) -> bytes:
    return bytes([seed, 0]) * 8_000


@pytest.mark.asyncio
async def test_voice_confirmation_requires_owner_and_frozen_semantic_operator() -> None:
    accepted = VoiceConfirmationVerifier(_Transcriber(text="SÍ!!!"))
    rejected = VoiceConfirmationVerifier(_Transcriber(text="quizá adelante"))

    result = await accepted.verify(
        _pcm(),
        speaker_identifier="guillermo",
        speaker_confidence=0.78,
        owner_profile_match=True,
    )
    semantic_mismatch = await rejected.verify(
        _pcm(2),
        speaker_identifier="guillermo",
        speaker_confidence=0.99,
        owner_profile_match=True,
    )

    assert result.authorized
    assert result.speaker_verified and result.semantic_verified
    assert not semantic_mismatch.authorized
    assert semantic_mismatch.speaker_verified


@pytest.mark.asyncio
async def test_voice_confirmation_rejects_replay_and_untrusted_speaker() -> None:
    verifier = VoiceConfirmationVerifier(_Transcriber())
    first = await verifier.verify(
        _pcm(),
        speaker_identifier="guillermo",
        speaker_confidence=0.91,
        owner_profile_match=True,
    )
    replay = await verifier.verify(
        _pcm(),
        speaker_identifier="guillermo",
        speaker_confidence=0.91,
        owner_profile_match=True,
    )
    untrusted = await verifier.verify(
        _pcm(2),
        speaker_identifier="unknown",
        speaker_confidence=1.0,
        owner_profile_match=True,
    )

    assert first.authorized
    assert not replay.authorized
    assert not untrusted.authorized
    assert not untrusted.speaker_verified


@pytest.mark.asyncio
async def test_voice_confirmation_fails_closed_when_local_whisper_fails() -> None:
    verifier = VoiceConfirmationVerifier(_Transcriber(should_fail=True))

    result = await verifier.verify(
        _pcm(),
        speaker_identifier="guillermo",
        speaker_confidence=0.95,
        owner_profile_match=True,
    )

    assert not result.authorized
    assert not result.semantic_verified
