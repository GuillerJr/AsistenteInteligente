"""Inspect bounded *snapshot bytes*, never an active profile or a SQLite path.

Only the schemas authored by Jarvis are accepted. SHA-256 detects byte changes;
it does not establish the author's identity or the truth of stored content.
The host supplies a continuously valid quiescent-cut/authorization guard.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
import sqlite3
from collections.abc import Callable
from dataclasses import dataclass

from jarvis_core import memory, scheduler, state


class SnapshotError(ValueError):
    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class SnapshotLimits:
    max_bytes: int = 64 * 1024 * 1024
    max_rows: int = 100_000
    max_cell_bytes: int = 16 * 1024 * 1024
    max_vm_steps: int = 20_000_000

    def __post_init__(self):
        if any(type(x) is not int or x <= 0 for x in vars(self).values()):
            raise SnapshotError("snapshot_limits_invalid")


@dataclass(frozen=True)
class SqliteInspection:
    role: str
    application_id: int
    user_version: int
    sha256: str
    byte_length: int
    row_counts: tuple[tuple[str, int], ...]
    agent_ids: tuple[str, ...] = ()
    session_ids: tuple[str, ...] = ()
    memory_scopes: tuple[tuple[str, str, str | None], ...] = ()
    session_agents: tuple[tuple[str, str], ...] = ()

    @property
    def schema_version(self):
        return self.user_version

    @property
    def integrity(self):
        return "ok"

    @property
    def counts(self):
        return dict(self.row_counts)


_MODULES = {"state": state, "memory": memory, "schedules": scheduler}
_TABLES = {
    "state": ("sessions", "runs", "messages", "events"),
    "memory": ("scopes", "documents", "chunks", "metadata"),
    "schedules": ("schedules", "occurrences"),
}
_UUID = re.compile(r"[a-f0-9]{32}\Z")
_AGENT = re.compile(r"[a-z][a-z0-9_-]{0,63}\Z")
_SCOPE = re.compile(r"[A-Za-z0-9_-]{1,64}\Z")
# Tokens preserve quoted literals/identifiers verbatim. In particular whitespace
# within 'jarvis' or the FTS tokenizer is never normalized away.
_SQL_TOKEN = re.compile(r"'(?:[^']|'')*'|\"(?:[^\"]|\"\")*\"|[A-Za-z_][A-Za-z_0-9]*|\d+|\S")


def _tokens(sql):
    return tuple(token if token[0] in "'\"" else token.lower() for token in _SQL_TOKEN.findall(sql))


def _schema(connection):
    rows = connection.execute(
        "SELECT type,name,tbl_name,sql FROM sqlite_schema ORDER BY type,name LIMIT 65"
    ).fetchall()
    if len(rows) > 64:
        raise SnapshotError("snapshot_schema_unsupported")
    return tuple(
        (kind, name, table, None if sql is None else _tokens(sql))
        for kind, name, table, sql in rows
    )


def _expected(role, fts):
    template = sqlite3.connect(":memory:")
    try:
        template.executescript(_MODULES[role]._SCHEMA)
        if fts:
            template.execute(
                "CREATE VIRTUAL TABLE chunk_fts USING "
                "fts5(text, tokenize='unicode61 remove_diacritics 2')"
            )
        return _schema(template)
    except sqlite3.Error:
        raise SnapshotError("snapshot_sqlite_capability_unavailable") from None
    finally:
        template.close()


def _safe_json(text):
    def pairs(rows):
        result = {}
        for key, value in rows:
            if key in result:
                raise SnapshotError("snapshot_data_invalid")
            result[key] = value
        return result

    try:
        value = json.loads(
            text,
            object_pairs_hook=pairs,
            parse_constant=lambda _: (_ for _ in ()).throw(ValueError()),
        )
    except (ValueError, TypeError, UnicodeError, RecursionError):
        raise SnapshotError("snapshot_data_invalid") from None

    def check(item, depth=0):
        if depth > 20:
            raise SnapshotError("snapshot_data_invalid")
        if type(item) is float and not math.isfinite(item):
            raise SnapshotError("snapshot_data_invalid")
        if isinstance(item, (list, dict)):
            if len(item) > 10_000:
                raise SnapshotError("snapshot_data_invalid")
            for child in item.values() if isinstance(item, dict) else item:
                check(child, depth + 1)

    check(value)
    return value


def _match(pattern, value):
    if not isinstance(value, str) or not pattern.fullmatch(value):
        raise SnapshotError("snapshot_data_invalid")
    return value


def _check_schedule_shape(value):
    if not isinstance(value, dict) or set(value) - {
        "kind",
        "timezone",
        "at",
        "interval_seconds",
        "local_time",
    }:
        raise SnapshotError("snapshot_data_invalid")
    kind = value.get("kind")
    at, interval, local = (value.get(key) for key in ("at", "interval_seconds", "local_time"))
    zone = value.get("timezone", "UTC")
    if (
        not isinstance(zone, str)
        or len(zone) > 128
        or not re.fullmatch(r"[A-Za-z0-9_+\-]+(?:/[A-Za-z0-9_+\-]+)*", zone)
    ):
        raise SnapshotError("snapshot_data_invalid")
    if at is not None and (type(at) not in {int, float} or not 0 <= at < 253402214400):
        raise SnapshotError("snapshot_data_invalid")
    if kind == "once":
        valid = at is not None and interval is None and local is None
    elif kind == "interval":
        valid = type(interval) is int and 1 <= interval <= 31_536_000 and local is None
    else:
        valid = (
            kind == "daily"
            and at is None
            and interval is None
            and isinstance(local, str)
            and re.fullmatch(r"(?:[01]\d|2[0-3]):[0-5]\d", local) is not None
        )
    if not valid:
        raise SnapshotError("snapshot_data_invalid")


def inspect_sqlite_snapshot(
    role: str,
    data: bytes,
    *,
    check_current: Callable[[], None],
    limits: SnapshotLimits | None = None,
) -> SqliteInspection:
    """Validate one immutable image; the caller owns path/quiescence attestation.

    No constructor, recovery, migration, filesystem or network access occurs.
    Cancellation raised by the supplied guard propagates unchanged, including
    when SQLite first reports the progress-handler interruption.
    """
    limits = limits or SnapshotLimits()
    if role not in _MODULES:
        raise SnapshotError("snapshot_role_unsupported")
    if type(data) is not bytes or not 512 <= len(data) <= limits.max_bytes:
        raise SnapshotError("snapshot_size_invalid")
    check_current()
    if not data.startswith(b"SQLite format 3\0") or data[18:20] != b"\x01\x01":
        # WAL headers need a real host-owned online snapshot, not immutable=1.
        raise SnapshotError("snapshot_image_not_standalone")
    connection = sqlite3.connect(":memory:")
    interrupted = []
    steps = 0

    def progress():
        nonlocal steps
        steps += 1000
        try:
            check_current()
            if steps > limits.max_vm_steps:
                raise SnapshotError("snapshot_work_limit")
        except BaseException as error:
            interrupted.append(error)
            return 1
        return 0

    try:
        if not hasattr(connection, "deserialize"):
            raise SnapshotError("snapshot_sqlite_capability_unavailable")
        connection.enable_load_extension(False)
        connection.setlimit(sqlite3.SQLITE_LIMIT_LENGTH, limits.max_bytes)
        connection.setlimit(sqlite3.SQLITE_LIMIT_SQL_LENGTH, 65_536)
        connection.deserialize(data)
        connection.execute("PRAGMA query_only=ON")
        connection.execute("PRAGMA trusted_schema=OFF")
        connection.set_progress_handler(progress, 1000)
        module = _MODULES[role]
        app_id = connection.execute("PRAGMA application_id").fetchone()[0]
        version = connection.execute("PRAGMA user_version").fetchone()[0]
        if app_id != module._APPLICATION_ID or version != module._SCHEMA_VERSION:
            raise SnapshotError("snapshot_schema_unsupported")
        schema = _schema(connection)
        fts = role == "memory" and any(row[1] == "chunk_fts" for row in schema)
        if schema != _expected(role, fts):
            raise SnapshotError("snapshot_schema_unsupported")
        checks = iter(connection.execute("PRAGMA integrity_check"))
        if next(checks, None) != ("ok",) or next(checks, None) is not None:
            raise SnapshotError("snapshot_integrity_failed")
        if connection.execute("PRAGMA foreign_key_check").fetchone() is not None:
            raise SnapshotError("snapshot_foreign_key_failed")

        counts = []
        for table in _TABLES[role]:
            check_current()
            # Names are constants, never caller-controlled SQL identifiers.
            count = connection.execute(f'SELECT count(*) FROM "{table}"').fetchone()[0]
            if count > limits.max_rows:
                raise SnapshotError("snapshot_row_limit")
            counts.append((table, count))
            for row in connection.execute(f'SELECT * FROM "{table}"'):
                check_current()
                for cell in row:
                    if isinstance(cell, bytes) or (
                        isinstance(cell, str) and len(cell.encode()) > limits.max_cell_bytes
                    ):
                        raise SnapshotError("snapshot_cell_limit")

        agents, sessions, scopes, session_agents = set(), set(), [], []
        if role == "state":
            for sid, agent, parent in connection.execute(
                "SELECT session_id,agent_id,parent_session_id FROM sessions"
            ):
                sessions.add(_match(_UUID, sid))
                agents.add(_match(_AGENT, agent))
                session_agents.append((sid, agent))
                if parent is not None:
                    _match(_UUID, parent)
            for run, receipt in connection.execute("SELECT run_id,receipt_json FROM runs"):
                _match(_UUID, run)
                if receipt is not None and not isinstance(_safe_json(receipt), dict):
                    raise SnapshotError("snapshot_data_invalid")
            for (payload,) in connection.execute("SELECT payload_json FROM events"):
                if not isinstance(_safe_json(payload), dict):
                    raise SnapshotError("snapshot_data_invalid")
            counts.append(
                (
                    "unsettled_runs",
                    connection.execute(
                        "SELECT count(*) FROM runs WHERE receipt_json IS NULL"
                    ).fetchone()[0],
                )
            )
        elif role == "memory":
            document_scopes = {
                row[0] for row in connection.execute("SELECT DISTINCT scope_key FROM documents")
            }
            metadata = dict(connection.execute("SELECT key,value FROM metadata"))
            if metadata != {"search_mode": "fts5_lexical" if fts else "lexical_scan"}:
                raise SnapshotError("snapshot_data_invalid")
            for (scope,) in connection.execute("SELECT scope_key FROM scopes"):
                parsed = _safe_json(scope)
                if not isinstance(parsed, list) or len(parsed) != 3:
                    raise SnapshotError("snapshot_data_invalid")
                agent, workspace, session = parsed
                _match(_AGENT, agent)
                if scope in document_scopes:
                    agents.add(agent)
                if json.dumps(parsed, separators=(",", ":")) != scope:
                    raise SnapshotError("snapshot_data_invalid")
                _match(_SCOPE, workspace)
                if session is not None:
                    _match(_SCOPE, session)
                if scope in document_scopes:
                    scopes.append((agent, workspace, session))
            for text, digest, size, doc, chunks in connection.execute(
                "SELECT text,text_sha256,byte_length,document_id,chunk_count FROM documents"
            ):
                _match(_UUID, doc)
                if (
                    not isinstance(text, str)
                    or hashlib.sha256(text.encode()).hexdigest() != digest
                    or len(text.encode()) != size
                    or chunks
                    != connection.execute(
                        "SELECT count(*) FROM chunks WHERE document_id=?", (doc,)
                    ).fetchone()[0]
                ):
                    raise SnapshotError("snapshot_memory_content_invalid")
                previous_start, previous_end, n = -1, 0, 0
                raw_text = text.encode()
                for ordinal, start, end, chunk in connection.execute(
                    "SELECT ordinal,start_byte,end_byte,text FROM chunks "
                    "WHERE document_id=? ORDER BY ordinal",
                    (doc,),
                ):
                    if (
                        type(start) is not int
                        or type(end) is not int
                        or ordinal != n
                        or not 0 <= start < end <= len(raw_text)
                        or start <= previous_start
                        or start > previous_end
                        or end <= previous_end
                        or not isinstance(chunk, str)
                        or raw_text[start:end] != chunk.encode()
                    ):
                        raise SnapshotError("snapshot_memory_content_invalid")
                    previous_start, previous_end, n = start, end, n + 1
                if n != chunks or previous_end != len(raw_text):
                    raise SnapshotError("snapshot_memory_content_invalid")
            if fts:
                mismatch = connection.execute(
                    "SELECT 1 FROM chunks c LEFT JOIN chunk_fts f ON f.rowid=c.chunk_id "
                    "WHERE f.rowid IS NULL OR f.text!=c.text LIMIT 1"
                ).fetchone()
                extra = connection.execute(
                    "SELECT 1 FROM chunk_fts f LEFT JOIN chunks c ON c.chunk_id=f.rowid "
                    "WHERE c.chunk_id IS NULL LIMIT 1"
                ).fetchone()
                if mismatch or extra:
                    raise SnapshotError("snapshot_memory_content_invalid")
        else:
            for (agent,) in connection.execute("SELECT agent_id FROM schedules"):
                agents.add(_match(_AGENT, agent))
            for (spec,) in connection.execute("SELECT spec_json FROM schedules"):
                _check_schedule_shape(_safe_json(spec))
            counts.append(
                (
                    "unsettled_occurrences",
                    connection.execute(
                        "SELECT count(*) FROM occurrences WHERE status IN ('claimed','running')"
                    ).fetchone()[0],
                )
            )
        check_current()
        return SqliteInspection(
            role,
            app_id,
            version,
            hashlib.sha256(data).hexdigest(),
            len(data),
            tuple(counts),
            tuple(sorted(agents)),
            tuple(sorted(sessions)),
            tuple(sorted(scopes, key=repr)),
            tuple(sorted(session_agents)),
        )
    except (sqlite3.Error, UnicodeError, TypeError, ValueError) as error:
        if interrupted:
            raise interrupted[0] from None
        if isinstance(error, SnapshotError):
            raise
        raise SnapshotError("snapshot_invalid") from None
    finally:
        connection.close()
