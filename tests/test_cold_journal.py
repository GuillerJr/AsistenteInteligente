"""Native rollback fixtures, original inode/metadata/content remain untouched."""

import hashlib
import sqlite3
import time
from dataclasses import replace
from uuid import uuid4

import pytest
from journal_fixture import make_journal
from wal_fixture import NAMES, TABLES, oracle

from jarvis_core import cold_wal
from jarvis_core.cold_journal import ColdJournalError, capture_journal
from jarvis_core.cold_sqlite import inspect_cold_family
from jarvis_core.recovery import RecoveryContext, RecoveryLimits
from jarvis_core.tools import HostCancellation


@pytest.fixture
def scratch(tmp_path, monkeypatch):
    path = tmp_path / "scratch"
    path.mkdir(mode=0o700)
    monkeypatch.setattr(cold_wal, "_TEMP_ROOT", path)
    return path


def capture(root, role, *, limits=None, callback=None):
    family = inspect_cold_family(root / NAMES[role], role)

    def guard(stage, write=False):
        assert not write
        if callback:
            callback(stage)

    context = RecoveryContext(uuid4().hex, "journal-fixture", 0, guard,
                              HostCancellation(), time.monotonic() + 20)
    return capture_journal(family, context, limits or RecoveryLimits(), family.assert_current)


@pytest.mark.parametrize("role", NAMES)
@pytest.mark.parametrize("hot,mode", [(True, "DELETE"), (True, "PERSIST"),
                                      (False, "PERSIST"), (False, "TRUNCATE")])
def test_real_multi_sector_journal_recovers_or_ignores_committed_body(
    tmp_path, scratch, monkeypatch, role, hot, mode,
):
    root = tmp_path / "profile"
    expected = make_journal(root, (role,), mode=mode, hot=hot)[role]
    before = oracle(root)
    connect, opened = sqlite3.connect, []

    def private(path, *args, **kwargs):
        value = str(path)
        assert value == ":memory:" or value.startswith(scratch.as_uri() + "/jarvis-cold-journal-")
        opened.append(value)
        return connect(path, *args, **kwargs)

    monkeypatch.setattr(sqlite3, "connect", private)
    data, digest, metadata = capture(root, role)
    assert digest == hashlib.sha256(data).hexdigest()
    assert data[18:20] == b"\1\1"
    with connect(":memory:") as db:
        db.deserialize(data)
        assert db.execute("PRAGMA integrity_check").fetchall() == [("ok",)]
        rows = db.execute("SELECT title,count(*) FROM " + TABLES[role]
                          + " GROUP BY title").fetchall()
        assert rows == [(expected["title"], expected["rows"])]
    assert metadata["applied"] is hot
    if hot:
        assert metadata["headers"] > 1
        assert metadata["unsynced_records_not_applied"] > 0
    assert oracle(root) == before
    assert list(scratch.iterdir()) == []
    assert len([value for value in opened if value != ":memory:"]) == 1


@pytest.mark.parametrize("mutation,code", [
    ("checksum", "checksum_invalid"), ("page", "page_invalid"),
    ("sector", "invalid"), ("short", "truncated"),
    ("padding", "ambiguous_tail"), ("super", "super_journal_unsupported"),
])
def test_damaged_or_ambiguous_journal_never_becomes_partial_success(
    tmp_path, scratch, mutation, code,
):
    root = tmp_path / "profile"
    make_journal(root, ("memory",))
    path = root / "memory.sqlite3-journal"
    raw = bytearray(path.read_bytes())
    page_size = int.from_bytes(raw[24:28], "big")
    sector = int.from_bytes(raw[20:24], "big")
    if mutation == "checksum":
        raw[sector + page_size + 4] ^= 1
    elif mutation == "page":
        raw[sector:sector + 4] = bytes(4)
    elif mutation == "sector":
        raw[20:24] = (513).to_bytes(4, "big")
    elif mutation == "short":
        raw = raw[:-1]
    elif mutation == "padding":
        raw[28] = 1
    else:
        raw += b"untrusted-super-path" + b"\0" * 8 + bytes.fromhex("d9d505f920a163d7")
    path.write_bytes(raw)
    before = oracle(root)
    with pytest.raises(ColdJournalError, match="profile_cold_journal_" + code):
        capture(root, "memory")
    assert oracle(root) == before and list(scratch.iterdir()) == []


@pytest.mark.parametrize("maximum", ["max_sqlite_bytes", "max_total_bytes", "max_rows",
                                      "max_vm_steps"])
def test_journal_limits_reject_before_native_effect(tmp_path, scratch, maximum):
    root = tmp_path / "profile"
    make_journal(root, ("memory",))
    before = oracle(root)
    limits = replace(RecoveryLimits(), **{maximum: 1})
    with pytest.raises(ColdJournalError, match=r"profile_cold_journal_.*limit"):
        capture(root, "memory", limits=limits)
    assert oracle(root) == before and list(scratch.iterdir()) == []


def test_journal_cancellation_settles_all_private_resources(tmp_path, scratch):
    root = tmp_path / "profile"
    make_journal(root, ("memory",))
    before = oracle(root)

    class Cancelled(BaseException):
        pass

    def cancel(stage):
        if stage == "cold_journal_validate":
            raise Cancelled()

    with pytest.raises(Cancelled):
        capture(root, "memory", callback=cancel)
    assert oracle(root) == before and list(scratch.iterdir()) == []
