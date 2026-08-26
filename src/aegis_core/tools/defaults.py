from __future__ import annotations

import re
from datetime import datetime, timedelta
from ipaddress import ip_network
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from aegis_core.contracts import AgentRole, Capability, RiskLevel
from aegis_core.tools.broker import (
    PolicyContext,
    PolicyViolation,
    ToolBroker,
    ToolDefinition,
    ToolRegistry,
)
from aegis_core.tools.computer import is_restricted_computer_bundle


class RuntimeInfoArguments(BaseModel):
    model_config = ConfigDict(extra="forbid")


class PowerStatusArguments(BaseModel):
    model_config = ConfigDict(extra="forbid")


class StorageStatusArguments(BaseModel):
    model_config = ConfigDict(extra="forbid")


class SystemObserveArguments(BaseModel):
    model_config = ConfigDict(extra="forbid")

    domain: Literal["audio", "network", "performance"]


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


class WebResearchArguments(BaseModel):
    model_config = ConfigDict(extra="forbid")

    query: str = Field(min_length=2, max_length=300)
    max_results: int = Field(default=3, ge=1, le=5)

    @field_validator("query")
    @classmethod
    def query_must_be_normalized(cls, value: str) -> str:
        if value != " ".join(value.split()):
            raise ValueError("query must use normalized whitespace")
        return value


class WebFetchArguments(BaseModel):
    model_config = ConfigDict(extra="forbid")

    url: str = Field(min_length=12, max_length=2_048)
    max_characters: int = Field(default=8_000, ge=512, le=16_000)


class MailListRecentArguments(BaseModel):
    model_config = ConfigDict(extra="forbid")

    limit: int = Field(default=10, ge=1, le=20)
    unread_only: bool = False


class MailSendArguments(BaseModel):
    model_config = ConfigDict(extra="forbid")

    recipients: list[str] = Field(min_length=1, max_length=10)
    subject: str = Field(min_length=1, max_length=300)
    body: str = Field(min_length=1, max_length=20_000)

    @field_validator("recipients")
    @classmethod
    def recipients_must_be_bounded_addresses(cls, values: list[str]) -> list[str]:
        normalized = [value.strip().lower() for value in values]
        pattern = re.compile(r"^[a-z0-9.!#$%&'*+/=?^_`{|}~-]+@[a-z0-9.-]+\.[a-z]{2,63}$")
        if len(set(normalized)) != len(normalized) or any(
            len(value) > 254 or pattern.fullmatch(value) is None for value in normalized
        ):
            raise ValueError("recipient address is invalid")
        return normalized

    @field_validator("subject", "body")
    @classmethod
    def mail_text_must_not_contain_control_characters(cls, value: str) -> str:
        if any(ord(character) < 32 and character not in {"\n", "\t"} for character in value):
            raise ValueError("mail text contains control characters")
        return value


class CalendarListArguments(BaseModel):
    model_config = ConfigDict(extra="forbid")

    start_at: datetime
    end_at: datetime
    limit: int = Field(default=20, ge=1, le=50)

    @model_validator(mode="after")
    def window_must_be_aware_and_bounded(self) -> CalendarListArguments:
        if any(
            value.tzinfo is None or value.utcoffset() is None
            for value in (self.start_at, self.end_at)
        ):
            raise ValueError("calendar timestamps must be timezone-aware")
        if self.end_at <= self.start_at or self.end_at - self.start_at > timedelta(days=31):
            raise ValueError("calendar window is invalid")
        return self


class CalendarCreateArguments(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str = Field(min_length=1, max_length=300)
    start_at: datetime
    end_at: datetime
    calendar_name: str | None = Field(default=None, min_length=1, max_length=128)
    location: str | None = Field(default=None, max_length=500)
    notes: str | None = Field(default=None, max_length=4_000)

    @model_validator(mode="after")
    def event_must_be_aware_and_bounded(self) -> CalendarCreateArguments:
        if any(
            value.tzinfo is None or value.utcoffset() is None
            for value in (self.start_at, self.end_at)
        ):
            raise ValueError("calendar timestamps must be timezone-aware")
        if self.end_at <= self.start_at or self.end_at - self.start_at > timedelta(days=14):
            raise ValueError("calendar event interval is invalid")
        return self


class BrowserOpenArguments(BaseModel):
    model_config = ConfigDict(extra="forbid")

    url: str = Field(min_length=12, max_length=2_048)


class ApplicationOpenArguments(BaseModel):
    model_config = ConfigDict(extra="forbid")

    bundle_identifier: str = Field(
        min_length=3,
        max_length=255,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9.-]{1,253}[A-Za-z0-9]$",
    )


class ShortcutRunArguments(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=128)

    @field_validator("name")
    @classmethod
    def shortcut_name_must_be_literal(cls, value: str) -> str:
        normalized = " ".join(value.split())
        if normalized != value or any(ord(character) < 32 for character in value):
            raise ValueError("shortcut name is not normalized")
        if "/" in value or "\\" in value or value in {".", ".."}:
            raise ValueError("shortcut name is unsafe")
        return value


class ComputerUseArguments(BaseModel):
    model_config = ConfigDict(extra="forbid")

    objective: str = Field(min_length=3, max_length=1_000)
    application_bundle_identifier: str = Field(
        min_length=3,
        max_length=255,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9.-]{1,253}[A-Za-z0-9]$",
    )
    max_steps: int = Field(default=8, ge=1, le=12)

    @field_validator("objective")
    @classmethod
    def objective_must_be_normalized_and_safe(cls, value: str) -> str:
        if value != " ".join(value.split()):
            raise ValueError("computer objective must use normalized whitespace")
        if any(ord(character) < 32 for character in value):
            raise ValueError("computer objective contains control characters")
        return value

    @field_validator("application_bundle_identifier")
    @classmethod
    def application_must_not_be_restricted(cls, value: str) -> str:
        if is_restricted_computer_bundle(value):
            raise ValueError("computer application is restricted")
        return value


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
    AgentRole.PLANNER: frozenset(
        {
            Capability.SYSTEM_READ,
            Capability.MEMORY_QUERY,
            Capability.WEB_READ,
            Capability.MAIL_READ,
            Capability.MAIL_WRITE,
            Capability.CALENDAR_READ,
            Capability.CALENDAR_WRITE,
            Capability.APPLICATION_CONTROL,
        }
    ),
    AgentRole.CRITICAL_REASONER: frozenset(
        {
            Capability.SYSTEM_READ,
            Capability.FILESYSTEM_READ,
            Capability.MEMORY_QUERY,
            Capability.WEB_READ,
        }
    ),
    AgentRole.CODE_SECURITY: frozenset(
        {
            Capability.SYSTEM_READ,
            Capability.FILESYSTEM_READ,
            Capability.NETWORK_DISCOVERY,
            Capability.PROCESS_EXECUTION,
            Capability.WEB_READ,
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
            name="system_power_status",
            description="Read the current macOS battery and power-source status on demand.",
            arguments_model=PowerStatusArguments,
            capability=Capability.SYSTEM_READ,
            risk=RiskLevel.LOW,
            allowed_roles=frozenset({AgentRole.PLANNER}),
        ),
        ToolDefinition(
            name="system_storage_status",
            description="Read bounded capacity statistics for the macOS startup volume.",
            arguments_model=StorageStatusArguments,
            capability=Capability.SYSTEM_READ,
            risk=RiskLevel.LOW,
            allowed_roles=frozenset({AgentRole.PLANNER}),
        ),
        ToolDefinition(
            name="system_observe_status",
            description=(
                "Read a bounded local macOS audio, network or performance status. "
                "Network results describe local connectivity only and omit identifiers."
            ),
            arguments_model=SystemObserveArguments,
            capability=Capability.SYSTEM_READ,
            risk=RiskLevel.LOW,
            allowed_roles=frozenset({AgentRole.PLANNER}),
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
            name="web_research",
            description=(
                "Search the public Internet and read a bounded set of public HTTPS pages. "
                "Use for current factual research; never claim access to authenticated content."
            ),
            arguments_model=WebResearchArguments,
            capability=Capability.WEB_READ,
            risk=RiskLevel.MEDIUM,
            allowed_roles=frozenset(
                {AgentRole.PLANNER, AgentRole.CRITICAL_REASONER, AgentRole.CODE_SECURITY}
            ),
        ),
        ToolDefinition(
            name="web_fetch",
            description="Read bounded text from one public HTTPS page without browser credentials.",
            arguments_model=WebFetchArguments,
            capability=Capability.WEB_READ,
            risk=RiskLevel.MEDIUM,
            allowed_roles=frozenset(
                {AgentRole.PLANNER, AgentRole.CRITICAL_REASONER, AgentRole.CODE_SECURITY}
            ),
        ),
        ToolDefinition(
            name="mail_list_recent",
            description=(
                "List bounded metadata for recent Apple Mail inbox messages. "
                "Returns sender, subject, date and read state, never message bodies."
            ),
            arguments_model=MailListRecentArguments,
            capability=Capability.MAIL_READ,
            risk=RiskLevel.MEDIUM,
            allowed_roles=frozenset({AgentRole.PLANNER}),
        ),
        ToolDefinition(
            name="mail_send_message",
            description="Send one Apple Mail message to exact recipients after user confirmation.",
            arguments_model=MailSendArguments,
            capability=Capability.MAIL_WRITE,
            risk=RiskLevel.CRITICAL,
            allowed_roles=frozenset({AgentRole.PLANNER}),
            requires_confirmation=True,
        ),
        ToolDefinition(
            name="calendar_list_events",
            description=(
                "List bounded Apple Calendar event metadata inside an explicit time window."
            ),
            arguments_model=CalendarListArguments,
            capability=Capability.CALENDAR_READ,
            risk=RiskLevel.MEDIUM,
            allowed_roles=frozenset({AgentRole.PLANNER}),
        ),
        ToolDefinition(
            name="calendar_create_event",
            description="Create one exact Apple Calendar event after user confirmation.",
            arguments_model=CalendarCreateArguments,
            capability=Capability.CALENDAR_WRITE,
            risk=RiskLevel.HIGH,
            allowed_roles=frozenset({AgentRole.PLANNER}),
            requires_confirmation=True,
        ),
        ToolDefinition(
            name="browser_open_url",
            description=(
                "Open one public HTTPS URL in the default macOS browser after confirmation."
            ),
            arguments_model=BrowserOpenArguments,
            capability=Capability.APPLICATION_CONTROL,
            risk=RiskLevel.HIGH,
            allowed_roles=frozenset({AgentRole.PLANNER}),
            requires_confirmation=True,
        ),
        ToolDefinition(
            name="application_open",
            description=(
                "Open one installed macOS application by exact bundle identifier after "
                "confirmation."
            ),
            arguments_model=ApplicationOpenArguments,
            capability=Capability.APPLICATION_CONTROL,
            risk=RiskLevel.HIGH,
            allowed_roles=frozenset({AgentRole.PLANNER}),
            requires_confirmation=True,
        ),
        ToolDefinition(
            name="shortcut_run",
            description=(
                "Run one existing macOS Shortcut by its exact name after confirmation. "
                "No input files or arbitrary command arguments are accepted."
            ),
            arguments_model=ShortcutRunArguments,
            capability=Capability.APPLICATION_CONTROL,
            risk=RiskLevel.CRITICAL,
            allowed_roles=frozenset({AgentRole.PLANNER}),
            requires_confirmation=True,
        ),
        ToolDefinition(
            name="computer_use",
            description=(
                "Visually operate one explicit macOS application for one bounded objective after "
                "confirmation. Screen snapshots are sent to the vision model. Never use for "
                "login, passwords, purchases, payments, messages, uploads, downloads, deletion, "
                "permissions, security settings, Terminal, Finder, Mail or password managers."
            ),
            arguments_model=ComputerUseArguments,
            capability=Capability.APPLICATION_CONTROL,
            risk=RiskLevel.CRITICAL,
            allowed_roles=frozenset({AgentRole.PLANNER}),
            requires_confirmation=True,
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
