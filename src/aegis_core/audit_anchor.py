from __future__ import annotations

import hmac
from typing import Protocol

from aegis_core.secrets import SecretNotFoundError
from aegis_core.tools.audit import AuditIntegrityError, HashChainAuditLog


class AuditAnchorStore(Protocol):
    def get(self) -> str: ...

    def set(self, anchor: str) -> None: ...


class DurableAuditAnchor:
    """Bridges the audit chain across controlled daemon lifecycles."""

    def __init__(self, store: AuditAnchorStore) -> None:
        self._store = store

    def validate_startup(self, audit_log: HashChainAuditLog) -> None:
        try:
            expected = self._store.get()
        except SecretNotFoundError as error:
            if audit_log.physical_log_present:
                raise AuditIntegrityError(
                    "audit anchor is missing for an existing log"
                ) from error
            return
        if not audit_log.physical_log_present:
            raise AuditIntegrityError("anchored audit log is missing")
        actual = audit_log.physical_digest()
        if not hmac.compare_digest(actual, expected):
            raise AuditIntegrityError("audit log does not match durable anchor")

    def seal_shutdown(self, audit_log: HashChainAuditLog) -> str:
        digest = audit_log.physical_digest()
        self._store.set(digest)
        return digest
