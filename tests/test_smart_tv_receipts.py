from __future__ import annotations

import json
from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import aiohttp
import pytest

from aegis_core.brain.agent_graph import PlanExecuteReflectRunner
from aegis_core.brain.behavior_tree import TacticalDeviceBehaviorTree
from aegis_core.brain.nodes.reflector import VisualReflectionNode
from aegis_core.contracts import PolicyDecision, ToolAuthorization
from aegis_core.job_execution import JobExecutionPipeline
from aegis_core.job_tool_presenter import JobToolPresenter
from aegis_core.tools.defaults import default_policy_context
from aegis_core.tools.native_receipts import SMART_TV_RECEIPT_SCOPE, SMART_TV_RECEIPT_STATES
from aegis_core.tools.smart_tv import (
    SmartTVController,
    SmartTVProfile,
    SmartTVToolService,
    TargetDeviceOffline,
    TVPlatform,
)
from aegis_core.tools.verification import (
    result_goal_is_verified,
    result_is_verified,
    result_matches_authorization,
)


def authorization() -> ToolAuthorization:
    return ToolAuthorization(
        call_id="synthetic-tv-command", tool_name="smart_tv_control", call_digest="a" * 64,
        decision=PolicyDecision.ALLOW, reason_code="confirmation_consumed",
        normalized_arguments={"device_id": "synthetic-tv", "action": "volume_up"},
    )


class SyntheticWebSocket:
    def __init__(self, payload, *, platform=TVPlatform.WEBOS, failure=None):
        self.payload, self.platform, self.failure = payload, platform, failure
        self.sent = []

    async def send_json(self, value):
        self.sent.append(value)
        if self.platform is TVPlatform.TIZEN and self.failure == "after_dispatch":
            raise TimeoutError("synthetic private transport error")

    async def receive(self):
        if self.platform is TVPlatform.TIZEN:
            value = {"event": "ms.channel.connect", "data": {}}
        else:
            if self.failure == "after_dispatch":
                raise TimeoutError("synthetic private transport error")
            value = {"id": self.sent[-1]["id"], "type": "response", "payload": self.payload}
        return SimpleNamespace(type=aiohttp.WSMsgType.TEXT, data=json.dumps(value))


def synthetic_controller(monkeypatch, payload, *, platform=TVPlatform.WEBOS, failure=None):
    # These literal profiles never reach a socket: _client is replaced below, and
    # registration/credentials are synthetic to avoid Keychain or pairing prompts.
    profile = SmartTVProfile(
        device_id="synthetic-tv", platform=platform, host="192.168.50.10",
        certificate_sha256="a" * 64,
    )
    controller = SmartTVController((profile,))
    websocket = SyntheticWebSocket(payload, platform=platform, failure=failure)

    @asynccontextmanager
    async def connection():
        if failure == "before_dispatch":
            raise TimeoutError("synthetic private transport error")
        yield websocket

    client = SimpleNamespace(ws_connect=lambda *args, **kwargs: connection())
    monkeypatch.setattr(controller, "_client", AsyncMock(return_value=client))
    monkeypatch.setattr(controller, "_webos_register", AsyncMock())
    monkeypatch.setattr(controller, "_credential_value", AsyncMock(return_value="synthetic-token"))
    monkeypatch.setattr(controller, "_store_credential", AsyncMock())
    return controller, websocket


@pytest.mark.parametrize("payload", [
    {}, {"returnValue": False}, {"returnValue": "false"}, {"returnValue": "true"},
    {"returnValue": 0}, {"returnValue": 1}, {"returnValue": None},
    {"returnValue": []}, {"returnValue": {}},
])
async def test_webos_requires_an_explicit_true_acknowledgement(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, payload,
) -> None:
    controller, websocket = synthetic_controller(monkeypatch, payload)
    audit = MagicMock()
    result = await SmartTVToolService(controller, audit_sink=audit).control(
        authorization(), default_policy_context(tmp_path),
    )
    assert len(websocket.sent) == 1  # The peer may already have applied the command.
    assert not result.success and result.error_code == "device_command_rejected"
    assert result.output == "" and not result_is_verified(result)
    audit.record_system_event.assert_not_called()


async def test_webos_boolean_true_is_only_a_command_acknowledgement(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    controller, websocket = synthetic_controller(monkeypatch, {"returnValue": True})
    result = await SmartTVToolService(controller).control(
        authorization(), default_policy_context(tmp_path),
    )
    assert len(websocket.sent) == 1 and result_is_verified(result)
    assert json.loads(result.output)["state"] == "true"
    assert not result_goal_is_verified(result)


@pytest.mark.parametrize("malformed", ["duplicate_return_value", "registration_as_response"])
async def test_malformed_webos_acknowledgement_halts_without_dispatching_again(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, malformed: str,
) -> None:
    controller, websocket = synthetic_controller(monkeypatch, {})

    async def receive():
        identifier = json.dumps(websocket.sent[-1]["id"])
        # Raw JSON is intentional: dict/json.dumps would erase duplicate keys in
        # the fixture before the production decoder sees the conflicting values.
        raw = ('{"id":' + identifier + ',"type":"response",'
               '"payload":{"returnValue":false,"returnValue":true}}'
               if malformed == "duplicate_return_value" else
               '{"id":' + identifier + ',"type":"registered",'
               '"payload":{"returnValue":true}}')
        return SimpleNamespace(type=aiohttp.WSMsgType.TEXT, data=raw)

    monkeypatch.setattr(websocket, "receive", receive)
    audit = MagicMock()
    service = SmartTVToolService(controller, audit_sink=audit)
    results = []

    async def execute(auth):
        result = await service.control(auth, default_policy_context(tmp_path))
        results.append(result)
        return result

    wake, verify = AsyncMock(return_value=(True, 1)), AsyncMock(return_value=(True, 1))
    plan = await PlanExecuteReflectRunner(
        execute, device_waker=wake, device_verifier=verify,
    ).run(goal="Orden sintética", authorizations=(authorization(),))
    assert plan.halted and len(results) == len(websocket.sent) == 1
    assert not results[0].success and results[0].error_code == "device_command_rejected"
    assert results[0].output == "" and not result_is_verified(results[0])
    wake.assert_not_awaited()
    verify.assert_not_awaited()
    audit.record_system_event.assert_not_called()


@pytest.mark.parametrize("state", [False, True, None, "false", "", "unknown", [], {},
                                    "private-rejected-payload"])
async def test_service_drops_unknown_or_negative_controller_receipts(tmp_path: Path, state) -> None:
    controller = SimpleNamespace(execute=AsyncMock(return_value=(state, 2)))
    audit = MagicMock()
    result = await SmartTVToolService(controller, audit_sink=audit).control(
        authorization(), default_policy_context(tmp_path),
    )
    assert not result.success and result.error_code == "device_command_rejected"
    assert result.output == "" and not result_is_verified(result)
    assert "private-rejected-payload" not in result.model_dump_json()
    controller.execute.assert_awaited_once()
    audit.record_system_event.assert_not_called()


@pytest.mark.parametrize("state", sorted(SMART_TV_RECEIPT_STATES))
async def test_four_positive_receipts_align_authorization_presentation_and_reflection(
    tmp_path: Path, state: str,
) -> None:
    controller = SimpleNamespace(execute=AsyncMock(return_value=(state, 2)))
    auth = authorization()
    result = await SmartTVToolService(controller).control(auth, default_policy_context(tmp_path))
    assert result.success and result_is_verified(result)
    assert result.metadata["verification_scope"] == SMART_TV_RECEIPT_SCOPE
    assert result.metadata["goal_verified"] is False and not result_goal_is_verified(result)
    assert result_matches_authorization(auth, result)
    assert JobToolPresenter.format_result(result, auth) == (
        "Envié la orden al televisor. No he comprobado su estado final.")
    assert (await VisualReflectionNode().evaluate(auth, result)).reason == "command_acknowledged"
    conversations = AsyncMock()
    conversations.recovery.return_value = None
    pipeline = JobExecutionPipeline(
        AsyncMock(), execution_timeout_seconds=1, conversations=conversations,
    )
    await pipeline.record_recovery(
        uuid4(), request_id=uuid4(), request_text="Orden sintética", tool_name=auth.tool_name,
        call_digest=auth.call_digest, status="observed", result=result,
    )
    observation = conversations.record_recovery.call_args.args[1].observation
    assert json.loads(observation.excerpt) == {
        "verification_scope": SMART_TV_RECEIPT_SCOPE, "goal_verified": False,
        "receipt": {"device_id": "synthetic-tv", "state": state},
    }
    other = auth.model_copy(update={"normalized_arguments": {
        "device_id": "other-tv", "action": "volume_up",
    }})
    assert not result_matches_authorization(other, result)
    with pytest.raises(ValueError, match="authorization"):
        JobToolPresenter.format_result(result, other)
    controller.execute.assert_awaited_once()


@pytest.mark.parametrize("change", [
    {"decision": PolicyDecision.DENY}, {"reason_code": "policy_allowed"},
    {"tool_name": "smart_tv_discover"},
])
async def test_control_rejects_unrelated_or_unapproved_authorizations(
    tmp_path: Path, change,
) -> None:
    controller = SimpleNamespace(execute=AsyncMock(return_value=("true", 1)))
    result = await SmartTVToolService(controller).control(
        authorization().model_copy(update=change), default_policy_context(tmp_path),
    )
    assert not result.success and result.error_code == "access_denied"
    controller.execute.assert_not_awaited()


@pytest.mark.parametrize("platform", [TVPlatform.WEBOS, TVPlatform.TIZEN])
@pytest.mark.parametrize("failure", ["before_dispatch", "after_dispatch"])
async def test_transport_marks_no_dispatch_only_before_sending_the_order(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, platform, failure,
) -> None:
    controller, websocket = synthetic_controller(
        monkeypatch, {}, platform=platform, failure=failure,
    )
    result = await SmartTVToolService(controller).control(
        authorization(), default_policy_context(tmp_path),
    )
    assert result.error_code == "target_device_offline" and not result_is_verified(result)
    assert len(websocket.sent) == (0 if failure == "before_dispatch" else 1)
    assert (result.metadata.get("dispatch_status") == "not_dispatched") is (
        failure == "before_dispatch")
    assert "private transport error" not in result.model_dump_json()


@pytest.mark.parametrize("failure", [
    "invalid_receipt", "offline_unknown", "offline_after_dispatch", "offline_before_dispatch",
])
async def test_recovery_never_repeats_a_command_without_proof_it_was_not_dispatched(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failure: str,
) -> None:
    failures = {
        "invalid_receipt": ("false", 2),
        "offline_unknown": TargetDeviceOffline("synthetic"),
        "offline_after_dispatch": TargetDeviceOffline("synthetic", command_dispatched=True),
        "offline_before_dispatch": TargetDeviceOffline("synthetic", command_dispatched=False),
    }
    controller = SimpleNamespace(execute=AsyncMock(side_effect=[failures[failure], ("true", 2)]))
    service = SmartTVToolService(controller)

    async def execute(auth):
        return await service.control(auth, default_policy_context(tmp_path))

    wake, verify = AsyncMock(return_value=(True, 1)), AsyncMock(return_value=(True, 1))
    monkeypatch.setattr(TacticalDeviceBehaviorTree, "RECOVERY_DELAY_SECONDS", 0)
    plan = await PlanExecuteReflectRunner(
        execute, device_waker=wake, device_verifier=verify,
    ).run(goal="Orden sintética", authorizations=(authorization(),))
    if failure == "offline_before_dispatch":
        assert not plan.halted and controller.execute.await_count == 2
        wake.assert_awaited_once()
        verify.assert_awaited_once()
    else:
        assert plan.halted and controller.execute.await_count == 1
        wake.assert_not_awaited()
        verify.assert_not_awaited()


@pytest.mark.parametrize("payload", [
    {}, {"device_id": "synthetic-tv", "state": "false"},
    {"device_id": "synthetic-tv", "state": True},
])
async def test_fabricated_tv_success_never_becomes_verified(tmp_path: Path, payload) -> None:
    controller = SimpleNamespace(execute=AsyncMock(return_value=("true", 2)))
    result = await SmartTVToolService(controller).control(
        authorization(), default_policy_context(tmp_path),
    )
    forged = result.model_copy(update={"output": json.dumps(payload)})
    assert not result_is_verified(forged) and not result_goal_is_verified(forged)
    assert not (await VisualReflectionNode().evaluate(authorization(), forged)).visual_verified
    for metadata in ({}, {**result.metadata, "goal_verified": True},
                     {**result.metadata, "device_state": "other"}):
        assert not result_is_verified(result.model_copy(update={"metadata": metadata}))
