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


def test_schema_filter_preserves_role_and_capability_boundaries() -> None:
    broker = build_default_tool_broker()
    schemas = broker.schemas_for(
        AgentRole.PLANNER,
        names=frozenset({"mail_list_recent", "network_discover_hosts"}),
    )

    assert [schema["function"]["name"] for schema in schemas] == ["mail_list_recent"]
    assert broker.schemas_for(AgentRole.PLANNER, names=frozenset()) == []


def test_authorization_rejects_a_tool_that_was_not_offered(tmp_path: Path) -> None:
    authorization = build_default_tool_broker().authorize(
        _call(
            "mail_send_message",
            {
                "recipients": ["owner@example.com"],
                "subject": "Estado",
                "body": "Listo.",
            },
            role=AgentRole.PLANNER,
        ),
        default_policy_context(tmp_path),
        allowed_names=frozenset({"mail_list_recent"}),
    )

    assert authorization.decision is PolicyDecision.DENY
    assert authorization.reason_code == "tool_not_offered"


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


def test_planner_can_read_power_status_without_confirmation(tmp_path: Path) -> None:
    authorization = build_default_tool_broker().authorize(
        _call("system_power_status", {}, role=AgentRole.PLANNER),
        default_policy_context(tmp_path),
    )

    assert authorization.decision is PolicyDecision.ALLOW


def test_planner_can_read_storage_status_without_confirmation(tmp_path: Path) -> None:
    authorization = build_default_tool_broker().authorize(
        _call("system_storage_status", {}, role=AgentRole.PLANNER),
        default_policy_context(tmp_path),
    )

    assert authorization.decision is PolicyDecision.ALLOW


def test_planner_can_observe_bounded_system_domains_without_confirmation(
    tmp_path: Path,
) -> None:
    broker = build_default_tool_broker()
    context = default_policy_context(tmp_path)

    assert all(
        broker.authorize(
            _call(
                "system_observe_status",
                {"domain": domain},
                role=AgentRole.PLANNER,
            ),
            context,
        ).decision
        is PolicyDecision.ALLOW
        for domain in ("audio", "network", "performance")
    )


def test_system_observation_rejects_unbounded_domains(tmp_path: Path) -> None:
    authorization = build_default_tool_broker().authorize(
        _call(
            "system_observe_status",
            {"domain": "processes"},
            role=AgentRole.PLANNER,
        ),
        default_policy_context(tmp_path),
    )

    assert authorization.decision is PolicyDecision.DENY
    assert authorization.reason_code == "invalid_arguments"


def test_planner_can_read_public_web_mail_and_calendar_without_confirmation(
    tmp_path: Path,
) -> None:
    broker = build_default_tool_broker()
    context = default_policy_context(tmp_path)
    calls = (
        _call(
            "web_research",
            {"query": "current NVIDIA NIM models", "max_results": 2},
            role=AgentRole.PLANNER,
        ),
        _call(
            "mail_list_recent",
            {"limit": 5, "unread_only": True},
            role=AgentRole.PLANNER,
        ),
        _call(
            "calendar_list_events",
            {
                "start_at": "2026-08-24T00:00:00-05:00",
                "end_at": "2026-08-25T00:00:00-05:00",
                "limit": 10,
            },
            role=AgentRole.PLANNER,
        ),
    )

    assert all(broker.authorize(call, context).decision is PolicyDecision.ALLOW for call in calls)


def test_planner_can_read_contacts_and_reminders_without_confirmation(
    tmp_path: Path,
) -> None:
    broker = build_default_tool_broker()
    context = default_policy_context(tmp_path)

    assert broker.authorize(
        _call(
            "contacts_search",
            {"query": "Ada", "limit": 5},
            role=AgentRole.PLANNER,
        ),
        context,
    ).decision is PolicyDecision.ALLOW
    assert broker.authorize(
        _call(
            "reminders_list",
            {"list_name": None, "include_completed": False, "limit": 20},
            role=AgentRole.PLANNER,
        ),
        context,
    ).decision is PolicyDecision.ALLOW


def test_contacts_and_reminder_mutations_require_confirmation(tmp_path: Path) -> None:
    broker = build_default_tool_broker()
    context = default_policy_context(tmp_path)
    calls = (
        _call(
            "contact_create",
            {"first_name": "Ada", "email": "ada@example.com"},
            role=AgentRole.PLANNER,
        ),
        _call(
            "reminder_create",
            {"title": "Revisar informe"},
            role=AgentRole.PLANNER,
        ),
        _call(
            "reminder_complete",
            {"title": "Revisar informe"},
            role=AgentRole.PLANNER,
        ),
    )

    assert all(
        broker.authorize(call, context).decision is PolicyDecision.REQUIRE_CONFIRMATION
        for call in calls
    )


def test_native_audio_media_and_spotlight_open_require_confirmation(
    tmp_path: Path,
) -> None:
    broker = build_default_tool_broker()
    context = default_policy_context(tmp_path)
    calls = (
        _call(
            "system_audio_set",
            {"volume_percent": 40, "muted": None},
            role=AgentRole.PLANNER,
        ),
        _call("media_control", {"action": "next"}, role=AgentRole.PLANNER),
        _call(
            "spotlight_open",
            {"query": "Informe.pdf"},
            role=AgentRole.PLANNER,
        ),
    )

    assert all(
        broker.authorize(call, context).decision is PolicyDecision.REQUIRE_CONFIRMATION
        for call in calls
    )
    assert broker.authorize(
        _call(
            "spotlight_search",
            {"query": "Informe", "limit": 10},
            role=AgentRole.PLANNER,
        ),
        context,
    ).decision is PolicyDecision.ALLOW


def test_external_app_mutations_require_exact_confirmation(tmp_path: Path) -> None:
    broker = build_default_tool_broker()
    context = default_policy_context(tmp_path)
    calls = (
        _call(
            "mail_send_message",
            {
                "recipients": ["owner@example.com"],
                "subject": "Estado",
                "body": "Todo listo.",
            },
            role=AgentRole.PLANNER,
        ),
        _call(
            "calendar_create_event",
            {
                "title": "Revisión",
                "start_at": "2026-08-24T10:00:00-05:00",
                "end_at": "2026-08-24T10:30:00-05:00",
            },
            role=AgentRole.PLANNER,
        ),
        _call(
            "browser_open_url",
            {"url": "https://example.com/report"},
            role=AgentRole.PLANNER,
        ),
        _call(
            "browser_search",
            {"query": "arquitectura segura", "browser": "safari"},
            role=AgentRole.PLANNER,
        ),
        _call(
            "application_open",
            {"bundle_identifier": "com.apple.Safari"},
            role=AgentRole.PLANNER,
        ),
        _call(
            "computer_use",
            {
                "objective": "Abrir la documentación del proyecto",
                "application_bundle_identifier": "com.apple.Safari",
                "max_steps": 6,
            },
            role=AgentRole.PLANNER,
        ),
    )

    assert all(
        broker.authorize(call, context).decision is PolicyDecision.REQUIRE_CONFIRMATION
        for call in calls
    )


def test_browser_search_rejects_credential_like_queries(tmp_path: Path) -> None:
    authorization = build_default_tool_broker().authorize(
        _call(
            "browser_search",
            {"query": "nvapi-secret-example", "browser": "safari"},
            role=AgentRole.PLANNER,
        ),
        default_policy_context(tmp_path),
    )

    assert authorization.decision is PolicyDecision.DENY
    assert authorization.reason_code == "invalid_arguments"


def test_computer_use_denies_restricted_applications(tmp_path: Path) -> None:
    authorization = build_default_tool_broker().authorize(
        _call(
            "computer_use",
            {
                "objective": "Ejecutar una orden",
                "application_bundle_identifier": "com.apple.Terminal",
                "max_steps": 2,
            },
            role=AgentRole.PLANNER,
        ),
        default_policy_context(tmp_path),
    )

    assert authorization.decision is PolicyDecision.DENY
    assert authorization.reason_code == "invalid_arguments"


def test_workspace_path_traversal_is_denied(tmp_path: Path) -> None:
    authorization = build_default_tool_broker().authorize(
        _call("filesystem_read_text", {"path": "../secret.txt"}),
        default_policy_context(tmp_path),
    )

    assert authorization.decision is PolicyDecision.DENY
    assert authorization.reason_code == "invalid_arguments"


def test_network_discovery_requires_confirmation(tmp_path: Path) -> None:
    authorization = build_default_tool_broker().authorize(
        _call("network_discover_hosts", {"target": "192.168.1.25", "ports": [443]}),
        default_policy_context(tmp_path),
    )

    assert authorization.decision is PolicyDecision.REQUIRE_CONFIRMATION
    assert authorization.normalized_arguments["target"] == "192.168.1.25/32"


def test_matching_confirmation_grant_allows_network_discovery(tmp_path: Path) -> None:
    call = _call("network_discover_hosts", {"target": "10.0.0.0/24", "ports": [22, 443]})
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
    call = _call("network_discover_hosts", {"target": "127.0.0.1", "ports": [443]})
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


def test_terminal_template_requires_confirmation(tmp_path: Path) -> None:
    authorization = build_default_tool_broker().authorize(
        _call("terminal_run_template", {"template": "git_status"}),
        default_policy_context(tmp_path),
    )

    assert authorization.decision is PolicyDecision.REQUIRE_CONFIRMATION
    assert authorization.normalized_arguments == {"template": "git_status"}


def test_security_posture_template_requires_confirmation(tmp_path: Path) -> None:
    authorization = build_default_tool_broker().authorize(
        _call("terminal_run_template", {"template": "security_posture"}),
        default_policy_context(tmp_path),
    )

    assert authorization.decision is PolicyDecision.REQUIRE_CONFIRMATION
    assert authorization.normalized_arguments == {"template": "security_posture"}


def test_terminal_template_rejects_arbitrary_commands(tmp_path: Path) -> None:
    authorization = build_default_tool_broker().authorize(
        _call("terminal_run_template", {"template": "cat /etc/passwd"}),
        default_policy_context(tmp_path),
    )

    assert authorization.decision is PolicyDecision.DENY
    assert authorization.reason_code == "invalid_arguments"


def test_public_network_scope_is_denied(tmp_path: Path) -> None:
    authorization = build_default_tool_broker().authorize(
        _call("network_discover_hosts", {"target": "8.8.8.8", "ports": [443]}),
        default_policy_context(tmp_path),
    )

    assert authorization.decision is PolicyDecision.DENY
    assert authorization.reason_code == "invalid_arguments"


def test_extra_arguments_cannot_override_policy(tmp_path: Path) -> None:
    authorization = build_default_tool_broker().authorize(
        _call(
            "network_discover_hosts",
            {"target": "10.0.0.1", "ports": [443], "requires_confirmation": False},
        ),
        default_policy_context(tmp_path),
    )

    assert authorization.decision is PolicyDecision.DENY
    assert authorization.reason_code == "invalid_arguments"


def test_network_discovery_rejects_more_than_256_addresses(tmp_path: Path) -> None:
    authorization = build_default_tool_broker().authorize(
        _call("network_discover_hosts", {"target": "10.0.0.0/23", "ports": [443]}),
        default_policy_context(tmp_path),
    )

    assert authorization.decision is PolicyDecision.DENY
    assert authorization.reason_code == "invalid_arguments"


def test_network_discovery_requires_one_to_eight_explicit_ports(tmp_path: Path) -> None:
    broker = build_default_tool_broker()
    context = default_policy_context(tmp_path)

    missing = broker.authorize(
        _call("network_discover_hosts", {"target": "127.0.0.1"}), context
    )
    excessive = broker.authorize(
        _call(
            "network_discover_hosts",
            {"target": "127.0.0.1", "ports": list(range(1, 10))},
        ),
        context,
    )

    assert missing.reason_code == "invalid_arguments"
    assert excessive.reason_code == "invalid_arguments"
