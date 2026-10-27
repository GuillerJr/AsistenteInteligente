"""Own bounded coordination on the same service/runtime and approval authority.

The durable ledger is made of typed events on the parent run and ordinary child
sessions/runs. It needs no parallel database or restore-time auto continuation.
Child model text is a report, never independent verification or an authorization.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import re
import time
from collections.abc import Mapping
from contextvars import ContextVar
from dataclasses import dataclass, field
from uuid import uuid4

from jarvis_core.host_profile import profile_background
from jarvis_core.protocol import RpcError, authenticated_client
from jarvis_core.runtime import (
    RunBudget,
    RunContext,
    RunHandle,
    RunPhase,
    RunResult,
    ToolDefinition,
    ToolResult,
)
from jarvis_core.state import SessionRef, StateError
from jarvis_core.tools import ToolSpec, check_tool_authority

_TERMINAL = frozenset({"completed", "failed", "cancelled", "interrupted"})
_BASE = {"session_id", "temporary", "parent_run_id", "goal_id"}
_SPEC = {"objective", "max_steps", "timeout_seconds", "children"}


def _params(value, allowed, required=()):
    if not isinstance(value, Mapping) or set(value) - set(allowed) or set(required) - set(value):
        raise RpcError(-32602, "Los parámetros de coordinación no son válidos.")


def _identity(value):
    if not isinstance(value, str) or re.fullmatch(r"[a-f0-9]{32}", value) is None:
        raise RpcError(-32602, "La identidad de coordinación no es válida.")
    return value


def _text(value, maximum):
    if (not isinstance(value, str) or not value.strip() or "\0" in value
            or len(value.encode()) > maximum):
        raise RpcError(-32602, "El texto de coordinación supera su límite.")
    return value


def _integer(value, minimum, maximum):
    if type(value) is not int or not minimum <= value <= maximum:
        raise RpcError(-32602, "El presupuesto de coordinación no es válido.")
    return value


def _digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                                    separators=(",", ":")).encode()).hexdigest()


def _prefix(text, limit):
    return text.encode()[:limit].decode("utf-8", errors="ignore")


def _bounded_report(run):
    """A snapshot is a bounded preview; original run receipts remain authoritative."""
    answer = run["answer"]
    receipts = run["action_receipts"]
    selected = []
    for receipt in receipts[:8]:
        item = dict(receipt)
        item["output"] = _prefix(item["output"], 128)
        item["output_truncated"] = (receipt.get("output_truncated", False)
                                    or item["output"] != receipt["output"])
        if item.get("error") is not None:
            item["error"] = _prefix(item["error"], 256)
            item["error_truncated"] = item["error"] != receipt["error"]
        selected.append(item)
    return {"answer": None if answer is None else _prefix(answer, 2048),
            "answer_source": run["answer_source"],
            "answer_truncated": answer is not None and len(answer.encode()) > 2048,
            "answer_bytes": len(answer.encode()) if answer is not None else 0,
            "answer_sha256": hashlib.sha256(answer.encode()).hexdigest() if answer else None,
            "action_receipts": selected, "action_receipts_total": len(receipts),
            "action_receipts_truncated": len(receipts) > len(selected),
            "unknown_effects": any(row.get("effect_status") == "unknown" for row in receipts)}


@dataclass
class _Goal:
    ref: SessionRef
    parent_run_id: str
    goal_id: str
    spec: dict
    created_ns: int
    status: str = "running"
    error_code: str | None = None
    children: list[dict] = field(default_factory=list)
    worker: asyncio.Task | None = None
    changed: asyncio.Event = field(default_factory=asyncio.Event)


class DelegationTools:
    def __init__(self, service):
        self.service = service

    def definitions(self):
        return (ToolDefinition("agents_available", self.validate_available, self.available),
                ToolDefinition("agents_delegate", self.validate, self.execute, True))

    @staticmethod
    def validate_available(arguments):
        _params(arguments, set())

    async def available(self, arguments, cancellation):
        self.validate_available(arguments)
        if cancellation.is_set():
            raise asyncio.CancelledError
        if self.service._active_ref.get() is None:
            raise ValueError("agent_catalog_run_scope_required")
        profiles = [{"agent_id": row.agent_id, "name": row.name,
                     "revision": row.revision} for row in self.service.agent_profiles.list()]
        return ToolResult("agents_available", True, False,
                          output=json.dumps({"agents": profiles, "grants_included": False,
                                             "instructions_included": False}, ensure_ascii=False))

    @staticmethod
    def validate(arguments):
        _validated_spec(arguments)

    def specs(self):
        return (ToolSpec("agents_available",
            "Consulta los IDs y revisiones actuales de perfiles locales elegibles para una "
            "delegación. Devuelve etiquetas, no instrucciones privadas ni permisos. "
            "Consulta este catálogo antes de proponer agents_delegate.",
            {"type": "object", "properties": {}, "additionalProperties": False}),
                ToolSpec("agents_delegate",
            "Delega hasta cuatro tareas a perfiles locales con un objetivo y presupuesto "
            "explícitos. Requiere aprobación exacta. Los hijos usan sesiones nuevas, "
            "sin historial ni permisos heredados; cada efecto necesita su propia aprobación. "
            "Espera sus recibos y devuelve reportes del modelo, no verificación independiente. "
            "No admite delegación recursiva, envíos externos ni reanudación automática.",
            {"type": "object", "properties": {
                "objective": {"type": "string", "maxLength": 512},
                "max_steps": {"type": "integer", "minimum": 1, "maximum": 100},
                "timeout_seconds": {"type": "integer", "minimum": 1, "maximum": 3600},
                "children": {"type": "array", "minItems": 1, "maxItems": 4,
                    "items": {"type": "object", "properties": {
                        "agent_id": {"type": "string", "maxLength": 64},
                        "expected_revision": {"type": "integer", "minimum": 1},
                        "message": {"type": "string", "maxLength": 8000},
                        "max_steps": {"type": "integer", "minimum": 1, "maximum": 32}},
                        "required": ["agent_id", "expected_revision", "message", "max_steps"],
                        "additionalProperties": False}}},
             "required": sorted(_SPEC), "additionalProperties": False}),)

    async def execute(self, arguments, cancellation):
        service = self.service
        check_tool_authority("agents_delegate", arguments)
        ref = service._active_ref.get()
        if ref is None:
            raise ValueError("delegation_parent_required")
        # The authority already binds the run. Resolve only that currently active
        # run for this session; a caller-provided routing ID is never authority.
        parents = [identity for identity, item in service._refs.items() if item == ref
                   and service._handles[identity].phase is RunPhase.RUNNING]
        if len(parents) != 1 or parents[0] in service._delegated_child_runs:
            raise ValueError("delegation_parent_not_current")
        spec = service._validate_goal_spec(arguments)
        parent_run_id = parents[0]
        check_tool_authority("agents_delegate", arguments, commit=True)
        goal = service._new_goal(ref, parent_run_id, uuid4().hex, spec)
        try:
            await service._execute_goal(goal, cancellation)
            snapshot = service._goal_snapshot(ref, parent_run_id, goal.goal_id)
            uncertain = any(child["unknown_effects"] for child in snapshot["children"])
            return ToolResult("agents_delegate",
                              snapshot["status"] == "completed" and not uncertain, False,
                              output=json.dumps(snapshot, ensure_ascii=False),
                              error=("delegation_child_effect_unknown" if uncertain
                                     else snapshot["error_code"]),
                              effect_status="unknown" if uncertain else "known")
        finally:
            service._active_goals.pop(goal.goal_id, None)


def _validated_spec(value):
    _params(value, _SPEC, _SPEC)
    objective = _text(value["objective"], 512)
    maximum = _integer(value["max_steps"], 1, 100)
    timeout = _integer(value["timeout_seconds"], 1, 3600)
    children = value["children"]
    if not isinstance(children, (list, tuple)) or not 1 <= len(children) <= 4:
        raise RpcError(-32602, "Elige entre una y cuatro tareas delegadas.")
    rows = []
    for child in children:
        _params(child, {"agent_id", "expected_revision", "message", "max_steps"},
                {"agent_id", "expected_revision", "message", "max_steps"})
        agent = child["agent_id"]
        if not isinstance(agent, str) or re.fullmatch(r"[a-z][a-z0-9_-]{0,63}", agent) is None:
            raise RpcError(-32602, "El agente delegado no es válido.")
        rows.append({"agent_id": agent,
                     "expected_revision": _integer(child["expected_revision"], 1, 2**52 - 1),
                     "message": _text(child["message"], 8000),
                     "max_steps": _integer(child["max_steps"], 1, 32)})
    if sum(row["max_steps"] for row in rows) > maximum:
        raise RpcError(-32602, "Las tareas exceden el presupuesto total de pasos.")
    return {"objective": objective, "max_steps": maximum,
            "timeout_seconds": timeout, "children": rows}


class DelegationAPI:
    def initialize_delegation(self):
        self._active_goals: dict[str, _Goal] = {}
        self._delegated_child_runs: set[str] = set()
        self._run_budget = ContextVar("jarvis_run_budget", default=None)
        self._run_offered_tools = ContextVar("jarvis_run_tools", default=None)
        self._allow_enqueue = ContextVar("jarvis_run_enqueue", default=False)
        self.delegation_tools = DelegationTools(self)

    def delegation_handlers(self):
        return {"delegations.start": self.start_delegation,
                "delegations.get": self.get_delegation,
                "delegations.lookup": self.lookup_delegation,
                "delegations.list": self.list_delegations,
                "delegations.wait": self.wait_delegation,
                "delegations.cancel": self.cancel_delegation,
                "delegations.steer": self.steer_delegation,
                "runs.steer": self.steer_run,
                "runs.enqueue": self.enqueue_run}

    def _validate_goal_spec(self, value):
        spec = _validated_spec(value)
        if len(self._active_goals) >= 2:
            raise RpcError(-32011, "Espera a que termine uno de los dos objetivos activos.")
        for row in spec["children"]:
            profile = self.agent_profiles.get(row["agent_id"])
            if profile.revision != row["expected_revision"]:
                raise RpcError(-32012, "El perfil del agente cambió. Revisa la tarea otra vez.")
        return spec

    def _new_goal(self, ref, parent_run_id, goal_id, spec):
        if goal_id in self._active_goals:
            raise RpcError(-32012, "La identidad del objetivo ya está en uso.")
        goal = _Goal(ref, parent_run_id, goal_id, spec, time.time_ns())
        self.store.append_event(ref, parent_run_id, "coordination.created", {
            "schema": "jarvis.coordination.v1", "goal_id": goal_id,
            "objective": spec["objective"], "max_steps": spec["max_steps"],
            "timeout_seconds": spec["timeout_seconds"], "created_ns": goal.created_ns,
            "request_sha256": _digest(spec),
            "reserved_steps": sum(row["max_steps"] for row in spec["children"]),
            "child_count": len(spec["children"])})
        self._active_goals[goal_id] = goal
        return goal

    async def start_delegation(self, value):
        _params(value, {"session_id", "temporary", "request_id"} | _SPEC,
                {"session_id", "request_id"} | _SPEC)
        ref = self._ref(value)
        identity = _identity(value["request_id"])
        if identity in self._refs and self._refs[identity] != ref:
            raise RpcError(-32012, "La identidad de solicitud ya está en uso.")
        # Stable owner request ID survives a lost response and a host restart.
        # A repeat only reads the original ledger; it never starts a second job.
        try:
            previous = self.store.get_run(ref, identity)
        except StateError as error:
            if error.code != "run_not_found":
                raise
        else:
            snapshot = self._goal_snapshot(ref, previous.run_id, identity)
            if self._goal_creation(ref, identity, identity)["request_sha256"] != _digest(
                    _validated_spec({key: value[key] for key in _SPEC})):
                raise RpcError(-32012, "La solicitud ya corresponde a otro objetivo.")
            return {"goal": snapshot}
        if self._closing or self._maintenance or self._provider_transition is not None:
            raise RpcError(-32011, "Espera a que el servicio termine su operación actual.")
        if self._busy(ref):
            raise RpcError(-32011, "Espera al cierre de esta conversación antes de delegar.")
        spec = self._validate_goal_spec({key: value[key] for key in _SPEC})
        self.provider_choice.assert_current()
        epoch = self.provider_choice.epoch()
        writer = self.profile_gate.hold("coordination.owner", mutating=True, closing=True)
        created = False
        try:
            self.store.create_run(ref, run_id=identity, user_text=spec["objective"])
            created = True
            self.store.set_run_status(ref, identity, "running")
            goal = self._new_goal(ref, identity, identity, spec)
        except BaseException:
            if created:
                # A lost SQL acknowledgement is not permission to admit another
                # run over an unowned queued record. Restart records interruption.
                self._closing = True
            writer.close()
            raise
        handle = RunHandle(RunContext(identity, ref.session_id, spec["objective"], ()))
        self._run_writers[identity] = writer
        self._refs[identity] = ref
        self._provider_epochs[identity] = epoch
        self._run_clients[identity] = authenticated_client.get()
        self._handles[identity] = handle
        handle._task = asyncio.create_task(self._run_goal_handle(goal, handle))
        goal.worker = handle._task
        observer = asyncio.create_task(self._settle(ref, handle))
        self._observers.add(observer)
        self._settlers[identity] = observer
        observer.add_done_callback(self._observers.discard)
        observer.add_done_callback(lambda _task: self._settlers.pop(identity, None))
        return {"goal": self._goal_snapshot(ref, identity, identity)}

    @profile_background("coordination.execution")
    async def _run_goal_handle(self, goal, handle):
        handle._entered = True
        handle._phase = RunPhase.RUNNING
        result = RunResult(handle.run_id, "failed", None, (), "coordination_failed")
        try:
            await self._execute_goal(goal, handle._cancellation)
            result = RunResult(handle.run_id, goal.status, None, (), goal.error_code)
        except asyncio.CancelledError:
            result = RunResult(handle.run_id, "cancelled", None, ())
        except Exception:
            result = RunResult(handle.run_id, "failed", None, (), "coordination_failed")
        finally:
            self._active_goals.pop(goal.goal_id, None)
            handle._phase = RunPhase.SETTLED
            handle._settled.set_result(result)

    async def _execute_goal(self, goal, cancellation):
        deadline = asyncio.get_running_loop().time() + goal.spec["timeout_seconds"]
        try:
            for index, row in enumerate(goal.spec["children"]):
                if cancellation.is_set():
                    raise asyncio.CancelledError
                self._assert_run_client(goal.parent_run_id)
                if self.agent_profiles.get(row["agent_id"]).revision != row["expected_revision"]:
                    raise RpcError(-32012, "El agente delegado cambió antes de iniciar.")
                child = self.store.create_session(
                    "Tarea " + str(index + 1) + ": " + goal.spec["objective"][:80],
                    temporary=goal.ref.temporary, agent_id=row["agent_id"],
                    parent_session_id=goal.ref.session_id)
                link = {"index": index, "agent_id": row["agent_id"],
                        "agent_revision": row["expected_revision"],
                        "session_id": child.ref.session_id, "run_id": uuid4().hex,
                        "max_steps": row["max_steps"]}
                goal.children.append(link)
                self.store.append_event(goal.ref, goal.parent_run_id, "coordination.child", {
                    "goal_id": goal.goal_id, **link,
                    "message_sha256": hashlib.sha256(row["message"].encode()).hexdigest()})
                remaining = deadline - asyncio.get_running_loop().time()
                if remaining <= 0:
                    raise TimeoutError
                budget_token = self._run_budget.set(RunBudget(row["max_steps"], remaining))
                tool_token = self._run_offered_tools.set(tuple(
                    spec["name"] for spec in self.catalog.specs()
                    if spec["name"] != "agents_delegate"))
                self._delegated_child_runs.add(link["run_id"])
                try:
                    await self.start_run({"session_id": child.ref.session_id,
                                          "temporary": child.ref.temporary,
                                          "message": row["message"],
                                          "request_id": link["run_id"]})
                finally:
                    self._run_budget.reset(budget_token)
                    self._run_offered_tools.reset(tool_token)
                goal.changed.set()
            await self._wait_children(goal, deadline)
            snapshot = self._goal_snapshot(goal.ref, goal.parent_run_id, goal.goal_id)
            goal.status = ("completed" if all(row["status"] == "completed"
                                            for row in snapshot["children"]) else "failed")
            if goal.status == "failed":
                goal.error_code = "coordination_child_failed"
        except asyncio.CancelledError:
            goal.status = "cancelled"
            goal.error_code = "coordination_cancelled"
            raise
        except TimeoutError:
            goal.status = "failed"
            goal.error_code = "coordination_deadline"
        except Exception:
            goal.status = "failed"
            goal.error_code = "coordination_failed"
            raise
        finally:
            final_status = goal.status
            if final_status != "completed":
                goal.status = "cancelling"
                for row in goal.children:
                    handle = self._handles.get(row["run_id"])
                    if handle is not None:
                        handle.cancel()
            # No early release of the parent's ownership, even if another Stop
            # arrives while a child is finalizing or its SQL receipt is pending.
            cleanup = asyncio.create_task(self._wait_children(goal, None))
            while not cleanup.done():
                try:
                    await asyncio.shield(cleanup)
                except asyncio.CancelledError:
                    final_status = "cancelled"
                    goal.error_code = "coordination_cancelled"
            cleanup.result()
            goal.status = final_status
            self.store.append_event(goal.ref, goal.parent_run_id, "coordination.finished", {
                "goal_id": goal.goal_id, "status": goal.status,
                "error_code": goal.error_code, "permits_replay": False})
            for row in goal.children:
                self._delegated_child_runs.discard(row["run_id"])
            goal.changed.set()

    async def _wait_children(self, goal, deadline):
        for row in goal.children:
            handle = self._handles.get(row["run_id"])
            if handle is None:
                continue
            ref = SessionRef(row["session_id"], goal.ref.temporary)
            async with asyncio.timeout_at(deadline):
                receipt = await self._await_run_receipt(ref, handle)
                if receipt is None:
                    raise StateError("coordination_child_receipt_unconfirmed")
            goal.changed.set()

    def _goal_events(self, ref, parent_run_id):
        records, after = [], 0
        while True:
            page = self.store.list_events(ref, parent_run_id, after=after, limit=500)
            records.extend((row.kind, json.loads(row.payload_json)) for row in page
                           if row.kind.startswith("coordination."))
            if len(page) < 500:
                return records
            after = page[-1].sequence

    def _goal_creation(self, ref, parent_run_id, goal_id):
        _identity(parent_run_id)
        _identity(goal_id)
        created = [payload for kind, payload in self._goal_events(ref, parent_run_id)
                   if kind == "coordination.created" and payload.get("goal_id") == goal_id]
        if len(created) != 1:
            raise RpcError(-32012, "El objetivo no pertenece a esta ejecución.")
        return created[0]

    def _goal_snapshot(self, ref, parent_run_id, goal_id):
        created = self._goal_creation(ref, parent_run_id, goal_id)
        events = self._goal_events(ref, parent_run_id)
        parent = self.store.get_run(ref, parent_run_id)
        finished = [payload for kind, payload in events if kind == "coordination.finished"
                    and payload.get("goal_id") == goal_id]
        goal = self._active_goals.get(goal_id)
        status = (goal.status if goal else finished[-1]["status"] if finished else
                  "interrupted" if parent.status in _TERMINAL else "unknown")
        children = []
        for kind, row in events:
            if kind != "coordination.child" or row.get("goal_id") != goal_id:
                continue
            child_ref = SessionRef(row["session_id"], ref.temporary)
            session = self.store.get_session(child_ref)
            if session.parent_session_id != ref.session_id or session.agent_id != row["agent_id"]:
                raise RpcError(-32012, "La identidad del hijo ya no coincide con su objetivo.")
            try:
                run = self._run_value(child_ref, row["run_id"])
                outcome = self.store.get_run(child_ref, row["run_id"]).outcome
            except StateError as error:
                if error.code != "run_not_found":
                    raise
                run = {"status": "interrupted", "answer": None, "answer_source": None,
                       "action_receipts": []}
                outcome = "unknown"
            handle = self._handles.get(row["run_id"])
            children.append({key: row[key] for key in (
                "index", "agent_id", "agent_revision", "session_id", "run_id", "max_steps")}
                | {"status": run["status"], "outcome": outcome,
                   "phase": handle.phase.value if handle else "settled",
                   "settled": run["status"] in _TERMINAL} | _bounded_report(run))
            if run["status"] == "interrupted":
                children[-1]["unknown_effects"] = True
        result = {"goal_id": goal_id, "parent_session_id": ref.session_id,
                "parent_run_id": parent_run_id, "temporary": ref.temporary,
                "objective": created["objective"], "status": status,
                "max_steps": created["max_steps"], "reserved_steps": created["reserved_steps"],
                "timeout_seconds": created["timeout_seconds"], "created_ns": created["created_ns"],
                "children": children, "error_code": (
                    goal.error_code if goal else finished[-1].get("error_code") if finished
                    else "coordination_interrupted" if status == "interrupted" else None),
                "parent_settled": parent.status in _TERMINAL,
                "parent_outcome": parent.outcome,
                "settled": parent.status in _TERMINAL and all(
                    row["settled"] for row in children),
                "permits_replay": False, "snapshot_truncated": any(
                    row["answer_truncated"] or row["action_receipts_truncated"]
                    or any(receipt.get("output_truncated") for receipt in row["action_receipts"])
                    for row in children)}
        if len(json.dumps(result, ensure_ascii=True).encode()) > 65536:
            for child in children:
                child["action_receipts_truncated"] = bool(child["action_receipts_total"])
                child["action_receipts"] = []
            result["snapshot_truncated"] = True
        return result

    async def get_delegation(self, value):
        _params(value, _BASE, {"session_id", "parent_run_id", "goal_id"})
        return {"goal": self._goal_snapshot(self._ref(value), value["parent_run_id"],
                                            value["goal_id"])}

    async def list_delegations(self, value):
        _params(value, {"session_id", "temporary", "offset", "limit"}, {"session_id"})
        ref = self._ref(value)
        offset = _integer(value.get("offset", 0), 0, 1000000)
        limit = _integer(value.get("limit", 20), 1, 100)
        records = self.store.list_coordination_events(ref, limit=limit, offset=offset)
        goals = []
        for row in records:
            created = json.loads(row.payload_json)
            snapshot = self._goal_snapshot(ref, row.run_id, created["goal_id"])
            # A list is a light preview, with details available by exact get.
            for child in snapshot["children"]:
                if child["answer"] is not None:
                    child["answer"] = _prefix(child["answer"], 256)
                    child["answer_truncated"] = child["answer_bytes"] > len(
                        child["answer"].encode())
                child["action_receipts"] = []
                child["action_receipts_truncated"] = bool(child["action_receipts_total"])
            snapshot["snapshot_truncated"] = any(
                child["answer_truncated"] or child["action_receipts_truncated"]
                for child in snapshot["children"])
            if goals and len(json.dumps([*goals, snapshot], ensure_ascii=True).encode()) > 524288:
                break
            goals.append(snapshot)
        return {"goals": goals, "has_more": len(records) == limit or len(goals) < len(records),
                "next_offset": offset + len(goals)}

    async def lookup_delegation(self, value):
        _params(value, {"session_id", "temporary", "request_id"}, {"session_id", "request_id"})
        ref, identity = self._ref(value), _identity(value["request_id"])
        try:
            self.store.get_run(ref, identity)
        except StateError as error:
            if error.code != "run_not_found":
                raise
            goal = None
        else:
            goal = self._goal_snapshot(ref, identity, identity)
        return {"goal": goal, "permits_replay": False, "historical_ack_established": False}

    async def wait_delegation(self, value):
        _params(value, _BASE | {"timeout_seconds"}, {"session_id", "parent_run_id", "goal_id"})
        timeout = _integer(value.get("timeout_seconds", 0), 0, 30)
        ref = self._ref(value)
        snapshot = self._goal_snapshot(ref, value["parent_run_id"], value["goal_id"])
        goal = self._active_goals.get(snapshot["goal_id"])
        if goal is not None and timeout and snapshot["status"] not in _TERMINAL:
            goal.changed.clear()
            try:
                async with asyncio.timeout(min(timeout, 25)):
                    await goal.changed.wait()
            except TimeoutError:
                pass
            snapshot = self._goal_snapshot(ref, value["parent_run_id"], value["goal_id"])
        return {"goal": snapshot}

    async def cancel_delegation(self, value):
        _params(value, _BASE, {"session_id", "parent_run_id", "goal_id"})
        ref = self._ref(value)
        snapshot = self._goal_snapshot(ref, value["parent_run_id"], value["goal_id"])
        goal = self._active_goals.get(snapshot["goal_id"])
        accepted = False
        if goal is not None:
            handle = self._handles.get(goal.parent_run_id)
            accepted = handle.cancel().accepted if handle else False
            if accepted:
                goal.status = "cancelling"
                self.store.set_run_status(ref, goal.parent_run_id, "cancelling")
            goal.changed.set()
        return {"goal_id": snapshot["goal_id"], "accepted": accepted,
                "settled": snapshot["settled"]}

    async def steer_delegation(self, value):
        _params(value, _BASE | {"child_run_id", "request_id", "message"},
                {"session_id", "parent_run_id", "goal_id", "child_run_id", "request_id", "message"})
        ref = self._ref(value)
        snapshot = self._goal_snapshot(ref, value["parent_run_id"], value["goal_id"])
        matches = [row for row in snapshot["children"] if row["run_id"] == value["child_run_id"]]
        if len(matches) != 1:
            raise RpcError(-32012, "El hijo no pertenece a este objetivo.")
        return await self.steer_run({
            "session_id": matches[0]["session_id"], "temporary": ref.temporary,
            "run_id": value["child_run_id"], "request_id": value["request_id"],
            "message": value["message"]})

    async def steer_run(self, value):
        _params(value, {"session_id", "temporary", "run_id", "request_id", "message"},
                {"session_id", "run_id", "request_id", "message"})
        ref = self._ref(value)
        run_id, identity = _identity(value["run_id"]), _identity(value["request_id"])
        message = _text(value["message"], 8000)
        digest = hashlib.sha256(message.encode()).hexdigest()
        events, after = [], 0
        while True:
            page = self.store.list_events(ref, run_id, after=after, limit=500)
            events.extend(page)
            if len(page) < 500:
                break
            after = page[-1].sequence
        for row in events:
            payload = json.loads(row.payload_json)
            if row.kind == "steering.intent" and payload.get("request_id") == identity:
                if payload.get("message_sha256") != digest:
                    raise RpcError(-32012, "La corrección ya corresponde a otro texto.")
                return {"run_id": run_id, "request_id": identity, "status": "unknown",
                        "permits_replay": False}
        handle = self._handles.get(run_id)
        session = self.store.get_session(ref)
        if (handle is None or self._refs.get(run_id) != ref
                or handle.phase is not RunPhase.RUNNING or handle._cancellation.is_set()):
            raise RpcError(-32011, "La ejecución no admite una corrección ahora.")
        self._assert_run_client(run_id)
        epoch = self._provider_epochs[run_id]
        self.provider_choice.assert_current(epoch)
        thread = session.provider_thread_id
        turn = self.provider.active_turn_id(thread) if thread and hasattr(
            self.provider, "active_turn_id") else None
        if not turn or not hasattr(self.provider, "steer"):
            raise RpcError(-32011, "El proveedor no tiene un turno activo para corregir.")
        self.store.append_event(ref, run_id, "steering.intent", {
            "request_id": identity, "message_sha256": digest, "message": message,
            "thread_id": thread, "turn_id": turn, "permits_replay": False})
        status = "unknown"
        token = self._provider_context.set(epoch)
        client_token = authenticated_client.set(self._run_clients.get(run_id))
        try:
            result = await self.provider.steer(thread, turn, message, request_id=identity)
            if result == {"turn_id": turn, "accepted": True}:
                status = "accepted"
        except Exception:
            pass
        finally:
            authenticated_client.reset(client_token)
            self._provider_context.reset(token)
        # A model can finish while the ACK is in flight. Its immutable terminal
        # receipt stays untouched; the retained intent is then conservatively unknown.
        try:
            self.store.append_event(ref, run_id, "steering.receipt", {
                "request_id": identity, "status": status, "permits_replay": False})
        except StateError:
            status = "unknown"
        return {"run_id": run_id, "request_id": identity, "status": status,
                "permits_replay": False}

    async def enqueue_run(self, value):
        _params(value, {"session_id", "temporary", "request_id", "message"},
                {"session_id", "request_id", "message"})
        ref = self._ref(value)
        _identity(value["request_id"])
        _text(value["message"], 32000)
        try:
            self.store.get_run(ref, value["request_id"])
            existing = True
        except StateError as error:
            if error.code != "run_not_found":
                raise
            existing = False
        predecessors = [identity for identity, previous in self._refs.items() if previous == ref]
        active = [handle for identity, handle in self._handles.items()
                  if self._refs.get(identity) == ref and handle.phase is RunPhase.QUEUED]
        if len(active) >= 4 or len(self._handles) >= 32:
            raise RpcError(-32011, "La cola está llena; espera o cancela una entrada.")
        token = self._allow_enqueue.set(True)
        try:
            result = await self.start_run(dict(value))
            if not existing:
                try:
                    self.store.append_event(ref, result["run_id"], "queue.enqueued", {
                        "predecessors": predecessors, "permits_replay": False})
                except BaseException:
                    handle = self._handles.get(result["run_id"])
                    if handle is not None:
                        handle.cancel()
                    self._closing = True
                    raise
            return result
        finally:
            self._allow_enqueue.reset(token)

    async def before_run_start(self, context):
        """A queued successor waits for durable settlement, not just a Runtime ACK."""
        ref = self._refs[context.run_id]
        dependencies = []
        for event in self.store.list_events(ref, context.run_id, limit=500):
            if event.kind == "queue.enqueued":
                dependencies.extend(json.loads(event.payload_json)["predecessors"])
        for identity, previous_ref in tuple(self._refs.items()):
            if identity == context.run_id:
                break
            if previous_ref != ref:
                continue
            dependencies.append(identity)
        for identity in dict.fromkeys(dependencies):
            try:
                previous = self._handles.get(identity)
                if previous is not None:
                    await previous.await_settled()
                observer = self._settlers.get(identity)
                if observer is not None:
                    await asyncio.shield(observer)
            except Exception:
                self.store.append_event(ref, context.run_id, "queue.blocked", {
                    "reason": "queued_predecessor_cleanup_unconfirmed",
                    "predecessor_run_id": identity,
                    "predecessor_receipt_sha256": None, "permits_replay": False})
                raise StateError("queued_predecessor_cleanup_unconfirmed") from None
            reason = None
            try:
                record = self.store.get_run(ref, identity)
                receipt = json.loads(record.receipt_json) if record.receipt_json else None
                if record.status not in _TERMINAL or not isinstance(receipt, dict):
                    reason = "queued_predecessor_unsettled"
                elif record.outcome == "unknown" or any(
                        row.get("effect_status") == "unknown"
                        for row in receipt.get("action_receipts", []) if isinstance(row, dict)):
                    reason = "queued_predecessor_unknown"
            except StateError:
                reason = "queued_predecessor_missing"
                record = None
            if reason:
                self.store.append_event(ref, context.run_id, "queue.blocked", {
                    "reason": reason, "predecessor_run_id": identity,
                    "predecessor_receipt_sha256": hashlib.sha256(
                        record.receipt_json.encode()).hexdigest() if record and record.receipt_json
                        else None, "permits_replay": False})
                raise StateError(reason)
