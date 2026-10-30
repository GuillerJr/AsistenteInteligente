"""Validation and explicit limits for local Jarvis session coordination."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation

from jarvis_core.state import SessionRef


class CoordinationError(ValueError):
    """Public errors contain codes only, never paths or conversation data."""

    def __init__(self, code):
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class CoordinationLimits:
    max_workspaces: int = 64
    max_sessions: int = 1000
    max_members: int = 32
    max_children: int = 4
    max_depth: int = 4
    max_messages: int = 10000
    max_pending_messages: int = 128
    max_reservations: int = 10000

    def __post_init__(self):
        if any(type(value) is not int or not 1 <= value <= 10000
               for value in vars(self).values()):
            raise CoordinationError("coordination_limits_invalid")


def identity(value, *, client=False):
    width = 64 if client else 32
    if type(value) is not str or re.fullmatch(rf"[a-f0-9]{{{width}}}", value) is None:
        raise CoordinationError("coordination_identity_invalid")
    return value


def ref_value(ref):
    if not isinstance(ref, SessionRef):
        raise CoordinationError("coordination_session_invalid")
    return {"session_id": ref.session_id, "temporary": ref.temporary}


def text(value, maximum=512, *, nullable=False):
    if nullable and value is None:
        return None
    if type(value) is not str or not value.strip() or "\0" in value:
        raise CoordinationError("coordination_text_invalid")
    try:
        size = len(value.encode())
    except UnicodeError:
        raise CoordinationError("coordination_text_invalid") from None
    if size > maximum:
        raise CoordinationError("coordination_text_invalid")
    return value


def integer(value, minimum=0, maximum=2**53 - 1):
    if type(value) is not int or not minimum <= value <= maximum:
        raise CoordinationError("coordination_integer_invalid")
    return value


def money(value, *, nullable=True):
    """Only exact decimal strings: missing information is never coerced to zero."""
    if value is None and nullable:
        return None
    if (type(value) is not str or len(value) > 40
            or re.fullmatch(r"(?:0|[1-9][0-9]{0,17})(?:\.[0-9]{1,12})?", value) is None):
        raise CoordinationError("coordination_cost_invalid")
    try:
        result = Decimal(value)
    except InvalidOperation:
        raise CoordinationError("coordination_cost_invalid") from None
    return format(result, "f")


def encoded(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def digest(value):
    return hashlib.sha256(encoded(value).encode()).hexdigest()
