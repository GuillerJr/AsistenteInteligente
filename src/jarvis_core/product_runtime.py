"""Session-bound workspaces; one catalogue and one authorization authority.

The properties below never change another task's current directory. A task gets
an immutable workspace selection, and identity/ACL are rechecked before effects.
"""

from __future__ import annotations

import hashlib
import json
from contextvars import ContextVar
from dataclasses import dataclass

from jarvis_core.processes import ProcessTools
from jarvis_core.runtime import ToolDefinition
from jarvis_core.workspace import WorkspaceError


@dataclass
class ProductWorkspace:
    workspace_id: str
    registry_id: str
    tools: object
    processes: object
    binding_revision: int

    @property
    def path(self):
        return self.tools.root


class SessionWorkspaceMixin:
    def initialize_workspace_context(self):
        self._product_workspace_context = ContextVar("jarvis_session_workspace", default=None)
        self._product_workspaces = {}
        self._run_workspaces = {}

    def _current_product_workspace(self):
        context = getattr(self, "_product_workspace_context", None)
        return context.get() if context is not None else None

    @property
    def _workspace_path(self):
        current = self._current_product_workspace()
        return current.path if current else self._default_workspace_path

    @_workspace_path.setter
    def _workspace_path(self, value):
        self._default_workspace_path = value

    @property
    def _workspace_id(self):
        current = self._current_product_workspace()
        return current.workspace_id if current else self._default_workspace_id

    @_workspace_id.setter
    def _workspace_id(self, value):
        self._default_workspace_id = value

    @property
    def workspace_tools(self):
        current = self._current_product_workspace()
        return current.tools if current else getattr(self, "_default_workspace_tools", None)

    @workspace_tools.setter
    def workspace_tools(self, value):
        self._default_workspace_tools = value

    @property
    def process_tools(self):
        current = self._current_product_workspace()
        return current.processes if current else getattr(self, "_default_process_tools", None)

    @process_tools.setter
    def process_tools(self, value):
        self._default_process_tools = value

    def workspace_definitions(self, sampled, *, process=False):
        """Keep schemas stable while resolving the executor in the run's task."""
        output = []
        for definition in sampled:
            name = definition.name

            def validate(arguments, name=name):
                target = self.process_tools if process else self.workspace_tools
                if target is None:
                    raise WorkspaceError("workspace_unavailable")
                selected = next(row for row in target.definitions() if row.name == name)
                return selected.validate(arguments)

            async def execute(arguments, cancellation, name=name):
                target = self.process_tools if process else self.workspace_tools
                if target is None:
                    raise WorkspaceError("workspace_unavailable")
                selected = next(row for row in target.definitions() if row.name == name)
                return await selected.execute(arguments, cancellation)

            output.append(
                ToolDefinition(
                    name, validate, execute, requires_confirmation=definition.requires_confirmation
                )
            )
        return tuple(output)

    def bind_product_workspace(self, client_id, ref):
        product = getattr(self, "product", None)
        if product is None or not product.enabled:
            return None
        try:
            binding = product.coordination.get_session(client_id, ref)
        except product.CoordinationError as error:
            if error.code == "coordination_session_unbound":
                return None
            raise
        tools = product.coordination.workspace_tools(client_id, ref)
        key = (binding["workspace_id"], tools.identity)
        runtime = self._product_workspaces.get(key)
        if runtime is None:
            processes = ProcessTools(tools, lambda: self._active_process_scope())
            processes.child_custody = self.profile_lease.child_custody_fds
            physical_scope = hashlib.sha256(
                json.dumps([str(tools.root), *tools.identity]).encode()
            ).hexdigest()
            runtime = ProductWorkspace(
                physical_scope, binding["workspace_id"], tools, processes, binding["revision"]
            )
            self._product_workspaces[key] = runtime
        # A binding revision is per session, never shared mutable cache state.
        return ProductWorkspace(
            runtime.workspace_id,
            runtime.registry_id,
            runtime.tools,
            runtime.processes,
            binding["revision"],
        )

    def activate_product_run(self, run_id):
        current = self._run_workspaces.get(run_id)
        if current is not None:
            self._product_workspace_context.set(current)

    async def close_product_workspaces(self):
        for workspace in tuple(self._product_workspaces.values()):
            await workspace.processes.aclose()
        self._product_workspaces.clear()
        self._run_workspaces.clear()
