from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable

from aegis_core.failure_boundary import record_boundary_error
from aegis_core.ipc.protocol import IpcRequest
from aegis_core.ipc.server import IpcHandlerResult, IpcMethodHandler


class ProviderStatusIpcService:
    METHOD = "provider.status"

    def __init__(
        self,
        credential_probe: Callable[[], bool] | None = None,
        local_model_probe: Callable[[], bool] | None = None,
        *,
        status_probe: Callable[[], Awaitable[dict[str, str]]] | None = None,
    ) -> None:
        if credential_probe is None and status_probe is None:
            raise ValueError("provider status needs a probe")
        self._credential_probe = credential_probe
        self._local_model_probe = local_model_probe
        self._status_probe = status_probe

    def handlers(self) -> dict[str, IpcMethodHandler]:
        return {self.METHOD: self.handle}

    @staticmethod
    async def _probe(probe: Callable[[], bool]) -> bool | None:
        try:
            return await asyncio.to_thread(probe)
        except Exception as error:
            record_boundary_error(error)
            return None

    async def status_payload(self) -> dict[str, str]:
        if self._status_probe is not None:
            try:
                async with asyncio.timeout(6):
                    status = await self._status_probe()
                if (
                    status.get("provider") != "codex"
                    or status.get("credential") not in {"configured", "missing", "unavailable"}
                ):
                    raise ValueError("invalid selected provider status")
                return {"provider": "codex", "credential": status["credential"],
                        "voice_output": "unconfigured"}
            except Exception as error:
                record_boundary_error(error)
                return {"provider": "codex", "credential": "unavailable",
                        "voice_output": "unconfigured"}
        assert self._credential_probe is not None
        if self._local_model_probe is None:
            configured = await self._probe(self._credential_probe)
            local_available = None
        else:
            configured, local_available = await asyncio.gather(
                self._probe(self._credential_probe),
                self._probe(self._local_model_probe),
            )
        state = "unavailable" if configured is None else "configured" if configured else "missing"
        payload = {"provider": "nvidia_nim", "credential": state}
        if self._local_model_probe is not None:
            payload["local_model"] = "available" if local_available else "unavailable"
        return payload

    async def handle(self, request: IpcRequest) -> IpcHandlerResult:
        if request.method != self.METHOD:
            return IpcHandlerResult(ok=False, error_code="method_not_found")
        if request.payload:
            return IpcHandlerResult(ok=False, error_code="invalid_payload")
        payload = await self.status_payload()
        return IpcHandlerResult(ok=True, payload=payload)
