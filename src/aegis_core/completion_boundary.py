"""Optional inference is an external failure boundary, parsing remains strict."""
from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable

LOGGER = logging.getLogger(__name__)


async def optional_completion(
    complete: Callable[[list[dict[str, str]]], Awaitable[str]],
    messages: list[dict[str, str]],
) -> str:
    """Return empty content when an optional provider fails, never swallow cancellation.

    Error messages and tracebacks can contain prompts, addresses or credentials.
    Only the exception's class name crosses this diagnostic boundary.
    """
    try:
        content = await complete(messages)
    except Exception as error:  # External model/callback boundary, not local parsing.
        LOGGER.warning("optional_completion_failed error_type=%s", type(error).__name__)
        return ""
    if not isinstance(content, str):
        LOGGER.warning("optional_completion_failed error_type=TypeError")
        return ""
    return content
