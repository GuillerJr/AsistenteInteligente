"""Encrypted transport state. Delivery recovery never replays tools or approvals."""

from __future__ import annotations

import base64
from pathlib import Path
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from aegis_core.memory.crypto import MemoryRowCipher
from aegis_core.memory.task_checkpoint import ResultReference
from aegis_core.settings_store import read_private_json, write_private_json


class MessageReference(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    token: str = Field(pattern=r"^[A-Za-z0-9_-]{16,32}$")
    conversation_id: UUID
    message_ids: tuple[int, ...] = Field(default=(), max_length=20)
    content: str = Field(max_length=4096)
    content_truncated: bool = False
    resources: tuple[ResultReference, ...] = Field(default=(), max_length=10)
    resource_mode: Literal["list", "mail_detail"] | None = None
    created: float
    question: bool = False


class Delivery(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    token: str
    conversation_id: UUID
    text: str = Field(max_length=32768)
    next_chunk: int = Field(default=0, ge=0, le=20)
    message_ids: tuple[int, ...] = Field(default=(), max_length=20)
    created: float
    uncertain: bool = False
    artifact_tokens: tuple[Annotated[str, Field(pattern=r"^[0-9a-f]{32}$")], ...] = Field(
        default=(), max_length=4
    )
    next_artifact: int = Field(default=0, ge=0, le=4)

    @model_validator(mode="after")
    def valid_artifact_cursor(self) -> Delivery:
        if self.next_artifact > len(self.artifact_tokens):
            raise ValueError("invalid artifact delivery cursor")
        return self


class InterruptedRun(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    conversation_id: UUID
    request_id: UUID
    job_id: UUID | None = None
    baseline: int = Field(ge=0)
    created: float


class TransportState(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    bot_id: int | None = None
    owner_id: int | None = None
    references: tuple[MessageReference, ...] = Field(default=(), max_length=16)
    deliveries: tuple[Delivery, ...] = Field(default=(), max_length=3)
    interrupted: InterruptedRun | None = None


class TelegramJournal:
    def __init__(self, path: Path, cipher: MemoryRowCipher) -> None:
        self.path, self.cipher = path, cipher

    def load(self) -> TransportState:
        if not self.path.exists() and not self.path.is_symlink():
            return TransportState()
        value = read_private_json(self.path)
        document = self.cipher.open(
            namespace="telegram-transport-v1",
            memory_id="state",
            nonce=base64.b64decode(value["nonce"], validate=True),
            ciphertext=base64.b64decode(value["ciphertext"], validate=True),
        )
        return TransportState.model_validate(document)

    def save(self, state: TransportState) -> None:
        # Fits the existing private JSON reader even with the base64 envelope.
        if len(state.model_dump_json().encode("utf-8")) > 180_000:
            raise ValueError("Telegram journal capacity reached")
        sealed = self.cipher.seal(
            namespace="telegram-transport-v1",
            memory_id="state",
            document=state.model_dump(mode="json"),
        )
        write_private_json(
            self.path,
            {
                "nonce": base64.b64encode(sealed.nonce).decode(),
                "ciphertext": base64.b64encode(sealed.ciphertext).decode(),
            },
        )
