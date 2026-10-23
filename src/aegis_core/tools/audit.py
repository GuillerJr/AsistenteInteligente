from __future__ import annotations

import fcntl
import hashlib
import json
import os
import re
import stat
import threading
from collections.abc import Callable, Iterator, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, ValidationError, field_validator

from aegis_core.contracts import ToolAuthorization, ToolExecutionResult
from aegis_core.failure_boundary import record_boundary_error
from aegis_core.tools.computer import ComputerActionSummary, ComputerUseReport

_COMPUTER_DIAGNOSTIC_ADAPTERS = {
    name: TypeAdapter(ComputerUseReport.model_fields[name].annotation)
    for name in ("status", "reason_code", "failed_check")
}
_COMPUTER_DIAGNOSTIC_ADAPTERS["last_action_type"] = TypeAdapter(
    ComputerActionSummary.model_fields["action"].annotation
)


def _computer_execution_diagnostics(result: ToolExecutionResult) -> dict[str, str | int | None]:
    if (
        result.tool_name != "computer_use"
        or result.metadata.get("source") != "native_computer_control"
    ):
        return {}
    diagnostics: dict[str, str | int | None] = {}
    for name, adapter in _COMPUTER_DIAGNOSTIC_ADAPTERS.items():
        value = result.metadata.get(name)
        if value is None and name in {"failed_check", "last_action_type"}:
            diagnostics[f"computer_{name}"] = None
        elif type(value) is str:
            try:
                diagnostics[f"computer_{name}"] = adapter.validate_python(value, strict=True)
            except ValidationError:
                pass  # Unrecognized text must never enter the content-free audit.
    steps = result.metadata.get("steps")
    if type(steps) is int and 0 <= steps <= 12:
        diagnostics["computer_steps"] = steps
    return diagnostics


class AuditIntegrityError(RuntimeError):
    """Raised when the append-only audit chain cannot be trusted."""

    def __init__(self, message: str, *, revokes_trust: bool = True) -> None:
        super().__init__(message)
        self.revokes_trust = revokes_trust


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

    def record_system_event(
        self,
        request_id: UUID,
        *,
        event_type: str,
        component: str,
        data: Mapping[str, str | int | bool | None],
        call_id: str | None = None,
    ) -> None: ...


class NullAuditSink:
    def record_authorization(self, request_id: UUID, authorization: ToolAuthorization) -> None:
        del request_id, authorization

    def record_execution(self, request_id: UUID, result: ToolExecutionResult) -> None:
        del request_id, result

    def record_system_event(
        self,
        request_id: UUID,
        *,
        event_type: str,
        component: str,
        data: Mapping[str, str | int | bool | None],
        call_id: str | None = None,
    ) -> None:
        del request_id, event_type, component, data, call_id


SEGMENT_STARTED_EVENT = "audit_segment_started"
_EMPTY_SHA256 = hashlib.sha256(b"").hexdigest()
_MAX_ARCHIVE_BYTES = 256 * 1_024 * 1_024


_SCAN_CHUNK_BYTES = 65_536


@dataclass(slots=True)
class _SegmentScan:
    """One streaming pass over a segment: chain summary plus exact byte digests."""

    count: int
    first: AuditRecord | None
    last: AuditRecord | None
    size: int
    sha256: str
    hasher: Any
    prefix_sha256: str | None
    records: tuple[AuditRecord, ...]


class HashChainAuditLog:
    """Append-only hash chain split into sealed, linked segments.

    ``max_bytes`` bounds the active segment (``path``). When the next record would
    not fit, the active file is hard-linked into ``<stem>.archive/`` as a read-only
    segment and replaced atomically by a new segment whose first record continues
    the sequence and hash chain and commits to the archived file's exact SHA-256.
    The newest ``archived_segments`` archives are retained; ``0`` disables rotation.
    """

    DEFAULT_MAX_BYTES = 16 * 1_024 * 1_024
    DEFAULT_ARCHIVED_SEGMENTS = 16

    def __init__(
        self,
        path: Path,
        *,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
        max_bytes: int = DEFAULT_MAX_BYTES,
        archived_segments: int = DEFAULT_ARCHIVED_SEGMENTS,
        activity_observer: AuditSink | None = None,
    ) -> None:
        if max_bytes < 1:
            raise ValueError("audit max_bytes must be positive")
        if archived_segments < 0:
            raise ValueError("audit archived_segments cannot be negative")
        self._path = path
        self._clock = clock
        self._max_bytes = max_bytes
        self._archived_segments = archived_segments
        self._archive_directory = path.parent / f"{path.stem}.archive"
        self._archive_pattern = re.compile(rf"^{re.escape(path.stem)}-(\d{{6}})\.jsonl$")
        self._expected_uid = os.getuid()
        self._file_identity: tuple[int, int] | None = None
        self._anchor_initialized = False
        self._anchored_count = 0
        self._anchored_head = ""
        self._anchored_sequence = 0
        self._anchored_size = 0
        self._anchored_sha256 = _EMPTY_SHA256
        self._anchored_hasher = hashlib.sha256()
        self._trust_revoked = False
        self._session_lock = threading.RLock()
        self._activity_observer = activity_observer

    @property
    def path(self) -> Path:
        return self._path

    @property
    def archive_directory(self) -> Path:
        return self._archive_directory

    @property
    def physical_log_present(self) -> bool:
        try:
            self._path.lstat()
        except FileNotFoundError:
            return False
        return True

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
        self._notify_activity("record_authorization", request_id, authorization)

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
                **_computer_execution_diagnostics(result),
            },
        )
        self._notify_activity("record_execution", request_id, result)

    def _notify_activity(self, method: str, request_id: UUID,
                         value: ToolAuthorization | ToolExecutionResult) -> None:
        if self._activity_observer is None:
            return
        try:
            getattr(self._activity_observer, method)(request_id, value)
        except Exception as error:  # Optional activity projection cannot affect the security audit.
            record_boundary_error(error)

    def record_system_event(
        self,
        request_id: UUID,
        *,
        event_type: str,
        component: str,
        data: Mapping[str, str | int | bool | None],
        call_id: str | None = None,
    ) -> None:
        self._append(
            event_type=event_type,
            request_id=request_id,
            call_id=call_id or str(request_id),
            tool_name=component,
            data=data,
        )

    def verify(self) -> tuple[AuditRecord, ...]:
        """Fully re-verify the active segment and its link to the newest archive."""
        with self._session_lock:
            self._assert_session_trusted()
            try:
                return self._verify()
            except AuditIntegrityError as error:
                if error.revokes_trust:
                    self._trust_revoked = True
                raise
            except OSError:
                self._trust_revoked = True
                raise

    def verify_integrity(self) -> int:
        """Authenticate the active segment and its link without retaining records.

        Returns the number of records in the active segment.
        """
        with self._session_lock:
            self._assert_session_trusted()
            try:
                return self._scan_active(retain=False).count
            except AuditIntegrityError as error:
                if error.revokes_trust:
                    self._trust_revoked = True
                raise
            except OSError:
                self._trust_revoked = True
                raise

    def verify_chain(self) -> int:
        """Verify every retained archive, their links and the active segment.

        The oldest retained segment is the trust root once older ones were pruned.
        Returns the number of records verified across all segments.
        """
        with self._session_lock:
            self._assert_session_trusted()
            try:
                return self._verify_chain()
            except AuditIntegrityError as error:
                if error.revokes_trust:
                    self._trust_revoked = True
                raise
            except OSError:
                self._trust_revoked = True
                raise

    def physical_digest(self) -> str:
        """Authenticate the chain and hash the exact on-disk JSONL bytes atomically."""
        with self._session_lock:
            self._assert_session_trusted()
            try:
                return self._physical_digest()
            except AuditIntegrityError as error:
                if error.revokes_trust:
                    self._trust_revoked = True
                raise
            except OSError:
                self._trust_revoked = True
                raise

    def _physical_digest(self) -> str:
        if not self._assert_private_parent(create=False):
            raise AuditIntegrityError("audit log is missing")
        try:
            descriptor = os.open(self._path, self._read_flags())
        except FileNotFoundError as error:
            raise AuditIntegrityError("audit log is missing") from error
        try:
            return self._scan_open_active(descriptor, retain=False).sha256
        finally:
            os.close(descriptor)

    def _verify(self) -> tuple[AuditRecord, ...]:
        return self._scan_active(retain=True).records

    def _scan_active(self, *, retain: bool) -> _SegmentScan:
        empty = _SegmentScan(0, None, None, 0, _EMPTY_SHA256, None, None, ())
        if not self._assert_private_parent(create=False):
            self._assert_not_disappeared()
            return empty
        try:
            descriptor = os.open(self._path, self._read_flags())
        except FileNotFoundError:
            self._assert_not_disappeared()
            return empty
        try:
            return self._scan_open_active(descriptor, retain=retain)
        finally:
            os.close(descriptor)

    def _scan_open_active(self, descriptor: int, *, retain: bool) -> _SegmentScan:
        self._assert_secure_file(descriptor)
        fcntl.flock(descriptor, fcntl.LOCK_SH)
        try:
            self._assert_same_file(descriptor)
            scan = self._scan(
                descriptor,
                verify=True,
                retain=retain,
                prefix_size=self._anchored_size if self._anchor_initialized else None,
            )
            self._verify_segment_link(scan.first, descriptor)
            self._assert_session_anchor(scan)
            return scan
        finally:
            fcntl.flock(descriptor, fcntl.LOCK_UN)

    def _verify_chain(self) -> int:
        total = self._scan_active(retain=False).count
        archives = self._archives(exclude_identity=self._file_identity)
        predecessor: tuple[str, str, AuditRecord] | None = None
        previous_index: int | None = None
        for index, archive in archives:
            if previous_index is not None and index != previous_index + 1:
                raise AuditIntegrityError("audit archive sequence has a gap")
            scan = self._scan_archive(archive)
            if scan.first is None or scan.last is None:
                raise AuditIntegrityError("audit archive is empty")
            if predecessor is not None:
                self._assert_link(scan.first, *predecessor)
            predecessor = (archive.name, scan.sha256, scan.last)
            previous_index = index
            total += scan.count
        return total

    def _append(
        self,
        *,
        event_type: str,
        request_id: UUID,
        call_id: str,
        tool_name: str,
        data: Mapping[str, str | int | bool | None],
    ) -> None:
        with self._session_lock:
            self._assert_session_trusted()
            try:
                self._append_unchecked(
                    event_type=event_type,
                    request_id=request_id,
                    call_id=call_id,
                    tool_name=tool_name,
                    data=data,
                )
            except AuditIntegrityError as error:
                if error.revokes_trust:
                    self._trust_revoked = True
                raise
            except OSError:
                self._trust_revoked = True
                raise

    def _append_unchecked(
        self,
        *,
        event_type: str,
        request_id: UUID,
        call_id: str,
        tool_name: str,
        data: Mapping[str, str | int | bool | None],
    ) -> None:
        self._assert_private_parent(create=True)
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
            fcntl.flock(descriptor, fcntl.LOCK_EX)
            try:
                self._assert_same_file(descriptor)
                if self._anchor_initialized:
                    # The writer already verified this segment; hashing the exact
                    # anchored bytes detects any change without re-parsing the log.
                    scan = self._scan(descriptor, verify=False, prefix_size=self._anchored_size)
                else:
                    scan = self._scan(descriptor, verify=True)
                    self._verify_segment_link(scan.first, descriptor)
                self._assert_session_anchor(scan)
                fields = {
                    "event_type": event_type,
                    "request_id": request_id,
                    "call_id": call_id,
                    "tool_name": tool_name,
                    "data": dict(data),
                }
                record = self._new_record(
                    sequence=self._anchored_sequence + 1,
                    previous_hash=self._anchored_head,
                    fields=fields,
                )
                serialized = self._serialize(record)
                if self._anchored_size + len(serialized) > self._max_bytes:
                    if self._archived_segments == 0 or self._anchored_size == 0:
                        raise AuditIntegrityError(
                            "audit log capacity reached",
                            revokes_trust=False,
                        )
                    self._rotate(descriptor, fields)
                    return
                os.write(descriptor, serialized)
                os.fsync(descriptor)
                self._advance_session_anchor(serialized, record)
            finally:
                fcntl.flock(descriptor, fcntl.LOCK_UN)
        finally:
            os.close(descriptor)

    def _rotate(
        self,
        descriptor: int,
        fields: Mapping[str, object],
    ) -> None:
        """Seal the full active segment and atomically start a linked successor."""

        self._assert_private_archive_directory(create=True)
        self._discard_interrupted_rotation(descriptor)
        archives = self._archives(exclude_identity=None)
        index = archives[-1][0] + 1 if archives else 1
        if index > 999_999:
            raise AuditIntegrityError("audit archive index space exhausted", revokes_trust=False)
        archive = self._archive_directory / f"{self._path.stem}-{index:06d}.jsonl"
        start = self._new_record(
            sequence=self._anchored_sequence + 1,
            previous_hash=self._anchored_head,
            fields={
                "event_type": SEGMENT_STARTED_EVENT,
                "request_id": uuid4(),
                "call_id": f"segment-{index:06d}",
                "tool_name": "audit_log",
                "data": {
                    "previous_segment": archive.name,
                    "previous_segment_sha256": self._anchored_sha256,
                    "previous_segment_records": self._anchored_count,
                },
            },
        )
        record = self._new_record(
            sequence=start.sequence + 1,
            previous_hash=start.record_hash,
            fields=fields,
        )
        successor = self._serialize(start) + self._serialize(record)
        if len(successor) > self._max_bytes:
            raise AuditIntegrityError("audit log capacity reached", revokes_trust=False)
        temporary = self._path.parent / f".{self._path.name}.{os.getpid()}.rotating"
        temporary.unlink(missing_ok=True)
        # The archive shares the active inode, so no byte of the sealed segment is
        # copied or lost; a crash before the rename leaves a link that is discarded.
        os.link(self._path, archive)
        os.chmod(archive, 0o400, follow_symlinks=False)
        self._fsync_directory(self._archive_directory)
        new_descriptor = os.open(
            temporary,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_CLOEXEC", 0),
            0o600,
        )
        try:
            os.write(new_descriptor, successor)
            os.fsync(new_descriptor)
            os.rename(temporary, self._path)
            self._fsync_directory(self._path.parent)
            status = os.fstat(new_descriptor)
        except BaseException:
            temporary.unlink(missing_ok=True)
            raise
        finally:
            os.close(new_descriptor)
        self._file_identity = (status.st_dev, status.st_ino)
        self._anchor_initialized = True
        self._anchored_count = 0
        self._anchored_size = 0
        self._anchored_hasher = hashlib.sha256()
        self._advance_session_anchor(self._serialize(start), start)
        self._advance_session_anchor(self._serialize(record), record)
        self._prune_archives()

    def _verify_segment_link(self, first: AuditRecord | None, descriptor: int) -> None:
        if first is None or first.sequence == 1:
            return
        status = os.fstat(descriptor)
        archives = self._archives(exclude_identity=(status.st_dev, status.st_ino))
        if not archives:
            raise AuditIntegrityError("audit segment predecessor is missing")
        _, archive = archives[-1]
        previous = self._scan_archive(archive)
        if previous.last is None:
            raise AuditIntegrityError("audit archive is empty")
        self._assert_link(first, archive.name, previous.sha256, previous.last)

    @staticmethod
    def _assert_link(
        start: AuditRecord,
        archive_name: str,
        archive_sha256: str,
        last: AuditRecord,
    ) -> None:
        if (
            start.event_type != SEGMENT_STARTED_EVENT
            or start.data.get("previous_segment") != archive_name
            or start.data.get("previous_segment_sha256") != archive_sha256
            or start.sequence != last.sequence + 1
            or start.previous_hash != last.record_hash
        ):
            raise AuditIntegrityError("audit segment link is broken")

    def _archives(
        self,
        *,
        exclude_identity: tuple[int, int] | None,
    ) -> list[tuple[int, Path]]:
        if not self._assert_private_archive_directory(create=False):
            return []
        found: list[tuple[int, Path]] = []
        for entry in os.scandir(self._archive_directory):
            match = self._archive_pattern.fullmatch(entry.name)
            if match is None:
                continue
            status = entry.stat(follow_symlinks=False)
            if exclude_identity is not None and (status.st_dev, status.st_ino) == exclude_identity:
                continue
            found.append((int(match.group(1)), Path(entry.path)))
        return sorted(found)

    def _discard_interrupted_rotation(self, descriptor: int) -> None:
        """Remove a link to the active inode left by a rotation that crashed mid-way."""
        status = os.fstat(descriptor)
        identity = (status.st_dev, status.st_ino)
        for _, archive in self._archives(exclude_identity=None):
            archived = archive.lstat()
            if (archived.st_dev, archived.st_ino) == identity:
                archive.unlink()

    def _prune_archives(self) -> None:
        archives = self._archives(exclude_identity=None)
        for _, archive in archives[: max(0, len(archives) - self._archived_segments)]:
            archive.unlink()
        self._fsync_directory(self._archive_directory)

    def _scan_archive(self, archive: Path) -> _SegmentScan:
        descriptor = os.open(archive, self._read_flags())
        try:
            status = os.fstat(descriptor)
            if (
                not stat.S_ISREG(status.st_mode)
                or status.st_uid != self._expected_uid
                or stat.S_IMODE(status.st_mode) & 0o077
            ):
                raise AuditIntegrityError("audit archive is not private")
            # Sealed segments keep the size limit that applied when they were written.
            return self._scan(descriptor, verify=True, limit=_MAX_ARCHIVE_BYTES)
        finally:
            os.close(descriptor)

    def _new_record(
        self,
        *,
        sequence: int,
        previous_hash: str,
        fields: Mapping[str, object],
    ) -> AuditRecord:
        body = {
            "sequence": sequence,
            "timestamp": self._clock(),
            **fields,
            "previous_hash": previous_hash,
        }
        return AuditRecord.model_validate({**body, "record_hash": self._hash_body(body)})

    @staticmethod
    def _serialize(record: AuditRecord) -> bytes:
        return (record.model_dump_json() + "\n").encode("utf-8")

    def _scan(
        self,
        descriptor: int,
        *,
        verify: bool,
        retain: bool = False,
        limit: int | None = None,
        prefix_size: int | None = None,
    ) -> _SegmentScan:
        """Stream a segment once, verifying its chain and hashing its exact bytes."""

        size = os.fstat(descriptor).st_size
        if size > (self._max_bytes if limit is None else limit):
            raise AuditIntegrityError("audit log exceeds maximum size")
        hasher = hashlib.sha256()
        count = 0
        first: AuditRecord | None = None
        last: AuditRecord | None = None
        retained: list[AuditRecord] = []
        if verify:
            for record in self._verified_records(self._lines(descriptor, size, hasher)):
                count += 1
                first = first or record
                last = record
                if retain:
                    retained.append(record)
        else:
            for chunk in self._chunks(descriptor, size):
                hasher.update(chunk)
        prefix_sha256: str | None = None
        if prefix_size is not None:
            if size == prefix_size:
                prefix_sha256 = hasher.hexdigest()
            elif size > prefix_size:
                prefix_sha256 = self._prefix_digest(descriptor, prefix_size)
        return _SegmentScan(
            count=count,
            first=first,
            last=last,
            size=size,
            sha256=hasher.hexdigest(),
            hasher=hasher,
            prefix_sha256=prefix_sha256,
            records=tuple(retained),
        )

    @staticmethod
    def _chunks(descriptor: int, size: int) -> Iterator[bytes]:
        offset = 0
        while offset < size:
            chunk = os.pread(descriptor, min(_SCAN_CHUNK_BYTES, size - offset), offset)
            if not chunk:
                break
            offset += len(chunk)
            yield chunk

    @classmethod
    def _lines(cls, descriptor: int, size: int, hasher: Any) -> Iterator[bytes]:
        pending = b""
        for chunk in cls._chunks(descriptor, size):
            hasher.update(chunk)
            pending += chunk
            *lines, pending = pending.split(b"\n")
            yield from lines
        if pending:
            yield pending

    def _prefix_digest(self, descriptor: int, prefix_size: int) -> str:
        hasher = hashlib.sha256()
        for chunk in self._chunks(descriptor, prefix_size):
            hasher.update(chunk)
        return hasher.hexdigest()

    @staticmethod
    def _read_flags() -> int:
        return os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)

    @staticmethod
    def _fsync_directory(directory: Path) -> None:
        descriptor = os.open(directory, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)

    def _assert_private_parent(self, *, create: bool) -> bool:
        return self._assert_private_directory(self._path.parent, create=create)

    def _assert_private_archive_directory(self, *, create: bool) -> bool:
        return self._assert_private_directory(self._archive_directory, create=create)

    def _assert_private_directory(self, directory: Path, *, create: bool) -> bool:
        try:
            status = directory.lstat()
        except FileNotFoundError:
            if not create:
                return False
            directory.mkdir(parents=True, mode=0o700)
            status = directory.lstat()
        if (
            not stat.S_ISDIR(status.st_mode)
            or status.st_uid != self._expected_uid
            or stat.S_IMODE(status.st_mode) & 0o077
        ):
            raise AuditIntegrityError("audit directory must be owner-only")
        return True

    def _assert_secure_file(self, descriptor: int) -> None:
        status = os.fstat(descriptor)
        if not stat.S_ISREG(status.st_mode):
            raise AuditIntegrityError("audit log is not a regular file")
        if status.st_uid != self._expected_uid:
            raise AuditIntegrityError("audit log owner is invalid")
        if stat.S_IMODE(status.st_mode) & 0o077:
            raise AuditIntegrityError("audit log permissions are too broad")

    def _assert_not_disappeared(self) -> None:
        if self._file_identity is not None:
            raise AuditIntegrityError("audit log disappeared during this session")

    def _assert_session_trusted(self) -> None:
        if self._trust_revoked:
            raise AuditIntegrityError("audit log trust was revoked during this session")

    def _assert_same_file(self, descriptor: int) -> None:
        status = os.fstat(descriptor)
        identity = (status.st_dev, status.st_ino)
        if self._file_identity is None:
            self._file_identity = identity
        elif identity != self._file_identity:
            raise AuditIntegrityError("audit log identity changed during this session")

    def _assert_session_anchor(self, scan: _SegmentScan) -> None:
        if not self._anchor_initialized:
            if scan.hasher is None:
                raise AuditIntegrityError("audit session anchor is not initialized")
            self._anchor_initialized = True
            self._anchored_count = scan.count
            self._anchored_head = scan.last.record_hash if scan.last is not None else ""
            self._anchored_sequence = scan.last.sequence if scan.last is not None else 0
            self._anchored_size = scan.size
            self._anchored_hasher = scan.hasher.copy()
            self._anchored_sha256 = scan.sha256
            return
        if scan.size < self._anchored_size:
            raise AuditIntegrityError("audit log rolled back during this session")
        if scan.prefix_sha256 != self._anchored_sha256:
            raise AuditIntegrityError("audit log history changed during this session")
        if scan.size > self._anchored_size:
            raise AuditIntegrityError("audit log grew outside the active writer")

    def _advance_session_anchor(self, serialized: bytes, record: AuditRecord) -> None:
        self._anchored_count += 1
        self._anchored_head = record.record_hash
        self._anchored_sequence = record.sequence
        self._anchored_size += len(serialized)
        self._anchored_hasher.update(serialized)
        self._anchored_sha256 = self._anchored_hasher.hexdigest()

    @classmethod
    def _verified_records(cls, lines: Iterator[bytes]) -> Iterator[AuditRecord]:
        previous_hash = ""
        expected_sequence = 1
        for position, raw in enumerate(lines):
            try:
                raw_line = raw.decode("utf-8")
            except UnicodeDecodeError as error:
                raise AuditIntegrityError("audit log is not valid UTF-8") from error
            if not raw_line.strip():
                raise AuditIntegrityError("audit log contains an empty record")
            try:
                record = AuditRecord.model_validate_json(raw_line)
            except ValueError as error:
                raise AuditIntegrityError("audit log contains an invalid record") from error
            if position == 0 and record.event_type == SEGMENT_STARTED_EVENT:
                # A continuation segment resumes the chain; the caller checks the link.
                expected_sequence = record.sequence
                previous_hash = record.previous_hash
            body = record.model_dump(exclude={"record_hash"}, mode="python")
            if record.sequence != expected_sequence:
                raise AuditIntegrityError("audit sequence is broken")
            if record.previous_hash != previous_hash:
                raise AuditIntegrityError("audit hash chain is broken")
            if record.record_hash != cls._hash_body(body):
                raise AuditIntegrityError("audit record hash is invalid")
            yield record
            previous_hash = record.record_hash
            expected_sequence += 1

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
