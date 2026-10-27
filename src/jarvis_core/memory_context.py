"""Bounded local recall capsules: data for one turn, never instructions or grants."""

from __future__ import annotations

import hashlib
import json
import math
import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from types import MappingProxyType

from jarvis_core.memory import MemoryError, SearchResult

_HASH = re.compile(r"[a-f0-9]{64}\Z")
_LIMIT = 8000


def _canonical(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False,
                      separators=(",", ":"), allow_nan=False).encode()


def _clip(text, size):
    data = text.encode("utf-8")
    return data[:size].decode("utf-8", errors="ignore"), len(data) > size


def recall_query(message: str, *, force_projection=False):
    """Declare projections; explicit search requests must never use this helper."""
    if not isinstance(message, str) or not message.strip():
        raise MemoryError("invalid_query")
    data = message.encode("utf-8")
    if len(data) > 32000:
        raise MemoryError("invalid_query")
    normalized = " ".join(message.split())
    if not force_projection and len(normalized.encode()) <= 512:
        return normalized, False
    # A finite automatic query projection, not an encoder silently truncating
    # tokens. Its use is visible in the recall receipt and native status.
    projected, _ = _clip(normalized, 96)
    return projected, True


@dataclass(frozen=True)
class RecallLease:
    text: str
    fingerprint: str
    status: Mapping[str, object]
    assert_current: Callable[[], None] = field(repr=False, compare=False)


def make_recall_lease(*, scope_key, generation, policy_digest, model_fingerprint,
                      result: SearchResult | None, assert_current, state="ready",
                      mode="lexical", query_projected=False, error=None, run_id=None):
    if (not isinstance(scope_key, str) or not scope_key or len(scope_key.encode()) > 1024
            or type(generation) is not int or not 0 <= generation <= 2**63 - 1
            or not isinstance(policy_digest, str) or _HASH.fullmatch(policy_digest) is None
            or (model_fingerprint is not None and (not isinstance(model_fingerprint, str)
                or _HASH.fullmatch(model_fingerprint) is None))
            or state not in {"disabled", "empty", "ready", "unavailable"}
            or mode not in {"lexical", "fts5_lexical", "lexical_scan", "semantic_local",
                            "hybrid_local"}
            or type(query_projected) is not bool or not callable(assert_current)
            or (error is not None and (not isinstance(error, str)
                or re.fullmatch(r"[a-z0-9_]{1,100}", error) is None))):
        raise MemoryError("memory_context_invalid")
    epoch = {"format": "jarvis-memory-epoch/1", "scope": scope_key,
             "generation": generation, "policy_digest": policy_digest,
             "model_fingerprint": model_fingerprint, "mode": mode}
    # Stable for a memory epoch, deliberately independent of query/results so
    # resumed provider threads still receive a *new* capsule on every turn.
    fingerprint = hashlib.sha256(_canonical(epoch)).hexdigest()
    body = {"format": "jarvis-memory-context/1", "usage": "untrusted_context_data",
            "permissions": False, "mode": mode, "generation": generation,
            "query_projected": query_projected, "notes": []}
    if result is not None:
        if result.generation != generation or result.mode != mode:
            raise MemoryError("memory_context_changed")
        for hit in result.hits[:4]:
            cite, record = hit.citation, hit.document
            data = hit.excerpt.encode()
            if (cite.document_id != record.document_id or cite.revision != record.revision
                    or cite.text_sha256 != record.text_sha256
                    or cite.source_uri != record.source_uri
                    or cite.end_byte - cite.start_byte != len(data)
                    or hashlib.sha256(data).hexdigest() != cite.excerpt_sha256
                    or not math.isfinite(hit.score) or len(data) > 768):
                raise MemoryError("memory_context_citation_invalid")
            label, label_cut = _clip(record.source_uri, 160)
            title, title_cut = _clip(record.title or record.key, 160)
            row = {"reference": f"M{len(body['notes']) + 1}",
                   "document_id": cite.document_id, "revision": cite.revision,
                   "text_sha256": cite.text_sha256, "start_byte": cite.start_byte,
                   "end_byte": cite.end_byte, "excerpt_sha256": cite.excerpt_sha256,
                   "source_label": label, "source_label_truncated": label_cut,
                   "title": title, "title_truncated": title_cut,
                   "source_verified": False, "excerpt": hit.excerpt}
            body["notes"].append(row)
            if len(_canonical(body)) > _LIMIT:
                body["notes"].pop()
                break
    text = _canonical(body).decode() if body["notes"] else ""
    if state == "ready" and not body["notes"]:
        state = "empty"
    status = MappingProxyType({"status": state, "mode": mode,
                               "citations": len(body["notes"]),
                               "query_projected": query_projected,
                               "error": error, "run_id": run_id})
    assert_current()
    return RecallLease(text, fingerprint, status, assert_current)
