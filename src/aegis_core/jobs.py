from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, Protocol
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from aegis_core.contracts import AgentResult, InputModality, UserRequest
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


class JobError(RuntimeError):
    """Base error for bounded local job management."""


class JobNotFoundError(JobError):
    pass


class JobCapacityError(JobError):
    pass


class JobConversationNotFoundError(JobError):
    pass


class JobStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


TERMINAL_STATUSES = frozenset({JobStatus.COMPLETED, JobStatus.FAILED, JobStatus.CANCELLED})
MAX_JOB_RESULT_BYTES = 24_576


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
        )


class SwarmGraph(Protocol):
    async def ainvoke(self, input: dict[str, Any]) -> dict[str, Any]: ...


class SwarmJobManager:
    def __init__(
        self,
        graph: SwarmGraph,
        *,
        max_jobs: int = 128,
        conversations: ConversationCoordinator | None = None,
    ) -> None:
        if max_jobs < 1:
            raise ValueError("max jobs must be positive")
        self._graph = graph
        self._max_jobs = max_jobs
        self._conversations = conversations
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
            self._evict_terminal_jobs()
            if len(self._jobs) >= self._max_jobs:
                raise JobCapacityError("job capacity reached")
            now = datetime.now(UTC)
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
            job = self._jobs.get(job_id)
            if job is None:
                raise JobNotFoundError("job does not exist")
            return job.snapshot()

    async def cancel(self, job_id: UUID) -> JobSnapshot:
        async with self._lock:
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
                    job.updated_at = datetime.now(UTC)

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
                    final_result = await self._invoke_graph(request, history)
                    conversation_persisted = await self._record_exchange_after_result(
                        conversation_id,
                        user_content=request.text,
                        assistant_content=self._bounded_conversation_content(final_result.content),
                    )
            else:
                final_result = await self._invoke_graph(request, ())
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
    ) -> AgentResult:
        state = await self._graph.ainvoke(
            {
                "request": request,
                "conversation_history": conversation_history,
            }
        )
        final_result = state.get("final_result")
        if not isinstance(final_result, AgentResult):
            raise ValueError("graph did not return a final agent result")
        return final_result

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
            job.updated_at = datetime.now(UTC)
            job.result = result
            job.error_code = error_code
            job.conversation_persisted = conversation_persisted

    async def _mark_cancelled_if_active(self, job_id: UUID) -> None:
        async with self._lock:
            job = self._jobs[job_id]
            if job.status not in TERMINAL_STATUSES:
                job.status = JobStatus.CANCELLED
                job.updated_at = datetime.now(UTC)

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


class SwarmIpcService:
    METHODS = frozenset({"swarm.submit", "jobs.status", "jobs.cancel"})

    def __init__(self, jobs: SwarmJobManager) -> None:
        self._jobs = jobs

    def handlers(self) -> dict[str, IpcMethodHandler]:
        return {method: self.handle for method in self.METHODS}

    async def handle(self, request: IpcRequest) -> IpcHandlerResult:
        try:
            if request.method == "swarm.submit":
                payload = SubmitJobPayload.model_validate(request.payload)
                if contains_likely_secret_material(payload.text):
                    return IpcHandlerResult(
                        ok=False,
                        error_code="secret_material_rejected",
                    )
                user_request = UserRequest(
                    text=payload.text,
                    modalities=payload.modalities,
                    metadata={
                        "ipc_request_id": str(request.request_id),
                        **(
                            {"conversation_id": str(payload.conversation_id)}
                            if payload.conversation_id is not None
                            else {}
                        ),
                    },
                )
                snapshot = await self._jobs.submit(
                    user_request,
                    conversation_id=payload.conversation_id,
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
        except JobError:
            return IpcHandlerResult(ok=False, error_code="job_manager_unavailable")
        return IpcHandlerResult(ok=True, payload=snapshot.model_dump(mode="json"))
