from __future__ import annotations

import json
from dataclasses import replace

import pytest

from aegis_core.contracts import AgentRole, Capability, PolicyDecision, ToolCall
from aegis_core.mcp.client import mcp_schema_sha256
from aegis_core.plugins.context7 import (
    CONTEXT7_ENDPOINT,
    CONTEXT7_PLUGIN_ID,
    CONTEXT7_TOOLS,
    REVIEWED_SCHEMAS,
    catalogue_report,
    context7_package,
    probe_context7,
    validate_context7_arguments,
)
from aegis_core.plugins.contracts import PluginPackage
from aegis_core.plugins.runtime import (
    PluginExecutionError,
    PluginMcpClient,
    PluginRuntime,
    PluginToolBinding,
    _decode_mcp_response,
)
from aegis_core.plugins.store import PluginStore
from aegis_core.skills import SkillRegistry, SkillStore
from aegis_core.tools.broker import PolicyViolation
from aegis_core.tools.confirmations import OneTimeConfirmationStore
from aegis_core.tools.defaults import build_default_tool_broker, default_policy_context


def binding() -> PluginToolBinding:
    pack = context7_package()
    connector = pack.manifest.connectors[0]
    return PluginToolBinding(
        plugin_id=CONTEXT7_PLUGIN_ID,
        plugin_name=pack.manifest.name,
        connector=connector,
        tool=connector.tools[0],
        exposed_name="plugin_context7_docs__docs__resolve_library_id",
    )


def test_context7_pack_has_only_read_tools_pins_and_explicit_activation(tmp_path):
    pack = context7_package()
    assert pack.manifest.declared_capabilities == {Capability.WEB_READ}
    assert pack.manifest.allowed_network_hosts == {"mcp.context7.com"}
    connector = pack.manifest.connectors[0]
    assert connector.endpoint == CONTEXT7_ENDPOINT
    assert {t.name: t.remote_input_schema_sha256 for t in connector.tools} == REVIEWED_SCHEMAS
    active = PluginRuntime((pack,))
    definitions = active.tool_definitions()
    assert len(definitions) == 2
    assert all(d.requires_confirmation for d in definitions)
    assert all(d.external_destination == "mcp.context7.com" for d in definitions)
    registry = SkillRegistry(
        build_default_tool_broker(definitions),
        SkillStore(tmp_path / "skills"),
        builtins=(),
        plugin_skills=active.skill_manifests(),
    )
    assert registry.select("consulta context7 sobre Python") is not None
    assert registry.select("explica la cancelación de tareas en Python") is None
    store = PluginStore(tmp_path / "plugins", lambda: b"s" * 32)
    store.install(pack)
    assert store.enabled_packages() == (pack,)
    assert store.verify() == {CONTEXT7_PLUGIN_ID: True}


@pytest.mark.parametrize(
    "query",
    [
        "token=secret",
        "API_KEY: private",
        "nvapi-private",
        "ghp_private",
        "github_pat_private",
        "Bearer abcdef",
        "ctx7sk-private",
        "consulta persona@example.com",
        "https://internal.example",
        "/Users/owner/private.py",
        "/home/owner/private",
        "C:\\private\\secret",
        "```python pass```",
        "private\nsource",
        "x" * 513,
    ],
)
def test_private_queries_are_denied_before_confirmation_or_network(tmp_path, query):
    active = PluginRuntime((context7_package(),))
    definition = active.tool_definitions()[0]
    authorization = build_default_tool_broker((definition,)).authorize(
        ToolCall(
            call_id="private-input",
            tool_name=definition.name,
            requested_by=AgentRole.PLANNER,
            arguments={"query": query, "libraryName": "Python"},
        ),
        default_policy_context(tmp_path),
    )
    assert authorization.decision is PolicyDecision.DENY
    assert authorization.reason_code == "invalid_arguments"


def test_public_question_still_requires_one_time_approval(tmp_path):
    class Client:
        def call(self, binding, arguments):
            return {"content": [{"type": "text", "text": "public docs"}], "isError": False}

    active = PluginRuntime((context7_package(),), client=Client())
    definition = active.tool_definitions()[0]
    broker = build_default_tool_broker((definition,))
    call = ToolCall(
        call_id="public-call",
        tool_name=definition.name,
        requested_by=AgentRole.PLANNER,
        arguments={"query": "asyncio task cancellation", "libraryName": "Python"},
    )
    context = default_policy_context(tmp_path)
    pending = broker.authorize(call, context)
    assert pending.decision is PolicyDecision.REQUIRE_CONFIRMATION
    assert not active.handlers()[definition.name](pending, context).success
    store = OneTimeConfirmationStore()
    store.issue(call, pending, approved_by="local-user")
    context = replace(context, confirmation_store=store)
    approved = broker.authorize(call, context)
    assert active.handlers()[definition.name](approved, context).success
    assert broker.authorize(call, context).reason_code == "confirmation_replayed"
    wrong = approved.model_copy(update={"tool_name": "different_tool"})
    assert not active.handlers()[definition.name](wrong, context).success


@pytest.mark.parametrize(
    "result",
    [
        {"resultType": "input_required"},
        {"resultType": "unknown"},
    ],
)
def test_mcp_never_follows_unrequested_interaction(result):
    body = json.dumps({"jsonrpc": "2.0", "id": "test", "result": result}).encode()
    with pytest.raises(PluginExecutionError):
        _decode_mcp_response(body, "application/json", "test")


@pytest.mark.parametrize(
    "payload",
    [
        [],
        None,
        {"jsonrpc": "2.0", "id": "wrong", "result": {}},
        {"jsonrpc": "2.0", "id": "test", "result": {}, "error": {}},
        {"jsonrpc": "2.0", "id": "test", "method": "roots/list", "result": {}},
    ],
)
def test_response_envelope_is_strict(payload):
    with pytest.raises(PluginExecutionError):
        _decode_mcp_response(json.dumps(payload).encode(), "application/json", "test")


def test_sse_parser_supports_multiline_but_no_extra_requests():
    data = b'event: message\ndata: {"jsonrpc":"2.0",\ndata: "id":"test","result":{}}\n\n'
    assert _decode_mcp_response(data, "text/event-stream", "test") == {}
    with pytest.raises(PluginExecutionError):
        _decode_mcp_response(data + data, "text/event-stream", "test")


def test_pinned_catalogue_is_checked_before_every_call_and_never_auto_updated():
    approved = binding()
    schema = {"type": "object", "properties": {}}
    pinned_tool = approved.tool.model_copy(
        update={"remote_input_schema_sha256": mcp_schema_sha256(schema)}
    )
    connector = approved.connector.model_copy(update={"tools": (pinned_tool,)})
    approved = replace(approved, connector=connector, tool=pinned_tool)

    class Client(PluginMcpClient):
        drift = False

        def __init__(self):
            self.calls = []

        def request(self, plugin_id, connector, method, parameters):
            self.calls.append(method)
            if method == "tools/list":
                remote_schema = {"type": "object", "properties": {}}
                if self.drift:
                    remote_schema["properties"]["new"] = {"type": "string"}
                return {"tools": [{"name": "resolve-library-id", "inputSchema": remote_schema}]}
            return {"content": [{"type": "text", "text": "public"}], "resultType": "complete"}

    client = Client()
    arguments = {"query": "asyncio cancellation", "libraryName": "Python"}
    assert client.call(approved, arguments)["isError"] is False
    assert client.calls == ["tools/list", "tools/call"]
    client.drift = True
    with pytest.raises(PluginExecutionError, match="schema changed"):
        client.call(approved, arguments)
    assert client.calls == ["tools/list", "tools/call", "tools/list"]


def test_probe_uses_only_fixed_public_input_and_reports_no_content():
    class Client:
        def __init__(self):
            self.calls = []

        def call(self, binding, arguments):
            self.calls.append((binding.tool.name, arguments))
            return {"content": [{"type": "text", "text": "/python/cpython asyncio cancellation"}]}

    client = Client()
    result = probe_context7(client)
    assert result["status"] == "passed"
    assert result["user_content_sent"] is result["source_code_sent"] is False
    assert {name for name, _ in client.calls} == set(CONTEXT7_TOOLS)
    assert all(args["query"] == "asyncio task cancellation" for _, args in client.calls)
    assert "asyncio" not in json.dumps(result)


def test_probe_does_not_claim_success_on_quota_or_empty_results():
    class Client:
        def call(self, binding, arguments):
            return {"content": [{"type": "text", "text": "quota reached"}]}

    with pytest.raises(PluginExecutionError):
        probe_context7(Client())


def test_catalogue_review_does_not_invoke_tools_or_approve():
    class Client:
        def discover_tools(self, *args):
            return [{"name": name, "inputSchema": {"type": "object"}} for name in CONTEXT7_TOOLS]

    report = catalogue_report(Client())
    assert report["tool_calls"] == 0
    assert report["approved"] is False


def test_direct_calls_reject_unreviewed_tool_and_library_id():
    for name, arguments in [
        ("delete", {"query": "x", "libraryId": "/python/cpython"}),
        ("query-docs", {"query": "asyncio", "libraryId": "../private"}),
    ]:
        with pytest.raises(PolicyViolation):
            validate_context7_arguments(name, arguments)


def test_pack_roundtrip_keeps_pins_and_detects_tampering():
    package = context7_package()
    assert PluginPackage.model_validate_json(package.model_dump_json()) == package
    raw = package.model_dump(mode="json")
    raw["manifest"]["connectors"][0]["tools"][0]["remote_input_schema_sha256"] = "0" * 64
    with pytest.raises(ValueError, match="checksum"):
        PluginPackage.model_validate(raw)


def test_existing_package_without_pins_keeps_legacy_checksum():
    from test_plugins import _package

    package = _package()
    raw = package.model_dump(mode="json")
    assert "remote_input_schema_sha256" not in raw["manifest"]["connectors"][0]["tools"][0]
    assert PluginPackage.model_validate(raw).checksum_sha256 == package.checksum_sha256


def test_normalized_remote_names_cannot_collide():
    package = context7_package()
    raw = package.manifest.model_dump(mode="python")
    connector = raw["connectors"][0]
    duplicate = dict(connector["tools"][0])
    duplicate["name"] = "resolve_library_id"
    connector["tools"] = (*connector["tools"], duplicate)
    with pytest.raises(ValueError, match="collide"):
        type(package.manifest).model_validate(raw)


def test_committed_package_matches_reviewed_builder():
    from pathlib import Path

    from aegis_core.plugins.store import load_plugin_package

    source = Path(__file__).parents[1] / "examples/plugins/context7-docs.jarvis-plugin.json"
    assert load_plugin_package(source) == context7_package()


@pytest.mark.parametrize(
    "body",
    [
        {"tools": [{"name": "duplicate"}, {"name": "duplicate"}]},
        {"tools": [None]},
        {"tools": {}, "nextCursor": None},
        {"tools": [], "nextCursor": "more"},
    ],
)
def test_catalogue_duplicates_and_pagination_fail_closed(body):
    class Client(PluginMcpClient):
        def request(self, *args):
            return body

    with pytest.raises(PluginExecutionError):
        Client().discover_tools(CONTEXT7_PLUGIN_ID, binding().connector)


def test_http_tool_error_is_not_reported_as_success(tmp_path):
    class Client:
        def call(self, *args):
            return {"content": [], "isError": True}

    active = PluginRuntime((context7_package(),), client=Client())
    definition = active.tool_definitions()[0]
    context = default_policy_context(tmp_path)
    call = ToolCall(
        call_id="server-error",
        tool_name=definition.name,
        requested_by=AgentRole.PLANNER,
        arguments={"query": "asyncio tasks", "libraryName": "Python"},
    )
    pending = build_default_tool_broker((definition,)).authorize(call, context)
    approved = pending.model_copy(
        update={"decision": PolicyDecision.ALLOW, "reason_code": "confirmation_consumed"}
    )
    result = active.handlers()[definition.name](approved, context)
    assert result.success is False
    assert result.error_code == "mcp_tool_error"


@pytest.mark.parametrize("policy", ["offline", "public_web", None])
def test_context7_cannot_override_engineering_network_policy(tmp_path, policy):
    from aegis_core.contracts import UserRequest
    from aegis_core.orchestration.routing import _effective_tool_names

    active = PluginRuntime((context7_package(),))
    registry = SkillRegistry(build_default_tool_broker(active.tool_definitions()),
                             SkillStore(tmp_path / "skills"), builtins=(),
                             plugin_skills=active.skill_manifests())
    request = UserRequest(text="consulta context7 sobre asyncio", metadata={
        "interaction_surface": "engineering_cli", "engineering_research_policy": policy,
    })
    names = _effective_tool_names(request, registry.select(request.text))
    assert bool(names) is (policy == "public_web")
