"""Prepared activation fixtures use only our synthetic backup/restore pipeline."""

import fcntl
import json
import os
import shutil
import sqlite3
import stat
import threading
from dataclasses import replace

import pytest
from test_recovery import bundle as bundle
from test_recovery import ctx, target

import jarvis_core.activation as module
from jarvis_core.activation import (
    ADMISSION_NAME,
    MARKER_NAME,
    ActivationError,
    ActivationLimits,
    activate_prepared_profile,
    review_prepared_profile,
    validate_bootstrap_admission,
)
from jarvis_core.recovery import (
    EXCLUSIONS,
    JsonSource,
    SourceBundle,
    SqliteImageSource,
    canonical,
    create_backup,
    restore_new,
    verify_backup,
)
from jarvis_core.recovery_fs import FileIdentity, digest, no_replace


@pytest.fixture
def prepared(tmp_path, bundle):
    create_backup(bundle, target(tmp_path), ctx())
    verified = verify_backup(tmp_path / "backup", ctx())
    restore_new(tmp_path / "backup", verified, target(tmp_path, "restored"), ctx())
    return tmp_path / "restored"


def snapshot(root):
    result = {}
    for path in [root, *sorted(root.rglob("*"))]:
        info = path.lstat()
        data = (path.read_bytes() if stat.S_ISREG(info.st_mode)
                else os.readlink(path) if stat.S_ISLNK(info.st_mode) else None)
        result[str(path.relative_to(root))] = (info.st_dev, info.st_ino, info.st_mode,
                                             info.st_uid, info.st_nlink, info.st_size,
                                             info.st_mtime_ns, info.st_ctime_ns, data)
    return result


def review(root, **kwargs):
    return review_prepared_profile(root, ctx(), **kwargs)


def activate(root, **kwargs):
    return activate_prepared_profile(review(root), ctx(), **kwargs)


def assert_no_admission(root):
    assert not os.path.lexists(root / ADMISSION_NAME)
    assert not list(root.glob(".jarvis-activation-*"))


def edit_json(path, transform):
    value = json.loads(path.read_bytes())
    transform(value)
    path.write_bytes(canonical(value))


def test_roundtrip_nonempty_review_admission_and_immutable_marker(prepared, monkeypatch):
    before = snapshot(prepared)
    real_connect = sqlite3.connect
    calls = []

    def ram_only(database, *args, **kwargs):
        calls.append(database)
        assert database == ":memory:", "core opened a profile through SQLite VFS"
        return real_connect(database, *args, **kwargs)

    monkeypatch.setattr(module.sqlite3, "connect", ram_only)
    result = review(prepared)
    assert snapshot(prepared) == before
    counts = {x.role: dict(x.counts) for x in result.resources}
    assert counts["memory"]["documents"] == 1 and counts["state"]["sessions"] == 1
    assert counts["schedules"]["schedules"] == 1
    assert b"TOPACIO" not in canonical(result.metadata())
    assert b"CONTENIDO" not in canonical(result.metadata())
    receipt = activate_prepared_profile(result, ctx())
    assert calls and receipt.status == "completed"
    assert receipt.effect_status == "local_admission_published"
    assert not receipt.cleanup_pending and not receipt.services_started
    assert not receipt.grants_restored
    assert (prepared / MARKER_NAME).read_bytes() == before[MARKER_NAME][-1]
    assert snapshot(prepared)[MARKER_NAME] == before[MARKER_NAME]
    local = validate_bootstrap_admission(prepared)
    assert local.state == "local_present_valid"
    assert local.prior_operation_durability == "not_established"
    assert local.authenticity == "not_established" and not local.grants_restored
    assert local.admission_sha256 == receipt.admission_sha256
    for path in prepared.rglob("*"):
        assert path.stat().st_mode & 0o777 == (0o700 if path.is_dir() else 0o600)
    assert set(p.name for p in prepared.iterdir()) == set(before) - {
        ".", "profiles/agents.json", "profiles/skills.json", "telegram/telegram.json"
    } | {"profile.lock", ADMISSION_NAME}
    for name in ("workspace", "provider", "transport.token", "service.sock", "mcp"):
        assert not (prepared / name).exists()


def test_bootstrap_does_not_require_frozen_store_hashes_or_sqlite(prepared, monkeypatch):
    activate(prepared)
    with sqlite3.connect(prepared / "jarvis.sqlite3") as connection:
        connection.execute("UPDATE sessions SET title='New title',provider_thread_id='new'")
        connection.execute("UPDATE messages SET text='New conversation after activation'")
    edit_json(prepared / "configuration.json",
              lambda value: value.update(workspace="/new/selected"))

    def forbidden(*args, **kwargs):
        pytest.fail("bootstrap re-opened a mutable store")

    monkeypatch.setattr(module.sqlite3, "connect", forbidden)
    assert validate_bootstrap_admission(prepared).state == "local_present_valid"


@pytest.mark.parametrize("when", ["begin", "activation_lock_open"])
def test_denial_is_before_first_effect_and_leaves_profile_untouched(prepared, when):
    result, before = review(prepared), snapshot(prepared)

    def denied(stage, write):
        if stage == when:
            raise ActivationError("test_authority_denied")

    with pytest.raises(ActivationError, match="test_authority_denied") as caught:
        activate_prepared_profile(result, ctx(guard=denied))
    assert caught.value.receipt.status == "failed" and caught.value.owner is None
    assert snapshot(prepared) == before


@pytest.mark.parametrize("change", ["marker", "message", "file_identity", "namespace"])
def test_review_changed_rejected_before_any_write(prepared, change):
    result = review(prepared)
    if change == "marker":
        edit_json(prepared / MARKER_NAME, lambda v: v.update(source_generation=9))
    elif change == "message":
        with sqlite3.connect(prepared / "jarvis.sqlite3") as c:
            c.execute("UPDATE messages SET text='modified after review'")
    elif change == "file_identity":
        path = prepared / "memory.sqlite3"
        data = path.read_bytes()
        path.unlink()
        path.write_bytes(data)
        path.chmod(0o600)
    else:
        (prepared / "unknown").mkdir(mode=0o700)
    before = snapshot(prepared)
    with pytest.raises(ActivationError):
        activate_prepared_profile(result, ctx())
    assert snapshot(prepared) == before
    assert_no_admission(prepared)


def test_root_replacement_rejected_without_retargeting(prepared, tmp_path):
    result = review(prepared)
    saved = tmp_path / "original"
    prepared.rename(saved)
    shutil.copytree(saved, prepared)
    with pytest.raises(ActivationError):
        activate_prepared_profile(result, ctx())
    assert_no_admission(prepared)
    assert_no_admission(saved)


@pytest.mark.parametrize("stage", ["activation_lock_open", "activation_pending_create",
                                   "activation_pending_write", "activation_publish"])
def test_cancellation_at_effect_guard_never_publishes(prepared, stage):
    cancel = threading.Event()

    def guard(current, write):
        if current == stage:
            cancel.set()

    with pytest.raises(ActivationError, match="recovery_cancelled") as caught:
        activate_prepared_profile(review(prepared), ctx(guard=guard, cancel=cancel))
    assert caught.value.receipt.status == "cancelled" and caught.value.owner is None
    assert_no_admission(prepared)
    assert (prepared / MARKER_NAME).exists()


def test_deadline_latched_before_first_effect_and_context_single_use(prepared):
    clock = [10]
    context = ctx(clock=lambda: clock[0], deadline=11)
    result = review(prepared)
    clock[0] = 11
    with pytest.raises(ActivationError, match="recovery_deadline_exceeded"):
        activate_prepared_profile(result, context)
    clock[0] = 10
    with pytest.raises(ActivationError, match="recovery_operation_already_used"):
        activate_prepared_profile(result, context)
    assert_no_admission(prepared)


def test_context_one_use_after_completed_operation(prepared):
    result, context = review(prepared), ctx()
    activate_prepared_profile(result, context)
    before = snapshot(prepared)
    with pytest.raises(ActivationError, match="recovery_operation_already_used") as caught:
        activate_prepared_profile(result, context)
    assert caught.value.receipt is None
    assert snapshot(prepared) == before


@pytest.mark.parametrize("stage", ["activation_lock_open", "activation_pending_write",
                                   "activation_publish"])
def test_target_changed_inside_host_guard_is_checked_before_effect(prepared, stage):
    def guard(current, write):
        if current == stage:
            edit_json(prepared / MARKER_NAME, lambda v: v.update(source_generation=8))

    called = []

    def publish(*args):
        called.append(args)
        no_replace(*args)

    with pytest.raises(ActivationError, match="activation_review_changed"):
        activate_prepared_profile(review(prepared), ctx(guard=guard), publish=publish)
    assert not called
    assert_no_admission(prepared)


def test_lock_busy_never_releases_or_deletes_another_owners_lock(prepared):
    path = prepared / "profile.lock"
    path.touch(mode=0o600)
    fd = os.open(path, os.O_RDWR)
    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    result, before = review(prepared), snapshot(prepared)
    try:
        with pytest.raises(ActivationError, match="activation_profile_busy") as caught:
            activate_prepared_profile(result, ctx())
        assert caught.value.owner is None
        assert snapshot(prepared) == before
        other = os.open(path, os.O_RDWR)
        try:
            with pytest.raises(BlockingIOError):
                fcntl.flock(other, fcntl.LOCK_EX | fcntl.LOCK_NB)
        finally:
            os.close(other)
    finally:
        os.close(fd)


@pytest.mark.parametrize("kind", ["symlink", "directory", "content"])
def test_unsafe_preexisting_lock_is_not_opened_or_modified(prepared, kind):
    path = prepared / "profile.lock"
    if kind == "symlink":
        path.symlink_to(prepared / MARKER_NAME)
    elif kind == "directory":
        path.mkdir(mode=0o700)
    else:
        path.write_bytes(b"UNKNOWN")
        path.chmod(0o600)
    before = snapshot(prepared)
    with pytest.raises(ActivationError):
        review(prepared)
    assert snapshot(prepared) == before


@pytest.mark.parametrize("point", ["before_rename", "after_rename", "after_fsync"])
def test_lost_publication_ack_is_unknown_retains_owner_and_never_replays(prepared, point):
    result, calls = review(prepared), []

    def publish(parent, old, new):
        calls.append(new)
        if point != "before_rename":
            no_replace(parent, old, new)
        if point == "after_fsync":
            os.fsync(parent.fd)
        raise OSError("synthetic ACK lost")

    context = ctx()
    with pytest.raises(ActivationError) as caught:
        activate_prepared_profile(result, context, publish=publish)
    error = caught.value
    assert error.receipt.status == "unknown" and error.receipt.cleanup_pending
    assert error.owner is not None and not error.owner.closed and len(calls) == 1
    if point != "before_rename":
        local = validate_bootstrap_admission(prepared)
        assert local.state == "local_present_valid"
        assert local.prior_operation_durability == "not_established"
    else:
        with pytest.raises(ActivationError, match="profile_activation_required"):
            validate_bootstrap_admission(prepared)
    lock = os.open(prepared / "profile.lock", os.O_RDWR)
    try:
        with pytest.raises(BlockingIOError):
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    finally:
        os.close(lock)
    error.owner.cleanup()
    assert error.owner.closed and error.receipt.status == "unknown"
    assert not list(prepared.glob(".jarvis-activation-*"))
    with pytest.raises(ActivationError, match="recovery_operation_already_used"):
        activate_prepared_profile(result, context, publish=publish)
    assert len(calls) == 1


def test_directory_fsync_failure_retains_current_admission_without_claiming_completion(
    prepared, monkeypatch,
):
    real_fsync = os.fsync

    def failing(fd):
        if stat.S_ISDIR(os.fstat(fd).st_mode):
            raise OSError("synthetic directory fsync failure")
        return real_fsync(fd)

    monkeypatch.setattr(module.os, "fsync", failing)
    with pytest.raises(ActivationError) as caught:
        activate(prepared)
    error = caught.value
    assert error.receipt.status == "unknown" and error.owner is not None
    assert validate_bootstrap_admission(prepared).prior_operation_durability == "not_established"
    with pytest.raises(OSError):
        error.owner.cleanup()
    assert not error.owner.closed
    monkeypatch.setattr(module.os, "fsync", real_fsync)
    error.owner.cleanup()
    assert error.owner.closed and error.receipt.status == "unknown"


def test_pending_fsync_failure_does_not_publish_and_cleans_own_temporary(prepared, monkeypatch):
    real_fsync = os.fsync

    def failing(fd):
        if stat.S_ISREG(os.fstat(fd).st_mode):
            raise OSError("synthetic pending file fsync failure")
        return real_fsync(fd)

    monkeypatch.setattr(module.os, "fsync", failing)
    with pytest.raises(ActivationError) as caught:
        activate(prepared)
    assert caught.value.receipt.status == "failed" and caught.value.owner is None
    assert_no_admission(prepared)


def test_no_replace_refuses_destination_without_clobbering_it(prepared):
    foreign = b"FOREIGN ADMISSION DO NOT OVERWRITE"

    def race(parent, old, new):
        path = parent.path / new
        path.write_bytes(foreign)
        path.chmod(0o600)
        no_replace(parent, old, new)

    with pytest.raises(ActivationError, match="recovery_destination_exists") as caught:
        activate(prepared, publish=race)
    assert caught.value.receipt.status == "failed" and caught.value.owner is None
    assert (prepared / ADMISSION_NAME).read_bytes() == foreign
    assert not list(prepared.glob(".jarvis-activation-*"))


def test_unknown_temporary_replacement_is_retained_never_deleted(prepared, tmp_path):
    cancel, saved = threading.Event(), tmp_path / "our-saved-pending"
    context = ctx(cancel=cancel)
    pending = prepared / (".jarvis-activation-" + context.operation_id + ".pending")

    def guard(stage, write):
        if stage == "activation_pending_fsync":
            pending.rename(saved)
            pending.write_bytes(b"UNKNOWN FILE")
            pending.chmod(0o600)
            cancel.set()

    context.check_current = guard
    with pytest.raises(ActivationError) as caught:
        activate_prepared_profile(review(prepared), context)
    error = caught.value
    assert error.owner is not None and error.receipt.cleanup_pending
    assert error.receipt.status == "unknown" and error.receipt.effect_status == "not_activated"
    assert pending.read_bytes() == b"UNKNOWN FILE"
    pending.unlink()
    saved.rename(pending)
    error.owner.cleanup()
    assert error.owner.closed
    assert_no_admission(prepared)


@pytest.mark.parametrize("copy_mode", ["whole_root", "admission_only"])
def test_admission_is_not_portable_to_another_physical_root(prepared, tmp_path, copy_mode):
    activate(prepared)
    if copy_mode == "whole_root":
        other = tmp_path / "copy"
        shutil.copytree(prepared, other)
    else:
        other = tmp_path / "fresh"
        other.mkdir(mode=0o700)
        for name in (MARKER_NAME, ADMISSION_NAME):
            shutil.copy2(prepared / name, other / name)
    with pytest.raises(ActivationError, match="activation_local_admission_invalid"):
        validate_bootstrap_admission(other)
    assert validate_bootstrap_admission(prepared).state == "local_present_valid"


@pytest.mark.parametrize("name", [MARKER_NAME, ADMISSION_NAME])
def test_modified_marker_or_admission_is_not_a_bootstrap_grant(prepared, name):
    activate(prepared)
    edit_json(prepared / name, lambda value: value.update(allowed=True))
    with pytest.raises(ActivationError):
        validate_bootstrap_admission(prepared)


def test_admission_without_marker_is_orphaned_and_ordinary_profile_needs_no_admission(tmp_path):
    root = tmp_path / "ordinary"
    root.mkdir(mode=0o700)
    assert validate_bootstrap_admission(root) is None
    (root / ADMISSION_NAME).write_bytes(b"{}")
    (root / ADMISSION_NAME).chmod(0o600)
    with pytest.raises(ActivationError, match="activation_local_admission_orphaned"):
        validate_bootstrap_admission(root)


@pytest.mark.parametrize("sidecar", ["-wal", "-shm", "-journal"])
def test_cold_sidecars_are_blocked_not_recovered(prepared, sidecar):
    path = prepared / ("memory.sqlite3" + sidecar)
    path.write_bytes(b"synthetic unknown sidecar")
    path.chmod(0o600)
    before = snapshot(prepared)
    with pytest.raises(ActivationError, match="activation_cold_sqlite_unavailable"):
        review(prepared)
    assert snapshot(prepared) == before


@pytest.mark.parametrize("kind", ["provider_thread", "schedule", "route", "skill", "binding"])
def test_actual_disarms_checked_not_trusted_from_marker(prepared, kind):
    if kind == "provider_thread":
        with sqlite3.connect(prepared / "jarvis.sqlite3") as c:
            c.execute("UPDATE sessions SET provider_thread_id='do not reconnect'")
    elif kind == "schedule":
        with sqlite3.connect(prepared / "schedules.sqlite3") as c:
            c.execute("UPDATE schedules SET enabled=1,next_at=100")
    elif kind == "route":
        edit_json(prepared / "telegram/telegram.json",
                  lambda value: value["routes"]["42"].update(enabled=True))
    elif kind == "skill":
        edit_json(prepared / "profiles/skills.json", lambda value: next(
            iter(value["skills"].values())).update(enabled=True))
    else:
        edit_json(prepared / "configuration.json", lambda value: value.update(bindings={"x": "y"}))
    before = snapshot(prepared)
    with pytest.raises(ActivationError):
        review(prepared)
    assert snapshot(prepared) == before


def test_memory_corruption_fails_closed(prepared):
    with sqlite3.connect(prepared / "memory.sqlite3") as c:
        c.execute("UPDATE documents SET text='wrong hash'")
    with pytest.raises(ActivationError):
        review(prepared)
    assert_no_admission(prepared)


def test_review_limits_are_real_budgets(prepared):
    with pytest.raises(ActivationError):
        review(prepared, limits=ActivationLimits(max_total_bytes=1024))
    assert_no_admission(prepared)
    with pytest.raises(ActivationError, match="activation_limits_invalid"):
        ActivationLimits(max_total_bytes=True)


def test_minimal_restore_with_absent_optional_roles_can_activate(tmp_path, bundle):
    minimal = replace(bundle, sqlite=(), json=(bundle.json[0],),
                      absent=("state", "memory", "schedules", "skills", "telegram"))
    create_backup(minimal, target(tmp_path, "small-backup"), ctx())
    verified = verify_backup(tmp_path / "small-backup", ctx())
    restore_new(tmp_path / "small-backup", verified, target(tmp_path, "small-restored"), ctx())
    root = tmp_path / "small-restored"
    activate(root)
    assert validate_bootstrap_admission(root).state == "local_present_valid"


def test_backup_format_one_omits_local_admission_and_second_restore_requires_new_activation(
    prepared, tmp_path,
):
    activate(prepared)
    sql, docs = [], []
    for role, filename in (("state", "jarvis.sqlite3"), ("memory", "memory.sqlite3"),
                           ("schedules", "schedules.sqlite3")):
        path = prepared / filename
        data = path.read_bytes()
        sql.append(SqliteImageSource(role, data, FileIdentity.of(path.stat()), digest(data),
                                     lambda: None))
    for role, filename in (("agents", "profiles/agents.json"),
                           ("skills", "profiles/skills.json"),
                           ("telegram", "telegram/telegram.json")):
        docs.append(JsonSource(role, (prepared / filename).read_bytes(), lambda: None))
    source = SourceBundle("fixture_profile", 1, prepared, FileIdentity.of(prepared.stat()),
                          tuple(sql), tuple(docs), ())
    destination = target(tmp_path, "second-backup", forbidden_roots=(prepared,))
    create_backup(source, destination, ctx())
    path = tmp_path / "second-backup"
    manifest = json.loads((path / "manifest.json").read_bytes())
    assert manifest["format"] == "jarvis-backup/1" and manifest["excluded"] == list(EXCLUSIONS)
    admission = (prepared / ADMISSION_NAME).read_bytes()
    assert all(p.read_bytes() != admission for p in path.rglob("*") if p.is_file())
    verified = verify_backup(path, ctx())
    destination = target(tmp_path, "second-restored", forbidden_roots=(prepared,))
    restore_new(path, verified, destination, ctx())
    second = tmp_path / "second-restored"
    assert not (second / ADMISSION_NAME).exists()
    assert (second / MARKER_NAME).read_bytes() != (prepared / MARKER_NAME).read_bytes()
    with pytest.raises(ActivationError, match="profile_activation_required"):
        validate_bootstrap_admission(second)


@pytest.mark.parametrize("stage", ["activation_publication_verify", "activation_publication_fsync",
                                   "activation_completed"])
def test_cancel_after_rename_is_unknown_with_current_pair_and_no_automatic_handoff(prepared, stage):
    cancel = threading.Event()

    def guard(current, write):
        if current == stage:
            cancel.set()

    with pytest.raises(ActivationError, match="recovery_cancelled") as caught:
        activate_prepared_profile(review(prepared), ctx(guard=guard, cancel=cancel))
    error = caught.value
    assert error.receipt.status == "unknown" and not error.receipt.services_started
    assert error.owner is not None
    local = validate_bootstrap_admission(prepared)
    assert local.state == "local_present_valid"
    assert local.prior_operation_durability == "not_established"
    error.owner.cleanup()
    assert error.owner.closed and error.receipt.status == "unknown"


def test_guard_denial_performs_no_fsync_or_file_creation(prepared, monkeypatch):
    result, before = review(prepared), snapshot(prepared)

    def denied(stage, write):
        if write:
            raise ActivationError("test_authority_denied")

    def forbidden(fd):
        pytest.fail("an uncommitted operation performed even cleanup fsync")

    monkeypatch.setattr(module.os, "fsync", forbidden)
    with pytest.raises(ActivationError, match="test_authority_denied") as caught:
        activate_prepared_profile(result, ctx(guard=denied))
    assert caught.value.owner is None and snapshot(prepared) == before


def test_agent_crossrefs_are_validated_from_images(prepared):
    with sqlite3.connect(prepared / "jarvis.sqlite3") as connection:
        connection.execute("UPDATE sessions SET agent_id='absent_agent'")
    with pytest.raises(ActivationError, match="recovery_cross_reference_invalid"):
        review(prepared)
    assert_no_admission(prepared)


def test_unfinished_records_are_not_recovered_by_activation(prepared):
    with sqlite3.connect(prepared / "jarvis.sqlite3") as connection:
        connection.execute("UPDATE runs SET status='running',outcome='pending',receipt_json=NULL")
    before = snapshot(prepared)
    with pytest.raises(ActivationError, match="recovery_source_unsettled"):
        review(prepared)
    assert snapshot(prepared) == before


@pytest.mark.parametrize("entry", ["provider", "workspace", ".pending", "transport.token"])
def test_unknown_or_forbidden_entries_never_get_opened(prepared, entry, monkeypatch):
    path = prepared / entry
    path.write_bytes(b"private fixture content must not be read")
    path.chmod(0o600)
    before = snapshot(prepared)
    real_open = os.open

    def check(name, flags, mode=0o777, *, dir_fd=None):
        if dir_fd is not None and os.fspath(name) == entry:
            pytest.fail("activation opened a forbidden entry")
        return real_open(name, flags, mode, dir_fd=dir_fd)

    monkeypatch.setattr(module.os, "open", check)
    with pytest.raises(ActivationError, match="activation_profile_entries_invalid"):
        review(prepared)
    assert snapshot(prepared) == before


def test_marker_absence_is_not_silently_repaired(prepared):
    (prepared / MARKER_NAME).unlink()
    before = snapshot(prepared)
    with pytest.raises(ActivationError, match="activation_profile_entries_invalid"):
        review(prepared)
    assert snapshot(prepared) == before


@pytest.mark.parametrize("path", [MARKER_NAME, ADMISSION_NAME])
def test_bootstrap_control_file_replacement_permissions_and_hardlinks_fail_closed(
    prepared, tmp_path, path,
):
    activate(prepared)
    os.link(prepared / path, tmp_path / "extra-link")
    with pytest.raises(ActivationError):
        validate_bootstrap_admission(prepared)


def test_bootstrap_rechecks_mutation_from_callback(prepared):
    activate(prepared)
    calls = [0]

    def guard():
        calls[0] += 1
        if calls[0] == 3:
            edit_json(prepared / MARKER_NAME, lambda value: value.update(source_generation=32))

    with pytest.raises(ActivationError):
        validate_bootstrap_admission(prepared, check_current=guard)


def test_missing_context_never_creates_authority_or_effects(prepared):
    result, before = review(prepared), snapshot(prepared)
    with pytest.raises(ActivationError, match="activation_context_invalid"):
        activate_prepared_profile(result, None)
    assert snapshot(prepared) == before


def test_authority_code_preserved_without_exposing_arbitrary_exception_text(prepared):
    from jarvis_core.tools import ToolError

    def denied(stage, write):
        if write:
            raise ToolError("host_authority_required")

    with pytest.raises(ActivationError, match="host_authority_required"):
        activate_prepared_profile(review(prepared), ctx(guard=denied))
    assert_no_admission(prepared)


def test_unknown_schema_is_rejected_before_any_custom_sql_can_run(prepared):
    with sqlite3.connect(prepared / "jarvis.sqlite3") as connection:
        connection.execute("CREATE VIEW injected AS SELECT load_extension('never-loaded')")
    before = snapshot(prepared)
    with pytest.raises(ActivationError, match="snapshot_schema_unsupported"):
        review(prepared)
    assert snapshot(prepared) == before


@pytest.mark.parametrize("name", [MARKER_NAME, ADMISSION_NAME])
def test_ordinary_bootstrap_rechecks_control_presence_after_guard(tmp_path, name):
    root = tmp_path / "ordinary"
    root.mkdir(mode=0o700)
    calls = [0]

    def guard():
        calls[0] += 1
        if calls[0] == 2:
            (root / name).write_bytes(b"introduced during guard")
            (root / name).chmod(0o600)

    with pytest.raises(ActivationError, match="activation_bootstrap_changed"):
        validate_bootstrap_admission(root, check_current=guard)


@pytest.mark.parametrize("kind", ["directory", "dangling", "fifo"])
def test_marker_without_admission_is_never_opened_or_parsed(tmp_path, kind, monkeypatch):
    root = tmp_path / "blocked"
    root.mkdir(mode=0o700)
    marker = root / MARKER_NAME
    if kind == "directory":
        marker.mkdir(mode=0o700)
    elif kind == "dangling":
        marker.symlink_to(root / "absent")
    else:
        os.mkfifo(marker, 0o600)
    real_open = os.open

    def check(name, flags, mode=0o777, *, dir_fd=None):
        if dir_fd is not None and os.fspath(name) == MARKER_NAME:
            pytest.fail("unadmitted marker body was opened")
        return real_open(name, flags, mode, dir_fd=dir_fd)

    monkeypatch.setattr(module.os, "open", check)
    with pytest.raises(ActivationError, match="profile_activation_required"):
        validate_bootstrap_admission(root)


def test_cleanup_can_retry_fsync_after_its_own_confirmed_unlink(prepared, monkeypatch):
    cancel = threading.Event()
    real_fsync = os.fsync

    def guard(stage, write):
        if stage == "activation_publish":
            cancel.set()

    def fail_directory(fd):
        if stat.S_ISDIR(os.fstat(fd).st_mode):
            raise OSError("synthetic cleanup fsync failure")
        return real_fsync(fd)

    monkeypatch.setattr(module.os, "fsync", fail_directory)
    with pytest.raises(ActivationError) as caught:
        activate_prepared_profile(review(prepared), ctx(guard=guard, cancel=cancel))
    error = caught.value
    assert error.owner is not None and error.receipt.status == "unknown"
    assert error.receipt.effect_status == "not_activated" and error.receipt.cleanup_pending
    assert error.owner._pending_unlinked and os.fstat(error.owner.pending_fd).st_nlink == 0
    assert_no_admission(prepared)
    monkeypatch.setattr(module.os, "fsync", real_fsync)
    error.owner.cleanup()
    assert error.owner.closed and error.receipt.status == "unknown"


def test_unknown_publication_cleanup_can_retry_fsync_after_own_unlink(prepared, monkeypatch):
    def fail_publish(*args):
        raise OSError("synthetic missing publication ACK before rename")

    with pytest.raises(ActivationError) as caught:
        activate(prepared, publish=fail_publish)
    error = caught.value
    assert error.owner is not None
    real_fsync = os.fsync

    def fail_directory(fd):
        if stat.S_ISDIR(os.fstat(fd).st_mode):
            raise OSError("synthetic reconciliation fsync failure")
        return real_fsync(fd)

    monkeypatch.setattr(module.os, "fsync", fail_directory)
    with pytest.raises(OSError):
        error.owner.cleanup()
    assert error.owner._pending_unlinked and not error.owner.closed
    monkeypatch.setattr(module.os, "fsync", real_fsync)
    error.owner.cleanup()
    assert error.owner.closed and error.receipt.status == "unknown"


def test_name_reappearing_after_own_unlink_is_not_deleted_on_cleanup_retry(prepared, monkeypatch):
    cancel = threading.Event()
    real_fsync = os.fsync

    def guard(stage, write):
        if stage == "activation_publish":
            cancel.set()

    def fail_directory(fd):
        if stat.S_ISDIR(os.fstat(fd).st_mode):
            raise OSError("synthetic cleanup fsync failure")
        return real_fsync(fd)

    monkeypatch.setattr(module.os, "fsync", fail_directory)
    with pytest.raises(ActivationError) as caught:
        activate_prepared_profile(review(prepared), ctx(guard=guard, cancel=cancel))
    error = caught.value
    owner = error.owner
    assert owner is not None and owner._pending_unlinked
    unknown = prepared / owner.pending_name
    unknown.write_bytes(b"DO NOT DELETE UNKNOWN")
    unknown.chmod(0o600)
    monkeypatch.setattr(module.os, "fsync", real_fsync)
    try:
        with pytest.raises(ActivationError, match="activation_pending_identity_changed"):
            owner.cleanup()
        assert not owner.closed and unknown.read_bytes() == b"DO NOT DELETE UNKNOWN"
    finally:
        unknown.unlink()  # Test fixture removes its own unrelated file.
        owner.cleanup()
    assert owner.closed and error.receipt.status == "unknown"


def _fail_first_pending_metadata(monkeypatch, *, replace_name=None):
    import errno

    real_open, real_fstat = os.open, os.fstat
    captured = {}

    def opened(path, *args, **kwargs):
        fd = real_open(path, *args, **kwargs)
        if os.fspath(path).startswith(".jarvis-activation-"):
            captured.update(fd=fd, name=os.fspath(path), failed=False)
        return fd

    def failed(fd):
        if fd == captured.get("fd") and not captured["failed"]:
            captured["failed"] = True
            if replace_name is not None:
                replace_name(captured["name"])
            raise OSError(errno.EIO, "synthetic first pending fstat failure")
        return real_fstat(fd)

    monkeypatch.setattr(module.os, "open", opened)
    monkeypatch.setattr(module.os, "fstat", failed)
    return captured, real_fstat


def test_initial_pending_metadata_failure_retains_owner_then_explicit_cleanup_recovers(
    prepared, monkeypatch,
):
    result = review(prepared)
    captured, real_fstat = _fail_first_pending_metadata(monkeypatch)
    with pytest.raises(ActivationError) as caught:
        activate_prepared_profile(result, ctx())
    error, owner = caught.value, caught.value.owner
    assert owner is not None and owner.namespace_dirty and owner._pending_created
    assert owner.pending_identity is None and owner.pending_fd == captured["fd"]
    assert real_fstat(owner.pending_fd).st_nlink == 1
    assert error.receipt.status == "unknown" and error.receipt.effect_status == "not_activated"
    assert error.receipt.cleanup_pending
    assert error.receipt.cleanup_error_code == "activation_pending_metadata_unavailable"
    owner.cleanup()
    assert owner.closed and owner.pending_identity is not None
    assert error.receipt.status == "unknown"  # Reconciliation never upgrades the old receipt.
    assert_no_admission(prepared)


def test_pending_metadata_retry_does_not_adopt_unknown_replacement(prepared, tmp_path, monkeypatch):
    saved = tmp_path / "own-pending-saved"

    def replace_name(name):
        pending = prepared / name
        pending.rename(saved)
        pending.write_bytes(b"UNKNOWN NAME DO NOT REMOVE")
        pending.chmod(0o600)

    captured, _ = _fail_first_pending_metadata(monkeypatch, replace_name=replace_name)
    with pytest.raises(ActivationError) as caught:
        activate_prepared_profile(review(prepared), ctx())
    owner = caught.value.owner
    assert owner is not None and owner.pending_identity is None
    pending = prepared / captured["name"]
    try:
        with pytest.raises(ActivationError, match="activation_pending_identity_changed"):
            owner.cleanup()
        assert not owner.closed and owner.pending_identity is None
        assert pending.read_bytes() == b"UNKNOWN NAME DO NOT REMOVE"
    finally:
        pending.unlink()  # The fixture owns this unrelated replacement.
        saved.rename(pending)
        owner.cleanup()
    assert owner.closed and not pending.exists()


def test_persistent_pending_metadata_failure_retains_descriptor_until_available(
    prepared, monkeypatch,
):
    import errno

    captured, real_fstat = _fail_first_pending_metadata(monkeypatch)
    with pytest.raises(ActivationError) as caught:
        activate_prepared_profile(review(prepared), ctx())
    owner = caught.value.owner
    assert owner is not None

    def unavailable(fd):
        if fd == captured["fd"]:
            raise OSError(errno.EIO, "synthetic persistent metadata failure")
        return real_fstat(fd)

    monkeypatch.setattr(module.os, "fstat", unavailable)
    with pytest.raises(OSError):
        owner.cleanup()
    assert not owner.closed and owner.pending_identity is None and owner.namespace_dirty
    monkeypatch.setattr(module.os, "fstat", real_fstat)
    owner.cleanup()
    assert owner.closed and caught.value.receipt.status == "unknown"
    assert_no_admission(prepared)
