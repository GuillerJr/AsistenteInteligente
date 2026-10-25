"""Actual private SQLite, real concurrency and async dispatch with synthetic jobs.

No owner account, existing automation, real model or effect approval is used.
"""

import asyncio
import json
import math
import os
import sqlite3
import stat
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest

from jarvis_core.scheduler import (
    DispatchResult,
    Scheduler,
    SchedulerError,
    SchedulerLimits,
    ScheduleSpec,
    ScheduleStore,
)


class Clock:
    def __init__(self, value=1_790_000_000.0):
        self.value = value

    def __call__(self):
        return self.value


@pytest.fixture
def clock():
    return Clock()


@pytest.fixture
def store(tmp_path, clock):
    value = ScheduleStore(tmp_path.resolve() / "scheduler" / "jobs.sqlite3", clock=clock)
    yield value
    value.close()


def job(store, spec=None, *, enabled=False, **kwargs):
    return store.create(
        "Prueba",
        "Consulta sintética sin acciones",
        "jarvis",
        "workspace_a",
        spec or ScheduleSpec("once", at=store.clock()),
        enabled=enabled,
        **kwargs,
    )


def instant(value):
    return datetime.fromisoformat(value).replace(tzinfo=UTC).timestamp()


async def until(predicate, *, timeout=2):
    async with asyncio.timeout(timeout):
        while not predicate():
            await asyncio.sleep(0.005)


def test_private_persistence_disabled_creation_and_immutable_snapshots(store, clock):
    assert not store.path.exists()
    record = job(store)
    assert UUID(record.schedule_id).version == 4
    assert record.revision == 1 and not record.enabled
    assert store.claim_due() == ()
    assert stat.S_IMODE(store.path.parent.stat().st_mode) == 0o700
    assert stat.S_IMODE(store.path.stat().st_mode) == 0o600
    reopened = ScheduleStore(store.path, clock=clock)
    try:
        assert reopened.get(record.schedule_id) == record
        assert reopened.history(record.schedule_id) == ()
        changed = reopened.update(record.schedule_id, 1, enabled=True, prompt="Texto nuevo")
        assert changed.revision == 2 and changed.prompt == "Texto nuevo"
        (claim,) = store.claim_due()
        assert claim.prompt == "Texto nuevo" and claim.schedule_revision == 2
        assert UUID(claim.occurrence_id).version == 4
        assert claim.status == "claimed" and claim.outcome == "pending"
        assert store.get(record.schedule_id).next_at is None
        assert not store.get(record.schedule_id).enabled
        assert store.claim_due() == ()
    finally:
        reopened.close()


def test_cas_rejects_stale_edits_and_removal_without_lost_fields(store):
    record = job(store)
    updated = store.update(record.schedule_id, 1, title="Título revisado")
    with pytest.raises(SchedulerError, match="revision_conflict"):
        store.update(record.schedule_id, 1, prompt="No sobreescribir")
    with pytest.raises(SchedulerError, match="revision_conflict"):
        store.remove(record.schedule_id, 1)
    assert store.get(record.schedule_id) == updated
    assert store.remove(record.schedule_id, 2)
    assert not store.remove(record.schedule_id, 2)
    with pytest.raises(SchedulerError, match="not_found"):
        store.get(record.schedule_id)


def test_once_claim_survives_restart_and_recovery_does_not_replay(store, clock):
    record = job(store, enabled=True)
    (occurrence,) = store.claim_due()
    dispatched = store.begin_dispatch(occurrence.occurrence_id)
    assert dispatched.status == "running"
    run_id = occurrence.occurrence_id
    store.attach_run(occurrence.occurrence_id, run_id)
    store.close()
    reopened = ScheduleStore(store.path, clock=clock)
    try:
        assert reopened.claim_due() == ()
        assert reopened.history()[0].status == "running"
        assert reopened.recover_unfinished() == 1
        assert reopened.recover_unfinished() == 0
        (final,) = reopened.history(record.schedule_id)
        assert final.status == "interrupted" and final.outcome == "unknown"
        assert final.run_id == run_id
        assert reopened.claim_due() == ()
        with pytest.raises(SchedulerError, match="finished_once"):
            reopened.update(record.schedule_id, 1, enabled=True)
    finally:
        reopened.close()


def test_two_real_store_instances_claim_each_occurrence_once(store, clock):
    for _ in range(10):
        job(store, enabled=True)
    other = ScheduleStore(store.path, clock=clock)
    other.list()  # initialize outside the race; contention is on actual BEGIN IMMEDIATE.
    try:
        with ThreadPoolExecutor(max_workers=2) as executor:
            results = tuple(executor.map(lambda item: item.claim_due(limit=10), (store, other)))
        all_claims = [claim for page in results for claim in page]
        assert len(all_claims) == len({item.occurrence_id for item in all_claims}) == 10
        assert len({(item.schedule_id, item.scheduled_at) for item in all_claims}) == 10
        assert len(store.history()) == 10
    finally:
        other.close()


def test_interval_coalesces_missed_slots_preserving_anchor_and_single_active(store, clock):
    record = job(
        store, ScheduleSpec("interval", at=clock() + 10, interval_seconds=10), enabled=True
    )
    assert store.claim_due() == ()
    clock.value += 97
    (occurrence,) = store.claim_due()
    assert occurrence.scheduled_at == clock() - 7
    assert store.get(record.schedule_id).next_at == clock() + 3
    clock.value += 100
    assert (
        store.claim_due() == ()
    )  # one job cannot overlap itself, even with extra worker capacity.
    store.begin_dispatch(occurrence.occurrence_id)
    store.finish(occurrence.occurrence_id, "failed", "unknown", error_code="synthetic_timeout")
    (later,) = store.claim_due()
    assert later.scheduled_at == clock() - 7
    assert later.occurrence_id != occurrence.occurrence_id
    assert store.get(record.schedule_id).next_at == clock() + 3


def test_daily_timezone_and_missed_days_coalesce_without_burst(store, clock):
    clock.value = instant("2026-01-01T12:00:00")
    record = job(
        store, ScheduleSpec("daily", timezone="America/Guayaquil", local_time="08:30"), enabled=True
    )
    assert record.next_at == instant("2026-01-01T13:30:00")
    clock.value = instant("2026-01-06T15:00:00")
    (occurrence,) = store.claim_due()
    assert occurrence.scheduled_at == instant("2026-01-06T13:30:00")
    assert store.get(record.schedule_id).next_at == instant("2026-01-07T13:30:00")
    assert store.claim_due() == ()


def test_nonexistent_daily_dst_wall_time_is_skipped():
    spec = ScheduleSpec("daily", timezone="America/New_York", local_time="02:30")
    assert spec.after(instant("2026-03-07T08:00:00")) == instant("2026-03-09T06:30:00")


def test_ambiguous_daily_dst_wall_time_uses_first_fold_only():
    spec = ScheduleSpec("daily", timezone="America/New_York", local_time="01:30")
    first = spec.after(instant("2026-11-01T04:00:00"))
    assert first == instant("2026-11-01T05:30:00")
    assert spec.after(first) == instant("2026-11-02T06:30:00")
    # Between the two folds, the first is due; it does not queue the second.
    due, following = spec.coalesce(first, instant("2026-11-01T06:00:00"))
    assert due == first and following == instant("2026-11-02T06:30:00")


@pytest.mark.parametrize("change", ["pause", "edit", "remove"])
def test_owner_change_before_dispatch_invalidates_claim_without_execution(store, change):
    record = job(
        store, ScheduleSpec("interval", interval_seconds=1, at=store.clock()), enabled=True
    )
    (occurrence,) = store.claim_due()
    if change == "remove":
        store.remove(record.schedule_id, 1)
    else:
        store.update(
            record.schedule_id,
            1,
            **({"enabled": False} if change == "pause" else {"prompt": "Texto cambiado"}),
        )
    assert store.begin_dispatch(occurrence.occurrence_id) is None
    (result,) = store.history(record.schedule_id)
    assert result.status == "cancelled" and result.outcome == "known"


def test_edit_after_dispatch_does_not_rewrite_running_snapshot_or_claim_completion(store):
    record = job(store, enabled=True)
    (occurrence,) = store.claim_due()
    store.begin_dispatch(occurrence.occurrence_id)
    store.update(record.schedule_id, 1, prompt="Nuevo trabajo", enabled=False)
    (existing,) = store.history(record.schedule_id)
    assert existing.prompt == record.prompt and existing.status == "running"
    store.finish(existing.occurrence_id, "completed", "known")
    assert store.history(record.schedule_id)[0].prompt == record.prompt


def test_run_attachment_idempotence_and_terminal_receipts(store):
    record = job(store, enabled=True)
    (claim,) = store.claim_due()
    run_id = uuid4().hex
    with pytest.raises(SchedulerError, match="occurrence_conflict"):
        store.attach_run(claim.occurrence_id, run_id)
    store.begin_dispatch(claim.occurrence_id)
    store.attach_run(claim.occurrence_id, run_id)
    store.attach_run(claim.occurrence_id, run_id)
    with pytest.raises(SchedulerError, match="occurrence_conflict"):
        store.attach_run(claim.occurrence_id, uuid4().hex)
    receipt = store.finish(claim.occurrence_id, "completed", "known")
    assert store.finish(claim.occurrence_id, "completed", "known") == receipt
    with pytest.raises(SchedulerError, match="occurrence_conflict"):
        store.finish(claim.occurrence_id, "failed", "unknown")
    assert store.history(record.schedule_id)[0].run_id == run_id


def test_explicit_reschedule_cannot_replay_an_already_claimed_once_slot(store, clock):
    record = job(store, enabled=True)
    (claim,) = store.claim_due()
    store.begin_dispatch(claim.occurrence_id)
    store.finish(claim.occurrence_id, "failed", "unknown")
    with pytest.raises(SchedulerError, match="slot_already_claimed"):
        store.update(record.schedule_id, 1, spec=ScheduleSpec("once", at=clock()), enabled=True)
    assert store.claim_due() == ()
    changed = store.update(
        record.schedule_id, 1, spec=ScheduleSpec("once", at=clock() + 10), enabled=True
    )
    assert changed.next_at == clock() + 10


@pytest.mark.parametrize(
    "field,value",
    [
        ("spec_json", '{"kind":"once","kind":"daily","local_time":"09:00"}'),
        ("spec_json", "{"),
        ("spec_json", json.dumps({"kind": "once", "at": 1, "extra": True})),
        ("prompt", "x" * 16001),
        ("agent_id", "../other"),
        ("title", ""),
    ],
)
def test_corrupt_persisted_job_metadata_is_not_dispatched(store, field, value):
    record = job(store, enabled=True)
    # Only this disposable database; column identifiers come from the parametrization.
    connection = sqlite3.connect(store.path)
    connection.execute(
        f"UPDATE schedules SET {field}=? WHERE schedule_id=?", (value, record.schedule_id)
    )
    connection.commit()
    connection.close()
    with pytest.raises(SchedulerError, match="database_invalid"):
        store.claim_due()
    assert store.history() == ()


def test_limits_fail_without_eviction_and_explicit_remove_releases_configuration(tmp_path, clock):
    store = ScheduleStore(
        tmp_path.resolve() / "small" / "jobs.sqlite3",
        clock=clock,
        limits=SchedulerLimits(max_schedules=1, max_occurrences=1),
    )
    try:
        first = job(store, enabled=True)
        with pytest.raises(SchedulerError, match="capacity_exceeded"):
            job(store)
        (occurrence,) = store.claim_due()
        assert store.remove(first.schedule_id, 1)
        second = job(store, enabled=True)
        with pytest.raises(SchedulerError, match="capacity_exceeded"):
            store.claim_due()
        assert store.get(second.schedule_id).next_at == clock()
        # An active occurrence is never discarded by explicit terminal-history cleanup.
        assert (
            store.clear_history(first.schedule_id) == 1
        )  # remove cancelled the undispatched claim.
        assert store.history(first.schedule_id) == ()
        (new_claim,) = store.claim_due()
        assert new_claim.schedule_id == second.schedule_id
        assert occurrence.occurrence_id != new_claim.occurrence_id
    finally:
        store.close()


@pytest.mark.parametrize(
    "data",
    [
        {"kind": "cron"},
        {"kind": False},
        {"kind": "once"},
        {"kind": "once", "at": math.nan},
        {"kind": "once", "at": True},
        {"kind": "once", "at": -1},
        {"kind": "once", "at": 1e30},
        {"kind": "once", "at": 1, "timezone": "../UTC"},
        {"kind": "once", "at": 1, "timezone": "Not/A_Zone"},
        {"kind": "interval", "interval_seconds": True},
        {"kind": "interval", "interval_seconds": 0},
        {"kind": "interval", "interval_seconds": 31_536_001},
        {"kind": "daily", "local_time": "24:00"},
        {"kind": "daily", "local_time": "2:30"},
        {"kind": "daily", "local_time": "09:00", "at": 10},
        {"kind": "daily", "local_time": "09:00", "permissions": "allow"},
    ],
)
def test_invalid_rule_fields_are_rejected_before_persistence(data):
    with pytest.raises(SchedulerError):
        ScheduleSpec.from_dict(data)


@pytest.mark.parametrize(
    "field,value",
    [
        ("title", ""),
        ("title", "x" * 513),
        ("prompt", "x" * 16001),
        ("prompt", "secret\0suffix"),
        ("prompt", "\ud800"),
        ("agent_id", "../other"),
        ("workspace_id", "other/path"),
        ("enabled", 1),
    ],
)
def test_invalid_job_metadata_never_creates_database(store, field, value):
    fields = {
        "title": "Prueba",
        "prompt": "Texto",
        "agent_id": "jarvis",
        "workspace_id": "workspace",
        "spec": ScheduleSpec("once", at=store.clock()),
    }
    fields[field] = value
    with pytest.raises(SchedulerError):
        store.create(**fields)
    assert not store.path.exists()


@pytest.mark.parametrize("kind", ["symlink", "hardlink", "fifo", "public"])
def test_unsafe_database_files_fail_closed_without_touching_target(tmp_path, clock, kind):
    directory = tmp_path.resolve() / "profile"
    directory.mkdir(mode=0o700)
    path = directory / "jobs.sqlite3"
    outside = tmp_path / "outside"
    outside.write_text("private synthetic sentinel")
    outside.chmod(0o600)
    if kind == "symlink":
        path.symlink_to(outside)
    elif kind == "hardlink":
        os.link(outside, path)
    elif kind == "fifo":
        os.mkfifo(path)
    else:
        path.touch(mode=0o644)
        path.chmod(0o644)
    store = ScheduleStore(path, clock=clock)
    try:
        with pytest.raises(SchedulerError):
            store.list()
        assert outside.read_text() == "private synthetic sentinel"
    finally:
        store.close()


@pytest.mark.parametrize("suffix", ["-journal", "-wal", "-shm"])
def test_unsafe_sqlite_sidecars_are_rejected(store, tmp_path, suffix):
    record = job(store)
    outside = tmp_path / "outside"
    outside.write_bytes(b"synthetic")
    outside.chmod(0o600)
    store.path.with_name(store.path.name + suffix).symlink_to(outside)
    with pytest.raises(SchedulerError):
        store.get(record.schedule_id)
    assert outside.read_bytes() == b"synthetic"


def test_directory_and_file_retargeting_do_not_follow_new_identity(store, tmp_path):
    record = job(store)
    old = store.path.with_name("old.sqlite3")
    store.path.rename(old)
    store.path.write_bytes(b"do not open")
    store.path.chmod(0o600)
    with pytest.raises(SchedulerError):
        store.get(record.schedule_id)
    store.path.unlink()
    old.rename(store.path)
    original_directory = store.path.parent
    moved = original_directory.with_name("moved-profile")
    original_directory.rename(moved)
    outside = tmp_path.resolve() / "outside-profile"
    outside.mkdir(mode=0o700)
    original_directory.symlink_to(outside, target_is_directory=True)
    with pytest.raises(SchedulerError):
        store.list()
    assert list(outside.iterdir()) == []


def test_foreign_or_corrupted_sqlite_is_not_treated_as_a_job_store(tmp_path):
    directory = tmp_path.resolve() / "profile"
    directory.mkdir(mode=0o700)
    path = directory / "foreign.sqlite3"
    connection = sqlite3.connect(path)
    connection.execute("CREATE TABLE foreign_data(value TEXT)")
    connection.close()
    path.chmod(0o600)
    store = ScheduleStore(path)
    try:
        with pytest.raises(SchedulerError, match="database_invalid"):
            store.list()
        path.write_bytes(b"corrupt")
        with pytest.raises(SchedulerError):
            store.list()
    finally:
        store.close()


def test_listing_history_and_claim_limits_are_checked(store):
    for limit, offset in ((0, 0), (101, 0), (True, 0), (1, -1), (1, True)):
        with pytest.raises(SchedulerError, match="page_invalid"):
            store.list(limit=limit, offset=offset)
        with pytest.raises(SchedulerError, match="page_invalid"):
            store.history(limit=limit, offset=offset)
    with pytest.raises(SchedulerError):
        store.claim_due(now=math.inf)
    assert not store.path.exists()


async def test_only_explicit_start_dispatches_and_callback_can_attach_runtime_identity(store):
    record = job(store, enabled=True)
    calls = []

    async def dispatch(occurrence):
        calls.append(occurrence)
        store.attach_run(occurrence.occurrence_id, occurrence.occurrence_id)
        return DispatchResult(occurrence.occurrence_id)

    runner = Scheduler(store, dispatch, poll_interval=0.01)
    await asyncio.sleep(0.025)
    assert calls == [] and store.history() == ()
    assert runner.start() and not runner.start()
    try:
        await until(lambda: bool(store.history()) and store.history()[0].status == "completed")
        assert len(calls) == 1
        assert calls[0].agent_id == record.agent_id and calls[0].workspace_id == record.workspace_id
        assert store.history()[0].run_id == calls[0].occurrence_id
        await asyncio.sleep(0.025)
        assert len(calls) == 1
    finally:
        await runner.aclose()
    assert not runner.running and runner.active_count == 0


async def test_paused_job_during_poll_wait_never_dispatches(store, clock):
    record = job(
        store, ScheduleSpec("interval", at=clock() + 10, interval_seconds=10), enabled=True
    )
    calls = []

    async def dispatch(item):
        calls.append(item)
        return DispatchResult(item.occurrence_id)

    runner = Scheduler(store, dispatch, poll_interval=0.01)
    runner.start()
    try:
        store.update(record.schedule_id, 1, enabled=False)
        clock.value += 100
        await asyncio.sleep(0.04)
        assert calls == [] and store.history() == ()
    finally:
        await runner.aclose()


async def test_single_runner_lease_blocks_live_recovery_and_other_runner(store, clock):
    other = ScheduleStore(store.path, clock=clock)

    async def dispatch(occurrence):
        return DispatchResult(occurrence.occurrence_id)

    first, second = Scheduler(store, dispatch), Scheduler(other, dispatch)
    first.start()
    try:
        with pytest.raises(SchedulerError, match="runner_in_use"):
            second.start()
        with pytest.raises(SchedulerError, match="runner_in_use"):
            other.recover_unfinished()
    finally:
        await first.aclose()
    try:
        assert second.start()
    finally:
        await second.aclose()
        other.close()


@pytest.mark.parametrize("mode", ["raise", "timeout", "invalid", "suppress_timeout"])
async def test_failed_or_expired_dispatch_is_unknown_and_not_replayed(store, mode):
    job(store, enabled=True)
    calls = []

    async def dispatch(item):
        calls.append(item)
        if mode == "raise":
            raise RuntimeError("PRIVATE ERROR MUST NOT LEAK")
        if mode == "invalid":
            return {"status": "completed"}
        if mode == "suppress_timeout":
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                return DispatchResult(item.occurrence_id)
        await asyncio.Event().wait()

    runner = Scheduler(store, dispatch, poll_interval=0.01, dispatch_timeout=0.025)
    runner.start()
    try:
        await until(lambda: bool(store.history()) and store.history()[0].status == "failed")
        (receipt,) = store.history()
        assert receipt.outcome == "unknown"
        assert "PRIVATE" not in (receipt.error_code or "")
        await asyncio.sleep(0.03)
        assert len(calls) == 1
    finally:
        await runner.aclose()


async def test_cancelled_dispatch_records_unknown_and_waits_for_cleanup(store):
    job(store, enabled=True)
    entered = asyncio.Event()

    async def dispatch(occurrence):
        store.attach_run(occurrence.occurrence_id, occurrence.occurrence_id)
        entered.set()
        await asyncio.Event().wait()

    runner = Scheduler(store, dispatch, poll_interval=0.01)
    runner.start()
    await asyncio.wait_for(entered.wait(), 2)
    await runner.aclose()
    (receipt,) = store.history()
    assert receipt.status == "cancelled" and receipt.outcome == "unknown"
    assert receipt.run_id == receipt.occurrence_id
    assert runner.active_count == 0


async def test_uncooperative_callback_retains_lease_until_it_settles(store, clock):
    job(store, enabled=True)
    entered, cancelled, release = asyncio.Event(), asyncio.Event(), asyncio.Event()

    async def dispatch(occurrence):
        entered.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            cancelled.set()
            await release.wait()
            return DispatchResult(occurrence.occurrence_id)

    runner = Scheduler(store, dispatch, poll_interval=0.01, close_timeout=0.02)
    runner.start()
    other = ScheduleStore(store.path, clock=clock)
    try:
        await asyncio.wait_for(entered.wait(), 2)
        with pytest.raises(SchedulerError, match="cleanup_incomplete"):
            await runner.aclose()
        assert cancelled.is_set()
        with pytest.raises(SchedulerError, match="runner_in_use"):
            other.recover_unfinished()
        release.set()
        await until(lambda: runner.active_count == 0)
        await runner.aclose()
        (receipt,) = store.history()
        assert receipt.status == "cancelled" and receipt.outcome == "unknown"
        assert other.recover_unfinished() == 0
    finally:
        release.set()
        await runner.aclose()
        other.close()


def test_serialized_spec_contains_only_reviewed_schedule_fields():
    spec = ScheduleSpec.from_dict(
        {"kind": "daily", "timezone": "America/Guayaquil", "local_time": "09:30"}
    )
    encoded = json.dumps(
        {"kind": spec.kind, "timezone": spec.timezone, "local_time": spec.local_time}
    )
    assert ScheduleSpec.from_dict(json.loads(encoded)) == spec
