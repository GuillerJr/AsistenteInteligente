import os
import sqlite3
import stat
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import pytest

from aegis_core.memory.contracts import MemoryEvidence, MemoryKind, MemoryRecord
from aegis_core.memory.sqlite import (
    DatabaseCapacityError,
    DecryptionAuthError,
    GraphEmbeddingCandidate,
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
    max_namespace_entries: int | None = None,
    max_node_embeddings: int = 2_000,
) -> SQLiteMemoryStore:
    tmp_path.chmod(0o700)
    store = SQLiteMemoryStore(
        tmp_path / "memory.sqlite3",
        max_entries=max_entries,
        max_namespace_entries=max_namespace_entries,
        max_node_embeddings=max_node_embeddings,
        encryption_secret=b"m" * 32,
    )
    store.initialize()
    return store


def _document_node(
    store: SQLiteMemoryStore,
    record: MemoryRecord,
) -> GraphEmbeddingCandidate:
    return next(
        candidate
        for candidate in store.graph_nodes_for_memory(
            namespace=record.namespace,
            memory_id=record.memory_id,
        )
        if candidate.type == "document"
    )


def _put_document_embedding(
    store: SQLiteMemoryStore,
    record: MemoryRecord,
    vector: tuple[float, ...],
) -> GraphEmbeddingCandidate:
    node = _document_node(store, record)
    store.put_node_embedding(
        namespace=node.namespace,
        node_id=node.node_id,
        model_id="test/embed",
        vector=vector,
        content_sha256=node.content_sha256,
    )
    return node


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


def test_namespace_capacity_atomically_evicts_oldest_memory(tmp_path: Path) -> None:
    store = _store(tmp_path, max_entries=1)
    first = store.put(
        namespace="user.default",
        kind=MemoryKind.EPISODIC,
        content="primera memoria",
    )
    _put_document_embedding(store, first, (1.0, 0.0))

    second = store.put(
        namespace="user.default",
        kind=MemoryKind.EPISODIC,
        content="segunda memoria",
    )

    with pytest.raises(MemoryNotFoundError):
        store.get(namespace="user.default", memory_id=first.memory_id)
    assert store.get(namespace="user.default", memory_id=second.memory_id) == second
    assert store.search(namespace="user.default", query="primera") == ()
    with store._connect(load_vector_extension=True) as connection:
        assert connection.execute("PRAGMA secure_delete").fetchone()[0] == 1
        assert connection.execute("SELECT COUNT(*) FROM node_embeddings").fetchone()[0] == 0


def test_global_capacity_does_not_evict_an_unrelated_namespace(tmp_path: Path) -> None:
    store = _store(tmp_path, max_entries=1)
    first = store.put(
        namespace="project.alpha",
        kind=MemoryKind.EPISODIC,
        content="memoria alfa",
    )

    with pytest.raises(DatabaseCapacityError, match="global memory capacity"):
        store.put(
            namespace="project.beta",
            kind=MemoryKind.EPISODIC,
            content="memoria beta",
        )

    assert store.get(namespace="project.alpha", memory_id=first.memory_id) == first


def test_parameterized_fifo_is_strictly_isolated_by_namespace(tmp_path: Path) -> None:
    store = _store(tmp_path, max_entries=4, max_namespace_entries=2)
    alpha_oldest = store.put(
        namespace="project.alpha",
        kind=MemoryKind.SEMANTIC,
        content="alpha primera",
    )
    alpha_kept = store.put(
        namespace="project.alpha",
        kind=MemoryKind.SEMANTIC,
        content="alpha segunda",
    )
    beta = store.put(
        namespace="project.beta",
        kind=MemoryKind.SEMANTIC,
        content="beta estable",
    )

    alpha_newest = store.put(
        namespace="project.alpha",
        kind=MemoryKind.SEMANTIC,
        content="alpha tercera",
    )

    with pytest.raises(MemoryNotFoundError):
        store.get(namespace="project.alpha", memory_id=alpha_oldest.memory_id)
    assert store.get(namespace="project.alpha", memory_id=alpha_kept.memory_id) == alpha_kept
    assert store.get(namespace="project.alpha", memory_id=alpha_newest.memory_id) == alpha_newest
    assert store.get(namespace="project.beta", memory_id=beta.memory_id) == beta


def test_failed_memory_write_rolls_back_as_database_capacity_error(tmp_path: Path) -> None:
    store = _store(tmp_path)
    with store._connect() as connection:
        connection.execute(
            """
            CREATE TRIGGER reject_memory_insert
            BEFORE INSERT ON memory_items
            BEGIN
                SELECT RAISE(ABORT, 'forced write failure');
            END
            """
        )

    with pytest.raises(DatabaseCapacityError, match="atomic memory capacity write failed"):
        store.put(
            namespace="user.default",
            kind=MemoryKind.EPISODIC,
            content="esta fila debe revertirse",
        )

    with store._connect(read_only=True) as connection:
        assert connection.execute("SELECT COUNT(*) FROM memory_items").fetchone()[0] == 0
        assert connection.execute("SELECT COUNT(*) FROM memory_fts").fetchone()[0] == 0


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
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 6
        assert connection.execute("SELECT COUNT(*) FROM nodes").fetchone()[0] >= 1
        assert connection.execute(
            "SELECT COUNT(*) FROM sqlite_master WHERE name = 'memory_embeddings'"
        ).fetchone()[0] == 0


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


def test_graph_seed_search_persists_embeddings_and_cascades_delete(tmp_path: Path) -> None:
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
    close_node = _put_document_embedding(store, close, (1.0, 0.0))
    far_node = _put_document_embedding(store, far, (0.0, 1.0))

    hits = store.graph_seed_search(
        namespace="user.default",
        model_id="test/embed",
        query_vector=(0.9, 0.1),
    )
    store.delete(namespace="user.default", memory_id=close.memory_id)
    after_delete = store.graph_seed_search(
        namespace="user.default",
        model_id="test/embed",
        query_vector=(1.0, 0.0),
    )

    assert [hit.node_id for hit in hits] == [close_node.node_id, far_node.node_id]
    assert [hit.node_id for hit in after_delete] == [far_node.node_id]


def test_graph_fifo_prunes_only_old_embedding_and_keeps_memory(tmp_path: Path) -> None:
    store = _store(tmp_path, max_node_embeddings=2)
    records = []
    for index in range(2):
        record = store.put(
            namespace="user.default",
            kind=MemoryKind.SEMANTIC,
            content=f"memoria vectorial token{index}",
        )
        _put_document_embedding(store, record, (1.0, 0.0))
        records.append(record)

    newest = store.put(
        namespace="user.default",
        kind=MemoryKind.SEMANTIC,
        content="memoria vectorial token2",
    )

    newest_node = _put_document_embedding(store, newest, (1.0, 0.0))

    assert store.get(namespace="user.default", memory_id=records[0].memory_id) == records[0]
    assert len(store.search(namespace="user.default", query="token0")) == 1
    with store._connect(load_vector_extension=True) as connection:
        assert connection.execute("PRAGMA secure_delete").fetchone()[0] == 1
        assert connection.execute("SELECT COUNT(*) FROM node_embeddings").fetchone()[0] == 2

    hits = store.graph_seed_search(
        namespace="user.default",
        model_id="test/embed",
        query_vector=(1.0, 0.0),
        limit=2,
    )

    retained = {hit.node_id for hit in hits}
    assert newest_node.node_id in retained
    assert _document_node(store, records[0]).node_id not in retained


def test_graph_embedding_fifo_capacity_is_isolated_per_namespace(tmp_path: Path) -> None:
    store = _store(tmp_path, max_node_embeddings=2)
    records = []
    for namespace in ("project.alpha", "project.beta"):
        for index in range(2):
            record = store.put(
                namespace=namespace,
                kind=MemoryKind.SEMANTIC,
                content=f"{namespace} vector {index}",
            )
            _put_document_embedding(store, record, (1.0, 0.0))
            records.append(record)

    with sqlite3.connect(store.path) as connection:
        counts = connection.execute(
            """
            SELECT namespace, COUNT(*)
            FROM node_embedding_metadata
            GROUP BY namespace ORDER BY namespace
            """
        ).fetchall()

    assert counts == [("project.alpha", 2), ("project.beta", 2)]
    assert all(
        store.get(namespace=record.namespace, memory_id=record.memory_id) == record
        for record in records
    )


def test_graph_search_authenticates_node_before_returning_context(tmp_path: Path) -> None:
    store = _store(tmp_path)
    record = store.put(
        namespace="user.default",
        kind=MemoryKind.SEMANTIC,
        content="contenido vectorial original",
    )
    node = _put_document_embedding(store, record, (1.0, 0.0))
    with sqlite3.connect(store.path) as connection:
        connection.execute(
            "UPDATE nodes SET name = ? WHERE node_id = ?",
            ("altered-blind-index", str(node.node_id)),
        )

    with pytest.raises(MemorySecurityError, match="authentication failed"):
        store.load_graph_neighborhood(
            namespace="user.default",
            seed_ids=(node.node_id,),
        )


def test_graph_seed_search_uses_sqlite_accelerator(
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

    store = _store(tmp_path)
    record = store.put(
        namespace="user.default",
        kind=MemoryKind.SEMANTIC,
        content="memoria acelerada localmente",
    )
    node = _put_document_embedding(store, record, (1.0, 0.0))

    hits = store.graph_seed_search(
        namespace="user.default",
        model_id="test/embed",
        query_vector=(1.0, 0.0),
    )

    assert [hit.node_id for hit in hits] == [node.node_id]
    assert load_calls >= 3
    assert store.vector_acceleration_available() is True


def test_graph_store_fails_closed_without_sqlite_accelerator(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import aegis_core.memory.sqlite as memory_sqlite

    monkeypatch.setattr(memory_sqlite, "sqlite_vec", None)
    tmp_path.chmod(0o700)
    store = SQLiteMemoryStore(tmp_path / "memory.sqlite3", encryption_secret=b"m" * 32)
    with pytest.raises(RuntimeError, match="acceleration is unavailable"):
        store.initialize()
    assert store.vector_acceleration_available() is False


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
