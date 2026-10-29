"""Explicit local Ogg decode to bounded mono PCM16 WAV in RAM.

No model, command, network client, temporary file or inference fallback is used.
The caller keeps the original attachment and authenticates the native speech
worker separately using the derived WAV hash returned here.
"""

from __future__ import annotations

import hashlib
import io
import re
import wave
from dataclasses import dataclass, field

from .media_documents import MediaError, MediaLimits, checkpoint


@dataclass(frozen=True)
class DecodedAudio:
    payload: bytes = field(repr=False)
    input_sha256: str
    derived_sha256: str
    sample_rate: int
    source_channels: int
    frame_count: int
    duration_seconds: float
    codec_version: str
    source_subtype: str

    @property
    def mime(self):
        return "audio/wav"

    def public(self):
        return {
            "format": "jarvis-local-audio-decode/1",
            "input_sha256": self.input_sha256,
            "derived_sha256": self.derived_sha256,
            "derived_mime": self.mime,
            "byte_length": len(self.payload),
            "sample_rate": self.sample_rate,
            "source_channels": self.source_channels,
            "channels": 1,
            "frame_count": self.frame_count,
            "duration_seconds": self.duration_seconds,
            "codec": "python-soundfile",
            "codec_version": self.codec_version,
            "source_subtype": self.source_subtype,
            "local_only": True,
            "original_bytes_modified": False,
        }


def decode_ogg_to_wav(
    payload, *, expected_sha256=None, limits=None, guard=None, cancellation=None, deadline=None
):
    """Decode only Ogg OPUS/VORBIS under explicit local size/time bounds.

    Cancellation/deadline are checked around every 4096-frame native read. A host
    must retain custody if its enclosing worker has not finished native cleanup;
    this function cannot forcibly interrupt an in-progress C decoder call.
    """
    limits = limits or MediaLimits()

    def current():
        checkpoint(cancellation, deadline)
        if guard is not None:
            if not callable(guard):
                raise MediaError("media_authority_required")
            guard()
        checkpoint(cancellation, deadline)

    current()
    if (
        type(payload) is not bytes
        or not payload.startswith(b"OggS")
        or not 1 <= len(payload) <= limits.max_file_bytes
    ):
        raise MediaError("media_audio_invalid")
    original_hash = hashlib.sha256(payload).hexdigest()
    if expected_sha256 is not None:
        if (
            not isinstance(expected_sha256, str)
            or re.fullmatch(r"[a-f0-9]{64}", expected_sha256) is None
        ):
            raise MediaError("media_digest_invalid")
        if expected_sha256 != original_hash:
            raise MediaError("media_attachment_changed")
    try:
        import numpy as np
        import soundfile as sf
    except (ImportError, OSError):
        raise MediaError("media_audio_codec_unavailable") from None
    current()
    try:
        if (
            sf.__version__ != "0.13.1"
            or "OPUS" not in sf.available_subtypes("OGG")
            or not isinstance(sf.__libsndfile_version__, str)
            or not re.fullmatch(r"[0-9]+(?:\.[0-9]+){1,3}", sf.__libsndfile_version__)
        ):
            raise MediaError("media_audio_codec_unavailable")
        with sf.SoundFile(io.BytesIO(payload), mode="r") as source:
            current()
            rate, channels, frames = source.samplerate, source.channels, source.frames
            if (
                source.format != "OGG"
                or source.subtype not in {"OPUS", "VORBIS"}
                or type(rate) is not int
                or not 8000 <= rate <= 48000
                or channels not in {1, 2}
                or not 0 < frames <= rate * limits.max_duration_seconds
                or frames * 2 + 44 > limits.max_file_bytes
            ):
                raise MediaError("media_audio_limit")
            output = io.BytesIO()
            decoded = 0
            with wave.open(output, "wb") as target:
                target.setnchannels(1)
                target.setsampwidth(2)
                target.setframerate(rate)
                while True:
                    current()
                    block = source.read(4096, dtype="float32", always_2d=True)
                    current()
                    if not len(block):
                        break
                    if (
                        block.ndim != 2
                        or block.shape[1] != channels
                        or not np.isfinite(block).all()
                    ):
                        raise MediaError("media_audio_invalid")
                    decoded += len(block)
                    if (
                        decoded > frames
                        or decoded > rate * limits.max_duration_seconds
                        or decoded * 2 + 44 > limits.max_file_bytes
                    ):
                        raise MediaError("media_audio_limit")
                    mono = block[:, 0] if channels == 1 else block.mean(axis=1, dtype=np.float32)
                    pcm = np.rint(np.clip(mono, -1.0, 1.0) * 32767).astype("<i2")
                    target.writeframesraw(pcm.tobytes())
                if decoded != frames:
                    raise MediaError("media_audio_invalid")
            result = output.getvalue()
            if len(result) > limits.max_file_bytes:
                raise MediaError("media_audio_limit")
            current()
            return DecodedAudio(
                result,
                original_hash,
                hashlib.sha256(result).hexdigest(),
                rate,
                channels,
                decoded,
                decoded / rate,
                "soundfile-0.13.1/libsndfile-" + sf.__libsndfile_version__,
                source.subtype,
            )
    except (
        sf.SoundFileError,
        RuntimeError,
        ValueError,
        OSError,
        OverflowError,
        wave.Error,
    ) as error:
        if isinstance(error, MediaError):
            raise
        raise MediaError("media_audio_decode_failed") from None
