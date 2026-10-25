"""Explicit agent profiles for the own Jarvis base; no discovery or provider fallback."""

from __future__ import annotations

import hashlib
import json
import re
import time
from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4

from jarvis_core.private_json import PrivateJsonDocument


class ProfileError(ValueError):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def agent_identity(value: str) -> str:
    if not isinstance(value, str) or re.fullmatch(r"[a-z][a-z0-9_-]{0,63}", value) is None:
        raise ProfileError("agent_id_invalid")
    return value


def _text(value, limit: int, *, empty: bool = False) -> str:
    try:
        if (
            not isinstance(value, str)
            or "\0" in value
            or len(value.encode()) > limit
            or (not empty and not value.strip())
        ):
            raise ProfileError("agent_text_invalid")
    except UnicodeError:
        raise ProfileError("agent_text_invalid") from None
    return value


def _revision(value) -> int:
    if type(value) is not int or value < 1:
        raise ProfileError("agent_revision_invalid")
    return value


@dataclass(frozen=True)
class AgentProfile:
    agent_id: str
    name: str
    instructions: str
    revision: int
    created_ns: int
    updated_ns: int

    @property
    def fingerprint(self) -> str:
        return hashlib.sha256(
            json.dumps(
                [self.agent_id, self.name, self.instructions, self.revision],
                ensure_ascii=False,
                separators=(",", ":"),
            ).encode()
        ).hexdigest()


def _initial() -> dict:
    return {
        "version": 1,
        "agents": {
            "jarvis": {
                "name": "Jarvis",
                "instructions": "",
                "revision": 1,
                "created_ns": 0,
                "updated_ns": 0,
            }
        },
    }


def _validate(value) -> None:
    if (
        not isinstance(value, dict)
        or set(value) != {"version", "agents"}
        or type(value["version"]) is not int
        or value["version"] != 1
        or not isinstance(value["agents"], dict)
        or "jarvis" not in value["agents"]
        or not 1 <= len(value["agents"]) <= 32
    ):
        raise ProfileError("agent_registry_invalid")
    for agent_id, row in value["agents"].items():
        agent_identity(agent_id)
        if not isinstance(row, dict) or set(row) != {
            "name",
            "instructions",
            "revision",
            "created_ns",
            "updated_ns",
        }:
            raise ProfileError("agent_registry_invalid")
        _text(row["name"], 256)
        _text(row["instructions"], 8192, empty=True)
        _revision(row["revision"])
        if (
            any(type(row[key]) is not int or row[key] < 0 for key in ("created_ns", "updated_ns"))
            or row["updated_ns"] < row["created_ns"]
        ):
            raise ProfileError("agent_registry_invalid")


class ProfilesStore:
    """Host configuration; instructions are context, never tool authority.

    All profiles share the explicit workspace selected by the host. This store
    cannot select a provider, read a workspace, install tools or start an agent.
    """

    def __init__(self, directory: Path) -> None:
        self.document = PrivateJsonDocument(
            directory, "agents.json", initial=_initial, validate=_validate, max_bytes=524288
        )

    @staticmethod
    def _record(agent_id: str, value: dict) -> AgentProfile:
        return AgentProfile(agent_id, **value)

    def list(self) -> tuple[AgentProfile, ...]:
        rows = self.document.read()["agents"]
        return tuple(self._record(key, rows[key]) for key in sorted(rows))

    def get(self, agent_id: str) -> AgentProfile:
        agent_identity(agent_id)
        row = self.document.read()["agents"].get(agent_id)
        if row is None:
            raise ProfileError("agent_not_found")
        return self._record(agent_id, row)

    def create(
        self, name: str, instructions: str = "", *, agent_id: str | None = None
    ) -> AgentProfile:
        identity = agent_identity("agent_" + uuid4().hex if agent_id is None else agent_id)
        _text(name, 256)
        _text(instructions, 8192, empty=True)
        now = time.time_ns()

        def change(value):
            rows = value["agents"]
            if identity in rows:
                raise ProfileError("agent_already_exists")
            if len(rows) >= 32:
                raise ProfileError("agent_limit")
            rows[identity] = {
                "name": name,
                "instructions": instructions,
                "revision": 1,
                "created_ns": now,
                "updated_ns": now,
            }
            return value

        result = self.document.update(change)
        return self._record(identity, result["agents"][identity])

    def update(
        self,
        agent_id: str,
        *,
        expected_revision: int,
        name: str | None = None,
        instructions: str | None = None,
    ) -> AgentProfile:
        agent_identity(agent_id)
        _revision(expected_revision)
        if name is not None:
            _text(name, 256)
        if instructions is not None:
            _text(instructions, 8192, empty=True)

        def change(value):
            row = value["agents"].get(agent_id)
            if row is None:
                raise ProfileError("agent_not_found")
            if row["revision"] != expected_revision:
                raise ProfileError("agent_revision_conflict")
            new_name = row["name"] if name is None else name
            new_instructions = row["instructions"] if instructions is None else instructions
            if (new_name, new_instructions) != (row["name"], row["instructions"]):
                row.update(
                    name=new_name,
                    instructions=new_instructions,
                    revision=row["revision"] + 1,
                    updated_ns=max(time.time_ns(), row["updated_ns"]),
                )
            return value

        result = self.document.update(change)
        return self._record(agent_id, result["agents"][agent_id])

    def delete(self, agent_id: str, *, expected_revision: int) -> None:
        agent_identity(agent_id)
        _revision(expected_revision)
        if agent_id == "jarvis":
            raise ProfileError("default_agent_required")

        def change(value):
            row = value["agents"].get(agent_id)
            if row is None:
                raise ProfileError("agent_not_found")
            if row["revision"] != expected_revision:
                raise ProfileError("agent_revision_conflict")
            del value["agents"][agent_id]
            return value

        self.document.update(change)
