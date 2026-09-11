from __future__ import annotations

import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import pytest

from aegis_core.memory.contracts import ConversationRecord, ConversationTurn, MemoryKind
from aegis_core.memory.errors import DecryptionAuthError, MemoryStoreError
from aegis_core.memory.schema import SCHEMA_VERSION
from aegis_core.memory.sqlite import SQLiteMemoryStore


def _legacy_store(
    tmp_path: Path,
) -> tuple[SQLiteMemoryStore, ConversationRecord, tuple[ConversationTurn, ...]]:
    tmp_path.chmod(0o700)
    store = SQLiteMemoryStore(tmp_path / "memory.sqlite3", encryption_secret=b"m" * 32)
    store.initialize()
    now = datetime.now(UTC)
    header = ConversationRecord(
        namespace="test.legacy", title="Synthetic legacy title", created_at=now, updated_at=now
    )
    turns = tuple(
        ConversationTurn(
            conversation_id=header.conversation_id,
            sequence=index,
            role=role,
            content=content,
            content_sha256=ConversationTurn.digest_content(content),
            created_at=now,
        )
        for index, role, content in (
            (1, "user", "Synthetic old question"),
            (2, "assistant", "Synthetic old answer"),
        )
    )
    # Deliberately reproduce the pre-v9 shape, independently of current DDL.
    with sqlite3.connect(store.path) as connection:
        connection.executescript("""
            DROP TABLE conversation_turns;
            DROP TABLE conversations;
            CREATE TABLE conversations (
                conversation_id TEXT PRIMARY KEY, namespace TEXT NOT NULL,
                title TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL
            );
            CREATE INDEX conversations_namespace_updated
                ON conversations(namespace, updated_at DESC);
            CREATE TABLE conversation_turns (
                turn_id TEXT PRIMARY KEY, conversation_id TEXT NOT NULL
                    REFERENCES conversations(conversation_id) ON DELETE CASCADE,
                sequence INTEGER NOT NULL, role TEXT NOT NULL, content TEXT NOT NULL,
                created_at TEXT NOT NULL, content_sha256 TEXT NOT NULL,
                UNIQUE(conversation_id, sequence)
            );
            PRAGMA user_version = 8;
        """)
        connection.execute(
            "INSERT INTO conversations VALUES (?, ?, ?, ?, ?)",
            (
                str(header.conversation_id),
                header.namespace,
                header.title,
                now.isoformat(),
                now.isoformat(),
            ),
        )
        for turn in turns:
            connection.execute(
                "INSERT INTO conversation_turns VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    str(turn.turn_id),
                    str(turn.conversation_id),
                    turn.sequence,
                    turn.role.value,
                    turn.content,
                    now.isoformat(),
                    turn.content_sha256,
                ),
            )
    return store, header, turns


def test_v8_migration_preserves_identity_history_and_encrypts_pages(tmp_path: Path) -> None:
    store, header, turns = _legacy_store(tmp_path)
    store.initialize()
    store.initialize()  # Idempotent, no re-encryption or destructive re-import.
    assert (
        store.get_conversation(namespace=header.namespace, conversation_id=header.conversation_id)
        == header
    )
    assert (
        store.conversation_history(
            namespace=header.namespace, conversation_id=header.conversation_id
        )
        == turns
    )
    with sqlite3.connect(store.path) as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
    contents = store.path.read_bytes()
    for payload in (header.title, *(turn.content for turn in turns)):
        assert payload.encode() not in contents
    store.delete_conversation(namespace=header.namespace, conversation_id=header.conversation_id)
    with sqlite3.connect(store.path) as connection:
        assert connection.execute("SELECT COUNT(*) FROM conversation_turns").fetchone()[0] == 0


def test_wrong_key_cannot_advance_legacy_schema_or_touch_history(tmp_path: Path) -> None:
    store, _, _ = _legacy_store(tmp_path)
    before = store.path.read_bytes()
    other = SQLiteMemoryStore(store.path, encryption_secret=b"x" * 32)
    with pytest.raises(DecryptionAuthError):
        other.initialize()
    assert store.path.read_bytes() == before


@pytest.mark.parametrize("damage", ["hash", "sequence", "orphan", "timestamp"])
def test_invalid_legacy_history_rolls_back_all_schema_and_rows(tmp_path: Path, damage: str) -> None:
    store, header, turns = _legacy_store(tmp_path)
    with sqlite3.connect(store.path) as connection:
        if damage == "hash":
            connection.execute(
                "UPDATE conversation_turns SET content = 'damaged' WHERE sequence = 2"
            )
        elif damage == "sequence":
            connection.execute("UPDATE conversation_turns SET sequence = 9 WHERE sequence = 2")
        elif damage == "orphan":
            connection.execute(
                "UPDATE conversation_turns SET conversation_id = ? WHERE sequence = 2",
                (str(uuid4()),),
            )
        else:
            connection.execute("UPDATE conversations SET created_at = 'not-a-date'")
        before = (
            connection.execute("SELECT * FROM conversations").fetchall(),
            connection.execute("SELECT * FROM conversation_turns ORDER BY turn_id").fetchall(),
            connection.execute("SELECT sql FROM sqlite_master ORDER BY name").fetchall(),
        )
    with pytest.raises(MemoryStoreError):
        store.initialize()
    with sqlite3.connect(store.path) as connection:
        assert (
            connection.execute("SELECT * FROM conversations").fetchall(),
            connection.execute("SELECT * FROM conversation_turns ORDER BY turn_id").fetchall(),
            connection.execute("SELECT sql FROM sqlite_master ORDER BY name").fetchall(),
        ) == before
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 8
        connection.execute(
            "UPDATE conversations SET created_at = ?", (header.created_at.isoformat(),)
        )
        connection.execute(
            "UPDATE conversation_turns SET conversation_id = ?, sequence = ?, content = ? "
            "WHERE turn_id = ?",
            (
                str(header.conversation_id),
                turns[1].sequence,
                turns[1].content,
                str(turns[1].turn_id),
            ),
        )
    store.initialize()
    assert (
        store.conversation_history(
            namespace=header.namespace, conversation_id=header.conversation_id
        )
        == turns
    )


def test_failure_halfway_through_sealing_keeps_v8_recoverable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store, header, turns = _legacy_store(tmp_path)
    original = store._conversation_codec.seal_turn
    calls = 0

    def fail_second(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise RuntimeError("synthetic interruption")
        return original(*args, **kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(store._conversation_codec, "seal_turn", fail_second)
        with pytest.raises(RuntimeError, match="synthetic interruption"):
            store.initialize()
    store.initialize()
    assert (
        store.conversation_history(
            namespace=header.namespace, conversation_id=header.conversation_id
        )
        == turns
    )


@pytest.mark.parametrize("version", [4, 7])
def test_wrong_key_is_rejected_before_older_non_crypto_migration(
    tmp_path: Path, version: int
) -> None:
    store, _, _ = _legacy_store(tmp_path)
    with sqlite3.connect(store.path) as connection:
        connection.execute("DROP INDEX memory_items_namespace_decay")
        connection.execute(f"PRAGMA user_version = {version}")
    before = store.path.read_bytes()
    with pytest.raises(DecryptionAuthError):
        SQLiteMemoryStore(store.path, encryption_secret=b"x" * 32).initialize()
    assert store.path.read_bytes() == before


def test_existing_encrypted_memories_remain_readable_after_history_migration(
    tmp_path: Path,
) -> None:
    store, header, turns = _legacy_store(tmp_path)
    memory = store.put(
        namespace=header.namespace,
        kind=MemoryKind.PREFERENCE,
        content="Synthetic preference survives migration",
    )
    reopened = SQLiteMemoryStore(store.path, encryption_secret=b"m" * 32)
    reopened.initialize()
    assert reopened.get(namespace=memory.namespace, memory_id=memory.memory_id) == memory
    assert (
        reopened.conversation_history(
            namespace=header.namespace, conversation_id=header.conversation_id
        )
        == turns
    )
