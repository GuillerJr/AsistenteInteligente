"""Local text classification only; it cannot execute tools or grant permissions."""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from aegis_core.memory.contracts import ConversationTurn

_QUOTED = re.compile(r'''```[\s\S]*?```|`[^`]*`|"[^"\n]*"|'[^'\n]*'|«[^»]*»|“[^”]*”''')
_PREFIX = (
    r"^(?:jarvis[ ,:¿¡]+|hola(?: jarvis)?[ ,:¿¡]+)?"
    r"(?:(?:me )?(?:puedes|podrias) |(?:quiero|necesito) (?:que )?|can you |could you )?"
)
_READ = re.compile(
    _PREFIX + r"(?:ver|ve|revisar|revisa|consultar|consulta|listar|lista|mostrar|"
    r"mostrarme|muestra|muestrame|revisame|mira|ensename|leer|lee|dime|decirme|"
    r"buscar|busca|buscame|encontrar|encuentra|"
    r"check|show|list|read|search|find)\b"
)
_WRITE = re.compile(
    _PREFIX + r"(?:crea|crear|agrega|agregar|programa|programar|redacta|redactar|"
    r"prepara|preparar|actualiza|actualizar|modifica|modificar|create|draft|update)\b"
)


def folded_request(text: str) -> str:
    return " ".join(
        "".join(c for c in unicodedata.normalize("NFKD", text.casefold())
                if not unicodedata.combining(c)).strip(" ¿¡.!?").split()
    )


def mask_quoted_text(text: str) -> str:
    """Keep offsets while removing examples/data from instruction classification."""
    return _QUOTED.sub(lambda match: " " * len(match[0]), text)


def google_request_tools(text: str) -> frozenset[str]:
    """Activate only the requested domain, never arbitrary mentions in quoted data."""
    command = folded_request(mask_quoted_text(text))
    if re.search(r"\b(?:tutorial|documentacion|manual|ejemplo) (?:sobre|de|para)\b", command):
        return frozenset()
    if re.search(r"\b(?:apple mail|correo de apple|calendario de apple|apple calendar)\b", command):
        return frozenset()
    read = bool(_READ.match(command)) or bool(re.match(
        r"^(?:que tengo|que (?:correos|emails|eventos|reuniones) (?:tengo|hay)|"
        r"tengo (?:algun |alguna )?(?:correo|evento|reunion)|"
        r"hay correos|cual (?:es|fue) mi (?:ultimo correo|proximo evento))\b", command
    )) or bool(re.match(
        _PREFIX + r"cuales? (?:son|es|fueron|fue) (?:mis?|los?|las?) "
        r"(?:(?:ultimos?|proximos?|primeros?|siguientes?|recientes?) |[0-9]{1,3} )*"
        r"(?:correos?|emails?|eventos?|reuniones?|archivos?|documentos?)\b", command
    ))
    write = bool(_WRITE.match(command))
    if not (read or write):
        return frozenset()
    if write:
        # Subject/body/title values are payload, not additional service requests.
        command = re.split(r"\bcon (?:el )?(?:asunto|cuerpo|titulo|contenido)\b",
                           command, maxsplit=1)[0]
    names: set[str] = set()
    if re.search(r"\b(?:correo|correos|email|emails|gmail|mail)\b", command):
        names.update({"search_gmail_messages", "get_gmail_message_content",
                      "get_gmail_messages_content_batch"}
                     if read else {"draft_gmail_message"})
    if (re.search(r"\b(?:agenda|calendario|calendar|evento|eventos|reunion|reuniones)\b", command)
            or command in {"que tengo hoy", "que tengo manana"}):
        names.update({"get_events", "list_calendars"} if read else {"manage_event"})
    if re.search(r"\bdrive\b", command):
        names.update({"search_drive_files", "get_drive_file_content", "list_drive_items"}
                     if read else {"create_drive_file", "create_drive_folder", "update_drive_file"})
    return frozenset(names)


def google_read_entrypoint(text: str) -> str | None:
    """Narrow a mailbox listing to discovery, never execute or authorize it.

    Reading an identified message is handled by the task-reference resolver. A list
    request first needs search results, not a content tool with an invented ID.
    """
    if google_request_tools(text) != frozenset({
        "search_gmail_messages", "get_gmail_message_content", "get_gmail_messages_content_batch",
    }):
        return None
    command = folded_request(mask_quoted_text(text))
    if re.search(r"\b(?:id|identificador|message_id|contenido|cuerpo|completo|completos)\b",
                 command):
        return None
    if re.search(r"\b(?:correos|emails)\b", command) or re.search(
        r"\b(?:revisa|revisar|consulta|consultar|lista|listar|busca|buscar|check|list|search)\b",
        command,
    ):
        return "search_gmail_messages"
    return None


def google_followup_text(text: str, history: Sequence[ConversationTurn]) -> str | None:
    """Recover only a recent, adjacent user read request behind an unanswered offer.

    This selects schemas only; assistant text can never authorize execution or writes.
    """
    from aegis_core.memory.contracts import ConversationRole

    if folded_request(text) not in {"si", "si por favor", "hazlo", "adelante", "yes", "yes please"}:
        return None
    if len(history) < 2:
        return None
    user, assistant = history[-2:]
    if (user.role is not ConversationRole.USER or assistant.role is not ConversationRole.ASSISTANT
            or user.conversation_id != assistant.conversation_id
            or user.sequence + 1 != assistant.sequence
            or not timedelta(0) <= datetime.now(UTC) - assistant.created_at
            <= timedelta(minutes=5)):
        return None
    offer = folded_request(assistant.content)
    if "?" not in assistant.content or not re.search(
        r"\b(?:quieres|deseas|shall i|would you like)\b.*\b"
        r"(?:busque|revise|consulte|muestre|liste|buscar|revisar|ver|search|check)\b", offer
    ):
        return None
    names = google_request_tools(user.content)
    if names and names <= {
        "search_gmail_messages", "get_gmail_message_content", "get_gmail_messages_content_batch",
        "get_events", "list_calendars",
        "search_drive_files", "get_drive_file_content", "list_drive_items",
    }:
        return user.content
    return None
