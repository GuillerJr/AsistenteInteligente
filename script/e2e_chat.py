"""Drive Jarvis's real job pipeline in-process, like the chat window does.

Uses live NVIDIA, the owner's configured MCP peers and the real graph, job manager,
approval and summary path, with a throwaway encrypted conversation store, so the
installed daemon, its audit log and the owner's conversations are untouched. Each
approval is granted the way the app's "Aprobar" button does (local owner grant); a
spoken approval cannot be simulated because it needs the owner's own voice.

Usage: PYTHONPATH=src .venv/bin/python script/e2e_chat.py "turn 1" "turn 2" ...
Read-only by default: a confirmation for any tool that is not a read is refused.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import tempfile
import time
from pathlib import Path

from aegis_core.brain.nvidia_client import NvidiaBrainClient
from aegis_core.brain.nvidia_summary import NvidiaToolResultSummarizer
from aegis_core.config import Settings
from aegis_core.contracts import Capability, UserRequest
from aegis_core.goals import (
    GOAL_TOOL_PURPOSES,
    GoalRunner,
    GoalStatus,
    GoalStore,
    GoalToolHandlers,
    goal_tool_definitions,
    nvidia_step_judge,
)
from aegis_core.job_contracts import JobStatus
from aegis_core.jobs import SwarmJobManager
from aegis_core.mcp.client import McpHost
from aegis_core.memory.conversation_context import ConversationContextManager
from aegis_core.memory.conversations import ConversationCoordinator
from aegis_core.memory.profile import OwnerProfile
from aegis_core.memory.semantic_profile import nvidia_memory_interpreter
from aegis_core.memory.sqlite import SQLiteMemoryStore
from aegis_core.orchestration.graph import build_swarm_graph
from aegis_core.personality import Personality
from aegis_core.providers.nvidia import NvidiaNimClient
from aegis_core.scheduling import (
    SCHEDULE_TOOL_PURPOSES,
    ScheduledTaskStore,
    ScheduleToolHandlers,
    TaskScheduler,
    nvidia_novelty_judge,
    schedule_tool_definitions,
)
from aegis_core.secrets import MacOSKeychain
from aegis_core.standing_grants import StandingGrantStore
from aegis_core.tools.broker import PolicyContext
from aegis_core.tools.confirmations import OneTimeConfirmationStore
from aegis_core.tools.defaults import build_default_tool_broker, default_policy_context
from aegis_core.tools.execution import ReadOnlyToolExecutor


class PrintingAudit:
    """Shows failure and routing events; never prints user content (events carry none)."""

    def record_system_event(self, request_id, *, event_type, component, data, call_id=None):
        if event_type.startswith("standing_grant"):
            print(f"    [audit] {event_type} {dict(data)}")
        if event_type in {"job_failed", "swarm_execution_failed",
                          "approved_tool_execution_failed", "mcp_tool_failed"}:
            print(f"    [audit] {event_type} {dict(data)}")
        elif event_type == "nvidia_inference" and data.get("outcome") != "completed":
            print(f"    [audit] nvidia {dict(data)}")

    def __getattr__(self, name):
        return lambda *args, **kwargs: None


READ_CAPABILITIES = {Capability.MAIL_READ, Capability.CALENDAR_READ, Capability.WEB_READ,
                     Capability.FILESYSTEM_READ}


async def run(turns: list[str], *, allow_writes: bool, always: bool = False,
              run_scheduled: bool = False, run_goals: bool = False) -> int:
    settings = Settings()
    keychain = MacOSKeychain(service=settings.nvidia_keychain_service,
                             account=settings.nvidia_keychain_account)
    host = McpHost.from_file(settings.mcp_configuration_path)
    await host.start()
    with tempfile.TemporaryDirectory() as scratch:
        os.chmod(scratch, 0o700)
        store = SQLiteMemoryStore(Path(scratch) / "memory.sqlite3",
                                  encryption_secret=os.urandom(32))
        store.initialize()
        try:
            async with NvidiaNimClient(settings, keychain.get) as client:
                brain = NvidiaBrainClient(client)
                confirmations = OneTimeConfirmationStore()
                base = default_policy_context(Path.cwd())
                context = PolicyContext(workspace_root=base.workspace_root,
                                        network_scopes=base.network_scopes,
                                        confirmation_store=confirmations)
                schedules = ScheduledTaskStore(Path(scratch).resolve() / "scheduled_tasks.json")
                schedule_tools = ScheduleToolHandlers(schedules)
                goals = GoalStore(Path(scratch).resolve() / "goals.json")
                goal_tools = GoalToolHandlers(goals)
                broker = build_default_tool_broker((
                    *schedule_tool_definitions(), *goal_tool_definitions(),
                    *host.tool_definitions()))
                executor = ReadOnlyToolExecutor(extra_async_handlers={
                    **schedule_tools.handlers(), **goal_tools.handlers(), **host.handlers()})
                conversations = ConversationCoordinator(
                    store, namespace="e2e",
                    context_max_bytes=settings.conversation_max_context_bytes,
                )
                conversations.context_manager = ConversationContextManager(
                    store, conversations, brain,
                )
                owner_profile = OwnerProfile(
                    store, namespace="e2e", interpreter=nvidia_memory_interpreter(brain),
                )
                graph = build_swarm_graph(
                    brain, inference_nvidia_only=True, tool_broker=broker,
                    owner_profile=owner_profile,
                    personality_instruction=Personality(
                        preset=os.environ.get("E2E_PERSONALITY", "default"),
                        custom=os.environ.get("E2E_PERSONALITY_CUSTOM", ""),
                    ).instruction,
                    policy_context=context, tool_executor=executor,
                    conversation_max_context_bytes=settings.conversation_max_context_bytes,
                    dynamic_tool_names=lambda request: host.tool_names_for_request(
                        request.text, None),
                    semantic_tool_catalogue=lambda: (
                        *SCHEDULE_TOOL_PURPOSES.items(), *GOAL_TOOL_PURPOSES.items(),
                        *host.explicit_request_catalogue()),
                )
                standing = StandingGrantStore(
                    Path(scratch).resolve() / "standing_grants.json",
                    capability_of=lambda name: (
                        definition.capability
                        if (definition := broker.definition(name)) is not None else None
                    ),
                )
                jobs = SwarmJobManager(
                    graph, standing_grants=standing,
                    max_jobs=2, execution_timeout_seconds=settings.job_timeout_seconds,
                    conversations=conversations, tool_broker=broker, policy_context=context,
                    confirmation_store=confirmations, tool_executor=executor,
                    approved_result_summarizer=NvidiaToolResultSummarizer(brain),
                    audit_sink=PrintingAudit(), owner_profile=owner_profile,
                )
                record = await conversations.create(
                    "e2e", temporary=bool(os.environ.get("E2E_TEMPORARY")))
                failures = 0
                for text in turns:
                    print(f"\n>>> {text}")
                    if os.environ.get("E2E_SHOW_EXPANSION"):
                        from aegis_core.orchestration.clarification import expand_option_answer
                        history = await conversations.history(record.conversation_id)
                        print(f"    [expansión] {expand_option_answer(text, history)!r}")
                    started = time.monotonic()
                    snapshot = await jobs.submit(
                        UserRequest(text=text, metadata={"source_channel": "chat"}),
                        conversation_id=record.conversation_id,
                    )
                    while snapshot.status not in {JobStatus.COMPLETED, JobStatus.FAILED,
                                                  JobStatus.CANCELLED}:
                        if snapshot.status is JobStatus.AWAITING_CONFIRMATION:
                            confirmation = snapshot.confirmation
                            definition = broker.definition(confirmation.tool_name)
                            read_only = (definition is not None
                                         and definition.capability in READ_CAPABILITIES)
                            print(f"    [aprobación] {confirmation.tool_name} :: "
                                  f"{confirmation.summary[:160]}")
                            # Schedule tools only write the throwaway task store.
                            if not (read_only or allow_writes
                                    or confirmation.tool_name.startswith("jarvis_")):
                                print("    [rechazada: no es de solo lectura]")
                                snapshot = await jobs.cancel(snapshot.job_id)
                                break
                            if always and confirmation.standing_grant_available:
                                print("    [aprobar siempre]")
                                snapshot = await jobs.approve_standing(
                                    snapshot.job_id, confirmation.call_digest)
                            else:
                                snapshot = await jobs.approve(snapshot.job_id,
                                                              confirmation.call_digest)
                            continue
                        snapshot = await jobs.wait_for_change(
                            snapshot.job_id, after_stream_version=snapshot.stream_version,
                            timeout_seconds=10,
                        )
                    elapsed = time.monotonic() - started
                    tool = snapshot.evaluation.tool_name if snapshot.evaluation else None
                    print(f"    estado={snapshot.status.value} error={snapshot.error_code} "
                          f"herramienta={tool} t={elapsed:.1f}s")
                    if snapshot.input_request:
                        print(f"    pregunta: {snapshot.input_request.question}")
                        print(f"    opciones: {list(snapshot.input_request.options)}")
                    indent = "\n" + " " * 15
                    print("    respuesta: " + (snapshot.result or "").replace("\n", indent))
                    failures += snapshot.status is not JobStatus.COMPLETED
                    await owner_profile.drain()
                    if os.environ.get("E2E_SHOW_MEMORY"):
                        for hit in await owner_profile.recall(limit=10):
                            print(f"    [memoria] {hit.excerpt}")
                if run_scheduled:
                    async def show(task, text):
                        print("    [entrega] " + text.replace("\n", "\n" + " " * 15))

                    scheduler = TaskScheduler(
                        schedules, jobs=jobs, conversations=conversations, deliver=(show,),
                        judge_new=nvidia_novelty_judge(brain))
                    for task in schedules.tasks():
                        print(f"\n### ejecutando tarea «{task.title}» "
                              f"({task.schedule.describe()}, {task.notify.value}): {task.prompt}")
                        await scheduler.run_now(task.task_id)
                        print(f"    estado_tarea={schedules.get(task.task_id).last_status}")
                if run_goals:
                    async def show_goal(goal, text):
                        print("    [entrega] " + text.replace("\n", "\n" + " " * 15))

                    runner = GoalRunner(goals, jobs=jobs, conversations=conversations,
                                        judge=nvidia_step_judge(brain), deliver=(show_goal,))
                    for goal in goals.goals():
                        print(f"\n### objetivo «{goal.title}» (hasta {goal.max_steps} pasos): "
                              f"{goal.objective}")
                        status = GoalStatus.ACTIVE
                        while status is GoalStatus.ACTIVE:
                            status = await runner.step(goal.goal_id)
                            current = goals.get(goal.goal_id)
                            print(f"    paso {current.steps_used}: {status.value} :: "
                                  f"{(current.notes or ['-'])[-1]}")
                return failures
        finally:
            await host.close()


def _trace_failures() -> None:
    """E2E_TRACE=1 prints the traceback of each mapped job failure (local debugging)."""
    import traceback

    from aegis_core.job_failures import JobFailurePolicy

    original = JobFailurePolicy._record_mapped

    def record(self, error, code, **kwargs):
        traceback.print_exception(error, limit=12)
        return original(self, error, code, **kwargs)

    JobFailurePolicy._record_mapped = record


def main() -> int:
    if os.environ.get("E2E_TRACE"):
        _trace_failures()
    logging.basicConfig(level=getattr(logging, os.environ.get("E2E_LOG", "ERROR")),
                        format="    [log] %(name)s: %(message)s")
    parser = argparse.ArgumentParser()
    parser.add_argument("turns", nargs="+")
    parser.add_argument("--allow-writes", action="store_true")
    parser.add_argument("--run-goals", action="store_true",
                        help="work every goal the turns created until it stops")
    parser.add_argument("--run-scheduled", action="store_true",
                        help="run every task the turns scheduled, right away")
    parser.add_argument("--always", action="store_true",
                        help="approve read-only tools with 'always allow'")
    args = parser.parse_args()
    return asyncio.run(run(args.turns, allow_writes=args.allow_writes, always=args.always,
                           run_scheduled=args.run_scheduled, run_goals=args.run_goals))


if __name__ == "__main__":
    raise SystemExit(main())
