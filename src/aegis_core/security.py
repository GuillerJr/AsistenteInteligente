from __future__ import annotations

import asyncio
from threading import RLock

from aegis_core.ipc.protocol import IpcRequest
from aegis_core.ipc.server import IpcHandlerResult, IpcMethodHandler
from aegis_core.tools.audit import AuditIntegrityError, HashChainAuditLog


class SecurityStateLatch:
    """Monotonic process-wide security state; compromise cannot be cleared in-process."""

    def __init__(self) -> None:
        self._lock = RLock()
        self._reason: str | None = None

    @property
    def compromised(self) -> bool:
        with self._lock:
            return self._reason is not None

    def compromise(self, reason: str) -> None:
        if not reason or len(reason) > 128:
            reason = "unspecified_security_failure"
        with self._lock:
            if self._reason is None:
                self._reason = reason


class AuditIntegrityIpcService:
    METHOD = "security.status"

    def __init__(
        self,
        audit_log: HashChainAuditLog,
        security_state: SecurityStateLatch | None = None,
    ) -> None:
        self._audit_log = audit_log
        self._security_state = security_state or SecurityStateLatch()

    def handlers(self) -> dict[str, IpcMethodHandler]:
        return {self.METHOD: self.handle}

    async def status_payload(self) -> dict[str, str]:
        if self._security_state.compromised:
            return {"state": "compromised"}
        try:
            await asyncio.to_thread(self._audit_log.verify)
        except (AuditIntegrityError, OSError):
            self._security_state.compromise("audit_integrity_failure")
            return {"state": "compromised"}
        return {"state": "intact"}

    async def handle(self, request: IpcRequest) -> IpcHandlerResult:
        if request.method != self.METHOD:
            return IpcHandlerResult(ok=False, error_code="method_not_found")
        if request.payload:
            return IpcHandlerResult(ok=False, error_code="invalid_payload")
        return IpcHandlerResult(ok=True, payload=await self.status_payload())
