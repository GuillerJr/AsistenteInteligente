from __future__ import annotations

import sqlite3
from datetime import UTC, datetime
from uuid import UUID

from aegis_core.memory.codec import MemoryRowCodec
from aegis_core.memory.contracts import (
    ConversationRecord,
    ConversationRole,
    ConversationTurn,
)
from aegis_core.memory.errors import (
    ConversationCapacityError,
    MemoryNotFoundError,
    MemoryQueryError,
)


class ConversationRepository:
    """Persist bounded conversation histories inside caller-owned connections."""

    def __init__(self, row_codec: MemoryRowCodec) -> None:
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
        connection.execute(
            """
            INSERT INTO conversations (
                conversation_id, namespace, title, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?)
            """,
            (
                str(record.conversation_id),
                record.namespace,
                record.title,
                record.created_at.isoformat(),
                record.updated_at.isoformat(),
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
            SELECT conversation_id, namespace, title, created_at, updated_at
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
        self.get(
            connection,
            namespace=namespace,
            conversation_id=conversation_id,
        )
        rows = connection.execute(
            """
            SELECT turn_id, conversation_id, sequence, role, content,
                   created_at, content_sha256
            FROM conversation_turns
            WHERE conversation_id = ?
            ORDER BY sequence DESC
            LIMIT ?
            """,
            (str(conversation_id), limit),
        ).fetchall()
        return tuple(self._row_codec.turn_from_row(row) for row in reversed(rows))

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
            SELECT conversation_id FROM conversations
            WHERE namespace = ? AND conversation_id = ?
            """,
            (namespace, str(conversation_id)),
        ).fetchone()
        if conversation is None:
            raise MemoryNotFoundError("conversation does not exist")
        count, last_sequence = connection.execute(
            """
            SELECT COUNT(*), COALESCE(MAX(sequence), 0)
            FROM conversation_turns
            WHERE conversation_id = ?
            """,
            (str(conversation_id),),
        ).fetchone()
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
            connection.execute(
                """
                INSERT INTO conversation_turns (
                    turn_id, conversation_id, sequence, role, content,
                    created_at, content_sha256
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    str(turn.turn_id),
                    str(turn.conversation_id),
                    turn.sequence,
                    turn.role.value,
                    turn.content,
                    turn.created_at.isoformat(),
                    turn.content_sha256,
                ),
            )
        connection.execute(
            "UPDATE conversations SET updated_at = ? WHERE conversation_id = ?",
            (now.isoformat(), str(conversation_id)),
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
