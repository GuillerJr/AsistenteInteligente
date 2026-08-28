from __future__ import annotations

import asyncio
import struct
from dataclasses import dataclass
from enum import IntEnum

from aegis_core.ipc.protocol import IpcAuthenticator, ProtocolError

MAGIC = b"\xae\x15"
HEADER = struct.Struct(">2sBHHB")
HEADER_BYTES = HEADER.size
AUTH_TAG_BYTES = 32
MAX_CHUNK_BYTES = 16_384
DEFAULT_MAX_MESSAGE_BYTES = 1_048_576


class StreamFrameType(IntEnum):
    START = 0x01
    DATA = 0x02
    END = 0x03


@dataclass(frozen=True, slots=True)
class TransportMetrics:
    framed: bool
    frame_count: int
    payload_bytes: int


def encode_stream(
    payload: bytes,
    authenticator: IpcAuthenticator,
    *,
    max_message_bytes: int = DEFAULT_MAX_MESSAGE_BYTES,
) -> tuple[bytes, ...]:
    if not payload:
        raise ProtocolError("IPC stream payload is empty")
    if len(payload) > max_message_bytes:
        raise ProtocolError("IPC message exceeds message limit")

    chunks = [
        payload[offset : offset + MAX_CHUNK_BYTES]
        for offset in range(0, len(payload), MAX_CHUNK_BYTES)
    ]
    # START and END are both mandatory. An empty END is unambiguous for a
    # one-chunk transaction and keeps the state machine deliberately small.
    if len(chunks) == 1:
        chunks.append(b"")
    if len(chunks) > 65_536:
        raise ProtocolError("IPC stream sequence space exhausted")

    encoded: list[bytes] = []
    for sequence, chunk in enumerate(chunks):
        if sequence == 0:
            frame_type = StreamFrameType.START
        elif sequence == len(chunks) - 1:
            frame_type = StreamFrameType.END
        else:
            frame_type = StreamFrameType.DATA
        header = HEADER.pack(MAGIC, frame_type, sequence, len(chunk), 0)
        tag = authenticator.authenticate_frame(header + chunk)
        encoded.append(header + chunk + tag)
    return tuple(encoded)


async def read_message(
    reader: asyncio.StreamReader,
    authenticator: IpcAuthenticator,
    *,
    legacy_frame_bytes: int,
    max_message_bytes: int = DEFAULT_MAX_MESSAGE_BYTES,
) -> tuple[bytes, TransportMetrics]:
    if legacy_frame_bytes < 1 or max_message_bytes < 1:
        raise ValueError("IPC framing limits must be positive")
    prefix = await reader.readexactly(len(MAGIC))
    if prefix != MAGIC:
        return await _read_legacy(
            reader,
            prefix,
            legacy_frame_bytes=legacy_frame_bytes,
        )

    expected_sequence = 0
    accumulated = bytearray()
    frame_count = 0
    while True:
        suffix = await reader.readexactly(HEADER_BYTES - len(MAGIC))
        header = prefix + suffix
        magic, raw_type, sequence, payload_length, reserved = HEADER.unpack(header)
        if magic != MAGIC or reserved != 0 or payload_length > MAX_CHUNK_BYTES:
            raise ProtocolError("IPC stream header is invalid")
        try:
            frame_type = StreamFrameType(raw_type)
        except ValueError as error:
            raise ProtocolError("IPC stream frame type is invalid") from error
        if sequence != expected_sequence:
            raise ProtocolError("IPC stream sequence is discontinuous")
        if expected_sequence == 0 and frame_type is not StreamFrameType.START:
            raise ProtocolError("IPC stream does not begin with START")
        if expected_sequence > 0 and frame_type is StreamFrameType.START:
            raise ProtocolError("IPC stream contains a duplicate START")

        payload = await reader.readexactly(payload_length)
        tag = await reader.readexactly(AUTH_TAG_BYTES)
        if not authenticator.verify_frame(header + payload, tag):
            raise ProtocolError("IPC stream authentication failed")
        if len(accumulated) + payload_length > max_message_bytes:
            raise ProtocolError("IPC message exceeds message limit")

        accumulated.extend(payload)
        frame_count += 1
        if frame_type is StreamFrameType.END:
            if not accumulated:
                raise ProtocolError("IPC stream payload is empty")
            return bytes(accumulated), TransportMetrics(
                framed=True,
                frame_count=frame_count,
                payload_bytes=len(accumulated),
            )
        if frame_type is not (
            StreamFrameType.START if expected_sequence == 0 else StreamFrameType.DATA
        ):
            raise ProtocolError("IPC stream terminated without END")
        if expected_sequence == 65_535:
            raise ProtocolError("IPC stream sequence space exhausted")
        expected_sequence += 1
        prefix = await reader.readexactly(len(MAGIC))


async def _read_legacy(
    reader: asyncio.StreamReader,
    prefix: bytes,
    *,
    legacy_frame_bytes: int,
) -> tuple[bytes, TransportMetrics]:
    try:
        remainder = await reader.readline()
    except ValueError as error:
        raise ProtocolError("legacy IPC frame exceeds frame limit") from error
    frame = prefix + remainder
    if not frame.endswith(b"\n") or len(frame) > min(
        legacy_frame_bytes,
        MAX_CHUNK_BYTES,
    ):
        raise ProtocolError("legacy IPC frame is invalid")
    payload = frame[:-1]
    if not payload:
        raise ProtocolError("legacy IPC payload is empty")
    return payload, TransportMetrics(
        framed=False,
        frame_count=1,
        payload_bytes=len(payload),
    )


def encode_message(
    payload: bytes,
    authenticator: IpcAuthenticator,
    *,
    legacy_frame_bytes: int,
    max_message_bytes: int = DEFAULT_MAX_MESSAGE_BYTES,
) -> tuple[bytes, TransportMetrics]:
    if not payload:
        raise ProtocolError("IPC payload is empty")
    if len(payload) > max_message_bytes:
        raise ProtocolError("IPC message exceeds message limit")
    if len(payload) + 1 <= min(legacy_frame_bytes, MAX_CHUNK_BYTES):
        frame = payload + b"\n"
        return frame, TransportMetrics(False, 1, len(payload))
    frames = encode_stream(
        payload,
        authenticator,
        max_message_bytes=max_message_bytes,
    )
    return b"".join(frames), TransportMetrics(True, len(frames), len(payload))
