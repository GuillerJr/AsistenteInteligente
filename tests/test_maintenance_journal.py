from dataclasses import replace
from uuid import uuid4

import pytest

from jarvis_core.maintenance_journal import MaintenanceJournal, wire
from jarvis_core.private_json import PrivateJsonError
from jarvis_core.recovery import RecoveryReceipt
from jarvis_core.recovery_fs import FileIdentity


def test_previous_unsettled_receipt_is_read_without_recovery_or_mutation(tmp_path):
    journal = MaintenanceJournal(tmp_path)
    identity = uuid4().hex
    journal.record(identity, "backup.create", "a" * 64, "working")
    path = tmp_path / "maintenance.json"
    before = (path.read_bytes(), path.stat().st_mtime_ns, set(tmp_path.iterdir()))
    reloaded = MaintenanceJournal(tmp_path)
    assert reloaded.previous[0]["operation_id"] == identity
    assert reloaded.previous[0]["phase"] == "interrupted_unknown"
    assert (path.read_bytes(), path.stat().st_mtime_ns, set(tmp_path.iterdir())) == before


def test_receipt_keeps_primary_cleanup_and_exact_64_bit_identity(tmp_path):
    journal = MaintenanceJournal(tmp_path)
    receipt = RecoveryReceipt(
        uuid4().hex, "backup.create", "unknown", "unknown", "respaldo",
        target_identity=FileIdentity(2**63 + 1, 2**64 - 1),
        error_code="recovery_cancelled", cleanup_error_code="recovery_cleanup_identity_changed",
        cleanup_pending=True,
    )
    journal.record(receipt.operation_id, receipt.purpose, "b" * 64,
                   "publication_unknown", receipt)
    stored = MaintenanceJournal(tmp_path).previous[0]["receipt"]
    assert stored == wire(receipt)
    assert stored["target_identity"] == {
        "device": str(2**63 + 1), "inode": str(2**64 - 1)}
    assert stored["cleanup_error_code"] == "recovery_cleanup_identity_changed"
    assert stored["error_code"] == "recovery_cancelled"


@pytest.mark.parametrize("invalid", ["../otro", "/private/otro", "raw\nPRIVATE DATA"])
def test_invalid_receipt_is_not_persisted_or_exposed(tmp_path, invalid):
    journal = MaintenanceJournal(tmp_path)
    receipt = RecoveryReceipt(uuid4().hex, "backup.create", "failed",
                              "no_published_effect", "respaldo")
    with pytest.raises(PrivateJsonError, match="maintenance_journal_invalid"):
        journal.record(receipt.operation_id, receipt.purpose, "b" * 64,
                       "failed", replace(receipt, target_name=invalid))
    assert journal.previous == ()
    assert not (tmp_path / "maintenance.json").exists()


def test_bounded_journal_never_evicts_unresolved_receipts_to_admit_new_work(tmp_path):
    journal = MaintenanceJournal(tmp_path)
    for _ in range(64):
        journal.record(uuid4().hex, "backup.create", "a" * 64, "publication_unknown")
    before = (tmp_path / "maintenance.json").read_bytes()
    with pytest.raises(PrivateJsonError, match="maintenance_journal_capacity"):
        journal.record(uuid4().hex, "backup.create", "a" * 64, "awaiting_owner")
    assert (tmp_path / "maintenance.json").read_bytes() == before


def test_bounded_journal_may_evict_only_settled_receipt(tmp_path):
    journal = MaintenanceJournal(tmp_path)
    settled = uuid4().hex
    journal.record(settled, "backup.create", "a" * 64, "complete")
    for _ in range(63):
        journal.record(uuid4().hex, "backup.create", "a" * 64, "publication_unknown")
    new = uuid4().hex
    journal.record(new, "backup.create", "a" * 64, "awaiting_owner")
    rows = journal.document.read()["operations"]
    assert len(rows) == 64
    assert settled not in {row["operation_id"] for row in rows}
    assert new in {row["operation_id"] for row in rows}
