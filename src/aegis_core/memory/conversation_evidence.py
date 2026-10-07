"""Bounded exact-source citations alongside the deliberately lossy narrative summary.

NVIDIA suggests which source passage matters. Only local, contiguous authenticated
fragments establish its quote, role, digest and offsets. Categories remain inferred;
an authentic quote is not proof of a fact, current state or permission to act.
"""

from __future__ import annotations

import json
import re
from collections import defaultdict
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from aegis_core.request_intent import folded_request

EVIDENCE_VERSION = 1
EVIDENCE_MAX_BYTES = 8_192
EVIDENCE_MAX_ITEMS = 24
EVIDENCE_REFERENCE_BYTES = 2_048
EVIDENCE_MAX_QUOTE_BYTES = 256
EVIDENCE_MAX_CANDIDATES = 4

EvidenceKind = Literal["decision", "quantity", "constraint", "pending", "correction"]
_KINDS = ("correction", "constraint", "quantity", "decision", "pending")
_IMPORTANCE = {kind: len(_KINDS) - index for index, kind in enumerate(_KINDS)}
_BOUNDARY = re.compile(r"[.!?](?=\s|$)|\n")
_COMMON = frozenset("a al de del el en es la las lo los para por que un una y con me mi "
                    "mis tu tus como cual cuales cuanto cuantos jarvis quiero saber sobre".split())


def _size(value) -> int:
    # Match the reference frame sent to NVIDIA, including JSON separator spaces.
    return len(json.dumps(value, ensure_ascii=False).encode("utf-8"))


class CitationProposal(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    kind: EvidenceKind
    sequence: int = Field(ge=1, strict=True)
    quote: str = Field(min_length=1, max_length=EVIDENCE_MAX_QUOTE_BYTES)

    @model_validator(mode="after")
    def quote_has_bounded_utf8(self):
        if len(self.quote.encode("utf-8")) > EVIDENCE_MAX_QUOTE_BYTES:
            raise ValueError("citation quote exceeds byte budget")
        return self


class SourceCitation(CitationProposal):
    role: Literal["user", "assistant"]
    digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    start_byte: int = Field(ge=0, strict=True)
    end_byte: int = Field(gt=0, strict=True)
    quote_verified: Literal[True] = True
    classification_inferred: Literal[True] = True

    @model_validator(mode="after")
    def source_range_matches_quote(self):
        if self.end_byte - self.start_byte != len(self.quote.encode("utf-8")):
            raise ValueError("citation source range does not match quote")
        return self


def _windows(batch: list[dict]) -> dict[int, dict]:
    """Join only adjacent fragments of one role and source digest."""
    sources = {}
    for fragment in batch:
        sequence = fragment["sequence"]
        previous = sources.get(sequence)
        if previous:
            if (previous["end_byte"] != fragment["start_byte"]
                    or previous["digest"] != fragment["digest"]
                    or previous["role"] != fragment["role"]):
                raise ValueError("citation source fragments are not contiguous")
            previous["content"] += fragment["content"]
            previous["end_byte"] = fragment["end_byte"]
        else:
            sources[sequence] = {**fragment}
    return sources


def _contextual_quote(source: dict, quote: str) -> tuple[str, int, int] | None:
    text = source["content"]
    quote = quote.strip()
    if not quote:
        return None
    found = text.find(quote)
    # Repeated short quotes can have different negations/attributions. Require
    # the model to supply enough exact context to identify one source passage.
    if found < 0 or text.find(quote, found + 1) >= 0:
        return None
    end = found + len(quote)
    left = 0
    for boundary in _BOUNDARY.finditer(text, 0, found):
        left = boundary.end()
    boundary = _BOUNDARY.search(text, max(found, end - 1))
    right = boundary.end() if boundary else len(text)
    if ((left == 0 and source["start_byte"] > 0)
            or (boundary is None and source["end_byte"] < source["total_bytes"])):
        return None  # Context might contain a negation outside this batch.
    # A colon-headed preceding line can identify a quotation or a hypothetical.
    # Keep that attribution with its example rather than turning it into a fact.
    before = text[:left].rstrip()
    if before.endswith(":"):
        prior = max(before.rfind("\n"), before.rfind(". "), before.rfind("! "),
                    before.rfind("? "))
        left = prior + 1 if prior >= 0 else 0
    while left < right and text[left].isspace():
        left += 1
    while right > left and text[right - 1].isspace():
        right -= 1
    expanded = text[left:right]
    if len(expanded.encode("utf-8")) > EVIDENCE_MAX_QUOTE_BYTES:
        return None  # Never retain just a convenient positive clause of a longer sentence.
    start_byte = source["start_byte"] + len(text[:left].encode("utf-8"))
    return expanded, start_byte, start_byte + len(expanded.encode("utf-8"))


def validate_citations(proposals: list, batch: list[dict]) -> tuple[list[dict], bool]:
    """Discard unsupported claims and derive provenance locally, not from NVIDIA."""
    if not isinstance(proposals, list) or len(proposals) > EVIDENCE_MAX_CANDIDATES:
        raise ValueError("invalid citation proposal list")
    sources = _windows(batch)
    accepted, rejected = [], False
    for value in proposals:
        try:
            proposal = CitationProposal.model_validate(value)
            source = sources.get(proposal.sequence)
            location = _contextual_quote(source, proposal.quote) if source else None
            if location is None:
                rejected = True
                continue
            quote, start, end = location
            citation = SourceCitation(kind=proposal.kind, sequence=proposal.sequence,
                quote=quote, role=source["role"], digest=source["digest"],
                start_byte=start, end_byte=end)
            accepted.append(citation.model_dump())
        except (ValidationError, ValueError, TypeError):
            rejected = True
    return accepted, rejected


def _validated_items(items: list[dict]) -> list[dict]:
    result = []
    for item in items:
        try:
            result.append(SourceCitation.model_validate(item).model_dump())
        except (ValidationError, TypeError):
            continue
    return result


def merge_citations(existing: list[dict], additions: list[dict]) -> tuple[list[dict], bool]:
    """Retain category and source diversity; corrections cannot crowd out all quantities."""
    unique = {}
    for item in _validated_items([*existing, *additions]):
        key = (item["sequence"], item["digest"], item["start_byte"], item["end_byte"])
        previous = unique.get(key)
        if previous is None or _IMPORTANCE[item["kind"]] > _IMPORTANCE[previous["kind"]]:
            unique[key] = item
    buckets = defaultdict(list)
    for item in unique.values():
        buckets[item["kind"]].append(item)
    for items in buckets.values():
        items.sort(key=lambda item: (item["role"] == "user", item["sequence"],
                                     item["start_byte"]), reverse=True)
    ordered, per_source = [], defaultdict(int)
    # Round-robin categories and limit each source to two items during the first
    # pass. Many extracts from one verbose turn must not erase unrelated decisions.
    while any(buckets.values()):
        for kind in _KINDS:
            bucket = buckets[kind]
            if not bucket:
                continue
            index = next((i for i, item in enumerate(bucket)
                          if per_source[item["sequence"]] < 2), 0)
            item = bucket.pop(index)
            ordered.append(item)
            per_source[item["sequence"]] += 1
    retained = []
    for item in ordered:
        trial = [*retained, item]
        if len(trial) <= EVIDENCE_MAX_ITEMS and _size(trial) <= EVIDENCE_MAX_BYTES:
            retained.append(item)
    retained.sort(key=lambda item: (item["sequence"], item["start_byte"]))
    return retained, len(retained) < len(unique)


def _terms(text: str) -> frozenset[str]:
    return frozenset(word for word in re.findall(r"[a-z0-9]+", folded_request(text))
                     if len(word) > 2 and word not in _COMMON)


def select_citations(items: list[dict], query: str = "", *, max_bytes: int =
                     EVIDENCE_REFERENCE_BYTES) -> tuple[list[dict], bool]:
    """Select authentic whole quotes under the model-context budget, never re-summarize."""
    wanted = _terms(query)
    candidates = _validated_items(items)
    ranked = sorted(candidates, key=lambda item: (
        len(wanted & _terms(item["quote"])), item["role"] == "user",
        _IMPORTANCE[item["kind"]], item["sequence"], item["start_byte"],
    ), reverse=True)
    selected = []
    for item in ranked:
        if wanted and not (wanted & _terms(item["quote"])):
            continue
        trial = [*selected, item]
        if _size(trial) <= max_bytes:
            selected.append(item)
    selected.sort(key=lambda item: (item["sequence"], item["start_byte"]))
    return selected, len(selected) < len(candidates)
