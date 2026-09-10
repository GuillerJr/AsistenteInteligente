from __future__ import annotations

import asyncio
import signal

import pytest

from aegis_core.runtime import BackgroundTaskSupervisor
from aegis_core.runtime.lifecycle import serve_until_shutdown


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


@pytest.mark.asyncio
async def test_concurrent_close_waits_for_the_same_cleanup() -> None:
    started = asyncio.Event()
    cleaning = asyncio.Event()
    release = asyncio.Event()

    async def worker() -> None:
        started.set()
        try:
            await asyncio.Event().wait()
        finally:
            cleaning.set()
            await release.wait()

    supervisor = BackgroundTaskSupervisor()
    supervisor.create(worker(), name="slow-cleanup")
    await started.wait()
    first = asyncio.create_task(supervisor.close())
    await cleaning.wait()
    second = asyncio.create_task(supervisor.close())
    try:
        await asyncio.sleep(0)
        assert not second.done()
        assert supervisor.active_count == 1
        first.cancel()
        await asyncio.gather(first, return_exceptions=True)
        assert not second.done()
    finally:
        release.set()
        await asyncio.gather(first, second, return_exceptions=True)
    assert supervisor.active_count == 0


@pytest.mark.asyncio
async def test_failure_reporter_error_is_private_and_does_not_escape(caplog) -> None:
    def failing_reporter(_: str, error: BaseException) -> None:
        raise OSError("private-key-in-error") from error

    async def worker() -> None:
        raise ValueError("private-prompt-in-error")

    supervisor = BackgroundTaskSupervisor(on_failure=failing_reporter)
    task = supervisor.create(worker(), name="worker")
    await asyncio.gather(task, return_exceptions=True)
    assert "background_failure_reporting_failed error_type=OSError" in caplog.text
    assert "private-key" not in caplog.text
    assert "private-prompt" not in caplog.text
    await supervisor.close()


@pytest.mark.asyncio
async def test_shutdown_requested_during_startup_does_not_enter_service() -> None:
    class Server:
        async def serve_forever(self) -> None:
            pytest.fail("shutdown was already requested before startup completed")

    shutdown = asyncio.Event()
    shutdown.set()
    await serve_until_shutdown(Server(), shutdown=shutdown)


@pytest.mark.asyncio
async def test_daemon_owns_signal_for_initialization_and_cleanup(monkeypatch) -> None:
    from aegis_core.runtime.daemon import run_daemon

    loop = asyncio.get_running_loop()
    handlers = {}
    phases = []

    def install(selected_signal, callback) -> None:
        handlers[selected_signal] = callback

    def remove(selected_signal) -> bool:
        handlers.pop(selected_signal)
        return True

    async def initialize_and_clean(shutdown: asyncio.Event) -> int:
        phases.append("initialize")
        handlers[signal.SIGTERM]()
        assert shutdown.is_set()
        await asyncio.sleep(0)
        phases.append("cleanup")
        handlers[signal.SIGTERM]()
        assert shutdown.is_set()
        return 0

    monkeypatch.setattr(loop, "add_signal_handler", install)
    monkeypatch.setattr(loop, "remove_signal_handler", remove)
    monkeypatch.setattr("aegis_core.runtime.daemon._run_daemon", initialize_and_clean)
    assert await run_daemon() == 0
    assert phases == ["initialize", "cleanup"]
    assert not handlers
