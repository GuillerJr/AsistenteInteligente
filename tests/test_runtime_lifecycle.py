from __future__ import annotations

import asyncio

import pytest

from aegis_core.runtime import BackgroundTaskSupervisor


@pytest.mark.asyncio
async def test_supervisor_cancels_every_active_task() -> None:
    cancelled = [asyncio.Event(), asyncio.Event()]

    async def worker(index: int) -> None:
        try:
            await asyncio.Event().wait()
        finally:
            cancelled[index].set()

    supervisor = BackgroundTaskSupervisor()
    supervisor.create(worker(0), name="first-worker")
    supervisor.create(worker(1), name="second-worker")
    await asyncio.sleep(0)

    await supervisor.close()

    assert all(event.is_set() for event in cancelled)
    assert supervisor.active_count == 0


@pytest.mark.asyncio
async def test_supervisor_reports_unexpected_failure() -> None:
    failures: list[tuple[str, str]] = []
    notified = asyncio.Event()

    async def worker() -> None:
        raise ValueError("worker failed")

    def record_failure(name: str, error: BaseException) -> None:
        failures.append((name, type(error).__name__))
        notified.set()

    supervisor = BackgroundTaskSupervisor(
        on_failure=record_failure,
    )
    supervisor.create(worker(), name="failing-worker")
    await asyncio.wait_for(notified.wait(), timeout=1)

    assert failures == [("failing-worker", "ValueError")]
    assert supervisor.active_count == 0
    await supervisor.close()


@pytest.mark.asyncio
async def test_supervisor_rejects_tasks_after_close() -> None:
    async def worker() -> None:
        return None

    supervisor = BackgroundTaskSupervisor()
    await supervisor.close()

    with pytest.raises(RuntimeError, match="supervisor is closed"):
        supervisor.create(worker(), name="late-worker")
