import asyncio
from typing import Any
from uuid import UUID, uuid4

import pytest

from aegis_core.contracts import AgentResult, AgentRole, UserRequest
from aegis_core.ipc.protocol import IpcAuthenticator
from aegis_core.jobs import (
    JobCapacityError,
    JobNotFoundError,
    JobStatus,
    SwarmIpcService,
    SwarmJobManager,
)


class ImmediateGraph:
    async def ainvoke(self, input: dict[str, Any]) -> dict[str, Any]:
        request = input["request"]
        return {
            "final_result": AgentResult(
                role=AgentRole.SYNTHESIZER,
                model_id="fake/synthesizer",
                content=f"respuesta:{request.text}",
            )
        }


class FailingGraph:
    async def ainvoke(self, input: dict[str, Any]) -> dict[str, Any]:
        del input
        raise RuntimeError("secret provider details")


class LargeUnicodeGraph:
    async def ainvoke(self, input: dict[str, Any]) -> dict[str, Any]:
        del input
        return {
            "final_result": AgentResult(
                role=AgentRole.SYNTHESIZER,
                model_id="fake/synthesizer",
                content="🛡️" * 20_000,
            )
        }


class BlockingGraph:
    def __init__(self) -> None:
        self.started = asyncio.Event()
        self.release = asyncio.Event()

    async def ainvoke(self, input: dict[str, Any]) -> dict[str, Any]:
        del input
        self.started.set()
        await self.release.wait()
        return {
            "final_result": AgentResult(
                role=AgentRole.SYNTHESIZER,
                model_id="fake/synthesizer",
                content="done",
            )
        }


async def _terminal(jobs: SwarmJobManager, job_id: UUID) -> Any:
    for _ in range(20):
        snapshot = await jobs.status(job_id)
        if snapshot.status in {
            JobStatus.COMPLETED,
            JobStatus.FAILED,
            JobStatus.CANCELLED,
        }:
            return snapshot
        await asyncio.sleep(0)
    raise AssertionError("job did not reach a terminal state")


@pytest.mark.asyncio
async def test_job_completes_with_bounded_public_result() -> None:
    jobs = SwarmJobManager(ImmediateGraph())
    queued = await jobs.submit(UserRequest(text="hola"))

    completed = await _terminal(jobs, queued.job_id)

    assert queued.status is JobStatus.QUEUED
    assert completed.status is JobStatus.COMPLETED
    assert completed.result == "respuesta:hola"
    assert completed.error_code is None
    await jobs.close()


@pytest.mark.asyncio
async def test_job_failure_does_not_expose_exception_details() -> None:
    jobs = SwarmJobManager(FailingGraph())
    queued = await jobs.submit(UserRequest(text="hola"))

    failed = await _terminal(jobs, queued.job_id)

    assert failed.status is JobStatus.FAILED
    assert failed.error_code == "swarm_execution_failed"
    assert failed.result is None
    assert "secret provider details" not in repr(failed)
    await jobs.close()


@pytest.mark.asyncio
async def test_job_result_is_bounded_by_utf8_bytes() -> None:
    jobs = SwarmJobManager(LargeUnicodeGraph())
    queued = await jobs.submit(UserRequest(text="grande"))

    completed = await _terminal(jobs, queued.job_id)

    assert completed.status is JobStatus.COMPLETED
    assert completed.result is not None
    assert len(completed.result.encode("utf-8")) <= 24_576
    await jobs.close()


@pytest.mark.asyncio
async def test_running_job_can_be_cancelled() -> None:
    graph = BlockingGraph()
    jobs = SwarmJobManager(graph)
    queued = await jobs.submit(UserRequest(text="espera"))
    await graph.started.wait()

    cancelled = await jobs.cancel(queued.job_id)

    assert cancelled.status is JobStatus.CANCELLED
    await jobs.close()


@pytest.mark.asyncio
async def test_queued_job_can_be_cancelled_before_it_starts() -> None:
    graph = BlockingGraph()
    jobs = SwarmJobManager(graph)
    queued = await jobs.submit(UserRequest(text="espera"))

    cancelled = await jobs.cancel(queued.job_id)

    assert cancelled.status is JobStatus.CANCELLED
    await jobs.close()


@pytest.mark.asyncio
async def test_capacity_fails_closed_when_all_jobs_are_active() -> None:
    graph = BlockingGraph()
    jobs = SwarmJobManager(graph, max_jobs=1)
    first = await jobs.submit(UserRequest(text="primero"))

    with pytest.raises(JobCapacityError):
        await jobs.submit(UserRequest(text="segundo"))

    await jobs.cancel(first.job_id)
    await jobs.close()


@pytest.mark.asyncio
async def test_terminal_job_is_evicted_when_capacity_is_reused() -> None:
    jobs = SwarmJobManager(ImmediateGraph(), max_jobs=1)
    first = await jobs.submit(UserRequest(text="primero"))
    await _terminal(jobs, first.job_id)

    second = await jobs.submit(UserRequest(text="segundo"))

    with pytest.raises(JobNotFoundError):
        await jobs.status(first.job_id)
    assert second.job_id != first.job_id
    await jobs.close()


@pytest.mark.asyncio
async def test_swarm_ipc_service_validates_payload_and_hides_missing_jobs() -> None:
    jobs = SwarmJobManager(ImmediateGraph())
    service = SwarmIpcService(jobs)
    authenticator = IpcAuthenticator(bytes.fromhex("77" * 32))
    invalid = authenticator.create_request("swarm.submit", {"text": ""})
    missing = authenticator.create_request("jobs.status", {"job_id": str(uuid4())})

    invalid_result = await service.handle(invalid)
    missing_result = await service.handle(missing)

    assert invalid_result.ok is False
    assert invalid_result.error_code == "invalid_payload"
    assert missing_result.ok is False
    assert missing_result.error_code == "job_not_found"
    await jobs.close()


@pytest.mark.asyncio
async def test_swarm_ipc_service_cancels_active_job() -> None:
    graph = BlockingGraph()
    jobs = SwarmJobManager(graph)
    service = SwarmIpcService(jobs)
    authenticator = IpcAuthenticator(bytes.fromhex("88" * 32))
    submit = authenticator.create_request("swarm.submit", {"text": "espera"})
    submitted = await service.handle(submit)
    await graph.started.wait()
    cancel = authenticator.create_request("jobs.cancel", {"job_id": submitted.payload["job_id"]})

    cancelled = await service.handle(cancel)

    assert cancelled.ok is True
    assert cancelled.payload["status"] == JobStatus.CANCELLED
    await jobs.close()
