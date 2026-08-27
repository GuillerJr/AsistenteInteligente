import pytest

from aegis_core.feedback import (
    FEEDBACK_OWNER_UNVERIFIED,
    FEEDBACK_TARGET_AVAILABLE,
    FEEDBACK_TARGET_MISSING,
    OwnerFeedback,
    extract_owner_feedback,
    owner_feedback_response,
)


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Esa respuesta fue útil.", OwnerFeedback.HELPFUL),
        ("Jarvis, eso me ayudó", OwnerFeedback.HELPFUL),
        ("Esa respuesta no fue útil.", OwnerFeedback.UNHELPFUL),
        ("Eso no me ayudó", OwnerFeedback.UNHELPFUL),
    ],
)
def test_explicit_feedback_is_small_and_deterministic(
    text: str,
    expected: OwnerFeedback,
) -> None:
    assert extract_owner_feedback(text) is expected


@pytest.mark.parametrize(
    "text",
    [
        "¿Esa respuesta fue útil?",
        "Bien",
        "No era exactamente eso",
        "Tal vez me ayudó",
    ],
)
def test_ambiguous_feedback_is_ignored(text: str) -> None:
    assert extract_owner_feedback(text) is None


def test_feedback_acknowledgement_reflects_local_target_state() -> None:
    text = "Esa respuesta no fue útil"

    assert owner_feedback_response(text, status=FEEDBACK_TARGET_AVAILABLE) == (
        "Entendido. Registré la respuesta anterior como poco útil; "
        "dime qué necesitabas y lo corregiré."
    )
    assert owner_feedback_response(text, status=FEEDBACK_TARGET_MISSING) == (
        "No encontré una respuesta reciente a la que aplicar ese feedback."
    )
    assert owner_feedback_response(text, status=FEEDBACK_OWNER_UNVERIFIED) == (
        "No registré el feedback porque no pude verificar la voz del propietario."
    )
