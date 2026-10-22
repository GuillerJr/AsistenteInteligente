from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from aegis_core.async_tasks import run_blocking_owned
from aegis_core.contracts import DocumentInput, ToolExecutionResult
from aegis_core.feedback import OwnerFeedback
from aegis_core.ipc.protocol import IpcRequest
from aegis_core.ipc.server import IpcHandlerResult, IpcMethodHandler
from aegis_core.memory.contracts import ConversationRecord, ConversationTurn
from aegis_core.memory.conversation_search import ConversationSearchPayload, SearchCursorCodec
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
    # A temporary chat (Codex/ChatGPT): not listed, never uses or creates memories,
    # deleted when the owner leaves it and purged at startup if left behind.
    temporary: bool = Field(default=False, strict=True)
    # A chat started inside a project (Claude's and Codex's projects).
    project_id: UUID | None = None


TEMPORARY_MARKER = "temporary_chat"
PROJECT_MARKER = "project_id"


def is_temporary(record: ConversationRecord) -> bool:
    return record.context_checkpoints.get(TEMPORARY_MARKER) is True


def project_of(record: ConversationRecord) -> UUID | None:
    value = record.context_checkpoints.get(PROJECT_MARKER)
    try:
        return UUID(value) if isinstance(value, str) else None
    except ValueError:
        return None


def public_record(record: ConversationRecord) -> dict:
    project = project_of(record)
    return {**record.model_dump(mode="json", exclude=PRIVATE_CONVERSATION_FIELDS),
            "project_id": str(project) if project else None,
            "filed": record.context_checkpoints.get("filed_chat") is True}


class ConversationIdPayload(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    conversation_id: UUID


class ConversationHistoryPayload(ConversationIdPayload):
    limit: int = Field(default=10, ge=1, le=10)


class RenameConversationPayload(ConversationIdPayload):
    title: str = Field(min_length=1, max_length=128)


class ForkConversationPayload(ConversationIdPayload):
    before_turn_id: UUID | None = None


class FileConversationPayload(ConversationIdPayload):
    filed: bool = Field(strict=True)


class ConversationFeedbackPayload(ConversationIdPayload):
    turn_id: UUID
    value: OwnerFeedback | None


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
    filed: bool = False


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
        self.retrieval_planner = None
        # Set by the daemon: a project's instructions and files for its chats.
        self.project_context: Callable[[UUID], dict | None] | None = None
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

    async def create(self, title: str | None = None, *, temporary: bool = False,
                     ) -> ConversationRecord:
        record = await run_blocking_owned(
            self._store.create_conversation,
            namespace=self._namespace,
            title=title,
            max_conversations=self._max_conversations,
        )
        if not temporary:
            return record
        await run_blocking_owned(
            self._store.mutate_conversation_context, namespace=self._namespace,
            conversation_id=record.conversation_id,
            transform=lambda context: {**context, TEMPORARY_MARKER: True},
        )
        return await run_blocking_owned(
            self._store.get_conversation, namespace=self._namespace,
            conversation_id=record.conversation_id,
        )

    async def set_project(self, conversation_id: UUID, project_id: UUID | None) -> None:
        def transform(context: dict) -> dict:
            context = {key: value for key, value in context.items() if key != PROJECT_MARKER}
            return {**context, PROJECT_MARKER: str(project_id)} if project_id else context

        await run_blocking_owned(
            self._store.mutate_conversation_context, namespace=self._namespace,
            conversation_id=conversation_id, transform=transform,
        )

    async def project_context_for(self, conversation_id: UUID) -> dict | None:
        if self.project_context is None:
            return None
        try:
            record = await run_blocking_owned(
                self._store.get_conversation, namespace=self._namespace,
                conversation_id=conversation_id,
            )
        except MemoryNotFoundError:
            return None
        project = project_of(record)
        if project is None or is_temporary(record):
            return None
        return await asyncio.to_thread(self.project_context, project)

    async def all_records(self, *, limit: int = 1_000) -> list[ConversationRecord]:
        """Every listed (not trashed, not temporary) chat, for per-project views."""
        found: list[ConversationRecord] = []
        offset = 0
        while len(found) < limit:
            records, has_more = await run_blocking_owned(
                self._store.list_conversations, namespace=self._namespace,
                archived=False, query="", offset=offset, limit=50,
            )
            found.extend(record for record in records if not is_temporary(record))
            offset += len(records)
            if not has_more or not records:
                break
        return found[:limit]

    async def project_records(
        self, project_id: UUID, *, before: tuple[datetime, UUID] | None = None, limit: int = 30,
    ) -> tuple[tuple[ConversationRecord, ...], bool]:
        return await run_blocking_owned(
            self._store.list_project_conversations, namespace=self._namespace,
            project_id=project_id, before=before, limit=limit,
        )

    async def is_temporary(self, conversation_id: UUID) -> bool:
        try:
            record = await run_blocking_owned(
                self._store.get_conversation, namespace=self._namespace,
                conversation_id=conversation_id,
            )
        except MemoryNotFoundError:
            return False
        return is_temporary(record)

    async def purge_temporary(self) -> int:
        """Delete temporary chats a crash or quit left behind."""
        purged, offset = 0, 0
        while True:
            records, has_more = await run_blocking_owned(
                self._store.list_conversations, namespace=self._namespace,
                archived=False, query="", offset=offset, limit=50,
            )
            removed = 0
            for record in records:
                if is_temporary(record):
                    await self.delete(record.conversation_id)
                    removed += 1
            purged += removed
            if not has_more:
                return purged
            offset += len(records) - removed  # Deleted rows no longer occupy the page.

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
        return tuple(record for record in records if not is_temporary(record))

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

    async def prepare_context(self, conversation_id: UUID, history, query: str = "") -> dict:
        if self.context_manager is None:
            return {}
        return await self.context_manager.prepare(conversation_id, history, query=query)

    async def shared_context(self, conversation_id: UUID, text: str, history) -> dict:
        from aegis_core.memory.shared_conversation_context import retrieval_query

        return await run_blocking_owned(
            self._store.shared_conversation_context, namespace=self._namespace,
            conversation_id=conversation_id, query=retrieval_query(text, history),
        )

    async def source_context(self, conversation_id: UUID, text: str, history,
                             *, across_chats: bool = False) -> dict:
        """Retrieve original evidence outside the visible window before relying on a summary.

        Desktop/voice remain scoped to their current conversation. Telegram may
        also recall the owner's other active chats, but never their permissions.
        """
        from aegis_core.memory.shared_conversation_context import retrieval_query
        from aegis_core.orchestration.conversation_window import conversation_window

        query = retrieval_query(text, history)
        window = conversation_window(history, max_bytes=self.context_max_bytes)
        visible_sequences = tuple(
            turn.sequence for turn, item in zip(history[-len(window):], window, strict=True)
            if item["content"] == turn.content
        ) if window else ()
        arguments = {
            "namespace": self._namespace, "conversation_id": conversation_id,
            # The current request owns ranking. Topic resolution is a secondary
            # hint: unrelated recent small talk must not crowd out an older fact.
            "query": text, "alternate_queries": (query,) if query != text else (),
            "include_current": True, "only_current": not across_chats,
            "recent_start_sequence": history[-len(window)].sequence if window else 0,
            "visible_sequences": visible_sequences,
        }
        local = await run_blocking_owned(self._store.shared_conversation_context, **arguments)
        if self.retrieval_planner is None or (not across_chats and not history):
            return local
        alternatives = await self.retrieval_planner.expand(text, history, local)
        if not alternatives:
            return local
        expanded = await run_blocking_owned(
            self._store.shared_conversation_context, **{
                **arguments,
                "alternate_queries": (*arguments["alternate_queries"], *alternatives),
            },
        )
        # A partial second pass must not hide evidence from a complete first pass.
        if local.get("sources") and (not expanded.get("sources") or (
            local.get("search_complete") and not expanded.get("search_complete")
        )):
            return local
        return expanded

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

    async def fork(self, conversation_id: UUID, *,
                   before_turn_id: UUID | None = None) -> ConversationRecord:
        async with self._locked(conversation_id):
            return await run_blocking_owned(self._store.fork_conversation,
                                           namespace=self._namespace,
                                           conversation_id=conversation_id,
                                           max_conversations=self._max_conversations,
                                           before_turn_id=before_turn_id)

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
        "conversations.feedback", "conversations.file",
        "conversations.search",
    })

    def __init__(
        self,
        store: SQLiteMemoryStore,
        coordinator: ConversationCoordinator,
        *,
        on_feedback: Callable[[UUID, OwnerFeedback | None], Awaitable[None]] | None = None,
    ) -> None:
        self._store = store
        self._coordinator = coordinator
        self._on_feedback = on_feedback
        self._search_cursors = SearchCursorCodec(coordinator.namespace)

    def handlers(self) -> dict[str, IpcMethodHandler]:
        return {method: self.handle for method in self.METHODS}

    async def handle(self, request: IpcRequest) -> IpcHandlerResult:
        try:
            if request.method == "conversations.create":
                payload = CreateConversationPayload.model_validate(request.payload)
                conversation = await self._coordinator.create(
                    payload.title, temporary=payload.temporary)
                if payload.project_id is not None and not payload.temporary:
                    await self._coordinator.set_project(
                        conversation.conversation_id, payload.project_id)
                    conversation = await run_blocking_owned(
                        self._store.get_conversation, namespace=self._coordinator.namespace,
                        conversation_id=conversation.conversation_id)
                response_payload = public_record(conversation)
            elif request.method == "conversations.search":
                search = ConversationSearchPayload.model_validate(request.payload)
                position = self._search_cursors.decode(search)
                page = await run_blocking_owned(
                    self._store.search_conversations, namespace=self._coordinator.namespace,
                    request=search, position=position,
                )
                response_payload = {
                    "conversations": [
                        {**public_record(hit.conversation), "search_match": (
                            {"excerpt": hit.excerpt, "sequence": hit.sequence,
                             "turn_id": str(hit.turn_id)} if hit.turn_id else None
                        )} for hit in page.matches
                    ],
                    "next_cursor": self._search_cursors.encode(page.next_position)
                    if page.next_position else None,
                }
            elif request.method == "conversations.list":
                listing = ConversationListPayload.model_validate(request.payload)
                records, has_more = await run_blocking_owned(
                    self._store.list_conversations, namespace=self._coordinator.namespace,
                    **listing.model_dump(),
                )
                response_payload = {
                    "conversations": [
                        public_record(record) for record in records if not is_temporary(record)
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
                payload = ForkConversationPayload.model_validate(request.payload)
                # The source is immutable except for feedback; identify the original user turn
                # before creating a branch. The repository verifies membership again atomically.
                replay = None
                if payload.before_turn_id is not None:
                    replay = await run_blocking_owned(
                        self._store.conversation_turn, namespace=self._coordinator.namespace,
                        conversation_id=payload.conversation_id, turn_id=payload.before_turn_id,
                    )
                    if replay.role.value != "user":
                        raise MemoryQueryError("replay requires a user turn")
                record = await self._coordinator.fork(
                    payload.conversation_id, before_turn_id=payload.before_turn_id)
                response_payload = public_record(record)
                if replay is not None:
                    response_payload["replay"] = {
                        "text": replay.content,
                        "documents": [document.model_dump(mode="json")
                                      for document in replay.documents],
                        "image_name": replay.image_name,
                    }
            elif request.method == "conversations.feedback":
                payload = ConversationFeedbackPayload.model_validate(request.payload)
                async with self._coordinator.serialized(payload.conversation_id):
                    turn = await run_blocking_owned(
                        self._store.set_conversation_feedback,
                        namespace=self._coordinator.namespace,
                        **payload.model_dump(),
                    )
                if self._on_feedback is not None and turn.request_id is not None:
                    await self._on_feedback(turn.request_id, turn.owner_feedback)
                response_payload = {"turn_id": str(turn.turn_id),
                                    "value": turn.owner_feedback}
            elif request.method == "conversations.file":
                payload = FileConversationPayload.model_validate(request.payload)
                async with self._coordinator.serialized(payload.conversation_id):
                    await run_blocking_owned(
                        self._store.mutate_conversation_context,
                        namespace=self._coordinator.namespace,
                        conversation_id=payload.conversation_id,
                        transform=lambda context: {**context, "filed_chat": payload.filed},
                    )
                response_payload = {"status": "filed" if payload.filed else "unfiled"}
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
