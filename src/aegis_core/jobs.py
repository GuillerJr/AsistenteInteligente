from __future__ import annotations

import asyncio
import time
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from aegis_core.capability_learning import CapabilityLearningCoordinator
from aegis_core.contracts import ToolAuthorization, ToolCall, UserRequest
from aegis_core.job_admission import JobAdmissionCoordinator
from aegis_core.job_authorization import (
    ApprovalGrant,
    AuthorizationConsumptionError,
    JobAuthorizationCoordinator,
)
from aegis_core.job_completion import JobCompletion, JobCompletionCoordinator
from aegis_core.job_contracts import (
    MAX_JOB_WAIT_SECONDS,
    TERMINAL_STATUSES,
    EvaluationStore,
    JobConfirmationError,
    JobError,
    JobSnapshot,
    JobStatus,
    VoiceConfirmationVerifierProtocol,
)
from aegis_core.job_contracts import BrainTarget as BrainTarget
from aegis_core.job_contracts import JobCapacityError as JobCapacityError
from aegis_core.job_contracts import JobEvaluation as JobEvaluation
from aegis_core.job_contracts import JobNotFoundError as JobNotFoundError
from aegis_core.job_execution import JobExecutionPipeline
from aegis_core.job_failures import JobFailureCode, JobFailurePolicy
from aegis_core.job_graph import GraphInvocation, SwarmGraph
from aegis_core.job_lifecycle import JobLifecycleCoordinator
from aegis_core.job_state import JobRegistry
from aegis_core.job_tasks import JobTaskSupervisor
from aegis_core.job_tool_presenter import JobToolPresenter
from aegis_core.job_tool_runtime import (
    ApprovedToolExecutor,
    JobToolRuntime,
)
from aegis_core.memory.conversations import ConversationCoordinator
from aegis_core.memory.profile import OwnerProfile
from aegis_core.memory.social import SocialMemory
from aegis_core.tools.audit import AuditSink, NullAuditSink
from aegis_core.tools.broker import PolicyContext, ToolBroker
from aegis_core.tools.confirmations import OneTimeConfirmationStore


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
        capability_learning: CapabilityLearningCoordinator | None = None,
        tool_broker: ToolBroker | None = None,
        policy_context: PolicyContext | None = None,
        confirmation_store: OneTimeConfirmationStore | None = None,
        tool_executor: ApprovedToolExecutor | None = None,
        audit_sink: AuditSink | None = None,
        evaluation_store: EvaluationStore | None = None,
        voice_confirmation_verifier: VoiceConfirmationVerifierProtocol | None = None,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
        monotonic_clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if max_jobs < 1:
            raise ValueError("max jobs must be positive")
        if not 0 < execution_timeout_seconds <= 600:
            raise ValueError("execution timeout is out of range")
        registry = JobRegistry(max_jobs)
        self._admission = JobAdmissionCoordinator(conversations)
        self._lifecycle = JobLifecycleCoordinator(
            registry,
            admission=self._admission,
            evaluation_store=evaluation_store,
            clock=clock,
            monotonic_clock=monotonic_clock,
        )
        self._execution = JobExecutionPipeline(
            graph,
            execution_timeout_seconds=execution_timeout_seconds,
            conversations=conversations,
        )
        self._completion = JobCompletionCoordinator(
            self._execution,
            owner_profile=owner_profile,
            social_memory=social_memory,
            capability_learning=capability_learning,
        )
        tool_presenter = JobToolPresenter(tool_broker)
        self._audit = audit_sink or NullAuditSink()
        self._failures = JobFailurePolicy(self._audit)
        tool_runtime = JobToolRuntime(
            broker=tool_broker,
            policy_context=policy_context,
            confirmation_store=confirmation_store,
            executor=tool_executor,
            audit_sink=self._audit,
            presenter=tool_presenter,
        )
        self._authorization = JobAuthorizationCoordinator(
            runtime=tool_runtime,
            presenter=tool_presenter,
            voice_verifier=voice_confirmation_verifier,
            audit_sink=self._audit,
        )
        self._tasks = JobTaskSupervisor()
        self._clock = clock
        self._lock = asyncio.Lock()
        self._closed = False
        self._close_task: asyncio.Task[None] | None = None

    async def submit(
        self,
        request: UserRequest,
        *,
        conversation_id: UUID | None = None,
        persist_conversation: bool = False,
    ) -> JobSnapshot:
        resolved = await self._admission.resolve(
            request,
            conversation_id=conversation_id,
            persist_conversation=persist_conversation,
        )
        async with self._lock:
            if self._closed:
                raise JobError("job manager is closed")
            self._expire_confirmations_locked()
            self._lifecycle.reserve_capacity()
            now = self._clock()
            prepared = self._lifecycle.prepare_admission(
                resolved,
                now=now,
            )
            snapshot = self._lifecycle.create(prepared, now=now)
            self._tasks.start(
                snapshot.job_id,
                lambda: self._run(
                    snapshot.job_id,
                    prepared.request,
                    prepared.conversation_id,
                ),
                name=f"aegis-job-{snapshot.job_id}",
            )
            return snapshot

    async def status(self, job_id: UUID) -> JobSnapshot:
        async with self._lock:
            self._expire_confirmations_locked()
            return self._lifecycle.snapshot(job_id)

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
            self._expire_confirmations_locked()
            snapshot, change_event = self._lifecycle.wait_state(job_id)
            if (
                snapshot.status in TERMINAL_STATUSES
                or snapshot.status is JobStatus.AWAITING_CONFIRMATION
                or snapshot.stream_version != after_stream_version
            ):
                return snapshot
        try:
            await asyncio.wait_for(change_event.wait(), timeout=timeout_seconds)
        except TimeoutError:
            pass
        return await self.status(job_id)

    async def metrics(self) -> dict[str, Any]:
        async with self._lock:
            current = self._lifecycle.current_evaluations()
        return self._lifecycle.build_metrics(current)

    async def approve(self, job_id: UUID, call_digest: str) -> JobSnapshot:
        return await self._approve(
            job_id,
            call_digest,
            self._authorization.local_user_grant(),
        )

    async def approve_by_voice(
        self,
        *,
        job_id: UUID,
        call_digest: str,
        pcm: bytes,
        speaker_identifier: str,
        speaker_confidence: float,
        owner_profile_match: bool,
    ) -> JobSnapshot:
        grant = await self._authorization.verified_voice_grant(
            pcm,
            speaker_identifier=speaker_identifier,
            speaker_confidence=speaker_confidence,
            owner_profile_match=owner_profile_match,
        )
        snapshot = await self._approve(
            job_id,
            call_digest,
            grant,
        )
        self._authorization.record_consumed(snapshot, grant)
        return snapshot

    async def _approve(
        self,
        job_id: UUID,
        call_digest: str,
        grant: ApprovalGrant,
    ) -> JobSnapshot:
        async with self._lock:
            if self._closed:
                raise JobError("job manager is closed")
            now = self._clock()
            self._expire_confirmations_locked(now)
            confirmation = self._lifecycle.confirmation_context(job_id)
            try:
                authorization = self._authorization.consume(
                    confirmation,
                    received_call_digest=call_digest,
                    grant=grant,
                )
            except AuthorizationConsumptionError as error:
                self._lifecycle.fail_confirmation_consumption(job_id, now=now)
                self._tasks.discard(job_id)
                raise JobConfirmationError("confirmation could not be consumed") from error
            self._lifecycle.resume_after_confirmation(job_id, now=now)
            self._tasks.replace(
                job_id,
                lambda: self._run_approved_tool(job_id, authorization),
                name=f"aegis-approved-tool-{job_id}",
            )
            return self._lifecycle.snapshot(job_id)

    async def cancel(self, job_id: UUID) -> JobSnapshot:
        async with self._lock:
            self._expire_confirmations_locked()
            snapshot = self._lifecycle.snapshot(job_id)
            should_cancel = snapshot.status not in TERMINAL_STATUSES
            task = self._tasks.cancel(job_id) if should_cancel else None
        if task is not None:
            await self._tasks.settle((task,))
        if should_cancel:
            await self._mark_cancelled_if_active(job_id)
        return await self.status(job_id)

    async def close(self) -> None:
        if self._close_task is None:
            self._closed = True
            self._close_task = asyncio.create_task(self._drain(), name="aegis-jobs-close")
        await asyncio.shield(self._close_task)

    async def _drain(self) -> None:
        async with self._lock:
            self._admission.clear()
            self._lifecycle.close_active()
            tasks = self._tasks.cancel_all()
        await self._tasks.settle(tasks)
        self._tasks.clear()

    async def _run(
        self,
        job_id: UUID,
        request: UserRequest,
        conversation_id: UUID | None,
    ) -> None:
        await self._transition(job_id, JobStatus.RUNNING)
        try:
            outcome = await self._execution.execute(
                request,
                conversation_id=conversation_id,
                stream_callback=lambda delta: self._publish_stream(job_id, delta),
                invocation_observer=lambda invocation: self._record_invocation(
                    job_id,
                    invocation,
                ),
            )
            pending = outcome.pending_confirmation
            if pending is not None:
                await self._mark_awaiting_confirmation(job_id, *pending)
                return
            completion = await self._completion.from_graph(request, outcome)
            await self._finish(job_id, completion)
        except asyncio.CancelledError:
            await asyncio.shield(self._transition(job_id, JobStatus.CANCELLED))
            raise
        except Exception as error:
            await self._transition(
                job_id,
                JobStatus.FAILED,
                error_code=self._failures.execution_code(
                    error,
                    request_id=request.request_id,
                    job_id=job_id,
                ),
            )

    async def _record_invocation(
        self,
        job_id: UUID,
        invocation: GraphInvocation,
    ) -> None:
        async with self._lock:
            self._lifecycle.record_invocation(job_id, invocation)

    async def _mark_awaiting_confirmation(
        self,
        job_id: UUID,
        call: ToolCall,
        authorization: ToolAuthorization,
    ) -> None:
        summary = self._authorization.confirmation_summary(call, authorization)
        if summary is None:
            await self._transition(
                job_id,
                JobStatus.FAILED,
                error_code=JobFailureCode.CONFIRMATION_TOOL_UNSUPPORTED.value,
            )
            return
        now = self._clock()
        async with self._lock:
            self._lifecycle.awaiting_confirmation(
                job_id,
                call,
                authorization,
                summary=summary,
                now=now,
            )

    async def _run_approved_tool(
        self,
        job_id: UUID,
        authorization: ToolAuthorization,
    ) -> None:
        context = self._lifecycle.approved_tool_context(job_id)
        try:
            outcome = await self._authorization.execute_approved(
                context.request_id,
                authorization,
            )
            completion = await self._completion.from_approved_tool(context, outcome)
            await self._finish(job_id, completion)
        except asyncio.CancelledError:
            await asyncio.shield(self._transition(job_id, JobStatus.CANCELLED))
            raise
        except Exception as error:
            await self._transition(
                job_id,
                JobStatus.FAILED,
                error_code=self._failures.approved_tool_code(
                    error,
                    request_id=context.request_id,
                    job_id=job_id,
                ),
            )

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
            changed = self._lifecycle.transition(
                job_id,
                status,
                now=self._clock(),
                result=result,
                error_code=error_code,
                conversation_persisted=conversation_persisted,
            )
            if changed and status in TERMINAL_STATUSES:
                self._tasks.discard(job_id)

    async def _mark_cancelled_if_active(self, job_id: UUID) -> None:
        async with self._lock:
            if self._lifecycle.cancel_if_active(job_id, now=self._clock()):
                self._tasks.discard(job_id)

    def _publish_stream(self, job_id: UUID, delta: str) -> None:
        self._lifecycle.publish_stream(job_id, delta)

    async def _finish(self, job_id: UUID, completion: JobCompletion) -> None:
        async with self._lock:
            if self._lifecycle.finish(
                job_id,
                completion,
                now=self._clock(),
            ):
                self._tasks.discard(job_id)

    def _expire_confirmations_locked(self, now: datetime | None = None) -> None:
        for job_id in self._lifecycle.expire_confirmations(now or self._clock()):
            self._tasks.discard(job_id)
