from __future__ import annotations

import asyncio
import subprocess
from contextlib import AsyncExitStack
from uuid import uuid4

from aegis_core.activity import SwarmActivityIpcService, SwarmActivityTracker
from aegis_core.application_qualification import (
    ApplicationQualificationIpcService,
    JarvisApplicationQualification,
)
from aegis_core.audio import AudioTelemetryIpcService, AudioTelemetryManager
from aegis_core.audit_anchor import DurableAuditAnchor
from aegis_core.biometric_training_service import (
    BiometricTrainingService,
    VoiceConfirmationVerifier,
)
from aegis_core.brain import (
    HybridBrainClient,
    LocalFoundationCascadeClient,
    MacLocalFoundationClient,
)
from aegis_core.brain.router import RoutingPolicySnapshot
from aegis_core.brain.speculative_engine import SpeculativeEngine
from aegis_core.brain.vision_fallback import (
    LocalizedVisionAnalyzer,
    VisionFallbackIpcService,
)
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
from aegis_core.contracts import AgentRole, ToolAuthorization
from aegis_core.engineering import EngineeringIpcService
from aegis_core.evaluation import EvaluationStoreError, SQLiteEvaluationStore
from aegis_core.ipc.protocol import IpcAuthenticator
from aegis_core.ipc.server import AegisDaemon, DaemonSecurityError
from aegis_core.job_ipc import SwarmIpcService
from aegis_core.jobs import SwarmJobManager
from aegis_core.mcp import McpConfigurationError, McpHostManager, McpProtocolError
from aegis_core.memory import (
    ConversationCoordinator,
    ConversationIpcService,
    EmbeddingBackfillWorker,
    GraphRAGService,
    HybridMemoryRetriever,
    MemoryDecayWorker,
    MemoryIpcService,
    OwnerProfile,
    SocialMemory,
    SQLiteMemoryStore,
)
from aegis_core.memory.errors import MemoryStoreError
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
from aegis_core.providers.apple import AppleLocalModelClient
from aegis_core.providers.apple_embedding import AppleLocalEmbeddingClient
from aegis_core.providers.mlx_distributed import (
    DistributedMLXProvider,
    ThunderboltPeerDiscovery,
    load_cluster_secret,
)
from aegis_core.providers.mlx_provider import (
    MLXProvider,
    MLXVerificationIpcService,
    MLXWhisperTranscriber,
)
from aegis_core.providers.nvidia import NvidiaNimClient
from aegis_core.runtime.lifecycle import BackgroundTaskSupervisor, serve_until_shutdown
from aegis_core.runtime_preflight import RuntimePreflightIpcService
from aegis_core.runtime_state import RuntimeSuspensionController
from aegis_core.secrets import (
    InvalidAuditAnchorError,
    InvalidIpcSecretError,
    InvalidMemorySecretError,
    MacOSAuditAnchor,
    MacOSIpcSecret,
    MacOSKeychain,
    MacOSMemorySecret,
    SecretNotFoundError,
)
from aegis_core.security import AuditIntegrityIpcService, SecurityStateLatch
from aegis_core.skills import SkillRegistry, SkillStore
from aegis_core.speech import (
    SpeechArtifactError,
    SpeechArtifactStore,
    SpeechSynthesisIpcService,
)
from aegis_core.tcc_privacy import TCCPrivacyIpcService
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


def _ipc_authenticator(settings: Settings) -> IpcAuthenticator:
    secret = MacOSIpcSecret(
        service=settings.ipc_keychain_service,
        account=settings.ipc_keychain_account,
    ).get_or_create()
    return IpcAuthenticator.from_hex(secret)


def _plugin_store(settings: Settings) -> PluginStore:
    keychain = MacOSIpcSecret(
        service=settings.ipc_keychain_service,
        account=settings.ipc_keychain_account,
    )
    return PluginStore(settings.plugins_directory, keychain.get_or_create)


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
        await serve_until_shutdown(daemon)
    return 0


async def run_daemon() -> int:
    from aegis_core.orchestration.graph import build_swarm_graph

    settings = Settings()
    local_foundation_client: MacLocalFoundationClient | None = None
    local_model_client: LocalFoundationCascadeClient | None = None
    native_local_model_client: AppleLocalModelClient | None = None
    mlx_verification_service: MLXVerificationIpcService | None = None
    distributed_discovery: ThunderboltPeerDiscovery | None = None
    distributed_provider: DistributedMLXProvider | None = None
    mcp_host: McpHostManager | None = None
    try:
        authenticator = _ipc_authenticator(settings)
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
        plugin_store = _plugin_store(settings)
        plugin_runtime = PluginRuntime(plugin_store.enabled_packages())
        plugin_status_service = PluginStatusIpcService(plugin_runtime)
        audit_sink = HashChainAuditLog(
            settings.ipc_socket_path.parent / "audit.jsonl",
            max_bytes=settings.audit_max_bytes,
        )
        durable_audit_anchor = DurableAuditAnchor(MacOSAuditAnchor())
        security_state = SecurityStateLatch()
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
            )
        mcp_host = McpHostManager.from_file(
            settings.mcp_configuration_path,
            audit_sink=audit_sink,
        )
        await mcp_host.start()
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
                *device_definitions,
            ),
            audit_sink=audit_sink,
        )
        skill_registry = SkillRegistry(
            tool_broker,
            SkillStore(settings.skills_directory),
            plugin_skills=plugin_runtime.skill_manifests(),
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
        if settings.mlx_enabled:
            candidate_mlx_provider = MLXProvider(
                settings.mlx_executable_path,
                model_id=settings.mlx_model_id,
                compact_model_id=settings.mlx_compact_model_id,
                draft_model_id=settings.mlx_draft_model_id,
                draft_model_bytes=settings.mlx_draft_model_bytes,
                timeout_seconds=settings.mlx_timeout_seconds,
                audit_sink=audit_sink,
            )
            if await asyncio.to_thread(candidate_mlx_provider.is_available):
                local_model_client = LocalFoundationCascadeClient(
                    None,
                    candidate_mlx_provider,
                )
                mlx_verification_service = MLXVerificationIpcService(
                    candidate_mlx_provider,
                    audit_sink,
                )
                audit_sink.record_system_event(
                    uuid4(),
                    event_type="local_brain_selected",
                    component="hybrid_brain",
                    data={"provider": "mlx_native", "model": settings.mlx_model_id},
                )
            else:
                await candidate_mlx_provider.aclose()
                audit_sink.record_system_event(
                    uuid4(),
                    event_type="local_brain_unavailable",
                    component="hybrid_brain",
                    data={"provider": "mlx_native", "fallback": "apple_foundation"},
                )
        if local_model_client is None:
            native_local_model_client = AppleLocalModelClient(
                settings.local_brain_executable_path,
                timeout_seconds=settings.local_brain_timeout_seconds,
                first_event_timeout_seconds=settings.local_brain_first_event_timeout_seconds,
            )
            local_foundation_client = MacLocalFoundationClient(
                str(settings.local_foundation_api_url),
                model_id=settings.local_foundation_model_id,
                timeout_seconds=settings.local_foundation_timeout_seconds,
            )
            local_model_client = LocalFoundationCascadeClient(
                local_foundation_client,
                native_local_model_client,
            )
        local_embedding_client = AppleLocalEmbeddingClient(
            settings.local_embedding_executable_path,
            timeout_seconds=settings.local_embedding_timeout_seconds,
        )
        if settings.mlx_distributed_enabled:
            distributed_discovery = ThunderboltPeerDiscovery(
                secret=load_cluster_secret(
                    settings.mlx_distributed_keychain_service,
                    settings.mlx_distributed_keychain_account,
                )
            )

            async def local_distributed_fallback(
                prompt: str,
                maximum_tokens: int,
            ) -> str:
                assert local_model_client is not None
                result = await local_model_client.complete(
                    role=AgentRole.PLANNER,
                    messages=(
                        {
                            "role": "system",
                            "content": "Resolve the request locally and return only the answer.",
                        },
                        {"role": "user", "content": prompt},
                    ),
                    max_tokens=min(maximum_tokens, 1_024),
                    temperature=0.1,
                )
                return result.content

            distributed_provider = DistributedMLXProvider(
                discovery=distributed_discovery,
                hostfile=settings.mlx_distributed_hostfile,
                model_id=settings.mlx_distributed_model_id,
                local_fallback=local_distributed_fallback,
                audit_sink=audit_sink,
                timeout_seconds=settings.mlx_distributed_timeout_seconds,
            )
        provider_status_service = ProviderStatusIpcService(
            nvidia_keychain.is_configured,
            local_model_client.is_available,
        )
        runtime_preflight_service = RuntimePreflightIpcService(
            provider_status_service,
            security_service,
            runtime_power_known=lambda: runtime_state.snapshot() is not None,
        )
        vision_fallback_service = VisionFallbackIpcService(
            LocalizedVisionAnalyzer(
                settings.mlx_vlm_executable_path,
                model_directory=settings.mlx_vlm_model_directory,
                timeout_seconds=settings.mlx_vlm_timeout_seconds,
                audit_sink=audit_sink,
            )
        )
        memory_store = SQLiteMemoryStore(
            settings.memory_database_path,
            max_entries=settings.memory_max_entries,
            max_namespace_entries=settings.memory_namespace_max_entries,
            max_node_embeddings=settings.memory_max_node_embeddings,
            encryption_secret=_memory_encryption_secret(settings),
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
            speculative_engine = SpeculativeEngine(
                nvidia_client,
                verifier_model_id=settings.speculative_verifier_model_id,
                network_deadline_seconds=settings.speculative_verifier_deadline_seconds,
                metric_sink=performance_store.record_speculative_transaction,
                audit_sink=audit_sink,
            )

            def current_routing_policy() -> RoutingPolicySnapshot:
                snapshot = runtime_state.snapshot()
                if snapshot is None:
                    return RoutingPolicySnapshot(
                        cloud_token_threshold=settings.thermal_cloud_token_threshold
                    )
                return RoutingPolicySnapshot(
                    thermal_throttled=snapshot.thermal_state in {"serious", "critical", "unknown"},
                    low_power_mode=snapshot.low_power_mode,
                    on_battery=snapshot.power_source == "battery",
                    cloud_token_threshold=settings.thermal_cloud_token_threshold,
                )

            hybrid_brain = HybridBrainClient(
                local_model_client,
                nvidia_client,
                confidence_threshold=settings.local_foundation_confidence_threshold,
                remote_first_event_timeout_seconds=(
                    settings.hybrid_remote_first_event_timeout_seconds
                ),
                audit_sink=audit_sink,
                speculative_engine=speculative_engine,
                runtime_policy_provider=current_routing_policy,
                distributed_provider=distributed_provider,
            )
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
                max_conversations=settings.conversation_max_sessions,
                max_turns=settings.conversation_max_turns,
            )
            memory_embedding_provider = local_embedding_client
            audit_sink.record_system_event(
                uuid4(),
                event_type="memory_embedding_path",
                component="semantic_memory",
                data={
                    "provider": "apple_natural_language",
                    "maximum_node_embeddings": settings.memory_max_node_embeddings,
                    "dimensions": 384,
                    "model": memory_embedding_provider.model_id,
                },
            )
            graph_service = GraphRAGService(memory_store)
            memory_retriever = HybridMemoryRetriever(
                memory_store,
                embedding_provider=memory_embedding_provider,
                background_gate=runtime_state,
                graph_service=graph_service,
            )
            embedding_backfill_worker = EmbeddingBackfillWorker(
                memory_retriever,
                namespace=settings.memory_rag_namespace,
                limit=settings.memory_embedding_backfill_limit,
                audit_sink=audit_sink,
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
                hybrid_brain,
                local_provider=local_model_client,
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
                dynamic_tool_names=lambda request: mcp_host.tool_names_for_application(
                    request.metadata.get("active_application_bundle_identifier")
                    if isinstance(
                        request.metadata.get("active_application_bundle_identifier"),
                        str,
                    )
                    else None
                ),
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
            )
            swarm_service = SwarmIpcService(jobs)
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
                    **engineering_service.handlers(),
                    **memory_service.handlers(),
                    **conversation_service.handlers(),
                    **audio_service.handlers(),
                    **system_audit_service.handlers(),
                    **speech_service.handlers(),
                    **provider_status_service.handlers(),
                    **security_service.handlers(),
                    **runtime_preflight_service.handlers(),
                    **plugin_status_service.handlers(),
                    **activity_service.handlers(),
                    **computer_relay_service.handlers(),
                    **application_qualification_service.handlers(),
                    **browser_qualification_service.handlers(),
                    **capability_service.handlers(),
                    **privacy_service.handlers(),
                    **performance_service.handlers(),
                    **vision_fallback_service.handlers(),
                    **active_vision_service.handlers(),
                    **browser_discovery_service.handlers(),
                    **ios_service.ipc_handlers(),
                    **(
                        biometric_training_service.handlers()
                        if biometric_training_service is not None
                        else {}
                    ),
                    **(
                        mlx_verification_service.handlers()
                        if mlx_verification_service is not None
                        else {}
                    ),
                },
                handler_timeout_overrides={
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
                        embedding_backfill_worker.arm(),
                        biometric_training_service.arm()
                        if biometric_training_service is not None
                        else None,
                        spotlight_sync.arm(),
                    ),
                },
                security_compromised=lambda: security_state.compromised,
                runtime_suspended=lambda: runtime_state.suspended,
                audit_sink=audit_sink,
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
                    background_tasks.create(
                        embedding_backfill_worker.run(),
                        name="semantic-memory-embedding-backfill",
                    )
                    if native_local_model_client is not None:
                        background_tasks.create(
                            native_local_model_client.prewarm(),
                            name="apple-foundation-model-prewarm",
                        )
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
                    if distributed_discovery is not None:
                        background_tasks.create(
                            distributed_discovery.run(),
                            name="thunderbolt-mlx-discovery",
                        )
                    print(f"status=ready socket={settings.ipc_socket_path}", flush=True)
                    await serve_until_shutdown(daemon)
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
                    cleanup.push_async_callback(local_model_client.aclose)
                    cleanup.push_async_callback(mcp_host.close)
                    cleanup.push_async_callback(smart_tv_controller.close)
                    cleanup.push_async_callback(speech_service.close)
                    cleanup.callback(computer_relay.close)
                    if distributed_discovery is not None:
                        cleanup.callback(distributed_discovery.close)
                    cleanup.push_async_callback(jobs.close)
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
        if local_model_client is not None:
            await local_model_client.aclose()
        elif local_foundation_client is not None:
            await local_foundation_client.aclose()
    return 0
