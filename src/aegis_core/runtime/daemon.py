from __future__ import annotations

import asyncio
import logging
import signal
import subprocess
from contextlib import AsyncExitStack
from uuid import uuid4

from aegis_core.activity import SwarmActivityIpcService, SwarmActivityTracker
from aegis_core.application_qualification import (
    ApplicationQualificationIpcService,
    JarvisApplicationQualification,
)
from aegis_core.approval_signing import PinnedApprovalSigner
from aegis_core.audio import AudioTelemetryIpcService, AudioTelemetryManager
from aegis_core.audit_anchor import DurableAuditAnchor
from aegis_core.biometric_training_service import (
    BiometricTrainingService,
    VoiceConfirmationVerifier,
)
from aegis_core.brain.nvidia_client import NvidiaBrainClient
from aegis_core.brain.nvidia_summary import NvidiaToolResultSummarizer
from aegis_core.brain.vision_fallback import LocalizedVisionAnalyzer, VisionFallbackIpcService
from aegis_core.brain.vision_processor import ActiveVisionIpcService
from aegis_core.browser_qualification import (
    BrowserQualificationIpcService,
    JarvisBrowserQualification,
)
from aegis_core.capability_learning import (
    CapabilityLearningCoordinator,
    CapabilityLearningIpcService,
    CapabilityLearningStore,
)
from aegis_core.config import Settings
from aegis_core.contracts import ToolAuthorization
from aegis_core.engineering import EngineeringIpcService
from aegis_core.evaluation import EvaluationStoreError, SQLiteEvaluationStore
from aegis_core.extensions_service import ExtensionsIpcService
from aegis_core.ipc.peer_identity import (
    JARVIS_CLIENT_METHODS,
    UNSIGNED_PEER_METHODS,
    JarvisPeerIdentifier,
    PeerIdentifierResolver,
    resolve_peer_requirement,
)
from aegis_core.ipc.protocol import IpcAuthenticator
from aegis_core.ipc.server import AegisDaemon, DaemonSecurityError
from aegis_core.job_ipc import SwarmIpcService
from aegis_core.jobs import SwarmJobManager
from aegis_core.mascot import MascotAutonomyService
from aegis_core.mcp import McpConfigurationError, McpHostManager, McpProtocolError
from aegis_core.mcp.browser_tasks import BrowserTaskService
from aegis_core.memory import (
    ConversationCoordinator,
    ConversationIpcService,
    GraphRAGService,
    HybridMemoryRetriever,
    MemoryDecayWorker,
    MemoryIpcService,
    OwnerProfile,
    SocialMemory,
    SQLiteMemoryStore,
)
from aegis_core.memory.conversation_context import ConversationContextManager
from aegis_core.memory.crypto import MemoryRowCipher
from aegis_core.memory.errors import MemoryStoreError
from aegis_core.memory.retrieval_planning import NvidiaRetrievalPlanner
from aegis_core.memory.semantic_profile import nvidia_memory_interpreter
from aegis_core.memory.spotlight_sync import SpotlightGraphSync
from aegis_core.performance_profiler import (
    PerformanceAnalyticsStore,
    PerformanceIpcService,
    PerformanceProfiler,
    PerformanceProfilerError,
)
from aegis_core.plugins.runtime import PluginRuntime
from aegis_core.plugins.service import PluginStatusIpcService
from aegis_core.plugins.store import PluginStore
from aegis_core.provider_status import ProviderStatusIpcService
from aegis_core.providers.apple_embedding import AppleLocalEmbeddingClient
from aegis_core.providers.mlx_provider import (
    MLXWhisperTranscriber,
)
from aegis_core.providers.nvidia import NvidiaNimClient
from aegis_core.providers.nvidia_asr import transcribe as transcribe_telegram_voice
from aegis_core.runtime.lifecycle import BackgroundTaskSupervisor, serve_until_shutdown
from aegis_core.runtime_preflight import RuntimePreflightIpcService
from aegis_core.runtime_state import RuntimeSuspensionController
from aegis_core.secrets import (
    InvalidAuditAnchorError,
    InvalidIpcSecretError,
    InvalidMemorySecretError,
    MacOSAuditAnchor,
    MacOSGenericSecret,
    MacOSIpcSecret,
    MacOSKeychain,
    MacOSMemorySecret,
    SecretNotFoundError,
)
from aegis_core.security import AuditIntegrityIpcService, SecurityStateLatch
from aegis_core.settings_service import SettingsIpcService
from aegis_core.skills import SkillRegistry, SkillStore
from aegis_core.skills.policy import SkillPolicyStore
from aegis_core.speech import (
    SpeechArtifactError,
    SpeechArtifactStore,
    SpeechSynthesisIpcService,
)
from aegis_core.tcc_privacy import TCCPrivacyIpcService
from aegis_core.telegram.configuration import TelegramConfigurationStore
from aegis_core.telegram.journal import TelegramJournal
from aegis_core.telegram.service import TelegramService
from aegis_core.tools.android_automation import (
    AndroidAutomationToolService,
    WirelessADBClient,
)
from aegis_core.tools.audit import AuditIntegrityError, HashChainAuditLog
from aegis_core.tools.audit_service import SystemAuditIpcService
from aegis_core.tools.broker import PolicyContext
from aegis_core.tools.chrome_cdp import BrowserDiscoveryIpcService, ChromeCDPController
from aegis_core.tools.computer import ComputerUseController
from aegis_core.tools.computer_relay import (
    ComputerCommandRelay,
    ComputerRelayIpcService,
    RelayedComputerBridge,
)
from aegis_core.tools.confirmations import OneTimeConfirmationStore
from aegis_core.tools.defaults import build_default_tool_broker, default_policy_context
from aegis_core.tools.execution import ReadOnlyToolExecutor
from aegis_core.tools.ios_bridge import (
    FocusPriorityState,
    IOSBridgeService,
    IOSShortcutBridge,
)
from aegis_core.tools.smart_tv import SmartTVController, SmartTVToolService

LOGGER = logging.getLogger(__name__)


def _ipc_authenticator(settings: Settings) -> IpcAuthenticator:
    secret = MacOSIpcSecret(
        service=settings.ipc_keychain_service,
        account=settings.ipc_keychain_account,
    ).get_or_create()
    return IpcAuthenticator.from_hex(secret)


def _peer_identifier(settings: Settings) -> PeerIdentifierResolver | None:
    if not settings.ipc_require_signed_peers:
        LOGGER.warning("ipc_signed_peers_disabled")
        return None
    requirement = resolve_peer_requirement(settings.ipc_peer_code_requirement)
    if requirement is None:
        # Fail closed: without a pinned signer no peer can reach privileged methods.
        LOGGER.error("ipc_peer_requirement_unavailable")
        return lambda _peer_socket: None
    return JarvisPeerIdentifier(requirement, tuple(JARVIS_CLIENT_METHODS))


def _plugin_store(settings: Settings) -> PluginStore:
    seal_keychain = MacOSIpcSecret(
        service=settings.plugin_seal_keychain_service,
        account=settings.ipc_keychain_account,
    )
    legacy_keychain = MacOSIpcSecret(
        service=settings.ipc_keychain_service,
        account=settings.ipc_keychain_account,
    )
    return PluginStore(
        settings.plugins_directory,
        seal_keychain.get_or_create,
        # Only consulted for records sealed before the plugin store had its own key.
        legacy_seal_key_loader=lambda: legacy_keychain.get(),
    )


def _memory_encryption_secret(settings: Settings) -> bytes:
    secret_store = MacOSMemorySecret(
        service=settings.memory_keychain_service,
        account=settings.memory_keychain_account,
    )
    if SQLiteMemoryStore.encryption_key_initialized(settings.memory_database_path):
        return secret_store.get()
    return secret_store.get_or_create()


async def _serve_compromised_daemon(
    settings: Settings,
    authenticator: IpcAuthenticator,
    security_service: AuditIntegrityIpcService,
    security_state: SecurityStateLatch,
    shutdown: asyncio.Event,
) -> int:
    daemon = AegisDaemon(
        settings.ipc_socket_path,
        authenticator,
        max_frame_bytes=settings.ipc_max_frame_bytes,
        max_message_bytes=settings.ipc_max_message_bytes,
        clock_skew_seconds=settings.ipc_clock_skew_seconds,
        max_clients=settings.ipc_max_clients,
        read_timeout_seconds=settings.ipc_read_timeout_seconds,
        write_timeout_seconds=settings.ipc_write_timeout_seconds,
        handler_timeout_seconds=settings.ipc_handler_timeout_seconds,
        handlers=security_service.handlers(),
        security_compromised=lambda: security_state.compromised,
    )
    async with daemon:
        print(
            f"status=compromised socket={settings.ipc_socket_path}",
            flush=True,
        )
        await serve_until_shutdown(daemon, shutdown=shutdown)
    return 0


async def run_daemon() -> int:
    # Own SIGTERM across initialization AND cleanup, including audit sealing.
    # Repeated stop requests set the same event instead of killing a half-closed runtime.
    loop = asyncio.get_running_loop()
    shutdown = asyncio.Event()
    signal_installed = False
    try:
        try:
            loop.add_signal_handler(signal.SIGTERM, shutdown.set)
            signal_installed = True
        except (NotImplementedError, RuntimeError, ValueError):
            pass
        return await _run_daemon(shutdown)
    finally:
        if signal_installed:
            loop.remove_signal_handler(signal.SIGTERM)


async def _run_daemon(shutdown: asyncio.Event) -> int:
    from aegis_core.orchestration.graph import build_swarm_graph

    settings = Settings()
    mcp_host: McpHostManager | None = None
    browser_tasks: BrowserTaskService | None = None
    try:
        authenticator = _ipc_authenticator(settings)
        workspace_root = settings.workspace_root.resolve(strict=True)
        if not workspace_root.is_dir():
            raise ValueError("workspace root is not a directory")
        base_context = default_policy_context(workspace_root)
        confirmation_store = OneTimeConfirmationStore()
        security_state = SecurityStateLatch()
        policy_context = PolicyContext(
            workspace_root=workspace_root,
            network_scopes=base_context.network_scopes,
            confirmation_store=confirmation_store,
            security_compromised=lambda: security_state.compromised,
        )
        plugin_store = _plugin_store(settings)
        plugin_runtime = PluginRuntime(plugin_store.enabled_packages(), store=plugin_store)
        plugin_status_service = PluginStatusIpcService(plugin_runtime)
        audit_sink = HashChainAuditLog(
            settings.ipc_socket_path.parent / "audit.jsonl",
            max_bytes=settings.audit_max_bytes,
            archived_segments=settings.audit_archived_segments,
        )
        durable_audit_anchor = DurableAuditAnchor(MacOSAuditAnchor())
        security_service = AuditIntegrityIpcService(audit_sink, security_state)
        runtime_state = RuntimeSuspensionController()
        try:
            durable_audit_anchor.validate_startup(audit_sink)
        except (AuditIntegrityError, InvalidAuditAnchorError, OSError):
            security_state.compromise("audit_cold_boot_anchor_failure")
            return await _serve_compromised_daemon(
                settings,
                authenticator,
                security_service,
                security_state,
                shutdown,
            )
        mcp_host = McpHostManager.from_file(
            settings.mcp_configuration_path,
            audit_sink=audit_sink,
        )
        await mcp_host.start()
        browser_tasks = BrowserTaskService(settings, audit_sink=audit_sink)
        smart_tv_controller = SmartTVController.from_file(
            settings.smart_tv_configuration_path,
            timeout_seconds=settings.smart_tv_timeout_seconds,
            audit_sink=audit_sink,
        )
        smart_tv_service = SmartTVToolService(
            smart_tv_controller,
            audit_sink=audit_sink,
        )
        android_service = AndroidAutomationToolService(
            WirelessADBClient.from_file(
                settings.android_adb_configuration_path,
                adb_path=settings.android_adb_executable_path,
                timeout_seconds=settings.android_adb_timeout_seconds,
            ),
            audit_sink=audit_sink,
        )
        focus_priority_state = FocusPriorityState()
        ios_service = IOSBridgeService(
            IOSShortcutBridge.from_file(
                settings.ios_shortcuts_configuration_path,
                authorizer_path=settings.ios_bridge_executable_path,
                timeout_seconds=settings.ios_shortcut_timeout_seconds,
            ),
            focus_priority_state,
            audit_sink=audit_sink,
        )
        device_definitions = (
            *smart_tv_service.tool_definitions(),
            *android_service.tool_definitions(),
            *ios_service.tool_definitions(),
        )
        tool_broker = build_default_tool_broker(
            (
                *plugin_runtime.tool_definitions(),
                *mcp_host.tool_definitions(),
                *browser_tasks.tool_definitions(),
                *device_definitions,
            ),
            audit_sink=audit_sink,
        )
        skill_registry = SkillRegistry(
            tool_broker,
            SkillStore(settings.skills_directory),
            plugin_skills=plugin_runtime.skill_manifests(),
            policy=SkillPolicyStore(settings.skills_directory.parent / "skills_policy.json"),
            plugin_available=lambda skill_id: any(
                any(s.skill_id == skill_id for s in package.manifest.skills)
                and plugin_runtime.is_active(package.manifest.plugin_id)
                for package in plugin_runtime.packages
            ),
        )

        def handle_memory_auth_failure(reason: str) -> None:
            security_state.compromise(reason)
            audit_sink.record_system_event(
                uuid4(),
                event_type="memory_auth_failure",
                component="memory_store",
                data={"reason": reason, "state": "compromised"},
            )

        activity_tracker = SwarmActivityTracker()
        activity_service = SwarmActivityIpcService(activity_tracker)
        computer_relay = ComputerCommandRelay(asyncio.get_running_loop())
        computer_relay_service = ComputerRelayIpcService(computer_relay)
        relayed_computer_bridge = RelayedComputerBridge(computer_relay)
        application_qualification_service = ApplicationQualificationIpcService(
            JarvisApplicationQualification(bridge=relayed_computer_bridge)
        )
        nvidia_keychain = MacOSKeychain(
            service=settings.nvidia_keychain_service,
            account=settings.nvidia_keychain_account,
        )
        # Inference is NVIDIA-only; the on-device helper only finds personal names so
        # they can be masked before a request leaves the Mac.
        local_embedding_client = AppleLocalEmbeddingClient(
            settings.local_embedding_executable_path,
            timeout_seconds=settings.local_embedding_timeout_seconds,
        )
        provider_status_service = ProviderStatusIpcService(
            nvidia_keychain.is_configured,
        )
        runtime_preflight_service = RuntimePreflightIpcService(
            provider_status_service,
            security_service,
            runtime_power_known=lambda: runtime_state.snapshot() is not None,
        )
        memory_secret = _memory_encryption_secret(settings)
        memory_store = SQLiteMemoryStore(
            settings.memory_database_path,
            max_entries=settings.memory_max_entries,
            max_namespace_entries=settings.memory_namespace_max_entries,
            max_node_embeddings=settings.memory_max_node_embeddings,
            encryption_secret=memory_secret,
            on_auth_failure=handle_memory_auth_failure,
        )
        memory_store.initialize()
        evaluation_store = SQLiteEvaluationStore(
            settings.evaluation_database_path,
            max_entries=settings.evaluation_max_entries,
        )
        evaluation_store.initialize()
        performance_store = PerformanceAnalyticsStore(
            settings.performance_database_path,
            max_entries=settings.performance_max_entries,
        )
        performance_store.initialize()
        performance_profiler = PerformanceProfiler(
            performance_store,
            memory_probe=lambda: memory_store.performance_metrics(
                namespace=settings.memory_rag_namespace,
            ),
            thermal_probe=runtime_state.snapshot,
        )
        performance_service = PerformanceIpcService(performance_profiler, audit_sink)
        async with NvidiaNimClient(settings, nvidia_keychain.get) as nvidia_client:
            nvidia_brain = NvidiaBrainClient(nvidia_client, audit_sink=audit_sink)
            device_tool_handlers = {
                **smart_tv_service.handlers(),
                **android_service.handlers(),
                **ios_service.tool_handlers(),
            }
            chrome_controller = ChromeCDPController(audit_sink=audit_sink)
            browser_discovery_service = BrowserDiscoveryIpcService()
            browser_qualification_service = BrowserQualificationIpcService(
                JarvisBrowserQualification(
                    chrome_controller,
                    bridge=relayed_computer_bridge,
                )
            )
            tool_executor = ReadOnlyToolExecutor(
                computer_controller=ComputerUseController(
                    nvidia_client,
                    bridge=relayed_computer_bridge,
                    activity_tracker=activity_tracker,
                ),
                extra_handlers=plugin_runtime.handlers(),
                extra_async_handlers={
                    **mcp_host.handlers(),
                    **browser_tasks.handlers(),
                    **device_tool_handlers,
                    "browser_play_media": chrome_controller.execute_tool,
                },
            )

            async def wake_device(
                authorization: ToolAuthorization,
            ) -> tuple[bool, int]:
                if authorization.tool_name == smart_tv_service.CONTROL_TOOL:
                    return await smart_tv_service.wake(authorization)
                if authorization.tool_name == android_service.TOOL_NAME:
                    return await android_service.wake(authorization)
                return False, 0

            async def verify_device(
                authorization: ToolAuthorization,
            ) -> tuple[bool, int]:
                if authorization.tool_name == smart_tv_service.CONTROL_TOOL:
                    return await smart_tv_service.verify(authorization)
                if authorization.tool_name == android_service.TOOL_NAME:
                    return await android_service.verify(authorization)
                return False, 0

            def current_focus_priority() -> dict[str, str | bool]:
                snapshot = focus_priority_state.snapshot()
                return {
                    "mode": snapshot.mode.value,
                    "active": snapshot.active,
                    "priority": snapshot.planning_priority,
                }

            conversations = ConversationCoordinator(
                memory_store,
                namespace=settings.memory_rag_namespace,
                history_limit=settings.conversation_history_turns,
                context_max_bytes=settings.conversation_max_context_bytes,
                max_conversations=settings.conversation_max_sessions,
                max_turns=settings.conversation_max_turns,
            )
            conversations.context_manager = ConversationContextManager(
                memory_store, conversations, nvidia_brain,
            )
            conversations.retrieval_planner = NvidiaRetrievalPlanner(nvidia_brain)
            graph_service = GraphRAGService(memory_store)
            memory_retriever = HybridMemoryRetriever(
                memory_store,
                embedding_provider=None,
                background_gate=runtime_state,
                graph_service=graph_service,
            )
            memory_decay_worker = MemoryDecayWorker(
                graph_service,
                namespace=settings.memory_rag_namespace,
                runtime_probe=runtime_state.snapshot,
                audit_sink=audit_sink,
            )
            biometric_training_service = (
                BiometricTrainingService(
                    training_directory=settings.biometric_training_directory,
                    enrollment_directory=settings.biometric_enrollment_directory,
                    active_model_path=settings.biometric_model_path,
                    trainer_executable=settings.biometric_trainer_executable_path,
                    calibrator_executable=(settings.biometric_calibrator_executable_path),
                    keychain_service=settings.biometric_keychain_service,
                    keychain_account=settings.biometric_keychain_account,
                    runtime_probe=runtime_state.snapshot,
                    audit_sink=audit_sink,
                    maximum_cpu_percent=(settings.biometric_training_maximum_cpu_percent),
                    minimum_idle_seconds=(settings.biometric_training_minimum_idle_seconds),
                )
                if settings.biometric_training_enabled
                else None
            )
            whisper_transcriber = MLXWhisperTranscriber(
                settings.mlx_whisper_model_path,
                timeout_seconds=settings.mlx_whisper_confirmation_timeout_seconds,
                audit_sink=audit_sink,
            )
            voice_confirmation_verifier = VoiceConfirmationVerifier(
                whisper_transcriber,
                audit_sink=audit_sink,
            )
            spotlight_sync = SpotlightGraphSync(
                memory_store,
                namespace=settings.memory_rag_namespace,
                helper_path=settings.spotlight_indexer_executable_path,
                enabled=settings.spotlight_graph_index_enabled,
                audit_sink=audit_sink,
            )
            owner_profile = OwnerProfile(
                memory_store,
                namespace=settings.memory_rag_namespace,
                interpreter=nvidia_memory_interpreter(nvidia_brain),
            )
            social_memory = SocialMemory(
                memory_store,
                namespace=settings.memory_rag_namespace,
            )
            capability_store = CapabilityLearningStore(
                settings.capability_learning_directory,
            )
            capability_learning = CapabilityLearningCoordinator(capability_store)
            capability_service = CapabilityLearningIpcService(capability_store)
            graph = build_swarm_graph(
                nvidia_brain,
                inference_nvidia_only=True,
                tool_broker=tool_broker,
                policy_context=policy_context,
                tool_executor=tool_executor,
                audit_sink=audit_sink,
                memory_retriever=memory_retriever,
                owner_profile=owner_profile,
                social_memory=social_memory,
                memory_namespace=settings.memory_rag_namespace,
                memory_limit=settings.memory_rag_limit,
                memory_max_context_bytes=settings.memory_rag_max_context_bytes,
                conversation_max_context_bytes=settings.conversation_max_context_bytes,
                activity_tracker=activity_tracker,
                skill_registry=skill_registry,
                capability_learning=capability_learning,
                device_waker=wake_device,
                device_verifier=verify_device,
                focus_priority_provider=current_focus_priority,
                runtime_context_provider=lambda: runtime_state.snapshot(),
                dynamic_tool_names=lambda request: mcp_host.tool_names_for_request(
                    request.text,
                    request.metadata.get("active_application_bundle_identifier")
                    if isinstance(
                        request.metadata.get("active_application_bundle_identifier"),
                        str,
                    )
                    else None,
                ) | browser_tasks.names_for_request(request),
                semantic_tool_catalogue=mcp_host.explicit_request_catalogue,
                browser_task_context=browser_tasks.context_for_request,
                person_name_detector=local_embedding_client.person_names,
            )
            jobs = SwarmJobManager(
                graph,
                max_jobs=settings.ipc_max_jobs,
                execution_timeout_seconds=settings.job_timeout_seconds,
                conversations=conversations,
                owner_profile=owner_profile,
                social_memory=social_memory,
                capability_learning=capability_learning,
                tool_broker=tool_broker,
                policy_context=policy_context,
                confirmation_store=confirmation_store,
                tool_executor=tool_executor,
                audit_sink=audit_sink,
                evaluation_store=evaluation_store,
                voice_confirmation_verifier=voice_confirmation_verifier,
                approved_result_summarizer=NvidiaToolResultSummarizer(nvidia_brain),
                request_cleanup=browser_tasks.finish_request,
            )
            telegram_service = TelegramService(
                TelegramConfigurationStore(settings.telegram_configuration_path),
                MacOSGenericSecret(service="ai.aegis.telegram-bot"),
                jobs,
                conversations,
                blocked=lambda: security_state.compromised or runtime_state.suspended,
                journal=TelegramJournal(
                    settings.telegram_configuration_path.with_name("telegram-transport.json"),
                    MemoryRowCipher(memory_secret),
                ),
                transcriber=lambda data: transcribe_telegram_voice(nvidia_client, data),
                synthesizer=nvidia_client.synthesize_speech,
            )
            mascot_service = MascotAutonomyService(
                nvidia_brain,
                blocked=lambda: security_state.compromised or runtime_state.suspended,
                foreground_busy=lambda: jobs.has_active_jobs,
            )
            swarm_service = SwarmIpcService(
                jobs,
                approval_signer=PinnedApprovalSigner(settings.approval_signer_public_key_path),
            )
            engineering_service = EngineeringIpcService(jobs, workspace_root)
            privacy_service = TCCPrivacyIpcService(jobs, audit_sink)
            active_vision_service = ActiveVisionIpcService()
            memory_service = MemoryIpcService(
                memory_store,
                retriever=memory_retriever,
                graph_service=graph_service,
            )
            conversation_service = ConversationIpcService(memory_store, conversations)
            audio_service = AudioTelemetryIpcService(
                AudioTelemetryManager(),
                runtime_state=runtime_state,
                audit_sink=audit_sink,
            )
            system_audit_service = SystemAuditIpcService(audit_sink)
            speech_service = SpeechSynthesisIpcService(
                nvidia_client.synthesize_speech,
                SpeechArtifactStore(settings.ipc_socket_path.parent / "speech"),
                nvidia_client.stream_speech,
            )
            settings_service = SettingsIpcService(settings, mcp_host, nvidia_keychain)
            localized_vision_service = VisionFallbackIpcService(
                LocalizedVisionAnalyzer(nvidia_client, timeout_seconds=5.0, audit_sink=audit_sink)
            )
            extensions_service = ExtensionsIpcService(
                skill_registry,
                plugin_store,
                plugin_runtime,
                tool_broker,
                jobs_busy=lambda: jobs.has_active_jobs,
                settings_busy=lambda: bool(
                    settings_service.task and not settings_service.task.done()
                ),
            )
            daemon = AegisDaemon(
                settings.ipc_socket_path,
                authenticator,
                max_frame_bytes=settings.ipc_max_frame_bytes,
                max_message_bytes=settings.ipc_max_message_bytes,
                clock_skew_seconds=settings.ipc_clock_skew_seconds,
                max_clients=settings.ipc_max_clients,
                read_timeout_seconds=settings.ipc_read_timeout_seconds,
                write_timeout_seconds=settings.ipc_write_timeout_seconds,
                handler_timeout_seconds=settings.ipc_handler_timeout_seconds,
                handlers={
                    **swarm_service.handlers(),
                    **telegram_service.handlers(),
                    **mascot_service.handlers(),
                    **engineering_service.handlers(),
                    **memory_service.handlers(),
                    **conversation_service.handlers(),
                    **audio_service.handlers(),
                    **system_audit_service.handlers(),
                    **speech_service.handlers(),
                    **provider_status_service.handlers(),
                    **settings_service.handlers(),
                    **extensions_service.handlers(),
                    **security_service.handlers(),
                    **runtime_preflight_service.handlers(),
                    **plugin_status_service.handlers(),
                    **mcp_host.ipc_handlers(),
                    **browser_tasks.ipc_handlers(),
                    **activity_service.handlers(),
                    **computer_relay_service.handlers(),
                    **application_qualification_service.handlers(),
                    **browser_qualification_service.handlers(),
                    **capability_service.handlers(),
                    **privacy_service.handlers(),
                    **performance_service.handlers(),
                    **active_vision_service.handlers(),
                    **localized_vision_service.handlers(),
                    **browser_discovery_service.handlers(),
                    **ios_service.ipc_handlers(),
                    **(
                        biometric_training_service.handlers()
                        if biometric_training_service is not None
                        else {}
                    ),
                },
                handler_timeout_overrides={
                    "telegram.configure": 80,
                    localized_vision_service.METHOD: (
                        localized_vision_service.MAXIMUM_HANDLER_SECONDS
                    ),
                    activity_service.WAIT_METHOD: activity_service.MAX_WAIT_SECONDS + 2,
                    swarm_service.WAIT_METHOD: swarm_service.MAX_WAIT_SECONDS + 2,
                    computer_relay_service.WAIT_METHOD: (
                        computer_relay_service.MAX_WAIT_SECONDS + 2
                    ),
                    application_qualification_service.METHOD: (
                        application_qualification_service.MAXIMUM_HANDLER_SECONDS
                    ),
                    browser_qualification_service.METHOD: (
                        browser_qualification_service.MAXIMUM_HANDLER_SECONDS
                    ),
                    speech_service.SYNTHESIZE_METHOD: settings.nvidia_tts_timeout_seconds + 2,
                    speech_service.STREAM_OPEN_METHOD: settings.nvidia_tts_timeout_seconds + 2,
                    speech_service.STREAM_NEXT_METHOD: settings.nvidia_tts_timeout_seconds + 2,
                },
                response_sent_hooks={
                    runtime_preflight_service.METHOD: lambda: (
                        biometric_training_service.arm()
                        if biometric_training_service is not None
                        else None,
                        spotlight_sync.arm(),
                    ),
                },
                security_compromised=lambda: security_state.compromised,
                runtime_suspended=lambda: runtime_state.suspended,
                audit_sink=audit_sink,
                peer_identifier=_peer_identifier(settings),
                peer_client_methods=JARVIS_CLIENT_METHODS,
                unsigned_peer_methods=UNSIGNED_PEER_METHODS,
            )

            def record_background_failure(name: str, error: BaseException) -> None:
                audit_sink.record_system_event(
                    uuid4(),
                    event_type="daemon_background_task_failed",
                    component="supervisor",
                    data={"task": name, "error_type": type(error).__name__},
                )

            background_tasks = BackgroundTaskSupervisor(
                on_failure=record_background_failure,
            )
            try:
                async with daemon:
                    background_tasks.create(telegram_service.run(), name="telegram-polling")
                    background_tasks.create(
                        memory_decay_worker.run(),
                        name="semantic-memory-decay-eviction",
                    )
                    if biometric_training_service is not None:
                        background_tasks.create(
                            biometric_training_service.run(),
                            name="biometric-adaptation-worker",
                        )
                    if settings.spotlight_graph_index_enabled:
                        background_tasks.create(
                            spotlight_sync.run(),
                            name="spotlight-graph-sync",
                        )
                    print(f"status=ready socket={settings.ipc_socket_path}", flush=True)
                    await serve_until_shutdown(daemon, shutdown=shutdown)
            finally:
                # LIFO: stop admission and producers before closing their dependencies.
                # AsyncExitStack still releases later resources if one cleanup fails;
                # any failure skips the controlled audit seal below.
                async with AsyncExitStack() as cleanup:
                    # Cancelling to_thread() only cancels the awaiter. Join actual
                    # SQLite/embedding work before hashing the closing audit file.
                    cleanup.push_async_callback(
                        asyncio.get_running_loop().shutdown_default_executor
                    )
                    cleanup.push_async_callback(nvidia_client.aclose)
                    cleanup.push_async_callback(mcp_host.close)
                    cleanup.push_async_callback(browser_tasks.close)
                    cleanup.push_async_callback(settings_service.close)
                    cleanup.push_async_callback(smart_tv_controller.close)
                    cleanup.push_async_callback(speech_service.close)
                    cleanup.callback(computer_relay.close)
                    cleanup.push_async_callback(jobs.close)
                    cleanup.push_async_callback(owner_profile.close)
                    cleanup.push_async_callback(telegram_service.close)
                    cleanup.push_async_callback(mascot_service.close)
                    cleanup.push_async_callback(background_tasks.close)
                    cleanup.push_async_callback(daemon.close)
                audit_sink.record_system_event(
                    uuid4(),
                    event_type="daemon_shutdown",
                    component="supervisor",
                    data={"state": "controlled"},
                )
                durable_audit_anchor.seal_shutdown(audit_sink)
    except (
        SecretNotFoundError,
        InvalidAuditAnchorError,
        InvalidIpcSecretError,
        InvalidMemorySecretError,
        DaemonSecurityError,
        AuditIntegrityError,
        EvaluationStoreError,
        PerformanceProfilerError,
        MemoryStoreError,
        McpConfigurationError,
        McpProtocolError,
        SpeechArtifactError,
        subprocess.SubprocessError,
        OSError,
        ValueError,
    ) as error:
        print(f"status=error reason={type(error).__name__}")
        return 1
    finally:
        if mcp_host is not None:
            await mcp_host.close()
        if browser_tasks is not None:
            await browser_tasks.close()
    return 0
