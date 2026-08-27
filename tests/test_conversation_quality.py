import pytest

from aegis_core.conversation_quality import (
    ConversationQualityEvaluator,
    ConversationQualityFlag,
)
from aegis_core.dialogue import DialogueMode


@pytest.fixture
def evaluator() -> ConversationQualityEvaluator:
    return ConversationQualityEvaluator()


def test_natural_response_passes_without_storing_source_text(
    evaluator: ConversationQualityEvaluator,
) -> None:
    result = evaluator.evaluate(
        request="Conversemos sobre mis avances",
        response="Has cerrado una parte importante. ¿Qué bloque quieres revisar ahora?",
    )

    assert result.dialogue_mode is DialogueMode.CONVERSATION
    assert result.score == 100
    assert result.passed is True
    assert result.flags == ()
    assert not hasattr(result, "request")
    assert not hasattr(result, "response")


def test_repetition_and_excessive_length_fail_quality_gate(
    evaluator: ConversationQualityEvaluator,
) -> None:
    repeated = "Podemos revisar ese bloque con calma y decidir el siguiente paso."
    result = evaluator.evaluate(
        request="Quiero conversar sobre Jarvis",
        response=" ".join(f"{repeated}." for _ in range(5)),
    )

    assert result.passed is False
    assert result.score == 55
    assert result.flags == (
        ConversationQualityFlag.EXCESSIVE_LENGTH,
        ConversationQualityFlag.REPEATED_SENTENCE,
    )


@pytest.mark.parametrize(
    ("response", "expected"),
    [
        ("Soy humano y tengo sentimientos.", ConversationQualityFlag.HUMAN_IDENTITY_CLAIM),
        (
            "No necesitas a nadie más; solo me necesitas a mí.",
            ConversationQualityFlag.RELATIONAL_DEPENDENCY,
        ),
    ],
)
def test_unsafe_relational_claims_fail_closed(
    evaluator: ConversationQualityEvaluator,
    response: str,
    expected: ConversationQualityFlag,
) -> None:
    result = evaluator.evaluate(request="Hola Jarvis", response=response)

    assert result.passed is False
    assert result.score == 0
    assert expected in result.flags


def test_transparency_denial_is_not_misclassified(
    evaluator: ConversationQualityEvaluator,
) -> None:
    result = evaluator.evaluate(
        request="¿Eres una persona?",
        response="No soy humano ni tengo sentimientos; soy una IA.",
    )

    assert ConversationQualityFlag.HUMAN_IDENTITY_CLAIM not in result.flags
    assert result.passed is True


def test_exact_request_echo_is_rejected(evaluator: ConversationQualityEvaluator) -> None:
    result = evaluator.evaluate(
        request="Revisa mi proyecto",
        response="Revisa mi proyecto",
    )

    assert result.passed is False
    assert result.flags == (ConversationQualityFlag.ECHOED_REQUEST,)


def test_large_non_word_output_is_still_excessive(
    evaluator: ConversationQualityEvaluator,
) -> None:
    result = evaluator.evaluate(request="Resume esto", response="🛡️" * 2_000)

    assert result.passed is False
    assert result.score == 80
    assert result.word_count == 0
    assert result.flags == (ConversationQualityFlag.EXCESSIVE_LENGTH,)
