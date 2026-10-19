from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable
from dataclasses import replace
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from aegis_core.capability_learning import CapabilityLearningCoordinator
from aegis_core.contracts import PolicyDecision, ToolAuthorization, ToolCall, UserRequest
from aegis_core.job_admission import JobAdmissionCoordinator
from aegis_core.job_authorization import (
    ApprovalGrant,
    AuthorizationConsumptionError,
    JobAuthorizationCoordinator,
)
from aegis_core.job_completion import ApprovedToolContext, JobCompletion, JobCompletionCoordinator
from aegis_core.job_contracts import (
    MAX_JOB_WAIT_SECONDS,
    PENDING_CONFIRMATION_TTL,
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
    ApprovedToolOutcome,
    JobToolRuntime,
)
from aegis_core.memory.conversations import ConversationCoordinator
from aegis_core.memory.profile import OwnerProfile
from aegis_core.memory.social import SocialMemory
from aegis_core.tools.audit import AuditSink, NullAuditSink
from aegis_core.tools.broker import PolicyContext, ToolBroker
from aegis_core.tools.computer_workflow import (
    ComputerWorkflowState,
    begin_workflow_step,
    next_pending_step,
    observe_workflow_step,
    recover_interrupted_workflow,
    workflow_step_call,
)
from aegis_core.tools.confirmations import OneTimeConfirmationStore
from aegis_core.tools.verification import result_is_verified

LOGGER = logging.getLogger(__name__)


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
        approved_result_summarizer: Callable[
            [ApprovedToolContext, ApprovedToolOutcome], Awaitable[str | None]
        ]
        | None = None,
        request_cleanup: Callable[[UUID], Awaitable[None]] | None = None,
    ) -> None:
        if max_jobs < 1:
            raise ValueError("max jobs must be positive")
        if not 0 < execution_timeout_seconds <= 600:
            raise ValueError("execution timeout is out of range")
        registry = JobRegistry(max_jobs)
        self._registry = registry
        self._max_jobs = max_jobs
        self._workflow_broker = tool_broker
        self._workflow_policy_context = policy_context
        self._admission = JobAdmissionCoordinator(conversations)
        self._lifecycle = JobLifecycleCoordinator(
            registry,
            admission=self._admission,
            evaluation_store=evaluation_store,
            clock=clock,
            monotonic_clock=monotonic_clock,
            execution_timeout_seconds=execution_timeout_seconds,
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
            approved_result_summarizer=approved_result_summarizer,
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
        self._confirmation_expiries: dict[UUID, asyncio.Task[None]] = {}
        self._confirmation_expiry_tasks: set[asyncio.Task[None]] = set()
        self._requests: dict[UUID, UserRequest] = {}
        self._request_cleanup = request_cleanup
        self._request_cleanups: dict[UUID, asyncio.Task[None]] = {}
        self._clock = clock
        self._lock = asyncio.Lock()
        self._closed = False
        self._close_task: asyncio.Task[None] | None = None

    @property
    def has_active_jobs(self) -> bool:
        return self._lifecycle.has_active_jobs

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
            if self._request_cleanup is not None:
                active = sum(job.status not in TERMINAL_STATUSES for job in self._registry.values())
                cleaning = sum(not task.done() for task in self._request_cleanups.values())
                if active + cleaning >= self._max_jobs:
                    raise JobCapacityError("job cleanup capacity reached")
                if any(job.request_id == resolved.request.request_id
                       for job in self._registry.values()):
                    raise JobError("request identity was already admitted")
            self._lifecycle.reserve_capacity()
            retained = {job.job_id for job in self._registry.values()}
            self._request_cleanups = {key: task for key, task in self._request_cleanups.items()
                                      if key in retained or not task.done()}
            now = self._clock()
            prepared = self._lifecycle.prepare_admission(
                resolved,
                now=now,
            )
            snapshot = self._lifecycle.create(prepared, now=now)
            self._requests[snapshot.job_id] = prepared.request
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
            snapshot = self._lifecycle.snapshot(job_id)
            cleanup = self._request_cleanups.get(job_id)
        if snapshot.status in TERMINAL_STATUSES and cleanup is not None:
            await asyncio.shield(cleanup)
        return snapshot

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

    async def approve_from_telegram(
        self,
        job_id: UUID,
        call_digest: str,
        *,
        owner_id: int,
    ) -> JobSnapshot:
        # Internal adapter entry point; never exposed as an IPC approval method.
        snapshot = await self._approve(
            job_id,
            call_digest,
            self._authorization.telegram_grant(owner_id),
        )
        self._authorization.record_consumed(snapshot, self._authorization.telegram_grant(owner_id))
        return snapshot

    async def approve_by_voice(
        self,
        *,
        job_id: UUID,
        call_digest: str,
        pcm: bytes,
        speaker_identifier: str,
        speaker_confidence: float,
        owner_profile_match: bool,
        transcript: str | None = None,
    ) -> JobSnapshot:
        # Reject stale or unrelated approvals before running the biometric/semantic
        # verifier or spending one of its bounded anti-replay slots. _approve checks
        # the same live job again after the asynchronous verification completes.
        async with self._lock:
            if self._closed:
                raise JobError("job manager is closed")
            self._expire_confirmations_locked()
            confirmation = self._lifecycle.confirmation_context(job_id)
            if call_digest != confirmation.expected_call_digest:
                raise JobConfirmationError("confirmation is unavailable")
        grant = await self._authorization.verified_voice_grant(
            pcm,
            speaker_identifier=speaker_identifier,
            speaker_confidence=speaker_confidence,
            owner_profile_match=owner_profile_match,
            transcript=transcript,
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
            if confirmation.authorization_request is not None:
                job = self._registry.require(job_id)
                confirmation = replace(
                    confirmation,
                    authorization_request=confirmation.authorization_request.model_copy(
                        update={"text": job.request_text},
                    ),
                )
            try:
                authorization = self._authorization.consume(
                    confirmation,
                    received_call_digest=call_digest,
                    grant=grant,
                )
            except AuthorizationConsumptionError as error:
                self._lifecycle.fail_confirmation_consumption(job_id, now=now)
                self._cancel_confirmation_expiry_locked(job_id)
                self._tasks.discard(job_id)
                self._schedule_request_cleanup_locked(job_id)
                raise JobConfirmationError("confirmation could not be consumed") from error
            self._lifecycle.resume_after_confirmation(job_id, now=now)
            self._cancel_confirmation_expiry_locked(job_id)
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
            await self._interrupt_workflow(job_id, cancelled=True)
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
            for job in self._registry.values():
                self._schedule_request_cleanup_locked(job.job_id)
            tasks = self._tasks.cancel_all()
            expiry_tasks = tuple(self._confirmation_expiry_tasks)
            self._confirmation_expiries.clear()
            for task in expiry_tasks:
                task.cancel()
        await self._tasks.settle((*tasks, *expiry_tasks))
        await self._tasks.settle(tuple(self._request_cleanups.values()))
        self._tasks.clear()

    async def _run(
        self,
        job_id: UUID,
        request: UserRequest,
        conversation_id: UUID | None,
    ) -> None:
        await self._transition(job_id, JobStatus.RUNNING)
        try:
            async with asyncio.timeout(self._lifecycle.remaining_execution_seconds(job_id)):
                await self._execute_job(job_id, request, conversation_id)
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

    async def _execute_job(
        self,
        job_id: UUID,
        request: UserRequest,
        conversation_id: UUID | None,
    ) -> None:
        # One daemon-owned budget includes context, inference and final persistence.
        outcome = await self._execution.execute(
            request,
            conversation_id=conversation_id,
            stream_callback=lambda delta: self._publish_stream(job_id, delta),
            invocation_observer=lambda invocation: self._record_invocation(job_id, invocation),
        )
        pending = outcome.pending_confirmation
        if pending is not None:
            await self._mark_awaiting_confirmation(job_id, *pending)
            return
        completion = await self._completion.from_graph(request, outcome)
        await self._finish(job_id, completion)

    async def _record_invocation(
        self,
        job_id: UUID,
        invocation: GraphInvocation,
    ) -> None:
        async with self._lock:
            self._lifecycle.record_invocation(job_id, invocation)
            job = self._registry.get(job_id)
            if job is not None:
                job.computer_workflow = invocation.computer_workflow

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
            job = self._registry.get(job_id)
            workflow = job.computer_workflow if job is not None else None
            if workflow is not None and (step := next_pending_step(workflow)) is not None:
                summary = f"Paso {step.ordinal} de {len(workflow.spec.steps)} · {summary}"[:512]
            self._lifecycle.awaiting_confirmation(
                job_id,
                call,
                authorization,
                summary=summary,
                now=now,
            )
            if self._lifecycle.snapshot(job_id).status is not JobStatus.AWAITING_CONFIRMATION:
                return
            self._cancel_confirmation_expiry_locked(job_id)
            task = asyncio.create_task(
                self._expire_confirmation_when_due(job_id),
                name=f"aegis-confirmation-expiry-{job_id}",
            )
            self._confirmation_expiries[job_id] = task
            self._confirmation_expiry_tasks.add(task)
            task.add_done_callback(self._confirmation_expiry_finished)

    def _confirmation_expiry_finished(self, task: asyncio.Task[None]) -> None:
        self._confirmation_expiry_tasks.discard(task)
        if not task.cancelled() and (error := task.exception()) is not None:
            LOGGER.error("confirmation_expiry_failed error_type=%s", type(error).__name__)

    async def _expire_confirmation_when_due(self, job_id: UUID) -> None:
        # A disconnected UI/CLI must not leave an expired approval marked busy.
        # Sleep uses the event loop's monotonic timer; lifecycle still checks both
        # clocks before expiring, exactly as status/approval/cancellation do.
        try:
            await asyncio.sleep(PENDING_CONFIRMATION_TTL.total_seconds())
            async with self._lock:
                if not self._closed:
                    self._expire_confirmations_locked()
        finally:
            if self._confirmation_expiries.get(job_id) is asyncio.current_task():
                self._confirmation_expiries.pop(job_id, None)

    def _cancel_confirmation_expiry_locked(self, job_id: UUID) -> None:
        task = self._confirmation_expiries.pop(job_id, None)
        if task is not None and task is not asyncio.current_task():
            task.cancel()

    async def _run_approved_tool(
        self,
        job_id: UUID,
        authorization: ToolAuthorization,
    ) -> None:
        context = self._lifecycle.approved_tool_context(job_id)
        try:
            async with asyncio.timeout(self._lifecycle.remaining_execution_seconds(job_id)):
                await self._execute_approved_tool(job_id, authorization, context)
        except asyncio.CancelledError:
            await asyncio.shield(self._interrupt_workflow(job_id, cancelled=True))
            await asyncio.shield(self._transition(job_id, JobStatus.CANCELLED))
            raise
        except TimeoutError:
            await self._interrupt_workflow(job_id)
            await self._transition(
                job_id, JobStatus.FAILED, error_code=JobFailureCode.SWARM_EXECUTION_TIMEOUT.value
            )
        except Exception as error:
            await self._interrupt_workflow(job_id)
            await self._transition(
                job_id,
                JobStatus.FAILED,
                error_code=self._failures.approved_tool_code(
                    error,
                    request_id=context.request_id,
                    job_id=job_id,
                ),
            )

    async def _execute_approved_tool(
        self,
        job_id: UUID,
        authorization: ToolAuthorization,
        context: ApprovedToolContext,
    ) -> None:
        # Resume the same active budget after a decision; never repeat the tool on timeout.
        async with self._lock:
            job = self._registry.get(job_id)
            workflow = job.computer_workflow if job is not None else None
            if workflow is not None:
                if context.conversation_id is None:
                    raise JobError("computer_workflow_requires_conversation")
                workflow = begin_workflow_step(
                    workflow,
                    conversation_id=context.conversation_id,
                    request_id=context.request_id,
                    authorization=authorization,
                )
                job.computer_workflow = workflow
        browser_tool = authorization.tool_name.startswith("browser_task_")
        if not browser_tool:
            await self._execution.record_recovery(
                context.conversation_id,
                request_id=context.request_id,
                request_text=context.request_text,
                tool_name=authorization.tool_name,
                call_digest=authorization.call_digest,
                status="executing",
                computer_workflow=workflow,
            )
        outcome = await self._authorization.execute_approved(
            context.request_id, authorization, request_text=context.request_text,
        )
        if authorization.tool_name == "browser_task_open" and outcome.result.success:
            if (not result_is_verified(outcome.result)
                    or outcome.result.metadata.get("verification_scope") != "browser_task_access"):
                raise JobError("browser_task_access_not_observed")
            async with self._lock:
                request = self._requests.get(job_id)
            if request is None or request.request_id != context.request_id:
                raise JobError("browser_task_request_unavailable")
            # The granted access only bootstraps an observation. Continue planning
            # under the same request, without persisting a session or executable grant.
            await self._execute_job(job_id, request, context.conversation_id)
            return
        if workflow is not None:
            workflow = observe_workflow_step(workflow, outcome.result)
            async with self._lock:
                job = self._registry.get(job_id)
                if job is not None:
                    job.computer_workflow = workflow
        if not browser_tool:
            await self._execution.record_recovery(
                context.conversation_id,
                request_id=context.request_id,
                request_text=context.request_text,
                tool_name=authorization.tool_name,
                call_digest=authorization.call_digest,
                status="observed",
                result=outcome.result,
                computer_workflow=workflow,
            )
        if workflow is not None and next_pending_step(workflow) is not None:
            await self._propose_next_workflow_step(job_id, context, workflow)
            return
        complete_workflow = workflow is not None and all(
            progress.status == "verified" for progress in workflow.progress
        )
        completion = (
            await self._complete_workflow(context, workflow)
            if complete_workflow
            else await self._completion.from_approved_tool(context, outcome)
        )
        if (
            completion.succeeded
            and completion.conversation_persisted
            and (workflow is None or complete_workflow)
            and not browser_tool
        ):
            await self._execution.record_recovery(
                context.conversation_id,
                request_id=context.request_id,
                request_text=context.request_text,
                tool_name=authorization.tool_name,
                call_digest=authorization.call_digest,
                status="completed",
                computer_workflow=workflow,
            )
        await self._finish(job_id, completion)

    async def _complete_workflow(
        self,
        context: ApprovedToolContext,
        workflow: ComputerWorkflowState,
    ) -> JobCompletion:
        # Every item below was individually approved and verified. Summarization
        # cannot expand this statement into an unobserved semantic accomplishment.
        content = "Completé los pasos verificados del flujo:\n" + "\n".join(
            f"{step.ordinal}. {step.application_bundle_identifier}: {step.objective}"
            for step in workflow.spec.steps
        )
        persisted = await self._execution.persist_exchange(
            workflow.conversation_id,
            user_content=context.request_text,
            assistant_content=content,
            documents=context.documents,
            image_name=context.image_name,
            source_checkpoint=context.task_checkpoint,
            request_id=context.request_id if context.source_channel == "telegram" else None,
        )
        return JobCompletion(
            result=content,
            error_code=None,
            conversation_persisted=persisted,
            tool_name="computer_use",
            action_verified=True,
        )

    async def _propose_next_workflow_step(
        self,
        job_id: UUID,
        context: ApprovedToolContext,
        workflow: ComputerWorkflowState,
    ) -> None:
        async with self._lock:
            job = self._registry.get(job_id)
            request = job.authorization_request if job is not None else None
        if (
            request is None
            or self._workflow_broker is None
            or self._workflow_policy_context is None
        ):
            raise JobError("computer_workflow_authorization_unavailable")
        call = workflow_step_call(workflow, context.request_id)
        authorization = self._workflow_broker.authorize(
            call,
            self._workflow_policy_context,
            allowed_names=frozenset({"computer_use"}),
            request=request,
        )
        # A prior step's consumed grant can never authorize another step, even if
        # its arguments happen to resemble the next operation.
        if authorization.decision is not PolicyDecision.REQUIRE_CONFIRMATION:
            raise JobError("computer_workflow_requires_fresh_confirmation")
        self._audit.record_authorization(context.request_id, authorization)
        await self._execution.record_recovery(
            context.conversation_id,
            request_id=context.request_id,
            request_text=context.request_text,
            tool_name="computer_use",
            call_digest=authorization.call_digest,
            status="awaiting_confirmation",
            computer_workflow=workflow,
        )
        await self._mark_awaiting_confirmation(job_id, call, authorization)

    async def _interrupt_workflow(self, job_id: UUID, *, cancelled: bool = False) -> None:
        async with self._lock:
            job = self._registry.get(job_id)
            if job is None or job.computer_workflow is None:
                return
            workflow = recover_interrupted_workflow(job.computer_workflow, cancelled=cancelled)
            if workflow == job.computer_workflow:
                return
            job.computer_workflow = workflow
            context = self._lifecycle.approved_tool_context(job_id)
            observed = next(
                (item for item in reversed(workflow.progress) if item.call_digest), None
            )
        await self._execution.record_recovery(
            context.conversation_id,
            request_id=context.request_id,
            request_text=context.request_text,
            tool_name="computer_use",
            call_digest=observed.call_digest if observed else workflow.spec.goal_sha256,
            status="observed",
            computer_workflow=workflow,
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
                self._cancel_confirmation_expiry_locked(job_id)
                self._tasks.discard(job_id)
                self._schedule_request_cleanup_locked(job_id)
            cleanup = self._request_cleanups.get(job_id)
        if cleanup is not None:
            await asyncio.shield(cleanup)

    async def _mark_cancelled_if_active(self, job_id: UUID) -> None:
        async with self._lock:
            if self._lifecycle.cancel_if_active(job_id, now=self._clock()):
                self._cancel_confirmation_expiry_locked(job_id)
                self._tasks.discard(job_id)
                self._schedule_request_cleanup_locked(job_id)
            cleanup = self._request_cleanups.get(job_id)
        if cleanup is not None:
            await asyncio.shield(cleanup)

    def _publish_stream(self, job_id: UUID, delta: str) -> None:
        self._lifecycle.publish_stream(job_id, delta)

    async def _finish(self, job_id: UUID, completion: JobCompletion) -> None:
        async with self._lock:
            if self._lifecycle.finish(
                job_id,
                completion,
                now=self._clock(),
            ):
                self._cancel_confirmation_expiry_locked(job_id)
                self._tasks.discard(job_id)
                self._schedule_request_cleanup_locked(job_id)
            cleanup = self._request_cleanups.get(job_id)
        if cleanup is not None:
            await asyncio.shield(cleanup)

    def _expire_confirmations_locked(self, now: datetime | None = None) -> None:
        for job_id in self._lifecycle.expire_confirmations(now or self._clock()):
            self._cancel_confirmation_expiry_locked(job_id)
            self._tasks.discard(job_id)
            self._schedule_request_cleanup_locked(job_id)

    def _schedule_request_cleanup_locked(self, job_id: UUID) -> None:
        job = self._registry.get(job_id)
        if job is None or job.status not in TERMINAL_STATUSES:
            return
        self._requests.pop(job_id, None)
        if self._request_cleanup is None or job_id in self._request_cleanups:
            return
        request_id = job.request_id

        async def cleanup() -> None:
            try:
                await self._request_cleanup(request_id)
            except Exception as error:
                # A provider/client error may contain a page, URL or credential.
                LOGGER.warning("request_cleanup_failed error_type=%s", type(error).__name__)

        self._request_cleanups[job_id] = asyncio.create_task(
            cleanup(), name=f"aegis-request-cleanup-{job_id}",
        )
