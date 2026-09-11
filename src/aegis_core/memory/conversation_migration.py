from __future__ import annotations

import sqlite3

from aegis_core.memory.contracts import ConversationRecord, ConversationTurn
from aegis_core.memory.conversation_codec import ConversationRowCodec
from aegis_core.memory.errors import MemoryStoreError


def migrate_conversations_v8_to_v9(
    connection: sqlite3.Connection, *, codec: ConversationRowCodec
) -> None:
    """Stream legacy history into AEAD rows in one rollback-safe transaction.

    SQLite secure_delete clears replaced logical pages; this is not an erasure
    guarantee for SSD blocks, external backups or filesystem snapshots.
    """
    connection.execute("PRAGMA secure_delete = ON")
    connection.execute("BEGIN IMMEDIATE")
    try:
        connection.execute("ALTER TABLE conversations RENAME TO conversations_v8")
        connection.execute("ALTER TABLE conversation_turns RENAME TO conversation_turns_v8")
        connection.execute("DROP INDEX IF EXISTS conversations_namespace_updated")
        connection.execute("""
            CREATE TABLE conversations (
                conversation_id TEXT PRIMARY KEY NOT NULL, namespace TEXT NOT NULL,
                title TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
                turn_count INTEGER NOT NULL CHECK(turn_count >= 0),
                nonce BLOB NOT NULL CHECK(length(nonce) = 12),
                ciphertext BLOB NOT NULL CHECK(length(ciphertext) >= 17)
            )
        """)
        connection.execute(
            "CREATE INDEX conversations_namespace_updated "
            "ON conversations(namespace, updated_at DESC)"
        )
        connection.execute("""
            CREATE TABLE conversation_turns (
                turn_id TEXT PRIMARY KEY NOT NULL,
                conversation_id TEXT NOT NULL
                    REFERENCES conversations(conversation_id) ON DELETE CASCADE,
                sequence INTEGER NOT NULL, role TEXT NOT NULL, content TEXT NOT NULL,
                created_at TEXT NOT NULL, content_sha256 TEXT NOT NULL,
                nonce BLOB NOT NULL CHECK(length(nonce) = 12),
                ciphertext BLOB NOT NULL CHECK(length(ciphertext) >= 17),
                UNIQUE(conversation_id, sequence)
            )
        """)
        orphan = connection.execute("""
            SELECT 1 FROM conversation_turns_v8 t
            LEFT JOIN conversations_v8 c ON c.conversation_id = t.conversation_id
            WHERE c.conversation_id IS NULL LIMIT 1
        """).fetchone()
        if orphan is not None:
            raise MemoryStoreError("legacy conversation contains an orphan turn")
        for row in connection.execute("SELECT * FROM conversations_v8 ORDER BY conversation_id"):
            record = ConversationRecord.model_validate(dict(row))
            count, last = connection.execute(
                """
                SELECT COUNT(*), COALESCE(MAX(sequence), 0)
                FROM conversation_turns_v8 WHERE conversation_id = ?
            """,
                (str(record.conversation_id),),
            ).fetchone()
            if count != last:
                raise MemoryStoreError("legacy conversation sequence is invalid")
            sealed = codec.seal_conversation(record, turn_count=count)
            connection.execute(
                "INSERT INTO conversations VALUES (?, ?, NULL, ?, ?, ?, ?, ?)",
                (
                    str(record.conversation_id),
                    record.namespace,
                    record.created_at.isoformat(),
                    record.updated_at.isoformat(),
                    count,
                    sealed.nonce,
                    sealed.ciphertext,
                ),
            )
        for row in connection.execute("""
            SELECT t.*, c.namespace FROM conversation_turns_v8 t
            JOIN conversations_v8 c ON c.conversation_id = t.conversation_id
            ORDER BY t.conversation_id, t.sequence
        """):
            document = dict(row)
            namespace = document.pop("namespace")
            turn = ConversationTurn.model_validate(document)
            if turn.digest_content(turn.content) != turn.content_sha256:
                raise MemoryStoreError("legacy conversation content hash is invalid")
            sealed = codec.seal_turn(turn, namespace=namespace)
            connection.execute(
                "INSERT INTO conversation_turns VALUES (?, ?, ?, ?, '', ?, ?, ?, ?)",
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
        connection.execute("DROP TABLE conversation_turns_v8")
        connection.execute("DROP TABLE conversations_v8")
        if connection.execute("PRAGMA foreign_key_check").fetchone() is not None:
            raise MemoryStoreError("conversation migration foreign-key validation failed")
        connection.execute("PRAGMA user_version = 9")
        connection.commit()
    except (ValueError, TypeError, sqlite3.Error) as error:
        connection.rollback()
        raise MemoryStoreError("conversation encryption migration failed") from error
    except BaseException:
        connection.rollback()
        raise
