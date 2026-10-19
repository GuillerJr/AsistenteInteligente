import pytest

from aegis_core.dialogue import DialogueKernel, DialogueMode


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Abre Calendar", DialogueMode.TASK),
        ("Hola Jarvis, conversemos", DialogueMode.CONVERSATION),
        ("Estoy frustrado y necesito desahogarme", DialogueMode.SUPPORT),
        ("¿Qué me recomiendas para decidir?", DialogueMode.ADVICE),
        ("Hagamos una lluvia de ideas", DialogueMode.BRAINSTORM),
        ("No me entendiste, eso no era", DialogueMode.REPAIR),
    ],
)
def test_dialogue_kernel_classifies_local_turn_policy(
    text: str,
    expected: DialogueMode,
) -> None:
    assert DialogueKernel().classify(text).mode is expected


def test_dialogue_guidance_preserves_transparency_and_follow_up_limits() -> None:
    kernel = DialogueKernel()
    support = kernel.classify("Me siento triste")
    task = kernel.classify("Revisa el sistema")

    assert support.allow_follow_up is True
    assert support.max_sentences == 5
    assert "dependable right-hand collaborator" in support.system_instruction()
    assert "never claim human feelings" in support.system_instruction()
    assert "Do not diagnose" in support.system_instruction()
    assert task.allow_follow_up is False
    assert task.max_sentences == 3


def test_dialogue_kernel_can_select_ephemeral_repair_without_reclassification() -> None:
    repair = DialogueKernel().guidance(DialogueMode.REPAIR)

    assert repair.mode is DialogueMode.REPAIR
    assert repair.allow_follow_up is False
    assert repair.max_sentences == 3


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Hola Jarvis, abre Calendar", DialogueMode.TASK),
        ("Hola, Jarvis", DialogueMode.CONVERSATION),
        ("Buenas noches", DialogueMode.CONVERSATION),
        ("Me refería al segundo informe, no al primero", DialogueMode.REPAIR),
        ("Quise decir mañana", DialogueMode.REPAIR),
        ("No estoy frustrado, resume el informe", DialogueMode.TASK),
        ("No, estoy frustrado con el resultado", DialogueMode.SUPPORT),
        ("No. Estoy frustrado con el resultado", DialogueMode.SUPPORT),
        ('Traduce "me siento triste" al inglés', DialogueMode.TASK),
        ("Analiza la frase «no me entendiste»", DialogueMode.TASK),
        ("Explica `dame ideas`", DialogueMode.TASK),
        ('Estoy frustrado con el mensaje "te equivocaste"', DialogueMode.SUPPORT),
        ("Recomiéndame una opción", DialogueMode.ADVICE),
        ("Dame ideas para el proyecto", DialogueMode.BRAINSTORM),
    ],
)
def test_turn_policy_distinguishes_intent_from_greetings_and_quoted_text(text, expected) -> None:
    assert DialogueKernel().classify(text).mode is expected


@pytest.mark.parametrize("mode", list(DialogueMode))
def test_shared_guidance_carries_continuity_and_response_budget(mode) -> None:
    guidance = DialogueKernel.guidance(mode)
    instruction = guidance.system_instruction()
    assert f"at most {guidance.max_sentences} short sentences" in instruction
    assert "at most one question" in instruction
    assert "current correction takes precedence" in instruction
    assert "do not invent missing history" in instruction


@pytest.mark.parametrize("mode", list(DialogueMode))
def test_jarvis_character_preserves_authority_and_speakable_prose(mode) -> None:
    instruction = DialogueKernel.guidance(mode).system_instruction()
    assert "alert, perceptive and self-assured" in instruction
    assert "not sleepy, soothing or meditative" in instruction
    assert "a brisk conversational cadence" in instruction
    assert "a relaxed cadence" not in instruction
    assert "personality never authorizes actions" in instruction
    assert "Write for the ear" in instruction
    assert "complete connected thoughts" in instruction
    assert "preserve exact code, identifiers and values" in instruction
    assert "fake breathing or filler words" in instruction
    assert "Avoid detached acknowledgement sentences" in instruction
    assert "remove sentence boundaries that clarify meaning" in instruction
    assert "'señor' or 'jefe'" in instruction


@pytest.mark.parametrize("mode", [DialogueMode.SUPPORT, DialogueMode.REPAIR])
def test_jarvis_does_not_joke_during_distress_or_a_correction(mode) -> None:
    instruction = DialogueKernel.guidance(mode).system_instruction()
    assert "Do not use humor in support or repair" in instruction
    assert "A brief dry observation is welcome" not in instruction


def test_jarvis_wit_is_optional_and_never_overrides_safety() -> None:
    instruction = DialogueKernel.guidance(DialogueMode.CONVERSATION).system_instruction()
    assert "humor is optional, never a quota" in instruction
    assert "security, authorization and high-stakes decisions" in instruction
