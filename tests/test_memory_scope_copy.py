"""Own real SQLite fixtures; copy is host-only and never touches providers/files in source URIs."""

import os
import sqlite3
import threading
import time
from dataclasses import replace
from types import SimpleNamespace
from uuid import uuid4

import pytest

from jarvis_core.memory import (
    MemoryCopyContext,
    MemoryCopyLimits,
    MemoryError,
    MemoryScope,
    MemoryScopeCopyError,
    MemoryStore,
    lookup_memory_scope_copy,
    memory_scope_summaries,
    review_memory_scope_copy,
)

SOURCE = MemoryScope("jarvis", "restored_scope")
DEST = MemoryScope("jarvis", "chosen_workspace")


@pytest.fixture
def store(tmp_path):
    value = MemoryStore(tmp_path.resolve() / "memory" / "memory.sqlite3")
    value.upsert(SOURCE, "first", "Nota azul café " * 220, source_uri="file:///NEVER/OPEN/THIS")
    value.upsert(SOURCE, "second", "TOPACIO amarillo " * 120, source_uri="jarvis://fixture")
    value.upsert(SOURCE, "first", "Nota verde café " * 220, source_uri="file:///NEVER/OPEN/THIS")
    try:
        yield value
    finally:
        if value._copy_owner is not None:
            value._copy_owner.cleanup()
        value.close()


def image(store):
    return store._disk.serialize()


def review(store, source=SOURCE, destination=DEST, *, copy_limits=None):
    info = os.fstat(store._file_fd)
    return review_memory_scope_copy(
        image(store),
        source,
        destination,
        database_identity=(info.st_dev, info.st_ino),
        check_current=lambda: None,
        limits=store.limits,
        copy_limits=copy_limits,
    )


def context(
    value,
    *,
    guard=lambda stage, write: None,
    cancellation=None,
    operation_id=None,
    request_digest="b" * 64,
    clock=time.monotonic,
    deadline=None,
):
    return MemoryCopyContext(
        operation_id or uuid4().hex,
        request_digest,
        value.digest,
        guard,
        cancellation or threading.Event(),
        clock() + 20 if deadline is None else deadline,
        clock,
    )


def sql_state(store):
    connection = store._disk
    version = connection.execute("PRAGMA user_version").fetchone()[0]
    return version, tuple(
        (
            name,
            tuple(
                tuple(row)
                for row in connection.execute('SELECT * FROM "' + name + '" ORDER BY 1,2')
            ),
        )
        for (name,) in connection.execute(
            "SELECT name FROM sqlite_schema WHERE type='table' "
            "AND name NOT LIKE 'chunk_fts%' ORDER BY name"
        )
    )


def test_atomic_copy_v1_upgrade_preserves_source_and_generates_new_ids_citations(store):
    old = store.get(SOURCE, "first")
    old_citation = store.search(SOURCE, "verde").hits[0].citation
    before = store.list_documents(SOURCE)
    value = review(store)
    assert value.migration_required and value.schema_before == 1 and value.schema_after == 2
    receipt = store.copy_scope(value, context(value))
    assert receipt.status == "completed" and receipt.effect_status == "scope_copy_committed"
    assert not store._disk.in_transaction and store._copy_owner is None
    assert store._disk.execute("PRAGMA user_version").fetchone()[0] == 2
    assert store.list_documents(SOURCE) == before
    copied = store.get(DEST, "first")
    assert copied.text == old.text and copied.document.document_id != old.document.document_id
    assert copied.document.revision == 1 and old.document.revision == 2
    assert copied.document.source_uri == old.document.source_uri
    assert not copied.document.source_verified
    assert store.resolve_citation(SOURCE, old_citation)
    with pytest.raises(MemoryError, match="stale_citation"):
        store.resolve_citation(DEST, old_citation)
    fresh = store.search(DEST, "verde").hits[0].citation
    assert store.resolve_citation(DEST, fresh) == store.resolve_citation(SOURCE, old_citation)
    observation = lookup_memory_scope_copy(
        image(store), receipt.operation_id, receipt.request_digest, check_current=lambda: None
    )
    assert observation.current_matching_documents == 2 and observation.current_documents_match
    assert observation.current_scope_generation_matches
    assert observation.state == "local_record_present"
    assert observation.prior_acknowledgement == "not_established"


def test_normal_v1_reads_and_writes_never_upgrade(store):
    before = image(store)
    memory_scope_summaries(before, check_current=lambda: None)
    review(store)
    lookup_memory_scope_copy(before, "a" * 32, "b" * 64, check_current=lambda: None)
    assert image(store) == before
    store.search(SOURCE, "verde")
    store.upsert(SOURCE, "normal", "Se queda en v1", source_uri="manual://fixture")
    assert store._disk.execute("PRAGMA user_version").fetchone()[0] == 1
    assert not store._disk.execute(
        "SELECT name FROM sqlite_schema WHERE name LIKE 'memory_scope_copy%'"
    ).fetchall()


@pytest.mark.parametrize("change", ["agent", "same_scope", "session", "temporary", "occupied"])
def test_invalid_scope_choices_fail_before_any_mutation(store, change):
    destination = DEST
    if change == "agent":
        destination = replace(DEST, agent_id="another")
    elif change == "same_scope":
        destination = SOURCE
    elif change == "session":
        destination = replace(DEST, session_id="history")
    elif change == "temporary":
        destination = replace(DEST, session_id="ram", temporary=True)
    else:
        store.upsert(DEST, "occupied", "Do not merge", source_uri="manual://fixture")
    before = sql_state(store)
    with pytest.raises(MemoryError):
        review(store, destination=destination)
    assert sql_state(store) == before


def test_empty_tombstone_destination_generation_is_respected(store):
    store.upsert(DEST, "gone", "Temporary old text", source_uri="manual://fixture")
    store.clear_scope(DEST)
    value = review(store)
    assert value.destination.exists and value.destination.generation == 2
    store.copy_scope(value, context(value))
    assert store.search(DEST, "verde").generation == 3


@pytest.mark.parametrize(
    "stage",
    [
        "memory_copy_open_existing",
        "memory_copy_schema",
        "memory_copy_document",
        "memory_copy_chunk",
        "memory_copy_generation",
        "memory_copy_commit",
    ],
)
def test_cancel_before_commit_rolls_back_everything_including_schema_and_fts(store, stage):
    value, before = review(store), sql_state(store)
    cancellation = threading.Event()

    def guard(current, write):
        if current == stage:
            cancellation.set()

    with pytest.raises(MemoryScopeCopyError, match="memory_copy_cancelled") as caught:
        store.copy_scope(value, context(value, guard=guard, cancellation=cancellation))
    assert caught.value.receipt.status == "cancelled"
    assert caught.value.receipt.effect_status == "no_scope_copy_committed"
    assert caught.value.owner is None and sql_state(store) == before
    assert not store._disk.in_transaction and not store.search(DEST, "verde").hits


def test_cancel_after_commit_is_unknown_with_exact_record_no_replay(store):
    value = review(store)
    cancellation = threading.Event()

    def guard(stage, write):
        if stage == "memory_copy_committed":
            cancellation.set()

    operation = context(value, guard=guard, cancellation=cancellation)
    with pytest.raises(MemoryScopeCopyError) as caught:
        store.copy_scope(value, operation)
    receipt = caught.value.receipt
    assert receipt.status == "unknown" and receipt.effect_status == "scope_copy_unknown"
    assert caught.value.owner is None and not store._disk.in_transaction
    assert len(store.list_documents(DEST)) == 2
    observed = lookup_memory_scope_copy(
        image(store), operation.operation_id, operation.request_digest, check_current=lambda: None
    )
    assert observed.current_documents_match and observed.prior_acknowledgement == "not_established"
    with pytest.raises(MemoryScopeCopyError, match="memory_copy_context_replayed") as replay:
        store.copy_scope(value, operation)
    assert replay.value.receipt is None
    another = context(value, operation_id=operation.operation_id)
    with pytest.raises(MemoryScopeCopyError, match="memory_copy_operation_replayed"):
        store.copy_scope(value, another)
    assert len(store.list_documents(DEST)) == 2


def test_borrowed_context_and_changed_source_or_destination_fail_closed(store):
    value = review(store)
    other = review(store, destination=MemoryScope("jarvis", "other_workspace"))
    before = sql_state(store)
    with pytest.raises(MemoryScopeCopyError, match="memory_copy_review_mismatch"):
        store.copy_scope(other, context(value))
    assert sql_state(store) == before
    store.upsert(SOURCE, "second", "Changed source", source_uri="manual://fixture")
    before = sql_state(store)
    with pytest.raises(MemoryScopeCopyError, match="memory_copy_review_changed"):
        store.copy_scope(value, context(value))
    assert sql_state(store) == before


def test_history_remains_valid_after_normal_edits_deletes_and_scope_clear(store):
    value = review(store)
    receipt = store.copy_scope(value, context(value))
    store.upsert(DEST, "first", "Changed current text", source_uri="manual://fixture")
    store.delete(SOURCE, "second")
    store.clear_scope(DEST)
    observation = lookup_memory_scope_copy(
        image(store), receipt.operation_id, receipt.request_digest, check_current=lambda: None
    )
    assert observation.document_count == 2 and not observation.current_documents_match
    assert not observation.current_scope_generation_matches
    store.upsert(SOURCE, "normal", "v2 is still writable", source_uri="manual://fixture")
    assert store._disk.execute("SELECT count(*) FROM memory_scope_copies").fetchone()[0] == 1


def test_imported_history_never_becomes_local_receipt_or_grant(store):
    value = review(store)
    receipt = store.copy_scope(value, context(value))
    store._disk.execute("UPDATE memory_scope_copies SET origin_kind='restored_history'")
    with pytest.raises(MemoryError, match="memory_copy_receipt_historical"):
        lookup_memory_scope_copy(
            image(store), receipt.operation_id, receipt.request_digest, check_current=lambda: None
        )


def test_copy_limits_metadata_and_physical_caps_reject_without_partial_copy(store):
    before = sql_state(store)
    with pytest.raises(MemoryError, match="memory_copy_history_capacity"):
        review(store, copy_limits=MemoryCopyLimits(max_metadata_bytes=1))
    assert sql_state(store) == before
    current_bytes = store._disk.execute("PRAGMA page_count").fetchone()[0] * 4096
    caps = MemoryCopyLimits(max_sqlite_bytes=current_bytes)
    value = review(store, copy_limits=caps)
    with pytest.raises(MemoryScopeCopyError) as caught:
        store.copy_scope(value, context(value), copy_limits=caps)
    assert caught.value.receipt.effect_status == "no_scope_copy_committed"
    assert caught.value.code == "memory_copy_physical_limit"
    assert not store._disk.in_transaction and sql_state(store) == before


def test_plain_v1_snapshot_inventory_does_not_open_any_source_file(store, monkeypatch):
    data = image(store)

    def forbidden(*args, **kwargs):
        pytest.fail("pure RAM inventory attempted filesystem IO")

    monkeypatch.setattr(os, "open", forbidden)
    result = memory_scope_summaries(data, check_current=lambda: None)
    assert result[0].document_count == 2 and result[0].scope == SOURCE


class FaultConnection(sqlite3.Connection):
    fault = None
    copied_documents = 0

    def execute(self, sql, parameters=()):
        statement = sql.strip().upper()
        if self.fault == "second_document" and statement.startswith("INSERT INTO DOCUMENTS"):
            self.copied_documents += 1
            if self.copied_documents == 2:
                raise sqlite3.OperationalError("synthetic second document failure")
        if self.fault == "version_ack" and statement == "PRAGMA USER_VERSION=2":
            super().execute(sql, parameters)
            self.fault = None
            raise sqlite3.OperationalError("synthetic DDL/version ACK failure")
        if self.fault == "commit_ack" and statement == "COMMIT":
            super().execute(sql, parameters)
            self.fault = None
            raise OSError("synthetic ACK failure after actual COMMIT")
        if self.fault == "rollback_once" and statement == "ROLLBACK":
            self.fault = None
            raise OSError("synthetic rollback could not be confirmed")
        if (self.fault == "restore_policy" and statement.startswith("PRAGMA SYNCHRONOUS=")
                and statement != "PRAGMA SYNCHRONOUS=EXTRA"):
            raise OSError("synthetic owned connection policy could not be restored")
        return super().execute(sql, parameters)


@pytest.fixture
def fault_store(tmp_path, monkeypatch):
    real_connect = sqlite3.connect

    def connector(*args, **kwargs):
        return real_connect(*args, factory=FaultConnection, **kwargs)

    monkeypatch.setattr(sqlite3, "connect", connector)
    value = MemoryStore(tmp_path.resolve() / "memory" / "memory.sqlite3")
    value.upsert(SOURCE, "one", "CAFÉ primero " * 200, source_uri="manual://one")
    value.upsert(SOURCE, "two", "CAFÉ segundo " * 200, source_uri="manual://two")
    try:
        yield value
    finally:
        value._disk.fault = None
        if value._copy_owner is not None:
            value._copy_owner.cleanup()
        value.close()


@pytest.mark.parametrize("fault", ["second_document", "version_ack"])
def test_sql_fault_rolls_back_v2_tables_docs_fts_generation_and_receipt(fault_store, fault):
    store = fault_store
    value, before = review(store), sql_state(store)
    store._disk.fault = fault
    with pytest.raises(MemoryScopeCopyError) as caught:
        store.copy_scope(value, context(value))
    assert caught.value.receipt.status == "failed" and caught.value.owner is None
    assert caught.value.receipt.effect_status == "no_scope_copy_committed"
    assert not store._disk.in_transaction and sql_state(store) == before
    assert not store.search(DEST, "cafe").hits
    assert len(store.search(SOURCE, "cafe").hits) > 0


def test_commit_ack_loss_is_persisted_local_observation_not_repeated(fault_store):
    store = fault_store
    value = review(store)
    operation = context(value)
    store._disk.fault = "commit_ack"
    with pytest.raises(MemoryScopeCopyError) as caught:
        store.copy_scope(value, operation)
    assert caught.value.receipt.status == "unknown" and not caught.value.receipt.cleanup_pending
    assert caught.value.owner is None and not store._disk.in_transaction
    observed = lookup_memory_scope_copy(
        image(store), operation.operation_id, operation.request_digest, check_current=lambda: None
    )
    assert observed.current_documents_match and observed.current_matching_documents == 2
    with pytest.raises(MemoryError, match="memory_copy_operation_conflict"):
        lookup_memory_scope_copy(
            image(store), operation.operation_id, "c" * 64, check_current=lambda: None
        )
    with pytest.raises(MemoryScopeCopyError, match="memory_copy_operation_conflict") as repeated:
        store.copy_scope(
            value, context(value, operation_id=operation.operation_id, request_digest="c" * 64)
        )
    assert repeated.value.receipt is None
    assert len(store.list_documents(DEST)) == 2


def test_failed_rollback_retains_same_store_connection_and_blocks_normal_use(fault_store):
    store = fault_store
    value, before = review(store), sql_state(store)
    cancellation, counts = threading.Event(), [0]

    def guard(stage, write):
        if stage == "memory_copy_document":
            counts[0] += 1
            if counts[0] == 2:
                cancellation.set()

    store._disk.fault = "rollback_once"
    with pytest.raises(MemoryScopeCopyError) as caught:
        store.copy_scope(value, context(value, guard=guard, cancellation=cancellation))
    error, owner = caught.value, caught.value.owner
    assert owner is not None and owner.connection is store._disk and store._disk.in_transaction
    assert error.receipt.status == "unknown" and error.receipt.cleanup_pending
    for operation in (
        lambda: store.get(SOURCE, "one"),
        lambda: store.search(DEST, "cafe"),
        lambda: store.upsert(DEST, "no", "must block", source_uri="manual://no"),
        store.close,
    ):
        with pytest.raises(MemoryError, match="memory_copy_cleanup_incomplete"):
            operation()
    owner.cleanup()
    assert owner.closed and store._copy_owner is None and not store._disk.in_transaction
    assert sql_state(store) == before and not store.search(DEST, "cafe").hits
    assert error.receipt.status == "unknown"  # No replacement acknowledgement.


def test_commit_policy_cleanup_failure_retains_same_owner_without_repeating_copy(fault_store):
    store = fault_store
    value, operation = review(store), None
    operation = context(value)
    store._disk.fault = "restore_policy"
    with pytest.raises(MemoryScopeCopyError) as caught:
        store.copy_scope(value, operation)
    error, owner = caught.value, caught.value.owner
    assert error.receipt.status == "unknown" and error.receipt.cleanup_pending
    assert owner is not None and owner.connection is store._disk
    assert not store._disk.in_transaction and owner.commit_attempted
    with pytest.raises(MemoryError, match="memory_copy_cleanup_incomplete"):
        store.get(DEST, "one")
    observation = lookup_memory_scope_copy(
        image(store), operation.operation_id, operation.request_digest, check_current=lambda: None
    )
    assert observation.current_documents_match and observation.current_matching_documents == 2
    store._disk.fault = None
    owner.cleanup()
    assert owner.closed and store._copy_owner is None and len(store.list_documents(DEST)) == 2
    assert store._disk.execute("SELECT count(*) FROM memory_scope_copies").fetchone()[0] == 1
    assert error.receipt.status == "unknown" and error.receipt.cleanup_pending


def test_new_document_identity_collision_never_adopts_old_document_or_upgrades(store, monkeypatch):
    import jarvis_core.memory as module

    value, before = review(store), sql_state(store)
    operation = context(value)
    existing = store.list_documents(SOURCE)[0].document_id
    monkeypatch.setattr(module, "uuid4", lambda: SimpleNamespace(hex=existing))
    with pytest.raises(MemoryScopeCopyError, match="memory_copy_id_collision") as caught:
        store.copy_scope(value, operation)
    assert caught.value.owner is None
    assert caught.value.receipt.effect_status == "no_scope_copy_committed"
    assert sql_state(store) == before and not store._disk.in_transaction


def test_snapshot_oversized_schema_sql_is_rejected_before_schema_tokenization(store, monkeypatch):
    import jarvis_core.memory as module

    clone = sqlite3.connect(":memory:")
    try:
        clone.deserialize(image(store))
        clone.execute("PRAGMA writable_schema=ON")
        sql = clone.execute("SELECT sql FROM sqlite_schema WHERE name='metadata'").fetchone()[0]
        # Same declared table/tokens, excessive physical SQL; accepted schema
        # comparison alone must not parse this unbounded representation.
        clone.execute("UPDATE sqlite_schema SET sql=? WHERE name='metadata'",
                      (sql.replace("CREATE", "CREATE" + " " * 70_000, 1),))
        clone.execute("PRAGMA writable_schema=OFF")
        clone.commit()
        data = clone.serialize()
    finally:
        clone.close()
    original = module._COPY_SQL_TOKEN
    tokenized = []

    def tokenize(sql):
        tokenized.append(True)
        return original.findall(sql)

    monkeypatch.setattr(module, "_COPY_SQL_TOKEN", SimpleNamespace(findall=tokenize))
    with pytest.raises(MemoryError, match="memory_copy_snapshot_invalid"):
        memory_scope_summaries(data, check_current=lambda: None)
    assert not tokenized


def test_cold_copy_requires_consent_before_owned_open_and_does_not_create_missing_database(store):
    value = review(store)
    path, limits = store.path, store.limits
    store.close()
    cold = MemoryStore(path, limits=limits)
    called = []

    def guard(stage, write):
        called.append((stage, write))
        if stage == "memory_copy_open_existing":
            assert cold._disk is None
            raise MemoryError("fixture_denied")

    try:
        with pytest.raises(MemoryScopeCopyError, match="fixture_denied"):
            cold.copy_scope(value, context(value, guard=guard))
        assert cold._disk is None and path.exists()
        receipt = cold.copy_scope(value, context(value))
        assert receipt.status == "completed" and len(cold.list_documents(DEST)) == 2
    finally:
        cold.close()
    missing = path.with_name("missing.sqlite3")
    absent = MemoryStore(missing, limits=limits)
    with pytest.raises(MemoryScopeCopyError):
        absent.copy_scope(value, context(value))
    assert not missing.exists() and absent._disk is None
    absent.close()


@pytest.mark.parametrize("sidecar", ["-wal", "-shm", "-journal"])
def test_cold_family_with_any_sidecar_fails_before_sqlite_open(store, sidecar):
    value, path = review(store), store.path
    store.close()
    extra = path.with_name(path.name + sidecar)
    extra.write_bytes(b"UNKNOWN FAMILY DO NOT RECOVER")
    extra.chmod(0o600)
    cold = MemoryStore(path)
    try:
        with pytest.raises(MemoryScopeCopyError, match="memory_copy_cold_image_unavailable"):
            cold.copy_scope(value, context(value))
        assert cold._disk is None and extra.read_bytes() == b"UNKNOWN FAMILY DO NOT RECOVER"
    finally:
        extra.unlink()  # Own synthetic sidecar.
        cold.close()


def test_cold_fstat_failure_closes_descriptor_never_loses_unassigned_owner(store, monkeypatch):
    import errno

    value, path = review(store), store.path
    store.close()
    cold = MemoryStore(path)
    real_open, real_fstat = os.open, os.fstat
    captured = {}

    def opened(file, flags, *args, **kwargs):
        fd = real_open(file, flags, *args, **kwargs)
        if os.fspath(file) == os.fspath(path.with_name(path.name + ".lock")):
            captured.update(fd=fd, failed=False)
        return fd

    def fstat(fd):
        if fd == captured.get("fd") and not captured["failed"]:
            captured["failed"] = True
            raise OSError(errno.EIO, "synthetic newly opened lock fstat failure")
        return real_fstat(fd)

    monkeypatch.setattr(os, "open", opened)
    monkeypatch.setattr(os, "fstat", fstat)
    try:
        with pytest.raises(MemoryScopeCopyError) as caught:
            cold.copy_scope(value, context(value))
        assert caught.value.owner is None and not caught.value.receipt.cleanup_pending
        assert cold._disk is None and cold._lock_fd is None
        with pytest.raises(OSError) as closed:
            real_fstat(captured["fd"])
        assert closed.value.errno == errno.EBADF
    finally:
        cold.close()


def test_v2_reopen_normal_operations_do_not_rewrite_copy_history(store):
    value = review(store)
    receipt = store.copy_scope(value, context(value))
    path, limits = store.path, store.limits
    store.close()
    reopened = MemoryStore(path, limits=limits)
    try:
        assert reopened.get(DEST, "first").document.revision == 1
        rows = tuple(
            tuple(row) for row in reopened._disk.execute("SELECT * FROM memory_scope_copies")
        )
        reopened.upsert(DEST, "first", "Later normal edit", source_uri="manual://later")
        assert (
            tuple(tuple(row) for row in reopened._disk.execute("SELECT * FROM memory_scope_copies"))
            == rows
        )
        observation = lookup_memory_scope_copy(
            image(reopened),
            receipt.operation_id,
            receipt.request_digest,
            check_current=lambda: None,
        )
        assert not observation.current_documents_match
    finally:
        reopened.close()


def test_corrupt_source_chunk_or_text_is_not_copied(store):
    store._disk.execute(
        "UPDATE chunks SET text='wrong chunk' WHERE chunk_id=(SELECT min(chunk_id) FROM chunks)"
    )
    before = sql_state(store)
    with pytest.raises(MemoryError, match="invalid_memory_database"):
        review(store)
    assert sql_state(store) == before


def test_current_document_limit_and_all_scope_capacity_are_preflighted(store):
    original = store.limits
    store.limits = replace(original, max_document_bytes=16)
    before = sql_state(store)
    with pytest.raises(MemoryError, match="memory_capacity_exceeded"):
        review(store)
    store.limits = replace(original, max_documents=3, max_documents_per_scope=3)
    with pytest.raises(MemoryError, match="memory_capacity_exceeded"):
        review(store)
    store.limits = original
    assert sql_state(store) == before


def test_prior_transaction_is_not_adopted_or_rolled_back(store):
    value = review(store)
    store._disk.execute("BEGIN IMMEDIATE")
    try:
        with pytest.raises(MemoryScopeCopyError, match="memory_copy_transaction_active"):
            store.copy_scope(value, context(value))
        assert store._disk.in_transaction
    finally:
        store._disk.execute("ROLLBACK")


def test_copy_context_timeout_and_reentrant_guard_latch_first_failure(store):
    value, now = review(store), [10]
    operation = context(value, clock=lambda: now[0], deadline=11)
    now[0] = 11
    with pytest.raises(MemoryScopeCopyError, match="memory_copy_deadline_exceeded") as caught:
        store.copy_scope(value, operation)
    assert caught.value.receipt.status == "cancelled"
    now[0] = 10
    with pytest.raises(MemoryScopeCopyError, match="memory_copy_context_replayed"):
        store.copy_scope(value, operation)


def test_new_copy_passes_independent_v2_inspector_and_restaged_history_is_not_local(store):
    from jarvis_core.recovery import RecoveryContext, RecoveryLimits, _transform_sqlite
    from jarvis_core.recovery_sqlite import inspect_sqlite_snapshot

    value = review(store)
    receipt = store.copy_scope(value, context(value))
    data = image(store)
    checked = inspect_sqlite_snapshot("memory", data, check_current=lambda: None)
    assert checked.user_version == 2 and checked.counts["documents"] == 4
    operation = RecoveryContext(
        uuid4().hex,
        "fixture",
        0,
        lambda stage, write: None,
        threading.Event(),
        time.monotonic() + 20,
    )
    operation.begin()
    restored = _transform_sqlite("memory", data, operation, RecoveryLimits(), restoring=True)
    inspect_sqlite_snapshot("memory", restored, check_current=lambda: None)
    with pytest.raises(MemoryError, match="memory_copy_receipt_historical"):
        lookup_memory_scope_copy(
            restored, receipt.operation_id, receipt.request_digest, check_current=lambda: None
        )
    assert lookup_memory_scope_copy(
        data, receipt.operation_id, receipt.request_digest, check_current=lambda: None
    ).current_documents_match


def test_full_v2_backup_restore_preserves_data_and_history_without_replay(store, tmp_path):
    from jarvis_core import profiles
    from jarvis_core.recovery import (
        JsonSource,
        NewTarget,
        RecoveryContext,
        SourceBundle,
        SqliteSource,
        canonical,
        create_backup,
        restore_new,
        verify_backup,
    )
    from jarvis_core.recovery_fs import FileIdentity

    value = review(store)
    receipt = store.copy_scope(value, context(value))
    root = store.path.parent
    original_source = store.get(SOURCE, "first")
    original_dest = store.get(DEST, "first")
    source = SourceBundle(
        "fixture",
        0,
        root,
        FileIdentity.of(root.stat()),
        (SqliteSource("memory", store._disk, lambda: None),),
        (JsonSource("agents", canonical(profiles._initial()), lambda: None),),
        ("state", "schedules", "skills", "telegram"),
    )

    def ctx():
        return RecoveryContext(
            uuid4().hex,
            "fixture",
            0,
            lambda stage, write: None,
            threading.Event(),
            time.monotonic() + 20,
        )

    parent = tmp_path.resolve()
    target = NewTarget(parent, FileIdentity.of(parent.stat()), "portable", (root,))
    create_backup(source, target, ctx())
    checked = verify_backup(parent / "portable", ctx())
    restore_new(
        parent / "portable",
        checked,
        NewTarget(parent, FileIdentity.of(parent.stat()), "restored", (root,)),
        ctx(),
    )
    with MemoryStore(parent / "restored/memory.sqlite3") as restored:
        assert restored.get(SOURCE, "first") == original_source
        assert restored.get(DEST, "first") == original_dest
        with pytest.raises(MemoryError, match="memory_copy_receipt_historical"):
            lookup_memory_scope_copy(
                image(restored),
                receipt.operation_id,
                receipt.request_digest,
                check_current=lambda: None,
            )
    assert (parent / "restored/restore_record.json").exists()
    assert not (parent / "restored/restore_admission.json").exists()


def test_history_quota_never_evicts_existing_record_to_make_copy_fit(store):
    caps = MemoryCopyLimits(max_receipts=1)
    value = review(store, copy_limits=caps)
    first = store.copy_scope(value, context(value), copy_limits=caps)
    before = sql_state(store)
    with pytest.raises(MemoryError, match="memory_copy_history_capacity"):
        review(store, destination=MemoryScope("jarvis", "third"), copy_limits=caps)
    assert sql_state(store) == before
    assert lookup_memory_scope_copy(
        image(store), first.operation_id, first.request_digest, check_current=lambda: None
    ).current_documents_match


def test_database_inode_replacement_is_not_followed(store, tmp_path):
    value = review(store)
    path = store.path
    old = path.with_name("saved.sqlite3")
    raw = path.read_bytes()
    path.rename(old)
    path.write_bytes(raw)
    path.chmod(0o600)
    try:
        with pytest.raises(MemoryScopeCopyError, match="memory_copy_database_changed"):
            store.copy_scope(value, context(value))
        assert store._copy_owner is None
    finally:
        path.unlink()
        old.rename(path)
    assert not store.list_documents(DEST)


def test_durable_inventory_excludes_session_scopes_without_labeling_them_as_ram(store):
    session = MemoryScope("jarvis", "legacy", "durable_session")
    store.upsert(session, "old", "Durable session linked data", source_uri="manual://fixture")
    summaries = memory_scope_summaries(image(store), check_current=lambda: None)
    assert all(row.scope.session_id is None for row in summaries)
    assert store.get(session, "old").text == "Durable session linked data"


def test_request_digest_is_host_digest_and_core_digest_is_separate(store):
    value = review(store)
    operation = context(value, request_digest="c" * 64)
    receipt = store.copy_scope(value, operation)
    assert receipt.request_digest == "c" * 64 and receipt.core_review_digest == value.digest
    assert receipt.request_digest != receipt.core_review_digest
    assert (
        lookup_memory_scope_copy(
            image(store), operation.operation_id, "c" * 64, check_current=lambda: None
        )
        is not None
    )
    with pytest.raises(MemoryError, match="memory_copy_operation_conflict"):
        lookup_memory_scope_copy(
            image(store), operation.operation_id, value.digest, check_current=lambda: None
        )
