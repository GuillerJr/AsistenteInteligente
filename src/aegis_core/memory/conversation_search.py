from __future__ import annotations

import base64
import hashlib
import hmac
import os
import re
import sqlite3
import time
import unicodedata
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Literal
from uuid import UUID

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, field_validator

from aegis_core.memory.contracts import ConversationRecord
from aegis_core.memory.conversation_codec import ConversationRowCodec


def search_text(value: str) -> str:
    return unicodedata.normalize("NFC", value).casefold()


class ConversationSearchPayload(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    query: str = Field(min_length=1, max_length=128, strict=True)
    archived: bool = Field(default=False, strict=True)
    filed: bool = Field(default=False, strict=True)
    limit: int = Field(default=30, ge=1, le=50, strict=True)
    cursor: str | None = Field(default=None, min_length=1, max_length=2048, strict=True)

    @field_validator("query")
    @classmethod
    def nonblank_query(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("empty conversation search")
        return value


class SearchPosition(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    version: Literal["1"] = "1"
    snapshot_at: AwareDatetime
    created_at: AwareDatetime
    conversation_id: UUID
    # None means this conversation is finished; an integer resumes its message scan.
    after_sequence: int | None = Field(default=None, ge=0, le=10_000, strict=True)
    max_sequence: int = Field(default=0, ge=0, le=10_000, strict=True)
    query_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    archived: bool
    filed: bool


class SearchCursorCodec:
    """Opaque authenticated position with no query text, scoped to this owner/service."""

    def __init__(self, namespace: str) -> None:
        self._namespace = namespace
        self._key = os.urandom(32)

    def _signature(self, encoded: str) -> str:
        return hmac.new(
            self._key, (self._namespace + "\0" + encoded).encode(), hashlib.sha256
        ).hexdigest()

    def encode(self, position: SearchPosition) -> str:
        encoded = base64.urlsafe_b64encode(position.model_dump_json().encode()).decode().rstrip("=")
        return encoded + "." + self._signature(encoded)

    def decode(self, request: ConversationSearchPayload) -> SearchPosition | None:
        if request.cursor is None:
            return None
        if re.fullmatch(r"[A-Za-z0-9_-]{1,1980}\.[0-9a-f]{64}", request.cursor) is None:
            raise ValueError("invalid conversation search cursor")
        encoded, signature = request.cursor.split(".")
        if not hmac.compare_digest(signature, self._signature(encoded)):
            raise ValueError("invalid conversation search cursor")
        raw = base64.b64decode(encoded + "=" * (-len(encoded) % 4), altchars=b"-_", validate=True)
        position = SearchPosition.model_validate_json(raw)
        if (
            position.query_digest != query_digest(request.query)
            or position.archived != request.archived
            or position.filed != request.filed
            or (
                position.after_sequence is not None
                and position.after_sequence > position.max_sequence
            )
        ):
            raise ValueError("conversation search cursor scope mismatch")
        return position


def query_digest(query: str) -> str:
    return hashlib.sha256(search_text(query).encode()).hexdigest()


@dataclass(frozen=True)
class ConversationSearchMatch:
    conversation: ConversationRecord
    excerpt: str | None = None
    sequence: int | None = None
    turn_id: UUID | None = None


@dataclass(frozen=True)
class ConversationSearchPage:
    matches: tuple[ConversationSearchMatch, ...]
    next_position: SearchPosition | None


def match_excerpt(content: str, query: str) -> str:
    text = unicodedata.normalize("NFC", " ".join(content.split()))
    folded = text.casefold()
    offset = folded.find(query)
    # Case folding can expand one character (ß → ss), so convert its offset back.
    consumed = 0
    index = 0
    for character in text:
        if consumed >= max(0, offset):
            break
        consumed += len(character.casefold())
        index += 1
    start = max(0, index - 48)
    excerpt = text[start : start + 192]
    return ("…" if start else "") + excerpt + ("…" if start + 192 < len(text) else "")


class ConversationSearchRepository:
    """Decrypt only bounded batches in memory; never persist searchable plaintext.

    The cursor orders by immutable creation time/ID and can stop inside a long
    conversation. Each conversation yields at most one hit. New chats are excluded
    after the initial snapshot; refresh includes subsequent edits and additions.
    """

    MAX_WORK_ITEMS = 600
    MAX_SECONDS = 0.75

    def __init__(
        self,
        codec: ConversationRowCodec,
        *,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._codec = codec
        self._clock = clock

    def search(
        self,
        connection: sqlite3.Connection,
        *,
        namespace: str,
        request: ConversationSearchPayload,
        position: SearchPosition | None,
    ) -> ConversationSearchPage:
        started = self._clock()
        snapshot = position.snapshot_at if position else datetime.now(UTC)
        needle = search_text(request.query)
        matches: list[ConversationSearchMatch] = []
        work = 0
        last_position = position
        boundary = ""
        arguments: list[object] = [namespace, snapshot.isoformat()]
        if position:
            inclusive = "<=" if position.after_sequence is not None else "<"
            boundary = (
                f" AND (created_at < ? OR (created_at = ? AND conversation_id {inclusive} ?))"
            )
            arguments += [
                position.created_at.isoformat(),
                position.created_at.isoformat(),
                str(position.conversation_id),
            ]
        rows = connection.execute(
            "SELECT * FROM conversations WHERE namespace = ? AND created_at <= ?"
            + boundary
            + " ORDER BY created_at DESC, conversation_id DESC",
            arguments,
        )
        for row in rows:
            # Always make progress before yielding, even with a slow filesystem.
            if work and (
                work >= self.MAX_WORK_ITEMS
                or self._clock() - started >= self.MAX_SECONDS
                or len(matches) >= request.limit
            ):
                return ConversationSearchPage(tuple(matches), last_position)
            record = self._codec.conversation_from_row(row)
            work += 1
            last_position = SearchPosition(
                snapshot_at=snapshot,
                created_at=record.created_at,
                conversation_id=record.conversation_id,
                query_digest=query_digest(request.query),
                archived=request.archived,
                filed=request.filed,
            )
            if (
                record.archived != request.archived
                or record.context_checkpoints.get("temporary_chat") is True
                or (
                    not request.archived
                    and (record.context_checkpoints.get("filed_chat") is True) != request.filed
                )
            ):
                continue
            resume = (
                position is not None
                and position.conversation_id == record.conversation_id
                and position.after_sequence is not None
            )
            after = position.after_sequence if resume else 0
            initial_after = after
            maximum = position.max_sequence if resume else int(row["turn_count"])
            if not resume and needle in search_text(record.title or ""):
                matches.append(ConversationSearchMatch(record))
                continue
            turns = connection.execute(
                "SELECT * FROM conversation_turns WHERE conversation_id = ? "
                "AND sequence > ? AND sequence <= ? ORDER BY sequence",
                (str(record.conversation_id), after, maximum),
            )
            for turn_row in turns:
                if (
                    work >= self.MAX_WORK_ITEMS or self._clock() - started >= self.MAX_SECONDS
                ) and not (resume and after == initial_after):
                    pending = last_position.model_copy(
                        update={
                            "after_sequence": after,
                            "max_sequence": maximum,
                        }
                    )
                    return ConversationSearchPage(tuple(matches), pending)
                turn = self._codec.turn_from_row(turn_row, namespace=namespace)
                work += 1
                if turn.sequence != after + 1:
                    self._codec.fail("conversation_search_sequence_mismatch")
                after = turn.sequence
                if needle in search_text(turn.content):
                    matches.append(
                        ConversationSearchMatch(
                            record,
                            match_excerpt(turn.content, needle),
                            turn.sequence,
                            turn.turn_id,
                        )
                    )
                    break
            else:
                if after != maximum:
                    self._codec.fail("conversation_search_count_mismatch")
        return ConversationSearchPage(tuple(matches), None)
