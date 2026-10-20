"""Owner-chosen standing approval for read-only tools ("Aprobar siempre").

Like Claude's "Always allow" and Codex's approval rules, the owner can let one
read-only tool run without asking each time; searching Gmail on every "revisa mis
correos" was the most repeated approval. A grant is per exact tool name, only for
tools whose capability only reads, and it never covers writes, sends or deletes:
those keep asking on every call. Grants persist in an owner-only file, are audited
when created, used and revoked, and the owner can list and revoke them.
"""

from __future__ import annotations

import re
import threading
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from aegis_core.contracts import Capability
from aegis_core.settings_store import read_private_json, write_private_json

STANDING_GRANT_CAPABILITIES = frozenset({
    Capability.MAIL_READ,
    Capability.CALENDAR_READ,
    Capability.CONTACTS_READ,
    Capability.REMINDERS_READ,
    Capability.FILESYSTEM_READ,
    Capability.WEB_READ,
})
MAX_STANDING_GRANTS = 64
_TOOL_NAME = re.compile(r"^[a-z][a-z0-9_-]{2,63}$")


class StandingGrantError(ValueError):
    """The tool cannot receive a standing grant."""


@dataclass(frozen=True, slots=True)
class StandingGrant:
    tool_name: str
    granted_at: datetime


class StandingGrantStore:
    def __init__(
        self,
        path: Path,
        *,
        capability_of: Callable[[str], Capability | None],
    ) -> None:
        self._path = path
        self._capability_of = capability_of
        self._lock = threading.Lock()
        self._grants: dict[str, StandingGrant] | None = None

    def eligible(self, tool_name: str) -> bool:
        return (
            isinstance(tool_name, str)
            and _TOOL_NAME.fullmatch(tool_name) is not None
            and self._capability_of(tool_name) in STANDING_GRANT_CAPABILITIES
        )

    def allows(self, tool_name: str) -> bool:
        # The capability is checked again on every use: a tool whose definition no
        # longer reads only (reconfigured MCP peer) loses its grant immediately.
        with self._lock:
            return self.eligible(tool_name) and tool_name in self._loaded()

    def grant(self, tool_name: str) -> StandingGrant:
        if not self.eligible(tool_name):
            raise StandingGrantError("only read-only tools can be always allowed")
        with self._lock:
            grants = dict(self._loaded())
            existing = grants.get(tool_name)
            if existing is not None:
                return existing
            if len(grants) >= MAX_STANDING_GRANTS:
                raise StandingGrantError("too many standing grants")
            grant = StandingGrant(tool_name=tool_name, granted_at=datetime.now(UTC))
            grants[tool_name] = grant
            self._save(grants)
            return grant

    def revoke(self, tool_name: str) -> bool:
        with self._lock:
            grants = dict(self._loaded())
            if grants.pop(tool_name, None) is None:
                return False
            self._save(grants)
            return True

    def entries(self) -> tuple[StandingGrant, ...]:
        with self._lock:
            return tuple(sorted(self._loaded().values(), key=lambda grant: grant.tool_name))

    def _loaded(self) -> dict[str, StandingGrant]:
        if self._grants is None:
            self._grants = self._read()
        return self._grants

    def _read(self) -> dict[str, StandingGrant]:
        if not self._path.exists() and not self._path.is_symlink():
            return {}
        try:
            raw = read_private_json(self._path)
        except (OSError, ValueError):
            # An unreadable or tampered file grants nothing.
            return {}
        if not isinstance(raw, dict) or raw.get("version") != 1:
            return {}
        grants: dict[str, StandingGrant] = {}
        for item in raw.get("grants", [])[:MAX_STANDING_GRANTS]:
            if not isinstance(item, dict):
                continue
            name, granted = item.get("tool_name"), item.get("granted_at")
            if not isinstance(name, str) or _TOOL_NAME.fullmatch(name) is None:
                continue
            try:
                when = datetime.fromisoformat(granted) if isinstance(granted, str) else None
            except ValueError:
                when = None
            if when is None or when.tzinfo is None:
                continue
            grants[name] = StandingGrant(tool_name=name, granted_at=when)
        return grants

    def _save(self, grants: dict[str, StandingGrant]) -> None:
        write_private_json(self._path, {
            "version": 1,
            "grants": [
                {"tool_name": grant.tool_name, "granted_at": grant.granted_at.isoformat()}
                for grant in sorted(grants.values(), key=lambda item: item.tool_name)
            ],
        })
        self._grants = grants
