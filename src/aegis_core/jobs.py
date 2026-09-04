from __future__ import annotations

import asyncio
import json
import logging
import time
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

from aegis_core.capability_learning import CapabilityLearningCoordinator
from aegis_core.contracts import (
    InputModality,
    ToolAuthorization,
    ToolCall,
    ToolExecutionResult,
    UserRequest,
)
from aegis_core.job_admission import JobAdmissionCoordinator
from aegis_core.job_contracts import (
    MAX_JOB_RESULT_BYTES,
    MAX_JOB_WAIT_SECONDS,
    PENDING_CONFIRMATION_TTL,
    TERMINAL_STATUSES,
    EmptyAgentResponseError,
    EvaluationStore,
    JobConfirmationError,
    JobError,
    JobSnapshot,
    JobStatus,
    PendingToolConfirmation,
    StoredJobEvaluation,
    VoiceConfirmationVerifierProtocol,
)
from aegis_core.job_contracts import BrainTarget as BrainTarget
from aegis_core.job_contracts import JobCapacityError as JobCapacityError
from aegis_core.job_contracts import JobEvaluation as JobEvaluation
from aegis_core.job_contracts import JobNotFoundError as JobNotFoundError
from aegis_core.job_graph import GraphInvocation, JobGraphInvoker, SwarmGraph
from aegis_core.job_metrics import build_job_metrics, evaluate_job
from aegis_core.job_state import JobRegistry, MutableJob
from aegis_core.job_tool_presenter import JobToolPresenter
from aegis_core.job_tool_runtime import (
    ApprovedToolExecutor,
    ConfirmationConsumptionError,
    JobToolRuntime,
)
from aegis_core.memory.contracts import MAX_MEMORY_CONTENT_BYTES, ConversationTurn
from aegis_core.memory.conversations import ConversationCoordinator
from aegis_core.memory.errors import (
    ConversationCapacityError,
    MemoryNotFoundError,
    MemoryStoreError,
)
from aegis_core.memory.profile import OwnerProfile
from aegis_core.memory.social import SocialMemory
from aegis_core.tools.audit import AuditSink, NullAuditSink
from aegis_core.tools.broker import PolicyContext, ToolBroker, VoiceConfirmationEvidence
from aegis_core.tools.confirmations import OneTimeConfirmationStore

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
    ) -> None:
        if max_jobs < 1:
            raise ValueError("max jobs must be positive")
        if not 0 < execution_timeout_seconds <= 600:
            raise ValueError("execution timeout is out of range")
        self._graph_invoker = JobGraphInvoker(graph)
        self._registry = JobRegistry(max_jobs)
        self._execution_timeout_seconds = execution_timeout_seconds
        self._conversations = conversations
        self._admission = JobAdmissionCoordinator(conversations)
        self._owner_profile = owner_profile
        self._social_memory = social_memory
        self._capability_learning = capability_learning
        self._tool_presenter = JobToolPresenter(tool_broker)
        self._audit = audit_sink or NullAuditSink()
        self._tool_runtime = JobToolRuntime(
            broker=tool_broker,
            policy_context=policy_context,
            confirmation_store=confirmation_store,
            executor=tool_executor,
            audit_sink=self._audit,
            presenter=self._tool_presenter,
        )
        self._evaluation_store = evaluation_store
        self._voice_confirmation_verifier = voice_confirmation_verifier
        self._clock = clock
        self._monotonic = monotonic_clock
        self._lock = asyncio.Lock()
        self._closed = False

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
            self._expire_pending_confirmations(self._clock())
            self._registry.reserve_capacity()
            now = self._clock()
            prepared = self._admission.prepare(
                resolved,
                registry=self._registry,
                now=now,
            )
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
            job.task = asyncio.create_task(
                self._run(job.job_id, prepared.request, prepared.conversation_id),
                name=f"aegis-job-{job.job_id}",
            )
            return job.snapshot()

    async def status(self, job_id: UUID) -> JobSnapshot:
        async with self._lock:
            self._expire_pending_confirmations(self._clock())
            job = self._registry.require(job_id)
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
            job = self._registry.require(job_id)
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
                for job in self._registry.values()
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
        return build_job_metrics(by_job_id.values())

    async def approve(self, job_id: UUID, call_digest: str) -> JobSnapshot:
        return await self._approve(
            job_id,
            call_digest,
            approved_by="local-menu-bar-user",
            voice_confirmation=None,
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
        verifier = self._voice_confirmation_verifier
        if verifier is None:
            raise JobConfirmationError("voice confirmation is unavailable")
        if len(pcm) % 2 or not 8_000 <= len(pcm) <= 96_000:
            raise JobConfirmationError("voice confirmation PCM is invalid")
        result = await verifier.verify(
            pcm,
            speaker_identifier=speaker_identifier,
            speaker_confidence=speaker_confidence,
            owner_profile_match=owner_profile_match,
        )
        if not result.authorized:
            raise JobConfirmationError("voice confirmation was rejected")
        snapshot = await self._approve(
            job_id,
            call_digest,
            approved_by="verified-owner-voice",
            voice_confirmation=VoiceConfirmationEvidence(
                speaker_verified=result.speaker_verified,
                semantic_verified=result.semantic_verified,
            ),
        )
        self._audit.record_system_event(
            snapshot.request_id,
            event_type="voice_tool_confirmation_consumed",
            component="tool_broker",
            data={
                "job_id": str(snapshot.job_id),
                "speaker_verified": True,
                "semantic_verified": True,
            },
        )
        return snapshot

    async def _approve(
        self,
        job_id: UUID,
        call_digest: str,
        *,
        approved_by: str,
        voice_confirmation: VoiceConfirmationEvidence | None,
    ) -> JobSnapshot:
        async with self._lock:
            now = self._clock()
            self._expire_pending_confirmations(now)
            job = self._registry.require(job_id)
            if (
                job.status is not JobStatus.AWAITING_CONFIRMATION
                or job.confirmation is None
                or job.pending_call is None
                or job.pending_authorization is None
            ):
                raise JobConfirmationError("confirmation is unavailable")
            try:
                authorization = self._tool_runtime.consume_confirmation(
                    call=job.pending_call,
                    pending_authorization=job.pending_authorization,
                    authorization_request=job.authorization_request,
                    expected_call_digest=job.confirmation.call_digest,
                    received_call_digest=call_digest,
                    approved_by=approved_by,
                    voice_confirmation=voice_confirmation,
                )
            except ConfirmationConsumptionError as error:
                job.transition(
                    JobStatus.FAILED,
                    now=now,
                    error_code="confirmation_consumption_failed",
                )
                self._record_evaluation(job, JobStatus.FAILED)
                job.clear_pending()
                job.publish_change()
                raise JobConfirmationError("confirmation could not be consumed") from error
            job.finish_confirmation_wait(self._monotonic)
            job.transition(JobStatus.RUNNING, now=now)
            job.confirmation = None
            job.publish_change()
            job.task = asyncio.create_task(
                self._run_approved_tool(job_id, authorization),
                name=f"aegis-approved-tool-{job_id}",
            )
            return job.snapshot()

    async def cancel(self, job_id: UUID) -> JobSnapshot:
        async with self._lock:
            self._expire_pending_confirmations(self._clock())
            job = self._registry.require(job_id)
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
            self._admission.clear()
            tasks = list(self._registry.active_tasks())
            for task in tasks:
                task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        async with self._lock:
            for job in self._registry.values():
                if job.status not in TERMINAL_STATUSES:
                    job.transition(JobStatus.CANCELLED, now=self._clock())
                    self._record_evaluation(job, JobStatus.CANCELLED)
                    job.clear_pending()
                    job.publish_change()

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
                    if (
                        InputModality.AUDIO in request.modalities
                        and not OwnerProfile.is_verified_owner_voice(request)
                        and history
                    ):
                        await self._transition(
                            job_id,
                            JobStatus.FAILED,
                            error_code="voice_conversation_owner_required",
                        )
                        return
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
                    result = self._validated_result(final_result.content)
                    await self._set_job_model(job_id, final_result.model_id)
                    conversation_persisted = await self._record_exchange_after_result(
                        conversation_id,
                        user_content=request.text,
                        assistant_content=self._bounded_conversation_content(result),
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
                result = self._validated_result(final_result.content)
                await self._set_job_model(job_id, final_result.model_id)
                conversation_persisted = None
            if invocation.public_sources is not None:
                await self._remember_public_sources(
                    job_id,
                    invocation.public_sources,
                )
            observers = []
            if self._owner_profile is not None:
                observers.append(self._owner_profile.observe(request))
            if self._social_memory is not None:
                observers.append(self._social_memory.observe(request))
            if self._capability_learning is not None and invocation.capability_gap:
                observers.append(
                    self._capability_learning.observe(
                        request,
                        invocation.capability_research,
                    )
                )
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
        except EmptyAgentResponseError:
            await self._transition(
                job_id,
                JobStatus.FAILED,
                error_code="empty_agent_response",
            )
        except Exception as error:
            error_type = type(error).__name__
            LOGGER.error(
                "swarm job failed job_id=%s error_type=%s",
                job_id,
                error_type,
                exc_info=(type(error), error, error.__traceback__),
            )
            self._audit.record_system_event(
                request.request_id,
                event_type="swarm_execution_failed",
                component="job_manager",
                data={"job_id": str(job_id), "error_type": error_type},
            )
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
    ) -> GraphInvocation:
        invocation = await self._graph_invoker.invoke(
            request,
            conversation_history,
            stream_callback=lambda delta: self._publish_stream(job_id, delta),
        )
        if invocation.tool_name is not None:
            await self._set_job_tool(
                job_id,
                invocation.tool_name,
                verified=invocation.action_verified,
            )
        if invocation.model_id is not None:
            await self._set_job_model(job_id, invocation.model_id)
        return invocation

    async def _mark_awaiting_confirmation(
        self,
        job_id: UUID,
        call: ToolCall,
        authorization: ToolAuthorization,
    ) -> None:
        if not self._tool_presenter.supports_confirmation(call.tool_name):
            await self._transition(
                job_id,
                JobStatus.FAILED,
                error_code="confirmation_tool_unsupported",
            )
            return
        summary = self._confirmation_summary(authorization)
        now = self._clock()
        async with self._lock:
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

    async def _run_approved_tool(
        self,
        job_id: UUID,
        authorization: ToolAuthorization,
    ) -> None:
        try:
            job = self._registry.require(job_id)
            outcome = await self._tool_runtime.execute(job.request_id, authorization)
            result = outcome.result
            await self._set_job_tool(
                job_id,
                result.tool_name,
                verified=outcome.verified,
            )
            if not result.success:
                await self._transition(
                    job_id,
                    JobStatus.FAILED,
                    error_code="approved_tool_execution_failed",
                )
                return
            if outcome.rendered_result is None:
                raise ValueError("approved tool result is empty")
            formatted_result = self._bounded_result(outcome.rendered_result)
            self._publish_stream(job_id, formatted_result)
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

    def _confirmation_summary(self, authorization: ToolAuthorization) -> str:
        return self._tool_presenter.confirmation_summary(authorization)

    @staticmethod
    def _format_tool_result(
        result: ToolExecutionResult,
        authorization: ToolAuthorization | None = None,
    ) -> str:
        return JobToolPresenter.format_result(result, authorization)

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
            job = self._registry.require(job_id)
            if not job.transition(
                status,
                now=self._clock(),
                result=result,
                error_code=error_code,
                conversation_persisted=conversation_persisted,
            ):
                return
            if status in TERMINAL_STATUSES:
                if result is not None and not job.partial_result:
                    self._publish_stream_locked(job, result)
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

    async def _mark_cancelled_if_active(self, job_id: UUID) -> None:
        async with self._lock:
            job = self._registry.require(job_id)
            if job.transition(JobStatus.CANCELLED, now=self._clock()):
                self._record_evaluation(job, JobStatus.CANCELLED)
                job.clear_pending()
                job.publish_change()

    def _expire_pending_confirmations(self, now: datetime) -> None:
        for job in self._registry.expire_confirmations(now):
            self._record_evaluation(job, JobStatus.FAILED)
            job.clear_pending()
            job.publish_change()

    def _publish_stream(self, job_id: UUID, delta: str) -> None:
        if not isinstance(delta, str) or not delta:
            return
        job = self._registry.get(job_id)
        if job is None or job.status in TERMINAL_STATUSES:
            return
        self._publish_stream_locked(job, delta)

    def _publish_stream_locked(self, job: MutableJob, delta: str) -> None:
        job.publish_stream(
            delta,
            bound=self._bounded_result,
            monotonic_clock=self._monotonic,
        )

    async def _set_job_model(self, job_id: UUID, model_id: str) -> None:
        async with self._lock:
            job = self._registry.get(job_id)
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
            job = self._registry.get(job_id)
            if job is not None:
                job.tool_name = tool_name
                job.action_verified = verified

    async def _remember_public_sources(
        self,
        job_id: UUID,
        urls: tuple[str, ...],
    ) -> None:
        async with self._lock:
            job = self._registry.get(job_id)
            if job is None:
                return
            self._admission.remember_public_sources(
                job,
                urls,
                now=self._clock(),
            )

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

    @classmethod
    def _validated_result(cls, content: str) -> str:
        result = cls._bounded_result(content)
        if not result.strip():
            raise EmptyAgentResponseError("agent response is empty")
        return result

    @staticmethod
    def _bounded_conversation_content(content: str) -> str:
        encoded = content.encode("utf-8")
        if len(encoded) <= MAX_MEMORY_CONTENT_BYTES:
            return content
        return encoded[:MAX_MEMORY_CONTENT_BYTES].decode("utf-8", errors="ignore")
