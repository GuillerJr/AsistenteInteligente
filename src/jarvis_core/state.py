"""Explicit, private SQLite state for the independent Jarvis base.

``StateStore(path)`` never selects an owner data directory. Its absolute path is
supplied by the host. Opening is lazy: ``SessionRef(..., temporary=True)`` routes
every operation to a separate SQLite ``:memory:`` database, including failed
lookups. Temporary sessions never consult, recover, or initialize the disk store.

Use ``create_session`` then retain its ``SessionRecord.ref``. Pass that reference
to message/run/event methods; a remote client must not choose a session's mode.
``create_run(..., run_id=handle.run_id)`` binds runtime and durable identities.
``finish_run`` atomically stores a terminal receipt, its event, and an optional
``assistant_text`` message. Model messages are separate from receipts: neither a
completed turn nor text verifies an effect.

Only one durable StateStore may own a database at a time. Its first durable access
recovers unfinished runs as interrupted with unknown outcomes. Recovery records
facts; it cannot execute tools or replay effects. Close the store on shutdown.
Capacity limits fail closed and require explicit deletion rather than silently
dropping history. This module is conversation state, not personal semantic memory.
"""

from __future__ import annotations

import fcntl
import json
import math
import os
import re
import sqlite3
import stat
import threading
import time
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from uuid import uuid4

_APPLICATION_ID = 0x4A525653  # ASCII JRVS, the identity of this SQLite format.
_SCHEMA_VERSION = 1
_ACTIVE = frozenset({"queued", "running", "waiting_approval", "cancelling", "cleaning"})
_TERMINAL = frozenset({"completed", "failed", "cancelled", "interrupted"})
_TRANSITIONS = {
    "queued": {"running", "cancelling"},
    "running": {"waiting_approval", "cancelling", "cleaning"},
    "waiting_approval": {"running", "cancelling", "cleaning"},
    "cancelling": {"cleaning"},
    "cleaning": set(),
}
_SCHEMA = """
CREATE TABLE sessions (
    session_id TEXT PRIMARY KEY, title TEXT NOT NULL,
    archived INTEGER NOT NULL DEFAULT 0 CHECK(archived IN (0, 1)), created_ns INTEGER NOT NULL,
    provider_thread_id TEXT
);
CREATE TABLE runs (
    run_id TEXT PRIMARY KEY, session_id TEXT NOT NULL REFERENCES sessions ON DELETE CASCADE,
    status TEXT NOT NULL CHECK(status IN
      ('queued','running','waiting_approval','cancelling','cleaning',
       'completed','failed','cancelled','interrupted')),
    outcome TEXT NOT NULL CHECK(outcome IN ('pending','known','unknown')),
    receipt_json TEXT, created_ns INTEGER NOT NULL, updated_ns INTEGER NOT NULL,
    CHECK((status IN ('queued','running','waiting_approval','cancelling','cleaning')
      AND outcome = 'pending' AND receipt_json IS NULL) OR
      (status IN ('completed','failed','cancelled','interrupted')
      AND outcome IN ('known','unknown') AND receipt_json IS NOT NULL)),
    UNIQUE(run_id, session_id)
);
CREATE TABLE messages (
    message_id TEXT PRIMARY KEY, session_id TEXT NOT NULL REFERENCES sessions ON DELETE CASCADE,
    sequence INTEGER NOT NULL CHECK(sequence > 0), role TEXT NOT NULL,
    text TEXT NOT NULL, run_id TEXT, created_ns INTEGER NOT NULL,
    UNIQUE(session_id, sequence),
    FOREIGN KEY(run_id, session_id) REFERENCES runs(run_id, session_id) ON DELETE CASCADE
);
CREATE TABLE events (
    event_id TEXT PRIMARY KEY, run_id TEXT NOT NULL REFERENCES runs ON DELETE CASCADE,
    sequence INTEGER NOT NULL CHECK(sequence > 0), kind TEXT NOT NULL,
    payload_json TEXT NOT NULL, created_ns INTEGER NOT NULL, UNIQUE(run_id, sequence)
);
CREATE INDEX runs_session ON runs(session_id, created_ns, run_id);
CREATE INDEX sessions_order ON sessions(created_ns, session_id);
CREATE INDEX messages_run ON messages(run_id, sequence);
"""


class StateError(Exception):
    """Public error codes never include SQL, paths, message text, or DB contents."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def _identity(value: str) -> str:
    if not isinstance(value, str) or re.fullmatch(r"[a-f0-9]{32}", value) is None:
        raise StateError("invalid_identity")
    return value


def ensure_private_directory(path: str | Path) -> Path:
    """Create an explicit 0700 directory; reject symlinks or unsafe existing paths.

    Existing ancestors need to be ordinary directories. The final directory must
    belong to the current uid with exactly 0700 permissions. Existing permissions
    are never silently changed. This helper reads file metadata, not file data.
    """
    path = Path(path)
    if not path.is_absolute() or ".." in path.parts:
        raise StateError("invalid_state_path")
    try:
        _directory_identity(path, create=True)
    except OSError:
        raise StateError("state_unavailable") from None
    return path


def _directory_identity(path: Path, *, create: bool) -> tuple[int, int]:
    current = Path(path.anchor)
    for component in path.parts[1:]:
        current /= component
        try:
            info = current.lstat()
        except FileNotFoundError:
            if not create:
                raise
            current.mkdir(mode=0o700)
            info = current.lstat()
        if not stat.S_ISDIR(info.st_mode):
            raise StateError("unsafe_state_path")
    info = path.lstat()
    if info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) != 0o700:
        raise StateError("unsafe_state_permissions")
    return info.st_dev, info.st_ino


@dataclass(frozen=True)
class SessionRef:
    session_id: str
    temporary: bool = False

    def __post_init__(self) -> None:
        _identity(self.session_id)
        if type(self.temporary) is not bool:
            raise StateError("invalid_temporary_mode")


@dataclass(frozen=True)
class StateLimits:
    max_sessions: int = 1000
    max_messages_per_session: int = 10000
    max_runs_per_session: int = 1000
    max_events_per_run: int = 10000
    max_text_bytes: int = 65536
    max_json_bytes: int = 65536
    max_page_size: int = 500

    def __post_init__(self) -> None:
        for value in vars(self).values():
            if type(value) is not int or not 1 <= value <= 1_000_000:
                raise StateError("invalid_limits")
        if self.max_events_per_run < 4:
            raise StateError("invalid_limits")


@dataclass(frozen=True)
class SessionRecord:
    ref: SessionRef
    title: str
    archived: bool
    created_ns: int
    provider_thread_id: str | None = None


@dataclass(frozen=True)
class MessageRecord:
    message_id: str
    session_id: str
    sequence: int
    role: str
    text: str
    run_id: str | None
    created_ns: int


@dataclass(frozen=True)
class RunRecord:
    run_id: str
    session_id: str
    status: str
    outcome: str
    receipt_json: str | None
    created_ns: int
    updated_ns: int


@dataclass(frozen=True)
class EventRecord:
    event_id: str
    run_id: str
    sequence: int
    kind: str
    payload_json: str
    created_ns: int


class StateStore:
    def __init__(self, path: str | Path, *, limits: StateLimits | None = None) -> None:
        self.path = Path(path)
        if not self.path.is_absolute() or ".." in self.path.parts:
            raise StateError("invalid_state_path")
        self.limits = limits or StateLimits()
        if not isinstance(self.limits, StateLimits):
            raise StateError("invalid_limits")
        self._mutex = threading.RLock()
        self._disk: sqlite3.Connection | None = None
        self._memory: sqlite3.Connection | None = None
        self._file_fd: int | None = None
        self._lock_fd: int | None = None
        self._parent_identity: tuple[int, int] | None = None
        self._closed = False

    def __enter__(self) -> StateStore:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def close(self) -> None:
        with self._mutex:
            for connection in (self._disk, self._memory):
                if connection is not None:
                    connection.close()
            self._disk = self._memory = None
            self._release_files()
            self._closed = True

    def _release_files(self) -> None:
        for descriptor in (self._file_fd, self._lock_fd):
            if descriptor is not None:
                os.close(descriptor)
        self._file_fd = self._lock_fd = None

    @staticmethod
    def _private_file(path: Path) -> tuple[int, bool]:
        flags = os.O_RDWR | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NONBLOCK
        try:
            descriptor = os.open(path, flags | os.O_CREAT | os.O_EXCL, 0o600)
            created = True
        except FileExistsError:
            descriptor = os.open(path, flags)
            created = False
        info = os.fstat(descriptor)
        if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid()
                or stat.S_IMODE(info.st_mode) != 0o600 or info.st_nlink != 1):
            os.close(descriptor)
            raise StateError("unsafe_state_permissions")
        return descriptor, created

    def _assert_disk_identity(self) -> None:
        if _directory_identity(self.path.parent, create=False) != self._parent_identity:
            raise StateError("unsafe_state_path")
        for path, descriptor in ((self.path, self._file_fd),
                                 (self.path.with_name(self.path.name + ".lock"), self._lock_fd)):
            info = path.lstat()
            opened = os.fstat(descriptor)
            if (not stat.S_ISREG(info.st_mode)
                    or (info.st_dev, info.st_ino) != (opened.st_dev, opened.st_ino)
                    or info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) != 0o600
                    or info.st_nlink != 1):
                raise StateError("unsafe_state_path")
        # A crash may leave a valid private rollback journal. Never let SQLite
        # follow a forged sidecar, including a journal symlink, on reopen.
        for suffix in ("-journal", "-wal", "-shm"):
            sidecar = self.path.with_name(self.path.name + suffix)
            try:
                info = sidecar.lstat()
            except FileNotFoundError:
                continue
            if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid()
                    or stat.S_IMODE(info.st_mode) != 0o600 or info.st_nlink != 1):
                raise StateError("unsafe_state_path")

    @staticmethod
    def _configure(connection: sqlite3.Connection) -> None:
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA trusted_schema=OFF")
        connection.execute("PRAGMA temp_store=MEMORY")
        connection.execute("PRAGMA busy_timeout=1000")

    @staticmethod
    def _initialize(connection: sqlite3.Connection) -> None:
        # executescript owns this initial transaction; no existing data is present.
        connection.executescript(
            "BEGIN IMMEDIATE;" + _SCHEMA
            + f"PRAGMA application_id={_APPLICATION_ID};"
            + f"PRAGMA user_version={_SCHEMA_VERSION};COMMIT;"
        )

    def _open_disk(self) -> sqlite3.Connection:
        if self._disk is not None:
            self._assert_disk_identity()
            return self._disk
        connection = None
        try:
            ensure_private_directory(self.path.parent)
            self._parent_identity = _directory_identity(self.path.parent, create=False)
            lock_path = self.path.with_name(self.path.name + ".lock")
            self._lock_fd, _ = self._private_file(lock_path)
            try:
                fcntl.flock(self._lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise StateError("state_in_use") from None
            self._file_fd, created = self._private_file(self.path)
            self._assert_disk_identity()
            connection = sqlite3.connect(str(self.path), isolation_level=None,
                                         check_same_thread=False, timeout=1)
            self._assert_disk_identity()
            self._configure(connection)
            if created:
                self._initialize(connection)
            elif (connection.execute("PRAGMA application_id").fetchone()[0] != _APPLICATION_ID
                    or connection.execute("PRAGMA user_version").fetchone()[0] != _SCHEMA_VERSION
                    or connection.execute("PRAGMA quick_check(1)").fetchone()[0] != "ok"
                    or connection.execute("PRAGMA foreign_key_check").fetchone() is not None):
                raise StateError("invalid_database")
            # Private rollback journals inherit the database's 0600 mode; no WAL,
            # shared-memory sidecar or disk-backed temporary table is needed.
            if connection.execute("PRAGMA journal_mode=DELETE").fetchone()[0] != "delete":
                raise StateError("invalid_database")
            connection.execute("PRAGMA secure_delete=ON")
            self._recover(connection)
            self._disk = connection
            return connection
        except (OSError, sqlite3.Error):
            if connection is not None:
                connection.close()
            self._release_files()
            raise StateError("state_unavailable") from None
        except StateError:
            if connection is not None:
                connection.close()
            self._release_files()
            raise

    def _connection(self, temporary: bool) -> sqlite3.Connection:
        if self._closed:
            raise StateError("state_closed")
        if type(temporary) is not bool:
            raise StateError("invalid_temporary_mode")
        if temporary:
            if self._memory is None:
                self._memory = sqlite3.connect(":memory:", isolation_level=None,
                                               check_same_thread=False)
                self._configure(self._memory)
                self._initialize(self._memory)
            return self._memory
        return self._open_disk()

    @contextmanager
    def _transaction(self, temporary: bool) -> Iterator[sqlite3.Connection]:
        with self._mutex:
            connection = None
            try:
                connection = self._connection(temporary)
                connection.execute("BEGIN IMMEDIATE")
                yield connection
                connection.execute("COMMIT")
            except (OSError, sqlite3.Error):
                if connection is not None and connection.in_transaction:
                    connection.rollback()
                raise StateError("state_unavailable") from None
            except BaseException:
                if connection is not None and connection.in_transaction:
                    connection.rollback()
                raise

    def _text(self, value: str, *, nonempty: bool = False) -> str:
        if not isinstance(value, str) or (nonempty and not value.strip()) or "\x00" in value:
            raise StateError("invalid_text")
        try:
            length = len(value.encode("utf-8"))
        except UnicodeError:
            raise StateError("invalid_text") from None
        if length > self.limits.max_text_bytes:
            raise StateError("text_too_large")
        return value

    def _json(self, value: Mapping[str, Any]) -> str:
        def check(item: Any, depth: int = 0) -> None:
            if depth > 20:
                raise StateError("invalid_payload")
            if item is None or type(item) in {str, bool, int}:
                return
            if type(item) is float and math.isfinite(item):
                return
            if type(item) is list:
                if len(item) > 10000:
                    raise StateError("invalid_payload")
                for child in item:
                    check(child, depth + 1)
                return
            if type(item) is dict and all(type(key) is str for key in item):
                if len(item) > 10000:
                    raise StateError("invalid_payload")
                for child in item.values():
                    check(child, depth + 1)
                return
            raise StateError("invalid_payload")

        if type(value) is not dict:
            raise StateError("invalid_payload")
        check(value)
        try:
            encoded = json.dumps(value, sort_keys=True, separators=(",", ":"),
                                 ensure_ascii=False, allow_nan=False)
            length = len(encoded.encode("utf-8"))
        except (ValueError, UnicodeError):
            raise StateError("invalid_payload") from None
        if length > self.limits.max_json_bytes:
            raise StateError("payload_too_large")
        return encoded

    def _page(self, limit: int, after: int = 0) -> None:
        if (type(limit) is not int or not 1 <= limit <= self.limits.max_page_size
                or type(after) is not int or not 0 <= after <= 2**63 - 1):
            raise StateError("invalid_page")

    @staticmethod
    def _ref(ref: SessionRef) -> SessionRef:
        if not isinstance(ref, SessionRef):
            raise StateError("invalid_session_reference")
        return ref

    @staticmethod
    def _session(connection: sqlite3.Connection, ref: SessionRef) -> sqlite3.Row:
        row = connection.execute("SELECT * FROM sessions WHERE session_id=?",
                                 (ref.session_id,)).fetchone()
        if row is None:
            raise StateError("session_not_found")
        return row

    @staticmethod
    def _run(connection: sqlite3.Connection, ref: SessionRef, run_id: str) -> sqlite3.Row:
        row = connection.execute("SELECT * FROM runs WHERE run_id=? AND session_id=?",
                                 (run_id, ref.session_id)).fetchone()
        if row is None:
            raise StateError("run_not_found")
        return row

    @staticmethod
    def _session_record(row: sqlite3.Row, temporary: bool) -> SessionRecord:
        return SessionRecord(SessionRef(row["session_id"], temporary), row["title"],
                             bool(row["archived"]), row["created_ns"], row["provider_thread_id"])

    @staticmethod
    def _capacity(connection: sqlite3.Connection, sql: str, args: tuple, maximum: int) -> None:
        if connection.execute(sql, args).fetchone()[0] >= maximum:
            raise StateError("capacity_exceeded")

    def create_session(self, title: str = "Nueva conversación", *,
                       temporary: bool = False) -> SessionRecord:
        title = self._text(title, nonempty=True)
        ref = SessionRef(uuid4().hex, temporary)
        with self._transaction(temporary) as connection:
            self._capacity(connection, "SELECT count(*) FROM sessions", (),
                           self.limits.max_sessions)
            connection.execute("INSERT INTO sessions VALUES(?, ?, 0, ?, NULL)",
                               (ref.session_id, title, time.time_ns()))
            return self._session_record(self._session(connection, ref), temporary)

    def get_session(self, ref: SessionRef) -> SessionRecord:
        self._ref(ref)
        with self._transaction(ref.temporary) as connection:
            return self._session_record(self._session(connection, ref), ref.temporary)

    def set_provider_thread(self, ref: SessionRef, thread_id: str | None) -> SessionRecord:
        """Bind only the host-supplied thread created for this session; no discovery.

        Temporary bindings stay in memory. Passing None removes the binding. A
        thread identity is opaque and cannot contain paths, controls, or spaces.
        """
        self._ref(ref)
        if thread_id is not None and (not isinstance(thread_id, str)
                or re.fullmatch(r"[A-Za-z0-9_-]{1,256}", thread_id) is None):
            raise StateError("invalid_provider_thread")
        with self._transaction(ref.temporary) as connection:
            self._session(connection, ref)
            connection.execute("UPDATE sessions SET provider_thread_id=? WHERE session_id=?",
                               (thread_id, ref.session_id))
            return self._session_record(self._session(connection, ref), ref.temporary)

    def list_sessions(self, *, temporary: bool = False, include_archived: bool = False,
                      limit: int = 100, offset: int = 0) -> tuple[SessionRecord, ...]:
        self._page(limit, offset)
        if type(include_archived) is not bool:
            raise StateError("invalid_archive_mode")
        with self._transaction(temporary) as connection:
            rows = connection.execute(
                "SELECT * FROM sessions WHERE (? OR archived=0) "
                "ORDER BY created_ns, session_id LIMIT ? OFFSET ?",
                (include_archived, limit, offset),
            ).fetchall()
            return tuple(self._session_record(row, temporary) for row in rows)

    def update_session(self, ref: SessionRef, *, title: str | None = None,
                       archived: bool | None = None) -> SessionRecord:
        self._ref(ref)
        if title is not None:
            title = self._text(title, nonempty=True)
        if archived is not None and type(archived) is not bool:
            raise StateError("invalid_archive_mode")
        with self._transaction(ref.temporary) as connection:
            row = self._session(connection, ref)
            connection.execute("UPDATE sessions SET title=?, archived=? WHERE session_id=?",
                               (row["title"] if title is None else title,
                                row["archived"] if archived is None else archived, ref.session_id))
            return self._session_record(self._session(connection, ref), ref.temporary)

    def delete_session(self, ref: SessionRef) -> None:
        self._ref(ref)
        with self._transaction(ref.temporary) as connection:
            self._session(connection, ref)
            if connection.execute(
                "SELECT 1 FROM runs WHERE session_id=? AND receipt_json IS NULL LIMIT 1",
                (ref.session_id,),
            ).fetchone() is not None:
                raise StateError("session_busy")
            connection.execute("DELETE FROM sessions WHERE session_id=?", (ref.session_id,))

    def append_message(self, ref: SessionRef, role: str, text: str, *,
                       run_id: str | None = None) -> MessageRecord:
        self._ref(ref)
        text = self._text(text)
        if not isinstance(role, str) or role not in {"user", "assistant", "system"}:
            raise StateError("invalid_role")
        if run_id is not None:
            _identity(run_id)
        with self._transaction(ref.temporary) as connection:
            self._session(connection, ref)
            if run_id is not None:
                self._run(connection, ref, run_id)
            return self._insert_message(connection, ref, role, text, run_id)

    def _insert_message(self, connection: sqlite3.Connection, ref: SessionRef, role: str,
                        text: str, run_id: str | None) -> MessageRecord:
        self._capacity(connection, "SELECT count(*) FROM messages WHERE session_id=?",
                       (ref.session_id,), self.limits.max_messages_per_session)
        sequence = connection.execute(
            "SELECT coalesce(max(sequence), 0)+1 FROM messages WHERE session_id=?",
            (ref.session_id,),
        ).fetchone()[0]
        record = MessageRecord(uuid4().hex, ref.session_id, sequence, role, text,
                               run_id, time.time_ns())
        connection.execute("INSERT INTO messages VALUES(?, ?, ?, ?, ?, ?, ?)",
                           tuple(vars(record).values()))
        return record

    def list_messages(self, ref: SessionRef, *, after: int = 0,
                      limit: int = 100) -> tuple[MessageRecord, ...]:
        self._ref(ref)
        self._page(limit, after)
        with self._transaction(ref.temporary) as connection:
            self._session(connection, ref)
            rows = connection.execute(
                "SELECT * FROM messages WHERE session_id=? AND sequence>? "
                "ORDER BY sequence LIMIT ?", (ref.session_id, after, limit),
            ).fetchall()
            return tuple(MessageRecord(**dict(row)) for row in rows)

    def get_run_messages(self, ref: SessionRef, run_id: str, *, after: int = 0,
                         limit: int = 100) -> tuple[MessageRecord, ...]:
        """Look up this run directly, independent of the session's history pages."""
        self._ref(ref)
        _identity(run_id)
        self._page(limit, after)
        with self._transaction(ref.temporary) as connection:
            self._run(connection, ref, run_id)
            rows = connection.execute(
                "SELECT * FROM messages WHERE session_id=? AND run_id=? AND sequence>? "
                "ORDER BY sequence LIMIT ?", (ref.session_id, run_id, after, limit),
            ).fetchall()
            return tuple(MessageRecord(**dict(row)) for row in rows)

    def list_recent_messages(self, ref: SessionRef, *, limit: int = 100
                             ) -> tuple[MessageRecord, ...]:
        self._ref(ref)
        self._page(limit)
        with self._transaction(ref.temporary) as connection:
            self._session(connection, ref)
            rows = connection.execute(
                "SELECT * FROM messages WHERE session_id=? ORDER BY sequence DESC LIMIT ?",
                (ref.session_id, limit),
            ).fetchall()
            return tuple(MessageRecord(**dict(row)) for row in reversed(rows))

    def create_run(self, ref: SessionRef, *, run_id: str | None = None,
                   user_text: str | None = None) -> RunRecord:
        """Commit queued run, initial event and optional request together.

        A caller-supplied run_id is rejected on duplication, rather than silently
        accepting different text or dispatching a second effect.
        """
        self._ref(ref)
        run_id = uuid4().hex if run_id is None else _identity(run_id)
        if user_text is not None:
            user_text = self._text(user_text)
        with self._transaction(ref.temporary) as connection:
            if self._session(connection, ref)["archived"]:
                raise StateError("session_archived")
            self._capacity(connection, "SELECT count(*) FROM runs WHERE session_id=?",
                           (ref.session_id,), self.limits.max_runs_per_session)
            if connection.execute("SELECT 1 FROM runs WHERE run_id=?", (run_id,)).fetchone():
                raise StateError("run_already_exists")
            now = time.time_ns()
            connection.execute("INSERT INTO runs VALUES(?, ?, 'queued', 'pending', NULL, ?, ?)",
                               (run_id, ref.session_id, now, now))
            if user_text is not None:
                self._insert_message(connection, ref, "user", user_text, run_id)
            self._insert_event(connection, run_id, "run_queued", "{}")
            return RunRecord(**dict(self._run(connection, ref, run_id)))

    def get_run(self, ref: SessionRef, run_id: str) -> RunRecord:
        self._ref(ref)
        _identity(run_id)
        with self._transaction(ref.temporary) as connection:
            return RunRecord(**dict(self._run(connection, ref, run_id)))

    def list_runs(self, ref: SessionRef, *, limit: int = 100,
                  offset: int = 0) -> tuple[RunRecord, ...]:
        self._ref(ref)
        self._page(limit, offset)
        with self._transaction(ref.temporary) as connection:
            self._session(connection, ref)
            rows = connection.execute(
                "SELECT * FROM runs WHERE session_id=? "
                "ORDER BY created_ns, run_id LIMIT ? OFFSET ?",
                (ref.session_id, limit, offset),
            ).fetchall()
            return tuple(RunRecord(**dict(row)) for row in rows)

    def set_run_status(self, ref: SessionRef, run_id: str, status: str) -> RunRecord:
        self._ref(ref)
        _identity(run_id)
        if not isinstance(status, str) or status not in _ACTIVE:
            raise StateError("invalid_run_status")
        with self._transaction(ref.temporary) as connection:
            current = self._run(connection, ref, run_id)
            if status not in _TRANSITIONS.get(current["status"], set()):
                raise StateError("invalid_run_transition")
            self._insert_event(connection, run_id, "run_status", self._json({"status": status}))
            connection.execute("UPDATE runs SET status=?, updated_ns=? WHERE run_id=?",
                               (status, time.time_ns(), run_id))
            return RunRecord(**dict(self._run(connection, ref, run_id)))

    def finish_run(self, ref: SessionRef, run_id: str, *, status: str,
                   receipt: Mapping[str, Any], outcome: str = "known",
                   assistant_text: str | None = None,
                   clear_provider_thread: bool = False) -> RunRecord:
        """Commit receipt, terminal event and optional model message together.

        A capacity/SQL failure rolls back all three. Repeating settlement raises
        run_already_settled and cannot duplicate the assistant message.
        """
        self._ref(ref)
        _identity(run_id)
        if type(clear_provider_thread) is not bool:
            raise StateError("invalid_provider_thread_mode")
        if (not isinstance(status, str) or status not in _TERMINAL
                or not isinstance(outcome, str) or outcome not in {"known", "unknown"}):
            raise StateError("invalid_run_status")
        if status == "interrupted" and outcome != "unknown":
            raise StateError("invalid_run_outcome")
        receipt_json = self._json(receipt)
        if assistant_text is not None:
            assistant_text = self._text(assistant_text)
        with self._transaction(ref.temporary) as connection:
            if self._run(connection, ref, run_id)["status"] not in _ACTIVE:
                raise StateError("run_already_settled")
            connection.execute(
                "UPDATE runs SET status=?, outcome=?, receipt_json=?, updated_ns=? WHERE run_id=?",
                (status, outcome, receipt_json, time.time_ns(), run_id),
            )
            if assistant_text is not None:
                self._insert_message(connection, ref, "assistant", assistant_text, run_id)
            if clear_provider_thread:
                connection.execute("UPDATE sessions SET provider_thread_id=NULL WHERE session_id=?",
                                   (ref.session_id,))
            self._insert_event(connection, run_id, "run_settled",
                               self._json({"status": status, "outcome": outcome}), terminal=True)
            return RunRecord(**dict(self._run(connection, ref, run_id)))

    def _insert_event(self, connection: sqlite3.Connection, run_id: str, kind: str,
                      payload_json: str, *, terminal: bool = False,
                      recovery: bool = False) -> EventRecord:
        # Reserve one slot for settlement/recovery even when an extension fills
        # its event budget. Recovery also records that fact when a host has
        # lowered its budget since the previous run; existing history is retained.
        maximum = self.limits.max_events_per_run - (0 if terminal else 1)
        if not recovery:
            self._capacity(connection, "SELECT count(*) FROM events WHERE run_id=?",
                           (run_id,), maximum)
        sequence = connection.execute(
            "SELECT coalesce(max(sequence), 0)+1 FROM events WHERE run_id=?", (run_id,),
        ).fetchone()[0]
        record = EventRecord(uuid4().hex, run_id, sequence, kind, payload_json, time.time_ns())
        connection.execute("INSERT INTO events VALUES(?, ?, ?, ?, ?, ?)",
                           tuple(vars(record).values()))
        return record

    def append_event(self, ref: SessionRef, run_id: str, kind: str,
                     payload: Mapping[str, Any]) -> EventRecord:
        self._ref(ref)
        _identity(run_id)
        if not isinstance(kind, str) or re.fullmatch(r"[a-z][a-z0-9_.]{0,63}", kind) is None:
            raise StateError("invalid_event_kind")
        if kind in {"run_queued", "run_status", "run_settled", "run_interrupted"}:
            raise StateError("reserved_event_kind")
        payload_json = self._json(payload)
        with self._transaction(ref.temporary) as connection:
            if self._run(connection, ref, run_id)["status"] not in _ACTIVE:
                raise StateError("run_already_settled")
            return self._insert_event(connection, run_id, kind, payload_json)

    def list_events(self, ref: SessionRef, run_id: str, *, after: int = 0,
                    limit: int = 100) -> tuple[EventRecord, ...]:
        self._ref(ref)
        _identity(run_id)
        self._page(limit, after)
        with self._transaction(ref.temporary) as connection:
            self._run(connection, ref, run_id)
            rows = connection.execute(
                "SELECT * FROM events WHERE run_id=? AND sequence>? ORDER BY sequence LIMIT ?",
                (run_id, after, limit),
            ).fetchall()
            return tuple(EventRecord(**dict(row)) for row in rows)

    def _recover(self, connection: sqlite3.Connection) -> None:
        receipt = json.dumps({"reason": "service_restart", "effect_status": "unknown",
                              "replay_allowed": False}, sort_keys=True, separators=(",", ":"))
        connection.execute("BEGIN IMMEDIATE")
        try:
            rows = connection.execute(
                "SELECT run_id, session_id FROM runs WHERE receipt_json IS NULL",
            ).fetchall()
            for row in rows:
                connection.execute(
                    "UPDATE runs SET status='interrupted', outcome='unknown', receipt_json=?, "
                    "updated_ns=? WHERE run_id=?", (receipt, time.time_ns(), row["run_id"]),
                )
                # A lost local owner cannot prove that the corresponding remote
                # turn settled. Preserve the receipt, but never resume that binding.
                connection.execute("UPDATE sessions SET provider_thread_id=NULL WHERE session_id=?",
                                   (row["session_id"],))
                self._insert_event(connection, row["run_id"], "run_interrupted", receipt,
                                   terminal=True, recovery=True)
            connection.execute("COMMIT")
        except BaseException:
            connection.rollback()
            raise
