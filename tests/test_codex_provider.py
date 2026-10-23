"""Isolated Codex inference; protocol doubles never contact a model or owner data."""

import asyncio
import json
from types import SimpleNamespace

import pytest

from aegis_core.contracts import AgentRole
from aegis_core.providers.base import IncompleteModelResponseError
from aegis_core.providers.codex import _DISABLED_FEATURES, CodexChatProvider, _StdioTransport
from aegis_core.providers.errors import ProviderError

MODEL = "available-test-model"
MESSAGES = [{"role": "system", "content": "Responde en español."},
            {"role": "user", "content": "¿Cuánto es dos más dos?"}]
TOOLS = [{"type": "function", "function": {
    "name": "computer_use", "description": "Proponer una acción autorizable.",
    "parameters": {"type": "object", "properties": {"objective": {"type": "string"}}},
}}]


class RpcDouble:
    def __init__(self, state):
        self.state = state
        self.calls = []
        self.events = asyncio.Queue()
        self.closed = 0
        self.authenticated = True
        self.profile_allowed = True
        self.profile_active = "jarvis-inference"
        self.turn_status = "completed"
        self.reply = json.dumps({"content": "4", "tool_name": "", "arguments_json": "{}"})
        self.item_type = "agentMessage"
        self.modalities = ["text", "image"]
        self.efforts = ["low", "high"]
        self.wait_forever = False
        self.bad_home = False
        self.unsafe_configuration = None
        self.notification_handler = None

    def set_notification_handler(self, handler):
        self.notification_handler = handler

    def notification(self, method, params):
        if self.notification_handler is not None:
            self.notification_handler({"method": method, "params": params})

    async def start(self):
        pass

    async def notify(self, method, params):
        self.calls.append((method, params))

    async def request(self, method, params):
        self.calls.append((method, params))
        if method == "initialize":
            return {"codexHome": "other" if self.bad_home else str(self.state)}
        if method == "permissionProfile/list":
            return {"data": [{"id": "jarvis-inference", "allowed": self.profile_allowed}]}
        if method == "config/read":
            config = {
                "features": {name: False for name in _DISABLED_FEATURES},
                "default_permissions": "jarvis-inference", "web_search": "disabled",
                "model_provider": "openai",
                "chatgpt_base_url": "https://chatgpt.com/backend-api/",
                "analytics": {"enabled": False}, "check_for_update_on_startup": False,
                "otel": {"exporter": "none", "trace_exporter": "none",
                         "metrics_exporter": "none", "log_user_prompt": False},
                "permissions": {"jarvis-inference": {
                    "filesystem": {":minimal": "read", params["cwd"]: "read"},
                    "network": {"enabled": False}}},
            }
            if self.unsafe_configuration == "tools":
                config["features"]["shell_tool"] = True
            elif self.unsafe_configuration == "network":
                config["permissions"]["jarvis-inference"]["network"]["enabled"] = True
            elif self.unsafe_configuration == "filesystem":
                config["permissions"]["jarvis-inference"]["filesystem"][":root"] = "read"
            elif self.unsafe_configuration == "legacy":
                config["sandbox_mode"] = "danger-full-access"
            elif self.unsafe_configuration in {"chatgpt_base_url", "openai_base_url"}:
                config[self.unsafe_configuration] = "https://example.invalid"
            elif self.unsafe_configuration == "provider":
                config["model_provider"] = "custom"
            elif self.unsafe_configuration == "analytics":
                config["analytics"]["enabled"] = True
            elif self.unsafe_configuration == "updates":
                config["check_for_update_on_startup"] = True
            elif self.unsafe_configuration == "traces":
                config["otel"]["trace_exporter"] = {"otlp-http": {}}
            return {"config": config}
        if method == "account/read":
            return {"account": {"type": "chatgpt", "email": "DO NOT RETURN"}
                    if self.authenticated else None, "requiresOpenaiAuth": True}
        if method == "model/list":
            return {"data": [{"model": MODEL, "id": "catalog-entry", "displayName": "Prueba",
                              "isDefault": True, "hidden": False,
                              "inputModalities": self.modalities,
                              "defaultReasoningEffort": "high",
                              "supportedReasoningEfforts": [
                                  {"reasoningEffort": value} for value in self.efforts]}]}
        if method == "thread/start":
            return {"thread": {"id": "thread-test"}, "model": params["model"],
                    "modelProvider": "openai", "cwd": params["cwd"],
                    "activePermissionProfile": {"id": self.profile_active},
                    "sandbox": {"type": "readOnly", "networkAccess": False}}
        if method == "turn/start":
            if not self.wait_forever:
                await self.events.put({"method": "turn/completed", "params": {
                    "threadId": "thread-test", "turn": {"id": "turn-test",
                        "status": self.turn_status, "items": [{"type": self.item_type,
                            "id": "answer", "text": self.reply, "phase": "final_answer"}]}}})
            return {"turn": {"id": "turn-test", "status": "inProgress"}}
        if method == "account/login/start":
            return {"type": "chatgpt", "loginId": "private-login-id",
                    "authUrl": "https://auth.openai.com/oauth/authorize?state=synthetic"}
        return {}

    async def next_notification(self):
        return await self.events.get()

    async def aclose(self):
        self.closed += 1


@pytest.fixture
def client(tmp_path):
    executable = tmp_path / "codex"
    executable.write_text("synthetic")
    executable.chmod(0o700)
    state, working = tmp_path / "state", tmp_path / "empty"
    rpc = RpcDouble(state)
    provider = CodexChatProvider(executable, state, working, lambda: None,
                                 transport=rpc, timeout_seconds=2)
    return provider, rpc


async def test_status_uses_own_home_and_real_default_catalog_without_account_details(client):
    provider, rpc = client
    status = await provider.status()
    assert status == {"state": "ready", "installed": True, "model_id": MODEL,
                      "models": [{"id": MODEL, "display_name": "Prueba", "images": True,
                                  "is_default": True}]}
    assert "DO NOT RETURN" not in json.dumps(status)
    assert provider.completion_model_ids(AgentRole.VISION) == (MODEL,)
    assert provider._default_reasoning[MODEL] == "high"
    assert provider.provider_id == "codex" and provider.cooldown_remaining() == 0
    assert provider.fallback_status() is None
    initialize = next(params for method, params in rpc.calls if method == "initialize")
    assert initialize["capabilities"]["experimentalApi"] is True


async def test_retired_saved_model_preserves_catalogue_for_explicit_owner_recovery(client):
    provider, rpc = client
    selection = "retired-test-model"
    provider._model_id = lambda: selection
    status = await provider.status()
    assert status["state"] == "ready" and status["model_id"] == selection
    assert [item["id"] for item in status["models"]] == [MODEL]
    with pytest.raises(ProviderError, match="modelo elegido"):
        await provider.complete(role=AgentRole.SYNTHESIZER, messages=MESSAGES)
    assert not any(method == "turn/start" for method, _ in rpc.calls)
    assert selection == "retired-test-model"
    selection = MODEL  # Equivalent to the owner's explicit saved choice.
    assert (await provider.complete(role=AgentRole.SYNTHESIZER, messages=MESSAGES)).content == "4"


async def test_missing_cli_still_allows_runtime_context_and_configuration(tmp_path):
    provider = CodexChatProvider(tmp_path / "absent", tmp_path / "state", tmp_path / "empty",
                                 lambda: None)
    async with provider:
        assert (await provider.status())["state"] == "missing"
        with pytest.raises(ProviderError, match="instalado"):
            await provider.complete(role=AgentRole.SYNTHESIZER, messages=MESSAGES)


async def test_login_is_chatgpt_only_and_keeps_login_identifier_internal(client):
    provider, rpc = client
    rpc.authenticated = False
    assert (await provider.status())["state"] == "signed_out"
    assert await provider.login_start() == {
        "authUrl": "https://auth.openai.com/oauth/authorize?state=synthetic"}
    assert (await provider.status())["state"] == "login_pending"
    await provider.login_cancel()
    assert ("account/login/cancel", {"loginId": "private-login-id"}) in rpc.calls
    assert (await provider.status())["state"] == "signed_out"
    await provider.logout()
    assert ("account/logout", {}) in rpc.calls
    assert ("account/login/start", {"type": "chatgpt"}) in rpc.calls


@pytest.mark.parametrize("success", [False, True])
async def test_completed_login_event_clears_pending_state_even_without_inference(client, success):
    provider, rpc = client
    rpc.authenticated = False
    await provider.login_start()
    assert (await provider.status())["state"] == "login_pending"
    rpc.notification("account/login/completed", {
        "loginId": "other-login", "success": False, "error": "synthetic",
    })
    assert (await provider.status())["state"] == "login_pending"
    rpc.notification("account/login/completed", {
        "loginId": "private-login-id", "success": success,
        "error": None if success else "synthetic",
    })
    rpc.authenticated = success
    status = await provider.status()
    assert status["state"] == ("ready" if success else "signed_out")
    assert "synthetic" not in json.dumps(status)


async def test_login_completion_before_start_reply_cannot_leave_a_stale_pending_state(client):
    provider, rpc = client
    rpc.authenticated = False
    original = rpc.request

    async def request(method, params):
        response = await original(method, params)
        if method == "account/login/start":
            rpc.notification("account/login/completed", {
                "loginId": "private-login-id", "success": False, "error": "synthetic",
            })
        return response

    rpc.request = request
    await provider.login_start()
    assert (await provider.status())["state"] == "signed_out"


@pytest.mark.parametrize("url", ["https://evil.test", "http://auth.openai.com",
                                 "https://auth0.openai.com/",
                                 "https://auth.openai.com.evil.test/",
                                 "https://token@auth.openai.com/",
                                 "https://auth.openai.com/#unexpected",
                                 "https://auth.openai.com:444/"])
async def test_login_rejects_nonofficial_or_credential_bearing_urls(client, url):
    provider, rpc = client
    original = rpc.request

    async def request(method, params):
        response = await original(method, params)
        if method == "account/login/start":
            response["authUrl"] = url
        return response

    rpc.request = request
    with pytest.raises(ProviderError, match="dirección"):
        await provider.login_start()


@pytest.mark.parametrize("failure", ["home", "profile-unavailable", "profile-changed"])
async def test_inference_fails_closed_without_confirmed_isolation(client, failure):
    provider, rpc = client
    rpc.bad_home = failure == "home"
    rpc.profile_allowed = failure != "profile-unavailable"
    if failure == "profile-changed":
        rpc.profile_active = ":workspace"
    with pytest.raises(ProviderError):
        await provider.complete(role=AgentRole.SYNTHESIZER, messages=MESSAGES)
    assert not any(method == "turn/start" for method, _ in rpc.calls)
    assert rpc.closed >= 1


@pytest.mark.parametrize("configuration", [
    "tools", "network", "filesystem", "legacy", "chatgpt_base_url", "openai_base_url",
    "provider", "analytics", "updates", "traces",
])
async def test_effective_configuration_is_verified_before_account_or_inference(
    client, configuration,
):
    provider, rpc = client
    rpc.unsafe_configuration = configuration
    assert (await provider.status())["state"] == "unavailable"
    assert not any(method in {"account/read", "thread/start", "turn/start"}
                   for method, _ in rpc.calls)


async def test_complete_is_ephemeral_isolated_and_never_passes_provider_specific_flags(client):
    provider, rpc = client
    result = await provider.complete(role=AgentRole.SYNTHESIZER, messages=MESSAGES,
                                     max_tokens=256, temperature=0, perception_only=True,
                                     primary_timeout_seconds=1, timeout_seconds=2)
    assert result.content == "4" and result.finish_reason == "stop" and not result.tool_calls
    assert ("thread/unsubscribe", {"threadId": "thread-test"}) in rpc.calls
    thread = next(params for method, params in rpc.calls if method == "thread/start")
    turn = next(params for method, params in rpc.calls if method == "turn/start")
    assert thread["permissions"] == turn["permissions"] == "jarvis-inference"
    assert thread["approvalPolicy"] == "untrusted"
    assert thread["ephemeral"] and not thread["dynamicTools"]
    assert not thread["runtimeWorkspaceRoots"] and not thread["environments"]
    assert thread["baseInstructions"] == "Responde en español."
    assert thread["config"] == {"web_search": "disabled"}
    assert turn["effort"] == "low"
    assert "sandbox" not in thread and "sandboxPolicy" not in turn
    for flags in ("perception_only", "temperature", "max_tokens", "chat_template_kwargs"):
        assert flags not in thread and flags not in turn


async def test_tool_is_only_a_local_proposal_and_arguments_are_validated_by_jarvis(client):
    provider, rpc = client
    rpc.reply = json.dumps({"content": "", "tool_name": "computer_use",
                            "arguments_json": '{"objective":"PRUEBA JARVIS B"}'})
    result = await provider.complete(role=AgentRole.PLANNER, messages=MESSAGES,
                                     extra_body={"tools": TOOLS, "tool_choice": "required"})
    call, = result.tool_calls
    assert call.tool_name == "computer_use" and call.call_id.startswith("codex-")
    assert call.arguments == {"objective": "PRUEBA JARVIS B"}
    assert call.requested_by is AgentRole.PLANNER
    turn = next(params for method, params in rpc.calls if method == "turn/start")
    assert turn["outputSchema"]["properties"]["tool_name"]["enum"] == ["computer_use"]
    assert not any(method.startswith("item/") for method, _ in rpc.calls)


async def test_named_tool_schema_offers_only_the_explicitly_selected_proposal(client):
    provider, rpc = client
    rpc.reply = json.dumps({"content": "", "tool_name": "computer_use",
                            "arguments_json": '{"objective":"PRUEBA JARVIS B"}'})
    extra_tool = {"type": "function", "function": {
        "name": "synthetic_status", "description": "Propuesta ficticia, sin ejecución.",
        "parameters": {"type": "object", "properties": {}},
    }}
    result = await provider.complete(role=AgentRole.PLANNER, messages=MESSAGES, extra_body={
        "tools": [*TOOLS, extra_tool],
        "tool_choice": {"type": "function", "function": {"name": "computer_use"}},
    })
    assert [call.tool_name for call in result.tool_calls] == ["computer_use"]
    turn = next(params for method, params in rpc.calls if method == "turn/start")
    assert turn["outputSchema"]["properties"]["tool_name"]["enum"] == ["computer_use"]


@pytest.mark.parametrize("extra", [
    {"tool_choice": "required"}, {"tools": [], "tool_choice": "required"},
])
async def test_required_tool_without_any_functions_is_rejected_before_rpc(client, extra):
    provider, rpc = client
    with pytest.raises(ProviderError, match="herramienta disponible"):
        await provider.complete(role=AgentRole.PLANNER, messages=MESSAGES, extra_body=extra)
    assert rpc.calls == []


@pytest.mark.parametrize("choice", [
    "required", {"type": "function", "function": {"name": "computer_use"}},
])
async def test_required_tool_parser_still_rejects_empty_choice_if_decoder_contract_is_broken(
    client, choice,
):
    provider, rpc = client
    # The protocol double intentionally violates outputSchema with a text reply.
    with pytest.raises(IncompleteModelResponseError):
        await provider.complete(role=AgentRole.PLANNER, messages=MESSAGES,
                                extra_body={"tools": TOOLS, "tool_choice": choice})
    assert rpc.closed >= 1


@pytest.mark.parametrize("extra, expected", [
    ({}, [""]),
    ({"tools": []}, [""]),
    ({"tools": TOOLS, "tool_choice": "auto"}, ["", "computer_use"]),
    ({"tools": TOOLS, "tool_choice": "none"}, [""]),
])
async def test_optional_tool_contract_keeps_text_response_available(client, extra, expected):
    provider, rpc = client
    result = await provider.complete(role=AgentRole.PLANNER, messages=MESSAGES, extra_body=extra)
    assert result.content == "4" and result.tool_calls == ()
    turn = next(params for method, params in rpc.calls if method == "turn/start")
    assert turn["outputSchema"]["properties"]["tool_name"]["enum"] == expected


@pytest.mark.parametrize("reply", [
    '{"content":"4","content":"5","tool_name":"","arguments_json":"{}"}',
    '{"content":"4","tool_name":"","arguments_json":"{}","extra":true}',
    '{"content":"","tool_name":"terminal_run","arguments_json":"{}"}',
    '{"content":"","tool_name":"computer_use","arguments_json":"{\\"x\\":NaN}"}',
    '{"content":"","tool_name":"computer_use","arguments_json":"{\\"x\\":1e999}"}',
    '{"content":"","tool_name":"computer_use","arguments_json":"{\\"x\\":1,\\"x\\":2}"}',
    '{"content":"","tool_name":"computer_use","arguments_json":"[]"}',
    '{"content":"","tool_name":"","arguments_json":"{}"}',
])
async def test_malformed_or_unoffered_proposals_never_become_tool_calls(client, reply):
    provider, rpc = client
    rpc.reply = reply
    with pytest.raises(IncompleteModelResponseError):
        await provider.complete(role=AgentRole.PLANNER, messages=MESSAGES,
                                extra_body={"tools": TOOLS})
    assert rpc.closed >= 1


@pytest.mark.parametrize("status", ["failed", "interrupted", "inProgress"])
async def test_failed_or_interrupted_turn_never_emits_partial_text_or_tools(client, status):
    provider, rpc = client
    rpc.turn_status = status
    deltas = []
    with pytest.raises(IncompleteModelResponseError):
        await provider.complete_stream(role=AgentRole.SYNTHESIZER, messages=MESSAGES,
                                       on_delta=deltas.append)
    assert deltas == []


@pytest.mark.parametrize("item", ["commandExecution", "fileChange", "mcpToolCall",
                                  "dynamicToolCall", "webSearch", "imageView",
                                  "collabAgentToolCall", "imageGeneration"])
async def test_native_codex_capabilities_in_events_abort_inference(client, item):
    provider, rpc = client
    rpc.item_type = item
    with pytest.raises(ProviderError, match="capacidad"):
        await provider.complete(role=AgentRole.SYNTHESIZER, messages=MESSAGES)
    assert rpc.closed >= 1


async def test_vision_uses_task_image_and_complete_json_content(client):
    provider, rpc = client
    rpc.reply = json.dumps({"content": '{"action":"type","text":"PRUEBA JARVIS B"}',
                            "tool_name": "", "arguments_json": "{}"})
    image = "data:image/png;base64,c3ludGhldGlj"
    result = await provider.complete(role=AgentRole.VISION, messages=[{
        "role": "user", "content": [{"type": "text", "text": "Escribe el literal de prueba."},
                                    {"type": "image_url", "image_url": {"url": image}}]}],
        extra_body={"response_format": {"type": "json_object"}}, perception_only=True)
    assert json.loads(result.content)["action"] == "type"
    turn = next(params for method, params in rpc.calls if method == "turn/start")
    assert turn["input"][1] == {"type": "image", "url": image}
    assert image not in turn["input"][0]["text"]


async def test_stream_publishes_only_validated_answer_and_review_reuses_same_transport(client):
    provider, rpc = client
    rpc.efforts = ["high"]
    deltas = []
    result = await provider.complete_stream(role=AgentRole.SYNTHESIZER, messages=MESSAGES,
                                            max_tokens=64, on_delta=deltas.append)
    assert deltas == ["4"] and result.content == "4"
    turn = next(params for method, params in rpc.calls if method == "turn/start")
    assert "effort" not in turn
    reviewed = await provider.complete_dialogue_review(draft_model_id=MODEL, messages=MESSAGES,
                                                       max_tokens=64, extended_reasoning=True)
    assert reviewed.role is AgentRole.SYNTHESIZER and reviewed.content == "4"


async def test_timeout_closes_child_without_publishing_an_answer(client):
    provider, rpc = client
    rpc.wait_forever = True
    with pytest.raises(ProviderError, match="tiempo"):
        await provider.complete(role=AgentRole.SYNTHESIZER, messages=MESSAGES,
                                timeout_seconds=0.02)
    assert rpc.closed >= 1


async def test_cancellation_closes_child_without_publishing_an_answer(client):
    provider, rpc = client
    rpc.wait_forever = True
    deltas = []
    task = asyncio.create_task(provider.complete_stream(
        role=AgentRole.SYNTHESIZER, messages=MESSAGES, on_delta=deltas.append,
    ))
    for _ in range(100):
        if any(method == "turn/start" for method, _ in rpc.calls):
            break
        await asyncio.sleep(0)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert rpc.closed >= 1 and deltas == []


class PausingCloseRpc(RpcDouble):
    def __init__(self, state):
        super().__init__(state)
        self.close_started = asyncio.Event()
        self.allow_close = asyncio.Event()
        self.turn_started = asyncio.Event()
        self.closing = False
        self.calls_during_close = []

    async def request(self, method, params):
        if self.closing:
            self.calls_during_close.append(method)
            raise ProviderError("synthetic transport is closing")
        if method == "turn/start":
            self.turn_started.set()
        return await super().request(method, params)

    async def aclose(self):
        self.closing = True
        self.close_started.set()
        await self.allow_close.wait()
        self.closed += 1
        self.closing = False


def provider_with_pausing_close(tmp_path):
    executable = tmp_path / "codex"
    executable.write_text("synthetic")
    executable.chmod(0o700)
    rpc = PausingCloseRpc(tmp_path / "state")
    provider = CodexChatProvider(executable, tmp_path / "state", tmp_path / "empty",
                                 lambda: None, timeout_seconds=2, transport=rpc)
    return provider, rpc


@pytest.mark.parametrize("interruption", ["cancel", "cancel_twice", "timeout"])
async def test_cancelled_turn_cleanup_finishes_before_queued_turn_uses_transport(
    tmp_path, interruption,
):
    provider, rpc = provider_with_pausing_close(tmp_path)
    rpc.wait_forever = True
    first = asyncio.create_task(provider.complete(
        role=AgentRole.SYNTHESIZER, messages=MESSAGES,
        timeout_seconds=0.02 if interruption == "timeout" else 2,
    ))
    await asyncio.wait_for(rpc.turn_started.wait(), timeout=1)
    second = asyncio.create_task(provider.complete(role=AgentRole.PLANNER, messages=MESSAGES))
    await asyncio.sleep(0)  # The new call is queued behind the first inference.
    if interruption != "timeout":
        first.cancel()
    await asyncio.wait_for(rpc.close_started.wait(), timeout=1)
    if interruption == "cancel_twice":
        first.cancel()
    rpc.wait_forever = False
    for _ in range(10):
        await asyncio.sleep(0)
    calls_during_close = list(rpc.calls_during_close)
    rpc.allow_close.set()
    outcomes = await asyncio.wait_for(
        asyncio.gather(first, second, return_exceptions=True), timeout=2,
    )
    if interruption == "timeout":
        assert isinstance(outcomes[0], ProviderError) and "tiempo" in str(outcomes[0])
    else:
        assert isinstance(outcomes[0], asyncio.CancelledError)
    assert calls_during_close == []
    assert outcomes[1].content == "4"
    assert rpc.closed == 1


async def test_status_waits_for_public_close_then_reinitializes_the_transport(tmp_path):
    provider, rpc = provider_with_pausing_close(tmp_path)
    assert (await provider.status())["state"] == "ready"
    closing = asyncio.create_task(provider.aclose())
    await asyncio.wait_for(rpc.close_started.wait(), timeout=1)
    status = asyncio.create_task(provider.status())
    for _ in range(10):
        await asyncio.sleep(0)
    calls_during_close = list(rpc.calls_during_close)
    rpc.allow_close.set()
    await asyncio.wait_for(closing, timeout=1)
    result = await asyncio.wait_for(status, timeout=1)
    assert calls_during_close == []
    assert result["state"] == "ready" and provider._started


async def test_repeated_cancellation_waits_for_cleanup_admission_behind_status(tmp_path):
    provider, rpc = provider_with_pausing_close(tmp_path)
    rpc.wait_forever = True
    inference = asyncio.create_task(provider.complete(
        role=AgentRole.SYNTHESIZER, messages=MESSAGES,
    ))
    await asyncio.wait_for(rpc.turn_started.wait(), timeout=1)
    account_started, allow_account = asyncio.Event(), asyncio.Event()
    request = rpc.request

    async def held_request(method, params):
        if method == "account/read":
            account_started.set()
            await allow_account.wait()
        return await request(method, params)

    rpc.request = held_request
    status = asyncio.create_task(provider.status())
    await asyncio.wait_for(account_started.wait(), timeout=1)
    inference.cancel()
    for _ in range(10):
        await asyncio.sleep(0)
    assert not rpc.close_started.is_set()
    inference.cancel()
    for _ in range(10):
        await asyncio.sleep(0)
    cleanup_finished_early = inference.done()
    allow_account.set()
    rpc.allow_close.set()
    outcomes = await asyncio.wait_for(
        asyncio.gather(inference, status, return_exceptions=True), timeout=2,
    )
    assert isinstance(outcomes[0], asyncio.CancelledError)
    assert not cleanup_finished_early
    assert rpc.closed == 1 and rpc.calls_during_close == []
    assert outcomes[1]["state"] == "ready"


@pytest.mark.parametrize("interruption", ["cancel", "timeout"])
async def test_interrupted_queued_turn_does_not_close_another_turn(tmp_path, interruption):
    provider, rpc = provider_with_pausing_close(tmp_path)
    rpc.wait_forever = True
    first = asyncio.create_task(provider.complete(role=AgentRole.SYNTHESIZER, messages=MESSAGES))
    await asyncio.wait_for(rpc.turn_started.wait(), timeout=1)
    waiter = asyncio.create_task(provider.complete(
        role=AgentRole.PLANNER, messages=MESSAGES,
        timeout_seconds=0.02 if interruption == "timeout" else 2,
    ))
    await asyncio.sleep(0)
    if interruption == "cancel":
        waiter.cancel()
        with pytest.raises(asyncio.CancelledError):
            await waiter
    else:
        with pytest.raises(ProviderError, match="tiempo"):
            await asyncio.wait_for(waiter, timeout=1)
    assert not rpc.close_started.is_set() and not first.done()
    assert sum(method == "turn/start" for method, _ in rpc.calls) == 1
    first.cancel()
    await asyncio.wait_for(rpc.close_started.wait(), timeout=1)
    rpc.allow_close.set()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(first, timeout=1)


async def test_public_close_waits_for_active_inference_without_interrupting_it(tmp_path):
    provider, rpc = provider_with_pausing_close(tmp_path)
    rpc.wait_forever = True
    inference = asyncio.create_task(provider.complete(
        role=AgentRole.SYNTHESIZER, messages=MESSAGES,
    ))
    await asyncio.wait_for(rpc.turn_started.wait(), timeout=1)
    closing = asyncio.create_task(provider.aclose())
    for _ in range(10):
        await asyncio.sleep(0)
    assert not rpc.close_started.is_set() and not closing.done()
    await rpc.events.put({"method": "turn/completed", "params": {
        "threadId": "thread-test", "turn": {"id": "turn-test", "status": "completed",
            "items": [{"type": "agentMessage", "id": "answer", "text": rpc.reply}]}}})
    assert (await asyncio.wait_for(inference, timeout=1)).content == "4"
    await asyncio.wait_for(rpc.close_started.wait(), timeout=1)
    rpc.allow_close.set()
    await asyncio.wait_for(closing, timeout=1)
    assert rpc.calls_during_close == [] and not provider._started


async def test_local_launch_uses_only_dedicated_home_and_disabled_capabilities(
    tmp_path, monkeypatch,
):
    invocations = []

    class Version:
        returncode = 0

        async def communicate(self):
            return b"codex-cli 0.163.0\n", b""

    class Process:
        returncode = None
        stdout = asyncio.StreamReader()

        def terminate(self):
            self.returncode = 0
            self.stdout.feed_eof()

        async def wait(self):
            return self.returncode

    async def spawn(*args, **kwargs):
        invocations.append((args, kwargs))
        return Version() if args[-1] == "--version" else Process()

    monkeypatch.setattr(asyncio, "create_subprocess_exec", spawn)
    monkeypatch.setenv("OPENAI_API_KEY", "must-not-inherit")
    monkeypatch.setenv("CODEX_HOME", "must-not-read")
    monkeypatch.setenv("NVIDIA_API_KEY", "must-not-inherit")
    state, working = tmp_path / "private-state", tmp_path / "empty"
    transport = _StdioTransport(tmp_path / "codex", state, working)
    await transport.start()
    args, options = invocations[1]
    assert args[1:3] == ("app-server", "--stdio") and "--strict-config" in args
    disabled = {args[index + 1] for index, item in enumerate(args) if item == "--disable"}
    assert {"shell_tool", "unified_exec", "apps", "plugins", "browser_use", "computer_use",
            "view_image", "multi_agent", "code_mode_host", "image_generation"} <= disabled
    overrides = {args[index + 1] for index, item in enumerate(args) if item == "-c"}
    assert 'default_permissions="jarvis-inference"' in overrides
    assert 'permissions.jarvis-inference.network.enabled=false' in overrides
    assert 'web_search="disabled"' in overrides
    assert 'cli_auth_credentials_store="file"' in overrides
    assert 'analytics.enabled=false' in overrides
    assert 'check_for_update_on_startup=false' in overrides
    assert 'otel.exporter="none"' in overrides
    assert 'otel.trace_exporter="none"' in overrides
    assert 'otel.metrics_exporter="none"' in overrides
    assert not any(value.startswith("sandbox") for value in overrides)
    environment = options["env"]
    assert environment["CODEX_HOME"] == environment["HOME"] == str(state)
    assert "OPENAI_API_KEY" not in environment and "NVIDIA_API_KEY" not in environment
    assert options["cwd"] == working
    await transport.aclose()


@pytest.mark.parametrize("method", ["item/commandExecution/requestApproval",
                                    "item/fileChange/requestApproval",
                                    "item/permissions/requestApproval", "item/tool/call",
                                    "account/chatgptAuthTokens/refresh", "unknown/request"])
async def test_stdio_denies_every_server_request_and_terminates_child(tmp_path, method):
    transport = _StdioTransport(tmp_path / "codex", tmp_path / "state", tmp_path / "empty")

    class Input:
        data = bytearray()

        def write(self, data):
            self.data.extend(data)

        async def drain(self):
            pass

    class Process:
        returncode = None
        stdin = Input()
        stdout = asyncio.StreamReader()
        terminated = False

        def terminate(self):
            self.terminated = True

    process = Process()
    transport.process = process
    process.stdout.feed_data(json.dumps({"id": 17, "method": method, "params": {}}).encode()+b"\n")
    process.stdout.feed_eof()
    await transport._read()
    reply = json.loads(process.stdin.data)
    assert reply["id"] == 17 and reply["error"]["code"] == -32601
    assert "result" not in reply and transport.failed and process.terminated


async def test_stdio_routes_login_completion_to_state_handler_outside_turn_queue(tmp_path):
    transport = _StdioTransport(tmp_path / "codex", tmp_path / "state", tmp_path / "empty")
    events = []
    transport.set_notification_handler(events.append)
    output = asyncio.StreamReader()
    transport.process = SimpleNamespace(stdout=output, returncode=0)
    event = {"method": "account/login/completed", "params": {
        "loginId": "own-test-login", "success": False, "error": "synthetic",
    }}
    output.feed_data(json.dumps(event).encode() + b"\n")
    output.feed_eof()
    await transport._read()
    assert events == [event]
    # EOF reports the transport failure, but account data is never retained as
    # pending inference output in the notification queue.
    assert transport.events.get_nowait()["method"] == "jarvis/transportFailed"
    assert transport.events.empty()
