from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest

from aegis_core.contracts import AgentRole, InputModality, UserRequest
from aegis_core.orchestration.direct_actions import direct_local_response, direct_tool_call


@pytest.mark.parametrize(
    ("text", "bundle_identifier"),
    [
        ("Abre Safari", "com.apple.Safari"),
        ("Jarvis, abre Google Chrome", "com.google.Chrome"),
        ("Open Firefox", "org.mozilla.firefox"),
        ("Abre la aplicación Calendario", "com.apple.iCal"),
        ("Abrir Vista Previa", "com.apple.Preview"),
    ],
)
def test_known_application_commands_become_exact_local_calls(
    text: str, bundle_identifier: str
) -> None:
    call = direct_tool_call(UserRequest(text=text))

    assert call is not None
    assert call.tool_name == "application_open"
    assert call.requested_by is AgentRole.PLANNER
    assert call.arguments == {"bundle_identifier": bundle_identifier}


@pytest.mark.parametrize(
    ("text", "name"),
    [
        ("Ejecuta el atajo Informe diario", "Informe diario"),
        ("Jarvis: ejecutar shortcut Estado del proyecto", "Estado del proyecto"),
        ("Run shortcut Daily Brief", "Daily Brief"),
    ],
)
def test_shortcut_commands_preserve_the_exact_name(text: str, name: str) -> None:
    call = direct_tool_call(UserRequest(text=text))

    assert call is not None
    assert call.tool_name == "shortcut_run"
    assert call.requested_by is AgentRole.PLANNER
    assert call.arguments == {"name": name}


@pytest.mark.parametrize(
    ("text", "template"),
    [
        ("Revisa el estado de Git", "git_status"),
        ("Lista los procesos", "list_processes"),
        ("Lista los puertos abiertos", "list_listeners"),
        ("Revisa la postura de seguridad", "security_posture"),
    ],
)
def test_fixed_diagnostics_become_exact_local_calls(text: str, template: str) -> None:
    call = direct_tool_call(UserRequest(text=text))

    assert call is not None
    assert call.tool_name == "terminal_run_template"
    assert call.requested_by is AgentRole.CODE_SECURITY
    assert call.arguments == {"template": template}


@pytest.mark.parametrize(
    ("text", "unread_only"),
    [
        ("Revisa mi correo", False),
        ("Muéstrame mis correos no leídos", True),
        ("Check my mail", False),
        ("Show unread mail", True),
    ],
)
def test_mail_reads_become_bounded_local_calls(text: str, unread_only: bool) -> None:
    call = direct_tool_call(UserRequest(text=text))

    assert call is not None
    assert call.tool_name == "mail_list_recent"
    assert call.requested_by is AgentRole.PLANNER
    assert call.arguments == {"limit": 10, "unread_only": unread_only}


@pytest.mark.parametrize(
    "text",
    [
        "Tengo correos no leídos",
        "¿Tengo algún correo sin leer?",
        "Hay correos no leidos",
        "Do I have unread mail",
    ],
)
def test_unread_mail_status_uses_a_single_private_result(text: str) -> None:
    call = direct_tool_call(UserRequest(text=text))

    assert call is not None
    assert call.tool_name == "mail_list_recent"
    assert call.requested_by is AgentRole.PLANNER
    assert call.arguments == {"limit": 1, "unread_only": True}


@pytest.mark.parametrize(
    "text",
    [
        "Cuál es mi último correo",
        "Cual fue mi ultimo correo",
        "Dime mi último correo",
        "What is my latest email",
    ],
)
def test_latest_mail_uses_a_single_private_result(text: str) -> None:
    call = direct_tool_call(UserRequest(text=text))

    assert call is not None
    assert call.tool_name == "mail_list_recent"
    assert call.requested_by is AgentRole.PLANNER
    assert call.arguments == {"limit": 1, "unread_only": False}


@pytest.mark.parametrize(
    "text",
    ["Lista mis recordatorios", "Muéstrame mis recordatorios", "Show my reminders"],
)
def test_reminder_reads_become_bounded_local_calls(text: str) -> None:
    call = direct_tool_call(UserRequest(text=text))

    assert call is not None
    assert call.tool_name == "reminders_list"
    assert call.requested_by is AgentRole.PLANNER
    assert call.arguments == {
        "list_name": None,
        "include_completed": False,
        "limit": 20,
    }


@pytest.mark.parametrize(
    ("text", "query"),
    [("Busca el contacto Ada Lovelace", "Ada Lovelace"), ("Find contact Alan", "Alan")],
)
def test_contact_searches_become_bounded_local_calls(text: str, query: str) -> None:
    call = direct_tool_call(UserRequest(text=text))

    assert call is not None
    assert call.tool_name == "contacts_search"
    assert call.requested_by is AgentRole.PLANNER
    assert call.arguments == {"query": query, "limit": 10}


@pytest.mark.parametrize(
    ("text", "arguments"),
    [
        ("Silencia el Mac", {"volume_percent": None, "muted": True}),
        ("Activa el sonido", {"volume_percent": None, "muted": False}),
        ("Pon el volumen al 42 por ciento", {"volume_percent": 42, "muted": None}),
        ("Set volume to 75 percent", {"volume_percent": 75, "muted": None}),
    ],
)
def test_audio_changes_become_exact_confirmed_calls(
    text: str, arguments: dict[str, object]
) -> None:
    call = direct_tool_call(UserRequest(text=text))

    assert call is not None
    assert call.tool_name == "system_audio_set"
    assert call.arguments == arguments


@pytest.mark.parametrize(
    ("text", "action"),
    [
        ("Pausa la música", "play_pause"),
        ("Siguiente canción", "next"),
        ("Previous track", "previous"),
    ],
)
def test_media_controls_become_fixed_confirmed_calls(text: str, action: str) -> None:
    call = direct_tool_call(UserRequest(text=text))

    assert call is not None
    assert call.tool_name == "media_control"
    assert call.arguments == {"action": action}


def test_spotlight_search_becomes_a_bounded_local_call() -> None:
    call = direct_tool_call(UserRequest(text="Busca en Spotlight Informe trimestral"))

    assert call is not None
    assert call.tool_name == "spotlight_search"
    assert call.arguments == {"query": "Informe trimestral", "limit": 10}


def test_spotlight_open_becomes_an_exact_confirmed_call() -> None:
    call = direct_tool_call(UserRequest(text="Abre con Spotlight Informe.pdf"))

    assert call is not None
    assert call.tool_name == "spotlight_open"
    assert call.arguments == {"query": "Informe.pdf"}


@pytest.mark.parametrize(
    "text",
    ["Qué tengo hoy", "Revisa mi calendario", "Lista mis eventos de hoy"],
)
def test_today_calendar_reads_use_the_local_day_window(text: str) -> None:
    current = datetime(2026, 8, 26, 14, 30, tzinfo=timezone(timedelta(hours=-5)))

    call = direct_tool_call(UserRequest(text=text), now=current)

    assert call is not None
    assert call.tool_name == "calendar_list_events"
    assert call.requested_by is AgentRole.PLANNER
    assert call.arguments == {
        "start_at": "2026-08-26T00:00:00-05:00",
        "end_at": "2026-08-27T00:00:00-05:00",
        "limit": 20,
    }


@pytest.mark.parametrize(
    "text",
    [
        "Tengo eventos hoy",
        "¿Tengo algún evento hoy?",
        "Tengo algo en el calendario hoy",
        "Do I have any calendar events today",
    ],
)
def test_today_calendar_status_uses_a_single_private_result(text: str) -> None:
    current = datetime(2026, 8, 26, 14, 30, tzinfo=timezone(timedelta(hours=-5)))

    call = direct_tool_call(UserRequest(text=text), now=current)

    assert call is not None
    assert call.tool_name == "calendar_list_events"
    assert call.requested_by is AgentRole.PLANNER
    assert call.arguments == {
        "start_at": "2026-08-26T00:00:00-05:00",
        "end_at": "2026-08-27T00:00:00-05:00",
        "limit": 1,
    }


@pytest.mark.parametrize(
    "text",
    [
        "Qué tengo mañana",
        "Revisa mi calendario de mañana",
        "Lista mis eventos de manana",
        "What is on my calendar tomorrow",
    ],
)
def test_tomorrow_calendar_reads_use_the_next_local_day_window(text: str) -> None:
    current = datetime(2026, 8, 26, 14, 30, tzinfo=timezone(timedelta(hours=-5)))

    call = direct_tool_call(UserRequest(text=text), now=current)

    assert call is not None
    assert call.tool_name == "calendar_list_events"
    assert call.requested_by is AgentRole.PLANNER
    assert call.arguments == {
        "start_at": "2026-08-27T00:00:00-05:00",
        "end_at": "2026-08-28T00:00:00-05:00",
        "limit": 20,
    }


@pytest.mark.parametrize(
    "text",
    [
        "Qué tengo pasado mañana",
        "Qué tengo el viernes",
        "Qué tengo mañana y abre Mail",
    ],
)
def test_other_calendar_ranges_keep_the_normal_planner(text: str) -> None:
    assert direct_tool_call(UserRequest(text=text)) is None


def test_tomorrow_calendar_rejects_a_naive_injected_timestamp() -> None:
    with pytest.raises(ValueError, match="timezone-aware"):
        direct_tool_call(
            UserRequest(text="Qué tengo mañana"),
            now=datetime(2026, 8, 26, 14, 30),
        )


def test_tomorrow_calendar_window_preserves_local_midnights_across_dst() -> None:
    current = datetime(2026, 3, 7, 14, 30, tzinfo=ZoneInfo("America/New_York"))

    call = direct_tool_call(UserRequest(text="Qué tengo mañana"), now=current)

    assert call is not None
    assert call.arguments["start_at"] == "2026-03-08T00:00:00-05:00"
    assert call.arguments["end_at"] == "2026-03-09T00:00:00-04:00"


@pytest.mark.parametrize(
    "text",
    [
        "Cuál es mi próximo evento",
        "Cual es mi proximo evento",
        "Qué sigue en mi calendario",
        "When is my next meeting",
    ],
)
def test_next_calendar_event_uses_a_single_bounded_local_read(text: str) -> None:
    current = datetime(2026, 8, 26, 14, 30, tzinfo=timezone(timedelta(hours=-5)))

    call = direct_tool_call(UserRequest(text=text), now=current)

    assert call is not None
    assert call.tool_name == "calendar_list_events"
    assert call.requested_by is AgentRole.PLANNER
    assert call.arguments == {
        "start_at": "2026-08-26T14:30:00-05:00",
        "end_at": "2026-09-26T14:30:00-05:00",
        "limit": 1,
    }


@pytest.mark.parametrize(
    "text",
    [
        "Cuáles son mis próximos eventos",
        "Cuál es mi próximo evento después del viernes",
        "Cuál es mi próximo evento y abre Mail",
    ],
)
def test_ambiguous_next_calendar_requests_keep_the_normal_planner(text: str) -> None:
    assert direct_tool_call(UserRequest(text=text)) is None


def test_next_calendar_event_rejects_a_naive_injected_timestamp() -> None:
    with pytest.raises(ValueError, match="timezone-aware"):
        direct_tool_call(
            UserRequest(text="Cuál es mi próximo evento"),
            now=datetime(2026, 8, 26, 14, 30),
        )


def test_next_calendar_window_remains_bounded_across_dst() -> None:
    current = datetime(2026, 10, 25, 14, 30, tzinfo=ZoneInfo("America/New_York"))

    call = direct_tool_call(UserRequest(text="Cuál es mi próximo evento"), now=current)

    assert call is not None
    assert call.arguments["start_at"] == "2026-10-25T14:30:00-04:00"
    assert call.arguments["end_at"] == "2026-11-25T13:30:00-05:00"


@pytest.mark.parametrize(
    "text",
    ["Describe este Mac", "¿Qué Mac tengo?", "What hardware does this Mac have"],
)
def test_runtime_questions_become_exact_local_reads(text: str) -> None:
    call = direct_tool_call(UserRequest(text=text))

    assert call is not None
    assert call.requested_by is AgentRole.PLANNER
    assert call.tool_name == "system_describe_runtime"
    assert call.arguments == {}


@pytest.mark.parametrize(
    "text",
    [
        "Estado de la batería",
        "¿Cuánta batería queda?",
        "Jarvis, cómo está la batería",
        "Battery status",
    ],
)
def test_power_questions_become_exact_local_reads(text: str) -> None:
    call = direct_tool_call(UserRequest(text=text))

    assert call is not None
    assert call.requested_by is AgentRole.PLANNER
    assert call.tool_name == "system_power_status"
    assert call.arguments == {}


@pytest.mark.parametrize(
    "text",
    [
        "¿Cuánto almacenamiento queda?",
        "Jarvis, cuánto espacio libre queda",
        "Estado del almacenamiento",
        "How much storage is left",
    ],
)
def test_storage_questions_become_exact_local_reads(text: str) -> None:
    call = direct_tool_call(UserRequest(text=text))

    assert call is not None
    assert call.requested_by is AgentRole.PLANNER
    assert call.tool_name == "system_storage_status"
    assert call.arguments == {}


@pytest.mark.parametrize(
    ("text", "domain"),
    [
        ("Estado del audio", "audio"),
        ("Jarvis, el Mac está silenciado", "audio"),
        ("Estado de la red", "network"),
        ("Tengo conexión de red", "network"),
        ("Estado del rendimiento", "performance"),
        ("System performance", "performance"),
    ],
)
def test_system_observation_questions_become_bounded_local_reads(
    text: str, domain: str
) -> None:
    call = direct_tool_call(UserRequest(text=text))

    assert call is not None
    assert call.requested_by is AgentRole.PLANNER
    assert call.tool_name == "system_observe_status"
    assert call.arguments == {"domain": domain}


def test_internet_question_does_not_claim_remote_connectivity_from_local_network() -> None:
    assert direct_tool_call(UserRequest(text="¿Estoy conectado a Internet?")) is None


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("¿Qué hora es?", "Son las 14:30."),
        ("Jarvis, dime la hora", "Son las 14:30."),
        ("¿Qué fecha es hoy?", "Hoy es miércoles 26 de agosto de 2026."),
        (
            "Dime la fecha y hora",
            "Hoy es miércoles 26 de agosto de 2026 y son las 14:30.",
        ),
        ("Cuál es mi zona horaria", "La zona horaria actual usa UTC-05:00."),
    ],
)
def test_clock_questions_return_exact_local_results(text: str, expected: str) -> None:
    current = datetime(2026, 8, 26, 14, 30, tzinfo=timezone(timedelta(hours=-5)))

    result = direct_local_response(UserRequest(text=text), now=current)

    assert result is not None
    assert result.role is AgentRole.SYNTHESIZER
    assert result.model_id == "local/deterministic-clock"
    assert result.content == expected


def test_timezone_supports_fractional_positive_offsets() -> None:
    current = datetime(
        2026,
        8,
        26,
        14,
        30,
        tzinfo=timezone(timedelta(hours=5, minutes=45)),
    )

    result = direct_local_response(UserRequest(text="En qué zona horaria estoy"), now=current)

    assert result is not None
    assert result.content == "La zona horaria actual usa UTC+05:45."


@pytest.mark.parametrize(
    ("text", "seconds", "expected"),
    [
        ("System uptime", 59, "Este Mac lleva encendido menos de un minuto."),
        ("Tiempo activo del Mac", 300, "Este Mac lleva encendido 5 minutos."),
        (
            "Cuánto tiempo lleva encendido este Mac",
            90_060,
            "Este Mac lleva encendido 1 día, 1 hora y 1 minuto.",
        ),
    ],
)
def test_uptime_questions_return_exact_local_results(
    text: str,
    seconds: float,
    expected: str,
) -> None:
    result = direct_local_response(UserRequest(text=text), uptime_seconds=seconds)

    assert result is not None
    assert result.role is AgentRole.SYNTHESIZER
    assert result.model_id == "local/deterministic-uptime"
    assert result.content == expected


@pytest.mark.parametrize("seconds", [-1, float("inf"), True])
def test_uptime_rejects_invalid_clock_values(seconds: float) -> None:
    with pytest.raises(ValueError, match="uptime"):
        direct_local_response(
            UserRequest(text="System uptime"),
            uptime_seconds=seconds,
        )


def test_uptime_clock_failure_stays_local(monkeypatch: pytest.MonkeyPatch) -> None:
    def fail(clock_id: int) -> float:
        del clock_id
        raise OSError("clock unavailable")

    monkeypatch.setattr(
        "aegis_core.orchestration.direct_actions.time.clock_gettime",
        fail,
    )

    result = direct_local_response(UserRequest(text="System uptime"))

    assert result is not None
    assert result.model_id == "local/deterministic-uptime"
    assert result.content == "No pude consultar el tiempo activo de este Mac."


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Cuánto es 25 más 17", "El resultado es 42."),
        ("Jarvis, calcula 12,5 por 4", "El resultado es 50."),
        ("What is -9 / 4", "El resultado es -2,25."),
        (
            "Calcula 1 dividido entre 3",
            "El resultado es aproximadamente 0,3333333333.",
        ),
        ("Calculate 2 x -3", "El resultado es -6."),
        ("Calcula .5 + .25", "El resultado es 0,75."),
        ("Calcula 1 / 0", "No puedo dividir entre cero."),
        ("Cuánto es doce por cuatro", "El resultado es 48."),
        ("Calcula cincuenta y nueve menos nueve", "El resultado es 50."),
        ("What is twenty-one plus four", "El resultado es 25."),
        ("Calculate negative nine times four", "El resultado es -36."),
        ("Calcula menos nueve por cuatro", "El resultado es -36."),
        ("Calcula cien dividido entre cuatro", "El resultado es 25."),
    ],
)
def test_binary_calculations_return_exact_local_results(text: str, expected: str) -> None:
    result = direct_local_response(UserRequest(text=text))

    assert result is not None
    assert result.role is AgentRole.SYNTHESIZER
    assert result.model_id == "local/deterministic-calculator"
    assert result.content == expected


@pytest.mark.parametrize(
    "text",
    [
        "Calcula 2 + 3 + 4",
        "Calcula 2 al cuadrado",
        "Calcula 1e3 + 2",
        "Calcula 1234567890123456789 + 1",
        "Calcula 0,000001 / 999999999999999999",
        "Calcula ciento uno más dos",
        "Calcula uno coma cinco por dos",
        "Calcula varios más dos",
    ],
)
def test_complex_or_out_of_bounds_calculations_use_the_normal_brain(text: str) -> None:
    assert direct_local_response(UserRequest(text=text)) is None


def test_clock_rejects_a_naive_injected_timestamp() -> None:
    with pytest.raises(ValueError, match="timezone-aware"):
        direct_local_response(
            UserRequest(text="Qué hora es"),
            now=datetime(2026, 8, 26, 14, 30),
        )


@pytest.mark.parametrize(
    "text",
    [
        "Qué hora es en Tokio",
        "Programa una hora",
        "Cuál es la fecha de lanzamiento",
        "Tiempo activo del servidor",
    ],
)
def test_nonlocal_clock_questions_do_not_use_the_direct_response(text: str) -> None:
    assert direct_local_response(UserRequest(text=text)) is None


@pytest.mark.parametrize(
    ("text", "query"),
    [
        ("Busca noticias de NVIDIA NIM", "noticias de NVIDIA NIM"),
        ("Investiga seguridad en Apple Silicon", "seguridad en Apple Silicon"),
        ("Search for current Swift releases", "current Swift releases"),
    ],
)
def test_web_research_preserves_the_explicit_query(text: str, query: str) -> None:
    call = direct_tool_call(UserRequest(text=text))

    assert call is not None
    assert call.tool_name == "web_research"
    assert call.arguments == {"query": query, "max_results": 3}


def test_explicit_public_page_read_becomes_a_bounded_fetch() -> None:
    call = direct_tool_call(UserRequest(text="Lee https://example.com/report"))

    assert call is not None
    assert call.tool_name == "web_fetch"
    assert call.arguments == {
        "url": "https://example.com/report",
        "max_characters": 8_000,
    }


def test_explicit_https_open_still_becomes_a_confirmed_browser_action() -> None:
    call = direct_tool_call(UserRequest(text="Abre https://example.com/report"))

    assert call is not None
    assert call.tool_name == "browser_open_url"
    assert call.arguments == {"url": "https://example.com/report"}


@pytest.mark.parametrize(
    ("text", "path"),
    [
        ("Lee el archivo README.md", "README.md"),
        ("Lee archivo docs/architecture/ADR-0093.md", "docs/architecture/ADR-0093.md"),
        ("Read file notes/daily brief.txt", "notes/daily brief.txt"),
    ],
)
def test_explicit_workspace_file_read_is_bounded(text: str, path: str) -> None:
    call = direct_tool_call(UserRequest(text=text))

    assert call is not None
    assert call.requested_by is AgentRole.CODE_SECURITY
    assert call.tool_name == "filesystem_read_text"
    assert call.arguments == {"path": path, "max_bytes": 8_192}


@pytest.mark.parametrize(
    "text",
    [
        "Cuéntame sobre Safari",
        "No abras Safari",
        "Abre Safari y envía un correo",
        "Abre Ajustes del Sistema",
        "Ejecuta un comando en Terminal",
        "Ejecuta el atajo",
        "Ejecuta el atajo ..",
        "Ejecuta el atajo ../peligroso",
        "Analiza la postura de seguridad",
        "Revisa mi correo reciente",
        "Lee http://example.com/report",
        "Abre http://example.com/report",
        "Lee el archivo ../secrets.txt",
        "Lee el archivo /etc/passwd",
        "Lee el archivo",
        "Cuánto almacenamiento queda en el servidor",
        "Cuántos correos no leídos tengo",
        "Tengo correos no leídos y abre Mail",
        "Cuál es mi último correo y abre Mail",
        "Cuántos eventos tengo hoy",
        "Tengo eventos hoy y abre Calendario",
    ],
)
def test_ambiguous_or_unsupported_commands_stay_out_of_the_direct_path(text: str) -> None:
    assert direct_tool_call(UserRequest(text=text)) is None


def test_force_remote_disables_the_direct_path() -> None:
    request = UserRequest(text="Abre Safari", metadata={"force_remote": True})

    assert direct_tool_call(request) is None

    clock_request = UserRequest(text="Qué hora es", metadata={"force_remote": True})
    assert direct_local_response(clock_request) is None

    calculator_request = UserRequest(text="Calcula 2 + 2", metadata={"force_remote": True})
    assert direct_local_response(calculator_request) is None


def test_audio_attachment_without_local_transcription_disables_the_direct_path() -> None:
    request = UserRequest(
        text="Abre Safari",
        modalities=frozenset({InputModality.TEXT, InputModality.AUDIO}),
    )

    assert direct_tool_call(request) is None


def test_local_voice_transcript_can_use_the_direct_path() -> None:
    request = UserRequest(
        text="Abre Safari",
        modalities=frozenset({InputModality.TEXT, InputModality.AUDIO}),
        metadata={"speech_on_device": True},
    )

    assert direct_tool_call(request) is not None

    clock_request = UserRequest(
        text="Qué hora es",
        modalities=frozenset({InputModality.TEXT, InputModality.AUDIO}),
        metadata={"speech_on_device": True},
    )
    assert direct_local_response(clock_request) is not None

    calculator_request = UserRequest(
        text="Calcula 2 + 2",
        modalities=frozenset({InputModality.TEXT, InputModality.AUDIO}),
        metadata={"speech_on_device": True},
    )
    assert direct_local_response(calculator_request) is not None
