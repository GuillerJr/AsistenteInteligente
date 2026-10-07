"""Conservative boundaries for deterministic learning from the owner's own words.

This is deliberately not a general language parser. Persistent facts require an
explicit first-person assertion or direct preference command. Referenced text,
examples and uncertain assertions are useful conversation input, not evidence
about the owner. Ambiguous statements remain in the conversation only.
"""

from __future__ import annotations

import re

_REFERENCE = "\x00"
_QUOTES = {'"': '"', "'": "'", "“": "”", "\u2018": "\u2019", "«": "»", "„": "“"}
_PREFIX = re.compile(
    r"^\s*(?:jarvis[, ]+)?"
    r"(?:(?:sobre m[ií]|acerca de m[ií]|en cuanto a m[ií])\s*[:,]\s*)?"
    r"(?:(?:en realidad|para aclarar|correcci[oó]n)\s*[:,]?\s+)?"
    r"(?:(?:recuerda|ten en cuenta|guarda|anota|quiero que recuerdes)\s+que\s+"
    r"(?=(?:yo\s+)?(?:me|mi|trabajo|estoy|estudio|odio|detesto|prefiero)\b))?"
    r"(?:yo\s+)?",
    re.IGNORECASE,
)
_OWNER_START = (
    r"(?:me (?:llamo|gusta|encanta|interesa)|mi (?:nombre|proyecto|objetivo)|"
    r"trabajo|estoy (?:aprendiendo|trabajando)|estudio|odio|detesto|prefiero|"
    r"(?:no|nunca|jam[aá]s) (?:me|trabajo|estoy|estudio)|"
    r"dame|h[aá]blame|resp[oó]ndeme|responde|explica|s[eé]|suena|ve al grano|"
    r"quiero (?:respuestas|que hables|un tono)|"
    r"no (?:hagas|termines|repitas|seas)|sin preguntas|haz preguntas|"
    r"preg[uú]ntame|puedes hacer preguntas|evita repetir|"
    r"(?:recuerda|ten en cuenta) que (?:tengo que|debo|quiero))\b"
)
_START = re.compile(rf"^{_OWNER_START}", re.IGNORECASE)
_CLAUSE_BOUNDARY = re.compile(
    rf"(?:\s*[,;]\s*(?:(?:y|pero|sino que)\s+)?|\s+(?:y|pero|sino que)\s+)"
    rf"(?=(?:yo\s+)?{_OWNER_START})",
    re.IGNORECASE,
)
_UNCERTAIN = re.compile(
    r"\b(?:si|quiz[aá]s?|tal vez|supongamos|imaginemos|imagina|"
    r"hipot[eé]ticamente|por ejemplo|un ejemplo|en el ejemplo|"
    r"(?:en|dentro de) (?:la|esta|una|el|este|un) "
    r"(?:historia|texto|ficci[oó]n|simulaci[oó]n|juego|rol)|"
    r"el personaje|fingiendo|finjo|es falso|es mentira|"
    r"no es cierto|no significa|no quiere decir|no es mi nombre|"
    r"seg[uú]n el ejemplo|dice|dijo|afirma|escribi[oó])\b",
    re.IGNORECASE,
)
_EXTERNAL_FRAME = re.compile(
    r"^(?:(?:por favor|jarvis)[, ]+)?"
    r"(?:(?:puedes|podr[ií]as|quiero que|necesito que)\s+)?"
    r"(?:tradu(?:ce|cir|zcas|zca)|reescrib(?:e|ir|as)|repite|repetir|"
    r"interpreta|analiza|resume|resumir|corrige|corrigir|copia|copiar|"
    r"explica (?:esta|la|el|estas)|"
    r"(?:dame|escribe|crea|inventa) (?:un|una) (?:ejemplo|personaje|historia)|"
    r"(?:(?:el|la|este|esta|un|una)\s+)?"
    r"(?:ejemplo|texto|frase|cita|di[aá]logo|personaje|historia|mensaje|"
    r"informaci[oó]n|perfil|datos|documento|correo|ficha)\b|"
    r".{0,80}\b(?:dice|dijo|escribi[oó]|afirma)\b|.{1,80}\bexplica\b)",
    re.IGNORECASE,
)


def explicit_owner_clauses(text: str) -> tuple[str, ...]:
    """Return only attributable, asserted clauses; never infer an implicit subject."""
    if not text:
        return ()
    masked = _mask_references(text)
    clauses: list[str] = []
    external = False
    # Retain terminators: a question is not evidence, even if followed by a fact.
    for match in re.finditer(r"([^.!?;\n]+)([.!?;\n]|$)", masked):
        sentence, terminator = match.groups()
        sentence = sentence.strip()
        if not sentence:
            continue
        if _EXTERNAL_FRAME.match(sentence):
            external = True
        if (
            external
            or terminator == "?"
            or "¿" in sentence
            or _REFERENCE in sentence
            or _UNCERTAIN.search(sentence)
        ):
            continue
        # A quote cannot be deleted and thereby turn its surrounding text into
        # a new owner assertion (or silently truncate a quoted fact's value).
        for clause in _CLAUSE_BOUNDARY.split(sentence):
            clause = _PREFIX.sub("", clause.strip(), count=1)
            if _REFERENCE not in clause and _START.match(clause):
                clauses.append(clause)
    return tuple(clauses)


def is_direct_owner_command(text: str) -> bool:
    """Exclude quoted reset/forget instructions from destructive local commands."""
    return (
        bool(text.strip())
        and _mask_references(text) == text
        and not any(marker in text for marker in ("?", "¿"))
    )


def _mask_references(text: str) -> str:
    # Structured source blocks, Markdown code/quotes and paired Unicode quotes.
    text = re.sub(
        r"<([a-z][\w:-]*)\b[^>]*>[\s\S]*?(?:</\1\s*>|$)",
        _REFERENCE,
        text,
        flags=re.IGNORECASE,
    )
    text = re.sub(r"```[\s\S]*?(?:```|$)|~~~[\s\S]*?(?:~~~|$)", _REFERENCE, text)
    text = re.sub(r"(?m)^\s*>[^\n]*", _REFERENCE, text)
    text = re.sub(r"(?m)^(?: {4}|\t)[^\n]*", _REFERENCE, text)
    text = re.sub(r"`[^`\n]*(?:`|$)", _REFERENCE, text)
    result: list[str] = []
    index = 0
    while index < len(text):
        character = text[index]
        closing = _QUOTES.get(character)
        if closing is None or (
            character == "'"
            and index > 0
            and text[index - 1].isalnum()
            and index + 1 < len(text)
            and text[index + 1].isalnum()
        ):
            result.append(character)
            index += 1
            continue
        end = text.find(closing, index + 1)
        result.append(_REFERENCE)
        index = end + 1 if end >= 0 else len(text)
    return "".join(result)
