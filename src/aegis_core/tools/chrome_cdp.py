from __future__ import annotations

import asyncio
import json
import os
import plistlib
import stat
from collections.abc import Mapping
from contextlib import suppress
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
from aegis_core.ipc.protocol import IpcRequest
from aegis_core.ipc.server import IpcHandlerResult, IpcMethodHandler
from aegis_core.tools.audit import AuditSink, NullAuditSink
from aegis_core.tools.broker import PolicyContext
from aegis_core.tools.defaults import BrowserMediaArguments

_CDP_HTTP_ORIGIN = "http://127.0.0.1:9222"
_YOUTUBE_ORIGIN = "https://www.youtube.com/"
_MAX_CDP_MESSAGE_BYTES = 1_048_576
_MAX_APPLICATION_PLIST_BYTES = 1_048_576
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
_JXA_SCRIPT = r"""
ObjC.import('Foundation');
const argv = ObjC.unwrap($.NSProcessInfo.processInfo.arguments);
const query = argv[argv.length - 1];
if (typeof query !== 'string' || query.length < 1 || query.length > 300) {
    throw new Error('invalid query');
}
const chrome = Application('Google Chrome');
chrome.activate();
if (chrome.windows.length === 0) chrome.Window().make();
const tab = chrome.windows[0].activeTab();
tab.url = 'https://www.youtube.com/';
delay(1.5);
const encoded = JSON.stringify(query);
tab.execute({javascript:
    "(() => {const i=document.querySelector('input[name=\\\"search_query\\\"]');" +
    "if(!i)return false;i.focus();i.value=" + encoded + ";" +
    "i.dispatchEvent(new Event('input',{bubbles:true}));" +
    "if(i.form&&i.form.requestSubmit){i.form.requestSubmit();return true;}" +
    "const b=document.querySelector('#search-icon-legacy');if(b){b.click();return true;}" +
    "return false;})()"
});
delay(1.5);
const clicked = tab.execute({javascript:
    "(() => {const a=document.querySelector('ytd-video-renderer a#video-title,#video-title');" +
    "if(!a)return false;a.click();return true;})()"
});
JSON.stringify({playing: clicked === true, provider: 'youtube'});
"""


class ChromeAutomationError(RuntimeError):
    """Chrome DOM automation failed without exposing browser content."""


@dataclass(frozen=True, slots=True)
class BrowserApplication:
    bundle_identifier: str
    name: str
    running: bool


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
        osascript_path: Path = Path("/usr/bin/osascript"),
    ) -> None:
        parsed = urlparse(endpoint)
        if parsed.scheme != "http" or parsed.port != 9222 or parsed.hostname is None:
            raise ValueError("Chrome CDP endpoint is invalid")
        try:
            loopback = ip_address(parsed.hostname).is_loopback
        except ValueError as error:
            raise ValueError("Chrome CDP must remain loopback-only") from error
        if not loopback:
            raise ValueError("Chrome CDP must remain loopback-only")
        self._endpoint = endpoint.rstrip("/")
        self._audit = audit_sink or NullAuditSink()
        self._osascript = osascript_path

    async def play_youtube(self, query: str) -> str:
        normalized = " ".join(query.split())
        if not 1 <= len(normalized) <= 300 or not normalized.isprintable():
            raise ChromeAutomationError("YouTube query is invalid")
        channel = "cdp"
        try:
            await self._play_youtube_cdp(normalized)
        except (TimeoutError, aiohttp.ClientError, ChromeAutomationError, OSError):
            channel = "jxa"
            await self._play_youtube_jxa(normalized)
        self._audit.record_system_event(
            uuid4(),
            event_type="chrome_media_started",
            component="chrome_cdp",
            data={"channel": channel, "provider": "youtube", "query_recorded": False},
        )
        return channel

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

    async def _play_youtube_cdp(self, query: str) -> None:
        timeout = aiohttp.ClientTimeout(total=12, connect=1, sock_connect=1, sock_read=8)
        connector = aiohttp.TCPConnector(limit=1, ttl_dns_cache=0)
        async with aiohttp.ClientSession(timeout=timeout, connector=connector) as client:
            async with client.get(
                f"{self._endpoint}/json/list",
                allow_redirects=False,
            ) as response:
                if response.status != 200:
                    raise ChromeAutomationError("Chrome debugging endpoint rejected discovery")
                if (
                    response.content_length is not None
                    and response.content_length > _MAX_CDP_MESSAGE_BYTES
                ):
                    raise ChromeAutomationError("Chrome target discovery was oversized")
                try:
                    discovery = await response.content.readexactly(_MAX_CDP_MESSAGE_BYTES + 1)
                except asyncio.IncompleteReadError as error:
                    discovery = error.partial
                if len(discovery) > _MAX_CDP_MESSAGE_BYTES:
                    raise ChromeAutomationError("Chrome target discovery was oversized")
                try:
                    targets = json.loads(discovery)
                except (UnicodeDecodeError, json.JSONDecodeError) as error:
                    raise ChromeAutomationError("Chrome target discovery is malformed") from error
            websocket_url = self._page_websocket_url(targets)
            async with client.ws_connect(
                websocket_url,
                max_msg_size=_MAX_CDP_MESSAGE_BYTES,
                autoping=True,
                autoclose=True,
            ) as websocket:
                cdp = _CDPSession(websocket)
                await cdp.command("Page.enable")
                await cdp.command("Runtime.enable")
                await cdp.command("Network.enable", {"maxTotalBufferSize": 0})
                await cdp.command("Page.navigate", {"url": _YOUTUBE_ORIGIN})
                await cdp.wait_event("Page.loadEventFired")
                query_literal = json.dumps(query, ensure_ascii=False)
                submitted = await cdp.evaluate(
                    "(() => {"
                    "const i=document.querySelector('input[name=\"search_query\"]');"
                    "if(!i)return false;i.focus();i.value=" + query_literal + ";"
                    "i.dispatchEvent(new Event('input',{bubbles:true}));"
                    "if(i.form?.requestSubmit){i.form.requestSubmit();return true;}"
                    "const b=document.querySelector('#search-icon-legacy');"
                    "if(b){b.click();return true;}return false;})()"
                )
                if submitted is not True:
                    raise ChromeAutomationError("YouTube search input was unavailable")
                with suppress(TimeoutError):
                    await cdp.wait_event("Page.loadEventFired", timeout=5)
                await asyncio.sleep(1.5)
                clicked = await cdp.evaluate(
                    "(() => {const a=document.querySelector("
                    "'ytd-video-renderer a#video-title,#video-title');"
                    "if(!a)return false;a.click();return true;})()"
                )
                if clicked is not True:
                    raise ChromeAutomationError("YouTube result was unavailable")

    async def _play_youtube_jxa(self, query: str) -> None:
        if self._osascript != Path("/usr/bin/osascript") or not self._osascript.is_file():
            raise ChromeAutomationError("JXA runtime is unavailable")
        process = await asyncio.create_subprocess_exec(
            str(self._osascript),
            "-l",
            "JavaScript",
            "-e",
            _JXA_SCRIPT,
            query,
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env={"PATH": "/usr/bin:/bin", "LANG": "C.UTF-8"},
        )
        try:
            stdout, _ = await asyncio.wait_for(process.communicate(), timeout=10)
        except TimeoutError:
            process.kill()
            await process.wait()
            raise ChromeAutomationError("JXA browser automation timed out") from None
        if process.returncode != 0 or len(stdout) > 4_096:
            raise ChromeAutomationError("JXA browser automation failed")
        try:
            result = json.loads(stdout)
        except json.JSONDecodeError as error:
            raise ChromeAutomationError("JXA browser result is malformed") from error
        if not isinstance(result, dict) or result.get("playing") is not True:
            raise ChromeAutomationError("JXA did not start media")

    @staticmethod
    def _page_websocket_url(targets: object) -> str:
        if not isinstance(targets, list):
            raise ChromeAutomationError("Chrome target discovery is malformed")
        for target in targets[:64]:
            if not isinstance(target, dict) or target.get("type") != "page":
                continue
            websocket_url = target.get("webSocketDebuggerUrl")
            if not isinstance(websocket_url, str):
                continue
            parsed = urlparse(websocket_url)
            if parsed.scheme != "ws" or parsed.hostname is None or parsed.port != 9222:
                continue
            try:
                if ip_address(parsed.hostname).is_loopback:
                    return websocket_url
            except ValueError:
                continue
        raise ChromeAutomationError("Chrome has no safe debuggable page")

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
