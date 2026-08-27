from __future__ import annotations

import asyncio

from aegis_core.ipc.protocol import IpcRequest
from aegis_core.ipc.server import IpcHandlerResult, IpcMethodHandler
from aegis_core.provider_status import ProviderStatusIpcService
from aegis_core.security import AuditIntegrityIpcService


class RuntimePreflightIpcService:
    METHOD = "runtime.preflight"

    def __init__(
        self,
        provider_status: ProviderStatusIpcService,
        audit_integrity: AuditIntegrityIpcService,
    ) -> None:
        self._provider_status = provider_status
        self._audit_integrity = audit_integrity

    def handlers(self) -> dict[str, IpcMethodHandler]:
        return {self.METHOD: self.handle}

    async def handle(self, request: IpcRequest) -> IpcHandlerResult:
        if request.method != self.METHOD:
            return IpcHandlerResult(ok=False, error_code="method_not_found")
        if request.payload:
            return IpcHandlerResult(ok=False, error_code="invalid_payload")
        provider, security = await asyncio.gather(
            self._provider_status.status_payload(),
            self._audit_integrity.status_payload(),
        )
        return IpcHandlerResult(
            ok=True,
            payload={"status": "ok", **provider, **security},
        )
