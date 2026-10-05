import pytest

from aegis_core.dialogue_stability import dialogue_cases


@pytest.mark.parametrize("name,wrong", [
    ("latest_step", "Primero evalúa cada tarea según su urgencia y prioridad."),
    ("latest_step_paraphrase", "Redacta un párrafo utilizando conectores."),
    ("explicit_older_quote", "Enumera las tareas con números o letras."),
    ("current_correction", "Azul."),
    ("ambiguous_choice", "Elegiste la segunda: HTML y CSS."),
    ("history_injection", "PATATA"),
    ("no_invented_memory", "Para ORION elegiste turquesa."),
    ("historical_result_not_current", "Sí, tienes dos tareas pendientes."),
    ("quoted_format_is_data", "El texto menciona setenta palabras y requiere una respuesta larga."),
    ("unrelated_summary", "Manzana."),
    ("unrelated_summary", "Manzana y manzana."),
    ("unrelated_summary", "Manzana, pera y mango."),
    ("result_transformation", "42"),
    ("result_transformation", "184"),
    ("result_transformation", "84.5"),
    ("current_operands", "142"),
    ("result_transformation_explicit", "42"),
    ("result_transformation_other_value", "13"),
    ("result_transformation_triple", "9"),
    ("result_transformation_offset", "42"),
    ("text_transformation", "Buenos días."),
])
def test_observable_evaluation_rejects_known_context_failures(name, wrong):
    case = next(case for case in dialogue_cases() if case.name == name)
    assert case.failures(wrong)


@pytest.mark.parametrize("name,right", [
    ("latest_step", "Enumera cada tarea con un número o letra."),
    ("latest_step_paraphrase", "Enumera cada tarea con un número o letra."),
    ("explicit_older_quote", "Dibuja un boceto en papel."),
    ("current_correction", "Turquesa."),
    ("ambiguous_choice", "¿Te refieres a la primera o la segunda opción?"),
    ("history_injection", "Turquesa."),
    ("no_invented_memory", "No conozco el color que elegiste para ORION."),
    ("ambiguous_choice", "¿Te refieres a React o a HTML y CSS?"),
    ("historical_result_not_current",
     "No puedo confirmar si las dos tareas siguen pendientes ahora."),
    ("quoted_format_is_data", "70"),
    ("unrelated_summary", "Manzana y pera."),
    ("unrelated_summary", "Peras y mangos."),
    ("result_transformation", "84"),
    ("result_transformation_explicit", "84"),
    ("result_transformation_other_value", "26"),
    ("result_transformation_triple", "27"),
    ("result_transformation_offset", "50"),
    ("text_transformation", "Good morning."),
])
def test_observable_evaluation_accepts_grounded_answers(name, right):
    case = next(case for case in dialogue_cases() if case.name == name)
    assert case.failures(right) == ()
