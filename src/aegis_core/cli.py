from __future__ import annotations

import asyncio
import json
import math
import os
import platform
import re
import signal
import subprocess
import time
from pathlib import Path
from uuid import uuid4

from aegis_core.acceptance_benchmark import JarvisAcceptanceBenchmark
from aegis_core.activity import (
    SwarmActivitySnapshot,
)
from aegis_core.application_qualification import (
    ApplicationQualificationIpcService,
    ApplicationQualificationReport,
)
from aegis_core.audit_anchor import DurableAuditAnchor
from aegis_core.browser_qualification import (
    BrowserQualificationIpcService,
    BrowserQualificationReport,
)
from aegis_core.capability_blueprints import build_capability_blueprint
from aegis_core.capability_learning import (
    CapabilityLearningError,
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
from aegis_core.engineering import (
    EngineeringCLI,
    EngineeringDomain,
    EngineeringInferencePolicy,
    EngineeringResearchPolicy,
)
from aegis_core.ipc.client import IpcClient
from aegis_core.ipc.protocol import IpcAuthenticator, ProtocolError
from aegis_core.long_horizon_reliability import (
    JarvisLongHorizonReliabilityBenchmark,
)
from aegis_core.macos_qualification import (
    MacOSQualificationError,
    MacOSQualificationGate,
    QualificationStatus,
    RuntimePowerPayload,
)
from aegis_core.memory import (
    SQLiteMemoryStore,
)
from aegis_core.models import model_for
from aegis_core.pilot_release_qualification import (
    PilotReleaseQualificationError,
    PilotReleaseQualificationGate,
)
from aegis_core.plugins import PluginManifest, PluginPackage
from aegis_core.plugins.runtime import PluginRuntime
from aegis_core.plugins.store import (
    PluginError,
    PluginStore,
    load_plugin_package,
    read_plugin_source,
)
from aegis_core.production_workflows import JarvisProductionWorkflowBenchmark
from aegis_core.providers.apple_embedding import AppleLocalEmbeddingClient
from aegis_core.providers.base import EmbeddingInputType
from aegis_core.providers.nvidia import NvidiaNimClient, NvidiaNimError
from aegis_core.secrets import (
    InvalidAuditAnchorError,
    InvalidGenericSecretError,
    InvalidIpcSecretError,
    InvalidPluginSecretError,
    InvalidSecretError,
    MacOSAuditAnchor,
    MacOSGenericSecret,
    MacOSIpcSecret,
    MacOSKeychain,
    MacOSPluginSecret,
    SecretNotFoundError,
    import_generic_secret_from_file,
    import_nvidia_key_from_clipboard,
    import_nvidia_key_from_file,
    import_plugin_secret_from_file,
)
from aegis_core.skills import SkillError, SkillRegistry, SkillStore, load_skill_draft
from aegis_core.tools.audit import AuditIntegrityError, HashChainAuditLog
from aegis_core.tools.broker import ToolBroker
from aegis_core.tools.defaults import build_default_tool_broker
from aegis_core.voice_qualification import (
    VoiceQualificationError,
    VoiceQualificationGate,
)

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


def devices_import_credential(device_id: str, platform_id: str, source: Path) -> int:
    if re.fullmatch(r"^[a-z][a-z0-9-]{2,31}$", device_id) is None or platform_id not in {
        "android",
        "tizen",
        "webos",
    }:
        print("status=error reason=invalid_device_credential_scope")
        return 2
    store = MacOSGenericSecret(f"ai.aegis.device.{device_id}.{platform_id}")
    try:
        import_generic_secret_from_file(store, source)
    except (InvalidGenericSecretError, OSError, SecretNotFoundError) as error:
        print(f"status=error reason={type(error).__name__}")
        return 1
    print(
        f"status=ok device={device_id} platform={platform_id} "
        "credential=keychain temporary_file=removed"
    )
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
    print(f"status=ok capability={gap_id} gate={dossier.review_gate.value} executable=false")
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
    print(f"status=ok capability={verdict.gap_id} review={verdict.status.value} executable=false")
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
        f"{'available' if SQLiteMemoryStore.vector_acceleration_available() else 'unavailable'} "
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


def recover_audit_anchor(path: Path) -> int:
    """Re-anchor an internally valid ledger while its only writer is stopped."""
    settings = Settings()
    if _daemon_launch_agent_loaded():
        print("status=blocked reason=daemon_service_must_be_stopped")
        return 2
    expected = settings.ipc_socket_path.parent / "audit.jsonl"
    try:
        if path.is_symlink() or path.resolve(strict=True) != expected.resolve(strict=True):
            print("status=error reason=unexpected_audit_path")
            return 1
        audit_log = HashChainAuditLog(path, max_bytes=settings.audit_max_bytes)
        records = audit_log.verify()
        if not records:
            print("status=error reason=empty_audit_log")
            return 1
        durable_anchor = DurableAuditAnchor(MacOSAuditAnchor())
        try:
            durable_anchor.validate_startup(audit_log)
        except (AuditIntegrityError, InvalidAuditAnchorError, OSError):
            pass
        else:
            print(f"status=ok recovery=not_required records={len(records)}")
            return 0
        audit_log.record_system_event(
            uuid4(),
            event_type="audit_anchor_recovered",
            component="supervisor",
            data={
                "state": "operator_verified",
                "records_verified": len(records),
            },
        )
        durable_anchor.seal_shutdown(audit_log)
        durable_anchor.validate_startup(HashChainAuditLog(path, max_bytes=settings.audit_max_bytes))
    except (
        AuditIntegrityError,
        InvalidAuditAnchorError,
        SecretNotFoundError,
        OSError,
        ValueError,
    ):
        print("status=error reason=audit_anchor_recovery_failed")
        return 1
    print(f"status=ok recovery=anchored records={len(records) + 1}")
    return 0


def _ipc_authenticator(settings: Settings, *, create: bool) -> IpcAuthenticator:
    secret_store = MacOSIpcSecret(
        service=settings.ipc_keychain_service,
        account=settings.ipc_keychain_account,
    )
    secret = secret_store.get_or_create() if create else secret_store.get()
    return IpcAuthenticator.from_hex(secret)


async def run_daemon() -> int:
    from aegis_core.runtime.daemon import run_daemon as run_runtime_daemon

    return await run_runtime_daemon()


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
    runtime = "active"
    if not activity_response.ok:
        if activity_response.error_code != "runtime_suspended":
            print(f"status=error reason={activity_response.error_code}")
            return 1
        runtime = "suspended"
    if not plugin_response.ok:
        print(f"status=error reason={plugin_response.error_code}")
        return 1
    if not capability_response.ok:
        print(f"status=error reason={capability_response.error_code}")
        return 1
    protocol = response.payload.get("protocol_version")
    architecture = response.payload.get("architecture")
    build_revision = response.payload.get("build_revision")
    if (
        not isinstance(protocol, str)
        or not isinstance(architecture, str)
        or not isinstance(build_revision, str)
        or (
            build_revision != "development"
            and re.fullmatch(r"[0-9a-f]{40}", build_revision) is None
        )
    ):
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
    if runtime == "suspended":
        activity = SwarmActivitySnapshot(agents=())
    else:
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
        f"build={build_revision[:12]} runtime={runtime} "
        f"security={security} provider={credential} local_model={local_model} "
        f"active_agents={active_agents} plugins={len(plugins)} mcp={plugin_protocol} "
        f"capabilities={capability_total} researched={capability_researched}"
    )
    return 0


async def engineering_cli(
    *,
    workspace: Path | None,
    domain: EngineeringDomain,
    research_policy: EngineeringResearchPolicy,
    inference_policy: EngineeringInferencePolicy,
    request: str | None,
) -> int:
    settings = Settings()
    try:
        authenticator = _ipc_authenticator(settings, create=False)
        client = IpcClient(
            settings.ipc_socket_path,
            authenticator,
            max_frame_bytes=settings.ipc_max_frame_bytes,
            max_message_bytes=settings.ipc_max_message_bytes,
            timeout_seconds=22,
            clock_skew_seconds=settings.ipc_clock_skew_seconds,
        )
        session = EngineeringCLI(
            client,
            workspace=workspace,
            domain=domain,
            research_policy=research_policy,
            inference_policy=inference_policy,
        )
        return (
            await session.run_once(request)
            if request is not None
            else await session.run_interactive()
        )
    except (
        EOFError,
        InvalidIpcSecretError,
        OSError,
        ProtocolError,
        SecretNotFoundError,
        TimeoutError,
        ValueError,
    ) as error:
        print(f"status=error reason={type(error).__name__}")
        return 1
    except RuntimeError as error:
        reason = str(error)
        if not re.fullmatch(r"[a-z][a-z0-9_]{2,63}", reason):
            reason = "engineering_session_failed"
        print(f"status=error reason={reason}")
        return 1


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
        jobs_response, performance_response = await asyncio.gather(
            client.call("jobs.metrics"),
            client.call("performance.snapshot"),
        )
    except (
        TimeoutError,
        SecretNotFoundError,
        InvalidIpcSecretError,
        ProtocolError,
        OSError,
    ) as error:
        print(f"status=error reason={type(error).__name__}")
        return 1
    if not jobs_response.ok:
        print(f"status=error reason={jobs_response.error_code}")
        return 1
    if not performance_response.ok:
        print(f"status=error reason={performance_response.error_code}")
        return 1
    report = {
        "jobs": jobs_response.payload,
        "performance": performance_response.payload,
        "privacy": {
            "contains_prompt_text": False,
            "contains_target_urls": False,
            "contains_transcripts": False,
        },
    }
    print(json.dumps(report, ensure_ascii=False, separators=(",", ":")))
    return 0


async def acceptance_benchmark() -> int:
    report = await JarvisAcceptanceBenchmark().run()
    print(report.private_json())
    return 0 if report.gate_passed else 1


async def production_workflows() -> int:
    report = await JarvisProductionWorkflowBenchmark().run()
    print(report.private_json())
    return 0 if report.gate_passed else 1


async def long_horizon_reliability() -> int:
    try:
        cycles = int(os.environ.get("AEGIS_RELIABILITY_CYCLES", "20"))
        report = await JarvisLongHorizonReliabilityBenchmark(cycles=cycles).run()
    except (OSError, RuntimeError, ValueError):
        print(
            json.dumps(
                {
                    "schema_version": "1.0",
                    "profile": "local_long_horizon_reliability",
                    "gate_passed": False,
                    "error_code": "reliability_unavailable",
                },
                separators=(",", ":"),
                sort_keys=True,
            )
        )
        return 2
    print(report.private_json())
    return 0 if report.gate_passed else 1


async def application_qualification() -> int:
    settings = Settings()
    try:
        authenticator = _ipc_authenticator(settings, create=False)
        client = IpcClient(
            settings.ipc_socket_path,
            authenticator,
            max_frame_bytes=settings.ipc_max_frame_bytes,
            max_message_bytes=settings.ipc_max_message_bytes,
            timeout_seconds=ApplicationQualificationIpcService.MAXIMUM_HANDLER_SECONDS + 2,
            clock_skew_seconds=settings.ipc_clock_skew_seconds,
        )
        response = await client.call(ApplicationQualificationIpcService.METHOD)
    except (
        InvalidIpcSecretError,
        OSError,
        ProtocolError,
        SecretNotFoundError,
        TimeoutError,
        ValueError,
    ) as error:
        print(f"status=error reason={type(error).__name__}")
        return 2
    if not response.ok:
        print(f"status=error reason={response.error_code}")
        return 2
    try:
        report = ApplicationQualificationReport.from_private_dict(response.payload)
    except ValueError:
        print("status=error reason=qualification_report_invalid")
        return 2
    print(report.private_json())
    return 0 if report.gate_passed else 1


async def browser_driver_qualification() -> int:
    settings = Settings()
    try:
        authenticator = _ipc_authenticator(settings, create=False)
        client = IpcClient(
            settings.ipc_socket_path,
            authenticator,
            max_frame_bytes=settings.ipc_max_frame_bytes,
            max_message_bytes=settings.ipc_max_message_bytes,
            timeout_seconds=BrowserQualificationIpcService.MAXIMUM_HANDLER_SECONDS + 2,
            clock_skew_seconds=settings.ipc_clock_skew_seconds,
        )
        response = await client.call(BrowserQualificationIpcService.METHOD)
    except (
        InvalidIpcSecretError,
        OSError,
        ProtocolError,
        SecretNotFoundError,
        TimeoutError,
        ValueError,
    ) as error:
        print(f"status=error reason={type(error).__name__}")
        return 2
    if not response.ok:
        print(f"status=error reason={response.error_code}")
        return 2
    try:
        report = BrowserQualificationReport.from_private_dict(response.payload)
    except ValueError:
        print("status=error reason=browser_qualification_report_invalid")
        return 2
    print(report.private_json())
    return 0 if report.gate_passed else 1


async def voice_qualification() -> int:
    settings = Settings()
    calibration_value = os.environ.get("AEGIS_VOICE_CALIBRATION_REPORT")
    if not calibration_value:
        print("status=error reason=voice_calibration_report_required")
        return 2
    calibration_path = Path(calibration_value).expanduser()
    try:
        authenticator = _ipc_authenticator(settings, create=False)
        client = IpcClient(
            settings.ipc_socket_path,
            authenticator,
            max_frame_bytes=settings.ipc_max_frame_bytes,
            max_message_bytes=settings.ipc_max_message_bytes,
            clock_skew_seconds=settings.ipc_clock_skew_seconds,
        )
        report = await VoiceQualificationGate(
            client,
            readiness_path=settings.ipc_socket_path.parent / "runtime-readiness.json",
            evidence_path=settings.ipc_socket_path.parent / "runtime-evidence.json",
            calibration_path=calibration_path,
        ).run()
    except (
        InvalidIpcSecretError,
        MacOSQualificationError,
        OSError,
        ProtocolError,
        SecretNotFoundError,
        TimeoutError,
        ValueError,
        VoiceQualificationError,
    ):
        print(
            json.dumps(
                {
                    "schema_version": "1.0",
                    "profile": "live_owner_voice_qualification",
                    "status": "blocked",
                    "gate_passed": False,
                    "error_code": "voice_qualification_unavailable",
                },
                separators=(",", ":"),
                sort_keys=True,
            )
        )
        return 2
    print(report.private_json())
    if report.gate_passed:
        return 0
    return 2 if report.status is QualificationStatus.BLOCKED else 1


async def pilot_release_qualification() -> int:
    evidence_variables = {
        "voice_report_path": "AEGIS_P11_VOICE_REPORT",
        "macos_report_path": "AEGIS_P11_MACOS_REPORT",
        "release_manifest_path": "AEGIS_P11_RELEASE_MANIFEST",
        "release_sbom_path": "AEGIS_P11_RELEASE_SBOM",
        "release_archive_path": "AEGIS_P11_RELEASE_ARCHIVE",
    }
    resolved: dict[str, Path] = {}
    for argument, variable in evidence_variables.items():
        value = os.environ.get(variable, "").strip()
        if not value:
            print("status=error reason=pilot_release_evidence_required")
            return 2
        resolved[argument] = Path(value).expanduser()
    project_root = Path(__file__).resolve().parents[2]
    try:
        report = await asyncio.to_thread(
            PilotReleaseQualificationGate(
                project_root=project_root,
                installed_info_path=(
                    Path.home() / "Applications/Jarvis.app/Contents/Info.plist"
                ),
                **resolved,
            ).run
        )
    except (OSError, PilotReleaseQualificationError, ValueError):
        print(
            json.dumps(
                {
                    "schema_version": "1.0",
                    "profile": "local_pilot_release_qualification",
                    "status": "blocked",
                    "gate_passed": False,
                    "error_code": "pilot_release_qualification_unavailable",
                },
                separators=(",", ":"),
                sort_keys=True,
            )
        )
        return 2
    print(report.private_json())
    if report.gate_passed:
        return 0
    return 2 if report.status is QualificationStatus.BLOCKED else 1


async def macos_qualification() -> int:
    settings = Settings()
    try:
        cycles = int(os.environ.get("AEGIS_QUALIFICATION_CYCLES", "100"))
        authenticator = _ipc_authenticator(settings, create=False)
        client = IpcClient(
            settings.ipc_socket_path,
            authenticator,
            max_frame_bytes=settings.ipc_max_frame_bytes,
            max_message_bytes=settings.ipc_max_message_bytes,
            clock_skew_seconds=settings.ipc_clock_skew_seconds,
        )
        report = await MacOSQualificationGate(
            client,
            readiness_path=settings.ipc_socket_path.parent / "runtime-readiness.json",
            evidence_path=settings.ipc_socket_path.parent / "runtime-evidence.json",
            cycles=cycles,
        ).run()
    except (
        MacOSQualificationError,
        SecretNotFoundError,
        InvalidIpcSecretError,
        ProtocolError,
        OSError,
        TimeoutError,
        ValueError,
    ):
        print(
            json.dumps(
                {
                    "schema_version": "1.0",
                    "profile": "live_macos_qualification",
                    "status": "blocked",
                    "gate_passed": False,
                    "error_code": "qualification_unavailable",
                },
                separators=(",", ":"),
                sort_keys=True,
            )
        )
        return 2
    print(report.private_json())
    if report.gate_passed:
        return 0
    return 2 if report.status is QualificationStatus.BLOCKED else 1


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
        first_rss_bytes: int | None = None
        last_cpu_seconds = 0.0
        last_rss_bytes = 0
        maximum_rss_bytes = 0
        first_thread_count: int | None = None
        last_thread_count = 0
        operating_mode: str | None = None
        warmup_cycles = min(5, max(1, cycles // 20))
        # Stabilize lazy native/SQLite pages before fixing the resource baseline.
        # These bounded rounds are excluded from the leak measurement; the following
        # `cycles` rounds still enforce the original strict 8 MiB growth ceiling.
        for cycle_index in range(cycles + warmup_cycles):
            started = time.perf_counter()
            health, runtime, metrics, security, power_response = await asyncio.gather(
                client.call("health"),
                client.call("runtime.info"),
                client.call("runtime.metrics"),
                client.call("security.status"),
                client.call("runtime.power.status"),
            )
            round_latency_ms = (time.perf_counter() - started) * 1_000
            if not all(
                response.ok for response in (health, runtime, metrics, security, power_response)
            ):
                print("status=error reason=soak_response_failed")
                return 1
            power = RuntimePowerPayload.model_validate(power_response.payload)
            if operating_mode is None:
                operating_mode = power.state
            elif power.state != operating_mode:
                print("status=error reason=power_state_changed")
                return 1
            if power.state == "active":
                activity = await client.call("swarm.activity")
                if not activity.ok:
                    print("status=error reason=soak_response_failed")
                    return 1
                if SwarmActivitySnapshot.model_validate(activity.payload).agents:
                    print("status=blocked reason=daemon_busy")
                    return 2
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
                or health.payload.get("runtime_state") != power.state
            ):
                print("status=error reason=invalid_soak_response")
                return 1
            uptime_seconds = metrics.payload.get("uptime_seconds")
            cpu_seconds = metrics.payload.get("cpu_seconds")
            rss_bytes = metrics.payload.get("rss_bytes")
            peak_rss_bytes = metrics.payload.get("peak_rss_bytes")
            thread_count = metrics.payload.get("thread_count")
            active_clients = metrics.payload.get("active_clients")
            if (
                not {
                    "uptime_seconds",
                    "cpu_seconds",
                    "rss_bytes",
                    "peak_rss_bytes",
                    "thread_count",
                    "active_clients",
                }.issubset(metrics.payload)
                or isinstance(uptime_seconds, bool)
                or not isinstance(uptime_seconds, (int, float))
                or not math.isfinite(uptime_seconds)
                or uptime_seconds < 0
                or isinstance(cpu_seconds, bool)
                or not isinstance(cpu_seconds, (int, float))
                or not math.isfinite(cpu_seconds)
                or cpu_seconds < 0
                or isinstance(rss_bytes, bool)
                or not isinstance(rss_bytes, int)
                or rss_bytes <= 0
                or isinstance(peak_rss_bytes, bool)
                or not isinstance(peak_rss_bytes, int)
                or peak_rss_bytes < rss_bytes
                or isinstance(thread_count, bool)
                or not isinstance(thread_count, int)
                or not 1 <= thread_count <= 1_024
                or isinstance(active_clients, bool)
                or not isinstance(active_clients, int)
                or not 1 <= active_clients <= 16
                or metrics.payload.get("runtime_state") != power.state
            ):
                print("status=error reason=invalid_metrics_response")
                return 1
            if cycle_index < warmup_cycles:
                continue
            latencies.append(round_latency_ms)
            last_cpu_seconds = float(cpu_seconds)
            last_rss_bytes = rss_bytes
            maximum_rss_bytes = max(maximum_rss_bytes, rss_bytes)
            last_thread_count = thread_count
            if first_cpu_seconds is None:
                first_cpu_seconds = last_cpu_seconds
                first_rss_bytes = last_rss_bytes
                first_thread_count = last_thread_count
            if last_thread_count - (first_thread_count or last_thread_count) > 4:
                print("status=error reason=thread_growth_budget_exceeded")
                return 1
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
    if (
        first_cpu_seconds is None
        or first_rss_bytes is None
        or first_thread_count is None
        or operating_mode is None
    ):
        print("status=error reason=missing_metrics_response")
        return 1
    cpu_ms = max(0.0, last_cpu_seconds - first_cpu_seconds) * 1_000
    rss_growth_bytes = max(0, last_rss_bytes - first_rss_bytes)
    rss_peak_growth_bytes = max(0, maximum_rss_bytes - first_rss_bytes)
    if max(rss_growth_bytes, rss_peak_growth_bytes) > max_rss_growth_bytes:
        print(
            f"status=error reason=memory_growth_budget_exceeded cycles={cycles} "
            f"rss_growth_kib={rss_growth_bytes / 1_024:.2f} "
            f"rss_peak_growth_kib={rss_peak_growth_bytes / 1_024:.2f}"
        )
        return 1
    if p95 > max_p95_ms:
        print(
            f"status=error reason=latency_budget_exceeded cycles={cycles} "
            f"p95_ms={p95:.2f} max_ms={peak:.2f}"
        )
        return 1
    print(
        f"status=ok cycles={cycles} mode={operating_mode} p95_ms={p95:.2f} "
        f"max_ms={peak:.2f} rss_growth_kib={rss_growth_bytes / 1_024:.2f} "
        f"rss_peak_growth_kib={rss_peak_growth_bytes / 1_024:.2f} "
        f"cpu_ms={cpu_ms:.2f} threads_growth={last_thread_count - first_thread_count}"
    )
    return 0


def main() -> None:
    from aegis_core.cli_entrypoint import main as entrypoint_main

    entrypoint_main()


if __name__ == "__main__":
    main()
