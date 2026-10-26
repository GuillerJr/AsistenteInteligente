"""Private, scoped lexical memory for the original Jarvis baseline.

The host supplies MemoryScope; model text never chooses its namespace. Sources are
labels on caller-provided text, not permission to open a URL or file. Citations
verify exact bytes of the stored snapshot, not the external source's truth.
Temporary scopes use only SQLite :memory:, even when durable storage exists.
Search reports FTS5 lexical retrieval or bounded lexical scan; no embeddings,
network, imported owner history or inference-provider fallback exists.
"""

from __future__ import annotations

import fcntl
import hashlib
import json
import math
import os
import re
import sqlite3
import stat
import threading
import time
import unicodedata
from collections.abc import Callable, Iterator, Sequence
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field
from pathlib import Path
from uuid import uuid4

from jarvis_core.state import StateError, ensure_private_directory

_APPLICATION_ID = 0x4A524D4D  # ASCII JRMM: distinct from the conversation database.
_SCHEMA_VERSION = 1
_SCHEMA = """
CREATE TABLE scopes (scope_key TEXT PRIMARY KEY, generation INTEGER NOT NULL);
CREATE TABLE documents (
    document_id TEXT PRIMARY KEY, scope_key TEXT NOT NULL REFERENCES scopes,
    key TEXT NOT NULL, title TEXT NOT NULL, source_uri TEXT NOT NULL,
    revision INTEGER NOT NULL CHECK(revision > 0), text TEXT NOT NULL,
    text_sha256 TEXT NOT NULL, byte_length INTEGER NOT NULL,
    chunk_count INTEGER NOT NULL, created_ns INTEGER NOT NULL, updated_ns INTEGER NOT NULL,
    UNIQUE(scope_key, key)
);
CREATE TABLE chunks (
    chunk_id INTEGER PRIMARY KEY, document_id TEXT NOT NULL REFERENCES documents ON DELETE CASCADE,
    ordinal INTEGER NOT NULL, start_byte INTEGER NOT NULL, end_byte INTEGER NOT NULL,
    text TEXT NOT NULL, UNIQUE(document_id, ordinal)
);
CREATE TABLE metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE INDEX documents_scope ON documents(scope_key, key);
"""


_COPY_SCHEMA_VERSION = 2
_COPY_SCHEMA = """
CREATE TABLE memory_scope_copies (
    operation_id TEXT PRIMARY KEY, request_digest TEXT NOT NULL,
    source_scope_key TEXT NOT NULL,
    source_generation INTEGER NOT NULL CHECK(source_generation >= 0),
    source_fingerprint TEXT NOT NULL, destination_scope_key TEXT NOT NULL,
    destination_generation_before INTEGER NOT NULL CHECK(destination_generation_before >= 0),
    destination_generation_after INTEGER NOT NULL,
    document_count INTEGER NOT NULL CHECK(document_count > 0),
    byte_length INTEGER NOT NULL CHECK(byte_length > 0), mapping_sha256 TEXT NOT NULL,
    created_ns INTEGER NOT NULL CHECK(created_ns > 0),
    origin_kind TEXT NOT NULL CHECK(origin_kind IN ('local','restored_history')),
    CHECK(source_scope_key != destination_scope_key),
    CHECK(destination_generation_after = destination_generation_before + 1)
);
CREATE TABLE memory_scope_copy_documents (
    operation_id TEXT NOT NULL REFERENCES memory_scope_copies(operation_id) ON DELETE RESTRICT,
    ordinal INTEGER NOT NULL CHECK(ordinal >= 0), source_document_id TEXT NOT NULL,
    source_revision INTEGER NOT NULL CHECK(source_revision > 0), source_text_sha256 TEXT NOT NULL,
    source_byte_length INTEGER NOT NULL CHECK(source_byte_length > 0),
    destination_document_id TEXT NOT NULL, destination_revision INTEGER NOT NULL
        CHECK(destination_revision = 1), destination_text_sha256 TEXT NOT NULL,
    destination_byte_length INTEGER NOT NULL,
    PRIMARY KEY(operation_id, ordinal), UNIQUE(operation_id, source_document_id),
    UNIQUE(operation_id, destination_document_id),
    CHECK(source_document_id != destination_document_id),
    CHECK(source_text_sha256 = destination_text_sha256),
    CHECK(source_byte_length = destination_byte_length)
);
"""


class MemoryError(Exception):
    """Stable public codes without database paths, SQL or user content."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def _utf8(value: str, code: str) -> bytes:
    try:
        return value.encode()
    except UnicodeError:
        raise MemoryError(code) from None


def _id(value: str) -> str:
    if not isinstance(value, str) or re.fullmatch(r"[A-Za-z0-9_-]{1,64}", value) is None:
        raise MemoryError("invalid_scope")
    return value


@dataclass(frozen=True)
class MemoryScope:
    agent_id: str
    workspace_id: str
    session_id: str | None = None
    temporary: bool = False

    def __post_init__(self) -> None:
        _id(self.agent_id)
        _id(self.workspace_id)
        if self.session_id is not None:
            _id(self.session_id)
        if type(self.temporary) is not bool or (self.temporary and self.session_id is None):
            raise MemoryError("invalid_scope")

    @property
    def key(self) -> str:
        return json.dumps([self.agent_id, self.workspace_id, self.session_id],
                          separators=(",", ":"))


@dataclass(frozen=True)
class MemoryLimits:
    max_scopes: int = 10000
    max_documents: int = 10000
    max_documents_per_scope: int = 1000
    max_scope_bytes: int = 16 * 1024 * 1024
    max_total_bytes: int = 64 * 1024 * 1024
    max_document_bytes: int = 262144
    chunk_bytes: int = 2048
    max_page_size: int = 100
    max_query_bytes: int = 512
    max_query_terms: int = 8
    excerpt_bytes: int = 768

    def __post_init__(self) -> None:
        if any(type(v) is not int or v < 1 for v in vars(self).values()):
            raise MemoryError("invalid_limits")
        if (not 512 <= self.chunk_bytes <= 32768 or self.max_document_bytes > 1_048_576
                or self.max_scope_bytes > 256 * 1024 * 1024 or self.max_documents > 100000
                or self.max_total_bytes > 256 * 1024 * 1024
                or self.max_scopes > 100000
                or self.max_documents_per_scope > self.max_documents
                or not 64 <= self.excerpt_bytes <= self.chunk_bytes
                or self.max_page_size > 500 or self.max_query_terms > 16
                or self.max_query_bytes > 4096):
            raise MemoryError("invalid_limits")


@dataclass(frozen=True)
class DocumentRecord:
    document_id: str
    key: str
    title: str
    source_uri: str
    revision: int
    text_sha256: str
    byte_length: int
    chunk_count: int
    created_ns: int
    updated_ns: int
    provenance: str = field(default="provided_text", init=False)
    source_verified: bool = field(default=False, init=False)


@dataclass(frozen=True)
class DocumentContent:
    document: DocumentRecord
    text: str


@dataclass(frozen=True)
class Citation:
    document_id: str
    revision: int
    text_sha256: str
    start_byte: int
    end_byte: int
    excerpt_sha256: str
    source_uri: str

    def __post_init__(self) -> None:
        if (not isinstance(self.document_id, str)
                or re.fullmatch(r"[a-f0-9]{32}", self.document_id) is None
                or type(self.revision) is not int or self.revision < 1
                or any(not isinstance(digest, str) or re.fullmatch(r"[a-f0-9]{64}", digest) is None
                       for digest in (self.text_sha256, self.excerpt_sha256))
                or type(self.start_byte) is not int or type(self.end_byte) is not int
                or not 0 <= self.start_byte < self.end_byte <= 1_048_576
                or not isinstance(self.source_uri, str)
                or len(_utf8(self.source_uri, "invalid_citation")) > 2048):
            raise MemoryError("invalid_citation")


@dataclass(frozen=True)
class SearchHit:
    document: DocumentRecord
    excerpt: str
    citation: Citation
    score: float


@dataclass(frozen=True)
class SearchResult:
    hits: tuple[SearchHit, ...]
    mode: str
    generation: int
    next_offset: int
    has_more: bool
    query_tokens: tuple[str, ...]


@dataclass(frozen=True)
class DeleteReceipt:
    document_id: str
    revision: int
    generation: int
    deleted: bool = field(default=True, init=False)


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _boundary(data: bytes, position: int, *, forward: bool = False) -> int:
    position = max(0, min(position, len(data)))
    while 0 < position < len(data) and data[position] & 0xC0 == 0x80:
        position += 1 if forward else -1
    return position


def _chunks(data: bytes, size: int) -> Iterator[tuple[int, int, str]]:
    start = 0
    overlap = min(256, size // 4)
    while start < len(data):
        end = _boundary(data, min(start + size, len(data)))
        yield start, end, data[start:end].decode()
        if end == len(data):
            break
        start = _boundary(data, end - overlap, forward=True)


def _normalize(value: str) -> str:
    return "".join(char for char in unicodedata.normalize("NFKD", value.casefold())
                   if not unicodedata.combining(char))


def _tokens(value: str) -> tuple[str, ...]:
    return tuple(dict.fromkeys(re.findall(r"[^\W_]+", _normalize(value), re.UNICODE)))


def _match_position(text: str, tokens: tuple[str, ...]) -> int:
    normalized, positions, offset = [], [], 0
    for char in text:
        part = _normalize(char)
        normalized.append(part)
        positions.extend([offset] * len(part))
        offset += len(char.encode())
    searchable = "".join(normalized)
    locations = [match.start() for token in tokens
                 if (match := re.search(r"(?<!\w)" + re.escape(token) + r"(?!\w)", searchable))]
    return positions[min(locations)] if locations else 0


def _parent_identity(path: Path) -> tuple[int, int]:
    current = Path(path.anchor)
    for part in path.parts[1:]:
        current /= part
        info = current.lstat()
        if not stat.S_ISDIR(info.st_mode):
            raise MemoryError("unsafe_memory_path")
    info = path.lstat()
    if info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) != 0o700:
        raise MemoryError("unsafe_memory_permissions")
    return info.st_dev, info.st_ino


class MemoryStore:
    def __init__(self, path: str | Path, *, limits: MemoryLimits | None = None) -> None:
        try:
            self.path = Path(path)
        except TypeError:
            raise MemoryError("invalid_memory_path") from None
        if (not self.path.is_absolute() or ".." in self.path.parts
                or self.path.suffix != ".sqlite3"
                or len(_utf8(self.path.name, "invalid_memory_path")) > 128):
            raise MemoryError("invalid_memory_path")
        self.limits = limits or MemoryLimits()
        if not isinstance(self.limits, MemoryLimits):
            raise MemoryError("invalid_limits")
        self._mutex = threading.RLock()
        self._disk: sqlite3.Connection | None = None
        self._memory: sqlite3.Connection | None = None
        self._file_fd: int | None = None
        self._lock_fd: int | None = None
        self._parent: tuple[int, int] | None = None
        self._closed = False
        self._copy_owner = None

    def __enter__(self) -> MemoryStore:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def close(self) -> None:
        with self._mutex:
            if self._copy_owner is not None and not self._copy_owner.closed:
                raise MemoryError("memory_copy_cleanup_incomplete")
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
    def _private_file(path: Path, *, create: bool = True) -> tuple[int, bool]:
        flags = os.O_RDWR | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC
        if create:
            try:
                descriptor = os.open(path, flags | os.O_CREAT | os.O_EXCL, 0o600)
                created = True
            except FileExistsError:
                descriptor = os.open(path, flags)
                created = False
        else:
            descriptor, created = os.open(path, flags), False
        try:
            info = os.fstat(descriptor)
            if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid()
                    or stat.S_IMODE(info.st_mode) != 0o600 or info.st_nlink != 1):
                raise MemoryError("unsafe_memory_permissions")
            return descriptor, created
        except BaseException:
            os.close(descriptor)
            raise

    def _check_disk(self) -> None:
        if _parent_identity(self.path.parent) != self._parent:
            raise MemoryError("unsafe_memory_path")
        for path, descriptor in ((self.path, self._file_fd),
                                 (self.path.with_name(self.path.name + ".lock"), self._lock_fd)):
            info, opened = path.lstat(), os.fstat(descriptor)
            if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid()
                    or stat.S_IMODE(info.st_mode) != 0o600 or info.st_nlink != 1
                    or (info.st_dev, info.st_ino) != (opened.st_dev, opened.st_ino)):
                raise MemoryError("unsafe_memory_path")
        for suffix in ("-journal", "-wal", "-shm"):
            path = self.path.with_name(self.path.name + suffix)
            try:
                info = path.lstat()
            except FileNotFoundError:
                continue
            if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid()
                    or stat.S_IMODE(info.st_mode) != 0o600 or info.st_nlink != 1):
                raise MemoryError("unsafe_memory_path")

    @staticmethod
    def _initialize(connection: sqlite3.Connection) -> None:
        connection.executescript("BEGIN IMMEDIATE;" + _SCHEMA)
        try:
            try:
                connection.execute(
                    "CREATE VIRTUAL TABLE chunk_fts USING "
                    "fts5(text, tokenize='unicode61 remove_diacritics 2')",
                )
                mode = "fts5_lexical"
            except sqlite3.OperationalError as error:
                if "no such module: fts5" not in str(error).lower():
                    raise
                mode = "lexical_scan"
            connection.execute("INSERT INTO metadata VALUES('search_mode', ?)", (mode,))
            connection.execute(f"PRAGMA application_id={_APPLICATION_ID}")
            connection.execute(f"PRAGMA user_version={_SCHEMA_VERSION}")
            connection.execute("COMMIT")
        except BaseException:
            if connection.in_transaction:
                connection.rollback()
            raise

    @staticmethod
    def _configure(connection: sqlite3.Connection) -> None:
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA trusted_schema=OFF")
        connection.execute("PRAGMA temp_store=MEMORY")
        connection.execute("PRAGMA busy_timeout=1000")

    def _open_disk(self, *, create: bool = True) -> sqlite3.Connection:
        if self._disk is not None:
            self._check_disk()
            return self._disk
        connection = None
        try:
            ensure_private_directory(self.path.parent)
            self._parent = _parent_identity(self.path.parent)
            self._lock_fd, _ = self._private_file(self.path.with_name(self.path.name + ".lock"))
            try:
                fcntl.flock(self._lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise MemoryError("memory_in_use") from None
            self._file_fd, created = self._private_file(self.path, create=create)
            self._check_disk()
            connection = sqlite3.connect(str(self.path), isolation_level=None,
                                         check_same_thread=False, timeout=1)
            self._check_disk()
            self._configure(connection)
            if created:
                self._initialize(connection)
            elif (connection.execute("PRAGMA application_id").fetchone()[0] != _APPLICATION_ID
                    or connection.execute("PRAGMA user_version").fetchone()[0]
                    not in {1, _COPY_SCHEMA_VERSION}
                    or connection.execute("PRAGMA quick_check(1)").fetchone()[0] != "ok"
                    or connection.execute("PRAGMA foreign_key_check").fetchone() is not None):
                raise MemoryError("invalid_memory_database")
            if connection.execute("PRAGMA journal_mode=DELETE").fetchone()[0] != "delete":
                raise MemoryError("invalid_memory_database")
            connection.execute("PRAGMA secure_delete=ON")
            _copy_assert_schema(connection)
            self._mode(connection)
            self._disk = connection
            return connection
        except StateError:
            raise MemoryError("unsafe_memory_path") from None
        except (OSError, sqlite3.Error):
            if connection is not None:
                connection.close()
            self._release_files()
            raise MemoryError("memory_unavailable") from None
        except MemoryError:
            if connection is not None:
                connection.close()
            self._release_files()
            raise

    def _connection(self, temporary: bool) -> sqlite3.Connection:
        if self._copy_owner is not None and not self._copy_owner.closed:
            raise MemoryError("memory_copy_cleanup_incomplete")
        if self._closed:
            raise MemoryError("memory_closed")
        if temporary:
            if self._memory is None:
                self._memory = sqlite3.connect(":memory:", isolation_level=None,
                                               check_same_thread=False)
                self._configure(self._memory)
                self._initialize(self._memory)
            return self._memory
        return self._open_disk()

    @contextmanager
    def _transaction(self, scope: MemoryScope) -> Iterator[sqlite3.Connection]:
        if not isinstance(scope, MemoryScope):
            raise MemoryError("invalid_scope")
        with self._mutex:
            connection = None
            try:
                connection = self._connection(scope.temporary)
                connection.execute("BEGIN IMMEDIATE")
                yield connection
                connection.execute("COMMIT")
            except (OSError, sqlite3.Error):
                if connection is not None and connection.in_transaction:
                    connection.rollback()
                raise MemoryError("memory_unavailable") from None
            except BaseException:
                if connection is not None and connection.in_transaction:
                    connection.rollback()
                raise

    @staticmethod
    def _mode(connection: sqlite3.Connection) -> str:
        row = connection.execute("SELECT value FROM metadata WHERE key='search_mode'").fetchone()
        if row is None or row[0] not in {"fts5_lexical", "lexical_scan"}:
            raise MemoryError("invalid_memory_database")
        return row[0]

    @staticmethod
    def _generation(connection: sqlite3.Connection, scope: MemoryScope) -> int:
        row = connection.execute("SELECT generation FROM scopes WHERE scope_key=?", (scope.key,))
        value = row.fetchone()
        generation = value[0] if value else 0
        if type(generation) is not int or generation < 0:
            raise MemoryError("invalid_memory_database")
        return generation

    @staticmethod
    def _key(key: str) -> str:
        if (not isinstance(key, str) or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_./-]{0,127}", key)
                is None or ".." in key.split("/") or "//" in key):
            raise MemoryError("invalid_document_key")
        return key

    @staticmethod
    def _revision(value: int | None) -> None:
        if value is not None and (type(value) is not int or value < 1):
            raise MemoryError("invalid_revision")

    @staticmethod
    def _document(connection: sqlite3.Connection, scope: MemoryScope, key: str) -> sqlite3.Row:
        row = connection.execute("SELECT * FROM documents WHERE scope_key=? AND key=?",
                                 (scope.key, key)).fetchone()
        if row is None:
            raise MemoryError("document_not_found")
        return row

    @staticmethod
    def _record(row: sqlite3.Row) -> DocumentRecord:
        if (not isinstance(row["document_id"], str)
                or re.fullmatch(r"[a-f0-9]{32}", row["document_id"]) is None
                or not isinstance(row["key"], str)
                or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_./-]{0,127}", row["key"]) is None
                or not isinstance(row["title"], str)
                or len(_utf8(row["title"], "invalid_memory_database")) > 512
                or not isinstance(row["source_uri"], str)
                or len(_utf8(row["source_uri"], "invalid_memory_database")) > 2048
                or re.fullmatch(r"[A-Za-z][A-Za-z0-9+.-]{0,31}://[^\x00-\x1f\x7f]+",
                                row["source_uri"]) is None
                or not isinstance(row["text_sha256"], str)
                or re.fullmatch(r"[a-f0-9]{64}", row["text_sha256"]) is None
                or any(type(row[name]) is not int or row[name] < 1 for name in (
                    "revision", "byte_length", "chunk_count", "created_ns", "updated_ns",
                )) or row["byte_length"] > 1_048_576 or row["chunk_count"] > 4096):
            raise MemoryError("invalid_memory_database")
        return DocumentRecord(**{key: row[key] for key in (
            "document_id", "key", "title", "source_uri", "revision", "text_sha256",
            "byte_length", "chunk_count", "created_ns", "updated_ns",
        )})

    @staticmethod
    def _assert_text(row: sqlite3.Row) -> bytes:
        if not isinstance(row["text"], str):
            raise MemoryError("invalid_memory_database")
        data = _utf8(row["text"], "invalid_memory_database")
        if len(data) != row["byte_length"] or _sha(data) != row["text_sha256"]:
            raise MemoryError("invalid_memory_database")
        return data

    def upsert(
        self, scope: MemoryScope, key: str, text: str, *, source_uri: str,
        title: str = "", expected_revision: int | None = None,
    ) -> DocumentRecord:
        key = self._key(key)
        self._revision(expected_revision)
        if (not isinstance(text, str) or not text.strip() or "\0" in text
                or len(_utf8(text, "invalid_document_text")) > self.limits.max_document_bytes):
            raise MemoryError("invalid_document_text")
        if (not isinstance(title, str)
                or len(_utf8(title, "invalid_document_source")) > 512 or "\0" in title
                or not isinstance(source_uri, str)
                or len(_utf8(source_uri, "invalid_document_source")) > 2048
                or re.fullmatch(r"[A-Za-z][A-Za-z0-9+.-]{0,31}://[^\x00-\x1f\x7f]+", source_uri)
                is None):
            raise MemoryError("invalid_document_source")
        data, now = text.encode(), time.time_ns()
        pieces = tuple(_chunks(data, self.limits.chunk_bytes))
        with self._transaction(scope) as connection:
            if (connection.execute("SELECT 1 FROM scopes WHERE scope_key=?", (scope.key,))
                    .fetchone() is None and connection.execute("SELECT count(*) FROM scopes")
                    .fetchone()[0] >= self.limits.max_scopes):
                raise MemoryError("memory_capacity_exceeded")
            connection.execute("INSERT OR IGNORE INTO scopes VALUES(?, 0)", (scope.key,))
            old = connection.execute("SELECT * FROM documents WHERE scope_key=? AND key=?",
                                     (scope.key, key)).fetchone()
            if expected_revision is not None and (
                old is None or old["revision"] != expected_revision
            ):
                raise MemoryError("revision_conflict")
            if old is not None:
                self._assert_text(old)
                if (old["text"] == text and old["source_uri"] == source_uri
                        and old["title"] == title):
                    return self._record(old)
            counts = connection.execute(
                "SELECT count(*), coalesce(sum(byte_length),0) FROM documents WHERE scope_key=?",
                (scope.key,),
            ).fetchone()
            total_bytes = connection.execute(
                "SELECT coalesce(sum(byte_length),0) FROM documents",
            ).fetchone()[0]
            if ((old is None and (counts[0] >= self.limits.max_documents_per_scope
                                  or connection.execute("SELECT count(*) FROM documents")
                                  .fetchone()[0] >= self.limits.max_documents))
                    or counts[1] - (old["byte_length"] if old else 0) + len(data)
                    > self.limits.max_scope_bytes
                    or total_bytes - (old["byte_length"] if old else 0) + len(data)
                    > self.limits.max_total_bytes):
                raise MemoryError("memory_capacity_exceeded")
            document_id = old["document_id"] if old else uuid4().hex
            revision = old["revision"] + 1 if old else 1
            mode = self._mode(connection)
            if old is not None and mode == "fts5_lexical":
                connection.execute("DELETE FROM chunk_fts WHERE rowid IN "
                                   "(SELECT chunk_id FROM chunks WHERE document_id=?)",
                                   (document_id,))
            connection.execute("DELETE FROM chunks WHERE document_id=?", (document_id,))
            if old is None:
                connection.execute(
                    "INSERT INTO documents VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                    (document_id, scope.key, key, title, source_uri, revision, text,
                     _sha(data), len(data), len(pieces), now, now),
                )
            else:
                connection.execute(
                    "UPDATE documents SET title=?,source_uri=?,revision=?,text=?,text_sha256=?,"
                    "byte_length=?,chunk_count=?,updated_ns=? WHERE document_id=?",
                    (title, source_uri, revision, text, _sha(data), len(data), len(pieces), now,
                     document_id),
                )
            for ordinal, (start, end, chunk) in enumerate(pieces):
                cursor = connection.execute("INSERT INTO chunks VALUES(NULL,?,?,?,?,?)",
                                            (document_id, ordinal, start, end, chunk))
                if mode == "fts5_lexical":
                    connection.execute("INSERT INTO chunk_fts(rowid,text) VALUES(?,?)",
                                       (cursor.lastrowid, chunk))
            connection.execute("UPDATE scopes SET generation=generation+1 WHERE scope_key=?",
                               (scope.key,))
            return self._record(self._document(connection, scope, key))

    def get(self, scope: MemoryScope, key: str) -> DocumentContent:
        key = self._key(key)
        with self._transaction(scope) as connection:
            row = self._document(connection, scope, key)
            self._assert_text(row)
            return DocumentContent(self._record(row), row["text"])

    def delete(self, scope: MemoryScope, key: str, *,
               expected_revision: int | None = None) -> DeleteReceipt:
        key = self._key(key)
        self._revision(expected_revision)
        with self._transaction(scope) as connection:
            row = self._document(connection, scope, key)
            if expected_revision is not None and row["revision"] != expected_revision:
                raise MemoryError("revision_conflict")
            if self._mode(connection) == "fts5_lexical":
                connection.execute("DELETE FROM chunk_fts WHERE rowid IN "
                                   "(SELECT chunk_id FROM chunks WHERE document_id=?)",
                                   (row["document_id"],))
            connection.execute("DELETE FROM documents WHERE document_id=?", (row["document_id"],))
            connection.execute("UPDATE scopes SET generation=generation+1 WHERE scope_key=?",
                               (scope.key,))
            return DeleteReceipt(row["document_id"], row["revision"],
                                 self._generation(connection, scope))

    def clear_scope(self, scope: MemoryScope) -> int:
        """Atomically clear one host-selected scope; a repeated clear is a no-op.

        The host uses this for the RAM scope of a deleted temporary session. It
        must not infer permission to clear shared workspace memory from deleting
        an ordinary chat. This storage API does not grant that authorization.
        """
        with self._transaction(scope) as connection:
            count = connection.execute("SELECT count(*) FROM documents WHERE scope_key=?",
                                       (scope.key,)).fetchone()[0]
            if not count:
                return 0
            if self._mode(connection) == "fts5_lexical":
                connection.execute(
                    "DELETE FROM chunk_fts WHERE rowid IN (SELECT c.chunk_id FROM chunks c "
                    "JOIN documents d ON d.document_id=c.document_id WHERE d.scope_key=?)",
                    (scope.key,),
                )
            connection.execute("DELETE FROM documents WHERE scope_key=?", (scope.key,))
            connection.execute("UPDATE scopes SET generation=generation+1 WHERE scope_key=?",
                               (scope.key,))
            return count

    def agent_has_documents(self, agent_id: str) -> bool:
        """Host-only deletion guard across workspaces, without returning content."""
        _id(agent_id)
        for temporary in (False, True):
            if temporary and self._memory is None:
                continue
            if not temporary and self._disk is None and not self.path.exists():
                continue
            scope = MemoryScope(agent_id, "deletion_guard",
                                "guard" if temporary else None, temporary)
            with self._transaction(scope) as connection:
                keys = connection.execute("SELECT DISTINCT scope_key FROM documents").fetchall()
                if any(json.loads(row[0])[0] == agent_id for row in keys):
                    return True
        return False

    def copy_scope(self, review, context, *, copy_limits=None, cold_family=None):
        """Host-only approved durable scope copy, never a model tool or implicit migration."""
        return _execute_scope_copy(self, review, context, copy_limits or MemoryCopyLimits(),
                                   cold_family=cold_family)

    def _page(self, limit: int, offset: int) -> None:
        if (type(limit) is not int or not 1 <= limit <= self.limits.max_page_size
                or type(offset) is not int or offset < 0 or offset > 1_000_000):
            raise MemoryError("invalid_page")

    def list_documents(self, scope: MemoryScope, *, limit: int = 100,
                       offset: int = 0) -> tuple[DocumentRecord, ...]:
        self._page(limit, offset)
        with self._transaction(scope) as connection:
            rows = connection.execute("SELECT * FROM documents WHERE scope_key=? ORDER BY key "
                                      "LIMIT ? OFFSET ?", (scope.key, limit, offset)).fetchall()
            return tuple(self._record(row) for row in rows)

    def search(self, scope: MemoryScope, query: str, *, limit: int = 20, offset: int = 0,
               expected_generation: int | None = None) -> SearchResult:
        self._page(limit, offset)
        if (not isinstance(query, str)
                or len(_utf8(query, "invalid_query")) > self.limits.max_query_bytes):
            raise MemoryError("invalid_query")
        tokens = _tokens(query)
        if (not tokens or len(tokens) > self.limits.max_query_terms
                or any(len(token) > 64 for token in tokens)):
            raise MemoryError("invalid_query")
        if expected_generation is not None and (
            type(expected_generation) is not int or expected_generation < 0
        ):
            raise MemoryError("invalid_generation")
        with self._transaction(scope) as connection:
            generation = self._generation(connection, scope)
            if expected_generation is not None and expected_generation != generation:
                raise MemoryError("search_changed")
            mode = self._mode(connection)
            if mode == "fts5_lexical":
                match = " AND ".join('"' + token.replace('"', '""') + '"' for token in tokens)
                rows = connection.execute(
                    "SELECT c.*,bm25(chunk_fts) AS rank FROM chunk_fts "
                    "JOIN chunks c ON c.chunk_id=chunk_fts.rowid "
                    "JOIN documents d ON d.document_id=c.document_id "
                    "WHERE chunk_fts MATCH ? AND d.scope_key=? "
                    "ORDER BY rank,d.key,c.ordinal LIMIT ? OFFSET ?",
                    (match, scope.key, limit + 1, offset),
                ).fetchall()
            else:
                candidates = connection.execute(
                    "SELECT c.* FROM chunks c JOIN documents d ON d.document_id=c.document_id "
                    "WHERE d.scope_key=? ORDER BY d.key,c.ordinal", (scope.key,),
                ).fetchall()
                rows = [dict(row) | {"rank": 0.0} for row in candidates
                        if set(tokens) <= set(_tokens(row["text"]))][offset:offset + limit + 1]
            hits = []
            for row in rows[:limit]:
                document = connection.execute("SELECT * FROM documents WHERE document_id=? "
                                              "AND scope_key=?", (row["document_id"], scope.key))
                document = document.fetchone()
                if document is None:
                    raise MemoryError("invalid_memory_database")
                data = self._assert_text(document)
                start, end = row["start_byte"], row["end_byte"]
                if (type(start) is not int or type(end) is not int
                        or not 0 <= start < end <= len(data)
                        or _boundary(data, start) != start or _boundary(data, end) != end
                        or data[start:end].decode() != row["text"]):
                    raise MemoryError("invalid_memory_database")
                location = start + _match_position(row["text"], tokens)
                start = _boundary(data, max(start, location - self.limits.excerpt_bytes // 4),
                                  forward=True)
                end = _boundary(data, min(end, start + self.limits.excerpt_bytes))
                excerpt_data = data[start:end]
                citation = Citation(document["document_id"], document["revision"],
                                    document["text_sha256"], start, end, _sha(excerpt_data),
                                    document["source_uri"])
                score = -float(row["rank"])
                if not math.isfinite(score):
                    raise MemoryError("invalid_memory_database")
                hits.append(SearchHit(self._record(document), excerpt_data.decode(),
                                      citation, score))
            return SearchResult(tuple(hits), mode, generation, offset + len(hits),
                                len(rows) > limit, tokens)

    def resolve_citation(self, scope: MemoryScope, citation: Citation) -> str:
        if not isinstance(citation, Citation):
            raise MemoryError("invalid_citation")
        with self._transaction(scope) as connection:
            row = connection.execute(
                "SELECT * FROM documents WHERE document_id=? AND scope_key=?",
                (citation.document_id, scope.key),
            ).fetchone()
            if (row is None or row["revision"] != citation.revision
                    or row["text_sha256"] != citation.text_sha256
                    or row["source_uri"] != citation.source_uri):
                raise MemoryError("stale_citation")
            data = self._assert_text(row)
            if (type(citation.start_byte) is not int or type(citation.end_byte) is not int
                    or not 0 <= citation.start_byte < citation.end_byte <= len(data)
                    or _boundary(data, citation.start_byte) != citation.start_byte
                    or _boundary(data, citation.end_byte) != citation.end_byte):
                raise MemoryError("invalid_citation")
            excerpt = data[citation.start_byte:citation.end_byte]
            if _sha(excerpt) != citation.excerpt_sha256:
                raise MemoryError("stale_citation")
            return excerpt.decode()


@dataclass(frozen=True)
class ConversationMessage:
    message_id: str
    session_id: str
    sequence: int
    role: str
    text: str


@dataclass(frozen=True)
class MessageExtract:
    message_id: str
    role: str
    source_sha256: str
    source_byte_length: int
    start_byte: int
    end_byte: int
    text: str
    truncated: bool


@dataclass(frozen=True)
class CompactionRecord:
    extracts: tuple[MessageExtract, ...]
    recent_messages: tuple[ConversationMessage, ...]
    omitted_message_ids: tuple[str, ...]
    source_digest: str
    used_bytes: int
    algorithm: str = field(default="extractive-prefix-v1", init=False)
    budget_unit: str = field(default="utf8_bytes", init=False)
    verified_action_claims: bool = field(default=False, init=False)


def compact_messages(
    scope: MemoryScope, messages: Sequence[ConversationMessage], *, budget_bytes: int,
    max_recent_messages: int = 8,
) -> CompactionRecord:
    """Produce bounded verbatim context and provenance, never a model summary.

    Bytes count message/extract text, not tokens or protocol framing. Oversized
    messages contribute exact prefixes. Roles survive: assistant text is still a
    model claim and never evidence that an action happened. No storage is opened.
    """
    if (not isinstance(scope, MemoryScope) or scope.session_id is None
            or type(budget_bytes) is not int or not 64 <= budget_bytes <= 1_048_576
            or type(max_recent_messages) is not int or not 0 <= max_recent_messages <= 100
            or not isinstance(messages, Sequence) or len(messages) > 10000):
        raise MemoryError("invalid_compaction")
    previous, identities, total_bytes = 0, set(), 0
    for message in messages:
        if (not isinstance(message, ConversationMessage) or message.session_id != scope.session_id
                or not isinstance(message.message_id, str)
                or re.fullmatch(r"[A-Za-z0-9_-]{1,64}", message.message_id) is None
                or message.message_id in identities or type(message.sequence) is not int
                or message.sequence <= previous
                or message.role not in {"user", "assistant", "system"}
                or not isinstance(message.text, str)
                or len(_utf8(message.text, "invalid_compaction")) > 1_048_576):
            raise MemoryError("invalid_compaction")
        total_bytes += len(message.text.encode())
        if total_bytes > 16 * 1024 * 1024:
            raise MemoryError("invalid_compaction")
        previous = message.sequence
        identities.add(message.message_id)
    digest = _sha(json.dumps([scope.key, scope.temporary, [
        [message.message_id, message.sequence, message.role, _sha(message.text.encode())]
        for message in messages
    ]], separators=(",", ":")).encode())
    recent: list[ConversationMessage] = []
    remaining = budget_bytes
    for message in reversed(messages):
        size = len(message.text.encode())
        if len(recent) >= max_recent_messages or size > remaining:
            break
        recent.append(message)
        remaining -= size
    recent.reverse()
    kept = {message.message_id for message in recent}
    extracts = []
    # Most recent omitted context gets priority; output restores source order.
    for message in reversed(messages):
        if message.message_id in kept or remaining == 0:
            continue
        data = message.text.encode()
        end = _boundary(data, min(remaining, 256))
        if end:
            extract = data[:end].decode()
            extracts.append(MessageExtract(message.message_id, message.role, _sha(data), len(data),
                                           0, end, extract, end != len(data)))
            remaining -= end
            kept.add(message.message_id)
    extracts.reverse()
    return CompactionRecord(tuple(extracts), tuple(recent),
                            tuple(m.message_id for m in messages if m.message_id not in kept),
                            digest, budget_bytes - remaining)


# Explicit restore reconciliation. Ordinary operations continue using schema v1;
# these tables and version2 appear only inside an approved copy transaction.
_MAX_COPY_GENERATION = (1 << 52) - 1
_MAX_SQL_INTEGER = (1 << 63) - 1
_COPY_RECEIPT_COLUMNS = (
    "operation_id",
    "request_digest",
    "source_scope_key",
    "source_generation",
    "source_fingerprint",
    "destination_scope_key",
    "destination_generation_before",
    "destination_generation_after",
    "document_count",
    "byte_length",
    "mapping_sha256",
    "created_ns",
    "origin_kind",
)
_COPY_MAP_COLUMNS = (
    "operation_id",
    "ordinal",
    "source_document_id",
    "source_revision",
    "source_text_sha256",
    "source_byte_length",
    "destination_document_id",
    "destination_revision",
    "destination_text_sha256",
    "destination_byte_length",
)
_COPY_SQL_TOKEN = re.compile(r"'(?:[^']|'')*'|\"(?:[^\"]|\"\")*\"|[A-Za-z_][A-Za-z_0-9]*|\d+|\S")


def _copy_canonical(value):
    try:
        return json.dumps(
            value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
        ).encode()
    except (ValueError, TypeError, UnicodeError, RecursionError):
        raise MemoryError("memory_copy_invalid_data") from None


def _copy_code(error):
    value = getattr(error, "code", None)
    if value is None:
        from jarvis_core.tools import ToolError

        if isinstance(error, ToolError) and len(error.args) == 1:
            value = error.args[0]
    return (
        value
        if isinstance(value, str) and re.fullmatch(r"[a-z][a-z0-9_]{0,127}", value)
        else "memory_copy_unavailable"
    )


def _copy_integer(value, low=0, high=_MAX_SQL_INTEGER):
    return type(value) is int and low <= value <= high


def _copy_hash(value):
    return isinstance(value, str) and re.fullmatch(r"[a-f0-9]{64}", value) is not None


def _copy_uuid(value):
    return isinstance(value, str) and re.fullmatch(r"[a-f0-9]{32}", value) is not None


def _copy_scope_key(value):
    try:
        parsed = json.loads(value)
        if not isinstance(parsed, list) or len(parsed) != 3:
            raise ValueError
        scope = MemoryScope(*parsed)
        if (scope.key != value
                or re.fullmatch(r"[a-z][a-z0-9_-]{0,63}", scope.agent_id) is None):
            raise ValueError
        return scope
    except (ValueError, TypeError, MemoryError):
        raise MemoryError("memory_copy_invalid_data") from None


@dataclass(frozen=True)
class MemoryCopyLimits:
    max_receipts: int = 256
    max_mapping_rows: int = 100_000
    max_metadata_bytes: int = 16 * 1024 * 1024
    max_sqlite_bytes: int = 64 * 1024 * 1024
    max_vm_steps: int = 20_000_000

    def __post_init__(self):
        maxima = (256, 100_000, 16 * 1024 * 1024, 64 * 1024 * 1024, 20_000_000)
        if any(
            not _copy_integer(value, 1, maximum)
            for value, maximum in zip(vars(self).values(), maxima, strict=True)
        ):
            raise MemoryError("memory_copy_invalid_limits")


@dataclass(frozen=True)
class MemoryScopeSummary:
    scope: MemoryScope
    exists: bool
    generation: int
    document_count: int
    byte_length: int
    largest_document_bytes: int
    chunk_count: int
    fingerprint: str


@dataclass(frozen=True)
class MemoryScopeCopyReview:
    database_identity: tuple[int, int]
    schema_before: int
    source: MemoryScopeSummary
    destination: MemoryScopeSummary
    new_chunk_count: int
    metadata_bytes: int
    physical_bytes_before: int
    limits_fingerprint: str

    @property
    def schema_after(self):
        return _COPY_SCHEMA_VERSION

    @property
    def migration_required(self):
        return self.schema_before == 1

    def metadata(self):
        return {
            **asdict(self),
            "schema_after": self.schema_after,
            "migration_required": self.migration_required,
            "policy": "copy_same_agent_empty_durable_destination",
        }

    @property
    def digest(self):
        return _sha(_copy_canonical(self.metadata()))


@dataclass(frozen=True)
class MemoryCopyContext:
    operation_id: str
    request_digest: str
    approved_core_review_digest: str
    check_current: Callable[[str, bool], None]
    cancellation: object
    deadline: float
    clock: Callable[[], float] = time.monotonic
    _used: bool = field(default=False, init=False, repr=False)
    _failure: BaseException | None = field(default=None, init=False, repr=False)
    _lock: threading.Lock = field(default_factory=threading.Lock, init=False, repr=False)

    def __post_init__(self):
        if (
            not _copy_uuid(self.operation_id)
            or not _copy_hash(self.request_digest)
            or not _copy_hash(self.approved_core_review_digest)
            or not callable(self.check_current)
            or not callable(self.clock)
            or not callable(getattr(self.cancellation, "is_set", None))
            or type(self.deadline) not in {int, float}
            or not math.isfinite(self.deadline)
            or self.deadline - self.clock() > 120
        ):
            raise MemoryError("memory_copy_invalid_context")

    def begin(self):
        with self._lock:
            if self._used:
                raise MemoryError("memory_copy_context_replayed")
            object.__setattr__(self, "_used", True)
        self.checkpoint("memory_copy_begin")

    def checkpoint(self, stage, write=False):
        with self._lock:
            failure = self._failure
        if failure is not None:
            raise failure
        try:
            if self.cancellation.is_set():
                raise MemoryError("memory_copy_cancelled")
            if self.clock() >= self.deadline:
                raise MemoryError("memory_copy_deadline_exceeded")
            self.check_current(stage, write)
            if self.cancellation.is_set():
                raise MemoryError("memory_copy_cancelled")
            if self.clock() >= self.deadline:
                raise MemoryError("memory_copy_deadline_exceeded")
            with self._lock:
                failure = self._failure
            if failure is not None:
                raise failure
        except BaseException as error:
            with self._lock:
                if self._failure is None:
                    object.__setattr__(self, "_failure", error)
                failure = self._failure
            raise failure from None


@dataclass(frozen=True)
class MemoryScopeCopyReceipt:
    operation_id: str
    request_digest: str
    core_review_digest: str
    status: str
    effect_status: str
    document_count: int = 0
    byte_length: int = 0
    mapping_sha256: str | None = None
    destination_generation_after: int | None = None
    error_code: str | None = None
    cleanup_pending: bool = False


@dataclass(frozen=True)
class MemoryScopeCopyObservation:
    operation_id: str
    request_digest: str
    origin_kind: str
    document_count: int
    byte_length: int
    mapping_sha256: str
    current_matching_documents: int
    current_destination_generation: int
    current_documents_match: bool
    current_scope_generation_matches: bool
    state: str = field(default="local_record_present", init=False)
    prior_acknowledgement: str = field(default="not_established", init=False)


class MemoryScopeCopyError(MemoryError):
    def __init__(self, code, *, receipt=None, owner=None):
        super().__init__(code)
        self.receipt, self.owner = receipt, owner


def _copy_schema_shape(connection):
    rows = connection.execute(
        "SELECT type,name,tbl_name,sql FROM sqlite_schema ORDER BY type,name LIMIT 65"
    ).fetchall()
    if len(rows) > 64:
        raise MemoryError("invalid_memory_database")
    return tuple(
        (
            row[0],
            row[1],
            row[2],
            None
            if row[3] is None
            else tuple(
                token if token[0] in "'\"" else token.lower()
                for token in _COPY_SQL_TOKEN.findall(row[3])
            ),
        )
        for row in rows
    )


def _copy_assert_schema(connection):
    version = connection.execute("PRAGMA user_version").fetchone()[0]
    if (
        version not in {1, 2}
        or connection.execute("PRAGMA application_id").fetchone()[0] != _APPLICATION_ID
    ):
        raise MemoryError("invalid_memory_database")
    observed = _copy_schema_shape(connection)
    fts = any(row[1] == "chunk_fts" for row in observed)
    template = sqlite3.connect(":memory:")
    try:
        template.executescript(_SCHEMA + (_COPY_SCHEMA if version == 2 else ""))
        if fts:
            template.execute(
                "CREATE VIRTUAL TABLE chunk_fts USING "
                "fts5(text, tokenize='unicode61 remove_diacritics 2')"
            )
        if _copy_schema_shape(template) != observed:
            raise MemoryError("invalid_memory_database")
    finally:
        template.close()
    if dict(connection.execute("SELECT key,value FROM metadata")) != {
        "search_mode": "fts5_lexical" if fts else "lexical_scan"
    }:
        raise MemoryError("invalid_memory_database")
    return version


def _copy_mapping_digest(rows):
    return _sha(_copy_canonical([list(row)[1:] for row in rows]))


def _copy_history(connection, copy_limits, check_current):
    if connection.execute("PRAGMA user_version").fetchone()[0] == 1:
        return [], [], len(_copy_canonical([[], []]))
    count = connection.execute("SELECT count(*) FROM memory_scope_copies").fetchone()[0]
    maps = connection.execute("SELECT count(*) FROM memory_scope_copy_documents").fetchone()[0]
    if count > copy_limits.max_receipts or maps > copy_limits.max_mapping_rows:
        raise MemoryError("memory_copy_history_capacity")
    receipts = [
        tuple(row)
        for row in connection.execute("SELECT * FROM memory_scope_copies ORDER BY operation_id")
    ]
    mappings = [
        tuple(row)
        for row in connection.execute(
            "SELECT * FROM memory_scope_copy_documents ORDER BY operation_id,ordinal"
        )
    ]
    grouped = {}
    for row in mappings:
        check_current()
        (
            op,
            ordinal,
            source_id,
            revision,
            source_hash,
            source_bytes,
            dest_id,
            drev,
            dhash,
            dbytes,
        ) = row
        if (
            not _copy_uuid(op)
            or not _copy_integer(ordinal, 0, copy_limits.max_mapping_rows)
            or not _copy_uuid(source_id)
            or not _copy_uuid(dest_id)
            or source_id == dest_id
            or not _copy_integer(revision, 1)
            or drev != 1
            or type(drev) is not int
            or not _copy_hash(source_hash)
            or dhash != source_hash
            or not _copy_integer(source_bytes, 1, 1_048_576)
            or type(dbytes) is not int
            or dbytes != source_bytes
        ):
            raise MemoryError("memory_copy_invalid_history")
        grouped.setdefault(op, []).append(row)
    for row in receipts:
        check_current()
        (
            op,
            request,
            source,
            generation,
            fingerprint,
            destination,
            before,
            after,
            n,
            size,
            sha,
            now,
            origin,
        ) = row
        src, dst = _copy_scope_key(source), _copy_scope_key(destination)
        items = grouped.pop(op, [])
        if (
            not _copy_uuid(op)
            or not _copy_hash(request)
            or not _copy_hash(fingerprint)
            or not _copy_hash(sha)
            or src.session_id is not None
            or dst.session_id is not None
            or src.agent_id != dst.agent_id
            or src == dst
            or not _copy_integer(generation, 0, _MAX_COPY_GENERATION)
            or not _copy_integer(before, 0, _MAX_COPY_GENERATION - 1)
            or after != before + 1
            or type(after) is not int
            or not _copy_integer(n, 1, copy_limits.max_mapping_rows)
            or not _copy_integer(size, 1, 64 * 1024 * 1024)
            or not _copy_integer(now, 1)
            or origin not in {"local", "restored_history"}
            or len(items) != n
            or [item[1] for item in items] != list(range(n))
            or len({item[2] for item in items}) != n
            or len({item[6] for item in items}) != n
            or bool({item[2] for item in items} & {item[6] for item in items})
            or sum(item[5] for item in items) != size
            or _copy_mapping_digest(items) != sha
        ):
            raise MemoryError("memory_copy_invalid_history")
    if grouped:
        raise MemoryError("memory_copy_invalid_history")
    budget = _copy_metadata_bytes(receipts, mappings)
    if budget > copy_limits.max_metadata_bytes:
        raise MemoryError("memory_copy_history_capacity")
    return receipts, mappings, budget


def _copy_metadata_bytes(receipts, mappings):
    normalized = [[*row[:-1], "restored_history"] for row in receipts]
    return len(_copy_canonical([normalized, [list(row) for row in mappings]]))


def _copy_read_documents(connection, scope, check_current):
    rows = connection.execute(
        "SELECT * FROM documents WHERE scope_key=? ORDER BY key", (scope.key,)
    ).fetchall()
    documents, records = [], []
    for row in rows:
        check_current()
        record = MemoryStore._record(row)
        MemoryStore._key(record.key)
        raw = MemoryStore._assert_text(row)
        if not row["text"].strip() or b"\0" in raw or "\0" in record.title:
            raise MemoryError("invalid_memory_database")
        chunks = connection.execute(
            "SELECT ordinal,start_byte,end_byte,text FROM chunks "
            "WHERE document_id=? ORDER BY ordinal",
            (record.document_id,),
        ).fetchall()
        previous_end, last_start = 0, -1
        for ordinal, chunk in enumerate(chunks):
            check_current()
            n, start, end, text = chunk
            if (
                n != ordinal
                or not _copy_integer(start)
                or not _copy_integer(end)
                or not 0 <= start < end <= len(raw)
                or start <= last_start
                or start > previous_end
                or end <= previous_end
                or not isinstance(text, str)
                or raw[start:end] != text.encode()
            ):
                raise MemoryError("invalid_memory_database")
            last_start, previous_end = start, end
        if len(chunks) != record.chunk_count or previous_end != len(raw):
            raise MemoryError("invalid_memory_database")
        documents.append((row, raw))
        records.append(
            [
                record.document_id,
                record.key,
                record.title,
                record.source_uri,
                record.revision,
                record.text_sha256,
                record.byte_length,
                record.chunk_count,
                record.created_ns,
                record.updated_ns,
                [[c[0], c[1], c[2], _sha(c[3].encode())] for c in chunks],
            ]
        )
    return documents, records


def _copy_summary(connection, scope, check_current):
    if not isinstance(scope, MemoryScope) or scope.temporary or scope.session_id is not None:
        raise MemoryError("memory_copy_durable_scope_required")
    generation = MemoryStore._generation(connection, scope)
    if not _copy_integer(generation, 0, _MAX_COPY_GENERATION):
        raise MemoryError("memory_copy_generation_invalid")
    exists = (
        connection.execute("SELECT 1 FROM scopes WHERE scope_key=?", (scope.key,)).fetchone()
        is not None
    )
    documents, records = _copy_read_documents(connection, scope, check_current)
    sizes = [len(raw) for _, raw in documents]
    value = [scope.key, exists, generation, records]
    return MemoryScopeSummary(
        scope,
        exists,
        generation,
        len(documents),
        sum(sizes),
        max(sizes, default=0),
        sum(row["chunk_count"] for row, _ in documents),
        _sha(_copy_canonical(value)),
    )


@contextmanager
def _copy_snapshot(data, limits, copy_limits, check_current):
    if (
        type(data) is not bytes
        or not 512 <= len(data) <= copy_limits.max_sqlite_bytes
        or data[:16] != b"SQLite format 3\0"
        or data[18:20] != b"\x01\x01"
    ):
        raise MemoryError("memory_copy_snapshot_invalid")
    check_current()
    connection = sqlite3.connect(":memory:", isolation_level=None)
    stopped, steps = [], 0

    def progress():
        nonlocal steps
        steps += 1000
        try:
            check_current()
            if steps > copy_limits.max_vm_steps:
                raise MemoryError("memory_copy_work_limit")
        except BaseException as error:
            stopped.append(error)
            return 1
        return 0

    try:
        from jarvis_core.recovery_sqlite import deny_extension_loading

        deny_extension_loading(connection)
        connection.setlimit(sqlite3.SQLITE_LIMIT_LENGTH, copy_limits.max_sqlite_bytes)
        connection.setlimit(sqlite3.SQLITE_LIMIT_SQL_LENGTH, 65_536)
        connection.set_progress_handler(progress, 1000)
        connection.execute("PRAGMA temp_store=MEMORY")
        connection.deserialize(data)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA query_only=ON")
        connection.execute("PRAGMA trusted_schema=OFF")
        _copy_assert_schema(connection)
        if connection.execute("PRAGMA quick_check(1)").fetchone()[0] != "ok":
            raise MemoryError("invalid_memory_database")
        if connection.execute("PRAGMA foreign_key_check").fetchone() is not None:
            raise MemoryError("invalid_memory_database")
        _copy_check_contents(connection, limits, copy_limits, check_current)
        yield connection
        check_current()
    except sqlite3.Error:
        if stopped:
            raise stopped[0] from None
        raise MemoryError("memory_copy_snapshot_invalid") from None
    finally:
        connection.close()


def memory_scope_summaries(data, *, check_current, limits=None, copy_limits=None):
    """Pure bounded RAM inventory; never open or initialize a store/source path."""
    limits, copy_limits = limits or MemoryLimits(), copy_limits or MemoryCopyLimits()
    with _copy_snapshot(data, limits, copy_limits, check_current) as connection:
        keys = connection.execute("SELECT scope_key FROM scopes ORDER BY scope_key").fetchall()
        if len(keys) > limits.max_scopes:
            raise MemoryError("memory_capacity_exceeded")
        results = []
        for (key,) in keys:
            scope = _copy_scope_key(key)
            if scope.session_id is None:
                results.append(_copy_summary(connection, scope, check_current))
        return tuple(results)


def _copy_review(
    connection, source, destination, database_identity, limits, copy_limits, check_current
):
    if (
        not isinstance(database_identity, tuple)
        or len(database_identity) != 2
        or any(not _copy_integer(n, 0, (1 << 64) - 1) for n in database_identity)
    ):
        raise MemoryError("memory_copy_database_identity_invalid")
    src, dst = (_copy_summary(connection, scope, check_current) for scope in (source, destination))
    if src.scope.agent_id != dst.scope.agent_id or src.scope == dst.scope:
        raise MemoryError("memory_copy_scope_mismatch")
    if not src.document_count:
        raise MemoryError("memory_copy_source_empty")
    if dst.document_count:
        raise MemoryError("memory_copy_destination_not_empty")
    if dst.generation >= _MAX_COPY_GENERATION:
        raise MemoryError("memory_copy_generation_invalid")
    total_count, total_size = connection.execute(
        "SELECT count(*),coalesce(sum(byte_length),0) FROM documents"
    ).fetchone()
    scopes = connection.execute("SELECT count(*) FROM scopes").fetchone()[0]
    if (
        src.largest_document_bytes > limits.max_document_bytes
        or src.document_count > limits.max_documents_per_scope
        or src.byte_length > limits.max_scope_bytes
        or total_count + src.document_count > limits.max_documents
        or total_size + src.byte_length > limits.max_total_bytes
        or scopes + (not dst.exists) > limits.max_scopes
    ):
        raise MemoryError("memory_capacity_exceeded")
    receipts, mappings, metadata_size = _copy_history(connection, copy_limits, check_current)
    if (
        len(receipts) >= copy_limits.max_receipts
        or len(mappings) + src.document_count > copy_limits.max_mapping_rows
    ):
        raise MemoryError("memory_copy_history_capacity")
    documents, _ = _copy_read_documents(connection, source, check_current)
    chunks = sum(len(tuple(_chunks(raw, limits.chunk_bytes))) for _, raw in documents)
    placeholders = [
        (
            "0" * 32,
            n,
            row["document_id"],
            row["revision"],
            row["text_sha256"],
            len(raw),
            "0" * 32,
            1,
            row["text_sha256"],
            len(raw),
        )
        for n, (row, raw) in enumerate(documents)
    ]
    projected = (
        "0" * 32,
        "0" * 64,
        source.key,
        src.generation,
        src.fingerprint,
        destination.key,
        dst.generation,
        dst.generation + 1,
        src.document_count,
        src.byte_length,
        "0" * 64,
        _MAX_SQL_INTEGER,
        "local",
    )
    metadata_size = _copy_metadata_bytes([*receipts, projected], [*mappings, *placeholders])
    if metadata_size > copy_limits.max_metadata_bytes:
        raise MemoryError("memory_copy_history_capacity")
    physical = (
        connection.execute("PRAGMA page_size").fetchone()[0]
        * connection.execute("PRAGMA page_count").fetchone()[0]
    )
    if physical > copy_limits.max_sqlite_bytes:
        raise MemoryError("memory_copy_physical_limit")
    return MemoryScopeCopyReview(
        database_identity,
        connection.execute("PRAGMA user_version").fetchone()[0],
        src,
        dst,
        chunks,
        metadata_size,
        physical,
        _sha(_copy_canonical([asdict(limits), asdict(copy_limits)])),
    )


def review_memory_scope_copy(
    data, source, destination, *, database_identity, check_current, limits=None, copy_limits=None
):
    limits, copy_limits = limits or MemoryLimits(), copy_limits or MemoryCopyLimits()
    with _copy_snapshot(data, limits, copy_limits, check_current) as connection:
        return _copy_review(
            connection, source, destination, database_identity, limits, copy_limits, check_current
        )


def lookup_memory_scope_copy(
    data, operation_id, request_digest, *, check_current, limits=None, copy_limits=None
):
    if not _copy_uuid(operation_id) or not _copy_hash(request_digest):
        raise MemoryError("memory_copy_invalid_request")
    limits, copy_limits = limits or MemoryLimits(), copy_limits or MemoryCopyLimits()
    with _copy_snapshot(data, limits, copy_limits, check_current) as connection:
        if connection.execute("PRAGMA user_version").fetchone()[0] == 1:
            return None
        receipts, mappings, _ = _copy_history(connection, copy_limits, check_current)
        found = next((row for row in receipts if row[0] == operation_id), None)
        if found is None:
            return None
        if found[1] != request_digest:
            raise MemoryError("memory_copy_operation_conflict")
        if found[12] != "local":
            raise MemoryError("memory_copy_receipt_historical")
        destination = _copy_scope_key(found[5])
        items = [row for row in mappings if row[0] == operation_id]
        matching = 0
        for item in items:
            check_current()
            row = connection.execute(
                "SELECT * FROM documents WHERE document_id=? AND scope_key=?",
                (item[6], destination.key),
            ).fetchone()
            if row is not None:
                MemoryStore._assert_text(row)
                if (
                    row["revision"] == item[7]
                    and row["text_sha256"] == item[8]
                    and row["byte_length"] == item[9]
                ):
                    matching += 1
        generation = MemoryStore._generation(connection, destination)
        return MemoryScopeCopyObservation(
            operation_id,
            request_digest,
            found[12],
            found[8],
            found[9],
            found[10],
            matching,
            generation,
            matching == found[8],
            generation == found[7],
        )


class MemoryCopyOwner:
    """Retained active-DB cleanup; host must keep its SAME profile cut alive.

    No thread-owned mutex stays locked across return. The store itself is
    latched closed while this owner exists. Cleanup only rolls back our own
    transaction and restores connection settings, never COMMIT or copy/replay.
    """

    def __init__(self, store, connection, identity):
        self.store, self.connection, self.identity = store, connection, identity
        self.closed = False
        self.begin_attempted = False
        self.commit_attempted = False
        self.old_synchronous = connection.execute("PRAGMA synchronous").fetchone()[0]
        self.old_max_pages = connection.execute("PRAGMA max_page_count").fetchone()[0]

    def assert_current(self):
        if self.closed or self.store._disk is not self.connection:
            raise MemoryError("memory_copy_cleanup_identity_changed")
        self.store._check_disk()
        info = os.fstat(self.store._file_fd)
        if (info.st_dev, info.st_ino) != self.identity:
            raise MemoryError("memory_copy_cleanup_identity_changed")

    def cleanup(self):
        with self.store._mutex:
            if self.closed:
                return
            self.assert_current()
            self.connection.set_progress_handler(None, 0)
            if self.connection.in_transaction:
                if not self.begin_attempted:
                    raise MemoryError("memory_copy_transaction_not_owned")
                self.connection.execute("ROLLBACK")
            if self.connection.in_transaction:
                raise MemoryError("memory_copy_cleanup_incomplete")
            self.assert_current()
            self.connection.execute(f"PRAGMA max_page_count={self.old_max_pages}")
            if self.connection.execute("PRAGMA max_page_count").fetchone()[0] != self.old_max_pages:
                raise MemoryError("memory_copy_cleanup_incomplete")
            self.connection.execute(f"PRAGMA synchronous={self.old_synchronous}")
            if self.connection.execute("PRAGMA synchronous").fetchone()[0] != self.old_synchronous:
                raise MemoryError("memory_copy_cleanup_incomplete")
            self.assert_current()
            self.closed = True
            self.store._copy_owner = None


def _copy_existing_identity(store, expected, *, cold, cold_family=None):
    if store._closed:
        raise MemoryError("memory_closed")
    if store._copy_owner is not None and not store._copy_owner.closed:
        raise MemoryError("memory_copy_cleanup_incomplete")
    _parent_identity(store.path.parent)
    info = store.path.lstat()
    if (
        not stat.S_ISREG(info.st_mode)
        or stat.S_IMODE(info.st_mode) != 0o600
        or info.st_uid != os.getuid()
        or info.st_nlink != 1
        or (info.st_dev, info.st_ino) != expected
    ):
        raise MemoryError("memory_copy_database_changed")
    if cold:
        if cold_family is not None:
            # Only the host's frozen family may admit WAL/journal recovery. The
            # core still checks its physical identity before the first consented
            # SQLite open and CAS-checks the logical review before and inside SQL.
            # This object is not a permission, and it never reaches RPC/model data.
            from jarvis_core.cold_sqlite import ColdFamily
            if (type(cold_family) is not ColdFamily or cold_family.role != "memory"
                    or cold_family.root != store.path.parent
                    or store.path.name != "memory.sqlite3"
                    or not cold_family.snapshot_eligible
                    or cold_family.members[0][:2] != expected):
                raise MemoryError("memory_copy_cold_image_unavailable")
            cold_family.assert_current()
            return
        for suffix in ("-wal", "-shm", "-journal"):
            if os.path.lexists(store.path.with_name(store.path.name + suffix)):
                raise MemoryError("memory_copy_cold_image_unavailable")
        descriptor = os.open(store.path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        try:
            opened = os.fstat(descriptor)
            if (opened.st_dev, opened.st_ino) != expected:
                raise MemoryError("memory_copy_database_changed")
            header = os.pread(descriptor, 100, 0)
            if (
                len(header) != 100
                or header[:16] != b"SQLite format 3\0"
                or header[18:20] != b"\x01\x01"
            ):
                raise MemoryError("memory_copy_cold_image_unavailable")
        finally:
            os.close(descriptor)


def _copy_physical(connection, copy_limits):
    pages = connection.execute("PRAGMA page_count").fetchone()[0]
    page_size = connection.execute("PRAGMA page_size").fetchone()[0]
    if (
        not _copy_integer(pages, 1)
        or not _copy_integer(page_size, 512, 65536)
        or pages * page_size > copy_limits.max_sqlite_bytes
    ):
        raise MemoryError("memory_copy_physical_limit")
    return pages * page_size


def _copy_check_contents(connection, limits, copy_limits, check_current):
    _copy_assert_schema(connection)
    if connection.execute("PRAGMA foreign_key_check").fetchone() is not None:
        raise MemoryError("invalid_memory_database")
    keys = connection.execute(
        "SELECT scope_key,generation FROM scopes ORDER BY scope_key"
    ).fetchall()
    if (
        len(keys) > limits.max_scopes
        or connection.execute("SELECT count(*) FROM documents").fetchone()[0] > limits.max_documents
    ):
        raise MemoryError("memory_capacity_exceeded")
    for key, generation in keys:
        check_current()
        if not _copy_integer(generation, 0, _MAX_COPY_GENERATION):
            raise MemoryError("memory_copy_generation_invalid")
        scope = _copy_scope_key(key)
        _copy_read_documents(connection, scope, check_current)
    if MemoryStore._mode(connection) == "fts5_lexical":
        missing = connection.execute(
            "SELECT 1 FROM chunks c LEFT JOIN chunk_fts f ON f.rowid=c.chunk_id "
            "WHERE f.rowid IS NULL OR f.text!=c.text LIMIT 1"
        ).fetchone()
        extra = connection.execute(
            "SELECT 1 FROM chunk_fts f LEFT JOIN chunks c ON c.chunk_id=f.rowid "
            "WHERE c.chunk_id IS NULL LIMIT 1"
        ).fetchone()
        if missing or extra:
            raise MemoryError("invalid_memory_database")
    _copy_history(connection, copy_limits, check_current)


def _copy_planned_rows(connection, review, context, limits, copy_limits, check_current):
    documents, _ = _copy_read_documents(connection, review.source.scope, check_current)
    receipts, mappings, _ = _copy_history(connection, copy_limits, check_current)
    used_ids = {row[0] for row in connection.execute("SELECT document_id FROM documents")}
    used_ids.update(row[2] for row in mappings)
    used_ids.update(row[6] for row in mappings)
    new_ids = []
    for _ in documents:
        for _attempt in range(4):
            identity = uuid4().hex
            if _copy_uuid(identity) and identity not in used_ids:
                used_ids.add(identity)
                new_ids.append(identity)
                break
        else:
            raise MemoryError("memory_copy_id_collision")
    new_maps = [
        (
            context.operation_id,
            ordinal,
            row["document_id"],
            row["revision"],
            row["text_sha256"],
            len(raw),
            new_ids[ordinal],
            1,
            row["text_sha256"],
            len(raw),
        )
        for ordinal, (row, raw) in enumerate(documents)
    ]
    mapping_hash = _copy_mapping_digest(new_maps)
    now = time.time_ns()
    if not _copy_integer(now, 1):
        raise MemoryError("memory_copy_invalid_clock")
    operation = (
        context.operation_id,
        context.request_digest,
        review.source.scope.key,
        review.source.generation,
        review.source.fingerprint,
        review.destination.scope.key,
        review.destination.generation,
        review.destination.generation + 1,
        len(documents),
        review.source.byte_length,
        mapping_hash,
        now,
        "local",
    )
    if (
        _copy_metadata_bytes([*receipts, operation], [*mappings, *new_maps])
        > copy_limits.max_metadata_bytes
    ):
        raise MemoryError("memory_copy_history_capacity")
    return documents, new_maps, operation


def _execute_scope_copy(store, review, context, copy_limits, *, cold_family=None):
    if not isinstance(context, MemoryCopyContext):
        raise MemoryScopeCopyError("memory_copy_invalid_context")
    owner, operation, connection = None, None, None
    try:
        context.begin()
        if (
            not isinstance(review, MemoryScopeCopyReview)
            or context.approved_core_review_digest != review.digest
        ):
            raise MemoryError("memory_copy_review_mismatch")
        with store._mutex:
            _copy_existing_identity(store, review.database_identity, cold=store._disk is None,
                                    cold_family=cold_family)
            if store._disk is not None and store._disk.in_transaction:
                raise MemoryError("memory_copy_transaction_active")
            # Original open may create its own .lock and configure SQLite.
            # No such effect happens before the single central host consent.
            context.checkpoint("memory_copy_open_existing", True)
            _copy_existing_identity(store, review.database_identity, cold=store._disk is None,
                                    cold_family=cold_family)
            connection = store._open_disk(create=False)
            store._check_disk()
            info = os.fstat(store._file_fd)
            if (info.st_dev, info.st_ino) != review.database_identity:
                raise MemoryError("memory_copy_database_changed")
            if connection.in_transaction:
                raise MemoryError("memory_copy_transaction_active")
            if connection.execute("PRAGMA journal_mode").fetchone()[0] != "delete":
                raise MemoryError("memory_copy_cold_image_unavailable")
            stopped, steps = [], 0

            def progress():
                nonlocal steps
                steps += 1000
                try:
                    context.checkpoint("memory_copy_sql_progress")
                    if steps > copy_limits.max_vm_steps:
                        raise MemoryError("memory_copy_work_limit")
                except BaseException as error:
                    stopped.append(error)
                    return 1
                return 0

            connection.set_progress_handler(progress, 1000)
            try:
                _copy_check_contents(
                    connection,
                    store.limits,
                    copy_limits,
                    lambda: context.checkpoint("memory_copy_validate"),
                )
                receipts, _, _ = _copy_history(
                    connection, copy_limits, lambda: context.checkpoint("memory_copy_history")
                )
                previous = next((row for row in receipts if row[0] == context.operation_id), None)
                if previous is not None:
                    raise MemoryError(
                        "memory_copy_operation_replayed"
                        if previous[1] == context.request_digest
                        else "memory_copy_operation_conflict"
                    )
                actual = _copy_review(
                    connection,
                    review.source.scope,
                    review.destination.scope,
                    review.database_identity,
                    store.limits,
                    copy_limits,
                    lambda: context.checkpoint("memory_copy_preflight"),
                )
                if actual != review:
                    raise MemoryError("memory_copy_review_changed")
                documents, new_maps, operation = _copy_planned_rows(
                    connection,
                    review,
                    context,
                    store.limits,
                    copy_limits,
                    lambda: context.checkpoint("memory_copy_plan"),
                )
                owner = MemoryCopyOwner(store, connection, review.database_identity)
                store._copy_owner = owner
                context.checkpoint("memory_copy_connection_policy", True)
                owner.assert_current()
                connection.execute("PRAGMA synchronous=EXTRA")
                if connection.execute("PRAGMA synchronous").fetchone()[0] != 3:
                    raise MemoryError("memory_copy_durability_unavailable")
                page_size = connection.execute("PRAGMA page_size").fetchone()[0]
                maximum = min(owner.old_max_pages, copy_limits.max_sqlite_bytes // page_size)
                result = connection.execute(f"PRAGMA max_page_count={maximum}").fetchone()[0]
                if result > maximum:
                    raise MemoryError("memory_copy_physical_limit")
                _copy_physical(connection, copy_limits)
                context.checkpoint("memory_copy_begin_immediate", True)
                owner.assert_current()
                owner.begin_attempted = True
                connection.execute("BEGIN IMMEDIATE")
                # Re-read under the actual SQLite write transaction, not only our mutex.
                current = _copy_review(
                    connection,
                    review.source.scope,
                    review.destination.scope,
                    review.database_identity,
                    store.limits,
                    copy_limits,
                    lambda: context.checkpoint("memory_copy_transaction_cas"),
                )
                if current != review:
                    raise MemoryError("memory_copy_review_changed")
                if review.schema_before == 1:
                    for statement in _COPY_SCHEMA.split(";"):
                        if statement.strip():
                            context.checkpoint("memory_copy_schema", True)
                            owner.assert_current()
                            connection.execute(statement)
                    context.checkpoint("memory_copy_version", True)
                    connection.execute("PRAGMA user_version=2")
                context.checkpoint("memory_copy_receipt_write", True)
                owner.assert_current()
                connection.execute(
                    "INSERT INTO memory_scope_copies VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)", operation
                )
                context.checkpoint("memory_copy_destination_scope", True)
                connection.execute(
                    "INSERT OR IGNORE INTO scopes VALUES(?,0)", (review.destination.scope.key,)
                )
                mode = store._mode(connection)
                for (row, raw), mapping in zip(documents, new_maps, strict=True):
                    context.checkpoint("memory_copy_document", True)
                    owner.assert_current()
                    pieces = tuple(_chunks(raw, store.limits.chunk_bytes))
                    connection.execute(
                        "INSERT INTO documents VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                        (
                            mapping[6],
                            review.destination.scope.key,
                            row["key"],
                            row["title"],
                            row["source_uri"],
                            1,
                            row["text"],
                            row["text_sha256"],
                            len(raw),
                            len(pieces),
                            operation[11],
                            operation[11],
                        ),
                    )
                    for number, (start, end, text) in enumerate(pieces):
                        context.checkpoint("memory_copy_chunk", True)
                        owner.assert_current()
                        cursor = connection.execute(
                            "INSERT INTO chunks VALUES(NULL,?,?,?,?,?)",
                            (mapping[6], number, start, end, text),
                        )
                        if mode == "fts5_lexical":
                            connection.execute(
                                "INSERT INTO chunk_fts(rowid,text) VALUES(?,?)",
                                (cursor.lastrowid, text),
                            )
                    context.checkpoint("memory_copy_mapping", True)
                    connection.execute(
                        "INSERT INTO memory_scope_copy_documents VALUES(?,?,?,?,?,?,?,?,?,?)",
                        mapping,
                    )
                context.checkpoint("memory_copy_generation", True)
                connection.execute(
                    "UPDATE scopes SET generation=generation+1 WHERE scope_key=?",
                    (review.destination.scope.key,),
                )
                _copy_check_contents(
                    connection,
                    store.limits,
                    copy_limits,
                    lambda: context.checkpoint("memory_copy_final_validate"),
                )
                _copy_physical(connection, copy_limits)
                context.checkpoint("memory_copy_commit", True)
                owner.assert_current()
                owner.commit_attempted = True
                connection.execute("COMMIT")
                if connection.in_transaction:
                    raise MemoryError("memory_copy_cleanup_incomplete")
                context.checkpoint("memory_copy_committed")
                owner.assert_current()
                owner.cleanup()
                return MemoryScopeCopyReceipt(
                    context.operation_id,
                    context.request_digest,
                    review.digest,
                    "completed",
                    "scope_copy_committed",
                    operation[8],
                    operation[9],
                    operation[10],
                    operation[7],
                )
            except sqlite3.Error as error:
                if stopped:
                    raise stopped[0] from None
                if getattr(error, "sqlite_errorcode", None) == sqlite3.SQLITE_FULL:
                    raise MemoryError("memory_copy_physical_limit") from None
                raise MemoryError("memory_copy_sql_failed") from None
            finally:
                connection.set_progress_handler(None, 0)
    except BaseException as error:
        code = _copy_code(error)
        if code in {
            "memory_copy_context_replayed",
            "memory_copy_operation_replayed",
            "memory_copy_operation_conflict",
        }:
            raise MemoryScopeCopyError(code) from None
        cleanup_error = None
        if owner is not None and not owner.closed:
            try:
                owner.cleanup()
            except BaseException as cleanup:
                cleanup_error = _copy_code(cleanup)
        pending = owner is not None and not owner.closed
        uncertain = (owner is not None and owner.commit_attempted) or cleanup_error is not None
        status = (
            "unknown"
            if uncertain
            else "cancelled"
            if code in {"memory_copy_cancelled", "memory_copy_deadline_exceeded"}
            else "failed"
        )
        receipt = MemoryScopeCopyReceipt(
            context.operation_id,
            context.request_digest,
            context.approved_core_review_digest,
            status,
            "scope_copy_unknown" if uncertain else "no_scope_copy_committed",
            operation[8] if uncertain and operation else 0,
            operation[9] if uncertain and operation else 0,
            operation[10] if uncertain and operation else None,
            operation[7] if uncertain and operation else None,
            cleanup_error or code,
            pending,
        )
        raise MemoryScopeCopyError(
            code, receipt=receipt, owner=owner if pending else None
        ) from None
