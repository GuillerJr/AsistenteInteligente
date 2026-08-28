from __future__ import annotations

import hashlib
from pathlib import Path
from uuid import UUID

import pytest

from aegis_core.audit_anchor import DurableAuditAnchor
from aegis_core.secrets import SecretNotFoundError
from aegis_core.tools.audit import AuditIntegrityError, HashChainAuditLog

REQUEST_ID = UUID("11111111-1111-4111-8111-111111111111")


class InMemoryAnchorStore:
    def __init__(self, anchor: str | None = None) -> None:
        self.anchor = anchor

    def get(self) -> str:
        if self.anchor is None:
            raise SecretNotFoundError("missing")
        return self.anchor

    def set(self, anchor: str) -> None:
        self.anchor = anchor


def _append(path: Path, *, event: str = "daemon_started") -> HashChainAuditLog:
    audit = HashChainAuditLog(path)
    audit.record_system_event(
        REQUEST_ID,
        event_type=event,
        component="supervisor",
        data={"state": "ok"},
    )
    return audit


def test_first_boot_without_log_or_anchor_is_allowed(tmp_path: Path) -> None:
    audit = HashChainAuditLog(tmp_path / "private" / "audit.jsonl")

    DurableAuditAnchor(InMemoryAnchorStore()).validate_startup(audit)

    assert audit.physical_log_present is False


def test_existing_log_without_keychain_anchor_is_fatal(tmp_path: Path) -> None:
    audit = _append(tmp_path / "private" / "audit.jsonl")

    with pytest.raises(AuditIntegrityError, match="anchor is missing"):
        DurableAuditAnchor(InMemoryAnchorStore()).validate_startup(audit)


def test_controlled_shutdown_seals_exact_physical_log_digest(tmp_path: Path) -> None:
    path = tmp_path / "private" / "audit.jsonl"
    audit = _append(path)
    store = InMemoryAnchorStore()
    anchor = DurableAuditAnchor(store)

    sealed = anchor.seal_shutdown(audit)

    assert sealed == hashlib.sha256(path.read_bytes()).hexdigest()
    assert store.anchor == sealed
    anchor.validate_startup(HashChainAuditLog(path))


def test_offline_valid_chain_growth_does_not_match_previous_boot_anchor(
    tmp_path: Path,
) -> None:
    path = tmp_path / "private" / "audit.jsonl"
    original = _append(path)
    store = InMemoryAnchorStore()
    anchor = DurableAuditAnchor(store)
    anchor.seal_shutdown(original)
    _append(path, event="offline_append")

    with pytest.raises(AuditIntegrityError, match="does not match durable anchor"):
        anchor.validate_startup(HashChainAuditLog(path))


def test_missing_anchored_log_is_fatal(tmp_path: Path) -> None:
    path = tmp_path / "private" / "audit.jsonl"
    audit = _append(path)
    store = InMemoryAnchorStore()
    anchor = DurableAuditAnchor(store)
    anchor.seal_shutdown(audit)
    path.unlink()

    with pytest.raises(AuditIntegrityError, match="anchored audit log is missing"):
        anchor.validate_startup(HashChainAuditLog(path))
