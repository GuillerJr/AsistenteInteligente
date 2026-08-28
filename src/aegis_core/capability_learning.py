from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
import stat
import threading
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from ipaddress import ip_address
from pathlib import Path
from typing import Any
from urllib.parse import urlparse
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

from aegis_core.contracts import InputModality, ToolExecutionResult, UserRequest
from aegis_core.ipc.protocol import IpcRequest
from aegis_core.ipc.server import IpcHandlerResult, IpcMethodHandler
from aegis_core.memory.profile import OwnerProfile
from aegis_core.privacy import redact_for_remote
from aegis_core.secrets import contains_likely_secret_material

MAX_CAPABILITY_RECORDS = 128
MAX_CAPABILITY_RECORD_BYTES = 16_384
CAPABILITY_RESEARCH_TTL = timedelta(days=30)
_WAKE_PREFIX = re.compile(r"^\s*jarvis\s*[,;:.-]?\s*", re.IGNORECASE)
_NEGATIONS = frozenset({"don\u2019t", "dont", "never", "no", "not", "nunca"})
_EXPLICIT_LEARNING_MARKERS = (
    "aprende a ",
    "averigua cómo ",
    "averigua como ",
    "descubre cómo ",
    "descubre como ",
    "find out how to ",
    "learn how to ",
)
_UNKNOWN_OPERATION_TERMS = frozenset(
    {
        "actualiza",
        "actualizar",
        "abre",
        "abrir",
        "automatiza",
        "automatizar",
        "borra",
        "borrar",
        "compra",
        "comprar",
        "configura",
        "configurar",
        "conecta",
        "conectar",
        "controla",
        "controlar",
        "convierte",
        "convertir",
        "descarga",
        "descargar",
        "elimina",
        "eliminar",
        "envia",
        "enviar",
        "envía",
        "ejecuta",
        "ejecutar",
        "exporta",
        "exportar",
        "importa",
        "importar",
        "instala",
        "instalar",
        "mueve",
        "mover",
        "organiza",
        "organizar",
        "publica",
        "publicar",
        "renombra",
        "renombrar",
        "reserva",
        "reservar",
        "respalda",
        "respaldar",
        "sincroniza",
        "sincronizar",
        "sube",
        "subir",
        "update",
        "open",
        "automate",
        "backup",
        "buy",
        "configure",
        "connect",
        "control",
        "convert",
        "delete",
        "download",
        "execute",
        "export",
        "import",
        "install",
        "move",
        "organize",
        "publish",
        "rename",
        "reserve",
        "send",
        "sync",
        "upload",
    }
)


class CapabilityLearningError(ValueError):
    """Raised when adaptive capability evidence violates the local contract."""


class CapabilityLearningStatus(StrEnum):
    OBSERVED = "observed"
    RESEARCHED = "researched"


class CapabilitySource(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    url: str = Field(min_length=12, max_length=2_048)
    title: str = Field(min_length=1, max_length=200)
    excerpt: str = Field(min_length=1, max_length=800)

    @field_validator("url")
    @classmethod
    def url_must_be_public_https_shape(cls, value: str) -> str:
        parsed = urlparse(value)
        hostname = parsed.hostname
        if (
            parsed.scheme != "https"
            or hostname is None
            or parsed.username is not None
            or parsed.password is not None
            or parsed.port not in {None, 443}
            or parsed.query
            or parsed.fragment
            or hostname in {"localhost", "localhost.localdomain"}
            or hostname.endswith(".local")
        ):
            raise ValueError("capability source must use public HTTPS shape")
        try:
            ip_address(hostname)
        except ValueError:
            pass
        else:
            raise ValueError("capability source cannot use an IP literal")
        return value

    @field_validator("title", "excerpt")
    @classmethod
    def text_must_be_bounded_untrusted_content(cls, value: str) -> str:
        if contains_likely_secret_material(value) or any(
            ord(character) < 32 and character not in {"\n", "\r", "\t"} for character in value
        ):
            raise ValueError("capability source text is unsafe")
        return value


class CapabilityRecord(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: int = Field(default=1, ge=1, le=1)
    gap_id: str = Field(pattern=r"^[0-9a-f]{64}$")
    normalized_goal: str = Field(min_length=4, max_length=600)
    terms: frozenset[str] = Field(min_length=1, max_length=32)
    status: CapabilityLearningStatus
    occurrences: int = Field(ge=1, le=10_000)
    first_seen_at: datetime
    last_seen_at: datetime
    researched_at: datetime | None = None
    expires_at: datetime | None = None
    sources: tuple[CapabilitySource, ...] = Field(default_factory=tuple, max_length=3)
    integrity_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    @field_validator("first_seen_at", "last_seen_at", "researched_at", "expires_at")
    @classmethod
    def timestamps_must_be_aware(cls, value: datetime | None) -> datetime | None:
        if value is not None and (value.tzinfo is None or value.utcoffset() is None):
            raise ValueError("capability timestamp must be timezone-aware")
        return value

    @model_validator(mode="after")
    def state_and_integrity_must_match(self) -> CapabilityRecord:
        if self.last_seen_at < self.first_seen_at:
            raise ValueError("capability timestamps are out of order")
        if self.status is CapabilityLearningStatus.OBSERVED:
            if self.researched_at is not None or self.expires_at is not None or self.sources:
                raise ValueError("observed capability cannot contain research")
        elif (
            self.researched_at is None
            or self.expires_at is None
            or self.expires_at <= self.researched_at
            or not self.sources
        ):
            raise ValueError("researched capability requires bounded evidence")
        if self.integrity_sha256 != _record_integrity(
            self.model_dump(exclude={"integrity_sha256"})
        ):
            raise ValueError("capability record integrity does not match")
        return self

    @classmethod
    def create(
        cls,
        *,
        gap_id: str,
        normalized_goal: str,
        terms: frozenset[str],
        status: CapabilityLearningStatus,
        occurrences: int,
        first_seen_at: datetime,
        last_seen_at: datetime,
        researched_at: datetime | None = None,
        expires_at: datetime | None = None,
        sources: tuple[CapabilitySource, ...] = (),
    ) -> CapabilityRecord:
        values = {
            "schema_version": 1,
            "gap_id": gap_id,
            "normalized_goal": normalized_goal,
            "terms": terms,
            "status": status,
            "occurrences": occurrences,
            "first_seen_at": first_seen_at,
            "last_seen_at": last_seen_at,
            "researched_at": researched_at,
            "expires_at": expires_at,
            "sources": sources,
        }
        return cls(**values, integrity_sha256=_record_integrity(values))


def _canonical(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): _canonical(item) for key, item in value.items()}
    if isinstance(value, (frozenset, set)):
        return sorted((_canonical(item) for item in value), key=str)
    if isinstance(value, (list, tuple)):
        return [_canonical(item) for item in value]
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, StrEnum):
        return value.value
    if isinstance(value, BaseModel):
        return _canonical(value.model_dump(mode="python"))
    return value


def _record_integrity(values: dict[str, Any]) -> str:
    payload = json.dumps(
        _canonical(values),
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def normalize_capability_goal(text: str) -> tuple[str, frozenset[str]] | None:
    if len(text.encode("utf-8")) > 1_200 or contains_likely_secret_material(text):
        return None
    redacted = redact_for_remote(text).text
    normalized = " ".join(_WAKE_PREFIX.sub("", redacted).casefold().split())
    if not 4 <= len(normalized) <= 600 or not normalized.isprintable():
        return None
    ordered_terms = tuple(
        dict.fromkeys(
            term
            for term in re.findall(r"[^\W_]+", normalized, flags=re.UNICODE)
            if 2 <= len(term) <= 40
        )
    )
    if not ordered_terms:
        return None
    return normalized, frozenset(ordered_terms[:32])


def is_capability_gap_request(
    request: UserRequest,
    *,
    known_tool_names: frozenset[str],
    has_skill: bool,
) -> bool:
    if (
        has_skill
        or known_tool_names
        or request.image is not None
        or InputModality.VIDEO in request.modalities
        or request.metadata.get("force_remote") is True
    ):
        return False
    normalized = normalize_capability_goal(request.text)
    if normalized is None:
        return False
    goal, terms = normalized
    if not terms.isdisjoint(_NEGATIONS):
        return False
    return any(marker in goal for marker in _EXPLICIT_LEARNING_MARKERS) or not terms.isdisjoint(
        _UNKNOWN_OPERATION_TERMS
    )


class CapabilityLearningStore:
    def __init__(self, directory: Path) -> None:
        self.directory = directory
        self._lock = threading.RLock()

    def observe(self, text: str, *, now: datetime | None = None) -> CapabilityRecord | None:
        normalized = normalize_capability_goal(text)
        if normalized is None:
            return None
        goal, terms = normalized
        timestamp = now or datetime.now(UTC)
        gap_id = hashlib.sha256(goal.encode("utf-8")).hexdigest()
        with self._lock:
            current = self.get(gap_id)
            record = CapabilityRecord.create(
                gap_id=gap_id,
                normalized_goal=goal,
                terms=terms,
                status=(
                    current.status if current is not None else CapabilityLearningStatus.OBSERVED
                ),
                occurrences=min(10_000, (current.occurrences if current is not None else 0) + 1),
                first_seen_at=current.first_seen_at if current is not None else timestamp,
                last_seen_at=timestamp,
                researched_at=current.researched_at if current is not None else None,
                expires_at=current.expires_at if current is not None else None,
                sources=current.sources if current is not None else (),
            )
            self._write(record)
            return record

    def record_research(
        self,
        text: str,
        result: ToolExecutionResult,
        *,
        now: datetime | None = None,
    ) -> CapabilityRecord | None:
        if result.tool_name != "web_research" or not result.success:
            return self.observe(text, now=now)
        observed = self.observe(text, now=now)
        if observed is None:
            return None
        try:
            payload = json.loads(result.output)
            raw_results = payload["results"]
            if not isinstance(raw_results, list):
                raise TypeError
            sources = tuple(
                self._source(item) for item in raw_results[:3] if isinstance(item, dict)
            )
        except (KeyError, TypeError, ValueError, ValidationError, json.JSONDecodeError):
            return observed
        if not sources:
            return observed
        timestamp = now or datetime.now(UTC)
        researched = CapabilityRecord.create(
            gap_id=observed.gap_id,
            normalized_goal=observed.normalized_goal,
            terms=observed.terms,
            status=CapabilityLearningStatus.RESEARCHED,
            occurrences=observed.occurrences,
            first_seen_at=observed.first_seen_at,
            last_seen_at=timestamp,
            researched_at=timestamp,
            expires_at=timestamp + CAPABILITY_RESEARCH_TTL,
            sources=sources,
        )
        with self._lock:
            self._write(researched)
        return researched

    def recall(self, text: str, *, now: datetime | None = None) -> CapabilityRecord | None:
        normalized = normalize_capability_goal(text)
        if normalized is None:
            return None
        goal, terms = normalized
        timestamp = now or datetime.now(UTC)
        exact = self.get(hashlib.sha256(goal.encode("utf-8")).hexdigest())
        if self._is_live_research(exact, timestamp):
            return exact
        candidates = []
        for record in self.load_all():
            if not self._is_live_research(record, timestamp):
                continue
            union = terms | record.terms
            if len(terms & record.terms) < 2 or not union:
                continue
            similarity = len(terms & record.terms) / len(union)
            if similarity >= 0.72:
                candidates.append((similarity, record.last_seen_at, record))
        return max(candidates, default=(0.0, timestamp, None), key=lambda item: (item[0], item[1]))[
            2
        ]

    def get(self, gap_id: str) -> CapabilityRecord | None:
        if re.fullmatch(r"^[0-9a-f]{64}$", gap_id) is None:
            raise CapabilityLearningError("capability identifier is invalid")
        path = self.directory / f"{gap_id}.json"
        try:
            return self._read(path)
        except (OSError, CapabilityLearningError, ValidationError, ValueError):
            return None

    def load_all(self) -> tuple[CapabilityRecord, ...]:
        if self.directory.is_symlink():
            return ()
        try:
            paths = sorted(self.directory.glob("*.json"))
        except OSError:
            return ()
        records = []
        for path in paths[: MAX_CAPABILITY_RECORDS + 1]:
            try:
                record = self._read(path)
                if path.stem == record.gap_id:
                    records.append(record)
            except (OSError, CapabilityLearningError, ValidationError, ValueError):
                continue
        return tuple(records)

    def forget(self, gap_id: str) -> bool:
        if re.fullmatch(r"^[0-9a-f]{64}$", gap_id) is None:
            raise CapabilityLearningError("capability identifier is invalid")
        with self._lock:
            if self.directory.is_symlink():
                raise CapabilityLearningError("capability directory is unsafe")
            target = self.directory / f"{gap_id}.json"
            if target.is_symlink():
                raise CapabilityLearningError("capability record is unsafe")
            try:
                target.unlink()
            except FileNotFoundError:
                return False
            self._sync_directory()
            return True

    def status_payload(self) -> dict[str, int]:
        records = self.load_all()
        return {
            "observed": sum(item.status is CapabilityLearningStatus.OBSERVED for item in records),
            "researched": sum(
                item.status is CapabilityLearningStatus.RESEARCHED for item in records
            ),
            "total": len(records),
        }

    @staticmethod
    def _source(item: dict[str, Any]) -> CapabilitySource:
        url = item.get("url")
        title = item.get("title")
        content = item.get("content")
        if not all(isinstance(value, str) for value in (url, title, content)):
            raise TypeError
        normalized_title = " ".join(title.split())[:200]
        normalized_excerpt = " ".join(content.split())[:800]
        return CapabilitySource(
            url=url,
            title=normalized_title or "Fuente pública",
            excerpt=normalized_excerpt,
        )

    @staticmethod
    def _is_live_research(record: CapabilityRecord | None, now: datetime) -> bool:
        return (
            record is not None
            and record.status is CapabilityLearningStatus.RESEARCHED
            and record.expires_at is not None
            and record.expires_at > now
        )

    def _read(self, path: Path) -> CapabilityRecord:
        if path.is_symlink() or not path.is_file():
            raise CapabilityLearningError("capability record must be a regular file")
        flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
        descriptor = os.open(path, flags)
        try:
            metadata = os.fstat(descriptor)
            if (
                not stat.S_ISREG(metadata.st_mode)
                or not 0 < metadata.st_size <= MAX_CAPABILITY_RECORD_BYTES
            ):
                raise CapabilityLearningError("capability record size or type is invalid")
            data = os.read(descriptor, MAX_CAPABILITY_RECORD_BYTES + 1)
        finally:
            os.close(descriptor)
        return CapabilityRecord.model_validate_json(data)

    def _write(self, record: CapabilityRecord) -> None:
        self._ensure_private_directory()
        existing = self.load_all()
        if (
            record.gap_id not in {item.gap_id for item in existing}
            and len(existing) >= MAX_CAPABILITY_RECORDS
        ):
            raise CapabilityLearningError("capability learning capacity reached")
        target = self.directory / f"{record.gap_id}.json"
        temporary = self.directory / f".{record.gap_id}.{uuid4().hex}.tmp"
        payload = record.model_dump_json(indent=2).encode("utf-8") + b"\n"
        if len(payload) > MAX_CAPABILITY_RECORD_BYTES:
            raise CapabilityLearningError("capability record exceeds size limit")
        descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        try:
            with os.fdopen(descriptor, "wb") as handle:
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, target)
            os.chmod(target, 0o600)
            self._sync_directory()
        finally:
            try:
                temporary.unlink()
            except FileNotFoundError:
                pass

    def _ensure_private_directory(self) -> None:
        self.directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        if self.directory.is_symlink() or not self.directory.is_dir():
            raise CapabilityLearningError("capability directory is unsafe")
        os.chmod(self.directory, 0o700)

    def _sync_directory(self) -> None:
        descriptor = os.open(self.directory, os.O_RDONLY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)


class CapabilityLearningCoordinator:
    def __init__(self, store: CapabilityLearningStore) -> None:
        self.store = store

    async def recall(self, request: UserRequest) -> CapabilityRecord | None:
        if InputModality.AUDIO in request.modalities and not OwnerProfile.is_verified_owner_voice(
            request
        ):
            return None
        try:
            return await asyncio.to_thread(self.store.recall, request.text)
        except (CapabilityLearningError, OSError, ValueError):
            return None

    async def observe(
        self,
        request: UserRequest,
        research_result: ToolExecutionResult | None,
    ) -> CapabilityRecord | None:
        if InputModality.AUDIO in request.modalities and not OwnerProfile.is_verified_owner_voice(
            request
        ):
            return None
        try:
            if research_result is None:
                return await asyncio.to_thread(self.store.observe, request.text)
            return await asyncio.to_thread(
                self.store.record_research,
                request.text,
                research_result,
            )
        except (CapabilityLearningError, OSError, ValueError):
            return None


class CapabilityLearningIpcService:
    METHOD = "capabilities.status"

    def __init__(self, store: CapabilityLearningStore) -> None:
        self._store = store

    def handlers(self) -> dict[str, IpcMethodHandler]:
        return {self.METHOD: self.handle}

    async def handle(self, request: IpcRequest) -> IpcHandlerResult:
        if request.method != self.METHOD:
            return IpcHandlerResult(ok=False, error_code="method_not_found")
        if request.payload:
            return IpcHandlerResult(ok=False, error_code="invalid_payload")
        payload = await asyncio.to_thread(self._store.status_payload)
        return IpcHandlerResult(ok=True, payload=payload)
