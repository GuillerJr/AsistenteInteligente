"""Owner IPC uses synthetic, private temporary sources; no external requests."""

import json
from unittest.mock import Mock

import pytest
from test_plugins import FakeHttpResponse, FakeMcpClient, _package

from aegis_core.contracts import AgentRole, ToolCall
from aegis_core.extensions_service import ExtensionsIpcService
from aegis_core.ipc.protocol import IpcAuthenticator
from aegis_core.mcp.client import mcp_schema_sha256
from aegis_core.plugins import PluginPackage
from aegis_core.plugins.runtime import PluginMcpClient, PluginRuntime
from aegis_core.plugins.store import PluginStore
from aegis_core.skills import SkillRegistry, SkillStore
from aegis_core.skills.contracts import SkillDraft
from aegis_core.skills.policy import SkillPolicyStore
from aegis_core.tools.defaults import build_default_tool_broker, default_policy_context


@pytest.fixture
def extensions(tmp_path):
    store = PluginStore(tmp_path / "plugins", lambda: b"s" * 32)
    package = _package()
    store.install(package)
    runtime = PluginRuntime((package,), client=FakeMcpClient(), store=store)
    broker = build_default_tool_broker(runtime.tool_definitions())
    registry = SkillRegistry(
        broker,
        SkillStore(tmp_path / "skills"),
        plugin_skills=runtime.skill_manifests(),
        policy=SkillPolicyStore(tmp_path / "policy.json"),
    )
    busy = {"jobs": False, "settings": False}
    instance = ExtensionsIpcService(
        registry,
        store,
        runtime,
        broker,
        jobs_busy=lambda: busy["jobs"],
        settings_busy=lambda: busy["settings"],
    )
    instance.busy_fixture = busy
    return instance


async def ipc(service, method, **payload):
    return await service.handle(IpcAuthenticator(b"i" * 32).create_request(method, payload=payload))


def source(tmp_path, *, text="Resume las facturas respetando sus fechas."):
    draft = SkillDraft(
        skill_id="invoice-review",
        name="Facturas",
        description="Revisión declarativa de facturas.",
        role=AgentRole.PLANNER,
        trigger_phrases=("revisa facturas",),
        trigger_terms=frozenset({"facturas"}),
        minimum_term_matches=1,
        instructions=(text,),
    )
    path = tmp_path / "invoice.json"
    path.write_text(draft.model_dump_json(), encoding="utf-8")
    return path


async def test_catalogue_has_all_origins_and_no_management_agent_tools(extensions):
    result = await ipc(extensions, "extensions.status")
    assert result.ok and not result.payload["busy"]
    assert {s["origin"] for s in result.payload["skills"]} == {"builtin", "plugin"}
    plugin = result.payload["plugins"][0]
    assert plugin["active"] and plugin["integrity"] == "intact"
    assert result.payload["tool_options"]["planner"]
    tools = {s["function"]["name"] for s in extensions.broker.schemas_for(AgentRole.PLANNER)}
    assert not tools & extensions.METHODS


def test_external_enable_of_unloaded_pack_sets_top_level_restart_required(extensions):
    package = _package()
    additional = PluginPackage.create(
        package.manifest.model_copy(
            update={"plugin_id": "other-pack", "skills": (), "skill_resources": {}}
        ),
        {},
    )
    extensions.store.install(additional, enabled=False)
    assert not extensions.status()["restart_required"]
    extensions.store.set_enabled("other-pack", True)
    status = extensions.status()
    assert status["restart_required"]
    assert next(p for p in status["plugins"] if p["plugin_id"] == "other-pack")["restart_required"]


async def test_installed_plugin_details_require_exact_review_hash_without_execution(extensions):
    package = extensions.store.get("research-kit").package
    result = await ipc(
        extensions,
        "extensions.plugin.details",
        plugin_id="research-kit",
        checksum_sha256=package.checksum_sha256,
    )
    assert result.ok
    assert result.payload["checksum_sha256"] == package.checksum_sha256
    assert len(result.payload["source_sha256"]) == 64
    assert result.payload["skills"][0]["instructions"]
    assert result.payload["skills"][0]["resources"] == [package.resources["references/usage.md"]]
    assert result.payload["hosts"] and result.payload["tools"]
    stale = await ipc(
        extensions, "extensions.plugin.details", plugin_id="research-kit", checksum_sha256="0" * 64
    )
    assert not stale.ok and stale.error_code == "extensions_source_changed"
    missing = await ipc(extensions, "extensions.plugin.details", plugin_id="research-kit")
    assert not missing.ok
    assert not extensions.runtime._client.calls


async def test_installed_plugin_details_reject_unsealed_source(extensions):
    package = extensions.store.get("research-kit").package
    path = extensions.store.directory / "research-kit.json"
    raw = json.loads(path.read_text())
    raw["enabled"] = False
    path.write_text(json.dumps(raw))
    result = await ipc(
        extensions,
        "extensions.plugin.details",
        plugin_id="research-kit",
        checksum_sha256=package.checksum_sha256,
    )
    assert not result.ok and result.error_code == "extensions_plugin_not_found"
    assert not extensions.runtime._client.calls


async def test_reimport_preserves_compromised_source_before_replacing(extensions, tmp_path):
    package = extensions.store.get("research-kit").package
    installed_path = extensions.store.directory / "research-kit.json"
    damaged = b'{"corrupt": "retain private original"}'
    installed_path.write_bytes(damaged)
    path = tmp_path / "new-pack.json"
    path.write_text(package.model_dump_json())
    preview = await ipc(extensions, "extensions.plugin.preview", path=str(path))
    assert preview.ok
    result = await ipc(
        extensions,
        "extensions.plugin.import",
        path=str(path),
        source_sha256=preview.payload["source_sha256"],
        confirm=True,
    )
    assert result.ok and not extensions.store.get("research-kit").enabled
    archives = list((extensions.store.directory / ".archive").glob("*.json"))
    assert len(archives) == 1 and archives[0].read_bytes() == damaged
    assert archives[0].stat().st_mode & 0o777 == 0o600
    assert not extensions.runtime.is_active("research-kit")
    assert not extensions.runtime._client.calls


@pytest.mark.parametrize("field", ["jobs", "settings"])
async def test_pending_jobs_or_settings_block_mutations_before_any_write(extensions, field):
    extensions.busy_fixture[field] = True
    before = extensions.store.get("research-kit")
    result = await ipc(
        extensions,
        "extensions.plugin.set_enabled",
        plugin_id="research-kit",
        enabled=False,
        confirm=True,
    )
    assert not result.ok and result.error_code == "extensions_busy"
    assert extensions.store.get("research-kit") == before
    assert extensions.runtime.is_active("research-kit")
    assert (await ipc(extensions, "extensions.status")).payload["busy"]


async def test_import_skill_disabled_then_enable_and_exact_hash_remote_consent(
    extensions, tmp_path
):
    path = source(tmp_path)
    preview = await ipc(extensions, "extensions.skill.preview", path=str(path))
    assert preview.ok and preview.payload["skill_id"] == "invoice-review"
    imported = await ipc(
        extensions,
        "extensions.skill.import",
        path=str(path),
        source_sha256=preview.payload["source_sha256"],
        choices={},
        confirm=True,
    )
    assert imported.ok
    record = imported.payload["skill"]
    assert not record["enabled"] and not record["remote_allowed"]
    selection = {"skill_id": record["skill_id"], "content_sha256": record["content_sha256"]}
    detail = await ipc(extensions, "extensions.skill.details", **selection)
    assert detail.ok and detail.payload["instructions"]
    denied = await ipc(
        extensions, "extensions.skill.remote", **selection, approved=True, confirm=False
    )
    assert not denied.ok and denied.error_code == "extensions_confirmation_required"
    enabled = await ipc(
        extensions, "extensions.skill.set_enabled", **selection, enabled=True, confirm=True
    )
    assert enabled.ok and enabled.payload["skill"]["enabled"]
    approved = await ipc(
        extensions, "extensions.skill.remote", **selection, approved=True, confirm=True
    )
    assert approved.ok and approved.payload["skill"]["remote_allowed"]
    rejected = await ipc(
        extensions,
        "extensions.skill.remote",
        skill_id=record["skill_id"],
        content_sha256="0" * 64,
        approved=True,
        confirm=True,
    )
    assert not rejected.ok


async def test_changed_source_is_rejected_without_import_or_execution(extensions, tmp_path):
    path = source(tmp_path)
    preview = await ipc(extensions, "extensions.skill.preview", path=str(path))
    path.write_text(source(tmp_path, text="Respeta la última corrección del usuario.").read_text())
    result = await ipc(
        extensions,
        "extensions.skill.import",
        path=str(path),
        source_sha256=preview.payload["source_sha256"],
        choices={},
        confirm=True,
    )
    assert not result.ok and result.error_code == "extensions_source_changed"
    assert not extensions.registry.store.load_all()
    assert not extensions.runtime._client.calls


async def test_skill_import_cannot_invent_a_tool_permission(extensions, tmp_path):
    path = source(tmp_path)
    raw = json.loads(path.read_text())
    raw["allowed_tools"] = ["unregistered_sensitive_action"]
    path.write_text(json.dumps(raw))
    schemas = extensions.broker.schemas_for(AgentRole.PLANNER)
    preview = await ipc(extensions, "extensions.skill.preview", path=str(path))
    assert preview.ok
    result = await ipc(
        extensions,
        "extensions.skill.import",
        path=str(path),
        source_sha256=preview.payload["source_sha256"],
        confirm=True,
    )
    assert not result.ok and not extensions.registry.store.load_all()
    assert extensions.broker.schemas_for(AgentRole.PLANNER) == schemas
    assert not extensions.runtime._client.calls


@pytest.mark.parametrize("existing", [False, True])
async def test_invalid_policy_rejects_skill_import_before_write_or_archive(
    extensions, tmp_path, existing
):
    path = source(tmp_path)
    stored = extensions.registry.store.directory / "invoice-review.json"
    if existing:
        extensions.registry.learn(SkillDraft.model_validate_json(path.read_text()))
        previous = stored.read_bytes()
    else:
        previous = None
    policy = extensions.registry.policy.path
    policy.write_text("{corrupt")
    policy.chmod(0o600)
    before = policy.read_bytes()
    path = source(tmp_path, text="Compara las cifras corregidas con la fuente original.")
    source_before = path.read_bytes()
    preview = await ipc(extensions, "extensions.skill.preview", path=str(path))
    result = await ipc(
        extensions,
        "extensions.skill.import",
        path=str(path),
        source_sha256=preview.payload["source_sha256"],
        confirm=True,
    )
    assert not result.ok and result.error_code == "extensions_policy_unavailable"
    assert policy.read_bytes() == before and path.read_bytes() == source_before
    if existing:
        assert stored.read_bytes() == previous
    else:
        assert not stored.exists()
    assert not (stored.parent / ".archive").exists()
    assert not extensions.runtime._client.calls


async def test_full_policy_rejects_new_skill_import_before_file_write(extensions, tmp_path):
    path = source(tmp_path)
    policy = extensions.registry.policy.path
    policy.write_text(json.dumps({"choices": {f"reserved-{i}": {} for i in range(512)}}))
    policy.chmod(0o600)
    before = policy.read_bytes()
    preview = await ipc(extensions, "extensions.skill.preview", path=str(path))
    result = await ipc(
        extensions,
        "extensions.skill.import",
        path=str(path),
        source_sha256=preview.payload["source_sha256"],
        confirm=True,
    )
    assert not result.ok and result.error_code == "extensions_policy_unavailable"
    assert policy.read_bytes() == before
    assert not extensions.registry.store.directory.exists()
    assert not extensions.runtime._client.calls


async def test_invalid_skill_policy_still_allows_plugin_revocation_and_removal(extensions):
    package = extensions.store.get("research-kit").package
    policy = extensions.registry.policy.path
    policy.write_text("{corrupt")
    policy.chmod(0o600)
    disabled = await ipc(
        extensions,
        "extensions.plugin.set_enabled",
        plugin_id="research-kit",
        checksum_sha256=package.checksum_sha256,
        enabled=False,
        confirm=True,
    )
    assert disabled.ok and not extensions.runtime.is_active("research-kit")
    removed = await ipc(
        extensions,
        "extensions.plugin.remove",
        plugin_id="research-kit",
        checksum_sha256=package.checksum_sha256,
        confirm=True,
    )
    assert removed.ok and extensions.store.get("research-kit") is None
    assert policy.read_text() == "{corrupt"
    assert not extensions.runtime._client.calls


async def test_markdown_import_needs_owner_role_and_triggers(extensions, tmp_path):
    path = tmp_path / "SKILL.md"
    path.write_text(
        "---\nname: invoice-guide\ndescription: Revisa facturas con cuidado.\n---\n"
        "Conserva las fechas y los importes completos.\n"
    )
    preview = await ipc(extensions, "extensions.skill.preview", path=str(path))
    assert preview.ok and preview.payload["role"] is None
    digest = preview.payload["source_sha256"]
    denied = await ipc(
        extensions,
        "extensions.skill.import",
        path=str(path),
        source_sha256=digest,
        choices={},
        confirm=True,
    )
    assert not denied.ok
    imported = await ipc(
        extensions,
        "extensions.skill.import",
        path=str(path),
        source_sha256=digest,
        choices={
            "role": "planner",
            "trigger_phrases": ["revisa facturas"],
            "trigger_terms": ["facturas"],
            "allowed_tools": [],
            "starter_tools": [],
        },
        confirm=True,
    )
    assert imported.ok and not imported.payload["skill"]["enabled"]
    assert not extensions.runtime._client.calls


async def test_executable_markdown_stays_disabled_until_reimported_as_declarative(
    extensions, tmp_path
):
    path = tmp_path / "SKILL.md"
    header = "---\nname: invoice-guide\ndescription: Revisa facturas con cuidado.\n---\n"
    path.write_text(header + "Ejecuta el script de análisis antes de resumir las facturas.\n")
    choices = {"role": "planner", "trigger_phrases": ["revisa facturas"]}

    async def import_current():
        preview = await ipc(extensions, "extensions.skill.preview", path=str(path))
        assert preview.ok
        return await ipc(
            extensions,
            "extensions.skill.import",
            path=str(path),
            source_sha256=preview.payload["source_sha256"],
            choices=choices,
            confirm=True,
        )

    imported = await import_current()
    assert imported.ok
    record = imported.payload["skill"]
    assert not record["enabled"]
    blocked = await ipc(
        extensions,
        "extensions.skill.set_enabled",
        skill_id=record["skill_id"],
        content_sha256=record["content_sha256"],
        enabled=True,
        confirm=True,
    )
    assert not blocked.ok
    assert blocked.error_code == "extensions_executable_unsupported"
    assert not next(
        s for s in extensions.registry.catalogue() if s["skill_id"] == record["skill_id"]
    )["enabled"]
    consent = await ipc(
        extensions,
        "extensions.skill.remote",
        skill_id=record["skill_id"],
        content_sha256=record["content_sha256"],
        approved=True,
        confirm=True,
    )
    assert not consent.ok and consent.error_code == "extensions_executable_unsupported"
    path.write_text(header + "Compara las fechas y conserva los importes completos.\n")
    adapted = await import_current()
    assert adapted.ok
    updated = adapted.payload["skill"]
    enabled = await ipc(
        extensions,
        "extensions.skill.set_enabled",
        skill_id=updated["skill_id"],
        content_sha256=updated["content_sha256"],
        enabled=True,
        confirm=True,
    )
    assert enabled.ok and enabled.payload["skill"]["enabled"]
    assert not enabled.payload["skill"]["remote_allowed"]
    assert not extensions.runtime._client.calls


async def test_plugin_disable_revokes_loaded_handler_and_skill_immediately(extensions, tmp_path):
    name = next(iter(extensions.runtime.handlers()))
    authorization = extensions.broker.authorize(
        ToolCall(
            call_id="synthetic",
            tool_name=name,
            arguments={"query": "public docs"},
            requested_by=AgentRole.PLANNER,
        ),
        default_policy_context(tmp_path),
        allowed_names=frozenset({name}),
    )
    result = await ipc(
        extensions,
        "extensions.plugin.set_enabled",
        plugin_id="research-kit",
        checksum_sha256=extensions.store.get("research-kit").package.checksum_sha256,
        enabled=False,
        confirm=True,
    )
    assert result.ok and result.payload["restart_required"]
    outcome = extensions.runtime.handlers()[name](authorization, default_policy_context(tmp_path))
    assert not outcome.success and outcome.error_code == "plugin_disabled_restart_required"
    assert not extensions.runtime._client.calls
    assert not any(s.origin.value == "plugin" for s in extensions.registry.all())
    active = await ipc(
        extensions,
        "extensions.plugin.set_enabled",
        plugin_id="research-kit",
        enabled=True,
        confirm=True,
    )
    assert active.ok and not extensions.runtime.is_active("research-kit")


def test_out_of_process_disable_or_update_fails_closed(extensions, tmp_path):
    name = next(iter(extensions.runtime.handlers()))
    authorization = extensions.broker.authorize(
        ToolCall(
            call_id="synthetic",
            tool_name=name,
            arguments={"query": "public docs"},
            requested_by=AgentRole.PLANNER,
        ),
        default_policy_context(tmp_path),
        allowed_names=frozenset({name}),
    )
    extensions.store.set_enabled("research-kit", False)
    outcome = extensions.runtime.handlers()[name](authorization, default_policy_context(tmp_path))
    assert not outcome.success and not extensions.runtime._client.calls
    extensions.store.set_enabled("research-kit", True)
    assert not extensions.runtime.is_active("research-kit")


def test_runtime_revocation_guard_reads_only_selected_sealed_record(extensions, monkeypatch):
    from aegis_core.plugins import store as store_module

    (extensions.store.directory / "other-kit.json").write_text('{"invalid":true}')
    read = Mock(wraps=store_module._read_regular_file)
    monkeypatch.setattr(store_module, "_read_regular_file", read)
    assert extensions.runtime.is_active("research-kit")
    assert read.call_count == 1
    assert read.call_args.args[0].name == "research-kit.json"


def pinned_runtime(tmp_path):
    package = _package()
    connector = package.manifest.connectors[0]
    tool = connector.tools[0]
    pinned = tool.model_copy(
        update={"remote_input_schema_sha256": mcp_schema_sha256(tool.input_schema)}
    )
    package = PluginPackage.create(
        package.manifest.model_copy(
            update={"connectors": (connector.model_copy(update={"tools": (pinned,)}),)}
        ),
        package.resources,
    )
    store = PluginStore(tmp_path / "plugins", lambda: b"s" * 32)
    store.install(package)
    client = PluginMcpClient()
    runtime = PluginRuntime((package,), client=client, store=store)
    broker = build_default_tool_broker(runtime.tool_definitions())
    name = next(iter(runtime.handlers()))
    context = default_policy_context(tmp_path)
    authorization = broker.authorize(
        ToolCall(
            call_id="synthetic",
            tool_name=name,
            arguments={"query": "public docs"},
            requested_by=AgentRole.PLANNER,
        ),
        context,
        allowed_names=frozenset({name}),
    )
    return store, client, runtime, name, context, authorization


def test_revocation_during_catalogue_preflight_prevents_new_tools_call(tmp_path, monkeypatch):
    store, client, runtime, name, context, authorization = pinned_runtime(tmp_path)
    monkeypatch.setattr(
        client, "verify_catalogue", lambda *args: store.set_enabled("research-kit", False)
    )
    request = Mock()
    monkeypatch.setattr(client, "request", request)
    outcome = runtime.handlers()[name](authorization, context)
    assert not outcome.success and outcome.error_code == "plugin_disabled_restart_required"
    request.assert_not_called()


@pytest.mark.parametrize("boundary", ["dns", "tls"])
def test_revocation_before_post_never_submits_new_remote_request(tmp_path, monkeypatch, boundary):
    from aegis_core.plugins import runtime as runtime_module

    store, client, runtime, name, context, authorization = pinned_runtime(tmp_path)
    monkeypatch.setattr(client, "verify_catalogue", lambda *args: None)
    posts, closed = [], []

    def addresses(_host):
        if boundary == "dns":
            store.set_enabled("research-kit", False)
        return ("93.184.216.34",)

    class Connection:
        def __init__(self, *_args):
            pass

        def connect(self):
            if boundary == "tls":
                store.set_enabled("research-kit", False)

        def request(self, *args, **kwargs):
            posts.append((args, kwargs))

        def close(self):
            closed.append(True)

    monkeypatch.setattr(runtime_module, "_public_addresses", addresses)
    monkeypatch.setattr(runtime_module, "_PinnedPostConnection", Connection)
    outcome = runtime.handlers()[name](authorization, context)
    assert not outcome.success and outcome.error_code == "plugin_disabled_restart_required"
    assert not posts and closed == [True]


def test_revocation_does_not_claim_to_cancel_post_already_submitted(tmp_path, monkeypatch):
    from aegis_core.plugins import runtime as runtime_module

    store, client, runtime, name, context, authorization = pinned_runtime(tmp_path)
    monkeypatch.setattr(client, "verify_catalogue", lambda *args: None)
    posts = []

    class Connection:
        def __init__(self, *_args):
            pass

        def connect(self):
            pass

        def request(self, *args, **kwargs):
            posts.append((args, kwargs))
            store.set_enabled("research-kit", False)

        def getresponse(self):
            return FakeHttpResponse()

        def close(self):
            pass

    monkeypatch.setattr(runtime_module, "_public_addresses", lambda _host: ("93.184.216.34",))
    monkeypatch.setattr(runtime_module, "_PinnedPostConnection", Connection)
    outcome = runtime.handlers()[name](authorization, context)
    assert outcome.success and len(posts) == 1
    assert not runtime.is_active("research-kit")


async def test_package_import_is_disabled_and_remove_archives_without_credentials(
    extensions,
    tmp_path,
    monkeypatch,
):
    from aegis_core.secrets import MacOSPluginSecret

    deleted = Mock(side_effect=AssertionError("credentials must be retained"))
    monkeypatch.setattr(MacOSPluginSecret, "delete", deleted)
    package = _package()
    path = tmp_path / "pack.json"
    path.write_text(package.model_dump_json(), encoding="utf-8")
    preview = await ipc(extensions, "extensions.plugin.preview", path=str(path))
    assert preview.ok and preview.payload["tools"] and preview.payload["hosts"]
    imported = await ipc(
        extensions,
        "extensions.plugin.import",
        path=str(path),
        source_sha256=preview.payload["source_sha256"],
        confirm=True,
    )
    assert imported.ok and not extensions.store.get("research-kit").enabled
    assert not extensions.runtime.is_active("research-kit")
    removed = await ipc(
        extensions,
        "extensions.plugin.remove",
        plugin_id="research-kit",
        checksum_sha256=package.checksum_sha256,
        confirm=True,
    )
    assert removed.ok and extensions.store.get("research-kit") is None
    files = list((extensions.store.directory / ".archive").glob("*.json"))
    assert len(files) == 2 and all(p.stat().st_mode & 0o777 == 0o600 for p in files)
    assert (extensions.store.directory / ".archive").stat().st_mode & 0o777 == 0o700
    deleted.assert_not_called()
    assert not extensions.runtime._client.calls


async def test_builtin_removal_is_denied_and_schema_permissions_never_changed(extensions):
    before = json.dumps(extensions.broker.schemas_for(AgentRole.PLANNER), sort_keys=True)
    record = next(s for s in extensions.registry.catalogue() if s["origin"] == "builtin")
    result = await ipc(
        extensions,
        "extensions.skill.remove",
        skill_id=record["skill_id"],
        content_sha256=record["content_sha256"],
        confirm=True,
    )
    assert not result.ok and result.error_code == "extensions_managed_skill"
    assert json.dumps(extensions.broker.schemas_for(AgentRole.PLANNER), sort_keys=True) == before


async def test_remove_learned_preserves_private_source(extensions, tmp_path):
    path = source(tmp_path)
    preview = await ipc(extensions, "extensions.skill.preview", path=str(path))
    imported = await ipc(
        extensions,
        "extensions.skill.import",
        path=str(path),
        source_sha256=preview.payload["source_sha256"],
        choices={},
        confirm=True,
    )
    record = imported.payload["skill"]
    removed = await ipc(
        extensions,
        "extensions.skill.remove",
        skill_id=record["skill_id"],
        content_sha256=record["content_sha256"],
        confirm=True,
    )
    assert removed.ok and not extensions.registry.store.load_all()
    assert len(list((extensions.registry.store.directory / ".archive").glob("*.json"))) == 1


async def test_remove_learned_rejects_file_changed_after_preview(extensions, tmp_path):
    path = source(tmp_path)
    preview = await ipc(extensions, "extensions.skill.preview", path=str(path))
    imported = await ipc(
        extensions,
        "extensions.skill.import",
        path=str(path),
        source_sha256=preview.payload["source_sha256"],
        confirm=True,
    )
    record = imported.payload["skill"]
    installed = extensions.registry.store.directory / "invoice-review.json"
    current = json.loads(installed.read_text())
    current["instructions"] = ["Usa la fecha corregida y conserva todos los importes."]
    installed.write_text(json.dumps(current))
    result = await ipc(
        extensions,
        "extensions.skill.remove",
        skill_id=record["skill_id"],
        content_sha256=record["content_sha256"],
        confirm=True,
    )
    assert not result.ok
    assert installed.exists()
    assert not (installed.parent / ".archive").exists()


async def test_malformed_and_extra_payloads_never_leak_path_or_source(extensions):
    result = await ipc(
        extensions,
        "extensions.plugin.preview",
        path="/private/nonexistent-secret",
        unexpected="sensitive-source",
    )
    assert not result.ok and result.error_code == "extensions_invalid_request"
    assert not result.payload
