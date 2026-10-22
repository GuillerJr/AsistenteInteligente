"""NVIDIA-only completion boundary and explicit historical-test compatibility."""
from __future__ import annotations

from collections.abc import Callable
from typing import Any
from uuid import UUID

from aegis_core.brain.errors import RemoteProviderUnavailableError
from aegis_core.contracts import AgentResult, AgentRole
from aegis_core.providers.base import require_complete_response


def create_completion(provider, activity, *, nvidia_only: bool, local_provider=None):
    # Lazy compatibility import is unreachable from the production factory.
    legacy = None
    if not nvidia_only:
        from historical.orchestration.compat_inference import legacy_completion
        legacy = legacy_completion(provider, local_provider)

    async def complete(
        role: AgentRole,
        *,
        prefer_local: bool = False,
        allow_remote_fallback: bool = True,
        nvidia_only: bool = nvidia_only,
        review_dialogue: bool = False,
        local_messages: list[dict[str, Any]] | None = None,
        stream_callback: Callable[[str], None] | None = None,
        audit_request_id: UUID | None = None,
        **kwargs: Any,
    ) -> AgentResult:
        async with activity.track(role):
            if legacy is None or nvidia_only:
                remote_only = getattr(provider, "complete_nvidia_only", None)
                if not callable(remote_only):
                    raise RemoteProviderUnavailableError("NVIDIA-only provider unavailable")
                result = await remote_only(
                    role=role, request_id=audit_request_id, on_delta=stream_callback,
                    **({"review_dialogue": True} if review_dialogue else {}), **kwargs,
                )
            else:
                result = await legacy(
                    role, prefer_local=prefer_local, allow_remote_fallback=allow_remote_fallback,
                    local_messages=local_messages, stream_callback=stream_callback,
                    audit_request_id=audit_request_id, **kwargs,
                )
            return require_complete_response(result)

    return complete
