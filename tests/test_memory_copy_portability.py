"""Own portable SQLite fixtures only. No account, app or owner's memory is read."""

import hashlib
import sqlite3
import threading
from dataclasses import replace

import pytest
from test_doctor import context as doctor_context
from test_doctor import fingerprint, inventory, write_json
from test_recovery import ctx, target

from jarvis_core import memory, profiles
from jarvis_core.activation import (
    activate_prepared_profile,
    review_prepared_profile,
    validate_bootstrap_admission,
)
from jarvis_core.doctor import DoctorError, _sqlite_check, inspect_report
from jarvis_core.recovery import (
    JsonSource,
    SourceBundle,
    SqliteImageSource,
    canonical,
    create_backup,
    restore_new,
    verify_backup,
)
from jarvis_core.recovery_fs import FileIdentity
from jarvis_core.recovery_sqlite import SnapshotError, SnapshotLimits, inspect_sqlite_snapshot

OPERATION = "c" * 32
SOURCE = '["jarvis","source_workspace",null]'
DESTINATION = '["jarvis","destination_workspace",null]'
TEXT = "MARCADOR SINTÉTICO Ω fase A"
TEXT_HASH = hashlib.sha256(TEXT.encode()).hexdigest()


def rows_digest(rows):
    return hashlib.sha256(canonical(rows)).hexdigest()


def mapping():
    return [
        0,
        "a" * 32,
        3,
        TEXT_HASH,
        len(TEXT.encode()),
        "b" * 32,
        1,
        TEXT_HASH,
        len(TEXT.encode()),
    ]


def update_digest(connection):
    rows = [
        list(row[1:])
        for row in connection.execute(
            "SELECT * FROM memory_scope_copy_documents WHERE operation_id=? ORDER BY ordinal",
            (OPERATION,),
        )
    ]
    connection.execute(
        "UPDATE memory_scope_copies SET mapping_sha256=? WHERE operation_id=?",
        (rows_digest(rows), OPERATION),
    )


def image(*, version=2, fts=False, live=True, change=None, copies=1):
    connection = sqlite3.connect(":memory:")
    try:
        connection.executescript(memory._SCHEMA)
        if version == 2:
            connection.executescript(memory._COPY_SCHEMA)
        connection.execute(f"PRAGMA application_id={memory._APPLICATION_ID}")
        connection.execute(f"PRAGMA user_version={version}")
        if fts:
            connection.execute(
                "CREATE VIRTUAL TABLE chunk_fts USING "
                "fts5(text, tokenize='unicode61 remove_diacritics 2')"
            )
        connection.execute(
            "INSERT INTO metadata VALUES('search_mode',?)",
            ("fts5_lexical" if fts else "lexical_scan",),
        )
        if live:
            for scope, doc, key, revision in (
                (SOURCE, "a" * 32, "original", 3),
                (DESTINATION, "b" * 32, "copy", 1),
            ):
                connection.execute("INSERT INTO scopes VALUES(?,?)", (scope, revision))
                connection.execute(
                    "INSERT INTO documents VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        doc,
                        scope,
                        key,
                        key,
                        "jarvis://synthetic",
                        revision,
                        TEXT,
                        TEXT_HASH,
                        len(TEXT.encode()),
                        1,
                        1,
                        revision,
                    ),
                )
                chunk = connection.execute(
                    "INSERT INTO chunks VALUES(NULL,?,?,?,?,?)",
                    (doc, 0, 0, len(TEXT.encode()), TEXT),
                )
                if fts:
                    connection.execute(
                        "INSERT INTO chunk_fts(rowid,text) VALUES(?,?)", (chunk.lastrowid, TEXT)
                    )
        if version == 2:
            for ordinal in range(copies):
                operation = OPERATION if ordinal == 0 else f"{ordinal:032x}"
                row = mapping()
                connection.execute(
                    "INSERT INTO memory_scope_copies VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        operation,
                        "d" * 64,
                        SOURCE,
                        3,
                        "e" * 64,
                        DESTINATION,
                        0,
                        1,
                        1,
                        len(TEXT.encode()),
                        rows_digest([row]),
                        1,
                        "local",
                    ),
                )
                connection.execute(
                    "INSERT INTO memory_scope_copy_documents VALUES(?,?,?,?,?,?,?,?,?,?)",
                    [operation, *row],
                )
        if change:
            change(connection)
        connection.commit()
        return connection.serialize()
    finally:
        connection.close()


def inspect(data, **kwargs):
    return inspect_sqlite_snapshot("memory", data, check_current=lambda: None, **kwargs)


@pytest.mark.parametrize("version", [1, 2])
@pytest.mark.parametrize("fts", [False, True])
def test_exact_v1_v2_memory_schemas_are_read_without_mutating_bytes(version, fts):
    data = image(version=version, fts=fts)
    before = hashlib.sha256(data).hexdigest()
    result = inspect(data)
    assert result.user_version == version and result.sha256 == before
    assert hashlib.sha256(data).hexdigest() == before
    assert result.agent_ids == ("jarvis",)
    if version == 2:
        assert result.counts["memory_scope_copies"] == 1
        assert result.counts["memory_scope_copy_documents"] == 1
    else:
        assert "memory_scope_copies" not in result.counts


@pytest.mark.parametrize(
    "change",
    [
        lambda c: c.execute("UPDATE memory_scope_copies SET request_digest='invalid'"),
        lambda c: c.execute("UPDATE memory_scope_copies SET source_fingerprint=?", ("G" * 64,)),
        lambda c: c.execute("UPDATE memory_scope_copies SET mapping_sha256=?", ("0" * 64,)),
        lambda c: c.execute(
            "UPDATE memory_scope_copies SET source_scope_key=?",
            ('["jarvis", "source_workspace", null]',),
        ),
        lambda c: c.execute(
            "UPDATE memory_scope_copies SET destination_scope_key=?",
            ('["other_agent","other_workspace",null]',),
        ),
        lambda c: c.execute(
            "UPDATE memory_scope_copies SET source_scope_key=?",
            ('["jarvis","source_workspace","session_bound"]',),
        ),
        lambda c: c.execute("UPDATE memory_scope_copies SET source_generation=?", (1 << 52,)),
        lambda c: c.execute("UPDATE memory_scope_copies SET source_generation='NaN'"),
        lambda c: c.execute("UPDATE memory_scope_copies SET document_count=2"),
        lambda c: c.execute("UPDATE memory_scope_copies SET byte_length=byte_length+1"),
        lambda c: c.execute("UPDATE memory_scope_copy_documents SET ordinal=1"),
        lambda c: c.execute("UPDATE memory_scope_copies SET created_ns=1e30"),
    ],
)
def test_invalid_historical_scope_metadata_map_and_digest_fail_closed(change):
    with pytest.raises(SnapshotError):
        inspect(image(change=change))


def test_history_is_not_revalidated_against_live_documents_or_agent_registry():
    def change(connection):
        connection.execute(
            "UPDATE memory_scope_copies SET source_scope_key=?,destination_scope_key=?",
            ('["removed_agent","old",null]', '["removed_agent","new",null]'),
        )

    result = inspect(image(live=False, change=change))
    assert result.agent_ids == () and result.memory_scopes == ()
    assert result.counts["documents"] == 0
    assert result.counts["memory_scope_copies"] == 1


def test_normal_edit_delete_and_scope_cleanup_do_not_invalidate_old_copy_facts():
    def change(connection):
        connection.execute("DELETE FROM chunks")
        connection.execute("DELETE FROM documents")
        connection.execute("DELETE FROM scopes")

    assert inspect(image(change=change)).counts["memory_scope_copy_documents"] == 1


def test_destinations_are_new_against_every_source_id_not_just_the_same_row():
    def change(connection):
        first = mapping()
        # A crafted permutation obeys per-row source!=dest, but neither target
        # is new: it claims to reuse the other source document from this copy.
        first[5] = "b" * 32
        second = [
            1,
            "b" * 32,
            1,
            TEXT_HASH,
            len(TEXT.encode()),
            "a" * 32,
            1,
            TEXT_HASH,
            len(TEXT.encode()),
        ]
        connection.execute("DELETE FROM memory_scope_copy_documents")
        for row in (first, second):
            connection.execute(
                "INSERT INTO memory_scope_copy_documents VALUES(?,?,?,?,?,?,?,?,?,?)",
                [OPERATION, *row],
            )
        connection.execute(
            "UPDATE memory_scope_copies SET document_count=2,byte_length=?",
            (2 * len(TEXT.encode()),),
        )
        update_digest(connection)

    with pytest.raises(SnapshotError, match="snapshot_memory_history_invalid"):
        inspect(image(live=False, change=change))


def test_large_legal_source_revision_and_unchanged_mapping_digest_recipe():
    def change(connection):
        connection.execute(
            "UPDATE memory_scope_copy_documents SET source_revision=?", ((1 << 63) - 1,)
        )
        update_digest(connection)

    assert inspect(image(change=change)).counts["memory_scope_copy_documents"] == 1


def test_foreign_map_references_and_schema_version_disagreements_fail():
    with pytest.raises(SnapshotError, match="snapshot_foreign_key_failed"):
        inspect(
            image(
                change=lambda c: c.execute(
                    "UPDATE memory_scope_copy_documents SET operation_id=?", ("f" * 32,)
                )
            )
        )
    for version in (1, 2):
        data = image(
            version=version,
            change=lambda c, version=version: c.execute(f"PRAGMA user_version={3 - version}"),
        )
        with pytest.raises(SnapshotError, match="snapshot_schema_unsupported"):
            inspect(data)


def test_receipt_limit_cannot_be_raised_by_generic_snapshot_row_limit():
    with pytest.raises(SnapshotError, match="snapshot_memory_history_limit"):
        inspect(image(live=False, copies=257), limits=SnapshotLimits(max_rows=200_000))


def test_historical_byte_limit_and_metadata_budget_are_separate(monkeypatch):
    import jarvis_core.recovery_sqlite as module

    def changed(connection):
        size = 1_048_577
        connection.execute(
            "UPDATE memory_scope_copy_documents SET source_byte_length=?, "
            "destination_byte_length=?",
            (size, size),
        )
        connection.execute("UPDATE memory_scope_copies SET byte_length=?", (size,))
        update_digest(connection)

    with pytest.raises(SnapshotError, match="snapshot_memory_history_invalid"):
        inspect(image(live=False, change=changed))
    data = image()
    monkeypatch.setattr(module, "_COPY_MAX_METADATA_BYTES", 1)
    with pytest.raises(SnapshotError, match="snapshot_memory_history_limit"):
        inspect(data)


@pytest.mark.parametrize("origin", ["local", "restored_history"])
def test_budget_reserves_restored_origin_length_without_mutating_rows(origin, monkeypatch):
    import jarvis_core.recovery_sqlite as module

    receipts, maps = [], []
    data = image(
        change=lambda c: c.execute("UPDATE memory_scope_copies SET origin_kind=?", (origin,))
    )
    with sqlite3.connect(":memory:") as connection:
        connection.deserialize(data)
        receipts = [
            [*row[:-1], "restored_history"]
            for row in connection.execute("SELECT * FROM memory_scope_copies ORDER BY operation_id")
        ]
        maps = [
            list(row)
            for row in connection.execute(
                "SELECT * FROM memory_scope_copy_documents ORDER BY operation_id,ordinal"
            )
        ]
    budget = len(canonical([receipts, maps]))
    monkeypatch.setattr(module, "_COPY_MAX_METADATA_BYTES", budget)
    assert inspect(data).user_version == 2
    monkeypatch.setattr(module, "_COPY_MAX_METADATA_BYTES", budget - 1)
    with pytest.raises(SnapshotError, match="snapshot_memory_history_limit"):
        inspect(data)


def test_cancellation_is_propagated_after_history_scan_started(monkeypatch):
    import jarvis_core.recovery_sqlite as module

    event = threading.Event()
    original = module._check_memory_copy_history

    class Stopped(RuntimeError):
        pass

    def guard():
        if event.is_set():
            raise Stopped("synthetic_cancel")

    def history(connection, check_current):
        event.set()
        return original(connection, check_current)

    monkeypatch.setattr(module, "_check_memory_copy_history", history)
    with pytest.raises(Stopped, match="synthetic_cancel"):
        inspect_sqlite_snapshot("memory", image(), check_current=guard)


@pytest.mark.parametrize("version", [1, 2])
def test_offline_doctor_uses_exact_schema_and_never_creates_cache_or_sidecars(
    tmp_path, monkeypatch, version
):
    root = tmp_path / "profile"
    root.mkdir(mode=0o700)
    data = image(version=version)
    (root / "memory.sqlite3").write_bytes(data)
    (root / "memory.sqlite3").chmod(0o600)
    write_json(root)
    before = fingerprint(root)
    original = sqlite3.connect

    def ram_only(path, *args, **kwargs):
        assert path == ":memory:"
        return original(path, *args, **kwargs)

    monkeypatch.setattr(sqlite3, "connect", ram_only)
    report = inspect_report(inventory(root, roles=("memory", "agents")), doctor_context())
    assert report.status == "ok"
    check = next(row for row in report.checks if row.role == "memory")
    assert check.schema_version == version and check.integrity == "ok"
    assert fingerprint(root) == before
    assert "MARCADOR SINTÉTICO" not in repr(report)
    assert "PRIVATE CANARY CHAT BODY" not in repr(report)


def test_doctor_does_not_accept_an_inspector_version_different_from_selected_header():
    data = image()
    valid = inspect(data)
    with pytest.raises(DoctorError, match="doctor_sqlite_identity_invalid"):
        _sqlite_check(
            "memory", data, doctor_context(), lambda *args, **kwargs: replace(valid, user_version=1)
        )


@pytest.mark.parametrize("version", [1, 2])
def test_backup_verify_restage_preserves_snapshots_and_marks_imported_history(tmp_path, version):
    root = tmp_path / "source"
    root.mkdir(mode=0o700)
    data = image(version=version, fts=True)
    path = root / "memory.sqlite3"
    path.write_bytes(data)
    path.chmod(0o600)
    selected = SourceBundle(
        "fixture_profile",
        1,
        root,
        FileIdentity.of(root.stat()),
        (
            SqliteImageSource(
                "memory",
                data,
                FileIdentity.of(path.stat()),
                hashlib.sha256(data).hexdigest(),
                lambda: None,
            ),
        ),
        (JsonSource("agents", canonical(profiles._initial()), lambda: None),),
        ("state", "schedules", "skills", "telegram"),
    )
    created = create_backup(selected, target(tmp_path), ctx())
    assert created.status == "completed"
    verified = verify_backup(tmp_path / "backup", ctx())
    restored = restore_new(tmp_path / "backup", verified, target(tmp_path, "restored"), ctx())
    assert restored.status == "completed" and path.read_bytes() == data
    copied = (tmp_path / "restored" / "memory.sqlite3").read_bytes()
    inspection = inspect(copied)
    assert inspection.user_version == version
    with sqlite3.connect(":memory:") as connection:
        connection.deserialize(copied)
        assert connection.execute("SELECT text FROM documents ORDER BY key").fetchall() == [
            (TEXT,),
            (TEXT,),
        ]
        if version == 2:
            assert connection.execute("SELECT origin_kind FROM memory_scope_copies").fetchall() == [
                ("restored_history",)
            ]
            assert connection.execute("SELECT mapping_sha256 FROM memory_scope_copies").fetchone()[
                0
            ] == rows_digest([mapping()])
    # Existing activation validator must understand this accepted memory schema;
    # all decisions/effects below concern only this own synthetic prepared root.
    prepared = tmp_path / "restored"
    review = review_prepared_profile(prepared, ctx())
    receipt = activate_prepared_profile(review, ctx())
    assert receipt.status == "completed"
    assert validate_bootstrap_admission(prepared).state == "local_present_valid"


def test_real_approved_core_copy_is_portable_and_preserves_actual_citations(tmp_path):
    import os
    import time
    from uuid import uuid4

    from jarvis_core.memory import (
        MemoryCopyContext,
        MemoryError,
        MemoryScope,
        MemoryStore,
        lookup_memory_scope_copy,
        review_memory_scope_copy,
    )
    from jarvis_core.recovery import SqliteSource

    root = tmp_path / "source"
    root.mkdir(mode=0o700)
    source_scope = MemoryScope("jarvis", "restored_source")
    destination_scope = MemoryScope("jarvis", "selected_destination")
    with MemoryStore(root / "memory.sqlite3") as store:
        store.upsert(
            source_scope,
            "first",
            "Nota azul café Ω " * 200,
            source_uri="file:///SYNTHETIC_NEVER_OPEN_THIS",
        )
        store.upsert(
            source_scope, "second", "MARCADOR violeta sintético", source_uri="jarvis://synthetic"
        )
        store.upsert(
            source_scope,
            "first",
            "Nota verde café Ω " * 200,
            source_uri="file:///SYNTHETIC_NEVER_OPEN_THIS",
            expected_revision=1,
        )
        before = store.list_documents(source_scope)
        old_citation = store.search(source_scope, "verde").hits[0].citation
        old_excerpt = store.resolve_citation(source_scope, old_citation)
        original_image = store._disk.serialize()
        assert inspect(original_image).user_version == 1
        identity = os.fstat(store._file_fd)
        review = review_memory_scope_copy(
            original_image,
            source_scope,
            destination_scope,
            database_identity=(identity.st_dev, identity.st_ino),
            check_current=lambda: None,
            limits=store.limits,
        )
        approved = MemoryCopyContext(
            uuid4().hex,
            "7" * 64,
            review.digest,
            lambda stage, write: None,
            threading.Event(),
            time.monotonic() + 20,
        )
        # Explicit synthetic owner/cut callbacks concern only this test's DB.
        receipt = store.copy_scope(review, approved)
        assert receipt.status == "completed" and not receipt.cleanup_pending
        assert store.list_documents(source_scope) == before
        after = store._disk.serialize()
        inspected = inspect(after)
        assert inspected.user_version == 2
        assert inspected.counts["memory_scope_copy_documents"] == 2
        assert (
            receipt.mapping_sha256
            == store._disk.execute("SELECT mapping_sha256 FROM memory_scope_copies").fetchone()[0]
        )
        copied = store.get(destination_scope, "first")
        assert copied.document.document_id != old_citation.document_id
        fresh_citation = store.search(destination_scope, "verde").hits[0].citation
        assert store.resolve_citation(destination_scope, fresh_citation) == old_excerpt
        with pytest.raises(MemoryError, match="stale_citation"):
            store.resolve_citation(destination_scope, old_citation)
        data_before_backup = (root / "memory.sqlite3").read_bytes()
        selected = SourceBundle(
            "fixture_profile",
            1,
            root,
            FileIdentity.of(root.stat()),
            (SqliteSource("memory", store._disk, lambda: None),),
            (JsonSource("agents", canonical(profiles._initial()), lambda: None),),
            ("state", "schedules", "skills", "telegram"),
        )
        assert create_backup(selected, target(tmp_path), ctx()).status == "completed"
        assert (root / "memory.sqlite3").read_bytes() == data_before_backup
        verified = verify_backup(tmp_path / "backup", ctx())
        assert (
            restore_new(tmp_path / "backup", verified, target(tmp_path, "restored"), ctx()).status
            == "completed"
        )
    prepared = tmp_path / "restored"
    admission = activate_prepared_profile(review_prepared_profile(prepared, ctx()), ctx())
    assert admission.status == "completed"
    with MemoryStore(prepared / "memory.sqlite3") as restored:
        assert restored.resolve_citation(source_scope, old_citation) == old_excerpt
        assert restored.resolve_citation(destination_scope, fresh_citation) == old_excerpt
        imported = restored._disk.serialize()
        assert inspect(imported).user_version == 2
        with pytest.raises(MemoryError, match="memory_copy_receipt_historical"):
            lookup_memory_scope_copy(
                imported, receipt.operation_id, receipt.request_digest, check_current=lambda: None
            )
        assert (
            restored._disk.execute("SELECT origin_kind FROM memory_scope_copies").fetchone()[0]
            == "restored_history"
        )
        # Ordinary v2 edits/deletes remain legal; history itself is not a live FK.
        restored.upsert(
            destination_scope,
            "first",
            "Contenido sintético cambiado",
            source_uri="jarvis://changed",
            expected_revision=1,
        )
        restored.clear_scope(source_scope)
        restored.clear_scope(destination_scope)
        empty = inspect(restored._disk.serialize())
        assert empty.counts["documents"] == 0 and empty.agent_ids == ()
        assert empty.counts["memory_scope_copies"] == 1
