from __future__ import annotations

import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class RemoteRedaction:
    text: str
    categories: frozenset[str]


_PATTERNS: tuple[tuple[str, re.Pattern[str], str], ...] = (
    (
        "credential",
        re.compile(r"\b(?:ya29\.[A-Za-z0-9._~-]{8,}|GOCSPX-[A-Za-z0-9_-]{8,}|"
                   r"1//[A-Za-z0-9._~-]{8,})"),
        "[REDACTED_CREDENTIAL]",
    ),
    (
        "secret_assignment",
        re.compile(
            r"(\b(?:api[_-]?key|access[_-]?token|refresh[_-]?token|client[_-]?secret|"
            r"password|passwd|secret|token)[\"']\s*:\s*[\"'])[^\"'\s,;]{4,}",
            re.IGNORECASE,
        ),
        r"\1[REDACTED_SECRET]",
    ),
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
            r"\b(api[_-]?key|access[_-]?token|refresh[_-]?token|client[_-]?secret|"
            r"token|password|passwd|secret|"
            r"contrase(?:ñ|n)a|clave)\s*[:=]\s*['\"]?[^\s'\",;]{4,}",
            re.IGNORECASE,
        ),
        r"\1=[REDACTED_SECRET]",
    ),
    (
        "secret_query",
        re.compile(
            r"([?&](?:api[_-]?key|access[_-]?token|refresh[_-]?token|client[_-]?secret|"
            r"token|key|code)=)[^&\s]+",
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


def redact_secrets_for_remote(text: str) -> RemoteRedaction:
    """Authorized NVIDIA dialogue may contain personal data, never credentials."""
    redacted = text
    categories: set[str] = set()
    for category, pattern, replacement in _PATTERNS:
        if category in {"email", "phone", "local_user"}:
            continue
        redacted, count = pattern.subn(replacement, redacted)
        if count:
            categories.add(category)
    return RemoteRedaction(text=redacted, categories=frozenset(categories))


_SECRET_FIELDS = frozenset({
    "apikey", "accesstoken", "refreshtoken", "clientsecret", "password", "passwd",
    "secret", "token", "authorization", "privatekey", "credential", "credentials",
})


def sanitize_remote_reference(value: Any, *, _depth: int = 0) -> Any:
    """Redact structured and JSON-encoded tool data before outer JSON escaping.

    Personal data is permitted by the user's NVIDIA consent. Secrets are not.
    References remain data and cannot add system messages or tool permissions.
    """
    if _depth > 20:
        return "[REDACTED_NESTED_DATA]"
    if isinstance(value, Mapping):
        return {
            str(key): "[REDACTED_SECRET]"
            if re.sub(r"[_-]", "", str(key).casefold()) in _SECRET_FIELDS
            else sanitize_remote_reference(item, _depth=_depth + 1)
            for key, item in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [sanitize_remote_reference(item, _depth=_depth + 1) for item in value]
    if isinstance(value, str):
        if value.lstrip().startswith(("{", "[")):
            try:
                parsed = json.loads(value)
            except (ValueError, RecursionError):
                pass
            else:
                if isinstance(parsed, (dict, list)):
                    value = json.dumps(
                        sanitize_remote_reference(parsed, _depth=_depth + 1),
                        ensure_ascii=False,
                    )
        return redact_secrets_for_remote(value).text
    return value
