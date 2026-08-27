from __future__ import annotations

import asyncio

from aegis_core.ipc.protocol import IpcRequest
from aegis_core.ipc.server import IpcHandlerResult, IpcMethodHandler
from aegis_core.tools.audit import AuditIntegrityError, HashChainAuditLog


class AuditIntegrityIpcService:
    METHOD = "security.status"

    def __init__(self, audit_log: HashChainAuditLog) -> None:
        self._audit_log = audit_log

    def handlers(self) -> dict[str, IpcMethodHandler]:
        return {self.METHOD: self.handle}

    async def status_payload(self) -> dict[str, str]:
        try:
            await asyncio.to_thread(self._audit_log.verify)
        except (AuditIntegrityError, OSError):
            return {"state": "compromised"}
        return {"state": "intact"}

    async def handle(self, request: IpcRequest) -> IpcHandlerResult:
        if request.method != self.METHOD:
            return IpcHandlerResult(ok=False, error_code="method_not_found")
        if request.payload:
            return IpcHandlerResult(ok=False, error_code="invalid_payload")
        return IpcHandlerResult(ok=True, payload=await self.status_payload())
