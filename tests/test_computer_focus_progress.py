"""Synthetic AX focus receipts and bounded vision decisions through NVIDIA's wire API."""

import hashlib
import json

import httpx
import pytest
from test_computer_textedit_request import FIELD, VALUE, SyntheticTextEditBridge
from test_computer_vision_inference import run_controller

from aegis_core.tools.background_automation import QuietActionVerification
from aegis_core.tools.computer import ComputerAction

DESCRIPTOR = f"{FIELD} área de texto"
FOCUS = {"action": "focus", "x": 500, "y": 300, "target": DESCRIPTOR}


class FocusTextEditBridge(SyntheticTextEditBridge):
    def __init__(self, *, focused):
        super().__init__()
        self.focused = focused
        self.focus_receipts = []

    def capture(self, bundle):
        observation = super().capture(bundle)
        field, = observation.perception.items
        return observation.model_copy(update={
            "perception": observation.perception.model_copy(update={
                "items": (field.model_copy(update={"focused": self.focused}),),
            }),
            # Focusing an already focused field produces no visual progress.
            "visual_signature": "f" * 64 if self.value else "0" * 64,
        })

    def act(self, action, *args):
        if action.action != "focus":
            assert self.focused
            return super().act(action, *args)
        assert action == ComputerAction(**FOCUS)
        before = hashlib.sha256(str(self.focused).encode()).hexdigest()
        self.focused = True
        after = hashlib.sha256(str(self.focused).encode()).hexdigest()
        self.actions.append(action)
        receipt = QuietActionVerification(
            pathway="accessibility", before_sha256=before, after_sha256=after,
            state_changed=before != after, verified=True,
        )
        self.focus_receipts.append(receipt)
        return receipt


def response(action):
    return httpx.Response(200, json={"choices": [{
        "message": {"content": json.dumps(action)}, "finish_reason": "stop",
    }]})


@pytest.mark.parametrize("focused", [False, True])
async def test_vision_gets_native_focus_receipt_before_typing_once(tmp_path, focused):
    calls = []

    def handler(request):
        payload = json.loads(request.content)
        context = json.loads(payload["messages"][1]["content"][0]["text"])
        calls.append(context)
        if len(calls) == 1:
            assert context["previous_action"] is None
            return response(FOCUS)
        # The native receipt refers to the dispatched action, not a fresh model
        # assertion. A verified focus can legitimately leave the state unchanged.
        assert context["previous_action"] == {
            "action": "focus", "target": DESCRIPTOR, "native_verified": True,
        }
        assert context["local_perception"]["items"][0]["focused"] is True
        return response({"action": "type", "text": VALUE})

    bridge = FocusTextEditBridge(focused=focused)
    report = await run_controller(tmp_path, handler, bridge)

    assert report.status == "completed" and report.steps == 2
    assert report.postcondition_checks[0].satisfied
    assert bridge.value == VALUE
    assert bridge.actions == [ComputerAction(**FOCUS), ComputerAction(action="type", text=VALUE)]
    assert len(calls) == 2
    assert bridge.focus_receipts[0].state_changed is (not focused)


async def test_repeated_focus_on_unchanged_field_still_stops_before_second_dispatch(tmp_path):
    bridge = FocusTextEditBridge(focused=True)
    report = await run_controller(tmp_path, lambda _: response(FOCUS), bridge)

    assert report.status == "partial" and report.steps == 1
    assert report.failed_check == "no_progress"
    assert bridge.actions == [ComputerAction(**FOCUS)]
    assert bridge.value == "" and not report.postcondition_checks[0].satisfied


async def test_focus_must_bind_full_accessibility_text_not_only_its_label(tmp_path):
    bridge = FocusTextEditBridge(focused=False)
    report = await run_controller(
        tmp_path, lambda _: response({**FOCUS, "target": FIELD}), bridge,
    )

    assert report.status == "blocked" and report.steps == 0
    assert bridge.actions == [] and bridge.value == ""
    assert not report.postcondition_checks[0].satisfied


async def test_verified_focus_receipt_does_not_certify_text_was_written(tmp_path):
    bridge = FocusTextEditBridge(focused=True)
    actions = iter((FOCUS, {"action": "done", "evidence": VALUE}))
    report = await run_controller(tmp_path, lambda _: response(next(actions)), bridge)

    assert report.status == "partial" and report.steps == 1
    assert report.failed_check == "postcondition_failed"
    assert bridge.actions == [ComputerAction(**FOCUS)]
    assert bridge.value == "" and not report.postcondition_checks[0].satisfied
