from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable, Coroutine
from typing import Any
from uuid import UUID

LOGGER = logging.getLogger(__name__)
TaskFactory = Callable[[], Coroutine[Any, Any, None]]


class JobTaskConflictError(RuntimeError):
    """Raised when two primary tasks are attached to one job without replacement."""


class JobTaskSupervisor:
    """Owns bounded asyncio task creation, replacement, cancellation and draining."""

    def __init__(self) -> None:
        self._current: dict[UUID, asyncio.Task[None]] = {}
        self._running: set[asyncio.Task[None]] = set()

    @property
    def tracked_count(self) -> int:
        return len(self._current)

    @property
    def active_count(self) -> int:
        return len(self._running)

    def start(
        self,
        job_id: UUID,
        factory: TaskFactory,
        *,
        name: str,
    ) -> asyncio.Task[None]:
        existing = self._current.get(job_id)
        if existing is not None and not existing.done():
            raise JobTaskConflictError("job already has an active task")
        return self._launch(job_id, factory, name=name)

    def replace(self, job_id: UUID, factory: TaskFactory, *, name: str) -> asyncio.Task[None]:
        if job_id not in self._current:
            raise JobTaskConflictError("job has no task to replace")
        # The previous graph coroutine may still be returning after publishing
        # AWAITING_CONFIRMATION. Keep it supervised, but make the approved tool
        # task the only cancellable task associated with the job.
        return self._launch(job_id, factory, name=name)

    def cancel(self, job_id: UUID) -> asyncio.Task[None] | None:
        task = self._current.get(job_id)
        if task is not None and not task.cancelling():
            task.cancel()
        return task

    def cancel_all(self) -> tuple[asyncio.Task[None], ...]:
        tasks = tuple(self._running)
        for task in tasks:
            if not task.cancelling():
                task.cancel()
        return tasks

    def discard(self, job_id: UUID) -> None:
        self._current.pop(job_id, None)

    def clear(self) -> None:
        if self._running:
            raise RuntimeError("cannot clear supervisor with active tasks")
        self._current.clear()

    @staticmethod
    async def settle(tasks: tuple[asyncio.Task[None], ...]) -> None:
        if tasks:
            # Disconnecting an IPC cancellation waiter must not interrupt the
            # job's own cleanup (or its atomic conversation commit) a second time.
            await asyncio.gather(*(asyncio.shield(task) for task in tasks), return_exceptions=True)

    def _launch(
        self,
        job_id: UUID,
        factory: TaskFactory,
        *,
        name: str,
    ) -> asyncio.Task[None]:
        coroutine = factory()
        try:
            task = asyncio.create_task(coroutine, name=name)
        except BaseException:
            coroutine.close()
            raise
        self._current[job_id] = task
        self._running.add(task)
        task.add_done_callback(self._task_finished)
        return task

    def _task_finished(self, task: asyncio.Task[None]) -> None:
        self._running.discard(task)
        if task.cancelled():
            return
        error = task.exception()
        if error is not None:
            # Never log exception text: provider and tool errors can contain secrets.
            LOGGER.error(
                "supervised job task escaped error_type=%s task_name=%s",
                type(error).__name__,
                task.get_name(),
            )
