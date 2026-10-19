"""Encrypted transport state. Delivery recovery never replays tools or approvals."""

from __future__ import annotations

import base64
import json
import os
import stat
from pathlib import Path
from typing import Any, Literal
from uuid import UUID, uuid4

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

    @model_validator(mode="before")
    @classmethod
    def read_previous_delivery_format(cls, value: Any) -> Any:
        # The retired generator wrote these fields into every encrypted outbox entry.
        # Discard only its metadata, preserving text delivery and unknown-field checks.
        if isinstance(value, dict):
            return {
                key: item
                for key, item in value.items()
                if key not in {"artifact_tokens", "next_artifact"}
            }
        return value


class InterruptedRun(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    conversation_id: UUID
    request_id: UUID
    job_id: UUID | None = None
    baseline: int = Field(ge=0)
    created: float
    request_text: str = Field(default="", max_length=8192, repr=False)
    submission_started: bool = Field(default=True, strict=True)
    input_recovery_allowed: bool = Field(default=False, strict=True)


class InboxEntry(BaseModel):
    """Only an owner message; callbacks and command authority never enter the inbox."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    update_id: int = Field(ge=0, lt=2**53, strict=True)
    message: dict[str, Any] = Field(repr=False)
    conversation_id: UUID | None = None
    claimed: bool = Field(default=False, strict=True)
    prepared_text: str | None = Field(default=None, max_length=8192, repr=False)
    received_while_active: bool = Field(default=False, strict=True)

    @model_validator(mode="after")
    def bounded_owner_message(self) -> InboxEntry:
        if self.prepared_text is not None and len(self.prepared_text.encode("utf-8")) > 8192:
            raise ValueError("Telegram prepared input capacity reached")
        if len(json.dumps(self.message, ensure_ascii=False).encode("utf-8")) > 16384:
            raise ValueError("Telegram inbox message capacity reached")
        if set(self.message) - {
            "date",
            "message_id",
            "from",
            "chat",
            "text",
            "caption",
            "reply_to_message",
            "voice",
            "document",
            "photo",
        }:
            raise ValueError("Unexpected inbox message fields")
        return self


class TransportState(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    bot_id: int | None = None
    owner_id: int | None = None
    references: tuple[MessageReference, ...] = Field(default=(), max_length=16)
    deliveries: tuple[Delivery, ...] = Field(default=(), max_length=3)
    interrupted: InterruptedRun | None = None
    acknowledged_offset: int = Field(default=0, ge=0, le=2**53, strict=True)
    inbox: tuple[InboxEntry, ...] = Field(default=(), max_length=4)

    @model_validator(mode="after")
    def inbox_is_unique_and_acknowledged(self) -> TransportState:
        identifiers = [entry.update_id for entry in self.inbox]
        if len(set(identifiers)) != len(identifiers) or any(
            identifier >= self.acknowledged_offset for identifier in identifiers
        ):
            raise ValueError("Invalid Telegram inbox acknowledgement")
        return self


class TelegramJournal:
    def __init__(self, path: Path, cipher: MemoryRowCipher) -> None:
        self.path, self.cipher = path, cipher

    def load(self) -> TransportState:
        if not self.path.exists() and not self.path.is_symlink():
            return TransportState()
        value = read_private_json(self.path)
        try:
            document = self.cipher.open(
                namespace="telegram-transport-v1",
                memory_id="state",
                nonce=base64.b64decode(value["nonce"], validate=True),
                ciphertext=base64.b64decode(value["ciphertext"], validate=True),
            )
        except RecursionError:
            raise ValueError("Telegram journal nesting limit exceeded") from None
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

    def reset(self, state: TransportState) -> None:
        """Explicit local recovery; retain the invalid private file for diagnosis."""
        if self.path.parent.resolve() != self.path.parent:
            raise ValueError("Invalid Telegram journal location")
        backup = None
        if self.path.exists() or self.path.is_symlink():
            metadata = self.path.lstat()
            if (
                not stat.S_ISREG(metadata.st_mode)
                or metadata.st_uid != os.getuid()
                or metadata.st_mode & 0o077
            ):
                raise ValueError("Invalid private Telegram journal")
            backup = self.path.with_name(f"{self.path.name}.invalid-{uuid4().hex}")
            os.replace(self.path, backup)
        try:
            self.save(state)
        except BaseException:
            if backup is not None:
                os.replace(backup, self.path)
            raise
