"""Bounded own retrieval over an authorized in-memory source snapshot.

The cache supplies only vectors and coordinates. Text and citation provenance
always come from the current host snapshot, never from an old cache or a URI.
Synthetic encoders can exercise this contract; they do not qualify semantics.
"""

from __future__ import annotations

import asyncio
import hashlib
import heapq
import math
import re
import time
import unicodedata
from array import array
from collections import Counter
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass

from jarvis_core.memory import Citation, MemoryError, SearchHit, SearchResult

CANDIDATE_LIMIT = 200
MAX_QUERY_BYTES = 512
MAX_QUERY_RESULTS = 50


class MemoryIndexError(MemoryError):
    def __init__(self, code: str, *, receipt: dict | None = None):
        super().__init__(code)
        self.receipt = receipt


def checkpoint(cancellation=None, deadline=None, assert_current: Callable = lambda: None):
    if cancellation is not None and cancellation.is_set():
        raise MemoryIndexError("memory_index_cancelled")
    if deadline is not None:
        if type(deadline) not in (int, float) or not math.isfinite(deadline):
            raise MemoryIndexError("memory_index_invalid_deadline")
        if time.monotonic() >= deadline:
            raise MemoryIndexError("memory_index_timeout")
    assert_current()


def tokens(text: str) -> tuple[str, ...]:
    normalized = "".join(
        c for c in unicodedata.normalize("NFKD", text.casefold()) if not unicodedata.combining(c)
    )
    return tuple(re.findall(r"[^\W_]+", normalized, re.UNICODE))


def validate_query(query, mode, limit, offset):
    if (
        not isinstance(query, str)
        or not query.strip()
        or len(query.encode("utf-8", errors="surrogatepass")) > MAX_QUERY_BYTES
        or any(0xD800 <= ord(c) <= 0xDFFF for c in query)
        or mode not in {"semantic_local", "hybrid_local"}
        or type(limit) is not int
        or not 1 <= limit <= MAX_QUERY_RESULTS
        or type(offset) is not int
        or not 0 <= offset <= CANDIDATE_LIMIT
    ):
        raise MemoryIndexError("memory_index_invalid_query")


def vector_values(value, dimension: int) -> array:
    if (
        type(dimension) is not int
        or not 1 <= dimension <= 4096
        or not isinstance(value, Sequence)
        or isinstance(value, (str, bytes, bytearray))
        or len(value) != dimension
    ):
        raise MemoryIndexError("memory_index_invalid_vector")
    values = array("f")
    try:
        for item in value:
            if type(item) not in (int, float) or not math.isfinite(item):
                raise MemoryIndexError("memory_index_invalid_vector")
            values.append(item)
    except (OverflowError, TypeError):
        raise MemoryIndexError("memory_index_invalid_vector") from None
    norm = math.fsum(x * x for x in values)
    if not math.isfinite(norm) or abs(norm - 1.0) > 0.001:
        raise MemoryIndexError("memory_index_invalid_vector")
    return values


def byte_boundary(data: bytes, position: int) -> bool:
    return 0 <= position <= len(data) and (position == len(data) or data[position] & 0xC0 != 0x80)


@dataclass(frozen=True)
class IndexedSpan:
    document_id: str
    revision: int
    text_sha256: str
    start_byte: int
    end_byte: int
    token_count: int
    vector: array

    @property
    def identity(self):
        return self.document_id, self.revision, self.start_byte, self.end_byte


def _retain(heap, score, ordinal, span):
    item = (score, -ordinal, ordinal, span)
    if len(heap) < CANDIDATE_LIMIT:
        heapq.heappush(heap, item)
    elif item[:2] > heap[0][:2]:
        heapq.heapreplace(heap, item)


def _ordered(heap):
    return sorted(
        ((score, span) for score, _, _, span in heap),
        key=lambda item: (-item[0], item[1].identity),
    )


def _near_duplicate(first: IndexedSpan, second: IndexedSpan) -> bool:
    if first.document_id != second.document_id:
        return False
    overlap = max(
        0,
        min(first.end_byte, second.end_byte) - max(first.start_byte, second.start_byte),
    )
    return overlap >= 0.65 * min(
        first.end_byte - first.start_byte, second.end_byte - second.start_byte
    )


def _cosine(first, second):
    return max(-1.0, min(1.0, math.fsum(a * b for a, b in zip(first, second, strict=True))))


async def _diverse(candidates, mode, needed, cancellation, deadline, assert_current):
    remaining = list(candidates)
    selected = []
    redundancy = {span.identity: 0.0 for _, span in remaining}
    while remaining and len(selected) < needed:
        checkpoint(cancellation, deadline, assert_current)
        if len(selected) % 4 == 0:
            await asyncio.sleep(0)
            checkpoint(cancellation, deadline, assert_current)

        def quality(item):
            score, span = item
            relevance = (score + 1.0) / 2 if mode == "semantic_local" else score
            return -(0.85 * relevance - 0.15 * redundancy[span.identity]), span.identity

        chosen = min(remaining, key=quality)
        selected.append(chosen)
        remaining = [item for item in remaining if not _near_duplicate(item[1], chosen[1])]
        for _, span in remaining:
            redundancy[span.identity] = max(
                redundancy[span.identity],
                max(0.0, _cosine(span.vector, chosen[1].vector)),
            )
    return selected


async def retrieve(
    snapshot,
    spans: Iterable[IndexedSpan],
    query_vector,
    query: str,
    *,
    mode: str,
    limit: int,
    offset: int,
    cancellation=None,
    deadline=None,
    assert_current=lambda: None,
) -> SearchResult:
    """Scan every admitted span, retaining bounded candidates, without text caches.

    Lexical ranking uses plain OR token matches and normalized term frequency.
    Hybrid fusion uses independent one-based channel ranks (RRF k=60). Returned
    scores are cosine or normalized RRF, not source truth or action verification.
    """
    validate_query(query, mode, limit, offset)
    query_terms = tuple(dict.fromkeys(tokens(query)))
    query_set = set(query_terms)
    phrase = tokens(query)
    documents = {item.document.document_id: item for item in snapshot.documents}
    source_bytes = {key: value.text.encode() for key, value in documents.items()}
    semantic, lexical = [], []
    for ordinal, span in enumerate(spans):
        if ordinal % 32 == 0:
            checkpoint(cancellation, deadline, assert_current)
            await asyncio.sleep(0)
            checkpoint(cancellation, deadline, assert_current)
        source = documents.get(span.document_id)
        if (
            source is None
            or source.document.revision != span.revision
            or source.document.text_sha256 != span.text_sha256
        ):
            raise MemoryIndexError("memory_index_stale")
        data = source_bytes[span.document_id]
        if (
            type(span.start_byte) is not int
            or type(span.end_byte) is not int
            or not 0 <= span.start_byte < span.end_byte <= len(data)
            or not byte_boundary(data, span.start_byte)
            or not byte_boundary(data, span.end_byte)
        ):
            raise MemoryIndexError("memory_index_corrupt")
        _retain(semantic, _cosine(query_vector, span.vector), ordinal, span)
        if mode == "hybrid_local" and query_set:
            words = tokens(data[span.start_byte : span.end_byte].decode())
            counts = Counter(words)
            common = query_set.intersection(counts)
            if common:
                score = math.fsum(1 + math.log(counts[word]) for word in common) / math.sqrt(
                    1 + len(words)
                )
                if phrase and any(
                    words[start : start + len(phrase)] == phrase
                    for start in range(len(words) - len(phrase) + 1)
                ):
                    score += 1.0
                _retain(lexical, score, ordinal, span)
    checkpoint(cancellation, deadline, assert_current)
    semantic_rank = _ordered(semantic)
    if mode == "semantic_local":
        candidates = semantic_rank
    else:
        fused = {}
        for channel in (semantic_rank, _ordered(lexical)):
            for rank, (_, span) in enumerate(channel, 1):
                previous = fused.get(span.identity, (0.0, span))
                fused[span.identity] = (previous[0] + 1 / (60 + rank), span)
        maximum = 2 / 61
        candidates = sorted(
            ((score / maximum, span) for score, span in fused.values()),
            key=lambda item: (-item[0], item[1].identity),
        )[:CANDIDATE_LIMIT]
    ranked = await _diverse(
        candidates,
        mode,
        min(CANDIDATE_LIMIT, offset + limit + 1),
        cancellation,
        deadline,
        assert_current,
    )
    hits = []
    for score, span in ranked[offset : offset + limit]:
        source = documents[span.document_id]
        data = source_bytes[span.document_id]
        start, end = span.start_byte, min(span.end_byte, span.start_byte + 768)
        while not byte_boundary(data, end):
            end -= 1
        excerpt = data[start:end]
        citation = Citation(
            span.document_id,
            span.revision,
            span.text_sha256,
            start,
            end,
            hashlib.sha256(excerpt).hexdigest(),
            source.document.source_uri,
        )
        hits.append(SearchHit(source.document, excerpt.decode(), citation, float(score)))
    checkpoint(cancellation, deadline, assert_current)
    return SearchResult(
        tuple(hits),
        mode,
        snapshot.generation,
        offset + len(hits),
        offset + len(hits) < len(ranked),
        query_terms,
    )
