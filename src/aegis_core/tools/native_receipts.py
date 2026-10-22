"""Bounded acknowledgements of native commands, never evidence of external outcomes."""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from typing import NoReturn
from urllib.parse import urlsplit

from aegis_core.contracts import ToolExecutionResult

NATIVE_RECEIPT_SCOPE = "native_command_acknowledgement"
SMART_TV_RECEIPT_SCOPE = "smart_tv_command_acknowledgement"
SMART_TV_RECEIPT_STATES = frozenset({"true", "key_sent", "command_sent", "wake_packet_sent"})
NATIVE_RECEIPT_SOURCES = {
    "mail_send_message": "apple_mail",
    "calendar_create_event": "apple_calendar",
    "reminder_create": "apple_reminders",
    "reminder_complete": "apple_reminders",
    "contact_create": "apple_contacts",
    "application_open": "macos_open",
    "browser_open_url": "macos_open",
    "browser_search": "macos_open",
    "shortcut_run": "macos_shortcuts",
    "media_control": "native_media_application",
}
_BROWSERS = {
    "default": "el navegador predeterminado", "safari": "Safari", "chrome": "Chrome",
    "arc": "Arc", "firefox": "Firefox",
}
_MEDIA_ACTIONS = {"play_pause", "next", "previous"}


def _label(value: object, maximum: int) -> bool:
    return (isinstance(value, str) and bool(value.strip()) and len(value) <= maximum
            and value.isprintable())


def native_receipt_is_valid(
    tool_name: str,
    payload: object,
    arguments: Mapping[str, object] | None = None,
) -> bool:
    """Validate exact receipt fields and, when available, the authorized command.

    Contacts chooses the display-name order itself, so its label is not an identity
    proof. None of these receipts contains a readback of the final external state.
    """
    if tool_name not in NATIVE_RECEIPT_SOURCES or not isinstance(payload, dict):
        return False
    expected: dict[str, object] = {}
    if tool_name == "mail_send_message":
        valid = (set(payload) == {"sent", "recipient_count"} and payload["sent"] is True
                 and type(payload["recipient_count"]) is int
                 and 1 <= payload["recipient_count"] <= 10)
        if arguments is not None:
            recipients = arguments.get("recipients")
            if not isinstance(recipients, list) or not 1 <= len(recipients) <= 10:
                return False
            expected["recipient_count"] = len(recipients)
    elif tool_name in {"calendar_create_event", "reminder_create", "reminder_complete"}:
        field = "calendar" if tool_name == "calendar_create_event" else "list"
        flag = "completed" if tool_name == "reminder_complete" else "created"
        valid = (set(payload) == {flag, field, "title"} and payload[flag] is True
                 and _label(payload[field], 200 if field == "calendar" else 128)
                 and _label(payload["title"], 300))
        if arguments is not None:
            expected["title"] = arguments.get("title")
            requested_container = arguments.get(f"{field}_name")
            if requested_container is not None:
                expected[field] = requested_container
    elif tool_name == "contact_create":
        valid = (set(payload) == {"created", "name"} and payload["created"] is True
                 and _label(payload["name"], 300))
    elif tool_name == "application_open":
        bundle = payload.get("bundle_identifier")
        valid = (set(payload) == {"opened", "bundle_identifier"} and payload["opened"] is True
                 and isinstance(bundle, str)
                 and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9.-]{1,253}[A-Za-z0-9]", bundle)
                 is not None)
        if arguments is not None:
            expected["bundle_identifier"] = arguments.get("bundle_identifier")
    elif tool_name == "browser_open_url":
        url = payload.get("url")
        valid = (set(payload) == {"opened", "url"} and payload["opened"] is True
                 and _label(url, 2_048))
        if valid:
            try:
                parsed = urlsplit(url)
                valid = (parsed.scheme == "https" and bool(parsed.hostname)
                         and parsed.username is None and parsed.password is None)
            except ValueError:
                valid = False
        if arguments is not None:
            expected["url"] = arguments.get("url")
    elif tool_name == "browser_search":
        valid = (set(payload) == {"opened", "browser", "query"} and payload["opened"] is True
                 and isinstance(payload["browser"], str) and payload["browser"] in _BROWSERS
                 and _label(payload["query"], 300))
        if arguments is not None:
            expected = {"query": arguments.get("query"),
                        "browser": arguments.get("browser", "default")}
    elif tool_name == "shortcut_run":
        valid = (set(payload) == {"completed", "name"} and payload["completed"] is True
                 and _label(payload["name"], 128))
        if arguments is not None:
            expected["name"] = arguments.get("name")
    else:  # media_control
        valid = (set(payload) == {"action", "bundle_identifier"}
                 and isinstance(payload["action"], str) and payload["action"] in _MEDIA_ACTIONS
                 and isinstance(payload["bundle_identifier"], str)
                 and payload["bundle_identifier"] in {"com.apple.Music", "com.spotify.client"})
        if arguments is not None:
            expected["action"] = arguments.get("action")
    return valid and all(payload.get(key) == value for key, value in expected.items())


def _unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate receipt field")
        result[key] = value
    return result


def _reject_constant(value: str) -> NoReturn:
    raise ValueError("non-finite native JSON value")


def decode_native_json(output: str) -> object:
    """Do not erase conflicting fields before the native contract can reject them."""
    return json.loads(output, object_pairs_hook=_unique_object, parse_constant=_reject_constant)


def native_result_is_acknowledged(result: ToolExecutionResult) -> bool:
    if (result.tool_name not in NATIVE_RECEIPT_SOURCES or not result.success
            or result.error_code is not None or result.metadata.get("verified") is not True
            or result.metadata.get("goal_verified") is not False
            or result.metadata.get("verification_scope") != NATIVE_RECEIPT_SCOPE
            or result.metadata.get("source") != NATIVE_RECEIPT_SOURCES[result.tool_name]
            or len(result.output) > 65_536):
        return False
    try:
        payload = decode_native_json(result.output)
    except (ValueError, RecursionError):
        return False
    return native_receipt_is_valid(result.tool_name, payload)


def smart_tv_receipt_is_valid(
    payload: object, arguments: Mapping[str, object] | None = None,
) -> bool:
    return (isinstance(payload, dict) and set(payload) == {"device_id", "state"}
            and isinstance(payload["device_id"], str)
            and re.fullmatch(r"[a-z][a-z0-9-]{2,31}", payload["device_id"]) is not None
            and isinstance(payload["state"], str) and payload["state"] in SMART_TV_RECEIPT_STATES
            and (arguments is None or payload["device_id"] == arguments.get("device_id")))


def smart_tv_result_is_acknowledged(result: ToolExecutionResult) -> bool:
    if (result.tool_name != "smart_tv_control" or not result.success
            or result.error_code is not None or result.metadata.get("verified") is not True
            or result.metadata.get("goal_verified") is not False
            or result.metadata.get("verification_scope") != SMART_TV_RECEIPT_SCOPE):
        return False
    try:
        payload = decode_native_json(result.output)
    except (ValueError, RecursionError):
        return False
    return (smart_tv_receipt_is_valid(payload)
            and payload["device_id"] == result.metadata.get("device_id")
            and payload["state"] == result.metadata.get("device_state"))


def native_receipt_summary(tool_name: str, payload: dict[str, object]) -> str:
    """Render only a previously validated receipt without promising a final state."""
    if tool_name == "mail_send_message":
        return (f"Mail aceptó la solicitud de envío a {payload['recipient_count']} "
                "destinatario(s). No he comprobado la entrega.")
    if tool_name == "calendar_create_event":
        return (f"Calendario aceptó crear «{payload['title']}» en «{payload['calendar']}». "
                "No he comprobado el evento resultante.")
    if tool_name in {"reminder_create", "reminder_complete"}:
        action = "crear" if tool_name == "reminder_create" else "marcar como completado"
        return (f"Recordatorios aceptó {action} «{payload['title']}» en «{payload['list']}». "
                "No he comprobado su estado final.")
    if tool_name == "contact_create":
        return (f"Contactos aceptó crear «{payload['name']}». "
                "No he comprobado el contacto resultante.")
    if tool_name == "application_open":
        return "macOS aceptó abrir la aplicación solicitada. No he comprobado su ventana."
    if tool_name == "browser_open_url":
        return "macOS aceptó abrir la dirección web solicitada. No he comprobado la página."
    if tool_name == "browser_search":
        return (f"macOS aceptó abrir la búsqueda en {_BROWSERS[payload['browser']]}. "
                "No he comprobado la página.")
    if tool_name == "shortcut_run":
        return f"Atajos terminó la ejecución de «{payload['name']}». No he comprobado sus efectos."
    if tool_name == "media_control":
        return ("La app de música aceptó la orden solicitada. "
                "No he comprobado el estado de reproducción.")
    raise ValueError("unsupported native receipt")
