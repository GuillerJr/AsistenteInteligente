from __future__ import annotations

import asyncio
import inspect
import logging
import signal
from collections.abc import Awaitable, Callable
from typing import Any, Protocol, TypeVar


class ForeverServer(Protocol):
    async def serve_forever(self) -> None: ...


TaskResult = TypeVar("TaskResult")
FailureHandler = Callable[[str, BaseException], None]
LOGGER = logging.getLogger(__name__)


class BackgroundTaskSupervisor:
    """Own daemon background tasks and close them as one lifecycle unit."""

    def __init__(self, *, on_failure: FailureHandler | None = None) -> None:
        self._on_failure = on_failure
        self._tasks: set[asyncio.Task[Any]] = set()
        self._closed = False
        self._close_task: asyncio.Task[None] | None = None

    @property
    def active_count(self) -> int:
        return len(self._tasks)

    def create(
        self,
        operation: Awaitable[TaskResult],
        *,
        name: str,
    ) -> asyncio.Task[TaskResult]:
        if self._closed:
            if inspect.iscoroutine(operation):
                operation.close()
            raise RuntimeError("background task supervisor is closed")
        task = asyncio.create_task(operation, name=name)
        self._tasks.add(task)
        task.add_done_callback(self._task_finished)
        return task

    def _task_finished(self, task: asyncio.Task[Any]) -> None:
        self._tasks.discard(task)
        if self._closed or task.cancelled():
            return
        try:
            error = task.exception()
        except asyncio.CancelledError:
            return
        if error is not None and self._on_failure is not None:
            try:
                self._on_failure(task.get_name(), error)
            except Exception as reporting_error:
                # Logging must not leak provider error text or break the event loop.
                LOGGER.error(
                    "background_failure_reporting_failed error_type=%s",
                    type(reporting_error).__name__,
                )

    async def close(self) -> None:
        if self._close_task is None:
            self._closed = True
            self._close_task = asyncio.create_task(self._drain(), name="aegis-background-close")
        # A cancelled caller must not cancel workers again during their cleanup.
        await asyncio.shield(self._close_task)

    async def _drain(self) -> None:
        pending = tuple(self._tasks)
        for task in pending:
            task.cancel()
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)


async def serve_until_shutdown(
    daemon: ForeverServer, *, shutdown: asyncio.Event | None = None
) -> None:
    """Serve until SIGTERM or until the server exits unexpectedly."""

    loop = asyncio.get_running_loop()
    signal_installed = False
    if shutdown is None:
        shutdown = asyncio.Event()
        try:
            loop.add_signal_handler(signal.SIGTERM, shutdown.set)
            signal_installed = True
        except (NotImplementedError, RuntimeError, ValueError):
            await daemon.serve_forever()
            return
    if shutdown.is_set():
        return

    serve_task = asyncio.create_task(daemon.serve_forever(), name="aegis-uds-server")
    shutdown_task = asyncio.create_task(shutdown.wait(), name="aegis-sigterm-wait")
    try:
        done, _ = await asyncio.wait(
            {serve_task, shutdown_task},
            return_when=asyncio.FIRST_COMPLETED,
        )
        if serve_task in done:
            await serve_task
    finally:
        serve_task.cancel()
        shutdown_task.cancel()
        await asyncio.gather(serve_task, shutdown_task, return_exceptions=True)
        if signal_installed:
            loop.remove_signal_handler(signal.SIGTERM)
