"""Explicit, bounded candidates; original SQLite families are never modified.

Candidates are bytes, not permission to publish, replace or activate a profile.
A caller must separately authorize the exact plan and retain its cold cut until
source revalidation and publication finish. Lost WAL transactions stay unknown.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from dataclasses import dataclass, field

from jarvis_core import cold_wal, memory
from jarvis_core.recovery_sqlite import (
    _expected,
    _schema,
    deny_extension_loading,
    inspect_sqlite_snapshot,
)


class ColdRepairError(cold_wal.ColdWalError):
    pass


@dataclass(frozen=True)
class RepairCandidate:
    data: bytes = field(repr=False)
    sha256: str
    _details: str = field(repr=False)
    _family: object = field(repr=False, compare=False)
    _limits: object = field(repr=False, compare=False)

    def metadata(self):
        # A fresh JSON object prevents a caller from altering captured bindings.
        return json.loads(self._details)

    def check_source(self, context, check_current):
        family, limits = self._family, self._limits
        expected = self.metadata()
        owner = cold_wal.WalCaptureOwner(family, limits.max_total_bytes)

        def check():
            checkpoint, hashes = cold_wal.acquire_family(
                owner, context, limits, check_current, set())
            if (family.fingerprint != expected["source_fingerprint"]
                    or {suffix or "main": value.hex() for suffix, value in hashes.items()}
                    != expected["source_member_sha256"]):
                raise ColdRepairError("profile_cold_repair_source_changed")
            cold_wal.finalize_family(owner, hashes, checkpoint)

        return cold_wal.run_owned_capture(owner, check)


def _uninspected_image(family, context, limits, check_current):
    if family.wal_eligible:
        return cold_wal.capture_wal(family, context, limits, check_current, inspect_image=False)
    if family.journal_eligible:
        from jarvis_core.cold_journal import capture_journal
        return capture_journal(family, context, limits, check_current, inspect_image=False)
    if not family.eligible:
        raise ColdRepairError("profile_cold_repair_unavailable")
    owner = cold_wal.WalCaptureOwner(family, limits.max_total_bytes)

    def capture():
        checkpoint, hashes = cold_wal.acquire_family(owner, context, limits, check_current, {""})
        page_size = cold_wal._main_page_size(family.header, family.members[0][5])
        data = cold_wal.native_image(owner, limits, checkpoint,
                                     pages=family.members[0][5] // page_size,
                                     page_size=page_size, wal=False, inspect_image=False)
        cold_wal.finalize_family(owner, hashes, checkpoint)
        return data, hashlib.sha256(data).hexdigest(), {
            "source_fingerprint": family.fingerprint,
            "source_member_sha256": {suffix or "main": digest.hex()
                                     for suffix, digest in hashes.items()},
        }

    return cold_wal.run_owned_capture(owner, capture)


def _rebuild_memory_fts(owner, data, context, limits, check_current):
    """Rebuild only the derived FTS index from validated own base tables.

    No SQL from sqlite_schema is executed. Recreating all tables from Jarvis's
    constants also avoids carrying damaged FTS shadow rows into the candidate.
    Arbitrary SQLite/B-tree recovery is deliberately not inferred from this.
    """
    steps, interrupted = 0, []

    def checkpoint():
        context.checkpoint("cold_repair_memory_fts")
        check_current()

    def progress():
        nonlocal steps
        steps += 1000
        try:
            checkpoint()
            if steps > limits.max_vm_steps:
                raise ColdRepairError("profile_cold_repair_work_limit")
        except BaseException as error:
            interrupted.append(error)
            return 1
        return 0

    source = sqlite3.connect(":memory:", check_same_thread=False)
    owner.connections["source"] = source
    try:
        for db in (source,):
            deny_extension_loading(db)
            db.setlimit(sqlite3.SQLITE_LIMIT_LENGTH, limits.max_sqlite_bytes)
            db.execute("PRAGMA temp_store=MEMORY")
            db.execute("PRAGMA trusted_schema=OFF")
        source.deserialize(data)
        source.execute("PRAGMA query_only=ON")
        source.set_progress_handler(progress, 1000)
        version = source.execute("PRAGMA user_version").fetchone()[0]
        if (source.execute("PRAGMA application_id").fetchone()[0] != memory._APPLICATION_ID
                or version not in {1, 2} or _schema(source) != _expected("memory", True, version)):
            raise ColdRepairError("profile_cold_repair_schema_unsupported")
        if source.execute("PRAGMA quick_check").fetchall() != [("ok",)]:
            raise ColdRepairError("profile_cold_repair_base_integrity_failed")
        before = source.execute("SELECT count(*) FROM chunk_fts").fetchone()[0]
        changes = source.execute(
            "SELECT count(*) FROM chunks c LEFT JOIN chunk_fts f ON f.rowid=c.chunk_id "
            "WHERE f.rowid IS NULL OR f.text!=c.text"
        ).fetchone()[0]
        extras = source.execute(
            "SELECT count(*) FROM chunk_fts f LEFT JOIN chunks c ON c.chunk_id=f.rowid "
            "WHERE c.chunk_id IS NULL"
        ).fetchone()[0]
        if not changes and not extras:
            raise ColdRepairError("profile_cold_repair_not_needed")
        if before > limits.max_rows:
            raise ColdRepairError("profile_cold_repair_row_limit")
        target = sqlite3.connect(":memory:", check_same_thread=False)
        owner.connections["ram"] = target
        deny_extension_loading(target)
        target.execute("PRAGMA temp_store=MEMORY")
        target.execute("PRAGMA trusted_schema=OFF")
        page_size = int.from_bytes(data[16:18], "big")
        page_size = 65536 if page_size == 1 else page_size
        target.execute(f"PRAGMA page_size={page_size}")
        target.execute(f"PRAGMA max_page_count={limits.max_sqlite_bytes // page_size}")
        target.set_progress_handler(progress, 1000)
        target.executescript(memory._SCHEMA)
        if version == 2:
            target.executescript(memory._COPY_SCHEMA)
        target.execute("CREATE VIRTUAL TABLE chunk_fts USING "
                       "fts5(text, tokenize='unicode61 remove_diacritics 2')")
        target.execute(f"PRAGMA application_id={memory._APPLICATION_ID}")
        target.execute(f"PRAGMA user_version={version}")
        tables = ("scopes", "documents", "chunks", "metadata")
        if version == 2:
            tables += ("memory_scope_copies", "memory_scope_copy_documents")
        counts = {}
        total_bytes = len(data)
        for table in tables:
            cursor = source.execute("SELECT * FROM " + table)
            statement = ("INSERT INTO " + table + " VALUES("
                         + ",".join("?" for _ in cursor.description) + ")")
            count = 0
            for row in cursor:
                checkpoint()
                count += 1
                if count > limits.max_rows:
                    raise ColdRepairError("profile_cold_repair_row_limit")
                for value in row:
                    if isinstance(value, bytes):
                        raise ColdRepairError("profile_cold_repair_base_data_invalid")
                    if isinstance(value, str):
                        size = len(value.encode())
                        if size > limits.sqlite().max_cell_bytes:
                            raise ColdRepairError("profile_cold_repair_size_limit")
                        total_bytes += size
                        if total_bytes > limits.max_total_bytes:
                            raise ColdRepairError("profile_cold_repair_size_limit")
                target.execute(statement, row)
            counts[table] = count
        target.execute("INSERT INTO chunk_fts(rowid,text) SELECT chunk_id,text FROM chunks")
        target.commit()
        candidate = target.serialize()
        if (len(candidate) > limits.max_sqlite_bytes
                or len(data) + len(candidate) > limits.max_total_bytes):
            raise ColdRepairError("profile_cold_repair_size_limit")
        inspect_sqlite_snapshot("memory", candidate, check_current=checkpoint,
                                limits=limits.sqlite())
        checkpoint()
        return candidate, {"base_rows_preserved": counts, "derived_rows_before": before,
                           "derived_rows_after": counts["chunks"],
                           "derived_missing_or_mismatched_rows": changes,
                           "derived_extra_rows_removed": extras,
                           "base_data_loss": False}
    except sqlite3.Error:
        if interrupted:
            raise interrupted[0] from None
        raise ColdRepairError("profile_cold_repair_sqlite_unavailable") from None


def repair_cold_image(family, context, limits, check_current, kind):
    """Produce a new image; authorization/publication belong to the host."""
    if kind == "wal_valid_prefix":
        data, digest, details = cold_wal.capture_wal(family, context, limits,
                                                    check_current, prefix=True)
        details.update(semantics="validated_committed_prefix_only",
                       lost_committed_transactions="unknown", data_loss_possible=True)
    elif kind == "memory_fts_rebuild":
        if family.role != "memory":
            raise ColdRepairError("profile_cold_repair_kind_unsupported")
        data, _, details = _uninspected_image(family, context, limits, check_current)
        owner = cold_wal.WalCaptureOwner(family, limits.max_total_bytes)
        source_data = data
        data, repair = cold_wal.run_owned_capture(owner, lambda: _rebuild_memory_fts(
            owner, source_data, context, limits, check_current))
        digest = hashlib.sha256(data).hexdigest()
        details.update(repair, semantics="derived_index_rebuilt_from_preserved_base_rows")
    else:
        raise ColdRepairError("profile_cold_repair_kind_unsupported")
    details.update(kind=kind, role=family.role, image_sha256=digest, image_bytes=len(data),
                   source_mode=family.capture_mode, source_modified=False)
    candidate = RepairCandidate(
        data, digest, json.dumps(details, sort_keys=True, separators=(",", ":")), family, limits)
    candidate.check_source(context, check_current)
    return candidate
