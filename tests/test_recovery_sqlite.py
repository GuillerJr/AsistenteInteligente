import hashlib
import sqlite3

import pytest

from jarvis_core import memory, scheduler, state
from jarvis_core.recovery_sqlite import SnapshotError, SnapshotLimits, inspect_sqlite_snapshot


def image(role="state", *, fts=False, change=None):
    mod = {"state": state, "memory": memory, "schedules": scheduler}[role]
    c = sqlite3.connect(":memory:")
    c.executescript(mod._SCHEMA)
    c.execute(f"pragma application_id={mod._APPLICATION_ID}")
    c.execute(f"pragma user_version={mod._SCHEMA_VERSION}")
    if role == "memory":
        if fts:
            c.execute(
                "CREATE VIRTUAL TABLE chunk_fts USING "
                "fts5(text, tokenize='unicode61 remove_diacritics 2')"
            )
        c.execute(
            "insert into metadata values('search_mode',?)",
            ("fts5_lexical" if fts else "lexical_scan",),
        )
    if change:
        change(c)
    c.commit()
    data = c.serialize()
    c.close()
    return data


@pytest.mark.parametrize("role", ["state", "memory", "schedules"])
def test_own_real_sqlite_images(role):
    data = image(role)
    result = inspect_sqlite_snapshot(role, data, check_current=lambda: None)
    assert result.role == role
    assert result.sha256 == hashlib.sha256(data).hexdigest()
    assert result.integrity == "ok"
    assert result.schema_version in (1, 2)
    assert result.counts


def test_fts_own_schema_and_content():
    data = image("memory", fts=True)
    result = inspect_sqlite_snapshot("memory", data, check_current=lambda: None)
    assert result.counts["metadata"] == 1


@pytest.mark.parametrize(
    "change,code",
    [
        (lambda c: c.execute("pragma application_id=123"), "snapshot_schema_unsupported"),
        (lambda c: c.execute("pragma user_version=900"), "snapshot_schema_unsupported"),
        (lambda c: c.execute("create table extra(x)"), "snapshot_schema_unsupported"),
        (
            lambda c: c.execute("create view malicious as select load_extension('x')"),
            "snapshot_schema_unsupported",
        ),
        (
            lambda c: c.execute(
                "create trigger malicious after insert on sessions begin "
                "select load_extension('x'); end"
            ),
            "snapshot_schema_unsupported",
        ),
    ],
)
def test_rejects_schema_extensions_without_executing_them(change, code):
    with pytest.raises(SnapshotError, match=code):
        inspect_sqlite_snapshot("state", image(change=change), check_current=lambda: None)


@pytest.mark.parametrize("data", [b"", b"not a database" * 100, b"SQLite format 3\0" + b"\0" * 600])
def test_corrupt_or_not_sqlite(data):
    with pytest.raises(SnapshotError):
        inspect_sqlite_snapshot("state", data, check_current=lambda: None)


def test_does_not_accept_wal_image_as_standalone():
    data = bytearray(image())
    data[18:20] = b"\x02\x02"
    with pytest.raises(SnapshotError, match="snapshot_image_not_standalone"):
        inspect_sqlite_snapshot("state", bytes(data), check_current=lambda: None)


def test_foreign_key_violation_fails_even_with_integrity_ok():
    # Explicit SQL fixture, never loading an owner's store.
    def insert(c):
        c.execute(
            "insert into messages values(?,?,?,?,?,?,?)",
            ("a" * 32, "b" * 32, 1, "user", "fixture", None, 1),
        )

    with pytest.raises(SnapshotError, match="snapshot_foreign_key_failed"):
        inspect_sqlite_snapshot("state", image(change=insert), check_current=lambda: None)


def test_source_guard_failure_propagates_unchanged():
    failure = RuntimeError("synthetic_guard_failure")

    def guard():
        raise failure

    with pytest.raises(RuntimeError) as caught:
        inspect_sqlite_snapshot("state", image(), check_current=guard)
    assert caught.value is failure


def test_progress_interrupt_propagates_guard_failure():
    def populate(c):
        c.executemany(
            "insert into sessions values(?,?,0,1,NULL,'jarvis',NULL)",
            ((f"{i:032x}", "synthetic") for i in range(2500)),
        )

    data = image(change=populate)
    calls = 0
    failure = RuntimeError("cancelled_latched")

    def guard():
        nonlocal calls
        calls += 1
        if calls == 4:
            raise failure

    with pytest.raises(RuntimeError) as caught:
        inspect_sqlite_snapshot("state", data, check_current=guard)
    assert caught.value is failure


def test_budget_limits_are_failures_not_partial_ok():
    with pytest.raises(SnapshotError, match="snapshot_size_invalid"):
        inspect_sqlite_snapshot(
            "state", image(), check_current=lambda: None, limits=SnapshotLimits(max_bytes=512)
        )
    with pytest.raises(SnapshotError, match="snapshot_work_limit"):
        inspect_sqlite_snapshot(
            "state",
            image(
                change=lambda c: c.executemany(
                    "insert into sessions values(?,?,0,1,NULL,'jarvis',NULL)",
                    ((f"{i:032x}", "fixture") for i in range(1000)),
                )
            ),
            check_current=lambda: None,
            limits=SnapshotLimits(max_vm_steps=1),
        )


def test_nonterminal_is_reported_and_original_bytes_unchanged():
    sid, run = "a" * 32, "b" * 32

    def insert(c):
        c.execute(
            "insert into sessions values(?,?,0,1,?,'jarvis',NULL)",
            (sid, "synthetic", "opaque-provider-thread"),
        )
        c.execute("insert into runs values(?,?,'running','pending',NULL,1,1)", (run, sid))

    data = image(change=insert)
    inspection = inspect_sqlite_snapshot("state", data, check_current=lambda: None)
    assert inspection.counts["unsettled_runs"] == 1
    assert inspection.session_ids == (sid,)
    c = sqlite3.connect(":memory:")
    c.deserialize(data)
    assert c.execute("select status from runs").fetchone() == ("running",)
    assert c.execute("select provider_thread_id from sessions").fetchone() == (
        "opaque-provider-thread",
    )
    c.close()


def test_parent_deleted_is_provenance_not_a_required_foreign_key():
    sid = "a" * 32
    data = image(
        change=lambda c: c.execute(
            "insert into sessions values(?,?,0,1,NULL,'jarvis',?)", (sid, "fixture", "b" * 32)
        )
    )
    inspection = inspect_sqlite_snapshot("state", data, check_current=lambda: None)
    assert inspection.session_agents == ((sid, "jarvis"),)


def test_empty_memory_scope_does_not_require_a_current_agent():
    def insert(c):
        c.execute("insert into scopes values(?,1)", ('["retired","workspace",null]',))

    inspection = inspect_sqlite_snapshot(
        "memory", image("memory", change=insert), check_current=lambda: None
    )
    assert inspection.counts["scopes"] == 1
    assert inspection.agent_ids == inspection.memory_scopes == ()


def test_terminal_occurrence_retired_agent_is_preserved_as_history():
    # 13 actual columns; historical occurrence need not retain its definition.
    def insert(c):
        c.execute(
            "insert into occurrences values(?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                "a" * 32,
                "b" * 32,
                1,
                "fixture",
                "fixture",
                "retired",
                "workspace",
                1,
                1,
                "interrupted",
                "unknown",
                None,
                "fixture_unknown",
            ),
        )

    inspection = inspect_sqlite_snapshot(
        "schedules", image("schedules", change=insert), check_current=lambda: None
    )
    assert inspection.agent_ids == ()
    assert inspection.counts["occurrences"] == 1


def test_own_real_fts_memory_image_and_corrupt_chunk(tmp_path):
    root = tmp_path / "own-memory"
    root.mkdir(mode=0o700)
    store = memory.MemoryStore(root / "memory.sqlite3")
    scope = memory.MemoryScope("jarvis", "fixture_workspace")
    try:
        store.upsert(scope, "fixture", "TOPACIO 4826", source_uri="jarvis://fixture")
        data = store._disk.serialize()
        inspection = inspect_sqlite_snapshot("memory", data, check_current=lambda: None)
        assert inspection.memory_scopes == (("jarvis", "fixture_workspace", None),)
        c = sqlite3.connect(":memory:")
        c.deserialize(data)
        c.execute("update chunks set start_byte=1")
        c.commit()
        corrupt = c.serialize()
        c.close()
        with pytest.raises(SnapshotError, match="snapshot_memory_content_invalid"):
            inspect_sqlite_snapshot("memory", corrupt, check_current=lambda: None)
    finally:
        store.close()


def test_readonly_inspector_rejects_modified_sql_literal():
    c = sqlite3.connect(":memory:")
    c.executescript(state._SCHEMA.replace("DEFAULT 'jarvis'", "DEFAULT 'jar vis'"))
    c.execute(f"pragma application_id={state._APPLICATION_ID}")
    c.execute(f"pragma user_version={state._SCHEMA_VERSION}")
    raw = c.serialize()
    c.close()
    with pytest.raises(SnapshotError, match="snapshot_schema_unsupported"):
        inspect_sqlite_snapshot("state", raw, check_current=lambda: None)


@pytest.mark.parametrize("where", ["receipt", "payload"])
def test_overflow_json_number_is_not_a_supported_finite_value(where):
    def populate(c):
        sid, run = "a" * 32, "b" * 32
        c.execute("insert into sessions values(?,?,0,1,NULL,'jarvis',NULL)", (sid, "fixture"))
        c.execute(
            "insert into runs values(?,?,'completed','known',?,1,1)",
            (run, sid, '{"nested":[1e400]}' if where == "receipt" else "{}"),
        )
        c.execute(
            "insert into events values(?,?,1,'fixture',?,1)",
            ("c" * 32, run, '{"nested":[1e400]}' if where == "payload" else "{}"),
        )

    with pytest.raises(SnapshotError, match="snapshot_data_invalid"):
        inspect_sqlite_snapshot("state", image(change=populate), check_current=lambda: None)
