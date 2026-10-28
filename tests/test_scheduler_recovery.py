"""Cold v1/v2 scheduler inspection and restoration preserve admission receipts."""

import hashlib
import json
import sqlite3
import threading
import time
from uuid import uuid4

import pytest

from jarvis_core.doctor import DoctorContext, _sqlite_check
from jarvis_core.recovery import RecoveryContext, RecoveryLimits, _transform_sqlite
from jarvis_core.recovery_sqlite import SnapshotError, inspect_sqlite_snapshot
from jarvis_core.scheduler import (
    _APPLICATION_ID,
    _SCHEMA,
    _SCHEMA_V1,
    SchedulerError,
    ScheduleSpec,
    ScheduleStore,
)

SCHEDULE = "a" * 32
OCCURRENCE = "b" * 32
EVENT_HASH = hashlib.sha256(b"synthetic-event").hexdigest()


def image(version=2, *, rule=None, mutation=None):
    db = sqlite3.connect(":memory:")
    try:
        db.executescript(_SCHEMA if version == 2 else _SCHEMA_V1)
        db.execute(f"PRAGMA application_id={_APPLICATION_ID}")
        db.execute(f"PRAGMA user_version={version}")
        db.execute(
            "INSERT INTO schedules VALUES(?,?,?,?,?,?,1,1,NULL,1,1)",
            (SCHEDULE, "Sintética", "Sin herramientas", "jarvis", "workspace",
             json.dumps(rule or {"kind": "event"} if version == 2
                        else rule or {"kind": "once", "at": 100})),
        )
        db.execute(
            "INSERT INTO occurrences VALUES(?,?,1,'Sintética','Sin herramientas',"
            "'jarvis','workspace',1,1,'claimed','pending',NULL,NULL)",
            (OCCURRENCE, SCHEDULE),
        )
        if version == 2:
            db.execute("INSERT INTO wake_events VALUES(?,?,1,?)",
                       (SCHEDULE, EVENT_HASH, OCCURRENCE))
        if mutation:
            mutation(db)
        db.commit()
        return db.serialize()
    finally:
        db.close()


def inspect(data):
    return inspect_sqlite_snapshot("schedules", data, check_current=lambda: None)


@pytest.mark.parametrize("version", [1, 2])
def test_cold_inspection_accepts_both_exact_schemas_without_source_vfs(version, monkeypatch):
    data = image(version)
    before = hashlib.sha256(data).hexdigest()
    connect = sqlite3.connect
    opened = []

    def only_memory(path, *args, **kwargs):
        assert path == ":memory:"
        opened.append(path)
        return connect(path, *args, **kwargs)

    monkeypatch.setattr(sqlite3, "connect", only_memory)
    result = inspect(data)
    assert result.user_version == version
    assert result.counts["unsettled_occurrences"] == 1
    assert result.counts.get("wake_events", 0) == (1 if version == 2 else 0)
    assert opened and set(opened) == {":memory:"}
    assert hashlib.sha256(data).hexdigest() == before


@pytest.mark.parametrize("rule", [
    {"kind": "cron", "cron_expression": "0 9 * * MON-FRI", "timezone": "Europe/Madrid"},
    {"kind": "heartbeat", "interval_seconds": 600,
     "active_start": "22:00", "active_end": "06:00", "timezone": "America/Guayaquil"},
    {"kind": "event"},
])
def test_v2_portable_rules_are_validated_without_loading_zone_files(rule):
    assert inspect(image(rule=rule)).counts["schedules"] == 1


@pytest.mark.parametrize("rule", [
    {"kind": "cron", "cron_expression": "0 9 30 FEB *"},
    {"kind": "cron", "cron_expression": ["* * * * *"]},
    {"kind": "event", "at": 1},
    {"kind": "heartbeat", "interval_seconds": True},
    {"kind": "heartbeat", "interval_seconds": 600,
     "active_start": "22:00", "active_end": "22:00"},
])
def test_invalid_v2_rule_does_not_pass_portable_validation(rule):
    with pytest.raises(SnapshotError, match="snapshot_data_invalid"):
        inspect(image(rule=rule))


@pytest.mark.parametrize("mutation", [
    lambda db: db.execute("UPDATE wake_events SET event_digest='not-a-digest'"),
    lambda db: db.execute("UPDATE wake_events SET schedule_id=?", ("d" * 32,)),
    lambda db: db.execute("UPDATE wake_events SET schedule_revision=2"),
    lambda db: db.execute("UPDATE wake_events SET occurrence_id=?", ("e" * 32,)),
    lambda db: db.execute("UPDATE occurrences SET scheduled_at=-1"),
    lambda db: db.execute("UPDATE schedules SET revision=-1"),
])
def test_tampered_wake_ledger_or_snapshot_is_rejected(mutation):
    with pytest.raises(SnapshotError):
        inspect(image(mutation=mutation))


def test_schema_v1_does_not_accept_an_unversioned_wake_table():
    with pytest.raises(SnapshotError, match="schema_unsupported"):
        inspect(image(1, mutation=lambda db: db.execute("CREATE TABLE wake_events (x)")))


@pytest.mark.parametrize("version", [1, 2])
def test_doctor_admits_v1_v2_counts_without_upgrading_original(version):
    data = image(version)
    context = DoctorContext(lambda *_args, **_kwargs: None, threading.Event(),
                            deadline=time.monotonic() + 10)
    result = _sqlite_check("schedules", data, context, None)
    assert result[0] == _APPLICATION_ID and result[1] == version
    assert dict(result[2])["occurrences"] == 1
    assert dict(result[2]).get("wake_events", 0) == (version == 2)
    assert inspect(data).user_version == version


def test_restored_wake_receipt_is_interrupted_disarmed_and_never_replayed(tmp_path):
    original = image()
    context = RecoveryContext(uuid4().hex, "synthetic_profile", 1, lambda *_: None,
                              threading.Event(), time.monotonic() + 10)
    restored = _transform_sqlite("schedules", original, context, RecoveryLimits(), restoring=True)
    assert inspect(original).counts["unsettled_occurrences"] == 1
    assert inspect(restored).counts["unsettled_occurrences"] == 0
    assert inspect(restored).counts["wake_events"] == 1
    root = tmp_path.resolve() / "restored"
    root.mkdir(mode=0o700)
    path = root / "schedules.sqlite3"
    path.write_bytes(restored)
    path.chmod(0o600)
    store = ScheduleStore(path)
    try:
        receipt = store.occurrence(OCCURRENCE)
        assert receipt.status == "interrupted" and receipt.outcome == "unknown"
        assert receipt.error_code == "restore_interrupted_no_replay"
        assert not store.get(SCHEDULE).enabled
        assert store.pending() == store.claim_due() == ()
        assert store.wake(SCHEDULE, 1, event_id="synthetic-event") == receipt
        store.update(SCHEDULE, 1, enabled=True)
        with pytest.raises(SchedulerError, match="event_conflict"):
            store.wake(SCHEDULE, 2, event_id="synthetic-event")
        assert len(store.history()) == 1
        fresh = store.wake(SCHEDULE, 2, event_id="new-synthetic-event")
        assert fresh.occurrence_id != OCCURRENCE
        assert store.get(SCHEDULE).spec == ScheduleSpec("event")
    finally:
        store.close()
