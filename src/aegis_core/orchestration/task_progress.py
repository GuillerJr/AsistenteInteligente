"""Evidence-based task continuity, independent of the inference provider."""

from __future__ import annotations

import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from pydantic import ValidationError

from aegis_core.contracts import ToolCall, ToolExecutionResult
from aegis_core.memory.task_checkpoint import (
    ResultReference,
    TaskCheckpoint,
    TaskStep,
    VerifiedTaskResult,
)
from aegis_core.privacy import sanitize_remote_reference
from aegis_core.request_intent import folded_request, google_request_tools, mask_quoted_text
from aegis_core.tools.verification import result_is_verified

CONTINUATIONS = frozenset({"si", "si por favor", "hazlo", "adelante", "yes", "yes please"})
_CANCEL = frozenset({"cancela", "cancelalo", "cancela lo anterior", "olvidalo", "dejalo",
                     "no lo hagas", "cancel", "stop"})
_ORDINALS = {"primero": 1, "primer": 1, "segundo": 2, "tercero": 3, "tercer": 3,
             "cuarto": 4, "quinto": 5, "sexto": 6, "septimo": 7, "octavo": 8,
             "noveno": 9, "decimo": 10}
_ORDINALS.update({word[:-1] + "a": number for word, number in tuple(_ORDINALS.items())
                  if word.endswith("o")})
_LIST_TO_KIND = {"search_gmail_messages": ("mail", "messages", "message_id"),
                 "search_drive_files": ("file", "files", "file_id"),
                 "list_drive_items": ("file", "items", "file_id"),
                 "get_events": ("event", "events", "event_id")}


def _clip(text: str, budget: int) -> str:
    return text.encode("utf-8")[:budget].decode("utf-8", errors="ignore")


def _payloads(value: Any, depth: int = 0) -> list[dict[str, Any]]:
    """Read only known MCP envelopes, never interpret prose as identifiers."""
    if depth > 4:
        return []
    if isinstance(value, str):
        try:
            return _payloads(json.loads(value), depth + 1)
        except (ValueError, TypeError):
            return []
    if not isinstance(value, dict):
        return []
    payloads = [value]
    for key in ("structuredContent", "data", "result"):
        if key in value:
            payloads.extend(_payloads(value[key], depth + 1))
    for part in value.get("content", []) if isinstance(value.get("content"), list) else []:
        if isinstance(part, dict) and part.get("type") == "text":
            payloads.extend(_payloads(part.get("text"), depth + 1))
    return payloads


def _peer_text(value: Any) -> str:
    if isinstance(value, str):
        try:
            return _peer_text(json.loads(value))
        except (ValueError, TypeError):
            return value
    if not isinstance(value, dict):
        return ""
    parts = value.get("content", [])
    if not isinstance(parts, list):
        return ""
    return "\n".join(part["text"] for part in parts if isinstance(part, dict)
                     and part.get("type") == "text" and isinstance(part.get("text"), str))


def _pinned_text_references(remote: str, text: str) -> list[ResultReference]:
    """Recognize the pinned workspace-mcp 1.26.1 list formats, fail closed on ambiguity."""
    found: list[ResultReference] = []
    if remote == "search_gmail_messages":
        header = re.match(r"Found ([0-9]+) messages matching [^\n]*:\n\n📧 MESSAGES:\n", text)
        if header is None:
            return []
        body = text[header.end():].split("\n💡 USAGE:", 1)[0]
        records = list(re.finditer(r"^  ([0-9]+)\. Message ID: ([a-f0-9]+)$", body, re.M))
        if len(records) != int(header[1]):
            return []
        for i, record in enumerate(records):
            position = int(record[1])
            if position != i + 1:
                return []
            block = body[record.end():records[i + 1].start() if i + 1 < len(records) else None]
            if not re.search(r"^     Web Link: https://mail\.google\.com/mail/u/[0-9]+/#all/"
                             + re.escape(record[2]) + r"$", block, re.M):
                return []
            subject = re.search(r"^     Subject: ([^\n]*)$", block, re.M)
            if position <= 20:
                found.append(ResultReference(kind="mail", identifier=record[2], position=position,
                                             label=_clip(subject[1], 180) if subject else ""))
    elif remote == "search_drive_files":
        header = re.match(r"Found ([0-9]+) files for [^\n]*:\n", text)
        if header is None:
            return []
        records = list(re.finditer(r'^- Name: "([^\n]*)" \(ID: ([A-Za-z0-9_-]+), Type: ',
                                  text[header.end():], re.M))
        if len(records) != int(header[1]):
            return []
        found = [ResultReference(kind="file", identifier=r[2], label=_clip(r[1], 180), position=i)
                 for i, r in enumerate(records[:20], 1)]
    # Calendar's prose lacks reliable calendar provenance; use structured results only.
    if len({ref.identifier for ref in found}) != len(found):
        return []
    return found


def verified_task_result(result: ToolExecutionResult) -> VerifiedTaskResult | None:
    if (not result_is_verified(result) or result.metadata.get("verified") is not True
            or result.metadata.get("integration") != "google-workspace"
            or not result.tool_name.startswith("mcp_")):
        return None
    remote = result.tool_name.rsplit("__", 1)[-1]
    clean = sanitize_remote_reference({"result": result.output})["result"]
    text = clean if isinstance(clean, str) else json.dumps(clean, ensure_ascii=False)
    arguments = result.metadata.get("operation_arguments_json", "")
    arguments = sanitize_remote_reference(arguments) if isinstance(arguments, str) else ""
    references: list[ResultReference] = []
    if remote in _LIST_TO_KIND:
        kind, list_key, id_key = _LIST_TO_KIND[remote]
        for payload in _payloads(text):
            items = payload.get(list_key)
            if not isinstance(items, list):
                continue
            for position, item in enumerate(items[:20], 1):
                if not isinstance(item, dict):
                    continue
                identifier = item.get(id_key, item.get("id"))
                if not isinstance(identifier, str):
                    continue
                label = item.get("subject", item.get("name", item.get("summary", "")))
                calendar = item.get("calendar_id", payload.get("calendar_id")) \
                    if kind == "event" else None
                if kind == "event" and not isinstance(calendar, str):
                    continue
                try:
                    references.append(ResultReference(
                        kind=kind, identifier=identifier,
                        label=_clip(label, 180) if isinstance(label, str) else "",
                        position=position,
                        container_id=calendar,
                    ))
                except ValidationError:
                    continue
            # Preserve the tool's ordering, including holes, and never merge lists.
            break
        if not references:
            references = _pinned_text_references(remote, _peer_text(text))
        if len({ref.identifier for ref in references}) != len(references):
            references = []
    return VerifiedTaskResult(
        tool_name=result.tool_name, call_id=result.call_id, observed_at=datetime.now(UTC),
        excerpt=_clip(text, 3_072), truncated=len(text.encode("utf-8")) > 3_072,
        arguments_json=_clip(arguments, 3_072),
        arguments_truncated=len(arguments.encode("utf-8")) > 3_072,
        references=tuple(references),
    )


def requested_task_steps(request: str) -> tuple[TaskStep, ...]:
    """A small deterministic workflow for explicit Google clauses, not guessed intentions."""
    separators = list(re.finditer(
        r"\s+(?:y luego|y despu[eé]s|luego|despu[eé]s)\s+|;\s*|"
        r"\s+y\s+(?=(?:prepara|redacta|crea|programa|busca|lee|revisa)\b)",
        mask_quoted_text(request), flags=re.I,
    ))
    starts = [0, *(separator.end() for separator in separators)]
    ends = [*(separator.start() for separator in separators), len(request)]
    clauses = [request[start:end] for start, end in zip(starts, ends, strict=True)]
    if len(clauses) > 4:
        return ()
    steps: list[TaskStep] = []
    domain = ""
    for clause in clauses[:4]:
        clause = clause.strip()
        names = google_request_tools(clause)
        if not names and domain:
            clause = f"{clause} {domain}"
            names = google_request_tools(clause)
        if not names:
            # An unknown clause prevents claiming the whole compound goal is verified.
            if len(clauses) > 1:
                return ()
            continue
        domains = sum(bool(names & group) for group in (
            {"search_gmail_messages", "get_gmail_message_content", "draft_gmail_message"},
            {"get_events", "list_calendars", "manage_event"},
            {"search_drive_files", "get_drive_file_content", "list_drive_items",
             "create_drive_file", "create_drive_folder", "update_drive_file"},
        ))
        if domains > 1:
            return ()
        if "search_gmail_messages" in names:
            domain = "correo"
            names = frozenset({"get_gmail_message_content" if re.match(
                r"^(?:lee|leer)\b", folded_request(clause))
                               else "search_gmail_messages"})
        elif "search_drive_files" in names:
            domain = "archivo en Drive"
            names = frozenset({"get_drive_file_content" if re.match(
                r"^(?:lee|leer)\b", folded_request(clause))
                               else "search_drive_files"})
        elif "get_events" in names:
            domain = "evento"
            names = frozenset({"get_events"})
        elif "draft_gmail_message" in names:
            domain = "correo"
        elif "manage_event" in names:
            domain = "evento"
        steps.append(TaskStep(instruction=_clip(clause, 512), tool_suffixes=tuple(sorted(names))))
    return tuple(steps)


def advance_checkpoint(
    previous: TaskCheckpoint | None, *, request: str, response: str,
    sequence: int, tool_results: Sequence[ToolExecutionResult] = (),
) -> TaskCheckpoint:
    folded = folded_request(request)
    if folded in _CANCEL:
        return TaskCheckpoint(active_intent=_clip(request, 2_048), status="cancelled",
                              sequence=sequence)
    resume = (folded in CONTINUATIONS and previous is not None
              and previous.status == "needs_action")
    intent = previous.active_intent if resume else _clip(request, 2_048)
    evidence = tuple(e for result in tool_results
                     if (e := verified_task_result(result)) is not None)
    old = previous.results if previous is not None else ()
    # A new list supersedes the previous list in that domain. Detail reads keep it.
    replaced = {_LIST_TO_KIND[e.tool_name.rsplit("__", 1)[-1]][0]
                for e in evidence if e.tool_name.rsplit("__", 1)[-1] in _LIST_TO_KIND}
    old = tuple(e for e in old if not any(r.kind in replaced for r in e.references))
    results = (*old, *evidence)[-3:]
    steps = previous.steps if resume else requested_task_steps(intent)
    # Match only this task's new evidence. A previous task's successful write is not reusable.
    observed = [e.tool_name.rsplit("__", 1)[-1] for e in evidence]
    verified_steps: list[TaskStep] = []
    for step in steps:
        match = next((suffix for suffix in observed if suffix in step.tool_suffixes), None)
        if not step.verified and match is not None:
            observed.remove(match)
            step = step.model_copy(update={"verified": True})
        verified_steps.append(step)
    steps = tuple(verified_steps)
    pending = next((step for step in steps if not step.verified), None)
    needs_action = bool(google_request_tools(intent)) and not evidence and (
        "?" in response and re.search(r"\b(?:quieres|deseas|would you like|shall i)\b",
                                      folded_request(response)) is not None
    )
    return TaskCheckpoint(
        active_intent=intent, sequence=sequence,
        status="needs_action" if needs_action or (evidence and pending) else
        "verified" if evidence and steps and pending is None else "answered",
        next_action=pending.instruction if (needs_action or evidence) and pending else "",
        results=results, steps=steps,
    )


@dataclass(frozen=True, slots=True)
class TaskResolution:
    scope_text: str | None = None
    tool_suffix: str | None = None
    identifier: str | None = None
    clarification: str | None = None
    container_identifier: str | None = None


def resolve_task_reference(text: str, checkpoint: TaskCheckpoint | None) -> TaskResolution:
    command = folded_request(text)
    command = re.sub(r"^jarvis[ ,:]+", "", command)
    if command in _CANCEL:
        return TaskResolution()
    if command in CONTINUATIONS:
        if checkpoint is None or checkpoint.status == "cancelled":
            return TaskResolution()
        if not timedelta(0) <= datetime.now(UTC) - checkpoint.updated_at <= timedelta(minutes=5):
            return TaskResolution()
        # Only recover read schemas, never elevate a prior offer into a write grant.
        tools = google_request_tools(checkpoint.next_action or checkpoint.active_intent)
        pending = next((step for step in checkpoint.steps if not step.verified), None)
        explicit_workflow = (len(checkpoint.steps) > 1 and pending is not None
                             and any(step.verified for step in checkpoint.steps))
        if checkpoint.status == "needs_action" and tools and (explicit_workflow or not tools & {
            "draft_gmail_message", "manage_event", "create_drive_file", "create_drive_folder",
            "update_drive_file",
        }):
            return TaskResolution(scope_text=checkpoint.next_action or checkpoint.active_intent,
                                  tool_suffix=pending.tool_suffixes[0]
                                  if pending and len(pending.tool_suffixes) == 1 else None)
        return TaskResolution()
    # Explicit read commands only; quoted examples, negations and corrections are not actions.
    if (not re.match(r"^(?:(?:puedes|podrias) )?(?:abre|abrir|lee|leer|muestra|mostrar)\b", command)
            or any(mark in text for mark in ('"', "'", "`", "«", "“"))):
        return TaskResolution()
    ordinal = re.search(r"\b(" + "|".join(_ORDINALS) + r"|[1-9][0-9]?)\b", command)
    if ordinal is None:
        return TaskResolution()
    if not re.fullmatch(r"(?:(?:puedes|podrias) )?(?:abre|abrir|lee|leer|muestra|mostrar) "
                        r"(?:(?:el|la|numero) )?" + re.escape(ordinal[0])
                        + r"(?: (?:correo|mensaje|email|archivo|documento|evento|reunion))?"
                        r"(?: por favor)?", command):
        return TaskResolution()
    number = _ORDINALS.get(ordinal[0], int(ordinal[0]) if ordinal[0].isdigit() else 0)
    if checkpoint is None or checkpoint.status == "cancelled":
        return TaskResolution(clarification="No tengo una lista verificada para ese elemento. "
                              "¿Qué correo, archivo o evento quieres consultar?")
    requested_kind = ("mail" if re.search(r"\b(?:correo|mensaje|email)\b", command) else
                      "file" if re.search(r"\b(?:archivo|documento)\b", command) else
                      "event" if re.search(r"\b(?:evento|reunion)\b", command) else None)
    lists = [e for e in checkpoint.results if e.references
             and (requested_kind is None or e.references[0].kind == requested_kind)]
    if len(lists) != 1:
        return TaskResolution(clarification="¿A qué correo, archivo o evento te refieres?")
    evidence = lists[0]
    if not timedelta(0) <= datetime.now(UTC) - evidence.observed_at <= timedelta(minutes=30):
        return TaskResolution(clarification="La lista anterior ya es antigua. "
                              "¿Qué elemento quieres consultar?")
    refs = [r for r in evidence.references if r.position == number]
    if len(refs) != 1:
        return TaskResolution(clarification="No tengo un identificador verificado "
                              "para ese elemento. ¿Cuál quieres consultar?")
    ref = refs[0]
    suffix, noun = {"mail": ("get_gmail_message_content", "correo"),
                    "file": ("get_drive_file_content", "archivo en Drive"),
                    "event": ("get_events", "evento")}[ref.kind]
    return TaskResolution(scope_text=f"Lee el {noun} con ID {ref.identifier}",
                          tool_suffix=suffix, identifier=ref.identifier,
                          container_identifier=ref.container_id)


def task_reference_context(checkpoint: TaskCheckpoint | None) -> dict[str, Any]:
    if checkpoint is None:
        return {}
    # Fixed maximums reserve space for the current request and offered schemas.
    payload = checkpoint.model_dump(mode="json")
    payload["active_intent"] = _clip(payload["active_intent"], 1_024)
    for evidence in payload["results"]:
        if len(evidence["excerpt"].encode("utf-8")) > 768:
            evidence["excerpt"] = _clip(evidence["excerpt"], 768)
            evidence["truncated"] = True
        for reference in evidence["references"]:
            reference["label"] = _clip(reference["label"], 64)
    while len(json.dumps(payload, ensure_ascii=False).encode("utf-8")) > 8_192:
        # Older evidence goes first; never shorten an identifier or corrupt a JSON record.
        if not payload["results"]:
            break
        payload["results"].pop(0)
    return {"task_checkpoint": payload}


def reject_duplicate_calls(calls: Sequence[ToolCall]) -> None:
    seen: set[str] = set()
    ids: set[str] = set()
    for call in calls:
        fingerprint = json.dumps([call.tool_name, call.arguments], sort_keys=True,
                                 ensure_ascii=False, separators=(",", ":"))
        if fingerprint in seen or call.call_id in ids:
            raise ValueError("duplicate tool proposal; no execution permitted")
        seen.add(fingerprint)
        ids.add(call.call_id)


def reject_repeated_completed_step(
    request: str, checkpoint: TaskCheckpoint | None, calls: Sequence[ToolCall],
) -> None:
    if (checkpoint is None or checkpoint.status != "needs_action"
            or folded_request(request) not in CONTINUATIONS
            or not any(step.verified for step in checkpoint.steps)):
        return
    for evidence in checkpoint.results:
        if evidence.arguments_truncated or not evidence.arguments_json:
            continue
        try:
            arguments = json.loads(evidence.arguments_json)
        except ValueError:
            continue
        if any(call.tool_name == evidence.tool_name and call.arguments == arguments
               for call in calls):
            raise ValueError("completed step repeated without task progress")


def repeated_followup_offer(messages: Sequence[Mapping[str, Any]], response: str) -> bool:
    users = [m["content"] for m in messages if m.get("role") == "user"
             and isinstance(m.get("content"), str)]
    assistants = [m["content"] for m in messages if m.get("role") == "assistant"
                  and isinstance(m.get("content"), str)]
    if not users or not assistants or folded_request(users[-1]) not in CONTINUATIONS:
        return False
    prior = folded_request(assistants[-1])
    current = folded_request(response)
    return ("?" in response and prior == current) or bool(
        "?" in response and re.search(r"\b(?:quieres|deseas)\b", current)
        and re.search(r"\b(?:quieres|deseas)\b", prior)
        and re.search(r"\b(?:busque|revise|consulte|haga|prepare)\b", current)
    )
