from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from uuid import UUID, uuid4

from aegis_core.contracts import ToolAuthorization, ToolCall
from aegis_core.job_admission import (
    JobAdmissionCoordinator,
    PreparedJobAdmission,
    ResolvedJobAdmission,
)
from aegis_core.job_authorization import ConfirmationContext
from aegis_core.job_contracts import (
    PENDING_CONFIRMATION_TTL,
    TERMINAL_STATUSES,
    EvaluationStore,
    JobConfirmationError,
    JobSnapshot,
    JobStatus,
    PendingToolConfirmation,
    StoredJobEvaluation,
)
from aegis_core.job_execution import bound_job_result
from aegis_core.job_graph import GraphInvocation
from aegis_core.job_metrics import build_job_metrics, evaluate_job
from aegis_core.job_state import JobRegistry, MutableJob

LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class ApprovedToolContext:
    request_id: UUID
    request_text: str
    conversation_id: UUID | None


class JobLifecycleCoordinator:
    """Single mutation boundary for bounded in-memory jobs.

    The caller owns synchronization. Every method is deliberately synchronous so
    one manager lock can cover compound authorization and lifecycle operations
    without nested locks or hidden suspension points.
    """

    def __init__(
        self,
        registry: JobRegistry,
        *,
        admission: JobAdmissionCoordinator,
        evaluation_store: EvaluationStore | None,
        clock: Callable[[], datetime],
        monotonic_clock: Callable[[], float],
    ) -> None:
        self._registry = registry
        self._admission = admission
        self._evaluation_store = evaluation_store
        self._clock = clock
        self._monotonic = monotonic_clock

    def reserve_capacity(self) -> None:
        self._registry.reserve_capacity()

    def prepare_admission(
        self,
        resolved: ResolvedJobAdmission,
        *,
        now: datetime,
    ) -> PreparedJobAdmission:
        return self._admission.prepare(resolved, registry=self._registry, now=now)

    def create(self, prepared: PreparedJobAdmission, *, now: datetime) -> JobSnapshot:
        job = MutableJob(
            job_id=uuid4(),
            request_id=prepared.request.request_id,
            authorization_request=prepared.authorization_request,
            request_text=prepared.request.text,
            conversation_id=prepared.conversation_id,
            status=JobStatus.QUEUED,
            created_at=now,
            updated_at=now,
            voice_request=prepared.voice_request,
            owner_verified=prepared.owner_verified,
            owner_feedback_request=prepared.owner_feedback_request,
            repair_attempt=prepared.repair_attempt,
            feedback_target_id=prepared.feedback_target_id,
            feedback_to_apply=prepared.feedback_to_apply,
            started_monotonic=self._monotonic(),
        )
        self._registry.add(job)
        return job.snapshot()

    def attach_task(self, job_id: UUID, task: asyncio.Task[None]) -> None:
        job = self._registry.require(job_id)
        # Approval may arrive after the state event fires but before the graph
        # coroutine returns; replacing that completed path matches the serialized
        # state transition and keeps the new approved execution cancellable.
        job.task = task

    def snapshot(self, job_id: UUID) -> JobSnapshot:
        return self._registry.require(job_id).snapshot()

    def wait_state(self, job_id: UUID) -> tuple[JobSnapshot, asyncio.Event]:
        job = self._registry.require(job_id)
        return job.snapshot(), job.change_event

    def expire_confirmations(self, now: datetime) -> None:
        for job in self._registry.expire_confirmations(now):
            self._finalize_terminal(job, JobStatus.FAILED)

    def confirmation_context(self, job_id: UUID) -> ConfirmationContext:
        job = self._registry.require(job_id)
        if (
            job.status is not JobStatus.AWAITING_CONFIRMATION
            or job.confirmation is None
            or job.pending_call is None
            or job.pending_authorization is None
        ):
            raise JobConfirmationError("confirmation is unavailable")
        return ConfirmationContext(
            call=job.pending_call,
            authorization=job.pending_authorization,
            authorization_request=job.authorization_request,
            expected_call_digest=job.confirmation.call_digest,
        )

    def fail_confirmation_consumption(self, job_id: UUID, *, now: datetime) -> None:
        self.transition(
            job_id,
            JobStatus.FAILED,
            now=now,
            error_code="confirmation_consumption_failed",
        )

    def resume_after_confirmation(self, job_id: UUID, *, now: datetime) -> None:
        job = self._registry.require(job_id)
        job.finish_confirmation_wait(self._monotonic)
        if not job.transition(JobStatus.RUNNING, now=now):
            raise JobConfirmationError("confirmation is unavailable")
        job.confirmation = None
        job.publish_change()

    def awaiting_confirmation(
        self,
        job_id: UUID,
        call: ToolCall,
        authorization: ToolAuthorization,
        *,
        summary: str,
        now: datetime,
    ) -> None:
        job = self._registry.require(job_id)
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
        job.confirmation_started_monotonic = self._monotonic()
        job.publish_change()

    def approved_tool_context(self, job_id: UUID) -> ApprovedToolContext:
        job = self._registry.require(job_id)
        return ApprovedToolContext(
            request_id=job.request_id,
            request_text=job.request_text,
            conversation_id=job.conversation_id,
        )

    def cancellable_task(self, job_id: UUID) -> asyncio.Task[None] | None:
        job = self._registry.require(job_id)
        return job.task if job.status not in TERMINAL_STATUSES else None

    def active_tasks(self) -> tuple[asyncio.Task[None], ...]:
        return self._registry.active_tasks()

    def close_active(self) -> None:
        for job in self._registry.values():
            if job.status not in TERMINAL_STATUSES:
                self._transition_job(job, JobStatus.CANCELLED, now=self._clock())

    def transition(
        self,
        job_id: UUID,
        status: JobStatus,
        *,
        now: datetime,
        result: str | None = None,
        error_code: str | None = None,
        conversation_persisted: bool | None = None,
    ) -> bool:
        return self._transition_job(
            self._registry.require(job_id),
            status,
            now=now,
            result=result,
            error_code=error_code,
            conversation_persisted=conversation_persisted,
        )

    def cancel_if_active(self, job_id: UUID, *, now: datetime) -> bool:
        return self.transition(job_id, JobStatus.CANCELLED, now=now)

    def record_invocation(self, job_id: UUID, invocation: GraphInvocation) -> None:
        job = self._registry.get(job_id)
        if job is None:
            return
        if invocation.tool_name is not None:
            job.tool_name = invocation.tool_name
            job.action_verified = invocation.action_verified
        if invocation.model_id is not None:
            job.model_id = invocation.model_id[:256]

    def record_tool(self, job_id: UUID, tool_name: str, *, verified: bool) -> None:
        job = self._registry.get(job_id)
        if job is None:
            return
        job.tool_name = tool_name
        job.action_verified = verified

    def publish_stream(self, job_id: UUID, delta: str) -> None:
        if not isinstance(delta, str) or not delta:
            return
        job = self._registry.get(job_id)
        if job is None or job.status in TERMINAL_STATUSES:
            return
        job.publish_stream(
            delta,
            bound=bound_job_result,
            monotonic_clock=self._monotonic,
        )

    def remember_public_sources(
        self,
        job_id: UUID,
        urls: tuple[str, ...],
        *,
        now: datetime,
    ) -> None:
        job = self._registry.get(job_id)
        if job is not None:
            self._admission.remember_public_sources(job, urls, now=now)

    def current_evaluations(self) -> tuple[StoredJobEvaluation, ...]:
        return tuple(
            StoredJobEvaluation(
                job_id=job.job_id,
                recorded_at=job.updated_at,
                evaluation=job.evaluation,
            )
            for job in self._registry.values()
            if job.evaluation is not None
        )

    def build_metrics(
        self,
        current: tuple[StoredJobEvaluation, ...],
    ) -> dict[str, object]:
        stored: tuple[StoredJobEvaluation, ...] = ()
        if self._evaluation_store is not None:
            try:
                stored = self._evaluation_store.load_recent()
            except Exception:
                LOGGER.warning("evaluation_history_read_failed")
        by_job_id = {item.job_id: item.evaluation for item in stored}
        by_job_id.update({item.job_id: item.evaluation for item in current})
        return build_job_metrics(by_job_id.values())

    def _transition_job(
        self,
        job: MutableJob,
        status: JobStatus,
        *,
        now: datetime,
        result: str | None = None,
        error_code: str | None = None,
        conversation_persisted: bool | None = None,
    ) -> bool:
        if not job.transition(
            status,
            now=now,
            result=result,
            error_code=error_code,
            conversation_persisted=conversation_persisted,
        ):
            return False
        if status in TERMINAL_STATUSES:
            if result is not None and not job.partial_result:
                job.publish_stream(
                    result,
                    bound=bound_job_result,
                    monotonic_clock=self._monotonic,
                )
            self._finalize_terminal(job, status)
        else:
            job.clear_pending()
            job.publish_change()
        return True

    def _finalize_terminal(self, job: MutableJob, status: JobStatus) -> None:
        self._record_evaluation(job, status)
        if status is JobStatus.COMPLETED:
            self._admission.apply_owner_feedback(
                job,
                registry=self._registry,
                evaluation_store=self._evaluation_store,
                now=self._clock(),
            )
        job.clear_pending()
        job.publish_change()

    def _record_evaluation(self, job: MutableJob, status: JobStatus) -> None:
        job.finish_confirmation_wait(self._monotonic)
        evaluation = evaluate_job(job, status, monotonic_clock=self._monotonic)
        job.evaluation = evaluation
        if self._evaluation_store is None:
            return
        try:
            self._evaluation_store.append(job.job_id, evaluation, job.updated_at)
        except Exception:
            LOGGER.warning("evaluation_history_write_failed")
