import json
import os
import sqlite3
import threading
from dataclasses import replace

import pytest

from jarvis_core.doctor import (
    DoctorContext,
    DoctorError,
    DoctorInventory,
    DoctorLimits,
    inspect_report,
)
from jarvis_core.profiles import _initial as initial_agents
from jarvis_core.telegram import _journal_initial


@pytest.fixture
def root(tmp_path):
    path = tmp_path.resolve() / "profile"
    path.mkdir(mode=0o700)
    return path


def inventory(root, *, roles=("agents",), **kwargs):
    info = root.stat() if root.exists() else None
    return DoctorInventory(
        "synthetic_profile",
        7,
        root,
        (info.st_dev, info.st_ino) if info else None,
        "offline_quiescent",
        roles=roles,
        **kwargs,
    )


def context(guard=lambda stage, *, write: None, event=None, **kwargs):
    return DoctorContext(guard, event or threading.Event(), **kwargs)


def write_json(root, role="agents", raw=None):
    if role in ("agents", "skills"):
        parent = root / "profiles"
        name = role + ".json"
    else:
        parent, name = root / "telegram", "telegram.json"
    parent.mkdir(mode=0o700, exist_ok=True)
    if raw is None:
        value = (
            initial_agents()
            if role == "agents"
            else (
                {"version": 1, "skills": {}} if role == "skills" else _journal_initial()
            )
        )
        if role == "agents":
            value["agents"]["jarvis"]["instructions"] = "PRIVATE CANARY CHAT BODY"
        raw = json.dumps(value).encode()
    path = parent / name
    path.write_bytes(raw)
    path.chmod(0o600)
    return path


def fingerprint(root):
    result = {}
    if not root.exists():
        return result
    for path in [root, *sorted(root.rglob("*"))]:
        info = path.lstat()
        result[str(path.relative_to(root))] = (
            info.st_dev,
            info.st_ino,
            info.st_mode,
            info.st_size,
            info.st_mtime_ns,
            path.read_bytes() if path.is_file() and not path.is_symlink() else None,
            os.readlink(path) if path.is_symlink() else None,
        )
    return result


def inspect(root, **kwargs):
    return inspect_report(inventory(root, **kwargs), context())


def test_absent_profile_and_uninitialized_files_are_never_created(root):
    absent = root / "does_not_exist"
    before = fingerprint(root)
    report = inspect(absent, roles=("state", "agents"))
    assert report.status == "absent"
    assert [row.status for row in report.checks] == ["absent", "absent"]
    assert not absent.exists() and fingerprint(root) == before
    file = root / "jarvis.sqlite3"
    file.touch(mode=0o600)
    before = fingerprint(root)
    report = inspect(root, roles=("state",))
    assert report.status == "uninitialized"
    assert report.checks[0].status == "uninitialized"
    assert fingerprint(root) == before


def test_valid_json_report_exposes_only_metadata_and_does_not_initialize_stores(
    root, monkeypatch
):
    for role in ("agents", "skills", "telegram"):
        write_json(root, role)
    (root / "auth.json").write_text("PRIVATE AUTH CANARY")
    (root / "configuration.json").write_text("PRIVATE MCP ARGV CANARY")
    from jarvis_core import (
        memory,
        private_json,
        profiles,
        scheduler,
        skills,
        state,
        telegram,
    )

    def forbidden(*args, **kwargs):
        raise AssertionError("mutating_constructor_or_network")

    for cls in (
        state.StateStore,
        memory.MemoryStore,
        scheduler.ScheduleStore,
        profiles.ProfilesStore,
        skills.SkillsStore,
        telegram.TelegramJournal,
        private_json.PrivateJsonDocument,
    ):
        monkeypatch.setattr(cls, "__init__", forbidden)
    import socket

    monkeypatch.setattr(socket, "create_connection", forbidden)
    opened = []
    real_open = os.open

    def track(path, *args, **kwargs):
        opened.append(str(path))
        return real_open(path, *args, **kwargs)

    monkeypatch.setattr(os, "open", track)
    before = fingerprint(root)
    inv = inventory(
        root,
        roles=("agents", "skills", "telegram", "configuration_portable"),
        configuration_metadata={
            "version": 1,
            "workspace_selected": True,
            "binding_count": 3,
            "mcp_server_count": 2,
        },
    )
    report = inspect_report(inv, context())
    assert report.status == "ok" and report.supported_repair_actions == ()
    encoded = json.dumps(report.to_dict())
    for secret in ("PRIVATE", "CANARY", "auth.json", "configuration.json", str(root)):
        assert secret not in encoded
    assert not any(
        "auth.json" in path or "configuration.json" in path for path in opened
    )
    assert fingerprint(root) == before
    assert not list(root.rglob("*.lock"))


@pytest.mark.parametrize(
    "raw",
    [
        b'{"version":1,"version":1,"agents":{}}',
        b'{"version":NaN}',
        b"\xff",
        b'{"secret":"PRIVATE JSON CANARY"}',
        b"[" * 2000 + b"]" * 2000,
    ],
)
def test_malformed_json_is_bounded_and_sanitized_without_changes(root, raw):
    write_json(root, raw=raw)
    before = fingerprint(root)
    report = inspect(root)
    assert report.status == "corrupt"
    assert report.checks[0].code == "doctor_json_invalid"
    assert "PRIVATE" not in json.dumps(report.to_dict())
    assert fingerprint(root) == before


@pytest.mark.parametrize(
    "kind", ["symlink", "hardlink", "directory_symlink", "file_mode", "directory_mode"]
)
def test_unsafe_paths_or_permissions_are_never_followed_or_repaired(root, kind):
    path = write_json(root)
    outside = root.parent / "outside"
    outside.mkdir(mode=0o700)
    target = outside / "agents.json"
    target.write_text("PRIVATE OUTSIDE")
    target.chmod(0o600)
    if kind == "symlink":
        path.unlink()
        path.symlink_to(target)
    elif kind == "hardlink":
        path.unlink()
        os.link(target, path)
    elif kind == "directory_symlink":
        path.unlink()
        path.parent.rmdir()
        path.parent.symlink_to(outside, target_is_directory=True)
    elif kind == "file_mode":
        path.chmod(0o644)
    else:
        path.parent.chmod(0o755)
    before = fingerprint(root)
    target_before = target.read_bytes()
    report = inspect(root)
    assert report.status == "unsafe"
    assert fingerprint(root) == before and target.read_bytes() == target_before


def test_symlink_ancestor_or_wrong_profile_identity_is_unknown_or_unsafe(root):
    write_json(root)
    alias = root.parent / "alias"
    alias.symlink_to(root, target_is_directory=True)
    report = inspect_report(replace(inventory(root), root=alias), context())
    assert report.status == "unsafe"
    report = inspect_report(
        replace(inventory(root), profile_identity=(1, 2)), context()
    )
    assert report.status == "unknown" and "doctor_profile_changed" in report.blockers


@pytest.mark.parametrize("suffix", ["-wal", "-shm", "-journal"])
def test_sidecars_block_before_opening_sqlite_even_when_host_claims_quiescent(
    root, suffix
):
    path = root / "jarvis.sqlite3"
    path.write_bytes(b"PRIVATE DB CANARY")
    path.chmod(0o600)
    sidecar = path.with_name(path.name + suffix)
    sidecar.touch(mode=0o600)
    before = fingerprint(root)
    report = inspect_report(
        inventory(root, roles=("state",)),
        context(),
        sqlite_inspector=lambda *args, **kwargs: pytest.fail("inspected live family"),
    )
    assert report.status == "blocked"
    assert report.checks[0].code == "doctor_needs_quiescent_snapshot"
    assert fingerprint(root) == before


def test_live_wal_with_uncheckpointed_data_is_not_ignored_or_modified(root):
    path = root / "jarvis.sqlite3"
    connection = sqlite3.connect(path)
    connection.execute("PRAGMA journal_mode=WAL")
    connection.execute("CREATE TABLE marker(value TEXT)")
    connection.execute("INSERT INTO marker VALUES('PRIVATE WAL CANARY')")
    connection.commit()
    path.chmod(0o600)
    for suffix in ("-wal", "-shm"):
        path.with_name(path.name + suffix).chmod(0o600)
    try:
        before = fingerprint(root)
        report = inspect(root, roles=("state",))
        assert report.status == "blocked"
        assert fingerprint(root) == before
    finally:
        connection.close()


def test_busy_or_unverified_inventory_reads_metadata_without_content(root):
    write_json(root)
    for inv in (
        replace(inventory(root), coherence="unverified"),
        inventory(root, blockers=("profile_busy", "PRIVATE_CANARY")),
    ):
        ctx = context()
        report = inspect_report(inv, ctx)
        assert report.status == "blocked" and ctx.bytes_read == 0
        assert "PRIVATE" not in json.dumps(report.to_dict())
        assert report.checks[0].code == "doctor_needs_quiescent_snapshot"


def test_cancellation_is_irreversible_and_no_late_inspection_happens(root):
    write_json(root)
    event = threading.Event()
    event.set()
    before = fingerprint(root)
    ctx = context(event=event)
    report = inspect_report(inventory(root), ctx)
    assert report.status == "cancelled" and "doctor_cancelled" in report.blockers
    event.clear()  # Even a faulty host cannot revive this operation's latched result.
    report = inspect_report(inventory(root), ctx)
    assert report.status == "cancelled" and ctx.bytes_read == 0
    assert fingerprint(root) == before


def test_cancel_and_deadline_during_validation_settle_without_publishing_ok(root):
    write_json(root)
    before = fingerprint(root)
    event = threading.Event()

    def guard(stage, *, write):
        assert write is False
        if stage == "json_validated":
            event.set()

    report = inspect_report(inventory(root), context(guard, event))
    assert report.status == "cancelled"
    now = [0.0]

    def expire(stage, *, write):
        if stage == "resource_metadata":
            now[0] = 20.0

    report = inspect_report(inventory(root), context(expire, clock=lambda: now[0]))
    assert report.status == "unknown" and "doctor_deadline" in report.blockers
    assert fingerprint(root) == before


def test_barrier_or_source_replacement_invalidates_the_report_without_leaking_errors(
    root,
):
    path = write_json(root)

    def changed(stage, *, write):
        if stage == "json_validated":
            raise ValueError("PRIVATE BARRIER DIAGNOSTIC")

    report = inspect_report(inventory(root), context(changed))
    assert report.status == "unknown" and "doctor_barrier_changed" in report.blockers
    assert "PRIVATE" not in json.dumps(report.to_dict())
    replaced = [False]

    def swap(stage, *, write):
        if stage == "resource_revalidated" and not replaced[0]:
            replaced[0] = True
            candidate = path.with_name("candidate")
            candidate.write_bytes(path.read_bytes())
            candidate.chmod(0o600)
            os.replace(candidate, path)

    report = inspect_report(inventory(root), context(swap))
    assert report.status == "unknown" and "doctor_resource_changed" in report.blockers
    assert all(check.status != "ok" for check in report.checks)


def test_profile_retarget_after_read_is_not_mistaken_for_the_original_profile(root):
    write_json(root)
    inv = inventory(root)

    def swap(stage, *, write):
        if stage == "resource_revalidated":
            root.rename(root.with_name("retained_old_profile"))
            root.mkdir(mode=0o700)
            write_json(root, raw=b"PRIVATE NEW PROFILE")

    report = inspect_report(inv, context(swap))
    assert report.status == "unknown" and "doctor_profile_changed" in report.blockers
    assert all(check.status == "unknown" for check in report.checks)


def test_resource_and_total_byte_limits_fail_before_reading_any_body(root):
    write_json(root)
    for limits in (DoctorLimits(json_bytes=100), DoctorLimits(total_bytes=100)):
        ctx = context(limits=limits)
        report = inspect_report(inventory(root), ctx)
        assert report.status == "blocked" and ctx.bytes_read == 0


def test_schema_header_old_format_never_migrates_or_calls_a_store(root):
    path = root / "jarvis.sqlite3"
    db = sqlite3.connect(path)
    db.execute("CREATE TABLE old(value TEXT)")
    db.execute(f"PRAGMA application_id={0x4A525653}")
    db.execute("PRAGMA user_version=1")
    db.commit()
    db.close()
    path.chmod(0o600)
    before = fingerprint(root)
    report = inspect_report(
        inventory(root, roles=("state",)),
        context(),
        sqlite_inspector=lambda *args, **kwargs: pytest.fail(
            "migrated/inspected unsupported schema"
        ),
    )
    assert (
        report.status == "partial"
        and report.checks[0].code == "doctor_schema_unsupported"
    )
    assert fingerprint(root) == before


def test_configuration_is_only_frozen_safe_metadata_and_restore_absence_is_checked(
    root,
):
    metadata = {
        "version": 1,
        "workspace_selected": False,
        "binding_count": 1,
        "mcp_server_count": 0,
    }
    inv = inventory(
        root,
        roles=("configuration_portable", "restore_record"),
        configuration_metadata=metadata,
    )
    metadata["binding_count"] = 999
    report = inspect_report(inv, context())
    assert report.status == "partial"
    assert dict(report.checks[0].counts)["bindings_excluded"] == 1
    assert report.checks[1].status == "absent"


@pytest.mark.parametrize(
    "changes",
    [
        {"profile_id": "../private"},
        {"generation": True},
        {"generation": 1.0},
        {"generation": 1 << 52},
        {"roles": ("auth.json",)},
        {"roles": (["agents"],)},
        {"blockers": (["secret"],)},
    ],
)
def test_inventory_is_host_typed_and_rejects_unbounded_or_arbitrary_paths(
    root, changes
):
    with pytest.raises(DoctorError):
        replace(inventory(root), **changes)


def sqlite_file(root, role="state", *, change=None, fts=False):
    # Own, actual SQLite fixture serialized to a standalone profile file. The
    # diagnostic never uses these constructors or opens a source SQLite VFS.
    from jarvis_core import memory, scheduler, state

    module = {"state": state, "memory": memory, "schedules": scheduler}[role]
    connection = sqlite3.connect(":memory:")
    try:
        connection.executescript(module._SCHEMA)
        connection.execute(f"PRAGMA application_id={module._APPLICATION_ID}")
        connection.execute(f"PRAGMA user_version={module._SCHEMA_VERSION}")
        if role == "memory":
            if fts:
                connection.execute(
                    "CREATE VIRTUAL TABLE chunk_fts USING "
                    "fts5(text, tokenize='unicode61 remove_diacritics 2')"
                )
            connection.execute(
                "INSERT INTO metadata VALUES('search_mode',?)",
                ("fts5_lexical" if fts else "lexical_scan",),
            )
        if change:
            change(connection)
        connection.commit()
        path = (
            root
            / {
                "state": "jarvis.sqlite3",
                "memory": "memory.sqlite3",
                "schedules": "schedules.sqlite3",
            }[role]
        )
        path.write_bytes(connection.serialize())
        path.chmod(0o600)
        return path
    finally:
        connection.close()


def session(connection, *, agent="jarvis", sid="a" * 32, parent=None):
    connection.execute(
        "INSERT INTO sessions VALUES(?,?,0,1,?,?,?)",
        (sid, "PRIVATE CANARY TITLE", "PRIVATE REMOTE THREAD", agent, parent),
    )


@pytest.mark.parametrize("role", ["state", "memory", "schedules"])
def test_actual_sqlite_schema_and_integrity_without_vfs_or_store_constructors(
    root, monkeypatch, role
):
    from jarvis_core import memory, scheduler, state

    sqlite_file(root, role)
    write_json(root)
    before = fingerprint(root)

    def forbidden(*args, **kwargs):
        raise AssertionError("source_vfs_or_store_constructor")

    for cls in (state.StateStore, memory.MemoryStore, scheduler.ScheduleStore):
        monkeypatch.setattr(cls, "__init__", forbidden)
    real_connect = sqlite3.connect
    opened = []

    def only_memory(database, *args, **kwargs):
        opened.append(database)
        assert database == ":memory:"
        return real_connect(database, *args, **kwargs)

    monkeypatch.setattr(sqlite3, "connect", only_memory)
    report = inspect(root, roles=(role, "agents"))
    assert report.status == "ok"
    check = next(check for check in report.checks if check.role == role)
    assert check.integrity == "ok" and check.application_id > 0
    assert check.schema_version == (2 if role in {"state", "schedules"} else 1)
    assert opened and set(opened) == {":memory:"}
    assert fingerprint(root) == before


@pytest.mark.parametrize(
    "change,status,code",
    [
        (
            lambda c: c.execute("CREATE TABLE unexpected(x)"),
            "unsafe",
            "doctor_schema_invalid",
        ),
        (
            lambda c: c.execute(
                "CREATE VIEW malicious AS SELECT load_extension('PRIVATE')"
            ),
            "unsafe",
            "doctor_schema_invalid",
        ),
        (
            lambda c: c.execute(
                "CREATE TRIGGER malicious AFTER INSERT ON sessions "
                "BEGIN SELECT load_extension('PRIVATE'); END"
            ),
            "unsafe",
            "doctor_schema_invalid",
        ),
        (
            lambda c: c.execute("PRAGMA application_id=123"),
            "unsafe",
            "doctor_sqlite_identity_invalid",
        ),
        (
            lambda c: c.execute("PRAGMA user_version=900"),
            "partial",
            "doctor_schema_unsupported",
        ),
        (
            lambda c: c.execute(
                "INSERT INTO messages VALUES(?,?,?,?,?,?,?)",
                ("c" * 32, "b" * 32, 1, "user", "PRIVATE BODY", None, 1),
            ),
            "corrupt",
            "doctor_sqlite_corrupt",
        ),
    ],
)
def test_sqlite_structural_and_reference_failures_are_sanitized_no_repair(
    root, change, status, code
):
    sqlite_file(root, change=change)
    before = fingerprint(root)
    report = inspect(root, roles=("state",))
    assert report.status == status
    assert report.checks[0].code == code
    assert "PRIVATE" not in json.dumps(report.to_dict())
    assert fingerprint(root) == before


def test_pending_run_is_reported_without_recovery_replay_or_provider_use(root):
    def pending(connection):
        session(connection)
        connection.execute(
            "INSERT INTO runs VALUES(?,?,'running','pending',NULL,1,1)",
            ("b" * 32, "a" * 32),
        )

    sqlite_file(root, change=pending)
    write_json(root)
    before = fingerprint(root)
    report = inspect(root, roles=("agents", "state"))
    assert report.status == "blocked"
    check = next(row for row in report.checks if row.role == "state")
    assert check.code == "doctor_unsettled_state"
    assert dict(check.counts)["unsettled_runs"] == 1
    assert "PRIVATE" not in json.dumps(report.to_dict())
    assert fingerprint(root) == before


def populate_memory(connection, *, fts=False, agent="jarvis", invalid_digest=False):
    import hashlib

    text = "PRIVATE MEMORY CANARY"
    scope = json.dumps(
        [agent, "workspace", "deleted_provenance_session"], separators=(",", ":")
    )
    connection.execute("INSERT INTO scopes VALUES(?,0)", (scope,))
    connection.execute(
        "INSERT INTO documents VALUES(?,?,?,?,?,1,?,?,?,?,1,1)",
        (
            "d" * 32,
            scope,
            "test",
            "PRIVATE TITLE",
            "file:///PRIVATE/DO-NOT-OPEN",
            text,
            "0" * 64 if invalid_digest else hashlib.sha256(text.encode()).hexdigest(),
            len(text.encode()),
            1,
        ),
    )
    connection.execute(
        "INSERT INTO chunks VALUES(1,?,0,0,?,?)", ("d" * 32, len(text.encode()), text)
    )
    if fts:
        connection.execute("INSERT INTO chunk_fts(rowid,text) VALUES(1,?)", (text,))


@pytest.mark.parametrize("fts", [False, True])
def test_actual_memory_contents_checked_without_emitting_or_opening_source_uri(
    root, fts
):
    sqlite_file(root, "memory", fts=fts, change=lambda c: populate_memory(c, fts=fts))
    write_json(root)
    before = fingerprint(root)
    report = inspect(root, roles=("memory", "agents"))
    assert report.status == "ok"
    check = next(row for row in report.checks if row.role == "memory")
    assert dict(check.counts)["documents"] == 1 and check.integrity == "ok"
    encoded = json.dumps(report.to_dict())
    assert "PRIVATE" not in encoded and "deleted_provenance_session" not in encoded
    assert fingerprint(root) == before


def test_memory_hash_corruption_and_absent_agent_are_separate_failures(root):
    sqlite_file(
        root, "memory", change=lambda c: populate_memory(c, invalid_digest=True)
    )
    write_json(root)
    before = fingerprint(root)
    report = inspect(root, roles=("memory", "agents"))
    assert (
        report.status == "corrupt" and report.checks[0].code == "doctor_sqlite_corrupt"
    )
    assert fingerprint(root) == before
    sqlite_file(root, "memory", change=lambda c: populate_memory(c, agent="missing"))
    before = fingerprint(root)
    report = inspect(root, roles=("memory", "agents"))
    assert (
        report.status == "corrupt"
        and report.checks[-1].code == "doctor_references_invalid"
    )
    assert fingerprint(root) == before


def test_sqlite_guard_cancels_actual_inspection_and_keeps_source_identical(root):
    sqlite_file(
        root,
        change=lambda c: c.executemany(
            "INSERT INTO sessions VALUES(?,?,0,1,NULL,'jarvis',NULL)",
            ((f"{i:032x}", "PRIVATE TITLE") for i in range(2500)),
        ),
    )
    before = fingerprint(root)
    cancellation = threading.Event()
    calls = 0

    def guard(stage, *, write):
        nonlocal calls
        assert write is False
        if stage == "sqlite_validation":
            calls += 1
            if calls == 4:
                cancellation.set()

    report = inspect_report(
        inventory(root, roles=("state",)), context(guard, cancellation)
    )
    assert calls == 4 and report.status == "cancelled"
    assert "doctor_cancelled" in report.blockers
    assert fingerprint(root) == before


def telegram_route(root, *, agent="jarvis", sid="a" * 32):
    value = _journal_initial()
    value["routes"]["123456"] = {
        "chat_id": 123456,
        "agent_id": agent,
        "session_id": sid,
        "revision": 1,
        "enabled": True,
    }
    return write_json(root, "telegram", json.dumps(value).encode())


@pytest.mark.parametrize(
    "actual_agent,sid,with_state,expected",
    [
        ("jarvis", "a" * 32, True, "ok"),
        ("jarvis", "b" * 32, True, "corrupt"),
        ("other", "a" * 32, True, "corrupt"),
        ("jarvis", "a" * 32, False, "partial"),
    ],
)
def test_cross_store_route_binding_requires_exact_session_and_agent(
    root, actual_agent, sid, with_state, expected
):
    write_json(root)
    if with_state:
        sqlite_file(
            root, change=lambda c: session(c, agent=actual_agent, parent="c" * 32)
        )
    telegram_route(root, sid=sid)
    before = fingerprint(root)
    report = inspect(root, roles=("agents", "state", "telegram"))
    assert report.status == expected
    assert "123456" not in json.dumps(report.to_dict())
    assert fingerprint(root) == before


def test_unselected_reference_source_is_unknown_not_implicit_default_agent(root):
    sqlite_file(root, change=lambda c: session(c))
    report = inspect(root, roles=("state",))
    assert report.status == "partial"
    assert report.checks[-1].code == "doctor_reference_sources_missing"


def test_skill_source_is_metadata_and_never_followed(root, monkeypatch):
    import hashlib

    text = ('---\nname: Prueba\ndescription: Texto propio\nresources: ["README.md"]'
            '\n---\nPRIVATE INSTRUCTIONS CANARY')
    row = {
        "agent_id": "jarvis",
        "text": text,
        "sha256": hashlib.sha256(text.encode()).hexdigest(),
        "enabled": True,
        "revision": 1,
        "source_path": "/PRIVATE/DO-NOT-OPEN",
        "source_identity": [1, 2],
        "source_file_identity": [3, 4],
        "created_ns": 0,
        "updated_ns": 0,
        "provenance": "imported_snapshot",
    }
    write_json(
        root,
        "skills",
        json.dumps({"version": 1, "skills": {"skill_" + "a" * 32: row}}).encode(),
    )
    write_json(root)
    real_open = os.open

    def safe_open(path, *args, **kwargs):
        assert "PRIVATE" not in str(path)
        return real_open(path, *args, **kwargs)

    monkeypatch.setattr(os, "open", safe_open)
    before = fingerprint(root)
    report = inspect(root, roles=("agents", "skills"))
    assert report.status == "ok"
    assert "PRIVATE" not in json.dumps(report.to_dict())
    assert fingerprint(root) == before


def test_renamed_intermediate_directory_invalidates_pinned_old_source(root):
    write_json(root)
    changed = False

    def guard(stage, *, write):
        nonlocal changed
        if stage == "json_validated" and not changed:
            changed = True
            (root / "profiles").rename(root / "profiles_old")
            write_json(root)

    report = inspect_report(inventory(root), context(guard))
    assert changed and report.status == "unknown"
    assert "doctor_resource_changed" in report.blockers
    assert all(row.integrity == "not_checked" for row in report.checks)


def test_unsafe_retarget_of_root_invalidates_previous_success_counts(root):
    write_json(root)
    changed = False

    def guard(stage, *, write):
        nonlocal changed
        if stage == "resource_revalidated" and not changed:
            changed = True
            root.rename(root.with_name("old-profile"))
            root.mkdir(mode=0o755)

    report = inspect_report(inventory(root), context(guard))
    assert changed and report.status == "unknown"
    assert "doctor_profile_changed" in report.blockers
    assert all(not row.counts for row in report.checks)


def test_unidentified_sqlite_does_not_become_a_jarvis_uninitialized_store(root):
    sqlite_file(
        root,
        change=lambda c: (
            c.execute("PRAGMA application_id=0"),
            c.execute("PRAGMA user_version=0"),
        ),
    )
    before = fingerprint(root)
    report = inspect(root, roles=("state",))
    assert report.checks[0].status == "unknown"
    assert report.checks[0].code == "doctor_sqlite_identity_missing"
    assert fingerprint(root) == before


def test_unsettled_scheduler_and_telegram_do_not_recover_or_dispatch(root):
    def claimed(c):
        c.execute(
            "INSERT INTO occurrences VALUES(?,?,1,?,?,?, ?,1,1,'claimed','pending',NULL,NULL)",
            (
                "c" * 32,
                "d" * 32,
                "PRIVATE TITLE",
                "PRIVATE PROMPT",
                "deleted_agent",
                "workspace",
            ),
        )

    sqlite_file(root, "schedules", change=claimed)
    journal = _journal_initial()
    journal["offset"] = 2
    journal["events"]["0:1"] = {
        "update_id": 1,
        "epoch": 0,
        "chat_id": 123456,
        "message_id": 7,
        "agent_id": "deleted_agent",
        "session_id": "a" * 32,
        "route_revision": 1,
        "status": "dispatching",
        "outcome": "pending",
        "run_id": "b" * 32,
        "error_code": None,
        "outgoing_message_id": None,
        "reply_sha256": None,
        "reply_truncated": False,
    }
    write_json(root, "telegram", json.dumps(journal).encode())
    write_json(root)
    before = fingerprint(root)
    report = inspect(root, roles=("schedules", "telegram", "agents"))
    assert report.status == "blocked"
    assert [row.code for row in report.checks[:2]] == ["doctor_unsettled_state"] * 2
    assert fingerprint(root) == before
    encoded = json.dumps(report.to_dict())
    assert all(
        text not in encoded
        for text in ("PRIVATE", "123456", "deleted_agent", "workspace")
    )


def test_empty_memory_tombstones_and_settled_occurrences_allow_retired_agents(root):
    def scopes(c):
        c.execute("INSERT INTO scopes VALUES(?,0)", ('["retired","workspace",null]',))

    sqlite_file(root, "memory", change=scopes)

    def completed(c):
        c.execute(
            "INSERT INTO occurrences VALUES(?,?,1,'synthetic','synthetic','retired',"
            "'workspace',1,1,'completed','known',?,NULL)",
            ("c" * 32, "d" * 32, "e" * 32),
        )

    sqlite_file(root, "schedules", change=completed)
    write_json(root)
    before = fingerprint(root)
    report = inspect(root, roles=("memory", "schedules", "agents"))
    assert report.status == "ok"
    assert fingerprint(root) == before


def test_special_file_is_rejected_without_blocking_or_reading(root):
    (root / "profiles").mkdir(mode=0o700)
    os.mkfifo(root / "profiles" / "agents.json", 0o600)
    report = inspect(root)
    assert report.status == "unsafe"
    assert report.checks[0].code == "doctor_file_unsafe"


def test_sidecar_symlink_is_never_followed(root):
    sqlite_file(root)
    outside = root.parent / "canary"
    outside.write_text("PRIVATE OUTSIDE")
    (root / "jarvis.sqlite3-wal").symlink_to(outside)
    before = fingerprint(root)
    report = inspect(root, roles=("state",))
    assert (
        report.status == "unsafe" and report.checks[0].code == "doctor_sidecar_unsafe"
    )
    assert fingerprint(root) == before
    assert outside.read_text() == "PRIVATE OUTSIDE"


def test_coherence_must_be_typed_not_an_unhashable_external_value(root):
    with pytest.raises(DoctorError, match="doctor_inventory_invalid"):
        replace(inventory(root), coherence=[])
