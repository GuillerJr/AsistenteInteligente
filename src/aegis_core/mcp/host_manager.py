from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path

from aegis_core.ipc.protocol import IpcRequest
from aegis_core.ipc.server import IpcHandlerResult, IpcMethodHandler
from aegis_core.mcp.client import AsyncToolHandler, McpHost
from aegis_core.tools.audit import AuditSink
from aegis_core.tools.broker import ToolDefinition


class McpHostManager:
    """Admit reviewed stdio tools only in explicitly configured applications."""

    def __init__(self, host: McpHost) -> None:
        self._host = host

    @classmethod
    def from_file(cls, path: Path, *, audit_sink: AuditSink | None = None) -> McpHostManager:
        return cls(McpHost.from_file(path, audit_sink=audit_sink))

    async def start(self) -> None:
        await self._host.start()

    async def close(self) -> None:
        await self._host.close()

    def tool_definitions(self) -> tuple[ToolDefinition, ...]:
        return self._host.tool_definitions()

    def handlers(self) -> Mapping[str, AsyncToolHandler]:
        return self._host.handlers()

    def tool_names_for_application(self, active_bundle_identifier: str | None) -> frozenset[str]:
        return self._host.tool_names_for_application(active_bundle_identifier)

    def tool_names_for_request(self, text: str, bundle_id: str | None) -> frozenset[str]:
        return self._host.tool_names_for_request(text, bundle_id)

    def ipc_handlers(self) -> dict[str, IpcMethodHandler]:
        return {"mcp.status": self.handle_status}

    async def handle_status(self, request: IpcRequest) -> IpcHandlerResult:
        if request.method != "mcp.status":
            return IpcHandlerResult(ok=False, error_code="method_not_found")
        if request.payload:
            return IpcHandlerResult(ok=False, error_code="invalid_payload")
        return IpcHandlerResult(ok=True, payload=self._host.status())
