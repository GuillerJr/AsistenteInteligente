"""NVIDIA's closed perception profile is scoped to one model and one attempt."""

from copy import deepcopy

import pytest

from aegis_core.contracts import AgentRole
from aegis_core.providers.nvidia import NvidiaNimClient

MUSE = "meta/muse-glimmer-30b"
OMNI = "nvidia/nemotron-3-nano-omni-30b-a3b-reasoning"


@pytest.mark.parametrize("budget, expected", [(256, 2048), (2048, 2048), (4096, 4096)])
@pytest.mark.parametrize("explicit_options", [None, {}, {"reasoning_strength": "high"}])
def test_muse_perception_reserves_shared_budget_and_respects_explicit_options(
    budget, expected, explicit_options,
):
    payload = {
        "messages": [{"role": "user", "content": "synthetic"}],
        "max_tokens": budget,
        "temperature": 0,
        "response_format": {"type": "json_object"},
    }
    if explicit_options is not None:
        payload["chat_template_kwargs"] = explicit_options
    original = deepcopy(payload)

    attempt = NvidiaNimClient._attempt_payload(
        payload, AgentRole.VISION, MUSE, perception_only=True,
    )

    assert attempt == {
        **original,
        "model": MUSE,
        "max_tokens": expected,
        "temperature": 0.95,
        "top_p": 1,
        "chat_template_kwargs": (
            explicit_options if explicit_options is not None else {"reasoning_strength": "low"}
        ),
    }
    assert payload == original
    # The same base payload may be used for another model on another attempt.
    omni = NvidiaNimClient._attempt_payload(
        payload, AgentRole.VISION, OMNI, perception_only=True,
    )
    assert omni["max_tokens"] == budget and omni["temperature"] == 0
    assert "top_p" not in omni
    assert omni["chat_template_kwargs"] == (
        explicit_options if explicit_options is not None else {"enable_thinking": False}
    )


@pytest.mark.parametrize("role", [role for role in AgentRole if role is not AgentRole.VISION])
def test_muse_perception_does_not_change_other_roles(role):
    payload = {"max_tokens": 256, "temperature": 0}
    assert NvidiaNimClient._attempt_payload(payload, role, MUSE, perception_only=True) == {
        **payload, "model": MUSE,
    }


@pytest.mark.parametrize("model", [MUSE, OMNI, "configured/other"])
def test_vision_without_closed_perception_preserves_model_defaults(model):
    payload = {"max_tokens": 256, "temperature": 0}
    assert NvidiaNimClient._attempt_payload(payload, AgentRole.VISION, model) == {
        **payload, "model": model,
    }


def test_closed_perception_does_not_guess_a_profile_for_other_models():
    payload = {"max_tokens": 256, "temperature": 0}
    assert NvidiaNimClient._attempt_payload(
        payload, AgentRole.VISION, "configured/other", perception_only=True,
    ) == {**payload, "model": "configured/other"}
