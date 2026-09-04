from __future__ import annotations

import asyncio
import inspect
import signal
from collections.abc import Awaitable, Callable
from typing import Any, Protocol, TypeVar


class ForeverServer(Protocol):
    async def serve_forever(self) -> None: ...


TaskResult = TypeVar("TaskResult")
FailureHandler = Callable[[str, BaseException], None]


class BackgroundTaskSupervisor:
    """Own daemon background tasks and close them as one lifecycle unit."""

    def __init__(self, *, on_failure: FailureHandler | None = None) -> None:
        self._on_failure = on_failure
        self._tasks: set[asyncio.Task[Any]] = set()
        self._closed = False

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
            self._on_failure(task.get_name(), error)

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        pending = tuple(self._tasks)
        self._tasks.clear()
        for task in pending:
            task.cancel()
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)


async def serve_until_shutdown(daemon: ForeverServer) -> None:
    """Serve until SIGTERM or until the server exits unexpectedly."""

    loop = asyncio.get_running_loop()
    shutdown = asyncio.Event()
    signal_installed = False
    try:
        loop.add_signal_handler(signal.SIGTERM, shutdown.set)
        signal_installed = True
    except (NotImplementedError, RuntimeError, ValueError):
        pass
    if not signal_installed:
        await daemon.serve_forever()
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
        loop.remove_signal_handler(signal.SIGTERM)
