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
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, field
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

    def __enter__(self) -> MemoryStore:
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
        flags = os.O_RDWR | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC
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
            raise MemoryError("unsafe_memory_permissions")
        return descriptor, created

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

    def _open_disk(self) -> sqlite3.Connection:
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
            self._file_fd, created = self._private_file(self.path)
            self._check_disk()
            connection = sqlite3.connect(str(self.path), isolation_level=None,
                                         check_same_thread=False, timeout=1)
            self._check_disk()
            self._configure(connection)
            if created:
                self._initialize(connection)
            elif (connection.execute("PRAGMA application_id").fetchone()[0] != _APPLICATION_ID
                    or connection.execute("PRAGMA user_version").fetchone()[0] != _SCHEMA_VERSION
                    or connection.execute("PRAGMA quick_check(1)").fetchone()[0] != "ok"
                    or connection.execute("PRAGMA foreign_key_check").fetchone() is not None):
                raise MemoryError("invalid_memory_database")
            if connection.execute("PRAGMA journal_mode=DELETE").fetchone()[0] != "delete":
                raise MemoryError("invalid_memory_database")
            connection.execute("PRAGMA secure_delete=ON")
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
