from __future__ import annotations

import math
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from ipaddress import IPv4Network, IPv6Network
from pathlib import Path
from types import MappingProxyType

from pydantic import BaseModel, ValidationError

from aegis_core.contracts import (
    AgentRole,
    Capability,
    InputModality,
    PolicyDecision,
    RiskLevel,
    ToolAuthorization,
    ToolCall,
    ToolCallBasis,
    UserRequest,
)
from aegis_core.tools.audit import AuditSink, NullAuditSink
from aegis_core.tools.confirmations import ConfirmationStatus, ConfirmationStore


class PolicyViolation(ValueError):
    """Raised when validated arguments violate local policy."""


@dataclass(frozen=True, slots=True)
class PolicyContext:
    workspace_root: Path
    network_scopes: tuple[IPv4Network | IPv6Network, ...] = ()
    confirmation_store: ConfirmationStore | None = None
    now: datetime | None = None

    def __post_init__(self) -> None:
        if self.now is not None and (self.now.tzinfo is None or self.now.utcoffset() is None):
            raise ValueError("policy context time must be timezone-aware")

    def current_time(self) -> datetime:
        return self.now or datetime.now(UTC)


ArgumentGuard = Callable[[BaseModel, PolicyContext], BaseModel]


@dataclass(frozen=True, slots=True)
class ToolDefinition:
    name: str
    description: str
    arguments_model: type[BaseModel]
    capability: Capability
    risk: RiskLevel
    allowed_roles: frozenset[AgentRole]
    requires_confirmation: bool = False
    enabled: bool = True
    argument_guard: ArgumentGuard | None = None
    explicit_local_intent_is_sufficient: bool = False
    parameters_schema: dict[str, object] | None = None
    provider_label: str | None = None
    external_destination: str | None = None

    def openai_schema(self) -> dict[str, object]:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters_schema or self.arguments_model.model_json_schema(),
            },
        }


class ToolRegistry:
    def __init__(self, definitions: Iterable[ToolDefinition] = ()) -> None:
        registry: dict[str, ToolDefinition] = {}
        for definition in definitions:
            if definition.name in registry:
                raise ValueError(f"duplicate tool definition: {definition.name}")
            registry[definition.name] = definition
        self._definitions = MappingProxyType(registry)

    def get(self, name: str) -> ToolDefinition | None:
        return self._definitions.get(name)

    def values(self) -> tuple[ToolDefinition, ...]:
        return tuple(self._definitions.values())


RISK_ORDER = {
    RiskLevel.LOW: 0,
    RiskLevel.MEDIUM: 1,
    RiskLevel.HIGH: 2,
    RiskLevel.CRITICAL: 3,
}

MINIMUM_CRITICAL_VOICE_CONFIDENCE = 0.78
_UNTRUSTED_SPEAKER_LABELS = frozenset({"unknown", "untrusted", "background"})


class ToolBroker:
    def __init__(
        self,
        registry: ToolRegistry,
        role_capabilities: dict[AgentRole, frozenset[Capability]],
        *,
        audit_sink: AuditSink | None = None,
    ) -> None:
        self._registry = registry
        self._role_capabilities = MappingProxyType(dict(role_capabilities))
        self._audit = audit_sink or NullAuditSink()

    def schemas_for(
        self,
        role: AgentRole,
        *,
        names: frozenset[str] | None = None,
    ) -> list[dict[str, object]]:
        capabilities = self._role_capabilities.get(role, frozenset())
        return [
            definition.openai_schema()
            for definition in self._registry.values()
            if definition.enabled
            and role in definition.allowed_roles
            and definition.capability in capabilities
            and (names is None or definition.name in names)
        ]

    def definition(self, name: str) -> ToolDefinition | None:
        return self._registry.get(name)

    def authorize(
        self,
        call: ToolCall,
        context: PolicyContext,
        *,
        allowed_names: frozenset[str] | None = None,
        request: UserRequest | None = None,
    ) -> ToolAuthorization:
        digest = call.digest()
        definition = self._registry.get(call.tool_name)
        if definition is None:
            return self._deny(call, digest, "unknown_tool")
        if allowed_names is not None and call.tool_name not in allowed_names:
            return self._deny(call, digest, "tool_not_offered")
        if not definition.enabled:
            return self._deny(call, digest, "tool_disabled")
        if call.requested_by not in definition.allowed_roles:
            return self._deny(call, digest, "role_not_allowed")

        capabilities = self._role_capabilities.get(call.requested_by, frozenset())
        if definition.capability not in capabilities:
            return self._deny(call, digest, "capability_not_granted")

        try:
            normalized = definition.arguments_model.model_validate(call.arguments)
            if definition.argument_guard is not None:
                normalized = definition.argument_guard(normalized, context)
        except (ValidationError, PolicyViolation, ValueError):
            return self._deny(call, digest, "invalid_arguments")

        normalized_arguments = normalized.model_dump(mode="json")
        needs_confirmation = (
            definition.requires_confirmation
            or RISK_ORDER[definition.risk] >= RISK_ORDER[RiskLevel.HIGH]
        )
        biometric_manual_confirmation = False
        if needs_confirmation and request is not None and InputModality.AUDIO in request.modalities:
            trusted, confidence = self._trusted_owner_voice(request)
            if not trusted:
                posture = (
                    "deny"
                    if definition.risk is RiskLevel.CRITICAL
                    else "require_manual_confirmation"
                )
                self._audit.record_system_event(
                    request.request_id,
                    event_type="biometric_mismatch",
                    component="tool_broker",
                    call_id=call.call_id,
                    data={
                        "confidence_millipercent": (
                            round(confidence * 100_000)
                            if confidence is not None
                            else None
                        ),
                        "owner_profile_match": request.metadata.get(
                            "owner_speaker_profile"
                        )
                        is True,
                        "sole_speaker": request.metadata.get("sole_speaker_profile") is True,
                        "posture": posture,
                        "risk": definition.risk.value,
                    },
                )
                if definition.risk is RiskLevel.CRITICAL:
                    return self._deny(call, digest, "biometric_untrusted")
                biometric_manual_confirmation = True
        reason_code = "policy_allowed"
        if (
            needs_confirmation
            and not biometric_manual_confirmation
            and definition.explicit_local_intent_is_sufficient
            and call.authorization_basis is ToolCallBasis.EXPLICIT_LOCAL_INTENT
        ):
            needs_confirmation = False
            reason_code = "explicit_local_intent"
        if needs_confirmation:
            if context.confirmation_store is None:
                return ToolAuthorization(
                    call_id=call.call_id,
                    tool_name=call.tool_name,
                    call_digest=digest,
                    decision=PolicyDecision.REQUIRE_CONFIRMATION,
                    reason_code=(
                        "biometric_manual_confirmation"
                        if biometric_manual_confirmation
                        else "confirmation_required"
                    ),
                    normalized_arguments=normalized_arguments,
                )
            status = context.confirmation_store.consume(digest, now=context.current_time())
            if status is ConfirmationStatus.MISSING:
                return ToolAuthorization(
                    call_id=call.call_id,
                    tool_name=call.tool_name,
                    call_digest=digest,
                    decision=PolicyDecision.REQUIRE_CONFIRMATION,
                    reason_code=(
                        "biometric_manual_confirmation"
                        if biometric_manual_confirmation
                        else "confirmation_required"
                    ),
                    normalized_arguments=normalized_arguments,
                )
            if status is ConfirmationStatus.EXPIRED:
                return self._deny(call, digest, "confirmation_expired")
            if status is ConfirmationStatus.REPLAYED:
                return self._deny(call, digest, "confirmation_replayed")
            if status is ConfirmationStatus.REVOKED:
                return self._deny(call, digest, "confirmation_revoked")
            if status is ConfirmationStatus.NOT_YET_VALID:
                return self._deny(call, digest, "confirmation_not_yet_valid")
            if status is ConfirmationStatus.CONSUMED:
                reason_code = "confirmation_consumed"
            else:
                return self._deny(call, digest, "confirmation_store_error")

        return ToolAuthorization(
            call_id=call.call_id,
            tool_name=call.tool_name,
            call_digest=digest,
            decision=PolicyDecision.ALLOW,
            reason_code=reason_code,
            normalized_arguments=normalized_arguments,
        )

    @staticmethod
    def _trusted_owner_voice(request: UserRequest) -> tuple[bool, float | None]:
        identity = request.metadata.get("speaker_identity")
        if not isinstance(identity, dict):
            return False, None
        identifier = identity.get("id")
        raw_confidence = identity.get("confidence")
        confidence = (
            float(raw_confidence)
            if isinstance(raw_confidence, (int, float))
            and not isinstance(raw_confidence, bool)
            and math.isfinite(float(raw_confidence))
            else None
        )
        normalized_identifier = identifier.strip().casefold() if isinstance(identifier, str) else ""
        trusted = (
            bool(normalized_identifier)
            and normalized_identifier not in _UNTRUSTED_SPEAKER_LABELS
            and confidence is not None
            and confidence >= MINIMUM_CRITICAL_VOICE_CONFIDENCE
            and request.metadata.get("owner_speaker_profile") is True
            and request.metadata.get("sole_speaker_profile") is True
        )
        return trusted, confidence

    @staticmethod
    def _deny(call: ToolCall, digest: str, reason_code: str) -> ToolAuthorization:
        return ToolAuthorization(
            call_id=call.call_id,
            tool_name=call.tool_name,
            call_digest=digest,
            decision=PolicyDecision.DENY,
            reason_code=reason_code,
        )
