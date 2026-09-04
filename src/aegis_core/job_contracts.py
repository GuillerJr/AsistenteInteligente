from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum
from typing import Protocol
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from aegis_core.conversation_quality import ConversationQualityFlag
from aegis_core.dialogue import DialogueMode
from aegis_core.feedback import OwnerFeedback

MAX_JOB_RESULT_BYTES = 24_576
PENDING_CONFIRMATION_TTL = timedelta(minutes=2)


class JobError(RuntimeError):
    """Base error for bounded local job management."""


class JobNotFoundError(JobError):
    pass


class JobCapacityError(JobError):
    pass


class JobConversationNotFoundError(JobError):
    pass


class JobConfirmationError(JobError):
    pass


class EmptyAgentResponseError(JobError):
    pass


class VoiceConfirmationResultProtocol(Protocol):
    speaker_verified: bool
    semantic_verified: bool

    @property
    def authorized(self) -> bool: ...


class VoiceConfirmationVerifierProtocol(Protocol):
    async def verify(
        self,
        pcm: bytes,
        *,
        speaker_identifier: str,
        speaker_confidence: float,
        owner_profile_match: bool,
    ) -> VoiceConfirmationResultProtocol: ...


class JobStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    AWAITING_CONFIRMATION = "awaiting_confirmation"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class BrainTarget(StrEnum):
    LOCAL = "local"
    NVIDIA = "nvidia"
    DETERMINISTIC = "deterministic"
    UNKNOWN = "unknown"


TERMINAL_STATUSES = frozenset({JobStatus.COMPLETED, JobStatus.FAILED, JobStatus.CANCELLED})


class PendingToolConfirmation(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    call_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    tool_name: str = Field(pattern=r"^[a-z][a-z0-9_-]{2,63}$")
    summary: str = Field(min_length=1, max_length=512)
    expires_at: datetime

    @field_validator("expires_at")
    @classmethod
    def expiry_must_be_aware(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("confirmation expiry must be timezone-aware")
        return value


class JobEvaluation(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    brain: BrainTarget
    model_id: str | None = Field(default=None, max_length=256)
    total_latency_ms: int = Field(ge=0, le=600_000)
    wall_latency_ms: int | None = Field(default=None, ge=0, le=600_000)
    confirmation_wait_ms: int = Field(default=0, ge=0, le=600_000)
    first_partial_latency_ms: int | None = Field(default=None, ge=0, le=600_000)
    wall_first_partial_latency_ms: int | None = Field(default=None, ge=0, le=600_000)
    stream_chunks: int = Field(ge=0, le=100_000)
    tool_name: str | None = Field(
        default=None,
        pattern=r"^[a-z][a-z0-9_-]{2,63}$",
    )
    succeeded: bool
    outcome_verified: bool
    voice_request: bool
    owner_verified: bool
    dialogue_mode: DialogueMode | None = None
    response_quality_score: int | None = Field(default=None, ge=0, le=100)
    response_quality_passed: bool | None = None
    response_word_count: int | None = Field(default=None, ge=0, le=10_000)
    response_sentence_count: int | None = Field(default=None, ge=0, le=1_000)
    response_quality_flags: tuple[ConversationQualityFlag, ...] = Field(
        default=(),
        max_length=6,
    )
    feedback_event: bool = False
    repair_attempt: bool = False
    owner_feedback: OwnerFeedback | None = None

    @property
    def active_total_ms(self) -> int:
        return self.total_latency_ms

    @property
    def active_first_partial_ms(self) -> int | None:
        return self.first_partial_latency_ms

    @property
    def wall_time_ms(self) -> int:
        return self.wall_latency_ms if self.wall_latency_ms is not None else self.total_latency_ms

    @model_validator(mode="after")
    def fields_must_match_evaluated_job(self) -> JobEvaluation:
        if self.wall_latency_ms is None:
            if self.confirmation_wait_ms:
                raise ValueError("legacy latency cannot contain confirmation wait")
        elif self.total_latency_ms + self.confirmation_wait_ms != self.wall_latency_ms:
            raise ValueError("active latency and confirmation wait do not match wall latency")
        if self.wall_latency_ms is not None and (
            (self.first_partial_latency_ms is None) != (self.wall_first_partial_latency_ms is None)
        ):
            raise ValueError("active and wall first partial latency must be present together")
        if (
            self.wall_first_partial_latency_ms is not None
            and self.first_partial_latency_ms is not None
            and self.first_partial_latency_ms > self.wall_first_partial_latency_ms
        ):
            raise ValueError("active first partial latency exceeds wall latency")
        if self.owner_verified and not self.voice_request:
            raise ValueError("owner verification requires a voice request")
        if self.owner_feedback is not None and not self.succeeded:
            raise ValueError("owner feedback requires a completed job")
        if self.repair_attempt and self.feedback_event:
            raise ValueError("feedback event cannot be a repair attempt")
        quality_fields = (
            self.dialogue_mode,
            self.response_quality_score,
            self.response_quality_passed,
            self.response_word_count,
            self.response_sentence_count,
        )
        if self.response_quality_score is None:
            if any(value is not None for value in quality_fields) or self.response_quality_flags:
                raise ValueError("partial conversation quality evaluation")
        elif (
            any(value is None for value in quality_fields)
            or not self.succeeded
            or self.tool_name is not None
        ):
            raise ValueError("conversation quality evaluation does not match job outcome")
        return self


@dataclass(frozen=True, slots=True)
class StoredJobEvaluation:
    job_id: UUID
    recorded_at: datetime
    evaluation: JobEvaluation


class EvaluationStore(Protocol):
    def append(
        self,
        job_id: UUID,
        evaluation: JobEvaluation,
        recorded_at: datetime,
    ) -> None: ...

    def load_recent(self) -> tuple[StoredJobEvaluation, ...]: ...


class JobSnapshot(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    job_id: UUID
    request_id: UUID
    conversation_id: UUID | None = None
    conversation_persisted: bool | None = None
    status: JobStatus
    created_at: datetime
    updated_at: datetime
    result: str | None = Field(default=None, max_length=32_768)
    error_code: str | None = Field(default=None, pattern=r"^[a-z][a-z0-9_]{2,63}$")
    confirmation: PendingToolConfirmation | None = None
    partial_result: str | None = Field(default=None, max_length=32_768)
    stream_version: int = Field(default=0, ge=0, le=100_000)
    evaluation: JobEvaluation | None = None

    @field_validator("created_at", "updated_at")
    @classmethod
    def timestamps_must_be_aware(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("job timestamps must be timezone-aware")
        return value

    @model_validator(mode="after")
    def terminal_fields_must_match_status(self) -> JobSnapshot:
        if self.status is JobStatus.COMPLETED and self.result is None:
            raise ValueError("completed job requires a result")
        if self.status is JobStatus.FAILED and self.error_code is None:
            raise ValueError("failed job requires an error code")
        if self.status not in {JobStatus.COMPLETED, JobStatus.FAILED} and (
            self.result is not None or self.error_code is not None
        ):
            raise ValueError("non-terminal job cannot contain result fields")
        if (self.status is JobStatus.AWAITING_CONFIRMATION) != (self.confirmation is not None):
            raise ValueError("confirmation does not match job state")
        if self.conversation_persisted is not None and (
            self.status is not JobStatus.COMPLETED or self.conversation_id is None
        ):
            raise ValueError("conversation persistence flag does not match job state")
        if (
            self.status is JobStatus.COMPLETED
            and self.conversation_id is not None
            and self.conversation_persisted is None
        ):
            raise ValueError("completed conversation job requires persistence status")
        if self.stream_version == 0 and self.partial_result is not None:
            raise ValueError("partial result requires a stream version")
        if self.status not in TERMINAL_STATUSES and self.evaluation is not None:
            raise ValueError("active job cannot contain an evaluation")
        return self
