#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import struct
import sys
from typing import BinaryIO


def read_exact(stream: BinaryIO, size: int) -> bytes | None:
    data = bytearray()
    while len(data) < size:
        chunk = stream.read(size - len(data))
        if not chunk:
            return None
        data.extend(chunk)
    return bytes(data)


def response(request: dict[str, object], sessions: set[str]) -> dict[str, object]:
    model_id = os.environ["AEGIS_MLX_MODEL_ID"]
    method = request["method"]
    content: str | None = None
    cache_reused = False
    verification: dict[str, object] | None = None
    if method == "status":
        content = "ready"
    elif method == "reset_conversation":
        sessions.discard(str(request["conversation_id"]))
        content = "reset"
    elif method == "generate":
        conversation_id = str(request["conversation_id"])
        cache_reused = conversation_id in sessions
        sessions.add(conversation_id)
        content = f"ok:{request['prompt']}"
    elif method == "verify_draft":
        tokens = request["draft_token_ids"]
        assert isinstance(tokens, list)
        verification = {
            "accepted_token_count": len(tokens) - 1,
            "correction_token_id": 42,
            "verified_token_count": len(tokens),
        }
    else:
        raise AssertionError("unexpected method")
    return {
        "protocol_version": "1.0",
        "request_id": request["request_id"],
        "success": True,
        "model_id": model_id,
        "content": content,
        "cache_reused": cache_reused,
        "verification": verification,
        "error_code": None,
    }


def write(payload: dict[str, object]) -> None:
    encoded = json.dumps(payload, separators=(",", ":")).encode()
    sys.stdout.buffer.write(struct.pack(">I", len(encoded)) + encoded)
    sys.stdout.buffer.flush()


def main() -> int:
    sessions: set[str] = set()
    if sys.argv[1:] == ["--status"]:
        write_payload = {
            "protocol_version": "1.0",
            "request_id": "00000000-0000-0000-0000-000000000000",
            "success": True,
            "model_id": os.environ["AEGIS_MLX_MODEL_ID"],
            "content": "ready",
            "cache_reused": False,
            "verification": None,
            "error_code": None,
        }
        sys.stdout.buffer.write(json.dumps(write_payload).encode())
        return 0
    while True:
        header = read_exact(sys.stdin.buffer, 4)
        if header is None:
            return 0
        length = struct.unpack(">I", header)[0]
        raw = read_exact(sys.stdin.buffer, length)
        if raw is None:
            return 76
        request = json.loads(raw)
        write(response(request, sessions))


if __name__ == "__main__":
    raise SystemExit(main())
