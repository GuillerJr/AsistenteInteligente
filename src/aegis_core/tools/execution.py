from __future__ import annotations

import errno
import json
import os
import platform
import socket
import stat
import subprocess
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from ipaddress import ip_address, ip_network
from pathlib import Path, PurePosixPath

from pydantic import ValidationError

from aegis_core.contracts import PolicyDecision, ToolAuthorization, ToolExecutionResult
from aegis_core.tools.broker import PolicyContext
from aegis_core.tools.defaults import (
    ApplicationOpenArguments,
    BrowserOpenArguments,
    CalendarCreateArguments,
    CalendarListArguments,
    MailListRecentArguments,
    MailSendArguments,
    NetworkDiscoveryArguments,
    ReadTextArguments,
    RuntimeInfoArguments,
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

_MAIL_LIST_SCRIPT = r"""
const Mail = Application("Mail");
const messages = Mail.inbox.messages();
const output = [];
for (let index = 0; index < messages.length && output.length < payload.limit; index++) {
    const message = messages[index];
    const unread = !message.readStatus();
    if (payload.unread_only && !unread) continue;
    output.push({
        id: String(message.id()),
        sender: String(message.sender() || "").slice(0, 500),
        subject: String(message.subject() || "").slice(0, 500),
        date_received: new Date(message.dateReceived()).toISOString(),
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

_CALENDAR_LIST_SCRIPT = r"""
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
            end_at: new Date(event.endDate()).toISOString(),
            location: String(event.location() || "").slice(0, 500)
        });
        if (output.length >= payload.limit) break;
    }
    if (output.length >= payload.limit) break;
}
output.sort((left, right) => left.start_at.localeCompare(right.start_at));
JSON.stringify({events: output.slice(0, payload.limit)});
"""

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


class ReadOnlyToolExecutor:
    def __init__(
        self,
        *,
        tcp_connector: TcpConnector | None = None,
        web_client_factory: WebClientFactory = PublicWebClient,
    ) -> None:
        self._tcp_connector = tcp_connector or self._probe_tcp
        self._web_client_factory = web_client_factory
        self._handlers: dict[str, ToolHandler] = {
            "system_describe_runtime": self._describe_runtime,
            "filesystem_read_text": self._read_text,
            "web_research": self._web_research,
            "web_fetch": self._web_fetch,
            "mail_list_recent": self._mail_list_recent,
            "mail_send_message": self._mail_send_message,
            "calendar_list_events": self._calendar_list_events,
            "calendar_create_event": self._calendar_create_event,
            "browser_open_url": self._browser_open_url,
            "application_open": self._application_open,
            "network_discover_hosts": self._discover_network,
            "terminal_run_template": self._run_terminal_template,
        }

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
            "operating_system": platform.system(),
            "os_release": platform.release(),
            "python": platform.python_version(),
        }
        return ToolExecutionResult(
            call_id=authorization.call_id,
            tool_name=authorization.tool_name,
            success=True,
            output=json.dumps(runtime, separators=(",", ":"), sort_keys=True),
            metadata={"source": "local_runtime"},
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
        return ReadOnlyToolExecutor._json_result(authorization, output, "apple_calendar")

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
        arguments = TerminalTemplateArguments.model_validate(
            authorization.normalized_arguments
        )
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
