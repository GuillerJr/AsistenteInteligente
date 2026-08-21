from __future__ import annotations

import asyncio
import json
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass
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
from aegis_core.ipc.protocol import IpcRequest
from aegis_core.ipc.server import IpcHandlerResult, IpcMethodHandler
from aegis_core.memory.contracts import MAX_MEMORY_CONTENT_BYTES, ConversationTurn
from aegis_core.memory.conversations import ConversationCoordinator
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


TERMINAL_STATUSES = frozenset({JobStatus.COMPLETED, JobStatus.FAILED, JobStatus.CANCELLED})
MAX_JOB_RESULT_BYTES = 24_576
PENDING_CONFIRMATION_TTL = timedelta(minutes=2)
MAX_IMAGE_SUBMIT_PAYLOAD_BYTES = 60_000
MAX_RECENT_VOICE_CAPTURES = 256


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
        return self


@dataclass(slots=True)
class _Job:
    job_id: UUID
    request_id: UUID
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
        )


class SwarmGraph(Protocol):
    async def ainvoke(self, input: dict[str, Any]) -> dict[str, Any]: ...


@dataclass(frozen=True, slots=True)
class _GraphInvocation:
    final_result: AgentResult | None
    pending: tuple[tuple[ToolCall, ToolAuthorization], ...]


class SwarmJobManager:
    def __init__(
        self,
        graph: SwarmGraph,
        *,
        max_jobs: int = 128,
        execution_timeout_seconds: float = 120.0,
        conversations: ConversationCoordinator | None = None,
        tool_broker: ToolBroker | None = None,
        policy_context: PolicyContext | None = None,
        confirmation_store: OneTimeConfirmationStore | None = None,
        tool_executor: ReadOnlyToolExecutor | None = None,
        audit_sink: AuditSink | None = None,
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
        self._tool_broker = tool_broker
        self._policy_context = policy_context
        self._confirmation_store = confirmation_store
        self._tool_executor = tool_executor
        self._audit = audit_sink or NullAuditSink()
        self._clock = clock
        self._jobs: dict[UUID, _Job] = {}
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
            job = _Job(
                job_id=uuid4(),
                request_id=request.request_id,
                conversation_id=conversation_id,
                status=JobStatus.QUEUED,
                created_at=now,
                updated_at=now,
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
                self._clear_pending(job)
                raise JobConfirmationError("confirmation could not be consumed")
            job.status = JobStatus.RUNNING
            job.updated_at = now
            job.confirmation = None
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
                    self._clear_pending(job)

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
                        self._invoke_graph(request, history),
                        timeout=self._execution_timeout_seconds,
                    )
                    if invocation.pending:
                        await self._transition(
                            job_id,
                            JobStatus.FAILED,
                            error_code="conversation_confirmation_unsupported",
                        )
                        return
                    if invocation.final_result is None:
                        raise ValueError("graph did not return a final agent result")
                    final_result = invocation.final_result
                    conversation_persisted = await self._record_exchange_after_result(
                        conversation_id,
                        user_content=request.text,
                        assistant_content=self._bounded_conversation_content(final_result.content),
                    )
            else:
                invocation = await asyncio.wait_for(
                    self._invoke_graph(request, ()),
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
                conversation_persisted = None
            result = self._bounded_result(final_result.content)
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
        request: UserRequest,
        conversation_history: tuple[ConversationTurn, ...],
    ) -> _GraphInvocation:
        state = await self._graph.ainvoke(
            {
                "request": request,
                "conversation_history": conversation_history,
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
        return _GraphInvocation(final_result=final_result, pending=tuple(pending))

    async def _mark_awaiting_confirmation(
        self,
        job_id: UUID,
        call: ToolCall,
        authorization: ToolAuthorization,
    ) -> None:
        if call.tool_name not in {"network_discover_hosts", "terminal_run_template"}:
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
            result = await asyncio.to_thread(
                executor.execute,
                authorization,
                context,
            )
            self._audit.record_execution(self._jobs[job_id].request_id, result)
            if not result.success:
                await self._transition(
                    job_id,
                    JobStatus.FAILED,
                    error_code="approved_tool_execution_failed",
                )
                return
            await self._transition(
                job_id,
                JobStatus.COMPLETED,
                result=self._bounded_result(self._format_tool_result(result)),
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
        if authorization.tool_name == "terminal_run_template":
            template = authorization.normalized_arguments.get("template")
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
        if authorization.tool_name != "network_discover_hosts":
            raise ValueError("confirmation tool is unsupported")
        target = authorization.normalized_arguments.get("target")
        ports = authorization.normalized_arguments.get("ports")
        if not isinstance(target, str) or not isinstance(ports, list) or not ports:
            raise ValueError("confirmation arguments are invalid")
        port_text = ", ".join(str(port) for port in ports)
        return f"Sondeo TCP en {target}; puertos {port_text}"

    @staticmethod
    def _format_tool_result(result: ToolExecutionResult) -> str:
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
                if not isinstance(payload, dict) or set(payload) != {
                    name for name, _ in controls
                }:
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
            self._clear_pending(job)

    async def _mark_cancelled_if_active(self, job_id: UUID) -> None:
        async with self._lock:
            job = self._jobs[job_id]
            if job.status not in TERMINAL_STATUSES:
                job.status = JobStatus.CANCELLED
                job.updated_at = self._clock()
                self._clear_pending(job)

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
                self._clear_pending(job)

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
    METHODS = frozenset(
        {
            "swarm.submit",
            "voice.submit",
            "image.submit",
            "jobs.status",
            "jobs.cancel",
            "jobs.approve",
        }
    )

    def __init__(self, jobs: SwarmJobManager) -> None:
        self._jobs = jobs
        self._voice_capture_ids: deque[UUID] = deque(maxlen=MAX_RECENT_VOICE_CAPTURES)

    def handlers(self) -> dict[str, IpcMethodHandler]:
        return {method: self.handle for method in self.METHODS}

    async def handle(self, request: IpcRequest) -> IpcHandlerResult:
        try:
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
