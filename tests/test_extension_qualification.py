"""Integrated RPC qualification with independently generated own version bytes."""

import base64
import hashlib
import importlib.util
from pathlib import Path

import pytest
from test_service import connection as connection

from jarvis_core.plugins import PluginsStore
from jarvis_core.protocol import RpcError
from jarvis_core.skills import SkillsStore

_SPEC = importlib.util.spec_from_file_location(
    "jarvis_extension_qualification_fixtures", Path(__file__).resolve().parents[1]
    / "script/extension_qualification_fixtures.py")
fixture = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(fixture)


@pytest.fixture
def corpus(tmp_path):
    directory = tmp_path.resolve() / "own-extension-corpus"
    return directory, fixture.prepare_fixture(directory)


def selected(row):
    return {"agent_id": row["agent_id"], "plugin_id": row["plugin_id"],
            "expected_revision": row["revision"], "expected_sha256": row["sha256"]}


def skill_selected(row):
    return {"agent_id": row["agent_id"], "skill_id": row["skill_id"],
            "expected_revision": row["revision"], "expected_sha256": row["sha256"]}


async def review(client, action, *, row=None, path=None, target=None, agent="jarvis"):
    values = {"action": action, "agent_id": agent}
    if row is not None:
        values.update(selected(row))
    if path is not None:
        inspected = (await client.call("plugins.inspect", {"path": str(path)}))["package"]
        values.update(path=str(path), package_sha256=inspected["sha256"])
    if target is not None:
        values["target_version"] = target
    return (await client.call("plugins.review", values))["review"]


async def decide(client, ticket, *, allow=True):
    return await client.call("plugins.decide", {"review_id": ticket["review_id"],
                             "digest": ticket["digest"], "allow": allow})


async def change(client, action, **kwargs):
    return await decide(client, await review(client, action, **kwargs))


async def read_resource(client, row, path):
    resource = (await client.call("plugins.resource", {**selected(row), "path": path}))["resource"]
    raw = base64.b64decode(resource["content_base64"], validate=True)
    assert hashlib.sha256(raw).hexdigest() == resource["sha256"]
    return raw


async def test_plugin_complete_version_cycle_preserves_resources_and_separate_authority(
    connection, corpus,
):
    service, client, provider, _ = connection
    directory, oracle = corpus
    first = directory / oracle["archives"]["1.0.0"]["path"]
    second = directory / oracle["archives"]["2.0.0"]["path"]
    denied = await review(client, "install", path=first)
    assert await decide(client, denied, allow=False) == {"denied": True}
    with pytest.raises(RpcError):
        await decide(client, denied)
    assert (await client.call("plugins.list", {"agent_id": "jarvis"}))["plugins"] == []

    installed = await change(client, "install", path=first)
    row = installed["plugin"]
    assert row["sha256"] == oracle["archives"]["1.0.0"]["sha256"] and not row["enabled"]
    assert not installed["receipt"]["connected_mcp"]
    assert not installed["receipt"]["executed_package_code"]
    assert service.plugins.context("jarvis")["text"] == ""
    assert await service.mcp.list_status() == ()
    row = (await change(client, "enable", row=row))["plugin"]
    assert "SKILL JARVIS V1 8316" in service.plugins.context("jarvis")["text"]
    assert await read_resource(client, row, fixture.RESOURCE_PATH) == (
        b"RECURSO PLUGIN JARVIS V1 6142")

    obsolete = row
    row = (await change(client, "update", row=row, path=second))["plugin"]
    assert row["version"] == "2.0.0" and not row["enabled"]
    assert service.plugins.context("jarvis")["text"] == ""
    with pytest.raises(RpcError) as stale:
        await read_resource(client, obsolete, fixture.RESOURCE_PATH)
    assert stale.value.code == -32011
    assert await read_resource(client, row, fixture.RESOURCE_PATH) == (
        b"RECURSO PLUGIN JARVIS V2 9257")
    row = (await change(client, "enable", row=row))["plugin"]
    assert "SKILL JARVIS V2 4729" in service.plugins.context("jarvis")["text"]
    restarted = PluginsStore(service.plugins.document.directory)
    assert restarted.context("jarvis") == service.plugins.context("jarvis")

    row = (await change(client, "rollback", row=row, target="1.0.0"))["plugin"]
    assert row["version"] == "1.0.0" and not row["enabled"]
    assert await read_resource(client, row, fixture.RESOURCE_PATH) == (
        b"RECURSO PLUGIN JARVIS V1 6142")
    versions = (await client.call("plugins.versions", {
        "agent_id": "jarvis", "plugin_id": row["plugin_id"]}))["versions"]
    assert {(v["version"], v["current"]) for v in versions} == {
        ("1.0.0", True), ("2.0.0", False)}
    row = (await change(client, "retire_history", row=row, target="2.0.0"))["plugin"]
    row = (await change(client, "enable", row=row))["plugin"]
    row = (await change(client, "disable", row=row))["plugin"]
    assert service.plugins.context("jarvis")["text"] == ""
    removed = await change(client, "remove", row=row)
    assert removed["removed"] and removed["plugin"] is None
    assert (await client.call("plugins.list", {"agent_id": "jarvis"}))["plugins"] == []
    assert await service.mcp.list_status() == () and provider.calls == []
    for relative, digest in oracle["files_sha256"].items():
        assert hashlib.sha256((directory / relative).read_bytes()).hexdigest() == digest


async def test_skill_library_edit_rollback_and_retirement_keep_exact_snapshot_without_turn(
    connection, corpus,
):
    service, client, provider, _ = connection
    directory, oracle = corpus
    source = directory / oracle["skill_directory"]
    inspected = (await client.call("skills.inspect_library", {"path": str(source)}))["library"]
    row = (await client.call("skills.import_library", {
        "agent_id": "jarvis", "path": str(source),
        "expected_sha256": inspected["skill_sha256"],
        "expected_library_sha256": inspected["library_sha256"]}))["skill"]
    assert not row["enabled"] and service.skills.context("jarvis").text == ""
    row = (await client.call("skills.enable", {**skill_selected(row), "enabled": True}))["skill"]
    original = row
    # The resource source is intentionally changed after import. Every later
    # resource result must still use the reviewed v1 bytes, including rollback.
    (source / fixture.SKILL_RESOURCE_PATH).write_bytes(b"NO IMPORTADO 7731")
    updated_text = (directory / oracle["skill_update_text"]).read_text()
    row = (await client.call("skills.update", {
        **skill_selected(row), "text": updated_text}))["skill"]
    metadata = (await client.call("skills.metadata", skill_selected(row)))["metadata"]
    assert metadata["version"] == "2.0.0"
    invocation = (await client.call("skills.invoke", {
        **skill_selected(row), "arguments": {"tema": "DATOS $(NO EJECUTAR)"}}))["invocation"]
    assert "SKILL JARVIS V2 4729" in invocation["context"]
    assert "DATOS $(NO EJECUTAR)" in invocation["context"] and not invocation["executed_code"]
    with pytest.raises(RpcError) as stale:
        await client.call("skills.invoke", {**skill_selected(original), "arguments": {"tema": "x"}})
    assert stale.value.code == -32011
    resource = (await client.call("skills.resource", {
        **skill_selected(row), "path": fixture.SKILL_RESOURCE_PATH}))["resource"]
    assert base64.b64decode(resource["content_base64"]) == b"RECURSO SKILL JARVIS 1804"
    row = (await client.call("skills.rollback", {
        **skill_selected(row), "target_revision": original["revision"]}))["skill"]
    assert not row["enabled"] and row["sha256"] == original["sha256"]
    reopened = SkillsStore(service.skills.document.directory)
    assert reopened.metadata("jarvis", row["skill_id"])["version"] == "1.0.0"
    resource = (await client.call("skills.resource", {
        **skill_selected(row), "path": fixture.SKILL_RESOURCE_PATH}))["resource"]
    assert base64.b64decode(resource["content_base64"]) == b"RECURSO SKILL JARVIS 1804"
    versions = (await client.call("skills.versions", skill_selected(row)))["versions"]
    previous_v2 = next(v for v in versions if v["version"] == "2.0.0" and not v["current"])
    row = (await client.call("skills.retire_version", {
        **skill_selected(row), "target_revision": previous_v2["revision"]}))["skill"]
    remaining = (await client.call("skills.versions", skill_selected(row)))["versions"]
    assert not any(v["version"] == "2.0.0" for v in remaining)
    assert await client.call("skills.remove", skill_selected(row)) == {"removed": True}
    assert (await client.call("skills.list", {"agent_id": "jarvis"}))["skills"] == []
    assert provider.calls == [] and await service.mcp.list_status() == ()


@pytest.mark.parametrize("enabled", [False, True])
async def test_agent_cannot_be_deleted_until_its_plugin_is_explicitly_removed(
    connection, corpus, enabled,
):
    service, client, provider, _ = connection
    directory, oracle = corpus
    agent = (await client.call("agents.create", {"name": "Agente sintético con plugin"}))["agent"]
    archive = directory / oracle["archives"]["1.0.0"]["path"]
    row = (await change(client, "install", path=archive, agent=agent["agent_id"]))["plugin"]
    if enabled:
        row = (await change(client, "enable", row=row))["plugin"]
    before = service.agent_profiles.document.path.read_bytes()
    with pytest.raises(RpcError) as caught:
        await client.call("agents.delete", {"agent_id": agent["agent_id"],
                                            "expected_revision": agent["revision"]})
    assert caught.value.code == -32032
    assert service.agent_profiles.document.path.read_bytes() == before
    assert service.plugins.get(agent["agent_id"], row["plugin_id"]).sha256 == row["sha256"]
    assert PluginsStore(service.plugins.document.directory).list(agent["agent_id"])
    await change(client, "remove", row=row)
    assert await client.call("agents.delete", {"agent_id": agent["agent_id"],
                                               "expected_revision": agent["revision"]}) == {
        "deleted": True}
    agents = (await client.call("agents.list", {}))["agents"]
    assert agent["agent_id"] not in {item["agent_id"] for item in agents}
    assert not service.plugins.list(agent["agent_id"]) and provider.calls == []
