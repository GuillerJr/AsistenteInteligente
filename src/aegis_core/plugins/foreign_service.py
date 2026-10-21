"""Jarvis finds and installs Codex and Claude plugins when asked, with approval.

"Instala el complemento de SwiftUI" makes the planner propose
``jarvis_plugin_install``; the owner sees in the approval exactly which skills come in
and what stays out, and only then Jarvis installs it (Claude's plugin suggestion card
and Codex's plugin-management skill do the same). ``jarvis_plugin_search`` only reads
the catalogue. The owner manages plugins and marketplaces in Settings through the
``plugins.foreign.*`` IPC methods, which are never agent tools.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from aegis_core.contracts import (
    AgentRole,
    Capability,
    RiskLevel,
    ToolAuthorization,
    ToolExecutionResult,
)
from aegis_core.plugins.foreign import ForeignPlugin, ForeignPluginError, ForeignPluginManager
from aegis_core.skills.importer import SkillImportError
from aegis_core.tools.broker import PolicyContext, ToolDefinition

FOREIGN_PLUGIN_TOOL_PURPOSES = {
    "jarvis_plugin_search": (
        "buscar complementos (plugins) de Codex o Claude que Jarvis puede instalar o ya "
        "instaló: '¿hay algún complemento para PDF?', '¿qué complementos tengo?'"),
    "jarvis_plugin_install": (
        "instalar en Jarvis un complemento (plugin) de Codex o Claude: 'instala el "
        "complemento de SwiftUI', 'agrega el plugin de Slack'"),
}

_PLANNER = frozenset({AgentRole.PLANNER})


class PluginSearchArguments(BaseModel):
    model_config = ConfigDict(extra="forbid")

    query: str = Field(default="", max_length=120, description=(
        "Palabras de lo que busca el propietario; vacío lista todos."))


class PluginInstallArguments(BaseModel):
    model_config = ConfigDict(extra="forbid")

    plugin: str = Field(min_length=2, max_length=80, description=(
        "El plugin_id que devolvió jarvis_plugin_search o el nombre que dijo el propietario."))


def foreign_plugin_tool_definitions() -> tuple[ToolDefinition, ...]:
    return (
        ToolDefinition(
            name="jarvis_plugin_search",
            description=("Busca complementos de Codex o Claude que Jarvis puede instalar "
                         "y dice cuáles ya están instalados."),
            arguments_model=PluginSearchArguments, capability=Capability.REMINDERS_READ,
            risk=RiskLevel.LOW, allowed_roles=_PLANNER,
        ),
        ToolDefinition(
            name="jarvis_plugin_install",
            description=("Instala un complemento de Codex o Claude: sus habilidades pasan a "
                         "Jarvis. El propietario lo aprueba antes."),
            arguments_model=PluginInstallArguments, capability=Capability.REMINDERS_WRITE,
            risk=RiskLevel.MEDIUM, allowed_roles=_PLANNER, requires_confirmation=True,
        ),
    )


def is_foreign_plugin_tool(tool_name: str) -> bool:
    return tool_name in FOREIGN_PLUGIN_TOOL_PURPOSES


def _skills(plugin: ForeignPlugin, limit: int = 4) -> str:
    names = [skill.name for skill in plugin.skills]
    shown = ", ".join(names[:limit])
    return shown + (f" y {len(names) - limit} más" if len(names) > limit else "")


def foreign_plugin_confirmation_summary(authorization: ToolAuthorization,
                                        manager: ForeignPluginManager) -> str:
    parsed = PluginInstallArguments.model_validate(authorization.normalized_arguments)
    matches = manager.catalogue.resolve(parsed.plugin)
    if len(matches) != 1:
        return f"Instalar el complemento «{parsed.plugin[:80]}»."
    plugin = matches[0]
    count = len(plugin.skills)
    text = (f"Instalar el complemento «{plugin.name}» de {plugin.format_label}"
            f"{f' ({plugin.publisher})' if plugin.publisher else ''}: {count} "
            f"habilidad{'es' if count != 1 else ''} ({_skills(plugin)}).")
    if plugin.left_out:
        text += f" Queda fuera: {'; '.join(plugin.left_out[:3])}."
    return text[:512]


def format_foreign_plugin_result(result: ToolExecutionResult) -> str:
    payload = json.loads(result.output)
    if not isinstance(payload, dict) or not isinstance(payload.get("message"), str):
        raise ValueError("plugin result is invalid")
    return payload["message"][:4_000]


class ForeignPluginToolHandlers:
    def __init__(self, manager: ForeignPluginManager) -> None:
        self._manager = manager

    def handlers(self) -> dict[str, Any]:
        return {"jarvis_plugin_search": self._search, "jarvis_plugin_install": self._install}

    @staticmethod
    def _result(authorization: ToolAuthorization, payload: dict[str, Any]) -> ToolExecutionResult:
        return ToolExecutionResult(
            call_id=authorization.call_id, tool_name=authorization.tool_name, success=True,
            metadata={"verified": True, "integration": "jarvis-plugins"},
            output=json.dumps(payload, ensure_ascii=False),
        )

    async def _search(self, authorization: ToolAuthorization, _: PolicyContext,
                      ) -> ToolExecutionResult:
        parsed = PluginSearchArguments.model_validate(authorization.normalized_arguments)
        found = await asyncio.to_thread(self._manager.catalogue.search, parsed.query)
        installed = await asyncio.to_thread(self._manager.catalogue.installed)
        if not found:
            message = ("No encontré complementos de Codex o Claude para eso. Puedes agregar "
                       "un marketplace en Ajustes, en Extensiones.")
        else:
            lines = []
            for plugin in found:
                state = "instalado" if plugin.plugin_id in installed else (
                    f"{len(plugin.skills)} habilidades" if plugin.skills
                    else "sin habilidades que Jarvis pueda usar")
                lines.append(f"- {plugin.name} ({plugin.format_label}, {state}): "
                             f"{plugin.description[:140]}")
            message = "Complementos disponibles:\n" + "\n".join(lines)
        return self._result(authorization, {
            "plugins": [{"plugin_id": p.plugin_id, "name": p.name, "format": p.format,
                         "installed": p.plugin_id in installed, "skills": len(p.skills)}
                        for p in found],
            "message": message,
        })

    async def _install(self, authorization: ToolAuthorization, _: PolicyContext,
                       ) -> ToolExecutionResult:
        parsed = PluginInstallArguments.model_validate(authorization.normalized_arguments)
        matches = await asyncio.to_thread(self._manager.catalogue.resolve, parsed.plugin)
        if len(matches) != 1:
            names = ", ".join(f"«{plugin.name}»" for plugin in matches[:5])
            message = (f"Varios complementos coinciden ({names}); dime cuál instalo."
                       if matches else "No encontré ese complemento de Codex o Claude.")
            return self._result(authorization, {"installed": False, "message": message})
        try:
            done = await asyncio.to_thread(self._manager.install, matches[0].plugin_id)
        except (ForeignPluginError, SkillImportError, ValueError) as error:
            message = (str(error) if isinstance(error, ForeignPluginError)
                       else f"No pude instalar «{matches[0].name}».")
            return self._result(authorization, {"installed": False, "message": message})
        skills = ", ".join(f"«{name}»" for name in done["skills"][:6])
        message = (f"Instalé «{done['name']}»: {len(done['skills'])} habilidades ({skills}). "
                   "Las usaré cuando me pidas algo de eso.")
        if done["left_out"]:
            message += f" Quedó fuera: {'; '.join(done['left_out'][:3])}."
        message += " Puedes quitarlo en Ajustes, en Extensiones."
        return self._result(authorization, {"installed": True, **done, "message": message})


class _Payload(BaseModel):
    model_config = ConfigDict(extra="forbid")


class _PluginPayload(_Payload):
    plugin_id: str = Field(pattern=r"^[a-z][a-z0-9-]{2,40}$")


class _MarketplaceAdd(_Payload):
    repository: str = Field(min_length=3, max_length=200)


class _MarketplaceRemove(_Payload):
    slug: str = Field(pattern=r"^[a-z0-9][a-z0-9._-]{1,80}$")


class ForeignPluginIpcService:
    """Owner-only management from Settings; these methods are never agent tools."""

    METHODS = frozenset({
        "plugins.foreign.status", "plugins.foreign.install", "plugins.foreign.remove",
        "plugins.foreign.marketplace.add", "plugins.foreign.marketplace.remove",
    })

    def __init__(self, manager: ForeignPluginManager) -> None:
        self._manager = manager
        self._lock = asyncio.Lock()

    def handlers(self) -> dict[str, Any]:
        return {method: self.handle for method in self.METHODS}

    async def handle(self, request: Any) -> Any:
        from aegis_core.ipc.server import IpcHandlerResult

        if request.method not in self.METHODS:
            return IpcHandlerResult(ok=False, error_code="method_not_found")
        try:
            async with self._lock:
                method, raw = request.method, request.payload or {}
                if method == "plugins.foreign.status":
                    _Payload.model_validate(raw)
                    self._manager.catalogue.invalidate()
                elif method == "plugins.foreign.install":
                    plugin = _PluginPayload.model_validate(raw).plugin_id
                    await asyncio.to_thread(self._manager.install, plugin)
                elif method == "plugins.foreign.remove":
                    plugin = _PluginPayload.model_validate(raw).plugin_id
                    await asyncio.to_thread(self._manager.remove, plugin)
                elif method == "plugins.foreign.marketplace.add":
                    await self._manager.add_marketplace(
                        _MarketplaceAdd.model_validate(raw).repository)
                else:
                    slug = _MarketplaceRemove.model_validate(raw).slug
                    await asyncio.to_thread(self._manager.remove_marketplace, slug)
                value = await asyncio.to_thread(self._manager.status)
            return IpcHandlerResult(ok=True, payload=value)
        except ForeignPluginError as error:
            return IpcHandlerResult(ok=True, payload={
                **(await asyncio.to_thread(self._manager.status)), "error": str(error)})
        except (ValidationError, SkillImportError, ValueError, OSError):
            return IpcHandlerResult(ok=False, error_code="plugins_invalid_request")
