"""Closed vision decisions through the NVIDIA wire contract and synthetic AX only."""

import json

import httpx
import pytest
from test_computer_textedit_request import (
    APPEND_CONDITION,
    APPEND_REQUEST,
    APPEND_VALUE,
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


async def run_controller(tmp_path, handler, bridge, *, objective=REQUEST, condition=CONDITION):
    settings = Settings(model_preferences_path=tmp_path / "model-preferences.json")
    async with NvidiaNimClient(
        settings, lambda: "synthetic", transport=httpx.MockTransport(handler),
    ) as provider:
        return await ComputerUseController(
            provider, bridge, settle_seconds=0, timeout_seconds=2,
        ).run(objective=objective, application_bundle_identifier=BUNDLE,
              max_steps=3, postconditions=(condition,))


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


async def test_vision_fallback_uses_muses_profile_and_preserves_existing_text(tmp_path):
    calls = []

    def handler(request):
        payload = json.loads(request.content)
        calls.append(payload)
        if len(calls) == 1:
            return httpx.Response(503)
        return httpx.Response(200, json={"choices": [{
            "message": {"content": json.dumps({"action": "type", "text": APPEND_VALUE})},
            "finish_reason": "stop",
        }]})

    bridge = SyntheticTextEditBridge()
    bridge.value = VALUE
    report = await run_controller(
        tmp_path, handler, bridge, objective=APPEND_REQUEST, condition=APPEND_CONDITION,
    )
    assert report.status == "completed" and bridge.value == VALUE + APPEND_VALUE
    assert report.postcondition_checks[0].satisfied
    assert report.postcondition_checks[0].effect_observed
    assert bridge.actions == [ComputerAction(action="type", text=APPEND_VALUE)]
    assert len(calls) == 2
    assert calls[0]["model"] == "nvidia/nemotron-3-nano-omni-30b-a3b-reasoning"
    assert calls[0]["max_tokens"] == 256 and calls[0]["temperature"] == 0
    assert calls[0]["chat_template_kwargs"] == {"enable_thinking": False}
    assert calls[1]["model"] == "meta/muse-glimmer-30b"
    assert calls[1]["chat_template_kwargs"] == {"reasoning_strength": "low"}
    assert calls[1]["max_tokens"] == 2048 and calls[1]["temperature"] == 0.95
    assert calls[1]["top_p"] == 1
    assert calls[1]["response_format"] == {"type": "json_object"}


@pytest.mark.parametrize("finish", ["length", "content_filter"])
async def test_muse_fallback_profile_never_dispatches_an_incomplete_action(tmp_path, finish):
    calls = []

    def handler(request):
        payload = json.loads(request.content)
        calls.append(payload)
        if len(calls) == 1:
            return httpx.Response(503)
        return httpx.Response(200, json={"choices": [{
            "message": {"content": json.dumps({"action": "type", "text": APPEND_VALUE})},
            "finish_reason": finish,
        }]})

    bridge = SyntheticTextEditBridge()
    bridge.value = VALUE
    with pytest.raises(ComputerVisionNotDispatchedError):
        await run_controller(
            tmp_path, handler, bridge, objective=APPEND_REQUEST, condition=APPEND_CONDITION,
        )
    assert len(calls) == 2
    assert calls[1]["chat_template_kwargs"] == {"reasoning_strength": "low"}
    assert calls[1]["max_tokens"] == 2048
    assert bridge.actions == [] and bridge.value == VALUE
