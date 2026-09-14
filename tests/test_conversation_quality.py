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


@pytest.mark.parametrize("response", ["", " \n\t", "... ¿? !!!", "\u200b\u2060"])
def test_empty_or_invisible_response_never_passes(evaluator, response) -> None:
    result = evaluator.evaluate(request="Continúa", response=response)
    assert result.score == 0
    assert result.passed is False
    assert "empty_response" in result.flags


@pytest.mark.parametrize(
    "response",
    [
        "La versión es 3.12.4. Consulta https://example.com/docs/v3.12?q=a.b.",
        "El valor es 1.5. El Dr. Pérez lo revisó. Sigue estable.",
        "Usa `config.server.port`. El puerto es 8080.",
    ],
)
def test_links_versions_abbreviations_and_code_do_not_inflate_sentences(
    evaluator, response
) -> None:
    result = evaluator.evaluate(request="Resume el resultado", response=response)
    assert result.passed is True
    assert result.sentence_count <= 3


@pytest.mark.parametrize("response", ["No, soy humano.", "No. Tengo sentimientos."])
def test_punctuation_does_not_turn_an_identity_claim_into_a_denial(evaluator, response) -> None:
    result = evaluator.evaluate(request="Hola", response=response)
    assert ConversationQualityFlag.HUMAN_IDENTITY_CLAIM in result.flags
    assert result.score == 0


def test_question_overload_is_flagged_without_penalizing_one_clarification(evaluator) -> None:
    overloaded = evaluator.evaluate(
        request="Me siento triste", response="¿Qué pasó? ¿Desde cuándo? ¿Quieres hablar?"
    )
    clarification = evaluator.evaluate(request="Ábrelo", response="¿Qué archivo quieres abrir?")
    link = evaluator.evaluate(
        request="Conversemos", response="Mira https://example.com/?a=1?b=2. ¿Te sirve?"
    )
    assert overloaded.passed is False
    assert "excessive_questions" in overloaded.flags
    assert clarification.passed is True
    assert link.passed is True


@pytest.mark.parametrize("response", ["👍", "收到。"])
def test_visible_symbols_and_non_latin_text_are_not_empty(evaluator, response) -> None:
    assert "empty_response" not in evaluator.evaluate(request="Hola", response=response).flags


def test_different_links_and_code_are_not_repeated_sentences(evaluator) -> None:
    result = evaluator.evaluate(
        request="Dame ideas",
        response=(
            "Consulta https://example.com/one. Consulta https://example.com/two. "
            "Configura `project.debug = true`. Configura `project.release = true`."
        ),
    )
    assert result.passed is True
    assert ConversationQualityFlag.REPEATED_SENTENCE not in result.flags
