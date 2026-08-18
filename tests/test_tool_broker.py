from datetime import UTC, datetime, timedelta
from pathlib import Path

from aegis_core.contracts import (
    AgentRole,
    PolicyDecision,
    ToolCall,
)
from aegis_core.tools.broker import PolicyContext
from aegis_core.tools.confirmations import OneTimeConfirmationStore
from aegis_core.tools.defaults import build_default_tool_broker, default_policy_context


def _call(
    tool_name: str,
    arguments: dict[str, object],
    *,
    role: AgentRole = AgentRole.CODE_SECURITY,
) -> ToolCall:
    return ToolCall(
        call_id="call-1",
        tool_name=tool_name,
        arguments=arguments,
        requested_by=role,
    )


def test_registry_is_deny_by_default_for_unknown_tools(tmp_path: Path) -> None:
    authorization = build_default_tool_broker().authorize(
        _call("terminal_arbitrary_shell", {"command": "whoami"}),
        default_policy_context(tmp_path),
    )

    assert authorization.decision is PolicyDecision.DENY
    assert authorization.reason_code == "unknown_tool"


def test_router_receives_no_tool_schemas() -> None:
    assert build_default_tool_broker().schemas_for(AgentRole.ROUTER) == []


def test_forged_tool_call_cannot_escalate_router_role(tmp_path: Path) -> None:
    authorization = build_default_tool_broker().authorize(
        _call("system_describe_runtime", {}, role=AgentRole.ROUTER),
        default_policy_context(tmp_path),
    )

    assert authorization.decision is PolicyDecision.DENY
    assert authorization.reason_code == "role_not_allowed"


def test_low_risk_capability_is_allowed(tmp_path: Path) -> None:
    authorization = build_default_tool_broker().authorize(
        _call("system_describe_runtime", {}, role=AgentRole.PLANNER),
        default_policy_context(tmp_path),
    )

    assert authorization.decision is PolicyDecision.ALLOW


def test_workspace_path_traversal_is_denied(tmp_path: Path) -> None:
    authorization = build_default_tool_broker().authorize(
        _call("filesystem_read_text", {"path": "../secret.txt"}),
        default_policy_context(tmp_path),
    )

    assert authorization.decision is PolicyDecision.DENY
    assert authorization.reason_code == "invalid_arguments"


def test_network_discovery_requires_confirmation(tmp_path: Path) -> None:
    authorization = build_default_tool_broker().authorize(
        _call("network_discover_hosts", {"target": "192.168.1.25"}),
        default_policy_context(tmp_path),
    )

    assert authorization.decision is PolicyDecision.REQUIRE_CONFIRMATION
    assert authorization.normalized_arguments["target"] == "192.168.1.25/32"


def test_matching_confirmation_grant_allows_network_discovery(tmp_path: Path) -> None:
    call = _call("network_discover_hosts", {"target": "10.0.0.0/24"})
    now = datetime.now(UTC)
    broker = build_default_tool_broker()
    base_context = default_policy_context(tmp_path)
    pending = broker.authorize(call, base_context)
    store = OneTimeConfirmationStore()
    store.issue(call, pending, approved_by="local-user", now=now)
    context = PolicyContext(
        workspace_root=base_context.workspace_root,
        network_scopes=base_context.network_scopes,
        confirmation_store=store,
        now=now,
    )

    authorization = broker.authorize(call, context)

    assert authorization.decision is PolicyDecision.ALLOW
    assert authorization.reason_code == "confirmation_consumed"


def test_expired_confirmation_grant_is_denied(tmp_path: Path) -> None:
    call = _call("network_discover_hosts", {"target": "127.0.0.1"})
    now = datetime.now(UTC)
    broker = build_default_tool_broker()
    base_context = default_policy_context(tmp_path)
    pending = broker.authorize(call, base_context)
    store = OneTimeConfirmationStore()
    store.issue(
        call,
        pending,
        approved_by="local-user",
        ttl=timedelta(seconds=1),
        now=now,
    )
    context = PolicyContext(
        workspace_root=base_context.workspace_root,
        network_scopes=base_context.network_scopes,
        confirmation_store=store,
        now=now + timedelta(seconds=2),
    )

    authorization = broker.authorize(call, context)

    assert authorization.decision is PolicyDecision.DENY
    assert authorization.reason_code == "confirmation_expired"


def test_disabled_terminal_template_is_denied(tmp_path: Path) -> None:
    authorization = build_default_tool_broker().authorize(
        _call("terminal_run_template", {"template": "git_status"}),
        default_policy_context(tmp_path),
    )

    assert authorization.decision is PolicyDecision.DENY
    assert authorization.reason_code == "tool_disabled"


def test_public_network_scope_is_denied(tmp_path: Path) -> None:
    authorization = build_default_tool_broker().authorize(
        _call("network_discover_hosts", {"target": "8.8.8.8"}),
        default_policy_context(tmp_path),
    )

    assert authorization.decision is PolicyDecision.DENY
    assert authorization.reason_code == "invalid_arguments"


def test_extra_arguments_cannot_override_policy(tmp_path: Path) -> None:
    authorization = build_default_tool_broker().authorize(
        _call(
            "network_discover_hosts",
            {"target": "10.0.0.1", "requires_confirmation": False},
        ),
        default_policy_context(tmp_path),
    )

    assert authorization.decision is PolicyDecision.DENY
    assert authorization.reason_code == "invalid_arguments"
