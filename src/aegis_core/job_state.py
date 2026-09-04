from __future__ import annotations

import asyncio
import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from datetime import datetime
from uuid import UUID

from aegis_core.contracts import ToolAuthorization, ToolCall, UserRequest
from aegis_core.feedback import OwnerFeedback
from aegis_core.job_contracts import (
    TERMINAL_STATUSES,
    JobCapacityError,
    JobEvaluation,
    JobNotFoundError,
    JobSnapshot,
    JobStatus,
    PendingToolConfirmation,
)


@dataclass(slots=True)
class MutableJob:
    """Private mutable state for one bounded daemon job."""

    job_id: UUID
    request_id: UUID
    authorization_request: UserRequest | None
    request_text: str
    conversation_id: UUID | None
    status: JobStatus
    created_at: datetime
    updated_at: datetime
    conversation_persisted: bool | None = None
    result: str | None = None
    error_code: str | None = None
    confirmation: PendingToolConfirmation | None = None
    pending_call: ToolCall | None = None
    pending_authorization: ToolAuthorization | None = None
    partial_result: str | None = None
    stream_version: int = 0
    stream_chunks: int = 0
    started_monotonic: float = field(default_factory=time.monotonic)
    first_partial_monotonic: float | None = None
    first_partial_active_latency_ms: int | None = None
    confirmation_started_monotonic: float | None = None
    confirmation_wait_ms: int = 0
    model_id: str | None = None
    tool_name: str | None = None
    action_verified: bool = False
    voice_request: bool = False
    owner_verified: bool = False
    owner_feedback_request: bool = False
    repair_attempt: bool = False
    feedback_target_id: UUID | None = None
    feedback_to_apply: OwnerFeedback | None = None
    evaluation: JobEvaluation | None = None
    change_event: asyncio.Event = field(default_factory=asyncio.Event)

    def snapshot(self) -> JobSnapshot:
        return JobSnapshot(
            job_id=self.job_id,
            request_id=self.request_id,
            conversation_id=self.conversation_id,
            conversation_persisted=self.conversation_persisted,
            status=self.status,
            created_at=self.created_at,
            updated_at=self.updated_at,
            result=self.result,
            error_code=self.error_code,
            confirmation=self.confirmation,
            partial_result=self.partial_result,
            stream_version=self.stream_version,
            evaluation=self.evaluation,
        )

    def transition(
        self,
        status: JobStatus,
        *,
        now: datetime,
        result: str | None = None,
        error_code: str | None = None,
        conversation_persisted: bool | None = None,
    ) -> bool:
        if self.status in TERMINAL_STATUSES:
            return False
        self.status = status
        self.updated_at = now
        self.result = result
        self.error_code = error_code
        self.conversation_persisted = conversation_persisted
        return True

    def expire_confirmation(self, now: datetime) -> bool:
        confirmation = self.confirmation
        if (
            self.status is not JobStatus.AWAITING_CONFIRMATION
            or confirmation is None
            or confirmation.expires_at > now
        ):
            return False
        return self.transition(
            JobStatus.FAILED,
            now=now,
            error_code="confirmation_expired",
        )

    def clear_pending(self) -> None:
        self.confirmation = None
        self.pending_call = None
        self.pending_authorization = None
        self.confirmation_started_monotonic = None

    def finish_confirmation_wait(self, monotonic_clock: Callable[[], float]) -> None:
        started = self.confirmation_started_monotonic
        if started is None:
            return
        elapsed = max(0, round((monotonic_clock() - started) * 1_000))
        self.confirmation_wait_ms = min(600_000, self.confirmation_wait_ms + elapsed)
        self.confirmation_started_monotonic = None

    def publish_stream(
        self,
        delta: str,
        *,
        bound: Callable[[str], str],
        monotonic_clock: Callable[[], float],
    ) -> bool:
        combined = (self.partial_result or "") + delta
        bounded = bound(combined)
        if bounded == self.partial_result:
            return False
        if self.first_partial_monotonic is None:
            monotonic_now = monotonic_clock()
            self.first_partial_monotonic = monotonic_now
            self.first_partial_active_latency_ms = max(
                0,
                round((monotonic_now - self.started_monotonic) * 1_000)
                - self.confirmation_wait_ms,
            )
        self.partial_result = bounded
        self.stream_chunks += 1
        self.stream_version = min(self.stream_version + 1, 100_000)
        self.publish_change()
        return True

    def publish_change(self) -> None:
        pending = self.change_event
        self.change_event = asyncio.Event()
        pending.set()


@dataclass(frozen=True, slots=True)
class RecentPublicSources:
    urls: tuple[str, ...]
    source_request_created_at: datetime
    expires_at: datetime


class JobRegistry:
    """In-memory storage and deterministic retention for daemon jobs.

    The caller owns synchronization. Keeping the registry synchronous makes lock
    ownership explicit and prevents nested asyncio locks in lifecycle paths.
    """

    def __init__(self, max_jobs: int) -> None:
        if max_jobs < 1:
            raise ValueError("max jobs must be positive")
        self._max_jobs = max_jobs
        self._jobs: dict[UUID, MutableJob] = {}

    def __len__(self) -> int:
        return len(self._jobs)

    def values(self) -> Iterable[MutableJob]:
        return self._jobs.values()

    def get(self, job_id: UUID) -> MutableJob | None:
        return self._jobs.get(job_id)

    def require(self, job_id: UUID) -> MutableJob:
        job = self.get(job_id)
        if job is None:
            raise JobNotFoundError("job does not exist")
        return job

    def reserve_capacity(self) -> None:
        self._evict_terminal_jobs()
        if len(self._jobs) >= self._max_jobs:
            raise JobCapacityError("job capacity reached")

    def add(self, job: MutableJob) -> None:
        if job.job_id in self._jobs:
            raise ValueError("job already exists")
        if len(self._jobs) >= self._max_jobs:
            raise JobCapacityError("job capacity reached")
        self._jobs[job.job_id] = job

    def expire_confirmations(self, now: datetime) -> tuple[MutableJob, ...]:
        return tuple(job for job in self._jobs.values() if job.expire_confirmation(now))

    def latest_feedback_target(self, conversation_id: UUID | None) -> UUID | None:
        candidates = (
            job
            for job in self._jobs.values()
            if job.status is JobStatus.COMPLETED
            and job.evaluation is not None
            and not job.owner_feedback_request
            and job.conversation_id == conversation_id
        )
        latest = max(candidates, key=lambda job: job.updated_at, default=None)
        return latest.job_id if latest is not None else None

    def _evict_terminal_jobs(self) -> None:
        if len(self._jobs) < self._max_jobs:
            return
        terminal = sorted(
            (job for job in self._jobs.values() if job.status in TERMINAL_STATUSES),
            key=lambda job: job.updated_at,
        )
        for job in terminal:
            if len(self._jobs) < self._max_jobs:
                break
            del self._jobs[job.job_id]
