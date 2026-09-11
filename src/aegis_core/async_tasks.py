from __future__ import annotations

import asyncio
import threading
from collections.abc import Awaitable, Callable
from typing import Any, ParamSpec, TypeVar

P = ParamSpec("P")
T = TypeVar("T")


async def run_blocking_owned(
    operation: Callable[P, T],
    *args: P.args,
    **kwargs: P.kwargs,
) -> T:
    """Keep a bounded blocking operation owned until its worker really exits.

    Cancelling to_thread alone abandons an already-running thread. Shield and
    drain it before releasing the caller's locks or reporting cancellation.
    Repeated cancellation cannot skip cleanup; queued work is skipped if it has
    not started. The operation MUST enforce its own I/O/process timeout.
    """
    cancelled = threading.Event()

    def invoke() -> T:
        if cancelled.is_set():
            raise asyncio.CancelledError
        return operation(*args, **kwargs)

    worker = asyncio.create_task(asyncio.to_thread(invoke), name="owned-blocking-operation")
    try:
        return await asyncio.shield(worker)
    except asyncio.CancelledError:
        cancelled.set()
        while not worker.done():
            try:
                await asyncio.shield(worker)
            except asyncio.CancelledError:
                continue
            except Exception:
                break
        if not worker.cancelled():
            worker.exception()  # Retrieve late failures without replacing cancellation.
        raise


async def gather_owned(*awaitables: Awaitable[Any]) -> tuple[Any, ...]:
    """Join request-owned work; failure/cancellation drains every sibling.

    Preserve the original exception (including CancelledError), so job failure
    classification does not lose its meaning inside an ExceptionGroup. Optional
    operations must handle their own recoverable errors before joining here.
    """
    tasks = tuple(asyncio.ensure_future(operation) for operation in awaitables)
    try:
        return tuple(await asyncio.gather(*tasks))
    finally:
        for task in tasks:
            if not task.done():
                task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
