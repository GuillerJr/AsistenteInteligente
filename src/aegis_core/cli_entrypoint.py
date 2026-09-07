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
    "long-horizon-reliability": "long_horizon_reliability",
    "macos-qualification": "macos_qualification",
    "probe-nvidia": "probe_nvidia",
    "probe-nvidia-embedding": "probe_nvidia_embedding",
    "probe-nvidia-swarm": "probe_nvidia_swarm",
    "probe-nvidia-tools": "probe_nvidia_tools",
    "probe-nvidia-tts": "probe_nvidia_tts",
    "probe-nvidia-vision": "probe_nvidia_vision",
    "production-workflows": "production_workflows",
    "self-evaluation": "self_evaluation",
    "voice-qualification": "voice_qualification",
}

_SYNC_COMMANDS: Mapping[str, str] = {
    "capabilities-list": "capabilities_list",
    "doctor": "doctor",
    "import-nvidia-key": "import_nvidia_key",
    "plugins-list": "plugins_list",
    "plugins-verify": "plugins_verify",
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
        description="Jarvis local-first assistant. Run without a command to open Engineering CLI.",
    )
    parser.add_argument(
        "command",
        nargs="?",
        default="engineer",
        choices=_COMMANDS,
    )
    parser.add_argument("resource_path", nargs="?", type=Path)
    parser.add_argument("--connector")
    parser.add_argument("--request", help="run one engineering request without opening the REPL")
    parser.add_argument(
        "--domain",
        choices=[domain.value for domain in EngineeringDomain],
        default=EngineeringDomain.AUTO.value,
        help="engineering discipline for this session",
    )
    parser.add_argument(
        "--workspace",
        type=Path,
        default=None,
        help="project directory; defaults to the daemon's authorized workspace",
    )
    parser.add_argument(
        "--research-policy",
        choices=[policy.value for policy in EngineeringResearchPolicy],
        default=EngineeringResearchPolicy.OFFLINE.value,
        help="permit bounded public HTTPS research or remain fully offline",
    )
    parser.add_argument(
        "--inference-policy",
        choices=[policy.value for policy in EngineeringInferencePolicy],
        default=EngineeringInferencePolicy.HYBRID.value,
        help="allow specialist fallback or require on-device inference",
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
        return asyncio.run(
            handlers.engineering_cli(
                workspace=args.workspace,
                domain=EngineeringDomain(args.domain),
                research_policy=EngineeringResearchPolicy(args.research_policy),
                inference_policy=EngineeringInferencePolicy(args.inference_policy),
                request=args.request,
            )
        )
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
