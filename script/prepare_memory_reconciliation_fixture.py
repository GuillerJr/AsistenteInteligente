#!/usr/bin/env python3
"""Own disconnected restored-memory fixtures, with an independent SQL oracle.

Only creates a fresh marked directory under the OS temporary root. No account,
personal profile, model, network or application is opened. Activation here is
synthetic fixture construction; the GUI qualifications use their normal gates.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
import sys
import tempfile
import threading
import time
from pathlib import Path
from uuid import uuid4

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from jarvis_core.activation import activate_prepared_profile, review_prepared_profile
from jarvis_core.memory import MemoryScope, MemoryStore
from jarvis_core.profiles import _initial
from jarvis_core.recovery import (
    JsonSource,
    NewTarget,
    RecoveryContext,
    SourceBundle,
    SqliteImageSource,
    canonical,
    create_backup,
    restore_new,
    verify_backup,
)
from jarvis_core.recovery_fs import Directory, FileIdentity, PinnedFile

FORMAT = "jarvis-memory-reconciliation-fixture/1"
SOURCE = MemoryScope("jarvis", "fixture_memory_origin")
SUFFIXES = ("", "-wal", "-shm", "-journal")
TEMP = Path("/private/tmp" if sys.platform == "darwin" else "/tmp")


def context():
    return RecoveryContext(uuid4().hex, "memory_fixture", 1, lambda stage, write: None,
                           threading.Event(), time.monotonic() + 30)


def _target(parent, name):
    return NewTarget(parent, FileIdentity.of(parent.stat()), name,
                     forbidden_roots=(parent / "source",))


def _read_private(path, maximum=64 * 1024 * 1024):
    with Directory(path.parent) as directory, PinnedFile(directory, path.name, maximum) as source:
        data = source.read(source.check)
        source.check()
        directory.check()
        return data, source.info


def physical_family(profile):
    result = {}
    for suffix in SUFFIXES:
        path = profile / ("memory.sqlite3" + suffix)
        try:
            data, info = _read_private(path)
        except FileNotFoundError:
            result[path.name] = None
            continue
        result[path.name] = {
            "identity": [info.st_dev, info.st_ino], "mode": info.st_mode & 0o7777,
            "bytes": info.st_size, "mtime_ns": info.st_mtime_ns, "ctime_ns": info.st_ctime_ns,
            "sha256": hashlib.sha256(data).hexdigest(),
        }
    return result


def logical_projection(connection):
    """Independent SQL expectation, never the product's receipt validator."""
    tables = ("scopes", "documents", "chunks", "metadata")
    result = {}
    for table in tables:
        result[table] = sorted([list(row) for row in connection.execute("SELECT * FROM " + table)],
                               key=canonical)
    return result


def make_cold_wal(profile):
    """Capture only an owned, idle fixture writer; close it before restoring bytes."""
    with MemoryStore(profile / "memory.sqlite3") as store:
        store.list_documents(SOURCE)
        db = store._disk
        assert db.execute("PRAGMA journal_mode=WAL").fetchone()[0] == "wal"
        db.execute("PRAGMA wal_autocheckpoint=0")
        store.upsert(SOURCE, "wal", "NOTA CONFIRMADA EN WAL Ω 6421",
                     source_uri="jarvis://fixture-wal")
        expected = logical_projection(db)
        raw = {suffix: (profile / ("memory.sqlite3" + suffix)).read_bytes()
               for suffix in SUFFIXES if (profile / ("memory.sqlite3" + suffix)).exists()}
    for suffix in SUFFIXES:
        path = profile / ("memory.sqlite3" + suffix)
        if suffix in raw:
            path.write_bytes(raw[suffix])
            path.chmod(0o600)
        else:
            path.unlink(missing_ok=True)
    return expected


def make_cold_journal(profile, *, hot=False):
    """Synthetic PERSIST rollback family; uncommitted pages never become notes."""
    db = sqlite3.connect(profile / "memory.sqlite3", isolation_level=None)
    try:
        assert db.execute("PRAGMA journal_mode=PERSIST").fetchone()[0] == "persist"
        db.execute("PRAGMA synchronous=FULL")
        db.execute("PRAGMA cache_size=1")
        expected = logical_projection(db)
        db.execute("BEGIN IMMEDIATE")
        # Force page spilling, and hence a synced hot journal header, using only
        # uncommitted fixture titles. The last committed notes remain the oracle.
        db.execute("UPDATE documents SET title=?", ("UNCOMMITTED FIXTURE " * 2048,))
        if not hot:
            db.execute("ROLLBACK")
        raw = {suffix: (profile / ("memory.sqlite3" + suffix)).read_bytes()
               for suffix in SUFFIXES if (profile / ("memory.sqlite3" + suffix)).exists()}
        assert raw["-journal"][:8] == (b"\xd9\xd5\x05\xf9\x20\xa1\x63\xd7"
                                      if hot else bytes(8))
    finally:
        if db.in_transaction:
            db.execute("ROLLBACK")
        db.close()
    for suffix in SUFFIXES:
        path = profile / ("memory.sqlite3" + suffix)
        if suffix in raw:
            path.write_bytes(raw[suffix])
            path.chmod(0o600)
        else:
            path.unlink(missing_ok=True)
    return expected


def prepare(parent, *, mode="wal"):
    if mode not in {"delete", "wal", "journal_hot", "journal_inactive"}:
        raise ValueError("fixture_mode_invalid")
    parent = Path(parent).resolve()
    if parent.parent != TEMP or not parent.name.startswith("jarvis-memory-"):
        raise ValueError("fixture_fresh_temporary_root_required")
    if any(parent.iterdir()):
        raise ValueError("fixture_root_must_be_empty")
    parent.chmod(0o700)
    source = parent / "source"
    source.mkdir(mode=0o700)
    with MemoryStore(source / "memory.sqlite3") as store:
        store.upsert(SOURCE, "primera", "NOTA ORIGINAL ÁMBAR 4826",
                     source_uri="file:///FIXTURE-NO-ABRIR")
        store.upsert(SOURCE, "segunda", "NOTA ORIGINAL TOPACIO 7319",
                     source_uri="jarvis://fixture-memory")
        store.upsert(SOURCE, "primera", "NOTA ORIGINAL ÁMBAR REVISIÓN DOS 4826",
                     source_uri="file:///FIXTURE-NO-ABRIR")
        store.upsert(MemoryScope("jarvis", SOURCE.workspace_id, "a" * 32), "sesion",
                     "NOTA LIGADA A SESIÓN; NO COPIAR", source_uri="jarvis://fixture-memory")
        image = store._disk.serialize()
        identity = FileIdentity.of((source / "memory.sqlite3").stat())
    bundle = SourceBundle("memory_fixture", 1, source, FileIdentity.of(source.stat()),
                          (SqliteImageSource("memory", image, identity,
                                             hashlib.sha256(image).hexdigest(), lambda: None),),
                          (JsonSource("agents", canonical(_initial()), lambda: None),),
                          ("state", "schedules", "skills", "telegram"))
    create_backup(bundle, _target(parent, "backup"), context())
    verified = verify_backup(parent / "backup", context())
    restore_new(parent / "backup", verified, _target(parent, "profile"), context())
    profile = parent / "profile"
    activate_prepared_profile(review_prepared_profile(profile, context()), context())
    if mode == "wal":
        expected = make_cold_wal(profile)
    elif mode in {"journal_hot", "journal_inactive"}:
        expected = make_cold_journal(profile, hot=mode == "journal_hot")
    elif mode == "delete":
        with sqlite3.connect(":memory:") as connection:
            connection.deserialize((profile / "memory.sqlite3").read_bytes())
            expected = logical_projection(connection)
    value = {"format": FORMAT, "profile": str(profile), "mode": mode,
             "source_scope": SOURCE.key, "source_projection": expected,
             "source_family": physical_family(profile), "provider_used": False,
             "activation": "synthetic_fixture_construction"}
    (parent / "fixture.json").write_bytes(canonical(value))
    (parent / "fixture.json").chmod(0o600)
    return value


def observe(parent):
    parent = Path(parent).resolve()
    raw, _ = _read_private(parent / "fixture.json", 4 * 1024 * 1024)
    value = json.loads(raw)
    if (value["format"] != FORMAT or parent.parent != TEMP
            or not parent.name.startswith("jarvis-memory-")
            or value["profile"] != str(parent / "profile")):
        raise ValueError("fixture_marker_invalid")
    profile = parent / "profile"
    before = physical_family(profile)
    with tempfile.TemporaryDirectory(prefix="jarvis-memory-oracle-", dir=TEMP) as directory:
        stage = Path(directory) / "memory.sqlite3"
        for suffix in ("", "-wal", "-journal"):
            original = profile / ("memory.sqlite3" + suffix)
            try:
                raw, _ = _read_private(original)
            except FileNotFoundError:
                continue
            with stage.with_name(stage.name + suffix).open("xb") as stream:
                stream.write(raw)
            stage.with_name(stage.name + suffix).chmod(0o600)
        connection = sqlite3.connect(stage)
        try:
            result = logical_projection(connection)
            has_receipts = connection.execute(
                "SELECT 1 FROM sqlite_schema WHERE name='memory_scope_copies'").fetchone()
            receipts = [list(row) for row in connection.execute(
                "SELECT * FROM memory_scope_copies ORDER BY operation_id")] if has_receipts else []
            mappings = ([list(row) for row in connection.execute(
                "SELECT * FROM memory_scope_copy_documents ORDER BY operation_id,ordinal")]
                if has_receipts else [])
        finally:
            connection.close()
    if physical_family(profile) != before:
        raise ValueError("fixture_oracle_source_changed")
    source = value["source_scope"]
    expected = value["source_projection"]
    original_documents = [row for row in result["documents"] if row[1] == source]
    expected_documents = [row for row in expected["documents"] if row[1] == source]
    current = {row[0]: row for row in result["documents"]}
    expected_by_id = {row[0]: row for row in expected_documents}
    generations = dict(result["scopes"])
    copies_match = True
    for receipt in receipts:
        maps = [row for row in mappings if row[0] == receipt[0]]
        copies_match &= (receipt[2] == source and receipt[5] != source
                         and receipt[8] == len(expected_documents) == len(maps)
                         and receipt[9] == sum(row[8] for row in expected_documents)
                         and receipt[10] == hashlib.sha256(canonical(
                             [row[1:] for row in maps])).hexdigest()
                         and generations.get(receipt[5]) == receipt[7]
                         and {row[2] for row in maps} == set(expected_by_id))
        for row in maps:
            before, after = expected_by_id.get(row[2]), current.get(row[6])
            copies_match &= bool(before and after and row[1] >= 0
                                 and row[2] != row[6] and row[7] == after[5] == 1
                                 and row[3:6] == [before[5], before[7], before[8]]
                                 and row[8:10] == [after[7], after[8]]
                                 and after[1] == receipt[5]
                                 and before[2:5] == after[2:5]
                                 and before[6:9] == after[6:9])
    return {"source_documents_intact": original_documents == expected_documents,
            "current_projection": result, "receipts": receipts, "mappings": mappings,
            "current_copies_match": copies_match,
            "original_physical_family_unchanged": before == value["source_family"],
            "oracle_did_not_modify_source": True}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("prepare", "observe"))
    parser.add_argument("--root", type=Path)
    parser.add_argument("--mode", choices=("delete", "wal", "journal_hot", "journal_inactive"),
                        default="wal")
    args = parser.parse_args()
    root = args.root or Path(tempfile.mkdtemp(prefix="jarvis-memory-", dir=TEMP))
    value = prepare(root, mode=args.mode) if args.action == "prepare" else observe(root)
    print(json.dumps(value, ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()
