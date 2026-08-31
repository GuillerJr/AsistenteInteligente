from __future__ import annotations

import re
import unicodedata
from collections.abc import Callable
from dataclasses import dataclass

MAX_ENTITIES = 16
MAX_RELATIONSHIPS = 24
MAX_ENTITY_NAME_CHARS = 128

_ENTITY_TYPES = frozenset({"person", "project", "document", "tool", "concept"})
_EDGE_TYPES = frozenset(
    {
        "CONNECTED_TO",
        "DEPENDS_ON",
        "MENTIONS",
        "REQUIRES",
        "SENDS",
        "USES",
    }
)
_KNOWN_TOOLS = (
    "Accessibility",
    "Calendar",
    "Chrome",
    "Electron",
    "Finder",
    "GitHub",
    "JXA",
    "LangGraph",
    "Mail",
    "NVIDIA NIM",
    "Python",
    "Safari",
    "ScreenCaptureKit",
    "SQLite",
    "Swift",
    "Tauri",
    "Three.js",
    "Vision",
    "Xcode",
)
_NAME = r"[\wÀ-ÖØ-öø-ÿ][\wÀ-ÖØ-öø-ÿ +#-]{0,126}?[\wÀ-ÖØ-öø-ÿ+#]"
_PROJECT_PATTERN = re.compile(
    r"\b(?:proyecto|project)\s+(?:llamado|named|denominado|denominada)?\s*"
    r"[\"“']?([\wÀ-ÖØ-öø-ÿ][\wÀ-ÖØ-öø-ÿ .+#-]{0,126}?)"
    r"(?=\s+\b(?:usa|utiliza|uses?|requiere|requires?|depende|depends?)\b|[.,;:\n]|$)",
    re.IGNORECASE,
)
_PERSON_PATTERN = re.compile(
    r"\b(?:con|with|para|for)\s+([A-ZÁÉÍÓÚÑ][\wÀ-ÖØ-öø-ÿ'-]+(?:\s+[A-ZÁÉÍÓÚÑ][\wÀ-ÖØ-öø-ÿ'-]+){0,2})"
)
_TRIPLE_PATTERNS = (
    (re.compile(rf"({_NAME})\s+\b(?:usa|utiliza|uses?|using)\b\s+({_NAME})", re.I), "USES"),
    (
        re.compile(
            rf"({_NAME})\s+\b(?:requiere|requires?|necesita|needs?)\b\s+({_NAME})",
            re.I,
        ),
        "REQUIRES",
    ),
    (
        re.compile(
            rf"({_NAME})\s+\b(?:depende\s+de|depends?\s+on)\b\s+({_NAME})",
            re.I,
        ),
        "DEPENDS_ON",
    ),
    (
        re.compile(
            rf"({_NAME})\s+\b(?:conecta(?:do)?\s+(?:a|con)|connects?\s+to|connected\s+to)\b\s+({_NAME})",
            re.I,
        ),
        "CONNECTED_TO",
    ),
    (re.compile(rf"({_NAME})\s+\b(?:envía|envia|sends?)\b\s+({_NAME})", re.I), "SENDS"),
)
_BOUNDARY_PUNCTUATION = re.compile(r"[\n\r.,;:!?()]|\s[-\u2013\u2014]\s")
_CONTROL_CHARACTERS = re.compile(r"[\x00-\x1f\x7f]")
_EMAIL_OR_URL = re.compile(r"(?:\bhttps?://\S+|[\w.+-]+@[\w.-]+\.[A-Za-z]{2,})", re.I)


@dataclass(frozen=True, slots=True)
class ExtractedEntity:
    name: str
    type: str

    def __post_init__(self) -> None:
        if self.type not in _ENTITY_TYPES:
            raise ValueError("unsupported graph entity type")
        if not self.name or len(self.name) > MAX_ENTITY_NAME_CHARS:
            raise ValueError("graph entity name is out of range")


@dataclass(frozen=True, slots=True)
class ExtractedRelationship:
    subject: ExtractedEntity
    type: str
    object: ExtractedEntity
    weight: float = 1.0

    def __post_init__(self) -> None:
        if self.type not in _EDGE_TYPES:
            raise ValueError("unsupported graph relationship type")
        if not 0.0 < self.weight <= 10.0:
            raise ValueError("graph relationship weight is out of range")


@dataclass(frozen=True, slots=True)
class ExtractionResult:
    entities: tuple[ExtractedEntity, ...]
    relationships: tuple[ExtractedRelationship, ...]


class DeterministicGraphExtractor:
    """Bounded, offline entity extraction with no model or network dependency."""

    def extract(self, text: str) -> ExtractionResult:
        normalized = unicodedata.normalize("NFKC", text)
        if not normalized.strip() or _CONTROL_CHARACTERS.search(normalized):
            return ExtractionResult(entities=(), relationships=())
        normalized = _EMAIL_OR_URL.sub(" ", normalized)

        entities: dict[tuple[str, str], ExtractedEntity] = {}

        def add(name: str, entity_type: str) -> ExtractedEntity | None:
            clean = self._clean_name(name)
            if clean is None:
                return None
            key = (entity_type, clean.casefold())
            if key in entities:
                return entities[key]
            if len(entities) >= MAX_ENTITIES:
                return None
            entity = ExtractedEntity(name=clean, type=entity_type)
            entities[key] = entity
            return entity

        for match in _PROJECT_PATTERN.finditer(normalized):
            add(match.group(1), "project")
        for match in _PERSON_PATTERN.finditer(normalized):
            add(match.group(1), "person")
        for tool in _KNOWN_TOOLS:
            if re.search(rf"(?<!\w){re.escape(tool)}(?!\w)", normalized, re.I):
                add(tool, "tool")

        relationships: list[ExtractedRelationship] = []
        seen_relationships: set[tuple[str, str, str, str, str]] = set()
        for pattern, edge_type in _TRIPLE_PATTERNS:
            for match in pattern.finditer(normalized):
                subject_name = self._clean_name(match.group(1))
                object_name = self._clean_name(match.group(2))
                if subject_name is None or object_name is None:
                    continue
                subject = self._match_or_add(subject_name, entities, add)
                target = self._match_or_add(object_name, entities, add)
                if subject is None or target is None or subject == target:
                    continue
                key = (
                    subject.type,
                    subject.name.casefold(),
                    edge_type,
                    target.type,
                    target.name.casefold(),
                )
                if key in seen_relationships:
                    continue
                seen_relationships.add(key)
                relationships.append(
                    ExtractedRelationship(subject=subject, type=edge_type, object=target)
                )
                if len(relationships) == MAX_RELATIONSHIPS:
                    break
            if len(relationships) == MAX_RELATIONSHIPS:
                break
        return ExtractionResult(
            entities=tuple(entities.values()),
            relationships=tuple(relationships),
        )

    @staticmethod
    def _match_or_add(
        name: str,
        entities: dict[tuple[str, str], ExtractedEntity],
        add: Callable[[str, str], ExtractedEntity | None],
    ) -> ExtractedEntity | None:
        folded = name.casefold()
        for (_, candidate), entity in entities.items():
            if candidate == folded or candidate in folded or folded in candidate:
                return entity
        inferred_type = "project" if name.casefold().startswith(("jarvis", "aegis")) else "concept"
        return add(name, inferred_type)

    @staticmethod
    def _clean_name(value: str) -> str | None:
        candidate = _BOUNDARY_PUNCTUATION.split(value, maxsplit=1)[0]
        candidate = " ".join(candidate.strip(" \t\"'\u2018\u2019\u201c\u201d.=").split())
        candidate = re.sub(
            r"\s+\b(?:y|and|que|that|para|for|con|with|en|in|a|to|de|of)\b.*$",
            "",
            candidate,
            flags=re.I,
        ).strip()
        if (
            not candidate
            or len(candidate) > MAX_ENTITY_NAME_CHARS
            or _EMAIL_OR_URL.search(candidate)
            or not any(character.isalpha() for character in candidate)
        ):
            return None
        return candidate
