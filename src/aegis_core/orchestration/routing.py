from __future__ import annotations

import hashlib
import json
import re

from aegis_core.contracts import (
    AgentRole,
    InputModality,
    RiskLevel,
    RouteDecision,
    ToolExecutionResult,
    UserRequest,
)
from aegis_core.engineering import (
    ENGINEERING_RESEARCH_METADATA,
    EngineeringResearchPolicy,
    engineering_role_for_request,
    is_engineering_request,
)
from aegis_core.request_intent import (
    google_request_tools,
    is_explicit_native_ui_request,
    smart_tv_discovery_intent,
)
from aegis_core.skills import (
    SkillActivation,
    selection_instruction_text,
)

CODE_SECURITY_ROUTE_TERMS = frozenset(
    {
        "archivo",
        "ciberseguridad",
        "code",
        "código",
        "cybersecurity",
        "escanea",
        "escanear",
        "file",
        "filevault",
        "fichero",
        "firewall",
        "gatekeeper",
        "git",
        "listener",
        "network",
        "port",
        "process",
        "proceso",
        "puerto",
        "red",
        "security",
        "seguridad",
        "sip",
        "socket",
        "terminal",
    }
)


EXPLICIT_CODE_SECURITY_ROUTE_TERMS = CODE_SECURITY_ROUTE_TERMS - {
    "archivo", "file", "fichero", "red", "network", "process", "proceso",
    "port", "puerto", "listener",
}


HIGH_RISK_SECURITY_TERMS = frozenset(
    {
        "ataque",
        "attack",
        "backdoor",
        "credencial",
        "credenciales",
        "credential",
        "credentials",
        "exploit",
        "exploitation",
        "explotación",
        "inyección",
        "injection",
        "malware",
        "password",
        "passwords",
        "privilege",
        "privilegios",
        "ransomware",
        "rootkit",
        "secreto",
        "secretos",
        "token",
        "tokens",
        "vulnerabilidad",
        "vulnerabilidades",
        "vulnerability",
    }
)


CRITICAL_RISK_SECURITY_TERMS = frozenset(
    {
        "backdoor",
        "exfiltra",
        "exfiltrar",
        "exfiltration",
        "persistencia",
        "persistence",
        "ransomware",
        "rootkit",
    }
)


TOOL_ACTION_TERMS = frozenset(
    {
        "diagnostica",
        "diagnosticar",
        "diagnose",
        "abre",
        "abrir",
        "add",
        "agrega",
        "agregar",
        "check",
        "control",
        "controla",
        "controlar",
        "create",
        "crea",
        "crear",
        "ejecuta",
        "ejecutar",
        "envia",
        "envía",
        "enviar",
        "escanea",
        "escanear",
        "execute",
        "fetch",
        "interact",
        "interactua",
        "interactúa",
        "interactuar",
        "lee",
        "leer",
        "list",
        "lista",
        "listar",
        "manda",
        "mandar",
        "muestra",
        "muestrame",
        "mostrar",
        "navigate",
        "navega",
        "navegar",
        "open",
        "programa",
        "programar",
        "read",
        "revisa",
        "revisar",
        "run",
        "scan",
        "send",
        "show",
    }
)


TOOL_OBJECT_TERMS = frozenset(
    {
        "almacenamiento",
        "batería",
        "bateria",
        "battery",
        "conexión",
        "conexion",
        "conectividad",
        "connection",
        "connectivity",
        "storage",
        "agenda",
        "app",
        "application",
        "applications",
        "aplicación",
        "aplicaciones",
        "audio",
        "archivo",
        "archivos",
        "atajo",
        "atajos",
        "browser",
        "botón",
        "botones",
        "button",
        "calendario",
        "calendar",
        "contact",
        "contacto",
        "contactos",
        "chrome",
        "code",
        "computer",
        "correo",
        "correos",
        "código",
        "email",
        "emails",
        "enlace",
        "evento",
        "event",
        "events",
        "file",
        "files",
        "filevault",
        "firefox",
        "firewall",
        "gatekeeper",
        "git",
        "inbox",
        "internet",
        "link",
        "listener",
        "listeners",
        "mail",
        "mac",
        "mensaje",
        "mensajes",
        "recordatorio",
        "recordatorios",
        "reminder",
        "reminders",
        "navegador",
        "network",
        "performance",
        "reproducción",
        "reproduccion",
        "page",
        "pantalla",
        "página",
        "process",
        "processes",
        "proceso",
        "procesos",
        "puerto",
        "puertos",
        "red",
        "rendimiento",
        "safari",
        "screen",
        "security",
        "seguridad",
        "shortcut",
        "smartphone",
        "televisor",
        "teléfono",
        "telefono",
        "tizen",
        "tv",
        "webos",
        "android",
        "iphone",
        "shortcuts",
        "spotlight",
        "site",
        "sitio",
        "terminal",
        "volumen",
        "url",
        "ventana",
        "web",
        "window",
    }
)


WEB_RESEARCH_ACTION_TERMS = frozenset(
    {
        "busca",
        "buscar",
        "googlea",
        "googlear",
        "investiga",
        "investigar",
        "research",
        "search",
    }
)


CURRENT_INFORMATION_TERMS = frozenset(
    {
        "clima",
        "cotización",
        "cotizacion",
        "news",
        "noticias",
        "precio",
        "precios",
        "price",
        "prices",
        "weather",
    }
)


INFORMATION_REQUEST_TERMS = frozenset(
    {
        "actual",
        "ahora",
        "cómo",
        "como",
        "cuál",
        "cuáles",
        "cual",
        "cuales",
        "dime",
        "hoy",
        "latest",
        "muéstrame",
        "muestrame",
        "qué",
        "que",
        "today",
    }
)


MAIL_TERMS = frozenset({"correo", "correos", "email", "emails", "inbox", "mail"})


CALENDAR_TERMS = frozenset(
    {"agenda", "calendario", "calendar", "evento", "event", "events"}
)


CONTACTS_TERMS = frozenset({"contact", "contacts", "contacto", "contactos"})


REMINDERS_TERMS = frozenset(
    {"recordatorio", "recordatorios", "reminder", "reminders"}
)


FILE_TERMS = frozenset({"archivo", "archivos", "code", "código", "file", "files"})


NETWORK_TERMS = frozenset(
    {"network", "port", "ports", "puerto", "puertos", "red", "socket"}
)


SYSTEM_OBSERVE_TERMS = frozenset(
    {"audio", "carga", "conexión", "conexion", "conectividad", "connection", "connectivity",
     "network", "performance", "red", "rendimiento", "volumen"}
)


MEDIA_TERMS = frozenset(
    {
        "audio",
        "canción",
        "cancion",
        "media",
        "música",
        "musica",
        "reproducción",
        "reproduccion",
        "track",
        "volumen",
        "volume",
    }
)


TERMINAL_DIAGNOSTIC_TERMS = frozenset(
    {
        "filevault",
        "firewall",
        "gatekeeper",
        "git",
        "listener",
        "listeners",
        "process",
        "processes",
        "proceso",
        "procesos",
        "security",
        "seguridad",
        "sip",
        "terminal",
    }
)


APPLICATION_TERMS = frozenset(
    {
        "app",
        "application",
        "aplicación",
        "browser",
        "chrome",
        "computer",
        "firefox",
        "mac",
        "navegador",
        "pantalla",
        "safari",
        "screen",
        "ventana",
        "window",
    }
)


OPEN_ACTION_TERMS = frozenset({"abre", "abrir", "navigate", "navega", "navegar", "open"})


READ_ACTION_TERMS = frozenset(
    {"check", "fetch", "lee", "leer", "list", "lista", "listar", "read", "revisa", "revisar"}
)


SEND_ACTION_TERMS = frozenset({"envia", "envía", "enviar", "manda", "mandar", "send"})


CREATE_ACTION_TERMS = frozenset(
    {"add", "agrega", "agregar", "create", "crea", "crear", "programa", "programar"}
)


CONTROL_ACTION_TERMS = frozenset(
    {"control", "controla", "controlar", "interact", "interactua", "interactúa", "interactuar"}
)


SCAN_ACTION_TERMS = frozenset({"escanea", "escanear", "scan"})


LOCAL_PROVIDER_RETRY_SECONDS = 30.0


PLANNER_LOCAL_READ_TOOLS = frozenset(
    {
        "calendar_list_events",
        "contacts_search",
        "mail_list_recent",
        "reminders_list",
        "spotlight_search",
        "web_fetch",
        "web_research",
    }
)


def _route_request(
    request: UserRequest,
    skill: SkillActivation | None = None,
) -> RouteDecision:
    modalities = request.modalities
    lowered = request.text.casefold()
    terms = frozenset(re.findall(r"\w+", lowered))
    local_voice_transcript = request.metadata.get("speech_on_device") is True
    engineering_role = engineering_role_for_request(request)
    if engineering_role is not None:
        role = engineering_role
    elif InputModality.VIDEO in modalities or (
        InputModality.AUDIO in modalities and not local_voice_transcript
    ):
        role = AgentRole.OMNI
    elif InputModality.IMAGE in modalities:
        role = AgentRole.VISION
    elif (smart_tv_discovery_intent(request.text) == "discover"
          or is_explicit_native_ui_request(request.text)):
        role = AgentRole.PLANNER
    elif (skill is not None and skill.manifest.role is AgentRole.PLANNER
          and google_request_tools(request.text)):
        # A service query can contain security/code words as its search subject.
        # Its matched domain procedure still needs the peer's planner-only tools.
        role = AgentRole.PLANNER
    elif not terms.isdisjoint(EXPLICIT_CODE_SECURITY_ROUTE_TERMS | HIGH_RISK_SECURITY_TERMS):
        role = AgentRole.CODE_SECURITY
    elif skill is not None:
        role = skill.manifest.role
    elif not terms.isdisjoint(CODE_SECURITY_ROUTE_TERMS):
        role = AgentRole.CODE_SECURITY
    else:
        role = AgentRole.PLANNER
    risk = RiskLevel.MEDIUM
    if role is AgentRole.CODE_SECURITY and not terms.isdisjoint(HIGH_RISK_SECURITY_TERMS):
        risk = RiskLevel.HIGH
        if not terms.isdisjoint(CRITICAL_RISK_SECURITY_TERMS) and not terms.isdisjoint(
            TOOL_ACTION_TERMS
        ):
            risk = RiskLevel.CRITICAL
    reason = (
        f"deterministic local skill: {skill.manifest.skill_id}"
        if skill is not None and role is skill.manifest.role
        else "deterministic local route"
    )
    return RouteDecision(role=role, risk=risk, reason=reason)


def _request_may_need_tools(
    request: UserRequest,
    skill: SkillActivation | None = None,
    *,
    capability_gap: bool = False,
) -> bool:
    if capability_gap:
        return True
    if skill is not None and skill.manifest.starter_tools:
        return True
    lowered = selection_instruction_text(request.text).casefold()
    ordered_terms = re.findall(r"\w+", lowered)
    if not ordered_terms:
        return False
    terms = frozenset(ordered_terms)
    if "https://" in request.text.casefold() and not terms.isdisjoint(TOOL_ACTION_TERMS):
        return True
    if not terms.isdisjoint(WEB_RESEARCH_ACTION_TERMS):
        return True
    if not terms.isdisjoint(TOOL_ACTION_TERMS) and not terms.isdisjoint(
        TOOL_OBJECT_TERMS
    ):
        return True
    if terms.isdisjoint(CURRENT_INFORMATION_TERMS):
        return False
    return (
        ordered_terms[0] in CURRENT_INFORMATION_TERMS
        or not terms.isdisjoint(INFORMATION_REQUEST_TERMS)
    )


def _tool_names_for_request(request: UserRequest) -> frozenset[str]:
    if smart_tv_discovery_intent(request.text) == "discover":
        return frozenset({"smart_tv_discover"})
    if is_explicit_native_ui_request(request.text):
        return frozenset({"computer_use"})
    lowered = selection_instruction_text(request.text).casefold()
    raw_lowered = request.text.casefold()
    ordered_terms = re.findall(r"\w+", lowered)
    terms = frozenset(ordered_terms)
    names: set[str] = set()
    explicit_web_search = not terms.isdisjoint(WEB_RESEARCH_ACTION_TERMS)
    visible_browser_search = explicit_web_search and any(
        marker in lowered
        for marker in (
            " en chrome",
            " en el navegador",
            " en firefox",
            " en google chrome",
            " en safari",
            " in chrome",
            " in firefox",
            " in google chrome",
            " in safari",
            " in the browser",
        )
    )

    if explicit_web_search or (
        not terms.isdisjoint(CURRENT_INFORMATION_TERMS)
        and (
            (ordered_terms and ordered_terms[0] in CURRENT_INFORMATION_TERMS)
            or not terms.isdisjoint(INFORMATION_REQUEST_TERMS)
        )
    ):
        names.add("browser_search" if visible_browser_search else "web_research")

    if "https://" in lowered or (
        "https://" in raw_lowered
        and not terms.isdisjoint(OPEN_ACTION_TERMS | READ_ACTION_TERMS)
    ):
        names.add(
            "browser_open_url"
            if not terms.isdisjoint(OPEN_ACTION_TERMS)
            else "web_fetch"
        )

    if not terms.isdisjoint(MAIL_TERMS):
        if not terms.isdisjoint(SEND_ACTION_TERMS):
            names.add("mail_send_message")
        elif not terms.isdisjoint(READ_ACTION_TERMS):
            names.add("mail_list_recent")
        else:
            names.update({"mail_list_recent", "mail_send_message"})

    if not terms.isdisjoint(CALENDAR_TERMS):
        if not terms.isdisjoint(CREATE_ACTION_TERMS):
            names.add("calendar_create_event")
        elif not terms.isdisjoint(READ_ACTION_TERMS):
            names.add("calendar_list_events")
        else:
            names.update({"calendar_create_event", "calendar_list_events"})

    if not terms.isdisjoint(REMINDERS_TERMS):
        if not terms.isdisjoint(CREATE_ACTION_TERMS):
            names.add("reminder_create")
        elif not terms.isdisjoint({"completa", "completar", "complete", "termina"}):
            names.add("reminder_complete")
        elif not terms.isdisjoint(READ_ACTION_TERMS):
            names.add("reminders_list")
        else:
            names.update({"reminder_create", "reminder_complete", "reminders_list"})

    if not terms.isdisjoint(CONTACTS_TERMS):
        if not terms.isdisjoint(CREATE_ACTION_TERMS):
            names.add("contact_create")
        else:
            names.add("contacts_search")

    if not terms.isdisjoint({"atajo", "atajos", "shortcut", "shortcuts"}):
        names.add("shortcut_run")
    if not terms.isdisjoint(APPLICATION_TERMS):
        if not terms.isdisjoint(CONTROL_ACTION_TERMS):
            names.add("computer_use")
        elif not terms.isdisjoint(OPEN_ACTION_TERMS):
            names.add("application_open")
    # Mentioning a file (including an output the user wants generated) is not
    # a request to read the local filesystem. Already attached text is input, not
    # a path; neither case should expose unrelated tools or their model route.
    file_read = re.match(
        r"^(?:¿?puedes |por favor |can you )?(?:lee|read|revisa|analiza|examina|resume) "
        r"(?:(?:el|la|los|las|the) )?(?:archivo|archivos|file|files|codigo|código)\b",
        lowered.strip(),
    )
    supplied_names = [d.name.casefold() for d in request.documents]
    stored_documents = request.metadata.get("chat_document_context", ())
    if isinstance(stored_documents, (list, tuple)):
        supplied_names.extend(d.get("name", "").casefold() for d in stored_documents
            if isinstance(d, dict) and isinstance(d.get("name"), str))
    supplied_file = bool(supplied_names) and ("adjunt" in lowered or any(
        name and name in raw_lowered for name in supplied_names))
    if file_read and not supplied_file:
        names.add("filesystem_read_text")
    if not terms.isdisjoint(NETWORK_TERMS):
        names.add(
            "network_discover_hosts"
            if not terms.isdisjoint(SCAN_ACTION_TERMS)
            else "system_observe_status"
        )
    if not terms.isdisjoint(SYSTEM_OBSERVE_TERMS):
        names.add("system_observe_status")
    if not terms.isdisjoint({"batería", "bateria", "battery"}):
        names.add("system_power_status")
    if not terms.isdisjoint({"almacenamiento", "storage", "disco", "disk", "espacio"}):
        names.add("system_storage_status")
    if "spotlight" in terms:
        names.add(
            "spotlight_open"
            if not terms.isdisjoint(OPEN_ACTION_TERMS)
            else "spotlight_search"
        )
    if not terms.isdisjoint(MEDIA_TERMS):
        if not terms.isdisjoint({"silencia", "mute", "volumen", "volume"}):
            names.add("system_audio_set")
        if not terms.isdisjoint(
            {"anterior", "next", "pausa", "play", "previous", "reanuda", "reproduce", "siguiente"}
        ):
            names.add("media_control")
    if not terms.isdisjoint(TERMINAL_DIAGNOSTIC_TERMS):
        names.add("terminal_run_template")
    if is_engineering_request(request):
        names.add("filesystem_read_text")
        if (
            request.metadata.get(ENGINEERING_RESEARCH_METADATA)
            == EngineeringResearchPolicy.OFFLINE.value
        ):
            names.difference_update({"browser_search", "web_fetch", "web_research"})
    return frozenset(names)


def _respect_engineering_offline(
    request: UserRequest,
    names: frozenset[str],
    offline_safe_names: frozenset[str] = frozenset(),
) -> frozenset[str]:
    if (
        is_engineering_request(request)
        and request.metadata.get(ENGINEERING_RESEARCH_METADATA)
        != EngineeringResearchPolicy.PUBLIC_WEB.value
    ):
        # Apply this after Skill starter tools and dynamic MCP tools have been merged.
        # Activating a local Skill must never override the CLI's network policy.
        return frozenset(
            name
            for name in names
            if (not name.startswith(("plugin_", "mcp_")) or name in offline_safe_names)
            and name
            not in {
                "browser_search",
                "web_fetch",
                "web_research",
                "browser_task_open",
                "browser_task_action",
                "browser_task_close",
            }
        )
    return names


def _effective_tool_names(
    request: UserRequest,
    skill: SkillActivation | None,
    *,
    capability_gap: bool = False,
) -> frozenset[str]:
    if capability_gap:
        return _respect_engineering_offline(request, frozenset({"web_research"}))
    requested = _tool_names_for_request(request)
    if skill is None:
        return _respect_engineering_offline(request, requested)
    manifest = skill.manifest
    return _respect_engineering_offline(
        request, (requested & manifest.allowed_tools) | manifest.starter_tools,
    )


def _swarm_roles(route: RouteDecision) -> tuple[AgentRole, ...]:
    roles = [route.role]
    if route.risk in {RiskLevel.HIGH, RiskLevel.CRITICAL} and (
        AgentRole.CRITICAL_REASONER not in roles
    ):
        roles.append(AgentRole.CRITICAL_REASONER)
    return tuple(roles)


def _bounded_browser_result_reference(
    result: ToolExecutionResult,
    *,
    maximum_bytes: int = 16_384,
) -> ToolExecutionResult:
    """Bound reference copies only; execution verification/audit keep the raw result."""
    if len(result.model_dump_json().encode()) <= maximum_bytes:
        return result
    output_digest = hashlib.sha256(result.output.encode()).hexdigest()
    try:
        payload = json.loads(result.output)
        data = payload["structuredContent"]
        if not isinstance(payload, dict) or not isinstance(data, dict):
            raise ValueError("browser_task_evidence_invalid")
        snapshot = data.get("snapshot")
        if not isinstance(snapshot, str):
            raise ValueError("browser_task_evidence_invalid")
        data.update(snapshot_truncated=True, excerpt_only=True)
        payload["original_output_sha256"] = output_digest
        metadata = {**result.metadata, "reference_excerpt_only": True}

        def reference(lines: list[str]) -> ToolExecutionResult:
            data["snapshot"] = "".join(lines)
            return result.model_copy(
                update={
                    "output": json.dumps(payload, ensure_ascii=False, allow_nan=False),
                    "metadata": metadata,
                }
            )

        lines = snapshot.splitlines(keepends=True)
        candidate = reference([])
        if len(candidate.model_dump_json().encode()) > maximum_bytes:
            raise ValueError("browser_task_evidence_exceeds_limit")
        low, high = 0, len(lines)
        while low < high:
            middle = (low + high + 1) // 2
            if len(reference(lines[:middle]).model_dump_json().encode()) <= maximum_bytes:
                low = middle
            else:
                high = middle - 1
        return reference(lines[:low])
    except (ValueError, KeyError, TypeError, RecursionError):
        # Even a non-snapshot structure may exceed the reference budget. Keep a
        # bounded failure record instead of dropping evidence and trusting "done".
        return ToolExecutionResult(
            call_id=result.call_id,
            tool_name=result.tool_name,
            success=False,
            error_code="browser_task_evidence_limit",
            metadata={
                "verified": False,
                "goal_verified": False,
                "status": "partial",
                "reference_excerpt_only": True,
                "original_output_sha256": output_digest,
            },
        )
