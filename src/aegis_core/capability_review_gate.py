from __future__ import annotations

import hashlib
import json
import os
import re
import stat
from enum import StrEnum
from ipaddress import ip_address
from pathlib import Path
from typing import Annotated, Any, Literal
from urllib.parse import urlparse

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    ValidationError,
    field_validator,
    model_validator,
)

from aegis_core.capability_learning import CapabilityRecord
from aegis_core.capability_reviews import (
    CapabilityReviewDossier,
    CapabilityReviewGate,
    build_capability_review_dossier,
)
from aegis_core.contracts import Capability
from aegis_core.plugins.contracts import (
    PLUGIN_ALLOWED_CAPABILITIES,
    validate_plugin_json_schema,
)
from aegis_core.secrets import contains_likely_secret_material

MAX_CAPABILITY_REVIEW_BYTES = 16_384
ReviewIndex = Annotated[int, Field(strict=True, ge=0, le=7)]


class CapabilityReviewError(ValueError):
    """Raised when a capability review does not satisfy its dossier."""


class CapabilityReviewDecision(StrEnum):
    ACCEPT_DESIGN = "accept_design"
    REJECT_DESIGN = "reject_design"


class CapabilityReviewStatus(StrEnum):
    READY_FOR_MANUAL_IMPLEMENTATION = "ready_for_manual_implementation"
    REJECTED = "rejected"


class CapabilityReviewSubmission(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    schema_version: int = Field(default=1, ge=1, le=1)
    gap_id: str = Field(pattern=r"^[0-9a-f]{64}$")
    dossier_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    decision: CapabilityReviewDecision
    review_gate: CapabilityReviewGate
    completed_inputs: dict[str, str] = Field(default_factory=dict, max_length=6)
    accepted_security_checks: tuple[ReviewIndex, ...] = Field(default_factory=tuple, max_length=8)
    passed_acceptance_tests: tuple[ReviewIndex, ...] = Field(default_factory=tuple, max_length=8)
    test_evidence_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    security_review_reference: str | None = Field(default=None, min_length=8, max_length=200)
    rationale: str = Field(min_length=8, max_length=500)
    acknowledges_no_execution: Literal[True]

    @field_validator("completed_inputs")
    @classmethod
    def inputs_must_be_bounded_and_non_secret(cls, values: dict[str, str]) -> dict[str, str]:
        for key, value in values.items():
            if (
                re.fullmatch(r"^[a-z][a-z0-9_]{2,63}$", key) is None
                or not 1 <= len(value) <= 2_048
                or value != " ".join(value.split())
                or not value.isprintable()
                or contains_likely_secret_material(value)
            ):
                raise ValueError("capability review input is unsafe or malformed")
        return values

    @field_validator("rationale", "security_review_reference")
    @classmethod
    def prose_must_be_normalized_and_non_secret(cls, value: str | None) -> str | None:
        if value is None:
            return None
        if (
            value != " ".join(value.split())
            or not value.isprintable()
            or contains_likely_secret_material(value)
        ):
            raise ValueError("capability review prose is unsafe or malformed")
        return value

    @model_validator(mode="after")
    def rejection_must_not_claim_validation(self) -> CapabilityReviewSubmission:
        if self.decision is CapabilityReviewDecision.REJECT_DESIGN and (
            self.completed_inputs
            or self.accepted_security_checks
            or self.passed_acceptance_tests
            or self.test_evidence_sha256 is not None
            or self.security_review_reference is not None
        ):
            raise ValueError("rejected capability review must not claim completed validation")
        if len(self.accepted_security_checks) != len(set(self.accepted_security_checks)) or len(
            self.passed_acceptance_tests
        ) != len(set(self.passed_acceptance_tests)):
            raise ValueError("capability review indexes must be unique")
        return self


class CapabilityReviewVerdict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: int = Field(default=1, ge=1, le=1)
    gap_id: str = Field(pattern=r"^[0-9a-f]{64}$")
    dossier_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    submission_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    decision: CapabilityReviewDecision
    review_gate: CapabilityReviewGate
    status: CapabilityReviewStatus
    reviewed_input_keys: tuple[str, ...] = Field(default_factory=tuple, max_length=6)
    execution_allowed: Literal[False] = False
    integrity_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def verdict_must_be_consistent_and_intact(self) -> CapabilityReviewVerdict:
        expected = (
            CapabilityReviewStatus.READY_FOR_MANUAL_IMPLEMENTATION
            if self.decision is CapabilityReviewDecision.ACCEPT_DESIGN
            else CapabilityReviewStatus.REJECTED
        )
        if self.status is not expected:
            raise ValueError("capability review verdict status does not match decision")
        if self.integrity_sha256 != _canonical_digest(
            self.model_dump(mode="json", exclude={"integrity_sha256"})
        ):
            raise ValueError("capability review verdict integrity does not match")
        return self

    @classmethod
    def create(
        cls,
        dossier: CapabilityReviewDossier,
        submission: CapabilityReviewSubmission,
    ) -> CapabilityReviewVerdict:
        values = {
            "schema_version": 1,
            "gap_id": dossier.gap_id,
            "dossier_sha256": dossier.integrity_sha256,
            "submission_sha256": _canonical_digest(submission.model_dump(mode="json")),
            "decision": submission.decision,
            "review_gate": dossier.review_gate,
            "status": (
                CapabilityReviewStatus.READY_FOR_MANUAL_IMPLEMENTATION
                if submission.decision is CapabilityReviewDecision.ACCEPT_DESIGN
                else CapabilityReviewStatus.REJECTED
            ),
            "reviewed_input_keys": tuple(sorted(submission.completed_inputs)),
            "execution_allowed": False,
        }
        return cls(**values, integrity_sha256=_canonical_digest(values))


def evaluate_capability_review(
    record: CapabilityRecord,
    submission: CapabilityReviewSubmission,
) -> CapabilityReviewVerdict:
    dossier = build_capability_review_dossier(record)
    if submission.gap_id != dossier.gap_id or submission.dossier_sha256 != dossier.integrity_sha256:
        raise CapabilityReviewError("capability review does not match current dossier")
    if submission.review_gate is not dossier.review_gate:
        raise CapabilityReviewError("capability review gate does not match current dossier")
    if submission.decision is CapabilityReviewDecision.REJECT_DESIGN:
        return CapabilityReviewVerdict.create(dossier, submission)
    if dossier.review_gate is CapabilityReviewGate.RESEARCH_REQUIRED:
        raise CapabilityReviewError("capability requires research before design review")

    required_keys = {item.key for item in dossier.required_inputs}
    if set(submission.completed_inputs) != required_keys:
        raise CapabilityReviewError("capability review inputs are incomplete or excessive")
    _validate_special_inputs(submission.completed_inputs)
    if set(submission.accepted_security_checks) != set(range(len(dossier.security_checks))):
        raise CapabilityReviewError("capability security checks are incomplete")
    if set(submission.passed_acceptance_tests) != set(range(len(dossier.acceptance_tests))):
        raise CapabilityReviewError("capability acceptance tests are incomplete")
    if submission.test_evidence_sha256 is None:
        raise CapabilityReviewError("capability test evidence is missing")
    if dossier.review_gate is CapabilityReviewGate.SECURITY_REVIEW:
        if submission.security_review_reference is None:
            raise CapabilityReviewError("critical capability requires security review evidence")
    elif submission.security_review_reference is not None:
        raise CapabilityReviewError("non-critical capability cannot claim a security review")
    return CapabilityReviewVerdict.create(dossier, submission)


def load_capability_review_submission(path: Path) -> CapabilityReviewSubmission:
    if path.is_symlink() or not path.is_file():
        raise CapabilityReviewError("capability review source must be a regular file")
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as error:
        raise CapabilityReviewError("capability review source is unavailable") from error
    try:
        metadata = os.fstat(descriptor)
        if (
            not stat.S_ISREG(metadata.st_mode)
            or not 0 < metadata.st_size <= MAX_CAPABILITY_REVIEW_BYTES
        ):
            raise CapabilityReviewError("capability review source size or type is invalid")
        data = os.read(descriptor, MAX_CAPABILITY_REVIEW_BYTES + 1)
    finally:
        os.close(descriptor)
    try:
        return CapabilityReviewSubmission.model_validate_json(data)
    except (UnicodeError, ValidationError, ValueError) as error:
        raise CapabilityReviewError("capability review source is invalid") from error


def _validate_special_inputs(values: dict[str, str]) -> None:
    endpoint = values.get("public_https_endpoint")
    if endpoint is not None:
        parsed = urlparse(endpoint)
        hostname = parsed.hostname
        if (
            parsed.scheme != "https"
            or hostname is None
            or parsed.username is not None
            or parsed.password is not None
            or parsed.port not in {None, 443}
            or parsed.fragment
            or hostname in {"localhost", "localhost.localdomain"}
            or hostname.endswith(".local")
        ):
            raise CapabilityReviewError("capability endpoint is not public HTTPS")
        try:
            ip_address(hostname)
        except ValueError:
            pass
        else:
            raise CapabilityReviewError("capability endpoint cannot use an IP literal")

    auth_mode = values.get("auth_mode")
    if auth_mode is not None and auth_mode not in {"none", "bearer"}:
        raise CapabilityReviewError("capability auth mode is unsupported")

    declared = values.get("declared_capabilities")
    if declared is not None:
        names = declared.split(",")
        try:
            capabilities = frozenset(Capability(name) for name in names)
        except ValueError as error:
            raise CapabilityReviewError("capability declaration is unsupported") from error
        if (
            not capabilities
            or len(names) != len(capabilities)
            or not capabilities.issubset(PLUGIN_ALLOWED_CAPABILITIES)
        ):
            raise CapabilityReviewError("capability declaration is unsupported")

    for key in ("closed_input_schema", "bounded_output_schema"):
        encoded_schema = values.get(key)
        if encoded_schema is not None:
            _validate_closed_schema(encoded_schema)

    encoded_tools = values.get("closed_tool_schemas")
    if encoded_tools is not None:
        try:
            tools = json.loads(encoded_tools)
            if (
                not isinstance(tools, dict)
                or not 1 <= len(tools) <= 8
                or any(
                    re.fullmatch(r"^[a-z][a-z0-9_]{2,31}$", name) is None
                    or not isinstance(schema, dict)
                    for name, schema in tools.items()
                )
            ):
                raise ValueError
            for schema in tools.values():
                validate_plugin_json_schema(schema)
        except (TypeError, ValueError, json.JSONDecodeError) as error:
            raise CapabilityReviewError("capability tool schemas are invalid") from error

    shortcut_name = values.get("exact_shortcut_name")
    if shortcut_name is not None and (
        len(shortcut_name) > 128
        or "/" in shortcut_name
        or "\\" in shortcut_name
        or shortcut_name in {".", ".."}
    ):
        raise CapabilityReviewError("capability shortcut name is unsafe")


def _validate_closed_schema(encoded: str) -> None:
    try:
        schema = json.loads(encoded)
        if not isinstance(schema, dict):
            raise TypeError
        validate_plugin_json_schema(schema)
    except (TypeError, ValueError, json.JSONDecodeError) as error:
        raise CapabilityReviewError("capability schema is invalid") from error


def _canonical_digest(values: dict[str, Any]) -> str:
    payload = json.dumps(
        values,
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()
