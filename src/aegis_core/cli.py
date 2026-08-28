from __future__ import annotations

import argparse
import asyncio
import json
import math
import os
import platform
import signal
import subprocess
import sys
import time
from pathlib import Path
from uuid import uuid4

from aegis_core.activity import (
    SwarmActivityIpcService,
    SwarmActivitySnapshot,
    SwarmActivityTracker,
)
from aegis_core.audio import AudioTelemetryIpcService, AudioTelemetryManager
from aegis_core.brain import (
    HybridBrainClient,
    LocalFoundationCascadeClient,
    MacLocalFoundationClient,
)
from aegis_core.capability_blueprints import build_capability_blueprint
from aegis_core.capability_learning import (
    CapabilityLearningCoordinator,
    CapabilityLearningError,
    CapabilityLearningIpcService,
    CapabilityLearningStore,
)
from aegis_core.capability_review_gate import (
    CapabilityReviewError,
    evaluate_capability_review,
    load_capability_review_submission,
)
from aegis_core.capability_reviews import build_capability_review_dossier
from aegis_core.config import Settings
from aegis_core.contracts import AgentRole
from aegis_core.evaluation import EvaluationStoreError, SQLiteEvaluationStore
from aegis_core.ipc.client import IpcClient
from aegis_core.ipc.protocol import IpcAuthenticator, ProtocolError
from aegis_core.ipc.server import AegisDaemon, DaemonSecurityError
from aegis_core.jobs import SwarmIpcService, SwarmJobManager
from aegis_core.memory import (
    ConversationCoordinator,
    ConversationIpcService,
    HybridMemoryRetriever,
    MemoryIpcService,
    OwnerProfile,
    SocialMemory,
    SQLiteMemoryStore,
)
from aegis_core.memory.sqlite import MemoryStoreError
from aegis_core.models import model_for
from aegis_core.plugins import PluginManifest, PluginPackage
from aegis_core.plugins.runtime import PluginRuntime
from aegis_core.plugins.service import PluginStatusIpcService
from aegis_core.plugins.store import (
    PluginError,
    PluginStore,
    load_plugin_package,
    read_plugin_source,
)
from aegis_core.provider_status import ProviderStatusIpcService
from aegis_core.providers.apple import AppleLocalModelClient
from aegis_core.providers.apple_embedding import AppleLocalEmbeddingClient
from aegis_core.providers.base import EmbeddingInputType
from aegis_core.providers.nvidia import NvidiaNimClient, NvidiaNimError
from aegis_core.runtime_preflight import RuntimePreflightIpcService
from aegis_core.secrets import (
    InvalidIpcSecretError,
    InvalidMemorySecretError,
    InvalidPluginSecretError,
    InvalidSecretError,
    MacOSIpcSecret,
    MacOSKeychain,
    MacOSMemorySecret,
    MacOSPluginSecret,
    SecretNotFoundError,
    import_nvidia_key_from_clipboard,
    import_nvidia_key_from_file,
    import_plugin_secret_from_file,
)
from aegis_core.security import AuditIntegrityIpcService, SecurityStateLatch
from aegis_core.skills import SkillError, SkillRegistry, SkillStore, load_skill_draft
from aegis_core.speech import (
    SpeechArtifactError,
    SpeechArtifactStore,
    SpeechSynthesisIpcService,
)
from aegis_core.tools.audit import AuditIntegrityError, HashChainAuditLog
from aegis_core.tools.audit_service import SystemAuditIpcService
from aegis_core.tools.broker import PolicyContext, ToolBroker
from aegis_core.tools.computer import ComputerUseController
from aegis_core.tools.computer_relay import (
    ComputerCommandRelay,
    ComputerRelayIpcService,
    RelayedComputerBridge,
)
from aegis_core.tools.confirmations import OneTimeConfirmationStore
from aegis_core.tools.defaults import build_default_tool_broker, default_policy_context
from aegis_core.tools.execution import ReadOnlyToolExecutor

_VISION_PROBE_DATA_URI = (
    "data:image/png;base64,"
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAIAAACQd1PeAAAADElEQVR4nGP4//8/AAX+Av4N70a4AAAAAElFTkSuQmCC"
)


def _skill_registry(settings: Settings, broker: ToolBroker | None = None) -> SkillRegistry:
    active_broker = broker if broker is not None else build_default_tool_broker()
    return SkillRegistry(active_broker, SkillStore(settings.skills_directory))


def _plugin_store(settings: Settings, *, create_key: bool) -> PluginStore:
    keychain = MacOSIpcSecret(
        service=settings.ipc_keychain_service,
        account=settings.ipc_keychain_account,
    )
    loader = keychain.get_or_create if create_key else keychain.get
    return PluginStore(settings.plugins_directory, loader)


def _validate_plugin_set(packages: tuple[PluginPackage, ...], settings: Settings) -> PluginRuntime:
    runtime = PluginRuntime(packages)
    broker = build_default_tool_broker(runtime.tool_definitions())
    SkillRegistry(
        broker,
        SkillStore(settings.skills_directory),
        plugin_skills=runtime.skill_manifests(),
    )
    return runtime


def plugins_list() -> int:
    settings = Settings()
    try:
        records = _plugin_store(settings, create_key=False).load_all()
    except (OSError, PluginError, SecretNotFoundError, ValueError) as error:
        print(f"status=error reason={type(error).__name__}")
        return 1
    for record in records:
        manifest = record.package.manifest
        print(
            json.dumps(
                {
                    "plugin_id": manifest.plugin_id,
                    "name": manifest.name,
                    "version": manifest.version,
                    "enabled": record.enabled,
                    "skills": len(manifest.skills),
                    "connectors": len(manifest.connectors),
                    "capabilities": sorted(value.value for value in manifest.declared_capabilities),
                    "integrity": "intact",
                },
                ensure_ascii=False,
                separators=(",", ":"),
            )
        )
    print(f"status=ok plugins={len(records)}")
    return 0


def plugins_install(source: Path) -> int:
    settings = Settings()
    try:
        package = load_plugin_package(source)
        store = _plugin_store(settings, create_key=True)
        current = store.load_all()
        candidates = (
            *(
                item.package
                for item in current
                if item.enabled and item.package.manifest.plugin_id != package.manifest.plugin_id
            ),
            package,
        )
        _validate_plugin_set(candidates, settings)
        installed = store.install(package)
    except (OSError, PluginError, SecretNotFoundError, ValueError) as error:
        print(f"status=error reason={type(error).__name__}")
        return 1
    print(
        f"status=ok plugin={installed.package.manifest.plugin_id} "
        f"version={installed.package.manifest.version} enabled=true restart_daemon=true"
    )
    return 0


def plugins_set_enabled(plugin_id: str, enabled: bool) -> int:
    settings = Settings()
    try:
        store = _plugin_store(settings, create_key=False)
        if enabled:
            records = store.load_all()
            target = next(
                (item for item in records if item.package.manifest.plugin_id == plugin_id),
                None,
            )
            if target is None:
                raise PluginError("plugin is not installed")
            candidates = tuple(
                item.package
                for item in records
                if item.enabled or item.package.manifest.plugin_id == plugin_id
            )
            _validate_plugin_set(candidates, settings)
        updated = store.set_enabled(plugin_id, enabled)
    except (OSError, PluginError, SecretNotFoundError, ValueError) as error:
        print(f"status=error reason={type(error).__name__}")
        return 1
    state = "true" if updated.enabled else "false"
    print(f"status=ok plugin={plugin_id} enabled={state} restart_daemon=true")
    return 0


def plugins_remove(plugin_id: str) -> int:
    settings = Settings()
    try:
        store = _plugin_store(settings, create_key=False)
        record = store.get(plugin_id)
        removed = store.uninstall(plugin_id)
        if removed and record is not None:
            for connector in record.package.manifest.connectors:
                MacOSPluginSecret(plugin_id, connector.connector_id).delete()
    except (OSError, PluginError, SecretNotFoundError, ValueError) as error:
        print(f"status=error reason={type(error).__name__}")
        return 1
    print(
        f"status=ok plugin={plugin_id} removed={'true' if removed else 'false'} "
        "credentials_removed=true restart_daemon=true"
    )
    return 0


def plugins_verify() -> int:
    settings = Settings()
    try:
        status = _plugin_store(settings, create_key=False).verify()
    except (OSError, PluginError, SecretNotFoundError, ValueError) as error:
        print(f"status=error reason={type(error).__name__}")
        return 1
    for plugin_id, intact in status.items():
        print(f"plugin={plugin_id} integrity={'intact' if intact else 'compromised'}")
    intact = all(status.values())
    print(f"status={'ok' if intact else 'error'} plugins={len(status)}")
    return 0 if intact else 1


def plugins_simulate(plugin_id: str, request_text: str) -> int:
    settings = Settings()
    try:
        record = _plugin_store(settings, create_key=False).get(plugin_id)
        if record is None or not record.enabled:
            raise PluginError("plugin is not installed and enabled")
        runtime = PluginRuntime((record.package,))
        broker = build_default_tool_broker(runtime.tool_definitions())
        registry = SkillRegistry(
            broker,
            SkillStore(settings.skills_directory),
            builtins=(),
            plugin_skills=runtime.skill_manifests(),
        )
        activation = registry.select(request_text)
        if activation is None:
            print(
                json.dumps(
                    {
                        "plugin_id": plugin_id,
                        "matched": False,
                        "execution": "none",
                    },
                    separators=(",", ":"),
                )
            )
            print("status=ok matched=false execution=none")
            return 0
        tools = []
        for name in sorted(activation.manifest.allowed_tools):
            definition = broker.definition(name)
            if definition is None:
                raise PluginError("simulated skill references an unavailable tool")
            tools.append(
                {
                    "name": name,
                    "risk": definition.risk.value,
                    "confirmation": (
                        definition.requires_confirmation
                        or definition.risk.value in {"high", "critical"}
                    ),
                    "destination": definition.external_destination,
                }
            )
        print(
            json.dumps(
                {
                    "plugin_id": plugin_id,
                    "matched": True,
                    "skill_id": activation.manifest.skill_id,
                    "score": activation.score,
                    "tools": tools,
                    "execution": "none",
                },
                ensure_ascii=False,
                separators=(",", ":"),
            )
        )
    except (OSError, PluginError, SecretNotFoundError, ValueError) as error:
        print(f"status=error reason={type(error).__name__}")
        return 1
    print("status=ok matched=true execution=none")
    return 0


def plugins_pack(source: Path) -> int:
    try:
        raw = json.loads(read_plugin_source(source))
        if not isinstance(raw, dict) or set(raw) != {"manifest", "resources"}:
            raise PluginError("plugin draft structure is invalid")
        manifest = PluginManifest.model_validate(raw["manifest"])
        resources = raw["resources"]
        if not isinstance(resources, dict) or not all(
            isinstance(key, str) and isinstance(value, str) for key, value in resources.items()
        ):
            raise PluginError("plugin resources are invalid")
        package = PluginPackage.create(manifest, resources)
        target = source.with_name(f"{source.stem}.jarvis-plugin.json")
        descriptor = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(package.model_dump_json(indent=2).encode("utf-8") + b"\n")
            handle.flush()
            os.fsync(handle.fileno())
    except (OSError, PluginError, ValueError, json.JSONDecodeError) as error:
        print(f"status=error reason={type(error).__name__}")
        return 1
    print(f"status=ok plugin={manifest.plugin_id} package={target}")
    return 0


def plugins_import_credential(plugin_id: str, connector_id: str, source: Path) -> int:
    settings = Settings()
    try:
        record = _plugin_store(settings, create_key=False).get(plugin_id)
        if record is None:
            raise PluginError("plugin is not installed")
        connector = next(
            (
                item
                for item in record.package.manifest.connectors
                if item.connector_id == connector_id and item.auth.value == "bearer"
            ),
            None,
        )
        if connector is None:
            raise PluginError("plugin connector does not accept a bearer credential")
        import_plugin_secret_from_file(MacOSPluginSecret(plugin_id, connector_id), source)
    except (
        OSError,
        InvalidPluginSecretError,
        PluginError,
        SecretNotFoundError,
        ValueError,
    ) as error:
        print(f"status=error reason={type(error).__name__}")
        return 1
    print(f"status=ok plugin={plugin_id} connector={connector_id} credential=keychain")
    return 0


def skills_list() -> int:
    settings = Settings()
    try:
        skills = _skill_registry(settings).all()
    except (OSError, SkillError, ValueError) as error:
        print(f"status=error reason={type(error).__name__}")
        return 1
    for skill in skills:
        print(
            json.dumps(
                {
                    "skill_id": skill.skill_id,
                    "name": skill.name,
                    "origin": skill.origin.value,
                    "role": skill.role.value,
                    "enabled": skill.enabled,
                    "allowed_tools": sorted(skill.allowed_tools),
                },
                ensure_ascii=False,
                separators=(",", ":"),
            )
        )
    print(f"status=ok skills={len(skills)}")
    return 0


def skills_learn(source: Path) -> int:
    settings = Settings()
    try:
        draft = load_skill_draft(source)
        learned = _skill_registry(settings).learn(draft)
    except (OSError, SkillError, ValueError) as error:
        print(f"status=error reason={type(error).__name__}")
        return 1
    print(f"status=ok skill={learned.skill_id} origin={learned.origin.value} remote=false")
    return 0


def skills_forget(skill_id: str) -> int:
    settings = Settings()
    try:
        forgotten = _skill_registry(settings).forget(skill_id)
    except (OSError, SkillError, ValueError) as error:
        print(f"status=error reason={type(error).__name__}")
        return 1
    print(f"status=ok skill={skill_id} forgotten={'true' if forgotten else 'false'}")
    return 0


def capabilities_list() -> int:
    settings = Settings()
    try:
        records = CapabilityLearningStore(settings.capability_learning_directory).load_all()
    except (CapabilityLearningError, OSError, ValueError) as error:
        print(f"status=error reason={type(error).__name__}")
        return 1
    ranked = sorted(
        ((build_capability_blueprint(record), record) for record in records),
        key=lambda item: (item[0].priority_score, item[1].last_seen_at, item[0].gap_id),
        reverse=True,
    )
    for blueprint, record in ranked:
        print(
            json.dumps(
                {
                    "gap_id": record.gap_id,
                    "goal": record.normalized_goal,
                    "status": record.status.value,
                    "occurrences": record.occurrences,
                    "researched_at": (
                        record.researched_at.isoformat() if record.researched_at else None
                    ),
                    "expires_at": record.expires_at.isoformat() if record.expires_at else None,
                    "sources": len(record.sources),
                    "readiness": blueprint.readiness.value,
                    "integration_path": blueprint.integration_path.value,
                    "risk": blueprint.risk.value,
                    "priority_score": blueprint.priority_score,
                },
                ensure_ascii=False,
                separators=(",", ":"),
            )
        )
    researched = sum(record.status.value == "researched" for record in records)
    print(f"status=ok capabilities={len(records)} researched={researched}")
    return 0


def capabilities_inspect(gap_id: str) -> int:
    settings = Settings()
    try:
        record = CapabilityLearningStore(settings.capability_learning_directory).get(gap_id)
    except (CapabilityLearningError, OSError, ValueError) as error:
        print(f"status=error reason={type(error).__name__}")
        return 1
    if record is None:
        print("status=error reason=capability_not_found")
        return 1
    blueprint = build_capability_blueprint(record)
    print(blueprint.model_dump_json())
    print(
        f"status=ok capability={gap_id} readiness={blueprint.readiness.value} "
        f"risk={blueprint.risk.value}"
    )
    return 0


def capabilities_plan(gap_id: str) -> int:
    settings = Settings()
    try:
        record = CapabilityLearningStore(settings.capability_learning_directory).get(gap_id)
    except (CapabilityLearningError, OSError, ValueError) as error:
        print(f"status=error reason={type(error).__name__}")
        return 1
    if record is None:
        print("status=error reason=capability_not_found")
        return 1
    dossier = build_capability_review_dossier(record)
    print(dossier.model_dump_json(indent=2))
    print(
        f"status=ok capability={gap_id} gate={dossier.review_gate.value} "
        f"executable=false"
    )
    return 0


def capabilities_review(source: Path) -> int:
    settings = Settings()
    try:
        submission = load_capability_review_submission(source)
        record = CapabilityLearningStore(settings.capability_learning_directory).get(
            submission.gap_id
        )
        if record is None:
            raise CapabilityReviewError("capability does not exist")
        verdict = evaluate_capability_review(record, submission)
    except (CapabilityReviewError, OSError, ValueError) as error:
        print(f"status=error reason={type(error).__name__}")
        return 1
    print(verdict.model_dump_json(indent=2))
    print(
        f"status=ok capability={verdict.gap_id} review={verdict.status.value} "
        "executable=false"
    )
    return 0


def capabilities_forget(gap_id: str) -> int:
    settings = Settings()
    try:
        forgotten = CapabilityLearningStore(settings.capability_learning_directory).forget(gap_id)
    except (CapabilityLearningError, OSError, ValueError) as error:
        print(f"status=error reason={type(error).__name__}")
        return 1
    print(f"status=ok capability={gap_id} forgotten={'true' if forgotten else 'false'}")
    return 0


def doctor() -> int:
    settings = Settings()
    architecture = platform.machine()
    print(f"architecture={architecture}")
    print(f"python={platform.python_version()}")
    print(f"nvidia_base_url={settings.nvidia_base_url}")
    local_embedding = AppleLocalEmbeddingClient(
        settings.local_embedding_executable_path,
        timeout_seconds=settings.local_embedding_timeout_seconds,
    )
    print(
        "local_semantic_memory="
        f"{'available' if local_embedding.is_available() else 'unavailable'} "
        f"model={local_embedding.model_id}"
    )
    print(
        "memory_vector_acceleration="
        f"{'available' if SQLiteMemoryStore.vector_acceleration_available() else 'fallback'} "
        "engine=sqlite-vec"
    )

    if architecture != "arm64":
        print("status=error reason=non_arm64_runtime")
        return 1

    keychain = MacOSKeychain(
        service=settings.nvidia_keychain_service,
        account=settings.nvidia_keychain_account,
    )
    try:
        keychain.get()
    except (SecretNotFoundError, InvalidSecretError):
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
    except (SecretNotFoundError, InvalidSecretError, NvidiaNimError, OSError) as error:
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
    except (SecretNotFoundError, InvalidSecretError, NvidiaNimError, OSError, ValueError) as error:
        print(f"status=error reason={type(error).__name__}")
        return 1
    print(f"status=ok model={batch.model_id} dimensions={batch.dimensions} credential=keychain")
    return 0


async def probe_nvidia_tts() -> int:
    settings = Settings()
    keychain = MacOSKeychain(
        service=settings.nvidia_keychain_service,
        account=settings.nvidia_keychain_account,
    )
    try:
        async with NvidiaNimClient(settings, keychain.get) as client:
            started_at = time.perf_counter()
            first_audio_ms: int | None = None
            audio_bytes = 0
            chunks = 0
            async for chunk in client.stream_speech("Sistemas en línea."):
                if first_audio_ms is None:
                    first_audio_ms = round((time.perf_counter() - started_at) * 1_000)
                audio_bytes += len(chunk)
                chunks += 1
            if first_audio_ms is None or not audio_bytes or audio_bytes % 2:
                raise NvidiaNimError("NVIDIA NIM speech returned invalid audio")
    except (SecretNotFoundError, InvalidSecretError, NvidiaNimError, OSError, ValueError) as error:
        print(f"status=error reason={type(error).__name__}")
        return 1
    print(
        f"status=ok voice={settings.nvidia_tts_voice} audio_bytes={audio_bytes} "
        f"chunks={chunks} first_audio_ms={first_audio_ms} mode=stream "
        "credential=keychain playback=none"
    )
    return 0


async def probe_nvidia_vision() -> int:
    settings = Settings()
    keychain = MacOSKeychain(
        service=settings.nvidia_keychain_service,
        account=settings.nvidia_keychain_account,
    )
    try:
        async with NvidiaNimClient(settings, keychain.get) as client:
            result = await client.complete(
                role=AgentRole.VISION,
                messages=[
                    {
                        "role": "user",
                        "content": [
                            {"type": "text", "text": "Reply with exactly: OK"},
                            {
                                "type": "image_url",
                                "image_url": {"url": _VISION_PROBE_DATA_URI},
                            },
                        ],
                    }
                ],
                max_tokens=16,
                temperature=0.0,
            )
    except (SecretNotFoundError, InvalidSecretError, NvidiaNimError, OSError, ValueError) as error:
        print(f"status=error reason={type(error).__name__}")
        return 1
    if not result.content.strip():
        print("status=error reason=invalid_vision_response")
        return 1
    print(f"status=ok model={result.model_id} input=synthetic credential=keychain")
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
    except (SecretNotFoundError, InvalidSecretError, NvidiaNimError, OSError, ValueError) as error:
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


async def probe_nvidia_swarm() -> int:
    settings = Settings()
    keychain = MacOSKeychain(
        service=settings.nvidia_keychain_service,
        account=settings.nvidia_keychain_account,
    )
    roles = (
        AgentRole.PLANNER,
        AgentRole.CODE_SECURITY,
        AgentRole.CRITICAL_REASONER,
        AgentRole.VISION,
    )
    try:
        async with NvidiaNimClient(settings, keychain.get) as client:
            for role in roles:
                content: str | list[dict[str, object]] = "Reply with exactly: OK"
                if role is AgentRole.VISION:
                    content = [
                        {"type": "text", "text": "Reply with exactly: OK"},
                        {
                            "type": "image_url",
                            "image_url": {"url": _VISION_PROBE_DATA_URI},
                        },
                    ]
                result = await client.complete(
                    role=role,
                    messages=[{"role": "user", "content": content}],
                    max_tokens=128,
                    temperature=0.0,
                )
                expected = model_for(role).model_id
                if result.model_id != expected:
                    result = await client.complete(
                        role=role,
                        messages=[{"role": "user", "content": content}],
                        max_tokens=128,
                        temperature=0.0,
                    )
                status = "ok" if result.model_id == expected else "degraded"
                print(
                    f"status={status} role={role.value} model={result.model_id} "
                    f"primary={expected} credential=keychain",
                    flush=True,
                )
                if status != "ok":
                    return 1
    except (SecretNotFoundError, InvalidSecretError, NvidiaNimError, OSError, ValueError) as error:
        print(f"status=error reason={type(error).__name__}")
        return 1
    return 0


def verify_audit(
    path: Path,
    max_bytes: int = HashChainAuditLog.DEFAULT_MAX_BYTES,
) -> int:
    try:
        records = HashChainAuditLog(path, max_bytes=max_bytes).verify()
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


def _memory_encryption_secret(settings: Settings) -> bytes:
    secret_store = MacOSMemorySecret(
        service=settings.memory_keychain_service,
        account=settings.memory_keychain_account,
    )
    if SQLiteMemoryStore.encryption_key_initialized(settings.memory_database_path):
        return secret_store.get()
    return secret_store.get_or_create()


async def run_daemon() -> int:
    from aegis_core.orchestration.graph import build_swarm_graph

    settings = Settings()
    local_foundation_client: MacLocalFoundationClient | None = None
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
        plugin_store = _plugin_store(settings, create_key=True)
        plugin_runtime = PluginRuntime(plugin_store.enabled_packages())
        plugin_status_service = PluginStatusIpcService(plugin_runtime)
        tool_broker = build_default_tool_broker(plugin_runtime.tool_definitions())
        skill_registry = SkillRegistry(
            tool_broker,
            SkillStore(settings.skills_directory),
            plugin_skills=plugin_runtime.skill_manifests(),
        )
        audit_sink = HashChainAuditLog(
            settings.ipc_socket_path.parent / "audit.jsonl",
            max_bytes=settings.audit_max_bytes,
        )
        security_state = SecurityStateLatch()
        security_service = AuditIntegrityIpcService(audit_sink, security_state)

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
        nvidia_keychain = MacOSKeychain(
            service=settings.nvidia_keychain_service,
            account=settings.nvidia_keychain_account,
        )
        native_local_model_client = AppleLocalModelClient(
            settings.local_brain_executable_path,
            timeout_seconds=settings.local_brain_timeout_seconds,
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
        provider_status_service = ProviderStatusIpcService(
            nvidia_keychain.is_configured,
            local_model_client.is_available,
        )
        runtime_preflight_service = RuntimePreflightIpcService(
            provider_status_service,
            security_service,
        )
        memory_store = SQLiteMemoryStore(
            settings.memory_database_path,
            max_entries=settings.memory_max_entries,
            max_vectors=settings.memory_max_vectors,
            encryption_secret=_memory_encryption_secret(settings),
            on_auth_failure=handle_memory_auth_failure,
        )
        memory_store.initialize()
        evaluation_store = SQLiteEvaluationStore(
            settings.evaluation_database_path,
            max_entries=settings.evaluation_max_entries,
        )
        evaluation_store.initialize()
        async with NvidiaNimClient(settings, nvidia_keychain.get) as nvidia_client:
            hybrid_brain = HybridBrainClient(
                local_model_client,
                nvidia_client,
                confidence_threshold=settings.local_foundation_confidence_threshold,
                audit_sink=audit_sink,
            )
            tool_executor = ReadOnlyToolExecutor(
                computer_controller=ComputerUseController(
                    nvidia_client,
                    bridge=RelayedComputerBridge(computer_relay),
                    activity_tracker=activity_tracker,
                ),
                extra_handlers=plugin_runtime.handlers(),
            )
            conversations = ConversationCoordinator(
                memory_store,
                namespace=settings.memory_rag_namespace,
                history_limit=settings.conversation_history_turns,
                max_conversations=settings.conversation_max_sessions,
                max_turns=settings.conversation_max_turns,
            )
            memory_embedding_provider = (
                nvidia_client
                if settings.memory_remote_embeddings_enabled
                else local_embedding_client
            )
            audit_sink.record_system_event(
                uuid4(),
                event_type="memory_embedding_path",
                component="semantic_memory",
                data={
                    "provider": (
                        "nvidia_nim"
                        if settings.memory_remote_embeddings_enabled
                        else "apple_natural_language"
                    ),
                    "maximum_vectors": settings.memory_max_vectors,
                    "model": memory_embedding_provider.model_id,
                },
            )
            memory_retriever = HybridMemoryRetriever(
                memory_store,
                embedding_provider=memory_embedding_provider,
                vector_scan_limit=settings.memory_vector_scan_limit,
            )
            embedding_backfill_task = asyncio.create_task(
                memory_retriever.backfill(
                    namespace=settings.memory_rag_namespace,
                    limit=settings.memory_embedding_backfill_limit,
                ),
                name="semantic-memory-embedding-backfill",
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
            )
            swarm_service = SwarmIpcService(jobs)
            memory_service = MemoryIpcService(
                memory_store,
                retriever=memory_retriever,
            )
            conversation_service = ConversationIpcService(memory_store, conversations)
            audio_service = AudioTelemetryIpcService(AudioTelemetryManager())
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
                    **capability_service.handlers(),
                },
                handler_timeout_overrides={
                    activity_service.WAIT_METHOD: activity_service.MAX_WAIT_SECONDS + 2,
                    swarm_service.WAIT_METHOD: swarm_service.MAX_WAIT_SECONDS + 2,
                    computer_relay_service.WAIT_METHOD: (
                        computer_relay_service.MAX_WAIT_SECONDS + 2
                    ),
                    speech_service.SYNTHESIZE_METHOD: settings.nvidia_tts_timeout_seconds + 2,
                    speech_service.STREAM_OPEN_METHOD: settings.nvidia_tts_timeout_seconds + 2,
                    speech_service.STREAM_NEXT_METHOD: settings.nvidia_tts_timeout_seconds + 2,
                },
                security_compromised=lambda: security_state.compromised,
                audit_sink=audit_sink,
            )
            try:
                async with daemon:
                    print(f"status=ready socket={settings.ipc_socket_path}", flush=True)
                    await daemon.serve_forever()
            finally:
                embedding_backfill_task.cancel()
                await asyncio.gather(embedding_backfill_task, return_exceptions=True)
                computer_relay.close()
                await speech_service.close()
                await jobs.close()
    except (
        SecretNotFoundError,
        InvalidIpcSecretError,
        InvalidMemorySecretError,
        DaemonSecurityError,
        AuditIntegrityError,
        EvaluationStoreError,
        MemoryStoreError,
        SpeechArtifactError,
        OSError,
        ValueError,
    ) as error:
        print(f"status=error reason={type(error).__name__}")
        return 1
    finally:
        if local_foundation_client is not None:
            await local_foundation_client.aclose()
    return 0


async def daemon_status() -> int:
    settings = Settings()
    try:
        authenticator = _ipc_authenticator(settings, create=False)
        client = IpcClient(
            settings.ipc_socket_path,
            authenticator,
            max_frame_bytes=settings.ipc_max_frame_bytes,
            max_message_bytes=settings.ipc_max_message_bytes,
            clock_skew_seconds=settings.ipc_clock_skew_seconds,
        )
        response = await client.call("health")
        provider_response = await client.call("provider.status")
        security_response = await client.call("security.status")
        activity_response = await client.call("swarm.activity")
        plugin_response = await client.call("plugins.status")
        capability_response = await client.call("capabilities.status")
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
    if not provider_response.ok:
        print(f"status=error reason={provider_response.error_code}")
        return 1
    if not activity_response.ok:
        print(f"status=error reason={activity_response.error_code}")
        return 1
    if not plugin_response.ok:
        print(f"status=error reason={plugin_response.error_code}")
        return 1
    if not capability_response.ok:
        print(f"status=error reason={capability_response.error_code}")
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
    provider = provider_response.payload.get("provider")
    credential = provider_response.payload.get("credential")
    if provider != "nvidia_nim" or credential not in {
        "configured",
        "missing",
        "unavailable",
    }:
        print("status=error reason=invalid_provider_response")
        return 1
    try:
        activity = SwarmActivitySnapshot.model_validate(activity_response.payload)
    except ValueError:
        print("status=error reason=invalid_activity_response")
        return 1
    active_agents = sum(agent.active_jobs for agent in activity.agents)
    plugins = plugin_response.payload.get("plugins")
    plugin_protocol = plugin_response.payload.get("protocol_version")
    if not isinstance(plugins, list) or not isinstance(plugin_protocol, str):
        print("status=error reason=invalid_plugin_response")
        return 1
    capability_total = capability_response.payload.get("total")
    capability_researched = capability_response.payload.get("researched")
    capability_observed = capability_response.payload.get("observed")
    if (
        type(capability_total) is not int
        or type(capability_researched) is not int
        or type(capability_observed) is not int
        or min(capability_total, capability_researched, capability_observed) < 0
        or capability_researched + capability_observed != capability_total
    ):
        print("status=error reason=invalid_capability_response")
        return 1
    local_model = provider_response.payload.get("local_model", "unavailable")
    if local_model not in {"available", "unavailable"}:
        print("status=error reason=invalid_provider_response")
        return 1
    print(
        f"status=ok protocol={protocol} architecture={architecture} "
        f"security={security} provider={credential} local_model={local_model} "
        f"active_agents={active_agents} plugins={len(plugins)} mcp={plugin_protocol} "
        f"capabilities={capability_total} researched={capability_researched}"
    )
    return 0


async def self_evaluation() -> int:
    settings = Settings()
    try:
        authenticator = _ipc_authenticator(settings, create=False)
        client = IpcClient(
            settings.ipc_socket_path,
            authenticator,
            max_frame_bytes=settings.ipc_max_frame_bytes,
            max_message_bytes=settings.ipc_max_message_bytes,
            clock_skew_seconds=settings.ipc_clock_skew_seconds,
        )
        response = await client.call("jobs.metrics")
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
    print(json.dumps(response.payload, ensure_ascii=False, separators=(",", ":")))
    return 0


def _daemon_launch_agent_loaded() -> bool:
    try:
        result = subprocess.run(
            ["/bin/launchctl", "print", f"gui/{os.getuid()}/ai.aegis.daemon"],
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=5,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return result.returncode == 0


async def daemon_recovery(*, attempts: int = 40, interval_seconds: float = 0.25) -> int:
    if not 1 <= attempts <= 120 or not 0 <= interval_seconds <= 5:
        print("status=error reason=invalid_recovery_config")
        return 2
    if not _daemon_launch_agent_loaded():
        print("status=blocked reason=daemon_service_not_loaded")
        return 2
    settings = Settings()
    try:
        authenticator = _ipc_authenticator(settings, create=False)
        client = IpcClient(
            settings.ipc_socket_path,
            authenticator,
            max_frame_bytes=settings.ipc_max_frame_bytes,
            max_message_bytes=settings.ipc_max_message_bytes,
            clock_skew_seconds=settings.ipc_clock_skew_seconds,
        )
        health, security, activity_response = await asyncio.gather(
            client.call("health"),
            client.call("security.status"),
            client.call("swarm.activity"),
        )
        if (
            not health.ok
            or not security.ok
            or not activity_response.ok
            or health.payload.get("protocol_version") != "1.0"
            or health.payload.get("architecture") != "arm64"
            or security.payload.get("state") != "intact"
        ):
            print("status=error reason=recovery_precondition_failed")
            return 1
        activity = SwarmActivitySnapshot.model_validate(activity_response.payload)
        if activity.agents:
            print("status=blocked reason=daemon_busy")
            return 2
        old_pid = health.payload.get("pid")
        if (
            isinstance(old_pid, bool)
            or not isinstance(old_pid, int)
            or old_pid <= 1
            or old_pid == os.getpid()
        ):
            print("status=error reason=invalid_health_response")
            return 1
        os.kill(old_pid, signal.SIGTERM)
        for _ in range(attempts):
            if interval_seconds:
                await asyncio.sleep(interval_seconds)
            try:
                restarted = await client.call("health")
            except (TimeoutError, ProtocolError, ConnectionError, OSError):
                continue
            new_pid = restarted.payload.get("pid")
            if (
                not restarted.ok
                or isinstance(new_pid, bool)
                or not isinstance(new_pid, int)
                or new_pid <= 1
                or new_pid == old_pid
                or restarted.payload.get("protocol_version") != "1.0"
                or restarted.payload.get("architecture") != "arm64"
            ):
                continue
            try:
                restarted_security = await client.call("security.status")
            except (TimeoutError, ProtocolError, ConnectionError, OSError):
                continue
            if restarted_security.ok and restarted_security.payload.get("state") == "intact":
                print("status=ok restart=verified security=intact")
                return 0
    except (
        TimeoutError,
        SecretNotFoundError,
        InvalidIpcSecretError,
        ProtocolError,
        OSError,
        ValueError,
    ) as error:
        print(f"status=error reason={type(error).__name__}")
        return 1
    print("status=error reason=daemon_restart_timeout")
    return 1


async def daemon_soak(
    *,
    cycles: int = 100,
    max_p95_ms: float = 250.0,
    max_rss_growth_bytes: int = 8 * 1_024 * 1_024,
) -> int:
    if (
        not 1 <= cycles <= 10_000
        or not 1.0 <= max_p95_ms <= 5_000.0
        or not 0 <= max_rss_growth_bytes <= 1_024 * 1_024 * 1_024
    ):
        print("status=error reason=invalid_soak_config")
        return 2
    settings = Settings()
    try:
        authenticator = _ipc_authenticator(settings, create=False)
        client = IpcClient(
            settings.ipc_socket_path,
            authenticator,
            max_frame_bytes=settings.ipc_max_frame_bytes,
            max_message_bytes=settings.ipc_max_message_bytes,
            clock_skew_seconds=settings.ipc_clock_skew_seconds,
        )
        latencies: list[float] = []
        daemon_pid: int | None = None
        first_cpu_seconds: float | None = None
        first_peak_rss_bytes: int | None = None
        last_cpu_seconds = 0.0
        last_peak_rss_bytes = 0
        for _ in range(cycles):
            started = time.perf_counter()
            health, runtime, metrics, security, activity = await asyncio.gather(
                client.call("health"),
                client.call("runtime.info"),
                client.call("runtime.metrics"),
                client.call("security.status"),
                client.call("swarm.activity"),
            )
            latencies.append((time.perf_counter() - started) * 1_000)
            if not all(response.ok for response in (health, runtime, metrics, security, activity)):
                print("status=error reason=soak_response_failed")
                return 1
            current_pid = health.payload.get("pid")
            if (
                isinstance(current_pid, bool)
                or not isinstance(current_pid, int)
                or current_pid <= 0
            ):
                print("status=error reason=invalid_health_response")
                return 1
            if daemon_pid is None:
                daemon_pid = current_pid
            elif current_pid != daemon_pid:
                print("status=error reason=daemon_restarted")
                return 1
            if (
                health.payload.get("protocol_version") != "1.0"
                or health.payload.get("architecture") != "arm64"
                or runtime.payload.get("architecture") != "arm64"
                or runtime.payload.get("operating_system") != "Darwin"
                or security.payload.get("state") != "intact"
            ):
                print("status=error reason=invalid_soak_response")
                return 1
            SwarmActivitySnapshot.model_validate(activity.payload)
            uptime_seconds = metrics.payload.get("uptime_seconds")
            cpu_seconds = metrics.payload.get("cpu_seconds")
            peak_rss_bytes = metrics.payload.get("peak_rss_bytes")
            if (
                set(metrics.payload) != {"uptime_seconds", "cpu_seconds", "peak_rss_bytes"}
                or isinstance(uptime_seconds, bool)
                or not isinstance(uptime_seconds, (int, float))
                or not math.isfinite(uptime_seconds)
                or uptime_seconds < 0
                or isinstance(cpu_seconds, bool)
                or not isinstance(cpu_seconds, (int, float))
                or not math.isfinite(cpu_seconds)
                or cpu_seconds < 0
                or isinstance(peak_rss_bytes, bool)
                or not isinstance(peak_rss_bytes, int)
                or peak_rss_bytes <= 0
            ):
                print("status=error reason=invalid_metrics_response")
                return 1
            last_cpu_seconds = float(cpu_seconds)
            last_peak_rss_bytes = peak_rss_bytes
            if first_cpu_seconds is None:
                first_cpu_seconds = last_cpu_seconds
                first_peak_rss_bytes = last_peak_rss_bytes
    except (
        TimeoutError,
        SecretNotFoundError,
        InvalidIpcSecretError,
        ProtocolError,
        OSError,
        ValueError,
    ) as error:
        print(f"status=error reason={type(error).__name__}")
        return 1

    ordered = sorted(latencies)
    p95 = ordered[max(0, (95 * len(ordered) + 99) // 100 - 1)]
    peak = ordered[-1]
    if first_cpu_seconds is None or first_peak_rss_bytes is None:
        print("status=error reason=missing_metrics_response")
        return 1
    cpu_ms = max(0.0, last_cpu_seconds - first_cpu_seconds) * 1_000
    rss_growth_bytes = max(0, last_peak_rss_bytes - first_peak_rss_bytes)
    if rss_growth_bytes > max_rss_growth_bytes:
        print(
            f"status=error reason=memory_growth_budget_exceeded cycles={cycles} "
            f"rss_growth_kib={rss_growth_bytes / 1_024:.2f}"
        )
        return 1
    if p95 > max_p95_ms:
        print(
            f"status=error reason=latency_budget_exceeded cycles={cycles} "
            f"p95_ms={p95:.2f} max_ms={peak:.2f}"
        )
        return 1
    print(
        f"status=ok cycles={cycles} p95_ms={p95:.2f} max_ms={peak:.2f} "
        f"rss_growth_kib={rss_growth_bytes / 1_024:.2f} cpu_ms={cpu_ms:.2f}"
    )
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(prog="aegis")
    parser.add_argument(
        "command",
        choices=[
            "doctor",
            "daemon",
            "daemon-recovery",
            "daemon-soak",
            "daemon-status",
            "capabilities-forget",
            "capabilities-inspect",
            "capabilities-list",
            "capabilities-plan",
            "capabilities-review",
            "import-nvidia-key",
            "import-nvidia-key-file",
            "probe-nvidia",
            "probe-nvidia-embedding",
            "probe-nvidia-tts",
            "probe-nvidia-vision",
            "probe-nvidia-tools",
            "probe-nvidia-swarm",
            "plugins-credential-import",
            "plugins-disable",
            "plugins-enable",
            "plugins-install",
            "plugins-list",
            "plugins-pack",
            "plugins-remove",
            "plugins-simulate",
            "plugins-verify",
            "self-evaluation",
            "skills-forget",
            "skills-learn",
            "skills-list",
            "verify-audit",
        ],
    )
    parser.add_argument("resource_path", nargs="?", type=Path)
    parser.add_argument("--connector")
    parser.add_argument("--request")
    args = parser.parse_args()
    if args.command == "doctor":
        raise SystemExit(doctor())
    if args.command == "daemon":
        raise SystemExit(asyncio.run(run_daemon()))
    if args.command == "daemon-status":
        raise SystemExit(asyncio.run(daemon_status()))
    if args.command == "self-evaluation":
        raise SystemExit(asyncio.run(self_evaluation()))
    if args.command == "capabilities-list":
        raise SystemExit(capabilities_list())
    if args.command == "capabilities-inspect":
        if args.resource_path is None:
            parser.error("capabilities-inspect requires gap_id")
        raise SystemExit(capabilities_inspect(str(args.resource_path)))
    if args.command == "capabilities-plan":
        if args.resource_path is None:
            parser.error("capabilities-plan requires gap_id")
        raise SystemExit(capabilities_plan(str(args.resource_path)))
    if args.command == "capabilities-review":
        if args.resource_path is None:
            parser.error("capabilities-review requires review_json_path")
        raise SystemExit(capabilities_review(args.resource_path))
    if args.command == "capabilities-forget":
        if args.resource_path is None:
            parser.error("capabilities-forget requires gap_id")
        raise SystemExit(capabilities_forget(str(args.resource_path)))
    if args.command == "skills-list":
        raise SystemExit(skills_list())
    if args.command == "skills-learn":
        if args.resource_path is None:
            parser.error("skills-learn requires skill_json_path")
        raise SystemExit(skills_learn(args.resource_path))
    if args.command == "skills-forget":
        if args.resource_path is None:
            parser.error("skills-forget requires skill_id")
        raise SystemExit(skills_forget(str(args.resource_path)))
    if args.command == "plugins-list":
        raise SystemExit(plugins_list())
    if args.command == "plugins-verify":
        raise SystemExit(plugins_verify())
    if args.command == "plugins-simulate":
        if args.resource_path is None or args.request is None:
            parser.error("plugins-simulate requires plugin_id and --request")
        raise SystemExit(plugins_simulate(str(args.resource_path), args.request))
    if args.command == "plugins-pack":
        if args.resource_path is None:
            parser.error("plugins-pack requires plugin_draft_path")
        raise SystemExit(plugins_pack(args.resource_path))
    if args.command == "plugins-install":
        if args.resource_path is None:
            parser.error("plugins-install requires plugin_package_path")
        raise SystemExit(plugins_install(args.resource_path))
    if args.command in {"plugins-enable", "plugins-disable", "plugins-remove"}:
        if args.resource_path is None:
            parser.error(f"{args.command} requires plugin_id")
        plugin_id = str(args.resource_path)
        if args.command == "plugins-enable":
            raise SystemExit(plugins_set_enabled(plugin_id, True))
        if args.command == "plugins-disable":
            raise SystemExit(plugins_set_enabled(plugin_id, False))
        raise SystemExit(plugins_remove(plugin_id))
    if args.command == "plugins-credential-import":
        if args.resource_path is None or args.connector is None:
            parser.error("plugins-credential-import requires credential_path and --connector")
        if "." not in args.connector:
            parser.error("--connector must use plugin_id.connector_id")
        plugin_id, connector_id = args.connector.split(".", 1)
        raise SystemExit(plugins_import_credential(plugin_id, connector_id, args.resource_path))
    if args.command == "daemon-recovery":
        raise SystemExit(asyncio.run(daemon_recovery()))
    if args.command == "daemon-soak":
        try:
            cycles = int(os.environ.get("AEGIS_SOAK_CYCLES", "100"))
            max_p95_ms = float(os.environ.get("AEGIS_SOAK_MAX_P95_MS", "250"))
            max_rss_growth_bytes = int(
                float(os.environ.get("AEGIS_SOAK_MAX_RSS_GROWTH_MB", "8")) * 1_024 * 1_024
            )
        except (OverflowError, ValueError):
            print("status=error reason=invalid_soak_config")
            raise SystemExit(2) from None
        raise SystemExit(
            asyncio.run(
                daemon_soak(
                    cycles=cycles,
                    max_p95_ms=max_p95_ms,
                    max_rss_growth_bytes=max_rss_growth_bytes,
                )
            )
        )
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
    if args.command == "probe-nvidia-tts":
        raise SystemExit(asyncio.run(probe_nvidia_tts()))
    if args.command == "probe-nvidia-vision":
        raise SystemExit(asyncio.run(probe_nvidia_vision()))
    if args.command == "probe-nvidia-tools":
        raise SystemExit(asyncio.run(probe_nvidia_tools()))
    if args.command == "probe-nvidia-swarm":
        raise SystemExit(asyncio.run(probe_nvidia_swarm()))
    if args.command == "verify-audit":
        if args.resource_path is None:
            parser.error("verify-audit requires audit_path")
        raise SystemExit(verify_audit(args.resource_path, Settings().audit_max_bytes))
    raise SystemExit(1)


if __name__ == "__main__":
    sys.exit(main())
