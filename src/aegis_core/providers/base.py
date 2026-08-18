from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Protocol

from aegis_core.contracts import AgentResult, AgentRole


class EmbeddingProviderError(RuntimeError):
    """Raised when an embedding provider is temporarily or permanently unavailable."""


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


class EmbeddingInputType(StrEnum):
    PASSAGE = "passage"
    QUERY = "query"


@dataclass(frozen=True, slots=True)
class EmbeddingBatch:
    model_id: str
    vectors: tuple[tuple[float, ...], ...]

    def __post_init__(self) -> None:
        if not self.model_id or not self.vectors:
            raise ValueError("embedding batch cannot be empty")
        dimensions = len(self.vectors[0])
        if not 1 <= dimensions <= 8_192:
            raise ValueError("embedding dimensions are out of range")
        if any(len(vector) != dimensions for vector in self.vectors):
            raise ValueError("embedding vectors must have equal dimensions")
        if any(
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not math.isfinite(value)
            for vector in self.vectors
            for value in vector
        ):
            raise ValueError("embedding vectors must contain finite values")

    @property
    def dimensions(self) -> int:
        return len(self.vectors[0])


class EmbeddingProvider(Protocol):
    async def embed(
        self,
        texts: Sequence[str],
        *,
        input_type: EmbeddingInputType,
    ) -> EmbeddingBatch: ...
