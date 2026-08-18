from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any, Protocol

from aegis_core.contracts import AgentResult, AgentRole


class ChatProvider(Protocol):
    async def complete(
        self,
        *,
        role: AgentRole,
        messages: Sequence[Mapping[str, Any]],
        max_tokens: int | None = None,
        temperature: float | None = None,
        extra_body: Mapping[str, Any] | None = None,
    ) -> AgentResult: ...
