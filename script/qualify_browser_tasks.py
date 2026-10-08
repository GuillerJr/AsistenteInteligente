#!/usr/bin/env python3
"""Opt-in browser-task qualification using only an owned synthetic Chrome session.

The service's immutable peer/schema pins and its production broker/handlers run
unchanged. An injected transport connects its peers to this harness's headless
Chrome through CDP instead of exercising the interactive extension handshake.
No owner browser, model API, credentials, persisted grants or package installation.
"""

# Standalone checkout script: resolve its own source/helper directories before imports.
# ruff: noqa: E402

from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import shutil
import sys
from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

import aiohttp

SCRIPT_DIRECTORY = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIRECTORY.parent / "src"))
sys.path.insert(0, str(SCRIPT_DIRECTORY))

from qualify_computer_workflows import (
    EXACT_VALUE,
    FixtureOracle,
    binding,
    default_packages,
    fixture_server,
    pinned_packages,
    private_chrome,
    target_ref,
)

from aegis_core.browser_preferences import (
    BrowserAutomationPreferences,
    browser_automation_path,
    save_browser_automation_preferences,
)
from aegis_core.config import Settings
from aegis_core.contracts import AgentRole, PolicyDecision, ToolCall, UserRequest
from aegis_core.mcp.client import McpServerConfiguration, StdioMcpClient
from aegis_core.mcp.integrations import BrowserSessionConfiguration
from aegis_core.mcp.local_peers import local_peer_configuration
from aegis_core.tools.confirmations import OneTimeConfirmationStore
from aegis_core.tools.defaults import build_default_tool_broker, default_policy_context
from aegis_core.tools.execution import ReadOnlyToolExecutor


class ObservedFixturePeer:
    def __init__(self, configuration: McpServerConfiguration) -> None:
        self.client = StdioMcpClient(configuration)
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.closed = False
        self.process_terminated = False

    @property
    def available(self) -> bool:
        return self.client.available

    async def start(self) -> tuple[dict[str, Any], ...]:
        return await self.client.start()

    async def call_tool(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        self.calls.append((name, dict(arguments)))
        return await self.client.call_tool(name, arguments)

    async def close(self) -> None:
        if self.closed:
            return
        process = self.client._process
        await self.client.close()
        self.closed = True
        self.process_terminated = process is None or process.returncode is not None


class FixturePeerFactory:
    """CDP override is local fixture code, never a task argument or saved preference."""

    def __init__(self, endpoint: str, directory: Path, packages: Path, node: Path) -> None:
        self.endpoint, self.directory = endpoint, directory
        self.packages, self.node = packages, node
        self.peers: list[ObservedFixturePeer] = []

    def __call__(self, configuration: McpServerConfiguration) -> ObservedFixturePeer:
        assert configuration.integration is not None, "fixture_integration_missing"
        assert configuration.integration.browser_origins == frozenset({"https://fixture.test"}), (
            "non_fixture_origin_scope"
        )
        assert configuration.browser_session is not None, "fixture_session_missing"
        assert configuration.browser_session.mode == "extension", "unexpected_production_mode"
        url = configuration.browser_session.selected_tab_url
        assert url is not None and url.startswith("https://fixture.test/"), "non_fixture_url"
        session = BrowserSessionConfiguration(
            mode="cdp",
            cdp_endpoint=self.endpoint,
            selected_tab_url=url,
            remote_content_allowed=True,
        )
        candidate = local_peer_configuration(
            configuration.integration,
            node=self.node,
            packages=self.packages,
            working_directory=self.directory,
            browser_session=session,
        )
        assert candidate.artifact_sha256 == configuration.artifact_sha256, "fixture_pin_changed"
        candidate = candidate.model_copy(
            update={
                "server_id": configuration.server_id,
                "approved_tools": dict(configuration.approved_tools),
                "enabled": True,
            }
        )
        peer = ObservedFixturePeer(candidate)
        self.peers.append(peer)
        return peer

    @property
    def call_count(self) -> int:
        return sum(len(peer.calls) for peer in self.peers)


class TaskWorkflow:
    def __init__(self, service: Any, directory: Path) -> None:
        self.service = service
        self.broker = build_default_tool_broker(service.tool_definitions())
        self.executor = ReadOnlyToolExecutor(extra_async_handlers=service.handlers())
        self.store = OneTimeConfirmationStore()
        self.base_context = replace(
            default_policy_context(directory), confirmation_store=self.store
        )
        self.invocations = 0
        self.access_approvals = 0
        self.action_approvals = 0

    def context(self, request: UserRequest):
        return replace(self.base_context, request_id=request.request_id, request_text=request.text)

    def call(self, name: str, arguments: dict[str, Any]) -> ToolCall:
        self.invocations += 1
        return ToolCall(
            call_id=f"browser-task-fixture-{self.invocations}",
            tool_name=name,
            arguments=arguments,
            requested_by=AgentRole.PLANNER,
        )

    async def open(
        self, request: UserRequest, url: str, factory: FixturePeerFactory, oracle: FixtureOracle
    ):
        call = self.call("browser_task_open", {"url": url, "objective": request.text})
        context = self.context(request)
        before = await oracle.state()
        peer_count, rpc_count = len(factory.peers), factory.call_count
        pending = self.broker.authorize(call, context, request=request)
        assert pending.decision is PolicyDecision.REQUIRE_CONFIRMATION, pending.reason_code
        denied = await self.executor.execute_async(pending, context)
        assert not denied.success and denied.error_code == "authorization_not_allowed"
        assert (len(factory.peers), factory.call_count) == (peer_count, rpc_count)
        assert await oracle.state() == before, "effect_before_access_approval"
        self.store.issue(
            call, pending, approved_by="synthetic-harness-owner", request_id=request.request_id
        )
        self.access_approvals += 1
        allowed = self.broker.authorize(call, context, request=request)
        assert allowed.decision is PolicyDecision.ALLOW, allowed.reason_code
        result = await self.executor.execute_async(allowed, context)
        replayed = self.broker.authorize(call, context, request=request)
        assert replayed.decision is PolicyDecision.DENY
        # An active-origin guard may reject before the consumed-grant check.
        assert replayed.reason_code in {"confirmation_replayed", "invalid_arguments"}
        return result, allowed

    async def action(
        self, request: UserRequest, snapshot: dict[str, Any], operation: str, **arguments: Any
    ):
        call = self.call(
            "browser_task_action",
            binding(snapshot)
            | {
                "operation": operation,
                **arguments,
            },
        )
        context = self.context(request)
        allowed = self.broker.authorize(call, context, request=request)
        if allowed.decision is PolicyDecision.REQUIRE_CONFIRMATION:
            self.action_approvals += 1
        assert allowed.decision is PolicyDecision.ALLOW, allowed.reason_code
        return await self.executor.execute_async(allowed, context)

    async def close(self, request: UserRequest, snapshot: dict[str, Any]) -> None:
        call = self.call("browser_task_close", {"session_id": snapshot["session_id"]})
        context = self.context(request)
        allowed = self.broker.authorize(call, context, request=request)
        assert allowed.decision is PolicyDecision.ALLOW, allowed.reason_code
        result = await self.executor.execute_async(allowed, context)
        assert result.success and structured(result)["closed"] is True, result.error_code
        assert self.service.context_for_request(request.request_id) is None


def structured(result: Any) -> dict[str, Any]:
    assert result.success, result.error_code
    data = json.loads(result.output)["structuredContent"]
    assert data["verified"] is True, "unverified_fixture_result"
    return data


def request_for(url: str) -> UserRequest:
    return UserRequest(
        text=(
            f"Abre el navegador en {url}. Rellena Workflow value con {EXACT_VALUE}. "
            "Selecciona Verified. Pulsa Complete workflow y comprueba "
            "Workflow complete 7319."
        )
    )


async def pages(client: aiohttp.ClientSession, endpoint: str) -> dict[str, str]:
    async with client.get(endpoint + "/json/list") as response:
        response.raise_for_status()
        entries = await response.json()
    result = {entry["id"]: entry["url"] for entry in entries if entry.get("type") == "page"}
    # Target.createTarget can briefly expose about:blank before its synthetic URL commits.
    assert result and all(
        url.startswith("https://fixture.test/") or url in {"", "about:blank"}
        for url in result.values()
    ), "unexpected_owned_browser_page"
    return result


async def workflows(
    service: Any, factory: FixturePeerFactory, oracle: FixtureOracle, directory: Path, origin: str
) -> dict[str, Any]:
    flow = TaskWorkflow(service, directory)
    checks: list[str] = []
    original_pages = await pages(oracle.client, oracle.endpoint)
    assert len(original_pages) == 1, "fixture_initial_page_count"
    request = request_for(origin + "/form")
    result, original_authorization = await flow.open(request, origin + "/form", factory, oracle)
    snapshot = structured(result)
    assert snapshot["expected_url"] == origin + "/form", "automatic_wrong_tab"
    assert snapshot["goal_verified"] is False, "access_is_not_task_completion"
    assert await pages(oracle.client, oracle.endpoint) == original_pages, "unique_tab_not_reused"
    checks.extend(
        [
            "no_effect_before_access_grant",
            "one_time_request_scoped_access_grant",
            "automatic_unique_exact_tab_reuse",
            "access_not_reported_as_task_completion",
        ]
    )

    created = len(factory.peers)
    replay = await flow.executor.execute_async(original_authorization, flow.context(request))
    assert not replay.success and replay.error_code == "browser_task_origin_already_approved"
    assert len(factory.peers) == created, "replayed_access_created_peer"
    assert service.context_for_request(request.request_id) is not None
    checks.append("cached_access_authorization_replay_denied")

    result = await flow.action(
        request,
        snapshot,
        "fill",
        target=target_ref(snapshot, "textbox", "Workflow value"),
        text=EXACT_VALUE,
    )
    assert result.metadata["goal_verified"] is True
    assert (await oracle.state())["value"] == EXACT_VALUE, "fill_exact_value_mismatch"
    snapshot = structured(result)
    result = await flow.action(
        request,
        snapshot,
        "select",
        target=target_ref(snapshot, "combobox", "Workflow choice"),
        values=["verified"],
        verify_text="Verified",
    )
    assert result.metadata["goal_verified"] is True
    assert (await oracle.state())["choice"] == "verified", "selected_value_mismatch"
    snapshot = structured(result)
    result = await flow.action(
        request,
        snapshot,
        "click",
        target=target_ref(snapshot, "button", "Complete workflow"),
        verify_text="Workflow complete 7319",
    )
    assert result.metadata["goal_verified"] is True
    snapshot = structured(result)
    assert await oracle.state() == {
        "url": origin + "/form",
        "value": EXACT_VALUE,
        "choice": "verified",
        "result": "Workflow complete 7319",
        "clicks": 1,
    }, "independent_dom_objective_mismatch"
    checks.extend(
        [
            "exact_fill_dom_oracle",
            "selected_value_dom_oracle",
            "visible_click_result_dom_oracle",
            "actions_without_repeated_access_approval",
        ]
    )

    other_request = request_for(origin + "/form")
    raw_calls, before = factory.call_count, await oracle.state()
    mismatched = flow.call(
        "browser_task_action",
        binding(snapshot)
        | {
            "operation": "click",
            "target": target_ref(snapshot, "button", "Complete workflow"),
        },
    )
    denied = flow.broker.authorize(mismatched, flow.context(other_request), request=other_request)
    assert denied.decision is PolicyDecision.DENY, "different_request_not_denied"
    execution = await flow.executor.execute_async(denied, flow.context(other_request))
    assert not execution.success and factory.call_count == raw_calls
    assert await oracle.state() == before, "different_request_caused_effect"
    checks.append("different_request_denied_without_effect")

    await flow.close(request, snapshot)
    assert await pages(oracle.client, oracle.endpoint) == original_pages, "disconnect_closed_tab"
    assert await oracle.state() == before, "disconnect_changed_owned_tab"
    checks.append("disconnect_preserves_browser_and_tab")

    request2 = request_for(origin + "/other")
    result, _ = await flow.open(request2, origin + "/other", factory, oracle)
    snapshot2 = structured(result)
    await oracle.target(origin + "/other")
    assert snapshot2["expected_url"] == origin + "/other", "automatic_new_url_wrong"
    current_pages = await pages(oracle.client, oracle.endpoint)
    assert len(current_pages) == 2 and original_pages.items() <= current_pages.items(), (
        "automatic_missing_url_new_tab_failed"
    )
    assert (await oracle.state())["value"] == "UNCHANGED"
    await service.finish_request(request2.request_id)
    assert service.context_for_request(request2.request_id) is None
    assert await pages(oracle.client, oracle.endpoint) == current_pages, "cleanup_closed_new_tab"
    checks.extend(["automatic_zero_match_creates_new_tab", "request_cleanup_disconnects_access"])

    await oracle.target(origin + "/form")
    duplicate = await oracle.command("Target.createTarget", {"url": origin + "/form"})
    duplicate_id = duplicate["targetId"]
    try:
        async with asyncio.timeout(10):
            while (
                list((await pages(oracle.client, oracle.endpoint)).values()).count(origin + "/form")
                != 2
            ):
                await asyncio.sleep(0.05)
        before_state = await oracle.state()
        peer_offset = len(factory.peers)
        ambiguous = request_for(origin + "/form")
        result, _ = await flow.open(ambiguous, origin + "/form", factory, oracle)
        assert not result.success and result.error_code == "browser_task_tab_ambiguous", (
            result.error_code
        )
        ambiguous_calls = [call for peer in factory.peers[peer_offset:] for call in peer.calls]
        assert ambiguous_calls and all(
            name == "browser_tabs" and args == {"action": "list"} for name, args in ambiguous_calls
        ), "ambiguous_tab_content_read"
        assert await oracle.state() == before_state, "ambiguous_tab_caused_effect"
        assert service.context_for_request(ambiguous.request_id) is None
        checks.append("duplicate_exact_urls_stop_without_snapshot_or_effect")
    finally:
        await oracle.command("Target.closeTarget", {"targetId": duplicate_id})

    request3 = request_for(origin + "/other")
    result, _ = await flow.open(request3, origin + "/other", factory, oracle)
    snapshot3 = structured(result)
    await oracle.target(origin + "/other")
    save_browser_automation_preferences(
        browser_automation_path(service.settings), BrowserAutomationPreferences(enabled=False)
    )
    raw_calls, before = factory.call_count, await oracle.state()
    revoked = flow.call(
        "browser_task_action",
        binding(snapshot3)
        | {
            "operation": "fill",
            "target": target_ref(snapshot3, "textbox", "Workflow value"),
            "text": EXACT_VALUE,
        },
    )
    denial = flow.broker.authorize(revoked, flow.context(request3), request=request3)
    assert denial.decision is PolicyDecision.DENY, "revoked_preference_not_denied"
    assert service.context_for_request(request3.request_id) is None
    execution = await flow.executor.execute_async(denial, flow.context(request3))
    assert not execution.success and factory.call_count == raw_calls
    assert await oracle.state() == before, "revoked_preference_caused_effect"
    await service.finish_request(request3.request_id)
    await service.close()
    assert all(peer.closed and peer.process_terminated for peer in factory.peers), (
        "owned_peer_cleanup_incomplete"
    )
    assert service.status()["active_sessions"] == 0
    assert await pages(oracle.client, oracle.endpoint) == current_pages, "final_cleanup_closed_tab"
    assert flow.action_approvals == 0
    checks.extend(
        [
            "revoked_preferences_deny_without_effect",
            "all_owned_peer_processes_terminated",
            "zero_active_sessions_after_cleanup",
        ]
    )
    return {
        "passed": True,
        "checks": checks,
        "broker_invocations": flow.invocations,
        "access_approvals": flow.access_approvals,
        "action_access_approvals": flow.action_approvals,
        "real_peer_calls": factory.call_count,
        "peer_processes": len(factory.peers),
        "peer_version": "0.0.80",
        "real_browser": True,
        "isolated_profile": True,
        "transport": "owned_fixture_cdp",
        "extension_consent_not_exercised": True,
        "synthetic_https_only": True,
        "owner_data_read": False,
        "model_api_calls": 0,
        "certifies": "synthetic_browser_task_flow_not_interactive_extension_handshake",
    }


async def qualify(arguments: argparse.Namespace) -> dict[str, Any]:
    # Defer importing the service until after the explicit --run gate.
    from aegis_core.mcp.browser_tasks import BrowserTaskService

    packages = pinned_packages(arguments.packages)
    chrome, node = arguments.chrome.resolve(strict=True), arguments.node.resolve(strict=True)
    with TemporaryDirectory(prefix="jarvis-browser-tasks-", dir="/private/tmp") as temporary:
        directory = Path(temporary)
        os.chmod(directory, 0o700)
        settings = Settings(
            ipc_socket_path=directory / "aegis.sock",
            mcp_node_packages_path=packages,
            mcp_node_executable_path=node,
            mcp_configuration_path=directory / "mcp.json",
            model_preferences_path=directory / "models.json",
        )
        save_browser_automation_preferences(
            browser_automation_path(settings), BrowserAutomationPreferences()
        )
        async with (
            fixture_server(directory) as (origin, port),
            private_chrome(
                chrome,
                directory,
                origin + "/form",
                port,
            ) as endpoint,
            aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=10),
                trust_env=False,
            ) as client,
        ):
            oracle = FixtureOracle(client, endpoint)
            await oracle.target(origin + "/form")
            factory = FixturePeerFactory(endpoint, directory, packages, node)
            service = BrowserTaskService(settings, client_factory=factory)
            try:
                assert service.status()["node_configured"], "pinned_peer_not_ready"
                return await workflows(service, factory, oracle, directory, origin)
            finally:
                await service.close()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", action="store_true", help="Opt in to the isolated real browser")
    parser.add_argument("--packages", type=Path, default=default_packages())
    parser.add_argument("--node", type=Path, default=Path(shutil.which("node") or "/missing/node"))
    parser.add_argument(
        "--chrome",
        type=Path,
        default=Path(
            "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
        ),
    )
    arguments = parser.parse_args()
    if not arguments.run:
        parser.error("--run is required; no Chrome or peer connection was started")

    async def bounded() -> dict[str, Any]:
        async with asyncio.timeout(120):
            return await qualify(arguments)

    try:
        print(json.dumps(asyncio.run(bounded()), sort_keys=True))
    except (AssertionError, OSError, ValueError, RuntimeError, TimeoutError) as error:
        reason = str(error)
        safe_reason = (
            reason if re.fullmatch(r"[a-z0-9_]{1,100}", reason) else "qualification_failed"
        )
        trace, failure_line = error.__traceback__, None
        while trace is not None:
            if trace.tb_frame.f_code.co_filename == __file__:
                failure_line = trace.tb_lineno
            trace = trace.tb_next
        print(
            json.dumps(
                {
                    "passed": False,
                    "failure_type": type(error).__name__,
                    "failure_code": safe_reason,
                    "failure_line": failure_line,
                    "extension_consent_not_exercised": True,
                    "transport": "owned_fixture_cdp",
                    "certifies": "no_success_claim",
                },
                sort_keys=True,
            )
        )
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
