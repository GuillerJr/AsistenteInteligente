from __future__ import annotations

import asyncio
import os
import re
import stat
import time
import xml.etree.ElementTree as ET
from ipaddress import ip_address
from pathlib import Path
from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from aegis_core.contracts import (
    AgentRole,
    Capability,
    PolicyDecision,
    RiskLevel,
    ToolAuthorization,
    ToolExecutionResult,
)
from aegis_core.tools.audit import AuditSink, NullAuditSink
from aegis_core.tools.broker import PolicyContext, ToolDefinition
from aegis_core.tools.device_config import load_private_profile_list
from aegis_core.tools.smart_tv import TargetDeviceOffline

MAX_ADB_OUTPUT_BYTES = 1_048_576
MAX_UI_NODES = 256
_BOUNDS_PATTERN = re.compile(r"^\[(\d+),(\d+)\]\[(\d+),(\d+)\]$")


class AndroidAutomationError(RuntimeError):
    """Raised when ADB reports a rejected or malformed device operation."""


class AndroidDeviceProfile(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    device_id: str = Field(pattern=r"^[a-z][a-z0-9-]{2,31}$")
    host: str
    port: int = Field(default=5555, ge=1, le=65_535)

    @field_validator("host")
    @classmethod
    def host_must_be_a_private_literal(cls, value: str) -> str:
        parsed = ip_address(value)
        if not parsed.is_private or parsed.is_loopback or parsed.is_multicast:
            raise ValueError("Android host must be a private, non-loopback IP literal")
        return parsed.compressed

    @property
    def serial(self) -> str:
        if ":" in self.host:
            return f"[{self.host}]:{self.port}"
        return f"{self.host}:{self.port}"


class AndroidAutomationArguments(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    device_id: str = Field(pattern=r"^[a-z][a-z0-9-]{2,31}$")
    action: Literal["tap", "text", "launch_app", "find_and_tap"]
    x: int | None = Field(default=None, ge=0, le=16_384)
    y: int | None = Field(default=None, ge=0, le=16_384)
    text: str | None = Field(default=None, max_length=256)
    package_name: str | None = Field(
        default=None,
        pattern=r"^[a-zA-Z][a-zA-Z0-9_]*(?:\.[a-zA-Z0-9_]+)+$",
    )
    activity: str | None = Field(default=None, pattern=r"^[a-zA-Z0-9_.]+$")
    selector: str | None = Field(default=None, max_length=256)
    selector_type: Literal["text", "content_description", "resource_id"] | None = None

    @model_validator(mode="after")
    def arguments_match_action(self) -> AndroidAutomationArguments:
        if self.action == "tap" and (self.x is None or self.y is None):
            raise ValueError("tap requires x and y")
        if self.action == "text":
            if self.text is None:
                raise ValueError("text injection requires text")
            sanitize_android_text(self.text)
        if self.action == "launch_app" and (
            self.package_name is None or self.activity is None
        ):
            raise ValueError("launch_app requires package_name and activity")
        if self.action == "find_and_tap" and (
            self.selector is None or self.selector_type is None
        ):
            raise ValueError("find_and_tap requires selector and selector_type")
        return self


class AndroidUINode(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    text: str = Field(max_length=256)
    content_description: str = Field(max_length=256)
    resource_id: str = Field(max_length=256)
    class_name: str = Field(max_length=256)
    center_x: int = Field(ge=0, le=16_384)
    center_y: int = Field(ge=0, le=16_384)
    clickable: bool
    enabled: bool


def sanitize_android_text(value: str) -> str:
    normalized = " ".join(value.split())
    if (
        not normalized
        or len(normalized) > 256
        or re.fullmatch(r"[\wÀ-ÖØ-öø-ÿ@.+,\- ]+", normalized) is None
    ):
        raise ValueError("Android input text contains unsupported characters")
    return normalized.replace(" ", "%s")


def parse_ui_hierarchy(xml_payload: bytes) -> tuple[AndroidUINode, ...]:
    if not xml_payload or len(xml_payload) > MAX_ADB_OUTPUT_BYTES:
        raise AndroidAutomationError("Android UI hierarchy is empty or oversized")
    lowered = xml_payload.lower()
    if b"<!doctype" in lowered or b"<!entity" in lowered:
        raise AndroidAutomationError("Android UI hierarchy contains forbidden XML declarations")
    start = xml_payload.find(b"<?xml")
    if start < 0:
        start = xml_payload.find(b"<hierarchy")
    end = xml_payload.rfind(b"</hierarchy>")
    if start < 0 or end < start:
        raise AndroidAutomationError("Android UI hierarchy XML is unavailable")
    document = xml_payload[start : end + len(b"</hierarchy>")]
    try:
        root = ET.fromstring(document)
    except ET.ParseError as error:
        raise AndroidAutomationError("Android UI hierarchy XML is malformed") from error
    nodes: list[AndroidUINode] = []
    for element in root.iter("node"):
        bounds = _BOUNDS_PATTERN.fullmatch(element.attrib.get("bounds", ""))
        if bounds is None:
            continue
        left, top, right, bottom = (int(value) for value in bounds.groups())
        if left > right or top > bottom or right > 16_384 or bottom > 16_384:
            continue
        nodes.append(
            AndroidUINode(
                text=element.attrib.get("text", "")[:256],
                content_description=element.attrib.get("content-desc", "")[:256],
                resource_id=element.attrib.get("resource-id", "")[:256],
                class_name=element.attrib.get("class", "")[:256],
                center_x=(left + right) // 2,
                center_y=(top + bottom) // 2,
                clickable=element.attrib.get("clickable") == "true",
                enabled=element.attrib.get("enabled") != "false",
            )
        )
        if len(nodes) == MAX_UI_NODES:
            break
    return tuple(nodes)


class WirelessADBClient:
    def __init__(
        self,
        profiles: tuple[AndroidDeviceProfile, ...],
        *,
        adb_path: Path,
        timeout_seconds: float = 8.0,
    ) -> None:
        if not 1.0 <= timeout_seconds <= 20.0:
            raise ValueError("ADB timeout is invalid")
        self._profiles = {profile.device_id: profile for profile in profiles}
        self._adb_path = adb_path
        self._timeout = timeout_seconds
        self._locks = {profile.device_id: asyncio.Lock() for profile in profiles}

    @classmethod
    def from_file(
        cls,
        configuration_path: Path,
        *,
        adb_path: Path,
        timeout_seconds: float,
    ) -> WirelessADBClient:
        return cls(
            load_private_profile_list(configuration_path, AndroidDeviceProfile),
            adb_path=adb_path,
            timeout_seconds=timeout_seconds,
        )

    async def connect(self, device_id: str) -> tuple[bool, int]:
        profile = self._profile(device_id)
        started = time.perf_counter_ns()
        async with self._locks[device_id]:
            output = await self._run("connect", profile.serial, timeout=self._timeout)
            normalized = output.casefold()
            if "connected to" not in normalized and "already connected" not in normalized:
                raise TargetDeviceOffline("wireless ADB connection was rejected")
            await self._verify_connected(profile)
        return True, round((time.perf_counter_ns() - started) / 1_000_000)

    async def verify(self, device_id: str) -> tuple[bool, int]:
        profile = self._profile(device_id)
        started = time.perf_counter_ns()
        try:
            await self._verify_connected(profile)
            connected = True
        except (AndroidAutomationError, TargetDeviceOffline, OSError):
            connected = False
        return connected, round((time.perf_counter_ns() - started) / 1_000_000)

    async def inject_tap(self, device_id: str, x: int, y: int) -> int:
        if not 0 <= x <= 16_384 or not 0 <= y <= 16_384:
            raise ValueError("tap coordinates are out of range")
        return await self._shell(device_id, "input", "tap", str(x), str(y))

    async def inject_text(self, device_id: str, text: str) -> int:
        return await self._shell(device_id, "input", "text", sanitize_android_text(text))

    async def launch_app(self, device_id: str, package_name: str, activity: str) -> int:
        if (
            re.fullmatch(r"^[a-zA-Z][a-zA-Z0-9_]*(?:\.[a-zA-Z0-9_]+)+$", package_name)
            is None
            or re.fullmatch(r"^[a-zA-Z0-9_.]+$", activity) is None
        ):
            raise ValueError("Android component identifier is invalid")
        return await self._shell(device_id, "am", "start", "-n", f"{package_name}/{activity}")

    async def capture_structure(self, device_id: str) -> tuple[AndroidUINode, ...]:
        profile = self._profile(device_id)
        await self._ensure_connected(profile)
        output = await self._run_bytes(
            "-s",
            profile.serial,
            "exec-out",
            "uiautomator",
            "dump",
            "/dev/tty",
            timeout=self._timeout,
        )
        return parse_ui_hierarchy(output)

    async def find_and_tap(
        self,
        device_id: str,
        *,
        selector: str,
        selector_type: Literal["text", "content_description", "resource_id"],
    ) -> int:
        normalized = " ".join(selector.split()).casefold()
        if not normalized or len(normalized) > 256:
            raise ValueError("Android UI selector is invalid")
        matches = [
            node
            for node in await self.capture_structure(device_id)
            if getattr(node, selector_type).casefold() == normalized and node.enabled
        ]
        if len(matches) != 1:
            raise AndroidAutomationError("Android UI selector is absent or ambiguous")
        node = matches[0]
        return await self.inject_tap(device_id, node.center_x, node.center_y)

    async def _shell(self, device_id: str, *arguments: str) -> int:
        profile = self._profile(device_id)
        started = time.perf_counter_ns()
        async with self._locks[device_id]:
            await self._ensure_connected(profile)
            await self._run("-s", profile.serial, "shell", *arguments, timeout=self._timeout)
        return round((time.perf_counter_ns() - started) / 1_000_000)

    async def _ensure_connected(self, profile: AndroidDeviceProfile) -> None:
        try:
            await self._verify_connected(profile)
        except (AndroidAutomationError, TargetDeviceOffline) as error:
            output = await self._run("connect", profile.serial, timeout=self._timeout)
            if (
                "connected to" not in output.casefold()
                and "already connected" not in output.casefold()
            ):
                raise TargetDeviceOffline("wireless ADB device is offline") from error
            await self._verify_connected(profile)

    async def _verify_connected(self, profile: AndroidDeviceProfile) -> None:
        output = await self._run("devices", timeout=self._timeout)
        states = {
            line.split("\t", maxsplit=1)[0]: line.split("\t", maxsplit=1)[1]
            for line in output.splitlines()
            if line.count("\t") == 1
        }
        if states.get(profile.serial) != "device":
            raise TargetDeviceOffline("wireless ADB device is not authorized and online")

    def _profile(self, device_id: str) -> AndroidDeviceProfile:
        profile = self._profiles.get(device_id)
        if profile is None:
            raise AndroidAutomationError("Android device profile is not configured")
        return profile

    def _validated_executable(self) -> Path:
        executable = self._adb_path.resolve(strict=True)
        info = executable.lstat()
        if (
            stat.S_ISLNK(info.st_mode)
            or not stat.S_ISREG(info.st_mode)
            or info.st_uid not in {0, os.getuid()}
            or not info.st_mode & stat.S_IXUSR
            or stat.S_IMODE(info.st_mode) & 0o022
        ):
            raise AndroidAutomationError("ADB executable is unsafe")
        return executable

    async def _run(self, *arguments: str, timeout: float) -> str:
        return (await self._run_bytes(*arguments, timeout=timeout)).decode("utf-8", errors="strict")

    async def _run_bytes(self, *arguments: str, timeout: float) -> bytes:
        executable = self._validated_executable()
        process = await asyncio.create_subprocess_exec(
            str(executable),
            *arguments,
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env={"PATH": "/usr/bin:/bin:/usr/sbin:/sbin", "LANG": "C.UTF-8"},
        )
        assert process.stdout is not None and process.stderr is not None
        stdout_task = asyncio.create_task(process.stdout.read(MAX_ADB_OUTPUT_BYTES + 1))
        stderr_task = asyncio.create_task(process.stderr.read(65_537))
        try:
            stdout, stderr, return_code = await asyncio.wait_for(
                self._collect_process(process, stdout_task, stderr_task),
                timeout=timeout,
            )
        except TimeoutError as error:
            process.kill()
            await process.wait()
            stdout_task.cancel()
            stderr_task.cancel()
            await asyncio.gather(stdout_task, stderr_task, return_exceptions=True)
            raise TargetDeviceOffline("ADB operation timed out") from error
        if len(stdout) > MAX_ADB_OUTPUT_BYTES or len(stderr) > 65_536:
            raise AndroidAutomationError("ADB output exceeds the safety ceiling")
        if return_code != 0:
            raise TargetDeviceOffline("ADB operation failed")
        return stdout

    @staticmethod
    async def _collect_process(
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


class AndroidAutomationToolService:
    TOOL_NAME = "android_device_control"

    def __init__(self, client: WirelessADBClient, *, audit_sink: AuditSink | None = None) -> None:
        self._client = client
        self._audit = audit_sink or NullAuditSink()

    def tool_definitions(self) -> tuple[ToolDefinition, ...]:
        return (
            ToolDefinition(
                name=self.TOOL_NAME,
                description=(
                    "Control one preconfigured Android device over wireless ADB using bounded "
                    "tap, text, app-launch or logical UI hierarchy operations."
                ),
                arguments_model=AndroidAutomationArguments,
                capability=Capability.DEVICE_CONTROL,
                risk=RiskLevel.CRITICAL,
                allowed_roles=frozenset({AgentRole.PLANNER}),
                requires_confirmation=True,
                provider_label="wireless_adb",
                external_destination="local_network",
            ),
        )

    def handlers(self) -> dict[str, Any]:
        return {self.TOOL_NAME: self.execute}

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
        arguments = AndroidAutomationArguments.model_validate(authorization.normalized_arguments)
        try:
            latency, output = await self._dispatch(arguments)
        except TargetDeviceOffline:
            return ToolExecutionResult(
                call_id=authorization.call_id,
                tool_name=authorization.tool_name,
                success=False,
                error_code="target_device_offline",
                metadata={
                    "device_id": arguments.device_id,
                    "device_state": "offline",
                    "verified": False,
                },
            )
        except (AndroidAutomationError, UnicodeDecodeError, ValueError):
            return self._error(authorization, "device_command_rejected")
        self._audit.record_system_event(
            uuid4(),
            event_type="device_command_completed",
            component="android_adb",
            call_id=authorization.call_id,
            data={
                "device_id": arguments.device_id,
                "action": arguments.action,
                "latency_ms": latency,
                "state": "online",
            },
        )
        return ToolExecutionResult(
            call_id=authorization.call_id,
            tool_name=authorization.tool_name,
            success=True,
            output=output,
            metadata={
                "device_id": arguments.device_id,
                "device_state": "online",
                "network_latency_ms": latency,
                "verified": True,
            },
        )

    async def _dispatch(self, arguments: AndroidAutomationArguments) -> tuple[int, str]:
        if arguments.action == "tap":
            assert arguments.x is not None and arguments.y is not None
            latency = await self._client.inject_tap(arguments.device_id, arguments.x, arguments.y)
            return latency, '{"state":"tap_sent"}'
        if arguments.action == "text":
            assert arguments.text is not None
            latency = await self._client.inject_text(arguments.device_id, arguments.text)
            return latency, '{"state":"text_sent"}'
        if arguments.action == "launch_app":
            assert arguments.package_name is not None and arguments.activity is not None
            latency = await self._client.launch_app(
                arguments.device_id,
                arguments.package_name,
                arguments.activity,
            )
            return latency, '{"state":"application_launched"}'
        if arguments.action != "find_and_tap":
            raise AndroidAutomationError("unsupported Android automation action")
        assert arguments.selector is not None and arguments.selector_type is not None
        started = time.perf_counter_ns()
        await self._client.find_and_tap(
            arguments.device_id,
            selector=arguments.selector,
            selector_type=arguments.selector_type,
        )
        return (
            round((time.perf_counter_ns() - started) / 1_000_000),
            '{"state":"logical_element_pressed"}',
        )

    async def wake(self, authorization: ToolAuthorization) -> tuple[bool, int]:
        arguments = AndroidAutomationArguments.model_validate(authorization.normalized_arguments)
        try:
            return await self._client.connect(arguments.device_id)
        except (AndroidAutomationError, TargetDeviceOffline, OSError, ValueError):
            return False, 0

    async def verify(self, authorization: ToolAuthorization) -> tuple[bool, int]:
        arguments = AndroidAutomationArguments.model_validate(authorization.normalized_arguments)
        try:
            return await self._client.verify(arguments.device_id)
        except (AndroidAutomationError, TargetDeviceOffline, OSError, ValueError):
            return False, 0

    @staticmethod
    def _error(authorization: ToolAuthorization, code: str) -> ToolExecutionResult:
        return ToolExecutionResult(
            call_id=authorization.call_id,
            tool_name=authorization.tool_name,
            success=False,
            error_code=code,
            metadata={"verified": False},
        )
