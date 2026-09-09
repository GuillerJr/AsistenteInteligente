from __future__ import annotations

import json
import os
import stat
from collections import Counter
from enum import StrEnum
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

SCHEMA_VERSION = "1.0"
MAX_MANIFEST_BYTES = 64 * 1_024
REQUIRED_CHANGE_GATES = frozenset({"adr", "owner_approval", "tests", "threat_model"})
REQUIRED_CAPABILITIES = frozenset(
    {
        "active_vision",
        "engineering_cli",
        "hybrid_brain",
        "local_conversation",
        "memory",
        "native_automation",
        "security_broker",
        "voice_interaction",
    }
)


class ReleaseScopeError(RuntimeError):
    """The checked-in product scope is absent, unsafe, or internally inconsistent."""


class CapabilityState(StrEnum):
    STABLE = "stable"
    CONDITIONAL = "conditional"
    DEFERRED_CERTIFICATION = "deferred_certification"


class ReleaseCapability(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    capability_id: str = Field(pattern=r"^[a-z][a-z0-9_]{2,47}$")
    state: CapabilityState
    surfaces: tuple[str, ...] = Field(min_length=1, max_length=6)
    acceptance_gate: str = Field(min_length=3, max_length=64)
    network: str = Field(pattern=r"^(none|local_only|optional_authenticated)$")
    reason: str = Field(min_length=8, max_length=240)

    @model_validator(mode="after")
    def validate_surfaces(self) -> ReleaseCapability:
        if len(set(self.surfaces)) != len(self.surfaces) or any(
            not surface.isascii()
            or not surface
            or len(surface) > 32
            or not surface.replace("_", "").isalnum()
            for surface in self.surfaces
        ):
            raise ValueError("capability surfaces are invalid")
        if self.state is CapabilityState.DEFERRED_CERTIFICATION and not self.acceptance_gate:
            raise ValueError("deferred capability requires an acceptance gate")
        return self


class ChangePolicy(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    approval_owner: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_-]{2,31}$")
    required_gates: tuple[str, ...] = Field(min_length=4, max_length=8)
    feature_additions_frozen: bool

    @model_validator(mode="after")
    def validate_gates(self) -> ChangePolicy:
        if set(self.required_gates) != REQUIRED_CHANGE_GATES:
            raise ValueError("scope change gates are incomplete")
        if not self.feature_additions_frozen:
            raise ValueError("release candidate feature additions must remain frozen")
        return self


class ReleaseScopeManifest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: str = Field(pattern=r"^1\.0$")
    release_line: str = Field(pattern=r"^1\.0-rc$")
    technical_namespace: str = Field(pattern=r"^aegis$")
    product_name: str = Field(pattern=r"^Jarvis$")
    target: str = Field(pattern=r"^macos-apple-silicon$")
    principles: tuple[str, ...] = Field(min_length=4, max_length=8)
    capabilities: tuple[ReleaseCapability, ...] = Field(min_length=8, max_length=24)
    prohibited_expansion: tuple[str, ...] = Field(min_length=3, max_length=12)
    change_policy: ChangePolicy

    @model_validator(mode="after")
    def validate_contract(self) -> ReleaseScopeManifest:
        identifiers = tuple(item.capability_id for item in self.capabilities)
        if len(set(identifiers)) != len(identifiers):
            raise ValueError("release scope contains duplicate capabilities")
        if not REQUIRED_CAPABILITIES.issubset(identifiers):
            raise ValueError("release scope omits a required capability")
        if len(set(self.principles)) != len(self.principles):
            raise ValueError("release principles contain duplicates")
        if len(set(self.prohibited_expansion)) != len(self.prohibited_expansion):
            raise ValueError("prohibited expansion contains duplicates")
        return self

    def report(self) -> dict[str, object]:
        counts = Counter(item.state.value for item in self.capabilities)
        return {
            "schema_version": SCHEMA_VERSION,
            "profile": "jarvis_v1_scope_freeze",
            "release_line": self.release_line,
            "status": "passed",
            "gate_passed": True,
            "capabilities": len(self.capabilities),
            "states": dict(sorted(counts.items())),
            "feature_additions_frozen": self.change_policy.feature_additions_frozen,
            "approval_owner": self.change_policy.approval_owner,
            "privacy": {
                "contains_credentials": False,
                "contains_prompts": False,
                "contains_transcripts": False,
                "network_calls": 0,
            },
        }


def load_release_scope(path: Path) -> ReleaseScopeManifest:
    try:
        metadata = path.stat(follow_symlinks=False)
        if (
            not stat.S_ISREG(metadata.st_mode)
            or path.is_symlink()
            or metadata.st_uid != os.getuid()
            or metadata.st_size <= 0
            or metadata.st_size > MAX_MANIFEST_BYTES
        ):
            raise ReleaseScopeError("release scope manifest is unsafe")
        payload = path.read_bytes()
        manifest = ReleaseScopeManifest.model_validate_json(payload)
    except (OSError, ValidationError) as error:
        raise ReleaseScopeError("release scope manifest is invalid") from error
    return manifest


def release_scope_json(path: Path) -> str:
    return json.dumps(
        load_release_scope(path).report(),
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    )
