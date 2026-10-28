"""Calendar and durable wake contracts, using only private synthetic stores."""

import asyncio
import hashlib
import sqlite3
from datetime import UTC, datetime

import pytest

from jarvis_core.automation_schedule import CronRuleError, cron_calendar
from jarvis_core.scheduler import (
    _APPLICATION_ID,
    _SCHEMA_V1,
    DispatchResult,
    Scheduler,
    SchedulerError,
    SchedulerLimits,
    ScheduleSpec,
    ScheduleStore,
)


def ts(value):
    return datetime.fromisoformat(value).replace(tzinfo=UTC).timestamp()


class Clock:
    def __init__(self, value=None):
        self.value = ts("2026-01-01T00:00:00") if value is None else value

    def __call__(self):
        return self.value


@pytest.fixture
def clock():
    return Clock()


@pytest.fixture
def store(tmp_path, clock):
    instance = ScheduleStore(tmp_path.resolve() / "scheduler" / "jobs.sqlite3", clock=clock)
    yield instance
    instance.close()


def create(store, spec, **kwargs):
    return store.create("Prueba", "No ejecutar herramientas", "jarvis", "workspace", spec,
                        **kwargs)


async def until(predicate):
    async with asyncio.timeout(2):
        while not predicate():
            await asyncio.sleep(0.001)


@pytest.mark.parametrize("expression,start,expected", [
    ("*/15 8-10 * * MON-FRI", "2026-01-02T10:45:00", "2026-01-05T08:00:00"),
    ("5,35 9,17 * JAN-MAR *", "2026-01-01T09:05:00", "2026-01-01T09:35:00"),
    ("0 0 29 FEB *", "2026-01-01T00:00:00", "2028-02-29T00:00:00"),
    ("0 0 * * 7", "2026-01-03T00:00:00", "2026-01-04T00:00:00"),
    ("0 0 * * 0", "2026-01-03T00:00:00", "2026-01-04T00:00:00"),
    ("0 0 13 * FRI", "2026-01-01T00:00:00", "2026-01-02T00:00:00"),
    ("0 0 13 * FRI", "2026-01-12T00:00:00", "2026-01-13T00:00:00"),
    ("5/20 * * * *", "2026-01-01T00:05:00", "2026-01-01T00:25:00"),
])
def test_cron_calendar_lists_steps_names_day_or_and_leap(expression, start, expected):
    spec = ScheduleSpec("cron", cron_expression=expression)
    assert spec.first_at(ts(start)) == ts(expected)


@pytest.mark.parametrize("expression", [
    "", "* * * *", "* * * * * *", "@daily", "0 0 30 FEB *", "60 * * * *",
    "* 24 * * *", "* * 0 * *", "* * * 13 *", "* * * * 8", "*/0 * * * *",
    "*/100 * * * *", "5-3 * * * *", "5,,6 * * * *", "1/2/3 * * * *",
    "* * * * UNKNOWN", "* * * JANY *", "* * * * MON-FRI/0", "* * * * 0;echo",
])
def test_invalid_cron_is_rejected_at_admission_without_database(store, expression):
    with pytest.raises(SchedulerError, match="rule_invalid"):
        create(store, ScheduleSpec("cron", cron_expression=expression))
    assert not store.path.exists()


def test_parser_error_never_contains_source_or_executes_text():
    with pytest.raises(CronRuleError, match=r"^cron_rule_invalid$"):
        cron_calendar("$PRIVATE * * * *")


def test_cron_dst_skips_missing_wall_time_and_uses_only_first_fold():
    spring = ScheduleSpec("cron", timezone="America/New_York", cron_expression="30 2 * * *")
    assert spring.after(ts("2026-03-07T08:00:00")) == ts("2026-03-09T06:30:00")
    autumn = ScheduleSpec("cron", timezone="America/New_York", cron_expression="30 1 * * *")
    first = autumn.after(ts("2026-11-01T04:00:00"))
    assert first == ts("2026-11-01T05:30:00")
    assert autumn.after(first) == ts("2026-11-02T06:30:00")
    assert autumn.coalesce(first, ts("2026-11-01T06:15:00")) == (
        first, ts("2026-11-02T06:30:00")
    )


def test_cron_latest_due_slot_is_coalesced_after_long_sleep(store, clock):
    record = create(store, ScheduleSpec("cron", cron_expression="*/15 * * * *"), enabled=True)
    clock.value = ts("2026-01-10T12:37:00")
    (claim,) = store.claim_due()
    assert claim.scheduled_at == ts("2026-01-10T12:30:00")
    assert store.get(record.schedule_id).next_at == ts("2026-01-10T12:45:00")
    assert store.claim_due() == ()


@pytest.mark.parametrize("kind", ["interval", "heartbeat"])
def test_periodic_aliases_preserve_grid_and_do_not_reanchor_to_poll_time(kind):
    spec = ScheduleSpec(kind, at=10, interval_seconds=60)
    assert spec.first_at(0) == 10
    assert spec.coalesce(10, 199) == (190, 250)


def test_heartbeat_window_skips_night_and_retains_interval_anchor():
    spec = ScheduleSpec("heartbeat", timezone="America/Guayaquil", interval_seconds=600,
                        at=ts("2026-01-01T12:03:00"),
                        active_start="08:00", active_end="18:00")
    assert spec.first_at(0) == ts("2026-01-01T13:03:00")
    assert spec.coalesce(ts("2026-01-01T13:03:00"), ts("2026-01-01T15:07:00")) == (
        ts("2026-01-01T15:03:00"), ts("2026-01-01T15:13:00")
    )


def test_heartbeat_does_not_dispatch_a_missed_slot_outside_active_window(store, clock):
    clock.value = ts("2026-01-01T08:00:00")
    record = create(store, ScheduleSpec("heartbeat", at=clock(), interval_seconds=600,
                                      active_start="08:00", active_end="18:00"), enabled=True)
    clock.value = ts("2026-01-01T23:00:00")
    assert store.claim_due() == ()
    assert store.history() == ()
    assert store.get(record.schedule_id).next_at == ts("2026-01-02T08:00:00")
    clock.value = ts("2026-01-02T08:25:00")
    assert store.claim_due()[0].scheduled_at == ts("2026-01-02T08:20:00")


def test_manual_wake_cannot_bypass_heartbeat_active_window(store, clock):
    clock.value = ts("2026-01-01T23:00:00")
    record = create(store, ScheduleSpec("heartbeat", interval_seconds=600,
                                      active_start="08:00", active_end="18:00"), enabled=True)
    with pytest.raises(SchedulerError, match="outside_active_window"):
        store.wake(record.schedule_id, 1, event_id="night-event")
    assert store.history() == ()


def test_overnight_heartbeat_window_and_dst_grid_are_real_utc_slots():
    spec = ScheduleSpec("heartbeat", timezone="America/New_York", interval_seconds=60,
                        at=ts("2026-11-01T05:59:00"),
                        active_start="22:00", active_end="02:00")
    assert spec.coalesce(spec.at, ts("2026-11-01T06:01:45")) == (
        ts("2026-11-01T06:01:00"), ts("2026-11-01T06:02:00")
    )
    assert spec.first_at(0) == spec.at


@pytest.mark.parametrize("data", [
    {"kind": "cron", "cron_expression": "0 * * * *", "at": 5},
    {"kind": "cron", "cron_expression": []},
    {"kind": "event", "at": 5},
    {"kind": "event", "cron_expression": "* * * * *"},
    {"kind": "heartbeat", "interval_seconds": 10, "active_start": "08:00"},
    {"kind": "heartbeat", "interval_seconds": 10,
     "active_start": "18:00", "active_end": "18:00"},
    {"kind": "heartbeat", "interval_seconds": 10,
     "active_start": "8:00", "active_end": "18:00"},
    {"kind": "interval", "interval_seconds": 10,
     "active_start": "08:00", "active_end": "18:00"},
])
def test_spec_fields_do_not_leak_between_rule_kinds(data):
    with pytest.raises(SchedulerError):
        ScheduleSpec.from_dict(data)


def test_event_has_no_implicit_deadline_but_explicit_wake_is_durable(store, clock):
    record = create(store, ScheduleSpec("event"), enabled=True)
    assert record.next_at is None and store.claim_due() == ()
    claim = store.wake(record.schedule_id, 1, event_id="fixture:event-1")
    assert claim.status == "claimed" and claim.outcome == "pending"
    assert store.wake(record.schedule_id, 1, event_id="fixture:event-1") == claim
    store.begin_dispatch(claim.occurrence_id)
    receipt = store.finish(claim.occurrence_id, "completed", "known")
    assert store.clear_history(record.schedule_id) == 0
    reopened = ScheduleStore(store.path, clock=clock)
    try:
        assert reopened.wake(record.schedule_id, 1, event_id="fixture:event-1") == receipt
        assert reopened.pending() == ()
        second = reopened.wake(record.schedule_id, 1, event_id="fixture:event-2")
        assert second.occurrence_id != claim.occurrence_id
        assert second.scheduled_at != claim.scheduled_at
    finally:
        reopened.close()
    db = sqlite3.connect(store.path)
    try:
        (digest,) = db.execute(
            "SELECT event_digest FROM wake_events ORDER BY event_digest"
        ).fetchone()
        assert digest in {hashlib.sha256(f"fixture:event-{n}".encode()).hexdigest() for n in (1, 2)}
    finally:
        db.close()


def test_wake_retains_error_and_never_replays_uncertain_result(store):
    record = create(store, ScheduleSpec("event"), enabled=True)
    claim = store.wake(record.schedule_id, 1, event_id="event")
    store.begin_dispatch(claim.occurrence_id)
    receipt = store.finish(claim.occurrence_id, "interrupted", "unknown",
                           error_code="synthetic_unconfirmed")
    assert store.wake(record.schedule_id, 1, event_id="event") == receipt
    assert store.pending() == ()


def test_wake_conflicts_are_rejected_before_new_occurrence(store):
    record = create(store, ScheduleSpec("event"))
    with pytest.raises(SchedulerError, match="schedule_paused"):
        store.wake(record.schedule_id, 1, event_id="event")
    record = store.update(record.schedule_id, 1, enabled=True)
    claim = store.wake(record.schedule_id, 2, event_id="event")
    with pytest.raises(SchedulerError, match="schedule_busy"):
        store.wake(record.schedule_id, 2, event_id="other-event")
    store.update(record.schedule_id, 2, prompt="Nueva revisión")
    assert store.occurrence(claim.occurrence_id).status == "cancelled"
    with pytest.raises(SchedulerError, match="event_conflict"):
        store.wake(record.schedule_id, 3, event_id="event")
    with pytest.raises(SchedulerError, match="revision_conflict"):
        store.wake(record.schedule_id, 2, event_id="other-event")
    assert len(store.history()) == 1


def test_event_ledger_is_bounded_and_not_implicitly_evicted(tmp_path, clock):
    store = ScheduleStore(tmp_path.resolve() / "quota" / "jobs.sqlite3", clock=clock,
                          limits=SchedulerLimits(max_occurrences=1))
    try:
        record = create(store, ScheduleSpec("event"), enabled=True)
        claim = store.wake(record.schedule_id, 1, event_id="one")
        store.finish(claim.occurrence_id, "cancelled", "known")
        with pytest.raises(SchedulerError, match="capacity_exceeded"):
            store.wake(record.schedule_id, 1, event_id="two")
        assert store.wake(record.schedule_id, 1, event_id="one").status == "cancelled"
    finally:
        store.close()


def test_v1_additive_upgrade_keeps_claimed_slot_and_legacy_configuration(tmp_path, clock):
    directory = tmp_path.resolve() / "old"
    directory.mkdir(mode=0o700)
    path = directory / "jobs.sqlite3"
    db = sqlite3.connect(path)
    db.executescript(_SCHEMA_V1)
    db.execute(f"PRAGMA application_id={_APPLICATION_ID}")
    db.execute("PRAGMA user_version=1")
    db.commit()
    db.close()
    path.chmod(0o600)
    store = ScheduleStore(path, clock=clock)
    try:
        assert store.list() == ()
        record = create(store, ScheduleSpec("event"), enabled=True)
        claim = store.wake(record.schedule_id, 1, event_id="v2-event")
        assert store.recover_unfinished() == 1
        assert store.wake(record.schedule_id, 1, event_id="v2-event").status == "interrupted"
        assert store.occurrence(claim.occurrence_id).outcome == "unknown"
    finally:
        store.close()
    db = sqlite3.connect(path)
    try:
        assert db.execute("PRAGMA user_version").fetchone()[0] == 2
        assert db.execute("PRAGMA foreign_key_check").fetchall() == []
    finally:
        db.close()


async def test_runner_wake_dispatches_once_and_retains_result_after_duplicate(store):
    record = create(store, ScheduleSpec("event"), enabled=True)
    calls = []

    async def dispatch(item):
        calls.append(item)
        return DispatchResult(item.occurrence_id)

    runner = Scheduler(store, dispatch, poll_interval=60)
    runner.start()
    try:
        # Let the initial pump finish so the explicit signal is what dispatches.
        await asyncio.sleep(0.01)
        claim = store.wake(record.schedule_id, 1, event_id="host-event")
        assert runner.wake() is True
        assert runner.wake() is False
        await until(lambda: store.occurrence(claim.occurrence_id).status == "completed")
        assert len(calls) == 1
        assert store.wake(record.schedule_id, 1, event_id="host-event").status == "completed"
        runner.wake()
        await asyncio.sleep(0.01)
        assert len(calls) == 1
    finally:
        await runner.aclose()


async def test_manual_wake_does_not_consume_or_collide_with_calendar_slot(store, clock):
    record = create(store, ScheduleSpec("interval", at=clock(), interval_seconds=60), enabled=True)
    claim = store.wake(record.schedule_id, 1, event_id="manual")
    store.finish(claim.occurrence_id, "cancelled", "known")
    (calendar,) = store.claim_due()
    assert calendar.scheduled_at == clock()
    assert calendar.occurrence_id != claim.occurrence_id


async def test_suspend_cancels_unconfirmed_run_retains_lease_and_resume_coalesces(store, clock):
    record = create(store, ScheduleSpec("interval", at=clock(), interval_seconds=60), enabled=True)
    entered = asyncio.Event()
    calls = []

    async def dispatch(item):
        calls.append(item)
        entered.set()
        if len(calls) == 1:
            await asyncio.Event().wait()
        return DispatchResult(item.occurrence_id)

    runner = Scheduler(store, dispatch, poll_interval=60)
    other = Scheduler(store, dispatch)
    runner.start()
    try:
        await asyncio.wait_for(entered.wait(), 2)
        await runner.suspend()
        assert runner.running and runner.suspended and runner.active_count == 0
        with pytest.raises(SchedulerError, match="runner_in_use"):
            other.start()
        assert store.history()[0].status == "cancelled"
        assert store.history()[0].outcome == "unknown"
        clock.value += 365
        runner.wake()
        await asyncio.sleep(0.01)
        assert len(calls) == 1
        assert runner.resume() is True
        await until(lambda: len(calls) == 2 and runner.active_count == 0)
        assert calls[-1].scheduled_at == clock() - 5
        assert store.get(record.schedule_id).next_at == clock() + 55
    finally:
        await runner.aclose()
        await other.aclose()


async def test_cancel_suppressed_by_callback_is_not_late_success(store):
    record = create(store, ScheduleSpec("event"), enabled=True)
    entered = asyncio.Event()

    async def dispatch(item):
        entered.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            return DispatchResult(item.occurrence_id)

    runner = Scheduler(store, dispatch, poll_interval=60)
    runner.start()
    try:
        await asyncio.sleep(0.01)
        claim = store.wake(record.schedule_id, 1, event_id="event")
        runner.wake()
        await asyncio.wait_for(entered.wait(), 2)
        receipt = await runner.cancel_occurrence(claim.occurrence_id)
        assert receipt.status == "cancelled" and receipt.outcome == "unknown"
        assert await runner.cancel_occurrence(claim.occurrence_id) == receipt
    finally:
        await runner.aclose()


async def test_suspend_unsettled_callback_retains_lease_until_explicit_resume(store):
    create(store, ScheduleSpec("once", at=store.clock()), enabled=True)
    entered, release = asyncio.Event(), asyncio.Event()

    async def dispatch(item):
        entered.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            await release.wait()
            return DispatchResult(item.occurrence_id)

    runner = Scheduler(store, dispatch, close_timeout=0.01)
    other = Scheduler(store, dispatch)
    runner.start()
    try:
        await asyncio.wait_for(entered.wait(), 2)
        with pytest.raises(SchedulerError, match="cleanup_incomplete"):
            await runner.suspend()
        assert runner.suspended and runner.active_count == 1
        with pytest.raises(SchedulerError, match="cleanup_incomplete"):
            runner.resume()
        with pytest.raises(SchedulerError, match="runner_in_use"):
            other.start()
        release.set()
        await until(lambda: runner.active_count == 0)
        assert store.history()[0].status == "cancelled"
        assert runner.resume()
    finally:
        release.set()
        await runner.aclose()
        await other.aclose()
