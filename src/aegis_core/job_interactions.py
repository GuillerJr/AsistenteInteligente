from __future__ import annotations

import json
import re
from datetime import datetime, timedelta

from aegis_core.contracts import ToolAuthorization
from aegis_core.dialogue import DialogueMode
from aegis_core.job_contracts import ActionPreview, ActionPreviewField, InputRequest, JobEvaluation
from aegis_core.secrets import contains_likely_secret_material

_LABELS = {
    "recipients": "Destinatarios",
    "subject": "Asunto",
    "body": "Contenido",
    "body_text": "Contenido",
    "title": "Título",
    "start_at": "Inicio",
    "end_at": "Fin",
    "start_time": "Inicio",
    "end_time": "Fin",
    "calendar_id": "Calendario",
    "description": "Descripción",
    "location": "Lugar",
    "due_at": "Vencimiento",
    "list_name": "Lista",
    "first_name": "Nombre",
    "last_name": "Apellido",
    "email": "Correo",
    "phone": "Teléfono",
    "url": "Dirección",
    "query": "Búsqueda",
    "browser": "Navegador",
    "mode": "Tipo de navegador",
    "bundle_identifier": "Aplicación",
    "name": "Nombre",
    "objective": "Objetivo",
    "application_bundle_identifier": "Aplicación",
    "max_steps": "Máximo de pasos",
    "target": "Destino",
    "ports": "Puertos",
    "template": "Diagnóstico",
    "volume_percent": "Volumen (%)",
    "muted": "Silencio",
    "action": "Acción",
    "user_google_email": "Cuenta de Google",
    "file_id": "Archivo",
    "content": "Contenido",
    "event_id": "Evento",
    "mime_type": "Formato",
    "summary": "Título",
    "text": "Texto",
    "to": "Destinatarios",
    "cc": "Copia",
    "bcc": "Copia oculta",
    "folder_id": "Carpeta",
    "timezone": "Zona horaria",
}


def confirmation_preview(authorization: ToolAuthorization) -> ActionPreview:
    """Local, bounded view of the exact normalized arguments; never an AI summary."""
    fields = []
    priority = {
        key: index
        for index, key in enumerate(
            (
                "url",
                "objective",
                "mode",
                "user_google_email",
                "recipients",
                "to",
                "cc",
                "bcc",
                "subject",
                "title",
                "summary",
                "calendar_id",
                "start_at",
                "start_time",
                "end_at",
                "end_time",
                "timezone",
                "location",
            )
        )
    }
    for key, value in sorted(
        authorization.normalized_arguments.items(),
        key=lambda item: (priority.get(item[0], 99), item[0]),
    ):
        if authorization.tool_name == "browser_task_open" and key == "mode":
            value = {
                None: "Navegador configurado",
                "extension": "Tu navegador",
                "isolated": "Navegador separado",
                "chrome": "Chrome directo (conexión CDP)",
            }.get(value, value)
        rendered = (
            value
            if isinstance(value, str)
            else json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False)
        )
        if contains_likely_secret_material(rendered):
            raise ValueError("credential material cannot be shown in an action preview")
        fields.append(
            ActionPreviewField(
                label=_LABELS.get(key, key.replace("_", " ")),
                value=rendered or "(vacío)",
            )
        )
    return ActionPreview(fields=tuple(fields))


_OPTIONS = re.compile(
    r"(?s)^(?P<body>.*?\?)\s*\n\s*\**(?:Opciones|Options)\**\s*:\**[ \t]*\n"
    r"(?P<options>(?:[ \t]*(?:[0-9]{1,2}[.)]|[-•*])[ \t]+[^\n]+(?:\n|$)){2,6})\s*$"
)


def question_with_options(result: str) -> tuple[str, tuple[str, ...]] | None:
    """A reply ending in a question plus an "Opciones:" list, as the dialogue asks for."""
    match = _OPTIONS.fullmatch(result.strip())
    if match is None:
        return None
    options = tuple(dict.fromkeys(
        re.sub(r"^[ \t]*(?:[0-9]{1,2}[.)]|[-•*])[ \t]+", "", line).strip().strip("*").strip()
        for line in match["options"].splitlines() if line.strip()
    ))
    body = match["body"].strip()
    question = body[-1_000:] if len(body) > 1_000 else body
    if len(options) < 2 or any(not 1 <= len(option) <= 80 for option in options):
        return None
    return question, options


def task_input_request(
    *,
    request_text: str,
    result: str | None,
    evaluation: JobEvaluation | None,
    now: datetime,
) -> InputRequest | None:
    # Only a short, explicit clarification for a task that executed no tool.
    # Social follow-ups and arbitrary questions inside tool results never open a form.
    if (
        result is None
        or evaluation is None
        or evaluation.tool_name is not None
        or not evaluation.succeeded
        or len(request_text) > 4_096
        or len(request_text.encode("utf-8")) > 8_192
        or contains_likely_secret_material(request_text)
        or contains_likely_secret_material(result)
    ):
        return None
    offered = question_with_options(result)
    if offered is not None:
        question_text, options = offered
        return InputRequest(question=question_text, request_text=request_text,
                            expires_at=now + timedelta(minutes=5), options=options)
    if evaluation.dialogue_mode not in {DialogueMode.TASK, DialogueMode.REPAIR}:
        return None
    question = re.fullmatch(r"\s*(?:[^¿?]{0,300}[.:]\s*)?(¿[^¿?]{2,998}\?)\s*", result)
    if question is None:
        return None
    text = question.group(1)
    if not re.match(
        r"¿(?:A qué|Qué|Cuál|Cuáles|Cuándo|Dónde|Cómo|Para quién|A quién|Con quién"
        r"|Cuánto|Cuántos|En qué)\b",
        text,
        re.IGNORECASE,
    ):
        return None
    return InputRequest(
        question=text, request_text=request_text, expires_at=now + timedelta(minutes=5)
    )
