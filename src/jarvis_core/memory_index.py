"""Own disposable vector cache, separate from portable memory schema 1/2.

No note text, titles, queries, source paths or URI bodies are persisted here.
One physically bound profile owns this cache. Model work happens before an
atomic publication; an old published cache is never repaired in place.
"""

from __future__ import annotations

import asyncio
import fcntl
import hashlib
import json
import os
import re
import sqlite3
import stat
import sys
import time
from array import array
from collections.abc import Callable
from contextlib import asynccontextmanager, suppress
from pathlib import Path
from typing import TYPE_CHECKING
from urllib.parse import quote
from uuid import uuid4

from jarvis_core.memory import DocumentContent, MemoryScope
from jarvis_core.memory_retrieval import (
    IndexedSpan,
    MemoryIndexError,
    byte_boundary,
    checkpoint,
    retrieve,
    validate_query,
    vector_values,
)
from jarvis_core.state import ensure_private_directory

if TYPE_CHECKING:
    from jarvis_core.memory_snapshot import ScopeSnapshot

_APPLICATION_ID = 0x4A564943  # JVIC; not the authoritative JRMM memory database.
_VERSION = 1
_MAX_DOCUMENTS = 10000
_MAX_SOURCE_BYTES = 256 * 1024 * 1024
_MAX_SPANS = 50000
_HASH = re.compile(r"[a-f0-9]{64}\Z")
_UUID = re.compile(r"[a-f0-9]{32}\Z")
_CACHE_FILE = re.compile(r"(?:[a-f0-9]{64}\.sqlite3|\.build-[a-f0-9]{32}\.sqlite3)(?:-journal)?\Z")
_SCHEMA = """
CREATE TABLE metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE documents (
 document_id TEXT PRIMARY KEY, revision INTEGER NOT NULL,
 text_sha256 TEXT NOT NULL, byte_length INTEGER NOT NULL
);
CREATE TABLE spans (
 span_id INTEGER PRIMARY KEY, document_id TEXT NOT NULL REFERENCES documents,
 start_byte INTEGER NOT NULL, end_byte INTEGER NOT NULL,
 token_count INTEGER NOT NULL, vector BLOB NOT NULL, span_sha256 TEXT NOT NULL,
 UNIQUE(document_id,start_byte,end_byte)
);
CREATE INDEX spans_document ON spans(document_id,start_byte,end_byte);
"""
_META_KEYS = frozenset(
    {
        "version",
        "profile_binding",
        "scope_key",
        "source_generation",
        "snapshot_fingerprint",
        "source_manifest_sha256",
        "model_fingerprint",
        "dimension",
        "document_count",
        "span_count",
        "publication_id",
    }
)


def _canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()


def _integer(value, low=0, high=2**63 - 1):
    return type(value) is int and low <= value <= high


def _manifest(snapshot):
    if (
        not isinstance(snapshot.scope, MemoryScope)
        or not _integer(snapshot.generation)
        or not isinstance(snapshot.fingerprint, str)
        or not _HASH.fullmatch(snapshot.fingerprint)
        or type(snapshot.documents) is not tuple
        or len(snapshot.documents) > _MAX_DOCUMENTS
    ):
        raise MemoryIndexError("memory_index_invalid_snapshot")
    seen, keys, size, rows = set(), set(), 0, []
    for item in snapshot.documents:
        if not isinstance(item, DocumentContent) or not isinstance(item.text, str):
            raise MemoryIndexError("memory_index_invalid_snapshot")
        doc = item.document
        try:
            data = item.text.encode()
        except UnicodeError:
            raise MemoryIndexError("memory_index_invalid_snapshot") from None
        if (
            not _UUID.fullmatch(doc.document_id)
            or doc.document_id in seen
            or doc.key in keys
            or not _integer(doc.revision, 1)
            or not data
            or len(data) > 1048576
            or not _integer(doc.byte_length, 1)
            or doc.byte_length != len(data)
            or hashlib.sha256(data).hexdigest() != doc.text_sha256
        ):
            raise MemoryIndexError("memory_index_invalid_snapshot")
        seen.add(doc.document_id)
        keys.add(doc.key)
        size += len(data)
        if size > _MAX_SOURCE_BYTES:
            raise MemoryIndexError("memory_index_capacity")
        rows.append(
            [
                doc.document_id,
                doc.revision,
                doc.text_sha256,
                doc.byte_length,
                doc.key,
                doc.title,
                doc.source_uri,
            ]
        )
    return hashlib.sha256(
        _canonical([snapshot.scope.key, snapshot.generation, sorted(rows)])
    ).hexdigest()


def _model(encoder):
    fingerprint, dimension = encoder.model_fingerprint, encoder.dimension
    if (
        not isinstance(fingerprint, str)
        or not _HASH.fullmatch(fingerprint)
        or not _integer(dimension, 1, 4096)
    ):
        raise MemoryIndexError("memory_index_invalid_model")
    return fingerprint, dimension


def _regular(info, *, maximum=None):
    if (
        not stat.S_ISREG(info.st_mode)
        or info.st_uid != os.getuid()
        or stat.S_IMODE(info.st_mode) != 0o600
        or info.st_nlink != 1
        or (maximum is not None and info.st_size > maximum)
    ):
        raise MemoryIndexError("memory_index_unsafe_cache")


def _physical(info):
    return info.st_dev, info.st_ino


def _span_digest(document_id, revision, text_sha256, start, end, amount, model, vector):
    coordinates = _canonical([document_id, revision, text_sha256, start, end, amount, model])
    return hashlib.sha256(coordinates + b"\0" + vector).hexdigest()


def _configure(connection, *, read_only=False):
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA trusted_schema=OFF")
    connection.execute("PRAGMA foreign_keys=ON")
    connection.execute("PRAGMA temp_store=MEMORY")
    connection.execute("PRAGMA busy_timeout=1000")
    if read_only:
        connection.execute("PRAGMA query_only=ON")


class _Shadow:
    def __init__(self, connection, name=None, identity=None, descriptor=None):
        self.connection, self.name, self.identity, self.descriptor = (
            connection,
            name,
            identity,
            descriptor,
        )
        self.published = False
        self.pending_cleanup = False


class MemoryIndex:
    def __init__(self, cache_directory, profile_binding, *, max_bytes=268435456):
        self.directory = Path(cache_directory)
        if (
            not self.directory.is_absolute()
            or ".." in self.directory.parts
            or not isinstance(profile_binding, str)
            or not _HASH.fullmatch(profile_binding)
            or not _integer(max_bytes, 16384, 268435456)
        ):
            raise MemoryIndexError("memory_index_invalid_configuration")
        self.profile_binding, self.max_bytes = profile_binding, max_bytes
        self._root_fd = self._lock_fd = None
        self._root_identity = self._lock_identity = None
        self._root_ready = False
        self._ram = {}
        self._tasks = set()
        self._owners = []
        self._build_lock = asyncio.Lock()
        self._progress = {}
        self._errors = {}
        self._closing = self._closed = False
        self._close_task = None

    def _scope_key(self, scope):
        if not isinstance(scope, MemoryScope):
            raise MemoryIndexError("memory_index_invalid_scope")
        return hashlib.sha256(_canonical([self.profile_binding, scope.key])).hexdigest()

    def _filename(self, scope):
        return self._scope_key(scope) + ".sqlite3"

    def _safe_root(self, *, create=False):
        if create:
            ensure_private_directory(self.directory)
        cursor = Path(self.directory.anchor)
        for component in self.directory.parts[1:]:
            cursor /= component
            info = cursor.lstat()
            if not stat.S_ISDIR(info.st_mode):
                raise MemoryIndexError("memory_index_unsafe_cache")
        info = self.directory.lstat()
        if info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) != 0o700:
            raise MemoryIndexError("memory_index_unsafe_cache")
        return _physical(info)

    def _check_root(self):
        if (
            self._root_fd is None
            or self._safe_root() != self._root_identity
            or _physical(os.fstat(self._root_fd)) != self._root_identity
        ):
            raise MemoryIndexError("memory_index_cache_changed")
        if self._root_ready:
            linked = os.stat("cache.lock", dir_fd=self._root_fd, follow_symlinks=False)
            _regular(linked, maximum=0)
            if (
                _physical(linked) != self._lock_identity
                or _physical(os.fstat(self._lock_fd)) != self._lock_identity
            ):
                raise MemoryIndexError("memory_index_cache_changed")

    def _ensure_owner(self, *, create=False):
        if self._root_fd is not None:
            if not self._root_ready:
                raise MemoryIndexError("memory_index_cleanup_pending")
            self._check_root()
            return
        expected = self._safe_root(create=create)
        descriptor = os.open(self.directory, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        self._root_fd, self._root_identity = descriptor, expected
        try:
            self._check_root()
            flags = os.O_RDWR | os.O_NOFOLLOW | os.O_NONBLOCK
            if create:
                flags |= os.O_CREAT
            lock = os.open("cache.lock", flags, 0o600, dir_fd=descriptor)
            self._lock_fd = lock
            self._lock_identity = _physical(os.fstat(lock))
            _regular(os.fstat(lock), maximum=0)
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            self._root_ready = True
        except BaseException as error:
            try:
                self._release_fds()
            except (OSError, MemoryIndexError):
                raise MemoryIndexError("memory_index_cleanup_pending") from None
            if isinstance(error, BlockingIOError):
                raise MemoryIndexError("memory_index_in_use") from None
            raise

    def _cache_bytes(self, *, exclude=None):
        self._check_root()
        total = 0
        for name in os.listdir(self._root_fd):
            if name == "cache.lock":
                continue
            if not _CACHE_FILE.fullmatch(name):
                raise MemoryIndexError("memory_index_unsafe_cache")
            info = os.stat(name, dir_fd=self._root_fd, follow_symlinks=False)
            _regular(info)
            if name != exclude:
                total += info.st_size
            if total > self.max_bytes:
                raise MemoryIndexError("memory_index_capacity")
        return total

    def _open_published(self, scope):
        self._ensure_owner()
        self._cache_bytes()
        name = self._filename(scope)
        info = os.stat(name, dir_fd=self._root_fd, follow_symlinks=False)
        _regular(info, maximum=self.max_bytes)
        connection = sqlite3.connect(
            "file:" + quote(str(self.directory / name), safe="/") + "?mode=ro&immutable=1",
            uri=True,
            isolation_level=None,
            timeout=1,
        )
        try:
            _configure(connection, read_only=True)
            self._assert_published(name, _physical(info))
            return connection, _physical(info)
        except BaseException:
            connection.close()
            raise

    def _assert_published(self, name, expected):
        self._check_root()
        info = os.stat(name, dir_fd=self._root_fd, follow_symlinks=False)
        _regular(info, maximum=self.max_bytes)
        if _physical(info) != expected:
            raise MemoryIndexError("memory_index_changed")

    def _metadata(self, connection):
        if (
            connection.execute("PRAGMA application_id").fetchone()[0] != _APPLICATION_ID
            or connection.execute("PRAGMA user_version").fetchone()[0] != _VERSION
        ):
            raise MemoryIndexError("memory_index_incompatible")
        if connection.execute("PRAGMA quick_check(1)").fetchone()[0] != "ok":
            raise MemoryIndexError("memory_index_corrupt")
        schema = {
            (r[0], r[1])
            for r in connection.execute(
                "SELECT type,name FROM sqlite_schema WHERE name NOT LIKE 'sqlite_%'"
            )
        }
        if schema != {
            ("table", "metadata"),
            ("table", "documents"),
            ("table", "spans"),
            ("index", "spans_document"),
        }:
            raise MemoryIndexError("memory_index_corrupt")
        meta = dict(connection.execute("SELECT key,value FROM metadata"))
        if (
            set(meta) != _META_KEYS
            or any(type(v) is not str for v in meta.values())
            or meta["version"] != "1"
            or meta["profile_binding"] != self.profile_binding
            or not _HASH.fullmatch(meta["snapshot_fingerprint"])
            or not _HASH.fullmatch(meta["source_manifest_sha256"])
            or not _HASH.fullmatch(meta["model_fingerprint"])
            or not _UUID.fullmatch(meta["publication_id"])
        ):
            raise MemoryIndexError("memory_index_corrupt")
        for key, maximum in (
            ("source_generation", 2**63 - 1),
            ("dimension", 4096),
            ("document_count", _MAX_DOCUMENTS),
            ("span_count", _MAX_SPANS),
        ):
            try:
                value = int(meta[key])
            except ValueError:
                raise MemoryIndexError("memory_index_corrupt") from None
            if (
                str(value) != meta[key]
                or not 0 <= value <= maximum
                or (key == "dimension" and not value)
            ):
                raise MemoryIndexError("memory_index_corrupt")
        if (
            connection.execute("SELECT count(*) FROM documents").fetchone()[0]
            != int(meta["document_count"])
            or connection.execute("SELECT count(*) FROM spans").fetchone()[0]
            != int(meta["span_count"])
            or connection.execute("PRAGMA foreign_key_check").fetchone() is not None
        ):
            raise MemoryIndexError("memory_index_corrupt")
        return meta

    def _check_snapshot(self, connection, meta, snapshot, manifest, model, dimension):
        if (
            meta["scope_key"] != snapshot.scope.key
            or meta["source_generation"] != str(snapshot.generation)
            or meta["snapshot_fingerprint"] != snapshot.fingerprint
            or meta["source_manifest_sha256"] != manifest
        ):
            raise MemoryIndexError("memory_index_stale")
        if meta["model_fingerprint"] != model or meta["dimension"] != str(dimension):
            raise MemoryIndexError("memory_index_model_changed")
        expected = sorted(
            (
                d.document.document_id,
                d.document.revision,
                d.document.text_sha256,
                d.document.byte_length,
            )
            for d in snapshot.documents
        )
        actual = [
            tuple(r)
            for r in connection.execute(
                "SELECT document_id,revision,text_sha256,byte_length FROM documents "
                "ORDER BY document_id"
            )
        ]
        if actual != expected:
            raise MemoryIndexError("memory_index_corrupt")

    def _spans(self, connection, snapshot, dimension, model, document_id=None):
        documents = {
            d.document.document_id: d
            for d in snapshot.documents
            if document_id is None or d.document.document_id == document_id
        }
        source_bytes = {key: doc.text.encode() for key, doc in documents.items()}
        coverage = {key: 0 for key in documents}
        previous = {}
        statement = (
            "SELECT s.*,d.revision,d.text_sha256 FROM spans s JOIN documents d USING(document_id) "
        )
        parameters = ()
        if document_id is not None:
            statement += "WHERE s.document_id=? "
            parameters = (document_id,)
        statement += "ORDER BY document_id,start_byte,end_byte"
        for row in connection.execute(statement, parameters):
            doc = documents.get(row["document_id"])
            if doc is None:
                raise MemoryIndexError("memory_index_corrupt")
            data = source_bytes[row["document_id"]]
            start, end = row["start_byte"], row["end_byte"]
            key = row["document_id"]
            if (
                not _integer(start)
                or not _integer(end, 1)
                or not 0 <= start < end <= len(data)
                or end - start > 768
                or not byte_boundary(data, start)
                or not byte_boundary(data, end)
                or start > coverage[key]
                or end <= previous.get(key, 0)
                or not _integer(row["token_count"], 1, 128)
                or type(row["vector"]) is not bytes
                or len(row["vector"]) != dimension * 4
                or row["span_sha256"]
                != _span_digest(
                    key,
                    row["revision"],
                    row["text_sha256"],
                    start,
                    end,
                    row["token_count"],
                    model,
                    row["vector"],
                )
            ):
                raise MemoryIndexError("memory_index_corrupt")
            raw = array("f")
            raw.frombytes(row["vector"])
            if sys.byteorder != "little":
                raw.byteswap()
            values = vector_values(raw, dimension)
            coverage[key] = max(coverage[key], end)
            previous[key] = end
            yield IndexedSpan(
                key,
                row["revision"],
                row["text_sha256"],
                start,
                end,
                row["token_count"],
                values,
            )
        if any(coverage[key] != len(source_bytes[key]) for key in documents):
            raise MemoryIndexError("memory_index_corrupt")

    def _reuse(self, snapshot, model, dimension, cancellation=None, deadline=None):
        """A cache hit needs the same physical profile, model and source identity.

        The full snapshot may have changed. Only immutable unchanged documents
        can reuse their vectors; removed documents never enter the new build.
        """
        owned = not snapshot.scope.temporary
        connection = None
        try:
            if owned:
                connection, _ = self._open_published(snapshot.scope)
            else:
                connection = self._ram.get(self._scope_key(snapshot.scope))
                if connection is None:
                    return None, False, None
            connection.set_progress_handler(
                lambda: int(
                    (cancellation is not None and cancellation.is_set())
                    or (deadline is not None and time.monotonic() >= deadline)
                ),
                1000,
            )
            meta = self._metadata(connection)
            if (
                meta["scope_key"] != snapshot.scope.key
                or meta["model_fingerprint"] != model
                or meta["dimension"] != str(dimension)
            ):
                if owned:
                    connection.close()
                return None, False, None
            return connection, owned, None
        except FileNotFoundError:
            return None, False, None
        except (MemoryIndexError, sqlite3.Error) as error:
            if connection is not None and owned:
                connection.close()
            checkpoint(cancellation, deadline)
            code = getattr(error, "code", "memory_index_corrupt")
            if code in {
                "memory_index_unsafe_cache",
                "memory_index_cache_changed",
                "memory_index_in_use",
                "memory_index_capacity",
            }:
                raise
            return None, False, code

    def _cleanup_owner(self, owner):
        if owner.connection is not None:
            owner.connection.close()
            owner.connection = None
        if owner.name is not None:
            self._check_root()
            try:
                info = os.stat(owner.name, dir_fd=self._root_fd, follow_symlinks=False)
            except FileNotFoundError:
                owner.name = None
            else:
                _regular(info)
                if _physical(info) != owner.identity:
                    raise MemoryIndexError("memory_index_cleanup_changed")
                os.unlink(owner.name, dir_fd=self._root_fd)
                owner.name = None
        if owner.descriptor is not None:
            if _physical(os.fstat(owner.descriptor)) != owner.identity:
                raise MemoryIndexError("memory_index_cleanup_changed")
            os.close(owner.descriptor)
            owner.descriptor = None
        if owner in self._owners:
            self._owners.remove(owner)

    async def _owned(self, callback):
        if self._closing or self._closed:
            raise MemoryIndexError("memory_index_closed")
        if any(owner.pending_cleanup for owner in self._owners):
            raise MemoryIndexError("memory_index_cleanup_pending")
        task = asyncio.create_task(callback())
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        try:
            return await asyncio.shield(task)
        except asyncio.CancelledError:
            task.cancel()
            while not task.done():
                try:
                    await asyncio.wait({task})
                except asyncio.CancelledError:
                    continue
            with suppress(BaseException):
                task.result()
            raise

    async def build(
        self,
        snapshot: ScopeSnapshot,
        encoder,
        *,
        cancellation=None,
        deadline=None,
        assert_current: Callable = lambda: None,
    ) -> dict:
        return await self._owned(
            lambda: self._build(snapshot, encoder, cancellation, deadline, assert_current)
        )

    @asynccontextmanager
    async def _build_slot(self, cancellation, deadline, assert_current):
        while True:
            checkpoint(cancellation, deadline, assert_current)
            try:
                await asyncio.wait_for(self._build_lock.acquire(), 0.05)
                break
            except TimeoutError:
                continue
        try:
            checkpoint(cancellation, deadline, assert_current)
            yield
        finally:
            self._build_lock.release()

    async def _build(self, snapshot, encoder, cancellation, deadline, assert_current):
        if deadline is None:
            deadline = time.monotonic() + 30
        async with self._build_slot(cancellation, deadline, assert_current):
            checkpoint(cancellation, deadline, assert_current)
            manifest = _manifest(snapshot)
            model, dimension = _model(encoder)
            key = self._scope_key(snapshot.scope)
            self._errors.pop(key, None)
            self._progress[key] = {
                "state": "building",
                "source_generation": snapshot.generation,
                "documents_done": 0,
                "spans_done": 0,
            }
            owner = None
            previous = None
            previous_owned = False
            published = False
            receipt = None
            try:
                if snapshot.scope.temporary:
                    connection = sqlite3.connect(":memory:", isolation_level=None)
                    owner = _Shadow(connection)
                    occupied = sum(
                        c.execute("PRAGMA page_count").fetchone()[0]
                        * c.execute("PRAGMA page_size").fetchone()[0]
                        for c in self._ram.values()
                    )
                    available = self.max_bytes - occupied
                    if available < 16384:
                        raise MemoryIndexError("memory_index_capacity")
                else:
                    self._ensure_owner(create=True)
                    available = self.max_bytes - self._cache_bytes()
                    if available < 16384:
                        raise MemoryIndexError("memory_index_capacity")
                    name = ".build-" + uuid4().hex + ".sqlite3"
                    descriptor = os.open(
                        name,
                        os.O_RDWR | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                        0o600,
                        dir_fd=self._root_fd,
                    )
                    identity = _physical(os.fstat(descriptor))
                    owner = _Shadow(None, name, identity, descriptor)
                    self._owners.append(owner)
                    connection = sqlite3.connect(
                        str(self.directory / name), isolation_level=None, timeout=1
                    )
                    owner.connection = connection
                    self._check_root()
                    if (
                        _physical(os.stat(name, dir_fd=self._root_fd, follow_symlinks=False))
                        != identity
                    ):
                        raise MemoryIndexError("memory_index_cache_changed")
                if owner not in self._owners:
                    self._owners.append(owner)
                _configure(connection)
                connection.execute("PRAGMA page_size=4096")
                connection.execute("PRAGMA journal_mode=DELETE")
                connection.execute("PRAGMA synchronous=FULL")
                pages = max(4, (available - 8192) // 4096)
                connection.execute("PRAGMA max_page_count=" + str(pages))
                connection.set_progress_handler(
                    lambda: int(
                        (cancellation is not None and cancellation.is_set())
                        or (deadline is not None and time.monotonic() >= deadline)
                    ),
                    1000,
                )
                previous, previous_owned, previous_error = self._reuse(
                    snapshot, model, dimension, cancellation, deadline
                )
                connection.executescript(_SCHEMA)
                connection.execute("BEGIN IMMEDIATE")
                count = 0
                reused_documents = 0
                for completed, item in enumerate(snapshot.documents, 1):
                    await asyncio.sleep(0)
                    checkpoint(cancellation, deadline, assert_current)
                    doc = item.document
                    reusable = (
                        None
                        if previous is None
                        else previous.execute(
                            "SELECT revision,text_sha256,byte_length FROM documents "
                            "WHERE document_id=?",
                            (doc.document_id,),
                        ).fetchone()
                    )
                    if reusable is not None and tuple(reusable) == (
                        doc.revision,
                        doc.text_sha256,
                        doc.byte_length,
                    ):
                        try:
                            reused_spans = []
                            for span in self._spans(
                                previous, snapshot, dimension, model, document_id=doc.document_id
                            ):
                                if len(reused_spans) % 32 == 0:
                                    await asyncio.sleep(0)
                                    checkpoint(cancellation, deadline, assert_current)
                                reused_spans.append(span)
                            spans = tuple(reused_spans)
                        except (MemoryIndexError, sqlite3.Error) as error:
                            checkpoint(cancellation, deadline, assert_current)
                            previous_error = getattr(error, "code", "memory_index_corrupt")
                            spans = None
                    else:
                        spans = None
                    if spans is None:
                        spans = await encoder.embed_document(
                            item.text, cancellation=cancellation, deadline=deadline
                        )
                    else:
                        reused_documents += 1
                    checkpoint(cancellation, deadline, assert_current)
                    if (
                        not isinstance(spans, (tuple, list))
                        or not spans
                        or len(spans) + count > _MAX_SPANS
                    ):
                        raise MemoryIndexError("memory_index_capacity")
                    connection.execute(
                        "INSERT INTO documents VALUES(?,?,?,?)",
                        (
                            doc.document_id,
                            doc.revision,
                            doc.text_sha256,
                            doc.byte_length,
                        ),
                    )
                    data = item.text.encode()
                    covered = 0
                    for span in spans:
                        if count % 32 == 0:
                            await asyncio.sleep(0)
                        checkpoint(cancellation, deadline, assert_current)
                        start, end, amount = (
                            span.start_byte,
                            span.end_byte,
                            span.token_count,
                        )
                        if (
                            not _integer(start)
                            or not _integer(end, 1)
                            or not 0 <= start < end <= len(data)
                            or end - start > 768
                            or not byte_boundary(data, start)
                            or not byte_boundary(data, end)
                            or start > covered
                            or end <= covered
                            or not _integer(amount, 1, 128)
                        ):
                            raise MemoryIndexError("memory_index_invalid_span")
                        values = vector_values(span.vector, dimension)
                        if sys.byteorder != "little":
                            values.byteswap()
                        vector = values.tobytes()
                        connection.execute(
                            "INSERT INTO spans VALUES(NULL,?,?,?,?,?,?)",
                            (
                                doc.document_id,
                                start,
                                end,
                                amount,
                                vector,
                                _span_digest(
                                    doc.document_id,
                                    doc.revision,
                                    doc.text_sha256,
                                    start,
                                    end,
                                    amount,
                                    model,
                                    vector,
                                ),
                            ),
                        )
                        covered = end
                        count += 1
                        if count % 32 == 0:
                            if (
                                connection.execute("PRAGMA page_count").fetchone()[0] * 4096
                                > available - 8192
                            ):
                                raise MemoryIndexError("memory_index_capacity")
                            await asyncio.sleep(0)
                    if covered != len(data):
                        raise MemoryIndexError("memory_index_invalid_span")
                    self._progress[key].update(documents_done=completed, spans_done=count)
                publication_id = uuid4().hex
                meta = {
                    "version": "1",
                    "profile_binding": self.profile_binding,
                    "scope_key": snapshot.scope.key,
                    "source_generation": str(snapshot.generation),
                    "snapshot_fingerprint": snapshot.fingerprint,
                    "source_manifest_sha256": manifest,
                    "model_fingerprint": model,
                    "dimension": str(dimension),
                    "document_count": str(len(snapshot.documents)),
                    "span_count": str(count),
                    "publication_id": publication_id,
                }
                connection.executemany("INSERT INTO metadata VALUES(?,?)", meta.items())
                connection.execute("PRAGMA application_id=" + str(_APPLICATION_ID))
                connection.execute("PRAGMA user_version=1")
                checkpoint(cancellation, deadline, assert_current)
                connection.execute("COMMIT")
                self._metadata(connection)
                self._check_snapshot(connection, meta, snapshot, manifest, model, dimension)
                connection.set_progress_handler(None, 0)
                receipt = {
                    "status": "completed",
                    "publication_id": publication_id,
                    "source_generation": snapshot.generation,
                    "snapshot_fingerprint": snapshot.fingerprint,
                    "model_fingerprint": model,
                    "documents": len(snapshot.documents),
                    "chunks": count,
                    "reused_documents": reused_documents,
                    "encoded_documents": len(snapshot.documents) - reused_documents,
                    "previous_cache_error": previous_error,
                    "temporary": snapshot.scope.temporary,
                    "cache_max_bytes": self.max_bytes,
                }
                if snapshot.scope.temporary:
                    checkpoint(cancellation, deadline, assert_current)
                    old = self._ram.get(key)
                    self._ram[key] = connection
                    owner.connection = None
                    published = True
                    if old is not None:
                        try:
                            old.close()
                        except sqlite3.Error:
                            retained = _Shadow(old)
                            retained.pending_cleanup = True
                            self._owners.append(retained)
                            raise MemoryIndexError(
                                "memory_index_cleanup_pending", receipt=receipt
                            ) from None
                else:
                    connection.close()
                    owner.connection = None
                    os.fsync(owner.descriptor)
                    info = os.fstat(owner.descriptor)
                    _regular(info, maximum=available)
                    self._cache_bytes()
                    checkpoint(cancellation, deadline, assert_current)
                    self._check_root()
                    current = os.stat(owner.name, dir_fd=self._root_fd, follow_symlinks=False)
                    if _physical(current) != owner.identity:
                        raise MemoryIndexError("memory_index_cache_changed")
                    target = self._filename(snapshot.scope)
                    try:
                        existing = os.stat(target, dir_fd=self._root_fd, follow_symlinks=False)
                    except FileNotFoundError:
                        pass
                    else:
                        _regular(existing, maximum=self.max_bytes)
                    os.replace(
                        owner.name,
                        target,
                        src_dir_fd=self._root_fd,
                        dst_dir_fd=self._root_fd,
                    )
                    owner.name = None
                    owner.published = True
                    published = True
                    try:
                        os.fsync(self._root_fd)
                    except OSError:
                        raise MemoryIndexError(
                            "memory_index_publication_unknown",
                            receipt={**receipt, "status": "unknown"},
                        ) from None
                self._progress.pop(key, None)
                return receipt
            except MemoryIndexError as error:
                self._errors[key] = error.code
                raise
            except (OSError, sqlite3.Error) as error:
                checkpoint(cancellation, deadline, assert_current)
                code = (
                    "memory_index_publication_unknown"
                    if published
                    else (
                        "memory_index_capacity"
                        if isinstance(error, sqlite3.Error) and "full" in str(error).lower()
                        else "memory_index_unavailable"
                    )
                )
                self._errors[key] = code
                unknown_receipt = (
                    {**receipt, "status": "unknown"} if published and receipt else None
                )
                raise MemoryIndexError(code, receipt=unknown_receipt) from None
            except BaseException as error:
                code = getattr(error, "code", "memory_index_failed")
                self._errors[key] = (
                    code
                    if isinstance(code, str) and re.fullmatch(r"[a-z0-9_]{1,80}", code)
                    else "memory_index_failed"
                )
                raise
            finally:
                self._progress.pop(key, None)
                cleanup_failed = False
                if previous is not None and previous_owned:
                    try:
                        previous.close()
                    except sqlite3.Error:
                        retained = _Shadow(previous)
                        retained.pending_cleanup = True
                        self._owners.append(retained)
                        cleanup_failed = True
                elif previous is not None:
                    previous.set_progress_handler(None, 0)
                if owner is not None:
                    try:
                        self._cleanup_owner(owner)
                    except (OSError, sqlite3.Error, MemoryIndexError):
                        owner.pending_cleanup = True
                        if owner not in self._owners:
                            self._owners.append(owner)
                        self._errors[key] = "memory_index_cleanup_pending"
                        raise MemoryIndexError(
                            "memory_index_cleanup_pending", receipt=receipt
                        ) from None
                if cleanup_failed:
                    self._errors[key] = "memory_index_cleanup_pending"
                    raise MemoryIndexError("memory_index_cleanup_pending", receipt=receipt)

    async def search(
        self,
        snapshot: ScopeSnapshot,
        query,
        encoder,
        *,
        mode,
        limit=10,
        offset=0,
        cancellation=None,
        deadline=None,
        assert_current=lambda: None,
    ):
        return await self._owned(
            lambda: self._search(
                snapshot,
                query,
                encoder,
                mode,
                limit,
                offset,
                cancellation,
                deadline,
                assert_current,
            )
        )

    async def _search(
        self,
        snapshot,
        query,
        encoder,
        mode,
        limit,
        offset,
        cancellation,
        deadline,
        assert_current,
    ):
        if deadline is None:
            deadline = time.monotonic() + 5
        validate_query(query, mode, limit, offset)
        checkpoint(cancellation, deadline, assert_current)
        manifest = _manifest(snapshot)
        model, dimension = _model(encoder)
        key = self._scope_key(snapshot.scope)
        connection = None
        try:
            if snapshot.scope.temporary:
                published = self._ram.get(key)
                if published is None:
                    raise MemoryIndexError("memory_index_missing")
                connection = sqlite3.connect(":memory:", isolation_level=None)
                published.backup(connection)
                _configure(connection, read_only=True)
                identity = published
            else:
                connection, identity = self._open_published(snapshot.scope)
            connection.set_progress_handler(
                lambda: int(
                    (cancellation is not None and cancellation.is_set())
                    or (deadline is not None and time.monotonic() >= deadline)
                ),
                1000,
            )
            meta = self._metadata(connection)
            self._check_snapshot(connection, meta, snapshot, manifest, model, dimension)
            query_vector = vector_values(
                await encoder.embed_query(query, cancellation=cancellation, deadline=deadline),
                dimension,
            )
            checkpoint(cancellation, deadline, assert_current)

            def current():
                assert_current()
                if snapshot.scope.temporary:
                    if self._ram.get(key) is not identity:
                        raise MemoryIndexError("memory_index_changed")
                else:
                    self._assert_published(self._filename(snapshot.scope), identity)

            result = await retrieve(
                snapshot,
                self._spans(connection, snapshot, dimension, model),
                query_vector,
                query,
                mode=mode,
                limit=limit,
                offset=offset,
                cancellation=cancellation,
                deadline=deadline,
                assert_current=current,
            )
            current()
            return result
        except FileNotFoundError:
            raise MemoryIndexError("memory_index_missing") from None
        except MemoryIndexError as error:
            self._errors[key] = error.code
            raise
        except sqlite3.Error:
            checkpoint(cancellation, deadline, assert_current)
            raise MemoryIndexError("memory_index_corrupt") from None
        except OSError:
            raise MemoryIndexError("memory_index_unavailable") from None
        finally:
            if connection is not None:
                try:
                    connection.close()
                except sqlite3.Error:
                    retained = _Shadow(connection)
                    retained.pending_cleanup = True
                    self._owners.append(retained)
                    raise MemoryIndexError("memory_index_cleanup_pending") from None

    def status(self, scope):
        key = self._scope_key(scope)
        result = {
            "state": "absent",
            "temporary": scope.temporary,
            "cache_max_bytes": self.max_bytes,
            "active_operations": len(self._tasks),
            "cleanup_pending": any(owner.pending_cleanup for owner in self._owners),
        }
        if self._closed:
            return {**result, "state": "closed"}
        if key in self._errors:
            result["last_error"] = self._errors[key]
        if key in self._progress:
            result.update(self._progress[key])
        connection = None
        status_deadline = time.monotonic() + 0.15
        try:
            if scope.temporary:
                connection = self._ram.get(key)
                if connection is None:
                    return result
                connection.set_progress_handler(
                    lambda: int(time.monotonic() >= status_deadline), 1000
                )
                meta = self._metadata(connection)
                size = (
                    connection.execute("PRAGMA page_count").fetchone()[0]
                    * connection.execute("PRAGMA page_size").fetchone()[0]
                )
            else:
                connection, _ = self._open_published(scope)
                connection.set_progress_handler(
                    lambda: int(time.monotonic() >= status_deadline), 1000
                )
                meta = self._metadata(connection)
                size = os.stat(
                    self._filename(scope), dir_fd=self._root_fd, follow_symlinks=False
                ).st_size
            if meta["scope_key"] != scope.key:
                raise MemoryIndexError("memory_index_corrupt")
            return {
                **result,
                "state": result["state"] if key in self._progress else "available",
                "source_generation": int(meta["source_generation"]),
                "snapshot_fingerprint": meta["snapshot_fingerprint"],
                "index_fingerprint": hashlib.sha256(_canonical(meta)).hexdigest(),
                "model_fingerprint": meta["model_fingerprint"],
                "dimension": int(meta["dimension"]),
                "documents": int(meta["document_count"]),
                "chunks": int(meta["span_count"]),
                "cache_bytes": size,
                "freshness": "requires_current_snapshot_check",
            }
        except FileNotFoundError:
            return result
        except MemoryIndexError as error:
            return {**result, "state": "unavailable", "last_error": error.code}
        except (OSError, sqlite3.Error):
            return {
                **result,
                "state": "unavailable",
                "last_error": (
                    "memory_index_timeout"
                    if time.monotonic() >= status_deadline
                    else "memory_index_corrupt"
                ),
            }
        finally:
            if connection is not None and scope.temporary:
                connection.set_progress_handler(None, 0)
            if connection is not None and not scope.temporary:
                try:
                    connection.close()
                except sqlite3.Error:
                    retained = _Shadow(connection)
                    retained.pending_cleanup = True
                    self._owners.append(retained)

    def _release_fds(self):
        for attr, identity_attr in (
            ("_lock_fd", "_lock_identity"),
            ("_root_fd", "_root_identity"),
        ):
            descriptor = getattr(self, attr)
            if descriptor is not None:
                identity = getattr(self, identity_attr)
                if identity is None:
                    identity = _physical(os.fstat(descriptor))
                    setattr(self, identity_attr, identity)
                if _physical(os.fstat(descriptor)) != identity:
                    raise MemoryIndexError("memory_index_cleanup_changed")
                os.close(descriptor)
                setattr(self, attr, None)
                setattr(self, identity_attr, None)
        self._root_ready = False

    async def close(self):
        self._closing = True
        if (
            self._close_task is not None
            and self._close_task.done()
            and self._close_task.exception() is not None
        ):
            self._close_task = None
        if self._close_task is None:
            self._close_task = asyncio.create_task(self._close())
        return await asyncio.shield(self._close_task)

    async def forget_temporary(self, scope):
        """Retire an isolated RAM cache after its coordinator has joined work."""
        if not isinstance(scope, MemoryScope) or not scope.temporary:
            raise MemoryIndexError("memory_index_invalid_snapshot")
        if self._tasks:
            raise MemoryIndexError("memory_index_in_use")
        key = self._scope_key(scope)
        connection = self._ram.get(key)
        if connection is not None:
            try:
                connection.close()
            except sqlite3.Error:
                retained = _Shadow(connection)
                retained.pending_cleanup = True
                self._owners.append(retained)
                raise MemoryIndexError("memory_index_cleanup_pending") from None
            self._ram.pop(key)
        self._errors.pop(key, None)
        self._progress.pop(key, None)

    async def _close(self):
        tasks = tuple(self._tasks)
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        try:
            for owner in tuple(self._owners):
                self._cleanup_owner(owner)
            for key, connection in tuple(self._ram.items()):
                connection.close()
                self._ram.pop(key)
            self._release_fds()
        except (OSError, sqlite3.Error, MemoryIndexError):
            raise MemoryIndexError("memory_index_cleanup_pending") from None
        self._closed = True
        return {"closed": True, "cleanup_confirmed": True, "active_operations": 0}
