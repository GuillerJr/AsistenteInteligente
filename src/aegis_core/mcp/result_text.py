"""Turn a relayed MCP result into text a person can read, hear or receive in Telegram."""

from __future__ import annotations

import json
import re
from email.utils import parseaddr, parsedate_to_datetime
from typing import Any

_MONTHS = ("ene", "feb", "mar", "abr", "may", "jun", "jul", "ago", "sep", "oct", "nov", "dic")
# Servers append guidance meant for a model ("USAGE", "PAGINATION"). It is not
# part of the answer, so everything from the first such heading on is dropped.
_MODEL_GUIDANCE = re.compile(r"^\W*(?:USAGE|PAGINATION|NEXT STEPS?|TIP)\s*:", re.IGNORECASE)
_GMAIL_ENTRY = re.compile(r"^\s*\d+\.\s+Message ID:", re.MULTILINE)
_GMAIL_FIELD = re.compile(r"^\s*(Subject|From|Date):[ \t]*(.*)$", re.MULTILINE)


def readable_mcp_result(payload: dict[str, Any], *, server_label: str, tool_name: str) -> str:
    text = _result_text(payload)
    if text is None:
        rendered = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
        return f"Resultado de {server_label}: {rendered}"
    if payload.get("isError") is True:
        return f"{server_label} devolvió un error: {_without_model_guidance(text)}"
    if tool_name.endswith("__search_gmail_messages"):
        mail = _gmail_search(text)
        if mail is not None:
            return mail
    body = _without_model_guidance(text)
    return f"Resultado de {server_label}:\n{body}" if body else f"{server_label} no devolvió datos."


def _result_text(payload: dict[str, Any]) -> str | None:
    blocks = payload.get("content")
    texts = [
        block["text"].strip()
        for block in blocks if isinstance(block, dict)
        and block.get("type") == "text" and isinstance(block.get("text"), str)
    ] if isinstance(blocks, list) else []
    if any(texts):
        return "\n\n".join(text for text in texts if text)
    structured = payload.get("structuredContent")
    if isinstance(structured, dict) and isinstance(structured.get("result"), str):
        return structured["result"].strip()
    return None


def strip_model_guidance(output: str) -> str:
    """Drop server hints written for a model from a relayed MCP output.

    Hints such as USAGE or PAGINATION name upstream tools that Jarvis may not have
    admitted; a model that sees them can promise or request tools that do not exist.
    The envelope and every other field are kept exactly.
    """
    try:
        payload = json.loads(output)
    except (ValueError, RecursionError):
        return _without_model_guidance(output)
    if not isinstance(payload, dict) or not isinstance(payload.get("content"), list):
        return output
    content = [
        {**block, "text": _without_model_guidance(block["text"])}
        if isinstance(block, dict) and block.get("type") == "text"
        and isinstance(block.get("text"), str) else block
        for block in payload["content"]
    ]
    structured = payload.get("structuredContent")
    if isinstance(structured, dict) and isinstance(structured.get("result"), str):
        structured = {**structured, "result": _without_model_guidance(structured["result"])}
        payload = {**payload, "structuredContent": structured}
    return json.dumps({**payload, "content": content}, ensure_ascii=False,
                      separators=(",", ":"), sort_keys=True)


def _without_model_guidance(text: str) -> str:
    kept: list[str] = []
    for line in text.splitlines():
        if _MODEL_GUIDANCE.match(line):
            break
        kept.append(line.rstrip())
    return "\n".join(kept).strip()


def _gmail_search(text: str) -> str | None:
    if text.startswith("No messages found"):
        return "No encontré correos que coincidan con la búsqueda."
    starts = [match.start() for match in _GMAIL_ENTRY.finditer(text)]
    if not starts:
        return None
    lines: list[str] = []
    for index, start in enumerate(starts):
        end = starts[index + 1] if index + 1 < len(starts) else len(text)
        fields = {name: value.strip() for name, value in _GMAIL_FIELD.findall(text[start:end])}
        # Without include_headers the server lists only identifiers.
        subject = fields.get("Subject") or ("(sin asunto)" if fields else "Correo sin detalles")
        details = [part for part in (_sender(fields.get("From", "")),
                                     _date(fields.get("Date", ""))) if part]
        lines.append(f"{index + 1}. {subject}")
        if details:
            lines.append("   " + " · ".join(details))
    count = len(starts)
    heading = "Encontré 1 correo:" if count == 1 else f"Encontré {count} correos:"
    more = "\n\nHay más correos; pídeme la siguiente página si quieres verlos." \
        if re.search(r"next_page_token|page_token=", text) else ""
    return heading + "\n\n" + "\n".join(lines) + more


def _sender(value: str) -> str:
    name, address = parseaddr(value)
    return (name or address or value).strip().strip('"')


def _date(value: str) -> str:
    try:
        moment = parsedate_to_datetime(re.sub(r"\s*\([^)]*\)\s*$", "", value)).astimezone()
    except (TypeError, ValueError, IndexError):
        return value
    return f"{moment.day} {_MONTHS[moment.month - 1]}, {moment:%H:%M}"
