from __future__ import annotations

import base64
import binascii
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from aegis_core.action_history import ActionHistoryConflict, ActionHistoryStore
from aegis_core.approval_signing import (
    ApprovalSignerError,
    PinnedApprovalSigner,
    action_history_deletion_digest,
)
from aegis_core.async_tasks import run_blocking_owned
from aegis_core.ipc.protocol import IpcRequest
from aegis_core.ipc.server import IpcHandlerResult, IpcMethodHandler


class ActionHistoryPagePayload(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    limit: int = Field(default=30, strict=True, ge=1, le=50)
    before_sequence: int | None = Field(default=None, strict=True, ge=1,
                                        le=9_007_199_254_740_991)
    revision: UUID | None = None
    conversation_id: UUID | None = None

    @model_validator(mode="after")
    def cursor_is_complete(self) -> ActionHistoryPagePayload:
        if (self.before_sequence is None) != (self.revision is None):
            raise ValueError("history cursor must contain revision and sequence")
        return self


class ActionHistoryDeletePayload(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    revision: UUID
    entry_id: UUID | None
    approval_nonce: str = Field(pattern=r"^[0-9a-f]{32}$")
    issued_at_milliseconds: int = Field(strict=True, ge=0)
    signature: str = Field(min_length=8, max_length=128)

    def decoded_signature(self) -> bytes:
        try:
            return base64.b64decode(self.signature, validate=True)
        except (binascii.Error, ValueError) as error:
            raise ValueError("invalid history signature encoding") from error


class ActionHistoryIpcService:
    """Read-only listing plus an owner-signed, snapshot-bound deletion method.

    The daemon's existing authenticated peer gate admits only Jarvis.app to these
    handlers. No endpoint can publish events or invent request context.
    """

    def __init__(self, store: ActionHistoryStore, *, approval_signer: PinnedApprovalSigner) -> None:
        self._store = store
        self._signer = approval_signer

    def handlers(self) -> dict[str, IpcMethodHandler]:
        return {"actions.history.list": self.handle, "actions.history.delete": self.handle}

    async def handle(self, request: IpcRequest) -> IpcHandlerResult:
        if request.method not in self.handlers():
            return IpcHandlerResult(ok=False, error_code="method_not_found")
        try:
            if request.method == "actions.history.list":
                payload = ActionHistoryPagePayload.model_validate(request.payload)
                result = await run_blocking_owned(self._store.page, **payload.model_dump())
            else:
                deletion = ActionHistoryDeletePayload.model_validate(request.payload)
                self._signer.verify(
                    method="actions.history.delete", job_id=deletion.revision,
                    call_digest=action_history_deletion_digest(deletion.entry_id),
                    approval_nonce=deletion.approval_nonce,
                    issued_at_milliseconds=deletion.issued_at_milliseconds,
                    signature=deletion.decoded_signature(),
                )
                result = await run_blocking_owned(
                    self._store.delete, revision=deletion.revision, entry_id=deletion.entry_id,
                )
        except ApprovalSignerError:
            return IpcHandlerResult(ok=False, error_code="action_history_signature_required")
        except ActionHistoryConflict:
            return IpcHandlerResult(ok=False, error_code="action_history_changed")
        except (ValueError, TypeError):
            return IpcHandlerResult(ok=False, error_code="action_history_invalid")
        except OSError:
            return IpcHandlerResult(ok=False, error_code="action_history_unavailable")
        return IpcHandlerResult(ok=True, payload=result)
