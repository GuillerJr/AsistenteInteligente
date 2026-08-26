from __future__ import annotations

import math
import re
import time
import unicodedata
from datetime import UTC, datetime, timedelta
from decimal import Decimal, InvalidOperation, localcontext
from pathlib import PurePosixPath
from types import MappingProxyType

from aegis_core.contracts import AgentResult, AgentRole, InputModality, ToolCall, UserRequest

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
_MAIL_UNREAD_STATUS_COMMANDS = frozenset(
    {
        "tengo algún correo no leído",
        "tengo algun correo no leido",
        "tengo correos no leídos",
        "tengo correos no leidos",
        "hay correos no leídos",
        "hay correos no leidos",
        "tengo algún correo sin leer",
        "tengo algun correo sin leer",
        "do i have unread email",
        "do i have unread mail",
    }
)
_LATEST_MAIL_COMMANDS = frozenset(
    {
        "cuál es mi último correo",
        "cual es mi ultimo correo",
        "cuál fue mi último correo",
        "cual fue mi ultimo correo",
        "dime mi último correo",
        "dime mi ultimo correo",
        "what is my latest email",
        "what is my latest mail",
    }
)
_CALENDAR_DAY_OFFSETS = MappingProxyType({
    "lista mis eventos de hoy": 0,
    "muestra mi agenda": 0,
    "muestra mi calendario": 0,
    "qué tengo hoy": 0,
    "que tengo hoy": 0,
    "qué tengo hoy en el calendario": 0,
    "que tengo hoy en el calendario": 0,
    "revisa mi agenda": 0,
    "revisa mi calendario": 0,
    "what is on my calendar today": 0,
    "lista mis eventos de mañana": 1,
    "lista mis eventos de manana": 1,
    "muestra mi agenda de mañana": 1,
    "muestra mi agenda de manana": 1,
    "muestra mi calendario de mañana": 1,
    "muestra mi calendario de manana": 1,
    "qué tengo mañana": 1,
    "que tengo mañana": 1,
    "que tengo manana": 1,
    "revisa mi agenda de mañana": 1,
    "revisa mi agenda de manana": 1,
    "revisa mi calendario de mañana": 1,
    "revisa mi calendario de manana": 1,
    "what is on my calendar tomorrow": 1,
})
_CALENDAR_TODAY_STATUS_COMMANDS = frozenset(
    {
        "tengo algún evento hoy",
        "tengo algun evento hoy",
        "tengo eventos hoy",
        "tengo algo en el calendario hoy",
        "hay eventos hoy en mi calendario",
        "do i have any calendar events today",
    }
)
_NEXT_CALENDAR_COMMANDS = frozenset(
    {
        "cuál es mi próximo evento",
        "cual es mi proximo evento",
        "cuál es mi próxima reunión",
        "cual es mi proxima reunion",
        "qué sigue en mi calendario",
        "que sigue en mi calendario",
        "what is my next calendar event",
        "when is my next meeting",
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
_STORAGE_STATUS_COMMANDS = frozenset(
    {
        "cuánto almacenamiento queda",
        "cuanto almacenamiento queda",
        "cuánto espacio libre queda",
        "cuanto espacio libre queda",
        "estado del almacenamiento",
        "how much storage is left",
        "storage status",
    }
)
_SYSTEM_OBSERVE_COMMANDS = MappingProxyType(
    {
        "audio status": "audio",
        "cómo está el volumen": "audio",
        "como esta el volumen": "audio",
        "el mac está silenciado": "audio",
        "el mac esta silenciado": "audio",
        "estado del audio": "audio",
        "estado del volumen": "audio",
        "is my mac muted": "audio",
        "estoy conectado a una red": "network",
        "estado de la red": "network",
        "network status": "network",
        "tengo conexión de red": "network",
        "tengo conexion de red": "network",
        "carga del sistema": "performance",
        "cómo está el rendimiento del mac": "performance",
        "como esta el rendimiento del mac": "performance",
        "estado del rendimiento": "performance",
        "system performance": "performance",
    }
)
_TIME_COMMANDS = frozenset(
    {
        "dime la hora",
        "qué hora es",
        "que hora es",
        "what time is it",
    }
)
_DATE_COMMANDS = frozenset(
    {
        "dime la fecha",
        "qué día es hoy",
        "que dia es hoy",
        "qué fecha es hoy",
        "que fecha es hoy",
        "what day is it",
        "what is today's date",
    }
)
_DATE_TIME_COMMANDS = frozenset(
    {
        "date and time",
        "dime la fecha y hora",
        "fecha y hora",
        "qué fecha y hora es",
        "que fecha y hora es",
    }
)
_TIMEZONE_COMMANDS = frozenset(
    {
        "cuál es mi zona horaria",
        "cual es mi zona horaria",
        "en qué zona horaria estoy",
        "en que zona horaria estoy",
        "what is my time zone",
        "what is my timezone",
    }
)
_UPTIME_COMMANDS = frozenset(
    {
        "cuánto tiempo lleva encendido este mac",
        "cuanto tiempo lleva encendido este mac",
        "cuánto tiempo lleva activo este mac",
        "cuanto tiempo lleva activo este mac",
        "tiempo activo del mac",
        "how long has this mac been on",
        "system uptime",
    }
)
_CLOCK_COMMANDS = (
    _TIME_COMMANDS | _DATE_COMMANDS | _DATE_TIME_COMMANDS | _TIMEZONE_COMMANDS
)
_CALCULATOR_OPERATORS = {
    "+": "+",
    "-": "-",
    "*": "*",
    "/": "/",
    "divided by": "/",
    "dividido entre": "/",
    "dividido para": "/",
    "dividido por": "/",
    "entre": "/",
    "mas": "+",
    "menos": "-",
    "minus": "-",
    "multiplicado por": "*",
    "más": "+",
    "plus": "+",
    "por": "*",
    "times": "*",
    "x": "*",
    "\N{MULTIPLICATION SIGN}": "*",
}
_SPANISH_SMALL = (
    "cero", "uno", "dos", "tres", "cuatro", "cinco", "seis", "siete", "ocho", "nueve",
    "diez", "once", "doce", "trece", "catorce", "quince", "dieciseis", "diecisiete",
    "dieciocho", "diecinueve",
)
_ENGLISH_SMALL = (
    "zero", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine",
    "ten", "eleven", "twelve", "thirteen", "fourteen", "fifteen", "sixteen", "seventeen",
    "eighteen", "nineteen",
)
_SPANISH_TWENTIES = (
    "veinte", "veintiuno", "veintidos", "veintitres", "veinticuatro", "veinticinco",
    "veintiseis", "veintisiete", "veintiocho", "veintinueve",
)
_SPANISH_TENS = {
    30: "treinta",
    40: "cuarenta",
    50: "cincuenta",
    60: "sesenta",
    70: "setenta",
    80: "ochenta",
    90: "noventa",
}
_ENGLISH_TENS = {
    20: "twenty",
    30: "thirty",
    40: "forty",
    50: "fifty",
    60: "sixty",
    70: "seventy",
    80: "eighty",
    90: "ninety",
}
_SPANISH_MONTHS = (
    "enero",
    "febrero",
    "marzo",
    "abril",
    "mayo",
    "junio",
    "julio",
    "agosto",
    "septiembre",
    "octubre",
    "noviembre",
    "diciembre",
)
_SPANISH_WEEKDAYS = (
    "lunes",
    "martes",
    "miércoles",
    "jueves",
    "viernes",
    "sábado",
    "domingo",
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
_CALCULATOR_NUMBER = r"[+-]?(?:\d{1,18}(?:[.,]\d{1,6})?|[.,]\d{1,6})"
_CALCULATOR_WORD_NUMBER = r"[^\W\d_]+(?:[\s-]+[^\W\d_]+){0,3}?"
_CALCULATOR_OPERAND = rf"(?:{_CALCULATOR_NUMBER}|{_CALCULATOR_WORD_NUMBER})"
_CALCULATOR_PATTERN = re.compile(
    rf"^(?:calcula|calculate|cuánto es|cuanto es|what is)\s+"
    rf"(?P<left>{_CALCULATOR_OPERAND})\s*"
    r"(?P<operator>dividido\s+(?:entre|para|por)|divided\s+by|"
    r"multiplicado\s+por|entre|menos|minus|más|mas|plus|por|times|"
    r"[+*/x\N{MULTIPLICATION SIGN}-])"
    rf"\s*(?P<right>{_CALCULATOR_OPERAND})$",
    re.IGNORECASE,
)
_WAKE_PREFIX = re.compile(r"^jarvis(?:[\s,:;-]+)", re.IGNORECASE)


def _spoken_calculator_integers() -> MappingProxyType[str, int]:
    values = {word: number for number, word in enumerate(_SPANISH_SMALL)}
    values.update({word: number for number, word in enumerate(_ENGLISH_SMALL)})
    values.update({word: number for number, word in enumerate(_SPANISH_TWENTIES, start=20)})
    for tens, word in _SPANISH_TENS.items():
        values[word] = tens
        for unit, unit_word in enumerate(_SPANISH_SMALL[1:10], start=1):
            values[f"{word} y {unit_word}"] = tens + unit
    for tens, word in _ENGLISH_TENS.items():
        values[word] = tens
        for unit, unit_word in enumerate(_ENGLISH_SMALL[1:10], start=1):
            values[f"{word} {unit_word}"] = tens + unit
    values.update({"un": 1, "una": 1, "cien": 100, "one hundred": 100})
    return MappingProxyType(values)


_SPOKEN_CALCULATOR_INTEGERS = _spoken_calculator_integers()


def direct_local_response(
    request: UserRequest,
    *,
    now: datetime | None = None,
    uptime_seconds: float | None = None,
) -> AgentResult | None:
    command = _direct_command(request)
    if command is None:
        return None
    normalized = command.casefold().lstrip("¿¡").rstrip(".!?")
    calculation = _calculator_response(normalized)
    if calculation is not None:
        return calculation
    if normalized in _UPTIME_COMMANDS:
        return _uptime_response(uptime_seconds)
    if normalized not in _CLOCK_COMMANDS:
        return None
    current = now or datetime.now().astimezone()
    if current.tzinfo is None or current.utcoffset() is None:
        raise ValueError("local clock requires a timezone-aware timestamp")
    date_text = (
        f"{_SPANISH_WEEKDAYS[current.weekday()]} {current.day} de "
        f"{_SPANISH_MONTHS[current.month - 1]} de {current.year}"
    )
    time_text = f"{current.hour:02d}:{current.minute:02d}"
    if normalized in _TIMEZONE_COMMANDS:
        offset_seconds = current.utcoffset().total_seconds()
        if (
            not math.isfinite(offset_seconds)
            or abs(offset_seconds) > 86_400
            or offset_seconds % 60
        ):
            raise ValueError("local clock returned an invalid UTC offset")
        offset_minutes = int(offset_seconds // 60)
        sign = "+" if offset_minutes >= 0 else "-"
        hours, minutes = divmod(abs(offset_minutes), 60)
        content = f"La zona horaria actual usa UTC{sign}{hours:02d}:{minutes:02d}."
    elif normalized in _TIME_COMMANDS:
        content = f"Son las {time_text}."
    elif normalized in _DATE_COMMANDS:
        content = f"Hoy es {date_text}."
    else:
        content = f"Hoy es {date_text} y son las {time_text}."
    return AgentResult(
        role=AgentRole.SYNTHESIZER,
        model_id="local/deterministic-clock",
        content=content,
    )


def _uptime_response(uptime_seconds: float | None) -> AgentResult:
    if uptime_seconds is None:
        clock_id = getattr(time, "CLOCK_MONOTONIC_RAW", time.CLOCK_MONOTONIC)
        try:
            uptime_seconds = time.clock_gettime(clock_id)
        except (OSError, ValueError):
            return AgentResult(
                role=AgentRole.SYNTHESIZER,
                model_id="local/deterministic-uptime",
                content="No pude consultar el tiempo activo de este Mac.",
            )
    if (
        isinstance(uptime_seconds, bool)
        or not isinstance(uptime_seconds, (int, float))
        or not math.isfinite(uptime_seconds)
        or not 0 <= uptime_seconds <= 315_576_000
    ):
        raise ValueError("system uptime is invalid")
    total_minutes = int(uptime_seconds) // 60
    if total_minutes == 0:
        duration = "menos de un minuto"
    else:
        days, remaining_minutes = divmod(total_minutes, 1_440)
        hours, minutes = divmod(remaining_minutes, 60)
        parts = []
        if days:
            parts.append(f"{days} día" if days == 1 else f"{days} días")
        if hours:
            parts.append(f"{hours} hora" if hours == 1 else f"{hours} horas")
        if minutes:
            parts.append(f"{minutes} minuto" if minutes == 1 else f"{minutes} minutos")
        duration = (
            parts[0]
            if len(parts) == 1
            else f"{', '.join(parts[:-1])} y {parts[-1]}"
        )
    return AgentResult(
        role=AgentRole.SYNTHESIZER,
        model_id="local/deterministic-uptime",
        content=f"Este Mac lleva encendido {duration}.",
    )


def _calculator_response(command: str) -> AgentResult | None:
    word_hyphens_normalized = re.sub(
        r"(?<=[^\W\d_])-(?=[^\W\d_])",
        " ",
        command,
    )
    match = _CALCULATOR_PATTERN.fullmatch(word_hyphens_normalized)
    if match is None:
        return None
    left = _calculator_operand(match.group("left"))
    right = _calculator_operand(match.group("right"))
    if left is None or right is None:
        return None
    operator = _CALCULATOR_OPERATORS[" ".join(match.group("operator").split())]
    if operator == "/" and right == 0:
        content = "No puedo dividir entre cero."
    else:
        with localcontext() as context:
            context.prec = 40
            if operator == "+":
                result = left + right
            elif operator == "-":
                result = left - right
            elif operator == "*":
                result = left * right
            else:
                result = left / right
        if result and abs(result) < Decimal("0.0000000001"):
            return None
        rendered = f"{result:.10f}".rstrip("0").rstrip(".")
        if not rendered or Decimal(rendered) == 0:
            rendered = "0"
        approximate = Decimal(rendered) != result
        rendered = rendered.replace(".", ",")
        qualifier = "aproximadamente " if approximate else ""
        content = f"El resultado es {qualifier}{rendered}."
    return AgentResult(
        role=AgentRole.SYNTHESIZER,
        model_id="local/deterministic-calculator",
        content=content,
    )


def _calculator_operand(value: str) -> Decimal | None:
    try:
        return Decimal(value.replace(",", "."))
    except InvalidOperation:
        pass
    normalized = "".join(
        character
        for character in unicodedata.normalize("NFKD", value.casefold())
        if not unicodedata.combining(character)
    )
    words = " ".join(normalized.replace("-", " ").split())
    sign = 1
    for prefix in ("menos ", "minus ", "negative "):
        if words.startswith(prefix):
            sign = -1
            words = words.removeprefix(prefix)
            break
    amount = _SPOKEN_CALCULATOR_INTEGERS.get(words)
    return Decimal(sign * amount) if amount is not None else None


def direct_tool_call(request: UserRequest, *, now: datetime | None = None) -> ToolCall | None:
    command = _direct_command(request)
    if command is None:
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
    if normalized in _STORAGE_STATUS_COMMANDS:
        return _call(
            request,
            role=AgentRole.PLANNER,
            tool_name="system_storage_status",
            arguments={},
        )
    observe_domain = _SYSTEM_OBSERVE_COMMANDS.get(normalized)
    if observe_domain is not None:
        return _call(
            request,
            role=AgentRole.PLANNER,
            tool_name="system_observe_status",
            arguments={"domain": observe_domain},
        )

    if normalized in _MAIL_UNREAD_STATUS_COMMANDS:
        return _call(
            request,
            role=AgentRole.PLANNER,
            tool_name="mail_list_recent",
            arguments={"limit": 1, "unread_only": True},
        )

    if normalized in _LATEST_MAIL_COMMANDS:
        return _call(
            request,
            role=AgentRole.PLANNER,
            tool_name="mail_list_recent",
            arguments={"limit": 1, "unread_only": False},
        )

    unread_only = _MAIL_READ_COMMANDS.get(normalized)
    if unread_only is not None:
        return _call(
            request,
            role=AgentRole.PLANNER,
            tool_name="mail_list_recent",
            arguments={"limit": 10, "unread_only": unread_only},
        )

    if normalized in _CALENDAR_TODAY_STATUS_COMMANDS:
        start, end = _calendar_day_window(now, day_offset=0)
        return _call(
            request,
            role=AgentRole.PLANNER,
            tool_name="calendar_list_events",
            arguments={
                "start_at": start.isoformat(),
                "end_at": end.isoformat(),
                "limit": 1,
            },
        )

    if normalized in _NEXT_CALENDAR_COMMANDS:
        start, end = _calendar_next_window(now)
        return _call(
            request,
            role=AgentRole.PLANNER,
            tool_name="calendar_list_events",
            arguments={
                "start_at": start.isoformat(),
                "end_at": end.isoformat(),
                "limit": 1,
            },
        )

    calendar_day_offset = _CALENDAR_DAY_OFFSETS.get(normalized)
    if calendar_day_offset is not None:
        start, end = _calendar_day_window(now, day_offset=calendar_day_offset)
        return _call(
            request,
            role=AgentRole.PLANNER,
            tool_name="calendar_list_events",
            arguments={
                "start_at": start.isoformat(),
                "end_at": end.isoformat(),
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


def _calendar_day_window(
    current: datetime | None,
    *,
    day_offset: int,
) -> tuple[datetime, datetime]:
    if current is None:
        local_midnight = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
        start = (local_midnight + timedelta(days=day_offset)).astimezone()
        end = (local_midnight + timedelta(days=day_offset + 1)).astimezone()
        return start, end
    if current.tzinfo is None or current.utcoffset() is None:
        raise ValueError("calendar planning requires timezone-aware local time")
    local_midnight = current.replace(hour=0, minute=0, second=0, microsecond=0)
    return (
        local_midnight + timedelta(days=day_offset),
        local_midnight + timedelta(days=day_offset + 1),
    )


def _calendar_next_window(current: datetime | None) -> tuple[datetime, datetime]:
    if current is None:
        local_now = datetime.now().replace(microsecond=0)
        start = local_now.astimezone()
        end_timezone = (local_now + timedelta(days=31)).astimezone().tzinfo
        end = (start.astimezone(UTC) + timedelta(days=31)).astimezone(end_timezone)
        return start, end
    if current.tzinfo is None or current.utcoffset() is None:
        raise ValueError("calendar planning requires timezone-aware local time")
    start = current.replace(microsecond=0)
    end = (start.astimezone(UTC) + timedelta(days=31)).astimezone(start.tzinfo)
    return start, end


def _direct_command(request: UserRequest) -> str | None:
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
    return command or None


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
