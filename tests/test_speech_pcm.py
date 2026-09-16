import struct

import pytest

from aegis_core.speech_pcm import SpeechPauseSmoother


def pcm(values):
    return struct.pack(f"<{len(values)}h", *values)


def test_leading_and_sentence_silences_are_bounded_without_touching_signal():
    smoother = SpeechPauseSmoother()
    lead = [0] * 5_000
    signal = [33, -33, 32_767, -32_768, 2_000, -2_000]
    pause = [16] * 20_000
    output = smoother.process(pcm(lead + signal + pause + signal))
    smoother.finish()

    assert output == pcm(
        lead[:smoother.LEADING_FRAMES] + signal + pause[:smoother.PAUSE_FRAMES] + signal
    )
    assert smoother.removed_frames == 25_000 - smoother.LEADING_FRAMES - smoother.PAUSE_FRAMES


@pytest.mark.parametrize("packet_bytes", [1, 3, 127, 4096, 32_768])
def test_identical_output_regardless_of_odd_or_even_packet_boundaries(packet_bytes):
    source = pcm([0] * 2_000 + [900, -900] + [32, -32] * 4_000 + [900] + [0] * 8_000)
    whole = SpeechPauseSmoother()
    expected = whole.process(source)
    whole.finish()
    packetized = SpeechPauseSmoother()
    result = b"".join(
        packetized.process(source[offset:offset + packet_bytes])
        for offset in range(0, len(source), packet_bytes)
    )
    packetized.finish()
    assert result == expected
    assert packetized.removed_frames == whole.removed_frames


@pytest.mark.parametrize("frames", [0, 1, 1000, SpeechPauseSmoother.PAUSE_FRAMES])
def test_ordinary_pauses_and_signal_above_threshold_are_unchanged(frames):
    source = pcm([1000, -1000] + [0] * frames + [33, -33] * 8_000)
    smoother = SpeechPauseSmoother()
    assert smoother.process(source) == source
    smoother.finish()
    assert smoother.removed_frames == 0


def test_incomplete_pcm_is_rejected_and_cannot_leak_into_another_utterance():
    smoother = SpeechPauseSmoother()
    assert smoother.process(b"\x10") == b""
    with pytest.raises(ValueError, match="incomplete PCM"):
        smoother.finish()
    with pytest.raises(ValueError, match="closed"):
        smoother.process(b"\x00")
    fresh = SpeechPauseSmoother()
    assert fresh.process(pcm([1000])) == pcm([1000])
    fresh.finish()


def test_small_audio_is_not_padded_and_finish_never_adds_fake_samples():
    smoother = SpeechPauseSmoother()
    assert smoother.process(b"") == b""
    assert smoother.process(pcm([0, 1, -1, 1000, 0])) == pcm([0, 1, -1, 1000, 0])
    assert smoother.finish() is None
    with pytest.raises(ValueError, match="closed"):
        smoother.process(b"")
