from __future__ import annotations

import sqlite3
from datetime import UTC, datetime
from uuid import UUID, uuid4

from aegis_core.contracts import DocumentInput, ToolExecutionResult
from aegis_core.memory.contracts import (
    ConversationRecord,
    ConversationRole,
    ConversationTurn,
)
from aegis_core.memory.conversation_codec import ConversationRowCodec
from aegis_core.memory.errors import (
    ConversationCapacityError,
    MemoryNotFoundError,
    MemoryQueryError,
)
from aegis_core.memory.task_checkpoint import TaskCheckpoint
from aegis_core.orchestration.task_progress import advance_checkpoint


class ConversationRepository:
    """Persist bounded conversation histories inside caller-owned connections."""

    def __init__(self, row_codec: ConversationRowCodec) -> None:
        self._row_codec = row_codec

    def create(
        self,
        connection: sqlite3.Connection,
        *,
        namespace: str,
        title: str | None,
        max_conversations: int,
    ) -> ConversationRecord:
        if not 1 <= max_conversations <= 100_000:
            raise MemoryQueryError("conversation capacity is out of range")
        now = datetime.now(UTC)
        record = ConversationRecord(
            namespace=namespace,
            title=title,
            created_at=now,
            updated_at=now,
        )
        connection.execute("BEGIN IMMEDIATE")
        count = int(
            connection.execute(
                "SELECT COUNT(*) FROM conversations WHERE namespace = ?",
                (namespace,),
            ).fetchone()[0]
        )
        if count >= max_conversations:
            raise ConversationCapacityError("conversation capacity reached")
        sealed = self._row_codec.seal_conversation(record, turn_count=0)
        connection.execute(
            """
            INSERT INTO conversations (
                conversation_id, namespace, title, created_at, updated_at,
                turn_count, nonce, ciphertext
            ) VALUES (?, ?, NULL, ?, ?, 0, ?, ?)
            """,
            (
                str(record.conversation_id),
                record.namespace,
                record.created_at.isoformat(),
                record.updated_at.isoformat(),
                sealed.nonce,
                sealed.ciphertext,
            ),
        )
        return record

    def get(
        self,
        connection: sqlite3.Connection,
        *,
        namespace: str,
        conversation_id: UUID,
        include_archived: bool = False,
    ) -> ConversationRecord:
        row = connection.execute(
            """
            SELECT *
            FROM conversations
            WHERE namespace = ? AND conversation_id = ?
            """,
            (namespace, str(conversation_id)),
        ).fetchone()
        if row is None:
            raise MemoryNotFoundError("conversation does not exist")
        record = self._row_codec.conversation_from_row(row)
        if record.archived and not include_archived:
            raise MemoryNotFoundError("conversation is in trash")
        return record

    def list_records(
        self, connection: sqlite3.Connection, *, namespace: str, archived: bool,
        query: str, offset: int, limit: int,
    ) -> tuple[tuple[ConversationRecord, ...], bool]:
        if not 1 <= limit <= 50 or not 0 <= offset <= 100_000:
            raise MemoryQueryError("conversation page is out of range")
        rows = connection.execute(
            "SELECT * FROM conversations WHERE namespace = ? "
            "ORDER BY updated_at DESC, conversation_id DESC", (namespace,),
        )
        records = []
        matched = 0
        for row in rows:
            record = self._row_codec.conversation_from_row(row)
            if record.archived != archived:
                continue
            if record.title in (None, "Chat de Jarvis"):
                first = connection.execute(
                    "SELECT * FROM conversation_turns WHERE conversation_id = ? AND sequence = 1",
                    (str(record.conversation_id),),
                ).fetchone()
                if first is not None:
                    turn = self._row_codec.turn_from_row(first, namespace=namespace)
                    title = " ".join(turn.content.split())[:128]
                    if title:
                        record = record.model_copy(update={"title": title})
            if (record.archived != archived
                    or query.casefold() not in (record.title or "").casefold()):
                continue
            matched += 1
            if matched <= offset:
                continue
            records.append(record)
            if len(records) > limit:
                break
        return tuple(records[:limit]), len(records) > limit

    def archive(
        self, connection: sqlite3.Connection, *, namespace: str,
        conversation_id: UUID, archived: bool,
    ) -> None:
        connection.execute("BEGIN IMMEDIATE")
        record = self.get(connection, namespace=namespace, conversation_id=conversation_id,
                          include_archived=True)
        count = connection.execute(
            "SELECT turn_count FROM conversations WHERE conversation_id = ?",
            (str(conversation_id),),
        ).fetchone()[0]
        now = datetime.now(UTC)
        sealed = self._row_codec.seal_conversation(
            record.model_copy(update={"archived": archived, "updated_at": now}), turn_count=count
        )
        connection.execute(
            "UPDATE conversations SET updated_at = ?, nonce = ?, ciphertext = ? "
            "WHERE namespace = ? AND conversation_id = ?",
            (now.isoformat(), sealed.nonce, sealed.ciphertext, namespace, str(conversation_id)),
        )

    def history(
        self,
        connection: sqlite3.Connection,
        *,
        namespace: str,
        conversation_id: UUID,
        limit: int,
        before_sequence: int | None = None,
    ) -> tuple[ConversationTurn, ...]:
        if not 1 <= limit <= 50:
            raise MemoryQueryError("conversation history limit is out of range")
        # Header and suffix must come from the same snapshot, including when
        # another process is appending or deleting this conversation.
        if not connection.in_transaction:
            connection.execute("BEGIN")
        header = connection.execute(
            "SELECT * FROM conversations WHERE namespace = ? AND conversation_id = ?",
            (namespace, str(conversation_id)),
        ).fetchone()
        if header is None:
            raise MemoryNotFoundError("conversation does not exist")
        if self._row_codec.conversation_from_row(header).archived:
            raise MemoryNotFoundError("conversation is in trash")
        count = header["turn_count"]
        end = count if before_sequence is None else min(count, before_sequence - 1)
        if end < 0:
            raise MemoryQueryError("conversation cursor is out of range")
        rows = connection.execute(
            """
            SELECT *
            FROM conversation_turns
            WHERE conversation_id = ? AND sequence <= ?
            ORDER BY sequence DESC
            LIMIT ?
            """,
            (str(conversation_id), end, limit),
        ).fetchall()
        turns = tuple(
            self._row_codec.turn_from_row(row, namespace=namespace) for row in reversed(rows)
        )
        if [turn.sequence for turn in turns] != list(range(max(1, end - limit + 1), end + 1)):
            self._row_codec.fail("conversation_history_sequence_mismatch")
        return turns

    def rename(self, connection: sqlite3.Connection, *, namespace: str,
               conversation_id: UUID, title: str) -> ConversationRecord:
        connection.execute("BEGIN IMMEDIATE")
        record = self.get(connection, namespace=namespace, conversation_id=conversation_id)
        count = connection.execute("SELECT turn_count FROM conversations WHERE conversation_id = ?",
                                   (str(conversation_id),)).fetchone()[0]
        record = record.model_copy(update={"title": title, "updated_at": datetime.now(UTC)})
        sealed = self._row_codec.seal_conversation(record, turn_count=count)
        connection.execute("UPDATE conversations SET updated_at = ?, nonce = ?, ciphertext = ? "
                           "WHERE namespace = ? AND conversation_id = ?",
                           (record.updated_at.isoformat(), sealed.nonce, sealed.ciphertext,
                            namespace, str(conversation_id)))
        return record

    def fork(self, connection: sqlite3.Connection, *, namespace: str,
             conversation_id: UUID, max_conversations: int) -> ConversationRecord:
        # create acquires the write transaction before reading the source snapshot.
        record = self.create(connection, namespace=namespace, title=None,
                             max_conversations=max_conversations)
        source = self.get(connection, namespace=namespace, conversation_id=conversation_id)
        rows = connection.execute("SELECT * FROM conversation_turns WHERE conversation_id = ? "
                                  "ORDER BY sequence", (str(conversation_id),)).fetchall()
        count = connection.execute("SELECT turn_count FROM conversations WHERE conversation_id = ?",
                                   (str(conversation_id),)).fetchone()[0]
        turns = [self._row_codec.turn_from_row(row, namespace=namespace) for row in rows]
        if [turn.sequence for turn in turns] != list(range(1, count + 1)):
            self._row_codec.fail("conversation_history_sequence_mismatch")
        title = source.title
        if title in (None, "Chat de Jarvis") and turns:
            title = " ".join(turns[0].content.split())[:128]
        record = record.model_copy(update={"title": (title or "Conversación")[:120] + " · fork",
                                          "task_checkpoint": source.task_checkpoint})
        for turn in turns:
            cloned = turn.model_copy(update={"turn_id": uuid4(),
                                             "conversation_id": record.conversation_id})
            sealed = self._row_codec.seal_turn(cloned, namespace=namespace)
            connection.execute("INSERT INTO conversation_turns (turn_id, conversation_id, "
                               "sequence, role, content, created_at, content_sha256, nonce, "
                               "ciphertext) VALUES (?, ?, ?, ?, '', ?, ?, ?, ?)",
                               (str(cloned.turn_id), str(record.conversation_id), cloned.sequence,
                                cloned.role.value, cloned.created_at.isoformat(),
                                cloned.content_sha256, sealed.nonce, sealed.ciphertext))
        sealed = self._row_codec.seal_conversation(record, turn_count=count)
        connection.execute("UPDATE conversations SET turn_count = ?, nonce = ?, ciphertext = ? "
                           "WHERE conversation_id = ?",
                           (count, sealed.nonce, sealed.ciphertext, str(record.conversation_id)))
        return record

    def empty_trash(self, connection: sqlite3.Connection, *, namespace: str) -> int:
        connection.execute("BEGIN IMMEDIATE")
        # Archive state is authenticated encrypted data, never inferred from a UI page/query.
        ids = [str(record.conversation_id)
               for row in connection.execute("SELECT * FROM conversations WHERE namespace = ?",
                                             (namespace,))
               if (record := self._row_codec.conversation_from_row(row)).archived]
        connection.executemany("DELETE FROM conversations WHERE namespace = ? "
                               "AND conversation_id = ?", [(namespace, id_) for id_ in ids])
        return len(ids)

    def append_exchange(
        self,
        connection: sqlite3.Connection,
        *,
        namespace: str,
        conversation_id: UUID,
        user_content: str,
        assistant_content: str,
        max_turns: int,
        documents: tuple[DocumentInput, ...] = (),
        image_name: str | None = None,
        tool_results: tuple[ToolExecutionResult, ...] = (),
        source_checkpoint: TaskCheckpoint | None = None,
    ) -> tuple[ConversationTurn, ConversationTurn]:
        if not 2 <= max_turns <= 10_000:
            raise MemoryQueryError("conversation turn capacity is out of range")
        now = datetime.now(UTC)
        connection.execute("BEGIN IMMEDIATE")
        conversation = connection.execute(
            """
            SELECT * FROM conversations
            WHERE namespace = ? AND conversation_id = ?
            """,
            (namespace, str(conversation_id)),
        ).fetchone()
        if conversation is None:
            raise MemoryNotFoundError("conversation does not exist")
        record = self._row_codec.conversation_from_row(conversation)
        if record.archived:
            raise MemoryNotFoundError("conversation is in trash")
        count, last_sequence = connection.execute(
            """
            SELECT COUNT(*), COALESCE(MAX(sequence), 0)
            FROM conversation_turns
            WHERE conversation_id = ?
            """,
            (str(conversation_id),),
        ).fetchone()
        if count != conversation["turn_count"] or last_sequence != count:
            self._row_codec.fail("conversation_history_sequence_mismatch")
        if int(count) + 2 > max_turns:
            raise ConversationCapacityError("conversation turn capacity reached")
        user_turn = ConversationTurn(
            conversation_id=conversation_id,
            sequence=int(last_sequence) + 1,
            role=ConversationRole.USER,
            content=user_content,
            created_at=now,
            content_sha256=ConversationTurn.digest_content(user_content),
            documents=documents,
            image_name=image_name,
        )
        assistant_turn = ConversationTurn(
            conversation_id=conversation_id,
            sequence=int(last_sequence) + 2,
            role=ConversationRole.ASSISTANT,
            content=assistant_content,
            created_at=now,
            content_sha256=ConversationTurn.digest_content(assistant_content),
        )
        for turn in (user_turn, assistant_turn):
            sealed = self._row_codec.seal_turn(turn, namespace=namespace)
            connection.execute(
                """
                INSERT INTO conversation_turns (
                    turn_id, conversation_id, sequence, role, content,
                    created_at, content_sha256, nonce, ciphertext
                ) VALUES (?, ?, ?, ?, '', ?, ?, ?, ?)
                """,
                (
                    str(turn.turn_id),
                    str(turn.conversation_id),
                    turn.sequence,
                    turn.role.value,
                    turn.created_at.isoformat(),
                    turn.content_sha256,
                    sealed.nonce,
                    sealed.ciphertext,
                ),
            )
        sealed = self._row_codec.seal_conversation(
            record.model_copy(update={"updated_at": now, "task_checkpoint": advance_checkpoint(
                source_checkpoint or record.task_checkpoint,
                request=user_content, response=assistant_content,
                sequence=int(count) + 2, tool_results=tool_results,
            )}), turn_count=int(count) + 2
        )
        connection.execute(
            "UPDATE conversations SET updated_at = ?, turn_count = ?, nonce = ?, ciphertext = ? "
            "WHERE conversation_id = ?",
            (
                now.isoformat(),
                int(count) + 2,
                sealed.nonce,
                sealed.ciphertext,
                str(conversation_id),
            ),
        )
        return user_turn, assistant_turn

    def documents(
        self, connection: sqlite3.Connection, *, namespace: str, conversation_id: UUID,
    ) -> tuple[DocumentInput, ...]:
        self.get(connection, namespace=namespace, conversation_id=conversation_id)
        selected = {}
        used = 0
        for row in connection.execute(
            "SELECT * FROM conversation_turns WHERE conversation_id = ? AND role = 'user' "
            "ORDER BY sequence DESC", (str(conversation_id),),
        ):
            turn = self._row_codec.turn_from_row(row, namespace=namespace)
            for document in turn.documents:
                size = len(document.content.encode("utf-8"))
                if document.name not in selected and used + size <= 65_536:
                    selected[document.name] = document
                    used += size
                    if len(selected) == 4:
                        return tuple(selected.values())
        return tuple(selected.values())

    def delete(
        self,
        connection: sqlite3.Connection,
        *,
        namespace: str,
        conversation_id: UUID,
    ) -> None:
        if not self.delete_optional(
            connection,
            namespace=namespace,
            conversation_id=conversation_id,
        ):
            raise MemoryNotFoundError("conversation does not exist")

    @staticmethod
    def delete_optional(
        connection: sqlite3.Connection,
        *,
        namespace: str,
        conversation_id: UUID,
    ) -> bool:
        cursor = connection.execute(
            "DELETE FROM conversations WHERE namespace = ? AND conversation_id = ?",
            (namespace, str(conversation_id)),
        )
        return cursor.rowcount == 1
