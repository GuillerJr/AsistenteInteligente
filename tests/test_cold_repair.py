"""Explicit candidate recovery never replaces, opens, or truncates a source."""

import hashlib
import sqlite3
import time
from uuid import uuid4

import pytest
from wal_fixture import NAMES, make_wal, oracle

from jarvis_core import cold_wal
from jarvis_core.cold_repair import ColdRepairError, repair_cold_image
from jarvis_core.cold_sqlite import inspect_cold_family
from jarvis_core.cold_wal import ColdWalError
from jarvis_core.memory import MemoryScope, MemoryStore
from jarvis_core.recovery import RecoveryContext, RecoveryLimits
from jarvis_core.recovery_sqlite import SnapshotError, inspect_sqlite_snapshot
from jarvis_core.tools import HostCancellation


@pytest.fixture
def scratch(tmp_path, monkeypatch):
    path = tmp_path / "scratch"
    path.mkdir(mode=0o700)
    monkeypatch.setattr(cold_wal, "_TEMP_ROOT", path)
    return path


def context():
    return RecoveryContext(uuid4().hex, "repair-fixture", 0, lambda *a, **k: None,
                           HostCancellation(), time.monotonic() + 20)


@pytest.mark.parametrize("tail", ["truncated", "checksum", "old_generation"])
def test_prefix_repair_is_explicit_measured_and_source_bound(tmp_path, scratch, tail):
    root = tmp_path / "profile"
    markers = make_wal(root, ("state",))
    wal = root / (NAMES["state"] + "-wal")
    raw = wal.read_bytes()
    stride = 24 + int.from_bytes(raw[8:12], "big")
    if tail == "truncated":
        damaged = raw + raw[-stride:][:101]
    else:
        frame = bytearray(raw[-stride:])
        frame[16 if tail == "checksum" else 8] ^= 1
        damaged = raw + frame
    wal.write_bytes(damaged)
    before = oracle(root)
    family = inspect_cold_family(root / NAMES["state"], "state")
    with pytest.raises(ColdWalError):
        family.capture_image(context(), RecoveryLimits(), family.assert_current)
    candidate = repair_cold_image(family, context(), RecoveryLimits(), family.assert_current,
                                  "wal_valid_prefix")
    metadata = candidate.metadata()
    assert metadata["discarded_bytes"] == len(damaged) - len(raw)
    assert metadata["lost_committed_transactions"] == "unknown"
    assert metadata["data_loss_possible"] is True
    assert metadata["source_modified"] is False
    assert metadata["source_fingerprint"] == family.fingerprint
    assert metadata["source_member_sha256"]["-wal"] == hashlib.sha256(damaged).hexdigest()
    assert metadata["image_sha256"] == candidate.sha256
    assert candidate.sha256 == hashlib.sha256(candidate.data).hexdigest()
    with sqlite3.connect(":memory:") as db:
        db.deserialize(candidate.data)
        actual = db.execute("SELECT title FROM sessions").fetchone()[0]
        assert actual == markers["state"]["committed"]
    candidate.check_source(context(), family.assert_current)
    metadata["source_member_sha256"].clear()
    assert candidate.metadata()["source_member_sha256"]
    assert oracle(root) == before and list(scratch.iterdir()) == []
    wal.write_bytes(damaged + b"changed")
    with pytest.raises(ValueError):
        candidate.check_source(context(), family.assert_current)
    assert list(scratch.iterdir()) == []


def test_prefix_without_verified_commit_cannot_claim_recovery(tmp_path, scratch):
    root = tmp_path / "profile"
    make_wal(root, ("state",))
    wal = root / (NAMES["state"] + "-wal")
    raw = bytearray(wal.read_bytes())
    raw[48] ^= 1  # first frame checksum: no validated commit exists.
    wal.write_bytes(raw)
    before = oracle(root)
    family = inspect_cold_family(root / NAMES["state"], "state")
    with pytest.raises(ColdWalError):
        repair_cold_image(family, context(), RecoveryLimits(), family.assert_current,
                          "wal_valid_prefix")
    assert oracle(root) == before and list(scratch.iterdir()) == []


def memory_fixture(root, damage):
    root.mkdir(mode=0o700)
    path = root / "memory.sqlite3"
    with MemoryStore(path) as store:
        store.upsert(MemoryScope("jarvis", "f" * 64), "repair-fixture", "PRUEBA MEMORIA BASE",
                     title="JARVIS REPAIR", source_uri="jarvis://synthetic-repair")
    with sqlite3.connect(path) as db:  # fixture preparation, before capture.
        if damage == "missing":
            db.execute("DELETE FROM chunk_fts")
        elif damage == "extra":
            db.execute("INSERT INTO chunk_fts(rowid,text) VALUES(1000,'synthetic extra')")
        elif damage == "wrong":
            db.execute("UPDATE chunk_fts SET text='synthetic wrong'")
        elif damage == "base":
            db.execute("UPDATE documents SET text='broken hash'")
            db.execute("DELETE FROM chunk_fts")
    db.close()
    return inspect_cold_family(path, "memory")


@pytest.mark.parametrize("damage", ["missing", "extra", "wrong"])
def test_memory_rebuild_preserves_base_data_and_reconstructs_only_derived_fts(
    tmp_path, scratch, damage,
):
    root = tmp_path / "profile"
    family = memory_fixture(root, damage)
    before = oracle(root)
    with pytest.raises(SnapshotError):
        family.capture_image(context(), RecoveryLimits(), family.assert_current)
    candidate = repair_cold_image(family, context(), RecoveryLimits(), family.assert_current,
                                  "memory_fts_rebuild")
    inspection = inspect_sqlite_snapshot("memory", candidate.data, check_current=lambda: None)
    assert inspection.counts["documents"] == 1
    metadata = candidate.metadata()
    assert metadata["base_data_loss"] is False
    assert metadata["base_rows_preserved"]["chunks"] == 1
    assert metadata["derived_rows_after"] == 1
    with sqlite3.connect(":memory:") as db:
        db.deserialize(candidate.data)
        assert db.execute("SELECT text FROM documents").fetchone() == ("PRUEBA MEMORIA BASE",)
        assert db.execute("SELECT text FROM chunk_fts").fetchone() == ("PRUEBA MEMORIA BASE",)
    candidate.check_source(context(), family.assert_current)
    assert oracle(root) == before and list(scratch.iterdir()) == []


def test_memory_rebuild_refuses_to_invent_or_repair_base_content(tmp_path, scratch):
    root = tmp_path / "profile"
    family = memory_fixture(root, "base")
    before = oracle(root)
    with pytest.raises(SnapshotError, match="snapshot_memory_content_invalid"):
        repair_cold_image(family, context(), RecoveryLimits(), family.assert_current,
                          "memory_fts_rebuild")
    assert oracle(root) == before and list(scratch.iterdir()) == []


def test_clean_memory_rebuild_is_not_a_repair(tmp_path, scratch):
    root = tmp_path / "profile"
    family = memory_fixture(root, "clean")
    before = oracle(root)
    with pytest.raises(ColdRepairError, match="profile_cold_repair_not_needed"):
        repair_cold_image(family, context(), RecoveryLimits(), family.assert_current,
                          "memory_fts_rebuild")
    assert oracle(root) == before and list(scratch.iterdir()) == []


def test_truncated_third_commit_recovers_second_with_loss_warning(tmp_path, scratch):
    root = tmp_path / "profile"
    make_wal(root, ("state",))
    wal = root / "jarvis.sqlite3-wal"
    wal.write_bytes(wal.read_bytes()[:-1])
    before = oracle(root)
    family = inspect_cold_family(root / "jarvis.sqlite3", "state")
    candidate = repair_cold_image(family, context(), RecoveryLimits(), family.assert_current,
                                  "wal_valid_prefix")
    with sqlite3.connect(":memory:") as db:
        db.deserialize(candidate.data)
        assert db.execute("SELECT title FROM sessions").fetchone() == (
            "JARVIS WAL COMMIT DOS state",)
    assert candidate.metadata()["data_loss_possible"] is True
    assert candidate.metadata()["lost_committed_transactions"] == "unknown"
    assert oracle(root) == before and list(scratch.iterdir()) == []


def test_clean_wal_cannot_be_called_prefix_repair(tmp_path, scratch):
    root = tmp_path / "profile"
    make_wal(root, ("state",))
    family = inspect_cold_family(root / "jarvis.sqlite3", "state")
    before = oracle(root)
    with pytest.raises(ColdWalError, match="profile_cold_repair_not_needed"):
        repair_cold_image(family, context(), RecoveryLimits(), family.assert_current,
                          "wal_valid_prefix")
    assert oracle(root) == before and list(scratch.iterdir()) == []


def test_memory_copy_history_is_preserved_for_portable_publisher(tmp_path, scratch):
    from test_memory_copy_portability import image
    root = tmp_path / "profile"
    root.mkdir(mode=0o700)
    path = root / "memory.sqlite3"
    path.write_bytes(image(fts=True, change=lambda db: db.execute("DELETE FROM chunk_fts")))
    path.chmod(0o600)
    family = inspect_cold_family(path, "memory")
    before = oracle(root)
    candidate = repair_cold_image(family, context(), RecoveryLimits(), family.assert_current,
                                  "memory_fts_rebuild")
    inspection = inspect_sqlite_snapshot("memory", candidate.data, check_current=lambda: None)
    assert inspection.user_version == 2
    assert inspection.counts["memory_scope_copies"] == 1
    assert candidate.metadata()["base_rows_preserved"]["memory_scope_copy_documents"] == 1
    assert oracle(root) == before and list(scratch.iterdir()) == []


def test_repair_native_close_uncertainty_retains_actual_owner(tmp_path, scratch, monkeypatch):
    from jarvis_core import cold_repair
    root = tmp_path / "profile"
    family = memory_fixture(root, "missing")
    before = oracle(root)
    original = cold_repair._rebuild_memory_fts
    fail_once = []

    class Connection:
        def __init__(self, actual):
            self.actual = actual

        def close(self):
            if not fail_once:
                fail_once.append(True)
                raise OSError("synthetic close refusal")
            self.actual.close()

    def rebuild(owner, *args):
        result = original(owner, *args)
        owner.connections["source"] = Connection(owner.connections["source"])
        return result

    monkeypatch.setattr(cold_repair, "_rebuild_memory_fts", rebuild)
    with pytest.raises(ColdWalError) as error:
        repair_cold_image(family, context(), RecoveryLimits(), family.assert_current,
                          "memory_fts_rebuild")
    owner = error.value.owner
    assert isinstance(owner, cold_wal.WalCaptureOwner) and not owner.closed
    assert owner.connections
    owner.cleanup()
    assert owner.closed and not owner.connections
    assert oracle(root) == before and list(scratch.iterdir()) == []
