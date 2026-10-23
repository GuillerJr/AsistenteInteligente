"""Closed vision decisions through the NVIDIA wire contract and synthetic AX only."""

import json

import httpx
import pytest
from test_computer_textedit_request import (
    BUNDLE,
    CONDITION,
    REQUEST,
    VALUE,
    SyntheticTextEditBridge,
)

from aegis_core.config import Settings
from aegis_core.providers.nvidia import NvidiaNimClient
from aegis_core.tools.computer import (
    ComputerAction,
    ComputerUseController,
    ComputerVisionNotDispatchedError,
)


async def run_controller(tmp_path, handler, bridge):
    settings = Settings(model_preferences_path=tmp_path / "model-preferences.json")
    async with NvidiaNimClient(
        settings, lambda: "synthetic", transport=httpx.MockTransport(handler),
    ) as provider:
        return await ComputerUseController(
            provider, bridge, settle_seconds=0, timeout_seconds=2,
        ).run(objective=REQUEST, application_bundle_identifier=BUNDLE,
              max_steps=3, postconditions=(CONDITION,))


async def test_closed_vision_reserves_its_small_budget_for_a_complete_grounded_action(tmp_path):
    requests = []

    def handler(request):
        payload = json.loads(request.content)
        requests.append(payload)
        direct = payload.get("chat_template_kwargs") == {"enable_thinking": False}
        # Omni documents that thinking can exhaust a small budget without a final
        # answer. Exercise that wire failure if the controller loses its closed mode.
        return httpx.Response(200, json={"choices": [{
            "message": {"content": json.dumps({"action": "type", "text": VALUE})
                        if direct else "", "reasoning_content": "" if direct else "synthetic"},
            "finish_reason": "stop" if direct else "length",
        }]})

    bridge = SyntheticTextEditBridge()
    report = await run_controller(tmp_path, handler, bridge)

    assert report.status == "completed" and report.postcondition_checks[0].satisfied
    assert bridge.actions == [ComputerAction(action="type", text=VALUE)]
    assert bridge.value == VALUE and len(requests) == 1
    assert requests[0]["max_tokens"] == 256
    assert requests[0]["response_format"] == {"type": "json_object"}
    assert "perception_only" not in requests[0]
    context = json.loads(requests[0]["messages"][1]["content"][0]["text"])
    assert context["objective"] == REQUEST
    assert context["postconditions"] == [CONDITION.model_dump(exclude_none=True)]


@pytest.mark.parametrize("finish", ["length", "content_filter"])
async def test_closed_vision_still_rejects_incomplete_actions_without_dispatch_or_retry(
    tmp_path, finish,
):
    calls = []

    def handler(request):
        calls.append(json.loads(request.content))
        # Even parseable JSON cannot become a gesture after an incomplete finish.
        return httpx.Response(200, json={"choices": [{
            "message": {"content": json.dumps({"action": "type", "text": VALUE})},
            "finish_reason": finish,
        }]})

    bridge = SyntheticTextEditBridge()
    with pytest.raises(ComputerVisionNotDispatchedError):
        await run_controller(tmp_path, handler, bridge)
    assert len(calls) == 1 and bridge.actions == [] and bridge.value == ""


async def test_vision_remote_fallback_does_not_inherit_omni_specific_options(tmp_path):
    calls = []

    def handler(request):
        payload = json.loads(request.content)
        calls.append(payload)
        if len(calls) == 1:
            return httpx.Response(404)
        return httpx.Response(200, json={"choices": [{
            "message": {"content": json.dumps({"action": "type", "text": VALUE})},
            "finish_reason": "stop",
        }]})

    bridge = SyntheticTextEditBridge()
    report = await run_controller(tmp_path, handler, bridge)
    assert report.status == "completed" and bridge.value == VALUE
    assert len(calls) == 2 and calls[0]["model"] != calls[1]["model"]
    assert calls[0]["chat_template_kwargs"] == {"enable_thinking": False}
    assert "chat_template_kwargs" not in calls[1]
