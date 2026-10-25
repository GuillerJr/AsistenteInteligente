"""Own durable scheduling, with explicit activation and no implicit tool consent.

The host supplies agent/workspace identities and routes dispatch to its existing
runtime. Constructing, configuring or listing this store never invokes a model.
Only ``Scheduler.start`` acquires the one-runner lease and starts polling. Every
claim advances its slot in the same transaction; recovery records uncertainty
instead of replaying a claimed occurrence. Pausing/editing cancels undispatched
claims; an already dispatched occurrence retains its immutable reviewed snapshot.

Times are UTC epoch seconds. Daily rules also retain an IANA zone and HH:MM.
Nonexistent DST wall times are skipped; ambiguous times use the first fold only.
Missed interval/daily slots coalesce to the latest due slot, never a backlog burst.
These explicit rules are not an arbitrary cron/RRULE parser.
"""

from __future__ import annotations

import asyncio
import fcntl
import json
import math
import os
import re
import sqlite3
import stat
import threading
import time
from collections.abc import Awaitable, Callable, Iterator
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from datetime import UTC, date, datetime, timedelta
from datetime import time as wall_time
from pathlib import Path
from uuid import uuid4
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from jarvis_core.state import StateError, ensure_private_directory

_APPLICATION_ID = 0x4A525343  # JRSC, independent of conversation/memory formats.
_SCHEMA_VERSION = 1
_ACTIVE = {"claimed", "running"}
_TERMINAL = {"completed", "failed", "cancelled", "interrupted"}
_MAX_TIMESTAMP = 253402214400.0  # Leaves room for next-date arithmetic in year 9999.
_SCHEMA = """
CREATE TABLE schedules (
 schedule_id TEXT PRIMARY KEY, title TEXT NOT NULL, prompt TEXT NOT NULL,
 agent_id TEXT NOT NULL, workspace_id TEXT NOT NULL, spec_json TEXT NOT NULL,
 enabled INTEGER NOT NULL CHECK(enabled IN (0,1)), revision INTEGER NOT NULL,
 next_at REAL, created_at REAL NOT NULL, updated_at REAL NOT NULL
);
CREATE INDEX schedules_due ON schedules(enabled,next_at,schedule_id);
CREATE TABLE occurrences (
 occurrence_id TEXT PRIMARY KEY, schedule_id TEXT NOT NULL,
 schedule_revision INTEGER NOT NULL, title TEXT NOT NULL, prompt TEXT NOT NULL,
 agent_id TEXT NOT NULL, workspace_id TEXT NOT NULL,
 scheduled_at REAL NOT NULL, created_at REAL NOT NULL,
 status TEXT NOT NULL CHECK(status IN
  ('claimed','running','completed','failed','cancelled','interrupted')),
 outcome TEXT NOT NULL CHECK(outcome IN ('pending','known','unknown')),
 run_id TEXT, error_code TEXT,
 UNIQUE(schedule_id,scheduled_at),
 CHECK((status IN ('claimed','running') AND outcome='pending') OR
       (status IN ('completed','failed','cancelled','interrupted') AND outcome!='pending'))
);
CREATE INDEX occurrences_schedule ON occurrences(schedule_id,created_at,occurrence_id);
"""


class SchedulerError(Exception):
    """Stable public codes, without SQL, private paths or prompt contents."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


def _timestamp(value: float) -> float:
    if (
        type(value) not in {float, int}
        or not math.isfinite(value)
        or not 0 <= value < _MAX_TIMESTAMP
    ):
        raise SchedulerError("scheduler_time_invalid")
    return float(value)


def _uuid(value: str) -> str:
    if not isinstance(value, str) or re.fullmatch(r"[a-f0-9]{32}", value) is None:
        raise SchedulerError("scheduler_identity_invalid")
    return value


def _scope(value: str) -> str:
    if not isinstance(value, str) or re.fullmatch(r"[A-Za-z0-9_-]{1,64}", value) is None:
        raise SchedulerError("scheduler_scope_invalid")
    return value


def _text(value: str, maximum: int) -> str:
    if not isinstance(value, str) or not value.strip() or "\0" in value:
        raise SchedulerError("scheduler_text_invalid")
    try:
        if len(value.encode()) > maximum:
            raise SchedulerError("scheduler_text_limit")
    except UnicodeError:
        raise SchedulerError("scheduler_text_invalid") from None
    return value


def _json_object(pairs):
    value = {}
    for key, item in pairs:
        if key in value:
            raise SchedulerError("scheduler_database_invalid")
        value[key] = item
    return value


@dataclass(frozen=True)
class ScheduleSpec:
    kind: str
    timezone: str = "UTC"
    at: float | None = None
    interval_seconds: int | None = None
    local_time: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.kind, str) or self.kind not in {"once", "interval", "daily"}:
            raise SchedulerError("scheduler_rule_invalid")
        if (
            not isinstance(self.timezone, str)
            or len(self.timezone) > 128
            or "\0" in self.timezone
            or self.timezone.startswith(("/", "."))
        ):
            raise SchedulerError("scheduler_timezone_invalid")
        try:
            ZoneInfo(self.timezone)
        except (ZoneInfoNotFoundError, ValueError):
            raise SchedulerError("scheduler_timezone_invalid") from None
        if self.at is not None:
            _timestamp(self.at)
        if self.kind == "once":
            if self.at is None or self.interval_seconds is not None or self.local_time is not None:
                raise SchedulerError("scheduler_rule_invalid")
        elif self.kind == "interval":
            if (
                type(self.interval_seconds) is not int
                or not 1 <= self.interval_seconds <= 31_536_000
                or self.local_time is not None
            ):
                raise SchedulerError("scheduler_rule_invalid")
        elif (
            self.at is not None
            or self.interval_seconds is not None
            or not isinstance(self.local_time, str)
            or re.fullmatch(r"(?:[01]\d|2[0-3]):[0-5]\d", self.local_time) is None
        ):
            raise SchedulerError("scheduler_rule_invalid")

    @classmethod
    def from_dict(cls, value: dict) -> ScheduleSpec:
        if not isinstance(value, dict) or set(value) - {
            "kind",
            "timezone",
            "at",
            "interval_seconds",
            "local_time",
        }:
            raise SchedulerError("scheduler_rule_invalid")
        try:
            return cls(**value)
        except TypeError:
            raise SchedulerError("scheduler_rule_invalid") from None

    def _daily_at(self, day: date) -> float | None:
        hour, minute = map(int, self.local_time.split(":"))
        local = datetime.combine(day, wall_time(hour, minute), ZoneInfo(self.timezone))
        # roundtrip rejects a gap; fold=0 chooses exactly the first ambiguous instant.
        candidate = local.astimezone(UTC)
        if candidate.astimezone(local.tzinfo).replace(tzinfo=None) != local.replace(tzinfo=None):
            return None
        return candidate.timestamp()

    def first_at(self, now: float) -> float:
        now = _timestamp(now)
        if self.kind == "once":
            return float(self.at)
        if self.kind == "interval":
            return (
                float(self.at) if self.at is not None else _timestamp(now + self.interval_seconds)
            )
        return self.after(now)

    def after(self, after: float) -> float:
        after = _timestamp(after)
        if self.kind != "daily":
            raise SchedulerError("scheduler_rule_invalid")
        local_day = datetime.fromtimestamp(after, ZoneInfo(self.timezone)).date()
        try:
            for offset in range(370):
                candidate = self._daily_at(local_day + timedelta(days=offset))
                if candidate is not None and candidate > after:
                    return _timestamp(candidate)
        except (OverflowError, ValueError):
            raise SchedulerError("scheduler_time_invalid") from None
        raise SchedulerError("scheduler_time_invalid")

    def coalesce(self, due: float, now: float) -> tuple[float, float | None]:
        due, now = _timestamp(due), _timestamp(now)
        if self.kind == "once":
            return due, None
        if self.kind == "interval":
            latest = (
                due + math.floor(max(0, now - due) / self.interval_seconds) * self.interval_seconds
            )
            return latest, _timestamp(latest + self.interval_seconds)
        local_day = datetime.fromtimestamp(now, ZoneInfo(self.timezone)).date()
        for offset in range(370):
            candidate = self._daily_at(local_day - timedelta(days=offset))
            if candidate is not None and due <= candidate <= now:
                return candidate, self.after(now)
        return due, self.after(now)


@dataclass(frozen=True)
class SchedulerLimits:
    max_schedules: int = 256
    max_occurrences: int = 10000
    max_prompt_bytes: int = 16000
    max_page_size: int = 100

    def __post_init__(self) -> None:
        if (
            any(type(value) is not int or value < 1 for value in vars(self).values())
            or self.max_schedules > 10000
            or self.max_occurrences > 100000
            or self.max_prompt_bytes > 32000
            or self.max_page_size > 500
        ):
            raise SchedulerError("scheduler_limits_invalid")


@dataclass(frozen=True)
class ScheduleRecord:
    schedule_id: str
    title: str
    prompt: str
    agent_id: str
    workspace_id: str
    spec: ScheduleSpec
    enabled: bool
    revision: int
    next_at: float | None
    created_at: float
    updated_at: float


@dataclass(frozen=True)
class JobOccurrence:
    occurrence_id: str
    schedule_id: str
    schedule_revision: int
    title: str
    prompt: str
    agent_id: str
    workspace_id: str
    scheduled_at: float
    created_at: float
    status: str
    outcome: str
    run_id: str | None = None
    error_code: str | None = None


@dataclass(frozen=True)
class DispatchResult:
    run_id: str | None
    status: str = "completed"
    outcome: str = "known"
    error_code: str | None = None

    def __post_init__(self) -> None:
        if self.run_id is not None:
            _uuid(self.run_id)
        _result(self.status, self.outcome, self.error_code)


def _result(status: str, outcome: str, error_code: str | None) -> None:
    if (
        not isinstance(status, str)
        or status not in _TERMINAL
        or not isinstance(outcome, str)
        or outcome not in {"known", "unknown"}
        or (status == "completed" and outcome != "known")
        or (
            error_code is not None
            and (
                not isinstance(error_code, str)
                or re.fullmatch(r"[a-z][a-z0-9_]{0,127}", error_code) is None
            )
        )
    ):
        raise SchedulerError("scheduler_result_invalid")


class ScheduleStore:
    def __init__(
        self,
        path: Path,
        *,
        limits: SchedulerLimits | None = None,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self.path = Path(path)
        if not self.path.is_absolute() or ".." in self.path.parts:
            raise SchedulerError("scheduler_path_invalid")
        self.limits = limits or SchedulerLimits()
        if not isinstance(self.limits, SchedulerLimits) or not callable(clock):
            raise SchedulerError("scheduler_limits_invalid")
        self.clock = clock
        self._mutex = threading.RLock()
        self._connection: sqlite3.Connection | None = None
        self._file_fd: int | None = None
        self._parent_identity: tuple[int, int] | None = None
        self._closed = False

    @staticmethod
    def _private_file(path: Path) -> tuple[int, bool]:
        flags = os.O_RDWR | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC
        try:
            fd, created = os.open(path, flags | os.O_CREAT | os.O_EXCL, 0o600), True
        except FileExistsError:
            fd, created = os.open(path, flags), False
        info = os.fstat(fd)
        if (
            not stat.S_ISREG(info.st_mode)
            or info.st_uid != os.getuid()
            or stat.S_IMODE(info.st_mode) != 0o600
            or info.st_nlink != 1
            or info.st_size > 64 * 1024 * 1024
        ):
            os.close(fd)
            raise SchedulerError("scheduler_path_unsafe")
        return fd, created

    def _check(self) -> None:
        current = Path(self.path.anchor)
        for part in self.path.parent.parts[1:]:
            current /= part
            if not stat.S_ISDIR(current.lstat().st_mode):
                raise SchedulerError("scheduler_path_unsafe")
        parent = self.path.parent.lstat()
        if (
            parent.st_uid != os.getuid()
            or stat.S_IMODE(parent.st_mode) != 0o700
            or (parent.st_dev, parent.st_ino) != self._parent_identity
        ):
            raise SchedulerError("scheduler_path_unsafe")
        info, opened = self.path.lstat(), os.fstat(self._file_fd)
        if (
            not stat.S_ISREG(info.st_mode)
            or info.st_uid != os.getuid()
            or stat.S_IMODE(info.st_mode) != 0o600
            or info.st_nlink != 1
            or info.st_size > 64 * 1024 * 1024
            or (info.st_dev, info.st_ino) != (opened.st_dev, opened.st_ino)
        ):
            raise SchedulerError("scheduler_path_unsafe")
        for suffix in ("-journal", "-wal", "-shm"):
            try:
                info = self.path.with_name(self.path.name + suffix).lstat()
            except FileNotFoundError:
                continue
            if (
                not stat.S_ISREG(info.st_mode)
                or info.st_uid != os.getuid()
                or stat.S_IMODE(info.st_mode) != 0o600
                or info.st_nlink != 1
            ):
                raise SchedulerError("scheduler_path_unsafe")

    def _open(self) -> sqlite3.Connection:
        if self._closed:
            raise SchedulerError("scheduler_closed")
        if self._connection is not None:
            self._check()
            return self._connection
        lock = connection = None
        try:
            ensure_private_directory(self.path.parent)
            parent = self.path.parent.lstat()
            self._parent_identity = parent.st_dev, parent.st_ino
            lock, _ = self._private_file(self.path.with_name(self.path.name + ".init.lock"))
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            self._file_fd, created = self._private_file(self.path)
            self._check()
            connection = sqlite3.connect(
                str(self.path), isolation_level=None, check_same_thread=False, timeout=1
            )
            self._check()
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA foreign_keys=ON")
            connection.execute("PRAGMA trusted_schema=OFF")
            connection.execute("PRAGMA temp_store=MEMORY")
            connection.execute("PRAGMA busy_timeout=1000")
            if created:
                connection.executescript(
                    "BEGIN IMMEDIATE;"
                    + _SCHEMA
                    + f"PRAGMA application_id={_APPLICATION_ID};"
                    + f"PRAGMA user_version={_SCHEMA_VERSION};"
                    + "COMMIT;"
                )
            elif (
                connection.execute("PRAGMA application_id").fetchone()[0] != _APPLICATION_ID
                or connection.execute("PRAGMA user_version").fetchone()[0] != _SCHEMA_VERSION
                or connection.execute("PRAGMA quick_check(1)").fetchone()[0] != "ok"
            ):
                raise SchedulerError("scheduler_database_invalid")
            if connection.execute("PRAGMA journal_mode=DELETE").fetchone()[0] != "delete":
                raise SchedulerError("scheduler_database_invalid")
            connection.execute("PRAGMA secure_delete=ON")
            self._connection = connection
            return connection
        except BlockingIOError:
            raise SchedulerError("scheduler_busy") from None
        except (StateError, OSError, sqlite3.Error):
            raise SchedulerError("scheduler_unavailable") from None
        finally:
            if lock is not None:
                os.close(lock)
            if self._connection is None:
                if connection is not None:
                    connection.close()
                if self._file_fd is not None:
                    os.close(self._file_fd)
                    self._file_fd = None

    @contextmanager
    def _transaction(self) -> Iterator[sqlite3.Connection]:
        with self._mutex:
            connection = None
            try:
                connection = self._open()
                connection.execute("BEGIN IMMEDIATE")
                yield connection
                self._check()
                connection.execute("COMMIT")
            except (OSError, sqlite3.Error):
                if connection is not None and connection.in_transaction:
                    connection.rollback()
                raise SchedulerError("scheduler_unavailable") from None
            except BaseException:
                if connection is not None and connection.in_transaction:
                    connection.rollback()
                raise

    def _record(self, row: sqlite3.Row) -> ScheduleRecord:
        try:
            _uuid(row["schedule_id"])
            _text(row["title"], 512), _text(row["prompt"], self.limits.max_prompt_bytes)
            _scope(row["agent_id"]), _scope(row["workspace_id"])
            _timestamp(row["created_at"]), _timestamp(row["updated_at"])
            self._revision(row["revision"])
            _text(row["spec_json"], 4096)
            if row["next_at"] is not None:
                _timestamp(row["next_at"])
            if type(row["enabled"]) is not int or row["enabled"] not in (0, 1):
                raise SchedulerError("scheduler_database_invalid")
            spec = ScheduleSpec.from_dict(
                json.loads(row["spec_json"], object_pairs_hook=_json_object)
            )
        except (ValueError, IndexError, TypeError, SchedulerError):
            raise SchedulerError("scheduler_database_invalid") from None
        return ScheduleRecord(
            row["schedule_id"],
            row["title"],
            row["prompt"],
            row["agent_id"],
            row["workspace_id"],
            spec,
            bool(row["enabled"]),
            row["revision"],
            row["next_at"],
            row["created_at"],
            row["updated_at"],
        )

    def _occurrence(self, row: sqlite3.Row) -> JobOccurrence:
        try:
            occurrence = JobOccurrence(**dict(row))
            _uuid(occurrence.occurrence_id), _uuid(occurrence.schedule_id)
            self._revision(occurrence.schedule_revision)
            _text(occurrence.title, 512), _text(occurrence.prompt, self.limits.max_prompt_bytes)
            _scope(occurrence.agent_id), _scope(occurrence.workspace_id)
            _timestamp(occurrence.scheduled_at), _timestamp(occurrence.created_at)
            if occurrence.run_id is not None:
                _uuid(occurrence.run_id)
            if occurrence.status in _ACTIVE:
                if occurrence.outcome != "pending":
                    raise SchedulerError("scheduler_database_invalid")
            else:
                _result(occurrence.status, occurrence.outcome, occurrence.error_code)
        except (ValueError, IndexError, TypeError, SchedulerError):
            raise SchedulerError("scheduler_database_invalid") from None
        return occurrence

    def _page(self, limit: int, offset: int = 0) -> None:
        if (
            type(limit) is not int
            or not 1 <= limit <= self.limits.max_page_size
            or type(offset) is not int
            or not 0 <= offset <= 100000
        ):
            raise SchedulerError("scheduler_page_invalid")

    @staticmethod
    def _revision(value: int) -> None:
        if type(value) is not int or value < 1:
            raise SchedulerError("scheduler_revision_invalid")

    def create(
        self,
        title: str,
        prompt: str,
        agent_id: str,
        workspace_id: str,
        spec: ScheduleSpec,
        *,
        enabled: bool = False,
    ) -> ScheduleRecord:
        _text(title, 512), _text(prompt, self.limits.max_prompt_bytes)
        _scope(agent_id), _scope(workspace_id)
        if not isinstance(spec, ScheduleSpec) or type(enabled) is not bool:
            raise SchedulerError("scheduler_rule_invalid")
        now = _timestamp(self.clock())
        next_at = spec.first_at(now)
        with self._transaction() as db:
            if (
                db.execute("SELECT count(*) FROM schedules").fetchone()[0]
                >= self.limits.max_schedules
            ):
                raise SchedulerError("scheduler_capacity_exceeded")
            identity = uuid4().hex
            db.execute(
                "INSERT INTO schedules VALUES(?,?,?,?,?,?,?,1,?,?,?)",
                (
                    identity,
                    title,
                    prompt,
                    agent_id,
                    workspace_id,
                    json.dumps(asdict(spec), sort_keys=True, allow_nan=False),
                    int(enabled),
                    next_at,
                    now,
                    now,
                ),
            )
            return self._record(
                db.execute("SELECT * FROM schedules WHERE schedule_id=?", (identity,)).fetchone()
            )

    def get(self, schedule_id: str) -> ScheduleRecord:
        _uuid(schedule_id)
        with self._transaction() as db:
            row = db.execute(
                "SELECT * FROM schedules WHERE schedule_id=?", (schedule_id,)
            ).fetchone()
            if row is None:
                raise SchedulerError("scheduler_not_found")
            return self._record(row)

    def list(self, *, limit: int = 100, offset: int = 0) -> tuple[ScheduleRecord, ...]:
        self._page(limit, offset)
        with self._transaction() as db:
            return tuple(
                self._record(row)
                for row in db.execute(
                    "SELECT * FROM schedules ORDER BY created_at,schedule_id LIMIT ? OFFSET ?",
                    (limit, offset),
                )
            )

    def update(
        self,
        schedule_id: str,
        expected_revision: int,
        *,
        title: str | None = None,
        prompt: str | None = None,
        agent_id: str | None = None,
        workspace_id: str | None = None,
        spec: ScheduleSpec | None = None,
        enabled: bool | None = None,
    ) -> ScheduleRecord:
        _uuid(schedule_id), self._revision(expected_revision)
        if title is not None:
            _text(title, 512)
        if prompt is not None:
            _text(prompt, self.limits.max_prompt_bytes)
        if agent_id is not None:
            _scope(agent_id)
        if workspace_id is not None:
            _scope(workspace_id)
        if (spec is not None and not isinstance(spec, ScheduleSpec)) or (
            enabled is not None and type(enabled) is not bool
        ):
            raise SchedulerError("scheduler_rule_invalid")
        now = _timestamp(self.clock())
        with self._transaction() as db:
            row = db.execute(
                "SELECT * FROM schedules WHERE schedule_id=?", (schedule_id,)
            ).fetchone()
            if row is None:
                raise SchedulerError("scheduler_not_found")
            old = self._record(row)
            if old.revision != expected_revision:
                raise SchedulerError("scheduler_revision_conflict")
            rule = spec or old.spec
            # Changing instructions/scope never inherits a stale reviewed occurrence.
            next_at = rule.first_at(now) if spec is not None else old.next_at
            if (
                spec is not None
                and db.execute(
                    "SELECT 1 FROM occurrences WHERE schedule_id=? AND scheduled_at=?",
                    (schedule_id, next_at),
                ).fetchone()
                is not None
            ):
                raise SchedulerError("scheduler_slot_already_claimed")
            if enabled is True and not old.enabled and next_at is None:
                raise SchedulerError("scheduler_finished_once")
            db.execute(
                "UPDATE schedules SET title=?,prompt=?,agent_id=?,workspace_id=?,"
                "spec_json=?,enabled=?,revision=revision+1,next_at=?,updated_at=?"
                " WHERE schedule_id=?",
                (
                    old.title if title is None else title,
                    old.prompt if prompt is None else prompt,
                    old.agent_id if agent_id is None else agent_id,
                    old.workspace_id if workspace_id is None else workspace_id,
                    json.dumps(asdict(rule), sort_keys=True, allow_nan=False),
                    int(old.enabled if enabled is None else enabled),
                    next_at,
                    now,
                    schedule_id,
                ),
            )
            db.execute(
                "UPDATE occurrences SET status='cancelled',outcome='known',"
                "error_code='schedule_changed_before_dispatch'"
                " WHERE schedule_id=? AND status='claimed'",
                (schedule_id,),
            )
            return self._record(
                db.execute("SELECT * FROM schedules WHERE schedule_id=?", (schedule_id,)).fetchone()
            )

    def remove(self, schedule_id: str, expected_revision: int) -> bool:
        _uuid(schedule_id), self._revision(expected_revision)
        with self._transaction() as db:
            row = db.execute(
                "SELECT revision FROM schedules WHERE schedule_id=?", (schedule_id,)
            ).fetchone()
            if row is None:
                return False
            if row[0] != expected_revision:
                raise SchedulerError("scheduler_revision_conflict")
            db.execute(
                "UPDATE occurrences SET status='cancelled',outcome='known',"
                "error_code='schedule_removed_before_dispatch'"
                " WHERE schedule_id=? AND status='claimed'",
                (schedule_id,),
            )
            db.execute("DELETE FROM schedules WHERE schedule_id=?", (schedule_id,))
            return True

    def claim_due(self, *, now: float | None = None, limit: int = 1) -> tuple[JobOccurrence, ...]:
        self._page(limit)
        instant = _timestamp(self.clock() if now is None else now)
        with self._transaction() as db:
            due = db.execute(
                "SELECT * FROM schedules WHERE enabled=1 AND next_at<=?"
                " AND NOT EXISTS (SELECT 1 FROM occurrences o"
                " WHERE o.schedule_id=schedules.schedule_id AND o.status IN ('claimed','running'))"
                " ORDER BY next_at,schedule_id LIMIT ?",
                (instant, limit),
            ).fetchall()
            count = db.execute("SELECT count(*) FROM occurrences").fetchone()[0]
            if count + len(due) > self.limits.max_occurrences:
                raise SchedulerError("scheduler_capacity_exceeded")
            claims = []
            for row in due:
                record = self._record(row)
                scheduled_at, next_at = record.spec.coalesce(record.next_at, instant)
                identity = uuid4().hex
                db.execute(
                    "INSERT INTO occurrences VALUES(?,?,?,?,?,?,?,?,?,"
                    "'claimed','pending',NULL,NULL)",
                    (
                        identity,
                        record.schedule_id,
                        record.revision,
                        record.title,
                        record.prompt,
                        record.agent_id,
                        record.workspace_id,
                        scheduled_at,
                        instant,
                    ),
                )
                db.execute(
                    "UPDATE schedules SET next_at=?,enabled=? WHERE schedule_id=?",
                    (next_at, int(next_at is not None), record.schedule_id),
                )
                claims.append(
                    self._occurrence(
                        db.execute(
                            "SELECT * FROM occurrences WHERE occurrence_id=?", (identity,)
                        ).fetchone()
                    )
                )
            return tuple(claims)

    def begin_dispatch(self, occurrence_id: str) -> JobOccurrence | None:
        _uuid(occurrence_id)
        with self._transaction() as db:
            row = db.execute(
                "SELECT * FROM occurrences WHERE occurrence_id=?", (occurrence_id,)
            ).fetchone()
            if row is None:
                raise SchedulerError("scheduler_occurrence_not_found")
            if row["status"] != "claimed":
                return None
            schedule = db.execute(
                "SELECT * FROM schedules WHERE schedule_id=?", (row["schedule_id"],)
            ).fetchone()
            # A once rule disables itself on claim. Its reviewed revision still
            # exists; explicit pause/edit increments revision and cancels claims.
            if schedule is None or schedule["revision"] != row["schedule_revision"]:
                db.execute(
                    "UPDATE occurrences SET status='cancelled',outcome='known',"
                    "error_code='schedule_changed_before_dispatch' WHERE occurrence_id=?",
                    (occurrence_id,),
                )
                return None
            db.execute(
                "UPDATE occurrences SET status='running' WHERE occurrence_id=?", (occurrence_id,)
            )
            return self._occurrence(
                db.execute(
                    "SELECT * FROM occurrences WHERE occurrence_id=?", (occurrence_id,)
                ).fetchone()
            )

    def attach_run(self, occurrence_id: str, run_id: str) -> None:
        _uuid(occurrence_id), _uuid(run_id)
        with self._transaction() as db:
            row = db.execute(
                "SELECT status,run_id FROM occurrences WHERE occurrence_id=?", (occurrence_id,)
            ).fetchone()
            if row is None:
                raise SchedulerError("scheduler_occurrence_not_found")
            if row["status"] != "running" or row["run_id"] not in {None, run_id}:
                raise SchedulerError("scheduler_occurrence_conflict")
            db.execute(
                "UPDATE occurrences SET run_id=? WHERE occurrence_id=?", (run_id, occurrence_id)
            )

    def finish(
        self, occurrence_id: str, status: str, outcome: str, *, error_code: str | None = None
    ) -> JobOccurrence:
        _uuid(occurrence_id), _result(status, outcome, error_code)
        with self._transaction() as db:
            row = db.execute(
                "SELECT * FROM occurrences WHERE occurrence_id=?", (occurrence_id,)
            ).fetchone()
            if row is None:
                raise SchedulerError("scheduler_occurrence_not_found")
            if row["status"] not in _ACTIVE:
                if (row["status"], row["outcome"], row["error_code"]) == (
                    status,
                    outcome,
                    error_code,
                ):
                    return self._occurrence(row)
                raise SchedulerError("scheduler_occurrence_conflict")
            db.execute(
                "UPDATE occurrences SET status=?,outcome=?,error_code=? WHERE occurrence_id=?",
                (status, outcome, error_code, occurrence_id),
            )
            return self._occurrence(
                db.execute(
                    "SELECT * FROM occurrences WHERE occurrence_id=?", (occurrence_id,)
                ).fetchone()
            )

    def history(
        self, schedule_id: str | None = None, *, limit: int = 100, offset: int = 0
    ) -> tuple[JobOccurrence, ...]:
        self._page(limit, offset)
        if schedule_id is not None:
            _uuid(schedule_id)
        where, parameters = (
            ("", ()) if schedule_id is None else (" WHERE schedule_id=?", (schedule_id,))
        )
        with self._transaction() as db:
            return tuple(
                self._occurrence(row)
                for row in db.execute(
                    "SELECT * FROM occurrences"
                    + where
                    + " ORDER BY created_at DESC,occurrence_id DESC LIMIT ? OFFSET ?",
                    (*parameters, limit, offset),
                )
            )

    def clear_history(self, schedule_id: str) -> int:
        _uuid(schedule_id)
        with self._transaction() as db:
            return db.execute(
                "DELETE FROM occurrences WHERE schedule_id=?"
                " AND status NOT IN ('claimed','running')",
                (schedule_id,),
            ).rowcount

    def _runner_lease(self) -> int:
        with self._mutex:
            self._open()
            try:
                fd, _ = self._private_file(self.path.with_name(self.path.name + ".runner.lock"))
                try:
                    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                except BlockingIOError:
                    os.close(fd)
                    raise SchedulerError("scheduler_runner_in_use") from None
                return fd
            except OSError:
                raise SchedulerError("scheduler_unavailable") from None

    def _recover(self) -> int:
        with self._transaction() as db:
            return db.execute(
                "UPDATE occurrences SET status='interrupted',outcome='unknown',"
                "error_code='scheduler_recovered_without_replay'"
                " WHERE status IN ('claimed','running')"
            ).rowcount

    def recover_unfinished(self) -> int:
        lease = self._runner_lease()
        try:
            return self._recover()
        finally:
            os.close(lease)

    def close(self) -> None:
        with self._mutex:
            self._closed = True
            if self._connection is not None:
                self._connection.close()
                self._connection = None
            if self._file_fd is not None:
                os.close(self._file_fd)
                self._file_fd = None


class Scheduler:
    """Explicit polling owner. Dispatch must cooperate with cancellation.

    ``aclose`` retains its runner lease when a host callback refuses to settle,
    rather than allowing another runner or claiming its resources were closed.
    The host may release that callback and retry aclose; never close its store
    underneath an active callback. No callback result verifies physical effects.
    """

    def __init__(
        self,
        store: ScheduleStore,
        dispatch: Callable[[JobOccurrence], Awaitable[DispatchResult]],
        *,
        poll_interval: float = 0.5,
        max_parallel: int = 1,
        dispatch_timeout: float = 1800,
        close_timeout: float = 5,
    ) -> None:
        for value in (poll_interval, dispatch_timeout, close_timeout):
            if type(value) not in {int, float} or not math.isfinite(value) or value <= 0:
                raise SchedulerError("scheduler_limits_invalid")
        if (
            not 0.01 <= poll_interval <= 60
            or dispatch_timeout > 3600
            or close_timeout > 30
            or type(max_parallel) is not int
            or not 1 <= max_parallel <= 8
            or not callable(dispatch)
        ):
            raise SchedulerError("scheduler_limits_invalid")
        self.store, self.dispatch = store, dispatch
        self.poll_interval, self.max_parallel = poll_interval, max_parallel
        self.dispatch_timeout, self.close_timeout = dispatch_timeout, close_timeout
        self._pump_task: asyncio.Task | None = None
        self._tasks: set[asyncio.Task] = set()
        self._lease: int | None = None
        self._stop = asyncio.Event()
        self._closed = False
        self.error_code: str | None = None

    @property
    def running(self) -> bool:
        return (
            self._pump_task is not None and not self._pump_task.done() and not self._stop.is_set()
        )

    @property
    def active_count(self) -> int:
        return len(self._tasks)

    def start(self) -> bool:
        if self._closed or self._stop.is_set():
            raise SchedulerError("scheduler_runner_closed")
        if self.running:
            return False
        self._lease = self.store._runner_lease()
        try:
            self.store._recover()
            self._pump_task = asyncio.create_task(self._pump())
        except BaseException:
            os.close(self._lease)
            self._lease = None
            raise
        return True

    async def _pump(self) -> None:
        try:
            while not self._stop.is_set():
                capacity = self.max_parallel - len(self._tasks)
                if capacity:
                    for occurrence in self.store.claim_due(limit=capacity):
                        task = asyncio.create_task(self._dispatch(occurrence))
                        self._tasks.add(task)
                        task.add_done_callback(self._task_done)
                try:
                    await asyncio.wait_for(self._stop.wait(), self.poll_interval)
                except TimeoutError:
                    pass
        except SchedulerError as error:
            self.error_code = error.code
            self._stop.set()
        except asyncio.CancelledError:
            self._stop.set()
            raise
        except Exception:
            self.error_code = "scheduler_runner_failed"
            self._stop.set()

    def _task_done(self, task: asyncio.Task) -> None:
        self._tasks.discard(task)
        if not task.cancelled() and task.exception() is not None:
            self.error_code = "scheduler_receipt_unconfirmed"
            self._stop.set()

    async def _dispatch(self, claim: JobOccurrence) -> None:
        occurrence = self.store.begin_dispatch(claim.occurrence_id)
        if occurrence is None:
            return
        try:
            async with asyncio.timeout(self.dispatch_timeout) as deadline:
                result = await self.dispatch(occurrence)
            if self._stop.is_set():
                # A host callback may suppress Task.cancel(). Closing is still
                # cancellation, never a late successful confirmation of effects.
                raise asyncio.CancelledError
            if deadline.expired() or not isinstance(result, DispatchResult):
                raise SchedulerError("scheduler_dispatch_unconfirmed")
            if result.run_id is not None:
                self.store.attach_run(occurrence.occurrence_id, result.run_id)
            self.store.finish(
                occurrence.occurrence_id,
                result.status,
                result.outcome,
                error_code=result.error_code,
            )
        except asyncio.CancelledError:
            self.store.finish(
                occurrence.occurrence_id,
                "cancelled",
                "unknown",
                error_code="scheduler_dispatch_cancelled",
            )
            raise
        except Exception as error:
            code = (
                error.code
                if isinstance(error, SchedulerError)
                else "scheduler_dispatch_unconfirmed"
            )
            self.store.finish(occurrence.occurrence_id, "failed", "unknown", error_code=code)

    async def aclose(self) -> None:
        self._stop.set()
        if self._pump_task is not None:
            await asyncio.gather(self._pump_task, return_exceptions=True)
        tasks = tuple(self._tasks)
        for task in tasks:
            task.cancel()
        if tasks:
            _, pending = await asyncio.wait(tasks, timeout=self.close_timeout)
            if pending:
                raise SchedulerError("scheduler_cleanup_incomplete")
            await asyncio.gather(*tasks, return_exceptions=True)
        if self._lease is not None:
            os.close(self._lease)
            self._lease = None
        self._closed = True
