from __future__ import annotations

from pathlib import Path
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from aegis_core.settings_store import read_private_json, write_private_json


class TelegramConfiguration(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    version: int = Field(default=1, ge=1, le=1)
    enabled: bool = Field(default=False, strict=True)
    bot_id: int | None = Field(default=None, gt=0, le=2**52, strict=True)
    bot_username: str | None = Field(default=None, pattern=r"^[A-Za-z0-9_]{5,32}$")
    owner_id: int | None = Field(default=None, gt=0, le=2**52, strict=True)
    owner_name: str | None = Field(default=None, max_length=100)
    conversation_id: UUID | None = None
    offset: int = Field(default=0, ge=0, le=2**53, strict=True)
    discard_backlog: bool = Field(default=False, strict=True)

    @model_validator(mode="after")
    def binding_is_complete(self) -> TelegramConfiguration:
        if self.enabled and (self.bot_id is None or self.owner_id is None):
            raise ValueError("Telegram owner binding is incomplete")
        if (self.bot_id is None) != (self.bot_username is None):
            raise ValueError("Telegram bot identity is incomplete")
        if self.owner_id is None and (self.owner_name or self.conversation_id):
            raise ValueError("Telegram conversation requires an owner")
        return self


class TelegramConfigurationStore:
    def __init__(self, path: Path) -> None:
        self.path = path

    def load(self) -> TelegramConfiguration:
        if not self.path.exists() and not self.path.is_symlink():
            return TelegramConfiguration()
        return TelegramConfiguration.model_validate(read_private_json(self.path))

    def save(self, value: TelegramConfiguration) -> None:
        write_private_json(self.path, value.model_dump(mode="json"))
