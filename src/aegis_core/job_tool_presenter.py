from __future__ import annotations

import json
from collections.abc import Callable, Mapping

from aegis_core.contracts import PolicyDecision, ToolAuthorization, ToolExecutionResult
from aegis_core.goals import format_goal_result, goal_confirmation_summary, is_goal_tool
from aegis_core.mcp.client import MAX_MCP_MESSAGE_BYTES
from aegis_core.mcp.result_text import readable_mcp_result
from aegis_core.plugins.foreign_service import (
    format_foreign_plugin_result,
    is_foreign_plugin_tool,
)
from aegis_core.scheduling import (
    format_schedule_result,
    is_schedule_tool,
    schedule_confirmation_summary,
)
from aegis_core.skills.teach import is_skill_tool, skill_confirmation_summary
from aegis_core.tools.broker import ToolBroker
from aegis_core.tools.computer import ComputerUseReport
from aegis_core.tools.native_receipts import (
    NATIVE_RECEIPT_SOURCES,
    decode_native_json,
    native_receipt_is_valid,
    native_receipt_summary,
    smart_tv_receipt_is_valid,
)
from aegis_core.tools.verification import result_is_verified

CONFIRMED_TOOL_NAMES = frozenset(
    {
        "application_open",
        "browser_open_url",
        "browser_play_media",
        "browser_search",
        "browser_task_open",
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

    def __init__(
        self,
        tool_broker: ToolBroker | None,
        *,
        summaries: Mapping[str, Callable[[ToolAuthorization], str]] | None = None,
    ) -> None:
        self._tool_broker = tool_broker
        # Summaries that need live state, such as which plugin a name resolves to.
        self._summaries = dict(summaries or {})

    def supports_confirmation(self, tool_name: str) -> bool:
        return (
            tool_name in CONFIRMED_TOOL_NAMES
            or is_schedule_tool(tool_name)
            or is_goal_tool(tool_name)
            or is_skill_tool(tool_name)
            or tool_name in self._summaries
            or self._is_plugin_tool(tool_name)
            or self._is_mcp_tool(tool_name)
        )

    def confirmation_summary(self, authorization: ToolAuthorization) -> str:
        if is_schedule_tool(authorization.tool_name):
            return schedule_confirmation_summary(authorization)
        if is_goal_tool(authorization.tool_name):
            return goal_confirmation_summary(authorization)
        if is_skill_tool(authorization.tool_name):
            return skill_confirmation_summary(authorization)
        if authorization.tool_name in self._summaries:
            return self._summaries[authorization.tool_name](authorization)
        arguments = authorization.normalized_arguments
        if authorization.tool_name == "browser_task_open":
            url = arguments.get("url")
            objective = arguments.get("objective")
            mode = arguments.get("mode")
            if (not isinstance(url, str) or not isinstance(objective, str)
                    or mode not in {None, "extension", "isolated", "chrome"}):
                raise ValueError("browser task confirmation arguments are invalid")
            browser = {"extension": "tu navegador", "isolated": "un navegador separado",
                       "chrome": "Chrome directo (conexión CDP)"}.get(
                mode, "el navegador configurado"
            )
            return (
                "Permitir a Jarvis analizar con NVIDIA el contenido del sitio y las instrucciones "
                f"de esta tarea. Acceso usando {browser}, sólo para esta tarea "
                "(10 min, hasta 24 acciones). "
                f"Destino: {url}"
            )[:512]
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
                f"{len(arguments.get('postconditions', []))} comprobaciones de resultado; "
                f"objetivo: {objective}"
            )[:512]
        if self._is_mcp_tool(authorization.tool_name):
            if self._tool_broker is None:
                raise ValueError("MCP broker is unavailable")
            definition = self._tool_broker.definition(authorization.tool_name)
            if (
                definition is None
                or not definition.provider_label
                or not definition.external_destination
            ):
                raise ValueError("MCP confirmation metadata is invalid")
            fields = ", ".join(sorted(authorization.normalized_arguments)) or "ninguno"
            action = authorization.tool_name.split("__", 1)[-1].replace("_", " ")
            return (
                f"{definition.provider_label}: {action}; enviará los campos [{fields}] a "
                f"{definition.external_destination}"
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
        if result.tool_name in NATIVE_RECEIPT_SOURCES:
            if (authorization is None or authorization.decision is not PolicyDecision.ALLOW
                    or authorization.call_id != result.call_id
                    or authorization.tool_name != result.tool_name
                    or not result_is_verified(result)):
                raise ValueError("native command acknowledgement is invalid")
            payload = json.loads(result.output)
            if not native_receipt_is_valid(
                result.tool_name, payload, authorization.normalized_arguments
            ):
                raise ValueError("native receipt does not match authorization")
            return native_receipt_summary(result.tool_name, payload)
        if result.tool_name == "smart_tv_control":
            if (authorization is None or authorization.decision is not PolicyDecision.ALLOW
                    or authorization.call_id != result.call_id
                    or authorization.tool_name != result.tool_name
                    or not result_is_verified(result)):
                raise ValueError("TV command acknowledgement is invalid")
            payload = decode_native_json(result.output)
            if not smart_tv_receipt_is_valid(payload, authorization.normalized_arguments):
                raise ValueError("TV receipt does not match authorization")
            return "Envié la orden al televisor. No he comprobado su estado final."
        if is_schedule_tool(result.tool_name):
            return format_schedule_result(result)
        if is_goal_tool(result.tool_name) or is_skill_tool(result.tool_name):
            return format_goal_result(result)
        if is_foreign_plugin_tool(result.tool_name):
            return format_foreign_plugin_result(result)
        if result.tool_name in {"browser_task_open", "browser_task_action", "browser_task_close"}:
            try:
                payload = json.loads(result.output)
            except (ValueError, RecursionError) as error:
                raise ValueError("browser task result is invalid") from error
            if (not isinstance(payload, dict)
                    or not isinstance(payload.get("structuredContent"), dict)):
                raise ValueError("browser task result is invalid")
            data = payload["structuredContent"]
            if result.tool_name == "browser_task_open":
                if (authorization is None or not result_is_verified(result)
                        or data.get("verified") is not True
                        or not isinstance(data.get("session_id"), str)
                        or data.get("expected_url")
                        != authorization.normalized_arguments.get("url")):
                    raise ValueError("browser task access is unverified")
                if authorization.normalized_arguments.get("mode") == "chrome":
                    return (
                        "Accedí al sitio en Chrome directo mediante CDP, con permiso temporal "
                        "para realizar esta tarea."
                    )
                return "Accedí al sitio con permiso temporal para realizar esta tarea."
            if result.tool_name == "browser_task_close":
                if data.get("closed") is not True:
                    raise ValueError("browser task close is unverified")
                return "Finalicé el acceso temporal al navegador."
            if data.get("verified") is True and result_is_verified(result):
                return "Comprobé el resultado de la acción en la pestaña de esta tarea."
            return (
                "La acción quedó sin verificar. Me detuve para evitar repetir cambios."
            )
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
        if result.tool_name == "spotlight_open":
            payload = json.loads(result.output)
            name = normalized_label(payload.get("name"), 500)
            if payload.get("opened") is not True:
                raise ValueError("Spotlight result is invalid")
            return f"Abrí {name} desde Spotlight."
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
        if result.tool_name == "computer_use":
            report = ComputerUseReport.model_validate_json(result.output)
            status, steps = report.status, report.steps
            bundle_identifier, reason_code = (
                report.application_bundle_identifier,
                report.reason_code,
            )
            expected = authorization.normalized_arguments if authorization else {}
            max_steps = expected.get("max_steps")
            if (
                authorization is None
                or type(steps) is not int
                or type(max_steps) is not int
                or not 0 <= steps <= max_steps
                or not isinstance(bundle_identifier, str)
                or bundle_identifier != expected.get("application_bundle_identifier")
                or not isinstance(reason_code, str)
            ):
                raise ValueError("computer result is invalid")
            if status == "completed":
                if (
                    reason_code != "objective_complete"
                    or not report.postcondition_checks
                    or not all(check.satisfied for check in report.postcondition_checks)
                    or report.failed_check is not None
                ):
                    raise ValueError("computer result reason is invalid")
                if not result_is_verified(result):
                    raise ValueError("computer result is unverified")
                if all(check.kind == "action_performed" for check in report.postcondition_checks):
                    return "Ejecuté y comprobé el gesto solicitado."
                return "Completé el objetivo y comprobé el resultado en la aplicación."
            if status == "step_limit":
                if reason_code != "step_limit" or steps != max_steps:
                    raise ValueError("computer result reason is invalid")
                return "Me detuve al alcanzar el límite de pasos sin verificar el objetivo."
            reasons = {
                "sensitive_action": "la siguiente acción era sensible",
                "unsupported_action": "la siguiente acción no está permitida",
                "uncertain_state": "no pudo verificar el estado visual con seguridad",
                "user_takeover": "detectó que retomaste el teclado o el puntero",
                "missing_postcondition": "falta una condición comprobable para el resultado",
                "postcondition_failed": "el resultado observado no coincide con lo solicitado",
                "step_limit": "alcanzó el límite de pasos sin verificar el objetivo",
            }
            reason = reasons.get(reason_code)
            if reason is None:
                raise ValueError("computer result reason is invalid")
            if status == "partial":
                return (
                    f"Jarvis detuvo el control visual porque {reason}. "
                    "El resultado final sigue sin verificar. Me detuve para evitar repetir cambios."
                )
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
        if result.tool_name.startswith("mcp_"):
            server_id = result.metadata.get("server_id")
            source = result.metadata.get("source")
            if (
                authorization is None
                or authorization.tool_name != result.tool_name
                or not isinstance(server_id, str)
                or source not in {"mcp_2025_11_25_http", "mcp_2025_11_25_stdio"}
                or result.metadata.get("verified") is not True
                # The relay already bounded this message; several full emails exceed 64 KiB.
                or len(result.output.encode("utf-8")) > MAX_MCP_MESSAGE_BYTES
            ):
                raise ValueError("MCP result is invalid")
            payload = json.loads(result.output)
            if not isinstance(payload, dict):
                raise ValueError("MCP result is invalid")
            server = normalized_label(server_id, 128)
            return readable_mcp_result(
                payload, server_label=server, tool_name=result.tool_name,
            )[:8_192]
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
            port_text = ", ".join(str(port) for port in ports) if ports else "sin puertos abiertos"
            observations.append(f"{address}: {port_text}")
        return f"Sondeo TCP completado en {target}. " + "; ".join(observations)

    def _is_plugin_tool(self, tool_name: str) -> bool:
        return (
            tool_name.startswith("plugin_")
            and self._tool_broker is not None
            and self._tool_broker.definition(tool_name) is not None
        )

    def _is_mcp_tool(self, tool_name: str) -> bool:
        if not tool_name.startswith("mcp_") or self._tool_broker is None:
            return False
        definition = self._tool_broker.definition(tool_name)
        return bool(
            definition is not None
            and definition.provider_label
            and definition.provider_label.startswith("MCP: ")
            and definition.external_destination
            and definition.external_destination.startswith(("http:", "stdio:"))
        )


def normalized_label(value: object, max_characters: int) -> str:
    if not isinstance(value, str):
        raise ValueError("result label is invalid")
    normalized = " ".join(value.split())
    if not normalized or len(normalized) > max_characters or not normalized.isprintable():
        raise ValueError("result label is invalid")
    return normalized
