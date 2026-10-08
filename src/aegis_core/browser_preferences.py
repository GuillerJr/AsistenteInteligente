"""Private browser defaults, separate from per-task access and remote consent."""

from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from aegis_core.config import Settings
from aegis_core.settings_store import read_private_json, write_private_json

BrowserAutomationMode = Literal["extension", "isolated"]


class BrowserAutomationPreferences(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    schema_version: int = Field(default=1, strict=True, ge=1, le=1)
    enabled: bool = Field(default=True, strict=True)
    mode: BrowserAutomationMode = "extension"


def browser_automation_path(settings: Settings) -> Path:
    return settings.ipc_socket_path.parent / "browser_automation.json"


def load_browser_automation_preferences(path: Path) -> BrowserAutomationPreferences:
    if path.parent.resolve() != path.parent:
        raise ValueError("Browser preferences directory must not contain symlinks")
    if not path.exists() and not path.is_symlink():
        return BrowserAutomationPreferences()
    return BrowserAutomationPreferences.model_validate(read_private_json(path))


def save_browser_automation_preferences(
    path: Path, preferences: BrowserAutomationPreferences
) -> None:
    # Revalidate copies as well: Pydantic model_copy(update=...) does not validate.
    checked = BrowserAutomationPreferences.model_validate(preferences.model_dump(mode="json"))
    write_private_json(path, checked.model_dump(mode="json"))
