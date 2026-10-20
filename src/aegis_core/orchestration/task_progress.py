"""Evidence-based task continuity, independent of the inference provider."""

from __future__ import annotations

import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

from pydantic import ValidationError

from aegis_core.contracts import ToolCall, ToolExecutionResult
from aegis_core.mcp.collections import (
    COLLECTIONS,
    CollectionContract,
    collection_words,
    contract_for_list_tool,
    contract_for_word,
)
from aegis_core.mcp.result_text import strip_model_guidance
from aegis_core.memory.task_checkpoint import (
    ResultReference,
    TaskCheckpoint,
    TaskStep,
    VerifiedTaskResult,
)
from aegis_core.privacy import sanitize_remote_reference
from aegis_core.request_intent import folded_request, google_request_tools, mask_quoted_text
from aegis_core.tools.verification import result_is_verified

CONTINUATIONS = frozenset({
    "si", "si por favor", "hazlo", "adelante", "yes", "yes please", "continua",
    "continua por favor", "sigue", "prosigue", "retoma", "resume", "continue",
})
_CANCEL = frozenset({"cancela", "cancelalo", "cancela lo anterior", "olvidalo", "dejalo",
                     "no lo hagas", "cancel", "stop"})
_ORDINALS = {"primero": 1, "primer": 1, "segundo": 2, "tercero": 3, "tercer": 3,
             "cuarto": 4, "quinto": 5, "sexto": 6, "septimo": 7, "octavo": 8,
             "noveno": 9, "decimo": 10}
_ORDINALS.update({word[:-1] + "a": number for word, number in tuple(_ORDINALS.items())
                  if word.endswith("o")})


def _clip(text: str, budget: int) -> str:
    return text.encode("utf-8")[:budget].decode("utf-8", errors="ignore")


def _payloads(value: Any, depth: int = 0) -> list[dict[str, Any]]:
    """Read only known MCP envelopes, never interpret prose as identifiers."""
    if depth > 4:
        return []
    if isinstance(value, str):
        try:
            return _payloads(json.loads(value), depth + 1)
        except (ValueError, TypeError, RecursionError):
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
        except (ValueError, TypeError, RecursionError):
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
    contract = contract_for_list_tool(remote)
    if contract is not None:
        kind = contract.kind
        source = contract.list_tools[remote]
        list_key, id_key = source.list_key, source.id_key
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
                container = contract.container_field
                calendar = item.get(container, payload.get(container)) if container else None
                if container and not isinstance(calendar, str):
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
        excerpt=_clip(guided := strip_model_guidance(text), 3_072),
        truncated=len(guided.encode("utf-8")) > 3_072,
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
            {"search_gmail_messages", "get_gmail_message_content",
             "get_gmail_messages_content_batch", "draft_gmail_message"},
            {"get_events", "list_calendars", "manage_event"},
            {"search_drive_files", "get_drive_file_content", "list_drive_items",
             "create_drive_file", "create_drive_folder", "update_drive_file"},
        ))
        if domains > 1:
            return ()
        if "search_gmail_messages" in names:
            domain = "correo"
            folded = folded_request(clause)
            names = frozenset({"get_gmail_messages_content_batch"
                               if folded.startswith("lee los correos con ids ")
                               else "get_gmail_message_content" if re.match(
                                   r"^(?:lee|leer)\b", folded)
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
    request_id: UUID | None = None,
) -> TaskCheckpoint:
    folded = folded_request(request)
    if folded in _CANCEL:
        return TaskCheckpoint(active_intent=_clip(request, 2_048), status="cancelled",
                              sequence=sequence)
    resume = (folded in CONTINUATIONS and previous is not None
              and previous.status == "needs_action")
    intent = previous.active_intent if resume else _clip(request, 2_048)
    evidence = tuple(e.model_copy(update={"request_id": request_id}) for result in tool_results
                     if (e := verified_task_result(result)) is not None)
    old = previous.results if previous is not None else ()
    # A new list supersedes the previous list in that domain. Detail reads keep it.
    replaced = {contract.kind for e in evidence
                if (contract := contract_for_list_tool(e.tool_name.rsplit("__", 1)[-1]))}
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
    identifiers: tuple[str, ...] = ()


_QUOTE_MARKS = ('"', "'", "`", "«", "“")
_KIND_WORDS = "|".join(sorted(collection_words(), key=len, reverse=True))
# "Detállame cada correo", "lee todos", "muéstrame el contenido de esos archivos".
_EVERY_ITEM = re.compile(
    r"(?:(?:puedes|podrias|me puedes|me podrias) )?"
    r"(?:detalla(?:me)?|detallar|lee(?:me)?|leer|abre(?:me)?|abrir|muestra(?:me)?|mostrar(?:me)?|"
    r"resume(?:me)?|resumir|dame|dime|explica(?:me)?)"
    r"(?: (?:el contenido|los contenidos|los detalles|el detalle|que dice|que dicen)(?: de)?)?"
    r" (?:cada uno|cada una|cada (?P<each>" + _KIND_WORDS + r")|todos|todas|todos ellos|"
    r"tod[oa]s l[oa]s (?P<all>" + _KIND_WORDS + r")|es[oa]s (?P<those>" + _KIND_WORDS + r")|"
    r"l[oa]s [0-9]{1,2} (?P<count>" + _KIND_WORDS + r"))"
    r"(?: (?:de la lista|anteriores|de arriba))?(?: por favor)?"
)


def fresh_list(
    checkpoint: TaskCheckpoint | None, contract: CollectionContract | None,
) -> tuple[VerifiedTaskResult | None, TaskResolution | None]:
    """The single fresh verified list a follow-up can refer to, or a clarification."""
    noun = contract.plural if contract else "elementos"
    if checkpoint is None or checkpoint.status == "cancelled":
        return None, TaskResolution(clarification=f"No tengo una lista verificada de {noun}. "
                                    "¿Quieres que la busque primero?")
    lists = [e for e in checkpoint.results if e.references
             and (contract is None or e.references[0].kind == contract.kind)
             and e.references[0].kind in COLLECTIONS]
    if len(lists) != 1:
        return None, TaskResolution(clarification="¿A qué lista te refieres?")
    evidence = lists[0]
    if not timedelta(0) <= datetime.now(UTC) - evidence.observed_at <= timedelta(minutes=30):
        return None, TaskResolution(clarification="La lista anterior ya es antigua. "
                                    "¿Quieres que la busque de nuevo?")
    return evidence, None


def list_clarification(evidence: VerifiedTaskResult) -> TaskResolution:
    """Ask which items to read, showing the verified list, instead of guessing."""
    contract = COLLECTIONS[evidence.references[0].kind]
    lines = [f"{ref.position}. {ref.label or 'Sin título'}"
             for ref in sorted(evidence.references, key=lambda ref: ref.position)]
    every = " o «todos»" if contract.batch_tool else ""
    return TaskResolution(clarification=f"¿Qué {contract.plural} quieres que lea?\n"
                          + "\n".join(lines) + f"\n\nDime los números{every}.")


def resolve_items(evidence: VerifiedTaskResult, positions: Sequence[int]) -> TaskResolution:
    """Bind list positions to verified identifiers; ids never come from free text."""
    contract = COLLECTIONS[evidence.references[0].kind]
    by_position = {ref.position: ref for ref in evidence.references}
    wanted = list(dict.fromkeys(positions))
    if not wanted or any(position not in by_position for position in wanted):
        return TaskResolution(clarification="No tengo un identificador verificado "
                              f"para ese {contract.noun}. ¿Cuál quieres consultar?")
    refs = [by_position[position] for position in wanted]
    if len(refs) == 1:
        ref = refs[0]
        return TaskResolution(scope_text=f"Lee el {contract.noun} con ID {ref.identifier}",
                              tool_suffix=contract.detail_tool, identifier=ref.identifier,
                              container_identifier=ref.container_id)
    if contract.batch_tool is None or len(refs) > contract.batch_max:
        return TaskResolution(clarification=f"Puedo leer un {contract.noun} cada vez. "
                              "¿Cuál quieres primero?")
    identifiers = tuple(ref.identifier for ref in refs)
    return TaskResolution(
        scope_text=f"Lee los {contract.plural} con IDs " + ", ".join(identifiers),
        tool_suffix=contract.batch_tool, identifiers=identifiers,
    )


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
    every = _EVERY_ITEM.fullmatch(command)
    if every and not any(mark in text for mark in _QUOTE_MARKS):
        word = next((value for value in every.groupdict().values() if value), None)
        evidence, clarification = fresh_list(
            checkpoint, contract_for_word(word) if word else None,
        )
        if clarification is not None or evidence is None:
            return clarification or TaskResolution()
        return resolve_items(evidence, sorted(ref.position for ref in evidence.references))
    # Explicit read commands only; quoted examples, negations and corrections are not actions.
    if (not re.match(r"^(?:(?:puedes|podrias) )?(?:abre|abrir|lee|leer|muestra|mostrar)\b", command)
            or any(mark in text for mark in _QUOTE_MARKS)):
        return TaskResolution()
    ordinal = re.search(r"\b(" + "|".join(_ORDINALS) + r"|[1-9][0-9]?)\b", command)
    if ordinal is None:
        return TaskResolution()
    if not re.fullmatch(r"(?:(?:puedes|podrias) )?(?:abre|abrir|lee|leer|muestra|mostrar) "
                        r"(?:(?:el|la|numero) )?" + re.escape(ordinal[0])
                        + r"(?: (?:" + _KIND_WORDS + r"))?"
                        r"(?: por favor)?", command):
        return TaskResolution()
    number = _ORDINALS.get(ordinal[0], int(ordinal[0]) if ordinal[0].isdigit() else 0)
    word = next((w for w in command.split() if contract_for_word(w)), None)
    if checkpoint is None or checkpoint.status == "cancelled":
        return TaskResolution(clarification="No tengo una lista verificada para ese elemento. "
                              "¿Qué correo, archivo o evento quieres consultar?")
    evidence, clarification = fresh_list(checkpoint, contract_for_word(word) if word else None)
    if clarification is not None or evidence is None:
        return clarification or TaskResolution()
    return resolve_items(evidence, (number,))


def resolve_quoted_reference(text: str, reference: dict) -> TaskResolution:
    """A quoted Telegram message wins over the most recent unrelated task list."""
    resources = reference.get("resources")
    if not isinstance(resources, list) or not resources:
        return TaskResolution()
    try:
        refs = tuple(ResultReference.model_validate(value) for value in resources[:10])
        created = datetime.fromtimestamp(float(reference["created"]), UTC)
    except (ValidationError, KeyError, ValueError, TypeError, OverflowError):
        return TaskResolution()
    checkpoint = TaskCheckpoint(
        active_intent="Mensaje citado por el usuario", status="verified", sequence=1,
        results=(VerifiedTaskResult(tool_name="telegram_message_reference", call_id="quote",
                                    observed_at=created, excerpt="", references=refs),),
    )
    if folded_request(mask_quoted_text(text)) in CONTINUATIONS:
        return TaskResolution()
    return resolve_task_reference(text, checkpoint)


def task_reference_context(checkpoint: TaskCheckpoint | None) -> dict[str, Any]:
    if checkpoint is None or checkpoint.status == "cancelled":
        return {}
    # Ordinary answered dialogue has no operational evidence to carry forward.
    # Its text already belongs to conversation history; presenting it again as
    # an active task can distract constrained tool planning for a new request.
    if checkpoint.status == "answered" and not checkpoint.results and not checkpoint.steps:
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
