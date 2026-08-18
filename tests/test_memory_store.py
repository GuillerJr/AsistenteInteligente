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
