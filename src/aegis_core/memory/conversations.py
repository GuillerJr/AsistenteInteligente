from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from aegis_core.async_tasks import run_blocking_owned
from aegis_core.ipc.protocol import IpcRequest
from aegis_core.ipc.server import IpcHandlerResult, IpcMethodHandler
from aegis_core.memory.contracts import ConversationRecord, ConversationTurn
from aegis_core.memory.errors import (
    ConversationCapacityError,
    MemoryNotFoundError,
    MemoryQueryError,
    MemoryStoreError,
    SecretMaterialError,
)
from aegis_core.memory.sqlite import SQLiteMemoryStore


class CreateConversationPayload(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    title: str | None = Field(default=None, min_length=1, max_length=128)


class ConversationIdPayload(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    conversation_id: UUID


class ConversationHistoryPayload(ConversationIdPayload):
    limit: int = Field(default=10, ge=1, le=10)


class ConversationCoordinator:
    def __init__(
        self,
        store: SQLiteMemoryStore,
        *,
        namespace: str,
        history_limit: int = 12,
        max_conversations: int = 1_000,
        max_turns: int = 1_000,
    ) -> None:
        if not 1 <= history_limit <= 50:
            raise ValueError("conversation history limit is out of range")
        if not 2 <= max_turns <= 10_000:
            raise ValueError("conversation capacity is out of range")
        if not 1 <= max_conversations <= 100_000:
            raise ValueError("conversation count capacity is out of range")
        self._store = store
        self._namespace = namespace
        self._history_limit = history_limit
        self._max_conversations = max_conversations
        self._max_turns = max_turns
        self._locks: dict[UUID, asyncio.Lock] = {}
        self._lock_users: dict[UUID, int] = {}

    @property
    def namespace(self) -> str:
        return self._namespace

    async def ensure_exists(self, conversation_id: UUID) -> None:
        await run_blocking_owned(
            self._store.get_conversation,
            namespace=self._namespace,
            conversation_id=conversation_id,
        )

    async def create(self, title: str | None = None) -> ConversationRecord:
        return await run_blocking_owned(
            self._store.create_conversation,
            namespace=self._namespace,
            title=title,
            max_conversations=self._max_conversations,
        )

    @asynccontextmanager
    async def serialized(self, conversation_id: UUID) -> AsyncIterator[None]:
        async with self._locked(conversation_id):
            await self.ensure_exists(conversation_id)
            yield

    async def history(self, conversation_id: UUID) -> tuple[ConversationTurn, ...]:
        return await run_blocking_owned(
            self._store.conversation_history,
            namespace=self._namespace,
            conversation_id=conversation_id,
            limit=self._history_limit,
        )

    async def record_exchange(
        self,
        conversation_id: UUID,
        *,
        user_content: str,
        assistant_content: str,
    ) -> bool:
        try:
            await run_blocking_owned(
                self._store.append_conversation_exchange,
                namespace=self._namespace,
                conversation_id=conversation_id,
                user_content=user_content,
                assistant_content=assistant_content,
                max_turns=self._max_turns,
            )
        except SecretMaterialError:
            return False
        return True

    async def delete(self, conversation_id: UUID) -> None:
        await self.ensure_exists(conversation_id)
        async with self._locked(conversation_id):
            await run_blocking_owned(
                self._store.delete_conversation,
                namespace=self._namespace,
                conversation_id=conversation_id,
            )

    @asynccontextmanager
    async def _locked(self, conversation_id: UUID) -> AsyncIterator[None]:
        # Count waiters as well as holders so cleanup never splits one ID into
        # two locks. Registration/cleanup have no await and are event-loop atomic.
        lock = self._locks.setdefault(conversation_id, asyncio.Lock())
        self._lock_users[conversation_id] = self._lock_users.get(conversation_id, 0) + 1
        try:
            async with lock:
                yield
        finally:
            self._lock_users[conversation_id] -= 1
            if not self._lock_users[conversation_id]:
                del self._lock_users[conversation_id]
                del self._locks[conversation_id]


class ConversationIpcService:
    METHODS = frozenset({"conversations.create", "conversations.history", "conversations.delete"})

    def __init__(
        self,
        store: SQLiteMemoryStore,
        coordinator: ConversationCoordinator,
    ) -> None:
        self._store = store
        self._coordinator = coordinator

    def handlers(self) -> dict[str, IpcMethodHandler]:
        return {method: self.handle for method in self.METHODS}

    async def handle(self, request: IpcRequest) -> IpcHandlerResult:
        try:
            if request.method == "conversations.create":
                payload = CreateConversationPayload.model_validate(request.payload)
                conversation = await self._coordinator.create(payload.title)
                response_payload = conversation.model_dump(mode="json")
            elif request.method == "conversations.history":
                payload = ConversationHistoryPayload.model_validate(request.payload)
                turns = await asyncio.to_thread(
                    self._store.conversation_history,
                    namespace=self._coordinator.namespace,
                    conversation_id=payload.conversation_id,
                    limit=payload.limit,
                )
                response_payload = {
                    "conversation_id": str(payload.conversation_id),
                    "turns": [self._public_turn(turn) for turn in turns],
                }
            elif request.method == "conversations.delete":
                payload = ConversationIdPayload.model_validate(request.payload)
                await self._coordinator.delete(payload.conversation_id)
                response_payload = {
                    "conversation_id": str(payload.conversation_id),
                    "status": "deleted",
                }
            else:
                return IpcHandlerResult(ok=False, error_code="method_not_found")
        except (ValidationError, ValueError, MemoryQueryError):
            return IpcHandlerResult(ok=False, error_code="invalid_payload")
        except MemoryNotFoundError:
            return IpcHandlerResult(ok=False, error_code="conversation_not_found")
        except ConversationCapacityError:
            return IpcHandlerResult(ok=False, error_code="conversation_capacity_reached")
        except MemoryStoreError:
            return IpcHandlerResult(ok=False, error_code="conversation_unavailable")
        return IpcHandlerResult(ok=True, payload=response_payload)

    @staticmethod
    def _public_turn(turn: ConversationTurn) -> dict[str, object]:
        payload = turn.model_dump(mode="json")
        payload["truncated"] = False
        if (
            len(json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))
            <= 4_096
        ):
            return payload
        lower = 0
        upper = len(turn.content)
        while lower < upper:
            midpoint = (lower + upper + 1) // 2
            payload["content"] = turn.content[:midpoint]
            payload["truncated"] = True
            size = len(
                json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
            )
            if size <= 4_096:
                lower = midpoint
            else:
                upper = midpoint - 1
        payload["content"] = turn.content[:lower]
        payload["truncated"] = True
        return payload
