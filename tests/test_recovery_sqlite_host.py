"""Full host backup/doctor/restore of own SQLite crash variants.

Only synthetic profiles and synthetic authority decisions. SQLite never opens
an original during recovery or a staged result; result checks deserialize RAM.
"""

import asyncio
import sqlite3

import pytest
from journal_fixture import make_journal
from test_wal_host_boundaries import (
    NoAccount,
    assert_pending_owner,
    decide,
    hold_cleanup,
)
from wal_fixture import NAMES, TABLES, make_wal, oracle

from jarvis_core import cold_wal
from jarvis_core.service import JarvisService

_VARIANTS = ["wal_absent", "wal_empty", "wal_header", "journal_hot_delete",
             "journal_hot_persist", "journal_inactive_persist", "journal_inactive_truncate"]


def prepare(root, role, variant):
    if variant.startswith("journal"):
        hot = "inactive" not in variant
        mode = variant.rsplit("_", 1)[-1].upper()
        expected = make_journal(root, (role,), mode=mode, hot=hot, duplicates=64)[role]
        return expected["title"], expected["rows"]
    markers = make_wal(root, (role,))
    wal = root / (NAMES[role] + "-wal")
    header = wal.read_bytes()[:32]
    with sqlite3.connect(root / NAMES[role]) as db:  # fixture preparation before host admission.
        db.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    db.close()
    for suffix in ("-wal", "-shm"):
        (root / (NAMES[role] + suffix)).unlink(missing_ok=True)
    if variant != "wal_absent":
        wal.write_bytes(header if variant == "wal_header" else b"")
        wal.chmod(0o600)
    return markers[role]["committed"], 1


async def finish(coordinator, operation):
    identity = operation if isinstance(operation, str) else operation["operation_id"]
    async with asyncio.timeout(35):
        while True:
            result = await coordinator.status({"operation_id": identity})
            if not result["worker_pending"]:
                return result
            await asyncio.sleep(.005)


def params(review):
    return {"review_id": review["review_id"], "review_digest": review["review_digest"]}


@pytest.mark.parametrize("role", NAMES)
@pytest.mark.parametrize("variant", _VARIANTS)
async def test_cold_variants_complete_doctor_backup_verified_restore_without_opening_source(
    tmp_path, monkeypatch, role, variant,
):
    root = tmp_path / "profile"
    expected_title, expected_count = prepare(root, role, variant)
    before = oracle(root)
    peer = NoAccount()
    service = JarvisService(root, peer)
    connect = sqlite3.connect

    def no_original(path, *args, **kwargs):
        private_prefix = cold_wal._TEMP_ROOT.as_uri() + "/jarvis-cold-"
        assert str(path) == ":memory:" or str(path).startswith(private_prefix)
        return connect(path, *args, **kwargs)

    monkeypatch.setattr(sqlite3, "connect", no_original)
    try:
        coordinator = service.maintenance_coordinator
        result = await finish(coordinator, await coordinator.doctor({}))
        assert result["phase"] == "complete", result
        row = next(x for x in result["result"]["report"]["checks"] if x["role"] == role)
        assert row["integrity"] == "ok" and row["inspection_source"] == "host_ram_snapshot"
        assert service.profile_gate._cut is None
        review = await coordinator.review_backup({"destination_parent": str(tmp_path),
                                                   "name": "backup"})
        mode = review["metadata"]["sqlite_recovery"][0]["mode"]
        assert mode.startswith("cold_wal" if variant.startswith("wal") else "cold_journal")
        operation = await coordinator.backup(params(review))
        assert not (tmp_path / "backup").exists()
        await decide(coordinator)
        result = await finish(coordinator, operation)
        assert result["phase"] == "complete" and result["receipt_durable"], result
        assert not result["worker_pending"] and not result["cleanup_owner_pending"]
        verified = await finish(coordinator, await coordinator.verify({
            "backup_path": str(tmp_path / "backup")}))
        assert verified["phase"] == "complete", verified
        review = await coordinator.review_restore({
            "verification_id": verified["result"]["verification_id"],
            "destination_parent": str(tmp_path), "name": "restored"})
        operation = await coordinator.restore(params(review))
        await decide(coordinator)
        restored = await finish(coordinator, operation)
        assert restored["phase"] == "staged_disarmed" and restored["receipt_durable"], restored
        assert restored["receipt"]["effect_status"] == "new_disarmed_profile"
        data = (tmp_path / "restored" / NAMES[role]).read_bytes()
        assert data[18:20] == b"\1\1"
        with connect(":memory:") as db:
            db.deserialize(data)
            rows = db.execute("SELECT title,count(*) FROM " + TABLES[role]
                              + " GROUP BY title").fetchall()
            assert rows == [(expected_title, expected_count)]
            if role == "schedules":
                enabled = db.execute("SELECT count(*) FROM schedules WHERE enabled!=0").fetchone()
                assert enabled == (0,)
        names = {p.name for p in (tmp_path / "restored").iterdir()}
        assert "restore_record.json" in names
        assert not names & {"auth.json", "service.sock", "host.sock", "activation.json"}
        assert not any(name.endswith(("-wal", "-shm", "-journal")) for name in names)
        assert service.profile_gate._cut is None
        assert service.store._disk is service.memory._disk is None
        assert service.schedule_store._connection is None
        assert oracle(root) == before and peer.calls == 0
    finally:
        await service.aclose()


@pytest.mark.parametrize("variant", ["journal_hot_delete", "journal_inactive_persist"])
async def test_journal_cleanup_before_next_read_keeps_same_owner_cut_without_recapture(
    tmp_path, monkeypatch, variant,
):
    root = tmp_path / "profile"
    prepare(root, "memory", variant)
    peer = NoAccount()
    service = JarvisService(root, peer)
    before = oracle(root)
    control = {"allow_cleanup": False}
    seen, _ = hold_cleanup(monkeypatch, service, control)
    try:
        coordinator = service.maintenance_coordinator
        result = await finish(coordinator, await coordinator.doctor({}))
        operation, held, owner = await assert_pending_owner(service, result, seen)
        assert type(owner).__name__ == "JournalCaptureOwner"
        original_cut_id = held.cut.cut_id
        control["allow_cleanup"] = True
        await coordinator.cleanup({"operation_id": operation.operation_id})
        result = await finish(coordinator, operation.operation_id)
        assert not result["cleanup_owner_pending"] and held.released and owner.closed
        assert original_cut_id == held.cut.cut_id
        assert all(item[0] is owner for item in seen)
        # The next read is a fresh operation only after confirmed cleanup.
        monkeypatch.undo()
        next_read = await finish(coordinator, await coordinator.doctor({}))
        assert next_read["phase"] == "complete", next_read
        assert service.profile_gate._cut is None
        assert oracle(root) == before and peer.calls == 0
    finally:
        control["allow_cleanup"] = True
        await service.aclose()
