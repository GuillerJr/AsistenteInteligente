from __future__ import annotations

import asyncio
from collections.abc import Awaitable
from typing import Any


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
