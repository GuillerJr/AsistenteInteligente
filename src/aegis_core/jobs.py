from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, Protocol
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from aegis_core.contracts import AgentResult, InputModality, UserRequest
from aegis_core.ipc.protocol import IpcRequest
from aegis_core.ipc.server import IpcHandlerResult, IpcMethodHandler


class JobError(RuntimeError):
    """Base error for bounded local job management."""


class JobNotFoundError(JobError):
    pass


class JobCapacityError(JobError):
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
        return self


@dataclass(slots=True)
class _Job:
    job_id: UUID
    request_id: UUID
    status: JobStatus
    created_at: datetime
    updated_at: datetime
    result: str | None = None
    error_code: str | None = None
    task: asyncio.Task[None] | None = None

    def snapshot(self) -> JobSnapshot:
        return JobSnapshot(
            job_id=self.job_id,
            request_id=self.request_id,
            status=self.status,
            created_at=self.created_at,
            updated_at=self.updated_at,
            result=self.result,
            error_code=self.error_code,
        )


class SwarmGraph(Protocol):
    async def ainvoke(self, input: dict[str, Any]) -> dict[str, Any]: ...


class SwarmJobManager:
    def __init__(self, graph: SwarmGraph, *, max_jobs: int = 128) -> None:
        if max_jobs < 1:
            raise ValueError("max jobs must be positive")
        self._graph = graph
        self._max_jobs = max_jobs
        self._jobs: dict[UUID, _Job] = {}
        self._lock = asyncio.Lock()
        self._closed = False

    async def submit(self, request: UserRequest) -> JobSnapshot:
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
                status=JobStatus.QUEUED,
                created_at=now,
                updated_at=now,
            )
            self._jobs[job.job_id] = job
            job.task = asyncio.create_task(
                self._run(job.job_id, request),
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

    async def _run(self, job_id: UUID, request: UserRequest) -> None:
        await self._transition(job_id, JobStatus.RUNNING)
        try:
            state = await self._graph.ainvoke({"request": request})
            final_result = state.get("final_result")
            if not isinstance(final_result, AgentResult):
                raise ValueError("graph did not return a final agent result")
            result = self._bounded_result(final_result.content)
            await self._transition(job_id, JobStatus.COMPLETED, result=result)
        except asyncio.CancelledError:
            await asyncio.shield(self._transition(job_id, JobStatus.CANCELLED))
            raise
        except Exception:
            await self._transition(
                job_id,
                JobStatus.FAILED,
                error_code="swarm_execution_failed",
            )

    async def _transition(
        self,
        job_id: UUID,
        status: JobStatus,
        *,
        result: str | None = None,
        error_code: str | None = None,
    ) -> None:
        async with self._lock:
            job = self._jobs[job_id]
            if job.status in TERMINAL_STATUSES:
                return
            job.status = status
            job.updated_at = datetime.now(UTC)
            job.result = result
            job.error_code = error_code

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
        encoded = content.encode("utf-8")
        if len(encoded) <= MAX_JOB_RESULT_BYTES:
            return content
        return encoded[:MAX_JOB_RESULT_BYTES].decode("utf-8", errors="ignore")


class SubmitJobPayload(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    text: str = Field(min_length=1, max_length=50_000)
    modalities: frozenset[InputModality] = Field(
        default_factory=lambda: frozenset({InputModality.TEXT})
    )


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
                user_request = UserRequest(
                    text=payload.text,
                    modalities=payload.modalities,
                    metadata={"ipc_request_id": str(request.request_id)},
                )
                snapshot = await self._jobs.submit(user_request)
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
        except JobCapacityError:
            return IpcHandlerResult(ok=False, error_code="job_capacity_reached")
        except JobError:
            return IpcHandlerResult(ok=False, error_code="job_manager_unavailable")
        return IpcHandlerResult(ok=True, payload=snapshot.model_dump(mode="json"))
