"""Authenticated product controls and context-bound coordination integration."""

from __future__ import annotations

import hashlib
import json
from decimal import ROUND_DOWN, Decimal, localcontext
from pathlib import Path

from jarvis_core.product_interactions import (
    InteractionActor,
    InteractionScope,
    ProductInteractionError,
    ProductInteractions,
)
from jarvis_core.product_projects import ProjectError, ProjectsBackend, ProjectsStore
from jarvis_core.product_projects_github import (
    GitHubCredentialService,
    GitHubProjects,
    GitHubPublicationsStore,
)
from jarvis_core.protocol import RpcError, authenticated_client
from jarvis_core.session_coordination import SessionCoordinationStore
from jarvis_core.session_coordination_types import CoordinationError
from jarvis_core.state import SessionRef, ensure_private_directory
from jarvis_core.workspace import WorkspaceTools

BASE = {"session_id", "temporary", "run_id"}


def params(value, allowed, required=()):
    if type(value) is not dict or set(value) - set(allowed) or set(required) - set(value):
        raise RpcError(-32602, "Los parámetros del producto no son válidos.")


def workspace_key(tools):
    return hashlib.sha256(json.dumps([str(tools.root), *tools.identity]).encode()).hexdigest()[:32]


class ProductService:
    CoordinationError = CoordinationError

    def __init__(self, service):
        self.service = service
        self.enabled = False
        self.coordination = self.interactions = self.projects = None
        self.ephemeral = {}
        self.usage = {}
        self.reservations = {}
        self.non_model_runs = set()
        self.final_usage = set()
        self.envelopes = {}
        self.observed = {}
        self.github = {}

    def actor_id(self):
        identity = authenticated_client.get()
        if identity is None:
            raise RpcError(-32003, "Esta función necesita un cliente local admitido.")
        self.service.client_authority.lookup(identity)
        return identity

    def _assert_client(self, identity, *, write=False):
        return self.service.client_authority.lookup(identity, scope="owner" if write else None)

    def _authorize(self, identity, operation, ref, arguments):
        self._assert_client(identity, write=True)
        self.service.profile_lease.assert_current()
        if self.service._closing or self.service._maintenance:
            raise RpcError(-32011, "Espera al cierre o mantenimiento del servicio.")

    def _idle(self, ref):
        if self.service._busy(ref) or ref in self.service._retiring_sessions:
            raise RpcError(-32011, "Espera a que termine esta conversación antes de cambiarla.")
        managers = [item.processes for item in self.service._product_workspaces.values()]
        if self.service._default_process_tools:
            managers.append(self.service._default_process_tools)
        if any(
            row["running"] for manager in managers for row in manager.list_processes(ref.session_id)
        ):
            raise RpcError(-32011, "Detén los procesos de esta conversación antes de cambiarla.")

    def enable(self, identity, *, temporary=False):
        self._assert_client(identity, write=True)
        if self.enabled:
            return
        service = self.service
        self.coordination = SessionCoordinationStore(
            service.directory / "session-coordination.sqlite3",
            assert_client=self._assert_client,
            assert_session=service.store.get_session,
            assert_idle=self._idle,
            authorize_change=self._authorize,
        )
        self.interactions = ProductInteractions(
            service.directory / "product-interactions.sqlite3",
            assert_current=self._interaction_guard,
            resolve_source=self._source,
        )
        project_store = ProjectsStore(ensure_private_directory(service.directory / "projects"))
        worktrees = ensure_private_directory(
            service.directory.with_name(service.directory.name + ".project-worktrees")
        )
        self.projects = ProjectsBackend(project_store, worktrees)
        self.projects.runner.child_custody = service.profile_lease.child_custody_fds
        owners = [
            row["client_id"]
            for row in service.client_authority.document.read()["clients"]
            if row["scope"] == "owner" and not row["revoked"]
        ]
        self.primary_owner = owners[0] if owners else identity
        self.enabled = True
        if identity == self.primary_owner:
            self._default_workspace(identity, temporary=temporary)

    def _default_workspace(self, identity, *, temporary=False):
        if identity != self.primary_owner:
            own = [
                row
                for row in self.coordination.list_workspaces(identity, temporary=temporary)
                if row["owner_id"] == identity
            ]
            if not own:
                raise RpcError(-32030, "Este cliente necesita elegir una carpeta propia.")
            return own[0]["workspace_id"]
        tools = self.service._default_workspace_tools
        if tools is None:
            raise RpcError(-32030, "Selecciona una carpeta de trabajo disponible.")
        if WorkspaceTools(tools.root).identity != tools.identity:
            raise CoordinationError("coordination_workspace_identity_changed")
        key = workspace_key(tools)
        rows = self.coordination.list_workspaces(identity, temporary=temporary)
        if not any(row["workspace_id"] == key for row in rows):
            self.coordination.register_workspace(
                identity,
                workspace_id=key,
                name="Carpeta principal",
                path=tools.root,
                temporary=temporary,
            )
        return key

    def binding(self, identity, ref, *, claim=False):
        try:
            return self.coordination.get_session(identity, ref)
        except CoordinationError as error:
            if error.code != "coordination_session_unbound" or not claim:
                raise
        if identity != self.primary_owner:
            raise RpcError(-32003, "El dueño de esta conversación debe compartirla primero.")
        record = self.service.store.get_session(ref)
        if record.parent_session_id:
            parent = SessionRef(record.parent_session_id, ref.temporary)
            source = self.binding(identity, parent, claim=True)
            return self.coordination.bind_session(
                identity, ref, workspace_id=source["workspace_id"], parent_ref=parent
            )
        return self.coordination.bind_session(
            identity, ref, workspace_id=self._default_workspace(identity, temporary=ref.temporary)
        )

    def can_read(self, identity, ref):
        if not self.enabled or identity is None:
            return True
        try:
            self.binding(identity, ref)
            return True
        except CoordinationError as error:
            return error.code == "coordination_session_unbound" and identity == self.primary_owner

    def check_access(self, identity, ref, *, write=False):
        if not self.enabled or identity is None:
            return
        binding = self.binding(identity, ref, claim=True)
        if (
            write
            and binding["owner_id"] != identity
            and not any(
                row["client_id"] == identity and row["role"] == "collaborate"
                for row in binding["members"]
            )
        ):
            raise RpcError(-32003, "Este cliente sólo puede leer esta conversación.")

    def context(self, value, *, identity=None):
        identity = identity or self.actor_id()
        ref = self.service._ref(value)
        if not self.enabled:
            self.enable(identity, temporary=ref.temporary)
        binding = self.binding(identity, ref, claim=True)
        run_id = value.get("run_id") or "host"
        if run_id != "host":
            self.service.store.get_run(ref, run_id)
        scope = InteractionScope(
            ref.session_id,
            run_id,
            self.service.store.get_session(ref).agent_id,
            binding["workspace_id"],
            ref.temporary,
            binding["security_generation"],
        )
        row = self.service.client_authority.lookup(identity)
        return ref, scope, InteractionActor(identity, row["scope"])

    def _interaction_guard(self, scope, actor, write):
        row = self._assert_client(actor.client_id, write=write)
        if row["scope"] != actor.role:
            raise ProductInteractionError("interaction_role_changed")
        ref = SessionRef(scope.session_id, scope.temporary)
        binding = self.binding(actor.client_id, ref)
        record = self.service.store.get_session(ref)
        self.check_access(actor.client_id, ref, write=write)
        if (
            record.agent_id != scope.agent_id
            or binding["workspace_id"] != scope.workspace_id
            or scope.generation != binding["security_generation"]
        ):
            raise ProductInteractionError("interaction_scope_changed")
        self.coordination.workspace_tools(actor.client_id, ref)
        if scope.run_id != "host":
            self.service.store.get_run(ref, scope.run_id)
        self.service.profile_lease.assert_current()

    def _source(self, scope, actor, source_id):
        ref = SessionRef(scope.session_id, scope.temporary)
        self._interaction_guard(scope, actor, False)
        if source_id == "session":
            return {
                "type": "sessions",
                "label": "Conversación",
                "text": self.service.store.get_session(ref).title,
            }
        if source_id == "runs":
            runs = self.service.store.list_runs(ref, limit=20)
            return {
                "type": "runs",
                "label": "Ejecuciones de esta conversación",
                "text": "\n".join(f"{r.run_id}: {r.status}" for r in runs),
            }
        if source_id == "inbox":
            rows = self.coordination.inbox(actor.client_id, ref)
            return {
                "type": "inbox",
                "label": "Bandeja de esta conversación",
                "text": str(len(rows.get("messages", []))),
            }
        if source_id == "usage":
            try:
                budget = self.coordination.get_budget(actor.client_id, ref)
            except CoordinationError:
                budget = {}
            return {
                "type": "usage",
                "label": "Presupuesto",
                "text": json.dumps(budget, ensure_ascii=False)[:4000],
            }
        if source_id == "projects":
            rows = self.service.product_visible_projects(actor.client_id, temporary=scope.temporary)
            return {
                "type": "projects",
                "label": "Proyectos admitidos",
                "text": "\n".join(row["name"] for row in rows)[:4000],
            }
        if source_id == "goals":
            rows = [goal for goal in self.service._active_goals.values() if goal.ref == ref]
            return {
                "type": "goals",
                "label": "Delegaciones activas",
                "text": "\n".join(goal.parent_run_id for goal in rows)[:4000],
            }
        raise ProductInteractionError("source_not_selected")

    def guard_run(self, run_id):
        if not self.enabled:
            return
        ref = self.service._refs.get(run_id)
        identity = self.service._run_clients.get(run_id)
        if ref is None or identity is None:
            return
        self.check_access(identity, ref, write=True)
        binding = self.binding(identity, ref)
        selected = self.service._run_workspaces.get(run_id)
        if selected and (
            binding["workspace_id"] != selected.registry_id
            or binding["revision"] != selected.binding_revision
        ):
            raise CoordinationError("coordination_run_workspace_changed")
        if selected:
            if WorkspaceTools(selected.path).identity != selected.tools.identity:
                raise CoordinationError("coordination_workspace_changed")

    def take_ephemeral(self, receipt):
        row = self.ephemeral.pop(receipt.operation_id, None)
        if row is None:
            return None
        run_id, text = row
        self.guard_run(run_id)
        if receipt.tool_name != "request_user_input" or not receipt.succeeded:
            return None
        return text

    def reserve_run(self, identity, ref, run_id, parameters):
        if self.service.host_tools.pending.get() is not None:
            return  # host-only operations do not call a billable model
        parent_id = None
        parent_envelope = None
        for goal in self.service._active_goals.values():
            if any(child["run_id"] == run_id for child in goal.children):
                entry = self.reservations.get(goal.parent_run_id)
                if entry:
                    parent_id = entry[1]
                    parent_envelope = self.coordination.get_reservation(
                        identity, goal.ref, parent_id
                    )
                    parts = max(1, len(goal.spec["children"]))
                    with localcontext() as decimal_context:
                        decimal_context.prec = 100
                        shared_cost = (
                            None
                            if parent_envelope["reserved_cost"] is None
                            else format(
                                (Decimal(parent_envelope["reserved_cost"]) / parts).quantize(
                                    Decimal("0.000000000001"), rounding=ROUND_DOWN
                                ),
                                "f",
                            )
                        )
                    parent_envelope = {
                        "tokens": None
                        if parent_envelope["reserved_tokens"] is None
                        else max(
                            0, parent_envelope["reserved_tokens"] - self.observed.get(parent_id, 0)
                        )
                        // parts,
                        "cost": shared_cost,
                    }
                break
        try:
            budget = self.coordination.get_budget(identity, ref)
        except CoordinationError as error:
            if error.code != "coordination_budget_not_configured":
                raise
            if parent_envelope is None:
                return
            budget = self.coordination.configure_budget(
                identity,
                ref,
                token_limit=parent_envelope["tokens"],
                cost_limit=parent_envelope["cost"],
            )
        tokens = parameters.get("reservation_tokens")
        cost = parameters.get("reservation_cost")
        if tokens is None and budget["token_limit"] is not None:
            used = budget["committed_tokens"]
            if used is None:
                raise CoordinationError("coordination_budget_usage_unknown")
            tokens = budget["token_limit"] - used
            if tokens <= 0:
                raise CoordinationError("coordination_budget_exceeded")
        if cost is None and budget["cost_limit"] is not None:
            used = budget["committed_cost"]
            if used is None:
                raise CoordinationError("coordination_budget_usage_unknown")
            cost = format(Decimal(budget["cost_limit"]) - Decimal(used), "f")
        reservation = self.coordination.reserve_budget(
            identity,
            ref,
            reservation_id=run_id,
            run_id=run_id,
            tokens=tokens,
            cost=cost,
            parent_reservation_id=parent_id,
        )
        if reservation["status"] != "reserved":
            raise CoordinationError("coordination_reservation_not_replayable")
        reservation = self.coordination.dispatch_reservation(
            identity, ref, reservation_id=run_id, expected_revision=reservation["revision"]
        )
        self.reservations[run_id] = identity, reservation["reservation_id"]
        self.envelopes[run_id] = {"parent": parent_id, "tokens": tokens}

    def observe_usage(self, run_id, tokens, *, final=False):
        self.usage[run_id] = tokens
        if final:
            self.final_usage.add(run_id)
        if run_id not in self.envelopes:
            return
        self.observed[run_id] = max(tokens, self.observed.get(run_id, 0))

        def total(identity, depth=0):
            if depth > 4:
                raise CoordinationError("coordination_reservation_lineage_invalid")
            return self.observed.get(identity, 0) + sum(
                total(key, depth + 1)
                for key, row in self.envelopes.items()
                if row["parent"] == identity
            )

        current = run_id
        while current in self.envelopes:
            row = self.envelopes[current]
            limit = row["tokens"]
            if limit is not None and total(current) >= limit and (current != run_id or not final):
                handle = self.service._handles.get(current)
                if handle is not None:
                    handle.cancel()
            current = row["parent"]

    def settle_run(self, ref, result):
        if not self.enabled:
            return
        entry = self.reservations.pop(result.run_id, None)
        measured = self.usage.pop(result.run_id, None)
        final_known = result.run_id in self.final_usage
        self.final_usage.discard(result.run_id)
        non_model = result.run_id in self.non_model_runs
        self.non_model_runs.discard(result.run_id)
        if entry:
            identity, reservation_id = entry
            current = self.coordination.get_reservation(identity, ref, reservation_id)
            self.coordination.reconcile_budget(
                identity,
                ref,
                reservation_id=reservation_id,
                actual_tokens=0
                if non_model
                else (measured if result.status == "completed" or final_known else None),
                actual_cost="0" if non_model else None,
                effect_status="known" if result.status == "completed" else "unknown",
                cleanup_confirmed=result.cleanup_confirmed,
                status="settled" if result.status == "completed" else "unknown",
                expected_revision=current["revision"],
            )
        envelope = self.envelopes.get(result.run_id)
        if envelope is not None and envelope["parent"] not in self.envelopes:
            retired = {result.run_id}
            for _ in range(4):
                retired.update(
                    key for key, row in self.envelopes.items() if row["parent"] in retired
                )
            for identity in retired:
                self.envelopes.pop(identity, None)
                self.observed.pop(identity, None)
        identity = self.service._run_clients.get(result.run_id)
        if identity:
            try:
                _, scope, actor = self.context(
                    {
                        "session_id": ref.session_id,
                        "temporary": ref.temporary,
                        "run_id": result.run_id,
                    },
                    identity=identity,
                )
                self.interactions.request_attention(
                    scope,
                    actor,
                    kind="run_completed" if result.status == "completed" else "run_failed",
                    correlation_id=result.run_id,
                )
            except ProductInteractionError as error:
                if error.code != "product_notifications_disabled":
                    raise

    def revoked(self, identity):
        entry = self.github.pop(identity, None)
        if entry:
            entry["backend"].credentials.revoke()
        if self.enabled:
            self.coordination.revoke_client(identity)
            self.interactions.forget_client(identity)
        self.ephemeral.clear()

    async def close(self):
        self.ephemeral.clear()
        for entry in self.github.values():
            entry["backend"].credentials.revoke()
            await entry["backend"].aclose()
        self.github.clear()
        if self.enabled:
            await self.projects.aclose()
            self.interactions.close()
            self.coordination.close()


class ProductManagementAPI:
    def initialize_product(self):
        self.initialize_workspace_context()
        self.product = ProductService(self)

    def product_handlers(self):
        return {
            "product.initialize": self.product_initialize,
            "product.snapshot": self.product_snapshot,
            "product.interaction": self.product_interaction,
            "product.coordination": self.product_coordination,
            "product.projects": self.product_projects,
            "product.tool.start": self.product_tool_start,
            "product.tool.result": self.product_tool_result,
        }

    async def product_initialize(self, value):
        params(value, {"temporary", "workspace_path", "workspace_name"})
        temporary = value.get("temporary", False)
        if type(temporary) is not bool:
            raise RpcError(-32602, "El tipo de conversación no es válido.")
        identity = self.product.actor_id()
        self.product.enable(identity, temporary=temporary)
        if {"workspace_path", "workspace_name"} & set(value):
            params(
                value,
                {"temporary", "workspace_path", "workspace_name"},
                {"workspace_path", "workspace_name"},
            )
            tools = self.product_admit_workspace(value["workspace_path"])
            self.product.coordination.register_workspace(
                identity,
                workspace_id=workspace_key(tools),
                name=value["workspace_name"],
                path=tools.root,
                temporary=temporary,
            )
        elif identity == self.product.primary_owner:
            self.product._default_workspace(identity, temporary=temporary)
        return {
            "workspaces": self.product.coordination.list_workspaces(identity, temporary=temporary)
        }

    async def product_snapshot(self, value):
        params(value, BASE, {"session_id"})
        ref, scope, actor = self.product.context(value)
        data = self.product.interactions.snapshot(scope, actor)
        data["client_id"] = actor.client_id
        for item in data["interactions"]:
            item["scope_run_id"] = scope.run_id
        if scope.run_id != "host":
            host = InteractionScope(
                scope.session_id,
                "host",
                scope.agent_id,
                scope.workspace_id,
                scope.temporary,
                scope.generation,
            )
            baseline = self.product.interactions.snapshot(host, actor)
            data["interactions"] += [
                dict(item, scope_run_id="host") for item in baseline["interactions"]
            ]
            data["notification_preferences"] = baseline["notification_preferences"]
        data["binding"] = self.product.binding(actor.client_id, ref)
        data["workspaces"] = self.product.coordination.list_workspaces(
            actor.client_id, temporary=ref.temporary
        )
        data["sessions"] = [
            dict(
                row,
                title=self.store.get_session(SessionRef(row["session_id"], row["temporary"])).title,
            )
            for row in self.product.coordination.list_sessions(
                actor.client_id, temporary=ref.temporary
            )
        ]
        data["inbox"] = self.product.coordination.inbox(actor.client_id, ref)
        try:
            data["budget"] = self.product.coordination.get_budget(actor.client_id, ref)
        except CoordinationError as error:
            if error.code != "coordination_budget_not_configured":
                raise
            data["budget"] = None
        data["projects"] = self.product_visible_projects(actor.client_id, temporary=ref.temporary)
        return data

    def product_visible_projects(self, identity, *, temporary=False):
        paths = {
            row["path"]
            for row in self.product.coordination.list_workspaces(identity, temporary=temporary)
        }
        return [
            row
            for row in self.product.projects.store.list()
            if row.get("pin", {}).get("root") in paths and row["temporary"] == temporary
        ]

    async def product_projects(self, value):
        params(value, BASE | {"kind", "arguments"}, {"session_id", "kind", "arguments"})
        ref, _, actor = self.product.context(value)
        identity, kind, arguments = actor.client_id, value["kind"], value["arguments"]
        if type(arguments) is not dict:
            raise RpcError(-32602, "La consulta de proyectos no es válida.")
        if kind == "list":
            params(arguments, set())
            result = {"projects": self.product_visible_projects(identity, temporary=ref.temporary)}
        elif kind in {"inspect", "log", "worktrees"}:
            allowed = (
                {"project_id", "limit", "offset"}
                if kind == "inspect"
                else ({"project_id", "limit"} if kind == "log" else {"project_id"})
            )
            params(arguments, allowed, {"project_id"})
            if arguments["project_id"] not in {
                row["project_id"]
                for row in self.product_visible_projects(identity, temporary=ref.temporary)
            }:
                raise ProjectError("project_not_admitted")
            result = await getattr(self.product.projects, kind)(**arguments)
        elif kind == "github.connect":
            params(arguments, {"token", "owner", "repository"}, {"token", "owner", "repository"})
            self.client_authority.lookup(identity, scope="owner")
            import re

            if (
                type(arguments["owner"]) is not str
                or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9-]{0,38}", arguments["owner"]) is None
                or type(arguments["repository"]) is not str
                or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,99}", arguments["repository"]) is None
            ):
                raise ProjectError("github_repository_invalid")
            previous = self.product.github.pop(identity, None)
            if previous:
                previous["backend"].credentials.revoke()
            credentials = GitHubCredentialService()
            credentials.install(arguments["token"])
            registry = ensure_private_directory(self.directory / "projects" / identity)
            backend = GitHubProjects(credentials, GitHubPublicationsStore(registry))
            try:
                account = await backend.identity()
                self.client_authority.lookup(identity, scope="owner")
                self.product._authorize(identity, "github.connect", ref, {})
            except BaseException:
                credentials.revoke()
                raise
            self.product.github[identity] = {
                "backend": backend,
                "owner": arguments["owner"],
                "repository": arguments["repository"],
                "login": account["login"],
                "account_id": account["id"],
            }
            result = {
                **credentials.status(),
                "login": account["login"],
                "owner": arguments["owner"],
                "repository": arguments["repository"],
            }
        elif kind == "github.status":
            params(arguments, set())
            entry = self.product.github.get(identity)
            result = (
                {"configured": False, "storage": "memory_only"}
                if entry is None
                else {
                    **entry["backend"].credentials.status(),
                    **{key: entry[key] for key in ("owner", "repository", "login")},
                }
            )
        elif kind == "github.disconnect":
            params(arguments, set())
            self.client_authority.lookup(identity, scope="owner")
            entry = self.product.github.pop(identity, None)
            if entry:
                entry["backend"].credentials.revoke()
            result = {"configured": False, "storage": "memory_only"}
        else:
            raise RpcError(-32602, "La operación de proyectos no existe.")
        return {"result": result}

    async def product_interaction(self, value):
        params(value, BASE | {"kind", "arguments"}, {"session_id", "kind", "arguments"})
        _, scope, actor = self.product.context(value)
        kind, arguments = value["kind"], value["arguments"]
        if type(arguments) is not dict:
            raise RpcError(-32602, "La acción no tiene argumentos válidos.")
        api = self.product.interactions
        selected = dict(arguments)
        identity, revision = selected.pop("id", None), selected.pop("revision", None)
        if kind == "question.answer":
            params(selected, {"answers"}, {"answers"})
            result = api.answer_question(scope, actor, identity, revision, selected["answers"])
        elif kind == "question.cancel":
            params(selected, set())
            result = api.cancel(scope, actor, identity, revision)
        elif kind == "suggestion.select":
            params(selected, {"suggestion_id"}, {"suggestion_id"})
            result = api.select_suggestion(
                scope, actor, identity, revision, selected["suggestion_id"]
            )
        elif kind == "suggestion.dismiss":
            params(selected, set())
            row = api.get(scope, actor, identity)
            if row["kind"] != "suggestions":
                raise ProductInteractionError("product_suggestions_invalid")
            result = api.cancel(scope, actor, identity, revision)
        elif kind == "panel.set":
            params(selected, {"panel"}, {"panel"})
            result = api.set_panel(scope, actor, expected_revision=revision, **selected)
        elif kind == "panel.toggle":
            params(
                selected,
                {"widget_id", "item_index", "checked"},
                {"widget_id", "item_index", "checked"},
            )
            result = api.toggle_widget_item(scope, actor, identity, revision, **selected)
        elif kind == "notifications.configure":
            params(selected, {"enabled", "silenced"}, {"enabled", "silenced"})
            result = api.configure_notifications(
                scope, actor, expected_revision=revision, **selected
            )
        elif kind.startswith("notification."):
            methods = {
                "notification.claim": api.claim_notification,
                "notification.validate": api.validate_notification_claim,
                "notification.settle": api.settle_notification,
                "notification.resume": api.resume_notification,
            }
            method = methods.get(kind)
            if method is None:
                raise RpcError(-32602, "La acción de notificación no existe.")
            result = method(scope, actor, identity, revision, **selected)
        else:
            raise RpcError(-32602, "Esta acción del producto no está disponible.")
        return {"result": result}

    async def product_coordination(self, value):
        params(value, BASE | {"kind", "arguments"}, {"session_id", "kind", "arguments"})
        ref, _, actor = self.product.context(value)
        kind, arguments = value["kind"], value["arguments"]
        if type(arguments) is not dict:
            raise RpcError(-32602, "La coordinación no tiene argumentos válidos.")
        api, identity = self.product.coordination, actor.client_id
        if kind == "workspace.register":
            params(arguments, {"path", "name"}, {"path", "name"})
            tools = self.product_admit_workspace(arguments["path"])
            result = api.register_workspace(
                identity,
                workspace_id=workspace_key(tools),
                name=arguments["name"],
                path=tools.root,
                temporary=ref.temporary,
            )
        elif kind == "session.bind":
            params(
                arguments,
                {"workspace_id", "expected_revision", "group", "pinned"},
                {"workspace_id", "expected_revision"},
            )
            self.authority.revoke(ref.session_id)
            self.automation_permissions.clear_session(ref.session_id)
            result = api.bind_session(identity, ref, **arguments)
        elif kind == "session.member":
            params(
                arguments,
                {"member_id", "role", "expected_revision"},
                {"member_id", "role", "expected_revision"},
            )
            result = api.set_member(identity, ref, **arguments)
            for run_id, candidate in tuple(self._refs.items()):
                if candidate == ref:
                    self.authority.revoke(ref.session_id)
                    self._handles[run_id].cancel()
        elif kind == "budget.configure":
            params(arguments, {"token_limit", "cost_limit", "currency", "expected_revision"})
            result = api.configure_budget(identity, ref, **arguments)
        elif kind == "message.settle":
            params(
                arguments,
                {"message_id", "status", "expected_revision"},
                {"message_id", "status", "expected_revision"},
            )
            result = api.settle_message(identity, ref, **arguments)
        elif kind == "message.revoke":
            params(
                arguments, {"message_id", "expected_revision"}, {"message_id", "expected_revision"}
            )
            result = api.revoke_message(identity, ref, **arguments)
        else:
            raise RpcError(-32602, "La acción de coordinación no existe.")
        return {"result": result}

    def product_admit_workspace(self, path):
        if type(path) is not str:
            raise RpcError(-32602, "Selecciona una carpeta explícita.")
        path = Path(path)
        try:
            private_part = path.relative_to(self.directory.parent).parts[0]
        except (ValueError, IndexError):
            private_part = ""
        if (
            path == self.directory
            or self.directory.is_relative_to(path)
            or (path.is_relative_to(self.directory) and path != self.directory / "workspace")
            or private_part.startswith(self.directory.name + ".memory-")
        ):
            raise RpcError(-32602, "El perfil privado no puede ser una carpeta de trabajo.")
        return WorkspaceTools(path)

    async def product_tool_start(self, value):
        params(
            value,
            BASE | {"tool", "arguments", "request_id"},
            {"session_id", "tool", "arguments", "request_id"},
        )
        ref, _, actor = self.product.context(value)
        allowed = {row.name for row in self.product_tools.definitions()}
        if value["tool"] not in allowed:
            raise RpcError(-32602, "Esta herramienta no pertenece al producto.")
        arguments = value["arguments"]
        if value["tool"] in {"github_pull_request", "github_publish"}:
            entry = self.product.github.get(actor.client_id)
            if entry is None:
                raise ProjectError("github_credential_required")
            arguments = {
                **arguments,
                **{key: entry[key] for key in ("owner", "repository", "login", "account_id")},
                "connection_id": entry["backend"].credentials.current().credential_id,
            }
        return await self.host_tools.start(
            ref,
            value["tool"],
            arguments,
            value["request_id"],
            guard=lambda: self.product.check_access(actor.client_id, ref, write=True),
            label="Operación del producto",
        )

    async def product_tool_result(self, value):
        params(value, BASE, {"session_id", "run_id"})
        ref, _, _ = self.product.context(value)
        return self.host_tools.result(ref, value["run_id"])
