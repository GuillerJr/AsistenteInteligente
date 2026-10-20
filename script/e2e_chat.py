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
from aegis_core.job_contracts import JobStatus
from aegis_core.jobs import SwarmJobManager
from aegis_core.mcp.client import McpHost
from aegis_core.memory.conversation_context import ConversationContextManager
from aegis_core.memory.conversations import ConversationCoordinator
from aegis_core.memory.sqlite import SQLiteMemoryStore
from aegis_core.orchestration.graph import build_swarm_graph
from aegis_core.providers.nvidia import NvidiaNimClient
from aegis_core.secrets import MacOSKeychain
from aegis_core.tools.broker import PolicyContext
from aegis_core.tools.confirmations import OneTimeConfirmationStore
from aegis_core.tools.defaults import build_default_tool_broker, default_policy_context
from aegis_core.tools.execution import ReadOnlyToolExecutor


class PrintingAudit:
    """Shows failure and routing events; never prints user content (events carry none)."""

    def record_system_event(self, request_id, *, event_type, component, data, call_id=None):
        if event_type in {"job_failed", "swarm_execution_failed",
                          "approved_tool_execution_failed", "mcp_tool_failed"}:
            print(f"    [audit] {event_type} {dict(data)}")

    def __getattr__(self, name):
        return lambda *args, **kwargs: None


READ_CAPABILITIES = {Capability.MAIL_READ, Capability.CALENDAR_READ, Capability.WEB_READ,
                     Capability.FILESYSTEM_READ}


async def run(turns: list[str], *, allow_writes: bool) -> int:
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
                broker = build_default_tool_broker(host.tool_definitions())
                executor = ReadOnlyToolExecutor(extra_async_handlers=host.handlers())
                conversations = ConversationCoordinator(
                    store, namespace="e2e",
                    context_max_bytes=settings.conversation_max_context_bytes,
                )
                conversations.context_manager = ConversationContextManager(
                    store, conversations, brain,
                )
                graph = build_swarm_graph(
                    brain, inference_nvidia_only=True, tool_broker=broker,
                    policy_context=context, tool_executor=executor,
                    conversation_max_context_bytes=settings.conversation_max_context_bytes,
                    dynamic_tool_names=lambda request: host.tool_names_for_request(
                        request.text, None),
                    semantic_tool_catalogue=host.explicit_request_catalogue,
                )
                jobs = SwarmJobManager(
                    graph, max_jobs=2, execution_timeout_seconds=settings.job_timeout_seconds,
                    conversations=conversations, tool_broker=broker, policy_context=context,
                    confirmation_store=confirmations, tool_executor=executor,
                    approved_result_summarizer=NvidiaToolResultSummarizer(brain),
                    audit_sink=PrintingAudit(),
                )
                record = await conversations.create("e2e")
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
                            if not (read_only or allow_writes):
                                print("    [rechazada: no es de solo lectura]")
                                snapshot = await jobs.cancel(snapshot.job_id)
                                break
                            snapshot = await jobs.approve(snapshot.job_id,
                                                          confirmation.call_digest)
                            continue
                        snapshot = await jobs.wait_for_change(
                            snapshot.job_id, after_stream_version=snapshot.stream_version,
                            timeout_seconds=10,
                        )
                    elapsed = time.monotonic() - started
                    print(f"    estado={snapshot.status.value} error={snapshot.error_code} "
                          f"t={elapsed:.1f}s")
                    if snapshot.input_request:
                        print(f"    pregunta: {snapshot.input_request.question}")
                        print(f"    opciones: {list(snapshot.input_request.options)}")
                    indent = "\n" + " " * 15
                    print("    respuesta: " + (snapshot.result or "").replace("\n", indent))
                    failures += snapshot.status is not JobStatus.COMPLETED
                return failures
        finally:
            await host.close()


def main() -> int:
    logging.basicConfig(level=logging.ERROR, format="    [log] %(name)s: %(message)s")
    parser = argparse.ArgumentParser()
    parser.add_argument("turns", nargs="+")
    parser.add_argument("--allow-writes", action="store_true")
    args = parser.parse_args()
    return asyncio.run(run(args.turns, allow_writes=args.allow_writes))


if __name__ == "__main__":
    raise SystemExit(main())
