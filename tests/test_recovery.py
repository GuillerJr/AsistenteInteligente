import asyncio
import json
import os
import sqlite3
import threading
import time
from dataclasses import replace
from uuid import uuid4

import pytest

from jarvis_core import memory, profiles, scheduler, state, telegram
from jarvis_core.recovery import (
    EXCLUSIONS,
    PORTABLE_CONFIG,
    JsonSource,
    NewTarget,
    RecoveryContext,
    RecoveryError,
    RecoveryLimits,
    SourceBundle,
    SqliteImageSource,
    SqliteSource,
    _transform_sqlite,
    canonical,
    capture_sqlite_snapshot,
    create_backup,
    restore_new,
    verify_backup,
)
from jarvis_core.recovery_fs import Directory, FileIdentity, RecoveryFsError, digest, no_replace
from jarvis_core.tools import (
    ApprovalAuthority,
    HostApprovalRequest,
    HostCancellation,
    HostPurpose,
    HostScope,
    canonical_host_arguments,
    host_operation_digest,
)


def ctx(*, guard=None, generation=1, cancel=None, clock=time.monotonic, deadline=None):
    return RecoveryContext(
        uuid4().hex,
        "fixture_profile",
        generation,
        guard or (lambda stage, write: None),
        cancel or threading.Event(),
        clock() + 30 if deadline is None else deadline,
        clock,
    )


def target(parent, name="backup", **kwargs):
    kwargs.setdefault("forbidden_roots", (parent / "source",))
    return NewTarget(parent, FileIdentity.of(parent.stat()), name, **kwargs)


@pytest.fixture
def bundle(tmp_path):
    root = tmp_path / "source"
    root.mkdir(mode=0o700)
    connections = []

    def connection(role, filename, module):
        c = sqlite3.connect(root / filename, isolation_level=None, check_same_thread=False)
        os.chmod(root / filename, 0o600)
        c.executescript(module._SCHEMA)
        c.execute(f"pragma application_id={module._APPLICATION_ID}")
        c.execute(f"pragma user_version={module._SCHEMA_VERSION}")
        connections.append(c)
        return c

    sid, run = "a" * 32, "b" * 32
    c = connection("state", "jarvis.sqlite3", state)
    c.execute(
        "insert into sessions values(?,?,0,1,?,'jarvis',NULL)",
        (sid, "Conversación sintética", "PROVIDER_BINDING_MARKER_7642"),
    )
    c.execute(
        "insert into runs values(?,?,'completed','known',?,1,1)",
        (run, sid, '{"effect_status":"reported","verified":false}'),
    )
    c.execute(
        "insert into messages values(?,?,1,'user',?,NULL,1)", ("c" * 32, sid, "CONTENIDO SINTÉTICO")
    )
    mem = connection("memory", "memory.sqlite3", memory)
    mem.execute("insert into metadata values('search_mode','lexical_scan')")
    scope = '["jarvis","fixture_workspace",null]'
    text = "TOPACIO 4826"
    mem.execute("insert into scopes values(?,1)", (scope,))
    mem.execute(
        "insert into documents values(?,?,?,?,?,?,?,?,?,?,?,?)",
        (
            "d" * 32,
            scope,
            "nota",
            "Nota sintética",
            "jarvis://fixture",
            1,
            text,
            digest(text.encode()),
            len(text.encode()),
            1,
            1,
            1,
        ),
    )
    mem.execute("insert into chunks values(1,?,0,0,?,?)", ("d" * 32, len(text.encode()), text))
    sched = connection("schedules", "schedules.sqlite3", scheduler)
    sched.execute(
        "insert into schedules values(?,?,?,?,?,?,1,1,100,1,1)",
        (
            "e" * 32,
            "Tarea sintética",
            "No ejecutar",
            "jarvis",
            "fixture_workspace",
            '{"kind":"once","at":100}',
        ),
    )
    agents = profiles._initial()
    skill_text = "---\nname: Sintética\ndescription: Solo prueba\n---\nContenido\n"
    sk = {
        "version": 1,
        "skills": {
            "skill_ffffffffffffffffffffffffffffffff": {
                "agent_id": "jarvis",
                "text": skill_text,
                "sha256": digest(skill_text.encode()),
                "enabled": True,
                "revision": 1,
                "source_path": "/synthetic/no-follow/SKILL.md",
                "source_identity": [1, 2],
                "source_file_identity": [1, 3],
                "created_ns": 1,
                "updated_ns": 1,
                "provenance": "imported_snapshot",
            }
        },
    }
    journal = telegram._journal_initial()
    journal["routes"] = {
        "42": {
            "chat_id": 42,
            "agent_id": "jarvis",
            "session_id": sid,
            "revision": 1,
            "enabled": True,
        }
    }
    sources = (
        SqliteSource("state", c, lambda: None),
        SqliteSource("memory", mem, lambda: None),
        SqliteSource("schedules", sched, lambda: None),
    )
    docs = tuple(
        JsonSource(role, canonical(value), lambda: None)
        for role, value in (("agents", agents), ("skills", sk), ("telegram", journal))
    )
    # These excluded markers prove no directory traversal or broad file copy.
    (root / "provider").mkdir(mode=0o700)
    (root / "provider" / "dedicated-secret.fixture").write_text("MUST_NOT_BACK_UP_8141")
    (root / "workspace").mkdir(mode=0o700)
    (root / "workspace" / "external.fixture").write_text("WORKSPACE_NOT_BACKED_UP_4815")
    (root / "configuration.json").write_text('{"mcp_servers":{"argv":"TOKEN_1954"}}')
    value = SourceBundle(
        "fixture_profile", 1, root, FileIdentity.of(root.stat()), sources, docs, ()
    )
    yield value
    for c in connections:
        c.close()


def test_roundtrip_real_sqlite_new_disarmed_profile(tmp_path, bundle):
    receipt = create_backup(bundle, target(tmp_path), ctx())
    assert receipt.status == "completed"
    assert receipt.effect_status == "published_verified_bytes"
    backup = tmp_path / "backup"
    verified = verify_backup(backup, ctx())
    assert verified.authenticity == "not_established"
    assert verified.excluded == EXCLUSIONS
    restored = restore_new(backup, verified, target(tmp_path, "restored"), ctx())
    assert restored.status == "completed"
    assert restored.effect_status == "new_disarmed_profile"
    root = tmp_path / "restored"
    assert set(p.name for p in root.iterdir()) == {
        "jarvis.sqlite3",
        "memory.sqlite3",
        "schedules.sqlite3",
        "profiles",
        "telegram",
        "configuration.json",
        "restore_record.json",
    }
    c = sqlite3.connect(root / "jarvis.sqlite3")
    assert c.execute("select text from messages").fetchone() == ("CONTENIDO SINTÉTICO",)
    assert c.execute("select provider_thread_id from sessions").fetchone() == (None,)
    assert json.loads(c.execute("select receipt_json from runs").fetchone()[0]) == {
        "effect_status": "reported",
        "verified": False,
    }
    c.close()
    c = sqlite3.connect(root / "memory.sqlite3")
    assert c.execute("select text from documents").fetchone() == ("TOPACIO 4826",)
    assert c.execute("select scope_key from scopes").fetchone() == (
        '["jarvis","fixture_workspace",null]',
    )
    c.close()
    c = sqlite3.connect(root / "schedules.sqlite3")
    assert c.execute("select enabled,next_at from schedules").fetchone() == (0, None)
    c.close()
    assert json.loads((root / "configuration.json").read_bytes()) == PORTABLE_CONFIG
    assert not json.loads((root / "profiles" / "skills.json").read_bytes())["skills"][
        "skill_ffffffffffffffffffffffffffffffff"
    ]["enabled"]
    assert not json.loads((root / "telegram" / "telegram.json").read_bytes())["routes"]["42"][
        "enabled"
    ]
    record = json.loads((root / "restore_record.json").read_bytes())
    assert record["activation_required"] and not record["replay_allowed"]
    assert record["profile_id"] != bundle.profile_id
    for path in backup.rglob("*"):
        if path.is_file():
            data = path.read_bytes()
            assert b"MUST_NOT_BACK_UP_8141" not in data
            assert b"WORKSPACE_NOT_BACKED_UP_4815" not in data
            assert b"TOKEN_1954" not in data
            assert b"PROVIDER_BINDING_MARKER_7642" not in data
            assert path.stat().st_mode & 0o777 == 0o600
    # Source provider binding/data and enabled source jobs are not changed.
    assert (
        bundle.sqlite[0].connection.execute("select provider_thread_id from sessions").fetchone()[0]
        == "PROVIDER_BINDING_MARKER_7642"
    )
    assert bundle.sqlite[2].connection.execute("select enabled from schedules").fetchone() == (1,)


def test_online_wal_snapshot_includes_uncheckpointed_committed_data(tmp_path, bundle):
    c = bundle.sqlite[0].connection
    assert c.execute("pragma journal_mode=wal").fetchone() == ("wal",)
    c.execute("update messages set text='COMMIT SOLO WAL'")
    create_backup(bundle, target(tmp_path), ctx())
    raw = (tmp_path / "backup" / "data" / "state.sqlite3").read_bytes()
    assert raw[18:20] == b"\x01\x01"
    read = sqlite3.connect(":memory:")
    read.deserialize(raw)
    assert read.execute("select text from messages").fetchone() == ("COMMIT SOLO WAL",)
    read.close()
    assert c.execute("pragma journal_mode").fetchone() == ("wal",)


@pytest.mark.parametrize("name", ["../escape", "a/b", ".", "..", ".pending", "", "a\\b"])
def test_destination_traversal_rejected_without_write(tmp_path, bundle, name):
    with pytest.raises(RecoveryError) as caught:
        create_backup(bundle, target(tmp_path, name), ctx())
    assert caught.value.receipt.effect_status == "no_published_effect"
    assert not list(tmp_path.glob(".jarvis-stage-*"))


def test_existing_destination_is_never_overwritten(tmp_path, bundle):
    existing = tmp_path / "backup"
    existing.mkdir(mode=0o700)
    (existing / "sentinel").write_bytes(b"preserve")
    with pytest.raises(RecoveryError) as caught:
        create_backup(bundle, target(tmp_path), ctx())
    assert caught.value.code == "recovery_destination_exists"
    assert caught.value.receipt.effect_status == "no_published_effect"
    assert (existing / "sentinel").read_bytes() == b"preserve"
    assert not list(tmp_path.glob(".jarvis-stage-*"))


def test_cancellation_before_publish_cleans_owned_staging(tmp_path, bundle):
    cancel = threading.Event()

    def guard(stage, write):
        if stage == "resource_write":
            cancel.set()

    with pytest.raises(RecoveryError) as caught:
        create_backup(bundle, target(tmp_path), ctx(cancel=cancel, guard=guard))
    assert caught.value.code == "recovery_cancelled"
    assert not (tmp_path / "backup").exists()
    assert not list(tmp_path.glob(".jarvis-stage-*"))


def test_guard_revocation_stops_before_effect(tmp_path, bundle):
    writes = []

    def guard(stage, write):
        if write:
            writes.append(stage)
            raise RecoveryFsError("synthetic_authority_revoked")

    with pytest.raises(RecoveryError) as caught:
        create_backup(bundle, target(tmp_path), ctx(guard=guard))
    assert caught.value.code == "synthetic_authority_revoked"
    assert writes == ["stage_create"]
    assert not list(tmp_path.glob(".jarvis-stage-*"))


def test_lost_publication_ack_is_unknown_never_cleaned_or_retried(tmp_path, bundle):
    calls = []

    def lost_ack(parent, old, new):
        calls.append(new)
        no_replace(parent, old, new)
        raise OSError("synthetic_lost_ack")

    with pytest.raises(RecoveryError) as caught:
        create_backup(bundle, target(tmp_path), ctx(), publish=lost_ack)
    assert calls == ["backup"]
    assert caught.value.receipt.status == "unknown"
    assert caught.value.receipt.cleanup_pending
    assert (tmp_path / "backup" / ".pending").is_file()
    with pytest.raises(RecoveryError, match="recovery_artifact_entries_invalid"):
        verify_backup(tmp_path / "backup", ctx())


def test_same_operation_cannot_replay_even_after_success(tmp_path, bundle):
    context = ctx()
    create_backup(bundle, target(tmp_path), context)
    with pytest.raises(RecoveryFsError, match="recovery_operation_already_used"):
        create_backup(bundle, target(tmp_path, "replay"), context)
    assert not (tmp_path / "replay").exists()


@pytest.mark.parametrize("change", ["resource", "manifest", "unknown_file", "hardlink", "symlink"])
def test_corruption_unsafe_entries_and_links_fail_closed(tmp_path, bundle, change):
    create_backup(bundle, target(tmp_path), ctx())
    root = tmp_path / "backup"
    file = root / "data" / "agents.json"
    if change == "resource":
        file.write_bytes(file.read_bytes() + b" ")
    elif change == "manifest":
        manifest = json.loads((root / "manifest.json").read_bytes())
        manifest["resources"][0]["path"] = "../../outside"
        (root / "manifest.json").write_bytes(canonical(manifest))
    elif change == "unknown_file":
        (root / "unexpected").write_bytes(b"x")
    elif change == "hardlink":
        os.link(file, tmp_path / "linked")
    else:
        data = file.read_bytes()
        file.unlink()
        (tmp_path / "elsewhere").write_bytes(data)
        file.symlink_to(tmp_path / "elsewhere")
    with pytest.raises(RecoveryError):
        verify_backup(root, ctx())


def test_review_identity_swap_cannot_restore(tmp_path, bundle):
    create_backup(bundle, target(tmp_path), ctx())
    expected = verify_backup(tmp_path / "backup", ctx())
    (tmp_path / "backup").rename(tmp_path / "old")
    create_backup(bundle, target(tmp_path), ctx())
    with pytest.raises(RecoveryError) as caught:
        restore_new(tmp_path / "backup", expected, target(tmp_path, "restored"), ctx())
    assert caught.value.code == "recovery_identity_changed"
    assert not (tmp_path / "restored").exists()


def test_source_generation_and_source_identity_are_checked(tmp_path, bundle):
    with pytest.raises(RecoveryError, match="recovery_source_generation_changed"):
        create_backup(bundle, target(tmp_path), ctx(generation=2))
    root = bundle.root
    root.rename(tmp_path / "replaced")
    root.mkdir(mode=0o700)
    with pytest.raises(RecoveryError, match="recovery_identity_changed"):
        create_backup(bundle, target(tmp_path), ctx())


def test_deadline_is_latched_and_does_not_gain_time(tmp_path, bundle):
    now = [0.0]

    def guard(stage, write):
        if stage == "json_snapshot":
            now[0] = 11

    context = ctx(clock=lambda: now[0], deadline=10, guard=guard)
    with pytest.raises(RecoveryError, match="recovery_deadline_exceeded"):
        create_backup(bundle, target(tmp_path), context)
    now[0] = 0
    with pytest.raises(RecoveryFsError, match="recovery_deadline_exceeded"):
        context.checkpoint("after_clock_reset", True)
    assert not list(tmp_path.glob(".jarvis-stage-*"))


def test_pending_source_fails_without_settling_or_replaying_it(tmp_path, bundle):
    c = bundle.sqlite[0].connection
    c.execute("update runs set status='running',outcome='pending',receipt_json=NULL")
    with pytest.raises(RecoveryError, match="recovery_source_unsettled"):
        create_backup(bundle, target(tmp_path), ctx())
    assert c.execute("select status from runs").fetchone() == ("running",)
    assert not (tmp_path / "backup").exists()


def test_defensive_restore_of_unfinished_data_is_unknown_not_success(tmp_path, bundle):
    c = bundle.sqlite[0].connection
    c.execute("update runs set status='running',outcome='pending',receipt_json=NULL")
    before = c.serialize()
    restored = _transform_sqlite("state", before, ctx(), RecoveryLimits(), restoring=True)
    r = sqlite3.connect(":memory:")
    r.deserialize(restored)
    status, outcome, raw = r.execute("select status,outcome,receipt_json from runs").fetchone()
    assert (status, outcome) == ("interrupted", "unknown")
    assert json.loads(raw)["replay_allowed"] is False
    r.close()
    assert c.execute("select status from runs").fetchone() == ("running",)


def test_missing_optional_sources_are_explicit_not_created(tmp_path, bundle):
    minimal = replace(
        bundle,
        sqlite=(),
        json=(bundle.json[0],),
        absent=("state", "memory", "schedules", "skills", "telegram"),
    )
    create_backup(minimal, target(tmp_path), ctx())
    verification = verify_backup(tmp_path / "backup", ctx())
    restore_new(tmp_path / "backup", verification, target(tmp_path, "restored"), ctx())
    assert not (tmp_path / "restored" / "jarvis.sqlite3").exists()
    assert not (tmp_path / "restored" / "memory.sqlite3").exists()
    assert not (tmp_path / "restored" / "schedules.sqlite3").exists()


def test_total_size_limit_fails_without_partial_backup(tmp_path, bundle):
    with pytest.raises(RecoveryError, match="recovery_resource_limit"):
        create_backup(bundle, target(tmp_path), ctx(), limits=RecoveryLimits(max_total_bytes=1000))
    assert not (tmp_path / "backup").exists()
    assert not list(tmp_path.glob(".jarvis-stage-*"))


def test_no_replace_primitive_real_owned_directories(tmp_path):
    (tmp_path / "source_dir").mkdir(mode=0o700)
    (tmp_path / "existing").mkdir(mode=0o700)
    with Directory(tmp_path, private=False) as directory:
        with pytest.raises(RecoveryFsError, match="recovery_destination_exists"):
            no_replace(directory, "source_dir", "existing")
        no_replace(directory, "source_dir", "new_dir")
    assert (tmp_path / "existing").is_dir() and (tmp_path / "new_dir").is_dir()


def test_change_earlier_resource_during_later_verification_is_detected(tmp_path, bundle):
    create_backup(bundle, target(tmp_path), ctx())
    root = tmp_path / "backup"
    changed = False

    def guard(stage, write):
        nonlocal changed
        if stage == "sqlite_inspect" and not changed:
            changed = True
            file = root / "data" / "agents.json"
            # Same content, different inode: digest alone must not certify it.
            data = file.read_bytes()
            file.rename(root / "data" / "old_agents.fixture")
            file.write_bytes(data)
            file.chmod(0o600)

    with pytest.raises(RecoveryError, match="recovery_identity_changed"):
        verify_backup(root, ctx(guard=guard))


def test_final_verification_guard_cannot_hide_resource_mutation(tmp_path, bundle):
    create_backup(bundle, target(tmp_path), ctx())
    root = tmp_path / "backup"

    def guard(stage, write):
        if stage == "verify_complete":
            file = root / "data" / "agents.json"
            file.write_bytes(file.read_bytes() + b" ")

    with pytest.raises(RecoveryError, match="recovery_identity_changed"):
        verify_backup(root, ctx(guard=guard))


def test_cleanup_refuses_replaced_owned_file_and_retains_both_errors(tmp_path, bundle):
    cancelled = threading.Event()
    context = ctx(cancel=cancelled)
    stage_root = tmp_path / (".jarvis-stage-" + context.operation_id)

    def guard(stage, write):
        file = stage_root / "data" / "configuration_portable.json"
        if stage == "resource_write" and file.exists():
            file.rename(file.with_name("original_retained.fixture"))
            file.write_bytes(b"FOREIGN_SENTINEL")
            file.chmod(0o600)
            cancelled.set()

    context.check_current = guard
    with pytest.raises(RecoveryError) as caught:
        create_backup(bundle, target(tmp_path), context)
    receipt = caught.value.receipt
    assert caught.value.code == "recovery_cancelled"
    assert receipt.cleanup_error_code == "recovery_cleanup_identity_changed"
    assert receipt.effect_status == "unknown" and receipt.cleanup_pending
    assert (stage_root / "data" / "configuration_portable.json").read_bytes() == b"FOREIGN_SENTINEL"
    assert not (tmp_path / "backup").exists()


def test_parent_identity_changed_after_review_is_not_followed(tmp_path, bundle):
    parent = tmp_path / "output"
    parent.mkdir(mode=0o700)
    reviewed = target(parent)
    parent.rename(tmp_path / "old_output")
    parent.mkdir(mode=0o700)
    with pytest.raises(RecoveryError, match="recovery_identity_changed"):
        create_backup(bundle, reviewed, ctx())
    assert not list(parent.iterdir())


def test_source_guard_rechecked_after_bytes_before_publication(tmp_path, bundle):
    source = bundle.json[0]
    checked = 0

    def source_guard():
        nonlocal checked
        checked += 1
        if checked == 3:
            raise RecoveryFsError("synthetic_source_generation_changed")

    replacement = replace(source, check_current=source_guard)
    bundle = replace(bundle, json=(replacement, *bundle.json[1:]))
    with pytest.raises(RecoveryError, match="synthetic_source_generation_changed"):
        create_backup(bundle, target(tmp_path), ctx())
    assert checked == 3
    assert not (tmp_path / "backup").exists()
    assert not list(tmp_path.glob(".jarvis-stage-*"))


def test_active_transaction_fails_closed_without_waiting_for_backup(tmp_path, bundle):
    c = bundle.sqlite[0].connection
    c.execute("BEGIN IMMEDIATE")
    try:
        with pytest.raises(RecoveryError, match="recovery_source_transaction_active"):
            create_backup(bundle, target(tmp_path), ctx())
    finally:
        c.rollback()
    assert not (tmp_path / "backup").exists()


def test_attached_databases_never_become_backup_sources(tmp_path, bundle):
    c = bundle.sqlite[0].connection
    c.execute("ATTACH DATABASE ':memory:' AS unrelated")
    try:
        with pytest.raises(RecoveryError, match="recovery_source_not_owned_durable"):
            create_backup(bundle, target(tmp_path), ctx())
    finally:
        c.execute("DETACH DATABASE unrelated")
    assert not (tmp_path / "backup").exists()


def test_deadline_at_dispatch_is_no_publication_and_owned_cleanup(tmp_path, bundle):
    now = [0.0]

    def guard(stage, write):
        if stage == "publish_dispatch":
            now[0] = 11

    with pytest.raises(RecoveryError) as caught:
        create_backup(bundle, target(tmp_path), ctx(clock=lambda: now[0], deadline=10, guard=guard))
    assert caught.value.code == "recovery_deadline_exceeded"
    assert caught.value.receipt.effect_status == "no_published_effect"
    assert not list(tmp_path.glob(".jarvis-stage-*"))
    assert not (tmp_path / "backup").exists()


def test_cancel_after_rename_retains_pending_unknown_and_no_replay(tmp_path, bundle):
    cancelled = threading.Event()

    def dispatch(parent, old, new):
        no_replace(parent, old, new)
        cancelled.set()

    with pytest.raises(RecoveryError) as caught:
        create_backup(bundle, target(tmp_path), ctx(cancel=cancelled), publish=dispatch)
    assert caught.value.code == "recovery_cancelled"
    assert caught.value.receipt.status == "unknown"
    assert (tmp_path / "backup" / ".pending").is_file()


def test_publication_rechecks_actual_bytes_before_certification(tmp_path, bundle):
    def dispatch(parent, old, new):
        no_replace(parent, old, new)
        file = parent.path / new / "data" / "agents.json"
        file.write_bytes(file.read_bytes() + b" ")

    with pytest.raises(RecoveryError) as caught:
        create_backup(bundle, target(tmp_path), ctx(), publish=dispatch)
    assert caught.value.code == "recovery_resource_changed"
    assert caught.value.receipt.status == "unknown"
    assert (tmp_path / "backup" / ".pending").is_file()


def test_fsync_after_rename_failure_is_unknown(tmp_path, bundle, monkeypatch):
    original = os.fsync

    def fsync(fd):
        if (
            FileIdentity.of(os.fstat(fd)) == FileIdentity.of(tmp_path.stat())
            and (tmp_path / "backup").exists()
        ):
            raise OSError("synthetic_disk_failure")
        return original(fd)

    monkeypatch.setattr(os, "fsync", fsync)
    with pytest.raises(RecoveryError) as caught:
        create_backup(bundle, target(tmp_path), ctx())
    assert caught.value.receipt.status == "unknown"
    assert (tmp_path / "backup" / ".pending").is_file()


def test_restore_cannot_replace_existing_profile(tmp_path, bundle):
    create_backup(bundle, target(tmp_path), ctx())
    verified = verify_backup(tmp_path / "backup", ctx())
    destination = tmp_path / "existing_profile"
    destination.mkdir(mode=0o700)
    sentinel = destination / "owner.fixture"
    sentinel.write_bytes(b"KEEP_EXISTING")
    with pytest.raises(RecoveryError, match="recovery_destination_exists"):
        restore_new(tmp_path / "backup", verified, target(tmp_path, "existing_profile"), ctx())
    assert sentinel.read_bytes() == b"KEEP_EXISTING"
    assert verify_backup(tmp_path / "backup", ctx()) == verified


def test_restore_detects_post_rename_tamper_and_keeps_backup_unchanged(tmp_path, bundle):
    create_backup(bundle, target(tmp_path), ctx())
    verified = verify_backup(tmp_path / "backup", ctx())

    def dispatch(parent, old, new):
        no_replace(parent, old, new)
        (parent.path / new / "configuration.json").write_bytes(b'{"workspace":"not_portable"}')

    with pytest.raises(RecoveryError) as caught:
        restore_new(
            tmp_path / "backup", verified, target(tmp_path, "restored"), ctx(), publish=dispatch
        )
    assert caught.value.code == "recovery_restore_bytes_changed"
    assert caught.value.receipt.status == "unknown"
    assert (tmp_path / "restored" / ".pending").is_file()
    assert verify_backup(tmp_path / "backup", ctx()) == verified


def test_schema_tamper_with_updated_checksum_is_still_rejected(tmp_path, bundle):
    create_backup(bundle, target(tmp_path), ctx())
    root = tmp_path / "backup"
    path = root / "data" / "state.sqlite3"
    c = sqlite3.connect(path)
    c.execute("create table injected(x)")
    c.close()
    manifest = json.loads((root / "manifest.json").read_bytes())
    for row in manifest["resources"]:
        if row["role"] == "state":
            row.update(size=path.stat().st_size, sha256=digest(path.read_bytes()))
    (root / "manifest.json").write_bytes(canonical(manifest))
    with pytest.raises(RecoveryError, match="snapshot_schema_unsupported"):
        verify_backup(root, ctx())


def test_artifact_verification_never_repairs_pending_marker(tmp_path, bundle):
    create_backup(bundle, target(tmp_path), ctx())
    root = tmp_path / "backup"
    pending = root / ".pending"
    pending.write_bytes(b"unacknowledged")
    pending.chmod(0o600)
    before = pending.stat().st_mtime_ns
    with pytest.raises(RecoveryError, match="recovery_artifact_entries_invalid"):
        verify_backup(root, ctx())
    assert pending.read_bytes() == b"unacknowledged" and pending.stat().st_mtime_ns == before


@pytest.mark.parametrize("unsafe", ["file_mode", "directory_mode", "root_link"])
def test_unsafe_selected_artifact_rejected(tmp_path, bundle, unsafe):
    create_backup(bundle, target(tmp_path), ctx())
    root = tmp_path / "backup"
    if unsafe == "file_mode":
        (root / "data" / "agents.json").chmod(0o644)
    elif unsafe == "directory_mode":
        (root / "data").chmod(0o755)
    else:
        root.rename(tmp_path / "real_backup")
        root.symlink_to(tmp_path / "real_backup")
    with pytest.raises(RecoveryError):
        verify_backup(root, ctx())


def test_target_cannot_be_inside_source_or_declared_workspace(tmp_path, bundle):
    with pytest.raises(RecoveryError, match="recovery_destination_overlaps_source"):
        create_backup(bundle, target(bundle.root, "nested"), ctx())
    output = tmp_path / "workspace"
    output.mkdir(mode=0o700)
    with pytest.raises(RecoveryError, match="recovery_destination_overlaps_source"):
        create_backup(bundle, target(output, forbidden_roots=(output,)), ctx())


def test_own_memory_store_fts_survives_roundtrip(tmp_path, bundle):
    bundle.sqlite[1].connection.close()
    (bundle.root / "memory.sqlite3").unlink()
    store = memory.MemoryStore(bundle.root / "memory.sqlite3")
    scope = memory.MemoryScope("jarvis", "fixture_workspace")
    try:
        store.upsert(scope, "own_api", "TOPACIO FTS 4826", source_uri="jarvis://synthetic")
        bundle = replace(
            bundle,
            sqlite=(
                bundle.sqlite[0],
                SqliteSource("memory", store._disk, store._check_disk),
                bundle.sqlite[2],
            ),
        )
        create_backup(bundle, target(tmp_path), ctx())
        verified = verify_backup(tmp_path / "backup", ctx())
        restore_new(tmp_path / "backup", verified, target(tmp_path, "restored"), ctx())
        c = sqlite3.connect(tmp_path / "restored" / "memory.sqlite3")
        assert c.execute("select text from documents").fetchone() == ("TOPACIO FTS 4826",)
        if c.execute("select value from metadata").fetchone() == ("fts5_lexical",):
            assert c.execute(
                "select text from chunk_fts where chunk_fts match 'TOPACIO'"
            ).fetchone()
        c.close()
    finally:
        store.close()


def test_cancel_before_start_returns_receipt_and_no_effect(tmp_path, bundle):
    cancel = threading.Event()
    cancel.set()
    with pytest.raises(RecoveryError) as caught:
        create_backup(bundle, target(tmp_path), ctx(cancel=cancel))
    assert caught.value.code == "recovery_cancelled"
    assert caught.value.receipt.status == "cancelled"
    assert caught.value.receipt.effect_status == "no_published_effect"
    assert not list(tmp_path.glob(".jarvis-stage-*"))


def test_restore_requires_active_root_inventory(tmp_path, bundle):
    create_backup(bundle, target(tmp_path), ctx())
    verification = verify_backup(tmp_path / "backup", ctx())
    destination = replace(target(tmp_path, "restored"), forbidden_roots=())
    with pytest.raises(RecoveryError, match="recovery_active_root_inventory_required"):
        restore_new(tmp_path / "backup", verification, destination, ctx())
    assert not (tmp_path / "restored").exists()


async def approved_host(authority, operation, cancel):
    task = asyncio.create_task(authority.request_host(operation, cancel))
    for _ in range(100):
        pending = authority.pending_host(operation.scope)
        if pending:
            break
        await asyncio.sleep(0)
    assert len(pending) == 1
    # Synthetic test owner; production decisions belong to the real native user.
    assert authority.decide_host(
        pending[0].approval_id, scope=operation.scope, digest=operation.digest, allow=True
    )
    return await asyncio.wait_for(task, 1)


def host_fixture(bundle, destination):
    authority = ApprovalAuthority()
    scope = HostScope(bundle.profile_id, bundle.generation)
    authority.bind_host_scope(scope)
    operation = HostApprovalRequest(
        scope,
        HostPurpose.BACKUP_CREATE,
        uuid4().hex,
        canonical_host_arguments(
            {
                "source_identity": list(vars(bundle.root_identity).values()),
                "parent_identity": list(vars(destination.parent_identity).values()),
                "name": destination.name,
                "generation": bundle.generation,
            }
        ),
        "",
    )
    operation = replace(operation, digest=host_operation_digest(operation))
    cancel = HostCancellation()
    committed = False
    lock = threading.Lock()

    def guard(stage, write):
        nonlocal committed
        with lock:
            # This fixture holds a fixed synthetic source and parent identity;
            # the real host also holds its all-mutator profile barrier here.
            assert FileIdentity.of(bundle.root.stat()) == bundle.root_identity
            assert FileIdentity.of(destination.parent.stat()) == destination.parent_identity
            if committed:
                authority.revalidate_committed_host(operation)
            else:
                authority.check_host(operation, commit=write)
                if write:
                    committed = True

    context = RecoveryContext(
        operation.operation_id,
        bundle.profile_id,
        bundle.generation,
        guard,
        cancel,
        time.monotonic() + 30,
    )
    return authority, scope, operation, cancel, context


@pytest.mark.asyncio
async def test_real_host_lease_carries_into_worker_and_commits_once(tmp_path, bundle):
    destination = target(tmp_path)
    authority, _scope, operation, cancel, context = host_fixture(bundle, destination)
    try:
        grant = await approved_host(authority, operation, cancel)
        with authority.execution_host(operation, grant):
            result = await asyncio.to_thread(create_backup, bundle, destination, context)
            assert result.status == "completed"
            authority.revalidate_committed_host(operation)
        with pytest.raises(Exception, match="host_authority_required"):
            authority.revalidate_committed_host(operation)
    finally:
        await authority.close()


@pytest.mark.asyncio
async def test_real_host_scope_revoked_while_worker_gated_has_no_publication(tmp_path, bundle):
    destination = target(tmp_path)
    authority, scope, operation, cancel, context = host_fixture(bundle, destination)
    ready, release = threading.Event(), threading.Event()
    original = context.check_current

    def gated_guard(stage, write):
        if stage == "resource_write":
            ready.set()
            assert release.wait(3), "synthetic gate not released"
        original(stage, write)

    context.check_current = gated_guard
    try:
        grant = await approved_host(authority, operation, cancel)
        with authority.execution_host(operation, grant):
            worker = asyncio.create_task(
                asyncio.to_thread(create_backup, bundle, destination, context)
            )
            try:
                assert await asyncio.to_thread(ready.wait, 2)
                authority.revoke_host(scope)
            finally:
                release.set()
            with pytest.raises(RecoveryError) as caught:
                await asyncio.wait_for(worker, 2)
            assert caught.value.receipt.effect_status == "no_published_effect"
            assert caught.value.code.startswith("host_")
        assert not (tmp_path / "backup").exists()
        assert not list(tmp_path.glob(".jarvis-stage-*"))
    finally:
        release.set()
        await authority.close()


@pytest.mark.asyncio
async def test_worker_without_central_execution_scope_cannot_create_stage(tmp_path, bundle):
    destination = target(tmp_path)
    authority, _scope, _operation, _cancel, context = host_fixture(bundle, destination)
    try:
        with pytest.raises(RecoveryError) as caught:
            await asyncio.to_thread(create_backup, bundle, destination, context)
        assert caught.value.code == "host_authority_required"
        assert caught.value.receipt.effect_status == "no_published_effect"
        assert not list(tmp_path.glob(".jarvis-stage-*"))
    finally:
        await authority.close()


def test_cold_standalone_images_without_opening_or_recovering_store(tmp_path, bundle):
    images = []
    for source in bundle.sqlite:
        raw = source.connection.serialize()
        filename = {
            "state": "jarvis.sqlite3",
            "memory": "memory.sqlite3",
            "schedules": "schedules.sqlite3",
        }[source.role]
        identity = FileIdentity.of((bundle.root / filename).stat())
        images.append(SqliteImageSource(source.role, raw, identity, digest(raw), lambda: None))
        source.connection.close()
    cold = replace(bundle, sqlite=tuple(images))
    create_backup(cold, target(tmp_path), ctx())
    verify_backup(tmp_path / "backup", ctx())
    assert b"PROVIDER_BINDING_MARKER_7642" in images[0].data
    assert (
        b"PROVIDER_BINDING_MARKER_7642"
        not in (tmp_path / "backup" / "data" / "state.sqlite3").read_bytes()
    )


@pytest.mark.parametrize("invalid", ["wal", "digest"])
def test_cold_image_invalidity_is_not_recovered_or_accepted(tmp_path, bundle, invalid):
    source = bundle.sqlite[0]
    raw = source.connection.serialize()
    if invalid == "wal":
        raw = raw[:18] + b"\x02\x02" + raw[20:]
    image = SqliteImageSource(
        "state",
        raw,
        FileIdentity.of((bundle.root / "jarvis.sqlite3").stat()),
        "0" * 64 if invalid == "digest" else digest(raw),
        lambda: None,
    )
    with pytest.raises(RecoveryError):
        create_backup(replace(bundle, sqlite=(image, *bundle.sqlite[1:])), target(tmp_path), ctx())
    assert not (tmp_path / "backup").exists()


def test_raw_owned_connection_capture_does_not_sanitize_or_recover(tmp_path, bundle):
    source = bundle.sqlite[0]
    source.connection.execute(
        "update runs set status='running',outcome='pending',receipt_json=NULL"
    )
    raw = capture_sqlite_snapshot(source, ctx(), RecoveryLimits(), bundle.root)
    assert b"PROVIDER_BINDING_MARKER_7642" in raw
    c = sqlite3.connect(":memory:")
    c.deserialize(raw)
    assert c.execute("select status from runs").fetchone() == ("running",)
    c.close()
    assert source.connection.execute("select status from runs").fetchone() == ("running",)
