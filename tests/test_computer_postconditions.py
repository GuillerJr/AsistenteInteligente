from __future__ import annotations

import base64
import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from aegis_core.contracts import AgentResult, AgentRole, ImageInput, PolicyDecision, ToolCall
from aegis_core.tools.background_automation import QuietActionVerification
from aegis_core.tools.computer import (
    ComputerObservation,
    ComputerPerception,
    ComputerPerceptionItem,
    ComputerUseController,
    ComputerUseError,
)
from aegis_core.tools.computer_postconditions import (
    ComputerPostcondition,
    check_postcondition,
    postconditions_are_bound,
)
from aegis_core.tools.defaults import build_default_tool_broker, default_policy_context


class Bridge:
    def __init__(self, *perceptions: ComputerPerception, verified: bool = True):
        self.perceptions = list(perceptions)
        self.current = perceptions[0]
        self.actions = []
        self.activations = []
        self.verified = verified

    def activate(self, bundle):
        self.activations.append(bundle)

    def capture(self, bundle):
        if self.perceptions:
            self.current = self.perceptions.pop(0)
        return ComputerObservation(
            image=ImageInput(
                media_type="image/jpeg", data_base64=base64.b64encode(b"\xff\xd8\xff\xd9").decode()
            ),
            perception=self.current,
            visual_context="a" * 64,
            visual_signature="0" * 64,
            user_input_counter=1,
        )

    def act(self, action, *args):
        self.actions.append(action)
        if self.verified:
            return QuietActionVerification(
                pathway="accessibility",
                before_sha256="a" * 64,
                after_sha256="b" * 64,
                state_changed=True,
                verified=True,
            )
        return None


class Provider:
    def __init__(self, *responses):
        self.responses = list(responses)
        self.calls = []

    async def complete(self, **kwargs):
        self.calls.append(kwargs)
        return AgentResult(
            role=AgentRole.VISION, model_id="synthetic/vision", content=self.responses.pop(0)
        )


def field(value=None, **kwargs):
    return ComputerPerceptionItem(
        source="accessibility",
        role="TextField",
        text="Buscar",
        x=500,
        y=200,
        focused=True,
        value=value,
        **kwargs,
    )


@pytest.mark.asyncio
async def test_unrelated_initial_title_cannot_complete_an_unverifiable_objective():
    bridge = Bridge(ComputerPerception(windows=("Panel de inicio",)))
    provider = Provider('{"action":"done","evidence":"Panel de inicio"}')
    report = await ComputerUseController(provider, bridge, settle_seconds=0).run(
        objective="Selecciona Informes y comprueba el informe trimestral",
        application_bundle_identifier="ai.aegis.fixture",
        max_steps=3,
    )
    assert report.status == "blocked"
    assert report.reason_code == "missing_postcondition"
    assert report.failed_check == "missing_postcondition"
    assert report.steps == 0 and bridge.actions == [] and provider.calls == []


@pytest.mark.parametrize(
    "objective,condition",
    [
        ("Selecciona Informes", ComputerPostcondition(kind="window_title", value="Inicio")),
        ("Selecciona Informes", ComputerPostcondition(kind="text_present", value="Informes")),
        (
            "Abre Informes",
            ComputerPostcondition(kind="field_value", target="Informes", value="Informes"),
        ),
        ("Selecciona Informes", ComputerPostcondition(kind="focused_control", target="Informes")),
    ],
)
def test_conditions_must_match_both_objective_literal_and_operation(objective, condition):
    assert not postconditions_are_bound((condition,), objective)


@pytest.mark.parametrize(
    "objective,condition",
    [
        (
            "Selecciona Informes y comprueba texto visible Informes",
            ComputerPostcondition(kind="text_present", value="Informes"),
        ),
        (
            "Selecciona Informes y abre la ventana Safari",
            ComputerPostcondition(kind="window_title", value="Safari"),
        ),
        ("En Safari abre Informes", ComputerPostcondition(kind="window_title", value="Safari")),
        (
            "Selecciona Informes",
            ComputerPostcondition(
                kind="control_state", target="Informes", attribute="selected", value=False
            ),
        ),
        (
            "Activa Informes",
            ComputerPostcondition(
                kind="control_state", target="Informes", attribute="enabled", value=False
            ),
        ),
        (
            "Selecciona Informes y después selecciona Resumen",
            ComputerPostcondition(
                kind="control_state", target="Informes", attribute="selected", value=True
            ),
        ),
    ],
)
def test_incidental_title_visibility_or_opposite_state_cannot_replace_requested_state(
    objective,
    condition,
):
    assert not postconditions_are_bound((condition,), objective)


def test_each_requested_target_and_state_must_have_its_own_postcondition():
    conditions = (
        ComputerPostcondition(
            kind="control_state", target="Informes", attribute="selected", value=True
        ),
        ComputerPostcondition(
            kind="control_state", target="Resumen", attribute="selected", value=False
        ),
    )
    assert postconditions_are_bound(conditions, "Selecciona Informes y deselecciona Resumen")


@pytest.mark.asyncio
async def test_unrelated_supplied_condition_is_rejected_before_activation():
    bridge = Bridge(ComputerPerception(windows=("Inicio",)))
    with pytest.raises(ComputerUseError, match="computer_invalid_postcondition"):
        await ComputerUseController(Provider(), bridge).run(
            objective="Abre la ventana Informes",
            application_bundle_identifier="ai.aegis.fixture",
            max_steps=3,
            postconditions=(ComputerPostcondition(kind="window_title", value="Inicio"),),
        )
    assert not bridge.activations and not bridge.actions


@pytest.mark.asyncio
async def test_selected_control_is_verified_after_final_budgeted_action_without_model_done():
    before = ComputerPerceptionItem(
        source="accessibility",
        role="RadioButton",
        text="Informes",
        x=500,
        y=200,
        pressable=True,
        selected=False,
    )
    after = before.model_copy(update={"selected": True})
    bridge = Bridge(ComputerPerception(items=(before,)), ComputerPerception(items=(after,)))
    provider = Provider(
        '{"action":"click","x":500,"y":200,"button":"left","click_count":1,"target":"Informes"}'
    )
    report = await ComputerUseController(provider, bridge, settle_seconds=0).run(
        objective="Selecciona Informes",
        application_bundle_identifier="ai.aegis.fixture",
        max_steps=1,
        postconditions=(
            ComputerPostcondition(
                kind="control_state", target="Informes", attribute="selected", value=True
            ),
        ),
    )
    assert report.status == "completed" and report.steps == 1
    assert report.postcondition_checks[0].satisfied
    assert report.last_action.target == "Informes"
    assert len(provider.calls) == 1 and len(bridge.actions) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "after_value,expected", [("informe", "completed"), ("incorrecto", "partial")]
)
async def test_field_replacement_checks_actual_value_and_never_repeats(after_value, expected):
    bridge = Bridge(
        ComputerPerception(items=(field("anterior"),)),
        ComputerPerception(items=(field(after_value),)),
    )
    provider = Provider()
    report = await ComputerUseController(provider, bridge, settle_seconds=0).run(
        objective="Reemplaza el contenido del campo «Buscar» por «informe»",
        application_bundle_identifier="ai.aegis.fixture",
        max_steps=4,
    )
    assert report.status == expected and len(bridge.actions) == 1 and not provider.calls
    assert report.postcondition_checks[0].satisfied is (expected == "completed")
    assert report.last_action.text_sha256 and "informe" not in report.model_dump_json()
    assert report.observation_sha256


@pytest.mark.parametrize(
    "conditions",
    [
        (ComputerPostcondition(kind="field_contains", target="Buscar", value="7319"),),
        (ComputerPostcondition(kind="field_value", target="Buscar", value="17319"),),
    ],
)
async def test_exact_replacement_rejects_weak_or_wrong_literal_before_acting(conditions):
    bridge = Bridge(ComputerPerception(items=(field("17319"),)))
    with pytest.raises(ComputerUseError, match="invalid_postcondition"):
        await ComputerUseController(Provider(), bridge).run(
            objective="Reemplaza el campo Buscar por «7319»",
            application_bundle_identifier="ai.aegis.fixture",
            max_steps=3,
            postconditions=conditions,
        )
    assert not bridge.activations and not bridge.actions


def test_broker_denies_contains_as_proof_of_exact_replacement():
    call = ToolCall(
        call_id="weak-replacement",
        tool_name="computer_use",
        requested_by=AgentRole.PLANNER,
        arguments={
            "objective": "Reemplaza el campo Buscar por «7319»",
            "application_bundle_identifier": "ai.aegis.fixture",
            "max_steps": 3,
            "postconditions": [{"kind": "field_contains", "target": "Buscar", "value": "7319"}],
        },
    )
    authorization = build_default_tool_broker().authorize(call, default_policy_context(Path.cwd()))
    assert authorization.decision is PolicyDecision.DENY
    assert authorization.reason_code == "invalid_arguments"


@pytest.mark.parametrize(
    "objective,condition",
    [
        (
            "Explica «Abre la ventana Informes»",
            ComputerPostcondition(kind="window_title", value="Informes"),
        ),
        (
            "No seleccionar Informes",
            ComputerPostcondition(
                kind="control_state", target="Informes", attribute="selected", value=True
            ),
        ),
        (
            "No abras https://example.org",
            ComputerPostcondition(kind="navigation_url", value="https://example.org"),
        ),
        (
            "Reemplaza el campo Buscar por «7319», no por «17319»",
            ComputerPostcondition(kind="field_value", target="Buscar", value="17319"),
        ),
    ],
)
def test_quoted_concept_negation_and_ambiguous_replacement_cannot_certify_goal(
    objective, condition
):
    assert not postconditions_are_bound((condition,), objective)


@pytest.mark.parametrize("kind", ["field_value", "field_contains"])
@pytest.mark.parametrize(
    "objective",
    [
        "Controla la app TextEdit para escribir únicamente «PRUEBA JARVIS A» en el documento "
        "nuevo vacío abierto. No abras otros documentos ni guardes.",
        "No abras otros documentos ni guardes. Escribe «PRUEBA JARVIS A».",
        'Type only "PRUEBA JARVIS A" in the empty document; do not open other documents.',
        "Escribe «PRUEBA JARVIS A» y no abras otros documentos ni guardes.",
        "Escribe «no abras otros documentos» en el campo Buscar.",
    ],
)
def test_opening_restriction_preserves_separate_affirmative_field_write(kind, objective):
    value = "no abras otros documentos" if "«no abras" in objective else "PRUEBA JARVIS A"
    assert postconditions_are_bound((ComputerPostcondition(kind=kind, value=value),), objective)


def test_negative_operator_is_not_itself_a_forbidden_payload():
    assert postconditions_are_bound(
        (ComputerPostcondition(kind="field_contains", value="No"),),
        "Escribe «No». No abras otros documentos ni guardes.",
    )


@pytest.mark.parametrize(
    "objective,condition",
    [
        ("No escribas «PRUEBA JARVIS A».",
         ComputerPostcondition(kind="field_contains", value="PRUEBA JARVIS A")),
        ("No escribir «PRUEBA JARVIS A».",
         ComputerPostcondition(kind="field_contains", value="PRUEBA JARVIS A")),
        ("Escribe «PRUEBA JARVIS A», no «OTRO TEXTO».",
         ComputerPostcondition(kind="field_contains", value="OTRO TEXTO")),
        ("Escribe «PRUEBA JARVIS A» en Buscar, no en Privado.",
         ComputerPostcondition(kind="field_contains", target="Privado", value="PRUEBA JARVIS A")),
        ("Escribe «PRUEBA JARVIS A». No abras el documento «PRUEBA JARVIS B».",
         ComputerPostcondition(kind="field_contains", value="PRUEBA JARVIS B")),
        ("Escribe «PRUEBA JARVIS A». No abras https://example.org.",
         ComputerPostcondition(kind="navigation_url", value="https://example.org")),
        ("No abras otros documentos ni escribas «PRUEBA JARVIS A».",
         ComputerPostcondition(kind="field_contains", value="PRUEBA JARVIS A")),
        ("Escribe «PRUEBA JARVIS A». No selecciones Privado.",
         ComputerPostcondition(kind="field_contains", value="PRUEBA JARVIS A")),
        ("No abras otros documentos. Comprueba que el campo Buscar contiene «PRUEBA JARVIS A».",
         ComputerPostcondition(kind="field_contains", target="Buscar", value="PRUEBA JARVIS A")),
    ],
)
def test_field_exception_never_authorizes_negated_operations_literals_or_targets(
    objective, condition,
):
    assert not postconditions_are_bound((condition,), objective)


async def test_negated_write_is_rejected_before_any_activation_or_capture():
    bridge = Bridge(ComputerPerception(items=(field(""),)))
    provider = Provider()
    with pytest.raises(ComputerUseError, match="computer_invalid_postcondition"):
        await ComputerUseController(provider, bridge).run(
            objective="Controla TextEdit. No escribas «PRUEBA JARVIS A».",
            application_bundle_identifier="com.apple.TextEdit",
            max_steps=1,
            postconditions=(ComputerPostcondition(kind="field_contains", value="PRUEBA JARVIS A"),),
        )
    assert not bridge.activations and not bridge.actions and not provider.calls


async def test_requested_replacement_17319_is_not_7319_and_matching_value_is_idempotent():
    for actual, expected_steps in (("17319", 1), ("7319", 0)):
        bridge = Bridge(
            ComputerPerception(items=(field(actual),)), ComputerPerception(items=(field("7319"),))
        )
        report = await ComputerUseController(Provider(), bridge, settle_seconds=0).run(
            objective="Reemplaza el campo Buscar por «7319»",
            application_bundle_identifier="ai.aegis.fixture",
            max_steps=3,
        )
        assert report.status == "completed" and report.steps == expected_steps
        assert report.postcondition_checks[0].kind == "field_value"
        assert len(bridge.actions) == expected_steps


@pytest.mark.parametrize("after,expected", [("7319", "partial"), ("73197319", "completed")])
async def test_insertion_requires_a_new_observed_literal_and_acknowledged_write(after, expected):
    bridge = Bridge(
        ComputerPerception(items=(field("7319"),)), ComputerPerception(items=(field(after),))
    )
    controller = ComputerUseController(Provider(), bridge, settle_seconds=0)
    report = await controller.run(
        objective="Escribe «7319»", application_bundle_identifier="ai.aegis.fixture", max_steps=2
    )
    assert report.status == expected and report.steps == 1 and len(bridge.actions) == 1
    proof = report.postcondition_checks[0]
    assert proof.previous_sha256 and proof.effect_observed is (expected == "completed")
    # A fresh observation on its own cannot establish that insertion occurred.
    assert not await controller.verify_objective(
        objective="Escribe «7319»", application_bundle_identifier="ai.aegis.fixture"
    )
    assert await controller.verify_objective(
        objective="Escribe «7319»",
        application_bundle_identifier="ai.aegis.fixture",
        verified_report=report,
    ) is (expected == "completed")


async def test_failed_capture_after_write_retains_partial_action_and_never_retries():
    class BrokenCapture(Bridge):
        def capture(self, bundle):
            if self.actions:
                raise ComputerUseError("computer_capture_failed")
            return super().capture(bundle)

    bridge = BrokenCapture(ComputerPerception(items=(field("anterior"),)))
    report = await ComputerUseController(Provider(), bridge, settle_seconds=0).run(
        objective="Reemplaza el campo Buscar por «7319»",
        application_bundle_identifier="ai.aegis.fixture",
        max_steps=3,
    )
    assert report.status == "partial" and report.failed_check == "unverified_action"
    assert report.last_action.action == "replace_text" and len(bridge.actions) == 1


@pytest.mark.asyncio
async def test_remote_text_mutation_stops_partial_instead_of_retrying_the_write():
    bridge = Bridge(
        ComputerPerception(items=(field(""),)), ComputerPerception(items=(field("otro"),))
    )
    provider = Provider('{"action":"type","text":"informe"}', '{"action":"type","text":"informe"}')
    report = await ComputerUseController(provider, bridge, settle_seconds=0).run(
        objective="En el campo Buscar, escribe literalmente informe y verifica su valor",
        application_bundle_identifier="ai.aegis.fixture",
        max_steps=4,
        postconditions=(
            ComputerPostcondition(kind="field_value", target="Buscar", value="informe"),
        ),
    )
    assert report.status == "partial" and report.failed_check == "postcondition_failed"
    assert len(provider.calls) == len(bridge.actions) == 1


@pytest.mark.parametrize(
    "perception,reason",
    [
        (ComputerPerception(items=(field("informe", value_truncated=True),)), "value_unavailable"),
        (ComputerPerception(items=(field("informe"),), truncated=True), "observation_truncated"),
        (ComputerPerception(items=(field("informe"), field("informe"))), "ambiguous_target"),
        (ComputerPerception(items=(field(None),)), "value_unavailable"),
        (ComputerPerception(secure_content=True), "sensitive_state"),
    ],
)
def test_field_evidence_must_be_complete_unique_and_non_sensitive(perception, reason):
    check = check_postcondition(
        ComputerPostcondition(kind="field_value", target="Buscar", value="informe"), perception
    )
    assert not check.satisfied and check.reason_code == reason


def test_ocr_cannot_certify_a_value_or_visible_text():
    with pytest.raises(ValidationError):
        ComputerPerceptionItem(
            source="vision", role="TextField", text="Buscar", value="informe", confidence=1
        )
    perception = ComputerPerception(
        items=(
            ComputerPerceptionItem(
                source="vision", role="Text", text="Informe listo", confidence=1
            ),
        )
    )
    assert not check_postcondition(
        ComputerPostcondition(kind="text_present", value="Informe listo"), perception
    ).satisfied


@pytest.mark.asyncio
async def test_exact_requested_url_can_verify_existing_page_without_actions_or_inference():
    bridge = Bridge(ComputerPerception(current_url="https://example.com/reports?year=2026"))
    provider = Provider()
    report = await ComputerUseController(provider, bridge).run(
        objective="Abre https://example.com/reports?year=2026",
        application_bundle_identifier="com.apple.Safari",
        max_steps=2,
        postconditions=(
            ComputerPostcondition(
                kind="navigation_url", value="https://example.com/reports?year=2026"
            ),
        ),
    )
    assert report.status == "completed" and report.steps == 0
    assert not provider.calls and not bridge.actions


@pytest.mark.asyncio
async def test_model_done_with_correct_title_cannot_certify_different_requested_url():
    bridge = Bridge(
        ComputerPerception(windows=("Informes",), current_url="https://example.com/home")
    )
    provider = Provider('{"action":"done","evidence":"Informes"}')
    report = await ComputerUseController(provider, bridge).run(
        objective="Abre https://example.com/reports",
        application_bundle_identifier="com.apple.Safari",
        max_steps=2,
        postconditions=(
            ComputerPostcondition(kind="navigation_url", value="https://example.com/reports"),
        ),
    )
    assert report.status == "blocked" and report.reason_code == "postcondition_failed"
    assert not bridge.actions


@pytest.mark.asyncio
async def test_generic_recovery_does_not_send_command_r_to_non_browser_application():
    bridge = Bridge(ComputerPerception())
    assert not await ComputerUseController(Provider(), bridge).reload_application(
        application_bundle_identifier="ai.aegis.fixture"
    )
    assert not bridge.activations and not bridge.actions


@pytest.mark.asyncio
async def test_browser_reload_uses_bound_native_verified_shortcut():
    bridge = Bridge(ComputerPerception())
    assert await ComputerUseController(Provider(), bridge).reload_application(
        application_bundle_identifier="com.apple.Safari"
    )
    assert bridge.actions[0].key == "r" and bridge.actions[0].modifiers == ["command"]


@pytest.mark.asyncio
async def test_literal_click_reports_action_delivery_instead_of_semantic_page_success():
    before = ComputerPerception(
        items=(
            ComputerPerceptionItem(
                source="accessibility", role="Button", text="Informes", pressable=True, x=500, y=200
            ),
        )
    )
    bridge = Bridge(before, ComputerPerception(windows=("Otra ventana",)))
    report = await ComputerUseController(Provider(), bridge, settle_seconds=0).run(
        objective="Haz clic en Informes",
        application_bundle_identifier="ai.aegis.fixture",
        max_steps=1,
    )
    assert report.status == "completed"
    assert report.postcondition_checks[0].kind == "action_performed"
    assert report.last_action.action == "click"


@pytest.mark.asyncio
async def test_unknown_native_ack_cannot_certify_literal_action_even_with_ui_progress():
    before = ComputerPerception(
        items=(
            ComputerPerceptionItem(
                source="accessibility", role="Button", text="Informes", pressable=True, x=500, y=200
            ),
        )
    )
    bridge = Bridge(before, ComputerPerception(windows=("Otra ventana",)), verified=False)
    report = await ComputerUseController(Provider(), bridge, settle_seconds=0).run(
        objective="Haz clic en Informes",
        application_bundle_identifier="ai.aegis.fixture",
        max_steps=1,
    )
    assert report.status == "partial" and report.reason_code == "missing_postcondition"


def test_remote_perception_keeps_ax_control_values_as_data_and_budgeted_item_count():
    result = ComputerUseController._remote_perception_summary(
        ComputerPerception(items=(field("informe", label="Buscar", selected=False),) * 30)
    )
    assert len(result["items"]) == 24
    assert result["items"][0]["value"] == "informe"
    assert result["items"][0]["selected"] is False
    assert len(json.dumps(result).encode()) < 8_192
