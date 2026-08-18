from __future__ import annotations

import asyncio
import re
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from aegis_core.ipc.protocol import IpcRequest
from aegis_core.ipc.server import IpcHandlerResult, IpcMethodHandler
from aegis_core.memory.contracts import NAMESPACE_PATTERN, TAG_PATTERN, MemoryKind
from aegis_core.memory.retrieval import HybridMemoryRetriever
from aegis_core.memory.sqlite import (
    MemoryCapacityError,
    MemoryNotFoundError,
    MemoryQueryError,
    MemoryStoreError,
    SecretMaterialError,
    SQLiteMemoryStore,
)


class PutMemoryPayload(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    namespace: str = Field(pattern=NAMESPACE_PATTERN)
    kind: MemoryKind
    content: str = Field(min_length=1, max_length=16_384)
    source: str | None = Field(default=None, min_length=1, max_length=256)
    tags: tuple[str, ...] = Field(default=(), max_length=16)

    @field_validator("tags")
    @classmethod
    def tags_must_be_normalized(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if len(set(value)) != len(value):
            raise ValueError("memory tags must be unique")
        for tag in value:
            if not re.fullmatch(TAG_PATTERN, tag):
                raise ValueError("invalid memory tag")
        return value


class GetMemoryPayload(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    namespace: str = Field(pattern=NAMESPACE_PATTERN)
    memory_id: UUID


class SearchMemoryPayload(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    namespace: str = Field(pattern=NAMESPACE_PATTERN)
    query: str = Field(min_length=1, max_length=2_048)
    limit: int = Field(default=5, ge=1, le=10)


class MemoryIpcService:
    METHODS = frozenset({"memory.put", "memory.get", "memory.search", "memory.delete"})

    def __init__(
        self,
        store: SQLiteMemoryStore,
        *,
        retriever: HybridMemoryRetriever | None = None,
    ) -> None:
        self._store = store
        self._retriever = retriever or HybridMemoryRetriever(store)

    def handlers(self) -> dict[str, IpcMethodHandler]:
        return {method: self.handle for method in self.METHODS}

    async def handle(self, request: IpcRequest) -> IpcHandlerResult:
        try:
            if request.method == "memory.put":
                payload = PutMemoryPayload.model_validate(request.payload)
                record = await asyncio.to_thread(
                    self._store.put,
                    namespace=payload.namespace,
                    kind=payload.kind,
                    content=payload.content,
                    source=payload.source,
                    tags=payload.tags,
                )
                response_payload = record.model_dump(mode="json")
                embedding_status = await self._retriever.index(record)
                response_payload["embedding_status"] = embedding_status.value
            elif request.method == "memory.get":
                payload = GetMemoryPayload.model_validate(request.payload)
                record = await asyncio.to_thread(
                    self._store.get,
                    namespace=payload.namespace,
                    memory_id=payload.memory_id,
                )
                response_payload = record.model_dump(mode="json")
            elif request.method == "memory.search":
                payload = SearchMemoryPayload.model_validate(request.payload)
                hits = await self._retriever.retrieve(
                    namespace=payload.namespace,
                    query=payload.query,
                    limit=payload.limit,
                )
                response_payload = {
                    "hits": [hit.model_dump(mode="json") for hit in hits],
                    "retrieval_mode": (
                        "hybrid" if self._retriever.remote_embeddings_enabled else "lexical"
                    ),
                }
            elif request.method == "memory.delete":
                payload = GetMemoryPayload.model_validate(request.payload)
                await asyncio.to_thread(
                    self._store.delete,
                    namespace=payload.namespace,
                    memory_id=payload.memory_id,
                )
                response_payload = {
                    "memory_id": str(payload.memory_id),
                    "status": "deleted",
                }
            else:
                return IpcHandlerResult(ok=False, error_code="method_not_found")
        except (ValidationError, ValueError, MemoryQueryError):
            return IpcHandlerResult(ok=False, error_code="invalid_payload")
        except MemoryNotFoundError:
            return IpcHandlerResult(ok=False, error_code="memory_not_found")
        except MemoryCapacityError:
            return IpcHandlerResult(ok=False, error_code="memory_capacity_reached")
        except SecretMaterialError:
            return IpcHandlerResult(ok=False, error_code="memory_secret_rejected")
        except MemoryStoreError:
            return IpcHandlerResult(ok=False, error_code="memory_unavailable")
        return IpcHandlerResult(ok=True, payload=response_payload)
