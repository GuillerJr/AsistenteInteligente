"""Exact native/extension requests use the ordinary Runtime and approval lease.

Only this authenticated host constructs plans. External JSON never becomes a
provider step, a system message or an approval. Results are bounded RAM views;
the durable action receipt remains authoritative after a lost IPC response.
"""

from __future__ import annotations

import hashlib
import json
import re
import time
from collections.abc import Callable
from contextvars import ContextVar
from dataclasses import dataclass

from .protocol import RpcError, authenticated_client
from .runtime import FinalAnswer, RunBudget, ToolProposal
from .tools import canonical_arguments


@dataclass
class HostPlan:
    run_id: str
    ref: object
    owner: str
    proposal: ToolProposal
    guard: Callable
    digest: str
    expires: float
    result: object = None
    payload: dict | None = None
    settled: bool = False


class HostToolRuns:
    def __init__(self, service):
        self.service = service
        self.plans: dict[str, HostPlan] = {}
        self.pending = ContextVar("jarvis_host_exact_plan", default=None)

    def get(self, run_id):
        return self.plans.get(run_id)

    async def start(self, ref, name, arguments, request_id, *, guard, label):
        if not isinstance(request_id, str) or re.fullmatch(r"[a-f0-9]{32}", request_id) is None:
            raise RpcError(-32602, "La identidad de la operación no es válida.")
        owner = authenticated_client.get()
        if owner is None:
            raise RpcError(-32003, "Se requiere un cliente dueño autenticado.")
        self.service.client_authority.lookup(owner, scope="owner")
        snapshot = canonical_arguments(arguments)
        identity = hashlib.sha256(
            json.dumps([name, snapshot], ensure_ascii=False).encode()
        ).hexdigest()
        previous = self.plans.get(request_id)
        if previous is not None:
            self.assert_owner(previous, ref)
            if previous.digest != identity:
                raise RpcError(-32602, "Esa identidad ya corresponde a otra operación.")
            return {
                "run_id": request_id,
                "status": self.service._run_value(ref, request_id)["status"],
            }
        for key, plan in tuple(self.plans.items()):
            if plan.settled and plan.expires < time.monotonic():
                self.plans.pop(key)
        product = self.service.mcp_product
        active_sources = {a.document.get("source_run_id") for a in product.apps.values()}
        product.app_sources = {key: source for key, source in product.app_sources.items()
                               if key in self.plans or key in active_sources}
        if len(self.plans) >= 64:
            raise RpcError(-32011, "Hay demasiados resultados MCP pendientes de cerrar.")
        guard()
        plan = HostPlan(
            request_id,
            ref,
            owner,
            ToolProposal(name, json.loads(snapshot)),
            guard,
            identity,
            time.monotonic() + 600,
        )
        token = self.pending.set(plan)
        offered = self.service._run_offered_tools.set((name,))
        budget = self.service._run_budget.set(RunBudget(2, 180))
        try:
            return await self.service.start_run(
                {
                    "session_id": ref.session_id,
                    "temporary": ref.temporary,
                    "message": f"{label} · {identity}",
                    "request_id": request_id,
                }
            )
        finally:
            self.pending.reset(token)
            self.service._run_offered_tools.reset(offered)
            self.service._run_budget.reset(budget)

    def attach(self, run_id):
        plan = self.pending.get()
        if plan is not None:
            if plan.run_id != run_id:
                raise RuntimeError("host_plan_identity_changed")
            self.plans[run_id] = plan

    def assert_owner(self, plan, ref=None):
        if authenticated_client.get() != plan.owner or (ref is not None and ref != plan.ref):
            raise RpcError(-32003, "El resultado pertenece a otro cliente o conversación.")
        self.service.client_authority.lookup(plan.owner, scope="owner")

    def next_step(self, context):
        plan = self.plans[context.run_id]
        self.service._assert_run_client(context.run_id)
        plan.guard()
        if self.service.store.get_run(plan.ref, context.run_id).status == "queued":
            self.service.store.set_run_status(plan.ref, context.run_id, "running")
        if not context.tool_results:
            return plan.proposal
        return FinalAnswer("Operación terminada. Consulta el recibo de la acción.", source="host")

    def capture(self, context, result):
        plan = self.plans.get(context.run_id)
        if plan is not None:
            plan.result = result
            if plan.payload is None and result.output:
                try:
                    plan.payload = json.loads(result.output)
                except ValueError:
                    pass

    def settle(self, run_id):
        plan = self.plans.get(run_id)
        if plan is not None:
            plan.settled = True

    def result(self, ref, run_id):
        plan = self.plans.get(run_id)
        if plan is None:
            # Inspect the existing receipt; absence of a volatile view NEVER
            # permits replay, including a previous service epoch.
            self.service.store.get_run(ref, run_id)
            return {
                "settled": True,
                "payload": None,
                "error": "volatile_result_expired",
                "provenance": "external_unverified",
            }
        self.assert_owner(plan, ref)
        if plan.expires < time.monotonic():
            return {
                "settled": plan.settled,
                "payload": None,
                "error": "volatile_result_expired",
                "provenance": "external_unverified",
            }
        error = plan.result.error if plan.result is not None else None
        return {
            "settled": plan.settled,
            "payload": plan.payload,
            "error": error,
            "provenance": "external_unverified",
        }
