"""Owner-visible activity metadata, separate from the immutable security audit.

Only daemon-admitted requests can publish here. No prompts, arguments, output,
URLs, app/document names or personal conversation titles are retained.
"""

from __future__ import annotations

import hashlib
import threading
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from aegis_core.contracts import PolicyDecision, ToolAuthorization, ToolExecutionResult
from aegis_core.failure_boundary import record_boundary_error
from aegis_core.settings_store import read_private_json, write_private_json

MAX_HISTORY_ENTRIES = 1_000
MAX_HISTORY_BYTES = 1_048_576
ActionState = Literal["authorized", "awaiting_approval", "denied", "reported_success",
                      "failed", "unconfirmed"]


class ActionHistoryConflict(ValueError):
    """The owner must refresh before applying an operation to a changed snapshot."""


class ActionHistoryEntry(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    id: UUID
    sequence: int = Field(strict=True, ge=1, le=9_007_199_254_740_991)
    timestamp: datetime
    tool_name: str = Field(pattern=r"^[a-z][a-z0-9_-]{2,63}$")
    state: ActionState
    conversation_id: UUID | None
    execution_count: int = Field(default=0, strict=True, ge=0, le=1_000_000)
    failure_count: int = Field(default=0, strict=True, ge=0, le=1_000_000)
    # An opaque correlation key; raw provider call IDs never enter this file.
    correlation: str = Field(pattern=r"^[0-9a-f]{64}$", exclude=True)

    @field_validator("timestamp")
    @classmethod
    def aware_timestamp(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("history timestamp must have a timezone")
        return value

    def persisted(self) -> dict:
        return {**self.model_dump(mode="json"), "correlation": self.correlation}

    @model_validator(mode="after")
    def valid_counts(self) -> ActionHistoryEntry:
        if self.failure_count > self.execution_count:
            raise ValueError("invalid action attempt counts")
        return self


class HistoryDocument(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    version: Literal[1] = 1
    revision: UUID = Field(default_factory=uuid4)
    next_sequence: int = Field(default=1, strict=True, ge=1, le=9_007_199_254_740_991)
    entries: tuple[ActionHistoryEntry, ...] = Field(default=(), max_length=MAX_HISTORY_ENTRIES)

    @model_validator(mode="after")
    def unique_ordered_entries(self) -> HistoryDocument:
        sequences = [entry.sequence for entry in self.entries]
        if (sequences != sorted(set(sequences))
                or len({entry.id for entry in self.entries}) != len(self.entries)
                or len({entry.correlation for entry in self.entries}) != len(self.entries)
                or any(sequence >= self.next_sequence for sequence in sequences)):
            raise ValueError("invalid history ordering")
        return self


@dataclass
class _RequestContext:
    conversation_id: UUID | None
    suppressed: set[str] = field(default_factory=set)
    correlations: set[str] = field(default_factory=set)
    cleared: bool = False


class ActionHistoryStore:
    """Bounded private metadata for actions recorded from this installation onward.

    Admission/cleanup hooks are internal: they are not exposed through IPC or
    accepted from tool arguments. Clearing history revokes all currently admitted
    writers so delayed results cannot repopulate the owner's cleared history.
    """

    def __init__(self, path: Path, *, clock: Callable[[], datetime] = lambda: datetime.now(UTC),
                 max_entries: int = MAX_HISTORY_ENTRIES) -> None:
        if type(max_entries) is not int or not 1 <= max_entries <= MAX_HISTORY_ENTRIES:
            raise ValueError("history capacity is invalid")
        self.path = path
        self._clock = clock
        self._max_entries = max_entries
        self._lock = threading.RLock()
        self._empty = HistoryDocument()
        self._requests: dict[UUID, _RequestContext] = {}
        self.recording_incomplete = False

    def _read(self) -> HistoryDocument:
        try:
            document = HistoryDocument.model_validate(
                read_private_json(self.path, max_bytes=MAX_HISTORY_BYTES)
            )
        except FileNotFoundError:
            return self._empty
        active = set().union(*(context.correlations for context in self._requests.values()))
        # A previous daemon can leave a pending marker after interruption. It is
        # never shown as still running or successful in this daemon's session.
        recovered = tuple(
            entry.model_copy(update={"state": "unconfirmed"})
            if entry.state in {"authorized", "awaiting_approval"}
            and entry.correlation not in active else entry for entry in document.entries
        )
        if recovered != document.entries:
            return self._write(list(recovered), next_sequence=document.next_sequence)
        return document

    def _write(self, entries: list[ActionHistoryEntry], *, next_sequence: int) -> HistoryDocument:
        document = HistoryDocument(entries=tuple(entries[-self._max_entries:]),
                                   next_sequence=next_sequence)
        write_private_json(self.path, {
            "version": 1, "revision": str(document.revision),
            "next_sequence": document.next_sequence,
            "entries": [entry.persisted() for entry in document.entries],
        }, max_bytes=MAX_HISTORY_BYTES)
        self._empty = document.model_copy(update={"entries": ()})
        return document

    def begin_request(self, request_id: UUID, *, conversation_id: UUID | None,
                      temporary: bool) -> None:
        with self._lock:
            if temporary:
                return
            if request_id in self._requests or len(self._requests) >= 4_096:
                self.recording_incomplete = True
                return
            self._requests[request_id] = _RequestContext(conversation_id)

    def finish_request(self, request_id: UUID) -> None:
        with self._lock:
            context = self._requests.get(request_id)
            if context is None or context.cleared:
                self._requests.pop(request_id, None)
                return
            try:
                document = self._read()
                # Only this request's correlations are eligible. They are opaque
                # in persistence, and never reconstructed from personal history.
                prefix = context.correlations
                entries = [entry.model_copy(update={"state": "unconfirmed"})
                           if entry.correlation in prefix
                           and entry.state in {"authorized", "awaiting_approval"}
                           else entry for entry in document.entries]
                if tuple(entries) != document.entries:
                    self._write(entries, next_sequence=document.next_sequence)
            except (OSError, ValueError) as error:
                self._capture_failed(error)
            finally:
                self._requests.pop(request_id, None)

    def _capture_failed(self, error: Exception) -> None:
        self.recording_incomplete = True
        record_boundary_error(error)

    @staticmethod
    def _correlation(request_id: UUID, call_id: str) -> str:
        return hashlib.sha256(f"{request_id}\n{call_id}".encode()).hexdigest()

    def _record(self, request_id: UUID, call_id: str, tool_name: str,
                state: ActionState) -> None:
        with self._lock:
            context = self._requests.get(request_id)
            if context is None or context.cleared:
                return
            correlation = self._correlation(request_id, call_id)
            if correlation in context.suppressed:
                return
            try:
                document = self._read()
                existing = next((entry for entry in document.entries
                                 if entry.correlation == correlation), None)
                execution = state in {"reported_success", "failed"}
                if existing is not None:
                    # Repeated authorizations must not erase an execution receipt.
                    if existing.execution_count and not execution:
                        return
                    entry = existing.model_copy(update={
                        "state": state,
                        "execution_count": min(existing.execution_count + int(execution),
                                               1_000_000),
                        "failure_count": min(existing.failure_count + int(state == "failed"),
                                             1_000_000),
                    })
                    entries = [entry if item.id == existing.id else item
                               for item in document.entries]
                    next_sequence = document.next_sequence
                else:
                    if correlation in context.correlations:
                        return  # Retention already evicted this entry; do not resurrect it.
                    if len(context.correlations) >= MAX_HISTORY_ENTRIES:
                        self.recording_incomplete = True
                        return
                    entry = ActionHistoryEntry(
                        id=uuid4(), sequence=document.next_sequence, timestamp=self._clock(),
                        tool_name=tool_name, state=state, conversation_id=context.conversation_id,
                        execution_count=int(execution), failure_count=int(state == "failed"),
                        correlation=correlation,
                    )
                    entries = [*document.entries, entry]
                    next_sequence = document.next_sequence + 1
                self._write(entries, next_sequence=next_sequence)
                context.correlations.add(correlation)
            except (OSError, ValueError) as error:
                self._capture_failed(error)

    def record_authorization(self, request_id: UUID, authorization: ToolAuthorization) -> None:
        state: ActionState = {
            PolicyDecision.ALLOW: "authorized", PolicyDecision.DENY: "denied",
            PolicyDecision.REQUIRE_CONFIRMATION: "awaiting_approval",
        }[authorization.decision]
        self._record(request_id, authorization.call_id, authorization.tool_name, state)

    def record_execution(self, request_id: UUID, result: ToolExecutionResult) -> None:
        self._record(request_id, result.call_id, result.tool_name,
                     "reported_success" if result.success else "failed")

    def record_system_event(self, request_id: UUID, *, event_type: str, component: str,
                            data: Mapping[str, str | int | bool | None],
                            call_id: str | None = None) -> None:
        # General telemetry and security events do not belong to activity history.
        return

    def page(self, *, limit: int = 30, before_sequence: int | None = None,
             revision: UUID | None = None, conversation_id: UUID | None = None) -> dict:
        if type(limit) is not int or not 1 <= limit <= 50:
            raise ValueError("invalid history page size")
        if before_sequence is not None and (type(before_sequence) is not int
                                            or before_sequence < 1 or revision is None):
            raise ValueError("invalid history cursor")
        with self._lock:
            document = self._read()
            if revision is not None and document.revision != revision:
                raise ActionHistoryConflict("history changed")
            rows = [entry for entry in reversed(document.entries)
                    if (before_sequence is None or entry.sequence < before_sequence)
                    and (conversation_id is None or entry.conversation_id == conversation_id)]
            page = rows[:limit]
            return {"revision": str(document.revision),
                    "entries": [entry.model_dump(mode="json") for entry in page],
                    "next_before_sequence": page[-1].sequence if len(rows) > limit else None,
                    "retention_limit": self._max_entries,
                    "recording_incomplete": self.recording_incomplete}

    def delete(self, *, revision: UUID, entry_id: UUID | None) -> dict:
        with self._lock:
            document = self._read()
            if revision != document.revision:
                raise ActionHistoryConflict("history changed")
            target = next((entry for entry in document.entries if entry.id == entry_id), None)
            if entry_id is not None and target is None:
                raise ActionHistoryConflict("history entry no longer exists")
            entries = [] if entry_id is None else [entry for entry in document.entries
                                                   if entry.id != entry_id]
            # Atomic replacement can succeed before directory fsync reports an
            # error. Revoke writers first: an uncertain receipt must never allow
            # a late result to recreate data the owner has just removed.
            for context in self._requests.values():
                if entry_id is None:
                    context.cleared = True
                elif target is not None:
                    context.suppressed.add(target.correlation)
            saved = self._write(entries, next_sequence=document.next_sequence)
            return {"revision": str(saved.revision),
                    "deleted": len(document.entries) - len(entries),
                    "entry_id": str(entry_id) if entry_id is not None else None}
