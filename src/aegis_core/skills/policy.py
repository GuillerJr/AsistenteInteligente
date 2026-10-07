"""Owner choices authorize sharing exact skill content, never tool execution."""

from __future__ import annotations

import hashlib
import json
import re
import threading
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

from aegis_core.settings_store import read_private_json, write_private_json
from aegis_core.skills.contracts import SKILL_ID_PATTERN, SkillManifest, SkillOrigin

MAX_SKILL_PREVIEW_BYTES = 10_240
MAX_SKILL_POLICY_CHOICES = 512


def skill_content_sha256(manifest: SkillManifest) -> str:
    content = manifest.model_dump(mode="json")
    for key in ("trigger_terms", "allowed_tools", "starter_tools"):
        content[key] = sorted(content[key])
    return hashlib.sha256(
        json.dumps(
            content, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
        ).encode()
    ).hexdigest()


def skill_preview_bytes(manifest: SkillManifest) -> int:
    # Use the complete IPC preview, with the longest boolean encoding. Approval
    # never covers a truncated body or a larger preview hidden by catalogue costs.
    preview = {
        "skill_id": manifest.skill_id,
        "name": manifest.name,
        "description": manifest.description,
        "origin": manifest.origin.value,
        "role": manifest.role.value,
        "enabled": False,
        "content_sha256": skill_content_sha256(manifest),
        "remote_allowed": False,
        "remote_authorized": False,
        "builtin_public": False,
        "approval_required": False,
        "instruction_count": len(manifest.instructions),
        "resource_count": len(manifest.resources),
        "preview_bytes": MAX_SKILL_PREVIEW_BYTES,
        "preview_available": False,
        "source_version": manifest.source_version,
        "adaptation_required": False,
        "allowed_tools": sorted(manifest.allowed_tools),
        "starter_tools": sorted(manifest.starter_tools),
        "instructions": list(manifest.instructions),
        "resources": list(manifest.resources),
    }
    return len(json.dumps(preview, ensure_ascii=False).encode())


class SkillPolicyChoice(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    enabled: bool | None = Field(default=None, strict=True)
    approved_content_sha256: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")
    remote_denied: bool = Field(default=False, strict=True)


class SkillPolicyDocument(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    schema_version: int = Field(default=1, ge=1, le=1, strict=True)
    choices: dict[str, SkillPolicyChoice] = Field(
        default_factory=dict, max_length=MAX_SKILL_POLICY_CHOICES
    )

    @field_validator("choices")
    @classmethod
    def valid_ids(cls, value: dict[str, SkillPolicyChoice]) -> dict[str, SkillPolicyChoice]:
        if any(re.fullmatch(SKILL_ID_PATTERN, key) is None for key in value):
            raise ValueError("invalid skill policy identity")
        return value


class SkillPolicyStore:
    def __init__(self, path: Path) -> None:
        self.path = path
        self._lock = threading.RLock()
        self._valid = True

    def _load(self) -> SkillPolicyDocument:
        try:
            if self.path.parent.resolve() != self.path.parent:
                self._valid = False
                return SkillPolicyDocument()
            document = SkillPolicyDocument.model_validate(read_private_json(self.path))
        except FileNotFoundError:
            self._valid = not self.path.is_symlink()
            return SkillPolicyDocument()
        except (OSError, ValueError, RecursionError, RuntimeError):
            self._valid = False
            return SkillPolicyDocument()
        self._valid = True
        return document

    @property
    def valid(self) -> bool:
        with self._lock:
            self._load()
            return self._valid

    def status(self) -> dict[str, Any]:
        valid = self.valid
        return {"valid": valid, "state": "ready" if valid else "invalid"}

    def enabled(self, manifest: SkillManifest) -> bool:
        with self._lock:
            document = self._load()
            choice = document.choices.get(manifest.skill_id, SkillPolicyChoice())
            return (
                self._valid
                and not manifest.adaptation_required
                and (manifest.enabled if choice.enabled is None else choice.enabled)
            )

    def _remote_authorized(self, manifest: SkillManifest, choice: SkillPolicyChoice) -> bool:
        if (
            not self._valid
            or choice.remote_denied
            or skill_preview_bytes(manifest) > MAX_SKILL_PREVIEW_BYTES
        ):
            return False
        return (
            manifest.origin is SkillOrigin.BUILTIN and manifest.remote_safe
        ) or choice.approved_content_sha256 == skill_content_sha256(manifest)

    def remote_authorized(self, manifest: SkillManifest) -> bool:
        with self._lock:
            document = self._load()
            return self._remote_authorized(
                manifest, document.choices.get(manifest.skill_id, SkillPolicyChoice())
            )

    def remote_allowed(self, manifest: SkillManifest) -> bool:
        with self._lock:
            document = self._load()
            choice = document.choices.get(manifest.skill_id, SkillPolicyChoice())
            enabled = manifest.enabled if choice.enabled is None else choice.enabled
            return (
                enabled
                and not manifest.adaptation_required
                and self._remote_authorized(manifest, choice)
            )

    def preflight_choice(self, skill_id: str) -> None:
        """Validate import policy capacity before a caller writes a new skill file."""
        if re.fullmatch(SKILL_ID_PATTERN, skill_id) is None:
            raise ValueError("invalid skill policy identity")
        with self._lock:
            document = self._load()
            if not self._valid:
                raise ValueError("skill policy is invalid; repair it before making changes")
            if (
                skill_id not in document.choices
                and len(document.choices) >= MAX_SKILL_POLICY_CHOICES
            ):
                raise ValueError("skill policy capacity reached")

    def set_choice(
        self,
        manifest: SkillManifest,
        content_sha256: str,
        *,
        enabled: bool | None = None,
        approved: bool | None = None,
    ) -> None:
        if content_sha256 != skill_content_sha256(manifest):
            raise ValueError("skill content changed; review the current preview")
        if enabled is not None and type(enabled) is not bool:
            raise ValueError("invalid skill enabled choice")
        if approved is not None and type(approved) is not bool:
            raise ValueError("invalid skill sharing choice")
        if approved and skill_preview_bytes(manifest) > MAX_SKILL_PREVIEW_BYTES:
            raise ValueError("skill exceeds the complete preview sharing limit")
        with self._lock:
            document = self._load()
            if not self._valid:
                raise ValueError("skill policy is invalid; repair it before making changes")
            choices = dict(document.choices)
            previous = choices.get(manifest.skill_id, SkillPolicyChoice())
            update: dict[str, Any] = {}
            if enabled is not None:
                update["enabled"] = enabled
            if approved is not None:
                update.update(
                    approved_content_sha256=content_sha256 if approved else None,
                    remote_denied=not approved,
                )
            choices[manifest.skill_id] = previous.model_copy(update=update)
            saved = SkillPolicyDocument(choices=choices)
            write_private_json(self.path, saved.model_dump(mode="json"))
