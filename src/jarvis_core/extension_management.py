"""Authenticated owner RPCs for reviewed extensions and versioned skill data.

The service supplies one outer profile admission per RPC. Reviews live only in
RAM and bind the admitted owner, physical profile, agent, workspace and exact
registry snapshot. No endpoint connects MCP, runs code or submits a model turn.
"""

from __future__ import annotations

import hashlib
import re
import time
from dataclasses import asdict
from functools import wraps

from jarvis_core.client_identity import ClientIdentityError, profile_binding
from jarvis_core.host_profile import HostProfileError
from jarvis_core.plugin_packages import PluginError, canonical, digest, inspect_package
from jarvis_core.private_json import PrivateJsonError
from jarvis_core.profiles import ProfileError
from jarvis_core.protocol import RpcError, authenticated_client
from jarvis_core.skills import SkillError, skill_metadata
from jarvis_core.workspace import WorkspaceError

EXTENSION_READ_RPCS = frozenset({
    "plugins.list", "plugins.inspect", "plugins.review", "plugins.versions", "plugins.catalog",
    "plugins.resource", "skills.metadata", "skills.versions", "skills.resource", "skills.invoke",
    "skills.inspect_library",
})


def _params(value, allowed, required=()):
    if (not isinstance(value, dict) or set(value) - set(allowed)
            or set(required) - set(value)):
        raise RpcError(-32602, "Los parámetros de extensiones no son válidos.")


def _translate(error):
    code = getattr(error, "code", "extension_unavailable")
    if isinstance(error, ClientIdentityError) or code == "client_not_admitted":
        return RpcError(-32003, "Se requiere un cliente dueño vigente para esta operación.")
    if (isinstance(error, (HostProfileError, WorkspaceError))
            or any(part in code for part in ("busy", "pending", "changed", "conflict", "expired",
                                            "revoked", "uncertain", "unknown"))):
        return RpcError(-32011, "El estado cambió o conserva una operación pendiente. "
                        "Revisa de nuevo.")
    return RpcError(-32602, "No se pudo validar la extensión, habilidad o revisión seleccionada.")


def _endpoint(callback):
    @wraps(callback)
    async def invoke(self, value):
        try:
            return await callback(self, value)
        except RpcError:
            raise
        except (PluginError, SkillError, PrivateJsonError, ProfileError,
                ClientIdentityError, HostProfileError, WorkspaceError) as error:
            raise _translate(error) from None
        except (OSError, TypeError, ValueError, UnicodeError):
            raise RpcError(-32602, "No se pudo validar la operación de extensiones.") from None
    return invoke


class ExtensionManagementAPI:
    def extension_handlers(self):
        return {
            "plugins.list": self.extension_list_plugins,
            "plugins.inspect": self.extension_inspect_package,
            "plugins.review": self.extension_review_plugin,
            "plugins.decide": self.extension_decide_plugin,
            "plugins.versions": self.extension_plugin_versions,
            "plugins.catalog": self.extension_plugin_catalog,
            "plugins.resource": self.extension_plugin_resource,
            "skills.metadata": self.extension_skill_metadata,
            "skills.versions": self.extension_skill_versions,
            "skills.rollback": self.extension_skill_rollback,
            "skills.retire_version": self.extension_skill_retire,
            "skills.resource": self.extension_skill_resource,
            "skills.invoke": self.extension_skill_invoke,
            "skills.inspect_library": self.extension_inspect_library,
            "skills.import_library": self.extension_import_library,
            "extensions.cleanup": self.extension_cleanup,
        }

    def _extension_guard(self, agent_id=None, *, mutating=False):
        owner = authenticated_client.get()
        if not isinstance(owner, str) or not re.fullmatch(r"[a-f0-9]{64}", owner):
            raise ClientIdentityError("client_not_admitted")
        self.client_authority.lookup(owner, scope="owner")
        self.profile_lease.assert_current()
        binding = profile_binding(self.directory)
        scope = self.profile_lease.scope
        workspace = self._workspace_id
        agent = self.agent_profiles.get(agent_id) if agent_id is not None else None
        deadline = time.monotonic() + 30 if mutating else None

        def guard():
            if authenticated_client.get() != owner:
                raise ClientIdentityError("client_not_admitted")
            self.client_authority.lookup(owner, scope="owner")
            self.profile_lease.assert_current(scope)
            if (profile_binding(self.directory) != binding or self._workspace_id != workspace
                    or (agent is not None
                        and self.agent_profiles.get(agent_id).fingerprint != agent.fingerprint)):
                raise PluginError("plugin_context_changed")
            self._assert_workspace_current()
            if deadline is not None and time.monotonic() >= deadline:
                raise PluginError("plugin_deadline")
            if mutating:
                self._management_idle()
                if self.plugins.pending_cleanup or self.skills.pending_cleanup:
                    raise PluginError("plugin_cleanup_pending")

        guard()
        return guard

    def _extension_tickets(self):
        if not hasattr(self, "_extension_reviews"):
            self._extension_reviews = {}
        now = time.monotonic()
        self._extension_reviews = {key: entry for key, entry in self._extension_reviews.items()
                                   if entry["expires"] > now}
        return self._extension_reviews

    @staticmethod
    def _extension_cas(value):
        fields = {"expected_revision", "expected_sha256"}
        present = fields & value.keys()
        if present and present != fields:
            raise RpcError(-32602, "La revisión y el hash deben proporcionarse juntos.")
        if present:
            if type(value["expected_revision"]) is not int or value["expected_revision"] < 1:
                raise RpcError(-32602, "La revisión de la habilidad no es válida.")
            digest(value["expected_sha256"])

    def _extension_skill(self, value):
        self._extension_cas(value)
        content = self.skills.get(value["agent_id"], value["skill_id"])
        if "expected_revision" in value and (
            content.record.revision != value["expected_revision"]
            or content.record.sha256 != value["expected_sha256"]
        ):
            raise SkillError("skill_revision_conflict")
        return content

    @_endpoint
    async def extension_list_plugins(self, value):
        _params(value, {"agent_id"}, {"agent_id"})
        guard = self._extension_guard(value["agent_id"])
        result = [asdict(row) for row in self.plugins.list(value["agent_id"])]
        guard()
        return {"plugins": result}

    @_endpoint
    async def extension_inspect_package(self, value):
        _params(value, {"path"}, {"path"})
        guard = self._extension_guard()
        package = inspect_package(value["path"], deadline=time.monotonic() + 20)
        guard()
        return {"package": package.public()}

    @_endpoint
    async def extension_review_plugin(self, value):
        _params(value, {"action", "agent_id", "path", "package_sha256", "plugin_id",
                        "expected_revision", "expected_sha256", "target_version"},
                {"action", "agent_id"})
        guard = self._extension_guard(value["agent_id"])
        candidate = None
        if value["action"] in {"install", "update"}:
            if not {"path", "package_sha256"} <= value.keys():
                raise RpcError(-32602, "Selecciona el paquete y su hash revisado.")
            digest(value["package_sha256"])
            candidate = inspect_package(value["path"], deadline=time.monotonic() + 20)
            if candidate.sha256 != value["package_sha256"]:
                raise PluginError("plugin_source_changed")
        elif "path" in value or "package_sha256" in value:
            raise RpcError(-32602, "Esta operación no instala un archivo nuevo.")
        tickets = self._extension_tickets()
        if len(tickets) >= 16:
            raise PluginError("plugin_review_capacity")
        options = {key: value[key] for key in ("plugin_id", "expected_revision",
                                               "expected_sha256", "target_version") if key in value}
        review = self.plugins.review(value["action"], value["agent_id"],
                                      package=candidate, **options)
        guard()
        tickets[review.review_id] = {"owner": authenticated_client.get(), "review": review,
                                     "guard": guard, "expires": time.monotonic() + 120}
        return {"review": review.public()}

    @_endpoint
    async def extension_decide_plugin(self, value):
        _params(value, {"review_id", "digest", "allow"}, {"review_id", "digest", "allow"})
        if (not isinstance(value["review_id"], str)
                or not re.fullmatch(r"[a-f0-9]{32}", value["review_id"])
                or type(value["allow"]) is not bool):
            raise RpcError(-32602, "La decisión de extensiones no es válida.")
        digest(value["digest"])
        self._extension_guard()
        tickets = self._extension_tickets()
        ticket = tickets.get(value["review_id"])
        if (ticket is None or ticket["owner"] != authenticated_client.get()
                or ticket["review"].digest != value["digest"]):
            raise PluginError("plugin_review_expired_or_changed")
        ticket["guard"]()
        if not value["allow"]:
            tickets.pop(value["review_id"])
            return {"denied": True}
        review = ticket["review"]
        effect_guard = self._extension_guard(review.agent_id, mutating=True)

        def guard():
            ticket["guard"]()
            effect_guard()

        guard()
        tickets.pop(value["review_id"])
        try:
            receipt = self.plugins.apply(review, assert_current=guard,
                                          deadline=time.monotonic() + 20)
        except PluginError as error:
            if error.receipt is None or error.receipt.get("status") != "publication_unknown":
                raise
            return {"receipt": error.receipt, "plugin": None, "removed": False}
        try:
            guard()
            record = (None if review.action == "remove"
                      else asdict(self.plugins.get(review.agent_id, review.plugin_id)))
        except (PluginError, ClientIdentityError, HostProfileError, ProfileError,
                WorkspaceError, RpcError, OSError):
            return {"receipt": {**receipt, "status": "publication_unknown"},
                    "plugin": None, "removed": False}
        return {"receipt": receipt, "plugin": record, "removed": review.action == "remove"}

    @_endpoint
    async def extension_plugin_versions(self, value):
        _params(value, {"agent_id", "plugin_id"}, {"agent_id", "plugin_id"})
        guard = self._extension_guard(value["agent_id"])
        versions = self.plugins.versions(value["agent_id"], value["plugin_id"])
        guard()
        return {"versions": list(versions)}

    @_endpoint
    async def extension_plugin_catalog(self, value):
        _params(value, {"agent_id", "plugin_id", "expected_revision", "expected_sha256"},
                {"agent_id", "plugin_id", "expected_revision", "expected_sha256"})
        guard = self._extension_guard(value["agent_id"])
        row = self.plugins._row(self.plugins.document.read(), value["agent_id"], value["plugin_id"])
        self.plugins._cas(row, value["expected_revision"], value["expected_sha256"])
        manifest = row["current"]["manifest"]
        result = {key: manifest[key] for key in ("files", "skills", "prompts", "hooks", "mcp")}
        result["hooks"] = [{**hook, "sha256": hashlib.sha256(canonical(hook)).hexdigest()}
                           for hook in manifest["hooks"]]
        guard()
        return {"catalog": result}

    @_endpoint
    async def extension_plugin_resource(self, value):
        fields = {"agent_id", "plugin_id", "path", "expected_revision", "expected_sha256"}
        _params(value, fields, fields)
        guard = self._extension_guard(value["agent_id"])
        resource = self.plugins.resource(**value)
        guard()
        return {"resource": resource}

    @_endpoint
    async def extension_skill_metadata(self, value):
        _params(value, {"agent_id", "skill_id", "expected_revision", "expected_sha256"},
                {"agent_id", "skill_id"})
        guard = self._extension_guard(value["agent_id"])
        content = self._extension_skill(value)
        metadata = skill_metadata(content.text)
        guard()
        return {"metadata": metadata}

    @_endpoint
    async def extension_skill_versions(self, value):
        _params(value, {"agent_id", "skill_id", "expected_revision", "expected_sha256"},
                {"agent_id", "skill_id"})
        guard = self._extension_guard(value["agent_id"])
        self._extension_skill(value)
        versions = self.skills.versions(value["agent_id"], value["skill_id"])
        guard()
        return {"versions": list(versions)}

    def _extension_skill_effect(self, value, operation, method):
        guard = self._extension_guard(value["agent_id"], mutating=True)
        try:
            record = method(**value, assert_current=guard)
        except (PrivateJsonError, SkillError) as error:
            code = getattr(error, "code", "skill_unavailable")
            if (getattr(error, "owner", None) is not None
                    or code in {"registry_durability_uncertain", "registry_unavailable",
                                "registry_cleanup_pending"}):
                return {"skill": None, "receipt": {"operation": operation,
                        "status": "publication_unknown", "cleanup_confirmed": not bool(
                            getattr(error, "owner", None)), "executed_code": False}}
            raise
        try:
            guard()
        except (PluginError, ClientIdentityError, HostProfileError, ProfileError,
                WorkspaceError, RpcError, OSError):
            return {"skill": None, "receipt": {"operation": operation,
                    "status": "publication_unknown", "cleanup_confirmed": True,
                    "executed_code": False}}
        return {"skill": asdict(record)}

    @_endpoint
    async def extension_skill_rollback(self, value):
        fields = {"agent_id", "skill_id", "target_revision", "expected_revision", "expected_sha256"}
        _params(value, fields, fields)
        return self._extension_skill_effect(value, "rollback", self.skills.rollback)

    @_endpoint
    async def extension_skill_retire(self, value):
        fields = {"agent_id", "skill_id", "target_revision", "expected_revision", "expected_sha256"}
        _params(value, fields, fields)
        return self._extension_skill_effect(value, "retire_version", self.skills.retire_version)

    @_endpoint
    async def extension_skill_resource(self, value):
        fields = {"agent_id", "skill_id", "path", "expected_revision", "expected_sha256"}
        _params(value, fields, fields)
        guard = self._extension_guard(value["agent_id"])
        result = self.skills.resource(**value)
        guard()
        return {"resource": result}

    @_endpoint
    async def extension_skill_invoke(self, value):
        fields = {"agent_id", "skill_id", "arguments", "expected_revision", "expected_sha256"}
        _params(value, fields, fields)
        guard = self._extension_guard(value["agent_id"])
        result = self.skills.invoke(**value)
        guard()
        return {"invocation": result}

    @_endpoint
    async def extension_inspect_library(self, value):
        _params(value, {"path"}, {"path"})
        guard = self._extension_guard()
        result = self.skills.inspect_library(value["path"]).public()
        guard()
        return {"library": result}

    @_endpoint
    async def extension_import_library(self, value):
        fields = {"agent_id", "path", "expected_sha256", "expected_library_sha256", "enabled"}
        _params(value, fields, fields - {"enabled"})
        options = {"enabled": False, **value}
        options["directory"] = options.pop("path")
        return self._extension_skill_effect(options, "import_library", self.skills.import_library)

    @_endpoint
    async def extension_cleanup(self, value):
        _params(value, set())
        guard = self._extension_guard()
        # _management_idle also rejects these exact retained owners. Check its
        # independent run/maintenance fences without interpreting an unrelated
        # busy exception as permission to retire an owner.
        intelligence = getattr(self, "memory_intelligence", None)
        if (any(bool(getattr(self, name, False)) for name in (
                "_closing", "_maintenance", "_handles", "_refs", "_retiring_sessions"))
                or (intelligence is not None and intelligence.blockers)):
            raise RpcError(-32011, "Espera al cierre de las ejecuciones antes de retirar "
                            "la custodia.")
        failures = []
        for name in ("plugins", "skills"):
            guard()
            store = getattr(self, name)
            if store.pending_cleanup:
                try:
                    store.settle_cleanup()
                except (PrivateJsonError, PluginError, SkillError, OSError):
                    failures.append(name)
        guard()
        pending = {"plugins_pending": self.plugins.pending_cleanup,
                   "skills_pending": self.skills.pending_cleanup}
        closed = not failures and not any(pending.values())
        return {"receipt": {"operation": "extensions_cleanup",
                "status": "closed" if closed else "cleanup_pending", "cleanup_confirmed": closed,
                "replayed_mutation": False}, **pending}
