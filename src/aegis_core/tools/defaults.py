from __future__ import annotations

from ipaddress import ip_network
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from aegis_core.contracts import AgentRole, Capability, RiskLevel
from aegis_core.tools.broker import (
    PolicyContext,
    PolicyViolation,
    ToolBroker,
    ToolDefinition,
    ToolRegistry,
)


class RuntimeInfoArguments(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ReadTextArguments(BaseModel):
    model_config = ConfigDict(extra="forbid")

    path: str = Field(min_length=1, max_length=1_024)
    max_bytes: int = Field(default=262_144, ge=1, le=1_048_576)


class NetworkDiscoveryArguments(BaseModel):
    model_config = ConfigDict(extra="forbid")

    target: str = Field(min_length=3, max_length=128)
    ports: list[int] = Field(min_length=1, max_length=8)

    @field_validator("ports")
    @classmethod
    def ports_must_be_unique_and_valid(cls, value: list[int]) -> list[int]:
        if any(port < 1 or port > 65_535 for port in value):
            raise ValueError("ports must be between 1 and 65535")
        if len(value) != len(set(value)):
            raise ValueError("ports must be unique")
        return sorted(value)


class TerminalTemplateArguments(BaseModel):
    model_config = ConfigDict(extra="forbid")

    template: Literal[
        "git_status",
        "list_processes",
        "list_listeners",
        "security_posture",
    ]


def _guard_workspace_path(arguments: BaseModel, context: PolicyContext) -> ReadTextArguments:
    assert isinstance(arguments, ReadTextArguments)
    requested = Path(arguments.path)
    if requested.is_absolute():
        raise PolicyViolation("absolute paths are not allowed")
    root = context.workspace_root.resolve()
    candidate = (root / requested).resolve(strict=False)
    if not candidate.is_relative_to(root):
        raise PolicyViolation("path escapes workspace root")
    return arguments.model_copy(update={"path": candidate.relative_to(root).as_posix()})


def _guard_network_scope(arguments: BaseModel, context: PolicyContext) -> NetworkDiscoveryArguments:
    assert isinstance(arguments, NetworkDiscoveryArguments)
    try:
        target = ip_network(arguments.target, strict=False)
    except ValueError as error:
        raise PolicyViolation("invalid network target") from error
    if target.num_addresses > 256:
        raise PolicyViolation("network target exceeds host limit")
    if not any(
        target.version == scope.version and target.subnet_of(scope)
        for scope in context.network_scopes
    ):
        raise PolicyViolation("network target is outside authorized scope")
    return arguments.model_copy(update={"target": target.with_prefixlen})


ROLE_CAPABILITIES: dict[AgentRole, frozenset[Capability]] = {
    AgentRole.ROUTER: frozenset(),
    AgentRole.PLANNER: frozenset({Capability.SYSTEM_READ, Capability.MEMORY_QUERY}),
    AgentRole.CRITICAL_REASONER: frozenset(
        {Capability.SYSTEM_READ, Capability.FILESYSTEM_READ, Capability.MEMORY_QUERY}
    ),
    AgentRole.CODE_SECURITY: frozenset(
        {
            Capability.SYSTEM_READ,
            Capability.FILESYSTEM_READ,
            Capability.NETWORK_DISCOVERY,
            Capability.PROCESS_EXECUTION,
        }
    ),
    AgentRole.VISION: frozenset({Capability.SCREEN_CAPTURE}),
    AgentRole.OMNI: frozenset({Capability.SCREEN_CAPTURE, Capability.AUDIO_CAPTURE}),
    AgentRole.SYNTHESIZER: frozenset(),
}


def build_default_tool_broker() -> ToolBroker:
    definitions = (
        ToolDefinition(
            name="system_describe_runtime",
            description="Read non-secret runtime and architecture metadata.",
            arguments_model=RuntimeInfoArguments,
            capability=Capability.SYSTEM_READ,
            risk=RiskLevel.LOW,
            allowed_roles=frozenset(
                {AgentRole.PLANNER, AgentRole.CRITICAL_REASONER, AgentRole.CODE_SECURITY}
            ),
        ),
        ToolDefinition(
            name="filesystem_read_text",
            description="Read a bounded UTF-8 text file inside the active workspace.",
            arguments_model=ReadTextArguments,
            capability=Capability.FILESYSTEM_READ,
            risk=RiskLevel.MEDIUM,
            allowed_roles=frozenset({AgentRole.CRITICAL_REASONER, AgentRole.CODE_SECURITY}),
            argument_guard=_guard_workspace_path,
        ),
        ToolDefinition(
            name="network_discover_hosts",
            description=(
                "Probe up to eight explicit TCP ports across at most 256 addresses inside an "
                "authorized local IP network."
            ),
            arguments_model=NetworkDiscoveryArguments,
            capability=Capability.NETWORK_DISCOVERY,
            risk=RiskLevel.HIGH,
            allowed_roles=frozenset({AgentRole.CODE_SECURITY}),
            requires_confirmation=True,
            argument_guard=_guard_network_scope,
        ),
        ToolDefinition(
            name="terminal_run_template",
            description=(
                "Run one fixed, read-only local diagnostic: Git status, process inventory, "
                "listening TCP sockets, or macOS security posture. No shell or free-form "
                "arguments are accepted."
            ),
            arguments_model=TerminalTemplateArguments,
            capability=Capability.PROCESS_EXECUTION,
            risk=RiskLevel.CRITICAL,
            allowed_roles=frozenset({AgentRole.CODE_SECURITY}),
            requires_confirmation=True,
        ),
    )
    return ToolBroker(ToolRegistry(definitions), ROLE_CAPABILITIES)


def default_policy_context(workspace_root: Path) -> PolicyContext:
    scopes = tuple(
        ip_network(value)
        for value in (
            "127.0.0.0/8",
            "10.0.0.0/8",
            "172.16.0.0/12",
            "192.168.0.0/16",
            "::1/128",
            "fc00::/7",
        )
    )
    return PolicyContext(workspace_root=workspace_root, network_scopes=scopes)
