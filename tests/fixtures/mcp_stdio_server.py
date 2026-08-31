from __future__ import annotations

import json
import sys


def send(request_id: int, result: dict[str, object]) -> None:
    sys.stdout.write(
        json.dumps(
            {"jsonrpc": "2.0", "id": request_id, "result": result},
            separators=(",", ":"),
        )
        + "\n"
    )
    sys.stdout.flush()


for line in sys.stdin:
    message = json.loads(line)
    if "id" not in message:
        continue
    request_id = message["id"]
    method = message["method"]
    if method == "initialize":
        send(
            request_id,
            {
                "protocolVersion": "2025-11-25",
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "aegis-test-mcp", "version": "1.0.0"},
            },
        )
    elif method == "tools/list":
        send(
            request_id,
            {
                "tools": [
                    {
                        "name": "read.status",
                        "description": "Return one bounded status value.",
                        "inputSchema": {
                            "type": "object",
                            "properties": {
                                "scope": {"type": "string", "maxLength": 32}
                            },
                            "required": ["scope"],
                            "additionalProperties": False,
                        },
                    }
                ]
            },
        )
    elif method == "tools/call":
        arguments = message["params"]["arguments"]
        send(
            request_id,
            {
                "content": [{"type": "text", "text": f"status:{arguments['scope']}"}],
                "isError": False,
            },
        )
    else:
        sys.stdout.write(
            json.dumps(
                {
                    "jsonrpc": "2.0",
                    "id": request_id,
                    "error": {"code": -32601, "message": "not found"},
                },
                separators=(",", ":"),
            )
            + "\n"
        )
        sys.stdout.flush()
