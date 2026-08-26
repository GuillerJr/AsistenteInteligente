import pytest

from aegis_core.contracts import AgentRole, InputModality, UserRequest
from aegis_core.orchestration.direct_actions import direct_tool_call


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
    ],
)
def test_ambiguous_or_unsupported_commands_stay_out_of_the_direct_path(text: str) -> None:
    assert direct_tool_call(UserRequest(text=text)) is None


def test_force_remote_disables_the_direct_path() -> None:
    request = UserRequest(text="Abre Safari", metadata={"force_remote": True})

    assert direct_tool_call(request) is None


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
