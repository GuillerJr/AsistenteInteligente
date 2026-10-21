"""Per-app approval for computer use ("¿Permitir que Jarvis use Notas?").

Like Codex's computer use, approving a `computer_use` action can also allow Jarvis
to keep working in that same application without asking again: for the rest of
this conversation, or always. A grant names one application bundle and only covers
single computer-use actions in it: multi-step workflows, other apps and restricted
apps (Terminal, System Settings, Keychain…) keep asking every time. "Always" grants
persist in an owner-only file the owner reviews and revokes in Settings; conversation
grants live in memory and end with the daemon. Every grant and use is audited.
"""

from __future__ import annotations

import re
import threading
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

from aegis_core.settings_store import read_private_json, write_private_json
from aegis_core.tools.computer import is_restricted_computer_bundle

MAX_APP_GRANTS = 64
MAX_CONVERSATION_GRANTS = 256
_BUNDLE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9.-]{1,253}[A-Za-z0-9]$")


class AppGrantError(ValueError):
    """The application cannot receive a grant."""


@dataclass(frozen=True, slots=True)
class AppGrant:
    bundle_identifier: str
    granted_at: datetime


def grantable(bundle_identifier: object) -> bool:
    return (isinstance(bundle_identifier, str) and _BUNDLE.fullmatch(bundle_identifier) is not None
            and not is_restricted_computer_bundle(bundle_identifier))


class ComputerAppGrantStore:
    def __init__(self, path: Path) -> None:
        self._path = path
        self._lock = threading.Lock()
        self._always: dict[str, AppGrant] | None = None
        self._conversations: set[tuple[UUID, str]] = set()

    def allows(self, bundle_identifier: object, conversation_id: UUID | None) -> bool:
        if not grantable(bundle_identifier):
            return False
        key = str(bundle_identifier).casefold()
        with self._lock:
            return key in self._loaded() or (
                conversation_id is not None and (conversation_id, key) in self._conversations)

    def grant(self, bundle_identifier: object, *, conversation_id: UUID | None) -> None:
        """For one conversation when it is given, otherwise always."""
        if not grantable(bundle_identifier):
            raise AppGrantError("this application cannot be allowed")
        key = str(bundle_identifier).casefold()
        with self._lock:
            if conversation_id is not None:
                if len(self._conversations) >= MAX_CONVERSATION_GRANTS:
                    self._conversations.clear()
                self._conversations.add((conversation_id, key))
                return
            grants = dict(self._loaded())
            if key not in grants and len(grants) >= MAX_APP_GRANTS:
                raise AppGrantError("too many application grants")
            grants.setdefault(key, AppGrant(key, datetime.now(UTC)))
            self._save(grants)

    def revoke(self, bundle_identifier: str) -> bool:
        key = bundle_identifier.casefold()
        with self._lock:
            self._conversations = {item for item in self._conversations if item[1] != key}
            grants = dict(self._loaded())
            if grants.pop(key, None) is None:
                return False
            self._save(grants)
            return True

    def entries(self) -> tuple[AppGrant, ...]:
        with self._lock:
            return tuple(sorted(self._loaded().values(), key=lambda g: g.bundle_identifier))

    def _loaded(self) -> dict[str, AppGrant]:
        if self._always is None:
            self._always = self._read()
        return self._always

    def _read(self) -> dict[str, AppGrant]:
        if not self._path.exists() and not self._path.is_symlink():
            return {}
        try:
            raw = read_private_json(self._path)
        except (OSError, ValueError):
            return {}  # An unreadable or tampered file grants nothing.
        if not isinstance(raw, dict) or raw.get("version") != 1:
            return {}
        grants: dict[str, AppGrant] = {}
        for item in raw.get("grants", [])[:MAX_APP_GRANTS]:
            if not isinstance(item, dict) or not grantable(item.get("bundle_identifier")):
                continue
            try:
                when = datetime.fromisoformat(str(item.get("granted_at")))
            except ValueError:
                continue
            key = str(item["bundle_identifier"]).casefold()
            grants[key] = AppGrant(key, when)
        return grants

    def _save(self, grants: dict[str, AppGrant]) -> None:
        write_private_json(self._path, {"version": 1, "grants": [
            {"bundle_identifier": grant.bundle_identifier,
             "granted_at": grant.granted_at.isoformat()} for grant in grants.values()]})
        self._always = grants
