from __future__ import annotations

import re
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictInt, StrictStr

from aegis_core.ipc.protocol import IpcRequest
from aegis_core.ipc.server import IpcHandlerResult, IpcMethodHandler
from aegis_core.tools.audit import AuditSink

AuditScalar = Annotated[
    StrictStr | StrictInt | StrictBool | None,
    Field(union_mode="left_to_right"),
]


class SystemAuditEventPayload(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    event_type: Literal["thermal_pause", "thermal_resume", "voice_interruption"]
    component: Literal["acoustic_sensor"]
    data: dict[str, AuditScalar] = Field(default_factory=dict, max_length=16)


class SystemAuditIpcService:
    METHOD = "audit.system.event"

    def __init__(self, audit_sink: AuditSink) -> None:
        self._audit = audit_sink

    def handlers(self) -> dict[str, IpcMethodHandler]:
        return {self.METHOD: self.handle}

    async def handle(self, request: IpcRequest) -> IpcHandlerResult:
        if request.method != self.METHOD:
            return IpcHandlerResult(ok=False, error_code="method_not_found")
        try:
            event = SystemAuditEventPayload.model_validate(request.payload)
        except ValueError:
            return IpcHandlerResult(ok=False, error_code="invalid_payload")
        if any(not self._valid_data_item(key, value) for key, value in event.data.items()):
            return IpcHandlerResult(ok=False, error_code="invalid_payload")
        self._audit.record_system_event(
            request.request_id,
            event_type=event.event_type,
            component=event.component,
            data=event.data,
            call_id=request.nonce,
        )
        return IpcHandlerResult(ok=True, payload={"recorded": True})

    @staticmethod
    def _valid_data_item(key: str, value: AuditScalar) -> bool:
        if re.fullmatch(r"[a-z][a-z0-9_]{1,63}", key) is None:
            return False
        if isinstance(value, str):
            return len(value.encode("utf-8")) <= 256
        if isinstance(value, bool) or value is None:
            return True
        return -9_007_199_254_740_991 <= value <= 9_007_199_254_740_991
