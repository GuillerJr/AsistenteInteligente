from __future__ import annotations

import asyncio
import hashlib
import re
import unicodedata
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum

from aegis_core.contracts import InputModality, UserRequest
from aegis_core.memory.contracts import MemoryEvidence, MemoryKind, MemorySearchHit
from aegis_core.memory.profile import OwnerProfile
from aegis_core.memory.sqlite import MemoryStoreError, SQLiteMemoryStore

SOCIAL_TOPIC_TAG = "social-topic"
OWNER_COMMITMENT_TAG = "owner-commitment"
MAX_SOCIAL_FACTS_PER_TURN = 2


class SocialMemoryAction(StrEnum):
    NONE = "none"
    UPSERTED = "upserted"
    FORGOTTEN = "forgotten"
    RESOLVED = "resolved"
    RESET = "reset"
    INELIGIBLE = "ineligible"
    UNAVAILABLE = "unavailable"


@dataclass(frozen=True, slots=True)
class SocialFact:
    category: str
    value: str
    content: str
    tag: str

    @property
    def source(self) -> str:
        return f"social:{self.category}:{_stable_key(self.value)}"


_VALUE = r"(?P<value>[^\n.!?;]{1,180})(?:[.!?;]|$)"
_TOPIC_PATTERNS = (
    re.compile(rf"\bestoy trabajando en\s+{_VALUE}", re.IGNORECASE),
    re.compile(rf"\bmi proyecto(?: actual)?(?: es| se llama)\s+{_VALUE}", re.IGNORECASE),
    re.compile(rf"\bmi objetivo(?: actual)? es\s+{_VALUE}", re.IGNORECASE),
)
_COMMITMENT_PATTERN = re.compile(
    rf"\b(?:recuerda|ten en cuenta) que (?:tengo que|debo|quiero)\s+{_VALUE}",
    re.IGNORECASE,
)
_RESOLVE_PATTERN = re.compile(
    r"^\s*(?:jarvis[, ]+)?(?:marca como resuelto|ya resolvi|ya resolví)\s+"
    r"(?P<value>[^\n.!?;]{1,180})[.!?;]?\s*$",
    re.IGNORECASE,
)
_FORGET_TOPIC_PATTERN = re.compile(
    r"^\s*(?:jarvis[, ]+)?(?:olvida|borra) el tema\s+"
    r"(?P<value>[^\n.!?;]{1,180})[.!?;]?\s*$",
    re.IGNORECASE,
)
_RESET_COMMITMENTS = frozenset(
    {"borra mis compromisos", "olvida mis compromisos", "borra todos mis compromisos"}
)
_RESET_SOCIAL = frozenset(
    {
        "borra mis temas y compromisos",
        "olvida mis temas y compromisos",
        "reinicia mi memoria social",
    }
)


class SocialMemory:
    """Explicit local relationship continuity without inferred emotional profiles."""

    def __init__(self, store: SQLiteMemoryStore, *, namespace: str) -> None:
        self._store = store
        self._namespace = namespace

    async def recall(self, *, limit: int = 4) -> tuple[MemorySearchHit, ...]:
        if not 1 <= limit <= 8:
            raise ValueError("social memory limit is out of range")
        per_tag = max(1, limit // 2)
        try:
            topics, commitments = await asyncio.gather(
                asyncio.to_thread(
                    self._store.list_by_tag,
                    namespace=self._namespace,
                    tag=SOCIAL_TOPIC_TAG,
                    limit=per_tag,
                ),
                asyncio.to_thread(
                    self._store.list_by_tag,
                    namespace=self._namespace,
                    tag=OWNER_COMMITMENT_TAG,
                    limit=per_tag,
                ),
            )
        except MemoryStoreError:
            return ()
        combined = sorted(
            (*topics, *commitments),
            key=lambda hit: (hit.updated_at, str(hit.memory_id)),
            reverse=True,
        )
        return tuple(combined[:limit])

    async def observe(self, request: UserRequest) -> SocialMemoryAction:
        if InputModality.AUDIO in request.modalities and not OwnerProfile.is_verified_owner_voice(
            request
        ):
            return SocialMemoryAction.INELIGIBLE
        folded = _fold(request.text)
        try:
            if folded in _RESET_SOCIAL:
                await self._delete_tag(SOCIAL_TOPIC_TAG)
                await self._delete_tag(OWNER_COMMITMENT_TAG)
                return SocialMemoryAction.RESET
            if folded in _RESET_COMMITMENTS:
                await self._delete_tag(OWNER_COMMITMENT_TAG)
                return SocialMemoryAction.RESET
            if match := _RESOLVE_PATTERN.match(request.text):
                return await self._delete_value(
                    "commitment",
                    match.group("value"),
                    SocialMemoryAction.RESOLVED,
                )
            if match := _FORGET_TOPIC_PATTERN.match(request.text):
                return await self._delete_value(
                    "topic",
                    match.group("value"),
                    SocialMemoryAction.FORGOTTEN,
                )
            facts = extract_social_facts(request.text)
            if not facts:
                return SocialMemoryAction.NONE
            now = datetime.now(UTC)
            is_voice = InputModality.AUDIO in request.modalities
            evidence = MemoryEvidence.VERIFIED_VOICE if is_voice else MemoryEvidence.EXPLICIT_TEXT
            for fact in facts[:MAX_SOCIAL_FACTS_PER_TURN]:
                await asyncio.to_thread(
                    self._store.upsert_by_source,
                    namespace=self._namespace,
                    kind=MemoryKind.EPISODIC,
                    content=fact.content,
                    source=fact.source,
                    tags=(fact.tag,),
                    confidence=1.0,
                    evidence=evidence,
                    expires_at=now
                    + timedelta(days=30 if fact.category == "commitment" else 90),
                    last_confirmed_at=now,
                )
            return SocialMemoryAction.UPSERTED
        except MemoryStoreError:
            return SocialMemoryAction.UNAVAILABLE

    async def _delete_tag(self, tag: str) -> None:
        await asyncio.to_thread(
            self._store.delete_by_tag,
            namespace=self._namespace,
            tag=tag,
        )

    async def _delete_value(
        self,
        category: str,
        value: str,
        action: SocialMemoryAction,
    ) -> SocialMemoryAction:
        cleaned = _clean_value(value)
        if not cleaned:
            return SocialMemoryAction.NONE
        deleted = await asyncio.to_thread(
            self._store.delete_by_source,
            namespace=self._namespace,
            source=f"social:{category}:{_stable_key(cleaned)}",
        )
        return action if deleted else SocialMemoryAction.NONE


def extract_social_facts(text: str) -> tuple[SocialFact, ...]:
    if not text or text.rstrip().endswith("?"):
        return ()
    facts: list[SocialFact] = []
    seen: set[str] = set()
    for pattern in _TOPIC_PATTERNS:
        for match in pattern.finditer(text):
            value = _clean_value(match.group("value"))
            if not value:
                continue
            key = f"topic:{_stable_key(value)}"
            if key in seen:
                continue
            seen.add(key)
            facts.append(
                SocialFact(
                    category="topic",
                    value=value,
                    content=f"Tema activo del propietario: {value}.",
                    tag=SOCIAL_TOPIC_TAG,
                )
            )
    for match in _COMMITMENT_PATTERN.finditer(text):
        value = _clean_value(match.group("value"))
        if not value:
            continue
        key = f"commitment:{_stable_key(value)}"
        if key in seen:
            continue
        seen.add(key)
        facts.append(
            SocialFact(
                category="commitment",
                value=value,
                content=f"Compromiso pendiente del propietario: {value}.",
                tag=OWNER_COMMITMENT_TAG,
            )
        )
    return tuple(facts[:MAX_SOCIAL_FACTS_PER_TURN])


def _clean_value(value: str) -> str:
    normalized = " ".join(value.split()).strip(" ,:\u2014\u2013-\"'“”«»")
    if not normalized or len(normalized) > 180:
        return ""
    return normalized


def _stable_key(value: str) -> str:
    return hashlib.sha256(_fold(value).encode("utf-8")).hexdigest()[:16]


def _fold(value: str) -> str:
    decomposed = unicodedata.normalize("NFKD", value.casefold())
    without_marks = "".join(
        character for character in decomposed if not unicodedata.combining(character)
    )
    return " ".join(re.findall(r"[a-z0-9]+", without_marks))
