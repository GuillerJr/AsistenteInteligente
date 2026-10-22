import io
import json
import tarfile

import pytest

from aegis_core.contracts import AgentRole, ToolAuthorization, ToolCall
from aegis_core.job_tool_presenter import JobToolPresenter
from aegis_core.plugins.foreign import (
    ForeignPluginCatalogue,
    ForeignPluginError,
    ForeignPluginManager,
    extract_marketplace,
    parse_marketplace_spec,
    read_plugin,
)
from aegis_core.plugins.foreign_service import (
    ForeignPluginIpcService,
    ForeignPluginToolHandlers,
    PluginInstallArguments,
    foreign_plugin_confirmation_summary,
)
from aegis_core.skills.policy import SkillPolicyStore
from aegis_core.skills.registry import SkillRegistry, SkillStore
from aegis_core.tools.defaults import build_default_tool_broker

LONG = "Revisa una propuesta de diseño con criterios claros " * 12


def _write(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _codex_plugin(root):
    _write(root / ".codex-plugin/plugin.json", json.dumps({
        "name": "design-review", "version": "0.2.1", "description": "Design reviews",
        "author": {"name": "Acme"}, "skills": "./skills/", "apps": "./.app.json",
        "keywords": ["design", "review"],
        "interface": {"displayName": "Design Review", "shortDescription": "Revisa diseños"},
    }))
    _write(root / ".app.json", "{}")
    # Long descriptions and extra keys, as Codex and Claude skills ship them.
    _write(root / "skills/critique/SKILL.md",
           f"---\nname: critique\ndescription: >\n  {LONG}\nmetadata:\n  tags: [a, b]\n---\n"
           "# Critique\n\nRevisa jerarquía, contraste y espaciado; da tres mejoras.\n")
    _write(root / "skills/runner/SKILL.md",
           "---\nname: runner\ndescription: Corre el linter del proyecto.\n---\n"
           "```bash\nnpm run lint\n```\n")


def _claude_plugin(root):
    _write(root / ".claude-plugin/plugin.json", json.dumps({
        "name": "release-notes", "version": "1.0.0", "description": "Notas de versión",
    }))
    _write(root / "commands/changelog.md",
           "---\ndescription: Redacta el changelog de la última versión.\n---\n"
           "Resume los cambios por categoría: nuevas funciones, arreglos y avisos.\n")
    _write(root / ".mcp.json", json.dumps({"mcpServers": {"git": {"command": "npx"}}}))
    (root / "hooks").mkdir()


def _registry(tmp_path):
    tmp_path.chmod(0o700)
    return SkillRegistry(build_default_tool_broker(), SkillStore(tmp_path / "skills"),
                         policy=SkillPolicyStore(tmp_path / "policy.json"), builtins=())


def _home(tmp_path):
    home = tmp_path / "home"
    old = home / ".codex/plugins/cache/openai-curated/design-review/0.1.0"
    _codex_plugin(old)
    new = home / ".codex/plugins/cache/openai-curated/design-review/0.2.1"
    _codex_plugin(new)
    import os
    os.utime(old, (1, 1))
    market = home / ".claude/plugins/marketplaces/team"
    _write(market / ".claude-plugin/marketplace.json", json.dumps({
        "name": "team-tools", "plugins": [
            {"name": "release-notes", "source": "./plugins/release-notes"},
            {"name": "outside", "source": "../escape"},
        ]}))
    _claude_plugin(market / "plugins/release-notes")
    return home


def test_codex_plugin_brings_declarative_skills_and_names_what_stays_out(tmp_path):
    root = tmp_path / "plugin"
    _codex_plugin(root)
    plugin = read_plugin(root, source="Codex")
    assert plugin.plugin_id == "design-review" and plugin.format == "codex"
    assert plugin.name == "Design Review" and plugin.publisher == "Acme"
    assert [skill.name for skill in plugin.skills] == ["critique"]
    assert len(plugin.skills[0].description) <= 300
    assert any("runner" in item and "scripts" in item for item in plugin.left_out)
    assert "conexiones de apps de ChatGPT" in plugin.left_out


def test_claude_plugin_commands_become_skills_and_servers_hooks_stay_out(tmp_path):
    root = tmp_path / "plugin"
    _claude_plugin(root)
    plugin = read_plugin(root, source="Claude")
    assert plugin.format == "claude"
    assert [skill.name for skill in plugin.skills] == ["/changelog"]
    assert plugin.skills[0].trigger == "changelog"
    assert any("servidor MCP" in item for item in plugin.left_out)
    assert "hooks (ejecutan comandos)" in plugin.left_out


def test_catalogue_reads_codex_cache_and_claude_marketplaces_inside_their_roots(tmp_path):
    catalogue = ForeignPluginCatalogue(tmp_path / "jarvis", home=_home(tmp_path))
    plugins = {plugin.plugin_id: plugin for plugin in catalogue.plugins()}
    assert set(plugins) == {"design-review", "release-notes"}
    assert plugins["design-review"].version == "0.2.1"  # The newest cached version.
    assert plugins["release-notes"].source == "team-tools"
    assert [p.plugin_id for p in catalogue.search("diseños")] == ["design-review"]
    assert [p.plugin_id for p in catalogue.resolve("design review")] == ["design-review"]


def test_install_learns_enabled_skills_and_remove_forgets_them(tmp_path):
    registry = _registry(tmp_path)
    catalogue = ForeignPluginCatalogue(tmp_path / "jarvis", home=_home(tmp_path))
    manager = ForeignPluginManager(catalogue, registry)
    done = manager.install("release-notes")
    assert done["skills"] == ["/changelog"]
    record = next(item for item in registry.catalogue() if item["skill_id"] == "changelog")
    assert record["enabled"] is True
    selected = registry.select("redacta el changelog de esta versión")
    assert selected is not None and selected.manifest.skill_id == "changelog"
    status = manager.status()
    assert next(p for p in status["plugins"] if p["plugin_id"] == "release-notes")["installed"]
    manager.remove("release-notes")
    assert all(item["skill_id"] != "changelog" for item in registry.catalogue())
    with pytest.raises(ForeignPluginError):
        manager.remove("release-notes")


def test_install_never_overwrites_a_skill_the_owner_taught(tmp_path):
    registry = _registry(tmp_path)
    catalogue = ForeignPluginCatalogue(tmp_path / "jarvis", home=_home(tmp_path))
    manager = ForeignPluginManager(catalogue, registry)
    plugin = catalogue.get("design-review")
    from aegis_core.skills.importer import finalize_skill_import
    registry.learn(finalize_skill_import(plugin.skills[0].preview, {
        "role": "planner", "trigger_phrases": ("mi crítica",)}))
    manager.install("design-review")
    ids = {item["skill_id"] for item in registry.catalogue()}
    assert {"critique", "design-review-critique"} <= ids


def _authorization(name, arguments):
    return ToolAuthorization.model_construct(call_id="c", tool_name=name,
                                             normalized_arguments=arguments)


@pytest.mark.asyncio
async def test_agent_install_shows_contents_in_the_approval_and_reports_the_result(tmp_path):
    registry = _registry(tmp_path)
    manager = ForeignPluginManager(
        ForeignPluginCatalogue(tmp_path / "jarvis", home=_home(tmp_path)), registry)
    plugin = manager.catalogue.get("design-review")
    authorization = _authorization("jarvis_plugin_install", {
        "plugin": plugin.plugin_id, "content_sha256": plugin.content_sha256})
    summary = foreign_plugin_confirmation_summary(authorization, manager)
    assert "«Design Review» de Codex (Acme)" in summary and "critique" in summary
    assert "Queda fuera" in summary
    presenter = JobToolPresenter(None, summaries={
        "jarvis_plugin_install": lambda a: foreign_plugin_confirmation_summary(a, manager)})
    assert presenter.supports_confirmation("jarvis_plugin_install")
    assert presenter.confirmation_summary(authorization) == summary
    handlers = ForeignPluginToolHandlers(manager).handlers()
    result = await handlers["jarvis_plugin_install"](authorization, None)
    assert "Instalé «Design Review»" in presenter.format_result(result)
    search = await handlers["jarvis_plugin_search"](
        _authorization("jarvis_plugin_search", {"query": "changelog"}), None)
    assert "Release Notes" in json.loads(search.output)["message"] or "release-notes" in (
        search.output)
    assert json.loads(search.output)["plugins"][0]["content_sha256"] == (
        manager.catalogue.get("release-notes").content_sha256)


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["new_skill", "same_name_new_body", "removed_skill"])
@pytest.mark.parametrize("expire_cache", [False, True])
async def test_install_rejects_content_changed_after_approval(tmp_path, change, expire_cache):
    registry = _registry(tmp_path)
    now = [0.0]
    catalogue = ForeignPluginCatalogue(tmp_path / "jarvis", home=_home(tmp_path),
                                       clock=lambda: now[0])
    manager = ForeignPluginManager(catalogue, registry)
    plugin = catalogue.get("design-review")
    authorization = _authorization("jarvis_plugin_install", {
        "plugin": plugin.plugin_id, "content_sha256": plugin.content_sha256})
    assert "critique" in foreign_plugin_confirmation_summary(authorization, manager)
    path = plugin.root / "skills/critique/SKILL.md"
    if change == "removed_skill":
        path.unlink()
    elif change == "same_name_new_body":
        path.write_text(path.read_text() + "\nInstrucción agregada tras la revisión.\n")
    else:
        _write(plugin.root / "skills/new-advice/SKILL.md",
               "---\nname: new-advice\ndescription: Habilidad añadida tras aprobar.\n---\n"
               "Recomienda tres mejoras para el texto.\n")
    if expire_cache:
        now[0] = 11.0
    result = await ForeignPluginToolHandlers(manager).handlers()["jarvis_plugin_install"](
        authorization, None)
    assert json.loads(result.output)["installed"] is False
    assert "cambió" in json.loads(result.output)["message"]
    assert registry.catalogue() == []
    assert catalogue.installed() == {}


def test_plugin_content_fingerprint_is_validated_and_part_of_signed_call_digest():
    with pytest.raises(ValueError):
        PluginInstallArguments.model_validate({"plugin": "design-review", "content_sha256": "x"})
    call = ToolCall(call_id="install", tool_name="jarvis_plugin_install",
                    requested_by=AgentRole.PLANNER,
                    arguments={"plugin": "design-review", "content_sha256": "a" * 64})
    changed = call.model_copy(update={
        "arguments": {**call.arguments, "content_sha256": "b" * 64}})
    assert call.digest() != changed.digest()


@pytest.mark.asyncio
async def test_install_without_prepared_content_fails_closed(tmp_path):
    registry = _registry(tmp_path)
    manager = ForeignPluginManager(
        ForeignPluginCatalogue(tmp_path / "jarvis", home=_home(tmp_path)), registry)
    result = await ForeignPluginToolHandlers(manager).handlers()["jarvis_plugin_install"](
        _authorization("jarvis_plugin_install", {"plugin": "design-review"}), None)
    assert json.loads(result.output)["installed"] is False
    assert result.success is False
    assert result.metadata["verified"] is False
    assert registry.catalogue() == []


@pytest.mark.asyncio
@pytest.mark.parametrize("changed", [False, True])
async def test_job_approval_binds_daemon_prepared_plugin_content(tmp_path, changed):
    import asyncio

    from aegis_core.contracts import AgentResult, UserRequest
    from aegis_core.job_contracts import JobStatus
    from aegis_core.jobs import SwarmJobManager
    from aegis_core.orchestration.graph import build_nvidia_swarm_graph as build_swarm_graph
    from aegis_core.plugins.foreign_service import foreign_plugin_tool_definitions
    from aegis_core.tools.broker import PolicyContext
    from aegis_core.tools.confirmations import OneTimeConfirmationStore
    from aegis_core.tools.execution import ReadOnlyToolExecutor

    registry = _registry(tmp_path)
    manager = ForeignPluginManager(
        ForeignPluginCatalogue(tmp_path / "jarvis", home=_home(tmp_path)), registry)
    handlers = ForeignPluginToolHandlers(manager)
    broker = build_default_tool_broker(foreign_plugin_tool_definitions())
    confirmations = OneTimeConfirmationStore()
    context = PolicyContext(workspace_root=tmp_path, confirmation_store=confirmations)
    executor = ReadOnlyToolExecutor(extra_async_handlers=handlers.handlers())
    model_call = ToolCall(call_id="install", tool_name="jarvis_plugin_install",
                         requested_by=AgentRole.PLANNER,
                         arguments={"plugin": "Design Review", "content_sha256": "0" * 64})

    class Provider:
        async def complete_nvidia_only(self, **kwargs):
            return AgentResult(role=kwargs["role"], model_id="synthetic",
                               content="Revisa el complemento.", tool_calls=(model_call,))

    graph = build_swarm_graph(
        Provider(), inference_nvidia_only=True, tool_broker=broker, policy_context=context,
        tool_executor=executor, tool_call_preparer=handlers.prepare_call,
        dynamic_tool_names=lambda _: frozenset({"jarvis_plugin_install"}))
    jobs = SwarmJobManager(
        graph, tool_broker=broker, policy_context=context, tool_executor=executor,
        confirmation_store=confirmations, confirmation_summaries={
            "jarvis_plugin_install": lambda a: foreign_plugin_confirmation_summary(a, manager)})
    try:
        submitted = await jobs.submit(UserRequest(text="Instala el complemento Design Review"))
        for _ in range(400):
            snapshot = await jobs.status(submitted.job_id)
            if snapshot.status not in {JobStatus.QUEUED, JobStatus.RUNNING}:
                break
            await asyncio.sleep(0.005)
        assert snapshot.status is JobStatus.AWAITING_CONFIRMATION
        plugin = manager.catalogue.get("design-review")
        expected = model_call.model_copy(update={"arguments": {
            "plugin": plugin.plugin_id, "content_sha256": plugin.content_sha256}})
        assert snapshot.confirmation.call_digest == expected.digest() != model_call.digest()
        if changed:
            path = plugin.root / "skills/critique/SKILL.md"
            path.write_text(path.read_text() + "\nNueva instrucción no aprobada.\n")
        await jobs.approve(submitted.job_id, snapshot.confirmation.call_digest)
        for _ in range(400):
            result = await jobs.status(submitted.job_id)
            if result.status not in {JobStatus.QUEUED, JobStatus.RUNNING}:
                break
            await asyncio.sleep(0.005)
        assert result.status is (JobStatus.FAILED if changed else JobStatus.COMPLETED)
        assert bool(registry.catalogue()) is not changed
        if changed:
            assert result.result is None
            assert result.error_code == "approved_tool_execution_failed"
        else:
            assert "Instalé" in result.result
    finally:
        await jobs.close()


def _tarball(files):
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as bundle:
        for name, content in files.items():
            data = content.encode("utf-8")
            info = tarfile.TarInfo(name)
            info.size = len(data)
            bundle.addfile(info, io.BytesIO(data))
        link = tarfile.TarInfo("repo-abc/evil")
        link.type, link.linkname = tarfile.SYMTYPE, "/etc/passwd"
        bundle.addfile(link)
    return buffer.getvalue()


def test_marketplace_spec_and_extraction_stay_inside_the_destination(tmp_path):
    assert parse_marketplace_spec("https://github.com/acme/tools.git") == ("acme", "tools")
    assert parse_marketplace_spec("acme/tools") == ("acme", "tools")
    with pytest.raises(ForeignPluginError):
        parse_marketplace_spec("https://evil.example/acme/tools")
    target = tmp_path / "market"
    extract_marketplace(_tarball({"repo-abc/a/b.txt": "hola"}), target)
    assert (target / "a/b.txt").read_text() == "hola"
    assert not (target / "evil").exists()
    with pytest.raises(ForeignPluginError):
        extract_marketplace(_tarball({"repo-abc/../x.txt": "no"}), tmp_path / "bad")
    assert not (tmp_path / "bad").exists()


@pytest.mark.asyncio
async def test_owner_adds_a_github_marketplace_and_installs_from_it(tmp_path):
    registry = _registry(tmp_path)
    plugin = {
        "repo-abc/.claude-plugin/marketplace.json": json.dumps({
            "name": "acme", "plugins": [{"name": "standup", "source": "./standup"}]}),
        "repo-abc/standup/.claude-plugin/plugin.json": json.dumps({"name": "standup"}),
        "repo-abc/standup/skills/standup/SKILL.md": (
            "---\nname: standup\ndescription: Prepara mi resumen diario de equipo.\n---\n"
            "Lista lo de ayer, lo de hoy y los bloqueos.\n"),
    }
    urls = []

    async def download(url):
        urls.append(url)
        return _tarball(plugin)

    manager = ForeignPluginManager(
        ForeignPluginCatalogue(tmp_path / "jarvis", home=tmp_path / "empty"), registry,
        download=download)
    service = ForeignPluginIpcService(manager)

    class Request:
        def __init__(self, method, payload):
            self.method, self.payload = method, payload

    added = await service.handle(Request("plugins.foreign.marketplace.add",
                                         {"repository": "acme/team-plugins"}))
    assert urls == ["https://codeload.github.com/acme/team-plugins/tar.gz/HEAD"]
    assert added.ok and [m["slug"] for m in added.payload["marketplaces"]] == [
        "acme-team-plugins"]
    installed = await service.handle(Request("plugins.foreign.install",
                                             {"plugin_id": "standup"}))
    assert installed.ok and next(
        p for p in installed.payload["plugins"] if p["plugin_id"] == "standup")["installed"]
    missing = await service.handle(Request("plugins.foreign.install", {"plugin_id": "nope"}))
    assert missing.ok and missing.payload["error"] == "Ese complemento ya no está disponible."
    removed = await service.handle(Request("plugins.foreign.marketplace.remove",
                                           {"slug": "acme-team-plugins"}))
    assert removed.ok and removed.payload["marketplaces"] == []


@pytest.mark.asyncio
async def test_a_slow_download_ends_with_a_clear_message(tmp_path, monkeypatch):
    import asyncio

    import aegis_core.plugins.foreign as foreign

    monkeypatch.setattr(foreign, "DOWNLOAD_SECONDS", 0.05)

    async def slow(url):
        await asyncio.sleep(1)

    manager = ForeignPluginManager(
        ForeignPluginCatalogue(tmp_path / "jarvis", home=tmp_path / "empty"),
        _registry(tmp_path), download=slow)
    with pytest.raises(ForeignPluginError, match="tardó demasiado"):
        await manager.add_marketplace("acme/tools")


@pytest.mark.parametrize("payload", [
    b"{broken", b"[]", b"{}", b'{"plugins": []}',
    b'{"plugins": {"design-review": null}}',
    b'{"plugins": {"design-review": {"skill_ids": "critique"}}}',
    b'{"plugins": {"design-review": {"skill_ids": ["../outside"]}}}',
])
def test_invalid_installed_state_is_preserved_before_any_skill_change(tmp_path, payload):
    registry = _registry(tmp_path)
    catalogue = ForeignPluginCatalogue(tmp_path / "jarvis", home=_home(tmp_path))
    manager = ForeignPluginManager(catalogue, registry)
    manager.install("design-review")
    previous_skills = registry.catalogue()
    path = catalogue.directory / "installed.json"
    path.write_bytes(payload)
    for operation in (manager.status, lambda: manager.install("design-review"),
                      lambda: manager.remove("design-review")):
        with pytest.raises(ForeignPluginError, match="registro"):
            operation()
        assert path.read_bytes() == payload
        assert registry.catalogue() == previous_skills


@pytest.mark.asyncio
@pytest.mark.parametrize("payload", [
    b"{broken", b"{}", b'{"marketplaces": {}}',
    b'{"marketplaces": [null]}', b'{"marketplaces": [{"slug": "../outside"}]}',
])
async def test_invalid_marketplace_state_prevents_download_and_delete(tmp_path, payload):
    catalogue = ForeignPluginCatalogue(tmp_path / "jarvis", home=tmp_path / "empty")
    path = catalogue.directory / "marketplaces.json"
    _write(path, payload.decode())
    path.chmod(0o600)
    calls = []

    async def download(url):
        calls.append(url)

    manager = ForeignPluginManager(catalogue, _registry(tmp_path), download=download)
    with pytest.raises(ForeignPluginError, match="registro"):
        await manager.add_marketplace("acme/tools")
    with pytest.raises(ForeignPluginError, match="registro"):
        manager.remove_marketplace("acme-tools")
    assert calls == []
    assert path.read_bytes() == payload


@pytest.mark.asyncio
async def test_invalid_plugin_state_returns_controlled_ipc_error(tmp_path):
    from types import SimpleNamespace

    catalogue = ForeignPluginCatalogue(tmp_path / "jarvis", home=tmp_path / "empty")
    path = catalogue.directory / "installed.json"
    _write(path, "{broken")
    path.chmod(0o600)
    service = ForeignPluginIpcService(ForeignPluginManager(catalogue, _registry(tmp_path)))
    result = await service.handle(SimpleNamespace(method="plugins.foreign.status", payload={}))
    assert result.ok is False
    assert result.error_code == "plugins_state_unavailable"
    assert path.read_text() == "{broken"


@pytest.mark.parametrize("kind", ["symlink", "directory", "public", "oversized"])
def test_unsafe_plugin_state_is_never_replaced(tmp_path, kind):
    from aegis_core.plugins.foreign import MAX_PLUGIN_STATE_BYTES

    catalogue = ForeignPluginCatalogue(tmp_path / "jarvis", home=tmp_path / "empty")
    path = catalogue.directory / "installed.json"
    path.parent.mkdir()
    other = tmp_path / "other.json"
    other.write_text('{"plugins": {}}')
    other.chmod(0o600)
    if kind == "symlink":
        path.symlink_to(other)
    elif kind == "directory":
        path.mkdir()
    else:
        path.write_text(" " * (MAX_PLUGIN_STATE_BYTES + 1) if kind == "oversized"
                        else '{"plugins": {}}')
        path.chmod(0o644 if kind == "public" else 0o600)
    for operation in (catalogue.installed, lambda: catalogue.save_installed({})):
        with pytest.raises(ForeignPluginError):
            operation()
    assert other.read_text() == '{"plugins": {}}'
    assert path.is_symlink() if kind == "symlink" else path.exists()


def _marketplace_archive(name, version="1.0.0"):
    return _tarball({
        "repo-abc/.claude-plugin/plugin.json": json.dumps({"name": name, "version": version}),
        f"repo-abc/skills/{name}/SKILL.md": (
            f"---\nname: {name}\ndescription: Revisa el texto de ejemplo con cuidado.\n---\n"
            "Resume los cambios y comprueba la redacción.\n"),
    })


@pytest.mark.asyncio
async def test_overlapping_marketplace_downloads_merge_current_registry(tmp_path):
    import asyncio

    started = asyncio.Event()
    resume = asyncio.Event()

    async def download(url):
        if "/first/" in url:
            started.set()
            await resume.wait()
            return _marketplace_archive("first")
        return _marketplace_archive("second")

    catalogue = ForeignPluginCatalogue(tmp_path / "jarvis", home=tmp_path / "empty")
    manager = ForeignPluginManager(catalogue, _registry(tmp_path), download=download)
    first = asyncio.create_task(manager.add_marketplace("acme/first"))
    await asyncio.wait_for(started.wait(), timeout=1)
    await manager.add_marketplace("acme/second")
    resume.set()
    await first
    assert {item["slug"] for item in catalogue.marketplaces()} == {"acme-first", "acme-second"}
    assert {plugin.plugin_id for plugin in catalogue.plugins()} == {"first", "second"}


@pytest.mark.asyncio
async def test_marketplace_revalidates_corruption_during_download(tmp_path):
    catalogue = ForeignPluginCatalogue(tmp_path / "jarvis", home=tmp_path / "empty")
    path = catalogue.directory / "marketplaces.json"

    async def download(url):
        _write(path, "{broken")
        path.chmod(0o600)
        return _marketplace_archive("first")

    manager = ForeignPluginManager(catalogue, _registry(tmp_path), download=download)
    with pytest.raises(ForeignPluginError, match="registro"):
        await manager.add_marketplace("acme/first")
    assert path.read_text() == "{broken"
    assert not catalogue.marketplaces_directory.exists()


@pytest.mark.asyncio
@pytest.mark.parametrize("after_replace", [False, True])
async def test_marketplace_update_failure_preserves_tree_matching_committed_state(
    tmp_path, monkeypatch, after_replace,
):
    import aegis_core.plugins.foreign as foreign

    version = ["1.0.0"]

    async def download(url):
        return _marketplace_archive("first", version[0])

    catalogue = ForeignPluginCatalogue(tmp_path / "jarvis", home=tmp_path / "empty")
    manager = ForeignPluginManager(catalogue, _registry(tmp_path), download=download)
    await manager.add_marketplace("acme/first")
    previous = (catalogue.directory / "marketplaces.json").read_bytes()
    real_write = foreign._write_json

    def fail(path, value):
        if after_replace:
            real_write(path, value)
        raise OSError("synthetic failure")

    monkeypatch.setattr(foreign, "_write_json", fail)
    version[0] = "2.0.0"
    with pytest.raises(OSError, match="synthetic"):
        await manager.add_marketplace("acme/first")
    assert catalogue.get("first").version == ("2.0.0" if after_replace else "1.0.0")
    if not after_replace:
        assert (catalogue.directory / "marketplaces.json").read_bytes() == previous
    assert not list(catalogue.marketplaces_directory.glob(".*"))


@pytest.mark.asyncio
@pytest.mark.parametrize("after_replace", [False, True])
async def test_marketplace_remove_failure_does_not_publish_uncommitted_removal(
    tmp_path, monkeypatch, after_replace,
):
    import aegis_core.plugins.foreign as foreign

    async def download(url):
        return _marketplace_archive("first")

    catalogue = ForeignPluginCatalogue(tmp_path / "jarvis", home=tmp_path / "empty")
    manager = ForeignPluginManager(catalogue, _registry(tmp_path), download=download)
    await manager.add_marketplace("acme/first")
    assert catalogue.get("first") is not None
    previous = (catalogue.directory / "marketplaces.json").read_bytes()
    real_write = foreign._write_json

    def fail(path, value):
        if after_replace:
            real_write(path, value)
        raise OSError("synthetic failure")

    monkeypatch.setattr(foreign, "_write_json", fail)
    with pytest.raises(OSError, match="synthetic"):
        manager.remove_marketplace("acme-first")
    assert (catalogue.get("first") is None) is after_replace
    if not after_replace:
        assert (catalogue.directory / "marketplaces.json").read_bytes() == previous
    assert (catalogue.marketplaces_directory / "acme-first").exists()


def test_install_capacity_is_checked_before_learning_and_allows_existing_update(
    tmp_path, monkeypatch,
):
    from dataclasses import replace

    import aegis_core.plugins.foreign as foreign

    registry = _registry(tmp_path)
    catalogue = ForeignPluginCatalogue(tmp_path / "jarvis", home=_home(tmp_path))
    manager = ForeignPluginManager(catalogue, registry)
    manager.install("design-review")
    manager.install("release-notes")
    monkeypatch.setattr(foreign, "MAX_CATALOGUE_PLUGINS", 2)
    get = catalogue.get
    third = replace(get("design-review"), plugin_id="third-plugin")
    monkeypatch.setattr(catalogue, "get", lambda key: third if key == "third-plugin" else get(key))
    before = registry.catalogue()
    previous = (catalogue.directory / "installed.json").read_bytes()
    with pytest.raises(ForeignPluginError, match="máximo"):
        manager.install("third-plugin")
    assert registry.catalogue() == before
    assert (catalogue.directory / "installed.json").read_bytes() == previous
    manager.install("design-review")
    assert len(catalogue.installed()) == 2


def test_invalid_proposed_registry_is_rejected_before_replacing_existing_state(tmp_path):
    catalogue = ForeignPluginCatalogue(tmp_path / "jarvis", home=_home(tmp_path))
    manager = ForeignPluginManager(catalogue, _registry(tmp_path))
    manager.install("design-review")
    path = catalogue.directory / "installed.json"
    previous = path.read_bytes()
    with pytest.raises(ForeignPluginError):
        catalogue.save_installed({"design-review": {"skill_ids": ["critique"]}})
    assert path.read_bytes() == previous
    catalogue.save_marketplaces([])
    path = catalogue.directory / "marketplaces.json"
    previous = path.read_bytes()
    with pytest.raises(ForeignPluginError):
        catalogue.save_marketplaces([{"slug": "acme-first"}])
    assert path.read_bytes() == previous
