from __future__ import annotations

import asyncio
import re
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from aegis_core.ipc.protocol import IpcRequest
from aegis_core.ipc.server import IpcHandlerResult, IpcMethodHandler
from aegis_core.memory.contracts import NAMESPACE_PATTERN, TAG_PATTERN, MemoryKind
from aegis_core.memory.errors import (
    DecryptionAuthError,
    MemoryCapacityError,
    MemoryNotFoundError,
    MemoryQueryError,
    MemoryStoreError,
    SecretMaterialError,
)
from aegis_core.memory.graph_service import GraphRAGService
from aegis_core.memory.retrieval import HybridMemoryRetriever
from aegis_core.memory.sqlite import SQLiteMemoryStore


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


class ResetMemorySessionPayload(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    namespace: str = Field(pattern=NAMESPACE_PATTERN)
    session_id: UUID
    confirm: bool

    @field_validator("confirm")
    @classmethod
    def reset_must_be_explicit(cls, value: bool) -> bool:
        if value is not True:
            raise ValueError("session reset requires explicit confirmation")
        return value


class MemoryIpcService:
    METHODS = frozenset(
        {"memory.put", "memory.get", "memory.search", "memory.delete", "memory.session.reset"}
    )

    def __init__(
        self,
        store: SQLiteMemoryStore,
        *,
        retriever: HybridMemoryRetriever | None = None,
        graph_service: GraphRAGService | None = None,
    ) -> None:
        self._store = store
        self._retriever = retriever or HybridMemoryRetriever(store)
        self._graph_service = graph_service or GraphRAGService(store)

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
                hits, graph_result = await self._retriever.retrieve_context(
                    namespace=payload.namespace,
                    query=payload.query,
                    limit=payload.limit,
                )
                response_payload = {
                    "hits": [hit.model_dump(mode="json") for hit in hits],
                    "graph_context": graph_result.markdown if graph_result else "",
                    "graph_traversal_ms": (
                        round(graph_result.traversal_ms, 3) if graph_result else None
                    ),
                    "retrieval_mode": (
                        "graphrag" if graph_result and graph_result.markdown else "lexical"
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
            elif request.method == "memory.session.reset":
                payload = ResetMemorySessionPayload.model_validate(request.payload)
                reset = await self._graph_service.reset_session(
                    namespace=payload.namespace,
                    session_id=payload.session_id,
                )
                response_payload = {
                    "session_id": str(payload.session_id),
                    "status": "reset",
                    "conversation_deleted": reset.conversation_deleted,
                    "memories_deleted": reset.memories_deleted,
                    "cached_seed_sets_cleared": reset.cached_seed_sets_cleared,
                }
            else:
                return IpcHandlerResult(ok=False, error_code="method_not_found")
        except DecryptionAuthError:
            return IpcHandlerResult(ok=False, error_code="security_compromised")
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
