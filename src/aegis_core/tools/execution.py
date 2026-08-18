from __future__ import annotations

import json
import os
import platform
import stat
from collections.abc import Callable
from pathlib import Path, PurePosixPath

from pydantic import ValidationError

from aegis_core.contracts import PolicyDecision, ToolAuthorization, ToolExecutionResult
from aegis_core.tools.broker import PolicyContext
from aegis_core.tools.defaults import ReadTextArguments, RuntimeInfoArguments

ToolHandler = Callable[[ToolAuthorization, PolicyContext], ToolExecutionResult]


class ReadOnlyToolExecutor:
    def __init__(self) -> None:
        self._handlers: dict[str, ToolHandler] = {
            "system_describe_runtime": self._describe_runtime,
            "filesystem_read_text": self._read_text,
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
