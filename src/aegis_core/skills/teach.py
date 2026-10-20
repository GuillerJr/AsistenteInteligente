"""Teach Jarvis a reusable procedure from the chat (Claude's and Codex's "skills").

"Aprende a preparar mi informe semanal así: …" becomes a learned skill. The planner
proposes ``jarvis_skill_create`` with the steps in the owner's words; the owner sees
the name and steps in the approval and, once approved, Jarvis writes it as an ordinary
SKILL.md through the same importer and registry as a file import. A taught skill is
declarative guidance only: it never declares tools, so it cannot widen what a request
may use, and the importer rejects anything executable.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Awaitable, Callable
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from aegis_core.contracts import (
    AgentRole,
    Capability,
    RiskLevel,
    ToolAuthorization,
    ToolExecutionResult,
)
from aegis_core.skills.importer import (
    SkillImportError,
    _has_executable_content,
    finalize_skill_import,
    preview_skill_text,
)
from aegis_core.tools.broker import PolicyContext, ToolDefinition

SKILL_TOOL_PURPOSES = {
    "jarvis_skill_create": (
        "enseñarle a Jarvis un procedimiento reutilizable o habilidad: 'aprende a hacer X "
        "así…', 'guarda esto como habilidad', 'cuando te pida mi informe semanal, haz…'"),
}


class SkillCreateArguments(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=3, max_length=80, description="Nombre corto, en español.")
    description: str = Field(min_length=8, max_length=300, description=(
        "Para qué sirve, en una frase."))
    when_to_use: list[str] = Field(min_length=1, max_length=6, description=(
        "Frases con que el propietario la pedirá, p. ej. 'mi informe semanal'."))
    steps: list[str] = Field(min_length=1, max_length=12, description=(
        "Los pasos en orden, con las palabras del propietario."))


def skill_tool_definitions() -> tuple[ToolDefinition, ...]:
    return (ToolDefinition(
        name="jarvis_skill_create",
        description=("Guarda un procedimiento que el propietario le enseña a Jarvis para "
                     "repetirlo cuando lo pida."),
        arguments_model=SkillCreateArguments, capability=Capability.REMINDERS_WRITE,
        risk=RiskLevel.MEDIUM, allowed_roles=frozenset({AgentRole.PLANNER}),
        requires_confirmation=True,
    ),)


def is_skill_tool(tool_name: str) -> bool:
    return tool_name in SKILL_TOOL_PURPOSES


def skill_confirmation_summary(authorization: ToolAuthorization) -> str:
    parsed = SkillCreateArguments.model_validate(authorization.normalized_arguments)
    steps = " · ".join(f"{index}. {step}" for index, step in enumerate(parsed.steps, 1))
    return f"Aprender la habilidad «{parsed.name}»: {steps}"[:512]


def _markdown(parsed: SkillCreateArguments) -> str:
    def line(value: str) -> str:
        return " ".join(value.split())

    when = "\n".join(f"- {line(phrase)}" for phrase in parsed.when_to_use)
    steps = "\n".join(f"{index}. {line(step)}" for index, step in enumerate(parsed.steps, 1))
    return (f"---\nname: {line(parsed.name)}\ndescription: {line(parsed.description)}\n---\n"
            f"# {line(parsed.name)}\n\nÚsala cuando el propietario pida:\n{when}\n\n"
            f"Pasos:\n{steps}\n")


class SkillTeachHandlers:
    def __init__(self, registry: Any, *, on_change: Callable[[], None] = lambda: None) -> None:
        self._registry = registry
        self._on_change = on_change

    def handlers(self) -> dict[str, Callable[[ToolAuthorization, PolicyContext],
                                             Awaitable[ToolExecutionResult]]]:
        return {"jarvis_skill_create": self._create}

    async def _create(
        self, authorization: ToolAuthorization, _: PolicyContext,
    ) -> ToolExecutionResult:
        parsed = SkillCreateArguments.model_validate(authorization.normalized_arguments)
        try:
            # Checked on the raw steps: flattening them into one line hides code fences.
            if _has_executable_content("\n".join(parsed.steps)) or any(
                    "`" in step for step in parsed.steps):
                raise SkillImportError("executable_content_disabled")
            preview = preview_skill_text(_markdown(parsed), source_name="SKILL.md")
            if preview.requires_disabled:
                raise SkillImportError("executable_content_disabled")
            draft = finalize_skill_import(preview, {
                "enabled": True, "role": AgentRole.PLANNER.value,
                "trigger_phrases": tuple(" ".join(p.split())[:120] for p in parsed.when_to_use),
            })
            await asyncio.to_thread(self._registry.learn, draft)
            record = next(item for item in self._registry.catalogue()
                          if item["skill_id"] == draft.skill_id)
            # The owner approved this exact skill: enable it, and let NVIDIA read it, as
            # Jarvis answers with NVIDIA.
            await asyncio.to_thread(self._registry.set_enabled, draft.skill_id,
                                    record["content_sha256"], True)
            await asyncio.to_thread(self._registry.set_remote_consent, draft.skill_id,
                                    record["content_sha256"], True)
        except (SkillImportError, ValueError) as error:  # SkillError is a ValueError
            return ToolExecutionResult(
                call_id=authorization.call_id, tool_name=authorization.tool_name,
                success=True, metadata={"verified": True, "integration": "jarvis-skill"},
                output=json.dumps({"created": False, "message": (
                    "No pude guardar esa habilidad: "
                    f"{getattr(error, 'args', ['inválida'])[0]}.")}, ensure_ascii=False),
            )
        self._on_change()
        phrases = ", ".join(f"«{phrase}»" for phrase in parsed.when_to_use[:3])
        return ToolExecutionResult(
            call_id=authorization.call_id, tool_name=authorization.tool_name, success=True,
            metadata={"verified": True, "integration": "jarvis-skill"},
            output=json.dumps({"created": True, "skill_id": draft.skill_id, "message": (
                f"Aprendí «{parsed.name}» ({len(parsed.steps)} pasos). La usaré cuando me "
                f"pidas {phrases}. Puedes desactivarla en Ajustes, en Extensiones.")},
                ensure_ascii=False),
        )
