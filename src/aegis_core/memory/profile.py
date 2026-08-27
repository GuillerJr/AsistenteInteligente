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
from aegis_core.memory.sqlite import MemoryStoreError, SQLiteMemoryStore
from aegis_core.style import (
    OWNER_STYLE_TAG,
    extract_style_preferences,
    style_reset_requested,
)

OWNER_PROFILE_TAG = "owner-profile"
MINIMUM_OWNER_VOICE_CONFIDENCE = 0.78
MAX_PROFILE_FACTS_PER_TURN = 4


class OwnerProfileAction(StrEnum):
    NONE = "none"
    UPSERTED = "upserted"
    FORGOTTEN = "forgotten"
    RESET = "reset"
    INELIGIBLE = "ineligible"
    UNAVAILABLE = "unavailable"


@dataclass(frozen=True, slots=True)
class OwnerProfileFact:
    source: str
    content: str
    tags: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class _FactPattern:
    expression: re.Pattern[str]
    category: str
    source_prefix: str
    template: str
    stable_slot: bool = False


_VALUE = (
    r"(?P<value>[^\n.!?;]{1,180}?)"
    r"(?=\s+(?:y\s+)?(?:me|mi|trabajo|estoy|odio|detesto|prefiero)\b|[.!?;]|$)"
)
_FACT_PATTERNS = (
    _FactPattern(
        re.compile(rf"\b(?:me llamo|mi nombre es)\s+{_VALUE}", re.IGNORECASE),
        "identity",
        "name",
        "El propietario se llama {value}.",
        stable_slot=True,
    ),
    _FactPattern(
        re.compile(
            rf"\b(?:prefiero que me hables|prefiero que me respondas|"
            rf"háblame|respóndeme)\s+{_VALUE}",
            re.IGNORECASE,
        ),
        "communication",
        "communication",
        "El propietario prefiere que Jarvis responda {value}.",
        stable_slot=True,
    ),
    _FactPattern(
        re.compile(rf"\b(?:no me gusta|odio|detesto)\s+{_VALUE}", re.IGNORECASE),
        "preference",
        "preference",
        "Al propietario no le gusta {value}.",
    ),
    _FactPattern(
        re.compile(rf"\b(?:(?<!no )me gusta|me encanta|prefiero)\s+{_VALUE}", re.IGNORECASE),
        "preference",
        "preference",
        "Al propietario le gusta o prefiere {value}.",
    ),
    _FactPattern(
        re.compile(rf"\b(?:me interesa|estoy aprendiendo|estudio)\s+{_VALUE}", re.IGNORECASE),
        "interest",
        "interest",
        "Al propietario le interesa {value}.",
    ),
    _FactPattern(
        re.compile(rf"\btrabajo\s+(?:como|en)\s+{_VALUE}", re.IGNORECASE),
        "work",
        "work",
        "El propietario trabaja en o como {value}.",
        stable_slot=True,
    ),
)
_RESET_PHRASES = frozenset(
    {
        "borra mi perfil",
        "borra mi perfil de jarvis",
        "borra todo lo que sabes de mi",
        "olvida todo lo que sabes de mi",
        "reinicia mi perfil",
    }
)
_FORGET_PREFIX = re.compile(r"^\s*(?:jarvis[, ]+)?(?:olvida|borra)\s+que\s+", re.IGNORECASE)
_TEMPORARY_PROFILE_TERMS = frozenset(
    {"esta semana", "por ahora", "temporalmente", "estos dias", "estos días"}
)


class OwnerProfile:
    """Small, deterministic and local owner adaptation layer."""

    def __init__(self, store: SQLiteMemoryStore, *, namespace: str) -> None:
        self._store = store
        self._namespace = namespace

    async def recall(self, *, limit: int = 6) -> tuple[MemorySearchHit, ...]:
        try:
            return await asyncio.to_thread(
                self._store.list_by_tag,
                namespace=self._namespace,
                tag=OWNER_PROFILE_TAG,
                limit=limit,
            )
        except MemoryStoreError:
            return ()

    async def observe(self, request: UserRequest) -> OwnerProfileAction:
        if not self._eligible_owner_request(request):
            return OwnerProfileAction.INELIGIBLE
        normalized = _fold(request.text)
        try:
            if style_reset_requested(request.text):
                await asyncio.to_thread(
                    self._store.delete_by_tag,
                    namespace=self._namespace,
                    tag=OWNER_STYLE_TAG,
                )
                return OwnerProfileAction.RESET
            if normalized in _RESET_PHRASES:
                await asyncio.to_thread(
                    self._store.delete_by_tag,
                    namespace=self._namespace,
                    tag=OWNER_PROFILE_TAG,
                )
                return OwnerProfileAction.RESET

            forget_match = _FORGET_PREFIX.match(request.text)
            if forget_match:
                facts = extract_owner_profile_facts(request.text[forget_match.end() :])
                if not facts:
                    return OwnerProfileAction.NONE
                forgotten = False
                for fact in facts:
                    forgotten = (
                        await asyncio.to_thread(
                            self._store.delete_by_source,
                            namespace=self._namespace,
                            source=fact.source,
                        )
                        or forgotten
                    )
                return OwnerProfileAction.FORGOTTEN if forgotten else OwnerProfileAction.NONE

            facts = extract_owner_profile_facts(request.text)
            if not facts:
                return OwnerProfileAction.NONE
            now = datetime.now(UTC)
            voice_identity = request.metadata.get("speaker_identity")
            is_voice = InputModality.AUDIO in request.modalities
            voice_confidence = (
                voice_identity.get("confidence") if isinstance(voice_identity, dict) else None
            )
            confidence = (
                min(1.0, max(MINIMUM_OWNER_VOICE_CONFIDENCE, float(voice_confidence)))
                if is_voice and isinstance(voice_confidence, (int, float))
                else 1.0
            )
            evidence = MemoryEvidence.VERIFIED_VOICE if is_voice else MemoryEvidence.EXPLICIT_TEXT
            folded_text = _fold(request.text)
            expires_at = (
                now + timedelta(days=14)
                if any(term in folded_text for term in _TEMPORARY_PROFILE_TERMS)
                else None
            )
            for fact in facts[:MAX_PROFILE_FACTS_PER_TURN]:
                await asyncio.to_thread(
                    self._store.upsert_by_source,
                    namespace=self._namespace,
                    kind=MemoryKind.PREFERENCE,
                    content=fact.content,
                    source=fact.source,
                    tags=fact.tags,
                    confidence=confidence,
                    evidence=evidence,
                    expires_at=expires_at,
                    last_confirmed_at=now,
                )
            return OwnerProfileAction.UPSERTED
        except MemoryStoreError:
            return OwnerProfileAction.UNAVAILABLE

    @staticmethod
    def _eligible_owner_request(request: UserRequest) -> bool:
        if InputModality.AUDIO not in request.modalities:
            return True
        return OwnerProfile.is_verified_owner_voice(request)

    @staticmethod
    def is_verified_owner_voice(request: UserRequest) -> bool:
        if InputModality.AUDIO not in request.modalities:
            return False
        identity = request.metadata.get("speaker_identity")
        if not isinstance(identity, dict):
            return False
        if request.metadata.get("sole_speaker_profile") is not True:
            return False
        confidence = identity.get("confidence")
        identifier = identity.get("id")
        return (
            isinstance(identifier, str)
            and bool(identifier)
            and isinstance(confidence, (int, float))
            and not isinstance(confidence, bool)
            and confidence >= MINIMUM_OWNER_VOICE_CONFIDENCE
        )


def extract_owner_profile_facts(text: str) -> tuple[OwnerProfileFact, ...]:
    if not text or text.rstrip().endswith("?"):
        return ()
    style_facts = extract_style_preferences(text)
    facts: list[OwnerProfileFact] = [
        OwnerProfileFact(
            source=fact.source,
            content=fact.content,
            tags=(OWNER_PROFILE_TAG, *fact.tags),
        )
        for fact in style_facts
    ]
    if len(facts) >= MAX_PROFILE_FACTS_PER_TURN:
        return tuple(facts[:MAX_PROFILE_FACTS_PER_TURN])
    seen_sources = {fact.source for fact in facts}
    for pattern in _FACT_PATTERNS:
        if pattern.category == "communication" and style_facts:
            continue
        for match in pattern.expression.finditer(text):
            value = _clean_value(match.group("value"))
            if not value:
                continue
            source = f"owner-profile:{pattern.source_prefix}"
            if not pattern.stable_slot:
                source = f"{source}:{_stable_key(value)}"
            if source in seen_sources:
                continue
            seen_sources.add(source)
            facts.append(
                OwnerProfileFact(
                    source=source,
                    content=pattern.template.format(value=value),
                    tags=(OWNER_PROFILE_TAG, pattern.category),
                )
            )
            if len(facts) >= MAX_PROFILE_FACTS_PER_TURN:
                return tuple(facts)
    return tuple(facts)


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
