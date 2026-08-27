from __future__ import annotations

import asyncio
import ctypes
import errno
import json
import math
import os
import platform
import re
import socket
import stat
import subprocess
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from ipaddress import ip_address, ip_network
from pathlib import Path, PurePosixPath
from urllib.parse import urlencode

from pydantic import ValidationError

from aegis_core.contracts import PolicyDecision, ToolAuthorization, ToolExecutionResult
from aegis_core.tools.broker import PolicyContext
from aegis_core.tools.computer import ComputerUseController, ComputerUseError
from aegis_core.tools.defaults import (
    ApplicationOpenArguments,
    BrowserOpenArguments,
    BrowserSearchArguments,
    CalendarCreateArguments,
    CalendarListArguments,
    ComputerUseArguments,
    ContactCreateArguments,
    ContactsSearchArguments,
    MailListRecentArguments,
    MailSendArguments,
    MediaControlArguments,
    NetworkDiscoveryArguments,
    PowerStatusArguments,
    ReadTextArguments,
    ReminderCompleteArguments,
    ReminderCreateArguments,
    RemindersListArguments,
    RuntimeInfoArguments,
    ShortcutRunArguments,
    SpotlightOpenArguments,
    SpotlightSearchArguments,
    StorageStatusArguments,
    SystemAudioSetArguments,
    SystemObserveArguments,
    TerminalTemplateArguments,
    WebFetchArguments,
    WebResearchArguments,
)
from aegis_core.tools.web import PublicWebClient, WebAccessError, validate_public_https_url

ToolHandler = Callable[[ToolAuthorization, PolicyContext], ToolExecutionResult]
TcpConnector = Callable[[str, int, float], str]
WebClientFactory = Callable[[], PublicWebClient]

_TCP_CONNECT_TIMEOUT_SECONDS = 0.25
_TCP_CONNECT_WORKERS = 32
_TERMINAL_TIMEOUT_SECONDS = 3.0
_TERMINAL_OUTPUT_MAX_BYTES = 16_384
_AUTOMATION_TIMEOUT_SECONDS = 12.0
_AUTOMATION_OUTPUT_MAX_BYTES = 65_536
_CALENDAR_CANDIDATE_LIMIT = 2_048
_PERSONAL_DATA_CANDIDATE_LIMIT = 2_048
_HARDWARE_QUERY_TIMEOUT_SECONDS = 1.0
_HARDWARE_QUERY_COMMAND = (
    "/usr/sbin/sysctl",
    "-n",
    "machdep.cpu.brand_string",
    "hw.model",
    "hw.memsize",
)
_POWER_QUERY_TIMEOUT_SECONDS = 1.0
_POWER_QUERY_COMMAND = ("/usr/bin/pmset", "-g", "batt")
_SYSTEM_OBSERVE_TIMEOUT_SECONDS = 2.0
_SYSTEM_OBSERVE_OUTPUT_MAX_BYTES = 131_072
_SPOTLIGHT_TIMEOUT_SECONDS = 3.0
_SPOTLIGHT_OUTPUT_MAX_BYTES = 131_072
_NETWORK_QUERY_COMMAND = ("/sbin/ifconfig",)
_MEMORY_QUERY_COMMAND = ("/usr/bin/memory_pressure", "-Q")
_CORE_AUDIO_PATH = "/System/Library/Frameworks/CoreAudio.framework/CoreAudio"
_CORE_AUDIO_SYSTEM_OBJECT = 1
_CORE_AUDIO_MAIN_ELEMENT = 0
_BROWSER_SEARCH_URL = "https://duckduckgo.com/?"
_BROWSER_BUNDLE_IDENTIFIERS = {
    "safari": "com.apple.Safari",
    "chrome": "com.google.Chrome",
    "firefox": "org.mozilla.firefox",
}
_TERMINAL_COMMANDS: dict[str, tuple[str, ...]] = {
    "git_status": (
        "/usr/bin/git",
        "--no-optional-locks",
        "-c",
        "core.fsmonitor=false",
        "-c",
        "core.untrackedCache=false",
        "-c",
        "core.hooksPath=/dev/null",
        "status",
        "--short",
        "--branch",
        "--untracked-files=no",
    ),
    "list_processes": ("/bin/ps", "-axo", "pid=,ppid=,user=,comm="),
    "list_listeners": ("/usr/sbin/lsof", "-nP", "-iTCP", "-sTCP:LISTEN"),
}
_SECURITY_POSTURE_COMMANDS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("sip", ("/usr/bin/csrutil", "status")),
    ("gatekeeper", ("/usr/sbin/spctl", "--status")),
    ("filevault", ("/usr/bin/fdesetup", "isactive")),
    (
        "firewall",
        ("/usr/libexec/ApplicationFirewall/socketfilterfw", "--getglobalstate"),
    ),
)

_LOCAL_ISO_JXA = r"""
function localISOString(value) {
    const offsetMinutes = -value.getTimezoneOffset();
    const shifted = new Date(value.getTime() + offsetMinutes * 60000);
    const sign = offsetMinutes >= 0 ? "+" : "-";
    const absolute = Math.abs(offsetMinutes);
    const hours = ("0" + Math.floor(absolute / 60)).slice(-2);
    const minutes = ("0" + absolute % 60).slice(-2);
    return shifted.toISOString().slice(0, 19) + sign + hours + ":" + minutes;
}
"""

_MAIL_LIST_SCRIPT = _LOCAL_ISO_JXA + r"""
const Mail = Application("Mail");
const messages = Mail.inbox.messages();
const output = [];
for (let index = 0; index < messages.length && output.length < payload.limit; index++) {
    const message = messages[index];
    const unread = !message.readStatus();
    if (payload.unread_only && !unread) continue;
    const received = new Date(message.dateReceived());
    output.push({
        id: String(message.id()),
        sender: String(message.sender() || "").slice(0, 500),
        subject: String(message.subject() || "").slice(0, 500),
        date_received: received.toISOString(),
        local_date_received: localISOString(received),
        unread: unread
    });
}
JSON.stringify({messages: output});
"""

_MAIL_SEND_SCRIPT = r"""
const Mail = Application("Mail");
const message = Mail.OutgoingMessage({
    subject: payload.subject,
    content: payload.body + "\n",
    visible: false
});
Mail.outgoingMessages.push(message);
for (const address of payload.recipients) {
    message.toRecipients.push(Mail.ToRecipient({address: address}));
}
message.send();
JSON.stringify({sent: true, recipient_count: payload.recipients.length});
"""

_CALENDAR_LIST_SCRIPT = (_LOCAL_ISO_JXA + r"""
const Calendar = Application("Calendar");
const start = new Date(payload.start_at);
const end = new Date(payload.end_at);
const output = [];
for (const calendar of Calendar.calendars()) {
    for (const event of calendar.events()) {
        const eventStart = new Date(event.startDate());
        if (eventStart < start || eventStart >= end) continue;
        output.push({
            id: String(event.uid() || event.id()),
            calendar: String(calendar.name() || "").slice(0, 200),
            title: String(event.summary() || "").slice(0, 500),
            start_at: eventStart.toISOString(),
            local_start_at: localISOString(eventStart),
            end_at: new Date(event.endDate()).toISOString(),
            location: String(event.location() || "").slice(0, 500)
        });
        if (output.length > __CALENDAR_CANDIDATE_LIMIT__) {
            throw new Error("calendar candidate limit exceeded");
        }
    }
}
output.sort((left, right) => left.start_at.localeCompare(right.start_at));
JSON.stringify({events: output.slice(0, payload.limit)});
""").replace("__CALENDAR_CANDIDATE_LIMIT__", str(_CALENDAR_CANDIDATE_LIMIT))

_CALENDAR_CREATE_SCRIPT = r"""
const Calendar = Application("Calendar");
const calendars = Calendar.calendars();
let selected = null;
if (payload.calendar_name !== null) {
    selected = calendars.find(
        calendar => String(calendar.name()) === payload.calendar_name
    ) || null;
} else {
    selected = calendars.find(calendar => {
        try { return calendar.writable(); } catch (_) { return false; }
    }) || calendars[0] || null;
}
if (selected === null) throw new Error("calendar_unavailable");
const properties = {
    summary: payload.title,
    startDate: new Date(payload.start_at),
    endDate: new Date(payload.end_at)
};
if (payload.location !== null) properties.location = payload.location;
if (payload.notes !== null) properties.description = payload.notes;
const event = Calendar.Event(properties);
selected.events.push(event);
JSON.stringify({created: true, calendar: String(selected.name()), title: payload.title});
"""

_REMINDERS_LIST_SCRIPT = (_LOCAL_ISO_JXA + r"""
const Reminders = Application("Reminders");
const output = [];
let visited = 0;
for (const list of Reminders.lists()) {
    const listName = String(list.name() || "").slice(0, 200);
    if (payload.list_name !== null && listName !== payload.list_name) continue;
    for (const reminder of list.reminders()) {
        visited += 1;
        if (visited > __PERSONAL_DATA_CANDIDATE_LIMIT__) {
            throw new Error("reminder candidate limit exceeded");
        }
        const completed = Boolean(reminder.completed());
        if (!payload.include_completed && completed) continue;
        let dueAt = null;
        let localDueAt = null;
        try {
            const rawDue = reminder.dueDate();
            if (rawDue !== null) {
                const due = new Date(rawDue);
                dueAt = due.toISOString();
                localDueAt = localISOString(due);
            }
        } catch (_) {}
        output.push({
            title: String(reminder.name() || "").slice(0, 500),
            list: listName,
            due_at: dueAt,
            local_due_at: localDueAt,
            completed: completed
        });
        if (output.length >= payload.limit) break;
    }
    if (output.length >= payload.limit) break;
}
JSON.stringify({reminders: output});
""").replace("__PERSONAL_DATA_CANDIDATE_LIMIT__", str(_PERSONAL_DATA_CANDIDATE_LIMIT))

_REMINDER_CREATE_SCRIPT = r"""
const Reminders = Application("Reminders");
const lists = Reminders.lists();
let selected = null;
if (payload.list_name !== null) {
    selected = lists.find(list => String(list.name()) === payload.list_name) || null;
} else {
    selected = Reminders.defaultList();
}
if (selected === null) throw new Error("reminder_list_unavailable");
const properties = {name: payload.title};
if (payload.due_at !== null) properties.dueDate = new Date(payload.due_at);
selected.reminders.push(Reminders.Reminder(properties));
JSON.stringify({created: true, list: String(selected.name()), title: payload.title});
"""

_REMINDER_COMPLETE_SCRIPT = r"""
const Reminders = Application("Reminders");
const matches = [];
let visited = 0;
for (const list of Reminders.lists()) {
    const listName = String(list.name() || "");
    if (payload.list_name !== null && listName !== payload.list_name) continue;
    for (const reminder of list.reminders()) {
        visited += 1;
        if (visited > __PERSONAL_DATA_CANDIDATE_LIMIT__) {
            throw new Error("reminder candidate limit exceeded");
        }
        if (!reminder.completed() && String(reminder.name()) === payload.title) {
            matches.push({reminder: reminder, list: listName});
        }
    }
}
if (matches.length !== 1) throw new Error("reminder_match_not_unique");
matches[0].reminder.completed = true;
JSON.stringify({completed: true, list: matches[0].list, title: payload.title});
""".replace("__PERSONAL_DATA_CANDIDATE_LIMIT__", str(_PERSONAL_DATA_CANDIDATE_LIMIT))

_CONTACTS_SEARCH_SCRIPT = r"""
const Contacts = Application("Contacts");
const people = Contacts.people();
if (people.length > __PERSONAL_DATA_CANDIDATE_LIMIT__) {
    throw new Error("contact candidate limit exceeded");
}
const query = payload.query.toLocaleLowerCase();
const output = [];
for (const person of people) {
    const name = String(person.name() || "").slice(0, 300);
    const emailValues = person.emails().map(item => String(item.value() || "").slice(0, 254));
    const phoneValues = person.phones().map(item => String(item.value() || "").slice(0, 80));
    const searchable = [name].concat(emailValues).concat(phoneValues).join(" ").toLocaleLowerCase();
    if (!searchable.includes(query)) continue;
    output.push({name: name, emails: emailValues.slice(0, 3), phones: phoneValues.slice(0, 3)});
    if (output.length >= payload.limit) break;
}
JSON.stringify({contacts: output, query: payload.query});
""".replace("__PERSONAL_DATA_CANDIDATE_LIMIT__", str(_PERSONAL_DATA_CANDIDATE_LIMIT))

_CONTACT_CREATE_SCRIPT = r"""
const Contacts = Application("Contacts");
const properties = {firstName: payload.first_name};
if (payload.last_name !== null) properties.lastName = payload.last_name;
const person = Contacts.Person(properties);
Contacts.people.push(person);
if (payload.email !== null) {
    person.emails.push(Contacts.Email({label: "home", value: payload.email}));
}
if (payload.phone !== null) {
    person.phones.push(Contacts.Phone({label: "mobile", value: payload.phone}));
}
Contacts.save();
JSON.stringify({created: true, name: String(person.name() || payload.first_name)});
"""

_MEDIA_CONTROL_SCRIPT = r"""
const candidates = [
    {bundle_identifier: "com.apple.Music", app: Application("Music")},
    {bundle_identifier: "com.spotify.client", app: Application("Spotify")}
];
const running = candidates.filter(candidate => {
    try { return candidate.app.running(); } catch (_) { return false; }
});
if (running.length !== 1) throw new Error("media_application_not_unique");
const selected = running[0];
if (payload.action === "play_pause") selected.app.playpause();
else if (payload.action === "next") selected.app.nextTrack();
else if (payload.action === "previous") selected.app.previousTrack();
else throw new Error("media_action_invalid");
JSON.stringify({action: payload.action, bundle_identifier: selected.bundle_identifier});
"""


def _security_control_state(label: str, output: bytes) -> str:
    value = b" ".join(output.split()).lower()
    if label == "sip":
        enabled, disabled = b"status: enabled", b"status: disabled"
    elif label == "gatekeeper":
        enabled, disabled = b"assessments enabled", b"assessments disabled"
    elif label == "filevault":
        if value == b"true":
            return "enabled"
        if value == b"false":
            return "disabled"
        return "unavailable"
    elif label == "firewall":
        enabled, disabled = b"firewall is enabled", b"firewall is disabled"
    else:
        return "unavailable"
    if enabled in value:
        return "enabled"
    if disabled in value:
        return "disabled"
    return "unavailable"


def _mac_hardware_metadata() -> dict[str, str | int]:
    if platform.system() != "Darwin":
        return {}
    try:
        completed = subprocess.run(
            _HARDWARE_QUERY_COMMAND,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            timeout=_HARDWARE_QUERY_TIMEOUT_SECONDS,
            check=False,
            text=True,
            env={"LC_ALL": "C", "PATH": "/usr/bin:/bin:/usr/sbin:/sbin"},
        )
        if completed.returncode != 0 or not isinstance(completed.stdout, str):
            return {}
        values = [value.strip() for value in completed.stdout.splitlines()]
        if len(values) != 3:
            return {}
        chip, hardware_model, memory_text = values
        memory_bytes = int(memory_text)
    except (OSError, subprocess.TimeoutExpired, UnicodeError, ValueError):
        return {}
    if (
        not chip
        or not hardware_model
        or len(chip) > 128
        or len(hardware_model) > 128
        or not chip.isprintable()
        or not hardware_model.isprintable()
        or not 1_073_741_824 <= memory_bytes <= 2_199_023_255_552
    ):
        return {}
    return {
        "chip": chip,
        "hardware_model": hardware_model,
        "memory_bytes": memory_bytes,
    }


def _mac_power_status() -> dict[str, str | int | bool]:
    completed = subprocess.run(
        _POWER_QUERY_COMMAND,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        timeout=_POWER_QUERY_TIMEOUT_SECONDS,
        check=False,
        text=True,
        env={"LC_ALL": "C", "PATH": "/usr/bin:/bin:/usr/sbin:/sbin"},
    )
    if completed.returncode != 0 or not isinstance(completed.stdout, str):
        raise OSError("power query failed")
    output = completed.stdout
    if (
        not output
        or len(output) > 4_096
        or any(
            ord(character) < 32 and character not in {"\n", "\r", "\t"}
            for character in output
        )
    ):
        raise OSError("power query returned invalid output")

    source_match = re.search(r"Now drawing from '([^']+)'", output)
    if source_match is None:
        raise OSError("power query returned no source")
    source_name = source_match.group(1)
    power_source = {
        "AC Power": "ac",
        "Battery Power": "battery",
        "UPS Power": "ups",
    }.get(source_name, "unknown")

    percentage_match = re.search(r"\b(\d{1,3})%;", output)
    if percentage_match is None:
        return {"battery_present": False, "power_source": power_source}
    percentage = int(percentage_match.group(1))
    if not 0 <= percentage <= 100:
        raise OSError("power query returned invalid percentage")

    lowered = output.casefold()
    if "; finishing charge;" in lowered or "; charging;" in lowered:
        battery_state = "charging"
    elif "; discharging;" in lowered:
        battery_state = "discharging"
    elif "; charged;" in lowered:
        battery_state = "charged"
    else:
        battery_state = "unknown"
    status: dict[str, str | int | bool] = {
        "battery_percent": percentage,
        "battery_present": True,
        "battery_state": battery_state,
        "power_source": power_source,
    }
    remaining_match = re.search(r"\b(\d{1,2}):(\d{2}) remaining\b", output)
    if remaining_match is not None:
        hours, minutes = (int(value) for value in remaining_match.groups())
        if minutes < 60:
            status["time_remaining_minutes"] = hours * 60 + minutes
    return status


def _mac_storage_status() -> dict[str, int]:
    statistics = os.statvfs("/")
    fragment_size = statistics.f_frsize or statistics.f_bsize
    total_bytes = fragment_size * statistics.f_blocks
    available_bytes = fragment_size * statistics.f_bavail
    if (
        isinstance(fragment_size, bool)
        or isinstance(total_bytes, bool)
        or isinstance(available_bytes, bool)
        or fragment_size <= 0
        or total_bytes <= 0
        or available_bytes < 0
        or available_bytes > total_bytes
        or total_bytes > 9_223_372_036_854_775_807
    ):
        raise OSError("storage query returned invalid statistics")
    return {
        "available_bytes": available_bytes,
        "total_bytes": total_bytes,
        "used_bytes": total_bytes - available_bytes,
    }


def _run_bounded_system_query(command: tuple[str, ...]) -> str:
    completed = subprocess.run(
        command,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        timeout=_SYSTEM_OBSERVE_TIMEOUT_SECONDS,
        check=False,
        text=True,
        env={"LC_ALL": "C", "PATH": "/usr/bin:/bin:/usr/sbin:/sbin"},
    )
    output = completed.stdout
    if (
        completed.returncode != 0
        or not isinstance(output, str)
        or not output
        or len(output.encode("utf-8")) > _SYSTEM_OBSERVE_OUTPUT_MAX_BYTES
        or any(
            ord(character) < 32 and character not in {"\n", "\r", "\t"}
            for character in output
        )
    ):
        raise OSError("system query returned invalid output")
    return output


class _AudioObjectPropertyAddress(ctypes.Structure):
    _fields_ = (
        ("selector", ctypes.c_uint32),
        ("scope", ctypes.c_uint32),
        ("element", ctypes.c_uint32),
    )


def _audio_fourcc(value: str) -> int:
    return int.from_bytes(value.encode("ascii"), "big")


def _core_audio_read(
    object_id: int,
    selector: str,
    scope: str,
    value_type: type[ctypes.c_uint32] | type[ctypes.c_float],
) -> int | float:
    library = ctypes.CDLL(_CORE_AUDIO_PATH)
    getter = library.AudioObjectGetPropertyData
    getter.argtypes = (
        ctypes.c_uint32,
        ctypes.POINTER(_AudioObjectPropertyAddress),
        ctypes.c_uint32,
        ctypes.c_void_p,
        ctypes.POINTER(ctypes.c_uint32),
        ctypes.c_void_p,
    )
    getter.restype = ctypes.c_int32
    address = _AudioObjectPropertyAddress(
        _audio_fourcc(selector),
        _audio_fourcc(scope),
        _CORE_AUDIO_MAIN_ELEMENT,
    )
    value = value_type()
    size = ctypes.c_uint32(ctypes.sizeof(value))
    status = getter(
        object_id,
        ctypes.byref(address),
        0,
        None,
        ctypes.byref(size),
        ctypes.byref(value),
    )
    if status != 0 or size.value != ctypes.sizeof(value):
        raise OSError("CoreAudio property unavailable")
    return value.value


def _core_audio_write(
    object_id: int,
    selector: str,
    scope: str,
    value_type: type[ctypes.c_uint32] | type[ctypes.c_float],
    raw_value: int | float,
) -> None:
    library = ctypes.CDLL(_CORE_AUDIO_PATH)
    setter = library.AudioObjectSetPropertyData
    setter.argtypes = (
        ctypes.c_uint32,
        ctypes.POINTER(_AudioObjectPropertyAddress),
        ctypes.c_uint32,
        ctypes.c_void_p,
        ctypes.c_uint32,
        ctypes.c_void_p,
    )
    setter.restype = ctypes.c_int32
    address = _AudioObjectPropertyAddress(
        _audio_fourcc(selector),
        _audio_fourcc(scope),
        _CORE_AUDIO_MAIN_ELEMENT,
    )
    value = value_type(raw_value)
    status = setter(
        object_id,
        ctypes.byref(address),
        0,
        None,
        ctypes.sizeof(value),
        ctypes.byref(value),
    )
    if status != 0:
        raise OSError("CoreAudio property could not be changed")


def _mac_audio_status() -> dict[str, int | bool]:
    output_device = _core_audio_read(
        _CORE_AUDIO_SYSTEM_OBJECT,
        "dOut",
        "glob",
        ctypes.c_uint32,
    )
    if isinstance(output_device, bool) or not isinstance(output_device, int) or output_device <= 0:
        raise OSError("CoreAudio returned invalid output device")
    volume = _core_audio_read(output_device, "vmvc", "outp", ctypes.c_float)
    muted = _core_audio_read(output_device, "mute", "outp", ctypes.c_uint32)
    if (
        isinstance(volume, bool)
        or not isinstance(volume, (int, float))
        or not math.isfinite(volume)
        or not 0 <= volume <= 1
        or type(muted) is not int
        or muted not in {0, 1}
    ):
        raise OSError("CoreAudio returned invalid output state")
    return {
        "output_muted": muted == 1,
        "output_volume_percent": int(volume * 100 + 0.5),
    }


def _mac_set_audio(*, volume_percent: int | None, muted: bool | None) -> dict[str, int | bool]:
    output_device = _core_audio_read(
        _CORE_AUDIO_SYSTEM_OBJECT,
        "dOut",
        "glob",
        ctypes.c_uint32,
    )
    if type(output_device) is not int or output_device <= 0:
        raise OSError("CoreAudio returned invalid output device")
    if volume_percent is not None:
        _core_audio_write(
            output_device,
            "vmvc",
            "outp",
            ctypes.c_float,
            volume_percent / 100,
        )
    if muted is not None:
        _core_audio_write(
            output_device,
            "mute",
            "outp",
            ctypes.c_uint32,
            int(muted),
        )
    return _mac_audio_status()


def _safe_spotlight_paths(output: bytes, *, home: Path, limit: int) -> list[Path]:
    if len(output) > _SPOTLIGHT_OUTPUT_MAX_BYTES:
        raise OSError("Spotlight output exceeded its limit")
    resolved_home = home.resolve(strict=True)
    paths: list[Path] = []
    for raw_path in output.split(b"\0"):
        if not raw_path:
            continue
        try:
            text = raw_path.decode("utf-8")
        except UnicodeDecodeError:
            continue
        candidate = Path(text)
        try:
            if not candidate.is_absolute():
                continue
            resolved_candidate = candidate.resolve(strict=True)
            if resolved_candidate != candidate:
                continue
            relative = resolved_candidate.relative_to(resolved_home)
            metadata = resolved_candidate.lstat()
        except (OSError, ValueError):
            continue
        if (
            stat.S_ISLNK(metadata.st_mode)
            or not (stat.S_ISREG(metadata.st_mode) or stat.S_ISDIR(metadata.st_mode))
            or not relative.parts
            or relative.parts[0] in {"Library", ".Trash"}
            or any(part.startswith(".") for part in relative.parts)
        ):
            continue
        paths.append(resolved_candidate)
        if len(paths) >= limit:
            break
    return paths


def _has_routable_address(values: list[str], *, version: int) -> bool:
    for value in values:
        try:
            address = ip_address(value)
        except ValueError:
            continue
        if (
            address.version == version
            and not address.is_loopback
            and not address.is_link_local
            and not address.is_unspecified
        ):
            return True
    return False


def _mac_network_status() -> dict[str, int | bool]:
    output = _run_bounded_system_query(_NETWORK_QUERY_COMMAND)
    active_interfaces = 0
    ipv4_available = False
    ipv6_available = False
    blocks = re.split(r"(?m)(?=^[A-Za-z0-9]+: flags=)", output)
    for block in blocks:
        header = re.match(
            r"^(?P<name>[A-Za-z0-9]+): flags=[0-9a-fA-F]+<(?P<flags>[^>]*)>",
            block,
        )
        if header is None or re.fullmatch(r"en\d+", header.group("name")) is None:
            continue
        flags = frozenset(header.group("flags").split(","))
        if not {"UP", "RUNNING"}.issubset(flags) or re.search(
            r"(?m)^\s*status:\s*active\s*$", block
        ) is None:
            continue
        interface_ipv4 = _has_routable_address(
            re.findall(r"(?m)^\s*inet\s+([0-9.]+)\b", block),
            version=4,
        )
        interface_ipv6 = _has_routable_address(
            re.findall(r"(?m)^\s*inet6\s+([0-9a-fA-F:]+)(?:%\S+)?", block),
            version=6,
        )
        if interface_ipv4 or interface_ipv6:
            active_interfaces += 1
            ipv4_available = ipv4_available or interface_ipv4
            ipv6_available = ipv6_available or interface_ipv6
    return {
        "active_interfaces": active_interfaces,
        "connected": active_interfaces > 0,
        "ipv4_available": ipv4_available,
        "ipv6_available": ipv6_available,
    }


def _mac_performance_status() -> dict[str, int | float]:
    logical_cpus = os.cpu_count()
    load_average_1m = os.getloadavg()[0]
    output = _run_bounded_system_query(_MEMORY_QUERY_COMMAND)
    memory_match = re.search(
        r"(?m)^System-wide memory free percentage:\s*(\d{1,3})%\s*$",
        output,
    )
    if (
        isinstance(logical_cpus, bool)
        or not isinstance(logical_cpus, int)
        or not 1 <= logical_cpus <= 1_024
        or isinstance(load_average_1m, bool)
        or not isinstance(load_average_1m, (int, float))
        or not math.isfinite(load_average_1m)
        or not 0 <= load_average_1m <= 100_000
        or memory_match is None
    ):
        raise OSError("performance query returned invalid output")
    memory_available_percent = int(memory_match.group(1))
    if not 0 <= memory_available_percent <= 100:
        raise OSError("performance query returned invalid memory percentage")
    return {
        "load_average_1m": round(float(load_average_1m), 2),
        "logical_cpus": logical_cpus,
        "memory_available_percent": memory_available_percent,
    }


class ReadOnlyToolExecutor:
    def __init__(
        self,
        *,
        tcp_connector: TcpConnector | None = None,
        web_client_factory: WebClientFactory = PublicWebClient,
        computer_controller: ComputerUseController | None = None,
    ) -> None:
        self._tcp_connector = tcp_connector or self._probe_tcp
        self._web_client_factory = web_client_factory
        self._computer_controller = computer_controller
        self._handlers: dict[str, ToolHandler] = {
            "system_describe_runtime": self._describe_runtime,
            "system_power_status": self._power_status,
            "system_storage_status": self._storage_status,
            "system_observe_status": self._system_observe_status,
            "system_audio_set": self._system_audio_set,
            "media_control": self._media_control,
            "spotlight_search": self._spotlight_search,
            "spotlight_open": self._spotlight_open,
            "filesystem_read_text": self._read_text,
            "web_research": self._web_research,
            "web_fetch": self._web_fetch,
            "mail_list_recent": self._mail_list_recent,
            "mail_send_message": self._mail_send_message,
            "calendar_list_events": self._calendar_list_events,
            "calendar_create_event": self._calendar_create_event,
            "reminders_list": self._reminders_list,
            "reminder_create": self._reminder_create,
            "reminder_complete": self._reminder_complete,
            "contacts_search": self._contacts_search,
            "contact_create": self._contact_create,
            "browser_search": self._browser_search,
            "browser_open_url": self._browser_open_url,
            "application_open": self._application_open,
            "shortcut_run": self._shortcut_run,
            "network_discover_hosts": self._discover_network,
            "terminal_run_template": self._run_terminal_template,
        }

    async def execute_async(
        self,
        authorization: ToolAuthorization,
        context: PolicyContext,
    ) -> ToolExecutionResult:
        if authorization.tool_name != "computer_use":
            return await asyncio.to_thread(self.execute, authorization, context)
        if authorization.decision is not PolicyDecision.ALLOW:
            return self._error(authorization, "authorization_not_allowed")
        if authorization.reason_code != "confirmation_consumed":
            return self._error(authorization, "access_denied")
        if self._computer_controller is None:
            return self._error(authorization, "computer_controller_unavailable")
        try:
            arguments = ComputerUseArguments.model_validate(authorization.normalized_arguments)
            report = await self._computer_controller.run(
                objective=arguments.objective,
                application_bundle_identifier=arguments.application_bundle_identifier,
                max_steps=arguments.max_steps,
            )
        except ValidationError:
            return self._error(authorization, "invalid_authorized_arguments")
        except ComputerUseError as error:
            return self._error(authorization, error.code)
        return ToolExecutionResult(
            call_id=authorization.call_id,
            tool_name=authorization.tool_name,
            success=True,
            output=report.model_dump_json(),
            metadata={
                "application_bundle_identifier": report.application_bundle_identifier,
                "source": "native_computer_control",
                "status": report.status,
                "steps": report.steps,
                "verified": report.status == "completed",
            },
        )

    def execute(
        self, authorization: ToolAuthorization, context: PolicyContext
    ) -> ToolExecutionResult:
        if authorization.decision is not PolicyDecision.ALLOW:
            return self._error(authorization, "authorization_not_allowed")
        handler = self._handlers.get(authorization.tool_name)
        if handler is None:
            return self._error(authorization, "executor_unavailable")
        try:
            return handler(authorization, context)
        except ValidationError:
            return self._error(authorization, "invalid_authorized_arguments")
        except FileNotFoundError:
            return self._error(authorization, "file_not_found")
        except PermissionError:
            return self._error(authorization, "access_denied")
        except UnicodeDecodeError:
            return self._error(authorization, "invalid_utf8")
        except subprocess.TimeoutExpired:
            return self._error(authorization, "execution_timeout")
        except WebAccessError:
            return self._error(authorization, "web_access_failed")
        except OSError:
            return self._error(authorization, "io_error")

    @staticmethod
    def _describe_runtime(
        authorization: ToolAuthorization, context: PolicyContext
    ) -> ToolExecutionResult:
        del context
        RuntimeInfoArguments.model_validate(authorization.normalized_arguments)
        runtime = {
            "architecture": platform.machine(),
            "macos_version": platform.mac_ver()[0],
            "operating_system": platform.system(),
            "os_release": platform.release(),
            "python": platform.python_version(),
            **_mac_hardware_metadata(),
        }
        return ToolExecutionResult(
            call_id=authorization.call_id,
            tool_name=authorization.tool_name,
            success=True,
            output=json.dumps(runtime, separators=(",", ":"), sort_keys=True),
            metadata={"source": "local_runtime"},
        )

    @staticmethod
    def _power_status(
        authorization: ToolAuthorization, context: PolicyContext
    ) -> ToolExecutionResult:
        del context
        PowerStatusArguments.model_validate(authorization.normalized_arguments)
        return ToolExecutionResult(
            call_id=authorization.call_id,
            tool_name=authorization.tool_name,
            success=True,
            output=json.dumps(_mac_power_status(), separators=(",", ":"), sort_keys=True),
            metadata={"source": "local_power"},
        )

    @staticmethod
    def _storage_status(
        authorization: ToolAuthorization, context: PolicyContext
    ) -> ToolExecutionResult:
        del context
        StorageStatusArguments.model_validate(authorization.normalized_arguments)
        return ToolExecutionResult(
            call_id=authorization.call_id,
            tool_name=authorization.tool_name,
            success=True,
            output=json.dumps(_mac_storage_status(), separators=(",", ":"), sort_keys=True),
            metadata={"source": "local_storage"},
        )

    @staticmethod
    def _system_observe_status(
        authorization: ToolAuthorization, context: PolicyContext
    ) -> ToolExecutionResult:
        del context
        arguments = SystemObserveArguments.model_validate(
            authorization.normalized_arguments
        )
        reader = {
            "audio": _mac_audio_status,
            "network": _mac_network_status,
            "performance": _mac_performance_status,
        }[arguments.domain]
        return ToolExecutionResult(
            call_id=authorization.call_id,
            tool_name=authorization.tool_name,
            success=True,
            output=json.dumps(reader(), separators=(",", ":"), sort_keys=True),
            metadata={"source": f"local_{arguments.domain}"},
        )

    @staticmethod
    def _system_audio_set(
        authorization: ToolAuthorization, context: PolicyContext
    ) -> ToolExecutionResult:
        del context
        ReadOnlyToolExecutor._require_consumed_confirmation(authorization)
        arguments = SystemAudioSetArguments.model_validate(
            authorization.normalized_arguments
        )
        output = _mac_set_audio(
            volume_percent=arguments.volume_percent,
            muted=arguments.muted,
        )
        return ReadOnlyToolExecutor._json_result(
            authorization, output, "core_audio"
        )

    @staticmethod
    def _media_control(
        authorization: ToolAuthorization, context: PolicyContext
    ) -> ToolExecutionResult:
        ReadOnlyToolExecutor._require_consumed_confirmation(authorization)
        arguments = MediaControlArguments.model_validate(
            authorization.normalized_arguments
        )
        output = ReadOnlyToolExecutor._run_jxa(
            arguments.model_dump(mode="json"), _MEDIA_CONTROL_SCRIPT, context
        )
        return ReadOnlyToolExecutor._json_result(
            authorization, output, "native_media_application"
        )

    @staticmethod
    def _spotlight_search(
        authorization: ToolAuthorization, context: PolicyContext
    ) -> ToolExecutionResult:
        arguments = SpotlightSearchArguments.model_validate(
            authorization.normalized_arguments
        )
        paths = ReadOnlyToolExecutor._spotlight_paths(
            arguments.query,
            context,
            limit=arguments.limit,
        )
        results = [
            {
                "kind": (
                    "application"
                    if path.suffix.casefold() == ".app" and path.is_dir()
                    else "folder"
                    if path.is_dir()
                    else "file"
                ),
                "name": path.name,
                "path": str(path),
            }
            for path in paths
        ]
        return ReadOnlyToolExecutor._json_result(
            authorization,
            {"query": arguments.query, "results": results},
            "macos_spotlight",
        )

    @staticmethod
    def _spotlight_open(
        authorization: ToolAuthorization, context: PolicyContext
    ) -> ToolExecutionResult:
        ReadOnlyToolExecutor._require_consumed_confirmation(authorization)
        arguments = SpotlightOpenArguments.model_validate(
            authorization.normalized_arguments
        )
        candidates = ReadOnlyToolExecutor._spotlight_paths(
            arguments.query,
            context,
            limit=20,
        )
        normalized_query = arguments.query.casefold()
        exact = [
            path
            for path in candidates
            if path.name.casefold() == normalized_query
            or path.stem.casefold() == normalized_query
        ]
        if len(exact) != 1:
            raise PermissionError("Spotlight result was not uniquely identified")
        return ReadOnlyToolExecutor._open_application_target(
            authorization,
            context,
            ("/usr/bin/open", str(exact[0])),
            {"name": exact[0].name, "opened": True},
        )

    @classmethod
    def _read_text(
        cls, authorization: ToolAuthorization, context: PolicyContext
    ) -> ToolExecutionResult:
        arguments = ReadTextArguments.model_validate(authorization.normalized_arguments)
        data, truncated = cls._read_regular_file(
            context.workspace_root, arguments.path, arguments.max_bytes
        )
        return ToolExecutionResult(
            call_id=authorization.call_id,
            tool_name=authorization.tool_name,
            success=True,
            output=data.decode("utf-8"),
            metadata={
                "path": arguments.path,
                "bytes_read": len(data),
                "truncated": truncated,
            },
        )

    def _discover_network(
        self, authorization: ToolAuthorization, context: PolicyContext
    ) -> ToolExecutionResult:
        if authorization.reason_code != "confirmation_consumed":
            raise PermissionError("network confirmation was not consumed")
        arguments = NetworkDiscoveryArguments.model_validate(authorization.normalized_arguments)
        target = ip_network(arguments.target, strict=False)
        if target.num_addresses > 256 or not any(
            target.version == scope.version and target.subnet_of(scope)
            for scope in context.network_scopes
        ):
            raise PermissionError("network target is outside execution policy")

        addresses = tuple(str(address) for address in target.hosts())
        endpoints = tuple((address, port) for address in addresses for port in arguments.ports)
        reachable: set[str] = set()
        open_ports: dict[str, list[int]] = {address: [] for address in addresses}

        def probe(endpoint: tuple[str, int]) -> tuple[str, int, str]:
            address, port = endpoint
            state = self._tcp_connector(address, port, _TCP_CONNECT_TIMEOUT_SECONDS)
            return address, port, state

        with ThreadPoolExecutor(
            max_workers=min(_TCP_CONNECT_WORKERS, len(endpoints)),
            thread_name_prefix="aegis-tcp",
        ) as pool:
            for address, port, state in pool.map(probe, endpoints):
                if state in {"open", "closed"}:
                    reachable.add(address)
                if state == "open":
                    open_ports[address].append(port)

        responsive = [
            {"address": address, "open_ports": open_ports[address]}
            for address in addresses
            if address in reachable
        ]
        output = json.dumps(
            {
                "hosts": responsive,
                "ports": arguments.ports,
                "target": target.with_prefixlen,
            },
            separators=(",", ":"),
            sort_keys=True,
        )
        return ToolExecutionResult(
            call_id=authorization.call_id,
            tool_name=authorization.tool_name,
            success=True,
            output=output,
            metadata={
                "endpoints_scanned": len(endpoints),
                "hosts_scanned": len(addresses),
                "responsive_hosts": len(responsive),
                "source": "tcp_connect",
            },
        )

    def _web_research(
        self, authorization: ToolAuthorization, context: PolicyContext
    ) -> ToolExecutionResult:
        del context
        arguments = WebResearchArguments.model_validate(authorization.normalized_arguments)
        client = self._web_client_factory()
        try:
            results = client.research(arguments.query, max_results=arguments.max_results)
        finally:
            client.close()
        output = json.dumps(
            {"query": arguments.query, "results": results},
            ensure_ascii=False,
            separators=(",", ":"),
        )
        return ToolExecutionResult(
            call_id=authorization.call_id,
            tool_name=authorization.tool_name,
            success=True,
            output=output,
            metadata={"results": len(results), "source": "public_https"},
        )

    def _web_fetch(
        self, authorization: ToolAuthorization, context: PolicyContext
    ) -> ToolExecutionResult:
        del context
        arguments = WebFetchArguments.model_validate(authorization.normalized_arguments)
        client = self._web_client_factory()
        try:
            page = client.fetch(arguments.url, max_characters=arguments.max_characters)
        finally:
            client.close()
        return ToolExecutionResult(
            call_id=authorization.call_id,
            tool_name=authorization.tool_name,
            success=True,
            output=json.dumps(page, ensure_ascii=False, separators=(",", ":")),
            metadata={"characters": len(page["content"]), "source": "public_https"},
        )

    @staticmethod
    def _mail_list_recent(
        authorization: ToolAuthorization, context: PolicyContext
    ) -> ToolExecutionResult:
        arguments = MailListRecentArguments.model_validate(authorization.normalized_arguments)
        output = ReadOnlyToolExecutor._run_jxa(
            arguments.model_dump(mode="json"), _MAIL_LIST_SCRIPT, context
        )
        return ReadOnlyToolExecutor._json_result(authorization, output, "apple_mail")

    @staticmethod
    def _mail_send_message(
        authorization: ToolAuthorization, context: PolicyContext
    ) -> ToolExecutionResult:
        if authorization.reason_code != "confirmation_consumed":
            raise PermissionError("mail confirmation was not consumed")
        arguments = MailSendArguments.model_validate(authorization.normalized_arguments)
        output = ReadOnlyToolExecutor._run_jxa(
            arguments.model_dump(mode="json"), _MAIL_SEND_SCRIPT, context
        )
        return ReadOnlyToolExecutor._json_result(authorization, output, "apple_mail")

    @staticmethod
    def _calendar_list_events(
        authorization: ToolAuthorization, context: PolicyContext
    ) -> ToolExecutionResult:
        arguments = CalendarListArguments.model_validate(authorization.normalized_arguments)
        output = ReadOnlyToolExecutor._run_jxa(
            arguments.model_dump(mode="json"), _CALENDAR_LIST_SCRIPT, context
        )
        if not isinstance(output, dict) or set(output) != {"events"}:
            raise OSError("calendar returned an invalid payload")
        events = output["events"]
        if (
            not isinstance(events, list)
            or len(events) > _CALENDAR_CANDIDATE_LIMIT
            or any(
                not isinstance(event, dict)
                or not isinstance(event.get("start_at"), str)
                for event in events
            )
        ):
            raise OSError("calendar returned invalid events")
        bounded = {
            "events": sorted(events, key=lambda event: event["start_at"])[
                : arguments.limit
            ]
        }
        return ReadOnlyToolExecutor._json_result(
            authorization, bounded, "apple_calendar"
        )

    @staticmethod
    def _calendar_create_event(
        authorization: ToolAuthorization, context: PolicyContext
    ) -> ToolExecutionResult:
        if authorization.reason_code != "confirmation_consumed":
            raise PermissionError("calendar confirmation was not consumed")
        arguments = CalendarCreateArguments.model_validate(authorization.normalized_arguments)
        output = ReadOnlyToolExecutor._run_jxa(
            arguments.model_dump(mode="json"), _CALENDAR_CREATE_SCRIPT, context
        )
        return ReadOnlyToolExecutor._json_result(authorization, output, "apple_calendar")

    @staticmethod
    def _reminders_list(
        authorization: ToolAuthorization, context: PolicyContext
    ) -> ToolExecutionResult:
        arguments = RemindersListArguments.model_validate(authorization.normalized_arguments)
        output = ReadOnlyToolExecutor._run_jxa(
            arguments.model_dump(mode="json"), _REMINDERS_LIST_SCRIPT, context
        )
        return ReadOnlyToolExecutor._validated_collection_result(
            authorization, output, key="reminders", limit=arguments.limit, source="apple_reminders"
        )

    @staticmethod
    def _reminder_create(
        authorization: ToolAuthorization, context: PolicyContext
    ) -> ToolExecutionResult:
        ReadOnlyToolExecutor._require_consumed_confirmation(authorization)
        arguments = ReminderCreateArguments.model_validate(authorization.normalized_arguments)
        output = ReadOnlyToolExecutor._run_jxa(
            arguments.model_dump(mode="json"), _REMINDER_CREATE_SCRIPT, context
        )
        return ReadOnlyToolExecutor._json_result(authorization, output, "apple_reminders")

    @staticmethod
    def _reminder_complete(
        authorization: ToolAuthorization, context: PolicyContext
    ) -> ToolExecutionResult:
        ReadOnlyToolExecutor._require_consumed_confirmation(authorization)
        arguments = ReminderCompleteArguments.model_validate(authorization.normalized_arguments)
        output = ReadOnlyToolExecutor._run_jxa(
            arguments.model_dump(mode="json"), _REMINDER_COMPLETE_SCRIPT, context
        )
        return ReadOnlyToolExecutor._json_result(authorization, output, "apple_reminders")

    @staticmethod
    def _contacts_search(
        authorization: ToolAuthorization, context: PolicyContext
    ) -> ToolExecutionResult:
        arguments = ContactsSearchArguments.model_validate(authorization.normalized_arguments)
        output = ReadOnlyToolExecutor._run_jxa(
            arguments.model_dump(mode="json"), _CONTACTS_SEARCH_SCRIPT, context
        )
        return ReadOnlyToolExecutor._validated_collection_result(
            authorization, output, key="contacts", limit=arguments.limit, source="apple_contacts"
        )

    @staticmethod
    def _contact_create(
        authorization: ToolAuthorization, context: PolicyContext
    ) -> ToolExecutionResult:
        ReadOnlyToolExecutor._require_consumed_confirmation(authorization)
        arguments = ContactCreateArguments.model_validate(authorization.normalized_arguments)
        output = ReadOnlyToolExecutor._run_jxa(
            arguments.model_dump(mode="json"), _CONTACT_CREATE_SCRIPT, context
        )
        return ReadOnlyToolExecutor._json_result(authorization, output, "apple_contacts")

    @staticmethod
    def _browser_open_url(
        authorization: ToolAuthorization, context: PolicyContext
    ) -> ToolExecutionResult:
        if authorization.reason_code != "confirmation_consumed":
            raise PermissionError("browser confirmation was not consumed")
        arguments = BrowserOpenArguments.model_validate(authorization.normalized_arguments)
        url = validate_public_https_url(arguments.url)
        return ReadOnlyToolExecutor._open_application_target(
            authorization,
            context,
            ("/usr/bin/open", url),
            {"opened": True, "url": url},
        )

    @staticmethod
    def _browser_search(
        authorization: ToolAuthorization, context: PolicyContext
    ) -> ToolExecutionResult:
        if authorization.reason_code != "confirmation_consumed":
            raise PermissionError("browser search confirmation was not consumed")
        arguments = BrowserSearchArguments.model_validate(
            authorization.normalized_arguments
        )
        search_url = _BROWSER_SEARCH_URL + urlencode({"q": arguments.query})
        bundle_identifier = _BROWSER_BUNDLE_IDENTIFIERS.get(arguments.browser)
        command = (
            ("/usr/bin/open", search_url)
            if bundle_identifier is None
            else ("/usr/bin/open", "-b", bundle_identifier, search_url)
        )
        return ReadOnlyToolExecutor._open_application_target(
            authorization,
            context,
            command,
            {
                "browser": arguments.browser,
                "opened": True,
                "query": arguments.query,
            },
        )

    @staticmethod
    def _application_open(
        authorization: ToolAuthorization, context: PolicyContext
    ) -> ToolExecutionResult:
        if authorization.reason_code != "confirmation_consumed":
            raise PermissionError("application confirmation was not consumed")
        arguments = ApplicationOpenArguments.model_validate(authorization.normalized_arguments)
        return ReadOnlyToolExecutor._open_application_target(
            authorization,
            context,
            ("/usr/bin/open", "-b", arguments.bundle_identifier),
            {"bundle_identifier": arguments.bundle_identifier, "opened": True},
        )

    @staticmethod
    def _shortcut_run(
        authorization: ToolAuthorization, context: PolicyContext
    ) -> ToolExecutionResult:
        if authorization.reason_code != "confirmation_consumed":
            raise PermissionError("shortcut confirmation was not consumed")
        arguments = ShortcutRunArguments.model_validate(authorization.normalized_arguments)
        workspace = context.workspace_root.resolve(strict=True)
        completed = subprocess.run(
            ("/usr/bin/shortcuts", "run", arguments.name),
            cwd=workspace,
            env={"LC_ALL": "C", "PATH": "/usr/bin:/bin:/usr/sbin:/sbin"},
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            timeout=30,
            check=False,
        )
        output = completed.stdout[:_AUTOMATION_OUTPUT_MAX_BYTES]
        if completed.returncode != 0:
            return ToolExecutionResult(
                call_id=authorization.call_id,
                tool_name=authorization.tool_name,
                success=False,
                error_code="shortcut_run_failed",
                metadata={"return_code": completed.returncode, "source": "macos_shortcuts"},
            )
        return ToolExecutionResult(
            call_id=authorization.call_id,
            tool_name=authorization.tool_name,
            success=True,
            output=json.dumps(
                {"name": arguments.name, "completed": True},
                ensure_ascii=False,
                separators=(",", ":"),
            ),
            metadata={
                "bytes_read": len(output),
                "source": "macos_shortcuts",
                "truncated": len(completed.stdout) > len(output),
            },
        )

    @staticmethod
    def _run_jxa(payload: dict[str, object], script: str, context: PolicyContext) -> object:
        workspace = context.workspace_root.resolve(strict=True)
        if not workspace.is_dir():
            raise PermissionError("workspace root is not a directory")
        source = (
            "const payload = "
            + json.dumps(payload, ensure_ascii=True, separators=(",", ":"))
            + ";\n"
            + script
        )
        completed = subprocess.run(
            ("/usr/bin/osascript", "-l", "JavaScript"),
            cwd=workspace,
            env={"LC_ALL": "C", "PATH": "/usr/bin:/bin:/usr/sbin:/sbin"},
            input=source.encode("utf-8"),
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            timeout=_AUTOMATION_TIMEOUT_SECONDS,
            check=False,
        )
        if completed.returncode != 0:
            raise PermissionError("macOS automation was denied or failed")
        if len(completed.stdout) > _AUTOMATION_OUTPUT_MAX_BYTES:
            raise OSError("macOS automation output exceeded its limit")
        try:
            output = json.loads(completed.stdout.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise OSError("macOS automation returned invalid output") from error
        if not isinstance(output, (dict, list)):
            raise OSError("macOS automation returned an invalid value")
        return output

    @staticmethod
    def _spotlight_paths(
        query: str,
        context: PolicyContext,
        *,
        limit: int,
    ) -> list[Path]:
        workspace = context.workspace_root.resolve(strict=True)
        if not workspace.is_dir():
            raise PermissionError("workspace root is not a directory")
        home = Path.home().resolve(strict=True)
        if not home.is_dir():
            raise PermissionError("home directory is not available")
        completed = subprocess.run(
            ("/usr/bin/mdfind", "-0", "-onlyin", str(home), "-interpret", query),
            cwd=workspace,
            env={"LC_ALL": "C", "PATH": "/usr/bin:/bin:/usr/sbin:/sbin"},
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            timeout=_SPOTLIGHT_TIMEOUT_SECONDS,
            check=False,
        )
        if completed.returncode != 0:
            raise OSError("Spotlight search failed")
        return _safe_spotlight_paths(completed.stdout, home=home, limit=limit)

    @staticmethod
    def _require_consumed_confirmation(authorization: ToolAuthorization) -> None:
        if authorization.reason_code != "confirmation_consumed":
            raise PermissionError("confirmation was not consumed")

    @staticmethod
    def _validated_collection_result(
        authorization: ToolAuthorization,
        output: object,
        *,
        key: str,
        limit: int,
        source: str,
    ) -> ToolExecutionResult:
        if (
            not isinstance(output, dict)
            or key not in output
            or not isinstance(output[key], list)
            or len(output[key]) > limit
            or any(not isinstance(item, dict) for item in output[key])
        ):
            raise OSError(f"{source} returned an invalid payload")
        return ReadOnlyToolExecutor._json_result(authorization, output, source)

    @staticmethod
    def _json_result(
        authorization: ToolAuthorization, output: object, source: str
    ) -> ToolExecutionResult:
        serialized = json.dumps(
            output,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )
        return ToolExecutionResult(
            call_id=authorization.call_id,
            tool_name=authorization.tool_name,
            success=True,
            output=serialized,
            metadata={
                "bytes_read": len(serialized.encode("utf-8")),
                "source": source,
            },
        )

    @staticmethod
    def _open_application_target(
        authorization: ToolAuthorization,
        context: PolicyContext,
        command: tuple[str, ...],
        payload: dict[str, object],
    ) -> ToolExecutionResult:
        workspace = context.workspace_root.resolve(strict=True)
        if not workspace.is_dir():
            raise PermissionError("workspace root is not a directory")
        completed = subprocess.run(
            command,
            cwd=workspace,
            env={"LC_ALL": "C", "PATH": "/usr/bin:/bin:/usr/sbin:/sbin"},
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=5.0,
            check=False,
        )
        if completed.returncode != 0:
            return ToolExecutionResult(
                call_id=authorization.call_id,
                tool_name=authorization.tool_name,
                success=False,
                error_code="application_open_failed",
                metadata={"return_code": completed.returncode},
            )
        return ToolExecutionResult(
            call_id=authorization.call_id,
            tool_name=authorization.tool_name,
            success=True,
            output=json.dumps(payload, separators=(",", ":"), sort_keys=True),
            metadata={"source": "macos_open"},
        )

    @staticmethod
    def _run_terminal_template(
        authorization: ToolAuthorization, context: PolicyContext
    ) -> ToolExecutionResult:
        if authorization.reason_code != "confirmation_consumed":
            raise PermissionError("terminal confirmation was not consumed")
        arguments = TerminalTemplateArguments.model_validate(authorization.normalized_arguments)
        if arguments.template == "security_posture":
            return ReadOnlyToolExecutor._run_security_posture(
                authorization, context, arguments.template
            )
        command = _TERMINAL_COMMANDS.get(arguments.template)
        if command is None:
            raise PermissionError("terminal template is not executable")
        workspace = context.workspace_root.resolve(strict=True)
        if not workspace.is_dir():
            raise PermissionError("workspace root is not a directory")

        completed = subprocess.run(
            command,
            cwd=workspace,
            env={
                "GIT_CONFIG_GLOBAL": "/dev/null",
                "GIT_CONFIG_NOSYSTEM": "1",
                "GIT_OPTIONAL_LOCKS": "0",
                "LC_ALL": "C",
                "PATH": "/usr/bin:/bin:/usr/sbin:/sbin",
            },
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            timeout=_TERMINAL_TIMEOUT_SECONDS,
            check=False,
        )
        accepted_codes = {0, 1} if arguments.template == "list_listeners" else {0}
        if completed.returncode not in accepted_codes:
            return ToolExecutionResult(
                call_id=authorization.call_id,
                tool_name=authorization.tool_name,
                success=False,
                error_code="terminal_template_failed",
                metadata={
                    "return_code": completed.returncode,
                    "template": arguments.template,
                },
            )

        output = completed.stdout
        truncated = len(output) > _TERMINAL_OUTPUT_MAX_BYTES
        bounded = output[:_TERMINAL_OUTPUT_MAX_BYTES]
        return ToolExecutionResult(
            call_id=authorization.call_id,
            tool_name=authorization.tool_name,
            success=True,
            output=bounded.decode("utf-8", errors="replace"),
            metadata={
                "bytes_read": len(bounded),
                "return_code": completed.returncode,
                "template": arguments.template,
                "truncated": truncated,
            },
        )

    @staticmethod
    def _run_security_posture(
        authorization: ToolAuthorization,
        context: PolicyContext,
        template: str,
    ) -> ToolExecutionResult:
        workspace = context.workspace_root.resolve(strict=True)
        if not workspace.is_dir():
            raise PermissionError("workspace root is not a directory")

        states: dict[str, str] = {}
        unavailable_controls = 0
        for label, command in _SECURITY_POSTURE_COMMANDS:
            try:
                completed = subprocess.run(
                    command,
                    cwd=workspace,
                    env={
                        "LC_ALL": "C",
                        "PATH": "/usr/bin:/bin:/usr/sbin:/sbin",
                    },
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.DEVNULL,
                    timeout=_TERMINAL_TIMEOUT_SECONDS,
                    check=False,
                )
            except (OSError, subprocess.TimeoutExpired):
                unavailable_controls += 1
                states[label] = "unavailable"
                continue
            state = (
                _security_control_state(label, completed.stdout)
                if completed.returncode == 0
                else "unavailable"
            )
            states[label] = state
            if state == "unavailable":
                unavailable_controls += 1

        output = json.dumps(states, separators=(",", ":"), sort_keys=True)
        return ToolExecutionResult(
            call_id=authorization.call_id,
            tool_name=authorization.tool_name,
            success=True,
            output=output,
            metadata={
                "bytes_read": len(output.encode("utf-8")),
                "controls": len(_SECURITY_POSTURE_COMMANDS),
                "template": template,
                "truncated": False,
                "unavailable_controls": unavailable_controls,
            },
        )

    @staticmethod
    def _probe_tcp(address: str, port: int, timeout_seconds: float) -> str:
        parsed = ip_address(address)
        family = socket.AF_INET6 if parsed.version == 6 else socket.AF_INET
        destination: tuple[object, ...]
        if parsed.version == 6:
            destination = (address, port, 0, 0)
        else:
            destination = (address, port)
        try:
            with socket.socket(family, socket.SOCK_STREAM) as connection:
                connection.settimeout(timeout_seconds)
                result = connection.connect_ex(destination)
        except OSError:
            return "unreachable"
        if result == 0:
            return "open"
        if result == errno.ECONNREFUSED:
            return "closed"
        return "unreachable"

    @staticmethod
    def _read_regular_file(root: Path, relative_path: str, max_bytes: int) -> tuple[bytes, bool]:
        parts = PurePosixPath(relative_path).parts
        if not parts or any(part in {"", ".", ".."} for part in parts):
            raise PermissionError("unsafe path")

        directory_flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
        no_follow = getattr(os, "O_NOFOLLOW", 0)
        close_on_exec = getattr(os, "O_CLOEXEC", 0)
        descriptors: list[int] = [
            os.open(root.resolve(strict=True), directory_flags | close_on_exec)
        ]
        try:
            for part in parts[:-1]:
                descriptor = os.open(
                    part,
                    directory_flags | no_follow | close_on_exec,
                    dir_fd=descriptors[-1],
                )
                descriptors.append(descriptor)

            file_descriptor = os.open(
                parts[-1],
                os.O_RDONLY | no_follow | close_on_exec | getattr(os, "O_NONBLOCK", 0),
                dir_fd=descriptors[-1],
            )
            descriptors.append(file_descriptor)
            if not stat.S_ISREG(os.fstat(file_descriptor).st_mode):
                raise PermissionError("only regular files may be read")

            chunks: list[bytes] = []
            remaining = max_bytes + 1
            while remaining > 0:
                chunk = os.read(file_descriptor, min(65_536, remaining))
                if not chunk:
                    break
                chunks.append(chunk)
                remaining -= len(chunk)
            payload = b"".join(chunks)
            return payload[:max_bytes], len(payload) > max_bytes
        finally:
            for descriptor in reversed(descriptors):
                os.close(descriptor)

    @staticmethod
    def _error(authorization: ToolAuthorization, error_code: str) -> ToolExecutionResult:
        return ToolExecutionResult(
            call_id=authorization.call_id,
            tool_name=authorization.tool_name,
            success=False,
            error_code=error_code,
        )
