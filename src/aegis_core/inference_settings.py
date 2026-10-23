"""Owner-selected inference connection; OAuth credentials belong to the official client."""

from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from aegis_core.settings_store import read_private_json, write_private_json


class InferencePreferences(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    version: Literal[1] = 1
    provider: Literal["codex"] = "codex"
    model_id: str | None = Field(
        default=None, min_length=1, max_length=256, pattern=r"^[A-Za-z0-9][A-Za-z0-9._:/-]*$",
    )


def load_inference_preferences(path: Path) -> InferencePreferences:
    try:
        value = read_private_json(path)
    except FileNotFoundError:
        return InferencePreferences()
    return InferencePreferences.model_validate(value)


def save_inference_preferences(path: Path, preferences: InferencePreferences) -> None:
    write_private_json(path, preferences.model_dump(mode="json"))
