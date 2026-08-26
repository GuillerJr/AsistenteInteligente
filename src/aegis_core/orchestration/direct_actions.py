from __future__ import annotations

import re

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
_WAKE_PREFIX = re.compile(r"^jarvis(?:[\s,:;-]+)", re.IGNORECASE)


def direct_tool_call(request: UserRequest) -> ToolCall | None:
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

    normalized = command.casefold().rstrip(".!?")
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
    arguments: dict[str, str],
) -> ToolCall:
    return ToolCall(
        call_id=f"local-{request.request_id}",
        tool_name=tool_name,
        arguments=arguments,
        requested_by=role,
    )
