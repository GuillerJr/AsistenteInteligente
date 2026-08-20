from __future__ import annotations

import argparse
import asyncio
import platform
import subprocess
import sys
from pathlib import Path

from aegis_core.activity import (
    SwarmActivityIpcService,
    SwarmActivitySnapshot,
    SwarmActivityTracker,
)
from aegis_core.audio import AudioTelemetryIpcService, AudioTelemetryManager
from aegis_core.config import Settings
from aegis_core.contracts import AgentRole
from aegis_core.ipc.client import IpcClient
from aegis_core.ipc.protocol import IpcAuthenticator, ProtocolError
from aegis_core.ipc.server import AegisDaemon, DaemonSecurityError
from aegis_core.jobs import SwarmIpcService, SwarmJobManager
from aegis_core.memory import (
    ConversationCoordinator,
    ConversationIpcService,
    HybridMemoryRetriever,
    MemoryIpcService,
    SQLiteMemoryStore,
)
from aegis_core.memory.sqlite import MemoryStoreError
from aegis_core.providers.base import EmbeddingInputType
from aegis_core.providers.nvidia import NvidiaNimClient, NvidiaNimError
from aegis_core.secrets import (
    InvalidIpcSecretError,
    InvalidSecretError,
    MacOSIpcSecret,
    MacOSKeychain,
    SecretNotFoundError,
    import_nvidia_key_from_clipboard,
    import_nvidia_key_from_file,
)
from aegis_core.security import AuditIntegrityIpcService
from aegis_core.tools.audit import AuditIntegrityError, HashChainAuditLog
from aegis_core.tools.broker import PolicyContext
from aegis_core.tools.confirmations import OneTimeConfirmationStore
from aegis_core.tools.defaults import build_default_tool_broker, default_policy_context
from aegis_core.tools.execution import ReadOnlyToolExecutor


def doctor() -> int:
    settings = Settings()
    architecture = platform.machine()
    print(f"architecture={architecture}")
    print(f"python={platform.python_version()}")
    print(f"nvidia_base_url={settings.nvidia_base_url}")

    if architecture != "arm64":
        print("status=error reason=non_arm64_runtime")
        return 1

    keychain = MacOSKeychain(
        service=settings.nvidia_keychain_service,
        account=settings.nvidia_keychain_account,
    )
    try:
        keychain.get()
    except SecretNotFoundError:
        print("nvidia_api_key=missing")
        return 2
    print("nvidia_api_key=available")
    return 0


def import_nvidia_key() -> int:
    settings = Settings()
    keychain = MacOSKeychain(
        service=settings.nvidia_keychain_service,
        account=settings.nvidia_keychain_account,
    )
    try:
        import_nvidia_key_from_clipboard(keychain)
    except (InvalidSecretError, SecretNotFoundError, subprocess.SubprocessError) as error:
        print(f"status=error reason={type(error).__name__}")
        return 1
    print("nvidia_api_key=stored clipboard=cleared")
    return 0


def import_nvidia_key_file(source: Path) -> int:
    settings = Settings()
    keychain = MacOSKeychain(
        service=settings.nvidia_keychain_service,
        account=settings.nvidia_keychain_account,
    )
    try:
        import_nvidia_key_from_file(keychain, source)
    except (InvalidSecretError, SecretNotFoundError, OSError, subprocess.SubprocessError) as error:
        print(f"status=error reason={type(error).__name__}")
        return 1
    print("nvidia_api_key=stored temporary_file=destroyed")
    return 0


async def probe_nvidia() -> int:
    settings = Settings()
    keychain = MacOSKeychain(
        service=settings.nvidia_keychain_service,
        account=settings.nvidia_keychain_account,
    )
    try:
        async with NvidiaNimClient(settings, keychain.get) as client:
            result = await client.complete(
                role=AgentRole.ROUTER,
                messages=[
                    {
                        "role": "user",
                        "content": "Reply with exactly: OK",
                    }
                ],
                max_tokens=8,
                temperature=0.0,
                extra_body={"chat_template_kwargs": {"enable_thinking": False}},
            )
    except (SecretNotFoundError, NvidiaNimError, OSError) as error:
        print(f"status=error reason={type(error).__name__}")
        return 1
    print(f"status=ok model={result.model_id} credential=keychain")
    return 0


async def probe_nvidia_embedding() -> int:
    settings = Settings()
    keychain = MacOSKeychain(
        service=settings.nvidia_keychain_service,
        account=settings.nvidia_keychain_account,
    )
    try:
        async with NvidiaNimClient(settings, keychain.get) as client:
            batch = await client.embed(
                ["Aegis embedding health probe"],
                input_type=EmbeddingInputType.QUERY,
            )
    except (SecretNotFoundError, NvidiaNimError, OSError, ValueError) as error:
        print(f"status=error reason={type(error).__name__}")
        return 1
    print(f"status=ok model={batch.model_id} dimensions={batch.dimensions} credential=keychain")
    return 0


async def probe_nvidia_tools() -> int:
    settings = Settings()
    keychain = MacOSKeychain(
        service=settings.nvidia_keychain_service,
        account=settings.nvidia_keychain_account,
    )
    schemas = []
    for schema in build_default_tool_broker().schemas_for(AgentRole.CODE_SECURITY):
        function = schema.get("function")
        if isinstance(function, dict) and function.get("name") == "terminal_run_template":
            schemas.append(schema)
    if len(schemas) != 1:
        print("status=error reason=tool_schema_unavailable")
        return 1
    try:
        async with NvidiaNimClient(settings, keychain.get) as client:
            result = await client.complete(
                role=AgentRole.CODE_SECURITY,
                messages=[
                    {
                        "role": "system",
                        "content": "Return exactly one required function call and no prose.",
                    },
                    {
                        "role": "user",
                        "content": "Request terminal_run_template with security_posture.",
                    },
                ],
                max_tokens=64,
                temperature=0.0,
                extra_body={"tools": schemas, "tool_choice": "required"},
            )
    except (SecretNotFoundError, NvidiaNimError, OSError, ValueError) as error:
        print(f"status=error reason={type(error).__name__}")
        return 1
    if len(result.tool_calls) != 1:
        print("status=error reason=invalid_tool_call_response")
        return 1
    call = result.tool_calls[0]
    if call.tool_name != "terminal_run_template" or call.arguments != {
        "template": "security_posture"
    }:
        print("status=error reason=invalid_tool_call_response")
        return 1
    print(
        f"status=ok model={result.model_id} tool={call.tool_name} "
        "execution=none credential=keychain"
    )
    return 0


def verify_audit(path: Path) -> int:
    try:
        records = HashChainAuditLog(path).verify()
    except (AuditIntegrityError, OSError):
        print("status=error reason=audit_integrity_failure")
        return 1
    head_hash = records[-1].record_hash if records else "empty"
    print(f"status=ok records={len(records)} head_hash={head_hash}")
    return 0


def _ipc_authenticator(settings: Settings, *, create: bool) -> IpcAuthenticator:
    secret_store = MacOSIpcSecret(
        service=settings.ipc_keychain_service,
        account=settings.ipc_keychain_account,
    )
    secret = secret_store.get_or_create() if create else secret_store.get()
    return IpcAuthenticator.from_hex(secret)


async def run_daemon() -> int:
    from aegis_core.orchestration.graph import build_swarm_graph

    settings = Settings()
    try:
        authenticator = _ipc_authenticator(settings, create=True)
        workspace_root = settings.workspace_root.resolve(strict=True)
        if not workspace_root.is_dir():
            raise ValueError("workspace root is not a directory")
        base_context = default_policy_context(workspace_root)
        confirmation_store = OneTimeConfirmationStore()
        policy_context = PolicyContext(
            workspace_root=workspace_root,
            network_scopes=base_context.network_scopes,
            confirmation_store=confirmation_store,
        )
        tool_broker = build_default_tool_broker()
        tool_executor = ReadOnlyToolExecutor()
        audit_sink = HashChainAuditLog(settings.ipc_socket_path.parent / "audit.jsonl")
        security_service = AuditIntegrityIpcService(audit_sink)
        activity_tracker = SwarmActivityTracker()
        activity_service = SwarmActivityIpcService(activity_tracker)
        nvidia_keychain = MacOSKeychain(
            service=settings.nvidia_keychain_service,
            account=settings.nvidia_keychain_account,
        )
        memory_store = SQLiteMemoryStore(
            settings.memory_database_path,
            max_entries=settings.memory_max_entries,
        )
        memory_store.initialize()
        async with NvidiaNimClient(settings, nvidia_keychain.get) as nvidia_client:
            conversations = ConversationCoordinator(
                memory_store,
                namespace=settings.memory_rag_namespace,
                history_limit=settings.conversation_history_turns,
                max_conversations=settings.conversation_max_sessions,
                max_turns=settings.conversation_max_turns,
            )
            memory_retriever = HybridMemoryRetriever(
                memory_store,
                embedding_provider=(
                    nvidia_client if settings.memory_remote_embeddings_enabled else None
                ),
                vector_scan_limit=settings.memory_vector_scan_limit,
            )
            graph = build_swarm_graph(
                nvidia_client,
                tool_broker=tool_broker,
                policy_context=policy_context,
                tool_executor=tool_executor,
                audit_sink=audit_sink,
                memory_retriever=memory_retriever,
                memory_namespace=settings.memory_rag_namespace,
                memory_limit=settings.memory_rag_limit,
                memory_max_context_bytes=settings.memory_rag_max_context_bytes,
                conversation_max_context_bytes=settings.conversation_max_context_bytes,
                activity_tracker=activity_tracker,
            )
            jobs = SwarmJobManager(
                graph,
                max_jobs=settings.ipc_max_jobs,
                conversations=conversations,
                tool_broker=tool_broker,
                policy_context=policy_context,
                confirmation_store=confirmation_store,
                tool_executor=tool_executor,
                audit_sink=audit_sink,
            )
            swarm_service = SwarmIpcService(jobs)
            memory_service = MemoryIpcService(
                memory_store,
                retriever=memory_retriever,
            )
            conversation_service = ConversationIpcService(memory_store, conversations)
            audio_service = AudioTelemetryIpcService(AudioTelemetryManager())
            daemon = AegisDaemon(
                settings.ipc_socket_path,
                authenticator,
                max_frame_bytes=settings.ipc_max_frame_bytes,
                clock_skew_seconds=settings.ipc_clock_skew_seconds,
                max_clients=settings.ipc_max_clients,
                handlers={
                    **swarm_service.handlers(),
                    **memory_service.handlers(),
                    **conversation_service.handlers(),
                    **audio_service.handlers(),
                    **security_service.handlers(),
                    **activity_service.handlers(),
                },
            )
            try:
                async with daemon:
                    print(f"status=ready socket={settings.ipc_socket_path}", flush=True)
                    await daemon.serve_forever()
            finally:
                await jobs.close()
    except (
        SecretNotFoundError,
        InvalidIpcSecretError,
        DaemonSecurityError,
        MemoryStoreError,
        OSError,
        ValueError,
    ) as error:
        print(f"status=error reason={type(error).__name__}")
        return 1
    return 0


async def daemon_status() -> int:
    settings = Settings()
    try:
        authenticator = _ipc_authenticator(settings, create=False)
        client = IpcClient(
            settings.ipc_socket_path,
            authenticator,
            max_frame_bytes=settings.ipc_max_frame_bytes,
            clock_skew_seconds=settings.ipc_clock_skew_seconds,
        )
        response = await client.call("health")
        security_response = await client.call("security.status")
        activity_response = await client.call("swarm.activity")
    except (
        TimeoutError,
        SecretNotFoundError,
        InvalidIpcSecretError,
        ProtocolError,
        OSError,
    ) as error:
        print(f"status=error reason={type(error).__name__}")
        return 1
    if not response.ok:
        print(f"status=error reason={response.error_code}")
        return 1
    if not security_response.ok:
        print(f"status=error reason={security_response.error_code}")
        return 1
    if not activity_response.ok:
        print(f"status=error reason={activity_response.error_code}")
        return 1
    protocol = response.payload.get("protocol_version")
    architecture = response.payload.get("architecture")
    if not isinstance(protocol, str) or not isinstance(architecture, str):
        print("status=error reason=invalid_health_response")
        return 1
    security = security_response.payload.get("state")
    if security not in {"intact", "compromised"}:
        print("status=error reason=invalid_security_response")
        return 1
    if security == "compromised":
        print("status=error reason=audit_integrity_failure")
        return 1
    try:
        activity = SwarmActivitySnapshot.model_validate(activity_response.payload)
    except ValueError:
        print("status=error reason=invalid_activity_response")
        return 1
    active_agents = sum(agent.active_jobs for agent in activity.agents)
    print(
        f"status=ok protocol={protocol} architecture={architecture} "
        f"security={security} active_agents={active_agents}"
    )
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(prog="aegis")
    parser.add_argument(
        "command",
        choices=[
            "doctor",
            "daemon",
            "daemon-status",
            "import-nvidia-key",
            "import-nvidia-key-file",
            "probe-nvidia",
            "probe-nvidia-embedding",
            "probe-nvidia-tools",
            "verify-audit",
        ],
    )
    parser.add_argument("resource_path", nargs="?", type=Path)
    args = parser.parse_args()
    if args.command == "doctor":
        raise SystemExit(doctor())
    if args.command == "daemon":
        raise SystemExit(asyncio.run(run_daemon()))
    if args.command == "daemon-status":
        raise SystemExit(asyncio.run(daemon_status()))
    if args.command == "import-nvidia-key":
        raise SystemExit(import_nvidia_key())
    if args.command == "import-nvidia-key-file":
        if args.resource_path is None:
            parser.error("import-nvidia-key-file requires credential_path")
        raise SystemExit(import_nvidia_key_file(args.resource_path))
    if args.command == "probe-nvidia":
        raise SystemExit(asyncio.run(probe_nvidia()))
    if args.command == "probe-nvidia-embedding":
        raise SystemExit(asyncio.run(probe_nvidia_embedding()))
    if args.command == "probe-nvidia-tools":
        raise SystemExit(asyncio.run(probe_nvidia_tools()))
    if args.command == "verify-audit":
        if args.resource_path is None:
            parser.error("verify-audit requires audit_path")
        raise SystemExit(verify_audit(args.resource_path))
    raise SystemExit(1)


if __name__ == "__main__":
    sys.exit(main())
