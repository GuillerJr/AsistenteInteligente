from __future__ import annotations

import array
import sys


class SpeechPauseSmoother:
    """Bound near-silent runs in 22050 Hz mono Int16 output, never microphone input.

    Samples above -60 dBFS are byte-for-byte unchanged. This is pause shortening,
    not denoising, time stretching, pitch shifting or a claim of phoneme detection.
    State is per synthesis request and independent of transport packet boundaries.
    """

    SAMPLE_RATE = 22_050
    QUIET_PEAK = 32
    LEADING_FRAMES = SAMPLE_RATE * 80 // 1_000
    PAUSE_FRAMES = SAMPLE_RATE * 300 // 1_000

    def __init__(self) -> None:
        self._carry = b""
        self._has_signal = False
        self._quiet_frames = 0
        self._closed = False
        self.removed_frames = 0

    def process(self, pcm: bytes) -> bytes:
        if self._closed:
            raise ValueError("speech pause smoother is closed")
        if not isinstance(pcm, bytes):
            raise ValueError("speech PCM must be bytes")
        data = self._carry + pcm
        complete_bytes = len(data) - len(data) % 2
        self._carry = data[complete_bytes:]
        samples = array.array("h")
        samples.frombytes(data[:complete_bytes])
        if sys.byteorder != "little":
            samples.byteswap()
        output = array.array("h")
        for sample in samples:
            if abs(sample) <= self.QUIET_PEAK:
                limit = self.PAUSE_FRAMES if self._has_signal else self.LEADING_FRAMES
                if self._quiet_frames >= limit:
                    self.removed_frames += 1
                    continue
                self._quiet_frames += 1
            else:
                self._has_signal = True
                self._quiet_frames = 0
            output.append(sample)
        if sys.byteorder != "little":
            output.byteswap()
        return output.tobytes()

    def finish(self) -> None:
        self._closed = True
        incomplete = bool(self._carry)
        self._carry = b""
        if incomplete:
            raise ValueError("speech stream returned incomplete PCM")
