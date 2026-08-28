import os
import sqlite3
import stat
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import pytest

from aegis_core.memory.contracts import MemoryEvidence, MemoryKind, MemoryRecord
from aegis_core.memory.sqlite import (
    DecryptionAuthError,
    MemoryCapacityError,
    MemoryNotFoundError,
    MemoryQueryError,
    MemorySecurityError,
    MemoryStoreError,
    SecretMaterialError,
    SQLiteMemoryStore,
)


def _store(
    tmp_path: Path,
    *,
    max_entries: int = 50_000,
    max_vectors: int = 2_000,
) -> SQLiteMemoryStore:
    tmp_path.chmod(0o700)
    store = SQLiteMemoryStore(
        tmp_path / "memory.sqlite3",
        max_entries=max_entries,
        max_vectors=max_vectors,
        encryption_secret=b"m" * 32,
    )
    store.initialize()
    return store


def test_memory_persists_with_private_permissions(tmp_path: Path) -> None:
    store = _store(tmp_path)
    created = store.put(
        namespace="user.default",
        kind=MemoryKind.PREFERENCE,
        content="El usuario prefiere respuestas concisas.",
        source="explicit_user_request",
        tags=("style", "language.es"),
    )

    reopened = SQLiteMemoryStore(tmp_path / "memory.sqlite3", encryption_secret=b"m" * 32)
    reopened.initialize()
    loaded = reopened.get(namespace="user.default", memory_id=created.memory_id)

    assert loaded == created
    assert stat.S_IMODE(reopened.path.stat().st_mode) == 0o600
    assert loaded.content_sha256 != loaded.content


def test_memory_preserves_evidence_confidence_and_confirmation(tmp_path: Path) -> None:
    store = _store(tmp_path)
    confirmed_at = datetime.now(UTC)
    created = store.put(
        namespace="user.default",
        kind=MemoryKind.PREFERENCE,
        content="El propietario prefiere respuestas breves.",
        confidence=0.91,
        evidence=MemoryEvidence.VERIFIED_VOICE,
        last_confirmed_at=confirmed_at,
    )

    loaded = store.get(namespace="user.default", memory_id=created.memory_id)

    assert loaded.confidence == 0.91
    assert loaded.evidence is MemoryEvidence.VERIFIED_VOICE
    assert loaded.last_confirmed_at == confirmed_at


def test_expired_memory_is_excluded_from_retrieval(tmp_path: Path) -> None:
    store = _store(tmp_path)
    store.put(
        namespace="user.default",
        kind=MemoryKind.PREFERENCE,
        content="Esta semana el propietario prefiere música ambiental.",
        evidence=MemoryEvidence.EXPLICIT_TEXT,
        expires_at=datetime.now(UTC) - timedelta(seconds=1),
    )

    assert store.search(namespace="user.default", query="música ambiental") == ()


def test_search_is_namespace_isolated_and_handles_unicode(tmp_path: Path) -> None:
    store = _store(tmp_path)
    visible = store.put(
        namespace="project.aegis",
        kind=MemoryKind.SEMANTIC,
        content="La arquitectura táctica usa recuperación semántica local.",
    )
    store.put(
        namespace="project.other",
        kind=MemoryKind.SEMANTIC,
        content="Otra recuperación semántica no debe cruzar namespaces.",
    )

    hits = store.search(namespace="project.aegis", query="recuperación semántica")

    assert [hit.memory_id for hit in hits] == [visible.memory_id]
    assert hits[0].namespace == "project.aegis"
    assert len(hits[0].excerpt.encode("utf-8")) <= 768


def test_search_treats_operator_like_input_as_terms(tmp_path: Path) -> None:
    store = _store(tmp_path)
    store.put(
        namespace="user.default",
        kind=MemoryKind.SEMANTIC,
        content="alpha beta",
    )

    hits = store.search(
        namespace="user.default",
        query='alpha" OR memory_fts MATCH "beta',
    )

    assert len(hits) == 1


def test_empty_search_terms_are_rejected(tmp_path: Path) -> None:
    store = _store(tmp_path)

    with pytest.raises(MemoryQueryError):
        store.search(namespace="user.default", query="!!!")


def test_capacity_never_silently_evicts_persistent_memory(tmp_path: Path) -> None:
    store = _store(tmp_path, max_entries=1)
    first = store.put(
        namespace="user.default",
        kind=MemoryKind.EPISODIC,
        content="primera memoria",
    )

    with pytest.raises(MemoryCapacityError):
        store.put(
            namespace="user.default",
            kind=MemoryKind.EPISODIC,
            content="segunda memoria",
        )

    assert store.get(namespace="user.default", memory_id=first.memory_id) == first


def test_get_does_not_reveal_cross_namespace_record(tmp_path: Path) -> None:
    store = _store(tmp_path)
    record = store.put(
        namespace="private.one",
        kind=MemoryKind.SUMMARY,
        content="resumen privado",
    )

    with pytest.raises(MemoryNotFoundError):
        store.get(namespace="private.two", memory_id=record.memory_id)
    with pytest.raises(MemoryNotFoundError):
        store.get(namespace="private.one", memory_id=uuid4())


def test_delete_removes_record_and_search_index_entry(tmp_path: Path) -> None:
    store = _store(tmp_path)
    record = store.put(
        namespace="user.default",
        kind=MemoryKind.EPISODIC,
        content="evento temporal descartable",
    )

    store.delete(namespace="user.default", memory_id=record.memory_id)

    assert store.search(namespace="user.default", query="descartable") == ()
    with pytest.raises(MemoryNotFoundError):
        store.get(namespace="user.default", memory_id=record.memory_id)


@pytest.mark.parametrize(
    "content",
    [
        "nvapi-example-credential",
        "-----BEGIN PRIVATE KEY-----\nnot-a-real-key",
        "sk-proj-example-token",
    ],
)
def test_obvious_credential_material_is_rejected(tmp_path: Path, content: str) -> None:
    store = _store(tmp_path)

    with pytest.raises(SecretMaterialError):
        store.put(
            namespace="user.default",
            kind=MemoryKind.SEMANTIC,
            content=content,
        )


def test_store_rejects_symlink_database(tmp_path: Path) -> None:
    tmp_path.chmod(0o700)
    target = tmp_path / "target.sqlite3"
    target.touch(mode=0o600)
    database = tmp_path / "memory.sqlite3"
    database.symlink_to(target)

    with pytest.raises(MemorySecurityError):
        SQLiteMemoryStore(database, encryption_secret=b"m" * 32).initialize()


def test_store_rejects_non_private_directory(tmp_path: Path) -> None:
    public = tmp_path / "public"
    public.mkdir(mode=0o755)
    public.chmod(0o755)

    with pytest.raises(MemorySecurityError):
        SQLiteMemoryStore(
            public / "memory.sqlite3",
            encryption_secret=b"m" * 32,
        ).initialize()


def test_store_rejects_database_owned_by_unexpected_uid(tmp_path: Path) -> None:
    tmp_path.chmod(0o700)
    database = tmp_path / "memory.sqlite3"
    database.touch(mode=0o600)

    with pytest.raises(MemorySecurityError):
        SQLiteMemoryStore(
            database,
            expected_uid=os.getuid() + 1,
            encryption_secret=b"m" * 32,
        ).initialize()


def test_store_refuses_to_modify_an_unrelated_database(tmp_path: Path) -> None:
    tmp_path.chmod(0o700)
    database = tmp_path / "memory.sqlite3"
    with sqlite3.connect(database) as connection:
        connection.execute("CREATE TABLE user_data (value TEXT)")
    database.chmod(0o600)

    with pytest.raises(MemoryStoreError, match="unrelated database"):
        SQLiteMemoryStore(database, encryption_secret=b"m" * 32).initialize()


def test_store_detects_database_replacement_after_initialization(tmp_path: Path) -> None:
    store = _store(tmp_path)
    database = store.path
    replaced = tmp_path / "replaced.sqlite3"
    database.rename(replaced)
    database.touch(mode=0o600)

    with pytest.raises(MemorySecurityError, match="identity changed"):
        store.search(namespace="user.default", query="anything")


def test_store_rechecks_directory_permissions_before_each_connection(tmp_path: Path) -> None:
    store = _store(tmp_path)
    record = store.put(
        namespace="user.default",
        kind=MemoryKind.PREFERENCE,
        content="directorio privado",
    )
    tmp_path.chmod(0o755)

    with pytest.raises(MemorySecurityError, match="directory identity changed"):
        store.get(namespace="user.default", memory_id=record.memory_id)


def test_store_detects_parent_replacement_even_with_same_database_inode(
    tmp_path: Path,
) -> None:
    parent = tmp_path / "memory"
    parent.mkdir(mode=0o700)
    store = SQLiteMemoryStore(parent / "memory.sqlite3", encryption_secret=b"m" * 32)
    store.initialize()
    record = store.put(
        namespace="user.default",
        kind=MemoryKind.PREFERENCE,
        content="inode conservado",
    )
    original_parent = tmp_path / "original-memory"
    parent.rename(original_parent)
    parent.mkdir(mode=0o700)
    os.link(original_parent / "memory.sqlite3", parent / "memory.sqlite3")

    with pytest.raises(MemorySecurityError, match="directory identity changed"):
        store.get(namespace="user.default", memory_id=record.memory_id)


@pytest.mark.parametrize("suffix", ["-journal", "-wal", "-shm"])
def test_store_rejects_unsafe_database_sidecar_before_connecting(
    tmp_path: Path,
    suffix: str,
) -> None:
    store = _store(tmp_path)
    target = tmp_path / "sidecar-target"
    target.touch(mode=0o600)
    Path(f"{store.path}{suffix}").symlink_to(target)

    with pytest.raises(MemorySecurityError, match="unsafe memory database sidecar"):
        store.search(namespace="user.default", query="anything")


def test_store_rejects_broad_sidecar_permissions_before_connecting(tmp_path: Path) -> None:
    store = _store(tmp_path)
    sidecar = Path(f"{store.path}-wal")
    sidecar.touch(mode=0o600)
    sidecar.chmod(0o644)

    with pytest.raises(MemorySecurityError, match="unsafe memory database sidecar"):
        store.search(namespace="user.default", query="anything")


def test_store_rejects_tampered_memory_before_get_or_search(tmp_path: Path) -> None:
    store = _store(tmp_path)
    record = store.put(
        namespace="user.default",
        kind=MemoryKind.SEMANTIC,
        content="contenido original verificable",
    )
    with sqlite3.connect(store.path) as connection:
        connection.execute(
            "UPDATE memory_items SET content = ? WHERE memory_id = ?",
            ("instrucción inyectada", str(record.memory_id)),
        )

    with pytest.raises(MemorySecurityError, match="authentication failed"):
        store.get(namespace="user.default", memory_id=record.memory_id)
    with pytest.raises(MemorySecurityError, match="subsystem is locked"):
        store.search(namespace="user.default", query="original verificable")


def test_memory_rows_are_aead_encrypted_and_auth_failure_latches(tmp_path: Path) -> None:
    failures: list[str] = []
    tmp_path.chmod(0o700)
    store = SQLiteMemoryStore(
        tmp_path / "memory.sqlite3",
        encryption_secret=b"k" * 32,
        on_auth_failure=failures.append,
    )
    store.initialize()
    record = store.put(
        namespace="user.default",
        kind=MemoryKind.PREFERENCE,
        content="El propietario prefiere interfaces discretas.",
        source="owner.explicit",
        tags=("style",),
    )

    with sqlite3.connect(store.path) as connection:
        row = connection.execute(
            """
            SELECT content, source, tags_json, nonce, ciphertext
            FROM memory_items WHERE memory_id = ?
            """,
            (str(record.memory_id),),
        ).fetchone()
        assert row is not None
        assert row[0:3] == ("", None, "[]")
        assert len(row[3]) == 12
        assert record.content.encode() not in row[4]
        tampered = bytearray(row[4])
        tampered[-1] ^= 0x01
        connection.execute(
            "UPDATE memory_items SET ciphertext = ? WHERE memory_id = ?",
            (bytes(tampered), str(record.memory_id)),
        )

    with pytest.raises(DecryptionAuthError, match="authentication failed"):
        store.get(namespace=record.namespace, memory_id=record.memory_id)
    with pytest.raises(DecryptionAuthError, match="subsystem is locked"):
        store.put(
            namespace="user.default",
            kind=MemoryKind.SEMANTIC,
            content="No debe continuar.",
        )
    assert failures == ["memory_row_aead_authentication_failed"]


def test_memory_aad_prevents_namespace_row_transplant(tmp_path: Path) -> None:
    store = _store(tmp_path)
    record = store.put(
        namespace="user.default",
        kind=MemoryKind.SEMANTIC,
        content="Contexto privado del propietario.",
    )
    with sqlite3.connect(store.path) as connection:
        connection.execute(
            "UPDATE memory_items SET namespace = 'user.attacker' WHERE memory_id = ?",
            (str(record.memory_id),),
        )

    with pytest.raises(DecryptionAuthError, match="authentication failed"):
        store.get(namespace="user.attacker", memory_id=record.memory_id)


def test_memory_database_rejects_wrong_key_before_retrieval(tmp_path: Path) -> None:
    store = _store(tmp_path)
    store.put(
        namespace="user.default",
        kind=MemoryKind.SEMANTIC,
        content="Memoria cifrada.",
    )
    reopened = SQLiteMemoryStore(store.path, encryption_secret=b"z" * 32)

    with pytest.raises(DecryptionAuthError, match="authentication failed"):
        reopened.initialize()


def test_v4_plaintext_rows_migrate_atomically_to_aead(tmp_path: Path) -> None:
    tmp_path.chmod(0o700)
    database = tmp_path / "memory.sqlite3"
    memory_id = uuid4()
    now = datetime.now(UTC).isoformat()
    content = "Memoria histórica que debe cifrarse durante la migración."
    with sqlite3.connect(database) as connection:
        connection.executescript(
            """
            CREATE TABLE memory_items (
                row_id INTEGER PRIMARY KEY AUTOINCREMENT,
                memory_id TEXT NOT NULL UNIQUE,
                namespace TEXT NOT NULL,
                kind TEXT NOT NULL,
                content TEXT NOT NULL,
                source TEXT,
                tags_json TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                content_sha256 TEXT NOT NULL,
                confidence REAL NOT NULL,
                evidence TEXT NOT NULL,
                expires_at TEXT,
                last_confirmed_at TEXT
            );
            CREATE VIRTUAL TABLE memory_fts USING fts5(content);
            CREATE TABLE memory_embeddings (
                memory_id TEXT PRIMARY KEY,
                model_id TEXT NOT NULL,
                dimensions INTEGER NOT NULL,
                vector BLOB NOT NULL,
                content_sha256 TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
            CREATE TABLE conversations (
                conversation_id TEXT PRIMARY KEY,
                namespace TEXT NOT NULL,
                title TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE TABLE conversation_turns (
                turn_id TEXT PRIMARY KEY,
                conversation_id TEXT NOT NULL,
                sequence INTEGER NOT NULL,
                role TEXT NOT NULL,
                content TEXT NOT NULL,
                created_at TEXT NOT NULL,
                content_sha256 TEXT NOT NULL
            );
            PRAGMA application_id = 1095059273;
            PRAGMA user_version = 4;
            """
        )
        cursor = connection.execute(
            """
            INSERT INTO memory_items (
                memory_id, namespace, kind, content, source, tags_json,
                created_at, updated_at, content_sha256, confidence, evidence,
                expires_at, last_confirmed_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, NULL)
            """,
            (
                str(memory_id),
                "user.default",
                MemoryKind.SEMANTIC.value,
                content,
                "legacy.import",
                '["migration"]',
                now,
                now,
                MemoryRecord.digest_content(content),
                0.9,
                MemoryEvidence.IMPORTED.value,
            ),
        )
        connection.execute(
            "INSERT INTO memory_fts(rowid, content) VALUES (?, ?)",
            (cursor.lastrowid, content),
        )
    database.chmod(0o600)

    store = SQLiteMemoryStore(database, encryption_secret=b"m" * 32)
    store.initialize()
    migrated = store.get(namespace="user.default", memory_id=memory_id)

    assert migrated.content == content
    assert migrated.source == "legacy.import"
    assert migrated.tags == ("migration",)
    with sqlite3.connect(database) as connection:
        row = connection.execute(
            "SELECT content, source, tags_json, nonce, ciphertext FROM memory_items"
        ).fetchone()
        assert row is not None
        assert row[0:3] == ("", None, "[]")
        assert len(row[3]) == 12
        assert content.encode() not in row[4]
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 5


def test_store_rejects_tampered_conversation_turn_before_history(tmp_path: Path) -> None:
    store = _store(tmp_path)
    conversation = store.create_conversation(namespace="user.default")
    turns = store.append_conversation_exchange(
        namespace="user.default",
        conversation_id=conversation.conversation_id,
        user_content="pregunta original",
        assistant_content="respuesta original",
    )
    with sqlite3.connect(store.path) as connection:
        connection.execute(
            "UPDATE conversation_turns SET content = ? WHERE turn_id = ?",
            ("respuesta alterada", str(turns[1].turn_id)),
        )

    with pytest.raises(MemoryStoreError, match="content hash is invalid"):
        store.conversation_history(
            namespace="user.default",
            conversation_id=conversation.conversation_id,
        )


def test_vector_search_persists_embeddings_and_cascades_delete(tmp_path: Path) -> None:
    store = _store(tmp_path)
    close = store.put(
        namespace="user.default",
        kind=MemoryKind.SEMANTIC,
        content="preferencia de interfaz visual",
    )
    far = store.put(
        namespace="user.default",
        kind=MemoryKind.SEMANTIC,
        content="configuración de red",
    )
    store.put_embedding(
        namespace="user.default",
        memory_id=close.memory_id,
        model_id="test/embed",
        vector=(1.0, 0.0),
        content_sha256=close.content_sha256,
    )
    store.put_embedding(
        namespace="user.default",
        memory_id=far.memory_id,
        model_id="test/embed",
        vector=(0.0, 1.0),
        content_sha256=far.content_sha256,
    )

    hits = store.vector_search(
        namespace="user.default",
        model_id="test/embed",
        query_vector=(0.9, 0.1),
    )
    store.delete(namespace="user.default", memory_id=close.memory_id)
    after_delete = store.vector_search(
        namespace="user.default",
        model_id="test/embed",
        query_vector=(1.0, 0.0),
    )

    assert [hit.memory_id for hit in hits] == [close.memory_id, far.memory_id]
    assert [hit.memory_id for hit in after_delete] == [far.memory_id]


def test_vector_index_prunes_oldest_memory_to_strict_capacity(tmp_path: Path) -> None:
    store = _store(tmp_path, max_vectors=2)
    records = [
        store.put(
            namespace="user.default",
            kind=MemoryKind.SEMANTIC,
            content=f"memoria vectorial número {index}",
        )
        for index in range(3)
    ]
    for record in records:
        store.put_embedding(
            namespace=record.namespace,
            memory_id=record.memory_id,
            model_id="test/embed",
            vector=(1.0, 0.0),
            content_sha256=record.content_sha256,
        )

    hits = store.vector_search(
        namespace="user.default",
        model_id="test/embed",
        query_vector=(1.0, 0.0),
        limit=3,
    )

    assert {hit.memory_id for hit in hits} == {records[1].memory_id, records[2].memory_id}


def test_vector_search_validates_content_hash_before_returning(tmp_path: Path) -> None:
    store = _store(tmp_path)
    record = store.put(
        namespace="user.default",
        kind=MemoryKind.SEMANTIC,
        content="contenido vectorial original",
    )
    store.put_embedding(
        namespace=record.namespace,
        memory_id=record.memory_id,
        model_id="test/embed",
        vector=(1.0, 0.0),
        content_sha256=record.content_sha256,
    )
    with sqlite3.connect(store.path) as connection:
        connection.execute(
            "UPDATE memory_items SET content = ? WHERE memory_id = ?",
            ("contenido vectorial alterado", str(record.memory_id)),
        )

    with pytest.raises(MemorySecurityError, match="authentication failed"):
        store.vector_search(
            namespace="user.default",
            model_id="test/embed",
            query_vector=(1.0, 0.0),
        )


def test_vector_search_uses_sqlite_accelerator(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import aegis_core.memory.sqlite as memory_sqlite

    real_accelerator = memory_sqlite.sqlite_vec
    assert real_accelerator is not None
    load_calls = 0

    class TrackedAccelerator:
        @staticmethod
        def load(connection: sqlite3.Connection) -> None:
            nonlocal load_calls
            load_calls += 1
            real_accelerator.load(connection)

    monkeypatch.setattr(memory_sqlite, "sqlite_vec", TrackedAccelerator)

    def reject_python_fallback(*args: object, **kwargs: object) -> list[sqlite3.Row]:
        del args, kwargs
        raise AssertionError("accelerated search unexpectedly used Python fallback")

    monkeypatch.setattr(
        memory_sqlite.SQLiteMemoryStore,
        "_python_vector_search_rows",
        reject_python_fallback,
    )
    store = _store(tmp_path)
    record = store.put(
        namespace="user.default",
        kind=MemoryKind.SEMANTIC,
        content="memoria acelerada localmente",
    )
    store.put_embedding(
        namespace="user.default",
        memory_id=record.memory_id,
        model_id="test/embed",
        vector=(1.0, 0.0),
        content_sha256=record.content_sha256,
    )

    hits = store.vector_search(
        namespace="user.default",
        model_id="test/embed",
        query_vector=(1.0, 0.0),
    )

    assert [hit.memory_id for hit in hits] == [record.memory_id]
    assert load_calls == 1
    assert store.vector_acceleration_available() is True


def test_vector_search_falls_back_without_sqlite_accelerator(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import aegis_core.memory.sqlite as memory_sqlite

    monkeypatch.setattr(memory_sqlite, "sqlite_vec", None)
    store = _store(tmp_path)
    record = store.put(
        namespace="user.default",
        kind=MemoryKind.SEMANTIC,
        content="respaldo vectorial local",
    )
    store.put_embedding(
        namespace="user.default",
        memory_id=record.memory_id,
        model_id="test/embed",
        vector=(1.0, 0.0),
        content_sha256=record.content_sha256,
    )

    hits = store.vector_search(
        namespace="user.default",
        model_id="test/embed",
        query_vector=(1.0, 0.0),
    )

    assert [hit.memory_id for hit in hits] == [record.memory_id]
    assert store.vector_acceleration_available() is False


def test_schema_v1_is_migrated_without_losing_memory(tmp_path: Path) -> None:
    store = _store(tmp_path)
    record = store.put(
        namespace="user.default",
        kind=MemoryKind.PREFERENCE,
        content="dato anterior a embeddings",
    )
    with sqlite3.connect(store.path) as connection:
        connection.execute("DROP TABLE conversation_turns")
        connection.execute("DROP TABLE conversations")
        connection.execute("DROP TABLE memory_embeddings")
        connection.execute("PRAGMA user_version = 1")

    reopened = SQLiteMemoryStore(store.path, encryption_secret=b"m" * 32)
    reopened.initialize()
    reopened.put_embedding(
        namespace="user.default",
        memory_id=record.memory_id,
        model_id="test/embed",
        vector=(1.0, 0.0),
        content_sha256=record.content_sha256,
    )

    assert reopened.get(namespace="user.default", memory_id=record.memory_id) == record


def test_schema_v2_is_migrated_for_conversations(tmp_path: Path) -> None:
    store = _store(tmp_path)
    with sqlite3.connect(store.path) as connection:
        connection.execute("DROP TABLE conversation_turns")
        connection.execute("DROP TABLE conversations")
        connection.execute("PRAGMA user_version = 2")

    reopened = SQLiteMemoryStore(store.path, encryption_secret=b"m" * 32)
    reopened.initialize()
    conversation = reopened.create_conversation(
        namespace="user.default",
        title="Migrada",
    )

    assert conversation.title == "Migrada"


def test_conversation_exchange_is_atomic_ordered_and_persistent(tmp_path: Path) -> None:
    store = _store(tmp_path)
    conversation = store.create_conversation(
        namespace="user.default",
        title="Arquitectura",
    )

    first = store.append_conversation_exchange(
        namespace="user.default",
        conversation_id=conversation.conversation_id,
        user_content="Primera pregunta",
        assistant_content="Primera respuesta",
    )
    second = store.append_conversation_exchange(
        namespace="user.default",
        conversation_id=conversation.conversation_id,
        user_content="Segunda pregunta",
        assistant_content="Segunda respuesta",
    )
    history = store.conversation_history(
        namespace="user.default",
        conversation_id=conversation.conversation_id,
        limit=3,
    )

    assert [turn.sequence for turn in first + second] == [1, 2, 3, 4]
    assert [turn.sequence for turn in history] == [2, 3, 4]
    assert [turn.content for turn in history] == [
        "Primera respuesta",
        "Segunda pregunta",
        "Segunda respuesta",
    ]


def test_conversation_capacity_rejects_whole_exchange(tmp_path: Path) -> None:
    store = _store(tmp_path)
    conversation = store.create_conversation(namespace="user.default")
    store.append_conversation_exchange(
        namespace="user.default",
        conversation_id=conversation.conversation_id,
        user_content="uno",
        assistant_content="dos",
        max_turns=2,
    )

    with pytest.raises(MemoryStoreError, match="capacity"):
        store.append_conversation_exchange(
            namespace="user.default",
            conversation_id=conversation.conversation_id,
            user_content="tres",
            assistant_content="cuatro",
            max_turns=2,
        )

    history = store.conversation_history(
        namespace="user.default",
        conversation_id=conversation.conversation_id,
    )
    assert [turn.content for turn in history] == ["uno", "dos"]


def test_delete_conversation_cascades_turns_and_is_namespace_isolated(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path)
    conversation = store.create_conversation(namespace="private.one")
    store.append_conversation_exchange(
        namespace="private.one",
        conversation_id=conversation.conversation_id,
        user_content="pregunta",
        assistant_content="respuesta",
    )

    with pytest.raises(MemoryNotFoundError):
        store.delete_conversation(
            namespace="private.two",
            conversation_id=conversation.conversation_id,
        )
    store.delete_conversation(
        namespace="private.one",
        conversation_id=conversation.conversation_id,
    )

    with pytest.raises(MemoryNotFoundError):
        store.conversation_history(
            namespace="private.one",
            conversation_id=conversation.conversation_id,
        )
