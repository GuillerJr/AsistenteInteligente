from __future__ import annotations

import json
import os
import sys
from dataclasses import replace
from pathlib import Path

import pytest
from pydantic import ValidationError

from aegis_core.contracts import AgentRole, Capability, PolicyDecision, ToolCall
from aegis_core.job_tool_presenter import JobToolPresenter
from aegis_core.mcp.client import (
    McpArguments,
    McpConfigurationError,
    McpHost,
    McpHostConfiguration,
    McpProtocolError,
    McpServerConfiguration,
    _argument_guard,
    artifact_sha256,
    integration_input_schema,
    reviewed_schema_sha256,
    validate_integration_process,
)
from aegis_core.mcp.integrations import (
    IntegrationId,
    IntegrationScope,
    guard_integration,
    integration_catalogue,
    integration_tools,
)
from aegis_core.tools.broker import PolicyViolation
from aegis_core.tools.confirmations import OneTimeConfirmationStore
from aegis_core.tools.defaults import build_default_tool_broker, default_policy_context


def google_scope():
    return IntegrationScope(provider="google-workspace", google_accounts={"owner@example.com"})


def google_arguments(**extra):
    return {"user_google_email": "owner@example.com", **extra}


@pytest.mark.parametrize(
    "name",
    [
        "delete_event",
        "send_gmail_message",
        "modify_gmail_message_labels",
        "manage_gmail_filter",
        "manage_drive_access",
        "set_drive_file_permissions",
        "batch_update_document",
        "run_code",
        "unknown_future_tool",
    ],
)
def test_google_forbidden_tools_fail_closed(name):
    with pytest.raises(PolicyViolation):
        guard_integration(google_scope(), name, google_arguments())


@pytest.mark.parametrize(
    "name,arguments",
    [
        ("manage_event", {"action": "delete", "send_updates": "none"}),
        ("manage_event", {"action": "rsvp", "send_updates": "none"}),
        ("manage_event", {"action": "update", "event_id": "e"}),
        ("manage_event", {"action": "update", "event_id": "e", "send_updates": "all"}),
        ("manage_event", {"action": "create", "send_updates": "none", "status": "cancelled"}),
        ("manage_event", {"action": "create", "send_updates": "none", "attendees": []}),
        ("update_drive_file", {"file_id": "f", "trashed": True}),
        ("update_drive_file", {"file_id": "f", "trashed": False}),
        ("update_drive_file", {"file_id": "f", "remove_parents": "folder"}),
        ("update_drive_file", {"file_id": "f", "content": "replacement"}),
        ("update_drive_file", {"file_id": "f", "content": "", "mode": "append"}),
        ("update_drive_file", {"file_id": "f", "file_path": "/etc/passwd"}),
        ("create_drive_file", {"file_name": "f", "fileUrl": "file:///etc/passwd"}),
        ("create_drive_file", {"file_name": "f", "mime_type": "application/javascript"}),
        ("draft_gmail_message", {"subject": "s", "body": "b", "attachments": []}),
        ("draft_gmail_message", {"subject": "s", "body": "b", "body_format": "html"}),
        ("draft_gmail_message", {"subject": "s", "body": "b"}),
        ("draft_gmail_message", {"subject": "s", "body": "b", "include_signature": True}),
    ],
)
def test_google_destructive_parameters_cannot_hide_in_update_tools(name, arguments):
    with pytest.raises(PolicyViolation):
        guard_integration(google_scope(), name, google_arguments(**arguments))


@pytest.mark.parametrize(
    "name,arguments",
    [
        ("draft_gmail_message", {"subject": "s", "body": "b", "include_signature": False}),
        ("manage_event", {"action": "create", "send_updates": "none", "summary": "Planning"}),
        ("manage_event", {"action": "update", "send_updates": "none", "event_id": "e"}),
        ("update_drive_file", {"file_id": "f", "name": "Revised title"}),
        ("update_drive_file", {"file_id": "f", "content": "Addition", "mode": "append"}),
        ("create_drive_file", {"file_name": "f", "content": "New text"}),
    ],
)
def test_google_selected_creates_and_updates_allowed(name, arguments):
    guard_integration(google_scope(), name, google_arguments(**arguments))
    with pytest.raises(PolicyViolation):
        guard_integration(google_scope(), name, {**arguments, "user_google_email": "other@x.com"})


def drive_schema():
    return {
        "type": "object",
        "properties": {
            "user_google_email": {"type": "string"},
            "file_id": {"type": "string"},
            "name": {
                "anyOf": [{"type": "string"}, {"type": "null"}],
                "description": "UNTRUSTED_INSTRUCTIONS",
                "default": None,
            },
            "trashed": {"anyOf": [{"type": "boolean"}, {"type": "null"}], "default": None},
            "properties": {"type": "object", "additionalProperties": True},
        },
        "required": ["user_google_email", "file_id"],
    }


def test_schema_projection_pins_even_excluded_fields_and_removes_remote_instructions(tmp_path):
    raw = drive_schema()
    exposed = integration_input_schema(raw, google_scope(), "update_drive_file")
    assert set(exposed["properties"]) == {"user_google_email", "file_id", "name"}
    assert "UNTRUSTED_INSTRUCTIONS" not in json.dumps(exposed)
    assert exposed["properties"]["name"]["type"] == "string"
    digest = reviewed_schema_sha256(raw, google_scope(), "update_drive_file")
    raw["properties"]["trashed"]["default"] = True
    assert reviewed_schema_sha256(raw, google_scope(), "update_drive_file") != digest
    guard = _argument_guard(exposed, google_scope(), "update_drive_file")
    with pytest.raises(PolicyViolation):
        guard(
            McpArguments(**google_arguments(file_id="f", name=None)),
            default_policy_context(tmp_path),
        )
    raw["required"].append("properties")
    with pytest.raises(PolicyViolation):
        integration_input_schema(raw, google_scope(), "update_drive_file")


def test_schema_projection_does_not_relax_arbitrary_union_or_reference():
    raw = drive_schema()
    raw["properties"]["name"] = {"anyOf": [{"type": "string"}, {"type": "number"}]}
    with pytest.raises(McpProtocolError):
        integration_input_schema(raw, google_scope(), "update_drive_file")
    for malformed in ({"anyOf": [{"type": "null"}, {"type": "null"}]}, {"type": ["null", "null"]}):
        raw["properties"]["name"] = malformed
        with pytest.raises(McpProtocolError):
            integration_input_schema(raw, google_scope(), "update_drive_file")
    raw["properties"]["name"] = {"$ref": "file:///etc/passwd"}
    with pytest.raises(McpProtocolError):
        integration_input_schema(raw, google_scope(), "update_drive_file")


def test_github_exact_repository_and_read_method_only():
    scope = IntegrationScope(provider="github", repositories={"Owner/Project"})
    guard_integration(scope, "get_file_contents", {"owner": "owner", "repo": "project"})
    for name, args in [
        ("get_file_contents", {"owner": "owner", "repo": "other"}),
        ("search_code", {"query": "repo:Owner/Project OR private"}),
        ("issue_read", {"owner": "Owner", "repo": "Project", "method": "delete"}),
        ("create_pull_request", {"owner": "Owner", "repo": "Project"}),
    ]:
        with pytest.raises(PolicyViolation):
            guard_integration(scope, name, args)
    with pytest.raises(PolicyViolation):
        guard_integration(IntegrationScope(provider="github"), "get_file_contents", {})


def test_filesystem_root_symlink_hardlink_sensitive_and_special_file_guards(tmp_path):
    root = tmp_path / "Documents"
    root.mkdir()
    scope = IntegrationScope(provider="filesystem", roots=(root,))
    path = root / "safe.txt"
    path.write_text("safe")
    guard_integration(scope, "read_text_file", {"path": str(path)})
    outside = tmp_path / "outside.txt"
    outside.write_text("outside")
    link = root / "linked.txt"
    link.symlink_to(outside)
    secret = root / ".env"
    secret.write_text("not-read")
    key = root / "private.pem"
    key.write_text("not-read")
    fifo = root / "fifo"
    os.mkfifo(fifo)
    hardlink = root / "hardlink.txt"
    os.link(outside, hardlink)
    large = root / "large.txt"
    with large.open("wb") as handle:
        handle.truncate(1_048_577)
    for denied in (outside, link, secret, key, fifo, hardlink, large, root / ".." / "outside.txt"):
        with pytest.raises(PolicyViolation):
            guard_integration(scope, "read_text_file", {"path": str(denied)})
    with pytest.raises(PolicyViolation):
        guard_integration(scope, "write_file", {"path": str(path), "content": "bad"})
    assert path.read_text() == "safe"


@pytest.mark.parametrize(
    "url",
    [
        "file:///etc/passwd",
        "javascript:alert(1)",
        "http://example.com",
        "https://other.com",
        "https://example.com@evil.com",
        "https://127.0.0.1",
        "https://example.com:444",
        "https://example.com\\@evil.com",
        "https://example.com\n.evil.com",
    ],
)
def test_browser_navigation_scope(url):
    scope = IntegrationScope(provider="playwright", browser_origins={"https://example.com"})
    with pytest.raises(PolicyViolation):
        guard_integration(scope, "browser_navigate", {"url": url})
    guard_integration(scope, "browser_navigate", {"url": "https://example.com/docs?q=one"})


@pytest.mark.parametrize(
    "tool",
    [
        "browser_evaluate",
        "browser_run_code",
        "browser_click",
        "browser_fill_form",
        "browser_file_upload",
        "browser_tabs",
    ],
)
def test_browser_cannot_mutate_or_execute_arbitrary_code(tool):
    scope = IntegrationScope(provider="playwright", browser_origins={"https://example.com"})
    with pytest.raises(PolicyViolation):
        guard_integration(scope, tool, {})


def test_home_assistant_is_observation_only_until_devices_are_reviewed():
    scope = IntegrationScope(provider="home-assistant")
    guard_integration(scope, "GetLiveContext", {})
    for tool in ("HassTurnOn", "HassTurnOff", "call_service", "HassLightSet", "HassLock"):
        with pytest.raises(PolicyViolation):
            guard_integration(scope, tool, {})


def configuration(**kwargs):
    return McpServerConfiguration(
        server_id="google-workspace",
        command=Path(sys.executable).resolve(),
        capability=Capability.WEB_READ,
        read_only=False,
        integration=google_scope(),
        allow_explicit_requests=True,
        **kwargs,
    )


async def test_natural_mail_selection_has_trusted_account_and_read_metadata(monkeypatch):
    search_schema = {
        "type": "object", "properties": {
            "user_google_email": {"type": "string"}, "query": {"type": "string"},
            "include_headers": {"type": "boolean", "default": False},
        }, "required": ["user_google_email", "query"],
    }
    draft_schema = {
        "type": "object", "properties": {
            "user_google_email": {"type": "string"}, "subject": {"type": "string"},
            "body": {"type": "string"},
        }, "required": ["user_google_email", "subject", "body"],
    }
    schemas = {"search_gmail_messages": search_schema, "draft_gmail_message": draft_schema}

    class Peer:
        available = True
        failure_code = None

        def __init__(self, *args, **kwargs):
            pass

        async def start(self):
            return tuple({"name": name, "inputSchema": schema} for name, schema in schemas.items())

        async def close(self):
            self.available = False

    monkeypatch.setattr("aegis_core.mcp.client.StdioMcpClient", Peer)
    config = configuration(enabled=True, approved_tools={
        name: reviewed_schema_sha256(schema, google_scope(), name)
        for name, schema in schemas.items()
    })
    host = McpHost(McpHostConfiguration(servers=(config,)))
    await host.start()
    try:
        selected = host.tool_names_for_request("Puedes ver mis ultimos correos pendientes?", None)
        assert len(selected) == 1
        definition = next(d for d in host.tool_definitions() if d.name in selected)
        assert definition.name.endswith("__search_gmail_messages")
        assert definition.requires_confirmation
        assert definition.parameters_schema["properties"]["user_google_email"]["enum"] == [
            "owner@example.com",
        ]
        assert definition.parameters_schema["properties"]["include_headers"]["type"] == "boolean"
        guard_integration(google_scope(), "search_gmail_messages",
                          google_arguments(query="in:inbox is:unread", include_headers=True))
        assert not host.tool_names_for_request("¿Qué es Gmail?", None)
    finally:
        await host.close()
    assert not host.tool_names_for_request("Revisa mi correo", None)


@pytest.mark.parametrize(
    "secrets",
    [
        {"NVIDIA_API_KEY": "default"},
        {"DYLD_INSERT_LIBRARIES": "default"},
        {"GOOGLE_OAUTH_CLIENT_SECRET": "ai.aegis/ipc"},
        {"PATH": "default"},
    ],
)
def test_secret_references_cannot_access_other_services_or_inject_runtime(secrets):
    with pytest.raises(ValidationError):
        configuration(secret_environment=secrets)


def test_secret_configuration_contains_references_not_values():
    config = configuration(secret_environment={"GOOGLE_OAUTH_CLIENT_SECRET": "oauth-client"})
    assert config.secret_environment == {"GOOGLE_OAUTH_CLIENT_SECRET": "oauth-client"}
    with pytest.raises(ValidationError):
        configuration(environment={"GOOGLE_OAUTH_CLIENT_SECRET": "plaintext"})


def test_google_generic_launcher_is_rejected_even_with_artifact_pins(tmp_path):
    script = tmp_path / "peer"
    script.write_text("original")
    config = configuration(
        artifact_sha256={
            str(Path(sys.executable).resolve()): artifact_sha256(Path(sys.executable).resolve()),
            str(script): artifact_sha256(script),
        }
    )
    with pytest.raises(McpConfigurationError):
        validate_integration_process(config)


async def test_host_rechecks_per_tool_policy_after_confirmation(monkeypatch, tmp_path):
    raw = drive_schema()
    calls = []

    class Peer:
        available = True
        failure_code = None

        def __init__(self, *args, **kwargs):
            pass

        async def start(self):
            return ({"name": "update_drive_file", "inputSchema": raw},)

        async def close(self):
            self.available = False

        async def call_tool(self, name, arguments):
            calls.append((name, arguments))
            return {"content": [{"type": "text", "text": "synthetic"}]}

    monkeypatch.setattr("aegis_core.mcp.client.StdioMcpClient", Peer)
    config = configuration(
        enabled=True,
        approved_tools={
            "update_drive_file": reviewed_schema_sha256(raw, google_scope(), "update_drive_file")
        },
    )
    host = McpHost(McpHostConfiguration(servers=(config,)))
    await host.start()
    try:
        (definition,) = host.tool_definitions()
        assert definition.capability is Capability.APPLICATION_CONTROL
        assert host.tool_names_for_request("abre Google Workspace", None) == {definition.name}
        assert not host.tool_names_for_request("hola", None)
        assert not host.tool_names_for_request("notgoogle workspacefake", None)
        broker = build_default_tool_broker((definition,))
        call = ToolCall(
            call_id="integration",
            tool_name=definition.name,
            arguments=google_arguments(file_id="f", name="New title"),
            requested_by=AgentRole.PLANNER,
        )
        context = default_policy_context(tmp_path)
        authorization = broker.authorize(call, context)
        assert authorization.decision is PolicyDecision.REQUIRE_CONFIRMATION
        presenter = JobToolPresenter(broker)
        assert presenter.supports_confirmation(definition.name)
        summary = presenter.confirmation_summary(authorization)
        assert "MCP: google-workspace" in summary
        assert "[file_id, name, user_google_email]" in summary
        assert "New title" not in summary
        store = OneTimeConfirmationStore()
        store.issue(call, authorization, approved_by="test-user")
        context = replace(context, confirmation_store=store)
        authorization = broker.authorize(call, context)
        result = await host.handlers()[definition.name](authorization, context)
        assert result.success and len(calls) == 1
        rendered = presenter.format_result(result, authorization)
        assert rendered.startswith("MCP google-workspace respondió:")
        assert "synthetic" in rendered
        # Even if someone bypasses broker validation, the relay revalidates.
        forged = authorization.model_copy(
            update={"normalized_arguments": google_arguments(file_id="f", trashed=True)}
        )
        result = await host.handlers()[definition.name](forged, context)
        assert not result.success and len(calls) == 1
    finally:
        await host.close()


async def test_explicit_remote_tool_name_narrows_integration_catalogue(monkeypatch):
    raw = drive_schema()

    class Peer:
        available = True
        failure_code = None

        def __init__(self, *args, **kwargs):
            pass

        async def start(self):
            return tuple(
                {"name": name, "inputSchema": raw}
                for name in ("get_drive_file_content", "update_drive_file")
            )

        async def close(self):
            self.available = False

    monkeypatch.setattr("aegis_core.mcp.client.StdioMcpClient", Peer)
    approved = {
        name: reviewed_schema_sha256(raw, google_scope(), name)
        for name in ("get_drive_file_content", "update_drive_file")
    }
    host = McpHost(McpHostConfiguration(servers=(configuration(
        enabled=True,
        approved_tools=approved,
    ),)))
    await host.start()
    try:
        definitions = {definition.name: definition for definition in host.tool_definitions()}
        all_names = host.tool_names_for_request("abre Google Workspace", None)
        assert all_names == set(definitions)
        update_name = next(name for name in definitions if name.endswith("update_drive_file"))
        assert host.tool_names_for_request(
            "Google Workspace update_drive_file", None
        ) == {update_name}
        assert host.tool_names_for_request(
            f"Google Workspace {update_name}", None
        ) == {update_name}
    finally:
        await host.close()


def test_catalogue_never_claims_connections_or_approvals():
    report = integration_catalogue()
    assert report["status"] == "profiles_available_not_connected"
    assert len(report["integrations"]) == 5
    assert len(integration_tools(IntegrationId.FILESYSTEM)) == 5


def test_preparation_is_private_disabled_and_never_overwrites(monkeypatch, tmp_path):
    from aegis_core.mcp.client import load_mcp_configuration
    from aegis_core.mcp.setup import prepare_local_profiles

    owner = tmp_path / "owner"
    owner.mkdir()
    for name in ("Documents", "Downloads"):
        (owner / name).mkdir()

    def fake_local(scope, **kwargs):
        return McpServerConfiguration(
            server_id=scope.provider.value,
            command=Path(sys.executable).resolve(),
            capability=Capability.FILESYSTEM_READ,
            integration=scope,
        )

    monkeypatch.setattr("aegis_core.mcp.setup.local_peer_configuration", fake_local)
    monkeypatch.setattr("aegis_core.mcp.setup.shutil.which", lambda name: sys.executable)
    destination = tmp_path / "profiles"
    report = prepare_local_profiles(destination, packages=tmp_path, owner_home=owner)
    assert report["live_configuration_changed"] is False and report["accounts_connected"] == 0
    assert len(report["files"]) == 5
    for path in map(Path, report["files"]):
        assert path.stat().st_mode & 0o077 == 0
        config = load_mcp_configuration(path)
        assert not config.servers[0].enabled
    with pytest.raises(ValueError):
        prepare_local_profiles(destination, packages=tmp_path, owner_home=owner)


def test_local_mcp_can_be_offline_but_a_provider_name_or_skill_cannot_grant_it():
    from aegis_core.contracts import UserRequest
    from aegis_core.engineering import ENGINEERING_RESEARCH_METADATA, ENGINEERING_SURFACE_METADATA
    from aegis_core.orchestration.graph import _respect_engineering_offline

    request = UserRequest(
        text="Filesystem",
        metadata={
            "interaction_surface": ENGINEERING_SURFACE_METADATA,
            ENGINEERING_RESEARCH_METADATA: "offline",
        },
    )
    names = frozenset({"mcp_filesystem__read_text_file", "mcp_github__get_file_contents"})
    assert not _respect_engineering_offline(request, names)
    assert _respect_engineering_offline(
        request,
        names,
        frozenset(
            {
                "mcp_filesystem__read_text_file",
            }
        ),
    ) == {"mcp_filesystem__read_text_file"}
