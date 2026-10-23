"""Bounded NVIDIA embeddings for the existing index; no local inference fallback."""

from __future__ import annotations

import asyncio
import math
from collections.abc import Sequence
from typing import Protocol

from aegis_core.privacy import redact_secrets_for_remote
from aegis_core.providers.base import EmbeddingBatch, EmbeddingInputType, EmbeddingProviderError

MEMORY_EMBEDDING_DIMENSIONS = 384
MEMORY_EMBEDDING_VERSION = "memory-v1-mrl384-l2"
# NVIDIA's model card documents prefix truncation followed by L2 normalization:
# https://huggingface.co/nvidia/Nemotron-3.5-Embed-1B-BF16
_MATRYOSHKA_MODELS = {"nvidia/nemotron-3.5-embed-1b": 2048}


class NvidiaEmbeddingTransport(Protocol):
    @property
    def model_id(self) -> str: ...

    async def embed(
        self, texts: Sequence[str], *, input_type: EmbeddingInputType
    ) -> EmbeddingBatch: ...


class NvidiaMemoryEmbeddingClient:
    """Reuse NVIDIA's authenticated transport, with a versioned index identity.

    The database's model_id column already isolates encoder spaces. A distinct
    identity prevents old folded vectors from matching Matryoshka 384-d vectors,
    without rewriting or migrating any owner's database.
    """

    dimensions = MEMORY_EMBEDDING_DIMENSIONS

    def __init__(
        self, client: NvidiaEmbeddingTransport, *, timeout_seconds: float = 8.0
    ) -> None:
        if not math.isfinite(timeout_seconds) or not 0 < timeout_seconds <= 8:
            raise ValueError("memory embedding timeout is invalid")
        self._client = client
        self._timeout = timeout_seconds
        self._source_model_id = client.model_id
        self.model_id = f"{self._source_model_id}@{MEMORY_EMBEDDING_VERSION}"
        if not self._source_model_id or len(self.model_id) > 256:
            raise ValueError("memory embedding model identity is invalid")

    async def embed(
        self, texts: Sequence[str], *, input_type: EmbeddingInputType
    ) -> EmbeddingBatch:
        if self._client.model_id != self._source_model_id:
            raise EmbeddingProviderError("memory embedding encoder changed")
        sanitized = tuple(redact_secrets_for_remote(text).text for text in texts)
        if input_type == EmbeddingInputType.PASSAGE:
            # Deterministic remote projection, like the API's truncate=END. Source
            # content and its hash remain intact; a long node cannot block backfill.
            sanitized = tuple(
                text.encode("utf-8")[:16_384].decode("utf-8", errors="ignore")
                for text in sanitized
            )
        try:
            async with asyncio.timeout(self._timeout):
                for attempt in range(2):
                    try:
                        batch = await self._client.embed(sanitized, input_type=input_type)
                        break
                    except EmbeddingProviderError as error:
                        status = getattr(error, "status_code", None)
                        if attempt or type(status) is not int or not 500 <= status < 600:
                            raise
                        await asyncio.sleep(0.15)
        except TimeoutError as error:
            raise EmbeddingProviderError("memory embedding deadline exceeded") from error
        except ValueError as error:
            # Oversized queries retain lexical recall without breaking the chat.
            raise EmbeddingProviderError("memory embedding input is unsupported") from error
        supported_dimensions = {self.dimensions}
        if self._source_model_id in _MATRYOSHKA_MODELS:
            supported_dimensions.add(_MATRYOSHKA_MODELS[self._source_model_id])
        if (
            batch.model_id != self._source_model_id
            or batch.dimensions not in supported_dimensions
            or len(batch.vectors) != len(sanitized)
        ):
            raise EmbeddingProviderError("memory embedding response is incompatible")
        vectors = []
        for vector in batch.vectors:
            # Obtain the native output and apply the model's supported
            # MRL prefix here; never hash/fold arbitrary encoders into this space.
            prefix = vector[:self.dimensions]
            norm = math.sqrt(math.fsum(value * value for value in prefix))
            if norm <= 0:
                raise EmbeddingProviderError("memory embedding response has a zero vector")
            vectors.append(tuple(value / norm for value in prefix))
        return EmbeddingBatch(model_id=self.model_id, vectors=tuple(vectors))
