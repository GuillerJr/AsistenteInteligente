from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from aegis_core.async_tasks import run_blocking_owned
from aegis_core.contracts import DocumentInput, ToolExecutionResult
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
from aegis_core.memory.task_checkpoint import TaskCheckpoint, TaskRecovery

PRIVATE_CONVERSATION_FIELDS = {"task_checkpoint", "context_checkpoints", "task_recovery"}


class CreateConversationPayload(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    title: str | None = Field(default=None, min_length=1, max_length=128)


class ConversationIdPayload(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    conversation_id: UUID


class ConversationHistoryPayload(ConversationIdPayload):
    limit: int = Field(default=10, ge=1, le=10)


class RenameConversationPayload(ConversationIdPayload):
    title: str = Field(min_length=1, max_length=128)


class EmptyTrashPayload(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class ConversationMessagesPayload(ConversationIdPayload):
    limit: int = Field(default=20, ge=1, le=20)
    before_sequence: int | None = Field(default=None, ge=1)


class ConversationListPayload(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    limit: int = Field(default=30, ge=1, le=50)
    offset: int = Field(default=0, ge=0, le=100_000)
    query: str = Field(default="", max_length=128)
    archived: bool = False


class ConversationCoordinator:
    def __init__(
        self,
        store: SQLiteMemoryStore,
        *,
        namespace: str,
        history_limit: int = 12,
        context_max_bytes: int = 4_096,
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
        if not 512 <= context_max_bytes <= 16_384:
            raise ValueError("conversation context budget is out of range")
        self.context_max_bytes = context_max_bytes
        self._history_limit = history_limit
        self._max_conversations = max_conversations
        self._max_turns = max_turns
        self.context_manager = None
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

    async def recovery(self, conversation_id: UUID) -> TaskRecovery | None:
        record = await run_blocking_owned(
            self._store.get_conversation, namespace=self._namespace,
            conversation_id=conversation_id,
        )
        return record.task_recovery

    async def record_recovery(self, conversation_id: UUID, recovery: TaskRecovery,
                              tool_results=()) -> None:
        await run_blocking_owned(
            self._store.set_task_recovery, namespace=self._namespace,
            conversation_id=conversation_id, recovery=recovery, tool_results=tool_results,
        )

    async def recent(self) -> tuple[ConversationRecord, ...]:
        records, _ = await run_blocking_owned(
            self._store.list_conversations, namespace=self._namespace,
            archived=False, query="", offset=0, limit=10,
        )
        return records

    async def context_batch(self, conversation_id: UUID, *, after_sequence: int,
                            before_sequence: int) -> tuple[ConversationTurn, ...]:
        """Read at most one contiguous batch, keeping the recent tail outside the summary."""
        end = min(before_sequence - 1, after_sequence + 12)
        if end <= after_sequence:
            return ()
        return await run_blocking_owned(
            self._store.conversation_history, namespace=self._namespace,
            conversation_id=conversation_id, limit=end - after_sequence,
            before_sequence=end + 1,
        )

    async def prepare_context(self, conversation_id: UUID, history) -> dict:
        if self.context_manager is None:
            return {}
        return await self.context_manager.prepare(conversation_id, history)

    async def shared_context(self, conversation_id: UUID, text: str, history) -> dict:
        from aegis_core.memory.shared_conversation_context import retrieval_query

        return await run_blocking_owned(
            self._store.shared_conversation_context, namespace=self._namespace,
            conversation_id=conversation_id, query=retrieval_query(text, history),
        )

    async def documents(self, conversation_id: UUID) -> tuple[DocumentInput, ...]:
        return await run_blocking_owned(
            self._store.conversation_documents, namespace=self._namespace,
            conversation_id=conversation_id,
        )

    async def checkpoint(self, conversation_id: UUID) -> TaskCheckpoint | None:
        record = await run_blocking_owned(
            self._store.get_conversation, namespace=self._namespace,
            conversation_id=conversation_id,
        )
        return record.task_checkpoint

    async def record_exchange(
        self,
        conversation_id: UUID,
        *,
        user_content: str,
        assistant_content: str,
        documents: tuple[DocumentInput, ...] = (),
        image_name: str | None = None,
        tool_results: tuple[ToolExecutionResult, ...] = (),
        source_checkpoint: TaskCheckpoint | None = None,
        request_id: UUID | None = None,
    ) -> bool:
        try:
            await run_blocking_owned(
                self._store.append_conversation_exchange,
                namespace=self._namespace,
                conversation_id=conversation_id,
                user_content=user_content,
                assistant_content=assistant_content,
                max_turns=self._max_turns,
                documents=documents,
                image_name=image_name,
                tool_results=tool_results,
                source_checkpoint=source_checkpoint,
                request_id=request_id,
            )
        except SecretMaterialError:
            return False
        return True

    async def archive(self, conversation_id: UUID, *, archived: bool) -> None:
        async with self._locked(conversation_id):
            await run_blocking_owned(
                self._store.archive_conversation, namespace=self._namespace,
                conversation_id=conversation_id, archived=archived,
            )

    async def delete(self, conversation_id: UUID) -> None:
        await self.ensure_exists(conversation_id)
        async with self._locked(conversation_id):
            await run_blocking_owned(
                self._store.delete_conversation,
                namespace=self._namespace,
                conversation_id=conversation_id,
            )

    async def rename(self, conversation_id: UUID, title: str) -> ConversationRecord:
        async with self._locked(conversation_id):
            return await run_blocking_owned(self._store.rename_conversation,
                                           namespace=self._namespace,
                                           conversation_id=conversation_id, title=title)

    async def fork(self, conversation_id: UUID) -> ConversationRecord:
        async with self._locked(conversation_id):
            return await run_blocking_owned(self._store.fork_conversation,
                                           namespace=self._namespace,
                                           conversation_id=conversation_id,
                                           max_conversations=self._max_conversations)

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
    METHODS = frozenset({
        "conversations.create", "conversations.history", "conversations.delete",
        "conversations.list", "conversations.messages", "conversations.trash",
        "conversations.restore",
        "conversations.rename", "conversations.fork", "conversations.empty_trash",
    })

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
                response_payload = conversation.model_dump(
                    mode="json", exclude=PRIVATE_CONVERSATION_FIELDS,
                )
            elif request.method == "conversations.list":
                listing = ConversationListPayload.model_validate(request.payload)
                records, has_more = await run_blocking_owned(
                    self._store.list_conversations, namespace=self._coordinator.namespace,
                    **listing.model_dump(),
                )
                response_payload = {
                    "conversations": [
                        record.model_dump(mode="json", exclude=PRIVATE_CONVERSATION_FIELDS)
                        for record in records
                    ],
                    "next_offset": listing.offset + len(records) if has_more else None,
                }
            elif request.method in {"conversations.history", "conversations.messages"}:
                payload = (
                    ConversationMessagesPayload.model_validate(request.payload)
                    if request.method == "conversations.messages"
                    else ConversationHistoryPayload.model_validate(request.payload)
                )
                turns = await asyncio.to_thread(
                    self._store.conversation_history,
                    namespace=self._coordinator.namespace,
                    conversation_id=payload.conversation_id,
                    limit=payload.limit,
                    before_sequence=getattr(payload, "before_sequence", None),
                )
                response_payload = {
                    "conversation_id": str(payload.conversation_id),
                    "turns": [self._public_turn(
                        turn, max_bytes=24_576 if request.method == "conversations.messages"
                        else 4_096
                    ) for turn in turns],
                    "next_before_sequence": turns[0].sequence if turns and turns[0].sequence > 1
                    else None,
                }
            elif request.method in {"conversations.trash", "conversations.restore"}:
                payload = ConversationIdPayload.model_validate(request.payload)
                archived = request.method == "conversations.trash"
                await self._coordinator.archive(payload.conversation_id, archived=archived)
                response_payload = {"conversation_id": str(payload.conversation_id),
                                    "status": "trashed" if archived else "restored"}
            elif request.method == "conversations.delete":
                payload = ConversationIdPayload.model_validate(request.payload)
                await self._coordinator.delete(payload.conversation_id)
                response_payload = {
                    "conversation_id": str(payload.conversation_id),
                    "status": "deleted",
                }
            elif request.method == "conversations.rename":
                payload = RenameConversationPayload.model_validate(request.payload)
                record = await self._coordinator.rename(payload.conversation_id, payload.title)
                response_payload = record.model_dump(
                    mode="json", exclude=PRIVATE_CONVERSATION_FIELDS,
                )
            elif request.method == "conversations.fork":
                payload = ConversationIdPayload.model_validate(request.payload)
                record = await self._coordinator.fork(payload.conversation_id)
                response_payload = record.model_dump(
                    mode="json", exclude=PRIVATE_CONVERSATION_FIELDS,
                )
            elif request.method == "conversations.empty_trash":
                EmptyTrashPayload.model_validate(request.payload)
                count = await run_blocking_owned(self._store.empty_conversation_trash,
                                                namespace=self._coordinator.namespace)
                response_payload = {"status": "emptied", "deleted_count": count}
            else:
                return IpcHandlerResult(ok=False, error_code="method_not_found")
        except (ValidationError, ValueError, MemoryQueryError):
            return IpcHandlerResult(ok=False, error_code="invalid_payload")
        except MemoryNotFoundError:
            return IpcHandlerResult(ok=False, error_code="conversation_not_found")
        except ConversationCapacityError:
            return IpcHandlerResult(ok=False, error_code="conversation_capacity_reached")
        except SecretMaterialError:
            return IpcHandlerResult(ok=False, error_code="secret_material_rejected")
        except MemoryStoreError:
            return IpcHandlerResult(ok=False, error_code="conversation_unavailable")
        return IpcHandlerResult(ok=True, payload=response_payload)

    @staticmethod
    def _public_turn(turn: ConversationTurn, *, max_bytes: int = 4_096) -> dict[str, object]:
        payload = turn.model_dump(mode="json", exclude={"request_id"})
        payload.pop("documents", None)
        payload.pop("image_name", None)
        names = [d.name for d in turn.documents] + (
            [turn.image_name] if turn.image_name else []
        )
        payload["attachment_names"] = [name[:80] for name in names] if max_bytes <= 4_096 else names
        payload["truncated"] = False
        if (
            len(json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))
            <= max_bytes
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
            if size <= max_bytes:
                lower = midpoint
            else:
                upper = midpoint - 1
        payload["content"] = turn.content[:lower]
        payload["truncated"] = True
        return payload
