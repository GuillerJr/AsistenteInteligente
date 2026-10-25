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


def deny_extension_loading(connection):
    """Keep both CPython build variants closed to dynamically loaded code.

    CPython may omit enable_load_extension altogether. A fresh connection still
    must not execute SQL load_extension: the authorizer denies that function even
    on a build where the Python toggle is unavailable. No probe loads a library.
    """
    disable = getattr(connection, "enable_load_extension", None)
    if disable is not None:
        disable(False)

    def authorize(action, first, second, _database, _source):
        if action == sqlite3.SQLITE_FUNCTION and any(
            isinstance(name, str) and name.lower() == "load_extension" for name in (first, second)
        ):
            return sqlite3.SQLITE_DENY
        return sqlite3.SQLITE_OK

    connection.set_authorizer(authorize)


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
_HASH = re.compile(r"[a-f0-9]{64}\Z")
_MAX_SAFE = (1 << 52) - 1
_MAX_SQLITE_INTEGER = (1 << 63) - 1
# Portable history v2 contract; no caller can raise these validation limits.
_COPY_MAX_RECEIPTS = 256
_COPY_MAX_MAPPING_ROWS = 100_000
_COPY_MAX_METADATA_BYTES = 16 * 1024 * 1024
_COPY_MAX_BYTES = 64 * 1024 * 1024
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


def _expected(role, fts, version=None):
    template = sqlite3.connect(":memory:")
    try:
        template.execute("PRAGMA temp_store=MEMORY")
        template.executescript(_MODULES[role]._SCHEMA)
        if role == "memory" and version == 2:
            extension = getattr(memory, "_COPY_SCHEMA", None)
            if not isinstance(extension, str):
                raise SnapshotError("snapshot_schema_unsupported")
            template.executescript(extension)
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


def _history_integer(value, low=0, high=_MAX_SAFE):
    if type(value) is not int or not low <= value <= high:
        raise SnapshotError("snapshot_memory_history_invalid")
    return value


def _history_scope(value):
    parsed = _safe_json(value)
    if not isinstance(parsed, list) or len(parsed) != 3 or parsed[2] is not None:
        raise SnapshotError("snapshot_memory_history_invalid")
    _match(_AGENT, parsed[0])
    _match(_SCOPE, parsed[1])
    if json.dumps(parsed, separators=(",", ":")) != value:
        raise SnapshotError("snapshot_memory_history_invalid")
    return parsed


def _history_json(value):
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode()


def _check_memory_copy_history(connection, check_current):
    """Validate historical snapshot facts, never grants or today's documents.

    Normal edit/delete/clear may change or remove both copied and source notes.
    A receipt is checked against its own immutable map, not live document IDs,
    revisions, hashes, scopes or the currently installed agent registry.
    """
    receipts = connection.execute("SELECT count(*) FROM memory_scope_copies").fetchone()[0]
    mappings = connection.execute("SELECT count(*) FROM memory_scope_copy_documents").fetchone()[0]
    if receipts > _COPY_MAX_RECEIPTS or mappings > _COPY_MAX_MAPPING_ROWS:
        raise SnapshotError("snapshot_memory_history_limit")
    # Exact canonical budget of [receipt_rows,mapping_rows], reserving the longer
    # restored_history label so a legitimate restore cannot exceed the budget.
    metadata_bytes = 7  # [[],[]]
    map_count = 0
    for receipt_index, receipt in enumerate(
        connection.execute("SELECT * FROM memory_scope_copies ORDER BY operation_id")
    ):
        check_current()
        (
            operation,
            request_digest,
            source_key,
            source_generation,
            source_fingerprint,
            destination_key,
            before,
            after,
            document_count,
            byte_length,
            mapping_digest,
            created_ns,
            origin_kind,
        ) = receipt
        _match(_UUID, operation)
        for value in (request_digest, source_fingerprint, mapping_digest):
            _match(_HASH, value)
        source, destination = _history_scope(source_key), _history_scope(destination_key)
        if source[0] != destination[0] or source_key == destination_key:
            raise SnapshotError("snapshot_memory_history_invalid")
        for value in (source_generation, before, after):
            _history_integer(value)
        if after != before + 1 or origin_kind not in {"local", "restored_history"}:
            raise SnapshotError("snapshot_memory_history_invalid")
        _history_integer(document_count, 1, _COPY_MAX_MAPPING_ROWS)
        _history_integer(byte_length, 1, _COPY_MAX_BYTES)
        _history_integer(created_ns, 1, _MAX_SQLITE_INTEGER)
        normalized_receipt = [*receipt[:-1], "restored_history"]
        metadata_bytes += len(_history_json(normalized_receipt)) + bool(receipt_index)
        if metadata_bytes > _COPY_MAX_METADATA_BYTES:
            raise SnapshotError("snapshot_memory_history_limit")
        observed, total = 0, 0
        source_ids, destination_ids = set(), set()
        hasher = hashlib.sha256(b"[")
        for row in connection.execute(
            "SELECT * FROM memory_scope_copy_documents WHERE operation_id=? ORDER BY ordinal",
            (operation,),
        ):
            check_current()
            (
                row_operation,
                ordinal,
                source_id,
                source_revision,
                source_hash,
                source_bytes,
                destination_id,
                destination_revision,
                destination_hash,
                destination_bytes,
            ) = row
            if row_operation != operation or ordinal != observed or observed >= document_count:
                raise SnapshotError("snapshot_memory_history_invalid")
            _history_integer(ordinal, 0, _COPY_MAX_MAPPING_ROWS - 1)
            _match(_UUID, source_id)
            _match(_UUID, destination_id)
            _history_integer(source_revision, 1, _MAX_SQLITE_INTEGER)
            _history_integer(destination_revision, 1, 1)
            _match(_HASH, source_hash)
            _match(_HASH, destination_hash)
            _history_integer(source_bytes, 1, 1_048_576)
            _history_integer(destination_bytes, 1, 1_048_576)
            if (
                source_id == destination_id
                or source_hash != destination_hash
                or source_bytes != destination_bytes
            ):
                raise SnapshotError("snapshot_memory_history_invalid")
            if source_id in destination_ids or destination_id in source_ids:
                raise SnapshotError("snapshot_memory_history_invalid")
            source_ids.add(source_id)
            destination_ids.add(destination_id)
            if observed:
                hasher.update(b",")
            hasher.update(_history_json(row[1:]))
            metadata_bytes += len(_history_json(row)) + bool(map_count)
            if metadata_bytes > _COPY_MAX_METADATA_BYTES:
                raise SnapshotError("snapshot_memory_history_limit")
            observed += 1
            map_count += 1
            total += source_bytes
            if total > byte_length:
                raise SnapshotError("snapshot_memory_history_invalid")
        hasher.update(b"]")
        if (
            observed != document_count
            or total != byte_length
            or hasher.hexdigest() != mapping_digest
        ):
            raise SnapshotError("snapshot_memory_history_invalid")
    if map_count != mappings:
        raise SnapshotError("snapshot_memory_history_invalid")
    check_current()


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
        deny_extension_loading(connection)
        connection.setlimit(sqlite3.SQLITE_LIMIT_LENGTH, limits.max_bytes)
        connection.setlimit(sqlite3.SQLITE_LIMIT_SQL_LENGTH, 65_536)
        connection.execute("PRAGMA temp_store=MEMORY")
        connection.deserialize(data)
        connection.execute("PRAGMA query_only=ON")
        connection.execute("PRAGMA trusted_schema=OFF")
        connection.set_progress_handler(progress, 1000)
        module = _MODULES[role]
        app_id = connection.execute("PRAGMA application_id").fetchone()[0]
        version = connection.execute("PRAGMA user_version").fetchone()[0]
        versions = {1, 2} if role == "memory" else {module._SCHEMA_VERSION}
        if app_id != module._APPLICATION_ID or version not in versions:
            raise SnapshotError("snapshot_schema_unsupported")
        schema = _schema(connection)
        fts = role == "memory" and any(row[1] == "chunk_fts" for row in schema)
        if schema != _expected(role, fts, version):
            raise SnapshotError("snapshot_schema_unsupported")
        checks = iter(connection.execute("PRAGMA integrity_check"))
        if next(checks, None) != ("ok",) or next(checks, None) is not None:
            raise SnapshotError("snapshot_integrity_failed")
        if connection.execute("PRAGMA foreign_key_check").fetchone() is not None:
            raise SnapshotError("snapshot_foreign_key_failed")

        counts = []
        tables = _TABLES[role]
        if role == "memory" and version == 2:
            tables += ("memory_scope_copies", "memory_scope_copy_documents")
        for table in tables:
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
            if version == 2:
                _check_memory_copy_history(connection, check_current)
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
