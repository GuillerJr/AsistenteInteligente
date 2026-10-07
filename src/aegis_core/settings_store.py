"""Validated, owner-only model preferences; credentials never belong in this file."""

from __future__ import annotations

import json
import os
import stat
import tempfile
from dataclasses import replace
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, model_validator

from aegis_core.contracts import AgentRole
from aegis_core.models import MODEL_REGISTRY, ModelSpec

MODEL_ID_PATTERN = r"^[a-z0-9][a-z0-9._-]*/[a-z0-9][a-z0-9._-]*$"


def read_private_json(path: Path) -> dict:
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        metadata = os.fstat(descriptor)
        if (
            not stat.S_ISREG(metadata.st_mode)
            or metadata.st_uid != os.getuid()
            or metadata.st_mode & 0o077
            or not 0 < metadata.st_size <= 262_144
        ):
            raise ValueError("Invalid private configuration")
        with os.fdopen(descriptor, "rb", closefd=False) as handle:
            try:
                value = json.loads(handle.read(262_145))
            except RecursionError:
                raise ValueError("Private JSON nesting limit exceeded") from None
        if not isinstance(value, dict):
            raise ValueError("Configuration must be an object")
        return value
    finally:
        os.close(descriptor)


def write_private_json(path: Path, value: dict) -> None:
    if path.parent.resolve() != path.parent or path.is_symlink():
        raise ValueError("Configuration location must not contain symlinks")
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    if path.exists():
        read_private_json(path)
    descriptor, temporary = tempfile.mkstemp(prefix=".settings-", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w") as handle:
            json.dump(value, handle, ensure_ascii=True, indent=2, allow_nan=False)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        directory = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


class SelectableModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    model_id: str = Field(pattern=MODEL_ID_PATTERN, max_length=256)
    images: bool = Field(default=False, strict=True)
    tools: bool = Field(default=False, strict=True)


class RoleSelection(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    primary: str = Field(pattern=MODEL_ID_PATTERN, max_length=256)
    fallback: str | None = Field(default=None, pattern=MODEL_ID_PATTERN, max_length=256)


class ModelPreferences(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    version: int = Field(default=1, ge=1, le=1)
    models: tuple[SelectableModel, ...] = Field(min_length=1, max_length=64)
    roles: dict[AgentRole, RoleSelection]

    @model_validator(mode="after")
    def assignments_must_be_compatible(self) -> ModelPreferences:
        catalogue = {item.model_id: item for item in self.models}
        if len(catalogue) != len(self.models) or set(self.roles) != set(AgentRole):
            raise ValueError("Every task must have a model, with no duplicate entries")
        for role, selection in self.roles.items():
            for identifier in (selection.primary, selection.fallback):
                if identifier is None:
                    continue
                model = catalogue.get(identifier)
                if model is None:
                    raise ValueError("Assigned model is missing from the catalogue")
                if (
                    role
                    in {
                        AgentRole.ROUTER,
                        AgentRole.PLANNER,
                        AgentRole.CRITICAL_REASONER,
                        AgentRole.CODE_SECURITY,
                    }
                    and not model.tools
                ):
                    raise ValueError("This task requires tools")
                if role in {AgentRole.VISION, AgentRole.OMNI} and not model.images:
                    raise ValueError("This task requires images")
            if selection.primary == selection.fallback:
                raise ValueError("Fallback must differ from primary")
        return self


def default_model_preferences() -> ModelPreferences:
    catalogue: dict[str, SelectableModel] = {}
    for role, spec in MODEL_REGISTRY.items():
        for identifier in (spec.model_id, spec.fallback_model_id):
            if identifier is not None:
                existing = catalogue.get(identifier)
                catalogue[identifier] = SelectableModel(
                    model_id=identifier,
                    images=role in {AgentRole.VISION, AgentRole.OMNI}
                    or bool(existing and existing.images),
                    tools=spec.tool_calling or bool(existing and existing.tools),
                )
    return ModelPreferences(
        models=tuple(catalogue.values()),
        roles={
            role: RoleSelection(primary=spec.model_id, fallback=spec.fallback_model_id)
            for role, spec in MODEL_REGISTRY.items()
        },
    )


def load_model_preferences(path: Path) -> ModelPreferences:
    if not path.exists() and not path.is_symlink():
        return default_model_preferences()
    return ModelPreferences.model_validate(read_private_json(path))


def selected_model_registry(preferences: ModelPreferences) -> dict[AgentRole, ModelSpec]:
    # Preserve each task's modality and context contract; changing its provider
    # does not grant tools or alter the application's authorization policy.
    return {
        role: replace(
            MODEL_REGISTRY[role], model_id=selection.primary, fallback_model_id=selection.fallback
        )
        for role, selection in preferences.roles.items()
    }
