from __future__ import annotations

import re
from collections.abc import Callable
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


def redact_for_remote(text: str) -> RemoteRedaction:
    """Remove credentials and personal identifiers before remote inference.

    Pattern-based redaction cannot recognise names, postal addresses or free-form
    sensitive facts (health, finances described in prose); those still reach the
    remote model when the owner writes them in a request.
    """
    redacted = text
    categories: set[str] = set()
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
