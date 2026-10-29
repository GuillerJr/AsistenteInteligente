"""Real pinned local codec, artificial tone; no microphone, provider or network."""

import hashlib
import io
import threading
import time
import wave

import numpy as np
import pytest
import soundfile as sf

from jarvis_core.media_audio import decode_ogg_to_wav
from jarvis_core.media_documents import (
    MediaError,
    MediaLimits,
    complete_native_document,
    extract_document,
    rebind_derived_audio,
)


def artificial_ogg(*, channels=1, seconds=0.25, rate=48000, subtype="OPUS"):
    samples = np.arange(int(rate * seconds), dtype=np.float32)
    tone = 0.1 * np.sin(2 * np.pi * 440 * samples / rate)
    if channels == 2:
        tone = np.stack([tone, -tone * 0.5], axis=1)
    target = io.BytesIO()
    sf.write(target, tone, rate, format="OGG", subtype=subtype)
    return target.getvalue()


def test_codec_is_exact_pinned_and_opus_available_in_actual_binary():
    assert sf.__version__ == "0.13.1"
    assert "OPUS" in sf.available_subtypes("OGG")
    assert sf.__libsndfile_version__


@pytest.mark.parametrize("subtype", ["OPUS", "VORBIS"])
@pytest.mark.parametrize("channels", [1, 2])
def test_actual_codec_decodes_original_to_mono_pcm16_without_files_or_inference(subtype, channels):
    original = artificial_ogg(channels=channels, subtype=subtype)
    source_hash = hashlib.sha256(original).hexdigest()
    result = decode_ogg_to_wav(original, expected_sha256=source_hash)
    assert result.input_sha256 == source_hash and result.derived_sha256 != source_hash
    assert hashlib.sha256(original).hexdigest() == source_hash
    assert result.derived_sha256 == hashlib.sha256(result.payload).hexdigest()
    assert result.mime == "audio/wav" and result.source_channels == channels
    assert result.source_subtype == subtype and result.duration_seconds <= 0.3
    assert "soundfile-0.13.1/libsndfile-" in result.codec_version
    assert result.public()["local_only"] and result.public()["original_bytes_modified"] is False
    assert "payload" not in result.public()
    with wave.open(io.BytesIO(result.payload)) as decoded:
        assert decoded.getnchannels() == 1 and decoded.getsampwidth() == 2
        assert decoded.getframerate() == result.sample_rate
        assert decoded.getnframes() == result.frame_count
        pcm = np.frombuffer(decoded.readframes(result.frame_count), dtype="<i2")
        assert pcm.size == result.frame_count and np.any(pcm)


def test_source_digest_mismatch_is_rejected_before_codec_entry(monkeypatch):
    original = artificial_ogg()

    def forbidden(*args, **kwargs):
        raise AssertionError("Decoder entered for unreviewed bytes")

    monkeypatch.setattr(sf, "SoundFile", forbidden)
    with pytest.raises(MediaError, match="media_attachment_changed"):
        decode_ogg_to_wav(original, expected_sha256="a" * 64)


@pytest.mark.parametrize("payload", [b"", b"not audio", b"OggS invalid synthetic container"])
def test_malformed_containers_fail_with_stable_error_without_echoing_content(payload):
    with pytest.raises(MediaError) as caught:
        decode_ogg_to_wav(payload)
    assert str(caught.value) in {"media_audio_invalid", "media_audio_decode_failed"}
    assert "synthetic" not in str(caught.value)


def test_duration_and_output_size_limits_precede_large_decode():
    original = artificial_ogg(seconds=2)
    with pytest.raises(MediaError, match="media_audio_limit"):
        decode_ogg_to_wav(original, limits=MediaLimits(max_duration_seconds=1))
    short = artificial_ogg()
    with pytest.raises(MediaError, match="media_audio_limit"):
        decode_ogg_to_wav(short, limits=MediaLimits(max_file_bytes=max(len(short) + 1, 4096)))


def test_cancel_and_expiry_before_import_and_during_actual_block_decode():
    original = artificial_ogg(seconds=0.5)
    stopped = threading.Event()
    stopped.set()
    with pytest.raises(MediaError, match="media_cancelled"):
        decode_ogg_to_wav(original, cancellation=stopped)
    with pytest.raises(MediaError, match="media_timeout"):
        decode_ogg_to_wav(original, deadline=time.monotonic() - 1)
    stopped.clear()
    calls = 0

    def guard():
        nonlocal calls
        calls += 1
        if calls == 5:
            stopped.set()

    with pytest.raises(MediaError, match="media_cancelled"):
        decode_ogg_to_wav(original, cancellation=stopped, guard=guard)


def test_missing_codec_capability_does_not_invoke_a_remote_fallback(monkeypatch):
    original = artificial_ogg()
    monkeypatch.setattr(sf, "available_subtypes", lambda format=None: {})
    with pytest.raises(MediaError, match="media_audio_codec_unavailable"):
        decode_ogg_to_wav(original)


def observed_derivation():
    source = artificial_ogg()
    original = extract_document(source, "voz-original.ogg", "audio/ogg")
    decoded = decode_ogg_to_wav(source, expected_sha256=original.sha256)
    wav = extract_document(decoded.payload, "derivado.wav", "audio/wav")
    observed = complete_native_document(
        wav,
        {
            "schema": "jarvis.media.v1",
            "succeeded": True,
            "verified": True,
            "effect_status": "known",
            "source_sha256": decoded.derived_sha256,
            "kind": "audio",
            "segments": [{"locator": "time:0-0.25", "text": "TRANSCRIPCION FIXTURE"}],
            "metadata": {"duration_seconds": decoded.duration_seconds},
            "truncated": False,
            "on_device": True,
        },
    )
    return original, observed, decoded.public()


def test_derived_observation_keeps_both_source_hashes_without_forging_worker_identity():
    original, observed, receipt = observed_derivation()
    result = rebind_derived_audio(original, observed, receipt)
    assert result.name == original.name and result.sha256 == original.sha256
    assert result.media_type.mime == "audio/ogg" and not result.requires_native
    assert result.metadata["worker_source_sha256"] == observed.sha256
    assert result.metadata["derivation"]["input_sha256"] == original.sha256
    assert result.metadata["derivation"]["derived_sha256"] == observed.sha256
    assert result.segments == observed.segments
    assert not result.public()["content_truth_verified"]


@pytest.mark.parametrize(
    "change",
    [
        {"input_sha256": "f" * 64},
        {"derived_sha256": "f" * 64},
        {"local_only": False},
        {"original_bytes_modified": True},
        {"sample_rate": 96000},
        {"duration_seconds": 121},
        {"frame_count": 1},
        {"channels": 2},
        {"payload": "data:audio/wav;base64,AAAA"},
        {"codec_version": "unreviewed"},
    ],
)
def test_derivation_claim_cannot_rebind_other_bytes_or_unreviewed_codec(change):
    original, observed, receipt = observed_derivation()
    with pytest.raises(MediaError, match="media_audio_derivation_invalid"):
        rebind_derived_audio(original, observed, {**receipt, **change})
