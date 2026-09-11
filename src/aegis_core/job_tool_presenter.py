from __future__ import annotations

import json

from aegis_core.contracts import ToolAuthorization, ToolExecutionResult
from aegis_core.tools.broker import ToolBroker
from aegis_core.tools.verification import result_is_verified

CONFIRMED_TOOL_NAMES = frozenset(
    {
        "application_open",
        "browser_open_url",
        "browser_play_media",
        "browser_search",
        "calendar_create_event",
        "computer_use",
        "contact_create",
        "mail_send_message",
        "media_control",
        "network_discover_hosts",
        "reminder_complete",
        "reminder_create",
        "shortcut_run",
        "spotlight_open",
        "system_audio_set",
        "terminal_run_template",
    }
)


class JobToolPresenter:
    """Validates tool evidence and renders bounded, user-facing Spanish text."""

    def __init__(self, tool_broker: ToolBroker | None) -> None:
        self._tool_broker = tool_broker

    def supports_confirmation(self, tool_name: str) -> bool:
        return tool_name in CONFIRMED_TOOL_NAMES or self._is_plugin_tool(tool_name)

    def confirmation_summary(self, authorization: ToolAuthorization) -> str:
        arguments = authorization.normalized_arguments
        if authorization.tool_name == "terminal_run_template":
            template = arguments.get("template")
            summaries = {
                "git_status": "Diagnóstico local: estado Git del workspace",
                "list_processes": "Diagnóstico local: inventario de procesos",
                "list_listeners": "Diagnóstico local: listeners TCP",
                "security_posture": "Diagnóstico local: postura de seguridad de macOS",
            }
            summary = summaries.get(template) if isinstance(template, str) else None
            if summary is None:
                raise ValueError("terminal confirmation arguments are invalid")
            return summary
        if authorization.tool_name == "network_discover_hosts":
            target = arguments.get("target")
            ports = arguments.get("ports")
            if not isinstance(target, str) or not isinstance(ports, list) or not ports:
                raise ValueError("confirmation arguments are invalid")
            return f"Sondeo TCP en {target}; puertos {', '.join(str(port) for port in ports)}"
        if authorization.tool_name == "mail_send_message":
            recipients = arguments.get("recipients")
            raw_subject = arguments.get("subject")
            if (
                not isinstance(recipients, list)
                or not recipients
                or not all(isinstance(value, str) for value in recipients)
                or not isinstance(raw_subject, str)
            ):
                raise ValueError("mail confirmation arguments are invalid")
            subject = normalized_label(raw_subject, 300)
            return f"Enviar correo a {', '.join(recipients)}; asunto: {subject}"[:512]
        if authorization.tool_name == "calendar_create_event":
            raw_title = arguments.get("title")
            start_at = arguments.get("start_at")
            end_at = arguments.get("end_at")
            if not all(isinstance(value, str) for value in (raw_title, start_at, end_at)):
                raise ValueError("calendar confirmation arguments are invalid")
            title = normalized_label(raw_title, 300)
            return f"Crear evento «{title}»; {start_at} — {end_at}"[:512]
        if authorization.tool_name == "reminder_create":
            title = normalized_label(arguments.get("title"), 300)
            list_name = arguments.get("list_name")
            due_at = arguments.get("due_at")
            summary = f"Crear recordatorio «{title}»"
            if isinstance(list_name, str):
                summary += f" en «{normalized_label(list_name, 128)}»"
            if isinstance(due_at, str):
                summary += f"; vence: {due_at}"
            return summary[:512]
        if authorization.tool_name == "reminder_complete":
            title = normalized_label(arguments.get("title"), 300)
            list_name = arguments.get("list_name")
            summary = f"Completar recordatorio «{title}»"
            if isinstance(list_name, str):
                summary += f" en «{normalized_label(list_name, 128)}»"
            return summary[:512]
        if authorization.tool_name == "contact_create":
            first_name = normalized_label(arguments.get("first_name"), 100)
            last_name = arguments.get("last_name")
            name = first_name
            if isinstance(last_name, str) and last_name:
                name += f" {normalized_label(last_name, 100)}"
            summary = f"Crear contacto «{name}»"
            email = arguments.get("email")
            phone = arguments.get("phone")
            if isinstance(email, str):
                summary += f"; correo: {email}"
            if isinstance(phone, str):
                summary += f"; teléfono: {phone}"
            return summary[:512]
        if authorization.tool_name == "system_audio_set":
            volume = arguments.get("volume_percent")
            muted = arguments.get("muted")
            changes = []
            if type(volume) is int and 0 <= volume <= 100:
                changes.append(f"volumen al {volume} %")
            if type(muted) is bool:
                changes.append("silenciar" if muted else "activar sonido")
            if not changes:
                raise ValueError("audio confirmation arguments are invalid")
            return "Cambiar audio del Mac: " + ", ".join(changes)
        if authorization.tool_name == "media_control":
            action = {
                "play_pause": "alternar reproducción y pausa",
                "next": "siguiente pista",
                "previous": "pista anterior",
            }.get(arguments.get("action"))
            if action is None:
                raise ValueError("media confirmation arguments are invalid")
            return f"Control multimedia: {action}"
        if authorization.tool_name == "spotlight_open":
            query = normalized_label(arguments.get("query"), 200)
            return f"Abrir resultado exacto de Spotlight: {query}"[:512]
        if authorization.tool_name == "browser_open_url":
            url = arguments.get("url")
            if not isinstance(url, str):
                raise ValueError("browser confirmation arguments are invalid")
            return f"Abrir en el navegador: {url}"[:512]
        if authorization.tool_name == "browser_search":
            query = normalized_label(arguments.get("query"), 300)
            browser = {
                "default": "el navegador predeterminado",
                "safari": "Safari",
                "chrome": "Chrome",
                "firefox": "Firefox",
            }.get(arguments.get("browser"))
            if browser is None:
                raise ValueError("browser search confirmation arguments are invalid")
            return f"Buscar con DuckDuckGo en {browser}: {query}"[:512]
        if authorization.tool_name == "application_open":
            bundle_identifier = arguments.get("bundle_identifier")
            if not isinstance(bundle_identifier, str):
                raise ValueError("application confirmation arguments are invalid")
            return f"Abrir aplicación: {bundle_identifier}"[:512]
        if authorization.tool_name == "shortcut_run":
            name = arguments.get("name")
            if not isinstance(name, str):
                raise ValueError("shortcut confirmation arguments are invalid")
            return f"Ejecutar atajo de macOS: {name}"[:512]
        if authorization.tool_name == "computer_use":
            objective = arguments.get("objective")
            bundle_identifier = arguments.get("application_bundle_identifier")
            max_steps = arguments.get("max_steps")
            if (
                not isinstance(objective, str)
                or not isinstance(bundle_identifier, str)
                or not isinstance(max_steps, int)
            ):
                raise ValueError("computer confirmation arguments are invalid")
            return (
                f"Control visual de {bundle_identifier}; hasta {max_steps} pasos; "
                f"objetivo: {objective}"
            )[:512]
        if self._is_plugin_tool(authorization.tool_name):
            if self._tool_broker is None:
                raise ValueError("plugin broker is unavailable")
            definition = self._tool_broker.definition(authorization.tool_name)
            if (
                definition is None
                or not definition.provider_label
                or not definition.external_destination
            ):
                raise ValueError("plugin confirmation metadata is invalid")
            fields = ", ".join(sorted(authorization.normalized_arguments)) or "ninguno"
            return (
                f"Plugin {definition.provider_label}: {definition.description}; enviará los "
                f"campos [{fields}] a {definition.external_destination}"
            )[:512]
        raise ValueError("confirmation tool is unsupported")

    @staticmethod
    def format_result(
        result: ToolExecutionResult,
        authorization: ToolAuthorization | None = None,
    ) -> str:
        if result.tool_name == "mail_send_message":
            payload = json.loads(result.output)
            sent = payload.get("sent")
            recipient_count = payload.get("recipient_count")
            if (
                sent is not True
                or type(recipient_count) is not int
                or not 1 <= recipient_count <= 10
            ):
                raise ValueError("mail result is invalid")
            return f"Correo enviado a {recipient_count} destinatario(s)."
        if result.tool_name == "calendar_create_event":
            payload = json.loads(result.output)
            calendar = normalized_label(payload.get("calendar"), 200)
            title = normalized_label(payload.get("title"), 300)
            if payload.get("created") is not True:
                raise ValueError("calendar result is invalid")
            return f"Evento «{title}» creado en «{calendar}»."
        if result.tool_name in {"reminder_create", "reminder_complete"}:
            payload = json.loads(result.output)
            expected_state = "created" if result.tool_name == "reminder_create" else "completed"
            title = normalized_label(payload.get("title"), 300)
            list_name = normalized_label(payload.get("list"), 128)
            if payload.get(expected_state) is not True:
                raise ValueError("reminder result is invalid")
            if authorization is not None and title != normalized_label(
                authorization.normalized_arguments.get("title"), 300
            ):
                raise ValueError("reminder result does not match authorization")
            action = "creado" if result.tool_name == "reminder_create" else "completado"
            return f"Recordatorio «{title}» {action} en «{list_name}»."
        if result.tool_name == "contact_create":
            payload = json.loads(result.output)
            name = normalized_label(payload.get("name"), 300)
            if payload.get("created") is not True:
                raise ValueError("contact result is invalid")
            return f"Contacto «{name}» creado."
        if result.tool_name == "system_audio_set":
            payload = json.loads(result.output)
            muted = payload.get("output_muted")
            volume = payload.get("output_volume_percent")
            if type(muted) is not bool or type(volume) is not int or not 0 <= volume <= 100:
                raise ValueError("audio result is invalid")
            if authorization is not None:
                expected_volume = authorization.normalized_arguments.get("volume_percent")
                expected_muted = authorization.normalized_arguments.get("muted")
                if (expected_volume is not None and volume != expected_volume) or (
                    expected_muted is not None and muted != expected_muted
                ):
                    raise ValueError("audio result does not match authorization")
            state = "silenciado" if muted else "con sonido"
            return f"Audio del Mac {state}, volumen al {volume} %."
        if result.tool_name == "media_control":
            payload = json.loads(result.output)
            action = payload.get("action")
            bundle_identifier = payload.get("bundle_identifier")
            expected_action = (
                authorization.normalized_arguments.get("action")
                if authorization is not None
                else action
            )
            rendered = {
                "play_pause": "Alterné reproducción y pausa.",
                "next": "Pasé a la siguiente pista.",
                "previous": "Volví a la pista anterior.",
            }.get(action)
            if (
                rendered is None
                or action != expected_action
                or bundle_identifier not in {"com.apple.Music", "com.spotify.client"}
            ):
                raise ValueError("media result is invalid")
            return rendered
        if result.tool_name == "spotlight_open":
            payload = json.loads(result.output)
            name = normalized_label(payload.get("name"), 500)
            if payload.get("opened") is not True:
                raise ValueError("Spotlight result is invalid")
            return f"Abrí {name} desde Spotlight."
        if result.tool_name == "browser_open_url":
            payload = json.loads(result.output)
            url = payload.get("url")
            if (
                authorization is None
                or set(payload) != {"opened", "url"}
                or payload.get("opened") is not True
                or not isinstance(url, str)
                or url != authorization.normalized_arguments.get("url")
            ):
                raise ValueError("browser result is invalid")
            return "Abrí la dirección web solicitada."
        if result.tool_name == "browser_search":
            payload = json.loads(result.output)
            query = normalized_label(payload.get("query"), 300)
            browser = payload.get("browser")
            if (
                authorization is None
                or set(payload) != {"browser", "opened", "query"}
                or payload.get("opened") is not True
                or query != authorization.normalized_arguments.get("query")
                or browser != authorization.normalized_arguments.get("browser")
            ):
                raise ValueError("browser search result is invalid")
            browser_name = {
                "default": "el navegador predeterminado",
                "safari": "Safari",
                "chrome": "Chrome",
                "firefox": "Firefox",
            }.get(browser)
            if browser_name is None:
                raise ValueError("browser search result is invalid")
            return f"Abrí la búsqueda solicitada en {browser_name}."
        if result.tool_name == "browser_play_media":
            payload = json.loads(result.output)
            if (
                authorization is None
                or set(payload) != {"browser", "channel", "playing", "provider", "query"}
                or payload.get("browser") != "chrome"
                or payload.get("provider") != "youtube"
                or payload.get("playing") is not True
                or payload.get("channel") not in {"cdp", "jxa"}
                or payload.get("query") != authorization.normalized_arguments.get("query")
            ):
                raise ValueError("browser media result is invalid")
            return "Inicié la reproducción solicitada en YouTube con Chrome."
        if result.tool_name == "application_open":
            payload = json.loads(result.output)
            bundle_identifier = payload.get("bundle_identifier")
            if (
                authorization is None
                or set(payload) != {"bundle_identifier", "opened"}
                or payload.get("opened") is not True
                or not isinstance(bundle_identifier, str)
                or bundle_identifier != authorization.normalized_arguments.get("bundle_identifier")
            ):
                raise ValueError("application result is invalid")
            return "Abrí la aplicación solicitada."
        if result.tool_name == "shortcut_run":
            payload = json.loads(result.output)
            name = payload.get("name")
            if payload.get("completed") is not True or not isinstance(name, str):
                raise ValueError("shortcut result is invalid")
            return f"Atajo «{name}» ejecutado."
        if result.tool_name == "computer_use":
            payload = json.loads(result.output)
            status = payload.get("status")
            steps = payload.get("steps")
            bundle_identifier = payload.get("application_bundle_identifier")
            reason_code = payload.get("reason_code")
            expected = authorization.normalized_arguments if authorization else {}
            max_steps = expected.get("max_steps")
            if (
                authorization is None
                or set(payload)
                != {"application_bundle_identifier", "reason_code", "status", "steps"}
                or status not in {"completed", "blocked", "step_limit"}
                or type(steps) is not int
                or type(max_steps) is not int
                or not 0 <= steps <= max_steps
                or not isinstance(bundle_identifier, str)
                or bundle_identifier != expected.get("application_bundle_identifier")
                or not isinstance(reason_code, str)
            ):
                raise ValueError("computer result is invalid")
            if status == "completed":
                if reason_code != "objective_complete":
                    raise ValueError("computer result reason is invalid")
                if not result_is_verified(result):
                    raise ValueError("computer result is unverified")
                return "Completé el control visual solicitado."
            if status == "step_limit":
                if reason_code != "step_limit" or steps != max_steps:
                    raise ValueError("computer result reason is invalid")
                return "Me detuve al alcanzar el límite de pasos sin verificar el objetivo."
            reasons = {
                "sensitive_action": "la siguiente acción era sensible",
                "unsupported_action": "la siguiente acción no está permitida",
                "uncertain_state": "no pudo verificar el estado visual con seguridad",
                "user_takeover": "detectó que retomaste el teclado o el puntero",
            }
            reason = reasons.get(reason_code)
            if reason is None:
                raise ValueError("computer result reason is invalid")
            return f"Jarvis detuvo el control visual porque {reason}."
        if result.tool_name == "terminal_run_template":
            template = result.metadata.get("template")
            if template == "security_posture":
                payload = json.loads(result.output)
                controls = (
                    ("sip", "SIP"),
                    ("gatekeeper", "Gatekeeper"),
                    ("filevault", "FileVault"),
                    ("firewall", "Firewall"),
                )
                translations = {
                    "enabled": "activado",
                    "disabled": "desactivado",
                    "unavailable": "no disponible",
                }
                if not isinstance(payload, dict) or set(payload) != {name for name, _ in controls}:
                    raise ValueError("security posture result is invalid")
                lines = []
                for name, title in controls:
                    state = payload.get(name)
                    translated = translations.get(state) if isinstance(state, str) else None
                    if translated is None:
                        raise ValueError("security posture state is invalid")
                    lines.append(f"{title}: {translated}")
                return "Postura de seguridad de macOS:\n" + "\n".join(lines)
            headings = {
                "git_status": "Estado Git",
                "list_processes": "Procesos locales",
                "list_listeners": "Listeners TCP locales",
                "security_posture": "Postura de seguridad de macOS",
            }
            heading = headings.get(template) if isinstance(template, str) else None
            if heading is None:
                raise ValueError("terminal result is invalid")
            output = result.output.strip()
            suffix = "\nSalida truncada por política." if result.metadata.get("truncated") else ""
            if not output:
                return f"{heading}: sin resultados.{suffix}"
            return f"{heading}:\n{output}{suffix}"
        if result.tool_name.startswith("plugin_"):
            plugin_id = result.metadata.get("plugin_id")
            destination = result.metadata.get("destination")
            if (
                authorization is None
                or not isinstance(plugin_id, str)
                or not isinstance(destination, str)
                or len(result.output.encode("utf-8")) > 65_536
            ):
                raise ValueError("plugin result is invalid")
            payload = json.loads(result.output)
            rendered = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
            return f"Plugin {plugin_id} respondió desde {destination}: {rendered}"[:8_192]
        if result.tool_name != "network_discover_hosts":
            raise ValueError("approved tool result is unsupported")
        payload = json.loads(result.output)
        target = payload.get("target")
        hosts = payload.get("hosts")
        if not isinstance(target, str) or not isinstance(hosts, list):
            raise ValueError("network result is invalid")
        if not hosts:
            return f"Sondeo TCP completado en {target}. No se observaron hosts con respuesta."
        observations: list[str] = []
        for host in hosts:
            if not isinstance(host, dict):
                raise ValueError("network host result is invalid")
            address = host.get("address")
            ports = host.get("open_ports")
            if not isinstance(address, str) or not isinstance(ports, list):
                raise ValueError("network host result is invalid")
            port_text = (
                ", ".join(str(port) for port in ports) if ports else "sin puertos abiertos"
            )
            observations.append(f"{address}: {port_text}")
        return f"Sondeo TCP completado en {target}. " + "; ".join(observations)

    def _is_plugin_tool(self, tool_name: str) -> bool:
        return (
            tool_name.startswith("plugin_")
            and self._tool_broker is not None
            and self._tool_broker.definition(tool_name) is not None
        )


def normalized_label(value: object, max_characters: int) -> str:
    if not isinstance(value, str):
        raise ValueError("result label is invalid")
    normalized = " ".join(value.split())
    if not normalized or len(normalized) > max_characters or not normalized.isprintable():
        raise ValueError("result label is invalid")
    return normalized
