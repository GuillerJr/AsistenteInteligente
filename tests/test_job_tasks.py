from __future__ import annotations

import asyncio
import logging
from collections.abc import Coroutine
from typing import Any
from uuid import uuid4

import pytest

from aegis_core.job_tasks import JobTaskConflictError, JobTaskSupervisor


@pytest.mark.asyncio
async def test_start_rejects_duplicate_active_task_without_invoking_factory() -> None:
    supervisor = JobTaskSupervisor()
    release = asyncio.Event()
    factory_calls = 0

    def operation_factory() -> Coroutine[Any, Any, None]:
        nonlocal factory_calls
        factory_calls += 1

        async def operation() -> None:
            await release.wait()

        return operation()

    job_id = uuid4()
    first = supervisor.start(job_id, operation_factory, name="first")

    with pytest.raises(JobTaskConflictError, match="already has an active task"):
        supervisor.start(job_id, operation_factory, name="duplicate")

    assert factory_calls == 1
    assert supervisor.tracked_count == 1
    assert supervisor.active_count == 1
    first.cancel()
    await supervisor.settle((first,))
    supervisor.discard(job_id)
    supervisor.clear()


@pytest.mark.asyncio
async def test_replacement_becomes_cancellation_target_while_prior_task_drains() -> None:
    supervisor = JobTaskSupervisor()
    prior_release = asyncio.Event()
    replacement_started = asyncio.Event()
    replacement_release = asyncio.Event()
    job_id = uuid4()

    async def prior_operation() -> None:
        await prior_release.wait()

    async def replacement_operation() -> None:
        replacement_started.set()
        await replacement_release.wait()

    prior = supervisor.start(job_id, prior_operation, name="prior")
    replacement = supervisor.replace(
        job_id,
        replacement_operation,
        name="replacement",
    )
    await replacement_started.wait()

    assert supervisor.tracked_count == 1
    assert supervisor.active_count == 2
    assert supervisor.cancel(job_id) is replacement

    prior_release.set()
    await supervisor.settle((prior, replacement))
    assert prior.cancelled() is False
    assert replacement.cancelled() is True
    assert supervisor.active_count == 0
    supervisor.discard(job_id)
    supervisor.clear()


@pytest.mark.asyncio
async def test_cancel_all_drains_every_running_task_before_clear() -> None:
    supervisor = JobTaskSupervisor()
    release = asyncio.Event()

    async def operation() -> None:
        await release.wait()

    tasks = tuple(
        supervisor.start(uuid4(), operation, name=f"job-{index}")
        for index in range(32)
    )

    cancelled = supervisor.cancel_all()
    await supervisor.settle(cancelled)

    assert set(cancelled) == set(tasks)
    assert all(task.cancelled() for task in tasks)
    assert supervisor.active_count == 0
    supervisor.clear()
    assert supervisor.tracked_count == 0


@pytest.mark.asyncio
async def test_escaped_error_log_never_contains_provider_message(
    caplog: pytest.LogCaptureFixture,
) -> None:
    supervisor = JobTaskSupervisor()
    secret = "nvapi-sensitive-provider-detail"

    async def operation() -> None:
        raise RuntimeError(secret)

    with caplog.at_level(logging.ERROR, logger="aegis_core.job_tasks"):
        task = supervisor.start(uuid4(), operation, name="failing-job")
        await supervisor.settle((task,))

    rendered = caplog.text
    assert "RuntimeError" in rendered
    assert "failing-job" in rendered
    assert secret not in rendered
    supervisor.clear()


@pytest.mark.asyncio
async def test_repeated_cancel_and_disconnected_waiter_preserve_cleanup() -> None:
    supervisor = JobTaskSupervisor()
    started, cleaning, release = asyncio.Event(), asyncio.Event(), asyncio.Event()
    cleaned = False

    async def operation() -> None:
        nonlocal cleaned
        started.set()
        try:
            await asyncio.Event().wait()
        finally:
            cleaning.set()
            await release.wait()
            cleaned = True

    job_id = uuid4()
    task = supervisor.start(job_id, operation, name="slow-cleanup")
    await started.wait()
    supervisor.cancel(job_id)
    await cleaning.wait()
    waiter = asyncio.create_task(supervisor.settle((task,)))
    try:
        await asyncio.sleep(0)
        supervisor.cancel(job_id)
        supervisor.cancel_all()
        waiter.cancel()
        await asyncio.gather(waiter, return_exceptions=True)
        assert task.cancelling() == 1
        assert not task.done()
    finally:
        release.set()
        await supervisor.settle((task,))
    assert cleaned
    assert supervisor.active_count == 0
