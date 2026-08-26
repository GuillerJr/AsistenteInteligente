from __future__ import annotations

import re
from datetime import datetime, timedelta
from pathlib import PurePosixPath

from aegis_core.contracts import AgentRole, InputModality, ToolCall, UserRequest

_APPLICATIONS = {
    "calendario": "com.apple.iCal",
    "calendar": "com.apple.iCal",
    "chrome": "com.google.Chrome",
    "correo": "com.apple.mail",
    "firefox": "org.mozilla.firefox",
    "google chrome": "com.google.Chrome",
    "mail": "com.apple.mail",
    "notas": "com.apple.Notes",
    "notes": "com.apple.Notes",
    "preview": "com.apple.Preview",
    "safari": "com.apple.Safari",
    "spotify": "com.spotify.client",
    "vista previa": "com.apple.Preview",
    "xcode": "com.apple.dt.Xcode",
}
_DIAGNOSTICS = {
    "diagnóstico de seguridad": "security_posture",
    "estado de git": "git_status",
    "git status": "git_status",
    "list listeners": "list_listeners",
    "list processes": "list_processes",
    "lista los listeners": "list_listeners",
    "lista los procesos": "list_processes",
    "lista los puertos abiertos": "list_listeners",
    "muestra el estado de git": "git_status",
    "muestra los procesos": "list_processes",
    "revisa el estado de git": "git_status",
    "revisa la postura de seguridad": "security_posture",
    "revisa la seguridad de macos": "security_posture",
    "security posture": "security_posture",
}
_MAIL_READ_COMMANDS = {
    "check my email": False,
    "check my mail": False,
    "lista mis correos": False,
    "muestra mi correo": False,
    "muéstrame mi correo": False,
    "revisa mi correo": False,
    "show my mail": False,
    "lista mis correos no leídos": True,
    "muestra mis correos no leídos": True,
    "muéstrame mis correos no leídos": True,
    "revisa mis correos no leídos": True,
    "show unread mail": True,
}
_TODAY_CALENDAR_COMMANDS = frozenset(
    {
        "lista mis eventos de hoy",
        "muestra mi agenda",
        "muestra mi calendario",
        "qué tengo hoy",
        "que tengo hoy",
        "qué tengo hoy en el calendario",
        "que tengo hoy en el calendario",
        "revisa mi agenda",
        "revisa mi calendario",
        "what is on my calendar today",
    }
)
_RUNTIME_COMMANDS = frozenset(
    {
        "describe este mac",
        "describe mi mac",
        "describe this mac",
        "qué hardware tiene este mac",
        "que hardware tiene este mac",
        "qué mac tengo",
        "que mac tengo",
        "what hardware does this mac have",
    }
)
_POWER_STATUS_COMMANDS = frozenset(
    {
        "battery status",
        "cómo está la batería",
        "como esta la bateria",
        "cuánta batería queda",
        "cuanta bateria queda",
        "estado de batería",
        "estado de la batería",
        "estado de bateria",
        "estado de la bateria",
        "how much battery is left",
    }
)
_APPLICATION_PATTERN = re.compile(
    r"^(?:abre|abrir|open)\s+"
    r"(?:(?:la|el)\s+)?"
    r"(?:(?:aplicación|aplicacion|app|application)\s+)?"
    r"(?P<name>.+?)$",
    re.IGNORECASE,
)
_SHORTCUT_PATTERN = re.compile(
    r"^(?:ejecuta|ejecutar|execute|run)\s+"
    r"(?:(?:el|un)\s+)?(?:atajo|shortcut)\s+(?P<name>.+?)$",
    re.IGNORECASE,
)
_WEB_RESEARCH_PATTERN = re.compile(
    r"^(?:busca|buscar|investiga|investigar|research(?:\s+for)?|search(?:\s+for)?)\s+"
    r"(?P<query>.+?)$",
    re.IGNORECASE,
)
_WEB_FETCH_PATTERN = re.compile(
    r"^(?:lee|leer|fetch|read)\s+(?P<url>https://\S+)$",
    re.IGNORECASE,
)
_BROWSER_OPEN_PATTERN = re.compile(
    r"^(?:abre|abrir|open)\s+(?P<url>https://\S+)$",
    re.IGNORECASE,
)
_FILE_READ_PATTERN = re.compile(
    r"^(?:lee|leer|read)\s+(?:(?:el|un|the)\s+)?(?:archivo|file)\s+(?P<path>.+?)$",
    re.IGNORECASE,
)
_WAKE_PREFIX = re.compile(r"^jarvis(?:[\s,:;-]+)", re.IGNORECASE)


def direct_tool_call(request: UserRequest, *, now: datetime | None = None) -> ToolCall | None:
    if (
        request.image is not None
        or InputModality.VIDEO in request.modalities
        or (
            InputModality.AUDIO in request.modalities
            and request.metadata.get("speech_on_device") is not True
        )
        or request.metadata.get("force_remote") is True
    ):
        return None
    command = " ".join(request.text.strip().split())
    command = _WAKE_PREFIX.sub("", command, count=1)
    if not command:
        return None

    normalized = command.casefold().lstrip("¿¡").rstrip(".!?")
    if normalized in _RUNTIME_COMMANDS:
        return _call(
            request,
            role=AgentRole.PLANNER,
            tool_name="system_describe_runtime",
            arguments={},
        )
    if normalized in _POWER_STATUS_COMMANDS:
        return _call(
            request,
            role=AgentRole.PLANNER,
            tool_name="system_power_status",
            arguments={},
        )

    unread_only = _MAIL_READ_COMMANDS.get(normalized)
    if unread_only is not None:
        return _call(
            request,
            role=AgentRole.PLANNER,
            tool_name="mail_list_recent",
            arguments={"limit": 10, "unread_only": unread_only},
        )

    if normalized in _TODAY_CALENDAR_COMMANDS:
        current = now or datetime.now().astimezone()
        if current.tzinfo is None or current.utcoffset() is None:
            raise ValueError("calendar planning requires timezone-aware local time")
        start = current.replace(hour=0, minute=0, second=0, microsecond=0)
        return _call(
            request,
            role=AgentRole.PLANNER,
            tool_name="calendar_list_events",
            arguments={
                "start_at": start.isoformat(),
                "end_at": (start + timedelta(days=1)).isoformat(),
                "limit": 20,
            },
        )

    research_match = _WEB_RESEARCH_PATTERN.fullmatch(command)
    if research_match is not None:
        query = research_match.group("query").rstrip(".!?")
        if 2 <= len(query) <= 300:
            return _call(
                request,
                role=AgentRole.PLANNER,
                tool_name="web_research",
                arguments={"query": query, "max_results": 3},
            )

    fetch_match = _WEB_FETCH_PATTERN.fullmatch(command)
    if fetch_match is not None:
        url = fetch_match.group("url")
        if 12 <= len(url) <= 2_048:
            return _call(
                request,
                role=AgentRole.PLANNER,
                tool_name="web_fetch",
                arguments={"url": url, "max_characters": 8_000},
            )

    browser_match = _BROWSER_OPEN_PATTERN.fullmatch(command)
    if browser_match is not None:
        url = browser_match.group("url")
        if 12 <= len(url) <= 2_048:
            return _call(
                request,
                role=AgentRole.PLANNER,
                tool_name="browser_open_url",
                arguments={"url": url},
            )

    file_match = _FILE_READ_PATTERN.fullmatch(command)
    if file_match is not None:
        path = file_match.group("path").rstrip(".!?")
        parsed_path = PurePosixPath(path)
        if (
            path
            and len(path) <= 1_024
            and not parsed_path.is_absolute()
            and all(part not in {"", ".", ".."} for part in parsed_path.parts)
        ):
            return _call(
                request,
                role=AgentRole.CODE_SECURITY,
                tool_name="filesystem_read_text",
                arguments={"path": path, "max_bytes": 8_192},
            )

    template = _DIAGNOSTICS.get(normalized)
    if template is not None:
        return _call(
            request,
            role=AgentRole.CODE_SECURITY,
            tool_name="terminal_run_template",
            arguments={"template": template},
        )

    shortcut_match = _SHORTCUT_PATTERN.fullmatch(command)
    if shortcut_match is not None:
        name = shortcut_match.group("name").rstrip(".!?")
        if (
            name
            and len(name) <= 128
            and name not in {".", ".."}
            and "/" not in name
            and "\\" not in name
        ):
            return _call(
                request,
                role=AgentRole.PLANNER,
                tool_name="shortcut_run",
                arguments={"name": name},
            )

    application_match = _APPLICATION_PATTERN.fullmatch(command)
    if application_match is None:
        return None
    application_name = application_match.group("name").casefold().rstrip(".!?")
    bundle_identifier = _APPLICATIONS.get(application_name)
    if bundle_identifier is None:
        return None
    return _call(
        request,
        role=AgentRole.PLANNER,
        tool_name="application_open",
        arguments={"bundle_identifier": bundle_identifier},
    )


def _call(
    request: UserRequest,
    *,
    role: AgentRole,
    tool_name: str,
    arguments: dict[str, object],
) -> ToolCall:
    return ToolCall(
        call_id=f"local-{request.request_id}",
        tool_name=tool_name,
        arguments=arguments,
        requested_by=role,
    )
