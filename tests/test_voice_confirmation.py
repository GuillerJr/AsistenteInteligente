from __future__ import annotations

import asyncio
from dataclasses import dataclass

import pytest

import aegis_core.biometric_training_service as biometric
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


@pytest.mark.parametrize("text", [
    "¿Sí?", "Confirmo?", '"aprobado"', "«adelante»", "`procede`", "sí…",
    "si/no", "no confirmo", "sí, pero espera", "\u202eaprobado",
])
async def test_voice_questions_quotes_and_ambiguous_operators_never_authorize(text) -> None:
    result = await VoiceConfirmationVerifier(_Transcriber(text=text)).verify(
        _pcm(), speaker_identifier="owner", speaker_confidence=0.99, owner_profile_match=True,
    )
    assert not result.authorized


@pytest.mark.parametrize("confidence", [True, -1, 1.01, float("nan"), float("inf")])
async def test_voice_confidence_must_be_a_real_probability(confidence) -> None:
    result = await VoiceConfirmationVerifier(_Transcriber()).verify(
        _pcm(), speaker_identifier="owner", speaker_confidence=confidence, owner_profile_match=True,
    )
    assert not result.authorized


async def test_unverified_speakers_do_not_evict_replay_protection(monkeypatch) -> None:
    monkeypatch.setattr(biometric, "_MAX_CONFIRMATION_REPLAYS", 2)
    verifier = VoiceConfirmationVerifier(_Transcriber())
    first = await verifier.verify(
        _pcm(), speaker_identifier="owner", speaker_confidence=0.99, owner_profile_match=True,
    )
    for seed in (2, 3, 4):
        rejected = await verifier.verify(
            _pcm(seed), speaker_identifier="guest", speaker_confidence=0.99,
            owner_profile_match=False,
        )
        assert not rejected.authorized
    replay = await verifier.verify(
        _pcm(), speaker_identifier="owner", speaker_confidence=0.99, owner_profile_match=True,
    )
    assert first.authorized
    assert not replay.authorized
    assert len(verifier._recent) == 1


async def test_full_voice_cache_fails_closed_until_expiry(monkeypatch) -> None:
    monkeypatch.setattr(biometric, "_MAX_CONFIRMATION_REPLAYS", 2)
    clock = [100.0]
    verifier = VoiceConfirmationVerifier(_Transcriber(), monotonic_clock=lambda: clock[0])

    async def verify(seed):
        return await verifier.verify(
            _pcm(seed), speaker_identifier="owner", speaker_confidence=0.99,
            owner_profile_match=True,
        )

    assert (await verify(1)).authorized
    assert (await verify(2)).authorized
    assert not (await verify(3)).authorized
    assert not (await verify(1)).authorized
    assert len(verifier._recent) == 2
    clock[0] += biometric._CONFIRMATION_TTL_SECONDS
    assert (await verify(3)).authorized


async def test_concurrent_voice_replay_only_transcribes_once() -> None:
    class BlockingTranscriber(_Transcriber):
        calls = 0
        started = asyncio.Event()
        release = asyncio.Event()

        async def transcribe_pcm_s16le(self, pcm, *, sample_rate=16000):
            self.calls += 1
            self.started.set()
            await self.release.wait()
            return self.text

    transcriber = BlockingTranscriber()
    verifier = VoiceConfirmationVerifier(transcriber)

    async def verify():
        return await verifier.verify(
            _pcm(), speaker_identifier="owner", speaker_confidence=0.99,
            owner_profile_match=True,
        )

    first = asyncio.create_task(verify())
    try:
        await asyncio.wait_for(transcriber.started.wait(), 1)
        assert not (await verify()).authorized
        transcriber.release.set()
        assert (await asyncio.wait_for(first, 1)).authorized
        assert transcriber.calls == 1
    finally:
        first.cancel()
        await asyncio.gather(first, return_exceptions=True)


@pytest.mark.parametrize("clock_value", [True, -1.0, float("inf"), float("nan")])
async def test_invalid_voice_clock_cannot_authorize(clock_value) -> None:
    verifier = VoiceConfirmationVerifier(_Transcriber(), monotonic_clock=lambda: clock_value)
    result = await verifier.verify(
        _pcm(), speaker_identifier="owner", speaker_confidence=0.99, owner_profile_match=True,
    )
    assert not result.authorized
    assert not verifier._recent


@pytest.mark.parametrize("finished", [99.0, 100.0 + biometric._CONFIRMATION_TTL_SECONDS])
async def test_voice_proof_cannot_outlive_its_clock_or_reservation(finished) -> None:
    clock = [100.0]

    class DelayedTranscriber(_Transcriber):
        async def transcribe_pcm_s16le(self, pcm, *, sample_rate=16000):
            clock[0] = finished
            return self.text

    verifier = VoiceConfirmationVerifier(DelayedTranscriber(), monotonic_clock=lambda: clock[0])
    result = await verifier.verify(
        _pcm(), speaker_identifier="owner", speaker_confidence=0.99, owner_profile_match=True,
    )
    assert not result.authorized


async def test_cancelled_transcription_keeps_audio_reserved_against_replay() -> None:
    class CancelledTranscriber(_Transcriber):
        calls = 0

        async def transcribe_pcm_s16le(self, pcm, *, sample_rate=16000):
            self.calls += 1
            raise asyncio.CancelledError

    transcriber = CancelledTranscriber()
    verifier = VoiceConfirmationVerifier(transcriber)
    evidence = dict(speaker_identifier="owner", speaker_confidence=0.99, owner_profile_match=True)
    with pytest.raises(asyncio.CancelledError):
        await verifier.verify(_pcm(), **evidence)
    assert not (await verifier.verify(_pcm(), **evidence)).authorized
    assert transcriber.calls == 1


@pytest.mark.parametrize("operator", ["¡Sí!", "Aprobado.", " confirmo ", "OK"])
async def test_unambiguous_voice_operators_still_authorize(operator) -> None:
    result = await VoiceConfirmationVerifier(_Transcriber(text=operator)).verify(
        _pcm(), speaker_identifier="owner", speaker_confidence=0.99, owner_profile_match=True,
    )
    assert result.authorized
