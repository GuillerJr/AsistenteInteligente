"""Read-only retrieval across the owner's encrypted chats, never task activation."""

from __future__ import annotations

import json
import re
import time
from collections import deque
from uuid import UUID

from aegis_core.memory.contracts import ConversationRole
from aegis_core.orchestration.task_progress import CONTINUATIONS
from aegis_core.privacy import redact_secrets_for_remote, sanitize_remote_reference
from aegis_core.request_intent import folded_request

_STOP = frozenset(
    """
a al algo algun alguna algunos algunas antes aqui asi ahora chat chats como con cual cuales
cuando de del desde despues dime donde el ella en era es esa ese eso esta estas este esto
estos fue ha hacer hasta hay hola jarvis la las le lo los me mi mis mucho muy no nos o otra
otro para pero por porque puedo puedes que quien quiero recuerda recuerdas recordar respuesta
responde se si sin sobre son su sus te tengo tiene tienes todo toda todos todas tu tus un una
uno unas unos ver voy ya y hemos hablamos hablado hicimos mac telegram conversacion conversaciones
the a an and are as at be can do for from how i in is it me my of on or our that this to was
we what when where which with you your
""".split()
)
_ALIASES = {
    "correos": "correo",
    "emails": "correo",
    "email": "correo",
    "gmail": "correo",
    "energia": "ahorro",
    "consumo": "ahorro",
    "bateria": "ahorro",
    "restricciones": "restriccion",
    "restriccion": "restriccion",
    "proyectos": "proyecto",
    "decisiones": "decision",
}


def terms(text: str) -> frozenset[str]:
    return frozenset(
        _ALIASES.get(word, word)
        for word in re.findall(r"[a-z0-9]+", folded_request(text))
        if len(word) > 2 and word not in _STOP
    )


def retrieval_query(text: str, history) -> str:
    # Resolve short topical follow-ups against this Telegram thread, not a random
    # other chat. A plain yes/continue never switches the active task implicitly.
    if folded_request(text) in CONTINUATIONS or not terms(text):
        return text
    if len(terms(text)) <= 3:
        previous = [t.content for t in history if t.role == ConversationRole.USER][-2:]
        return "\n".join([text[:2_048], *[t[:1_024] for t in previous]])[:4_096]
    return text[:4_096]


def _clip(text: str, budget: int) -> str:
    return text.encode("utf-8")[:budget].decode("utf-8", errors="ignore")


def _excerpt(text: str, query_terms: frozenset[str], budget: int = 800) -> str:
    # Center long sources on a match instead of silently losing the relevant tail.
    clean = redact_secrets_for_remote(text).text
    # Redact before clipping so splitting a credential cannot evade detection.
    matches = [m.start() for m in re.finditer(r"\w+", clean) if terms(m.group()) & query_terms]
    start = max(0, matches[0] - min(120, budget // 8)) if matches else 0
    return _clip(clean[start:], budget)


def _summary(record) -> dict:
    for entry in reversed(record.context_checkpoints.get("entries", [])):
        values = entry.get("checkpoint", {}).get("channel_values", {})
        text = values.get("summary")
        if isinstance(text, str) and text:
            return {
                "text": _clip(redact_secrets_for_remote(text).text, 1_200),
                "covered_sequence": values.get("covered_sequence", 0),
                "inferred": True,
            }
    return {}


def search_shared_context(
    connection,
    codec,
    *,
    namespace: str,
    current_id: UUID,
    query: str,
    max_bytes: int = 8_192,
    time_budget: float = 3.0,
) -> dict:
    """Stream authenticated history with bounded latency and explicit coverage.

    No derived plaintext index, extra database or private-data cache survives
    deletion. The store owns the connection/lock and supplies a consistent snapshot.
    All active chats and historical turns are eligible, not just the recent page.
    """
    wanted = frozenset() if folded_request(query) in CONTINUATIONS else terms(query)
    overview = any(
        phrase in folded_request(query)
        for phrase in (
            "que hablamos",
            "que hemos hablado",
            "que hicimos",
            "que hemos hecho",
            "que quedo pendiente",
            "que tenemos pendiente",
            "chats del mac",
            "conversaciones del mac",
            "resumen de nuestras conversaciones",
        )
    )
    result = {
        "scope": "owner_active_chats",
        "sources": [],
        "search_complete": True,
        "scanned_chats": 0,
        "scanned_turns": 0,
    }
    if not wanted and not overview:
        return result
    deadline = time.monotonic() + time_budget
    candidates = []
    rows = connection.execute(
        "SELECT * FROM conversations WHERE namespace = ? "
        "ORDER BY updated_at DESC, conversation_id DESC",
        (namespace,),
    )
    for row in rows:
        if time.monotonic() >= deadline:
            result["search_complete"] = False
            break
        record = codec.conversation_from_row(row)
        if record.archived or record.conversation_id == current_id:
            continue
        result["scanned_chats"] += 1
        summary = _summary(record)
        score = len(wanted & terms((record.title or "") + " " + summary.get("text", "")))
        tail = deque(maxlen=4)
        best = []
        matched_terms = set()
        expected_sequence = 1
        complete = True
        for turn_row in connection.execute(
            "SELECT * FROM conversation_turns WHERE conversation_id = ? ORDER BY sequence",
            (str(record.conversation_id),),
        ):
            if time.monotonic() >= deadline:
                complete = result["search_complete"] = False
                break
            turn = codec.turn_from_row(turn_row, namespace=namespace)
            if turn.sequence != expected_sequence:
                codec.fail("conversation_history_sequence_mismatch")
            expected_sequence += 1
            result["scanned_turns"] += 1
            tail.append(turn)
            found = wanted & terms(turn.content)
            for document in turn.documents:
                found |= wanted & terms(document.name + " " + document.content)
            matched_terms.update(found)
            if found:
                best.append((len(found), turn))
                best.sort(key=lambda item: (item[0], item[1].sequence), reverse=True)
                del best[2:]
        if complete and expected_sequence != row["turn_count"] + 1:
            codec.fail("conversation_history_sequence_mismatch")
        score += len(matched_terms)
        if not score and not overview:
            continue
        selected = {turn.sequence: turn for _, turn in best}
        selected.update({turn.sequence: turn for turn in tail})
        sources = []
        for turn in sorted(selected.values(), key=lambda t: t.sequence):
            source = {
                "sequence": turn.sequence,
                "role": turn.role.value,
                "observed_at": turn.created_at.isoformat(),
                "excerpt": _excerpt(turn.content, wanted),
                "digest": turn.content_sha256,
                "excerpt_only": True,
            }
            docs = [
                {"name": d.name, "excerpt": _excerpt(d.content, wanted), "excerpt_only": True}
                for d in turn.documents
                if wanted & terms(d.name + " " + d.content)
            ]
            if docs:
                source["documents"] = docs[:2]
            sources.append(source)
        candidate = {
            "conversation_id": str(record.conversation_id),
            "title": record.title or "Chat de Jarvis",
            "updated_at": record.updated_at.isoformat(),
            "source_scan_complete": complete,
            "excerpt_only": True,
            "messages": sources,
        }
        if summary:
            candidate["summary"] = summary
        # Task status is historical reference only. Never export argument JSON,
        # approvals, journals, call digests or a task checkpoint into another chat.
        if record.task_checkpoint:
            candidate["historical_goal"] = {
                "text": _clip(redact_secrets_for_remote(
                    record.task_checkpoint.active_intent).text, 800),
                "status": record.task_checkpoint.status,
                "observed_at": record.task_checkpoint.updated_at.isoformat(),
            }
        if not sources and not summary and not record.task_checkpoint:
            continue
        candidates.append((score, record.updated_at, candidate))
        candidates.sort(key=lambda item: (item[0], item[1]), reverse=True)
        del candidates[4:]
    seen = set()
    for _, _, candidate in candidates:
        # Forks can have identical sources; don't crowd out independent memories.
        digests = tuple(m["digest"] for m in candidate["messages"])
        if digests and digests in seen:
            continue
        seen.add(digests)
        safe = sanitize_remote_reference(candidate)
        # Keep a useful source even for long messages/documents. Fit each source
        # before packing the aggregate so one large chat cannot consume everything.
        if len(json.dumps(safe, ensure_ascii=False).encode("utf-8")) > 3_900:
            for message in safe["messages"]:
                message["excerpt"] = _excerpt(message["excerpt"], wanted, 300)
                for document in message.get("documents", []):
                    document["excerpt"] = _excerpt(document["excerpt"], wanted, 200)
            if "summary" in safe:
                safe["summary"]["text"] = _clip(safe["summary"]["text"], 600)
            while (
                len(json.dumps(safe, ensure_ascii=False).encode("utf-8")) > 3_900
                and len(safe["messages"]) > 1
            ):
                # Retain the strongest old match and most recent correction.
                del safe["messages"][1 if len(safe["messages"]) > 2 else 0]
            safe["context_truncated"] = True
        trial = {**result, "sources": [*result["sources"], safe]}
        if len(json.dumps(trial, ensure_ascii=False).encode("utf-8")) <= max_bytes:
            result["sources"].append(safe)
        else:
            result["context_truncated"] = True
    return result
