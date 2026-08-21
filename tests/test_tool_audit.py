import json
import stat
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

import pytest

from aegis_core.contracts import (
    PolicyDecision,
    ToolAuthorization,
    ToolExecutionResult,
)
from aegis_core.tools.audit import AuditIntegrityError, HashChainAuditLog

REQUEST_ID = UUID("11111111-1111-4111-8111-111111111111")
FIXED_TIME = datetime(2026, 8, 18, 12, 0, tzinfo=UTC)


def _authorization() -> ToolAuthorization:
    return ToolAuthorization(
        call_id="call-1",
        tool_name="filesystem_read_text",
        call_digest="a" * 64,
        decision=PolicyDecision.ALLOW,
        reason_code="policy_allowed",
        normalized_arguments={"path": "secret.txt", "max_bytes": 128},
    )


def test_audit_log_chains_events_without_storing_tool_output(tmp_path: Path) -> None:
    path = tmp_path / "private" / "audit.jsonl"
    audit = HashChainAuditLog(path, clock=lambda: FIXED_TIME)
    audit.record_authorization(REQUEST_ID, _authorization())
    audit.record_execution(
        REQUEST_ID,
        ToolExecutionResult(
            call_id="call-1",
            tool_name="filesystem_read_text",
            success=True,
            output="sensitive-content",
            metadata={"bytes_read": 17},
        ),
    )

    records = audit.verify()

    assert [record.sequence for record in records] == [1, 2]
    assert records[1].previous_hash == records[0].record_hash
    assert records[1].data["output_bytes"] == 17
    assert "sensitive-content" not in path.read_text(encoding="utf-8")
    assert stat.S_IMODE(path.stat().st_mode) == 0o600


def test_audit_log_detects_record_tampering(tmp_path: Path) -> None:
    path = tmp_path / "audit.jsonl"
    audit = HashChainAuditLog(path, clock=lambda: FIXED_TIME)
    audit.record_authorization(REQUEST_ID, _authorization())
    original = path.read_text(encoding="utf-8")
    record = json.loads(path.read_text(encoding="utf-8"))
    record["data"]["decision"] = "deny"
    path.write_text(json.dumps(record) + "\n", encoding="utf-8")

    with pytest.raises(AuditIntegrityError, match="hash is invalid"):
        audit.verify()

    path.write_text(original, encoding="utf-8")
    with pytest.raises(AuditIntegrityError, match="trust was revoked"):
        audit.verify()


def test_audit_log_detects_deletion_after_observation(tmp_path: Path) -> None:
    path = tmp_path / "audit.jsonl"
    audit = HashChainAuditLog(path, clock=lambda: FIXED_TIME)
    audit.record_authorization(REQUEST_ID, _authorization())

    path.unlink()

    with pytest.raises(AuditIntegrityError, match="disappeared"):
        audit.verify()


def test_audit_log_detects_replacement_after_observation(tmp_path: Path) -> None:
    path = tmp_path / "audit.jsonl"
    audit = HashChainAuditLog(path, clock=lambda: FIXED_TIME)
    audit.record_authorization(REQUEST_ID, _authorization())
    original = path.read_bytes()

    path.unlink()
    path.write_bytes(original)
    path.chmod(0o600)

    with pytest.raises(AuditIntegrityError, match="identity changed"):
        audit.verify()


def test_audit_log_detects_truncation_after_observation(tmp_path: Path) -> None:
    path = tmp_path / "audit.jsonl"
    audit = HashChainAuditLog(path, clock=lambda: FIXED_TIME)
    audit.record_authorization(REQUEST_ID, _authorization())

    path.write_bytes(b"")

    with pytest.raises(AuditIntegrityError, match="rolled back"):
        audit.verify()


def test_audit_log_detects_valid_history_rewrite_after_observation(tmp_path: Path) -> None:
    path = tmp_path / "audit.jsonl"
    audit = HashChainAuditLog(path, clock=lambda: FIXED_TIME)
    audit.record_authorization(REQUEST_ID, _authorization())
    alternate_path = tmp_path / "alternate.jsonl"
    alternate = HashChainAuditLog(alternate_path, clock=lambda: FIXED_TIME)
    alternate.record_authorization(
        REQUEST_ID,
        _authorization().model_copy(update={"call_id": "call-2"}),
    )

    path.write_bytes(alternate_path.read_bytes())

    with pytest.raises(AuditIntegrityError, match="history changed"):
        audit.verify()


def test_audit_log_accepts_valid_growth_from_another_instance(tmp_path: Path) -> None:
    path = tmp_path / "audit.jsonl"
    observer = HashChainAuditLog(path, clock=lambda: FIXED_TIME)
    writer = HashChainAuditLog(path, clock=lambda: FIXED_TIME)
    observer.record_authorization(REQUEST_ID, _authorization())

    writer.record_authorization(
        REQUEST_ID,
        _authorization().model_copy(update={"call_id": "call-2"}),
    )

    assert len(observer.verify()) == 2


def test_audit_log_rejects_broad_file_permissions(tmp_path: Path) -> None:
    path = tmp_path / "audit.jsonl"
    path.touch()
    path.chmod(0o644)
    audit = HashChainAuditLog(path, clock=lambda: FIXED_TIME)

    with pytest.raises(AuditIntegrityError, match="permissions"):
        audit.record_authorization(REQUEST_ID, _authorization())


def test_audit_log_rejects_broad_parent_permissions(tmp_path: Path) -> None:
    parent = tmp_path / "audit"
    parent.mkdir(mode=0o700)
    parent.chmod(0o755)
    audit = HashChainAuditLog(parent / "audit.jsonl", clock=lambda: FIXED_TIME)

    with pytest.raises(AuditIntegrityError, match="directory must be owner-only"):
        audit.record_authorization(REQUEST_ID, _authorization())


def test_audit_log_rejects_symlinked_parent(tmp_path: Path) -> None:
    target = tmp_path / "target"
    target.mkdir(mode=0o700)
    parent = tmp_path / "audit"
    parent.symlink_to(target, target_is_directory=True)
    audit = HashChainAuditLog(parent / "audit.jsonl", clock=lambda: FIXED_TIME)

    with pytest.raises(AuditIntegrityError, match="directory must be owner-only"):
        audit.record_authorization(REQUEST_ID, _authorization())


def test_audit_verification_rechecks_parent_permissions(tmp_path: Path) -> None:
    parent = tmp_path / "audit"
    path = parent / "audit.jsonl"
    audit = HashChainAuditLog(path, clock=lambda: FIXED_TIME)
    audit.record_authorization(REQUEST_ID, _authorization())
    parent.chmod(0o755)

    with pytest.raises(AuditIntegrityError, match="directory must be owner-only"):
        audit.verify()


def test_authorization_audit_does_not_store_normalized_arguments(tmp_path: Path) -> None:
    path = tmp_path / "audit.jsonl"
    audit = HashChainAuditLog(path, clock=lambda: FIXED_TIME)
    authorization = _authorization().model_copy(
        update={"normalized_arguments": {"path": "credentials.txt", "max_bytes": 128}}
    )

    audit.record_authorization(REQUEST_ID, authorization)

    assert "credentials.txt" not in path.read_text(encoding="utf-8")


def test_audit_log_serializes_concurrent_writers(tmp_path: Path) -> None:
    audit = HashChainAuditLog(tmp_path / "audit.jsonl", clock=lambda: FIXED_TIME)

    def append(index: int) -> None:
        authorization = _authorization().model_copy(
            update={"call_id": f"call-{index}", "call_digest": f"{index:064x}"}
        )
        audit.record_authorization(REQUEST_ID, authorization)

    with ThreadPoolExecutor(max_workers=4) as pool:
        tuple(pool.map(append, range(12)))

    records = audit.verify()
    assert len(records) == 12
    assert [record.sequence for record in records] == list(range(1, 13))


def test_audit_log_rejects_append_before_exceeding_capacity(tmp_path: Path) -> None:
    path = tmp_path / "audit.jsonl"
    initial = HashChainAuditLog(path, clock=lambda: FIXED_TIME)
    initial.record_authorization(REQUEST_ID, _authorization())
    initial_content = path.read_bytes()
    bounded = HashChainAuditLog(
        path,
        clock=lambda: FIXED_TIME,
        max_bytes=len(initial_content),
    )

    with pytest.raises(AuditIntegrityError, match="capacity reached"):
        bounded.record_authorization(REQUEST_ID, _authorization())

    assert path.read_bytes() == initial_content
    assert len(bounded.verify()) == 1


def test_audit_log_rejects_oversized_existing_file(tmp_path: Path) -> None:
    path = tmp_path / "audit.jsonl"
    initial = HashChainAuditLog(path, clock=lambda: FIXED_TIME)
    initial.record_authorization(REQUEST_ID, _authorization())
    bounded = HashChainAuditLog(path, max_bytes=path.stat().st_size - 1)

    with pytest.raises(AuditIntegrityError, match="exceeds maximum size"):
        bounded.verify()


def test_audit_log_rejects_non_positive_capacity(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="must be positive"):
        HashChainAuditLog(tmp_path / "audit.jsonl", max_bytes=0)
