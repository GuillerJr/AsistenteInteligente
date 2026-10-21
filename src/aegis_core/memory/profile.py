from __future__ import annotations

import asyncio
import hashlib
import re
import unicodedata
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum

from aegis_core.contracts import InputModality, UserRequest, is_unsigned_peer_request
from aegis_core.memory.contracts import MemoryEvidence, MemoryKind, MemorySearchHit
from aegis_core.memory.errors import MemoryStoreError
from aegis_core.memory.semantic_profile import (
    MAX_KNOWN_FACTS,
    Complete,
    KnownFact,
    MemoryAction,
    MemoryOperation,
    interpret_owner_memory,
)
from aegis_core.memory.sqlite import SQLiteMemoryStore
from aegis_core.owner_language import explicit_owner_clauses, is_direct_owner_command
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
    # Interpretation runs after the reply, so the owner never waits for it.
    SCHEDULED = "scheduled"


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
    """What Jarvis knows about its owner, learned from the owner's own messages.

    With an interpreter the router decides what each owner message teaches (see
    ``semantic_profile``); the fixed phrasings below remain only as the offline
    fallback when the router is unavailable or returns something unusable.
    """

    def __init__(
        self,
        store: SQLiteMemoryStore,
        *,
        namespace: str,
        interpreter: Complete | None = None,
        interpret_delay_seconds: float = 0.0,
    ) -> None:
        self._store = store
        self._namespace = namespace
        self._interpreter = interpreter
        # Nobody waits for this result: running it after the reply's own NVIDIA burst
        # keeps it from tipping the account's rate limit during the turn.
        self._interpret_delay = max(0.0, interpret_delay_seconds)
        self._pending: set[asyncio.Task[OwnerProfileAction]] = set()

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
        if self._interpreter is None:
            return await self._observe_locally(request)
        task = asyncio.create_task(self._observe_semantically(request, self._interpreter))
        self._pending.add(task)
        task.add_done_callback(self._pending.discard)
        return OwnerProfileAction.SCHEDULED

    async def drain(self) -> None:
        """Wait for scheduled interpretations (tests, shutdown)."""
        while self._pending:
            await asyncio.gather(*tuple(self._pending), return_exceptions=True)

    async def close(self) -> None:
        for task in tuple(self._pending):
            task.cancel()
        await asyncio.gather(*tuple(self._pending), return_exceptions=True)

    async def _observe_semantically(
        self, request: UserRequest, interpreter: Complete,
    ) -> OwnerProfileAction:
        if self._interpret_delay:
            await asyncio.sleep(self._interpret_delay)
        try:
            known = await asyncio.to_thread(
                self._store.list_by_tag,
                namespace=self._namespace,
                tag=OWNER_PROFILE_TAG,
                limit=MAX_KNOWN_FACTS,
            )
        except MemoryStoreError:
            return OwnerProfileAction.UNAVAILABLE
        operations = await interpret_owner_memory(
            request.text,
            [KnownFact(source=hit.source, content=hit.excerpt) for hit in known if hit.source],
            interpreter,
        )
        if operations is None:
            return await self._observe_locally(request)
        try:
            return await self._apply(request, operations)
        except MemoryStoreError:
            return OwnerProfileAction.UNAVAILABLE

    async def _apply(
        self, request: UserRequest, operations: tuple[MemoryOperation, ...],
    ) -> OwnerProfileAction:
        result = OwnerProfileAction.NONE
        for operation in operations:
            if operation.action is MemoryAction.FORGET_ALL:
                await asyncio.to_thread(
                    self._store.delete_by_tag, namespace=self._namespace, tag=OWNER_PROFILE_TAG,
                )
                return OwnerProfileAction.RESET
            if operation.action is MemoryAction.RESET_STYLE:
                await asyncio.to_thread(
                    self._store.delete_by_tag, namespace=self._namespace, tag=OWNER_STYLE_TAG,
                )
                result = OwnerProfileAction.RESET
                continue
            if operation.action is MemoryAction.FORGET:
                if await asyncio.to_thread(
                    self._store.delete_by_source,
                    namespace=self._namespace,
                    source=operation.target_source,
                ) and result is OwnerProfileAction.NONE:
                    result = OwnerProfileAction.FORGOTTEN
                continue
            assert operation.category is not None and operation.fact is not None
            if operation.style is not None:
                dimension = operation.style.split(".", 2)[1]
                source = f"owner-profile:style:{dimension}"
                tags: tuple[str, ...] = (
                    OWNER_PROFILE_TAG, OWNER_STYLE_TAG, "communication", operation.style,
                )
            else:
                source = f"owner-profile:{operation.category.value}:{operation.key}"
                tags = (OWNER_PROFILE_TAG, operation.category.value)
            if operation.target_source is not None and operation.target_source != source:
                await asyncio.to_thread(
                    self._store.delete_by_source,
                    namespace=self._namespace,
                    source=operation.target_source,
                )
            confidence, evidence, now = self._provenance(request)
            await asyncio.to_thread(
                self._store.upsert_by_source,
                namespace=self._namespace,
                kind=MemoryKind.PREFERENCE,
                content=operation.fact,
                source=source,
                tags=tags,
                confidence=confidence,
                evidence=evidence,
                expires_at=now + timedelta(days=14) if operation.temporary else None,
                last_confirmed_at=now,
            )
            result = OwnerProfileAction.UPSERTED
        return result

    @staticmethod
    def _provenance(request: UserRequest) -> tuple[float, MemoryEvidence, datetime]:
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
        return confidence, evidence, datetime.now(UTC)

    async def _observe_locally(self, request: UserRequest) -> OwnerProfileAction:
        normalized = _fold(request.text)
        try:
            if style_reset_requested(request.text):
                await asyncio.to_thread(
                    self._store.delete_by_tag,
                    namespace=self._namespace,
                    tag=OWNER_STYLE_TAG,
                )
                return OwnerProfileAction.RESET
            if normalized in _RESET_PHRASES and is_direct_owner_command(request.text):
                await asyncio.to_thread(
                    self._store.delete_by_tag,
                    namespace=self._namespace,
                    tag=OWNER_PROFILE_TAG,
                )
                return OwnerProfileAction.RESET

            forget_match = (
                _FORGET_PREFIX.match(request.text)
                if is_direct_owner_command(request.text)
                else None
            )
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
            confidence, evidence, now = self._provenance(request)
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
        return OwnerProfile.speaks_for_owner(request)

    @staticmethod
    def speaks_for_owner(request: UserRequest) -> bool:
        """Whether the request may read or change the owner's private state."""

        if is_unsigned_peer_request(request):
            return False
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
        if request.metadata.get("owner_speaker_profile") is not True:
            return False
        if request.metadata.get("owner_presence_verified") is not True:
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
    clauses = explicit_owner_clauses(text)
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
    positions = {fact.source: index for index, fact in enumerate(facts)}
    # Clauses remain attributable owner assertions after reference/uncertainty
    # filtering. Resolve each slot in actual speech order, so a later correction
    # cannot lose to an earlier statement or to the negative-pattern priority.
    for clause in clauses:
        for pattern in _FACT_PATTERNS:
            if pattern.category == "communication" and style_facts:
                continue
            match = pattern.expression.match(clause)
            if match is None:
                continue
            value = _clean_value(match.group("value"))
            if not value:
                continue
            source = f"owner-profile:{pattern.source_prefix}"
            if not pattern.stable_slot:
                source = f"{source}:{_stable_key(value)}"
            fact = OwnerProfileFact(
                source=source,
                content=pattern.template.format(value=value),
                tags=(OWNER_PROFILE_TAG, pattern.category),
            )
            if source in positions:
                facts[positions[source]] = fact
            elif len(facts) < MAX_PROFILE_FACTS_PER_TURN:
                positions[source] = len(facts)
                facts.append(fact)
            # Keep inspecting later clauses even after filling the four slots:
            # the bounded set must still receive its latest explicit corrections.
            break
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
