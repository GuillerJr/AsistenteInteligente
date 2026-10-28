"""Bound extension lifecycle contributions, never executable package imports.

Context hooks are untrusted data. MCP hooks become exact Runtime proposals and
consume ordinary per-operation approvals. No hidden effect on install/update.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace

from .mcp_registry import ServerSpec
from .plugin_packages import PluginError
from .runtime import ToolProposal
from .tools import canonical_arguments


@dataclass
class HookTurn:
    fingerprint: str
    hooks: list
    excluded_results: set = field(default_factory=set)
    pending: tuple | None = None
    index: int = 0
    answer: object = None
    after_loaded: bool = False


class ExtensionHooks:
    def __init__(self, service):
        self.service = service
        self.turns = {}

    def _contributions(self, context, event):
        ref = self.service._refs[context.run_id]
        agent = self.service.store.get_session(ref).agent_id
        value = self.service.plugins.contributions(agent, event=event)
        hooks = []
        for plugin in value["plugins"]:
            plugin = {**plugin, "agent_id": agent}
            for hook in plugin["hooks"]:
                if "mcp_server_id" in hook:
                    hooks.append((plugin, hook))
        return value, hooks

    def binding(self, plugin, hook):
        # The configured endpoint/program must be exactly the reviewed package
        # descriptor. A matching peer name alone is never plugin provenance.
        descriptor = next(s for s in plugin["mcp"] if s["server_id"] == hook["mcp_server_id"])
        expected = ServerSpec(
            descriptor["server_id"],
            tuple(descriptor.get("command", [])),
            descriptor["protocol_version"],
            descriptor.get("transport", "stdio"),
            descriptor.get("endpoint"),
            descriptor.get("oauth"),
        )
        entry = self.service.mcp._entries.get(expected.server_id)
        if entry is None or entry.spec != expected or not self.service.mcp._connected(entry):
            raise PluginError("plugin_hook_server_not_connected")
        binding = next((b for b in entry.bindings if b.tool.name == hook["tool"]), None)
        if binding is None:
            raise PluginError("plugin_hook_tool_not_listed")
        self.service.mcp._assert_binding(binding)
        self.service.mcp._validate(binding, hook["arguments"])
        return binding

    def before(self, context):
        state = self.turns.get(context.run_id)
        current, hooks = self._contributions(context, "before_turn")
        if state is None:
            ref = self.service._refs[context.run_id]
            if hooks and self.service.store.get_run(ref, context.run_id).status == "queued":
                self.service.store.set_run_status(ref, context.run_id, "running")
            # Refuse a disconnected hook without running its code or silently
            # substituting another peer. Owner can disable the extension or
            # connect its separately reviewed descriptor.
            for plugin, hook in hooks:
                if not self.service.mcp.model_visible(self.binding(plugin, hook).public_name):
                    raise PluginError("plugin_hook_app_only_requires_explicit_invocation")
            state = self.turns[context.run_id] = HookTurn(current["fingerprint"], hooks)
        elif not state.after_loaded and state.fingerprint != current["fingerprint"]:
            raise PluginError("plugin_hook_context_changed")
        if state.pending is not None:
            pending, before_count = state.pending
            if len(context.tool_results) != before_count + 1:
                raise PluginError("plugin_hook_receipt_missing")
            result = context.tool_results[-1]
            if result.tool_name != pending.public_name:
                raise PluginError("plugin_hook_receipt_mismatch")
            state.excluded_results.add(result.operation_id)
            state.pending = None
            state.index += 1
            # A denied/uncertain hook is recorded once and not retried. The
            # model receives a bounded lifecycle observation, never a grant.
        if state.index < len(state.hooks):
            plugin, hook = state.hooks[state.index]
            binding = self.binding(plugin, hook)
            state.pending = (binding, len(context.tool_results))
            return ToolProposal(binding.public_name, hook["arguments"])
        return state.answer

    def after(self, context, answer):
        state = self.turns[context.run_id]
        if not state.after_loaded:
            _current, hooks = self._contributions(context, "after_turn")
            state.after_loaded = True
            state.answer = answer
            state.hooks += hooks
            for plugin, hook in hooks:
                self.binding(plugin, hook)
        if state.index < len(state.hooks):
            return self.before(context)
        return answer

    def is_hook(self, operation):
        state = self.turns.get(operation.run_id)
        if state is None or state.pending is None:
            return False
        binding, _count = state.pending
        plugin, hook = state.hooks[state.index]
        current = (
            self.service.plugins.get(plugin["agent_id"], plugin["plugin_id"])
            if ("agent_id" in plugin)
            else None
        )
        if current is not None and (
            not current.enabled
            or current.revision != plugin["revision"]
            or current.sha256 != plugin["sha256"]
        ):
            raise PluginError("plugin_hook_context_changed")
        self.binding(plugin, hook)
        return (
            operation.tool_name == binding.public_name
            and operation.arguments_json == canonical_arguments(hook["arguments"])
        )

    def model_context(self, context):
        state = self.turns.get(context.run_id)
        return replace(
            context,
            tool_results=tuple(
                r
                for r in context.tool_results
                if state is None or r.operation_id not in state.excluded_results
            ),
        )

    def clear(self, run_id):
        self.turns.pop(run_id, None)
