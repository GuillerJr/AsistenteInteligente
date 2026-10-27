"""Finite, immutable scoped views of already approved memory documents."""

from __future__ import annotations

import hashlib
import json
import math
import time
from contextlib import contextmanager
from dataclasses import asdict, dataclass

from jarvis_core.memory import DocumentContent, MemoryError, MemoryScope


@dataclass(frozen=True)
class ScopeSnapshot:
    scope: MemoryScope
    generation: int
    fingerprint: str
    documents: tuple[DocumentContent, ...]


def _deadline(value):
    if value is None:
        return time.monotonic() + 5
    if type(value) not in {int, float} or not math.isfinite(value):
        raise MemoryError("memory_snapshot_deadline_invalid")
    return value


def _check(cancellation, deadline):
    if cancellation is not None and cancellation.is_set():
        raise MemoryError("memory_cancelled")
    if time.monotonic() >= deadline:
        raise MemoryError("memory_deadline")


@contextmanager
def _locked(store, cancellation, deadline):
    # SQLite's configured busy_timeout is already finite (one second). Unlike
    # RLock.__enter__, this wait also observes cancellation and the caller's cut.
    _check(cancellation, deadline)
    while not store._mutex.acquire(timeout=min(0.02, max(0, deadline - time.monotonic()))):
        _check(cancellation, deadline)
    try:
        _check(cancellation, deadline)
        yield
    finally:
        store._mutex.release()


def snapshot_scope(store, scope, *, cancellation=None, deadline=None):
    if not isinstance(scope, MemoryScope):
        raise MemoryError("invalid_scope")
    deadline = _deadline(deadline)
    with _locked(store, cancellation, deadline):
        try:
            with store._transaction(scope) as connection:
                connection.set_progress_handler(
                    lambda: int(time.monotonic() >= deadline or
                                (cancellation is not None and cancellation.is_set())), 1000)
                try:
                    _check(cancellation, deadline)
                    generation = store._generation(connection, scope)
                    rows = connection.execute(
                        "SELECT * FROM documents WHERE scope_key=? ORDER BY key", (scope.key,))
                    documents, total = [], 0
                    for row in rows:
                        _check(cancellation, deadline)
                        record = store._record(row)
                        data = store._assert_text(row)
                        total += len(data)
                        if (len(documents) >= store.limits.max_documents_per_scope or
                                total > store.limits.max_scope_bytes):
                            raise MemoryError("memory_snapshot_capacity")
                        documents.append(DocumentContent(record, data.decode("utf-8")))
                    identity = {
                        "format": "jarvis-memory-snapshot/1", "scope": scope.key,
                        "generation": generation,
                        "documents": [asdict(item.document) for item in documents],
                    }
                    digest = hashlib.sha256(json.dumps(
                        identity, sort_keys=True, ensure_ascii=False,
                        separators=(",", ":"), allow_nan=False).encode()).hexdigest()
                    _check(cancellation, deadline)
                    return ScopeSnapshot(scope, generation, digest, tuple(documents))
                finally:
                    # Cancellation must not interrupt the rollback/commit that
                    # settles our read transaction and releases its SQLite lock.
                    connection.set_progress_handler(None, 0)
        except MemoryError:
            _check(cancellation, deadline)
            raise


def scope_generation(store, scope, *, cancellation=None, deadline=None):
    if not isinstance(scope, MemoryScope):
        raise MemoryError("invalid_scope")
    deadline = _deadline(deadline)
    with _locked(store, cancellation, deadline):
        with store._transaction(scope) as connection:
            _check(cancellation, deadline)
            generation = store._generation(connection, scope)
            _check(cancellation, deadline)
            return generation


def binding_epoch(store, scope):
    """Invalidate provider bindings without opening a cold/absent memory family.

    Pure chat with recall disabled must not recover/checkpoint a cold WAL just
    to compute a fingerprint. Temporary chats never stat the durable family.
    """
    if not isinstance(scope, MemoryScope):
        raise MemoryError("invalid_scope")
    with _locked(store, None, time.monotonic() + 5):
        if store._closed:
            raise MemoryError("memory_closed")
        connection = store._memory if scope.temporary else store._disk
        if connection is not None:
            generation = scope_generation(store, scope)
            version = connection.execute("PRAGMA data_version").fetchone()[0]
            identity = [scope.key, "open", generation, version]
        elif scope.temporary:
            identity = [scope.key, "ram_unopened"]
        else:
            family = []
            for suffix in ("", "-wal", "-journal"):
                try:
                    info = store.path.with_name(store.path.name + suffix).lstat()
                    family.append((suffix, info.st_dev, info.st_ino, info.st_mode,
                                   info.st_size, info.st_mtime_ns, info.st_ctime_ns))
                except FileNotFoundError:
                    family.append((suffix, "absent"))
                except OSError:
                    family.append((suffix, "unavailable"))
            identity = [scope.key, "cold_unopened", family]
        return hashlib.sha256(json.dumps(identity, separators=(",", ":"),
                                         ensure_ascii=False).encode()).hexdigest()
