from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class RemoteRedaction:
    text: str
    categories: frozenset[str]


_PATTERNS: tuple[tuple[str, re.Pattern[str], str], ...] = (
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
        "credential",
        re.compile(
            r"\b(?:nvapi-|sk-(?:proj-)?|gh[pousr]_|xox[baprs]-|AIza)"
            r"[A-Za-z0-9._-]{8,}\b"
        ),
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
    (
        "phone",
        re.compile(r"(?<!\w)(?:\+\d{1,3}[ -]?)?(?:\(?\d{2,4}\)?[ -]){2,4}\d{2,4}(?!\w)"),
        "[REDACTED_PHONE]",
    ),
    (
        "local_user",
        re.compile(r"(?<!\w)/Users/[^/\s]+"),
        "/Users/[REDACTED_USER]",
    ),
)


def redact_for_remote(text: str) -> RemoteRedaction:
    """Remove common credentials and personal identifiers before remote inference."""
    redacted = text
    categories: set[str] = set()
    for category, pattern, replacement in _PATTERNS:
        updated, count = pattern.subn(replacement, redacted)
        if count:
            categories.add(category)
            redacted = updated
    return RemoteRedaction(text=redacted, categories=frozenset(categories))
