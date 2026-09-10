from __future__ import annotations

import argparse
import asyncio
import os
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path
from types import ModuleType
from typing import Any, NoReturn

from aegis_core.engineering import (
    EngineeringDomain,
    EngineeringInferencePolicy,
    EngineeringResearchPolicy,
)

_ASYNC_COMMANDS: Mapping[str, str] = {
    "acceptance-benchmark": "acceptance_benchmark",
    "application-qualification": "application_qualification",
    "browser-driver-qualification": "browser_driver_qualification",
    "daemon": "run_daemon",
    "daemon-recovery": "daemon_recovery",
    "daemon-status": "daemon_status",
    "distribution-release-qualification": "distribution_release_qualification",
    "long-horizon-reliability": "long_horizon_reliability",
    "macos-qualification": "macos_qualification",
    "pilot-release-qualification": "pilot_release_qualification",
    "probe-nvidia": "probe_nvidia",
    "probe-nvidia-embedding": "probe_nvidia_embedding",
    "probe-nvidia-swarm": "probe_nvidia_swarm",
    "probe-nvidia-tools": "probe_nvidia_tools",
    "probe-nvidia-tts": "probe_nvidia_tts",
    "probe-nvidia-vision": "probe_nvidia_vision",
    "production-workflows": "production_workflows",
    "self-evaluation": "self_evaluation",
    "self-contained-release-qualification": "self_contained_release_qualification",
    "secure-update-qualification": "secure_update_qualification",
    "secure-update-recover": "secure_update_recover",
    "voice-qualification": "voice_qualification",
}

_SYNC_COMMANDS: Mapping[str, str] = {
    "capabilities-list": "capabilities_list",
    "doctor": "doctor",
    "import-nvidia-key": "import_nvidia_key",
    "plugins-list": "plugins_list",
    "plugins-verify": "plugins_verify",
    "release-scope": "release_scope",
    "skills-list": "skills_list",
}

_RESOURCE_COMMANDS: Mapping[str, tuple[str, str]] = {
    "capabilities-forget": ("capabilities_forget", "gap_id"),
    "capabilities-inspect": ("capabilities_inspect", "gap_id"),
    "capabilities-plan": ("capabilities_plan", "gap_id"),
    "capabilities-review": ("capabilities_review", "review_json_path"),
    "import-nvidia-key-file": ("import_nvidia_key_file", "credential_path"),
    "plugins-install": ("plugins_install", "plugin_package_path"),
    "plugins-pack": ("plugins_pack", "plugin_draft_path"),
    "recover-audit-anchor": ("recover_audit_anchor", "audit_path"),
    "skills-forget": ("skills_forget", "skill_id"),
    "skills-learn": ("skills_learn", "skill_json_path"),
}

_COMMANDS = tuple(
    sorted(
        {
            *_ASYNC_COMMANDS,
            *_SYNC_COMMANDS,
            *_RESOURCE_COMMANDS,
            "daemon-soak",
            "devices-credential-import",
            "engineer",
            "plugins-credential-import",
            "plugins-disable",
            "plugins-enable",
            "plugins-remove",
            "plugins-simulate",
            "secure-update-install",
            "secure-update-verify",
            "update-channel-sign",
            "update-key-initialize",
            "verify-audit",
        }
    )
)


def build_parser(*, program_name: str | None = None) -> argparse.ArgumentParser:
    executable_name = program_name or os.environ.get(
        "AEGIS_CLI_PROGRAM_NAME",
        Path(sys.argv[0]).name,
    )
    parser = argparse.ArgumentParser(
        prog=executable_name if executable_name in {"aegis", "jarvis"} else "aegis",
        description="Jarvis Engineering CLI · sin argumentos abre una sesión interactiva.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Ejemplos:\n"
            "  jarvis --inference-policy local_only\n"
            '  jarvis --domain backend --request "Revisa la concurrencia"\n'
            "  jarvis --request - --inference-policy local_only < consulta.txt\n\n"
            "Dentro de la sesión: /help · /status · /resume · /cancel · /new\n"
            "Offline limita la investigación web; local_only impide inferencia remota.\n"
            "El repositorio autorizado es de lectura; no hay escritura ni shell libre."
        ),
    )
    parser.add_argument(
        "command",
        nargs="?",
        default="engineer",
        choices=_COMMANDS,
        metavar="comando",
        help="engineer (por defecto), doctor, daemon-status u otro comando administrativo",
    )
    parser.add_argument("resource_path", nargs="?", type=Path, help="recurso administrativo")
    engineering = parser.add_argument_group("Sesión de ingeniería")
    administration = parser.add_argument_group("Administración y distribución")
    administration.add_argument("--connector")
    administration.add_argument("--archive", type=Path)
    administration.add_argument("--release-manifest", type=Path)
    administration.add_argument("--output", type=Path)
    administration.add_argument(
        "--public-key",
        type=Path,
        default=(
            Path.home() / "Applications/Jarvis.app/Contents/Resources/JarvisUpdatePublicKey.ed25519"
        ),
    )
    administration.add_argument("--confirm-install")
    engineering.add_argument(
        "--request",
        help="una instrucción; '-' lee una solicitud multilínea desde stdin",
    )
    engineering.add_argument(
        "--domain",
        choices=[domain.value for domain in EngineeringDomain],
        default=EngineeringDomain.AUTO.value,
        help="especialidad de la sesión, sin ampliar permisos",
    )
    engineering.add_argument(
        "--workspace",
        type=Path,
        default=None,
        help="carpeta dentro de la raíz autorizada por el daemon",
    )
    engineering.add_argument(
        "--research-policy",
        choices=[policy.value for policy in EngineeringResearchPolicy],
        default=EngineeringResearchPolicy.OFFLINE.value,
        help="lectura web pública opcional; offline no desactiva la inferencia remota",
    )
    engineering.add_argument(
        "--inference-policy",
        choices=[policy.value for policy in EngineeringInferencePolicy],
        default=EngineeringInferencePolicy.NVIDIA_ONLY.value,
        help="nvidia_only usa NVIDIA sin modelo local; local_only e hybrid son optativos",
    )
    return parser


def run(
    argv: Sequence[str] | None = None,
    *,
    handlers: ModuleType | Any | None = None,
    environment: Mapping[str, str] | None = None,
) -> int:
    if handlers is None:
        from aegis_core import cli as handlers

    parser = build_parser()
    args = parser.parse_args(argv)
    command = args.command

    if command == "engineer":
        if args.resource_path is not None:
            parser.error("use --request for a task or --workspace for a project directory")
        try:
            return asyncio.run(
                handlers.engineering_cli(
                    workspace=args.workspace,
                    domain=EngineeringDomain(args.domain),
                    research_policy=EngineeringResearchPolicy(args.research_policy),
                    inference_policy=EngineeringInferencePolicy(args.inference_policy),
                    request=args.request,
                )
            )
        except KeyboardInterrupt:
            return 130
    if command in _ASYNC_COMMANDS:
        return asyncio.run(getattr(handlers, _ASYNC_COMMANDS[command])())
    if command in _SYNC_COMMANDS:
        return getattr(handlers, _SYNC_COMMANDS[command])()
    if command in _RESOURCE_COMMANDS:
        function_name, resource_name = _RESOURCE_COMMANDS[command]
        if args.resource_path is None:
            parser.error(f"{command} requires {resource_name}")
        argument: Path | str = args.resource_path
        if command in {
            "capabilities-forget",
            "capabilities-inspect",
            "capabilities-plan",
            "skills-forget",
        }:
            argument = str(argument)
        return getattr(handlers, function_name)(argument)
    if command == "plugins-simulate":
        if args.resource_path is None or args.request is None:
            parser.error("plugins-simulate requires plugin_id and --request")
        return handlers.plugins_simulate(str(args.resource_path), args.request)
    if command in {"plugins-enable", "plugins-disable", "plugins-remove"}:
        if args.resource_path is None:
            parser.error(f"{command} requires plugin_id")
        plugin_id = str(args.resource_path)
        if command == "plugins-enable":
            return handlers.plugins_set_enabled(plugin_id, True)
        if command == "plugins-disable":
            return handlers.plugins_set_enabled(plugin_id, False)
        return handlers.plugins_remove(plugin_id)
    if command in {"plugins-credential-import", "devices-credential-import"}:
        if args.resource_path is None or args.connector is None:
            parser.error(f"{command} requires credential_path and --connector")
        if "." not in args.connector:
            required = (
                "plugin_id.connector_id" if command.startswith("plugins") else "device_id.platform"
            )
            parser.error(f"--connector must use {required}")
        if command == "plugins-credential-import":
            plugin_id, connector_id = args.connector.split(".", 1)
            return handlers.plugins_import_credential(
                plugin_id,
                connector_id,
                args.resource_path,
            )
        device_id, platform_id = args.connector.rsplit(".", 1)
        return handlers.devices_import_credential(device_id, platform_id, args.resource_path)
    if command == "update-key-initialize":
        if args.resource_path is None:
            parser.error("update-key-initialize requires public_key_path")
        return handlers.update_key_initialize(args.resource_path)
    if command == "update-channel-sign":
        if args.resource_path is None or args.archive is None or args.output is None:
            parser.error(
                "update-channel-sign requires release_manifest_path, --archive and --output"
            )
        return handlers.update_channel_sign(
            args.resource_path,
            args.archive,
            args.output,
            args.public_key,
        )
    if command in {"secure-update-verify", "secure-update-install"}:
        if args.resource_path is None or args.archive is None or args.release_manifest is None:
            parser.error(
                f"{command} requires update_manifest_path, --archive and --release-manifest"
            )
        if command == "secure-update-verify":
            return handlers.secure_update_verify(
                args.resource_path,
                args.archive,
                args.release_manifest,
                args.public_key,
            )
        if args.confirm_install is None:
            parser.error("secure-update-install requires --confirm-install BUILD_REVISION")
        return asyncio.run(
            handlers.secure_update_install(
                args.resource_path,
                args.archive,
                args.release_manifest,
                args.public_key,
                args.confirm_install,
            )
        )
    if command == "daemon-soak":
        configuration = environment if environment is not None else os.environ
        try:
            cycles = int(configuration.get("AEGIS_SOAK_CYCLES", "100"))
            max_p95_ms = float(configuration.get("AEGIS_SOAK_MAX_P95_MS", "250"))
            max_rss_growth_bytes = int(
                float(configuration.get("AEGIS_SOAK_MAX_RSS_GROWTH_MB", "8")) * 1_024 * 1_024
            )
        except (OverflowError, ValueError):
            print("status=error reason=invalid_soak_config")
            return 2
        return asyncio.run(
            handlers.daemon_soak(
                cycles=cycles,
                max_p95_ms=max_p95_ms,
                max_rss_growth_bytes=max_rss_growth_bytes,
            )
        )
    if command == "verify-audit":
        if args.resource_path is None:
            parser.error("verify-audit requires audit_path")
        return handlers.verify_audit(args.resource_path, handlers.Settings().audit_max_bytes)
    parser.error(f"unsupported command: {command}")


def main() -> NoReturn:
    raise SystemExit(run())
