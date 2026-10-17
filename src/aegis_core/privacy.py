from __future__ import annotations

import re
from collections.abc import Callable, Iterable
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class RemoteRedaction:
    text: str
    categories: frozenset[str]


# A replacement is either a fixed substitution or a validator that returns the
# replacement for a genuine match and None to keep the text (checksums, ranges).
_Replacement = str | Callable[[re.Match[str]], str | None]


def _digits(value: str) -> str:
    return "".join(character for character in value if character.isdigit())


def _luhn_valid(number: str) -> bool:
    total = 0
    for index, character in enumerate(reversed(number)):
        digit = int(character)
        if index % 2:
            digit = digit * 2 - 9 if digit > 4 else digit * 2
        total += digit
    return total % 10 == 0


def _payment_card(match: re.Match[str]) -> str | None:
    number = _digits(match.group(0))
    # Major networks start with 3-6; this also keeps epoch timestamps and order ids.
    if 13 <= len(number) <= 19 and number[0] in "3456" and _luhn_valid(number):
        return "[REDACTED_CARD]"
    return None


def _iban(match: re.Match[str]) -> str | None:
    compact = match.group(0).replace(" ", "").upper()
    if not 15 <= len(compact) <= 34:
        return None
    rearranged = compact[4:] + compact[:4]
    numeric = "".join(str(int(character, 36)) for character in rearranged)
    return "[REDACTED_BANK_ACCOUNT]" if int(numeric) % 97 == 1 else None


def _ecuador_cedula_valid(number: str) -> bool:
    if len(number) != 10 or not number.isdigit():
        return False
    province = int(number[:2])
    if not (1 <= province <= 24 or province == 30) or int(number[2]) >= 6:
        return False
    total = 0
    for index, character in enumerate(number[:9]):
        digit = int(character) * (2 if index % 2 == 0 else 1)
        total += digit - 9 if digit > 9 else digit
    return (10 - total % 10) % 10 == int(number[9])


def _ecuador_id(match: re.Match[str]) -> str | None:
    number = match.group(0)
    if len(number) == 13:
        # RUC of a natural person: valid cédula followed by an establishment code.
        if number.endswith("001") and _ecuador_cedula_valid(number[:10]):
            return "[REDACTED_NATIONAL_ID]"
        return None
    return "[REDACTED_NATIONAL_ID]" if _ecuador_cedula_valid(number) else None


def _us_ssn(match: re.Match[str]) -> str | None:
    area, group, serial = match.group(0).split("-")
    if area in {"000", "666"} or area.startswith("9") or group == "00" or serial == "0000":
        return None
    return "[REDACTED_NATIONAL_ID]"


_DNI_LETTERS = "TRWAGMYFPDXBNJZSQVHLCKE"


def _spanish_id(match: re.Match[str]) -> str | None:
    value = match.group(0).upper()
    body = value[:-1]
    if body[0] in "XYZ":
        body = str("XYZ".index(body[0])) + body[1:]
    return "[REDACTED_NATIONAL_ID]" if _DNI_LETTERS[int(body) % 23] == value[-1] else None


def _coordinates(match: re.Match[str]) -> str | None:
    latitude, longitude = float(match.group(1)), float(match.group(2))
    if -90 <= latitude <= 90 and -180 <= longitude <= 180:
        return "[REDACTED_LOCATION]"
    return None


_ISO_DATE = re.compile(r"\d{4}-\d{2}-\d{2}")


def _phone(match: re.Match[str]) -> str | None:
    value = match.group(0)
    digits = _digits(value)
    # Dates and short numeric runs (ports, versions, counts) are not phone numbers.
    if _ISO_DATE.fullmatch(value.strip()) or not 7 <= len(digits) <= 15:
        return None
    return "[REDACTED_PHONE]"


_PATTERNS: tuple[tuple[str, re.Pattern[str], _Replacement], ...] = (
    (
        "private_key",
        re.compile(
            r"-----BEGIN [A-Z0-9 ]*PRIVATE KEY-----.*?"
            r"-----END [A-Z0-9 ]*PRIVATE KEY-----",
            re.DOTALL,
        ),
        "[REDACTED_PRIVATE_KEY]",
    ),
    (
        # Must run before the e-mail rule, which would otherwise keep the user name.
        "url_credentials",
        re.compile(r"\b([a-z][a-z0-9+.-]*://)[^\s/:@]+:[^\s/@]+@", re.IGNORECASE),
        r"\1[REDACTED_CREDENTIAL]@",
    ),
    (
        "credential",
        re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.eyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}"),
        "[REDACTED_CREDENTIAL]",
    ),
    (
        "credential",
        re.compile(
            r"\b(?:nvapi-|sk-|sk_(?:live|test)_|rk_(?:live|test)_|gh[pousr]_|github_pat_|"
            r"glpat-|xox[baprs]-|AIza|ya29\.)"
            r"[A-Za-z0-9._-]{8,}"
        ),
        "[REDACTED_CREDENTIAL]",
    ),
    (
        "credential",
        re.compile(r"\b(?:hf|npm)_[A-Za-z0-9]{20,}\b"),
        "[REDACTED_CREDENTIAL]",
    ),
    (
        "credential",
        re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
        "[REDACTED_CREDENTIAL]",
    ),
    (
        "authorization",
        re.compile(r"\bBearer\s+[A-Za-z0-9._~+/=-]{8,}", re.IGNORECASE),
        "Bearer [REDACTED_CREDENTIAL]",
    ),
    (
        "secret_assignment",
        re.compile(
            r"\b(api[_-]?key|access[_-]?token|token|password|passwd|secret|"
            r"contrase(?:ñ|n)a|clave)\s*[:=]\s*['\"]?[^\s'\",;]{4,}",
            re.IGNORECASE,
        ),
        r"\1=[REDACTED_SECRET]",
    ),
    (
        "secret_query",
        re.compile(
            r"([?&](?:api[_-]?key|access[_-]?token|token|key)=)[^&\s]+",
            re.IGNORECASE,
        ),
        r"\1[REDACTED_SECRET]",
    ),
    (
        "email",
        re.compile(r"(?<![\w.+-])[\w.+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}(?!\w)"),
        "[REDACTED_EMAIL]",
    ),
    # Identifiers with checksums run before the generic phone rule, which would
    # otherwise swallow them with the wrong category.
    (
        "payment_card",
        re.compile(r"(?<![\d.])\d(?:[ -]?\d){12,18}(?![\d.])"),
        _payment_card,
    ),
    (
        "bank_account",
        re.compile(r"\b[A-Z]{2}\d{2}(?: ?[A-Z0-9]){11,30}\b"),
        _iban,
    ),
    (
        "national_id",
        re.compile(r"(?<![\d.])(?:\d{13}|\d{10})(?![\d.])"),
        _ecuador_id,
    ),
    (
        "national_id",
        re.compile(r"(?<![\d-])\d{3}-\d{2}-\d{4}(?![\d-])"),
        _us_ssn,
    ),
    (
        "national_id",
        re.compile(r"\b(?:\d{8}|[XYZ]\d{7})[A-Z]\b", re.IGNORECASE),
        _spanish_id,
    ),
    (
        "location",
        re.compile(r"(?<![\d.])(-?\d{1,2}\.\d{4,}),\s*(-?\d{1,3}\.\d{4,})(?![\d.])"),
        _coordinates,
    ),
    (
        "phone",
        re.compile(
            r"(?<!\w)(?:\+\d{1,3}[ -]?)?(?:\(?\d{2,4}\)?[ -]){2,4}\d{2,4}(?!\w)"
            r"|(?<![\w+])\+\d{8,15}(?!\d)"
            r"|(?<![\d.])09\d{8}(?![\d.])"
        ),
        _phone,
    ),
    (
        "postal_address",
        re.compile(
            r"\b(?:Av(?:enida)?\.?|Calle|Cl\.|Carrera|Cra\.|Pasaje|Psje\.|Diagonal|"
            r"Transversal|Callej[oó]n|Boulevard|Blvd\.|Malec[oó]n|Urb(?:anizaci[oó]n)?\.?|"
            r"Cdla\.?|Ciudadela)\s+[\wÁÉÍÓÚÑáéíóúñ.\- ]{2,60}?"
            r"(?:\s+y\s+[\wÁÉÍÓÚÑáéíóúñ.\- ]{2,40}?)?"
            r"[\s,]+(?:#\s?\d+|N(?:o|ro|°|º)\.?\s?\d+|[NSE]\d{1,3}-\d{1,4}|Oe?\d{1,3}-\d{1,4}|"
            r"Mz\.?\s?\d+|Manzana\s+\d+|Villa\s+\d+|Solar\s+\d+)"
            r"(?:[\s,]+(?:Mz\.?\s?\d+|Villa\s+\d+|Solar\s+\d+|Dpto\.?\s?\w+|Piso\s+\d+))*"
            r"(?:\s+y\s+[A-ZÁÉÍÓÚÑ][\wáéíóúñ]+)?",
            re.IGNORECASE,
        ),
        "[REDACTED_ADDRESS]",
    ),
    (
        "postal_address",
        re.compile(
            r"\b\d{1,6}\s+(?:[NSEW]\.?\s+)?[A-Z][a-z]+(?:\s+[A-Z][a-z]+){0,3}\s+"
            r"(?:Street|St\.|Avenue|Ave\.|Road|Rd\.|Boulevard|Blvd\.|Lane|Ln\.|Drive|Dr\.|"
            r"Court|Ct\.|Way|Place|Pl\.)(?:,?\s+(?:Apt|Suite|Unit)\.?\s*\w+)?"
        ),
        "[REDACTED_ADDRESS]",
    ),
    (
        "postal_address",
        re.compile(r"\b(?:Mz\.?|Manzana)\s?\d+[\s,]+(?:Villa|Solar)\s+\d+", re.IGNORECASE),
        "[REDACTED_ADDRESS]",
    ),
    (
        "local_user",
        re.compile(r"(?<!\w)/(?:Users|home)/[^/\s]+"),
        lambda match: match.group(0).rsplit("/", 1)[0] + "/[REDACTED_USER]",
    ),
    (
        "local_user",
        re.compile(r"\b([A-Za-z]:\\Users\\)[^\\\s]+", re.IGNORECASE),
        r"\1[REDACTED_USER]",
    ),
)


_NAME = r"[A-ZÁÉÍÓÚÑ][a-záéíóúñü]+(?:\s+(?:de\s+(?:la\s+|los\s+)?)?[A-ZÁÉÍÓÚÑ][a-záéíóúñü]+){0,3}"
_RELATIONS = (
    r"esposa|esposo|marido|mujer|pareja|novia|novio|hija|hijo|madre|mam[aá]|padre|pap[aá]|"
    r"hermana|hermano|abuela|abuelo|t[ií]a|t[ií]o|prima|primo|sobrina|sobrino|suegra|suegro|"
    r"cuñada|cuñado|amiga|amigo|jefa|jefe|compañera|compañero|vecina|vecino|doctora|doctor|"
    r"wife|husband|partner|girlfriend|boyfriend|daughter|son|mother|mom|father|dad|sister|"
    r"brother|grandmother|grandfather|aunt|uncle|cousin|friend|boss|coworker|neighbor"
)
# Cues that make the following capitalised words a private person's name.
_NAME_CUES = (
    re.compile(
        r"(?i:\b(?:me\s+llamo|mi\s+nombre\s+es|my\s+name\s+is|"
        r"(?:mi|mis|my)\s+(?:" + _RELATIONS + r")(?:\s+se\s+llama)?)\s+)(" + _NAME + r")"
    ),
    re.compile(
        r"\b(?:Sr|Sra|Srta|Dr|Dra|Lic|Ing|Mr|Mrs|Ms)\.\s*(" + _NAME + r")"
        r"|\b(?:Don|Doña)\s+(" + _NAME + r")"
    ),
)
_PERSONAL_CONTEXT = re.compile(
    r"(?i)\b(?:mi|mis|me|conmigo|nosotros|nuestra|nuestro|my|me|our|us)\b"
)
_NEVER_REDACTED_NAMES = frozenset({"jarvis", "siri", "apple", "nvidia"})


def cue_based_person_names(text: str) -> tuple[str, ...]:
    """Names introduced by first-person or relationship cues ("mi esposa Ana")."""
    names: list[str] = []
    for pattern in _NAME_CUES:
        for match in pattern.finditer(text):
            name = next((group for group in match.groups() if group), None)
            if name and name.casefold() not in _NEVER_REDACTED_NAMES and name not in names:
                names.append(name)
    return tuple(names)


def has_personal_context(text: str) -> bool:
    return _PERSONAL_CONTEXT.search(text) is not None


def _redact_names(text: str, names: Iterable[str]) -> tuple[str, int]:
    count = 0
    # Longest first so "Ana María López" is not left as "[REDACTED_NAME] López".
    for name in sorted({name.strip() for name in names if name.strip()}, key=len, reverse=True):
        if len(name) < 2 or name.casefold() in _NEVER_REDACTED_NAMES:
            continue
        text, replaced = re.subn(
            r"(?<![\wÁÉÍÓÚÑáéíóúñ])" + re.escape(name) + r"(?![\wÁÉÍÓÚÑáéíóúñ])",
            "[REDACTED_NAME]",
            text,
        )
        count += replaced
    return text, count


_SENSITIVE_TOPICS: tuple[tuple[str, re.Pattern[str]], ...] = (
    (
        "health",
        re.compile(
            r"(?i)\b(?:diagn[oó]stic\w*|diagnosed|enfermedad|c[aá]ncer|cancer|vih|hiv|sida|aids|"
            r"diabetes|depresi[oó]n|depression|ansiedad|anxiety|terapia|therapy|psiquiatr\w+|"
            r"psychiatr\w+|psic[oó]log[oa]|medicamento\w*|medication\w*|receta m[eé]dica|"
            r"embarazad[oa]|embarazo|pregnan\w+|aborto|abortion|s[ií]ntomas|symptoms|cirug[ií]a|"
            r"surgery|hospitalizad[oa]|salud mental|mental health|suicid\w*|autolesi\w+|"
            r"self[- ]harm|infecci[oó]n de transmisi[oó]n sexual|sexually transmitted)\b"
        ),
    ),
    (
        "personal_finance",
        re.compile(
            r"(?i)\b(?:mi|mis|my)\s+(?:sueldo|salario|salary|deuda|deudas|debt|debts|saldo|"
            r"balance|cuenta bancaria|bank account|pr[eé]stamo|loan|hipoteca|mortgage|"
            r"puntaje crediticio|credit score|ingresos|income|patrimonio|net worth)\b"
        ),
    ),
    (
        "sexual_orientation",
        re.compile(
            r"(?i)\b(?:soy|i am|i'm)\s+(?:gay|lesbiana|lesbian|bisexual|trans|transg[eé]nero|"
            r"transgender|asexual)\b|\b(?:orientaci[oó]n sexual|sexual orientation)\b"
        ),
    ),
    (
        "criminal_record",
        re.compile(
            r"(?i)\b(?:antecedentes penales|criminal record|me arrestaron|i was arrested|"
            r"me detuvieron|estuve preso|estuve en la c[aá]rcel|i was in jail|"
            r"denuncia en mi contra|demanda en mi contra|lawsuit against me)\b"
        ),
    ),
)


_FIRST_PERSON = re.compile(
    r"(?i)\b(?:mi|mis|me|tengo|estoy|padezco|sufro|tomo|my|i\s+have|i'm|i\s+am|i\s+feel|"
    r"i\s+take|i\s+was)\b"
)


def sensitive_topics(text: str) -> frozenset[str]:
    """First-person special-category topics that masking cannot remove.

    Callers keep such requests on the on-device brain instead of masking words. A
    general question ("¿qué síntomas tiene la gripe?") is not about the owner and
    stays eligible for remote inference.
    """
    if _FIRST_PERSON.search(text) is None:
        return frozenset()
    return frozenset(
        category for category, pattern in _SENSITIVE_TOPICS if pattern.search(text)
    )


def redact_for_remote(
    text: str,
    *,
    person_names: Iterable[str] = (),
) -> RemoteRedaction:
    """Remove credentials and personal identifiers before remote inference.

    ``person_names`` are names found by an entity recogniser (Apple NLTagger); they
    are masked only when the request has a personal context, so questions about
    public figures keep their subject. Names introduced by cues such as "mi esposa
    Ana" are always masked. Sensitive prose (health, finances) is not masked here:
    see ``sensitive_topics``.
    """
    redacted = text
    categories: set[str] = set()
    names = list(cue_based_person_names(text))
    if has_personal_context(text):
        names.extend(person_names)
    redacted, name_count = _redact_names(redacted, names)
    if name_count:
        categories.add("person_name")
    for category, pattern, replacement in _PATTERNS:
        if isinstance(replacement, str):
            updated, count = pattern.subn(replacement, redacted)
        else:
            count = 0
            validator = replacement

            def substitute(match: re.Match[str], validator=validator) -> str:
                nonlocal count
                value = validator(match)
                if value is None:
                    return match.group(0)
                count += 1
                return value

            updated = pattern.sub(substitute, redacted)
        if count:
            categories.add(category)
            redacted = updated
    return RemoteRedaction(text=redacted, categories=frozenset(categories))
