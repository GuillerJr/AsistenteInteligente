from __future__ import annotations

import os
import shutil
import textwrap
from pathlib import Path
from typing import TextIO

_CLI_ERROR_MESSAGES = {
    "model_context_limit": "La petición y su contexto superan la capacidad del modelo local.",
    "workspace_unavailable": (
        "Esa carpeta no existe o no se puede leer. Conservé el proyecto actual."
    ),
    "invalid_input_encoding": "La entrada contiene bytes que no son texto UTF-8 válido.",
    "brain_unavailable": ("El cerebro local no respondió y no existe una ruta segura disponible."),
    "remote_provider_unavailable": (
        "NVIDIA no pudo completar la respuesta. La sesión sigue activa."
    ),
    "remote_rate_limited": "NVIDIA alcanzó su límite de solicitudes o cuota.",
    "remote_authentication_failed": "NVIDIA no aceptó la credencial o no está disponible.",
    "swarm_execution_failed": (
        "El motor no pudo completar esta solicitud. La sesión sigue activa; "
        "puedes intentarlo de nuevo."
    ),
    "swarm_execution_timeout": "La operación superó el tiempo seguro de ejecución.",
    "conversation_unavailable": "La memoria de conversación no está disponible.",
    "empty_agent_response": "El modelo terminó sin producir una respuesta válida.",
    "incomplete_model_response": (
        "El modelo dejó la respuesta incompleta. No se guardó como un turno completado."
    ),
    "security_compromised": (
        "Jarvis bloqueó la ejecución porque la integridad de seguridad no pudo verificarse."
    ),
    "tcc_permission_denied": (
        "macOS retiró un permiso necesario y Jarvis detuvo la acción sin reintentar."
    ),
    "job_status_unavailable": "No se pudo verificar el estado final de la tarea.",
}
_CLI_ERROR_RECOVERY = {
    "model_context_limit": (
        "Usa /new para liberar contexto o divide el texto. No se truncó tu petición."
    ),
    "workspace_unavailable": 'Usa /workspace "subcarpeta" dentro de la raíz autorizada.',
    "invalid_input_encoding": "Convierte el archivo a UTF-8 antes de enviarlo al CLI.",
    "brain_unavailable": "Comprueba Apple Intelligence y vuelve a ejecutar /status.",
    "remote_provider_unavailable": (
        "Revisa la conexión y la disponibilidad del endpoint. No se cambió de modelo localmente."
    ),
    "remote_rate_limited": "Espera al menos 5 segundos y revisa la cuota antes de reintentar.",
    "remote_authentication_failed": (
        "Revisa la clave NVIDIA en el Llavero; no la pegues en el chat."
    ),
    "swarm_execution_failed": (
        "Repite una vez; si persiste, ejecuta ./script/jarvis_beta.sh check."
    ),
    "swarm_execution_timeout": (
        "Divide la solicitud en una tarea más corta o usa /inference local_only."
    ),
    "conversation_unavailable": "Inicia un contexto limpio con /new.",
    "empty_agent_response": "Reformula la instrucción con un resultado esperado concreto.",
    "incomplete_model_response": (
        "El fragmento visible puede estar cortado. Pide una respuesta más acotada; "
        "la sesión conserva el último turno completo."
    ),
    "security_compromised": (
        "No continúes: ejecuta ./script/jarvis_beta.sh check y revisa la auditoría."
    ),
    "tcc_permission_denied": "Reactiva el permiso correspondiente en Ajustes del Sistema.",
    "job_status_unavailable": "Verifica el daemon con ./script/jarvis_beta.sh check.",
}

_CLI_ERROR_MESSAGES.update(
    {
        "ipc_unavailable": "Se perdió la conexión con Jarvis. No reenvié la solicitud.",
        "ipc_invalid_response": "La respuesta IPC no pudo verificarse. Detuve el seguimiento.",
        "invalid_engineering_request": "La solicitud o el proyecto no son válidos.",
        "secret_material_rejected": (
            "La solicitud parece contener una credencial. No se envió al modelo."
        ),
        "job_not_found": "La tarea ya no está disponible en el daemon.",
        "job_pending": "Hay una tarea sin resolver. No iniciaré otra encima.",
        "invalid_command": "El comando o sus argumentos no son válidos.",
        "cancel_unverified": "No pude verificar la cancelación. La tarea podría seguir activa.",
        "engineering_preflight_invalid": "El daemon anunció un proyecto no válido.",
        "input_too_large": "La instrucción supera el límite de 50 000 caracteres.",
    }
)
_CLI_ERROR_RECOVERY.update(
    {
        "ipc_unavailable": (
            "Abre Jarvis y consulta /status; usa /resume si hay una tarea pendiente."
        ),
        "ipc_invalid_response": (
            "Revisa /status y el HUD. No repitas una acción con efecto sin verificarla."
        ),
        "invalid_engineering_request": (
            "Usa /workspace con una carpeta autorizada e incluye una instrucción."
        ),
        "secret_material_rejected": "Sustituye contraseñas y claves por nombres de variables.",
        "job_not_found": "Revisa el resultado en el proyecto antes de iniciar otra tarea.",
        "job_pending": "Usa /resume para seguirla o /cancel para detenerla.",
        "invalid_command": "Usa /help o Tab para consultar las opciones.",
        "cancel_unverified": "Reconecta y usa /cancel; comprueba también el HUD.",
        "engineering_preflight_invalid": "Abre Jarvis y comprueba la carpeta autorizada.",
        "input_too_large": "Reduce la instrucción o divídela en pasos.",
    }
)


_BIDI_CONTROLS = frozenset({*range(0x202A, 0x202F), *range(0x2066, 0x206A)})


def safe_terminal_text(value: str) -> str:
    """Model/file text is data: never let it drive ANSI, OSC52 or bidi rendering."""
    return "".join(
        character
        if (character in "\n\t" or ord(character) >= 32)
        and not (127 <= ord(character) <= 159)
        and ord(character) not in _BIDI_CONTROLS
        else f"\\u{ord(character):04x}"
        for character in value
    )


def is_terminal(stream: TextIO) -> bool:
    try:
        return stream.isatty()
    except (AttributeError, OSError):
        return False


class EngineeringTerminal:
    """Output only. No IPC, tool execution, prompts on disk or inferred progress."""

    def __init__(self, stdout: TextIO, stderr: TextIO, *, interactive: bool) -> None:
        self.stdout = stdout
        self.stderr = stderr
        self.interactive = interactive
        self.ansi = interactive and is_terminal(stderr) and os.environ.get("TERM") != "dumb"
        self.color = self.ansi and "NO_COLOR" not in os.environ
        self.activity_visible = False

    def paint(self, text: str, color: str = "36") -> str:
        text = safe_terminal_text(text)
        return f"\033[{color}m{text}\033[0m" if self.color else text

    @property
    def width(self) -> int:
        try:
            columns = os.get_terminal_size(self.stderr.fileno()).columns
        except (AttributeError, OSError, ValueError):
            columns = shutil.get_terminal_size(fallback=(80, 24)).columns
        return min(88, max(1, columns - 2))

    def notice(self, text: str = "", *, color: str = "2") -> None:
        self.end_activity()
        for line in safe_terminal_text(text).split("\n"):
            for part in textwrap.wrap(line, width=self.width, replace_whitespace=False) or [""]:
                self.stderr.write(self.paint(part, color) + "\n")
        self.stderr.flush()

    def banner(self, workspace: Path, domain: str, research: str, inference: str) -> None:
        self.notice()
        self.notice("◆ J A R V I S  ·  ENGINEERING CLI", color="1;36")
        self.notice("─" * self.width)
        self.notice(f"Proyecto  {workspace.name}  ·  {domain}", color="1")
        self.notice(f"Carpeta   {workspace}")
        self.notice(f"Consulta web  {research}  ·  Inferencia  {inference}")
        self.notice("Repositorio: lectura autorizada; escritura y shell libre no habilitados.")
        if inference == "hybrid":
            self.notice("Hybrid permite enviar texto a especialistas remotos.", color="33")
        elif inference == "nvidia_only":
            self.notice("NVIDIA: consultas, historial de esta sesión y código leído van a la API.",
                        color="33")
            self.notice("Sin inferencia local ni fallback local; requiere conexión y cuota.")
        self.notice("─" * self.width)
        self.notice("Enter enviar · Alt+Enter nueva línea · Tab comandos · ↑ historial")
        self.notice("Ctrl-C cancelar tarea/borrador · Ctrl-D salir · /help ayuda")
        self.notice()

    def result(self, text: str) -> None:
        self.end_activity()
        self.stdout.write(safe_terminal_text(text))
        self.stdout.flush()

    def response_heading(self) -> None:
        if self.interactive:
            self.notice("JARVIS", color="1;36")

    def activity(self, label: str) -> None:
        if not self.interactive:
            return
        self.end_activity()
        text = f"◇ {label} · Ctrl-C cancela"
        if self.ansi:
            self.stderr.write(self.paint(text[: self.width]))
            self.stderr.flush()
            self.activity_visible = True
        else:
            self.notice(text)

    def end_activity(self) -> None:
        if self.activity_visible:
            self.stderr.write("\r\033[2K")
            self.stderr.flush()
            self.activity_visible = False

    def error(self, code: str) -> None:
        self.notice()
        self.notice(
            "✕ " + _CLI_ERROR_MESSAGES.get(code, "No pude completar la operación."), color="1;31"
        )
        self.notice(_CLI_ERROR_RECOVERY.get(code, "Consulta /status y /help."), color="33")
        # Only fixed, known error codes reach the terminal. Never exception text.
        self.notice("Código: " + (code if code in _CLI_ERROR_MESSAGES else "operation_failed"))

    def clear(self) -> None:
        self.end_activity()
        if self.ansi:
            self.stderr.write("\033[2J\033[H")
            self.stderr.flush()
