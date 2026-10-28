"""Native controls for remembered decisions and an explicitly chosen search URL."""

from __future__ import annotations

import ipaddress
import json
from urllib.parse import urlsplit

from jarvis_core.protocol import RpcError, authenticated_client
from jarvis_core.tools import current_tool_operation


class AutomationAPI:
    def automation_handlers(self):
        return {
            **self.native_broker.handlers(),
            "automation.permissions.list": self.automation_permissions_list,
            "automation.permissions.revoke": self.automation_permission_revoke,
            "automation.search.set": self.automation_search_set,
        }

    def _native_scope(self):
        operation = current_tool_operation()
        return operation.session_id, operation.run_id

    def _native_guard(self, run_id):
        self._assert_run_client(run_id)
        self.provider_choice.assert_current(self._provider_epochs[run_id])
        host = getattr(self, "host_tools", None)
        plan = host.get(run_id) if host is not None else None
        if plan is not None:
            plan.guard()
        else:
            self._adapter.bridge.assert_can_execute(run_id)

    def _permission_context(self, operation):
        ref = self._refs.get(operation.run_id)
        client = self._run_clients.get(operation.run_id)
        if ref is None or client is None:
            return None
        return {
            "client_id": client,
            "agent_id": self.store.get_session(ref).agent_id,
            "workspace_id": self._workspace_id,
            "session_id": ref.session_id,
            "temporary": ref.temporary,
            "tool": operation.tool_name,
            "arguments": json.loads(operation.arguments_json),
        }

    def _decide_remembered(self, operation, view):
        context = self._permission_context(operation)
        if context is None:
            return
        hooks = getattr(self, "extension_hooks", None)
        if hooks is not None and hooks.is_hook(operation):
            self._assert_run_client(operation.run_id)
            self.provider_choice.assert_current(self._provider_epochs[operation.run_id])
        else:
            self._native_guard(operation.run_id)
        rule = self.automation_permissions.match(**context)
        if rule is not None:
            self.store.append_event(
                self._refs[operation.run_id],
                operation.run_id,
                "approval.remembered",
                {
                    "permission_id": rule,
                    "operation_id": operation.operation_id,
                    "digest": operation.digest,
                    "tool_name": operation.tool_name,
                },
            )
            self.authority.decide(view.approval_id, True)

    async def automation_permissions_list(self, value):
        if value:
            raise RpcError(-32602, "La consulta de permisos no acepta parámetros.")
        self.client_authority.lookup(authenticated_client.get(), scope="owner")
        return self.automation_permissions.snapshot()

    async def automation_permission_revoke(self, value):
        if set(value) - {"id", "expected_revision", "expected_session_revision"} or {
            "id",
            "expected_revision",
        } - set(value):
            raise RpcError(-32602, "La revisión del permiso no es válida.")
        client = authenticated_client.get()
        if client is None:
            raise RpcError(-32003, "Se requiere identidad local.")
        data = self.automation_permissions.snapshot()
        row = next(
            (x for x in data["rules"] + data["volatile_rules"] if x["id"] == value["id"]), None
        )
        if row is None or row["client_id"] != client:
            raise RpcError(-32003, "Este cliente no posee el permiso.")
        if (
            type(value["expected_revision"]) is not int
            or value["expected_revision"] != data["revision"]
        ):
            raise RpcError(-32602, "La revisión del permiso cambió.")
        # Revocation invalidates issued leases even for an already connected worker.
        for run_id, owner in tuple(self._run_clients.items()):
            if owner == client:
                ref = self._refs.get(run_id)
                if ref is not None:
                    self.authority.revoke(ref.session_id)
                handle = self._handles.get(run_id)
                if handle is not None:
                    handle.cancel()
        self.native_broker.cancel_all()
        self.automation_permissions.revoke(
            value["id"], value["expected_revision"], value.get("expected_session_revision")
        )
        return {"revoked": True, "revision": self.automation_permissions.snapshot()["revision"]}

    async def automation_search_set(self, value):
        if set(value) != {"template", "expected_revision"}:
            raise RpcError(-32602, "La revisión del buscador no es válida.")
        self._management_idle()
        template = value["template"]
        if template is not None:
            if (
                not isinstance(template, str)
                or template.count("{query}") != 1
                or len(template.encode()) > 2048
                or "\0" in template
            ):
                raise RpcError(-32602, "La URL debe contener una sola variable {query}.")
            url = urlsplit(template.replace("{query}", "jarvis"))
            try:
                loopback = ipaddress.ip_address(url.hostname or "").is_loopback
            except ValueError:
                loopback = False
            if (
                (url.scheme != "https" and not (url.scheme == "http" and loopback))
                or not url.hostname
                or url.username is not None
                or url.password is not None
                or url.fragment
                or any(c.isspace() for c in template)
            ):
                raise RpcError(-32602, "El buscador necesita una URL HTTPS sin credenciales.")
        self.automation_permissions.set_search(template, value["expected_revision"])
        return self.automation_permissions.snapshot()
