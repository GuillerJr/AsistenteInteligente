from __future__ import annotations

import asyncio
import json
import os
import plistlib
import re
import stat
from collections.abc import AsyncIterator, Mapping
from contextlib import asynccontextmanager, suppress
from dataclasses import dataclass
from ipaddress import ip_address
from pathlib import Path
from typing import Any
from urllib.parse import urlparse
from uuid import uuid4

import aiohttp
import psutil
from pydantic import ValidationError

from aegis_core.brain.router import browser_name, decide_browser_target
from aegis_core.contracts import PolicyDecision, ToolAuthorization, ToolExecutionResult
from aegis_core.failure_boundary import record_boundary_error
from aegis_core.ipc.protocol import IpcRequest
from aegis_core.ipc.server import IpcHandlerResult, IpcMethodHandler
from aegis_core.tools.audit import AuditSink, NullAuditSink
from aegis_core.tools.broker import PolicyContext
from aegis_core.tools.defaults import BrowserMediaArguments

_CDP_HTTP_ORIGIN = "http://127.0.0.1:9222"
_YOUTUBE_ORIGIN = "https://www.youtube.com/"
_MAX_CDP_MESSAGE_BYTES = 1_048_576
_MAX_APPLICATION_PLIST_BYTES = 1_048_576
_QUALIFICATION_INPUT = "P9 isolated DOM input"
_QUALIFICATION_HTML = """<!doctype html>
<html lang="en">
<head><meta charset="utf-8"><title>Jarvis P9 Driver Qualification</title></head>
<body>
  <label for="jarvis-input">P9 Input</label>
  <input id="jarvis-input" type="text" autocomplete="off">
  <button id="jarvis-advance" type="button">P9 Advance</button>
  <output id="jarvis-status" data-state="ready">P9 Ready</output>
  <script>
    document.getElementById("jarvis-advance").addEventListener("click", () => {
      const input = document.getElementById("jarvis-input");
      const output = document.getElementById("jarvis-status");
      if (input.value === "P9 isolated DOM input") {
        output.dataset.state = "complete";
        output.textContent = "P9 Complete";
      }
    });
  </script>
</body>
</html>"""
_SUPPORTED_BROWSER_BUNDLES = frozenset(
    {
        "com.apple.safari",
        "com.google.chrome",
        "com.parent.arc",
        "company.thebrowser.browser",
        "org.mozilla.firefox",
    }
)
_BROWSER_PROCESS_NAMES = {
    "com.apple.safari": frozenset({"safari"}),
    "com.google.chrome": frozenset({"google chrome", "chrome"}),
    "com.parent.arc": frozenset({"arc"}),
    "company.thebrowser.browser": frozenset({"arc"}),
    "org.mozilla.firefox": frozenset({"firefox"}),
}


class ChromeAutomationError(RuntimeError):
    """Chrome DOM automation failed without exposing browser content."""


@dataclass(frozen=True, slots=True)
class BrowserApplication:
    bundle_identifier: str
    name: str
    running: bool


@dataclass(frozen=True, slots=True)
class ChromeDOMQualification:
    browser_major_version: int
    protocol_version: str
    fixture_loaded: bool
    text_replaced: bool
    press_verified: bool


@dataclass(slots=True)
class OwnedChromePage:
    session: _CDPSession
    retain: bool = False


class LocalBrowserDiscovery:
    """Bounded, read-only browser discovery using signed app metadata and processes."""

    def __init__(self, applications_directory: Path | None = None) -> None:
        self._application_directories = (
            (applications_directory,)
            if applications_directory is not None
            else (Path("/Applications"), Path("/System/Applications"))
        )

    async def discover(self) -> tuple[BrowserApplication, ...]:
        return await asyncio.to_thread(self._discover_blocking)

    def _discover_blocking(self) -> tuple[BrowserApplication, ...]:
        installed = self._installed_bundle_identifiers()
        running = self._running_bundle_identifiers(installed)
        return tuple(
            BrowserApplication(
                bundle_identifier=bundle_identifier,
                name=browser_name(bundle_identifier) or "Browser",
                running=bundle_identifier in running,
            )
            for bundle_identifier in sorted(
                installed,
                key=lambda identifier: (
                    browser_name(identifier) or identifier,
                    identifier,
                ),
            )
        )

    def _installed_bundle_identifiers(self) -> set[str]:
        discovered: set[str] = set()
        for directory in self._application_directories:
            discovered.update(self._browser_bundles_in(directory))
        return discovered

    @staticmethod
    def _browser_bundles_in(applications_directory: Path) -> set[str]:
        try:
            root_status = applications_directory.stat(follow_symlinks=False)
            entries = tuple(applications_directory.iterdir())
        except OSError:
            return set()
        if not stat.S_ISDIR(root_status.st_mode):
            return set()
        discovered: set[str] = set()
        for application in entries[:512]:
            try:
                if application.is_symlink() or application.suffix.casefold() != ".app":
                    continue
                info = application / "Contents/Info.plist"
                status = info.stat(follow_symlinks=False)
                if (
                    not stat.S_ISREG(status.st_mode)
                    or status.st_size <= 0
                    or status.st_size > _MAX_APPLICATION_PLIST_BYTES
                ):
                    continue
                payload = plistlib.loads(info.read_bytes())
            except (OSError, plistlib.InvalidFileException, ValueError):
                continue
            identifier = payload.get("CFBundleIdentifier") if isinstance(payload, dict) else None
            if isinstance(identifier, str) and identifier.casefold() in _SUPPORTED_BROWSER_BUNDLES:
                discovered.add(identifier)
        return discovered

    @staticmethod
    def _running_bundle_identifiers(installed: set[str]) -> set[str]:
        running_names: set[str] = set()
        for process in psutil.process_iter(("name", "uids")):
            try:
                uids = process.info.get("uids")
                if uids is not None and uids.real != os.getuid():
                    continue
                name = process.info.get("name")
                if isinstance(name, str):
                    running_names.add(name.casefold())
            except (psutil.AccessDenied, psutil.NoSuchProcess, psutil.ZombieProcess):
                continue
        return {
            identifier
            for identifier in installed
            if _BROWSER_PROCESS_NAMES.get(identifier.casefold(), frozenset()) & running_names
        }


class BrowserDiscoveryIpcService:
    METHOD = "browser.discover"

    def __init__(self, discovery: LocalBrowserDiscovery | None = None) -> None:
        self._discovery = discovery or LocalBrowserDiscovery()

    def handlers(self) -> dict[str, IpcMethodHandler]:
        return {self.METHOD: self.handle}

    async def handle(self, request: IpcRequest) -> IpcHandlerResult:
        intent_text = request.payload.get("intent_text")
        if (
            request.method != self.METHOD
            or set(request.payload) != {"intent_text"}
            or not isinstance(intent_text, str)
            or not 1 <= len(intent_text) <= 4_096
            or not intent_text.isprintable()
        ):
            return IpcHandlerResult(ok=False, error_code="invalid_payload")
        browsers = await self._discovery.discover()
        installed = tuple(browser.bundle_identifier for browser in browsers)
        running = tuple(browser.bundle_identifier for browser in browsers if browser.running)
        decision = decide_browser_target(
            intent_text,
            installed_bundle_identifiers=installed,
            running_bundle_identifiers=running,
        )
        return IpcHandlerResult(
            ok=True,
            payload={
                "browsers": [
                    {
                        "bundle_identifier": browser.bundle_identifier,
                        "name": browser.name,
                        "running": browser.running,
                    }
                    for browser in browsers
                ],
                "requires_selection": decision.requires_selection,
                "selected_bundle_identifier": decision.selected_bundle_identifier,
            },
        )


class _CDPSession:
    def __init__(self, websocket: aiohttp.ClientWebSocketResponse) -> None:
        self._websocket = websocket
        self._request_id = 0
        self._events: list[dict[str, Any]] = []

    async def command(
        self,
        method: str,
        params: Mapping[str, object] | None = None,
        *,
        timeout: float = 5.0,
    ) -> dict[str, Any]:
        self._request_id += 1
        request_id = self._request_id
        await self._websocket.send_json(
            {"id": request_id, "method": method, "params": dict(params or {})}
        )
        async with asyncio.timeout(timeout):
            while True:
                payload = await self._receive_object()
                if payload.get("id") == request_id:
                    if "error" in payload:
                        raise ChromeAutomationError("Chrome rejected a CDP command")
                    result = payload.get("result")
                    if not isinstance(result, dict):
                        raise ChromeAutomationError("Chrome returned a malformed CDP response")
                    return result
                if isinstance(payload.get("method"), str):
                    self._events.append(payload)

    async def wait_event(self, method: str, *, timeout: float = 8.0) -> None:
        for index, event in enumerate(self._events):
            if event.get("method") == method:
                del self._events[index]
                return
        async with asyncio.timeout(timeout):
            while True:
                payload = await self._receive_object()
                if payload.get("method") == method:
                    return
                if isinstance(payload.get("method"), str):
                    self._events.append(payload)

    async def evaluate(self, expression: str, *, timeout: float = 5.0) -> object:
        result = await self.command(
            "Runtime.evaluate",
            {
                "expression": expression,
                "awaitPromise": True,
                "returnByValue": True,
                "userGesture": True,
            },
            timeout=timeout,
        )
        if "exceptionDetails" in result:
            raise ChromeAutomationError("Chrome DOM expression failed")
        remote = result.get("result")
        if not isinstance(remote, dict):
            raise ChromeAutomationError("Chrome DOM result is malformed")
        return remote.get("value")

    async def _receive_object(self) -> dict[str, Any]:
        message = await self._websocket.receive()
        if message.type != aiohttp.WSMsgType.TEXT:
            raise ChromeAutomationError("Chrome closed the debugging channel")
        if len(message.data.encode("utf-8")) > _MAX_CDP_MESSAGE_BYTES:
            raise ChromeAutomationError("Chrome CDP message exceeded the safety ceiling")
        try:
            payload = json.loads(message.data)
        except json.JSONDecodeError as error:
            raise ChromeAutomationError("Chrome CDP message is not JSON") from error
        if not isinstance(payload, dict):
            raise ChromeAutomationError("Chrome CDP message is malformed")
        return payload


class ChromeCDPController:
    def __init__(
        self,
        *,
        endpoint: str = _CDP_HTTP_ORIGIN,
        audit_sink: AuditSink | None = None,
    ) -> None:
        parsed = urlparse(endpoint)
        if (
            parsed.scheme != "http"
            or parsed.port != 9222
            or parsed.hostname is None
            or parsed.username is not None
            or parsed.password is not None
            or parsed.path not in {"", "/"}
            or parsed.query
            or parsed.fragment
        ):
            raise ValueError("Chrome CDP endpoint is invalid")
        try:
            loopback = ip_address(parsed.hostname).is_loopback
        except ValueError as error:
            raise ValueError("Chrome CDP must remain loopback-only") from error
        if not loopback:
            raise ValueError("Chrome CDP must remain loopback-only")
        self._endpoint = endpoint.rstrip("/")
        self._audit = audit_sink or NullAuditSink()

    async def play_youtube(self, query: str) -> str:
        normalized = " ".join(query.split())
        if not 1 <= len(normalized) <= 300 or not normalized.isprintable():
            raise ChromeAutomationError("YouTube query is invalid")
        try:
            await self._play_youtube_cdp(normalized)
        except (TimeoutError, aiohttp.ClientError, ChromeAutomationError, OSError) as error:
            raise ChromeAutomationError(
                "Chrome CDP is unavailable; launch Chrome with local remote debugging enabled"
            ) from error
        self._audit.record_system_event(
            uuid4(),
            event_type="chrome_media_started",
            component="chrome_cdp",
            data={"channel": "cdp", "provider": "youtube", "query_recorded": False},
        )
        return "cdp"

    async def qualify_dom_driver(self) -> ChromeDOMQualification:
        """Exercise the real Chrome CDP engine using a network-free in-memory document."""

        try:
            result = await self._qualify_dom_driver()
        except (TimeoutError, aiohttp.ClientError, ChromeAutomationError, OSError) as error:
            self._audit.record_system_event(
                uuid4(),
                event_type="chrome_driver_qualification_failed",
                component="chrome_cdp",
                data={"reason": "cdp_contract_failed"},
            )
            raise ChromeAutomationError("Chrome CDP qualification failed closed") from error
        self._audit.record_system_event(
            uuid4(),
            event_type="chrome_driver_qualified",
            component="chrome_cdp",
            data={
                "browser_major_version": result.browser_major_version,
                "protocol_version": result.protocol_version,
                "fixture": "memory_only",
                "external_content_requested": False,
            },
        )
        return result

    async def execute_tool(
        self,
        authorization: ToolAuthorization,
        context: PolicyContext,
    ) -> ToolExecutionResult:
        del context
        if authorization.decision is not PolicyDecision.ALLOW:
            return self._error(authorization, "authorization_not_allowed")
        if authorization.reason_code not in {"explicit_local_intent", "confirmation_consumed"}:
            return self._error(authorization, "access_denied")
        try:
            arguments = BrowserMediaArguments.model_validate(authorization.normalized_arguments)
            channel = await self.play_youtube(arguments.query)
        except (ValidationError, ChromeAutomationError):
            return self._error(authorization, "browser_automation_failed")
        return ToolExecutionResult(
            call_id=authorization.call_id,
            tool_name=authorization.tool_name,
            success=True,
            output=json.dumps(
                {
                    "browser": "chrome",
                    "channel": channel,
                    "playing": True,
                    "provider": "youtube",
                    "query": arguments.query,
                },
                ensure_ascii=False,
                separators=(",", ":"),
            ),
            metadata={"verified": True, "source": "chrome_dom"},
        )

    async def _read_endpoint_json(self, client: aiohttp.ClientSession, route: str) -> Any:
        async with client.get(f"{self._endpoint}{route}", allow_redirects=False) as response:
            if response.status != 200:
                raise ChromeAutomationError("Chrome endpoint rejected discovery")
            if (
                response.content_length is not None
                and response.content_length > _MAX_CDP_MESSAGE_BYTES
            ):
                raise ChromeAutomationError("Chrome discovery was oversized")
            try:
                encoded = await response.content.readexactly(_MAX_CDP_MESSAGE_BYTES + 1)
            except asyncio.IncompleteReadError as error:
                encoded = error.partial
            if len(encoded) > _MAX_CDP_MESSAGE_BYTES:
                raise ChromeAutomationError("Chrome discovery was oversized")
            try:
                return json.loads(encoded)
            except (UnicodeDecodeError, json.JSONDecodeError) as error:
                raise ChromeAutomationError("Chrome discovery is malformed") from error

    def _validated_websocket(self, value: object, *, path: str) -> str:
        if not isinstance(value, str):
            raise ChromeAutomationError("Chrome debugging channel is missing")
        parsed = urlparse(value)
        endpoint = urlparse(self._endpoint)
        if (
            parsed.scheme != "ws"
            or parsed.hostname != endpoint.hostname
            or parsed.port != endpoint.port
            or parsed.path != path
            or parsed.username is not None
            or parsed.password is not None
            or parsed.query
            or parsed.fragment
        ):
            raise ChromeAutomationError("Chrome debugging channel does not match its target")
        return value

    @staticmethod
    def _target_identifier(value: object) -> str:
        if not isinstance(value, str) or re.fullmatch(r"[A-Za-z0-9_-]{1,128}", value) is None:
            raise ChromeAutomationError("Chrome target identifier is invalid")
        return value

    @asynccontextmanager
    async def _owned_page(
        self,
        client: aiohttp.ClientSession,
        *,
        isolated: bool,
    ) -> AsyncIterator[OwnedChromePage]:
        # Never select an existing user's tab, including when qualification is called
        # directly rather than through the isolated shell gate.
        version = await self._read_endpoint_json(client, "/json/version")
        if not isinstance(version, dict):
            raise ChromeAutomationError("Chrome version response is incomplete")
        # HTTP discovery and the Browser.getVersion command use different key names.
        self._validated_version(
            {
                "product": version.get("Browser"),
                "protocolVersion": version.get("Protocol-Version"),
            }
        )
        browser_url = version.get("webSocketDebuggerUrl")
        if not isinstance(browser_url, str):
            raise ChromeAutomationError("Chrome browser channel is unavailable")
        browser_id = self._target_identifier(urlparse(browser_url).path.rsplit("/", 1)[-1])
        browser_url = self._validated_websocket(
            browser_url,
            path=f"/devtools/browser/{browser_id}",
        )
        async with client.ws_connect(browser_url, max_msg_size=_MAX_CDP_MESSAGE_BYTES) as websocket:
            browser = _CDPSession(websocket)
            context_id = None
            target_id = None
            owned = None
            try:
                if isolated:
                    context_result = await browser.command(
                        "Target.createBrowserContext",
                        {"disposeOnDetach": True},
                    )
                    context_id = self._target_identifier(context_result.get("browserContextId"))
                options: dict[str, Any] = {"url": "about:blank", "background": True}
                if context_id is not None:
                    options["browserContextId"] = context_id

                async def create_target() -> None:
                    nonlocal target_id
                    result = await browser.command("Target.createTarget", options)
                    target_id = self._target_identifier(result.get("targetId"))

                # Creation can take effect before its acknowledgement. Join an already
                # started command on cancellation so finally can close the exact new ID.
                creation = asyncio.create_task(create_target(), name="owned-chrome-target-create")
                try:
                    await asyncio.shield(creation)
                except asyncio.CancelledError:
                    while not creation.done():
                        try:
                            await asyncio.shield(creation)
                        except asyncio.CancelledError:
                            continue
                        except Exception as error:
                            record_boundary_error(error)
                            break
                    if not creation.cancelled():
                        creation.exception()
                    raise
                targets = await self._read_endpoint_json(client, "/json/list")
                page_url = self._page_websocket_url(targets, target_id=target_id)
                page_url = self._validated_websocket(page_url, path=f"/devtools/page/{target_id}")
                async with client.ws_connect(page_url, max_msg_size=_MAX_CDP_MESSAGE_BYTES) as page:
                    owned = OwnedChromePage(_CDPSession(page))
                    await owned.session.command("Page.enable")
                    await owned.session.command("Runtime.enable")
                    yield owned
            finally:
                if target_id is not None and (isolated or owned is None or not owned.retain):
                    with suppress(ChromeAutomationError, aiohttp.ClientError, TimeoutError):
                        async with asyncio.timeout(2):
                            await browser.command("Target.closeTarget", {"targetId": target_id})
                if context_id is not None:
                    with suppress(ChromeAutomationError, aiohttp.ClientError, TimeoutError):
                        async with asyncio.timeout(2):
                            await browser.command(
                                "Target.disposeBrowserContext",
                                {"browserContextId": context_id},
                            )

    async def _play_youtube_cdp(self, query: str) -> None:
        timeout = aiohttp.ClientTimeout(total=25, connect=1, sock_connect=1, sock_read=8)
        connector = aiohttp.TCPConnector(limit=2, ttl_dns_cache=0)
        async with (
            asyncio.timeout(25),
            aiohttp.ClientSession(timeout=timeout, connector=connector) as client,
        ):
            async with self._owned_page(client, isolated=False) as owned:
                cdp = owned.session
                from urllib.parse import urlencode

                url = _YOUTUBE_ORIGIN + "results?" + urlencode({"search_query": query})
                await cdp.command("Page.navigate", {"url": url})
                await cdp.wait_event("Page.loadEventFired")
                destination = None
                for _ in range(16):
                    candidate = await cdp.evaluate(
                        "(() => {const a=document.querySelector("
                        "'ytd-video-renderer a#video-title');"
                        "return a?.href||null;})()"
                    )
                    if isinstance(candidate, str):
                        parsed = urlparse(candidate)
                        if (
                            parsed.scheme == "https"
                            and parsed.hostname == "www.youtube.com"
                            and parsed.path == "/watch"
                            and parsed.username is None
                            and parsed.password is None
                            and parsed.port in {None, 443}
                        ):
                            destination = candidate
                            break
                    await asyncio.sleep(0.25)
                if destination is None:
                    raise ChromeAutomationError("YouTube result was unavailable")
                await cdp.command("Page.navigate", {"url": destination})
                await cdp.wait_event("Page.loadEventFired")
                previous_time = None
                play_requested = False
                for _ in range(24):
                    state = await cdp.evaluate(
                        "(() => {const v=document.querySelector('video');"
                        "return v?{url:location.href,paused:v.paused,ended:v.ended,"
                        "readyState:v.readyState,currentTime:v.currentTime,"
                        "adPlaying:!!document.querySelector('.ad-showing,.ad-interrupting')}"
                        ":null;})()"
                    )
                    if self._playback_verified(state, previous_time, destination):
                        owned.retain = True
                        return
                    if (
                        not play_requested
                        and isinstance(state, dict)
                        and state.get("url") == destination
                        and state.get("paused") is True
                    ):
                        play_requested = True
                        # One explicit play request on our owned tab. Its acknowledgement
                        # still cannot certify playback; only later advancing samples can.
                        await cdp.command(
                            "Runtime.evaluate",
                            {
                                "expression": (
                                    "document.querySelector('video')?.play().catch(()=>{})"
                                ),
                                "returnByValue": True,
                                "userGesture": True,
                                "awaitPromise": False,
                            },
                        )
                    if isinstance(state, dict) and type(state.get("currentTime")) in {int, float}:
                        previous_time = state["currentTime"]
                    await asyncio.sleep(0.25)
                raise ChromeAutomationError("YouTube playback was not verified")

    @staticmethod
    def _playback_verified(state: object, previous_time: object, destination: str) -> bool:
        import math

        if not isinstance(state, dict) or type(previous_time) not in {int, float}:
            return False
        time_value = state.get("currentTime")
        return bool(
            state.get("url") == destination
            and state.get("paused") is False
            and state.get("ended") is False
            and state.get("adPlaying") is False
            and type(state.get("readyState")) is int
            and state["readyState"] >= 2
            and type(time_value) in {int, float}
            and math.isfinite(time_value)
            and math.isfinite(previous_time)
            and time_value > previous_time
            and time_value >= 0
        )

    async def _qualify_dom_driver(self) -> ChromeDOMQualification:
        timeout = aiohttp.ClientTimeout(total=15, connect=1, sock_connect=1, sock_read=6)
        connector = aiohttp.TCPConnector(limit=2, ttl_dns_cache=0)
        async with (
            asyncio.timeout(15),
            aiohttp.ClientSession(timeout=timeout, connector=connector) as client,
        ):
            async with self._owned_page(client, isolated=True) as owned:
                cdp = owned.session
                await cdp.command("Network.enable", {"maxTotalBufferSize": 0})
                await cdp.command(
                    "Network.setBlockedURLs", {"urls": ["http://*", "https://*", "ftp://*"]}
                )
                version = await cdp.command("Browser.getVersion")
                browser_major_version, protocol_version = self._validated_version(version)
                frame_id = self._main_frame_id(await cdp.command("Page.getFrameTree"))
                await cdp.command(
                    "Page.setDocumentContent", {"frameId": frame_id, "html": _QUALIFICATION_HTML}
                )
                initial = await cdp.evaluate(
                    "(() => {const i=document.getElementById('jarvis-input');"
                    "const b=document.getElementById('jarvis-advance');"
                    "const o=document.getElementById('jarvis-status');"
                    "return Boolean(i&&b&&o&&o.dataset.state==='ready'&&i.value==='');})()"
                )
                replaced = await cdp.evaluate(
                    "(() => {const i=document.getElementById('jarvis-input');"
                    "if(!i)return false;i.focus();i.value="
                    + json.dumps(_QUALIFICATION_INPUT)
                    + ";i.dispatchEvent(new Event('input',{bubbles:true}));"
                    "return i.value==="
                    + json.dumps(_QUALIFICATION_INPUT)
                    + "&&document.activeElement===i;})()"
                )
                pressed = await cdp.evaluate(
                    "(() => {const b=document.getElementById('jarvis-advance');"
                    "const o=document.getElementById('jarvis-status');"
                    "if(!b||!o)return false;b.click();"
                    "return o.dataset.state==='complete'&&o.textContent==='P9 Complete';})()"
                )
        return ChromeDOMQualification(
            browser_major_version=browser_major_version,
            protocol_version=protocol_version,
            fixture_loaded=initial is True,
            text_replaced=replaced is True,
            press_verified=pressed is True,
        )

    @staticmethod
    def _validated_version(version: object) -> tuple[int, str]:
        if not isinstance(version, dict):
            raise ChromeAutomationError("Chrome version response is malformed")
        product = version.get("product")
        protocol_version = version.get("protocolVersion")
        if not isinstance(product, str) or not isinstance(protocol_version, str):
            raise ChromeAutomationError("Chrome version response is incomplete")
        family, separator, release = product.partition("/")
        if family not in {"Chrome", "HeadlessChrome"} or separator != "/":
            raise ChromeAutomationError("The debugging target is not Google Chrome")
        major, dot, _ = release.partition(".")
        if not major.isdigit() or dot != "." or not 1 <= int(major) <= 10_000:
            raise ChromeAutomationError("Chrome version is invalid")
        protocol_parts = protocol_version.split(".")
        if len(protocol_parts) != 2 or not all(part.isdigit() for part in protocol_parts):
            raise ChromeAutomationError("Chrome protocol version is invalid")
        return int(major), protocol_version[:16]

    @staticmethod
    def _main_frame_id(frame_tree: object) -> str:
        if not isinstance(frame_tree, dict):
            raise ChromeAutomationError("Chrome frame tree is malformed")
        root = frame_tree.get("frameTree")
        frame = root.get("frame") if isinstance(root, dict) else None
        frame_id = frame.get("id") if isinstance(frame, dict) else None
        if not isinstance(frame_id, str) or not 1 <= len(frame_id) <= 256:
            raise ChromeAutomationError("Chrome main frame is unavailable")
        return frame_id

    @staticmethod
    def _page_websocket_url(targets: object, *, target_id: str) -> str:
        ChromeCDPController._target_identifier(target_id)
        if not isinstance(targets, list):
            raise ChromeAutomationError("Chrome target discovery is malformed")
        matches = [
            target
            for target in targets[:64]
            if isinstance(target, dict)
            and target.get("type") == "page"
            and target.get("id") == target_id
        ]
        if len(matches) != 1:
            raise ChromeAutomationError("Owned Chrome page is unavailable or ambiguous")
        websocket_url = matches[0].get("webSocketDebuggerUrl")
        if not isinstance(websocket_url, str):
            raise ChromeAutomationError("Owned Chrome page is not depurable")
        parsed = urlparse(websocket_url)
        if (
            parsed.scheme != "ws"
            or parsed.hostname is None
            or parsed.port != 9222
            or parsed.path != f"/devtools/page/{target_id}"
            or parsed.username is not None
            or parsed.password is not None
            or parsed.query
            or parsed.fragment
        ):
            raise ChromeAutomationError("Owned Chrome page channel is invalid")
        try:
            if not ip_address(parsed.hostname).is_loopback:
                raise ValueError
        except ValueError as error:
            raise ChromeAutomationError("Chrome page must remain loopback-only") from error
        return websocket_url

    @staticmethod
    def _error(
        authorization: ToolAuthorization,
        code: str,
    ) -> ToolExecutionResult:
        return ToolExecutionResult(
            call_id=authorization.call_id,
            tool_name=authorization.tool_name,
            success=False,
            error_code=code,
            metadata={"verified": False},
        )
