"""Reproducible configurations for the two pinned Node peers (no installation)."""

from __future__ import annotations

from pathlib import Path

from aegis_core.contracts import AgentRole, Capability
from aegis_core.mcp.client import McpServerConfiguration, artifact_sha256
from aegis_core.mcp.integrations import (
    BrowserSessionConfiguration,
    IntegrationId,
    IntegrationScope,
)


def browser_launch_arguments(
    scope: IntegrationScope, session: BrowserSessionConfiguration,
) -> tuple[str, ...]:
    mode = {
        "isolated": ("--isolated", *(("--headless",) if not session.visible else ()),
                     "--sandbox", "--block-service-workers"),
        "extension": ("--extension",),
        "cdp": ("--cdp-endpoint", session.cdp_endpoint or "", "--cdp-timeout", "10000"),
    }[session.mode]
    return (*mode, "--codegen", "none", "--image-responses", "omit", "--snapshot-mode", "none",
            "--allowed-origins", ";".join(sorted(scope.browser_origins)),
            "--timeout-navigation", "10000", "--timeout-action", "5000")


def local_peer_configuration(
    scope: IntegrationScope,
    *,
    node: Path,
    packages: Path,
    working_directory: Path,
    browser_session: BrowserSessionConfiguration | None = None,
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
    artifacts = (node, entry, lock)
    if browser_session is not None:
        if scope.provider is not IntegrationId.PLAYWRIGHT:
            raise ValueError("Browser session belongs to Playwright only")
        artifacts += tuple((packages / "node_modules" / "playwright-core" / "lib" / name)
                           .resolve(strict=True) for name in ("coreBundle.js", "utilsBundle.js"))
        artifacts += ((entry.parent / "package.json").resolve(strict=True),)
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
    if scope.provider is IntegrationId.PLAYWRIGHT and browser_session is not None:
        arguments = (str(entry), *browser_launch_arguments(scope, browser_session))
    if scope.provider is IntegrationId.PLAYWRIGHT and not scope.browser_origins:
        arguments = (str(entry),)  # Incomplete by design; cannot pass admission/startup.
    return McpServerConfiguration(
        server_id=scope.provider.value,
        command=node,
        arguments=arguments,
        working_directory=working_directory,
        integration=scope,
        browser_session=browser_session,
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
        artifact_sha256={str(p): artifact_sha256(p) for p in artifacts},
    )
