from __future__ import annotations

from aegis_core.ipc.protocol import IpcRequest
from aegis_core.ipc.server import IpcHandlerResult, IpcMethodHandler
from aegis_core.plugins.runtime import PluginRuntime


class PluginStatusIpcService:
    METHOD = "plugins.status"

    def __init__(self, runtime: PluginRuntime) -> None:
        self._runtime = runtime

    def handlers(self) -> dict[str, IpcMethodHandler]:
        return {self.METHOD: self.handle}

    async def handle(self, request: IpcRequest) -> IpcHandlerResult:
        if request.method != self.METHOD:
            return IpcHandlerResult(ok=False, error_code="method_not_found")
        if request.payload:
            return IpcHandlerResult(ok=False, error_code="invalid_payload")
        return IpcHandlerResult(ok=True, payload=self._runtime.status_payload())
