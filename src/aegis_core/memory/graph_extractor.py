from __future__ import annotations

import re
import unicodedata
from collections.abc import Callable
from dataclasses import dataclass
from ipaddress import ip_address

MAX_ENTITIES = 16
MAX_RELATIONSHIPS = 24
MAX_ENTITY_NAME_CHARS = 128

_ENTITY_TYPES = frozenset(
    {"person", "project", "document", "tool", "concept", "device", "sensor", "location"}
)
_EDGE_TYPES = frozenset(
    {
        "CONNECTED_TO",
        "DEPENDS_ON",
        "MENTIONS",
        "OWNED_BY",
        "REQUIRES",
        "SENDS",
        "USES",
    }
)
_SENSITIVE_PROPERTY_NAMES = frozenset({"ip_address", "mac_address", "api_token"})
_DEVICE_TERMS = (
    "android tv",
    "foco inteligente",
    "smart tv",
    "smartphone",
    "televisor",
    "teléfono",
    "telefono",
    "tv",
)
_SENSOR_TERMS = ("sensor", "termómetro", "termometro", "cámara", "camara")
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
_PHYSICAL_LOCATION_PATTERN = re.compile(
    r"\b(?:el|la|the)\s+([^\n,.;:]{2,96}?)\s+"
    r"(?:está|esta|se\s+encuentra|is\s+located|is)\s+en\s+"
    r"(?:el|la|the)?\s*([^\n,.;:]{2,128}?)"
    r"(?=\s+(?:y|and)\s+(?:su|its)\s+(?:ip|mac|token)\b|[,.;:\n]|$)",
    re.I,
)
_OWNERSHIP_PATTERN = re.compile(
    r"\b(?:el|la|the)\s+([^\n,.;:]{2,96}?)\s+"
    r"(?:pertenece\s+a|es\s+de|belongs\s+to|is\s+owned\s+by)\s+"
    r"([^\n,.;:]{2,96}?)(?=\s+(?:y|and)\s+(?:su|its)\b|[,.;:\n]|$)",
    re.I,
)
_IP_PROPERTY_PATTERN = re.compile(
    r"\b(?:ip|direcci[oó]n\s+ip)\s*(?:es|=|:)?\s*"
    r"((?:\d{1,3}\.){3}\d{1,3})\b",
    re.I,
)
_MAC_PROPERTY_PATTERN = re.compile(
    r"\bmac\s*(?:es|=|:)?\s*([0-9a-f]{2}(?::[0-9a-f]{2}){5})\b",
    re.I,
)
_TOKEN_PROPERTY_PATTERN = re.compile(
    r"\b(?:api[_ -]?token|token)\s*(?:es|=|:)?\s*([A-Za-z0-9._~-]{8,256})\b",
    re.I,
)
_BOUNDARY_PUNCTUATION = re.compile(r"[\n\r.,;:!?()]|\s[-\u2013\u2014]\s")
_CONTROL_CHARACTERS = re.compile(r"[\x00-\x1f\x7f]")
_EMAIL_OR_URL = re.compile(r"(?:\bhttps?://\S+|[\w.+-]+@[\w.-]+\.[A-Za-z]{2,})", re.I)


@dataclass(frozen=True, slots=True)
class ExtractedEntity:
    name: str
    type: str
    properties: tuple[tuple[str, str], ...] = ()

    def __post_init__(self) -> None:
        if self.type not in _ENTITY_TYPES:
            raise ValueError("unsupported graph entity type")
        if not self.name or len(self.name) > MAX_ENTITY_NAME_CHARS:
            raise ValueError("graph entity name is out of range")
        keys = [key for key, _ in self.properties]
        if (
            len(keys) != len(set(keys))
            or any(key not in _SENSITIVE_PROPERTY_NAMES for key in keys)
            or any(not value or len(value) > 512 for _, value in self.properties)
        ):
            raise ValueError("graph entity properties are invalid")


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

        def add(
            name: str,
            entity_type: str,
            properties: tuple[tuple[str, str], ...] = (),
        ) -> ExtractedEntity | None:
            clean = self._clean_name(name, preserve_connectors=entity_type == "location")
            if clean is None:
                return None
            key = (entity_type, clean.casefold())
            if key in entities:
                current = entities[key]
                merged = dict(current.properties)
                merged.update(properties)
                updated = ExtractedEntity(
                    name=current.name,
                    type=current.type,
                    properties=tuple(sorted(merged.items())),
                )
                entities[key] = updated
                return updated
            if len(entities) >= MAX_ENTITIES:
                return None
            entity = ExtractedEntity(name=clean, type=entity_type, properties=properties)
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
        location_matches = tuple(_PHYSICAL_LOCATION_PATTERN.finditer(normalized))
        ownership_matches = tuple(_OWNERSHIP_PATTERN.finditer(normalized))
        physical_subjects = {
            self._clean_name(match.group(1))
            for match in (*location_matches, *ownership_matches)
            if self._physical_type(match.group(1)) in {"device", "sensor"}
        }
        physical_subjects.discard(None)
        sensitive_properties = (
            self._extract_sensitive_properties(normalized)
            if len(physical_subjects) == 1
            else ()
        )
        for match in location_matches:
            raw_device, raw_location = match.groups()
            entity_type = self._physical_type(raw_device)
            if entity_type not in {"device", "sensor"}:
                continue
            device = add(raw_device, entity_type, sensitive_properties)
            location = add(raw_location, "location")
            if device is not None and location is not None:
                self._append_relationship(
                    relationships,
                    seen_relationships,
                    device,
                    "CONNECTED_TO",
                    location,
                )
        for match in ownership_matches:
            raw_device, raw_owner = match.groups()
            entity_type = self._physical_type(raw_device)
            if entity_type not in {"device", "sensor"}:
                continue
            device = add(raw_device, entity_type, sensitive_properties)
            owner = add(raw_owner, "person")
            if device is not None and owner is not None:
                self._append_relationship(
                    relationships,
                    seen_relationships,
                    device,
                    "OWNED_BY",
                    owner,
                )

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
                self._append_relationship(
                    relationships,
                    seen_relationships,
                    subject,
                    edge_type,
                    target,
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
    def _append_relationship(
        relationships: list[ExtractedRelationship],
        seen: set[tuple[str, str, str, str, str]],
        subject: ExtractedEntity,
        edge_type: str,
        target: ExtractedEntity,
    ) -> None:
        key = (
            subject.type,
            subject.name.casefold(),
            edge_type,
            target.type,
            target.name.casefold(),
        )
        if key in seen or len(relationships) >= MAX_RELATIONSHIPS:
            return
        seen.add(key)
        relationships.append(
            ExtractedRelationship(subject=subject, type=edge_type, object=target)
        )

    @staticmethod
    def _physical_type(value: str) -> str:
        folded = value.casefold()
        if any(term in folded for term in _SENSOR_TERMS):
            return "sensor"
        if any(term in folded for term in _DEVICE_TERMS):
            return "device"
        return "concept"

    @staticmethod
    def _extract_sensitive_properties(text: str) -> tuple[tuple[str, str], ...]:
        properties: dict[str, str] = {}
        for key, pattern in (
            ("ip_address", _IP_PROPERTY_PATTERN),
            ("mac_address", _MAC_PROPERTY_PATTERN),
            ("api_token", _TOKEN_PROPERTY_PATTERN),
        ):
            match = pattern.search(text)
            if match is not None:
                value = match.group(1)
                if key == "ip_address":
                    try:
                        parsed = ip_address(value)
                    except ValueError:
                        continue
                    if not parsed.is_private or parsed.is_loopback:
                        continue
                    value = parsed.compressed
                properties[key] = value
        return tuple(sorted(properties.items()))

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
        folded_name = name.casefold()
        physical_type = DeterministicGraphExtractor._physical_type(name)
        if physical_type in {"device", "sensor"}:
            inferred_type = physical_type
        elif any(marker in folded_name for marker in ("oficina", "office", "casa", "home")):
            inferred_type = "location"
        else:
            inferred_type = "project" if folded_name.startswith(("jarvis", "aegis")) else "concept"
        return add(name, inferred_type)

    @staticmethod
    def _clean_name(value: str, *, preserve_connectors: bool = False) -> str | None:
        candidate = _BOUNDARY_PUNCTUATION.split(value, maxsplit=1)[0]
        candidate = " ".join(candidate.strip(" \t\"'\u2018\u2019\u201c\u201d.=").split())
        if not preserve_connectors:
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
