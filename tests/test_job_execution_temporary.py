from aegis_core.job_execution import without_memory_claims

NOTE = "Lo tengo presente en este chat, pero no lo guardaré: es un chat temporal."


def test_a_temporary_reply_does_not_claim_it_noted_anything() -> None:
    assert without_memory_claims("Entendido. Café sin azúcar, anotado.") == (
        f"Entendido. Café sin azúcar. {NOTE}")
    assert without_memory_claims("Perfecto, lo tendré en cuenta para la próxima.") == (
        f"Perfecto. {NOTE}")
    assert without_memory_claims("Anotado.") == NOTE
    assert without_memory_claims("Listo, ya lo he guardado. ¿Algo más?") == (
        f"Listo. {NOTE} ¿Algo más?")


def test_reminders_and_action_results_are_left_alone() -> None:
    for reply in ("Te lo recordaré mañana.", "El correo quedó guardado en borradores.",
                  "Lo guardo en Drive si apruebas.",
                  "Entendido. Respuestas breves.\n\n¿En qué puedo ayudarte?"):
        assert without_memory_claims(reply) == reply


def test_noting_that_becomes_understanding_and_the_note_is_not_repeated() -> None:
    assert without_memory_claims(
        "Entendido. Anoto que prefieres el café sin azúcar. Lo tendré presente mientras "
        "este chat esté activo, pero no lo guardaré.") == (
        "Entendido. Entiendo que prefieres el café sin azúcar. Lo tendré presente mientras "
        "este chat esté activo, pero no lo guardaré.")
    assert without_memory_claims("Café sin azúcar, anotado: no lo guardaré, es temporal.") == (
        "Café sin azúcar: no lo guardaré, es temporal.")
