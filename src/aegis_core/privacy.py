from __future__ import annotations

import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from itertools import zip_longest
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

_SOURCE_LINE_MAX_BYTES = 8192
_SOURCE_LINE_MAX_COUNT = 128
_SOURCE_LINE_REFERENCE_MAX_BYTES = 12_288
_LINE_SEPARATORS = re.compile(r"\r\n|[\n\r\v\f\x1c-\x1e\x85\u2028\u2029]")


def _remote_source_lines(original: Mapping, sanitized: dict) -> list[dict[str, Any]] | None:
    """Bounded, redacted line references; never reconstruct a changed source layout."""
    metadata = sanitized.get("metadata")
    if (original.get("success") is not True or not isinstance(metadata, dict)
            or not isinstance(metadata.get("path"), str) or not metadata["path"].strip()
            or metadata.get("source_layout_changed") not in (None, False)
            or metadata.get("source_lines_omitted") is True):
        return None
    source, text = original.get("output"), sanitized.get("output")
    if not isinstance(source, str) or not isinstance(text, str):
        return None
    try:
        if max(len(source.encode("utf-8")), len(text.encode("utf-8"))) > _SOURCE_LINE_MAX_BYTES:
            return None
        metadata["path"].encode("utf-8")
    except UnicodeError:
        return None
    lines = text.splitlines()
    if len(lines) > _SOURCE_LINE_MAX_COUNT:
        return None
    references = [{"line": index, "text": line} for index, line in enumerate(lines, start=1)]
    if len(json.dumps(references, ensure_ascii=False).encode("utf-8")) \
            > _SOURCE_LINE_REFERENCE_MAX_BYTES:
        return None
    return references


def sanitize_remote_reference(
    value: Any, *, personal_identifiers: bool = False,
    _depth: int = 0, _line_references: bool = True,
) -> Any:
    """Redact structured and JSON-encoded tool data before outer JSON escaping.

    Personal data is permitted by the user's NVIDIA consent unless the caller
    chooses the stricter engineering policy. Secrets are never permitted.
    References remain data and cannot add system messages or tool permissions.
    """
    if _depth > 20:
        return "[REDACTED_NESTED_DATA]"
    redactor = redact_for_remote if personal_identifiers else redact_secrets_for_remote
    if isinstance(value, Mapping):
        text_result = _line_references and value.get("tool_name") == "filesystem_read_text"
        sanitized = {
            redactor(str(key)).text: "[REDACTED_SECRET]"
            if re.sub(r"[_-]", "", str(key).casefold()) in _SECRET_FIELDS
            else sanitize_remote_reference(
                item, personal_identifiers=personal_identifiers, _depth=_depth + 1,
                _line_references=_line_references and not (text_result and key == "output"),
            )
            for key, item in value.items()
            # This derived view belongs to the outbound reference only. Rebuild it
            # instead of recursively accepting supplied or previously derived lines.
            if not (text_result and key == "source_lines")
        }
        if (text_result
                and isinstance(value.get("output"), str)
                and isinstance(sanitized.get("output"), str)):
            original_breaks = (match[0] for match in _LINE_SEPARATORS.finditer(value["output"]))
            clean_breaks = (match[0] for match in _LINE_SEPARATORS.finditer(sanitized["output"]))
            if any(before != after for before, after in zip_longest(original_breaks, clean_breaks)):
                metadata = sanitized.get("metadata")
                sanitized["metadata"] = {
                    **(metadata if isinstance(metadata, dict) else {}),
                    "source_layout_changed": True,
                }
            source_lines = _remote_source_lines(value, sanitized)
            if source_lines is not None:
                sanitized["source_lines"] = source_lines
            elif value.get("success") is True and isinstance(sanitized.get("metadata"), dict) \
                    and sanitized["metadata"].get("source_layout_changed") is not True:
                # A large original may shrink below the limit after redaction. Keep
                # its omitted view omitted on subsequent sanitization passes too.
                sanitized["metadata"] = {**sanitized["metadata"], "source_lines_omitted": True}
        return sanitized
    if isinstance(value, (list, tuple)):
        return [sanitize_remote_reference(item, personal_identifiers=personal_identifiers,
                                          _depth=_depth + 1,
                                          _line_references=_line_references)
                for item in value]
    if isinstance(value, str):
        if value.lstrip().startswith(("{", "[")):
            duplicate_keys = False

            def collect_pairs(pairs):
                nonlocal duplicate_keys
                result = {}
                for key, item in pairs:
                    if key in result:
                        duplicate_keys = True
                    result[key] = item
                return result

            try:
                parsed = json.loads(value, object_pairs_hook=collect_pairs)
            except (ValueError, RecursionError):
                pass
            else:
                if isinstance(parsed, (dict, list)):
                    cleaned = sanitize_remote_reference(parsed,
                                                        personal_identifiers=personal_identifiers,
                                                        _depth=_depth + 1,
                                                        _line_references=_line_references)
                    # Preserve source whitespace/lines when no structured redaction
                    # is needed. Re-encoding clean JSON destroys line citations.
                    # Duplicate keys cannot take this path: hidden earlier values
                    # might contain secrets absent from the decoded mapping.
                    if duplicate_keys or cleaned != parsed:
                        value = json.dumps(cleaned, ensure_ascii=False)
                    # Keys and decoded values have already been sanitized. A
                    # second regex pass over JSON escapes can swallow following
                    # source lines (e.g. token=...\\nnext=...) and is not idempotent.
                    return value
        return redactor(value).text
    return value
