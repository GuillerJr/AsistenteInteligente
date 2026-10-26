"""Pure image acquisition for host memory review, without lazy-store opening."""
from __future__ import annotations

import hashlib
from dataclasses import dataclass, replace

from jarvis_core.host_profile import HostProfileError
from jarvis_core.memory import MemoryCopyLimits, MemoryError
from jarvis_core.recovery import (
    PORTABLE_CONFIG,
    RecoveryLimits,
    SqliteSource,
    canonical,
    capture_sqlite_snapshot,
)
from jarvis_core.recovery_fs import FileIdentity
from jarvis_core.recovery_sqlite import inspect_sqlite_snapshot


@dataclass(frozen=True)
class MemoryImage:
    data: bytes | None
    identity: FileIdentity | None
    schema_version: int | None
    source_kind: str
    scopes_total: int
    sibling_bytes: int
    copy_limits: MemoryCopyLimits
    sha256: str | None
    row_counts: tuple[tuple[str, int], ...]


def _capture(row, frozen, cut, context, limits):
    def check():
        context.checkpoint("memory_source_snapshot")
        frozen.assert_current(cut)

    check()
    if row.connection is not None:
        source = SqliteSource(row.role, row.connection, check)
        with row.store._mutex:
            data = capture_sqlite_snapshot(source, context, limits, frozen.service.directory)
    elif row.cold is not None and row.cold.members[0] is None:
        return None
    elif row.cold is not None and row.cold.snapshot_eligible:
        data, _ = row.cold.capture_image(context, limits, check)
    else:
        raise HostProfileError("profile_memory_source_unavailable")
    check()
    inspect_sqlite_snapshot(row.role, data, check_current=check, limits=limits.sqlite())
    return data


def read_memory_image(frozen, cut, context, *, recovery_limits=None, copy_limits=None,
                      for_copy=True):
    """All source image sizes belong to this one still-held coherent cut.

    Sibling payloads are discarded after validation/counting; no body, URI or
    history is exposed. Missing resources remain missing. Copies clamp their
    actual SQLite page cap to the remaining aggregate profile payload budget.
    """
    limits = recovery_limits or RecoveryLimits()
    copy_limits = copy_limits or MemoryCopyLimits()
    frozen.assert_current(cut, full=True)
    row = next(item for item in frozen._sql if item.role == "memory")
    if row.fingerprint[0] == "absent":
        return MemoryImage(None, None, None, "absent", 0, 0, copy_limits, None, ())
    if row.connection is None and not (row.cold and row.cold.snapshot_eligible):
        raise HostProfileError("profile_memory_source_unavailable")
    portable = PORTABLE_CONFIG if type(PORTABLE_CONFIG) is bytes else canonical(PORTABLE_CONFIG)
    total = len(portable) + sum(len(item.data) for item in frozen._json if item.data is not None)
    # Measure exact sibling images, including WAL-visible owned pages. Main-file
    # fstat sizes alone could underestimate a committed WAL snapshot.
    for sibling in frozen._sql if for_copy else ():
        if sibling.role == "memory":
            continue
        remaining = limits.max_total_bytes - total
        if remaining <= 0:
            raise HostProfileError("profile_memory_snapshot_budget")
        bound = replace(limits, max_sqlite_bytes=min(limits.max_sqlite_bytes, remaining))
        data = _capture(sibling, frozen, cut, context, bound)
        if data is not None:
            total += len(data)
        del data
    remaining = limits.max_total_bytes - total if for_copy else copy_limits.max_sqlite_bytes
    if remaining <= 0:
        raise HostProfileError("profile_memory_snapshot_budget")
    effective = replace(copy_limits, max_sqlite_bytes=min(copy_limits.max_sqlite_bytes, remaining))
    bound = replace(limits, max_sqlite_bytes=min(limits.max_sqlite_bytes,
                                                effective.max_sqlite_bytes))
    data = _capture(row, frozen, cut, context, bound)
    if data is None:
        raise HostProfileError("profile_memory_source_changed")
    inspected = inspect_sqlite_snapshot("memory", data,
                                       check_current=lambda: frozen.assert_current(cut),
                                       limits=bound.sqlite())
    frozen.assert_current(cut, full=True)
    source_kind = ("owned_snapshot" if row.connection is not None else
                   "cold_wal_snapshot" if row.cold.wal_eligible else
                   "cold_journal_snapshot" if row.cold.journal_eligible else
                   "cold_delete_snapshot")
    return MemoryImage(data, FileIdentity(*row.fingerprint[1:3]), inspected.schema_version,
                       source_kind,
                       inspected.counts["scopes"], total, effective,
                       hashlib.sha256(data).hexdigest(), inspected.row_counts)


def validate_memory_projection(image, review, snapshot_limits):
    """Keep every resulting table within the real portable inspector row cap."""
    counts = dict(image.row_counts)
    allowed = {"scopes", "documents", "chunks", "metadata", "memory_scope_copies",
               "memory_scope_copy_documents"}
    if set(counts) - allowed:
        raise MemoryError("memory_copy_snapshot_count_unknown")
    projected = dict(counts)
    projected["scopes"] += int(not review.destination.exists)
    projected["documents"] += review.source.document_count
    projected["chunks"] += review.new_chunk_count
    # The exact RAM inspector already proves that FTS, if present, has a 1:1
    # row match with chunks. No SQL or parallel source-body parser is needed.
    projected["chunk_fts_if_present"] = projected["chunks"]
    projected["memory_scope_copies"] = counts.get("memory_scope_copies", 0) + 1
    projected["memory_scope_copy_documents"] = (
        counts.get("memory_scope_copy_documents", 0) + review.source.document_count)
    if any(value > snapshot_limits.max_rows for value in projected.values()):
        raise MemoryError("memory_copy_snapshot_row_limit")
    return tuple(sorted(projected.items()))
