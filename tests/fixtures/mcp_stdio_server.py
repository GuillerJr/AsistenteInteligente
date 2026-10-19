from __future__ import annotations

import json
import sys
import time

MODE = sys.argv[1] if len(sys.argv) > 1 else "normal"


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
        if MODE == "hang-start":
            time.sleep(60)
        send(
            request_id,
            {
                "protocolVersion": "2025-11-25",
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "aegis-test-mcp", "version": "1.0.0"},
            },
        )
    elif method == "tools/list":
        if MODE == "stdout-flood":
            sys.stdout.write("x" * 2_000_000)
            sys.stdout.flush()
            time.sleep(60)
            continue
        result = {
            "tools": [
                {
                    "name": "read.status",
                    "description": "Return one bounded status value.",
                    "inputSchema": {
                        "type": "object",
                        "properties": {"scope": {"type": "string", "maxLength": 32}},
                        "required": ["scope"],
                        "additionalProperties": False,
                    },
                }
            ]
        }
        tool = result["tools"][0]
        if MODE == "schema-drift":
            tool["inputSchema"]["properties"]["scope"]["maxLength"] = 64
        if MODE == "mutating":
            tool["annotations"] = {"readOnlyHint": False}
        if MODE == "missing":
            tool["name"] = "other"
        if MODE in {"duplicate", "extra"}:
            result["tools"].append(
                {**tool, "name": "write.danger" if MODE == "extra" else tool["name"]}
            )
        if MODE == "cursor-loop":
            result["tools"] = []
            result["nextCursor"] = "same"
        send(request_id, result)
    elif method == "tools/call":
        if MODE == "hang-call" or (
            MODE == "hang-slow-scope" and message["params"]["arguments"]["scope"] == "slow"
        ):
            time.sleep(60)
        if MODE == "catalogue-change":
            sys.stdout.write(
                json.dumps({"jsonrpc": "2.0", "method": "notifications/tools/list_changed"}) + "\n"
            )
            sys.stdout.flush()
            continue
        arguments = message["params"]["arguments"]
        send(
            request_id,
            {
                "content": [{"type": "text", "text": f"status:{arguments['scope']}"}],
                "isError": "false" if MODE == "bad-error-flag" else False,
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
