"""Request-owned browser access; every fixture is synthetic and runs no browser."""
from __future__ import annotations

import asyncio
import json
import sys
from dataclasses import replace
from pathlib import Path
from uuid import uuid4

import pytest
from test_mcp_browser_session import URL, Peer, make_packages, schemas, text_result

from aegis_core.browser_preferences import BrowserAutomationPreferences
from aegis_core.config import Settings
from aegis_core.contracts import (
    AgentRole,
    Capability,
    PolicyDecision,
    ToolAuthorization,
    ToolCall,
    UserRequest,
)
from aegis_core.ipc.protocol import IpcAuthenticator
from aegis_core.mcp.browser_tasks import (
    BrowserTaskActionArguments,
    BrowserTaskOpenArguments,
    BrowserTaskService,
    canonical_browser_url,
    explicit_browser_urls,
    is_browser_task_request,
)
from aegis_core.mcp.client import reviewed_schema_sha256, validate_integration_process
from aegis_core.mcp.integrations import BrowserSessionConfiguration, IntegrationScope
from aegis_core.tools.broker import PolicyContext, ToolBroker, ToolRegistry
from aegis_core.tools.confirmations import OneTimeConfirmationStore


class TaskPeer(Peer):
    def __init__(self, configuration):
        super().__init__()
        self.configuration = configuration
        self.close_count = 0
        self.start_gate = None
        self.schema_override = None
        self.before_call = None
        self.empty_tabs_message = "No open tabs"

    async def start(self):
        validate_integration_process(self.configuration)
        if self.start_gate is not None:
            await self.start_gate.wait()
        return tuple({"name": name, "inputSchema": schema}
                     for name, schema in (self.schema_override or schemas()).items())

    async def close(self):
        self.close_count += 1
        await super().close()

    async def call_tool(self, name, arguments):
        if self.before_call is not None:
            self.before_call(name, arguments)
        if name == "browser_tabs" and arguments["action"] == "list" and not self.urls:
            self.calls.append((name, dict(arguments)))
            return text_result("### Result\n" + self.empty_tabs_message)
        if name == "browser_snapshot" and len(self.urls) < 3:
            original = self.urls
            self.urls = [*self.urls, *("https://private.example/account"
                                      for _ in range(3 - len(self.urls)))]
            try:
                return await super().call_tool(name, arguments)
            finally:
                self.urls = original
        return await super().call_tool(name, arguments)


@pytest.fixture
async def harness(tmp_path):
    packages = make_packages(tmp_path)
    settings = Settings(_env_file=None, mcp_node_packages_path=packages,
                        mcp_node_executable_path=Path(sys.executable).resolve(),
                        ipc_socket_path=tmp_path / "runtime/jarvis.sock")
    scope = IntegrationScope(provider="playwright", browser_origins={"https://example.com"})
    pins = {name: reviewed_schema_sha256(schema, scope, name)
            for name, schema in schemas().items()}
    preferences = [BrowserAutomationPreferences()]
    peers = []
    services = []
    def create(*, adjust=None, **kwargs):
        def factory(configuration):
            peer = TaskPeer(configuration)
            if adjust is not None:
                adjust(peer)
            peers.append(peer)
            return peer
        service = BrowserTaskService(settings, preferences_loader=lambda: preferences[0],
                                     client_factory=factory, schema_pins=pins, **kwargs)
        services.append(service)
        return service
    yield create, preferences, peers, settings
    await asyncio.gather(*(service.close() for service in services))


def request(text=None):
    return UserRequest(text=text or f"Abre {URL} en mi navegador y escribe hola; "
                                   "selecciona Reciente; espera Hecho.")


def context(req, workspace=Path("/tmp")):
    return PolicyContext(workspace_root=workspace, request_id=req.request_id, request_text=req.text)


def authorization(name, arguments, *, reason="policy_allowed", call_id=None):
    return ToolAuthorization(call_id=call_id or str(uuid4()), tool_name=name, call_digest="a" * 64,
                             decision=PolicyDecision.ALLOW, reason_code=reason,
                             normalized_arguments=arguments)


async def opened(service, req=None, *, url=URL, mode=None, call_id=None):
    req = req or request()
    args = {"url": url, "objective": req.text, "mode": mode}
    result = await service.handlers()["browser_task_open"](
        authorization("browser_task_open", args, reason="confirmation_consumed", call_id=call_id),
        context(req),
    )
    data = json.loads(result.output).get("structuredContent", {}) if result.output else {}
    return req, result, data


async def act(service, req, data, operation="snapshot", **fields):
    args = {key: data[key] for key in ("session_id", "expected_url", "snapshot_sha256")}
    args.update(operation=operation, **fields)
    return await service.handlers()["browser_task_action"](
        authorization("browser_task_action", args), context(req),
    )


@pytest.mark.parametrize("text, expected", [
    (f"Abre {URL} y selecciona Informes", True),
    (f'Abre "{URL}"', True),
    (f'Abre “{URL}”', True),
    (f"¿Puedes abrir {URL}?", True),
    ("Abre YouTube en mi navegador y busca música", True),
    ("Usa Chrome para seleccionar Informes", True),
    ("Busca tutoriales de Python en mi navegador", True),
    ("Busca Python en Chrome", True),
    ("Abre la página de YouTube", True),
    (f"Visita {URL}", True),
    ("Ve a la página de Example", True),
    (f"¿Qué opinas de {URL}?", False),
    (f"Investiga {URL} y busca información", False),
    (f"No abras {URL} en mi navegador", False),
    (f"Resume esta cita: «abre {URL} en el navegador»", False),
    (URL, False),
])
def test_activation_requires_actual_browser_control(text, expected):
    assert is_browser_task_request(text) is expected


async def test_access_requires_consumed_grant_and_original_identity(harness):
    create, _, peers, _ = harness
    service = create()
    req = request()
    result = await service.handlers()["browser_task_open"](
        authorization("browser_task_open", {"url": URL, "objective": req.text}), context(req),
    )
    assert result.error_code == "confirmation_not_consumed" and not peers
    result = await service.handlers()["browser_task_open"](
        authorization("browser_task_open", {"url": URL, "objective": req.text},
                      reason="confirmation_consumed"), PolicyContext(workspace_root=Path("/tmp")),
    )
    assert result.error_code == "browser_task_request_context_required" and not peers
    _, result, _ = await opened(service, req, url="https://other.example/start")
    assert result.error_code == "browser_task_original_url_required" and not peers


async def test_broker_approves_exact_access_once_and_action_needs_no_second_modal(harness):
    create, _, _, _ = harness
    service = create()
    req = request()
    grants = OneTimeConfirmationStore()
    ctx = replace(context(req), confirmation_store=grants)
    broker = ToolBroker(ToolRegistry(service.tool_definitions()),
                        {AgentRole.PLANNER: frozenset({Capability.APPLICATION_CONTROL})})
    call = ToolCall(call_id="open_once", tool_name="browser_task_open",
                    arguments={"url": URL, "objective": req.text}, requested_by=AgentRole.PLANNER)
    pending = broker.authorize(call, ctx, request=req)
    assert pending.decision is PolicyDecision.REQUIRE_CONFIRMATION
    assert pending.normalized_arguments["mode"] == "extension"
    grants.issue(call, pending, approved_by="synthetic-owner", request_id=req.request_id)
    approved = broker.authorize(call, ctx, request=req)
    assert approved.reason_code == "confirmation_consumed"
    result = await service.handlers()["browser_task_open"](approved, ctx)
    data = json.loads(result.output)["structuredContent"]
    args = {key: data[key] for key in ("session_id", "expected_url", "snapshot_sha256")}
    args.update(operation="fill", target="e1", text="hola")
    next_call = ToolCall(call_id="fill", tool_name="browser_task_action", arguments=args,
                        requested_by=AgentRole.PLANNER)
    allowed = broker.authorize(next_call, ctx, request=req)
    assert allowed.decision is PolicyDecision.ALLOW and allowed.reason_code == "policy_allowed"
    performed = await service.handlers()["browser_task_action"](allowed, ctx)
    assert performed.success and performed.metadata["goal_verified"] is True
    assert broker.authorize(call, ctx, request=req).decision is PolicyDecision.DENY


@pytest.mark.parametrize("selected", [0, 1])
async def test_exact_unique_tab_reused_without_returning_other_tab_content(harness, selected):
    create, _, peers, _ = harness
    service = create(adjust=lambda peer: setattr(peer, "selected", selected))
    req, result, data = await opened(service)
    assert result.success and data["verified"] is True and data["goal_verified"] is False
    assert data["verification_scope"] == "browser_task_access"
    assert data["task_request_id"] == str(req.request_id)
    assert data["remaining_actions"] == 24
    assert peers[0].selected == 0
    assert not any(name == "browser_navigate" for name, _ in peers[0].calls)
    assert not any(args.get("action") == "new" for _, args in peers[0].calls)
    assert "PRIVATE" not in result.output and "private.example" not in result.output
    assert service.names_for_request(req) == frozenset(
        {"browser_task_action", "browser_task_close"})


@pytest.mark.parametrize("empty", [False, True])
async def test_missing_match_creates_owned_tab_including_no_open_tabs(harness, empty):
    create, _, peers, _ = harness
    service = create(adjust=lambda peer: setattr(peer, "urls", [] if empty else ["about:blank"]))
    _, result, data = await opened(service)
    assert result.success, result.error_code
    assert peers[0].urls[-1] == URL and data["expected_url"] == URL
    assert any(args.get("action") == "new" for _, args in peers[0].calls)


async def test_duplicate_tabs_stop_before_select_or_read_and_disconnect(harness):
    create, _, peers, _ = harness
    service = create(adjust=lambda peer: peer.urls.append(URL))
    _, result, _ = await opened(service)
    assert result.error_code == "browser_task_tab_ambiguous"
    assert [name for name, _ in peers[0].calls] == ["browser_tabs"]
    assert peers[0].close_count == 1


async def test_site_without_literal_url_is_proposal_approved_once(harness):
    create, _, _, _ = harness
    service = create(adjust=lambda peer: setattr(peer, "urls", ["https://example.com/start"]))
    req = request("Abre Example en mi navegador y lee su página principal")
    _, result, data = await opened(service, req)
    assert result.success and data["expected_url"] == URL
    assert service.names_for_request(request("¿Cómo usar Example en mi navegador?")) == frozenset()


async def test_cross_request_or_changed_objective_cannot_reuse_session(harness):
    create, _, peers, _ = harness
    service = create()
    req, _, data = await opened(service)
    other = request()
    result = await act(service, other, data, "fill", target="e1", text="hola")
    assert result.error_code == "browser_task_session_unavailable"
    assert peers[0].value == "" and peers[0].close_count == 0
    altered = req.model_copy(update={"text": req.text + " y escribe secreto"})
    result = await act(service, altered, data, "fill", target="e1", text="secreto")
    assert result.error_code == "browser_task_session_unavailable" and peers[0].value == ""
    assert peers[0].close_count == 1


async def test_action_strips_local_binding_and_verifies_actual_value(harness):
    create, _, peers, _ = harness
    service = create()
    req, _, data = await opened(service)
    result = await act(service, req, data, "fill", target="e1", text="hola")
    assert result.success and result.metadata["goal_verified"] is True
    calls = [args for name, args in peers[0].calls if name == "browser_type"]
    assert calls == [{"target": "e1", "text": "hola", "submit": False}]
    new_data = service.context_for_request(req.request_id)
    assert new_data["snapshot_sha256"] != data["snapshot_sha256"]


async def test_unverified_mutation_disconnects_and_cannot_repeat(harness):
    create, _, peers, _ = harness
    service = create()
    req, _, data = await opened(service)
    result = await act(service, req, data, "click", target="e2")
    assert result.success and result.metadata["goal_verified"] is False
    assert result.metadata["action_performed"] is True and peers[0].close_count == 1
    assert service.context_for_request(req.request_id) is None
    repeated = await act(service, req, data, "click", target="e2")
    assert repeated.error_code == "browser_task_session_unavailable"
    assert sum(name == "browser_click" for name, _ in peers[0].calls) == 1


@pytest.mark.parametrize("fields", [
    {"operation": "fill", "target": "e1", "text": "invented"},
    {"operation": "click", "target": "e2", "verify_text": "invented"},
    {"operation": "wait", "wait_text": "invented"},
    {"operation": "wait", "wait_text_gone": "invented"},
    {"operation": "snapshot", "snapshot_sha256": "0" * 64},
    {"operation": "navigate", "url": "https://other.example/outside"},
])
async def test_literal_stale_or_other_origin_actions_do_not_dispatch(harness, fields):
    create, _, peers, _ = harness
    service = create()
    req, _, data = await opened(service)
    before = len(peers[0].calls)
    result = await act(service, req, data, **fields)
    assert not result.success
    assert not any(name in {"browser_click", "browser_type", "browser_navigate", "browser_wait_for"}
                   for name, _ in peers[0].calls[before:])


@pytest.mark.parametrize("operation,fields", [
    ("fill", {"target": "e1", "text": "hola", "submit": True}),
    ("key", {"key": "Enter"}), ("tabs", {"tab_action": "close"}),
    ("snapshot", {"code": "alert(1)"}), ("evaluate", {}),
])
def test_schema_rejects_arbitrary_code_submit_close_and_hidden_fields(operation, fields):
    with pytest.raises(ValueError):
        BrowserTaskActionArguments(session_id=uuid4(), expected_url=URL, snapshot_sha256="a" * 64,
                                   operation=operation, **fields)


async def test_schema_changed_after_review_rejected_before_any_browser_call(harness):
    create, _, peers, _ = harness
    def modify(peer):
        peer.schema_override = schemas()
        peer.schema_override["browser_click"]["properties"]["hidden_execute"] = {"type": "string"}
    service = create(adjust=modify)
    _, result, _ = await opened(service)
    assert not result.success and not peers[0].calls and peers[0].close_count == 1


async def test_pinned_artifact_changed_after_service_init_is_not_reapproved(harness):
    create, _, peers, settings = harness
    service = create()
    (settings.mcp_node_packages_path / "node_modules/playwright-core/lib/coreBundle.js").write_text(
        "// tampered after startup")
    _, result, _ = await opened(service)
    assert not result.success and not peers[0].calls and peers[0].close_count == 1


async def test_preference_revocation_invalidates_existing_session_immediately(harness):
    create, preferences, peers, _ = harness
    service = create()
    req, _, data = await opened(service)
    preferences[0] = BrowserAutomationPreferences(enabled=False)
    result = await act(service, req, data, "fill", target="e1", text="hola")
    assert not result.success and peers[0].value == "" and peers[0].close_count == 1
    assert service.names_for_request(req) == frozenset()


async def test_request_finish_is_idempotent_and_never_closes_personal_tab(harness):
    create, _, peers, _ = harness
    service = create()
    req, _, _ = await opened(service)
    urls = list(peers[0].urls)
    await service.finish_request(req.request_id)
    await service.finish_request(req.request_id)
    assert peers[0].close_count == 1 and peers[0].urls == urls
    assert not any(name == "browser_close" or args.get("action") == "close"
                   for name, args in peers[0].calls)
    _, result, _ = await opened(service, req)
    assert result.error_code == "browser_task_request_finished"


async def test_expiry_closes_peer_without_next_action(harness):
    create, _, peers, _ = harness
    service = create()
    service.TTL_SECONDS = 0.25
    req, result, _ = await opened(service)
    assert result.success
    await asyncio.sleep(0.3)
    assert peers[0].close_count == 1 and service.context_for_request(req.request_id) is None


async def test_cancellation_during_bootstrap_closes_owned_peer_only(harness):
    create, _, peers, _ = harness
    gate = asyncio.Event()
    service = create(adjust=lambda peer: setattr(peer, "start_gate", gate))
    pending = asyncio.create_task(opened(service))
    await asyncio.sleep(0)
    pending.cancel()
    with pytest.raises(asyncio.CancelledError):
        await pending
    assert peers[0].close_count == 1 and not peers[0].calls
    assert service.status()["active_sessions"] == 0


async def test_action_budget_and_session_capacity_are_bounded(harness):
    create, _, peers, _ = harness
    service = create()
    req, _, data = await opened(service)
    for _ in range(24):
        result = await act(service, req, data)
        assert result.success and result.metadata["goal_verified"] is False
        data = service.context_for_request(req.request_id)
    result = await act(service, req, data)
    assert result.error_code == "browser_task_action_limit"
    assert peers[0].close_count == 1
    for _ in range(4):
        assert (await opened(service))[1].success
    _, result, _ = await opened(service)
    assert result.error_code == "browser_task_capacity" and len(peers) == 5


async def test_second_approved_origin_replaces_previous_context_only_after_verified(harness):
    create, _, peers, _ = harness
    service = create()
    req = request(f"Abre {URL} y https://other.example/start en mi navegador")
    _, first, original = await opened(service, req)
    assert first.success
    _, second, current = await opened(service, req, url="https://other.example/start")
    assert second.success
    assert current["session_id"] != original["session_id"] and peers[0].close_count == 1
    assert service.context_for_request(req.request_id)["expected_url"] == "https://other.example/start"


async def test_snapshot_context_truncates_whole_lines_without_changing_digest(harness):
    create, _, _, _ = harness
    service = create(adjust=lambda peer: setattr(peer, "extra", "\n".join(
        f'- generic: line {index} ' + '"quoted" ' * 20 for index in range(100))))
    req, result, data = await opened(service)
    assert result.success
    excerpt = service.context_for_request(req.request_id)
    assert excerpt["snapshot_truncated"] is True
    assert len(json.dumps(excerpt, ensure_ascii=False).encode()) <= 15360
    assert excerpt["snapshot_sha256"] == data["snapshot_sha256"]
    assert data["snapshot"].startswith(excerpt["snapshot"])
    assert excerpt["snapshot"].splitlines()[-1] in data["snapshot"].splitlines()


async def test_status_never_returns_content_urls_or_grants_and_payload_must_be_empty(harness):
    create, _, _, _ = harness
    service = create()
    await opened(service)
    ipc = IpcAuthenticator(b"a" * 32)
    handler = service.ipc_handlers()["browser.task.status"]
    reply = await handler(ipc.create_request("browser.task.status"))
    encoded = json.dumps(reply.payload)
    assert reply.ok and URL not in encoded
    assert "snapshot" not in encoded and "session_id" not in encoded
    assert reply.payload["active_sessions"] == 1
    reply = await handler(ipc.create_request("browser.task.status", {"url": URL}))
    assert not reply.ok and reply.error_code == "invalid_payload"


async def test_tabs_list_is_local_only_and_never_reports_goal_completed(harness):
    create, _, _, _ = harness
    service = create()
    req, _, data = await opened(service)
    result = await act(service, req, data, "tabs", tab_action="list")
    assert result.success and result.metadata["goal_verified"] is False
    output = json.loads(result.output)["structuredContent"]
    assert "tabs" not in output and "https://example.com/other" not in result.output


@pytest.mark.parametrize("mode, instruction, success", [
    ("isolated", "en modo aislado", True),
    ("isolated", "en mi navegador", False),
    ("extension", "en mi navegador", True),
])
async def test_reviewed_mode_is_concrete_and_isolated_browser_is_visible(harness, mode,
                                                                        instruction, success):
    create, _, peers, _ = harness
    service = create()
    req = request(f"Abre {URL} {instruction}")
    _, result, _ = await opened(service, req, mode=mode)
    assert result.success is success
    if success:
        config = peers[0].configuration
        assert config.browser_session.mode == mode
        validate_integration_process(config)
        if mode == "isolated":
            assert config.browser_session.visible is True
            assert "--headless" not in config.arguments and "--isolated" in config.arguments
        else:
            assert "--extension" in config.arguments and "--isolated" not in config.arguments
    else:
        assert result.error_code == "browser_task_mode_not_requested" and not peers


@pytest.mark.parametrize("value, label, success", [
    ("Delete", "Delete", False), ("eliminar", "Eliminar", False),
    ("op2", "Borrar historial", False), ("Download", "Descargar archivo", False),
    ("Reciente", "Borrar historial", False),
    ("Reciente", "Delete", False),
    ("Reciente", "Antiguo", True),
])
async def test_destructive_option_values_or_opaque_labels_are_not_selected(harness, value,
                                                                        label, success):
    create, _, peers, _ = harness
    service = create()
    req, _, data = await opened(service)
    peer = peers[0]
    original_call = peer.call_tool
    async def with_option(name, args):
        result = await original_call(name, args)
        if name == "browser_snapshot":
            result["content"][0]["text"] = result["content"][0]["text"].replace(
                '- generic: Ready', f'  - option "{label}"\n- generic: Ready').replace(
                '- generic: Hecho', f'  - option "{label}"\n- generic: Hecho')
        return result
    peer.call_tool = with_option
    refreshed = await act(service, req, data)
    assert refreshed.success
    data = service.context_for_request(req.request_id)
    result = await act(service, req, data, "select", target="e3", values=[value])
    assert result.success is success
    assert any(name == "browser_select_option" for name, _ in peer.calls) is success


async def test_selected_tab_race_after_bootstrap_select_never_reads_other_page(harness):
    create, _, peers, _ = harness
    def adjust(peer):
        peer.selected = 1
        listed = 0
        def change(name, args):
            nonlocal listed
            if name == "browser_tabs" and args.get("action") == "list":
                listed += 1
                if listed == 2:
                    peer.selected = 1
        peer.before_call = change
    service = create(adjust=adjust)
    _, result, _ = await opened(service)
    assert result.error_code == "browser_task_selection_unverified"
    assert not any(name == "browser_snapshot" for name, _ in peers[0].calls)
    assert peers[0].close_count == 1


async def test_cancellation_after_action_dispatch_invalidates_without_retry(harness):
    create, _, peers, _ = harness
    service = create()
    req, _, data = await opened(service)
    peer = peers[0]
    peer.block_action = asyncio.Event()
    pending = asyncio.create_task(act(service, req, data, "fill", target="e1", text="hola"))
    for _ in range(20):
        if any(name == "browser_type" for name, _ in peer.calls):
            break
        await asyncio.sleep(0)
    assert any(name == "browser_type" for name, _ in peer.calls)
    pending.cancel()
    with pytest.raises(asyncio.CancelledError):
        await pending
    assert peer.close_count == 1 and service.context_for_request(req.request_id) is None
    repeated = await act(service, req, data, "fill", target="e1", text="hola")
    assert not repeated.success
    assert sum(name == "browser_type" for name, _ in peer.calls) == 1


async def test_nonbrowser_completions_do_not_accumulate_finished_tombstones(harness):
    create, _, _, _ = harness
    service = create()
    for _ in range(300):
        await service.finish_request(uuid4())
    assert not service._finished and not service._open_calls
    req, _, _ = await opened(service)
    await service.finish_request(req.request_id)
    assert req.request_id in service._finished
    assert service.names_for_request(req) == frozenset()


async def test_retention_capacity_does_not_evict_unexpired_consumed_grants(harness):
    create, _, _, _ = harness
    now = [0.0]
    service = create(clock=lambda: now[0])
    service.MAX_PENDING_CALLS = 2
    first, _, _ = await opened(service)
    await service.finish_request(first.request_id)
    second, _, _ = await opened(service)
    await service.finish_request(second.request_id)
    _, denied, _ = await opened(service)
    assert denied.error_code == "browser_task_pending_capacity"
    assert set(service._finished) == {first.request_id, second.request_id}
    now[0] = 601.0
    assert (await opened(service))[1].success
    assert len(service._finished) == 0


async def test_malformed_peer_version_cannot_crash_daemon_construction(harness):
    create, _, _, settings = harness
    (settings.mcp_node_packages_path / "node_modules/@playwright/mcp/package.json").write_text("[]")
    service = create()
    assert service.status()["reason_code"] == "browser_task_peer_unavailable"
    assert service.status()["node_configured"] is False
    _, result, _ = await opened(service)
    assert result.error_code == "browser_task_peer_unavailable"


async def test_stale_failure_reports_only_current_call_dispatch_after_prior_effect(harness):
    create, _, peers, _ = harness
    service = create(adjust=lambda peer: setattr(peer, "urls", ["about:blank"]))
    req, _, data = await opened(service)
    assert any(args.get("action") == "new" for _, args in peers[0].calls)
    filled = await act(service, req, data, "fill", target="e1", text="hola")
    assert filled.success
    current = service.context_for_request(req.request_id)
    stale = await act(service, req, current, "fill", target="e1", text="hola",
                      snapshot_sha256="0" * 64)
    assert stale.error_code == "browser_task_snapshot_stale"
    assert stale.metadata["action_dispatched"] is False
    assert sum(name == "browser_type" for name, _ in peers[0].calls) == 1


async def test_public_search_with_login_link_works_but_login_reference_is_omitted(harness):
    create, _, peers, _ = harness
    service = create(adjust=lambda peer: setattr(peer, "extra", '- link "Sign in" [ref=e9]'))
    req, result, data = await opened(service)
    assert result.success and "ref=e9" not in data["snapshot"]
    assert "Sign in" not in data["snapshot"]
    performed = await act(service, req, data, "fill", target="e1", text="hola")
    assert performed.success and performed.metadata["goal_verified"] is True
    assert peers[0].value == "hola"


@pytest.mark.parametrize("password, path", [(True, "/start"), (False, "/auth")])
async def test_password_fields_and_auth_routes_still_block_all_interaction(harness, password, path):
    create, _, peers, _ = harness
    url = "https://example.com" + path
    def adjust(peer):
        peer.urls[0] = url
        if password:
            peer.extra = '- textbox "Password" [ref=e9]: "PRIVATE_PASSWORD"'
    service = create(adjust=adjust)
    req = request(f"Abre {url} en mi navegador y escribe hola")
    _, result, data = await opened(service, req, url=url)
    assert result.success and "PRIVATE_PASSWORD" not in result.output
    assert "ref=e9" not in result.output
    performed = await act(service, req, data, "fill", target="e1", text="hola")
    assert performed.error_code == "browser_sensitive_page_action_excluded"
    assert peers[0].value == ""


@pytest.mark.parametrize("sentinel, success", [
    ("No open tabs. Navigate to a URL to create one.", True),
    ("No open tabs. Maybe clear data and start again.", False),
    ("No open tabs\nPRIVATE_USER_PAGE", False),
])
async def test_bootstrap_only_recognizes_exact_peer_empty_sentinel(harness, sentinel, success):
    create, _, peers, _ = harness
    def adjust(peer):
        peer.urls = []
        peer.empty_tabs_message = sentinel
    service = create(adjust=adjust)
    _, result, _ = await opened(service)
    assert result.success is success
    assert any(args.get("action") == "new" for _, args in peers[0].calls) is success
    if not success:
        assert result.error_code == "browser_tabs_invalid"
        assert not any(name == "browser_snapshot" for name, _ in peers[0].calls)


async def test_url_in_typographic_quotes_still_binds_exact_original_url(harness):
    create, _, _, _ = harness
    service = create()
    req = request(f'Abre “{URL}” en mi navegador')
    _, result, data = await opened(service, req)
    assert result.success and data["expected_url"] == URL


@pytest.mark.parametrize("original, canonical", [
    ("https://example.com", "https://example.com/"),
    ("https://EXAMPLE.com:443", "https://example.com/"),
    ("https://EXAMPLE.com:443/A%2Fb?q=KeepCase#Section", "https://example.com/A%2Fb?q=KeepCase#Section"),
])
def test_benign_url_canonicalization_preserves_path_query_fragment(original, canonical):
    assert canonical_browser_url(original) == canonical
    args = BrowserTaskOpenArguments(url=original, objective="Abre el navegador")
    assert args.url == canonical
    assert explicit_browser_urls(f'Abre “{original}”') == frozenset({canonical})


@pytest.mark.parametrize("reuse", [True, False])
async def test_bare_domain_reuses_or_creates_canonical_url_after_exact_approval(harness, reuse):
    create, _, peers, _ = harness
    def adjust(peer):
        peer.urls[0] = "https://example.com/" if reuse else "about:blank"
    service = create(adjust=adjust)
    req = request('Abre “https://EXAMPLE.com:443” en mi navegador')
    _, result, data = await opened(service, req, url="https://example.com")
    assert result.success and data["expected_url"] == "https://example.com/"
    assert peers[0].configuration.browser_session.selected_tab_url == "https://example.com/"
    assert any(args.get("action") == "new" for _, args in peers[0].calls) is not reuse


async def test_canonicalization_never_accepts_different_path_or_redirect(harness):
    create, _, peers, _ = harness
    service = create()
    req = request("Abre https://example.com/ en mi navegador")
    _, denied, _ = await opened(service, req, url=URL)
    assert denied.error_code == "browser_task_original_url_required" and not peers
    def redirect(peer):
        listed = 0
        def after(name, args):
            nonlocal listed
            if name == "browser_tabs" and args.get("action") == "list":
                listed += 1
                if listed == 2:
                    peer.urls[peer.selected] = "https://example.com/unapproved"
        peer.before_call = after
    redirected = create(adjust=redirect)
    _, denied, _ = await opened(redirected)
    assert denied.error_code == "browser_task_selection_unverified"
    assert not any(name == "browser_snapshot" for name, _ in peers[-1].calls)


async def test_redundant_open_is_not_offered_and_denied_without_touching_active_session(harness):
    create, _, peers, _ = harness
    service = create()
    req, first, data = await opened(service)
    assert first.success
    assert service.names_for_request(req) == frozenset(
        {"browser_task_action", "browser_task_close"})
    before = list(peers[0].calls)
    _, repeated, _ = await opened(service, req)
    assert repeated.error_code == "browser_task_origin_already_approved"
    assert len(peers) == 1 and peers[0].calls == before and peers[0].close_count == 0
    assert service.context_for_request(req.request_id)["session_id"] == data["session_id"]
    assert (await act(service, req, data, "fill", target="e1", text="hola")).success


async def test_distinct_original_origin_offers_new_approval_same_origin_uses_actions(harness):
    create, _, peers, _ = harness
    service = create()
    other = "https://other.example/start"
    req = request(f"Abre {URL} y https://example.com/other y {other} en mi navegador")
    _, result, original = await opened(service, req)
    assert result.success and "browser_task_open" in service.names_for_request(req)
    _, redundant, _ = await opened(service, req, url="https://example.com/other")
    assert redundant.error_code == "browser_task_origin_already_approved"
    assert peers[0].close_count == 0
    _, result, current = await opened(service, req, url=other)
    assert result.success and current["session_id"] != original["session_id"]
    assert service.names_for_request(req) == frozenset(
        {"browser_task_action", "browser_task_close"})
    before = len(peers)
    _, redundant, _ = await opened(service, req)
    assert redundant.error_code == "browser_task_origin_already_approved" and len(peers) == before
    assert service.context_for_request(req.request_id)["session_id"] == current["session_id"]


async def test_closed_or_failed_access_is_not_automatically_reopened_in_same_task(harness):
    create, _, peers, _ = harness
    service = create()
    req, _, data = await opened(service)
    await service.handlers()["browser_task_close"](
        authorization("browser_task_close", {"session_id": data["session_id"]}), context(req))
    assert service.names_for_request(req) == frozenset()
    _, denied, _ = await opened(service, req)
    assert denied.error_code == "browser_task_origin_already_approved" and len(peers) == 1
    failed = create(adjust=lambda peer: peer.urls.append(URL))
    rejected_req, result, _ = await opened(failed)
    assert not result.success and failed.names_for_request(rejected_req) == frozenset()
    _, result, _ = await opened(failed, rejected_req)
    assert result.error_code == "browser_task_origin_already_approved"


@pytest.mark.parametrize("endpoint, admitted", [
    ("chrome", True), ("http://127.0.0.1:9222", True),
    ("Chrome", False), ("chrome ", False), (" chrome", False),
    ("chrome-beta", False), ("chromium", False), ("msedge", False),
    ("chrome://inspect/#remote-debugging", False), ("http://chrome", False),
    ("http://10.0.0.1:9222", False), ("ws://127.0.0.1:9222", False),
])
def test_cdp_review_accepts_only_exact_chrome_alias_or_existing_loopback(endpoint, admitted):
    arguments = dict(mode="cdp", cdp_endpoint=endpoint, selected_tab_url=URL)
    if admitted:
        assert BrowserSessionConfiguration(**arguments).cdp_endpoint == endpoint
    else:
        with pytest.raises(ValueError):
            BrowserSessionConfiguration(**arguments)


@pytest.mark.parametrize("instruction, allowed", [
    ("en Chrome directo", True), ("con CDP", True), ("con depuración", True),
    ("en mi navegador", False), ("en Chrome", False),
    ('en mi navegador, etiqueta “Chrome directo”', False),
    ('en mi navegador, cita “CDP”', False),
])
async def test_chrome_override_requires_direct_mode_in_original_outside_quotes(harness,
                                                                            instruction, allowed):
    create, _, peers, _ = harness
    service = create()
    req = request(f"Abre {URL} {instruction}")
    _, result, _ = await opened(service, req, mode="chrome")
    assert result.success is allowed
    if allowed:
        config = peers[0].configuration
        assert config.browser_session.mode == "cdp"
        assert config.browser_session.cdp_endpoint == "chrome"
        assert config.browser_session.visible is False
    else:
        assert result.error_code == "browser_task_mode_not_requested" and not peers


async def test_chrome_connection_is_not_started_until_exact_broker_grant(harness):
    create, preferences, peers, _ = harness
    preferences[0] = BrowserAutomationPreferences(mode="chrome")
    service = create()
    req = request()
    grants = OneTimeConfirmationStore()
    ctx = replace(context(req), confirmation_store=grants)
    broker = ToolBroker(ToolRegistry(service.tool_definitions()),
                        {AgentRole.PLANNER: frozenset({Capability.APPLICATION_CONTROL})})
    call = ToolCall(call_id="chrome_once", tool_name="browser_task_open",
                    arguments={"url": URL, "objective": req.text}, requested_by=AgentRole.PLANNER)
    pending = broker.authorize(call, ctx, request=req)
    assert pending.decision is PolicyDecision.REQUIRE_CONFIRMATION
    assert pending.normalized_arguments["mode"] == "chrome" and not peers
    assert service.status()["active_sessions"] == 0
    denied = await service.handlers()["browser_task_open"](pending, ctx)
    assert not denied.success and not peers
    grants.issue(call, pending, approved_by="synthetic-owner", request_id=req.request_id)
    approved = broker.authorize(call, ctx, request=req)
    assert approved.reason_code == "confirmation_consumed"
    opened_result = await service.handlers()["browser_task_open"](approved, ctx)
    assert opened_result.success and opened_result.metadata["goal_verified"] is False
    config = peers[0].configuration
    assert config.command.name.startswith("python") and "npx" not in config.arguments
    assert "--extension" not in config.arguments and "--isolated" not in config.arguments
    assert "--cdp-endpoint" in config.arguments
    assert config.arguments[config.arguments.index("--cdp-endpoint") + 1] == "chrome"
    assert not any("latest" in arg for arg in config.arguments)
    validate_integration_process(config)
    assert broker.authorize(call, ctx, request=req).decision is PolicyDecision.DENY
    data = json.loads(opened_result.output)["structuredContent"]
    assert (await act(service, req, data, "fill", target="e1", text="hola")).success
    assert len(peers) == 1


@pytest.mark.parametrize("existing, selected", [(True, 0), (True, 1), (False, 0)])
async def test_chrome_mode_bootstrap_binds_exact_url_without_manual_indices(harness,
                                                                          existing, selected):
    create, preferences, peers, _ = harness
    preferences[0] = BrowserAutomationPreferences(mode="chrome")
    def adjust(peer):
        if existing:
            peer.selected = selected
        else:
            peer.urls = []
            peer.empty_tabs_message = "No open tabs. Navigate to a URL to create one."
    service = create(adjust=adjust)
    req, result, data = await opened(service)
    assert result.success and data["expected_url"] == URL
    config = peers[0].configuration
    assert config.browser_session.mode == "cdp" and config.browser_session.cdp_endpoint == "chrome"
    assert config.browser_session.selected_tab_url == URL
    assert any(args.get("action") == "new" for _, args in peers[0].calls) is not existing
    urls = list(peers[0].urls)
    await service.finish_request(req.request_id)
    assert peers[0].urls == urls and peers[0].close_count == 1
    assert not any(name == "browser_close" or args.get("action") == "close"
                   for name, args in peers[0].calls)


async def test_chrome_preference_mode_change_revokes_current_access_before_effect(harness):
    create, preferences, peers, _ = harness
    preferences[0] = BrowserAutomationPreferences(mode="chrome")
    service = create()
    req, result, data = await opened(service)
    assert result.success
    preferences[0] = BrowserAutomationPreferences(mode="extension")
    changed = await act(service, req, data, "fill", target="e1", text="hola")
    assert changed.error_code == "browser_task_session_unavailable"
    assert peers[0].value == "" and peers[0].close_count == 1


def test_chrome_mode_cannot_choose_an_endpoint_through_tool_arguments():
    with pytest.raises(ValueError):
        BrowserTaskOpenArguments(url=URL, objective="Abre Chrome directo", mode="chrome",
                                 cdp_endpoint="http://10.0.0.1:9222")


@pytest.mark.parametrize("instruction, allowed", [
    ("en mi navegador", False), ("my browser", False),
    ("con la extensión", True), ("using the extension", True),
])
async def test_ambiguous_browser_does_not_override_configured_chrome_with_extension(harness,
                                                                                  instruction,
                                                                                  allowed):
    create, preferences, peers, _ = harness
    preferences[0] = BrowserAutomationPreferences(mode="chrome")
    service = create()
    req = request(f"Abre {URL} {instruction}")
    _, result, _ = await opened(service, req, mode="extension")
    assert result.success is allowed
    if allowed:
        assert peers[0].configuration.browser_session.mode == "extension"
    else:
        assert result.error_code == "browser_task_mode_not_requested" and not peers


async def test_chrome_connection_error_is_safe_and_does_not_fallback_or_replay(harness):
    create, preferences, peers, _ = harness
    preferences[0] = BrowserAutomationPreferences(mode="chrome")
    def adjust(peer):
        async def unavailable(name, arguments):
            peer.calls.append((name, dict(arguments)))
            return {"isError": True, "content": [{"type": "text", "text":
                "browserType.connectOverCDP: Could not connect to chrome: "
                "DevToolsActivePort file not found at PRIVATE_PROFILE_PATH"}]}
        peer.call_tool = unavailable
    service = create(adjust=adjust)
    req, result, _ = await opened(service)
    assert result.error_code == "browser_task_chrome_connection_failed"
    assert "PRIVATE_PROFILE_PATH" not in json.dumps(result.model_dump())
    assert len(peers) == 1 and peers[0].close_count == 1
    assert peers[0].configuration.browser_session.cdp_endpoint == "chrome"
    assert peers[0].calls == [("browser_tabs", {"action": "list"})]
    assert service.context_for_request(req.request_id) is None
    _, repeated, _ = await opened(service, req)
    assert repeated.error_code == "browser_task_origin_already_approved" and len(peers) == 1
