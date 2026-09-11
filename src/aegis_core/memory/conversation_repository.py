from __future__ import annotations

import sqlite3
from datetime import UTC, datetime
from uuid import UUID

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
        return self._row_codec.conversation_from_row(row)

    def history(
        self,
        connection: sqlite3.Connection,
        *,
        namespace: str,
        conversation_id: UUID,
        limit: int,
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
        self._row_codec.conversation_from_row(header)
        rows = connection.execute(
            """
            SELECT *
            FROM conversation_turns
            WHERE conversation_id = ?
            ORDER BY sequence DESC
            LIMIT ?
            """,
            (str(conversation_id), limit),
        ).fetchall()
        turns = tuple(
            self._row_codec.turn_from_row(row, namespace=namespace) for row in reversed(rows)
        )
        count = header["turn_count"]
        if [turn.sequence for turn in turns] != list(range(max(1, count - limit + 1), count + 1)):
            self._row_codec.fail("conversation_history_sequence_mismatch")
        return turns

    def append_exchange(
        self,
        connection: sqlite3.Connection,
        *,
        namespace: str,
        conversation_id: UUID,
        user_content: str,
        assistant_content: str,
        max_turns: int,
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
            record.model_copy(update={"updated_at": now}), turn_count=int(count) + 2
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
