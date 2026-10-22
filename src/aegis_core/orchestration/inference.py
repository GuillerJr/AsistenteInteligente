"""NVIDIA completion boundary. There is no local or alternate provider fallback."""
from __future__ import annotations

from collections.abc import Callable
from typing import Any
from uuid import UUID

from aegis_core.brain.errors import RemoteProviderUnavailableError
from aegis_core.contracts import AgentResult, AgentRole
from aegis_core.providers.base import require_complete_response


def create_completion(provider, activity):
    async def complete(
        role: AgentRole,
        *,
        review_dialogue: bool = False,
        stream_callback: Callable[[str], None] | None = None,
        audit_request_id: UUID | None = None,
        **kwargs: Any,
    ) -> AgentResult:
        async with activity.track(role):
            remote_only = getattr(provider, "complete_nvidia_only", None)
            if not callable(remote_only):
                raise RemoteProviderUnavailableError("NVIDIA-only provider unavailable")
            result = await remote_only(
                role=role, request_id=audit_request_id, on_delta=stream_callback,
                **({"review_dialogue": True} if review_dialogue else {}), **kwargs,
            )
            return require_complete_response(result)

    return complete
