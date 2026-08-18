from __future__ import annotations

import hashlib
import hmac
import json
import secrets
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from threading import RLock
from typing import Any, Literal
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

PROTOCOL_VERSION = "1.0"


class ProtocolError(RuntimeError):
    """Raised when an IPC frame fails validation or authentication."""


class FreshnessStatus(StrEnum):
    ACCEPTED = "accepted"
    STALE = "stale"
    REPLAYED = "replayed"
    CAPACITY_REACHED = "capacity_reached"


class IpcRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    protocol_version: Literal["1.0"] = PROTOCOL_VERSION
    request_id: UUID = Field(default_factory=uuid4)
    method: str = Field(pattern=r"^[a-z][a-z0-9_.-]{1,63}$")
    timestamp: datetime
    nonce: str = Field(pattern=r"^[0-9a-f]{32}$")
    payload: dict[str, Any] = Field(default_factory=dict)
    auth_tag: str = Field(pattern=r"^[0-9a-f]{64}$")

    @field_validator("timestamp")
    @classmethod
    def timestamp_must_be_aware(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("IPC timestamp must be timezone-aware")
        return value

    @field_validator("payload")
    @classmethod
    def payload_must_be_json(cls, value: dict[str, Any]) -> dict[str, Any]:
        try:
            json.dumps(value, allow_nan=False)
        except (TypeError, ValueError) as error:
            raise ValueError("IPC payload must be JSON serializable") from error
        return value


class IpcResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    protocol_version: Literal["1.0"] = PROTOCOL_VERSION
    request_id: UUID
    request_nonce: str = Field(pattern=r"^[0-9a-f]{32}$")
    timestamp: datetime
    ok: bool
    payload: dict[str, Any] = Field(default_factory=dict)
    error_code: str | None = Field(default=None, pattern=r"^[a-z][a-z0-9_]{2,63}$")
    auth_tag: str = Field(pattern=r"^[0-9a-f]{64}$")

    @field_validator("timestamp")
    @classmethod
    def timestamp_must_be_aware(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("IPC timestamp must be timezone-aware")
        return value

    @field_validator("payload")
    @classmethod
    def payload_must_be_json(cls, value: dict[str, Any]) -> dict[str, Any]:
        try:
            json.dumps(value, allow_nan=False)
        except (TypeError, ValueError) as error:
            raise ValueError("IPC payload must be JSON serializable") from error
        return value

    @model_validator(mode="after")
    def error_must_match_status(self) -> IpcResponse:
        if self.ok and self.error_code is not None:
            raise ValueError("successful IPC response cannot contain an error")
        if not self.ok and self.error_code is None:
            raise ValueError("failed IPC response requires an error code")
        return self


class IpcAuthenticator:
    def __init__(self, secret: bytes) -> None:
        if len(secret) != 32:
            raise ValueError("IPC authentication secret must contain exactly 32 bytes")
        self._secret = secret

    @classmethod
    def from_hex(cls, secret: str) -> IpcAuthenticator:
        if len(secret) != 64 or any(char not in "0123456789abcdef" for char in secret):
            raise ValueError("IPC authentication secret must be lowercase 32-byte hex")
        try:
            decoded = bytes.fromhex(secret)
        except ValueError as error:
            raise ValueError("IPC authentication secret is not hexadecimal") from error
        return cls(decoded)

    def create_request(
        self,
        method: str,
        payload: dict[str, Any] | None = None,
        *,
        now: datetime | None = None,
        nonce: str | None = None,
    ) -> IpcRequest:
        body = {
            "protocol_version": PROTOCOL_VERSION,
            "request_id": uuid4(),
            "method": method,
            "timestamp": now or datetime.now(UTC),
            "nonce": nonce or secrets.token_hex(16),
            "payload": payload or {},
        }
        return IpcRequest.model_validate({**body, "auth_tag": self._sign(body)})

    def verify_request(self, request: IpcRequest) -> bool:
        return hmac.compare_digest(request.auth_tag, self._sign(self._body(request)))

    def create_response(
        self,
        request: IpcRequest,
        *,
        ok: bool,
        payload: dict[str, Any] | None = None,
        error_code: str | None = None,
        now: datetime | None = None,
    ) -> IpcResponse:
        body = {
            "protocol_version": PROTOCOL_VERSION,
            "request_id": request.request_id,
            "request_nonce": request.nonce,
            "timestamp": now or datetime.now(UTC),
            "ok": ok,
            "payload": payload or {},
            "error_code": error_code,
        }
        return IpcResponse.model_validate({**body, "auth_tag": self._sign(body)})

    def verify_response(self, response: IpcResponse) -> bool:
        return hmac.compare_digest(response.auth_tag, self._sign(self._body(response)))

    def _sign(self, body: dict[str, Any]) -> str:
        return hmac.new(
            self._secret,
            self._canonical(body),
            digestmod=hashlib.sha256,
        ).hexdigest()

    @staticmethod
    def _body(message: IpcRequest | IpcResponse) -> dict[str, Any]:
        return message.model_dump(exclude={"auth_tag"}, mode="python")

    @staticmethod
    def _canonical(body: dict[str, Any]) -> bytes:
        return json.dumps(
            body,
            allow_nan=False,
            default=lambda value: value.isoformat() if isinstance(value, datetime) else str(value),
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")


class NonceWindow:
    def __init__(self, *, clock_skew: timedelta, max_entries: int = 4_096) -> None:
        if clock_skew.total_seconds() <= 0:
            raise ValueError("clock skew must be positive")
        if max_entries < 1:
            raise ValueError("nonce window capacity must be positive")
        self._clock_skew = clock_skew
        self._max_entries = max_entries
        self._nonces: dict[str, datetime] = {}
        self._lock = RLock()

    def accept(self, request: IpcRequest, *, now: datetime | None = None) -> FreshnessStatus:
        current = now or datetime.now(UTC)
        if abs(current - request.timestamp) >= self._clock_skew:
            return FreshnessStatus.STALE
        with self._lock:
            self._nonces = {
                nonce: expiry for nonce, expiry in self._nonces.items() if expiry > current
            }
            if request.nonce in self._nonces:
                return FreshnessStatus.REPLAYED
            if len(self._nonces) >= self._max_entries:
                return FreshnessStatus.CAPACITY_REACHED
            self._nonces[request.nonce] = current + (self._clock_skew * 2)
            return FreshnessStatus.ACCEPTED
