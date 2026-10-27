"""Host management of phase A profiles, skills, schedules and process views.

Only native owner controls call mutations. These are not offered as model tools.
Scheduled prompts enter the same Runtime and approval authority as manual chats.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
from dataclasses import asdict
from pathlib import Path

from jarvis_core.host_profile import profile_background
from jarvis_core.protocol import RpcError
from jarvis_core.scheduler import DispatchResult, Scheduler, ScheduleSpec
from jarvis_core.workspace import WorkspaceError


def params(value: dict, allowed: set, required: set = frozenset()):
    if not isinstance(value, dict) or set(value) - allowed or required - set(value):
        raise RpcError(-32602, "Los parámetros de gestión no son válidos.")


class ManagementAPI:
    def management_handlers(self):
        return {
            "agents.list": self.list_agents,
            "agents.create": self.create_agent,
            "agents.update": self.update_agent,
            "agents.delete": self.delete_agent,
            "skills.list": self.list_skills,
            "skills.inspect": self.inspect_skill,
            "skills.import": self.import_skill,
            "skills.get": self.get_skill,
            "skills.update": self.update_skill,
            "skills.enable": self.enable_skill,
            "skills.remove": self.remove_skill,
            "schedules.list": self.list_schedules,
            "schedules.create": self.create_schedule,
            "schedules.update": self.update_schedule,
            "schedules.remove": self.remove_schedule,
            "schedules.history": self.schedule_history,
            "scheduler.start": self.start_scheduler,
            "scheduler.stop": self.stop_scheduler,
            "processes.list": self.list_processes,
            "processes.poll": self.poll_process,
            "sessions.fork": self.fork_session,
            "sessions.context": self.session_context,
            "sessions.operation": self.session_operation,
            "sessions.operations": self.session_operations,
            "sessions.compact": self.compact_session,
            "sessions.rewind": self.rewind_session,
        }

    def _management_idle(self):
        if self._closing or self._maintenance or self._handles or self._retiring_sessions:
            raise RpcError(
                -32011, "Espera a que terminen las ejecuciones antes de cambiar ajustes."
            )

    async def list_agents(self, value):
        params(value, set())
        return {"agents": [asdict(row) for row in self.agent_profiles.list()]}

    async def create_agent(self, value):
        params(value, {"name", "instructions"}, {"name"})
        self._management_idle()
        row = self.agent_profiles.create(value["name"], value.get("instructions", ""))
        return {"agent": asdict(row)}

    async def update_agent(self, value):
        params(
            value,
            {"agent_id", "expected_revision", "name", "instructions"},
            {"agent_id", "expected_revision"},
        )
        self._management_idle()
        return {"agent": asdict(self.agent_profiles.update(**value))}

    async def delete_agent(self, value):
        params(value, {"agent_id", "expected_revision"}, {"agent_id", "expected_revision"})
        self._management_idle()
        agent = self.agent_profiles.get(value["agent_id"])
        if self.telegram_uses_agent(agent.agent_id):
            raise RpcError(
                -32032, "Quita su ruta o vinculación de Telegram antes de borrar el agente."
            )
        # Prevent orphaning existing contexts and private memory in other roots.
        for temporary in (False, True):
            for offset in range(0, self.store.limits.max_sessions, 100):
                rows = self.store.list_sessions(
                    temporary=temporary, include_archived=True, limit=100, offset=offset
                )
                if any(row.agent_id == agent.agent_id for row in rows):
                    raise RpcError(-32032, "El agente conserva conversaciones. Bórralas primero.")
                if len(rows) < 100:
                    break
        if (
            self.skills.list(agent.agent_id)
            or self.memory.agent_has_documents(agent.agent_id)
            or any(row.agent_id == agent.agent_id for row in self.schedule_store.list())
        ):
            raise RpcError(
                -32032, "El agente conserva skills, memoria o tareas. Retíralas primero."
            )
        self.agent_profiles.delete(**value)
        return {"deleted": True}

    async def list_skills(self, value):
        params(value, {"agent_id"}, {"agent_id"})
        self.agent_profiles.get(value["agent_id"])
        return {"skills": [asdict(row) for row in self.skills.list(value["agent_id"])]}

    async def inspect_skill(self, value):
        params(value, {"path"}, {"path"})
        if not isinstance(value["path"], str) or len(value["path"].encode()) > 4096:
            raise RpcError(-32602, "La ruta de la skill no es válida.")
        preview = self.skills.inspect_directory(Path(value["path"]))
        return {
            "preview": {
                key: item
                for key, item in asdict(preview).items()
                if key not in {"source_identity", "source_file_identity"}
            }
        }

    async def import_skill(self, value):
        params(
            value,
            {"agent_id", "path", "expected_sha256", "enabled"},
            {"agent_id", "path", "expected_sha256"},
        )
        self._management_idle()
        self.agent_profiles.get(value["agent_id"])
        if not isinstance(value["path"], str) or len(value["path"].encode()) > 4096:
            raise RpcError(-32602, "La ruta de la skill no es válida.")
        row = self.skills.import_directory(
            value["agent_id"],
            Path(value["path"]),
            expected_sha256=value["expected_sha256"],
            enabled=value.get("enabled", False),
        )
        return {"skill": asdict(row)}

    async def get_skill(self, value):
        params(value, {"agent_id", "skill_id"}, {"agent_id", "skill_id"})
        self.agent_profiles.get(value["agent_id"])
        row = self.skills.get(**value)
        return {"skill": asdict(row.record), "text": row.text}

    async def update_skill(self, value):
        params(
            value,
            {"agent_id", "skill_id", "text", "expected_revision", "expected_sha256"},
            {"agent_id", "skill_id", "text", "expected_revision", "expected_sha256"},
        )
        self._management_idle()
        self.agent_profiles.get(value["agent_id"])
        return {"skill": asdict(self.skills.update(**value))}

    async def enable_skill(self, value):
        params(
            value,
            {"agent_id", "skill_id", "enabled", "expected_revision", "expected_sha256"},
            {"agent_id", "skill_id", "enabled", "expected_revision", "expected_sha256"},
        )
        self._management_idle()
        self.agent_profiles.get(value["agent_id"])
        return {"skill": asdict(self.skills.set_enabled(**value))}

    async def remove_skill(self, value):
        params(
            value,
            {"agent_id", "skill_id", "expected_revision", "expected_sha256"},
            {"agent_id", "skill_id", "expected_revision", "expected_sha256"},
        )
        self._management_idle()
        self.agent_profiles.get(value["agent_id"])
        self.skills.remove(**value)
        return {"removed": True}

    def _schedule_snapshot(self):
        records = self.schedule_store.list(limit=33)
        if len(records) > 32 or any(len(row.prompt.encode()) > 8000 for row in records):
            raise RpcError(-32033, "El programador supera el límite revisable de 32 tareas.")
        rows = [asdict(row) for row in records]
        digest = hashlib.sha256(json.dumps(rows, sort_keys=True).encode()).hexdigest()
        return rows, digest

    async def list_schedules(self, value):
        params(value, set())
        rows, digest = self._schedule_snapshot()
        return {
            "schedules": rows,
            "configuration_hash": digest,
            "runner_running": self.scheduler.running,
            "workspace_id": self._workspace_id,
            "error": self.scheduler.error_code,
        }

    async def create_schedule(self, value):
        params(
            value,
            {"title", "prompt", "agent_id", "spec", "enabled"},
            {"title", "prompt", "agent_id", "spec"},
        )
        self._management_idle()
        self.agent_profiles.get(value["agent_id"])
        row = self.schedule_store.create(
            value["title"],
            value["prompt"],
            value["agent_id"],
            self._workspace_id,
            ScheduleSpec.from_dict(value["spec"]),
            enabled=value.get("enabled", False),
        )
        return {"schedule": asdict(row)}

    async def update_schedule(self, value):
        params(
            value,
            {"schedule_id", "expected_revision", "title", "prompt", "spec", "enabled"},
            {"schedule_id", "expected_revision"},
        )
        self._management_idle()
        data = dict(value)
        if "spec" in data:
            data["spec"] = ScheduleSpec.from_dict(data["spec"])
        return {"schedule": asdict(self.schedule_store.update(**data))}

    async def remove_schedule(self, value):
        params(value, {"schedule_id", "expected_revision"}, {"schedule_id", "expected_revision"})
        self._management_idle()
        return {"removed": self.schedule_store.remove(**value)}

    async def schedule_history(self, value):
        params(value, {"schedule_id"}, {"schedule_id"})
        rows = []
        for row in self.schedule_store.history(**value):
            ref = self.store.session_for_run(row.run_id) if row.run_id else None
            rows.append(
                {**asdict(row), "session_id": ref.session_id if ref else None, "temporary": False}
            )
        return {"occurrences": rows}

    async def start_scheduler(self, value):
        params(value, {"configuration_hash"}, {"configuration_hash"})
        self._management_idle()
        if value["configuration_hash"] != self._schedule_snapshot()[1]:
            raise RpcError(
                -32033, "Las tareas cambiaron. Revísalas antes de activar el programador."
            )
        if not self.scheduler.running:
            self._maintenance = True
            try:
                await self.scheduler.aclose()
                self.scheduler = Scheduler(
                    self.schedule_store, self._dispatch_schedule, close_timeout=10
                )
                self.scheduler.start()
            finally:
                self._maintenance = False
        return {"running": self.scheduler.running}

    async def stop_scheduler(self, value):
        params(value, set())
        if self._maintenance:
            raise RpcError(-32011, "Espera a que termine el cambio de configuración actual.")
        self._maintenance = True
        try:
            await self.scheduler.aclose()
        finally:
            self._maintenance = False
        return {"running": self.scheduler.running}

    @profile_background("scheduler.dispatch")
    async def _dispatch_schedule(self, occurrence):
        # Workspace identity is pinned at creation, not retargeted by a later UI
        # selection. No unattended decision grants a tool capability.
        if occurrence.workspace_id != self._workspace_id or self._closing:
            return DispatchResult(None, "failed", "known", "scheduled_workspace_unavailable")
        try:
            self._assert_workspace_current()
        except (WorkspaceError, OSError):
            return DispatchResult(None, "failed", "known", "scheduled_workspace_unavailable")
        self.agent_profiles.get(occurrence.agent_id)
        session = self.store.create_session(occurrence.title, agent_id=occurrence.agent_id)
        try:
            run = await self.start_run(
                {
                    "session_id": session.ref.session_id,
                    "message": occurrence.prompt,
                    "request_id": occurrence.occurrence_id,
                }
            )
        except RpcError:
            return DispatchResult(None, "failed", "known", "scheduled_dispatch_unavailable")
        run_id = run["run_id"]
        handle = self._handles.get(run_id)
        if handle is None:
            return DispatchResult(run_id, "interrupted", "unknown", "scheduled_receipt_unavailable")
        try:
            self.schedule_store.attach_run(occurrence.occurrence_id, run_id)
            result = await self._await_run_receipt(session.ref, handle)
        except BaseException:
            handle.cancel()
            cleanup = asyncio.create_task(handle.await_settled())
            while not cleanup.done():
                try:
                    await asyncio.shield(cleanup)
                except asyncio.CancelledError:
                    continue
            cleanup.result()
            raise
        if result is None:
            return DispatchResult(run_id, "interrupted", "unknown", "scheduled_receipt_unavailable")
        return DispatchResult(
            run_id,
            result["status"],
            result["outcome"],
            result["error"],
        )

    async def list_processes(self, value):
        params(value, {"session_id", "temporary"}, {"session_id"})
        ref = self._ref(value)
        return {
            "sandbox": self.process_tools.status() if self.process_tools else {"available": False},
            "processes": self.process_tools.list_processes(ref.session_id)
            if self.process_tools
            else [],
        }

    async def poll_process(self, value):
        params(
            value, {"session_id", "temporary", "process_id", "cursor"}, {"session_id", "process_id"}
        )
        ref = self._ref(value)
        if self.process_tools is None:
            raise RpcError(-32034, "El aislamiento de procesos no está disponible.")
        return {
            "process": self.process_tools.poll(
                ref.session_id, value["process_id"], cursor=value.get("cursor", 0)
            )
        }

    async def fork_session(self, value):
        params(value, {"session_id", "temporary", "title", "expected_revision", "operation_id"},
               {"session_id", "expected_revision", "operation_id"})
        if (type(value["expected_revision"]) is not int or value["expected_revision"] < 0
                or type(value["operation_id"]) is not str):
            raise RpcError(-32602, "La revisión y la identidad de la rama no son válidas.")
        if "title" in value:
            self._title(value["title"])
        self._management_idle()
        ref = self._ref(value)
        if self._busy(ref):
            raise RpcError(-32011, "Espera a que termine la ejecución antes de ramificar.")
        row = self.store.fork_session(
            ref, title=value.get("title"), expected_revision=value["expected_revision"],
            operation_id=value["operation_id"],
        )
        from jarvis_core.service import _session

        operation = self.store.session_operation(ref, value["operation_id"])
        return {"session": _session(row), "lineage": operation["result"]["lineage"]}

    async def session_context(self, value):
        params(value, {"session_id", "temporary"}, {"session_id"})
        return {"context": self.store.context_snapshot(self._ref(value))}

    async def session_operation(self, value):
        params(value, {"session_id", "temporary", "operation_id"}, {"session_id", "operation_id"})
        return {"operation": self.store.session_operation(self._ref(value), value["operation_id"])}

    async def session_operations(self, value):
        params(value, {"session_id", "temporary", "limit", "offset"}, {"session_id"})
        return self.store.session_operations(
            self._ref(value), limit=value.get("limit", 20), offset=value.get("offset", 0)
        )

    async def compact_session(self, value):
        params(value, {"session_id", "temporary", "expected_revision", "operation_id",
                       "budget_bytes", "keep_recent", "retained_decisions"},
               {"session_id", "expected_revision", "operation_id"})
        self._management_idle()
        ref = self._ref(value)
        if self._busy(ref):
            raise RpcError(-32011, "Espera a que termine la ejecución antes de compactar.")
        snapshot = self.store.compact_context(
            ref, expected_revision=value["expected_revision"], operation_id=value["operation_id"],
            budget_bytes=value.get("budget_bytes", 12000), keep_recent=value.get("keep_recent", 12),
            retained_decisions=value.get("retained_decisions"),
        )
        from jarvis_core.service import _session

        return {"context": snapshot, "session": _session(self.store.get_session(ref))}

    async def rewind_session(self, value):
        params(value, {"session_id", "temporary", "expected_revision", "operation_id",
                       "until_sequence", "title"},
               {"session_id", "expected_revision", "operation_id", "until_sequence"})
        if "title" in value:
            self._title(value["title"])
        self._management_idle()
        ref = self._ref(value)
        if self._busy(ref):
            raise RpcError(-32011, "Espera a que termine la ejecución antes de retroceder.")
        row, lineage = self.store.rewind_session(
            ref, expected_revision=value["expected_revision"],
            operation_id=value["operation_id"],
            until_sequence=value["until_sequence"],
            title=value.get("title"),
        )
        from jarvis_core.service import _session

        return {"session": _session(row), "lineage": lineage}
