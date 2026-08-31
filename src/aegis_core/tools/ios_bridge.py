from __future__ import annotations

import asyncio
import json
import os
import stat
import struct
import threading
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any, ClassVar
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from aegis_core.contracts import (
    AgentRole,
    Capability,
    PolicyDecision,
    RiskLevel,
    ToolAuthorization,
    ToolExecutionResult,
)
from aegis_core.ipc.protocol import IpcRequest
from aegis_core.ipc.server import IpcHandlerResult, IpcMethodHandler
from aegis_core.secrets import contains_likely_secret_material
from aegis_core.tools.audit import AuditSink, NullAuditSink
from aegis_core.tools.broker import PolicyContext, ToolDefinition
from aegis_core.tools.device_config import load_private_profile_list

MAX_SHORTCUT_OUTPUT_BYTES = 16_384
MAX_AUTH_FRAME_BYTES = 4_096
SHORTCUTS_JXA = r"""
function run(argv) {
    if (argv.length !== 1) { throw new Error("invalid_argument_count"); }
    const request = JSON.parse(argv[0]);
    const shortcuts = Application("Shortcuts Events");
    const candidates = shortcuts.shortcuts.whose({name: request.name})();
    if (candidates.length !== 1) { throw new Error("shortcut_absent_or_ambiguous"); }
    const result = shortcuts.run(candidates[0], {withInput: request.input});
    return JSON.stringify({ok: true, result: result === undefined ? null : String(result)});
}
"""


class IOSBridgeError(RuntimeError):
    """Raised when a paired-device automation cannot be safely dispatched."""


class FocusMode(StrEnum):
    NORMAL = "normal"
    WORK = "work"
    SLEEPING = "sleeping"
    PERSONAL = "personal"


@dataclass(frozen=True, slots=True)
class FocusPrioritySnapshot:
    mode: FocusMode
    active: bool
    planning_priority: str


class FocusPriorityState:
    _PRIORITIES: ClassVar[dict[FocusMode, str]] = {
        FocusMode.NORMAL: "normal",
        FocusMode.WORK: "focused",
        FocusMode.SLEEPING: "quiet",
        FocusMode.PERSONAL: "balanced",
    }

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._mode = FocusMode.NORMAL
        self._active = False

    def update(self, mode: FocusMode, active: bool) -> FocusPrioritySnapshot:
        with self._lock:
            self._mode = mode if active else FocusMode.NORMAL
            self._active = active
            return self._snapshot_unlocked()

    def snapshot(self) -> FocusPrioritySnapshot:
        with self._lock:
            return self._snapshot_unlocked()

    def _snapshot_unlocked(self) -> FocusPrioritySnapshot:
        return FocusPrioritySnapshot(
            mode=self._mode,
            active=self._active,
            planning_priority=self._PRIORITIES[self._mode],
        )


class IOSShortcutProfile(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    shortcut_id: str = Field(pattern=r"^[a-z][a-z0-9-]{2,31}$")
    name: str = Field(min_length=1, max_length=128)
    critical: bool = False

    @field_validator("name")
    @classmethod
    def name_must_be_normalized(cls, value: str) -> str:
        if value != " ".join(value.split()) or not value.isprintable():
            raise ValueError("Shortcut name must be normalized")
        return value


class IOSShortcutArguments(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    shortcut_id: str = Field(pattern=r"^[a-z][a-z0-9-]{2,31}$")
    input_text: str = Field(default="", max_length=1_024)


class FocusUpdatePayload(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    mode: FocusMode
    active: bool
    source: str = Field(pattern=r"^focus_filter$")


class IOSShortcutBridge:
    def __init__(
        self,
        profiles: tuple[IOSShortcutProfile, ...],
        *,
        authorizer_path: Path,
        timeout_seconds: float = 30.0,
    ) -> None:
        if not 2.0 <= timeout_seconds <= 60.0:
            raise ValueError("iOS Shortcut timeout is invalid")
        self._profiles = {profile.shortcut_id: profile for profile in profiles}
        self._authorizer_path = authorizer_path
        self._timeout = timeout_seconds

    @classmethod
    def from_file(
        cls,
        configuration_path: Path,
        *,
        authorizer_path: Path,
        timeout_seconds: float,
    ) -> IOSShortcutBridge:
        return cls(
            load_private_profile_list(configuration_path, IOSShortcutProfile),
            authorizer_path=authorizer_path,
            timeout_seconds=timeout_seconds,
        )

    async def run(self, shortcut_id: str, input_text: str) -> tuple[str, bool]:
        profile = self._profiles.get(shortcut_id)
        if profile is None:
            raise IOSBridgeError("iOS Shortcut profile is not configured")
        normalized = " ".join(input_text.split())
        if len(normalized) > 1_024 or contains_likely_secret_material(normalized):
            raise IOSBridgeError("Shortcut input is unsafe")
        touch_id_verified = False
        if profile.critical:
            await self._authorize_user_presence(
                reason=f"Autorizar el atajo crítico de Jarvis: {profile.name}"
            )
            touch_id_verified = True
        request = json.dumps(
            {"name": profile.name, "input": normalized},
            ensure_ascii=False,
            separators=(",", ":"),
        )
        output = await self._run_subprocess(
            "/usr/bin/osascript",
            "-l",
            "JavaScript",
            "-e",
            SHORTCUTS_JXA,
            request,
            maximum_output=MAX_SHORTCUT_OUTPUT_BYTES,
        )
        try:
            payload = json.loads(output)
        except json.JSONDecodeError as error:
            raise IOSBridgeError("Shortcuts Events returned malformed JSON") from error
        if not isinstance(payload, dict) or payload.get("ok") is not True:
            raise IOSBridgeError("Shortcuts Events rejected the automation")
        result = payload.get("result")
        if result is not None and not isinstance(result, str):
            raise IOSBridgeError("Shortcut result has an invalid type")
        return (result or "shortcut_completed")[:MAX_SHORTCUT_OUTPUT_BYTES], touch_id_verified

    async def _authorize_user_presence(self, *, reason: str) -> None:
        self._validate_authorizer()
        request_id = uuid4()
        challenge = os.urandom(32).hex()
        body = json.dumps(
            {
                "protocol_version": "1.0",
                "request_id": str(request_id),
                "challenge": challenge,
                "reason": reason,
            },
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
        if len(body) > MAX_AUTH_FRAME_BYTES:
            raise IOSBridgeError("Touch ID request exceeds the safety ceiling")
        process = await asyncio.create_subprocess_exec(
            str(self._authorizer_path),
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
            env={"PATH": "/usr/bin:/bin:/usr/sbin:/sbin", "LANG": "C.UTF-8"},
        )
        framed = struct.pack(">I", len(body)) + body
        try:
            stdout, _ = await asyncio.wait_for(process.communicate(framed), timeout=30.0)
        except TimeoutError as error:
            process.kill()
            await process.wait()
            raise IOSBridgeError("Touch ID authorization timed out") from error
        if process.returncode != 0 or len(stdout) < 4:
            raise IOSBridgeError("Touch ID authorization was denied")
        response_length = struct.unpack(">I", stdout[:4])[0]
        if response_length > MAX_AUTH_FRAME_BYTES or len(stdout) != response_length + 4:
            raise IOSBridgeError("Touch ID response framing is invalid")
        try:
            response = json.loads(stdout[4:].decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise IOSBridgeError("Touch ID response is malformed") from error
        if (
            not isinstance(response, dict)
            or response.get("ok") is not True
            or response.get("request_id") != str(request_id)
            or response.get("challenge") != challenge
        ):
            raise IOSBridgeError("Touch ID response did not authenticate the request")

    def _validate_authorizer(self) -> None:
        info = self._authorizer_path.lstat()
        if (
            stat.S_ISLNK(info.st_mode)
            or not stat.S_ISREG(info.st_mode)
            or info.st_uid not in {0, os.getuid()}
            or not info.st_mode & stat.S_IXUSR
            or stat.S_IMODE(info.st_mode) & 0o022
        ):
            raise IOSBridgeError("Touch ID helper executable is unsafe")

    async def _run_subprocess(
        self,
        executable: str,
        *arguments: str,
        maximum_output: int,
    ) -> str:
        process = await asyncio.create_subprocess_exec(
            executable,
            *arguments,
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env={"PATH": "/usr/bin:/bin:/usr/sbin:/sbin", "LANG": "C.UTF-8"},
        )
        assert process.stdout is not None and process.stderr is not None
        stdout_task = asyncio.create_task(process.stdout.read(maximum_output + 1))
        stderr_task = asyncio.create_task(process.stderr.read(4_097))
        try:
            stdout, stderr, return_code = await asyncio.wait_for(
                self._collect(process, stdout_task, stderr_task),
                timeout=self._timeout,
            )
        except TimeoutError as error:
            process.kill()
            await process.wait()
            stdout_task.cancel()
            stderr_task.cancel()
            await asyncio.gather(stdout_task, stderr_task, return_exceptions=True)
            raise IOSBridgeError("Shortcut execution timed out") from error
        if len(stdout) > maximum_output or len(stderr) > 4_096 or return_code != 0:
            raise IOSBridgeError("Shortcut execution failed closed")
        try:
            return stdout.decode("utf-8")
        except UnicodeDecodeError as error:
            raise IOSBridgeError("Shortcut output is not UTF-8") from error

    @staticmethod
    async def _collect(
        process: asyncio.subprocess.Process,
        stdout_task: asyncio.Task[bytes],
        stderr_task: asyncio.Task[bytes],
    ) -> tuple[bytes, bytes, int]:
        stdout, stderr, return_code = await asyncio.gather(
            stdout_task,
            stderr_task,
            process.wait(),
        )
        return stdout, stderr, return_code


class IOSBridgeService:
    TOOL_NAME = "ios_shortcut_run"
    FOCUS_METHOD = "system.focus.update"

    def __init__(
        self,
        bridge: IOSShortcutBridge,
        focus_state: FocusPriorityState,
        *,
        audit_sink: AuditSink | None = None,
    ) -> None:
        self._bridge = bridge
        self._focus_state = focus_state
        self._audit = audit_sink or NullAuditSink()

    def tool_definitions(self) -> tuple[ToolDefinition, ...]:
        return (
            ToolDefinition(
                name=self.TOOL_NAME,
                description=(
                    "Run one allowlisted iCloud-synchronized Shortcut by opaque id. Critical "
                    "mobile or smart-lock shortcuts require a fresh Touch ID verification."
                ),
                arguments_model=IOSShortcutArguments,
                capability=Capability.DEVICE_CONTROL,
                risk=RiskLevel.CRITICAL,
                allowed_roles=frozenset({AgentRole.PLANNER}),
                requires_confirmation=True,
                provider_label="shortcuts_events",
                external_destination="icloud_shortcuts",
            ),
        )

    def tool_handlers(self) -> dict[str, Any]:
        return {self.TOOL_NAME: self.execute}

    def ipc_handlers(self) -> dict[str, IpcMethodHandler]:
        return {self.FOCUS_METHOD: self.handle_focus_update}

    async def execute(
        self,
        authorization: ToolAuthorization,
        context: PolicyContext,
    ) -> ToolExecutionResult:
        del context
        if (
            authorization.decision is not PolicyDecision.ALLOW
            or authorization.reason_code != "confirmation_consumed"
        ):
            return self._error(authorization, "access_denied")
        arguments = IOSShortcutArguments.model_validate(authorization.normalized_arguments)
        try:
            output, touch_id_verified = await self._bridge.run(
                arguments.shortcut_id,
                arguments.input_text,
            )
        except (IOSBridgeError, OSError, ValueError):
            return self._error(authorization, "ios_shortcut_failed")
        self._audit.record_system_event(
            uuid4(),
            event_type="device_command_completed",
            component="ios_bridge",
            call_id=authorization.call_id,
            data={
                "shortcut_id": arguments.shortcut_id,
                "touch_id_verified": touch_id_verified,
                "state": "completed",
            },
        )
        return ToolExecutionResult(
            call_id=authorization.call_id,
            tool_name=authorization.tool_name,
            success=True,
            output=output,
            metadata={
                "device_state": "completed",
                "shortcut_id": arguments.shortcut_id,
                "touch_id_verified": touch_id_verified,
                "verified": True,
            },
        )

    async def handle_focus_update(self, request: IpcRequest) -> IpcHandlerResult:
        if request.method != self.FOCUS_METHOD:
            return IpcHandlerResult(ok=False, error_code="method_not_found")
        try:
            payload = FocusUpdatePayload.model_validate(request.payload)
        except ValidationError:
            return IpcHandlerResult(ok=False, error_code="invalid_payload")
        snapshot = self._focus_state.update(payload.mode, payload.active)
        self._audit.record_system_event(
            request.request_id,
            event_type="focus_state_updated",
            component="ios_bridge",
            call_id=request.nonce,
            data={
                "mode": snapshot.mode.value,
                "active": snapshot.active,
                "planning_priority": snapshot.planning_priority,
            },
        )
        return IpcHandlerResult(
            ok=True,
            payload={
                "mode": snapshot.mode.value,
                "active": snapshot.active,
                "planning_priority": snapshot.planning_priority,
            },
        )

    @staticmethod
    def _error(authorization: ToolAuthorization, code: str) -> ToolExecutionResult:
        return ToolExecutionResult(
            call_id=authorization.call_id,
            tool_name=authorization.tool_name,
            success=False,
            error_code=code,
            metadata={"verified": False},
        )
