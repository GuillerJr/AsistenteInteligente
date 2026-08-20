from __future__ import annotations

import fcntl
import hashlib
import json
import os
import stat
from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Protocol
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

from aegis_core.contracts import ToolAuthorization, ToolExecutionResult


class AuditIntegrityError(RuntimeError):
    """Raised when the append-only audit chain cannot be trusted."""


class AuditRecord(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    sequence: int = Field(ge=1)
    timestamp: datetime
    event_type: str = Field(pattern=r"^[a-z][a-z0-9_]{2,63}$")
    request_id: UUID
    call_id: str = Field(min_length=1, max_length=256)
    tool_name: str = Field(pattern=r"^[a-z][a-z0-9_-]{2,63}$")
    data: dict[str, str | int | bool | None]
    previous_hash: str = Field(pattern=r"^(?:[0-9a-f]{64})?$")
    record_hash: str = Field(pattern=r"^[0-9a-f]{64}$")

    @field_validator("timestamp")
    @classmethod
    def timestamp_must_be_timezone_aware(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("audit timestamp must be timezone-aware")
        return value


class AuditSink(Protocol):
    def record_authorization(self, request_id: UUID, authorization: ToolAuthorization) -> None: ...

    def record_execution(self, request_id: UUID, result: ToolExecutionResult) -> None: ...


class NullAuditSink:
    def record_authorization(self, request_id: UUID, authorization: ToolAuthorization) -> None:
        del request_id, authorization

    def record_execution(self, request_id: UUID, result: ToolExecutionResult) -> None:
        del request_id, result


class HashChainAuditLog:
    DEFAULT_MAX_BYTES = 16 * 1_024 * 1_024

    def __init__(
        self,
        path: Path,
        *,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
        max_bytes: int = DEFAULT_MAX_BYTES,
    ) -> None:
        if max_bytes < 1:
            raise ValueError("audit max_bytes must be positive")
        self._path = path
        self._clock = clock
        self._max_bytes = max_bytes

    def record_authorization(self, request_id: UUID, authorization: ToolAuthorization) -> None:
        self._append(
            event_type="tool_authorization",
            request_id=request_id,
            call_id=authorization.call_id,
            tool_name=authorization.tool_name,
            data={
                "call_digest": authorization.call_digest,
                "decision": authorization.decision.value,
                "reason_code": authorization.reason_code,
            },
        )

    def record_execution(self, request_id: UUID, result: ToolExecutionResult) -> None:
        encoded_output = result.output.encode("utf-8")
        self._append(
            event_type="tool_execution",
            request_id=request_id,
            call_id=result.call_id,
            tool_name=result.tool_name,
            data={
                "success": result.success,
                "error_code": result.error_code,
                "output_bytes": len(encoded_output),
                "output_sha256": hashlib.sha256(encoded_output).hexdigest(),
            },
        )

    def verify(self) -> tuple[AuditRecord, ...]:
        flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
        try:
            descriptor = os.open(self._path, flags)
        except FileNotFoundError:
            return ()
        try:
            self._assert_secure_file(descriptor)
            with os.fdopen(descriptor, "r", encoding="utf-8", closefd=False) as handle:
                fcntl.flock(descriptor, fcntl.LOCK_SH)
                try:
                    self._assert_within_size_limit(descriptor)
                    return self._read_and_verify(handle.read())
                finally:
                    fcntl.flock(descriptor, fcntl.LOCK_UN)
        finally:
            os.close(descriptor)

    def _append(
        self,
        *,
        event_type: str,
        request_id: UUID,
        call_id: str,
        tool_name: str,
        data: Mapping[str, str | int | bool | None],
    ) -> None:
        self._prepare_parent()
        flags = (
            os.O_RDWR
            | os.O_CREAT
            | os.O_APPEND
            | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_NOFOLLOW", 0)
        )
        descriptor = os.open(self._path, flags, 0o600)
        try:
            self._assert_secure_file(descriptor)
            with os.fdopen(descriptor, "r+", encoding="utf-8", closefd=False) as handle:
                fcntl.flock(descriptor, fcntl.LOCK_EX)
                try:
                    self._assert_within_size_limit(descriptor)
                    handle.seek(0)
                    records = self._read_and_verify(handle.read())
                    previous_hash = records[-1].record_hash if records else ""
                    body = {
                        "sequence": len(records) + 1,
                        "timestamp": self._clock(),
                        "event_type": event_type,
                        "request_id": request_id,
                        "call_id": call_id,
                        "tool_name": tool_name,
                        "data": dict(data),
                        "previous_hash": previous_hash,
                    }
                    record_hash = self._hash_body(body)
                    record = AuditRecord.model_validate({**body, "record_hash": record_hash})
                    serialized_record = record.model_dump_json() + "\n"
                    current_size = os.fstat(descriptor).st_size
                    if current_size + len(serialized_record.encode("utf-8")) > self._max_bytes:
                        raise AuditIntegrityError("audit log capacity reached")
                    handle.seek(0, os.SEEK_END)
                    handle.write(serialized_record)
                    handle.flush()
                    os.fsync(descriptor)
                finally:
                    fcntl.flock(descriptor, fcntl.LOCK_UN)
        finally:
            os.close(descriptor)

    def _prepare_parent(self) -> None:
        parent = self._path.parent
        if parent.exists():
            if not parent.is_dir():
                raise AuditIntegrityError("audit parent is not a directory")
            return
        parent.mkdir(parents=True, mode=0o700)

    @staticmethod
    def _assert_secure_file(descriptor: int) -> None:
        status = os.fstat(descriptor)
        if not stat.S_ISREG(status.st_mode):
            raise AuditIntegrityError("audit log is not a regular file")
        if stat.S_IMODE(status.st_mode) & 0o077:
            raise AuditIntegrityError("audit log permissions are too broad")

    def _assert_within_size_limit(self, descriptor: int) -> None:
        if os.fstat(descriptor).st_size > self._max_bytes:
            raise AuditIntegrityError("audit log exceeds maximum size")

    @classmethod
    def _read_and_verify(cls, content: str) -> tuple[AuditRecord, ...]:
        records: list[AuditRecord] = []
        previous_hash = ""
        for expected_sequence, raw_line in enumerate(content.splitlines(), start=1):
            if not raw_line.strip():
                raise AuditIntegrityError("audit log contains an empty record")
            try:
                record = AuditRecord.model_validate_json(raw_line)
            except ValueError as error:
                raise AuditIntegrityError("audit log contains an invalid record") from error
            body = record.model_dump(exclude={"record_hash"}, mode="python")
            if record.sequence != expected_sequence:
                raise AuditIntegrityError("audit sequence is broken")
            if record.previous_hash != previous_hash:
                raise AuditIntegrityError("audit hash chain is broken")
            if record.record_hash != cls._hash_body(body):
                raise AuditIntegrityError("audit record hash is invalid")
            records.append(record)
            previous_hash = record.record_hash
        return tuple(records)

    @staticmethod
    def _hash_body(body: Mapping[str, object]) -> str:
        canonical = json.dumps(
            body,
            default=HashChainAuditLog._json_default,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    @staticmethod
    def _json_default(value: object) -> str:
        if isinstance(value, datetime):
            return value.isoformat()
        if isinstance(value, UUID):
            return str(value)
        raise TypeError(f"unsupported audit value: {type(value).__name__}")
