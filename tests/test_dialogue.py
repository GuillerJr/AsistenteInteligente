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
    assert "never claim human feelings" in support.system_instruction()
    assert "Do not diagnose" in support.system_instruction()
    assert task.allow_follow_up is False
    assert task.max_sentences == 3


def test_dialogue_kernel_can_select_ephemeral_repair_without_reclassification() -> None:
    repair = DialogueKernel().guidance(DialogueMode.REPAIR)

    assert repair.mode is DialogueMode.REPAIR
    assert repair.allow_follow_up is False
    assert repair.max_sentences == 3
