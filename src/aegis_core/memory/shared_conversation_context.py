"""Read-only retrieval across the owner's encrypted chats, never task activation."""

from __future__ import annotations

import hashlib
import json
import re
import time
from collections import Counter, deque
from copy import deepcopy
from uuid import UUID

from aegis_core.memory.contracts import ConversationRole
from aegis_core.memory.shared_context_ranking import (
    RetrievalPlan,
    correction_statement,
    factual_statement,
    numerical_evidence,
)
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
        if (len(word) > 2 or word.isdigit()) and word not in _STOP
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
        byte_offset += len(clean[previous_end : match.start()].encode("utf-8"))
        end = byte_offset + len(match.group().encode("utf-8"))
        matches.extend(
            (byte_offset, end, term) for term in sorted(terms(match.group()) & query_terms)
        )
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
    excerpt = encoded[start:end].decode("utf-8", errors="ignore")
    # Never publish the beginning of a cut number/identifier as if it were the
    # entire value. Whole source tokens are more useful than an arbitrary edge.
    if start and encoded[start - 1 : start] not in b" \n\t.,:;!?()[]{}":
        excerpt = re.sub(r"^\S+\s*", "", excerpt)
    if end < len(encoded) and encoded[end : end + 1] not in b" \n\t.,:;!?()[]{}":
        excerpt = re.sub(r"\s*\S+$", "", excerpt)
    return excerpt, start, end


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
        other_matches = [
            m for m in matches if m[2] in missing and (m[1] <= first_start or m[0] >= first_end)
        ]
        if other_matches:
            second, second_start, _ = _passage(
                encoded, other_matches, budget - first_budget - len(separator.encode())
            )
            # Preserve source order so a later correction remains recognizably later.
            return separator.join(
                [first, second] if first_start < second_start else [second, first]
            )
    return excerpt


def _summary(record) -> dict:
    for entry in reversed(record.context_checkpoints.get("entries", [])):
        values = entry.get("checkpoint", {}).get("channel_values", {})
        text = values.get("summary")
        has_evidence = values.get("evidence_version") == 1 and bool(values.get("evidence"))
        if (isinstance(text, str) and text) or has_evidence:
            reliable_coverage = values.get("coverage_version") == 2
            summary = {
                "text": _clip(
                    redact_secrets_for_remote(text if isinstance(text, str) else "").text, 1_200
                ),
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
            if values.get("evidence_version") == 1:
                raw = values.get("evidence")
                if isinstance(raw, list):
                    # Entries are re-verified against the authenticated original
                    # turn before any citation crosses the retrieval boundary.
                    summary["evidence"] = [entry for entry in raw[:24] if isinstance(entry, dict)]
                    summary["evidence_version"] = 1
                    for flag in ("evidence_partial", "evidence_truncated"):
                        if values.get(flag) is True:
                            summary[flag] = True
            return summary
    return {}


def _size(value: dict) -> int:
    return len(json.dumps(value, ensure_ascii=False).encode("utf-8"))


def _query_plan(query: str, alternate_queries: tuple[str, ...]) -> RetrievalPlan:
    entities = frozenset().union(
        *(
            terms(match.group())
            for match in re.finditer(r"\b[^\W\d_]\w*\b", query)
            if match.group().isupper() or (match.start() > 0 and match.group()[0].isupper())
        )
    )
    # Expansion is a retrieval aid, never a replacement for the original request.
    original = terms(query[:4_096])
    queries = [original]
    for alternate in alternate_queries[:4]:
        if isinstance(alternate, str):
            found = terms(_clip(alternate, 1_024))
            if found and found not in queries:
                queries.append(found)
    return RetrievalPlan(tuple(queries), entities & original)


def _verified_summary_evidence(summary: dict, turn) -> list[dict]:
    evidence = []
    for entry in summary.get("evidence", []):
        if not isinstance(entry, dict) or entry.get("sequence") != turn.sequence:
            continue
        start, end = entry.get("start_byte"), entry.get("end_byte")
        quote = entry.get("quote")
        if (
            entry.get("quote_verified") is not True
            or entry.get("digest") != turn.content_sha256
            or entry.get("role") != turn.role.value
            or type(start) is not int
            or type(end) is not int
            or not isinstance(quote, str)
            or not 0 <= start < end
            or entry.get("kind")
            not in {"decision", "quantity", "constraint", "pending", "correction"}
        ):
            continue
        clean = redact_secrets_for_remote(turn.content).text.encode("utf-8")
        if end > len(clean) or clean[start:end] != quote.encode("utf-8"):
            continue
        evidence.append(
            {
                key: entry[key]
                for key in ("kind", "sequence", "role", "digest", "start_byte", "end_byte", "quote")
            }
            | {"quote_verified": True, "classification_inferred": True}
        )
    return evidence


def _turn_source(turn, wanted: frozenset[str], *, documents_only: bool = False) -> dict:
    clean = redact_secrets_for_remote(turn.content).text
    source = {
        "sequence": turn.sequence,
        "role": turn.role.value,
        "observed_at": turn.created_at.isoformat(),
        "excerpt": "" if documents_only else _excerpt(turn.content, wanted, 1_200),
        "digest": turn.content_sha256,
        "excerpt_only": True,
        "excerpt_truncated": documents_only or len(clean.encode("utf-8")) > 1_200,
    }
    docs = []
    for document in turn.documents:
        found = wanted & terms(document.name + " " + document.content)
        if not found:
            continue
        docs.append(
            (
                len(found),
                {
                    "name": document.name,
                    "digest": hashlib.sha256(document.content.encode("utf-8")).hexdigest(),
                    "excerpt": _excerpt(document.content, wanted, 1_200),
                    "excerpt_only": True,
                    "excerpt_truncated": len(
                        redact_secrets_for_remote(document.content).text.encode()
                    )
                    > 1_200,
                },
            )
        )
    if docs:
        docs.sort(key=lambda item: item[0], reverse=True)
        source["documents"] = [document for _, document in docs[:2]]
        if len(docs) > 2:
            source["documents_truncated"] = True
    return source


def _select_messages(
    pool: list[dict],
    plan: RetrievalPlan,
    weights: dict[str, float],
    *,
    overview: bool,
    current: bool,
) -> tuple[list[dict], dict[int, float]]:
    selected, covered = [], set()
    pending = {entry["source"]["sequence"]: entry for entry in pool}
    relevance = {}
    while pending and len(selected) < 6:

        def rank(entry):
            base = plan.score(entry["found"], weights)
            complement = sum(weights.get(term, 0) for term in entry["found"] - covered)
            correction = 4 if entry["correction"] else 0
            old = 1 if current and entry["old"] else 0
            numeric = 0.7 if entry["numerical"] and entry["found"] else 0
            return base + complement * 2 + correction + old + numeric

        best = max(pending.values(), key=lambda entry: (rank(entry), entry["source"]["sequence"]))
        sequence = best["source"]["sequence"]
        if not best["found"] and not best["correction"] and not overview:
            del pending[sequence]
            continue
        selected.append(best["source"])
        relevance[sequence] = rank(best)
        covered.update(best["found"])
        del pending[sequence]
    return sorted(selected, key=lambda source: source["sequence"]), relevance


def _fit_source(
    candidate: dict, wanted: frozenset[str], relevance: dict[int, float], budget: int
) -> dict | None:
    safe = sanitize_remote_reference(deepcopy(candidate))
    if _size(safe) <= budget:
        return safe
    safe["context_truncated"] = True
    if "summary" in safe:
        summary = safe["summary"]
        summary["text"] = _clip(summary.get("text", ""), 240)
    # Evidence is selected by relevance, not chronological position. Keep the
    # strongest statement and the newest correction for as long as both fit.
    for excerpt_budget in (650, 400, 240):
        for message in safe["messages"]:
            if len(message["excerpt"].encode()) > excerpt_budget:
                message["excerpt"] = _excerpt(message["excerpt"], wanted, excerpt_budget)
                message["excerpt_truncated"] = True
            for document in message.get("documents", []):
                if len(document["excerpt"].encode()) > excerpt_budget:
                    document["excerpt"] = _excerpt(document["excerpt"], wanted, excerpt_budget)
                    document["excerpt_truncated"] = True
        if _size(safe) <= budget:
            return safe
    while _size(safe) > budget and len(safe["messages"]) > 1:
        strongest = max(
            safe["messages"],
            key=lambda message: (relevance.get(message["sequence"], 0), message["sequence"]),
        )["sequence"]
        newest_correction = max(
            (message["sequence"] for message in safe["messages"] if message.get("correction")),
            default=-1,
        )
        discard = min(
            range(len(safe["messages"])),
            key=lambda index: (
                safe["messages"][index]["sequence"] == strongest,
                safe["messages"][index]["sequence"] == newest_correction,
                relevance.get(safe["messages"][index]["sequence"], 0),
                safe["messages"][index]["sequence"],
            ),
        )
        del safe["messages"][discard]
    if _size(safe) > budget and "summary" in safe:
        summary = safe["summary"]
        # Citations have exact provenance and survive prose compression. If the
        # remaining budget cannot fit all of them, drop whole entries explicitly.
        while _size(safe) > budget and summary.get("evidence"):
            summary["evidence"].pop(0)
            summary["evidence_truncated"] = True
    if _size(safe) > budget and "summary" in safe:
        del safe["summary"]
    if _size(safe) > budget and "historical_goal" in safe:
        del safe["historical_goal"]
    return safe if _size(safe) <= budget else None


def search_shared_context(
    connection,
    codec,
    *,
    namespace: str,
    current_id: UUID,
    query: str,
    alternate_queries: tuple[str, ...] = (),
    include_current: bool = False,
    only_current: bool = False,
    recent_start_sequence: int = 0,
    visible_sequences: tuple[int, ...] | None = None,
    max_bytes: int = 8_192,
    time_budget: float = 3.0,
) -> dict:
    """Retrieve exact encrypted sources across bounded independent formulations.

    No plaintext index/cache survives deletion. Expansion supplies vocabulary;
    original entities, rare terms, titles and complementary evidence own ranking.
    Current-chat evidence can be recovered independently of a lossy summary.
    """
    continuations = folded_request(query) in CONTINUATIONS
    plan = _query_plan(query, () if continuations else alternate_queries)
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
        "scope": "current_chat" if only_current else "owner_active_chats",
        # Bind selected evidence to the actual query, including paraphrases that
        # have no lexical overlap with their source. This grants no authority.
        "request_sha256": hashlib.sha256(
            redact_secrets_for_remote(query).text.encode("utf-8")
        ).hexdigest(),
        "sources": [],
        "search_complete": True,
        "scanned_chats": 0,
        "scanned_turns": 0,
        "query_coverage": {
            "original_term_count": len(plan.original),
            "original_matched_count": 0,
            "query_count": len(plan.queries),
            "matched_query_count": 0,
        },
    }
    if (
        continuations
        or (not plan.original and not overview)
        or (only_current and not include_current)
    ):
        return result
    wanted = plan.wanted
    fully_visible = frozenset(visible_sequences) if visible_sequences is not None else None
    deadline = time.monotonic() + time_budget
    candidates, frequency = [], Counter()
    candidate_limit_reached = False
    query_sql = "SELECT * FROM conversations WHERE namespace = ? "
    parameters = (namespace,)
    if only_current:
        query_sql += "AND conversation_id = ? "
        parameters += (str(current_id),)
    rows = connection.execute(
        query_sql + "ORDER BY updated_at DESC, conversation_id DESC", parameters
    )
    for row in rows:
        if time.monotonic() >= deadline:
            result["search_complete"] = False
            break
        record = codec.conversation_from_row(row)
        current = record.conversation_id == current_id
        if record.archived or (current and not include_current):
            continue
        result["scanned_chats"] += 1
        summary = _summary(record)
        if current and recent_start_sequence > 0 and summary.get("evidence"):
            previous = summary["evidence"]
            summary["evidence"] = [
                entry
                for entry in previous
                if type(entry.get("sequence")) is int
                and (
                    entry["sequence"] < recent_start_sequence
                    or (fully_visible is not None and entry["sequence"] not in fully_visible)
                )
            ]
            if len(summary["evidence"]) < len(previous):
                summary["evidence_truncated"] = True
        title_terms = wanted & terms(record.title or "")
        found_all = title_terms | (wanted & terms(summary.get("text", "")))
        representatives, strongest, corrections = {}, [], []
        tail = deque(maxlen=2)
        valid_evidence = []
        history_fingerprint = hashlib.sha256()
        eligible_turn_count = 0
        expected_sequence, complete = 1, True
        preliminary = plan.weights({}, 1)
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
            history_fingerprint.update(
                json.dumps(
                    [
                        turn.role.value,
                        turn.sequence,
                        turn.content_sha256,
                        [
                            (
                                document.name,
                                hashlib.sha256(document.content.encode("utf-8")).hexdigest(),
                            )
                            for document in turn.documents
                        ],
                    ],
                    ensure_ascii=False,
                    separators=(",", ":"),
                ).encode("utf-8")
            )
            history_fingerprint.update(b"\n")
            valid_evidence.extend(_verified_summary_evidence(summary, turn))
            recent = (
                current
                and recent_start_sequence > 0
                and turn.sequence >= recent_start_sequence
                and (fully_visible is None or turn.sequence in fully_visible)
            )
            found = frozenset() if recent else wanted & terms(turn.content)
            document_terms = frozenset().union(
                *(wanted & terms(d.name + " " + d.content) for d in turn.documents)
            )
            found = found | document_terms
            found_all |= found
            correction = (
                not recent
                and turn.role == ConversationRole.USER
                and correction_statement(turn.content)
            )
            if recent and not document_terms:
                continue
            if not found and not correction and not overview:
                continue
            eligible_turn_count += 1
            source = _turn_source(turn, wanted, documents_only=recent)
            if correction:
                source["correction"] = True
                source["correction_classification_inferred"] = True
            entry = {
                "source": source,
                "found": found,
                "correction": correction,
                "old": turn.sequence <= max(0, row["turn_count"] - 4),
                "numerical": numerical_evidence(turn.content),
            }
            weight = (
                plan.score(found, preliminary)
                + (3 if correction else 0)
                + (2 if factual_statement(turn.content) else 0)
            )
            for term in found:
                prior = representatives.get(term)
                if prior is None or (weight, turn.sequence) > prior[0]:
                    representatives[term] = ((weight, turn.sequence), entry)
            if found:
                strongest.append((weight, turn.sequence, entry))
                strongest.sort(key=lambda item: (item[0], item[1]), reverse=True)
                del strongest[6:]
            if correction:
                corrections.append((weight, turn.sequence, entry))
                corrections.sort(key=lambda item: (item[0], item[1]), reverse=True)
                del corrections[4:]
            if overview:
                tail.append(entry)
        if complete and expected_sequence != row["turn_count"] + 1:
            codec.fail("conversation_history_sequence_mismatch")
        frequency.update(found_all)
        if not found_all and not overview:
            continue
        pool = [entry for _, _, entry in strongest]
        pool += [entry for _, entry in representatives.values()]
        pool += [entry for _, _, entry in corrections] + list(tail)
        if summary.get("evidence"):
            if len(valid_evidence) != len(summary["evidence"]):
                summary["evidence_partial"] = True
            summary["evidence"] = valid_evidence
        candidate = {
            "conversation_id": str(record.conversation_id),
            "title": record.title or "Chat de Jarvis",
            "updated_at": record.updated_at.isoformat(),
            "source_scan_complete": complete,
            "excerpt_only": True,
        }
        if current:
            candidate["is_current"] = True
        if summary:
            candidate["summary"] = summary
        if record.task_checkpoint:
            candidate["historical_goal"] = {
                "text": _clip(
                    redact_secrets_for_remote(record.task_checkpoint.active_intent).text, 800
                ),
                "status": record.task_checkpoint.status,
                "observed_at": record.task_checkpoint.updated_at.isoformat(),
            }
        if not pool and not summary and not record.task_checkpoint:
            continue
        item = {
            "candidate": candidate,
            "pool": pool,
            "found": found_all,
            "title_terms": title_terms,
            "current": current,
            "updated_at": record.updated_at,
            "eligible_turn_count": eligible_turn_count,
        }
        # Compare complete authenticated histories, not query-selected passages.
        # A different role or an unmatched divergent turn remains distinct.
        # Partial scans cannot prove that forks are equal.
        identity = history_fingerprint.hexdigest() if complete else None
        item["identity"] = identity
        item["facets"] = frozenset().union(
            *(
                terms(entry["source"]["excerpt"]) - wanted
                for entry in pool
                if entry["source"]["role"] == ConversationRole.USER.value
            )
        )
        if identity:
            duplicate = next((entry for entry in candidates if entry["identity"] == identity), None)
            if duplicate is not None:
                if (current, record.updated_at) <= (duplicate["current"], duplicate["updated_at"]):
                    continue
                candidates.remove(duplicate)
        candidates.append(item)
        candidates.sort(
            key=lambda entry: (
                len(plan.entities & entry["found"]),
                plan.score(entry["found"], preliminary),
                entry["updated_at"],
            ),
            reverse=True,
        )
        if len(candidates) > 24:
            candidate_limit_reached = True
            del candidates[24:]
    weights = plan.weights(frequency, result["scanned_chats"])
    ranked, covered, covered_facets = [], set(), set()
    while candidates and len(ranked) < 8:
        eligible = (
            candidates
            if len(ranked) < 4
            else [
                entry
                for entry in candidates
                if entry["found"] - covered or entry["facets"] - covered_facets
            ]
        )
        if not eligible:
            break
        best = max(
            eligible,
            key=lambda entry: (
                len(plan.entities & entry["found"]),
                plan.score(entry["found"], weights)
                + sum(weights.get(term, 0) for term in entry["title_terms"])
                + 2 * sum(weights.get(term, 0) for term in entry["found"] - covered),
                len(entry["facets"] - covered_facets),
                entry["updated_at"],
            ),
        )
        candidates.remove(best)
        ranked.append(best)
        covered.update(best["found"])
        covered_facets.update(best["facets"])
    result["retrieval_truncated"] = bool(candidates or candidate_limit_reached)
    if result["retrieval_truncated"]:
        result["context_truncated"] = True
    for index, entry in enumerate(ranked):
        candidate = entry["candidate"]
        messages, relevance = _select_messages(
            entry["pool"], plan, weights, overview=overview, current=entry["current"]
        )
        candidate["messages"] = messages
        if entry["eligible_turn_count"] > len(messages):
            candidate["context_truncated"] = True
        remaining_sources = len(ranked) - index
        available = max_bytes - _size(result) - 64
        # Reserve a useful citation for complementary sources before granting a
        # larger budget to the strongest source. Unused space rolls forward.
        budget = min(3_900, max(600, available - (remaining_sources - 1) * 600))
        safe = _fit_source(candidate, wanted, relevance, budget)
        if (
            safe is not None
            and _size({**result, "sources": [*result["sources"], safe]}) <= max_bytes - 64
        ):
            result["sources"].append(safe)
            if safe.get("context_truncated"):
                result["context_truncated"] = True
                result["retrieval_truncated"] = True
        else:
            result["context_truncated"] = True
            result["retrieval_truncated"] = True
    surfaced = set()
    for source in result["sources"]:
        surfaced.update(terms(source["title"]))
        summary = source.get("summary", {})
        surfaced.update(terms(summary.get("text", "")))
        for entry in summary.get("evidence", []):
            surfaced.update(terms(entry["quote"]))
        for message in source["messages"]:
            surfaced.update(terms(message["excerpt"]))
            for document in message.get("documents", []):
                surfaced.update(terms(document["name"] + " " + document["excerpt"]))
    result["query_coverage"]["original_matched_count"] = len(plan.original & surfaced)
    result["query_coverage"]["matched_query_count"] = sum(
        bool(query_terms & surfaced) for query_terms in plan.queries
    )
    return result
