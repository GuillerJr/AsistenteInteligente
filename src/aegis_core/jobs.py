from __future__ import annotations

import asyncio
import json
import logging
import math
import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import Any, Protocol
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from aegis_core.audio.contracts import LocalTranscriptEvent
from aegis_core.contracts import (
    AgentResult,
    ImageInput,
    InputModality,
    PolicyDecision,
    ToolAuthorization,
    ToolCall,
    ToolExecutionResult,
    UserRequest,
)
from aegis_core.conversation_quality import (
    ConversationQualityEvaluator,
    ConversationQualityFlag,
)
from aegis_core.dialogue import REPAIR_CONTEXT_METADATA, DialogueMode
from aegis_core.feedback import (
    FEEDBACK_OWNER_UNVERIFIED,
    FEEDBACK_STATUS_METADATA,
    FEEDBACK_TARGET_AVAILABLE,
    FEEDBACK_TARGET_MISSING,
    OwnerFeedback,
    extract_owner_feedback,
)
from aegis_core.ipc.protocol import IpcRequest
from aegis_core.ipc.server import IpcHandlerResult, IpcMethodHandler
from aegis_core.memory.contracts import MAX_MEMORY_CONTENT_BYTES, ConversationTurn
from aegis_core.memory.conversations import ConversationCoordinator
from aegis_core.memory.profile import OwnerProfile
from aegis_core.memory.social import SocialMemory
from aegis_core.memory.sqlite import (
    ConversationCapacityError,
    MemoryNotFoundError,
    MemoryStoreError,
)
from aegis_core.secrets import contains_likely_secret_material
from aegis_core.tools.audit import AuditSink, NullAuditSink
from aegis_core.tools.broker import PolicyContext, ToolBroker
from aegis_core.tools.confirmations import ConfirmationError, OneTimeConfirmationStore
from aegis_core.tools.execution import ReadOnlyToolExecutor

LOGGER = logging.getLogger(__name__)
REPAIR_WINDOW_TTL = timedelta(minutes=2)


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
MAX_JOB_RESULT_BYTES = 24_576
PENDING_CONFIRMATION_TTL = timedelta(minutes=2)
MAX_IMAGE_SUBMIT_PAYLOAD_BYTES = 60_000
MAX_RECENT_VOICE_CAPTURES = 256
QUALITY_MINIMUM_SAMPLES = 20
QUALITY_SUCCESS_RATE_TARGET = 0.95
QUALITY_FIRST_PARTIAL_P95_TARGET_MS = 2_000
QUALITY_CONVERSATION_P95_TARGET_MS = 8_000
QUALITY_RESPONSE_PASS_RATE_TARGET = 0.95
QUALITY_OWNER_RECOGNITION_TARGET = 0.90
QUALITY_OWNER_FEEDBACK_TARGET = 0.80
QUALITY_OWNER_FEEDBACK_MINIMUM_SAMPLES = 5
MAX_JOB_WAIT_SECONDS = 20
CONFIRMED_TOOL_NAMES = frozenset(
    {
        "application_open",
        "browser_open_url",
        "calendar_create_event",
        "computer_use",
        "contact_create",
        "mail_send_message",
        "media_control",
        "network_discover_hosts",
        "reminder_complete",
        "reminder_create",
        "shortcut_run",
        "spotlight_open",
        "system_audio_set",
        "terminal_run_template",
    }
)
_CONVERSATION_QUALITY_EVALUATOR = ConversationQualityEvaluator()


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
    first_partial_latency_ms: int | None = Field(default=None, ge=0, le=600_000)
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
    owner_feedback: OwnerFeedback | None = None

    @model_validator(mode="after")
    def fields_must_match_evaluated_job(self) -> JobEvaluation:
        if self.owner_verified and not self.voice_request:
            raise ValueError("owner verification requires a voice request")
        if self.owner_feedback is not None and not self.succeeded:
            raise ValueError("owner feedback requires a completed job")
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


@dataclass(slots=True)
class _Job:
    job_id: UUID
    request_id: UUID
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
    task: asyncio.Task[None] | None = None
    partial_result: str | None = None
    stream_version: int = 0
    stream_chunks: int = 0
    started_monotonic: float = field(default_factory=time.monotonic)
    first_partial_monotonic: float | None = None
    model_id: str | None = None
    tool_name: str | None = None
    action_verified: bool = False
    voice_request: bool = False
    owner_verified: bool = False
    owner_feedback_request: bool = False
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


class SwarmGraph(Protocol):
    async def ainvoke(self, input: dict[str, Any]) -> dict[str, Any]: ...


@dataclass(frozen=True, slots=True)
class _GraphInvocation:
    final_result: AgentResult | None
    pending: tuple[tuple[ToolCall, ToolAuthorization], ...]
    model_id: str | None
    tool_name: str | None


class SwarmJobManager:
    def __init__(
        self,
        graph: SwarmGraph,
        *,
        max_jobs: int = 128,
        execution_timeout_seconds: float = 120.0,
        conversations: ConversationCoordinator | None = None,
        owner_profile: OwnerProfile | None = None,
        social_memory: SocialMemory | None = None,
        tool_broker: ToolBroker | None = None,
        policy_context: PolicyContext | None = None,
        confirmation_store: OneTimeConfirmationStore | None = None,
        tool_executor: ReadOnlyToolExecutor | None = None,
        audit_sink: AuditSink | None = None,
        evaluation_store: EvaluationStore | None = None,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        if max_jobs < 1:
            raise ValueError("max jobs must be positive")
        if not 0 < execution_timeout_seconds <= 600:
            raise ValueError("execution timeout is out of range")
        self._graph = graph
        self._max_jobs = max_jobs
        self._execution_timeout_seconds = execution_timeout_seconds
        self._conversations = conversations
        self._owner_profile = owner_profile
        self._social_memory = social_memory
        self._tool_broker = tool_broker
        self._policy_context = policy_context
        self._confirmation_store = confirmation_store
        self._tool_executor = tool_executor
        self._audit = audit_sink or NullAuditSink()
        self._evaluation_store = evaluation_store
        self._clock = clock
        self._jobs: dict[UUID, _Job] = {}
        self._repair_windows: dict[UUID | None, datetime] = {}
        self._lock = asyncio.Lock()
        self._closed = False

    async def submit(
        self,
        request: UserRequest,
        *,
        conversation_id: UUID | None = None,
    ) -> JobSnapshot:
        if conversation_id is not None:
            if self._conversations is None:
                raise JobError("conversation coordinator is unavailable")
            try:
                await self._conversations.ensure_exists(conversation_id)
            except MemoryNotFoundError as error:
                raise JobConversationNotFoundError("conversation does not exist") from error
            except MemoryStoreError as error:
                raise JobError("conversation store is unavailable") from error
        async with self._lock:
            if self._closed:
                raise JobError("job manager is closed")
            self._expire_pending_confirmations(self._clock())
            self._evict_terminal_jobs()
            if len(self._jobs) >= self._max_jobs:
                raise JobCapacityError("job capacity reached")
            now = self._clock()
            self._expire_repair_windows(now)
            owner_verified = OwnerProfile.is_verified_owner_voice(request)
            requested_feedback = extract_owner_feedback(request.text)
            feedback_target_id = None
            feedback_to_apply = None
            if requested_feedback is not None:
                if InputModality.AUDIO in request.modalities and not owner_verified:
                    feedback_status = FEEDBACK_OWNER_UNVERIFIED
                else:
                    feedback_target_id = self._latest_feedback_target(conversation_id)
                    feedback_status = (
                        FEEDBACK_TARGET_AVAILABLE
                        if feedback_target_id is not None
                        else FEEDBACK_TARGET_MISSING
                    )
                    if feedback_target_id is not None:
                        feedback_to_apply = requested_feedback
                request = request.model_copy(
                    update={
                        "metadata": {
                            **request.metadata,
                            FEEDBACK_STATUS_METADATA: feedback_status,
                        }
                    }
                )
            elif self._repair_windows.pop(conversation_id, None) is not None:
                request = request.model_copy(
                    update={
                        "metadata": {
                            **request.metadata,
                            REPAIR_CONTEXT_METADATA: True,
                        }
                    }
                )
            job = _Job(
                job_id=uuid4(),
                request_id=request.request_id,
                request_text=request.text,
                conversation_id=conversation_id,
                status=JobStatus.QUEUED,
                created_at=now,
                updated_at=now,
                voice_request=InputModality.AUDIO in request.modalities,
                owner_verified=owner_verified,
                owner_feedback_request=requested_feedback is not None,
                feedback_target_id=feedback_target_id,
                feedback_to_apply=feedback_to_apply,
            )
            self._jobs[job.job_id] = job
            job.task = asyncio.create_task(
                self._run(job.job_id, request, conversation_id),
                name=f"aegis-job-{job.job_id}",
            )
            return job.snapshot()

    async def status(self, job_id: UUID) -> JobSnapshot:
        async with self._lock:
            self._expire_pending_confirmations(self._clock())
            job = self._jobs.get(job_id)
            if job is None:
                raise JobNotFoundError("job does not exist")
            return job.snapshot()

    async def wait_for_change(
        self,
        job_id: UUID,
        *,
        after_stream_version: int,
        timeout_seconds: float,
    ) -> JobSnapshot:
        if not 0 <= after_stream_version <= 100_000:
            raise ValueError("stream version is out of range")
        if not 0.1 <= timeout_seconds <= MAX_JOB_WAIT_SECONDS:
            raise ValueError("job wait timeout is out of range")
        async with self._lock:
            self._expire_pending_confirmations(self._clock())
            job = self._jobs.get(job_id)
            if job is None:
                raise JobNotFoundError("job does not exist")
            if (
                job.status in TERMINAL_STATUSES
                or job.status is JobStatus.AWAITING_CONFIRMATION
                or job.stream_version != after_stream_version
            ):
                return job.snapshot()
            change_event = job.change_event
        try:
            await asyncio.wait_for(change_event.wait(), timeout=timeout_seconds)
        except TimeoutError:
            pass
        return await self.status(job_id)

    async def metrics(self) -> dict[str, Any]:
        async with self._lock:
            current = tuple(
                StoredJobEvaluation(
                    job_id=job.job_id,
                    recorded_at=job.updated_at,
                    evaluation=job.evaluation,
                )
                for job in self._jobs.values()
                if job.evaluation is not None
            )
        stored: tuple[StoredJobEvaluation, ...] = ()
        if self._evaluation_store is not None:
            try:
                stored = self._evaluation_store.load_recent()
            except Exception:
                LOGGER.warning("evaluation_history_read_failed")
        by_job_id = {item.job_id: item.evaluation for item in stored}
        by_job_id.update({item.job_id: item.evaluation for item in current})
        evaluations = tuple(by_job_id.values())
        latencies = sorted(item.total_latency_ms for item in evaluations)
        first_partials = sorted(
            item.first_partial_latency_ms
            for item in evaluations
            if item.first_partial_latency_ms is not None
        )
        completed = sum(item.succeeded for item in evaluations)
        conversations = tuple(
            item
            for item in evaluations
            if item.tool_name is None and not item.feedback_event
        )
        actions = tuple(item for item in evaluations if item.tool_name is not None)
        feedback_events = tuple(item for item in evaluations if item.feedback_event)
        conversation_latencies = sorted(item.total_latency_ms for item in conversations)
        action_successes = sum(
            item.succeeded and item.outcome_verified for item in actions
        )
        success_rate = round(completed / len(evaluations), 4) if evaluations else 0.0
        action_success_rate = (
            round(action_successes / len(actions), 4) if actions else None
        )
        voice_jobs = tuple(item for item in evaluations if item.voice_request)
        owner_recognition_rate = (
            round(sum(item.owner_verified for item in voice_jobs) / len(voice_jobs), 4)
            if voice_jobs
            else None
        )
        quality_assessed = tuple(
            item for item in conversations if item.response_quality_passed is not None
        )
        response_quality_pass_rate = (
            round(
                sum(item.response_quality_passed is True for item in quality_assessed)
                / len(quality_assessed),
                4,
            )
            if quality_assessed
            else None
        )
        response_quality_scores = sorted(
            item.response_quality_score
            for item in quality_assessed
            if item.response_quality_score is not None
        )
        response_quality_flags = {
            flag.value: sum(flag in item.response_quality_flags for item in quality_assessed)
            for flag in ConversationQualityFlag
        }
        feedback_evaluations = tuple(
            item for item in evaluations if item.owner_feedback is not None
        )
        owner_feedback_helpful_rate = (
            round(
                sum(
                    item.owner_feedback is OwnerFeedback.HELPFUL
                    for item in feedback_evaluations
                )
                / len(feedback_evaluations),
                4,
            )
            if feedback_evaluations
            else None
        )
        first_partial_p95 = self._percentile(first_partials, 0.95)
        conversation_p95 = self._percentile(conversation_latencies, 0.95)
        quality_checks = {
            "success_rate": success_rate >= QUALITY_SUCCESS_RATE_TARGET,
            "first_partial_p95_ms": (
                first_partial_p95 is not None
                and first_partial_p95 <= QUALITY_FIRST_PARTIAL_P95_TARGET_MS
            ),
            "conversation_p95_ms": (
                conversation_p95 is not None
                and conversation_p95 <= QUALITY_CONVERSATION_P95_TARGET_MS
            ),
            "action_success_rate": (
                action_success_rate >= QUALITY_SUCCESS_RATE_TARGET
                if action_success_rate is not None
                else None
            ),
            "owner_recognition_rate": (
                owner_recognition_rate >= QUALITY_OWNER_RECOGNITION_TARGET
                if owner_recognition_rate is not None
                else None
            ),
            "response_quality_pass_rate": (
                response_quality_pass_rate >= QUALITY_RESPONSE_PASS_RATE_TARGET
                if response_quality_pass_rate is not None
                else None
            ),
            "owner_feedback_helpful_rate": (
                owner_feedback_helpful_rate >= QUALITY_OWNER_FEEDBACK_TARGET
                if len(feedback_evaluations) >= QUALITY_OWNER_FEEDBACK_MINIMUM_SAMPLES
                and owner_feedback_helpful_rate is not None
                else None
            ),
        }
        required_checks = tuple(
            value for value in quality_checks.values() if value is not None
        )
        quality_status = (
            "insufficient_data"
            if len(evaluations) < QUALITY_MINIMUM_SAMPLES
            else "competitive"
            if required_checks and all(required_checks)
            else "needs_attention"
        )
        return {
            "jobs": len(evaluations),
            "completed": completed,
            "failed_or_cancelled": len(evaluations) - completed,
            "success_rate": success_rate,
            "latency_ms": {
                "p50": self._percentile(latencies, 0.50),
                "p95": self._percentile(latencies, 0.95),
                "first_partial_p50": self._percentile(first_partials, 0.50),
                "first_partial_p95": first_partial_p95,
                "conversation_p95": conversation_p95,
            },
            "brain": {
                target.value: sum(item.brain is target for item in evaluations)
                for target in BrainTarget
            },
            "quality": {
                "status": quality_status,
                "minimum_samples": QUALITY_MINIMUM_SAMPLES,
                "targets": {
                    "success_rate": QUALITY_SUCCESS_RATE_TARGET,
                    "first_partial_p95_ms": QUALITY_FIRST_PARTIAL_P95_TARGET_MS,
                    "conversation_p95_ms": QUALITY_CONVERSATION_P95_TARGET_MS,
                    "action_success_rate": QUALITY_SUCCESS_RATE_TARGET,
                    "owner_recognition_rate": QUALITY_OWNER_RECOGNITION_TARGET,
                    "response_quality_pass_rate": QUALITY_RESPONSE_PASS_RATE_TARGET,
                    "owner_feedback_helpful_rate": QUALITY_OWNER_FEEDBACK_TARGET,
                    "owner_feedback_minimum_samples": QUALITY_OWNER_FEEDBACK_MINIMUM_SAMPLES,
                },
                "observed": {
                    "success_rate": success_rate,
                    "first_partial_p95_ms": first_partial_p95,
                    "conversation_p95_ms": conversation_p95,
                    "action_success_rate": action_success_rate,
                    "owner_recognition_rate": owner_recognition_rate,
                    "response_quality_pass_rate": response_quality_pass_rate,
                    "response_quality_score_p50": self._percentile(
                        response_quality_scores,
                        0.50,
                    ),
                    "response_quality_assessed": len(quality_assessed),
                    "response_quality_flags": response_quality_flags,
                    "owner_feedback_helpful_rate": owner_feedback_helpful_rate,
                    "owner_feedback_count": len(feedback_evaluations),
                    "feedback_jobs": len(feedback_events),
                    "conversation_jobs": len(conversations),
                    "action_jobs": len(actions),
                    "voice_jobs": len(voice_jobs),
                },
                "passes": quality_checks,
            },
        }

    async def approve(self, job_id: UUID, call_digest: str) -> JobSnapshot:
        async with self._lock:
            now = self._clock()
            self._expire_pending_confirmations(now)
            job = self._jobs.get(job_id)
            if job is None:
                raise JobNotFoundError("job does not exist")
            if (
                job.status is not JobStatus.AWAITING_CONFIRMATION
                or job.confirmation is None
                or job.pending_call is None
                or job.pending_authorization is None
                or job.confirmation.call_digest != call_digest
            ):
                raise JobConfirmationError("confirmation is unavailable")
            if (
                self._tool_broker is None
                or self._policy_context is None
                or self._confirmation_store is None
                or self._tool_executor is None
            ):
                raise JobConfirmationError("confirmation runtime is unavailable")
            try:
                self._confirmation_store.issue(
                    job.pending_call,
                    job.pending_authorization,
                    approved_by="local-menu-bar-user",
                    now=self._policy_context.current_time(),
                )
            except ConfirmationError as error:
                raise JobConfirmationError("confirmation could not be issued") from error
            authorization = self._tool_broker.authorize(job.pending_call, self._policy_context)
            if (
                authorization.decision is not PolicyDecision.ALLOW
                or authorization.reason_code != "confirmation_consumed"
            ):
                job.status = JobStatus.FAILED
                job.updated_at = now
                job.error_code = "confirmation_consumption_failed"
                self._record_evaluation(job, JobStatus.FAILED)
                self._clear_pending(job)
                self._publish_change(job)
                raise JobConfirmationError("confirmation could not be consumed")
            job.status = JobStatus.RUNNING
            job.updated_at = now
            job.confirmation = None
            self._publish_change(job)
            job.task = asyncio.create_task(
                self._run_approved_tool(job_id, authorization),
                name=f"aegis-approved-tool-{job_id}",
            )
            return job.snapshot()

    async def cancel(self, job_id: UUID) -> JobSnapshot:
        async with self._lock:
            self._expire_pending_confirmations(self._clock())
            job = self._jobs.get(job_id)
            if job is None:
                raise JobNotFoundError("job does not exist")
            task = job.task if job.status not in TERMINAL_STATUSES else None
            if task is not None:
                task.cancel()
        if task is not None:
            await asyncio.gather(task, return_exceptions=True)
            await self._mark_cancelled_if_active(job_id)
        return await self.status(job_id)

    async def close(self) -> None:
        async with self._lock:
            self._closed = True
            tasks = [
                job.task
                for job in self._jobs.values()
                if job.task is not None and not job.task.done()
            ]
            for task in tasks:
                task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        async with self._lock:
            for job in self._jobs.values():
                if job.status not in TERMINAL_STATUSES:
                    job.status = JobStatus.CANCELLED
                    job.updated_at = self._clock()
                    self._record_evaluation(job, JobStatus.CANCELLED)
                    self._clear_pending(job)
                    self._publish_change(job)

    async def _run(
        self,
        job_id: UUID,
        request: UserRequest,
        conversation_id: UUID | None,
    ) -> None:
        await self._transition(job_id, JobStatus.RUNNING)
        try:
            if conversation_id is not None and self._conversations is not None:
                async with self._conversations.serialized(conversation_id):
                    history = await self._conversations.history(conversation_id)
                    invocation = await asyncio.wait_for(
                        self._invoke_graph(job_id, request, history),
                        timeout=self._execution_timeout_seconds,
                    )
                    if len(invocation.pending) > 1:
                        await self._transition(
                            job_id,
                            JobStatus.FAILED,
                            error_code="multiple_confirmations_unsupported",
                        )
                        return
                    if invocation.pending:
                        call, authorization = invocation.pending[0]
                        await self._mark_awaiting_confirmation(job_id, call, authorization)
                        return
                    if invocation.final_result is None:
                        raise ValueError("graph did not return a final agent result")
                    final_result = invocation.final_result
                    await self._set_job_model(job_id, final_result.model_id)
                    conversation_persisted = await self._record_exchange_after_result(
                        conversation_id,
                        user_content=request.text,
                        assistant_content=self._bounded_conversation_content(final_result.content),
                    )
            else:
                invocation = await asyncio.wait_for(
                    self._invoke_graph(job_id, request, ()),
                    timeout=self._execution_timeout_seconds,
                )
                if len(invocation.pending) > 1:
                    await self._transition(
                        job_id,
                        JobStatus.FAILED,
                        error_code="multiple_confirmations_unsupported",
                    )
                    return
                if invocation.pending:
                    call, authorization = invocation.pending[0]
                    await self._mark_awaiting_confirmation(job_id, call, authorization)
                    return
                if invocation.final_result is None:
                    raise ValueError("graph did not return a final agent result")
                final_result = invocation.final_result
                await self._set_job_model(job_id, final_result.model_id)
                conversation_persisted = None
            result = self._bounded_result(final_result.content)
            observers = []
            if self._owner_profile is not None:
                observers.append(self._owner_profile.observe(request))
            if self._social_memory is not None:
                observers.append(self._social_memory.observe(request))
            if observers:
                await asyncio.gather(*observers)
            await self._transition(
                job_id,
                JobStatus.COMPLETED,
                result=result,
                conversation_persisted=conversation_persisted,
            )
        except asyncio.CancelledError:
            await asyncio.shield(self._transition(job_id, JobStatus.CANCELLED))
            raise
        except TimeoutError:
            await self._transition(
                job_id,
                JobStatus.FAILED,
                error_code="swarm_execution_timeout",
            )
        except ConversationCapacityError:
            await self._transition(
                job_id,
                JobStatus.FAILED,
                error_code="conversation_capacity_reached",
            )
        except MemoryNotFoundError:
            await self._transition(
                job_id,
                JobStatus.FAILED,
                error_code="conversation_not_found",
            )
        except MemoryStoreError:
            await self._transition(
                job_id,
                JobStatus.FAILED,
                error_code="conversation_unavailable",
            )
        except Exception:
            await self._transition(
                job_id,
                JobStatus.FAILED,
                error_code="swarm_execution_failed",
            )

    async def _invoke_graph(
        self,
        job_id: UUID,
        request: UserRequest,
        conversation_history: tuple[ConversationTurn, ...],
    ) -> _GraphInvocation:
        state = await self._graph.ainvoke(
            {
                "request": request,
                "conversation_history": conversation_history,
                "stream_callback": lambda delta: self._publish_stream(job_id, delta),
            }
        )
        authorizations = state.get("tool_authorizations", ())
        if not isinstance(authorizations, tuple):
            raise ValueError("graph did not return valid tool state")
        specialist = state.get("specialist_result")
        if authorizations and not isinstance(specialist, AgentResult):
            raise ValueError("graph did not return valid tool state")
        calls = (
            {call.call_id: call for call in specialist.tool_calls}
            if isinstance(specialist, AgentResult)
            else {}
        )
        tool_name = next(iter(calls.values())).tool_name if len(calls) == 1 else None
        raw_tool_results = state.get("tool_results", ())
        if not isinstance(raw_tool_results, tuple) or not all(
            isinstance(result, ToolExecutionResult) for result in raw_tool_results
        ):
            raise ValueError("graph did not return valid tool results")
        action_verified = bool(
            tool_name is not None
            and len(raw_tool_results) == 1
            and raw_tool_results[0].tool_name == tool_name
            and raw_tool_results[0].success
            and raw_tool_results[0].metadata.get("verified", True) is True
        )
        if tool_name is not None:
            await self._set_job_tool(job_id, tool_name, verified=action_verified)
        pending: list[tuple[ToolCall, ToolAuthorization]] = []
        for authorization in authorizations:
            if not isinstance(authorization, ToolAuthorization):
                raise ValueError("graph returned an invalid authorization")
            if authorization.decision is not PolicyDecision.REQUIRE_CONFIRMATION:
                continue
            call = calls.get(authorization.call_id)
            if (
                call is None
                or call.tool_name != authorization.tool_name
                or call.digest() != authorization.call_digest
            ):
                raise ValueError("pending authorization does not match tool call")
            pending.append((call, authorization))
        final_result = state.get("final_result")
        if final_result is not None and not isinstance(final_result, AgentResult):
            raise ValueError("graph returned an invalid final result")
        if not pending and final_result is None:
            raise ValueError("graph did not return a final agent result")
        model_id = final_result.model_id if final_result is not None else None
        if model_id is None and isinstance(specialist, AgentResult):
            model_id = specialist.model_id
        if model_id is not None:
            await self._set_job_model(job_id, model_id)
        return _GraphInvocation(
            final_result=final_result,
            pending=tuple(pending),
            model_id=model_id,
            tool_name=tool_name,
        )

    async def _mark_awaiting_confirmation(
        self,
        job_id: UUID,
        call: ToolCall,
        authorization: ToolAuthorization,
    ) -> None:
        if call.tool_name not in CONFIRMED_TOOL_NAMES:
            await self._transition(
                job_id,
                JobStatus.FAILED,
                error_code="confirmation_tool_unsupported",
            )
            return
        summary = self._confirmation_summary(authorization)
        now = self._clock()
        async with self._lock:
            job = self._jobs[job_id]
            if job.status in TERMINAL_STATUSES:
                return
            job.status = JobStatus.AWAITING_CONFIRMATION
            job.updated_at = now
            job.confirmation = PendingToolConfirmation(
                call_digest=authorization.call_digest,
                tool_name=authorization.tool_name,
                summary=summary,
                expires_at=now + PENDING_CONFIRMATION_TTL,
            )
            job.pending_call = call
            job.pending_authorization = authorization
            job.tool_name = call.tool_name
            self._publish_change(job)

    async def _run_approved_tool(
        self,
        job_id: UUID,
        authorization: ToolAuthorization,
    ) -> None:
        context = self._policy_context
        executor = self._tool_executor
        if context is None or executor is None:
            await self._transition(
                job_id,
                JobStatus.FAILED,
                error_code="approved_tool_execution_failed",
            )
            return
        try:
            self._audit.record_authorization(self._jobs[job_id].request_id, authorization)
            result = await executor.execute_async(authorization, context)
            await self._set_job_tool(
                job_id,
                result.tool_name,
                verified=(
                    result.success and result.metadata.get("verified", True) is True
                ),
            )
            self._audit.record_execution(self._jobs[job_id].request_id, result)
            if not result.success:
                await self._transition(
                    job_id,
                    JobStatus.FAILED,
                    error_code="approved_tool_execution_failed",
                )
                return
            formatted_result = self._bounded_result(
                self._format_tool_result(result, authorization)
            )
            self._publish_stream(job_id, formatted_result)
            job = self._jobs[job_id]
            conversation_persisted = None
            if job.conversation_id is not None:
                if self._conversations is None:
                    raise JobError("conversation coordinator is unavailable")
                async with self._conversations.serialized(job.conversation_id):
                    conversation_persisted = await self._record_exchange_after_result(
                        job.conversation_id,
                        user_content=job.request_text,
                        assistant_content=self._bounded_conversation_content(formatted_result),
                    )
            await self._transition(
                job_id,
                JobStatus.COMPLETED,
                result=formatted_result,
                conversation_persisted=conversation_persisted,
            )
        except asyncio.CancelledError:
            await asyncio.shield(self._transition(job_id, JobStatus.CANCELLED))
            raise
        except Exception:
            await self._transition(
                job_id,
                JobStatus.FAILED,
                error_code="approved_tool_execution_failed",
            )

    @staticmethod
    def _confirmation_summary(authorization: ToolAuthorization) -> str:
        arguments = authorization.normalized_arguments
        if authorization.tool_name == "terminal_run_template":
            template = arguments.get("template")
            summaries = {
                "git_status": "Diagnóstico local: estado Git del workspace",
                "list_processes": "Diagnóstico local: inventario de procesos",
                "list_listeners": "Diagnóstico local: listeners TCP",
                "security_posture": "Diagnóstico local: postura de seguridad de macOS",
            }
            summary = summaries.get(template) if isinstance(template, str) else None
            if summary is None:
                raise ValueError("terminal confirmation arguments are invalid")
            return summary
        if authorization.tool_name == "network_discover_hosts":
            target = arguments.get("target")
            ports = arguments.get("ports")
            if not isinstance(target, str) or not isinstance(ports, list) or not ports:
                raise ValueError("confirmation arguments are invalid")
            port_text = ", ".join(str(port) for port in ports)
            return f"Sondeo TCP en {target}; puertos {port_text}"
        if authorization.tool_name == "mail_send_message":
            recipients = arguments.get("recipients")
            raw_subject = arguments.get("subject")
            if (
                not isinstance(recipients, list)
                or not recipients
                or not all(isinstance(value, str) for value in recipients)
                or not isinstance(raw_subject, str)
            ):
                raise ValueError("mail confirmation arguments are invalid")
            subject = SwarmJobManager._normalized_label(raw_subject, 300)
            summary = f"Enviar correo a {', '.join(recipients)}; asunto: {subject}"
            return summary[:512]
        if authorization.tool_name == "calendar_create_event":
            raw_title = arguments.get("title")
            start_at = arguments.get("start_at")
            end_at = arguments.get("end_at")
            if not all(isinstance(value, str) for value in (raw_title, start_at, end_at)):
                raise ValueError("calendar confirmation arguments are invalid")
            title = SwarmJobManager._normalized_label(raw_title, 300)
            summary = f"Crear evento «{title}»; {start_at} — {end_at}"
            return summary[:512]
        if authorization.tool_name == "reminder_create":
            title = SwarmJobManager._normalized_label(arguments.get("title"), 300)
            list_name = arguments.get("list_name")
            due_at = arguments.get("due_at")
            summary = f"Crear recordatorio «{title}»"
            if isinstance(list_name, str):
                summary += f" en «{SwarmJobManager._normalized_label(list_name, 128)}»"
            if isinstance(due_at, str):
                summary += f"; vence: {due_at}"
            return summary[:512]
        if authorization.tool_name == "reminder_complete":
            title = SwarmJobManager._normalized_label(arguments.get("title"), 300)
            list_name = arguments.get("list_name")
            summary = f"Completar recordatorio «{title}»"
            if isinstance(list_name, str):
                summary += f" en «{SwarmJobManager._normalized_label(list_name, 128)}»"
            return summary[:512]
        if authorization.tool_name == "contact_create":
            first_name = SwarmJobManager._normalized_label(
                arguments.get("first_name"), 100
            )
            last_name = arguments.get("last_name")
            name = first_name
            if isinstance(last_name, str) and last_name:
                name += f" {SwarmJobManager._normalized_label(last_name, 100)}"
            summary = f"Crear contacto «{name}»"
            email = arguments.get("email")
            phone = arguments.get("phone")
            if isinstance(email, str):
                summary += f"; correo: {email}"
            if isinstance(phone, str):
                summary += f"; teléfono: {phone}"
            return summary[:512]
        if authorization.tool_name == "system_audio_set":
            volume = arguments.get("volume_percent")
            muted = arguments.get("muted")
            changes = []
            if type(volume) is int and 0 <= volume <= 100:
                changes.append(f"volumen al {volume} %")
            if type(muted) is bool:
                changes.append("silenciar" if muted else "activar sonido")
            if not changes:
                raise ValueError("audio confirmation arguments are invalid")
            return "Cambiar audio del Mac: " + ", ".join(changes)
        if authorization.tool_name == "media_control":
            action = {
                "play_pause": "alternar reproducción y pausa",
                "next": "siguiente pista",
                "previous": "pista anterior",
            }.get(arguments.get("action"))
            if action is None:
                raise ValueError("media confirmation arguments are invalid")
            return f"Control multimedia: {action}"
        if authorization.tool_name == "spotlight_open":
            query = SwarmJobManager._normalized_label(arguments.get("query"), 200)
            return f"Abrir resultado exacto de Spotlight: {query}"[:512]
        if authorization.tool_name == "browser_open_url":
            url = arguments.get("url")
            if not isinstance(url, str):
                raise ValueError("browser confirmation arguments are invalid")
            return f"Abrir en el navegador: {url}"[:512]
        if authorization.tool_name == "application_open":
            bundle_identifier = arguments.get("bundle_identifier")
            if not isinstance(bundle_identifier, str):
                raise ValueError("application confirmation arguments are invalid")
            return f"Abrir aplicación: {bundle_identifier}"[:512]
        if authorization.tool_name == "shortcut_run":
            name = arguments.get("name")
            if not isinstance(name, str):
                raise ValueError("shortcut confirmation arguments are invalid")
            return f"Ejecutar atajo de macOS: {name}"[:512]
        if authorization.tool_name == "computer_use":
            objective = arguments.get("objective")
            bundle_identifier = arguments.get("application_bundle_identifier")
            max_steps = arguments.get("max_steps")
            if (
                not isinstance(objective, str)
                or not isinstance(bundle_identifier, str)
                or not isinstance(max_steps, int)
            ):
                raise ValueError("computer confirmation arguments are invalid")
            return (
                f"Control visual de {bundle_identifier}; hasta {max_steps} pasos; "
                f"objetivo: {objective}"
            )[:512]
        raise ValueError("confirmation tool is unsupported")

    @staticmethod
    def _format_tool_result(
        result: ToolExecutionResult,
        authorization: ToolAuthorization | None = None,
    ) -> str:
        if result.tool_name == "mail_send_message":
            payload = json.loads(result.output)
            sent = payload.get("sent")
            recipient_count = payload.get("recipient_count")
            if (
                sent is not True
                or type(recipient_count) is not int
                or not 1 <= recipient_count <= 10
            ):
                raise ValueError("mail result is invalid")
            return f"Correo enviado a {recipient_count} destinatario(s)."
        if result.tool_name == "calendar_create_event":
            payload = json.loads(result.output)
            created = payload.get("created")
            calendar = SwarmJobManager._normalized_label(payload.get("calendar"), 200)
            title = SwarmJobManager._normalized_label(payload.get("title"), 300)
            if created is not True:
                raise ValueError("calendar result is invalid")
            return f"Evento «{title}» creado en «{calendar}»."
        if result.tool_name in {"reminder_create", "reminder_complete"}:
            payload = json.loads(result.output)
            expected_state = "created" if result.tool_name == "reminder_create" else "completed"
            title = SwarmJobManager._normalized_label(payload.get("title"), 300)
            list_name = SwarmJobManager._normalized_label(payload.get("list"), 128)
            if payload.get(expected_state) is not True:
                raise ValueError("reminder result is invalid")
            if authorization is not None and title != SwarmJobManager._normalized_label(
                authorization.normalized_arguments.get("title"), 300
            ):
                raise ValueError("reminder result does not match authorization")
            action = "creado" if result.tool_name == "reminder_create" else "completado"
            return f"Recordatorio «{title}» {action} en «{list_name}»."
        if result.tool_name == "contact_create":
            payload = json.loads(result.output)
            name = SwarmJobManager._normalized_label(payload.get("name"), 300)
            if payload.get("created") is not True:
                raise ValueError("contact result is invalid")
            return f"Contacto «{name}» creado."
        if result.tool_name == "system_audio_set":
            payload = json.loads(result.output)
            muted = payload.get("output_muted")
            volume = payload.get("output_volume_percent")
            if type(muted) is not bool or type(volume) is not int or not 0 <= volume <= 100:
                raise ValueError("audio result is invalid")
            if authorization is not None:
                expected_volume = authorization.normalized_arguments.get("volume_percent")
                expected_muted = authorization.normalized_arguments.get("muted")
                if (expected_volume is not None and volume != expected_volume) or (
                    expected_muted is not None and muted != expected_muted
                ):
                    raise ValueError("audio result does not match authorization")
            state = "silenciado" if muted else "con sonido"
            return f"Audio del Mac {state}, volumen al {volume} %."
        if result.tool_name == "media_control":
            payload = json.loads(result.output)
            action = payload.get("action")
            bundle_identifier = payload.get("bundle_identifier")
            expected_action = (
                authorization.normalized_arguments.get("action")
                if authorization is not None
                else action
            )
            rendered = {
                "play_pause": "Alterné reproducción y pausa.",
                "next": "Pasé a la siguiente pista.",
                "previous": "Volví a la pista anterior.",
            }.get(action)
            if (
                rendered is None
                or action != expected_action
                or bundle_identifier not in {"com.apple.Music", "com.spotify.client"}
            ):
                raise ValueError("media result is invalid")
            return rendered
        if result.tool_name == "spotlight_open":
            payload = json.loads(result.output)
            name = SwarmJobManager._normalized_label(payload.get("name"), 500)
            if payload.get("opened") is not True:
                raise ValueError("Spotlight result is invalid")
            return f"Abrí {name} desde Spotlight."
        if result.tool_name == "browser_open_url":
            payload = json.loads(result.output)
            url = payload.get("url")
            if payload.get("opened") is not True or not isinstance(url, str):
                raise ValueError("browser result is invalid")
            return f"URL abierta en el navegador: {url}"
        if result.tool_name == "application_open":
            payload = json.loads(result.output)
            bundle_identifier = payload.get("bundle_identifier")
            if payload.get("opened") is not True or not isinstance(bundle_identifier, str):
                raise ValueError("application result is invalid")
            return f"Aplicación abierta: {bundle_identifier}"
        if result.tool_name == "shortcut_run":
            payload = json.loads(result.output)
            name = payload.get("name")
            if payload.get("completed") is not True or not isinstance(name, str):
                raise ValueError("shortcut result is invalid")
            return f"Atajo «{name}» ejecutado."
        if result.tool_name == "computer_use":
            payload = json.loads(result.output)
            status = payload.get("status")
            steps = payload.get("steps")
            bundle_identifier = payload.get("application_bundle_identifier")
            reason_code = payload.get("reason_code")
            if (
                status not in {"completed", "blocked", "step_limit"}
                or not isinstance(steps, int)
                or not isinstance(bundle_identifier, str)
                or not isinstance(reason_code, str)
            ):
                raise ValueError("computer result is invalid")
            if status == "completed":
                return f"Control visual completado en {bundle_identifier} tras {steps} paso(s)."
            if status == "step_limit":
                return (
                    f"Jarvis se detuvo en {bundle_identifier} al alcanzar el límite de "
                    f"{steps} pasos sin verificar el objetivo."
                )
            reasons = {
                "sensitive_action": "la siguiente acción era sensible",
                "unsupported_action": "la siguiente acción no está permitida",
                "uncertain_state": "no pudo verificar el estado visual con seguridad",
            }
            reason = reasons.get(reason_code)
            if reason is None:
                raise ValueError("computer result reason is invalid")
            return f"Jarvis detuvo el control visual porque {reason}."
        if result.tool_name == "terminal_run_template":
            template = result.metadata.get("template")
            if template == "security_posture":
                payload = json.loads(result.output)
                controls = (
                    ("sip", "SIP"),
                    ("gatekeeper", "Gatekeeper"),
                    ("filevault", "FileVault"),
                    ("firewall", "Firewall"),
                )
                translations = {
                    "enabled": "activado",
                    "disabled": "desactivado",
                    "unavailable": "no disponible",
                }
                if not isinstance(payload, dict) or set(payload) != {name for name, _ in controls}:
                    raise ValueError("security posture result is invalid")
                lines = []
                for name, title in controls:
                    state = payload.get(name)
                    translated = translations.get(state) if isinstance(state, str) else None
                    if translated is None:
                        raise ValueError("security posture state is invalid")
                    lines.append(f"{title}: {translated}")
                return "Postura de seguridad de macOS:\n" + "\n".join(lines)
            headings = {
                "git_status": "Estado Git",
                "list_processes": "Procesos locales",
                "list_listeners": "Listeners TCP locales",
                "security_posture": "Postura de seguridad de macOS",
            }
            heading = headings.get(template) if isinstance(template, str) else None
            if heading is None:
                raise ValueError("terminal result is invalid")
            output = result.output.strip()
            suffix = "\nSalida truncada por política." if result.metadata.get("truncated") else ""
            if not output:
                return f"{heading}: sin resultados.{suffix}"
            return f"{heading}:\n{output}{suffix}"
        if result.tool_name != "network_discover_hosts":
            raise ValueError("approved tool result is unsupported")
        payload = json.loads(result.output)
        target = payload.get("target")
        hosts = payload.get("hosts")
        if not isinstance(target, str) or not isinstance(hosts, list):
            raise ValueError("network result is invalid")
        if not hosts:
            return f"Sondeo TCP completado en {target}. No se observaron hosts con respuesta."
        observations: list[str] = []
        for host in hosts:
            if not isinstance(host, dict):
                raise ValueError("network host result is invalid")
            address = host.get("address")
            ports = host.get("open_ports")
            if not isinstance(address, str) or not isinstance(ports, list):
                raise ValueError("network host result is invalid")
            port_text = ", ".join(str(port) for port in ports) if ports else "sin puertos abiertos"
            observations.append(f"{address}: {port_text}")
        return f"Sondeo TCP completado en {target}. " + "; ".join(observations)

    @staticmethod
    def _normalized_label(value: object, max_characters: int) -> str:
        if not isinstance(value, str):
            raise ValueError("result label is invalid")
        normalized = " ".join(value.split())
        if not normalized or len(normalized) > max_characters or not normalized.isprintable():
            raise ValueError("result label is invalid")
        return normalized

    async def _record_exchange_after_result(
        self,
        conversation_id: UUID,
        *,
        user_content: str,
        assistant_content: str,
    ) -> bool:
        if self._conversations is None:
            raise JobError("conversation coordinator is unavailable")
        persistence = asyncio.create_task(
            self._conversations.record_exchange(
                conversation_id,
                user_content=user_content,
                assistant_content=assistant_content,
            )
        )
        try:
            return await asyncio.shield(persistence)
        except asyncio.CancelledError:
            return await persistence

    async def _transition(
        self,
        job_id: UUID,
        status: JobStatus,
        *,
        result: str | None = None,
        error_code: str | None = None,
        conversation_persisted: bool | None = None,
    ) -> None:
        async with self._lock:
            job = self._jobs[job_id]
            if job.status in TERMINAL_STATUSES:
                return
            job.status = status
            job.updated_at = self._clock()
            job.result = result
            job.error_code = error_code
            job.conversation_persisted = conversation_persisted
            if status in TERMINAL_STATUSES:
                if result is not None and not job.partial_result:
                    self._publish_stream_locked(job, result)
                self._record_evaluation(job, status)
                if status is JobStatus.COMPLETED:
                    self._apply_owner_feedback_locked(job)
            self._clear_pending(job)
            self._publish_change(job)

    async def _mark_cancelled_if_active(self, job_id: UUID) -> None:
        async with self._lock:
            job = self._jobs[job_id]
            if job.status not in TERMINAL_STATUSES:
                job.status = JobStatus.CANCELLED
                job.updated_at = self._clock()
                self._record_evaluation(job, JobStatus.CANCELLED)
                self._clear_pending(job)
                self._publish_change(job)

    def _expire_pending_confirmations(self, now: datetime) -> None:
        for job in self._jobs.values():
            if (
                job.status is JobStatus.AWAITING_CONFIRMATION
                and job.confirmation is not None
                and job.confirmation.expires_at <= now
            ):
                job.status = JobStatus.FAILED
                job.updated_at = now
                job.error_code = "confirmation_expired"
                self._record_evaluation(job, JobStatus.FAILED)
                self._clear_pending(job)
                self._publish_change(job)

    def _publish_stream(self, job_id: UUID, delta: str) -> None:
        if not isinstance(delta, str) or not delta:
            return
        job = self._jobs.get(job_id)
        if job is None or job.status in TERMINAL_STATUSES:
            return
        self._publish_stream_locked(job, delta)

    def _publish_stream_locked(self, job: _Job, delta: str) -> None:
        combined = (job.partial_result or "") + delta
        bounded = self._bounded_result(combined)
        if bounded == job.partial_result:
            return
        if job.first_partial_monotonic is None:
            job.first_partial_monotonic = time.monotonic()
        job.partial_result = bounded
        job.stream_chunks += 1
        job.stream_version = min(job.stream_version + 1, 100_000)
        self._publish_change(job)

    @staticmethod
    def _publish_change(job: _Job) -> None:
        pending = job.change_event
        job.change_event = asyncio.Event()
        pending.set()

    async def _set_job_model(self, job_id: UUID, model_id: str) -> None:
        async with self._lock:
            job = self._jobs.get(job_id)
            if job is not None:
                job.model_id = model_id[:256]

    async def _set_job_tool(
        self,
        job_id: UUID,
        tool_name: str,
        *,
        verified: bool = False,
    ) -> None:
        async with self._lock:
            job = self._jobs.get(job_id)
            if job is not None:
                job.tool_name = tool_name
                job.action_verified = verified

    def _apply_owner_feedback_locked(self, feedback_job: _Job) -> None:
        if (
            feedback_job.feedback_target_id is None
            or feedback_job.feedback_to_apply is None
        ):
            return
        target = self._jobs.get(feedback_job.feedback_target_id)
        if target is None or target.evaluation is None:
            return
        target.evaluation = target.evaluation.model_copy(
            update={"owner_feedback": feedback_job.feedback_to_apply}
        )
        if feedback_job.feedback_to_apply is OwnerFeedback.UNHELPFUL:
            self._repair_windows[feedback_job.conversation_id] = (
                self._clock() + REPAIR_WINDOW_TTL
            )
        else:
            self._repair_windows.pop(feedback_job.conversation_id, None)
        if self._evaluation_store is not None:
            try:
                self._evaluation_store.append(
                    target.job_id,
                    target.evaluation,
                    self._clock(),
                )
            except Exception:
                LOGGER.warning("owner_feedback_write_failed")

    def _expire_repair_windows(self, now: datetime) -> None:
        expired = [
            conversation_id
            for conversation_id, expires_at in self._repair_windows.items()
            if expires_at <= now
        ]
        for conversation_id in expired:
            self._repair_windows.pop(conversation_id, None)

    def _latest_feedback_target(self, conversation_id: UUID | None) -> UUID | None:
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

    @staticmethod
    def _evaluate(job: _Job, status: JobStatus) -> JobEvaluation:
        model_id = job.model_id
        if model_id == "apple/system-language-model":
            brain = BrainTarget.LOCAL
        elif model_id is not None and model_id.startswith("local/deterministic-"):
            brain = BrainTarget.DETERMINISTIC
        elif model_id:
            brain = BrainTarget.NVIDIA
        elif job.tool_name:
            brain = BrainTarget.DETERMINISTIC
        else:
            brain = BrainTarget.UNKNOWN
        finished = time.monotonic()
        first_partial = (
            round((job.first_partial_monotonic - job.started_monotonic) * 1_000)
            if job.first_partial_monotonic is not None
            else None
        )
        conversation_quality = (
            _CONVERSATION_QUALITY_EVALUATOR.evaluate(
                request=job.request_text,
                response=job.result,
            )
            if status is JobStatus.COMPLETED
            and job.tool_name is None
            and job.result
            and not job.owner_feedback_request
            else None
        )
        return JobEvaluation(
            brain=brain,
            model_id=model_id,
            total_latency_ms=max(0, round((finished - job.started_monotonic) * 1_000)),
            first_partial_latency_ms=max(0, first_partial) if first_partial is not None else None,
            stream_chunks=job.stream_chunks,
            tool_name=job.tool_name,
            succeeded=status is JobStatus.COMPLETED,
            outcome_verified=(
                status is JobStatus.COMPLETED
                and (job.tool_name is None or job.action_verified)
            ),
            voice_request=job.voice_request,
            owner_verified=job.owner_verified,
            dialogue_mode=(
                conversation_quality.dialogue_mode if conversation_quality is not None else None
            ),
            response_quality_score=(
                conversation_quality.score if conversation_quality is not None else None
            ),
            response_quality_passed=(
                conversation_quality.passed if conversation_quality is not None else None
            ),
            response_word_count=(
                conversation_quality.word_count if conversation_quality is not None else None
            ),
            response_sentence_count=(
                conversation_quality.sentence_count if conversation_quality is not None else None
            ),
            response_quality_flags=(
                conversation_quality.flags if conversation_quality is not None else ()
            ),
            feedback_event=job.owner_feedback_request,
        )

    def _record_evaluation(self, job: _Job, status: JobStatus) -> None:
        evaluation = self._evaluate(job, status)
        job.evaluation = evaluation
        if self._evaluation_store is None:
            return
        try:
            self._evaluation_store.append(job.job_id, evaluation, job.updated_at)
        except Exception:
            LOGGER.warning("evaluation_history_write_failed")

    @staticmethod
    def _percentile(values: list[int], fraction: float) -> int | None:
        if not values:
            return None
        index = max(0, min(len(values) - 1, math.ceil(len(values) * fraction) - 1))
        return values[index]

    @staticmethod
    def _clear_pending(job: _Job) -> None:
        job.confirmation = None
        job.pending_call = None
        job.pending_authorization = None

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

    @staticmethod
    def _bounded_result(content: str) -> str:
        if len(json.dumps(content, ensure_ascii=False).encode("utf-8")) <= MAX_JOB_RESULT_BYTES:
            return content
        lower = 0
        upper = len(content)
        while lower < upper:
            midpoint = (lower + upper + 1) // 2
            size = len(json.dumps(content[:midpoint], ensure_ascii=False).encode("utf-8"))
            if size <= MAX_JOB_RESULT_BYTES:
                lower = midpoint
            else:
                upper = midpoint - 1
        return content[:lower]

    @staticmethod
    def _bounded_conversation_content(content: str) -> str:
        encoded = content.encode("utf-8")
        if len(encoded) <= MAX_MEMORY_CONTENT_BYTES:
            return content
        return encoded[:MAX_MEMORY_CONTENT_BYTES].decode("utf-8", errors="ignore")


class SubmitJobPayload(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    text: str = Field(min_length=1, max_length=50_000)
    modalities: frozenset[InputModality] = Field(
        default_factory=lambda: frozenset({InputModality.TEXT})
    )
    conversation_id: UUID | None = None

    @model_validator(mode="after")
    def conversation_text_must_fit_persistent_limit(self) -> SubmitJobPayload:
        if (
            self.conversation_id is not None
            and len(self.text.encode("utf-8")) > MAX_MEMORY_CONTENT_BYTES
        ):
            raise ValueError("conversation request exceeds persistent turn limit")
        return self


class JobIdPayload(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    job_id: UUID


class JobWaitPayload(JobIdPayload):
    model_config = ConfigDict(extra="forbid", frozen=True)

    after_stream_version: int = Field(ge=0, le=100_000)
    timeout_milliseconds: int = Field(ge=100, le=MAX_JOB_WAIT_SECONDS * 1_000)


class JobApprovalPayload(JobIdPayload):
    call_digest: str = Field(pattern=r"^[0-9a-f]{64}$")


class VoiceSubmitPayload(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    transcript: LocalTranscriptEvent
    conversation_id: UUID | None = None


class ImageSubmitPayload(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    text: str = Field(min_length=1, max_length=4_096)
    image: ImageInput
    conversation_id: UUID | None = None

    @model_validator(mode="after")
    def conversation_text_must_fit_persistent_limit(self) -> ImageSubmitPayload:
        payload_bytes = len(
            json.dumps(
                self.model_dump(mode="json"),
                ensure_ascii=False,
                separators=(",", ":"),
            ).encode("utf-8")
        )
        if payload_bytes > MAX_IMAGE_SUBMIT_PAYLOAD_BYTES:
            raise ValueError("image request exceeds IPC payload limit")
        if (
            self.conversation_id is not None
            and len(self.text.encode("utf-8")) > MAX_MEMORY_CONTENT_BYTES
        ):
            raise ValueError("conversation request exceeds persistent turn limit")
        return self


class SwarmIpcService:
    WAIT_METHOD = "jobs.wait"
    MAX_WAIT_SECONDS = MAX_JOB_WAIT_SECONDS
    METHODS = frozenset(
        {
            "swarm.submit",
            "voice.submit",
            "image.submit",
            "jobs.status",
            WAIT_METHOD,
            "jobs.cancel",
            "jobs.approve",
            "jobs.metrics",
        }
    )

    def __init__(self, jobs: SwarmJobManager) -> None:
        self._jobs = jobs
        self._voice_capture_ids: deque[UUID] = deque(maxlen=MAX_RECENT_VOICE_CAPTURES)

    def handlers(self) -> dict[str, IpcMethodHandler]:
        return {method: self.handle for method in self.METHODS}

    async def handle(self, request: IpcRequest) -> IpcHandlerResult:
        try:
            if request.method == "jobs.metrics":
                if request.payload:
                    return IpcHandlerResult(ok=False, error_code="invalid_payload")
                return IpcHandlerResult(ok=True, payload=await self._jobs.metrics())
            if request.method in {"swarm.submit", "voice.submit", "image.submit"}:
                image = None
                voice_capture_id = None
                if request.method == "voice.submit":
                    voice_payload = VoiceSubmitPayload.model_validate(request.payload)
                    text = voice_payload.transcript.text
                    voice_capture_id = voice_payload.transcript.capture_id
                    modalities = frozenset({InputModality.TEXT, InputModality.AUDIO})
                    conversation_id = voice_payload.conversation_id
                    voice_metadata = {
                        "speech_capture_id": str(voice_payload.transcript.capture_id),
                        "speech_locale": voice_payload.transcript.locale_identifier,
                        "speech_on_device": True,
                        **(
                            {
                                "speaker_identity": {
                                    "confidence": voice_payload.transcript.speaker_confidence,
                                    "id": voice_payload.transcript.speaker_id,
                                }
                            }
                            if voice_payload.transcript.speaker_id is not None
                            else {}
                        ),
                        "sole_speaker_profile": voice_payload.transcript.sole_speaker_profile,
                    }
                elif request.method == "image.submit":
                    image_payload = ImageSubmitPayload.model_validate(request.payload)
                    text = image_payload.text
                    modalities = frozenset({InputModality.TEXT, InputModality.IMAGE})
                    conversation_id = image_payload.conversation_id
                    image = image_payload.image
                    voice_metadata = {}
                else:
                    payload = SubmitJobPayload.model_validate(request.payload)
                    text = payload.text
                    modalities = payload.modalities
                    conversation_id = payload.conversation_id
                    voice_metadata = {}
                if contains_likely_secret_material(text):
                    return IpcHandlerResult(
                        ok=False,
                        error_code="secret_material_rejected",
                    )
                if voice_capture_id is not None:
                    if voice_capture_id in self._voice_capture_ids:
                        return IpcHandlerResult(
                            ok=False,
                            error_code="voice_capture_replayed",
                        )
                    self._voice_capture_ids.append(voice_capture_id)
                user_request = UserRequest(
                    text=text,
                    modalities=modalities,
                    image=image,
                    metadata={
                        "ipc_request_id": str(request.request_id),
                        **voice_metadata,
                        **(
                            {"conversation_id": str(conversation_id)}
                            if conversation_id is not None
                            else {}
                        ),
                    },
                )
                snapshot = await self._jobs.submit(
                    user_request,
                    conversation_id=conversation_id,
                )
            else:
                if request.method == "jobs.approve":
                    approval = JobApprovalPayload.model_validate(request.payload)
                    snapshot = await self._jobs.approve(
                        approval.job_id,
                        approval.call_digest,
                    )
                elif request.method == self.WAIT_METHOD:
                    wait = JobWaitPayload.model_validate(request.payload)
                    snapshot = await self._jobs.wait_for_change(
                        wait.job_id,
                        after_stream_version=wait.after_stream_version,
                        timeout_seconds=wait.timeout_milliseconds / 1_000,
                    )
                else:
                    payload = JobIdPayload.model_validate(request.payload)
                    if request.method == "jobs.status":
                        snapshot = await self._jobs.status(payload.job_id)
                    elif request.method == "jobs.cancel":
                        snapshot = await self._jobs.cancel(payload.job_id)
                    else:
                        return IpcHandlerResult(ok=False, error_code="method_not_found")
        except ValueError:
            return IpcHandlerResult(ok=False, error_code="invalid_payload")
        except JobNotFoundError:
            return IpcHandlerResult(ok=False, error_code="job_not_found")
        except JobConversationNotFoundError:
            return IpcHandlerResult(ok=False, error_code="conversation_not_found")
        except JobCapacityError:
            return IpcHandlerResult(ok=False, error_code="job_capacity_reached")
        except JobConfirmationError:
            return IpcHandlerResult(ok=False, error_code="confirmation_unavailable")
        except JobError:
            return IpcHandlerResult(ok=False, error_code="job_manager_unavailable")
        return IpcHandlerResult(ok=True, payload=snapshot.model_dump(mode="json"))
