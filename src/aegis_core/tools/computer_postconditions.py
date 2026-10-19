"""Bounded AX postconditions; model statements and pixels cannot certify a goal."""

from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from typing import TYPE_CHECKING, Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, StrictBool, model_validator

from aegis_core.request_intent import mask_quoted_text

if TYPE_CHECKING:
    from aegis_core.tools.computer import ComputerPerception

PostconditionKind = Literal[
    "text_present",
    "window_title",
    "field_value",
    "field_contains",
    "control_state",
    "focused_control",
    "navigation_url",
]
_FIELD_ROLES = frozenset({"ComboBox", "SearchField", "TextArea", "TextField"})
_REPLACEMENT = re.compile(
    r"^(?:reemplaza\s+(?:el\s+)?(?:(?:contenido|valor)\s+del\s+)?campo\s+"
    r'(?:«(?P<es_target_q>[^»]{1,256})»|"(?P<es_target_d>[^\"]{1,256})"|'
    r"(?P<es_target>[^«»\"]{1,256}?))\s+por\s+(?:«(?P<es_value>[^»]{1,500})»|"
    r'"(?P<es_value_d>[^\"]{1,500})")|replace\s+(?:the\s+)?'
    r'(?:contents?\s+of\s+(?:the\s+)?)?"(?P<en_target>[^\"]{1,256})"\s+field\s+'
    r'with\s+"(?P<en_value>[^\"]{1,500})")[.!?]?$',
    re.IGNORECASE,
)


def _fold(value: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", value).casefold().split())


def requested_replacement(objective: str) -> tuple[str, str] | None:
    """Exact supported assignment grammar; quoted data/negations are not instructions."""
    match = _REPLACEMENT.fullmatch(objective.strip())
    if match is None:
        return None
    values = match.groupdict()
    target = next(
        (
            values[key]
            for key in ("es_target_q", "es_target_d", "es_target", "en_target")
            if values[key]
        ),
        None,
    )
    value = next(
        (values[key] for key in ("es_value", "es_value_d", "en_value") if values[key]), None
    )
    if (
        target is None
        or value is None
        or not target.isprintable()
        or not value.isprintable()
        or target != target.strip()
        or value != value.strip()
    ):
        return None
    return value, target


class ComputerPostcondition(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    kind: PostconditionKind
    target: str | None = Field(default=None, min_length=1, max_length=256)
    value: str | StrictBool | None = None
    attribute: Literal["selected", "expanded", "enabled"] | None = None

    @model_validator(mode="after")
    def closed_shape(self) -> ComputerPostcondition:
        if isinstance(self.value, str) and (
            not self.value or len(self.value) > 500 or not self.value.isprintable()
        ):
            raise ValueError("postcondition value is not bounded printable text")
        if self.target is not None and not self.target.isprintable():
            raise ValueError("postcondition target is not printable")
        if self.kind == "control_state":
            if self.target is None or self.attribute is None or type(self.value) is not bool:
                raise ValueError("control state requires a target, AX attribute and boolean")
        elif self.kind == "focused_control":
            if self.target is None or self.value is not None or self.attribute is not None:
                raise ValueError("focus requires only a target")
        elif self.kind in {"field_value", "field_contains"}:
            if not isinstance(self.value, str) or self.attribute is not None:
                raise ValueError("field postcondition requires literal text")
        elif (
            not isinstance(self.value, str) or self.target is not None or self.attribute is not None
        ):
            raise ValueError("visible text, title and navigation require only literal text")
        if self.kind == "navigation_url":
            assert isinstance(self.value, str)
            parsed = urlsplit(self.value)
            if (
                parsed.scheme != "https"
                or not parsed.hostname
                or parsed.username is not None
                or parsed.password is not None
                or len(self.value.encode()) > 512
            ):
                raise ValueError("navigation postcondition requires an exact HTTPS URL")
        return self


class ComputerPostconditionCheck(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    kind: PostconditionKind | Literal["action_performed"]
    satisfied: StrictBool
    reason_code: Literal[
        "observed",
        "expected_not_observed",
        "ambiguous_target",
        "observation_truncated",
        "sensitive_state",
        "value_unavailable",
    ]
    observed_sha256: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")
    previous_sha256: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")
    effect_observed: StrictBool | None = None


def postcondition_requires_change(condition: ComputerPostcondition, objective: str) -> bool:
    instructions = mask_quoted_text(_fold(objective))
    append = r"\b(?:añade|añadir|agrega|agregar|append|insert|inserta|insertar)\b"
    return condition.kind in {"field_contains", "field_value"} and bool(
        re.search(
            append
            if condition.kind == "field_value"
            else append + r"|\b(?:escribe|escribir|type)\b",
            instructions,
        )
    )


def postconditions_are_bound(
    conditions: tuple[ComputerPostcondition, ...],
    objective: str,
) -> bool:
    """The requested literal/target must come from the approved objective, not the screen."""
    if (
        len(conditions) > 6
        or len(
            json.dumps(
                [condition.model_dump(mode="json", exclude_none=True) for condition in conditions],
                ensure_ascii=False,
            ).encode()
        )
        > 4_096
    ):
        return False
    if not conditions:
        return True  # The controller will block goals with no inferable outcome.
    folded = _fold(objective)
    instructions = mask_quoted_text(folded)
    if re.match(
        r"^(?:explica|describe|traduce|translate|explain|qué significa|what does)\b", instructions
    ):
        return False
    if re.search(
        r"\b(?:no|not|never|nunca|don't|do not)\s+"
        r"(?:selecciona|seleccionar|select|expande|expand|activa|activar|enable|"
        r"abre|abrir|open|abras|escribe|escribir|type|reemplaza|replace)\b",
        instructions,
    ):
        return False
    replacement = requested_replacement(objective)
    if re.search(r"\b(?:reemplaza|reemplazar|replace)\b", instructions):
        if replacement is None:
            return False
        value, target = replacement
        if not any(
            condition.kind == "field_value"
            and condition.target is not None
            and _fold(condition.target) == _fold(target)
            and condition.value == value
            for condition in conditions
        ):
            return False
        if any(
            condition.kind in {"field_value", "field_contains"}
            and (
                condition.kind != "field_value"
                or condition.value != value
                or condition.target is None
                or _fold(condition.target) != _fold(target)
            )
            for condition in conditions
        ):
            return False
    # A check for an incidental label/title cannot replace an explicitly requested
    # state change, nor can "false" certify a requested activation/selection.
    operations = (
        ("selected", True, r"\b(?:selecciona|seleccionar|select)\b"),
        ("selected", False, r"\b(?:deselecciona|deseleccionar|unselect|deselect)\b"),
        ("expanded", True, r"\b(?:expande|expandir|expand)\b"),
        ("expanded", False, r"\b(?:contrae|collapse)\b"),
        ("enabled", True, r"\b(?:activa|activar|enable|habilita)\b"),
        ("enabled", False, r"\b(?:desactiva|disable|deshabilita)\b"),
    )
    for attribute, expected, pattern in operations:
        for operation in re.finditer(pattern, instructions):
            operand = folded[operation.end() :].strip()
            quoted = re.match(r'^["“«\']([^"”»\']+)["”»\']', operand)
            if quoted:
                operand = quoted[1]
            else:
                operand = re.split(r"[;.!?]|\b(?:y|and|para|then|después|luego)\b", operand)[0]
            if not any(
                condition.kind == "control_state"
                and condition.attribute == attribute
                and condition.value is expected
                and condition.target is not None
                and re.search(rf"(?<!\w){re.escape(_fold(condition.target))}(?!\w)", operand)
                for condition in conditions
            ):
                return False
    for condition in conditions:
        literals = (condition.target, condition.value if isinstance(condition.value, str) else None)
        for literal in literals:
            if literal is not None and _fold(literal) not in folded:
                return False
        if condition.kind == "text_present" and not re.search(
            r"\b(?:comprueba|verifica|check|verify)\b.{0,100}\b"
            r"(?:texto|text|visible|aparece|muestra|contains|contiene|shown)\b",
            instructions,
        ):
            return False
        if condition.kind == "focused_control" and not re.search(
            r"\b(?:enfoca|enfocar|focus)\b",
            instructions,
        ):
            return False
        if condition.kind == "control_state":
            intents = {
                "selected": r"\b(?:selecciona|seleccionar|selected|select|deselecciona|"
                r"deseleccionar|unselect|deselect)\b",
                "expanded": r"\b(?:expande|expandir|expand|abre|abrir|cierra|close|collapse)\b",
                "enabled": r"\b(?:activa|activar|enable|desactiva|disable|habilita|"
                r"deshabilita|habilitado)\b",
            }
            if not re.search(intents[condition.attribute], instructions):
                return False
        if condition.kind == "window_title" and not re.search(
            r"\b(?:ventana|window|titulo|título|title|abre|abrir|open|navega|navigate)\b",
            instructions,
        ):
            return False
        if condition.kind == "window_title":
            value = re.escape(_fold(condition.value))
            if not re.search(
                rf"\b(?:ventana|window|titulo|título|title|abre|abrir|open|navega|navigate)\b"
                rf"\s+(?:(?:la|el|the|ventana|window|con|título|title|a)\s+)*[\"'“«]?{value}",
                folded,
            ):
                return False
        if condition.kind in {"field_value", "field_contains"} and not re.search(
            r"\b(?:escribe|escribir|type|reemplaza|replace|establece|set|busca|find|"
            r"comprueba|verifica|check|verify|añade|agrega|append|insert|inserta)\b",
            instructions,
        ):
            return False
        if condition.kind == "navigation_url" and not re.search(
            r"\b(?:abre|abrir|open|navega|navigate|visita|visit|comprueba|verifica|check|verify)\b",
            instructions,
        ):
            return False
    return True


def check_postcondition(
    condition: ComputerPostcondition,
    perception: ComputerPerception,
    *,
    previous: ComputerPerception | None = None,
    require_change: bool = False,
) -> ComputerPostconditionCheck:
    reason: str = "expected_not_observed"
    observed: object | None = None
    satisfied = False
    previous_value = None
    effect_observed = None
    if perception.secure_content:
        reason = "sensitive_state"
    elif perception.truncated:
        reason = "observation_truncated"
    elif condition.kind == "navigation_url":
        observed = perception.current_url
        satisfied = observed is not None and observed == condition.value
        if observed is None:
            reason = "value_unavailable"
    elif condition.kind == "window_title":
        satisfied = condition.value in perception.windows
        observed = condition.value if satisfied else None
    elif condition.kind == "text_present":
        satisfied = any(
            item.source == "accessibility" and not item.sensitive and item.text == condition.value
            for item in perception.items
        )
        observed = condition.value if satisfied else None
    else:
        candidates = [
            item
            for item in perception.items
            if item.source == "accessibility"
            and not item.sensitive
            and (
                _fold(item.label or item.text) == _fold(condition.target)
                if condition.target is not None
                else item.focused
            )
        ]
        if condition.kind in {"field_value", "field_contains"}:
            candidates = [item for item in candidates if item.role in _FIELD_ROLES]
        if len(candidates) > 1:
            reason = "ambiguous_target"
        elif candidates:
            item = candidates[0]
            if condition.kind in {"field_value", "field_contains"}:
                observed = item.value
                if observed is None or item.value_truncated:
                    reason = "value_unavailable"
                else:
                    assert isinstance(condition.value, str)
                    satisfied = (
                        observed == condition.value
                        if condition.kind == "field_value"
                        else condition.value in observed
                    )
                    if require_change:
                        effect_observed = False
                        prior = (
                            [
                                candidate
                                for candidate in previous.items
                                if candidate.source == "accessibility"
                                and not candidate.sensitive
                                and candidate.role == item.role
                                and _fold(candidate.label or candidate.text)
                                == _fold(item.label or item.text)
                                and (condition.target is not None or candidate.focused)
                            ]
                            if previous is not None
                            and not previous.truncated
                            and not previous.secure_content
                            else []
                        )
                        if len(prior) != 1 or prior[0].value is None or prior[0].value_truncated:
                            reason = "value_unavailable"
                        else:
                            previous_value = prior[0].value
                            effect_observed = observed != previous_value and (
                                observed.count(condition.value)
                                > previous_value.count(condition.value)
                                if condition.kind == "field_contains"
                                else True
                            )
                        satisfied = satisfied and effect_observed
            elif condition.kind == "focused_control":
                observed, satisfied = item.focused, item.focused
            else:
                assert condition.attribute is not None
                observed = getattr(item, condition.attribute)
                satisfied = observed is not None and observed is condition.value
                if observed is None:
                    reason = "value_unavailable"
    if satisfied:
        reason = "observed"
    return ComputerPostconditionCheck(
        kind=condition.kind,
        satisfied=satisfied,
        reason_code=reason,
        observed_sha256=(
            hashlib.sha256(json.dumps(observed, ensure_ascii=False).encode()).hexdigest()
            if observed is not None
            else None
        ),
        previous_sha256=(
            hashlib.sha256(json.dumps(previous_value, ensure_ascii=False).encode()).hexdigest()
            if previous_value is not None
            else None
        ),
        effect_observed=effect_observed,
    )
