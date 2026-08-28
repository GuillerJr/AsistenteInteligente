from __future__ import annotations

import asyncio

import pytest

from aegis_core.ipc.framing import (
    HEADER,
    MAGIC,
    MAX_CHUNK_BYTES,
    StreamFrameType,
    encode_frames,
    encode_stream,
    read_message,
)
from aegis_core.ipc.protocol import IpcAuthenticator, ProtocolError

AUTHENTICATOR = IpcAuthenticator(bytes.fromhex("4a" * 32))


def _reader(data: bytes) -> asyncio.StreamReader:
    reader = asyncio.StreamReader(limit=65_537)
    reader.feed_data(data)
    reader.feed_eof()
    return reader


@pytest.mark.asyncio
async def test_stream_round_trip_uses_strict_16_kib_chunks() -> None:
    payload = bytes(range(256)) * 200
    frames = encode_stream(payload, AUTHENTICATOR)

    assert len(frames) == 4
    first_header = frames[0][: HEADER.size]
    assert HEADER.unpack(first_header) == (
        MAGIC,
        StreamFrameType.START,
        0,
        MAX_CHUNK_BYTES,
        0,
    )

    decoded, metrics = await read_message(
        _reader(b"".join(frames)),
        AUTHENTICATOR,
        legacy_frame_bytes=65_536,
    )
    assert decoded == payload
    assert metrics.framed is True
    assert metrics.frame_count == 4


def test_transport_exposes_independently_writable_authenticated_frames() -> None:
    payload = b"m" * 40_000

    frames, metrics = encode_frames(
        payload,
        AUTHENTICATOR,
        legacy_frame_bytes=65_536,
    )

    assert metrics.framed is True
    assert len(frames) == 3
    assert all(len(frame) <= HEADER.size + MAX_CHUNK_BYTES + 32 for frame in frames)
    assert [HEADER.unpack(frame[: HEADER.size])[2] for frame in frames] == [0, 1, 2]


@pytest.mark.asyncio
async def test_stream_rejects_tampered_payload_authentication() -> None:
    frames = list(encode_stream(b"s" * 20_000, AUTHENTICATOR))
    tampered = bytearray(frames[1])
    tampered[HEADER.size] ^= 0x01
    frames[1] = bytes(tampered)

    with pytest.raises(ProtocolError, match="authentication failed"):
        await read_message(
            _reader(b"".join(frames)),
            AUTHENTICATOR,
            legacy_frame_bytes=65_536,
        )


@pytest.mark.asyncio
async def test_stream_rejects_skipped_sequence_even_with_valid_hmac() -> None:
    frames = list(encode_stream(b"q" * 20_000, AUTHENTICATOR))
    final_payload = frames[1][HEADER.size:-32]
    wrong_header = HEADER.pack(
        MAGIC,
        StreamFrameType.END,
        2,
        len(final_payload),
        0,
    )
    frames[1] = (
        wrong_header
        + final_payload
        + AUTHENTICATOR.authenticate_frame(wrong_header + final_payload)
    )

    with pytest.raises(ProtocolError, match="sequence is discontinuous"):
        await read_message(
            _reader(b"".join(frames)),
            AUTHENTICATOR,
            legacy_frame_bytes=65_536,
        )


@pytest.mark.asyncio
async def test_legacy_transport_cannot_bypass_sixteen_kib_boundary() -> None:
    with pytest.raises(ProtocolError, match="legacy IPC frame is invalid"):
        await read_message(
            _reader(b"x" * 20_000 + b"\n"),
            AUTHENTICATOR,
            legacy_frame_bytes=65_536,
        )
