from __future__ import annotations

import json
import math
from datetime import datetime, timedelta
from ipaddress import ip_address
from urllib.parse import urlparse

from aegis_core.contracts import (
    AgentResult,
    AgentRole,
    PolicyDecision,
    ToolAuthorization,
    ToolCall,
    ToolExecutionResult,
)
from aegis_core.orchestration.routing import (
    PLANNER_LOCAL_READ_TOOLS,
)
from aegis_core.tools.native_receipts import (
    NATIVE_RECEIPT_SOURCES,
    native_receipt_is_valid,
    native_receipt_summary,
)
from aegis_core.tools.verification import result_is_verified


def _deterministic_automation_error_response(
    authorizations: tuple[ToolAuthorization, ...],
    tool_results: tuple[ToolExecutionResult, ...],
) -> str | None:
    if len(authorizations) != 1 or len(tool_results) != 1:
        return None
    authorization, result = authorizations[0], tool_results[0]
    if (
        authorization.decision is not PolicyDecision.ALLOW
        or result.call_id != authorization.call_id
        or result.tool_name != authorization.tool_name
        or result.success
        or result.output != ""
        or result.tool_name not in {
            "mail_list_recent", "mail_send_message", "calendar_list_events",
            "calendar_create_event", "reminders_list", "reminder_create",
            "reminder_complete", "contacts_search", "contact_create", "media_control",
        }
    ):
        return None
    if result.tool_name in {
        "mail_send_message", "calendar_create_event", "reminder_create",
        "reminder_complete", "contact_create", "media_control",
    } and result.error_code in {
        "automation_target_unavailable", "automation_target_ambiguous",
        "automation_output_limit", "automation_invalid_response", "automation_failed",
    }:
        # Native calls can fail while reading back AFTER a mutation succeeded.
        # An error category is not proof that it is safe to repeat that mutation.
        return (
            "No pude comprobar el resultado de la acción en la app. "
            "Revisa si se aplicó algún cambio antes de repetirla."
        )
    return {
        "automation_target_unavailable": "No encontré el destino solicitado en la app.",
        "automation_target_ambiguous": (
            "No pude identificar un único destino. Indica la app o la lista concreta."
        ),
        "automation_output_limit": (
            "La consulta superó el límite de elementos. Acota la lista o el intervalo."
        ),
        "automation_invalid_response": (
            "La app devolvió una respuesta que no pude validar. "
            "Revisa su estado antes de repetir una acción que cambie datos."
        ),
        "automation_failed": (
            "La automatización de macOS falló. No pude comprobar el resultado; "
            "revisa la app antes de repetir una acción que cambie datos."
        ),
    }.get(result.error_code)


def _deterministic_smart_tv_discovery_response(
    authorizations: tuple[ToolAuthorization, ...],
    tool_results: tuple[ToolExecutionResult, ...],
) -> str | None:
    if not any(authorization.tool_name == "smart_tv_discover"
               and authorization.decision is PolicyDecision.ALLOW
               for authorization in authorizations):
        return None
    invalid = "No pude validar los resultados del descubrimiento de televisores en la red local."
    if len(authorizations) != 1 or len(tool_results) != 1:
        return invalid
    authorization = authorizations[0]
    result = tool_results[0]
    if result.call_id != authorization.call_id or result.tool_name != authorization.tool_name:
        return invalid
    if not result.success:
        if result.error_code == "device_discovery_failed":
            return (
                "No pude completar el descubrimiento de televisores en la red local. "
                "Revisa la conexión y el permiso Red local de Jarvis en Ajustes del Sistema."
            )
        return invalid
    if (result.error_code is not None or result.metadata.get("verified") is not True
            or result.metadata.get("source") != "local_ssdp"
            or type(result.metadata.get("devices")) is not int
            or len(result.output) > 65_536):
        return invalid
    try:
        devices = json.loads(result.output)
    except (ValueError, RecursionError):
        return invalid
    if (not isinstance(devices, list) or len(devices) > 64
            or len(devices) != result.metadata["devices"]):
        return invalid
    platforms = {"webos": "webOS", "tizen": "Samsung Tizen", "android": "Android/Google TV"}
    found: set[tuple[str, str]] = set()
    for device in devices:
        if not isinstance(device, dict):
            return invalid
        host, platform = device.get("host"), device.get("platform")
        if not isinstance(host, str) or not isinstance(platform, str) or platform not in platforms:
            return invalid
        try:
            address = ip_address(host)
        except ValueError:
            return invalid
        if (not address.is_private or address.is_loopback or address.is_unspecified
                or address.is_multicast or "%" in host):
            return invalid
        found.add((address.compressed, platforms[platform]))
    if not found:
        return "No encontré televisores anunciándose en la red local."
    # SSDP headers are untrusted advertisements, not instructions or verified TV names.
    # Show only validated addresses and known platform labels; never copy server text.
    lines = [f"- {platform}: {host}" for host, platform in sorted(found)]
    return "Detecté anuncios compatibles con televisores en la red local:\n\n" + "\n".join(lines)


def _deterministic_runtime_response(
    direct_call: ToolCall | None,
    tool_results: tuple[ToolExecutionResult, ...],
) -> str | None:
    if (
        direct_call is None
        or direct_call.tool_name != "system_describe_runtime"
        or len(tool_results) != 1
        or not tool_results[0].success
        or tool_results[0].tool_name != direct_call.tool_name
    ):
        return None
    try:
        payload = json.loads(tool_results[0].output)
    except json.JSONDecodeError:
        return None
    if not isinstance(payload, dict):
        return None
    architecture = payload.get("architecture")
    operating_system = payload.get("operating_system")
    os_release = payload.get("os_release")
    python_version = payload.get("python")
    macos_version = payload.get("macos_version")
    if not all(
        isinstance(value, str) and value
        for value in (architecture, operating_system, os_release, python_version)
    ):
        return None
    hardware = []
    chip = payload.get("chip")
    hardware_model = payload.get("hardware_model")
    memory_bytes = payload.get("memory_bytes")
    if isinstance(chip, str) and chip:
        hardware.append(chip)
    if isinstance(hardware_model, str) and hardware_model:
        hardware.append(f"modelo {hardware_model}")
    hardware.append(f"arquitectura {architecture}")
    if isinstance(memory_bytes, int) and memory_bytes > 0:
        memory_gib = memory_bytes / 1_073_741_824
        memory = f"{memory_gib:.1f}".rstrip("0").rstrip(".")
        hardware.append(f"{memory} GB de memoria")
    hardware_text = ", ".join(hardware[:-1]) + (
        f" y {hardware[-1]}" if len(hardware) > 1 else hardware[-1]
    )
    system = (
        f"macOS {macos_version}"
        if isinstance(macos_version, str) and macos_version
        else f"{operating_system} {os_release}"
    )
    return (
        f"Este Mac: {hardware_text}. Ejecuta {system}; "
        f"el núcleo de Jarvis usa Python {python_version}."
    )


def _deterministic_power_response(
    direct_call: ToolCall | None,
    tool_results: tuple[ToolExecutionResult, ...],
) -> str | None:
    if (
        direct_call is None
        or direct_call.tool_name != "system_power_status"
        or len(tool_results) != 1
        or tool_results[0].tool_name != direct_call.tool_name
    ):
        return None
    result = tool_results[0]
    if not result.success:
        if result.error_code in {"file_not_found", "executor_unavailable"}:
            return "El estado de batería no está disponible en este equipo."
        if result.error_code == "execution_timeout":
            return "La consulta de batería superó el tiempo máximo permitido."
        return "No pude consultar el estado de batería en este momento."
    try:
        payload = json.loads(result.output)
    except json.JSONDecodeError:
        return None
    if not isinstance(payload, dict):
        return None
    source_value = payload.get("power_source")
    if source_value not in {"ac", "battery", "ups", "unknown"}:
        return None
    source = {"ac": "el adaptador de corriente", "battery": "la batería", "ups": "un UPS"}.get(
        source_value,
        "una fuente no identificada",
    )
    if payload.get("battery_present") is False:
        return f"No detecté una batería interna; el Mac usa {source}."
    if payload.get("battery_present") is not True:
        return None
    percentage = payload.get("battery_percent")
    state = payload.get("battery_state")
    if (
        isinstance(percentage, bool)
        or not isinstance(percentage, int)
        or not 0 <= percentage <= 100
        or state
        not in {
            "charged",
            "charging",
            "discharging",
            "unknown",
        }
    ):
        return None
    state_text = {
        "charged": "cargada",
        "charging": "cargando",
        "discharging": "en descarga",
        "unknown": "con estado de carga no identificado",
    }[state]
    response = f"La batería está al {percentage} % y está {state_text}; el Mac usa {source}."
    remaining = payload.get("time_remaining_minutes")
    if (
        not isinstance(remaining, bool)
        and isinstance(remaining, int)
        and 0 < remaining <= 5_999
    ):
        hours, minutes = divmod(remaining, 60)
        estimate = (
            f"{hours} h {minutes} min"
            if hours and minutes
            else (f"{hours} h" if hours else f"{minutes} min")
        )
        response += f" Autonomía estimada: {estimate}."
    return response


def _deterministic_storage_response(
    direct_call: ToolCall | None,
    tool_results: tuple[ToolExecutionResult, ...],
) -> str | None:
    if (
        direct_call is None
        or direct_call.tool_name != "system_storage_status"
        or len(tool_results) != 1
        or tool_results[0].tool_name != direct_call.tool_name
    ):
        return None
    result = tool_results[0]
    if not result.success:
        return "No pude consultar el almacenamiento local en este momento."
    try:
        payload = json.loads(result.output)
    except json.JSONDecodeError:
        return None
    if not isinstance(payload, dict):
        return None
    available = payload.get("available_bytes")
    total = payload.get("total_bytes")
    used = payload.get("used_bytes")
    if not all(type(value) is int for value in (available, total, used)):
        return None
    assert isinstance(available, int) and isinstance(total, int) and isinstance(used, int)
    if (
        total <= 0
        or available < 0
        or used < 0
        or available > total
        or used != total - available
    ):
        return None
    available_percent = (available * 100 + total // 2) // total
    return (
        f"El disco de inicio tiene {_format_storage_bytes(available)} disponibles de "
        f"{_format_storage_bytes(total)}; queda libre el {available_percent} %."
    )


def _format_storage_bytes(value: int) -> str:
    divisor, unit = (
        (1_000_000_000_000, "TB")
        if value >= 1_000_000_000_000
        else (1_000_000_000, "GB")
    )
    rendered = f"{value / divisor:.1f}".rstrip("0").rstrip(".").replace(".", ",")
    return f"{rendered} {unit}"


def _deterministic_system_observe_response(
    direct_call: ToolCall | None,
    tool_results: tuple[ToolExecutionResult, ...],
) -> str | None:
    if direct_call is None or direct_call.tool_name != "system_observe_status":
        return None
    domain = direct_call.arguments.get("domain")
    if domain not in {"audio", "network", "performance"}:
        return None
    failure = {
        "audio": "No pude consultar el estado del audio en este momento.",
        "network": "No pude consultar la conectividad de red local en este momento.",
        "performance": "No pude consultar el rendimiento local en este momento.",
    }[domain]
    if (
        len(tool_results) != 1
        or tool_results[0].tool_name != direct_call.tool_name
        or not tool_results[0].success
    ):
        return failure
    try:
        payload = json.loads(tool_results[0].output)
    except json.JSONDecodeError:
        return failure
    if not isinstance(payload, dict):
        return failure

    if domain == "audio":
        if set(payload) != {"output_muted", "output_volume_percent"}:
            return failure
        muted = payload["output_muted"]
        volume = payload["output_volume_percent"]
        if type(muted) is not bool or type(volume) is not int or not 0 <= volume <= 100:
            return failure
        if muted:
            return f"El audio de salida está silenciado; el volumen configurado es {volume} %."
        return f"El audio de salida está al {volume} % y no está silenciado."

    if domain == "network":
        expected = {
            "active_interfaces",
            "connected",
            "ipv4_available",
            "ipv6_available",
        }
        if set(payload) != expected:
            return failure
        active = payload["active_interfaces"]
        connected = payload["connected"]
        ipv4 = payload["ipv4_available"]
        ipv6 = payload["ipv6_available"]
        if (
            type(active) is not int
            or not 0 <= active <= 128
            or any(type(value) is not bool for value in (connected, ipv4, ipv6))
            or connected != (active > 0)
            or (not connected and (ipv4 or ipv6))
            or (connected and not (ipv4 or ipv6))
        ):
            return failure
        if not connected:
            return "No detecté conectividad de red local activa."
        protocols = " e ".join(
            name for name, available in (("IPv4", ipv4), ("IPv6", ipv6)) if available
        )
        availability = "disponible" if ipv4 != ipv6 else "disponibles"
        return (
            f"La conectividad de red local está activa mediante {active} "
            f"{'interfaz' if active == 1 else 'interfaces'}; {protocols} {availability}."
        )

    if set(payload) != {
        "load_average_1m",
        "logical_cpus",
        "memory_available_percent",
    }:
        return failure
    load = payload["load_average_1m"]
    cpus = payload["logical_cpus"]
    memory = payload["memory_available_percent"]
    if (
        isinstance(load, bool)
        or not isinstance(load, (int, float))
        or not math.isfinite(load)
        or not 0 <= load <= 100_000
        or type(cpus) is not int
        or not 1 <= cpus <= 1_024
        or type(memory) is not int
        or not 0 <= memory <= 100
    ):
        return failure
    pressure = load / cpus
    level = "baja" if pressure < 0.5 else "moderada" if pressure < 1 else "alta"
    rendered_load = f"{load:.2f}".rstrip("0").rstrip(".").replace(".", ",")
    return (
        f"La carga de un minuto es {level}: {rendered_load} para {cpus} núcleos "
        f"lógicos. La memoria disponible estimada es {memory} %."
    )


def _can_synthesize_read_locally(
    specialists: tuple[AgentResult, ...],
    direct_call: ToolCall | None,
    tool_result: ToolExecutionResult,
) -> bool:
    return tool_result.success and _is_local_read(
        specialists,
        direct_call,
        tool_result,
    )


def _is_local_read(
    specialists: tuple[AgentResult, ...],
    direct_call: ToolCall | None,
    tool_result: ToolExecutionResult,
) -> bool:
    return (
        (
            len(specialists) == 1
            and specialists[0].role is AgentRole.PLANNER
            and tool_result.tool_name in PLANNER_LOCAL_READ_TOOLS
        )
        or (
            direct_call is not None
            and direct_call.tool_name == "filesystem_read_text"
            and tool_result.tool_name == direct_call.tool_name
        )
    )


def _deterministic_read_error_response(
    specialists: tuple[AgentResult, ...],
    direct_call: ToolCall | None,
    tool_results: tuple[ToolExecutionResult, ...],
) -> str | None:
    if (
        len(tool_results) != 1
        or not _is_local_read(specialists, direct_call, tool_results[0])
        or tool_results[0].success
        or tool_results[0].output != ""
    ):
        return None
    result = tool_results[0]
    if result.error_code == "file_not_found":
        return (
            "No encontré el archivo solicitado."
            if result.tool_name == "filesystem_read_text"
            else "No encontré un componente local necesario para completar la lectura."
        )
    if result.error_code == "invalid_utf8":
        return (
            "El archivo no contiene texto UTF-8 válido."
            if result.tool_name == "filesystem_read_text"
            else None
        )
    if result.error_code == "web_access_failed":
        if result.tool_name == "web_research":
            failures = result.metadata.get("search_provider_failures")
            if isinstance(failures, dict) and "result_pages_unavailable" in failures.values():
                return "Encontré referencias, pero no pude leer sus páginas para verificarlas."
            return (
                "No pude consultar los proveedores de búsqueda web. "
                "Inténtalo de nuevo más tarde."
            )
        return (
            "No pude acceder al recurso web público solicitado."
            if result.tool_name == "web_fetch"
            else None
        )
    return {
        "access_denied": "macOS denegó el acceso a esta lectura.",
        "execution_timeout": "La lectura superó el tiempo máximo permitido.",
        "io_error": "La lectura falló por un error local de entrada o salida.",
    }.get(result.error_code)


def _deterministic_empty_read_response(
    specialists: tuple[AgentResult, ...],
    direct_call: ToolCall | None,
    tool_results: tuple[ToolExecutionResult, ...],
) -> str | None:
    if len(tool_results) != 1 or not _can_synthesize_read_locally(
        specialists,
        direct_call,
        tool_results[0],
    ):
        return None
    result = tool_results[0]
    if result.tool_name == "filesystem_read_text":
        if (
            result.output == ""
            and result.metadata.get("bytes_read") == 0
            and result.metadata.get("truncated") is False
        ):
            return "El archivo está vacío."
        return None
    try:
        payload = json.loads(result.output)
    except json.JSONDecodeError:
        return None
    if not isinstance(payload, dict):
        return None
    if result.tool_name == "mail_list_recent":
        return (
            "No encontré correos en el alcance solicitado."
            if set(payload) == {"messages"} and payload["messages"] == []
            else None
        )
    if result.tool_name == "calendar_list_events":
        return (
            "No encontré eventos en el intervalo solicitado."
            if set(payload) == {"events"} and payload["events"] == []
            else None
        )
    if result.tool_name == "web_research":
        return (
            "No encontré resultados públicos para esa búsqueda."
            if set(payload) == {"query", "results"}
            and isinstance(payload["query"], str)
            and payload["query"]
            and payload["results"] == []
            else None
        )
    if result.tool_name == "web_fetch":
        return (
            "La página no contiene texto legible."
            if set(payload) == {"content", "title", "url"}
            and payload["content"] == ""
            and isinstance(payload["title"], str)
            and isinstance(payload["url"], str)
            and payload["url"]
            else None
        )
    return None


def _deterministic_public_web_response(
    specialists: tuple[AgentResult, ...],
    direct_call: ToolCall | None,
    tool_results: tuple[ToolExecutionResult, ...],
) -> tuple[str, str] | None:
    if direct_call is None or direct_call.tool_name not in {"web_fetch", "web_research"}:
        return None
    model_id = f"local/deterministic-{direct_call.tool_name.replace('_', '-')}"
    failure = "No pude validar la evidencia pública recibida."
    if len(tool_results) != 1:
        return failure, model_id
    result = tool_results[0]
    if (
        result.call_id != direct_call.call_id
        or result.tool_name != direct_call.tool_name
        or not result.success
        or not _is_local_read(specialists, direct_call, result)
        or result.metadata.get("source") != "public_https"
        or result.metadata.get("verified") is not True
    ):
        return failure, model_id
    try:
        payload = json.loads(result.output)
    except json.JSONDecodeError:
        return failure, model_id
    if not isinstance(payload, dict):
        return failure, model_id

    if direct_call.tool_name == "web_fetch":
        if set(payload) != {"content", "title", "url"}:
            return failure, model_id
        hostname = _public_https_hostname(payload["url"])
        title = _normalized_printable_text(payload["title"], max_characters=300)
        excerpt = _bounded_web_excerpt(payload["content"], maximum_input_characters=8_000)
        if hostname is None or title is None or excerpt is None:
            return failure, model_id
        label = title or hostname
        return f"{label} ({hostname}): {excerpt}", model_id

    query = _normalized_printable_text(payload.get("query"), max_characters=300)
    requested_query = _normalized_printable_text(
        direct_call.arguments.get("query"),
        max_characters=300,
    )
    maximum_results = direct_call.arguments.get("max_results")
    results = payload.get("results")
    if (
        set(payload) != {"query", "results"}
        or query is None
        or query != requested_query
        or type(maximum_results) is not int
        or not 1 <= maximum_results <= 5
        or not isinstance(results, list)
        or not 1 <= len(results) <= maximum_results
    ):
        return failure, model_id
    rendered: list[str] = []
    for item in results:
        if not isinstance(item, dict) or set(item) not in (
            {"content", "title", "url"},
            {"content", "snippet", "title", "url"},
        ):
            return failure, model_id
        hostname = _public_https_hostname(item["url"])
        title = _normalized_printable_text(item["title"], max_characters=300)
        excerpt = _bounded_web_excerpt(
            item.get("snippet") or item["content"],
            maximum_input_characters=6_000,
        )
        if hostname is None or title is None or excerpt is None:
            return failure, model_id
        rendered.append(f"{len(rendered) + 1}. {title or hostname} ({hostname}): {excerpt}")
    noun = "fuente pública" if len(rendered) == 1 else "fuentes públicas"
    return f"Encontré {len(rendered)} {noun} para «{query}»:\n" + "\n".join(rendered), model_id


def _public_https_hostname(value: object) -> str | None:
    if not isinstance(value, str) or not 12 <= len(value) <= 2_048 or not value.isprintable():
        return None
    try:
        parsed = urlparse(value)
        port = parsed.port
    except ValueError:
        return None
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or port not in {None, 443}
        or parsed.fragment
    ):
        return None
    return parsed.hostname


def _bounded_web_excerpt(value: object, *, maximum_input_characters: int) -> str | None:
    normalized = _normalized_printable_text(value, max_characters=maximum_input_characters)
    if not normalized:
        return None
    if len(normalized) <= 280:
        return normalized
    prefix = normalized[:279].rstrip()
    boundary = prefix.rfind(" ")
    if boundary >= 180:
        prefix = prefix[:boundary]
    return prefix + "…"


def _deterministic_personal_data_response(
    specialists: tuple[AgentResult, ...],
    direct_call: ToolCall | None,
    tool_results: tuple[ToolExecutionResult, ...],
) -> str | None:
    if direct_call is None or direct_call.tool_name not in {
        "contacts_search",
        "reminders_list",
    }:
        return None
    failure = (
        "No pude consultar tus contactos."
        if direct_call.tool_name == "contacts_search"
        else "No pude consultar tus recordatorios."
    )
    if len(tool_results) != 1:
        return failure
    result = tool_results[0]
    if (
        result.tool_name != direct_call.tool_name
        or not result.success
        or not _is_local_read(specialists, direct_call, result)
    ):
        return failure
    try:
        payload = json.loads(result.output)
    except json.JSONDecodeError:
        return failure
    if not isinstance(payload, dict):
        return failure

    if direct_call.tool_name == "reminders_list":
        reminders = payload.get("reminders")
        if set(payload) != {"reminders"} or not isinstance(reminders, list):
            return failure
        if not reminders:
            return "No tienes recordatorios pendientes en el alcance solicitado."
        rendered: list[str] = []
        for reminder in reminders:
            if not isinstance(reminder, dict):
                return failure
            title = _normalized_printable_text(reminder.get("title"), max_characters=300)
            list_name = _normalized_printable_text(reminder.get("list"), max_characters=128)
            if title is None or list_name is None or reminder.get("completed") is not False:
                return failure
            due = _aware_iso_datetime(reminder.get("local_due_at"))
            detail = f"«{title}»" if title else "Recordatorio sin título"
            if due is not None:
                detail += f", vence el {due.strftime('%d/%m/%Y a las %H:%M')}"
            if list_name:
                detail += f" ({list_name})"
            rendered.append(detail)
        return "Recordatorios pendientes: " + "; ".join(rendered) + "."

    contacts = payload.get("contacts")
    query = payload.get("query")
    if (
        set(payload) != {"contacts", "query"}
        or not isinstance(contacts, list)
        or _normalized_printable_text(query, max_characters=100) is None
    ):
        return failure
    if not contacts:
        return "No encontré contactos que coincidan con esa búsqueda."
    rendered_contacts: list[str] = []
    for contact in contacts:
        if not isinstance(contact, dict) or set(contact) != {"emails", "name", "phones"}:
            return failure
        name = _normalized_printable_text(contact["name"], max_characters=300)
        emails = contact["emails"]
        phones = contact["phones"]
        if (
            name is None
            or not isinstance(emails, list)
            or not isinstance(phones, list)
            or len(emails) > 3
            or len(phones) > 3
        ):
            return failure
        channels = []
        for value in [*emails, *phones]:
            normalized = _normalized_printable_text(value, max_characters=254)
            if normalized is None:
                return failure
            if normalized:
                channels.append(normalized)
        label = name or "Contacto sin nombre"
        rendered_contacts.append(
            f"{label}: {', '.join(channels)}" if channels else label
        )
    return "Contactos encontrados: " + "; ".join(rendered_contacts) + "."


def _deterministic_mail_list_response(
    specialists: tuple[AgentResult, ...],
    direct_call: ToolCall | None,
    tool_results: tuple[ToolExecutionResult, ...],
) -> str | None:
    if len(tool_results) != 1 or tool_results[0].tool_name != "mail_list_recent":
        return None
    result = tool_results[0]
    if not _is_local_read(specialists, direct_call, result):
        return None
    failure = "No pude consultar tus correos recientes."
    if not result.success:
        return failure
    try:
        payload = json.loads(result.output)
    except json.JSONDecodeError:
        return failure
    if not isinstance(payload, dict) or set(payload) != {"messages"}:
        return failure
    messages = payload["messages"]
    if not isinstance(messages, list) or not 1 <= len(messages) <= 20:
        return failure
    rendered: list[str] = []
    for message in messages[:5]:
        if not isinstance(message, dict):
            return failure
        sender = _normalized_printable_text(message.get("sender"), max_characters=500)
        subject = _normalized_printable_text(message.get("subject"), max_characters=500)
        if sender is None or subject is None:
            return failure
        unread = message.get("unread")
        if unread is not None and type(unread) is not bool:
            return failure
        received_value = message.get("local_date_received")
        received = _aware_iso_datetime(received_value) if received_value is not None else None
        if received_value is not None and received is None:
            return failure
        detail = "No leído" if unread is True else "Correo"
        if sender:
            detail += f" de {sender}"
        if subject:
            detail += f", asunto «{subject}»"
        if received is not None:
            detail += f", {received.strftime('%d/%m/%Y a las %H:%M')}"
        rendered.append(detail)
    remaining = len(messages) - len(rendered)
    suffix = f"; y {remaining} más" if remaining else ""
    return "Correos recientes: " + "; ".join(rendered) + suffix + "."


def _deterministic_calendar_list_response(
    specialists: tuple[AgentResult, ...],
    direct_call: ToolCall | None,
    tool_results: tuple[ToolExecutionResult, ...],
) -> str | None:
    if len(tool_results) != 1 or tool_results[0].tool_name != "calendar_list_events":
        return None
    result = tool_results[0]
    if not _is_local_read(specialists, direct_call, result):
        return None
    failure = "No pude consultar tu agenda."
    if not result.success:
        return failure
    try:
        payload = json.loads(result.output)
    except json.JSONDecodeError:
        return failure
    if not isinstance(payload, dict) or set(payload) != {"events"}:
        return failure
    events = payload["events"]
    if not isinstance(events, list) or not 1 <= len(events) <= 50:
        return failure
    rendered: list[str] = []
    for event in events[:5]:
        if not isinstance(event, dict):
            return failure
        title = _normalized_printable_text(event.get("title"), max_characters=500)
        local_start_value = event.get("local_start_at", event.get("start_at"))
        local_start = _aware_iso_datetime(local_start_value)
        calendar_value = event.get("calendar")
        calendar = (
            _normalized_printable_text(calendar_value, max_characters=200)
            if calendar_value is not None
            else ""
        )
        if title is None or local_start is None or calendar is None:
            return failure
        detail = f"«{title}»" if title else "Evento sin título"
        detail += f", {local_start.strftime('%d/%m/%Y a las %H:%M')}"
        if calendar:
            detail += f" ({calendar})"
        rendered.append(detail)
    remaining = len(events) - len(rendered)
    suffix = f"; y {remaining} más" if remaining else ""
    return "Agenda: " + "; ".join(rendered) + suffix + "."


def _deterministic_native_control_response(
    direct_call: ToolCall | None,
    tool_results: tuple[ToolExecutionResult, ...],
) -> str | None:
    supported = {
        "application_open",
        "browser_open_url",
        "browser_play_media",
        "browser_search",
        "media_control",
        "spotlight_open",
        "spotlight_search",
        "system_audio_set",
    }
    if direct_call is None or direct_call.tool_name not in supported:
        return None
    if direct_call.tool_name in NATIVE_RECEIPT_SOURCES:
        failure = ("No pude comprobar el resultado de la acción en la app. "
                   "Revisa si se aplicó algún cambio antes de repetirla.")
        if len(tool_results) != 1:
            return failure
        result = tool_results[0]
        if (result.call_id != direct_call.call_id or result.tool_name != direct_call.tool_name
                or not result_is_verified(result)):
            return failure
        payload = json.loads(result.output)  # The receipt verifier checked the JSON shape.
        if not native_receipt_is_valid(result.tool_name, payload, direct_call.arguments):
            return failure
        return native_receipt_summary(result.tool_name, payload)
    failures = {
        "application_open": "No pude abrir la aplicación.",
        "browser_open_url": "No pude abrir la dirección web.",
        "browser_play_media": "No pude iniciar la reproducción en YouTube.",
        "browser_search": "No pude abrir la búsqueda en el navegador.",
        "media_control": "No pude controlar la reproducción multimedia.",
        "spotlight_open": "Spotlight no encontró un único resultado exacto y seguro para abrir.",
        "spotlight_search": "No pude completar la búsqueda local de Spotlight.",
        "system_audio_set": "No pude cambiar el audio del Mac.",
    }
    if len(tool_results) != 1:
        return failures[direct_call.tool_name]
    result = tool_results[0]
    if result.tool_name != direct_call.tool_name or not result.success:
        return failures[direct_call.tool_name]
    try:
        payload = json.loads(result.output)
    except json.JSONDecodeError:
        return failures[direct_call.tool_name]
    if not isinstance(payload, dict):
        return failures[direct_call.tool_name]

    if result.tool_name == "system_audio_set":
        if (
            set(payload) != {"output_muted", "output_volume_percent"}
            or not isinstance(payload["output_muted"], bool)
            or type(payload["output_volume_percent"]) is not int
            or not 0 <= payload["output_volume_percent"] <= 100
        ):
            return failures[result.tool_name]
        state = "silenciado" if payload["output_muted"] else "con sonido"
        return f"Audio del Mac {state}, volumen al {payload['output_volume_percent']} %."

    if result.tool_name == "spotlight_search":
        results = payload.get("results")
        query = payload.get("query")
        if (
            set(payload) != {"query", "results"}
            or query != direct_call.arguments.get("query")
            or not isinstance(results, list)
            or len(results) > 10
        ):
            return failures[result.tool_name]
        if not results:
            return "Spotlight no encontró resultados seguros en tu carpeta personal."
        names: list[str] = []
        for item in results:
            if not isinstance(item, dict) or set(item) != {"kind", "name", "path"}:
                return failures[result.tool_name]
            name = _normalized_printable_text(item["name"], max_characters=500)
            kind = item["kind"]
            if name is None or kind not in {"application", "file", "folder"}:
                return failures[result.tool_name]
            names.append(f"{name} ({kind})")
        return "Spotlight encontró: " + "; ".join(names) + "."

    if result.tool_name == "spotlight_open":
        name = _normalized_printable_text(payload.get("name"), max_characters=500)
        if set(payload) != {"name", "opened"} or payload.get("opened") is not True or name is None:
            return failures[result.tool_name]
        return f"Abrí {name} desde Spotlight."

    if result.tool_name == "browser_play_media":
        if (
            set(payload) != {"browser", "channel", "playing", "provider", "query"}
            or payload.get("browser") != "chrome"
            or payload.get("provider") != "youtube"
            or payload.get("playing") is not True
            or payload.get("channel") not in {"cdp", "jxa"}
            or payload.get("query") != direct_call.arguments.get("query")
        ):
            return failures[result.tool_name]
        return "Inicié la reproducción solicitada en YouTube con Chrome."

    return failures[result.tool_name]


def _deterministic_mail_unread_status_response(
    specialists: tuple[AgentResult, ...],
    direct_call: ToolCall | None,
    tool_results: tuple[ToolExecutionResult, ...],
) -> str | None:
    if (
        direct_call is None
        or direct_call.tool_name != "mail_list_recent"
        or direct_call.arguments != {"limit": 1, "unread_only": True}
    ):
        return None
    failure = "No pude comprobar si tienes correos no leídos."
    if len(tool_results) != 1:
        return failure
    result = tool_results[0]
    if (
        result.tool_name != "mail_list_recent"
        or not result.success
        or not _is_local_read(specialists, direct_call, result)
    ):
        return failure
    try:
        payload = json.loads(result.output)
    except json.JSONDecodeError:
        return failure
    if not isinstance(payload, dict) or set(payload) != {"messages"}:
        return failure
    messages = payload["messages"]
    if not isinstance(messages, list) or len(messages) > 1:
        return failure
    return (
        "Tienes al menos un correo no leído."
        if messages
        else "No tienes correos no leídos."
    )


def _deterministic_latest_mail_response(
    specialists: tuple[AgentResult, ...],
    direct_call: ToolCall | None,
    tool_results: tuple[ToolExecutionResult, ...],
) -> str | None:
    if (
        direct_call is None
        or direct_call.tool_name != "mail_list_recent"
        or direct_call.arguments != {"limit": 1, "unread_only": False}
    ):
        return None
    failure = "No pude consultar tu último correo."
    if len(tool_results) != 1:
        return failure
    result = tool_results[0]
    if (
        result.tool_name != "mail_list_recent"
        or not result.success
        or not _is_local_read(specialists, direct_call, result)
    ):
        return failure
    try:
        payload = json.loads(result.output)
    except json.JSONDecodeError:
        return failure
    if not isinstance(payload, dict) or set(payload) != {"messages"}:
        return failure
    messages = payload["messages"]
    if not isinstance(messages, list) or len(messages) > 1:
        return failure
    if not messages:
        return "No encontré correos en tu bandeja de entrada."
    message = messages[0]
    if not isinstance(message, dict):
        return failure
    sender = message.get("sender")
    subject = message.get("subject")
    received_value = message.get("local_date_received")
    normalized_sender = _normalized_printable_text(sender, max_characters=500)
    normalized_subject = _normalized_printable_text(subject, max_characters=500)
    received = _aware_iso_datetime(received_value)
    if normalized_sender is None or normalized_subject is None or received is None:
        return failure
    schedule = received.strftime("%d/%m/%Y a las %H:%M")
    if normalized_sender and normalized_subject:
        return (
            f"Tu último correo es de {normalized_sender}, con el asunto "
            f"«{normalized_subject}», recibido el {schedule}."
        )
    if normalized_sender:
        return f"Tu último correo es de {normalized_sender}, recibido el {schedule}."
    if normalized_subject:
        return f"Tu último correo tiene el asunto «{normalized_subject}», recibido el {schedule}."
    return f"Tu último correo fue recibido el {schedule}."


def _deterministic_calendar_today_status_response(
    specialists: tuple[AgentResult, ...],
    direct_call: ToolCall | None,
    tool_results: tuple[ToolExecutionResult, ...],
) -> str | None:
    if not _is_single_local_calendar_day_call(direct_call):
        return None
    failure = "No pude comprobar si tienes eventos hoy."
    if len(tool_results) != 1:
        return failure
    result = tool_results[0]
    if (
        result.tool_name != "calendar_list_events"
        or not result.success
        or not _is_local_read(specialists, direct_call, result)
    ):
        return failure
    try:
        payload = json.loads(result.output)
    except json.JSONDecodeError:
        return failure
    if not isinstance(payload, dict) or set(payload) != {"events"}:
        return failure
    events = payload["events"]
    if not isinstance(events, list) or len(events) > 1:
        return failure
    return "Tienes al menos un evento hoy." if events else "No tienes eventos hoy."


def _deterministic_next_calendar_event_response(
    specialists: tuple[AgentResult, ...],
    direct_call: ToolCall | None,
    tool_results: tuple[ToolExecutionResult, ...],
) -> str | None:
    if not _is_next_calendar_event_call(direct_call):
        return None
    failure = "No pude comprobar cuál es tu próximo evento."
    if len(tool_results) != 1:
        return failure
    result = tool_results[0]
    if (
        result.tool_name != "calendar_list_events"
        or not result.success
        or not _is_local_read(specialists, direct_call, result)
    ):
        return failure
    try:
        payload = json.loads(result.output)
    except json.JSONDecodeError:
        return failure
    if not isinstance(payload, dict) or set(payload) != {"events"}:
        return failure
    events = payload["events"]
    if not isinstance(events, list) or len(events) > 1:
        return failure
    if not events:
        return "No encontré próximos eventos en los siguientes 31 días."
    event = events[0]
    if not isinstance(event, dict):
        return failure
    title = event.get("title")
    local_start_value = event.get("local_start_at")
    normalized_title = _normalized_printable_text(title, max_characters=500)
    local_start = _aware_iso_datetime(local_start_value)
    if normalized_title is None or local_start is None:
        return failure
    window = _single_result_calendar_window(direct_call)
    if window is None or not window[0] <= local_start < window[1]:
        return failure
    schedule = local_start.strftime("%d/%m/%Y a las %H:%M")
    return (
        f"Tu próximo evento es «{normalized_title}» el {schedule}."
        if normalized_title
        else f"Tu próximo evento comienza el {schedule}."
    )


def _is_single_local_calendar_day_call(direct_call: ToolCall | None) -> bool:
    window = _single_result_calendar_window(direct_call)
    if window is None:
        return False
    start, end = window
    return (
        start.hour == start.minute == start.second == start.microsecond == 0
        and end.hour == end.minute == end.second == end.microsecond == 0
        and end > start
        and (end.date() - start.date()).days == 1
    )


def _is_next_calendar_event_call(direct_call: ToolCall | None) -> bool:
    window = _single_result_calendar_window(direct_call)
    return window is not None and window[1] - window[0] == timedelta(days=31)


def _normalized_printable_text(
    value: object,
    *,
    max_characters: int,
) -> str | None:
    if not isinstance(value, str):
        return None
    normalized = " ".join(value.split())
    if len(normalized) > max_characters or (normalized and not normalized.isprintable()):
        return None
    return normalized


def _aware_iso_datetime(value: object) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        return None
    return parsed


def _single_result_calendar_window(
    direct_call: ToolCall | None,
) -> tuple[datetime, datetime] | None:
    if direct_call is None or direct_call.tool_name != "calendar_list_events":
        return None
    arguments = direct_call.arguments
    if set(arguments) != {"start_at", "end_at", "limit"} or arguments["limit"] != 1:
        return None
    start = _aware_iso_datetime(arguments["start_at"])
    end = _aware_iso_datetime(arguments["end_at"])
    if start is None or end is None:
        return None
    return start, end
