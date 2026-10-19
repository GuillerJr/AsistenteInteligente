"""Bounded Streamable HTTP MCP client for an explicitly configured HA LAN endpoint.

No discovery, redirect following, environment proxy, DNS, insecure TLS override,
automatic retry, OAuth browser launch, or physical control is performed here.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
from collections.abc import Mapping
from typing import Any

import httpx

from aegis_core.mcp.client import (
    MAX_MCP_MESSAGE_BYTES,
    MCP_PROTOCOL_VERSION,
    McpConfigurationError,
    McpExecutionError,
    McpProtocolError,
    StdioMcpClient,
    _sanitize_call_result,
)
from aegis_core.mcp.integrations import guard_integration
from aegis_core.secrets import MacOSGenericSecret


class HomeAssistantMcpClient(StdioMcpClient):
    def __init__(self, configuration, *, audit_sink=None):
        super().__init__(configuration, audit_sink=audit_sink)
        self._http: httpx.AsyncClient | None = None
        self._http_lock = asyncio.Lock()
        self._session_id: str | None = None
        self._catalogue_digest: str | None = None

    @property
    def available(self) -> bool:
        return bool(
            self._initialized
            and not self._closed
            and self._failure_code is None
            and self._http is not None
            and not self._http.is_closed
        )

    async def start(self):
        endpoint = self.configuration.home_assistant
        if endpoint is None or self._closed or self._http is not None:
            raise McpConfigurationError("Home Assistant endpoint or lifecycle is invalid")
        try:
            token = await asyncio.to_thread(
                MacOSGenericSecret(
                    service=f"ai.aegis.mcp.{self.configuration.server_id}",
                    account=endpoint.credential_account,
                ).get
            )
        except Exception as error:
            raise McpConfigurationError("Home Assistant credential is unavailable") from error
        self._http = httpx.AsyncClient(
            follow_redirects=False,
            trust_env=False,
            limits=httpx.Limits(max_connections=1, max_keepalive_connections=1),
            timeout=self.configuration.call_timeout_seconds,
            headers={
                "Authorization": f"Bearer {token}",
                "Accept": "application/json, text/event-stream",
                "MCP-Protocol-Version": MCP_PROTOCOL_VERSION,
            },
        )
        try:
            async with asyncio.timeout(self.configuration.start_timeout_seconds):
                result = await self._request(
                    "initialize",
                    {
                        "protocolVersion": MCP_PROTOCOL_VERSION,
                        "capabilities": {},
                        "clientInfo": {"name": "Jarvis", "version": "0.1.0"},
                    },
                )
                self._validate_initialize(result)
                await self._post({"jsonrpc": "2.0", "method": "notifications/initialized"})
                tools = await self._list_tools()
                self._catalogue_digest = self._digest(tools)
                self._initialized = True
                return tools
        except BaseException:
            await self.close()
            raise

    async def call_tool(self, name: str, arguments: Mapping[str, Any]):
        if not self.available or self.configuration.integration is None:
            raise McpExecutionError("Home Assistant is unavailable")
        guard_integration(self.configuration.integration, name, arguments)
        try:
            async with self._http_lock, asyncio.timeout(self.configuration.call_timeout_seconds):
                # We do not keep a background notification stream open. Revalidate
                # the catalogue before EACH call so schema changes cannot go unnoticed.
                if self._digest(await self._list_tools()) != self._catalogue_digest:
                    self._failure_code = "catalogue_changed"
                    raise McpProtocolError("Home Assistant catalogue requires review")
                return _sanitize_call_result(
                    await self._request(
                        "tools/call",
                        {"name": name, "arguments": dict(arguments)},
                    )
                )
        except TimeoutError as error:
            self._failure_code = "timeout"
            await self.close()
            raise McpExecutionError("Home Assistant request timed out") from error
        except BaseException:
            self._failure_code = self._failure_code or "protocol_failed"
            await self.close()
            raise

    @staticmethod
    def _digest(tools) -> str:
        encoded = json.dumps(tools, sort_keys=True, allow_nan=False).encode()
        return hashlib.sha256(encoded).hexdigest()

    async def _request(self, method: str, params: Mapping[str, Any]) -> dict[str, Any]:
        request_id = self._next_request_id
        self._next_request_id += 1
        result = await self._post(
            {"jsonrpc": "2.0", "id": request_id, "method": method, "params": dict(params)}
        )
        if result is None:
            raise McpProtocolError("Home Assistant response is missing")
        return result

    async def _post(self, message: dict[str, Any]) -> dict[str, Any] | None:
        endpoint = self.configuration.home_assistant
        if self._http is None or endpoint is None:
            raise McpProtocolError("Home Assistant transport is unavailable")
        headers = {"MCP-Session-Id": self._session_id} if self._session_id else {}
        try:
            async with self._http.stream("POST", endpoint.url, json=message, headers=headers) as r:
                if "id" not in message:
                    if r.status_code != 202:
                        raise McpProtocolError("Home Assistant notification was rejected")
                    return None
                if r.status_code != 200:
                    raise McpProtocolError("Home Assistant request failed")
                session_id = r.headers.get("mcp-session-id")
                if session_id is not None:
                    if (
                        not 1 <= len(session_id) <= 256
                        or any(not 0x21 <= ord(c) <= 0x7E for c in session_id)
                        or (message["method"] != "initialize" and session_id != self._session_id)
                    ):
                        raise McpProtocolError("Home Assistant session changed")
                    self._session_id = session_id
                content_type = r.headers.get("content-type", "").split(";", 1)[0].strip()
                if content_type not in {"application/json", "text/event-stream"}:
                    raise McpProtocolError("Home Assistant response format is unsupported")
                payload = bytearray()
                async for chunk in r.aiter_bytes():
                    payload.extend(chunk)
                    if len(payload) > MAX_MCP_MESSAGE_BYTES:
                        raise McpProtocolError("Home Assistant response exceeds its limit")
                if content_type == "application/json":
                    return self._response(json.loads(payload), message["id"])
                results = []
                for block in payload.decode("utf-8").replace("\r\n", "\n").split("\n\n"):
                    data = "\n".join(
                        line[5:].lstrip(" ")
                        for line in block.splitlines()
                        if line.startswith("data:")
                    )
                    if not data:
                        continue
                    envelope = json.loads(data)
                    # No sampling, elicitation or other server-originated requests.
                    results.append(self._response(envelope, message["id"]))
                if len(results) != 1:
                    raise McpProtocolError("Home Assistant event stream is ambiguous")
                return results[0]
        except (httpx.HTTPError, ValueError, UnicodeError, RecursionError) as error:
            raise McpProtocolError("Home Assistant transport failed") from error

    @staticmethod
    def _response(envelope: Any, request_id: int) -> dict[str, Any]:
        if (
            not isinstance(envelope, dict)
            or envelope.get("jsonrpc") != "2.0"
            or type(envelope.get("id")) is not int
            or envelope["id"] != request_id
            or "method" in envelope
            or "error" in envelope
            or not isinstance(envelope.get("result"), dict)
        ):
            raise McpProtocolError("Home Assistant JSON-RPC envelope is invalid")
        return envelope["result"]

    async def close(self) -> None:
        if self._close_task is None:
            self._close_task = asyncio.create_task(self._close_http())
        await asyncio.shield(self._close_task)

    async def _close_http(self) -> None:
        self._closed = True
        self._initialized = False
        self._session_id = None
        if self._http is not None:
            await self._http.aclose()
            self._http = None
