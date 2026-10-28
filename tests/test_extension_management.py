"""Owner-RPC boundary with real profile admission and tiny synthetic packages."""

import base64

import pytest
from test_plugins import package

from jarvis_core.client_identity import ClientIdentityError
from jarvis_core.extension_management import EXTENSION_READ_RPCS, ExtensionManagementAPI
from jarvis_core.host_profile import ProfileLease, ServiceProfileGate
from jarvis_core.plugins import PluginsStore
from jarvis_core.private_json import PrivateJsonError
from jarvis_core.profiles import ProfilesStore
from jarvis_core.protocol import RpcError, authenticated_client
from jarvis_core.skills import SkillsStore
from jarvis_core.workspace import WorkspaceError

CLIENT = "a" * 64
OTHER = "b" * 64


class Authority:
    def __init__(self):
        self.revoked = set()

    def lookup(self, owner, *, scope):
        if owner not in {CLIENT, OTHER} or owner in self.revoked or scope != "owner":
            raise ClientIdentityError("client_not_admitted")
        return {"scope": "owner", "client_id": owner}


class Host(ExtensionManagementAPI):
    def __init__(self, directory):
        self.directory = directory
        self.profile_lease = ProfileLease.acquire(directory)
        self.profile_gate = ServiceProfileGate(self.profile_lease, blockers=lambda: ())
        self.agent_profiles = ProfilesStore(directory / "profiles")
        self.skills = SkillsStore(directory / "profiles")
        self.plugins = PluginsStore(directory / "profiles")
        self.client_authority = Authority()
        self._workspace_id = "f" * 64
        self.busy, self.workspace_current = False, True

    def _assert_workspace_current(self):
        if not self.workspace_current:
            raise WorkspaceError("workspace_identity_changed")

    def _management_idle(self):
        if self.busy or self.plugins.pending_cleanup or self.skills.pending_cleanup:
            raise RpcError(-32011, "Operación sintética pendiente.")

    async def call(self, method, value):
        # Same one-admission contract as service.safe, without constructing a
        # provider, reading credentials, running a child or touching old profiles.
        with self.profile_gate.admit(method, mutating=method not in EXTENSION_READ_RPCS):
            return await self.extension_handlers()[method](value)


@pytest.fixture
def host(tmp_path):
    directory = tmp_path.resolve() / "synthetic-profile"
    directory.mkdir(mode=0o700)
    value = Host(directory)
    token = authenticated_client.set(CLIENT)
    try:
        yield value
    finally:
        authenticated_client.reset(token)
        value.profile_lease.close()


async def review(host, path, **options):
    inspected = await host.call("plugins.inspect", {"path": str(path)})
    value = {"action": "install", "agent_id": "jarvis", "path": str(path),
             "package_sha256": inspected["package"]["sha256"], **options}
    return (await host.call("plugins.review", value))["review"]


async def decide(host, ticket, allow=True):
    return await host.call("plugins.decide", {"review_id": ticket["review_id"],
                                             "digest": ticket["digest"], "allow": allow})


async def test_review_survives_read_admission_and_owner_applies_exact_package(host, tmp_path):
    scope = host.profile_lease.scope
    ticket = await review(host, package(tmp_path))
    assert host.profile_lease.scope == scope
    assert ticket["package"]["mcp"][0]["transport"] == "stdio"
    assert (await host.call("plugins.list", {"agent_id": "jarvis"}))["plugins"] == []
    result = await decide(host, ticket)
    assert result["receipt"]["status"] == "committed" and not result["removed"]
    row = result["plugin"]
    assert row["enabled"] is False and host.profile_lease.scope != scope
    assert not result["receipt"]["connected_mcp"]
    catalog = await host.call("plugins.catalog", {"agent_id": "jarvis",
                             "plugin_id": row["plugin_id"], "expected_revision": row["revision"],
                             "expected_sha256": row["sha256"]})
    assert catalog["catalog"]["skills"][0]["skill_id"] == "example"
    resource = await host.call("plugins.resource", {"agent_id": "jarvis",
                               "plugin_id": row["plugin_id"], "expected_revision": row["revision"],
                               "expected_sha256": row["sha256"],
                               "path": "skills/example/references/data.txt"})
    assert base64.b64decode(resource["resource"]["content_base64"]) == b"RESOURCE SYNTHETIC 4682"
    with pytest.raises(RpcError) as stale:
        await decide(host, ticket)
    assert stale.value.code == -32011


async def test_deny_never_installs_and_unrelated_owner_cannot_consume_review(host, tmp_path):
    ticket = await review(host, package(tmp_path))
    token = authenticated_client.set(OTHER)
    try:
        with pytest.raises(RpcError):
            await decide(host, ticket)
    finally:
        authenticated_client.reset(token)
    # The rejected mutating RPC advances its own profile epoch; a fresh review
    # is required, and the foreign owner never acquired or installed its bytes.
    assert not host.plugins.list("jarvis")
    fresh = await review(host, package(tmp_path))
    assert await decide(host, fresh, False) == {"denied": True}
    assert not host.plugins.list("jarvis")


@pytest.mark.parametrize("change", ["workspace", "agent", "owner", "profile_generation", "busy"])
async def test_review_rechecks_context_immediately_before_effect(host, tmp_path, change):
    ticket = await review(host, package(tmp_path))
    if change == "workspace":
        host._workspace_id = "e" * 64
    elif change == "agent":
        host.agent_profiles.update("jarvis", expected_revision=1, name="Otro nombre")
    elif change == "owner":
        host.client_authority.revoked.add(CLIENT)
    elif change == "profile_generation":
        host.profile_lease.advance_generation()
    else:
        host.busy = True
    with pytest.raises(RpcError) as caught:
        await decide(host, ticket)
    assert caught.value.code in {-32003, -32011}
    assert not host.plugins.list("jarvis")


async def test_changed_archive_does_not_create_a_review_for_unseen_bytes(host, tmp_path):
    path = package(tmp_path)
    digest = (await host.call("plugins.inspect", {"path": str(path)}))["package"]["sha256"]
    path.write_bytes(b"not the reviewed archive")
    with pytest.raises(RpcError):
        await host.call("plugins.review", {"action": "install", "agent_id": "jarvis",
                                            "path": str(path), "package_sha256": digest})
    assert not host.plugins.list("jarvis")


async def test_revocation_at_final_private_json_guard_leaves_no_installed_package(
    host, tmp_path, monkeypatch,
):
    ticket = await review(host, package(tmp_path))
    original = host.plugins.document._write_temporary

    def revoke(*args, **kwargs):
        result = original(*args, **kwargs)
        host.client_authority.revoked.add(CLIENT)
        return result

    monkeypatch.setattr(host.plugins.document, "_write_temporary", revoke)
    with pytest.raises(RpcError) as caught:
        await decide(host, ticket)
    assert caught.value.code == -32003
    assert not host.plugins.list("jarvis")


async def test_unknown_publication_receipt_is_not_false_failure(host, tmp_path, monkeypatch):
    ticket = await review(host, package(tmp_path))

    def lost(*args, **kwargs):
        raise PrivateJsonError("registry_durability_uncertain")

    monkeypatch.setattr(host.plugins.document, "update", lost)
    result = await decide(host, ticket)
    assert result["receipt"]["status"] == "publication_unknown"
    assert result["plugin"] is None and result["removed"] is False


async def test_skill_library_versions_metadata_and_draft_are_owner_controlled(host, tmp_path):
    source = tmp_path / "skill"
    source.mkdir(mode=0o700)
    (source / "data.txt").write_text("SYNTHETIC RESOURCE")
    (source / "SKILL.md").write_text(
        "---\nname: Prueba\ndescription: Biblioteca propia\nversion: 1.0.0\n"
        'resources: ["data.txt"]\n---\nExplica brevemente el ejemplo.\n')
    library = (await host.call("skills.inspect_library", {"path": str(source)}))["library"]
    row = (await host.call("skills.import_library", {"agent_id": "jarvis", "path": str(source),
                           "expected_sha256": library["skill_sha256"],
                           "expected_library_sha256": library["library_sha256"]}))["skill"]
    args = {"agent_id": "jarvis", "skill_id": row["skill_id"],
            "expected_revision": row["revision"], "expected_sha256": row["sha256"]}
    assert (await host.call("skills.metadata", args))["metadata"]["version"] == "1.0.0"
    assert (await host.call("skills.versions", args))["versions"][0]["current"]
    invocation = (await host.call("skills.invoke", {**args, "arguments": {}}))["invocation"]
    assert invocation["executed_code"] is False and "nunca permisos" in invocation["context"]
    resource = (await host.call("skills.resource", {**args, "path": "data.txt"}))["resource"]
    assert base64.b64decode(resource["content_base64"]) == b"SYNTHETIC RESOURCE"
    with pytest.raises(RpcError) as incomplete:
        await host.call("skills.metadata", {"agent_id": "jarvis", "skill_id": row["skill_id"],
                                             "expected_revision": row["revision"]})
    assert incomplete.value.code == -32602


async def test_skill_unknown_mutation_has_nullable_record_and_no_automatic_replay(host, tmp_path,
                                                                                monkeypatch):
    source = tmp_path / "skill"
    source.mkdir(mode=0o700)
    (source / "SKILL.md").write_text("---\nname: Prueba\ndescription: Propia\n---\nTexto\n")
    library = (await host.call("skills.inspect_library", {"path": str(source)}))["library"]

    def lost(*args, **kwargs):
        raise PrivateJsonError("registry_durability_uncertain")

    monkeypatch.setattr(host.skills.document, "update", lost)
    result = await host.call("skills.import_library", {"agent_id": "jarvis", "path": str(source),
                              "expected_sha256": library["skill_sha256"],
                              "expected_library_sha256": library["library_sha256"]})
    assert result["skill"] is None and result["receipt"]["status"] == "publication_unknown"


@pytest.mark.parametrize("method,value", [
    ("plugins.list", {"agent_id": "jarvis", "token": "not accepted"}),
    ("plugins.decide", {"review_id": "wrong", "digest": "0" * 64, "allow": True}),
    ("skills.inspect_library", {"path": "relative"}),
    ("skills.invoke", {}),
])
async def test_invalid_rpc_fields_never_silently_select_a_default(host, method, value):
    with pytest.raises(RpcError) as caught:
        await host.call(method, value)
    assert caught.value.code == -32602
    assert not host.plugins.list("jarvis")


async def test_no_authenticated_identity_cannot_inspect_even_a_synthetic_archive(host, tmp_path):
    path = package(tmp_path)
    token = authenticated_client.set(None)
    try:
        with pytest.raises(RpcError) as caught:
            await host.call("plugins.inspect", {"path": str(path)})
        assert caught.value.code == -32003
    finally:
        authenticated_client.reset(token)


async def test_cleanup_retains_the_exact_owner_and_never_replays_a_mutation(host, monkeypatch):
    class Owner:
        calls = 0

        def cleanup(self):
            self.calls += 1
            if self.calls == 1:
                raise PrivateJsonError("registry_cleanup_pending")

    owner = Owner()
    host.plugins._pending = owner
    monkeypatch.setattr(host.plugins.document, "update",
                        lambda *args, **kwargs: pytest.fail("cleanup replayed a mutation"))
    first = await host.call("extensions.cleanup", {})
    assert first["receipt"]["status"] == "cleanup_pending" and first["plugins_pending"]
    assert host.plugins._pending is owner and owner.calls == 1
    second = await host.call("extensions.cleanup", {})
    assert second["receipt"]["cleanup_confirmed"] and not second["plugins_pending"]
    assert second["receipt"]["replayed_mutation"] is False and owner.calls == 2
    assert (await host.call("extensions.cleanup", {}))["receipt"]["status"] == "closed"


async def test_cleanup_never_runs_over_active_turns(host):
    class Owner:
        def cleanup(self):
            pytest.fail("active turn lost its cleanup fence")

    host.skills._pending = Owner()
    host._handles = {"synthetic": object()}
    with pytest.raises(RpcError) as error:
        await host.call("extensions.cleanup", {})
    assert error.value.code == -32011 and host.skills.pending_cleanup
    host.skills._pending = None  # Own fixture has no descriptors or effects.
