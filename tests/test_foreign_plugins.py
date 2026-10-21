import io
import json
import tarfile

import pytest

from aegis_core.contracts import ToolAuthorization
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
    authorization = _authorization("jarvis_plugin_install", {"plugin": "Design Review"})
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
