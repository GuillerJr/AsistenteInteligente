"""User-facing failures selected from public codes, never exception text."""

import re


def speech_content(text: str) -> str:
    """Read prose, not Markdown syntax or long tracking links, in the current voice."""
    text = re.sub(r"\[([^\]\n]+)\]\(https?://[^\s)]+\)", r"\1", text)
    text = re.sub(r"https?://[^\s<>()]+", "", text)
    text = re.sub(r"(?m)^#{1,6}\s+", "", text)
    return " ".join(text.replace("**", "").replace("`", "").split())


def failure_message(code: str | None) -> str:
    return {
        "remote_provider_unavailable": (
            "NVIDIA no pudo responder a esta solicitud. Vuelve a intentarlo en unos instantes."
        ),
        "remote_rate_limited": "NVIDIA pidió una pausa por su límite de uso. Inténtalo más tarde.",
        "remote_authentication_failed": (
            "NVIDIA rechazó la credencial. Revisa su clave en Configuración de Jarvis."
        ),
        "brain_unavailable": "La conexión con NVIDIA no está disponible. Inténtalo más tarde.",
        "swarm_execution_timeout": (
            "La solicitud superó el tiempo de espera. Comprueba si hubo cambios antes de repetirla."
        ),
        "model_context_limit": (
            "La conversación es demasiado larga. Usa /nuevo y repite tu solicitud."
        ),
        "conversation_capacity_reached": (
            "Esta conversación alcanzó su límite. Usa /nuevo para continuar."
        ),
        "conversation_not_found": "Esta conversación ya no existe. Usa /nuevo para continuar.",
        "conversation_unavailable": "No pude acceder al historial. Revisa el servicio de Jarvis.",
        "incomplete_model_response": (
            "NVIDIA devolvió una respuesta incompleta. "
            "Comprueba si hubo cambios antes de repetirla."
        ),
        "empty_agent_response": "NVIDIA devolvió una respuesta vacía. Inténtalo nuevamente.",
        "confirmation_expired": "La aprobación caducó. Envía de nuevo tu solicitud para revisarla.",
        "confirmation_consumption_failed": (
            "No pude validar la aprobación. Envía de nuevo tu solicitud para revisarla."
        ),
        "approved_tool_execution_failed": (
            "La herramienta falló después de la aprobación. "
            "Comprueba si hubo cambios antes de repetir la acción."
        ),
        "browser_task_chrome_connection_failed": (
            "No pude conectar a Chrome. Comprueba que la depuración remota esté habilitada "
            "y que hayas autorizado la conexión en Chrome. Después envía una nueva tarea."
        ),
        "runtime_suspended": "Jarvis está pausado por el estado térmico del Mac.",
        "security_compromised": (
            "Jarvis bloqueó la solicitud por un problema de integridad del servicio."
        ),
        "owner_verification_required": (
            "Esta acción necesita verificar tu identidad de voz en el Mac. "
            "La vinculación de Telegram no sustituye esa verificación."
        ),
        "voice_conversation_owner_required": (
            "Esta acción necesita verificar tu identidad de voz en el Mac."
        ),
    }.get(
        code, "No pude completar la tarea. Si se repite, revisa el diagnóstico de Jarvis en tu Mac."
    )
