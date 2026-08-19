from __future__ import annotations

import errno
import json
import os
import platform
import socket
import stat
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from ipaddress import ip_address, ip_network
from pathlib import Path, PurePosixPath

from pydantic import ValidationError

from aegis_core.contracts import PolicyDecision, ToolAuthorization, ToolExecutionResult
from aegis_core.tools.broker import PolicyContext
from aegis_core.tools.defaults import (
    NetworkDiscoveryArguments,
    ReadTextArguments,
    RuntimeInfoArguments,
)

ToolHandler = Callable[[ToolAuthorization, PolicyContext], ToolExecutionResult]
TcpConnector = Callable[[str, int, float], str]

_TCP_CONNECT_TIMEOUT_SECONDS = 0.25
_TCP_CONNECT_WORKERS = 32


class ReadOnlyToolExecutor:
    def __init__(self, *, tcp_connector: TcpConnector | None = None) -> None:
        self._tcp_connector = tcp_connector or self._probe_tcp
        self._handlers: dict[str, ToolHandler] = {
            "system_describe_runtime": self._describe_runtime,
            "filesystem_read_text": self._read_text,
            "network_discover_hosts": self._discover_network,
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
