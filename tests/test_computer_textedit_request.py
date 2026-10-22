"""The live TextEdit instruction through the real controller, with synthetic AX only."""

from __future__ import annotations

import base64
import json

from aegis_core.contracts import AgentResult, AgentRole, ImageInput
from aegis_core.tools.background_automation import QuietActionVerification
from aegis_core.tools.computer import (
    ComputerAction,
    ComputerObservation,
    ComputerPerception,
    ComputerPerceptionItem,
    ComputerUseController,
)
from aegis_core.tools.computer_postconditions import ComputerPostcondition

REQUEST = (
    "Controla la app TextEdit para escribir únicamente «PRUEBA JARVIS A» en el campo "
    "«First Text View» del documento nuevo vacío abierto. No abras otros documentos ni guardes."
)
BUNDLE = "com.apple.TextEdit"
VALUE = "PRUEBA JARVIS A"
FIELD = "First Text View"
CONDITION = ComputerPostcondition(kind="field_contains", target=FIELD, value=VALUE)


class SyntheticTextEditBridge:
    def __init__(self, *, sensitive: bool = False):
        self.sensitive = sensitive
        self.value = ""
        self.actions = []

    def activate(self, bundle_identifier):
        assert bundle_identifier == BUNDLE

    def capture(self, expected_bundle_identifier):
        assert expected_bundle_identifier == BUNDLE
        return ComputerObservation(
            image=ImageInput(
                media_type="image/jpeg",
                data_base64=base64.b64encode(b"\xff\xd8\xff\xd9").decode("ascii"),
            ),
            perception=ComputerPerception(
                windows=("Sin título",),
                items=(ComputerPerceptionItem(
                    source="accessibility", role="TextArea", text=FIELD, label=FIELD,
                    value=None if self.sensitive else self.value,
                    focused=True, x=500, y=300, sensitive=self.sensitive,
                ),),
                secure_content=self.sensitive,
            ),
            visual_context="a" * 64,
            visual_signature="f" * 64 if self.actions else "0" * 64,
            user_input_counter=42,
        )

    def act(self, action, expected_bundle_identifier, expected_visual_context,
            expected_user_input_counter):
        assert expected_bundle_identifier == BUNDLE
        assert expected_visual_context == "a" * 64
        assert expected_user_input_counter == 42
        assert action.action == "type"
        self.actions.append(action)
        self.value += action.text
        return QuietActionVerification(
            pathway="accessibility", before_sha256="0" * 64, after_sha256="f" * 64,
            state_changed=True, verified=True,
        )


class SyntheticVision:
    def __init__(self, action):
        self.action = action
        self.requests = []

    async def complete(self, **kwargs):
        self.requests.append(kwargs)
        assert kwargs["role"] is AgentRole.VISION
        return AgentResult(
            role=AgentRole.VISION, model_id="synthetic/vision",
            content=json.dumps(self.action),
        )


async def run_request(bridge, provider):
    return await ComputerUseController(
        provider, bridge, settle_seconds=0, timeout_seconds=2,
    ).run(
        objective=REQUEST, application_bundle_identifier=BUNDLE,
        max_steps=3, postconditions=(CONDITION,),
    )


async def test_exact_textedit_request_types_only_its_literal_and_verifies_the_field():
    bridge = SyntheticTextEditBridge()
    provider = SyntheticVision({"action": "type", "text": VALUE})

    report = await run_request(bridge, provider)

    assert report.status == "completed"
    assert report.reason_code == "objective_complete"
    assert report.steps == 1
    assert bridge.actions == [ComputerAction(action="type", text=VALUE)]
    assert bridge.value == VALUE
    check, = report.postcondition_checks
    assert check.kind == "field_contains"
    assert check.satisfied and check.effect_observed
    assert check.reason_code == "observed"
    assert len(provider.requests) == 1
    request = json.loads(provider.requests[0]["messages"][1]["content"][0]["text"])
    assert request["objective"] == REQUEST
    assert request["postconditions"] == [CONDITION.model_dump(exclude_none=True)]


async def test_exact_textedit_request_blocks_sensitive_ax_before_vision_or_actions():
    bridge = SyntheticTextEditBridge(sensitive=True)
    provider = SyntheticVision({"action": "type", "text": VALUE})

    report = await run_request(bridge, provider)

    assert report.status == "blocked"
    assert report.reason_code == "sensitive_action"
    assert report.steps == 0
    assert report.failed_check == "sensitive_state"
    assert provider.requests == []
    assert bridge.actions == []
    assert bridge.value == ""


async def test_exact_textedit_field_label_is_not_an_authorized_typing_literal():
    bridge = SyntheticTextEditBridge()
    provider = SyntheticVision({"action": "type", "text": FIELD})

    report = await run_request(bridge, provider)

    assert report.status == "blocked"
    assert report.reason_code == "unsupported_action"
    assert report.steps == 0
    assert report.failed_check == "action_binding"
    assert len(provider.requests) == 1
    assert bridge.actions == []
    assert bridge.value == ""
