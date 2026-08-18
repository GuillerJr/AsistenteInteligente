from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from ipaddress import IPv4Network, IPv6Network
from pathlib import Path
from types import MappingProxyType

from pydantic import BaseModel, ValidationError

from aegis_core.contracts import (
    AgentRole,
    Capability,
    ConfirmationGrant,
    PolicyDecision,
    RiskLevel,
    ToolAuthorization,
    ToolCall,
)


class PolicyViolation(ValueError):
    """Raised when validated arguments violate local policy."""


@dataclass(frozen=True, slots=True)
class PolicyContext:
    workspace_root: Path
    network_scopes: tuple[IPv4Network | IPv6Network, ...] = ()
    confirmation_grants: tuple[ConfirmationGrant, ...] = ()
    now: datetime = field(default_factory=lambda: datetime.now(UTC))

    def __post_init__(self) -> None:
        if self.now.tzinfo is None or self.now.utcoffset() is None:
            raise ValueError("policy context time must be timezone-aware")


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

    def openai_schema(self) -> dict[str, object]:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.arguments_model.model_json_schema(),
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


class ToolBroker:
    def __init__(
        self,
        registry: ToolRegistry,
        role_capabilities: dict[AgentRole, frozenset[Capability]],
    ) -> None:
        self._registry = registry
        self._role_capabilities = MappingProxyType(dict(role_capabilities))

    def schemas_for(self, role: AgentRole) -> list[dict[str, object]]:
        capabilities = self._role_capabilities.get(role, frozenset())
        return [
            definition.openai_schema()
            for definition in self._registry.values()
            if definition.enabled
            and role in definition.allowed_roles
            and definition.capability in capabilities
        ]

    def authorize(self, call: ToolCall, context: PolicyContext) -> ToolAuthorization:
        digest = call.digest()
        definition = self._registry.get(call.tool_name)
        if definition is None:
            return self._deny(call, digest, "unknown_tool")
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
        if needs_confirmation:
            grant = next(
                (
                    candidate
                    for candidate in context.confirmation_grants
                    if candidate.call_digest == digest
                ),
                None,
            )
            if grant is None:
                return ToolAuthorization(
                    call_id=call.call_id,
                    tool_name=call.tool_name,
                    call_digest=digest,
                    decision=PolicyDecision.REQUIRE_CONFIRMATION,
                    reason_code="confirmation_required",
                    normalized_arguments=normalized_arguments,
                )
            if grant.expires_at <= context.now:
                return self._deny(call, digest, "confirmation_expired")

        return ToolAuthorization(
            call_id=call.call_id,
            tool_name=call.tool_name,
            call_digest=digest,
            decision=PolicyDecision.ALLOW,
            reason_code="policy_allowed",
            normalized_arguments=normalized_arguments,
        )

    @staticmethod
    def _deny(call: ToolCall, digest: str, reason_code: str) -> ToolAuthorization:
        return ToolAuthorization(
            call_id=call.call_id,
            tool_name=call.tool_name,
            call_digest=digest,
            decision=PolicyDecision.DENY,
            reason_code=reason_code,
        )
