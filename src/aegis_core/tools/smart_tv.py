from __future__ import annotations

import asyncio
import base64
import json
import re
import socket
import ssl
import subprocess
import time
from collections.abc import Mapping
from enum import StrEnum
from ipaddress import ip_address
from pathlib import Path
from typing import Any, Literal
from urllib.parse import quote
from uuid import uuid4

import aiohttp
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from aegis_core.contracts import (
    AgentRole,
    Capability,
    PolicyDecision,
    RiskLevel,
    ToolAuthorization,
    ToolExecutionResult,
)
from aegis_core.secrets import (
    InvalidGenericSecretError,
    MacOSGenericSecret,
    SecretNotFoundError,
)
from aegis_core.tools.audit import AuditSink, NullAuditSink
from aegis_core.tools.broker import PolicyContext, ToolDefinition
from aegis_core.tools.device_config import load_private_profile_list

MAX_DISCOVERY_DATAGRAM_BYTES = 8_192
WEBOS_PERMISSIONS = (
    "LAUNCH",
    "CONTROL_AUDIO",
    "CONTROL_DISPLAY",
    "CONTROL_INPUT_JOYSTICK",
    "CONTROL_POWER",
    "READ_CURRENT_CHANNEL",
    "READ_INPUT_DEVICE_LIST",
)


class TargetDeviceOffline(ConnectionError):
    """Raised when an authorized local device cannot be reached or authenticated."""


class TVProtocolError(RuntimeError):
    """Raised when a TV returns a malformed or rejected protocol response."""


class TVPlatform(StrEnum):
    WEBOS = "webos"
    TIZEN = "tizen"
    ANDROID = "android"


class SmartTVProfile(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    device_id: str = Field(pattern=r"^[a-z][a-z0-9-]{2,31}$")
    platform: TVPlatform
    host: str
    mac_address: str | None = None
    port: int | None = Field(default=None, ge=1, le=65_535)
    certificate_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    rpc_path: str = Field(default="/jsonrpc", pattern=r"^/[A-Za-z0-9._~!$&'()*+,;=:@/-]{0,255}$")

    @field_validator("host")
    @classmethod
    def host_must_be_a_private_literal(cls, value: str) -> str:
        parsed = ip_address(value)
        if not parsed.is_private or parsed.is_loopback or parsed.is_multicast:
            raise ValueError("TV host must be a private, non-loopback IP literal")
        return parsed.compressed

    @field_validator("mac_address")
    @classmethod
    def normalize_mac(cls, value: str | None) -> str | None:
        if value is None:
            return None
        compact = re.sub(r"[:-]", "", value).lower()
        if re.fullmatch(r"[0-9a-f]{12}", compact) is None or compact == "0" * 12:
            raise ValueError("TV MAC address is invalid")
        return ":".join(compact[index : index + 2] for index in range(0, 12, 2))

    @model_validator(mode="after")
    def transport_must_be_pinned(self) -> SmartTVProfile:
        if (
            self.platform in {TVPlatform.WEBOS, TVPlatform.TIZEN}
            and self.certificate_sha256 is None
        ):
            raise ValueError("TV WebSocket TLS certificate fingerprint is required")
        return self

    @property
    def resolved_port(self) -> int:
        if self.port is not None:
            return self.port
        return {
            TVPlatform.WEBOS: 3001,
            TVPlatform.TIZEN: 8002,
            TVPlatform.ANDROID: 6466,
        }[self.platform]

    @property
    def credential(self) -> MacOSGenericSecret:
        return MacOSGenericSecret(f"ai.aegis.device.{self.device_id}.{self.platform.value}")


class SmartTVControlArguments(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    device_id: str = Field(pattern=r"^[a-z][a-z0-9-]{2,31}$")
    action: Literal[
        "wake",
        "launch_app",
        "set_volume",
        "volume_up",
        "volume_down",
        "mute",
        "set_input",
        "key",
        "power_off",
    ]
    value: str | int | None = None

    @model_validator(mode="after")
    def validate_action_value(self) -> SmartTVControlArguments:
        if self.action in {"wake", "power_off", "volume_up", "volume_down", "mute"}:
            if self.value is not None:
                raise ValueError("this TV action does not accept a value")
            return self
        if self.action == "set_volume":
            if (
                isinstance(self.value, bool)
                or not isinstance(self.value, int)
                or not 0 <= self.value <= 100
            ):
                raise ValueError("TV volume must be an integer between 0 and 100")
            return self
        if not isinstance(self.value, str) or not 1 <= len(self.value) <= 128:
            raise ValueError("TV action requires a bounded string value")
        pattern = r"^[A-Za-z0-9][A-Za-z0-9._:+/-]{0,127}$"
        if re.fullmatch(pattern, self.value) is None:
            raise ValueError("TV action value contains unsupported characters")
        return self


class SmartTVDiscoveryArguments(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    timeout_milliseconds: int = Field(default=1_200, ge=250, le=2_000)


def build_magic_packet(mac_address: str) -> bytes:
    compact = re.sub(r"[:-]", "", mac_address).lower()
    if re.fullmatch(r"[0-9a-f]{12}", compact) is None or compact == "0" * 12:
        raise ValueError("MAC address is invalid")
    hardware = bytes.fromhex(compact)
    return b"\xff" * 6 + hardware * 16


async def send_wake_on_lan(mac_address: str, *, port: int = 9) -> None:
    if not 1 <= port <= 65_535:
        raise ValueError("Wake-on-LAN port is invalid")
    packet = build_magic_packet(mac_address)
    loop = asyncio.get_running_loop()
    udp = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        udp.setblocking(False)
        udp.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        await loop.sock_sendto(udp, packet, ("255.255.255.255", port))
    except OSError as error:
        raise TargetDeviceOffline("Wake-on-LAN broadcast failed") from error
    finally:
        udp.close()


async def discover_smart_tvs(*, timeout_seconds: float = 1.2) -> tuple[dict[str, str], ...]:
    if not 0.25 <= timeout_seconds <= 2.0:
        raise ValueError("SSDP timeout is invalid")
    request = (
        b"M-SEARCH * HTTP/1.1\r\n"
        b"HOST: 239.255.255.250:1900\r\n"
        b'MAN: "ssdp:discover"\r\n'
        b"MX: 1\r\n"
        b"ST: ssdp:all\r\n\r\n"
    )
    loop = asyncio.get_running_loop()
    udp = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    results: dict[tuple[str, str], dict[str, str]] = {}
    try:
        udp.setblocking(False)
        await loop.sock_sendto(udp, request, ("239.255.255.250", 1900))
        deadline = loop.time() + timeout_seconds
        while True:
            remaining = deadline - loop.time()
            if remaining <= 0:
                break
            try:
                data, source = await asyncio.wait_for(
                    loop.sock_recvfrom(udp, MAX_DISCOVERY_DATAGRAM_BYTES),
                    timeout=remaining,
                )
            except TimeoutError:
                break
            if len(data) >= MAX_DISCOVERY_DATAGRAM_BYTES:
                continue
            try:
                text = data.decode("iso-8859-1")
            except UnicodeDecodeError:
                continue
            headers: dict[str, str] = {}
            for line in text.split("\r\n")[1:]:
                if ":" not in line:
                    continue
                key, value = line.split(":", maxsplit=1)
                headers[key.strip().casefold()] = value.strip()[:512]
            server = headers.get("server", "")
            search_target = headers.get("st", "")
            combined = f"{server} {search_target}".casefold()
            if not any(marker in combined for marker in ("webos", "samsung", "android", "google")):
                continue
            host = ip_address(source[0])
            if not host.is_private or host.is_loopback:
                continue
            platform = (
                "webos" if "webos" in combined else "tizen" if "samsung" in combined else "android"
            )
            results[(host.compressed, platform)] = {
                "host": host.compressed,
                "platform": platform,
                "server": server[:160],
            }
    except OSError as error:
        raise TargetDeviceOffline("SSDP discovery failed") from error
    finally:
        udp.close()
    return tuple(results[key] for key in sorted(results))


class SmartTVController:
    def __init__(
        self,
        profiles: tuple[SmartTVProfile, ...],
        *,
        timeout_seconds: float = 6.0,
        audit_sink: AuditSink | None = None,
    ) -> None:
        if not 1.0 <= timeout_seconds <= 15.0:
            raise ValueError("TV timeout is invalid")
        self._profiles = {profile.device_id: profile for profile in profiles}
        self._timeout = aiohttp.ClientTimeout(
            total=timeout_seconds,
            connect=min(3.0, timeout_seconds),
        )
        self._audit = audit_sink or NullAuditSink()
        self._session: aiohttp.ClientSession | None = None

    @classmethod
    def from_file(
        cls,
        path: Path,
        *,
        timeout_seconds: float,
        audit_sink: AuditSink | None = None,
    ) -> SmartTVController:
        return cls(
            load_private_profile_list(path, SmartTVProfile),
            timeout_seconds=timeout_seconds,
            audit_sink=audit_sink,
        )

    async def close(self) -> None:
        if self._session is not None:
            await self._session.close()
            self._session = None

    async def execute(self, arguments: SmartTVControlArguments) -> tuple[str, int]:
        profile = self._profile(arguments.device_id)
        started = time.perf_counter_ns()
        if arguments.action == "wake":
            if profile.mac_address is None:
                raise TVProtocolError("TV profile has no Wake-on-LAN MAC address")
            await send_wake_on_lan(profile.mac_address)
            state = "wake_packet_sent"
        elif profile.platform is TVPlatform.WEBOS:
            state = await self._execute_webos(profile, arguments)
        elif profile.platform is TVPlatform.TIZEN:
            state = await self._execute_tizen(profile, arguments)
        else:
            state = await self._execute_android(profile, arguments)
        latency = round((time.perf_counter_ns() - started) / 1_000_000)
        return state, latency

    async def verify(self, device_id: str) -> tuple[bool, int]:
        profile = self._profile(device_id)
        started = time.perf_counter_ns()
        try:
            reader, writer = await asyncio.wait_for(
                asyncio.open_connection(profile.host, profile.resolved_port),
                timeout=min(self._timeout.total or 6.0, 3.0),
            )
            del reader
            writer.close()
            await writer.wait_closed()
            online = True
        except (TimeoutError, OSError):
            online = False
        return online, round((time.perf_counter_ns() - started) / 1_000_000)

    async def wake(self, device_id: str) -> tuple[bool, int]:
        profile = self._profile(device_id)
        if profile.mac_address is None:
            return False, 0
        started = time.perf_counter_ns()
        await send_wake_on_lan(profile.mac_address)
        return True, round((time.perf_counter_ns() - started) / 1_000_000)

    def _profile(self, device_id: str) -> SmartTVProfile:
        profile = self._profiles.get(device_id)
        if profile is None:
            raise TVProtocolError("TV profile is not configured")
        return profile

    async def _client(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            connector = aiohttp.TCPConnector(limit=4, ttl_dns_cache=0, enable_cleanup_closed=True)
            self._session = aiohttp.ClientSession(timeout=self._timeout, connector=connector)
        return self._session

    @staticmethod
    def _fingerprint(profile: SmartTVProfile) -> aiohttp.Fingerprint:
        if profile.certificate_sha256 is None:
            raise TVProtocolError("TLS certificate pin is unavailable")
        return aiohttp.Fingerprint(bytes.fromhex(profile.certificate_sha256))

    @staticmethod
    async def _credential_value(profile: SmartTVProfile) -> str | None:
        try:
            return await asyncio.to_thread(profile.credential.get)
        except SecretNotFoundError:
            return None
        except (InvalidGenericSecretError, OSError, subprocess.SubprocessError) as error:
            raise TVProtocolError("TV credential could not be read safely") from error

    @staticmethod
    async def _store_credential(profile: SmartTVProfile, value: str) -> None:
        try:
            await asyncio.to_thread(profile.credential.set, value)
        except (InvalidGenericSecretError, OSError, subprocess.SubprocessError) as error:
            raise TVProtocolError("TV credential could not be stored safely") from error

    async def _execute_webos(
        self,
        profile: SmartTVProfile,
        arguments: SmartTVControlArguments,
    ) -> str:
        client = await self._client()
        url = f"wss://{profile.host}:{profile.resolved_port}/"
        try:
            async with client.ws_connect(
                url,
                ssl=self._fingerprint(profile),
                heartbeat=20,
                max_msg_size=65_536,
                autoclose=True,
            ) as websocket:
                await self._webos_register(websocket, profile)
                if arguments.action == "key":
                    return await self._webos_key(websocket, profile, str(arguments.value))
                uri, payload = self._webos_command(arguments)
                response = await self._webos_request(websocket, uri, payload)
                return str(response.get("returnValue", True)).lower()
        except (TimeoutError, OSError, aiohttp.ClientError, ssl.SSLError) as error:
            raise TargetDeviceOffline("WebOS TV is unavailable") from error

    async def _webos_register(
        self,
        websocket: aiohttp.ClientWebSocketResponse,
        profile: SmartTVProfile,
    ) -> None:
        client_key = await self._credential_value(profile)
        payload: dict[str, Any] = {
            "forcePairing": False,
            "pairingType": "PROMPT",
            "manifest": {
                "manifestVersion": 1,
                "appVersion": "1.0",
                "signed": {"appId": "ai.aegis.jarvis", "vendorId": "ai.aegis"},
                "permissions": list(WEBOS_PERMISSIONS),
            },
        }
        if client_key is not None:
            payload["client-key"] = client_key
        await websocket.send_json({"id": "register_0", "type": "register", "payload": payload})
        message = await asyncio.wait_for(websocket.receive(), timeout=self._timeout.total)
        response = self._json_message(message)
        if response.get("type") != "registered":
            raise TVProtocolError("WebOS pairing was rejected")
        returned_key = response.get("payload", {}).get("client-key")
        if isinstance(returned_key, str) and returned_key and returned_key != client_key:
            await self._store_credential(profile, returned_key)
        if client_key is None and not isinstance(returned_key, str):
            raise TVProtocolError("WebOS pairing did not return an authentication key")

    @staticmethod
    def _webos_command(arguments: SmartTVControlArguments) -> tuple[str, dict[str, Any]]:
        if arguments.action == "launch_app":
            return "ssap://system.launcher/launch", {"id": arguments.value}
        if arguments.action == "set_volume":
            return "ssap://audio/setVolume", {"volume": arguments.value}
        if arguments.action == "volume_up":
            return "ssap://audio/volumeUp", {}
        if arguments.action == "volume_down":
            return "ssap://audio/volumeDown", {}
        if arguments.action == "mute":
            return "ssap://audio/setMute", {"mute": True}
        if arguments.action == "set_input":
            return "ssap://tv/switchInput", {"inputId": arguments.value}
        if arguments.action == "power_off":
            return "ssap://system/turnOff", {}
        raise TVProtocolError("unsupported WebOS action")

    async def _webos_request(
        self,
        websocket: aiohttp.ClientWebSocketResponse,
        uri: str,
        payload: Mapping[str, Any],
    ) -> dict[str, Any]:
        request_id = f"jarvis_{uuid4().hex}"
        await websocket.send_json(
            {"id": request_id, "type": "request", "uri": uri, "payload": dict(payload)}
        )
        message = await asyncio.wait_for(websocket.receive(), timeout=self._timeout.total)
        response = self._json_message(message)
        if response.get("id") != request_id or response.get("type") == "error":
            raise TVProtocolError("WebOS command was rejected")
        result = response.get("payload")
        if not isinstance(result, dict):
            raise TVProtocolError("WebOS response payload is malformed")
        return result

    async def _webos_key(
        self,
        websocket: aiohttp.ClientWebSocketResponse,
        profile: SmartTVProfile,
        raw_key: str,
    ) -> str:
        key = raw_key.upper()
        if key not in {"HOME", "ENTER", "BACK", "UP", "DOWN", "LEFT", "RIGHT"}:
            raise TVProtocolError("unsupported WebOS navigation key")
        response = await self._webos_request(
            websocket,
            "ssap://com.webos.service.networkinput/getPointerInputSocket",
            {},
        )
        socket_path = response.get("socketPath")
        if not isinstance(socket_path, str) or not socket_path.startswith("wss://"):
            raise TVProtocolError("WebOS input socket is invalid")
        async with (await self._client()).ws_connect(
            socket_path,
            ssl=self._fingerprint(profile),
            max_msg_size=4_096,
        ) as input_socket:
            await input_socket.send_str(f"type:button\nname:{key}\n\n")
        return "key_sent"

    async def _execute_tizen(
        self,
        profile: SmartTVProfile,
        arguments: SmartTVControlArguments,
    ) -> str:
        token = await self._credential_value(profile) or ""
        name = base64.b64encode(b"Jarvis").decode("ascii")
        url = (
            f"wss://{profile.host}:{profile.resolved_port}/api/v2/channels/"
            f"samsung.remote.control?name={quote(name)}"
            + (f"&token={quote(token)}" if token else "")
        )
        try:
            async with (await self._client()).ws_connect(
                url,
                ssl=self._fingerprint(profile),
                max_msg_size=65_536,
                heartbeat=20,
            ) as websocket:
                connect_message = self._json_message(
                    await asyncio.wait_for(websocket.receive(), timeout=self._timeout.total)
                )
                if connect_message.get("event") != "ms.channel.connect":
                    raise TVProtocolError("Tizen authentication was rejected")
                returned = connect_message.get("data", {}).get("token")
                if isinstance(returned, str) and returned and returned != token:
                    await self._store_credential(profile, returned)
                await websocket.send_json(self._tizen_command(arguments))
                return "command_sent"
        except (TimeoutError, OSError, aiohttp.ClientError, ssl.SSLError) as error:
            raise TargetDeviceOffline("Tizen TV is unavailable") from error

    @staticmethod
    def _tizen_command(arguments: SmartTVControlArguments) -> dict[str, Any]:
        if arguments.action == "launch_app":
            return {
                "method": "ms.channel.emit",
                "params": {
                    "event": "ed.apps.launch",
                    "to": "host",
                    "data": {"action_type": "DEEP_LINK", "appId": arguments.value},
                },
            }
        key_map = {
            "HOME": "KEY_HOME",
            "ENTER": "KEY_ENTER",
            "BACK": "KEY_RETURN",
            "UP": "KEY_UP",
            "DOWN": "KEY_DOWN",
            "LEFT": "KEY_LEFT",
            "RIGHT": "KEY_RIGHT",
            "POWER_OFF": "KEY_POWEROFF",
            "INPUT": "KEY_SOURCE",
            "VOLUME_UP": "KEY_VOLUP",
            "VOLUME_DOWN": "KEY_VOLDOWN",
            "MUTE": "KEY_MUTE",
        }
        if arguments.action == "key":
            key = key_map.get(str(arguments.value).upper())
        elif arguments.action == "power_off":
            key = key_map["POWER_OFF"]
        elif arguments.action == "set_input":
            key = key_map["INPUT"]
        elif arguments.action == "volume_up":
            key = key_map["VOLUME_UP"]
        elif arguments.action == "volume_down":
            key = key_map["VOLUME_DOWN"]
        elif arguments.action == "mute":
            key = key_map["MUTE"]
        else:
            raise TVProtocolError("Tizen supports volume through explicit volume keys only")
        if key is None:
            raise TVProtocolError("unsupported Tizen navigation key")
        return {
            "method": "ms.remote.control",
            "params": {
                "Cmd": "Click",
                "DataOfCmd": key,
                "Option": "false",
                "TypeOfRemote": "SendRemoteKey",
            },
        }

    async def _execute_android(
        self,
        profile: SmartTVProfile,
        arguments: SmartTVControlArguments,
    ) -> str:
        method = {
            "launch_app": "device.launch_app",
            "set_volume": "device.set_volume",
            "volume_up": "device.volume_up",
            "volume_down": "device.volume_down",
            "mute": "device.mute",
            "set_input": "device.set_input",
            "key": "device.key",
            "power_off": "device.power_off",
        }.get(arguments.action)
        if method is None:
            raise TVProtocolError("unsupported Android TV action")
        token = await self._credential_value(profile)
        if token is None:
            raise TVProtocolError("Android TV bearer credential is unavailable")
        request_id = uuid4().hex
        body = {
            "jsonrpc": "2.0",
            "id": request_id,
            "method": method,
            "params": {} if arguments.value is None else {"value": arguments.value},
        }
        url = f"https://{profile.host}:{profile.resolved_port}{profile.rpc_path}"
        try:
            async with (await self._client()).post(
                url,
                json=body,
                headers={"Authorization": f"Bearer {token}"},
                ssl=self._fingerprint(profile) if profile.certificate_sha256 else None,
            ) as response:
                oversized = (
                    response.content_length is not None
                    and response.content_length > 65_536
                )
                if response.status != 200 or oversized:
                    raise TVProtocolError("Android TV command was rejected")
                raw_response = await response.content.read(65_537)
                if len(raw_response) > 65_536:
                    raise TVProtocolError("Android TV response exceeds the safety ceiling")
                payload = json.loads(raw_response)
        except (
            TimeoutError,
            OSError,
            aiohttp.ClientError,
            json.JSONDecodeError,
            UnicodeDecodeError,
        ) as error:
            raise TargetDeviceOffline("Android TV is unavailable") from error
        if not isinstance(payload, dict) or payload.get("id") != request_id or "error" in payload:
            raise TVProtocolError("Android TV JSON-RPC response is invalid")
        return "command_sent"

    @staticmethod
    def _json_message(message: aiohttp.WSMessage) -> dict[str, Any]:
        if message.type is not aiohttp.WSMsgType.TEXT or not isinstance(message.data, str):
            raise TVProtocolError("TV WebSocket returned a non-text frame")
        if len(message.data.encode("utf-8")) > 65_536:
            raise TVProtocolError("TV WebSocket response exceeds the safety ceiling")
        try:
            value = json.loads(message.data)
        except json.JSONDecodeError as error:
            raise TVProtocolError("TV WebSocket response is not JSON") from error
        if not isinstance(value, dict):
            raise TVProtocolError("TV WebSocket response is malformed")
        return value


class SmartTVToolService:
    CONTROL_TOOL = "smart_tv_control"
    DISCOVERY_TOOL = "smart_tv_discover"

    def __init__(
        self,
        controller: SmartTVController,
        *,
        audit_sink: AuditSink | None = None,
    ) -> None:
        self._controller = controller
        self._audit = audit_sink or NullAuditSink()

    def tool_definitions(self) -> tuple[ToolDefinition, ...]:
        return (
            ToolDefinition(
                name=self.CONTROL_TOOL,
                description=(
                    "Control one preconfigured local Smart TV by opaque device id. Credentials "
                    "remain in Keychain and every state-changing command is policy-gated."
                ),
                arguments_model=SmartTVControlArguments,
                capability=Capability.DEVICE_CONTROL,
                risk=RiskLevel.HIGH,
                allowed_roles=frozenset({AgentRole.PLANNER}),
                requires_confirmation=True,
                explicit_local_intent_is_sufficient=True,
                provider_label="local_smart_tv",
                external_destination="local_network",
            ),
            ToolDefinition(
                name=self.DISCOVERY_TOOL,
                description="Discover bounded Smart TV SSDP advertisements on the local subnet.",
                arguments_model=SmartTVDiscoveryArguments,
                capability=Capability.DEVICE_CONTROL,
                risk=RiskLevel.MEDIUM,
                allowed_roles=frozenset({AgentRole.PLANNER}),
                provider_label="local_ssdp",
                external_destination="local_network",
            ),
        )

    def handlers(self) -> dict[str, Any]:
        return {self.CONTROL_TOOL: self.control, self.DISCOVERY_TOOL: self.discover}

    async def control(
        self,
        authorization: ToolAuthorization,
        context: PolicyContext,
    ) -> ToolExecutionResult:
        del context
        if authorization.decision is not PolicyDecision.ALLOW or authorization.reason_code not in {
            "confirmation_consumed",
            "explicit_local_intent",
        }:
            return self._error(authorization, "access_denied")
        arguments = SmartTVControlArguments.model_validate(authorization.normalized_arguments)
        try:
            state, latency = await self._controller.execute(arguments)
        except TargetDeviceOffline:
            return self._offline(authorization, arguments.device_id)
        except (TVProtocolError, ValueError):
            return self._error(authorization, "device_command_rejected")
        self._audit.record_system_event(
            uuid4(),
            event_type="device_command_completed",
            component="smart_tv",
            call_id=authorization.call_id,
            data={
                "device_id": arguments.device_id,
                "action": arguments.action,
                "latency_ms": latency,
                "state": state,
            },
        )
        return ToolExecutionResult(
            call_id=authorization.call_id,
            tool_name=authorization.tool_name,
            success=True,
            output=json.dumps(
                {"device_id": arguments.device_id, "state": state},
                separators=(",", ":"),
                sort_keys=True,
            ),
            metadata={
                "device_id": arguments.device_id,
                "device_state": state,
                "network_latency_ms": latency,
                "verified": True,
            },
        )

    async def discover(
        self,
        authorization: ToolAuthorization,
        context: PolicyContext,
    ) -> ToolExecutionResult:
        del context
        arguments = SmartTVDiscoveryArguments.model_validate(authorization.normalized_arguments)
        try:
            devices = await discover_smart_tvs(
                timeout_seconds=arguments.timeout_milliseconds / 1_000.0
            )
        except TargetDeviceOffline:
            return self._error(authorization, "device_discovery_failed")
        return ToolExecutionResult(
            call_id=authorization.call_id,
            tool_name=authorization.tool_name,
            success=True,
            output=json.dumps(devices, separators=(",", ":"), sort_keys=True),
            metadata={"devices": len(devices), "source": "local_ssdp", "verified": True},
        )

    async def wake(self, authorization: ToolAuthorization) -> tuple[bool, int]:
        arguments = SmartTVControlArguments.model_validate(authorization.normalized_arguments)
        try:
            return await self._controller.wake(arguments.device_id)
        except (TargetDeviceOffline, TVProtocolError, ValueError):
            return False, 0

    async def verify(self, authorization: ToolAuthorization) -> tuple[bool, int]:
        arguments = SmartTVControlArguments.model_validate(authorization.normalized_arguments)
        try:
            return await self._controller.verify(arguments.device_id)
        except (TargetDeviceOffline, TVProtocolError, ValueError):
            return False, 0

    @staticmethod
    def _offline(authorization: ToolAuthorization, device_id: str) -> ToolExecutionResult:
        return ToolExecutionResult(
            call_id=authorization.call_id,
            tool_name=authorization.tool_name,
            success=False,
            error_code="target_device_offline",
            metadata={"device_id": device_id, "device_state": "offline", "verified": False},
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
