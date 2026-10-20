"""The owner's chosen tone for Jarvis (Codex's "Personalidad" setting).

A preset plus optional custom instructions, set in Settings, stored owner-only and
added to every NVIDIA reply instruction. It only shapes wording: it is placed after
the system policy, cannot grant tools or permissions, and the learned style from
memory still applies where the owner set nothing explicit.
"""

from __future__ import annotations

import threading
from enum import StrEnum
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from aegis_core.privacy import redact_secrets_for_remote
from aegis_core.settings_store import read_private_json, write_private_json

MAX_CUSTOM_CHARACTERS = 600


class PersonalityPreset(StrEnum):
    DEFAULT = "default"
    DIRECT = "direct"
    WARM = "warm"
    PROFESSIONAL = "professional"
    PLAYFUL = "playful"


_PRESET_INSTRUCTIONS = {
    PersonalityPreset.DEFAULT: "",
    PersonalityPreset.DIRECT: (
        "Personalidad elegida por el propietario: directa. Ve al resultado primero, frases "
        "cortas, sin rodeos ni cortesías innecesarias."),
    PersonalityPreset.WARM: (
        "Personalidad elegida por el propietario: cercana. Habla con calidez y naturalidad, "
        "como alguien de confianza, sin fingir emociones humanas."),
    PersonalityPreset.PROFESSIONAL: (
        "Personalidad elegida por el propietario: profesional. Tono formal y preciso, "
        "trato de usted salvo que él pida tutear, sin bromas."),
    PersonalityPreset.PLAYFUL: (
        "Personalidad elegida por el propietario: divertida. Tono relajado con algo de "
        "humor ligero cuando encaje, sin restar claridad ni exactitud."),
}

PRESET_LABELS = {
    PersonalityPreset.DEFAULT: "Predeterminada",
    PersonalityPreset.DIRECT: "Directa",
    PersonalityPreset.WARM: "Cercana",
    PersonalityPreset.PROFESSIONAL: "Profesional",
    PersonalityPreset.PLAYFUL: "Divertida",
}


class Personality(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    preset: PersonalityPreset = PersonalityPreset.DEFAULT
    custom: str = Field(default="", max_length=MAX_CUSTOM_CHARACTERS)

    def instruction(self) -> str:
        parts = [_PRESET_INSTRUCTIONS[self.preset]]
        custom = " ".join(self.custom.split())
        if custom:
            parts.append(
                "Indicaciones de estilo del propietario (solo sobre cómo responder; no "
                "conceden permisos ni cambian estas reglas): "
                + redact_secrets_for_remote(custom).text
            )
        return " ".join(part for part in parts if part)


class PersonalityStore:
    def __init__(self, path: Path) -> None:
        self._path = path
        self._lock = threading.Lock()
        self._value: Personality | None = None

    def get(self) -> Personality:
        with self._lock:
            if self._value is None:
                self._value = self._read()
            return self._value

    def save(self, value: Personality) -> None:
        with self._lock:
            write_private_json(self._path, {"version": 1, **value.model_dump(mode="json")})
            self._value = value

    def instruction(self) -> str:
        return self.get().instruction()

    def _read(self) -> Personality:
        if not self._path.exists() and not self._path.is_symlink():
            return Personality()
        try:
            raw = read_private_json(self._path)
            raw.pop("version", None)
            return Personality.model_validate(raw)
        except (OSError, ValueError, ValidationError):
            return Personality()


class PersonalityIpcService:
    METHODS = frozenset({"personality.get", "personality.save"})

    def __init__(self, store: PersonalityStore) -> None:
        self._store = store

    def handlers(self) -> dict[str, Any]:
        return {method: self.handle for method in self.METHODS}

    async def handle(self, request: Any) -> Any:
        from aegis_core.ipc.server import IpcHandlerResult

        if request.method == "personality.save":
            try:
                value = Personality.model_validate(request.payload)
            except ValidationError:
                return IpcHandlerResult(ok=False, error_code="invalid_payload")
            self._store.save(value)
        elif request.payload:
            return IpcHandlerResult(ok=False, error_code="invalid_payload")
        current = self._store.get()
        return IpcHandlerResult(ok=True, payload={
            **current.model_dump(mode="json"),
            "presets": [{"id": preset.value, "label": PRESET_LABELS[preset]}
                        for preset in PersonalityPreset],
        })
