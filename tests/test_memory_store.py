import os
import sqlite3
import stat
from pathlib import Path
from uuid import uuid4

import pytest

from aegis_core.memory.contracts import MemoryKind
from aegis_core.memory.sqlite import (
    MemoryCapacityError,
    MemoryNotFoundError,
    MemoryQueryError,
    MemorySecurityError,
    MemoryStoreError,
    SecretMaterialError,
    SQLiteMemoryStore,
)


def _store(tmp_path: Path, *, max_entries: int = 50_000) -> SQLiteMemoryStore:
    tmp_path.chmod(0o700)
    store = SQLiteMemoryStore(tmp_path / "memory.sqlite3", max_entries=max_entries)
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

    reopened = SQLiteMemoryStore(tmp_path / "memory.sqlite3")
    reopened.initialize()
    loaded = reopened.get(namespace="user.default", memory_id=created.memory_id)

    assert loaded == created
    assert stat.S_IMODE(reopened.path.stat().st_mode) == 0o600
    assert loaded.content_sha256 != loaded.content


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
        SQLiteMemoryStore(database).initialize()


def test_store_rejects_non_private_directory(tmp_path: Path) -> None:
    public = tmp_path / "public"
    public.mkdir(mode=0o755)
    public.chmod(0o755)

    with pytest.raises(MemorySecurityError):
        SQLiteMemoryStore(public / "memory.sqlite3").initialize()


def test_store_rejects_database_owned_by_unexpected_uid(tmp_path: Path) -> None:
    tmp_path.chmod(0o700)
    database = tmp_path / "memory.sqlite3"
    database.touch(mode=0o600)

    with pytest.raises(MemorySecurityError):
        SQLiteMemoryStore(database, expected_uid=os.getuid() + 1).initialize()


def test_store_refuses_to_modify_an_unrelated_database(tmp_path: Path) -> None:
    tmp_path.chmod(0o700)
    database = tmp_path / "memory.sqlite3"
    with sqlite3.connect(database) as connection:
        connection.execute("CREATE TABLE user_data (value TEXT)")
    database.chmod(0o600)

    with pytest.raises(MemoryStoreError, match="unrelated database"):
        SQLiteMemoryStore(database).initialize()


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
    store = SQLiteMemoryStore(parent / "memory.sqlite3")
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

    with pytest.raises(MemoryStoreError, match="content hash is invalid"):
        store.get(namespace="user.default", memory_id=record.memory_id)
    with pytest.raises(MemoryStoreError, match="content hash is invalid"):
        store.search(namespace="user.default", query="original verificable")


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

    reopened = SQLiteMemoryStore(store.path)
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

    reopened = SQLiteMemoryStore(store.path)
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
