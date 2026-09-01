from __future__ import annotations

import re
from collections.abc import Mapping
from pathlib import Path

from aegis_core.contracts import Capability
from aegis_core.mcp.client import AsyncToolHandler, McpHost
from aegis_core.tools.audit import AuditSink
from aegis_core.tools.broker import ToolDefinition

_TOKENS = re.compile(r"[\wáéíóúüñ]+", re.IGNORECASE)
_MAIL = frozenset({"correo", "correos", "email", "mail", "mensaje"})
_CALENDAR = frozenset({"agenda", "calendar", "calendario", "cita", "evento"})
_CAMERA = frozenset({"cámara", "camara", "camera", "facetime", "mírame", "mirame"})
_SCREEN = frozenset(
    {
        "captura",
        "escritorio",
        "pantalla",
        "screen",
        "visual",
        "ventana",
        "window",
    }
)


class McpHostManager:
    """Owns isolated MCP processes and exposes only intent-relevant schema names."""

    def __init__(self, host: McpHost) -> None:
        self._host = host
        self._definitions: tuple[ToolDefinition, ...] = ()
        self._names_by_intent: dict[str, frozenset[str]] = {}

    @classmethod
    def from_file(
        cls,
        path: Path,
        *,
        audit_sink: AuditSink | None = None,
    ) -> McpHostManager:
        return cls(McpHost.from_file(path, audit_sink=audit_sink))

    async def start(self) -> None:
        await self._host.start()
        self._definitions = self._host.tool_definitions()
        intents: dict[str, set[str]] = {
            "mail": set(),
            "calendar": set(),
            "camera": set(),
            "screen": set(),
            "general": set(),
        }
        for definition in self._definitions:
            capability = definition.capability
            name = definition.name.casefold()
            if capability in {Capability.MAIL_READ, Capability.MAIL_WRITE} or "mail" in name:
                intents["mail"].add(definition.name)
            elif capability in {Capability.CALENDAR_READ, Capability.CALENDAR_WRITE} or any(
                marker in name for marker in ("calendar", "event")
            ):
                intents["calendar"].add(definition.name)
            elif "camera" in name or "facetime" in name:
                intents["camera"].add(definition.name)
            elif capability is Capability.SCREEN_CAPTURE or any(
                marker in name for marker in ("screen", "vision", "window")
            ):
                intents["screen"].add(definition.name)
            else:
                intents["general"].add(definition.name)
        self._names_by_intent = {key: frozenset(value) for key, value in intents.items()}

    async def close(self) -> None:
        self._definitions = ()
        self._names_by_intent.clear()
        await self._host.close()

    def tool_definitions(self) -> tuple[ToolDefinition, ...]:
        return self._definitions

    def handlers(self) -> Mapping[str, AsyncToolHandler]:
        return self._host.handlers()

    def tool_names_for_request(self, text: str) -> frozenset[str]:
        terms = frozenset(token.casefold() for token in _TOKENS.findall(text))
        selected = {
            name
            for name in self._names_by_intent.get("general", ())
            if any(
                token in terms
                for token in re.findall(r"[a-z0-9]+", name.casefold())
                if len(token) >= 3
            )
        }
        if not terms.isdisjoint(_MAIL):
            selected.update(self._names_by_intent.get("mail", ()))
        if not terms.isdisjoint(_CALENDAR):
            selected.update(self._names_by_intent.get("calendar", ()))
        if not terms.isdisjoint(_CAMERA):
            selected.update(self._names_by_intent.get("camera", ()))
        if not terms.isdisjoint(_SCREEN):
            selected.update(self._names_by_intent.get("screen", ()))
        return frozenset(selected)
