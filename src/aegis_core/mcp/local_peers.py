"""Reproducible configurations for the two pinned Node peers (no installation)."""

from __future__ import annotations

from pathlib import Path

from aegis_core.contracts import AgentRole, Capability
from aegis_core.mcp.client import McpServerConfiguration, artifact_sha256
from aegis_core.mcp.integrations import IntegrationId, IntegrationScope


def local_peer_configuration(
    scope: IntegrationScope,
    *,
    node: Path,
    packages: Path,
    working_directory: Path,
) -> McpServerConfiguration:
    if scope.provider not in {IntegrationId.FILESYSTEM, IntegrationId.PLAYWRIGHT}:
        raise ValueError("Only the pinned local Node peers are supported by this launcher")
    node = node.resolve(strict=True)
    entry = (
        packages
        / "node_modules"
        / (
            "@modelcontextprotocol/server-filesystem/dist/index.js"
            if scope.provider is IntegrationId.FILESYSTEM
            else "@playwright/mcp/cli.js"
        )
    )
    entry = entry.resolve(strict=True)
    lock = (packages / "package-lock.json").resolve(strict=True)
    arguments = (
        (str(entry), *(str(root) for root in scope.roots))
        if scope.provider is IntegrationId.FILESYSTEM
        else (
            str(entry),
            "--isolated",
            "--headless",
            "--sandbox",
            "--block-service-workers",
            "--codegen",
            "none",
            "--image-responses",
            "omit",
            "--allowed-origins",
            ";".join(sorted(scope.browser_origins)),
            "--timeout-navigation",
            "10000",
            "--timeout-action",
            "5000",
        )
    )
    if scope.provider is IntegrationId.PLAYWRIGHT and not scope.browser_origins:
        arguments = (str(entry),)  # Incomplete by design; cannot pass admission/startup.
    return McpServerConfiguration(
        server_id=scope.provider.value,
        command=node,
        arguments=arguments,
        working_directory=working_directory,
        integration=scope,
        capability=(
            Capability.FILESYSTEM_READ
            if scope.provider is IntegrationId.FILESYSTEM
            else Capability.WEB_READ
        ),
        allow_explicit_requests=True,
        read_only=scope.provider is IntegrationId.FILESYSTEM,
        allowed_roles=(
            frozenset({AgentRole.CODE_SECURITY, AgentRole.CRITICAL_REASONER})
            if scope.provider is IntegrationId.FILESYSTEM
            else frozenset({AgentRole.PLANNER})
        ),
        start_timeout_seconds=20,
        call_timeout_seconds=20,
        # Deliberately disabled/unapproved. Discovery cannot promote this configuration.
        artifact_sha256={str(p): artifact_sha256(p) for p in (node, entry, lock)},
    )
