"""Read-only retrieval across the owner's encrypted chats, never task activation."""

from __future__ import annotations

import json
import re
import time
from collections import Counter, deque
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


def _matches(clean: str, query_terms: frozenset[str]) -> list[tuple[int, int, str]]:
    """Locate query terms using UTF-8 offsets in one pass over the source."""
    matches = []
    byte_offset, previous_end = 0, 0
    for match in re.finditer(r"\w+", clean):
        byte_offset += len(clean[previous_end:match.start()].encode("utf-8"))
        end = byte_offset + len(match.group().encode("utf-8"))
        matches.extend((byte_offset, end, term) for term in sorted(
            terms(match.group()) & query_terms
        ))
        byte_offset, previous_end = end, match.end()
    return matches


def _passage(encoded: bytes, matches: list[tuple[int, int, str]], budget: int) -> tuple:
    """Prefer a cluster of distinct, rarer terms over its first generic match.

    The sliding window is linear in the source's matches. Repeated generic words
    do not outrank a passage containing several specific query terms.
    """
    frequencies = Counter(term for _, _, term in matches)
    active = deque()
    counts = Counter()
    right = 0
    best_score, best_bounds = None, (0, budget)
    for anchor, _, _ in matches:
        start = max(0, anchor - min(120, budget // 8))
        end = start + budget
        while right < len(matches) and matches[right][1] <= end:
            item = matches[right]
            active.append(item)
            counts[item[2]] += 1
            right += 1
        while active and active[0][0] < start:
            item = active.popleft()
            counts[item[2]] -= 1
            if not counts[item[2]]:
                del counts[item[2]]
        if not counts:
            continue
        span = active[-1][1] - active[0][0]
        score = (len(counts), sum(1 / frequencies[t] for t in counts), -span, -start)
        if best_score is None or score > best_score:
            best_score, best_bounds = score, (start, end)
    start, end = best_bounds
    return encoded[start:end].decode("utf-8", errors="ignore"), start, end


def _excerpt(text: str, query_terms: frozenset[str], budget: int = 800) -> str:
    # Redact the entire source before any cut: a boundary must never hide a secret.
    clean = redact_secrets_for_remote(text).text
    encoded = clean.encode("utf-8")
    if len(encoded) <= budget:
        return clean
    matches = _matches(clean, query_terms)
    if not matches:
        return _clip(clean, budget)
    excerpt, _start, _end = _passage(encoded, matches, budget)
    missing = {term for _, _, term in matches} - terms(excerpt)
    if missing and budget >= 300:
        # Distant relevant facts can require two passages. Keep most of the budget
        # for the strongest cluster, then add a separately marked source fragment.
        separator = "\n[…]\n"
        first_budget = (budget - len(separator.encode())) * 2 // 3
        first, first_start, first_end = _passage(encoded, matches, first_budget)
        missing = {term for _, _, term in matches} - terms(first)
        other_matches = [m for m in matches if m[2] in missing and
                         (m[1] <= first_start or m[0] >= first_end)]
        if other_matches:
            second, second_start, _ = _passage(
                encoded, other_matches, budget - first_budget - len(separator.encode())
            )
            # Preserve source order so a later correction remains recognizably later.
            return separator.join([first, second] if first_start < second_start
                                  else [second, first])
    return excerpt


def _summary(record) -> dict:
    for entry in reversed(record.context_checkpoints.get("entries", [])):
        values = entry.get("checkpoint", {}).get("channel_values", {})
        text = values.get("summary")
        if isinstance(text, str) and text:
            reliable_coverage = values.get("coverage_version") == 2
            summary = {
                "text": _clip(redact_secrets_for_remote(text).text, 1_200),
                "covered_sequence": values.get("covered_sequence", 0) if reliable_coverage else 0,
                "inferred": True,
                "excerpt_only": True,
            }
            if not reliable_coverage:
                summary["coverage_uncertain"] = True
            if reliable_coverage and values.get("partial_sequence"):
                summary["partial_turn"] = {
                    "sequence": values["partial_sequence"],
                    "processed_bytes": values["partial_offset"],
                    "total_bytes": values["partial_total_bytes"],
                }
            return summary
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
        relevance = {turn.sequence: weight for weight, turn in best}
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
        candidates.append((score, record.updated_at, candidate, relevance))
        candidates.sort(key=lambda item: (item[0], item[1]), reverse=True)
        del candidates[4:]
    seen = set()
    for _, _, candidate, relevance in candidates:
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
                # Chronological order is not relevance order. Protect the strongest
                # source and the newest correction rather than blindly removing index 1.
                strongest = max(safe["messages"], key=lambda m: (
                    relevance.get(m["sequence"], 0), m["sequence"]
                ))["sequence"]
                newest = max((m["sequence"] for m in safe["messages"]
                              if m["role"] == ConversationRole.USER.value),
                             default=safe["messages"][-1]["sequence"])
                discard = min(range(len(safe["messages"])), key=lambda index: (
                    safe["messages"][index]["sequence"] == strongest,
                    safe["messages"][index]["sequence"] == newest,
                    relevance.get(safe["messages"][index]["sequence"], 0),
                    safe["messages"][index]["sequence"],
                ))
                del safe["messages"][discard]
            safe["context_truncated"] = True
        trial = {**result, "sources": [*result["sources"], safe]}
        if len(json.dumps(trial, ensure_ascii=False).encode("utf-8")) <= max_bytes:
            result["sources"].append(safe)
        else:
            result["context_truncated"] = True
    return result
