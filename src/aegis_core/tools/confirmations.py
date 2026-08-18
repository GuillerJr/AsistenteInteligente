from __future__ import annotations

from datetime import UTC, datetime, timedelta
from enum import StrEnum
from threading import RLock
from typing import Protocol
from uuid import UUID

from aegis_core.contracts import (
    ConfirmationGrant,
    PolicyDecision,
    ToolAuthorization,
    ToolCall,
)


class ConfirmationError(ValueError):
    """Raised when an approval request violates confirmation policy."""


class ConfirmationStatus(StrEnum):
    CONSUMED = "consumed"
    MISSING = "missing"
    EXPIRED = "expired"
    REPLAYED = "replayed"
    REVOKED = "revoked"
    NOT_YET_VALID = "not_yet_valid"


class ConfirmationStore(Protocol):
    def consume(self, call_digest: str, *, now: datetime) -> ConfirmationStatus: ...


class OneTimeConfirmationStore:
    MIN_TTL = timedelta(seconds=1)
    MAX_TTL = timedelta(minutes=5)
    MAX_GRANTS = 4_096

    def __init__(self) -> None:
        self._grants: dict[str, ConfirmationGrant] = {}
        self._consumed: set[UUID] = set()
        self._revoked: set[UUID] = set()
        self._expired: set[UUID] = set()
        self._lock = RLock()

    def issue(
        self,
        call: ToolCall,
        authorization: ToolAuthorization,
        *,
        approved_by: str,
        ttl: timedelta = timedelta(minutes=2),
        now: datetime | None = None,
    ) -> ConfirmationGrant:
        issued_at = now or datetime.now(UTC)
        self._validate_time(issued_at)
        if not self.MIN_TTL <= ttl <= self.MAX_TTL:
            raise ConfirmationError("confirmation TTL is outside the allowed range")
        identity = approved_by.strip()
        if not identity:
            raise ConfirmationError("approver identity is required")
        if authorization.decision is not PolicyDecision.REQUIRE_CONFIRMATION:
            raise ConfirmationError("authorization is not pending confirmation")
        if (
            authorization.call_id != call.call_id
            or authorization.tool_name != call.tool_name
            or authorization.call_digest != call.digest()
        ):
            raise ConfirmationError("authorization does not match the exact tool call")

        grant = ConfirmationGrant(
            call_id=call.call_id,
            tool_name=call.tool_name,
            call_digest=call.digest(),
            approved_by=identity,
            issued_at=issued_at,
            expires_at=issued_at + ttl,
        )
        with self._lock:
            if grant.call_digest in self._grants:
                raise ConfirmationError("a confirmation already exists for this tool call")
            if len(self._grants) >= self.MAX_GRANTS:
                raise ConfirmationError("confirmation store capacity reached")
            self._grants[grant.call_digest] = grant
        return grant

    def consume(self, call_digest: str, *, now: datetime) -> ConfirmationStatus:
        self._validate_time(now)
        with self._lock:
            grant = self._grants.get(call_digest)
            if grant is None:
                return ConfirmationStatus.MISSING
            if grant.grant_id in self._revoked:
                return ConfirmationStatus.REVOKED
            if grant.grant_id in self._consumed:
                return ConfirmationStatus.REPLAYED
            if grant.grant_id in self._expired:
                return ConfirmationStatus.EXPIRED
            if now < grant.issued_at:
                return ConfirmationStatus.NOT_YET_VALID
            if grant.expires_at <= now:
                self._expired.add(grant.grant_id)
                return ConfirmationStatus.EXPIRED
            self._consumed.add(grant.grant_id)
            return ConfirmationStatus.CONSUMED

    def revoke(self, grant_id: UUID) -> bool:
        with self._lock:
            if grant_id in self._consumed:
                return False
            if not any(grant.grant_id == grant_id for grant in self._grants.values()):
                return False
            self._revoked.add(grant_id)
            return True

    @staticmethod
    def _validate_time(value: datetime) -> None:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ConfirmationError("confirmation time must be timezone-aware")
