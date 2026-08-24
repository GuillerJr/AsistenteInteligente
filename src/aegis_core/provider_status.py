from __future__ import annotations

import asyncio
from collections.abc import Callable

from aegis_core.ipc.protocol import IpcRequest
from aegis_core.ipc.server import IpcHandlerResult, IpcMethodHandler


class ProviderStatusIpcService:
    METHOD = "provider.status"

    def __init__(self, credential_probe: Callable[[], bool]) -> None:
        self._credential_probe = credential_probe

    def handlers(self) -> dict[str, IpcMethodHandler]:
        return {self.METHOD: self.handle}

    async def handle(self, request: IpcRequest) -> IpcHandlerResult:
        if request.method != self.METHOD:
            return IpcHandlerResult(ok=False, error_code="method_not_found")
        if request.payload:
            return IpcHandlerResult(ok=False, error_code="invalid_payload")
        try:
            configured = await asyncio.to_thread(self._credential_probe)
        except Exception:
            state = "unavailable"
        else:
            state = "configured" if configured else "missing"
        return IpcHandlerResult(
            ok=True,
            payload={"provider": "nvidia_nim", "credential": state},
        )
