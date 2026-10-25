"""Independent memory receipt contract using own producer DTOs and private JSON."""
from dataclasses import replace

import pytest

from jarvis_core.maintenance_journal import MaintenanceJournal, _valid
from jarvis_core.memory import MemoryScopeCopyReceipt
from jarvis_core.memory_management import _receipt
from jarvis_core.private_json import PrivateJsonError

OP = "a" * 32
DIGEST = "b" * 64
CORE = "c" * 64
MAPPING = "d" * 64
PURPOSE = "memory.restore.copy_scope"


def producer(status="completed", *, operation_id=OP, cleanup=False):
    committed_or_unknown = status in {"completed", "unknown"}
    return MemoryScopeCopyReceipt(
        operation_id, DIGEST, CORE, status,
        "scope_copy_committed" if status == "completed" else
        "scope_copy_unknown" if status == "unknown" else "no_scope_copy_committed",
        2 if committed_or_unknown else 0, 40 if committed_or_unknown else 0,
        MAPPING if committed_or_unknown else None, 1 if committed_or_unknown else None,
        None if status == "completed" else "memory_copy_cancelled", cleanup,
    )


def row(receipt, *, phase="complete"):
    return {"operation_id": OP, "purpose": PURPOSE, "review_digest": DIGEST,
            "phase": phase, "receipt": receipt}


def validate(receipt):
    _valid({"version": 1, "operations": [row(receipt)]})


@pytest.mark.parametrize("status,cleanup", [
    ("completed", False), ("failed", False), ("cancelled", False),
    ("unknown", False), ("unknown", True),
])
def test_actual_producer_receipts_remain_exact_and_unknown_never_becomes_ack(
    tmp_path, status, cleanup,
):
    value = _receipt(producer(status, cleanup=cleanup))
    validate(value)
    phase = ("complete" if status == "completed" else "cleanup_pending" if cleanup else
             "publication_unknown" if status == "unknown" else status)
    journal = MaintenanceJournal(tmp_path)
    journal.record(OP, PURPOSE, DIGEST, phase, value)
    path = tmp_path / "maintenance.json"
    before = path.read_bytes()
    reopened = MaintenanceJournal(tmp_path)
    assert reopened.previous == (row(value, phase=phase),)
    assert path.read_bytes() == before


@pytest.mark.parametrize("change", [
    {"status": "unknown_status"}, {"effect_status": "no_scope_copy_committed"},
    {"error_code": "fixture_error"}, {"cleanup_pending": True},
    {"document_count": 0}, {"document_count": -1}, {"document_count": True},
    {"document_count": 1.0}, {"document_count": 100_001},
    {"byte_length": 0}, {"byte_length": 1}, {"byte_length": True},
    {"byte_length": 67_108_865}, {"mapping_sha256": None},
    {"mapping_sha256": "not_a_hash"}, {"destination_generation_after": None},
    {"destination_generation_after": 1}, {"destination_generation_after": "01"},
    {"destination_generation_after": "4503599627370496"},
    {"operation_id": "e" * 32}, {"request_digest": "e" * 64},
    {"core_review_digest": "file:///NEVER/OPEN"}, {"purpose": "restore.stage"},
    {"scope_key": '["jarvis","private",null]'}, {"source_uri": "file:///private"},
    {"text": "fixture body must never enter receipts"},
])
def test_completed_contradictions_and_unprojected_fields_never_replace_valid_journal(
    tmp_path, change,
):
    journal = MaintenanceJournal(tmp_path)
    good = _receipt(producer())
    journal.record(OP, PURPOSE, DIGEST, "complete", good)
    path = tmp_path / "maintenance.json"
    before = path.read_bytes()
    with pytest.raises(PrivateJsonError, match="maintenance_journal_invalid"):
        journal.record(OP, PURPOSE, DIGEST, "complete", {**good, **change})
    assert path.read_bytes() == before
    assert MaintenanceJournal(tmp_path).previous[0]["receipt"] == good


@pytest.mark.parametrize("status", ["failed", "cancelled"])
@pytest.mark.parametrize("change", [
    {"effect_status": "scope_copy_committed"}, {"document_count": 1},
    {"byte_length": 1}, {"mapping_sha256": MAPPING},
    {"destination_generation_after": "1"}, {"cleanup_pending": True},
])
def test_failed_or_cancelled_receipt_cannot_claim_data_or_cleanup(status, change):
    value = {**_receipt(producer(status)), **change}
    with pytest.raises(PrivateJsonError, match="maintenance_journal_invalid"):
        validate(value)


@pytest.mark.parametrize("effect", ["scope_copy_committed", "no_scope_copy_committed",
                                    "publication_unknown", "unknown"])
def test_unknown_can_only_report_scope_copy_unknown(effect):
    with pytest.raises(PrivateJsonError, match="maintenance_journal_invalid"):
        validate({**_receipt(producer("unknown")), "effect_status": effect})


@pytest.mark.parametrize("shape", [[], ["operation_id"], "receipt", True, False, 7, 1.25])
def test_receipt_shape_must_be_dict_and_null_is_only_absence(shape):
    with pytest.raises(PrivateJsonError, match="maintenance_journal_invalid"):
        validate(shape)
    _valid({"version": 1, "operations": [row(None, phase="awaiting_owner")]})


def test_missing_required_fields_rejects_instead_of_projecting_partial_body():
    for key in _receipt(producer()):
        receipt = _receipt(producer())
        del receipt[key]
        with pytest.raises(PrivateJsonError, match="maintenance_journal_invalid"):
            validate(receipt)


def test_journal_capacity_cannot_evict_unknown_cleanup_or_interrupted_work(tmp_path):
    journal = MaintenanceJournal(tmp_path)
    for index in range(64):
        identity = f"{index:032x}"
        if index % 3 == 0:
            receipt = _receipt(producer("unknown", operation_id=identity))
            phase = "publication_unknown"
        elif index % 3 == 1:
            receipt = _receipt(producer("unknown", operation_id=identity, cleanup=True))
            phase = "cleanup_pending"
        else:
            receipt, phase = None, "working"
        journal.record(identity, PURPOSE, DIGEST, phase, receipt)
    path = tmp_path / "maintenance.json"
    before = path.read_bytes()
    next_id = "f" * 32
    with pytest.raises(PrivateJsonError, match="maintenance_journal_capacity"):
        journal.record(next_id, PURPOSE, DIGEST, "awaiting_owner")
    assert path.read_bytes() == before
    reopened = MaintenanceJournal(tmp_path)
    assert len(reopened.previous) == 64
    assert all(item["operation_id"] != next_id for item in reopened.previous)
    assert {item["phase"] for item in reopened.previous} == {
        "publication_unknown", "cleanup_pending", "interrupted_unknown"}


def test_distinct_core_and_host_digests_are_preserved_without_equating_them():
    value = _receipt(replace(producer("unknown"), core_review_digest="e" * 64))
    validate(value)
    assert value["core_review_digest"] != value["request_digest"]
