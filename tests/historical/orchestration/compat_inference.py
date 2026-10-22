"""Historical hybrid adapter used only by compatibility graphs and benchmarks.

The daemon constructs build_nvidia_swarm_graph, which never imports this module.
Keeping this adapter lets the regression suite exercise former privacy boundaries
without linking AFM/MLX inference into the production graph.
"""
from __future__ import annotations

import asyncio
from collections.abc import Callable
from typing import Any
from uuid import UUID

from aegis_core.contracts import AgentResult, AgentRole
from aegis_core.orchestration.routing import LOCAL_PROVIDER_RETRY_SECONDS


def legacy_completion(provider, local_provider):
    local_retry_after = 0.0

    async def complete(
        role: AgentRole,
        *,
        prefer_local: bool = False,
        allow_remote_fallback: bool = True,
        local_messages: list[dict[str, Any]] | None = None,
        stream_callback: Callable[[str], None] | None = None,
        audit_request_id: UUID | None = None,
        **kwargs: Any,
    ) -> AgentResult:
        nonlocal local_retry_after
        cascade = getattr(provider, "complete_cascade", None)
        if callable(cascade):
            cascade_kwargs = dict(kwargs)
            remote_messages = cascade_kwargs.pop("messages", None)
            if not isinstance(remote_messages, list):
                raise RuntimeError("hybrid provider messages are invalid")
            return await cascade(
                role=role,
                local_messages=local_messages or remote_messages,
                remote_messages=remote_messages,
                allow_remote_fallback=allow_remote_fallback,
                request_id=audit_request_id,
                on_delta=stream_callback,
                **cascade_kwargs,
            )
        loop = asyncio.get_running_loop()
        if (
            prefer_local
            and local_provider is not None
            and loop.time() >= local_retry_after
        ):
            local_emitted = False

            def publish_local(delta: str) -> None:
                nonlocal local_emitted
                local_emitted = True
                if stream_callback is not None:
                    stream_callback(delta)

            try:
                local_kwargs = dict(kwargs)
                if local_messages is not None:
                    local_kwargs["messages"] = local_messages
                local_stream = getattr(local_provider, "complete_stream", None)
                if stream_callback is not None and callable(local_stream):
                    result = await local_stream(
                        role=role,
                        on_delta=publish_local,
                        **local_kwargs,
                    )
                else:
                    result = await local_provider.complete(role=role, **local_kwargs)
                    if stream_callback is not None and result.content:
                        stream_callback(result.content)
                local_retry_after = 0.0
                return result
            except (OSError, RuntimeError):
                if local_emitted:
                    raise
                local_retry_after = loop.time() + LOCAL_PROVIDER_RETRY_SECONDS
        if not allow_remote_fallback:
            raise RuntimeError("local-only inference is unavailable")
        remote_stream = getattr(provider, "complete_stream", None)
        if stream_callback is not None and callable(remote_stream):
            return await remote_stream(
                role=role,
                on_delta=stream_callback,
                **kwargs,
            )
        result = await provider.complete(role=role, **kwargs)
        if stream_callback is not None and result.content:
            stream_callback(result.content)
        return result

    return complete
