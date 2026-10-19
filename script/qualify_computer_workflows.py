#!/usr/bin/env python3
"""Opt-in real browser workflows against a private, synthetic HTTPS fixture.

Uses the installed, pinned Playwright MCP peer, a new headless Chrome process,
an ephemeral profile and a loopback-only server. No owner browser, model API,
credentials, Telegram, daemon configuration or package installation is used.
The CDP oracle reads only the page created here; fixture disturbances test stale
observations and are not production browser-control actions.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import shutil
import signal
import ssl
import sys
from contextlib import asynccontextmanager, suppress
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

import aiohttp
from aiohttp import web
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from aegis_core.contracts import AgentRole, PolicyDecision, ToolCall, UserRequest
from aegis_core.mcp.client import (
    McpHost,
    McpHostConfiguration,
    StdioMcpClient,
    reviewed_schema_sha256,
)
from aegis_core.mcp.integrations import (
    BrowserSessionConfiguration,
    IntegrationScope,
    integration_tools,
)
from aegis_core.mcp.local_peers import local_peer_configuration
from aegis_core.tools.confirmations import OneTimeConfirmationStore
from aegis_core.tools.defaults import build_default_tool_broker, default_policy_context
from aegis_core.tools.execution import ReadOnlyToolExecutor

EXACT_VALUE = "JARVIS workflow 7319"
FIXTURE = """<!doctype html><html lang="en"><meta charset="utf-8">
<title>Jarvis synthetic workflow</title><h1>Workflow fixture</h1>
<label for="value">Workflow value</label>
<input id="value" autocomplete="off" value="UNCHANGED">
<label for="choice">Workflow choice</label><select id="choice">
<option value="initial">Initial</option><option value="verified">Verified</option></select>
<p id="selection">Workflow option initial</p>
<button id="complete">Complete workflow</button><section aria-label="Workflow result">
<h2 id="result">Workflow ready</h2></section>
<script>
window.fixtureClicks=0;
choice.addEventListener('change',()=>selection.textContent='Workflow option '+choice.value);
complete.addEventListener('click',()=>{
  window.fixtureClicks++;
  result.textContent=value.value==='JARVIS workflow 7319'&&choice.value==='verified'
    ?'Workflow complete 7319':'Workflow incorrect';
});
</script></html>"""
ORACLE = """JSON.stringify({url:location.href,value:document.querySelector('#value').value,
choice:document.querySelector('#choice').value,result:document.querySelector('#result').textContent,
clicks:window.fixtureClicks})"""


def target_ref(snapshot: dict[str, Any], role: str, name: str) -> str:
    """Use the unique accessible name in the latest bound snapshot, not selectors."""
    matches = re.findall(
        r"\b" + re.escape(role) + " " + re.escape(json.dumps(name))
        + r"[^\n]*?\[ref=((?:f[1-9][0-9]{0,7})?e[1-9][0-9]{0,7})\]", snapshot["snapshot"],
    )
    if len(matches) != 1:
        raise ValueError("fixture_target_not_unique")
    return matches[0]


def binding(snapshot: dict[str, Any]) -> dict[str, str]:
    return {key: snapshot[key] for key in ("session_id", "expected_url", "snapshot_sha256")}


def pinned_packages(path: Path) -> Path:
    root = path.resolve(strict=True)
    manifest = root / "node_modules/@playwright/mcp/package.json"
    if json.loads(manifest.read_text(encoding="utf-8")).get("version") != "0.0.80":
        raise ValueError("playwright_peer_version_not_0_0_80")
    return root


def default_packages() -> Path:
    candidates = (
        Path(__file__).resolve().parents[1] / "integrations/mcp",
        Path.home() / "Library/Application Support/Jarvis/integrations/mcp",
    )
    return next((path for path in candidates
                 if (path / "node_modules/@playwright/mcp/package.json").is_file()), candidates[0])


def fixture_tls(directory: Path) -> ssl.SSLContext:
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "fixture.test")])
    now = datetime.now(UTC)
    certificate = (
        x509.CertificateBuilder().subject_name(subject).issuer_name(subject)
        .public_key(key.public_key()).serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(minutes=1)).not_valid_after(now + timedelta(hours=1))
        .add_extension(x509.SubjectAlternativeName([x509.DNSName("fixture.test")]), critical=False)
        .sign(key, hashes.SHA256())
    )
    key_path, cert_path = directory / "fixture.key", directory / "fixture.crt"
    for path, payload in (
        (key_path, key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                     serialization.NoEncryption())),
        (cert_path, certificate.public_bytes(serialization.Encoding.PEM)),
    ):
        with path.open("xb") as stream:
            os.chmod(path, 0o600)
            stream.write(payload)
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    context.load_cert_chain(cert_path, key_path)
    return context


@asynccontextmanager
async def fixture_server(directory: Path):
    async def serve(request: web.Request) -> web.Response:
        if request.path not in {"/form", "/other"}:
            raise web.HTTPNotFound()
        return web.Response(text=FIXTURE, content_type="text/html", headers={
            "Cache-Control": "no-store", "Referrer-Policy": "no-referrer",
            "Content-Security-Policy": "default-src 'none'; script-src 'unsafe-inline'",
        })

    app = web.Application()
    app.router.add_get("/{tail:.*}", serve)
    runner = web.AppRunner(app, access_log=None)
    await runner.setup()
    try:
        # Bind first so the port is owned rather than probed then raced.
        site = web.TCPSite(runner, "127.0.0.1", 0, ssl_context=fixture_tls(directory))
        await site.start()
        port = runner.addresses[0][1]
        yield "https://fixture.test", port
    finally:
        await runner.cleanup()


@asynccontextmanager
async def private_chrome(chrome: Path, directory: Path, url: str, fixture_port: int):
    profile = directory / "ChromeProfile"
    profile.mkdir(mode=0o700)
    arguments = (
        str(chrome), "--headless=new", "--remote-debugging-address=127.0.0.1",
        "--remote-debugging-port=0", f"--user-data-dir={profile}",
        f"--host-resolver-rules=MAP fixture.test 127.0.0.1:{fixture_port}",
        "--proxy-server=127.0.0.1:9", "--proxy-bypass-list=fixture.test",
        "--ignore-certificate-errors", "--disable-background-networking", "--disable-breakpad",
        "--disable-client-side-phishing-detection", "--disable-component-update",
        "--disable-default-apps", "--disable-domain-reliability", "--disable-sync",
        "--disable-features=AutofillServerCommunication,MediaRouter,OptimizationHints",
        "--no-default-browser-check", "--no-first-run", "--password-store=basic",
        "--use-mock-keychain", "--metrics-recording-only", url,
    )
    process = await asyncio.create_subprocess_exec(
        *arguments, cwd=directory, stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.DEVNULL, start_new_session=True,
    )
    try:
        endpoint = None
        async with asyncio.timeout(15):
            while True:
                if process.returncode is not None:
                    raise RuntimeError("owned_chrome_launch_failed")
                active = profile / "DevToolsActivePort"
                if active.is_file():
                    fields = active.read_text(encoding="ascii").splitlines()
                    if len(fields) == 2 and fields[0].isdigit():
                        port = int(fields[0])
                        if 0 < port < 65536 and fields[1].startswith("/devtools/browser/"):
                            endpoint = f"http://127.0.0.1:{port}"
                            break
                await asyncio.sleep(0.05)
        yield endpoint
    finally:
        if process.returncode is None:
            with suppress(ProcessLookupError):
                os.killpg(process.pid, signal.SIGTERM)
            try:
                await asyncio.wait_for(process.wait(), 3)
            except TimeoutError:
                with suppress(ProcessLookupError):
                    os.killpg(process.pid, signal.SIGKILL)
                await process.wait()


class FixtureOracle:
    """Independent CDP oracle pinned to a page on this owned Chrome endpoint."""

    def __init__(self, client: aiohttp.ClientSession, endpoint: str) -> None:
        self.client, self.endpoint = client, endpoint
        self.page_id: str | None = None
        self.counter = 0

    async def target(self, url: str) -> None:
        async with asyncio.timeout(10):
            while True:
                async with self.client.get(self.endpoint + "/json/list") as response:
                    response.raise_for_status()
                    targets = await response.json()
                matches = [page for page in targets if page.get("type") == "page"
                           and page.get("url") == url]
                if len(matches) == 1:
                    self.page_id = matches[0]["id"]
                    ready = await self.evaluate(
                        "document.readyState==='complete'"
                        "&&!!document.querySelector('#value')&&!!document.querySelector('#result')",
                    )
                    if ready and (await self.state())["url"] == url:
                        return
                await asyncio.sleep(0.05)

    async def command(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        if self.page_id is None:
            raise RuntimeError("fixture_page_not_bound")
        async with self.client.ws_connect(
            self.endpoint.replace("http:", "ws:", 1) + "/devtools/page/" + self.page_id,
            max_msg_size=1_048_576,
        ) as websocket, asyncio.timeout(5):
            self.counter += 1
            await websocket.send_json({"id": self.counter, "method": method, "params": params})
            async for message in websocket:
                payload = json.loads(message.data)
                if payload.get("id") == self.counter:
                    if "error" in payload:
                        raise RuntimeError("fixture_cdp_command_failed")
                    return payload["result"]
        raise RuntimeError("fixture_cdp_response_missing")

    async def evaluate(self, expression: str) -> Any:
        result = await self.command("Runtime.evaluate", {
            "expression": expression, "returnByValue": True, "awaitPromise": True,
        })
        if "exceptionDetails" in result:
            raise RuntimeError("fixture_oracle_evaluation_failed")
        return result["result"].get("value")

    async def state(self) -> dict[str, Any]:
        return json.loads(await self.evaluate(ORACLE))

    async def navigate(self, url: str) -> None:
        await self.command("Page.navigate", {"url": url})
        await self.target(url)


class ApprovedWorkflow:
    def __init__(self, host: McpHost, directory: Path) -> None:
        self.host = host
        self.broker = build_default_tool_broker(host.tool_definitions())
        self.executor = ReadOnlyToolExecutor(extra_async_handlers=host.handlers())
        self.store = OneTimeConfirmationStore()
        self.context = replace(default_policy_context(directory), confirmation_store=self.store)
        self.request = UserRequest(text="Qualify the private synthetic browser workflow")
        self.invocations = 0

    async def invoke(self, name: str, arguments: dict[str, Any]):
        self.invocations += 1
        call = ToolCall(call_id=f"workflow-{self.invocations}",
                        tool_name="mcp_playwright__" + name, arguments=arguments,
                        requested_by=AgentRole.PLANNER)
        pending = self.broker.authorize(call, self.context, request=self.request)
        assert pending.decision is PolicyDecision.REQUIRE_CONFIRMATION, pending.reason_code
        self.store.issue(call, pending, approved_by="synthetic-harness-owner",
                         request_id=self.request.request_id)
        authorized = self.broker.authorize(call, self.context, request=self.request)
        assert authorized.decision is PolicyDecision.ALLOW, authorized.reason_code
        result = await self.executor.execute_async(authorized, self.context)
        assert self.broker.authorize(call, self.context, request=self.request).reason_code == (
            "confirmation_replayed"
        )
        return result

    async def observe(self) -> dict[str, Any]:
        result = await self.invoke("browser_snapshot", {})
        assert result.success, result.error_code
        data = json.loads(result.output)["structuredContent"]
        assert data["verified"] is True and data["outcome"] == "observed"
        return data


async def workflows(host: McpHost, oracle: FixtureOracle, directory: Path,
                    origin: str) -> dict[str, Any]:
    flow = ApprovedWorkflow(host, directory)
    snapshot = await flow.observe()
    result = await flow.invoke("browser_type", binding(snapshot) | {
        "target": target_ref(snapshot, "textbox", "Workflow value"),
        "text": EXACT_VALUE, "submit": False,
    })
    assert result.success and result.metadata["goal_verified"] is True, result.error_code
    assert (await oracle.state())["value"] == EXACT_VALUE
    snapshot = await flow.observe()
    result = await flow.invoke("browser_select_option", binding(snapshot) | {
        "target": target_ref(snapshot, "combobox", "Workflow choice"), "values": ["verified"],
        "verify_text": "Verified",
    })
    assert result.success and result.metadata["goal_verified"] is True, result.error_code
    assert (await oracle.state())["choice"] == "verified"
    snapshot = await flow.observe()
    click = binding(snapshot) | {"target": target_ref(snapshot, "button", "Complete workflow"),
                                 "verify_text": "Workflow complete 7319"}
    result = await flow.invoke("browser_click", click)
    assert result.success and result.metadata["goal_verified"] is True, result.error_code
    state = await oracle.state()
    assert state == {"url": origin + "/form", "value": EXACT_VALUE, "choice": "verified",
                     "result": "Workflow complete 7319", "clicks": 1}
    checks = ["exact_fill", "selected_value", "click_visible_result", "bound_url", "grant_replay"]

    snapshot = await flow.observe()
    stale = binding(snapshot) | {"target": target_ref(snapshot, "button", "Complete workflow")}
    result = await flow.invoke("browser_click", stale | {"snapshot_sha256": "0" * 64})
    assert not result.success and result.error_code == "browser_snapshot_stale", result.error_code
    assert (await oracle.state())["clicks"] == 1
    checks.append("invalid_digest_denied_without_effect")

    await oracle.evaluate("document.querySelector('#result').textContent='Fixture DOM changed'")
    result = await flow.invoke("browser_click", stale)
    assert not result.success and result.error_code == "browser_snapshot_stale", result.error_code
    assert (await oracle.state())["clicks"] == 1
    checks.append("changed_dom_denied_without_effect")

    snapshot = await flow.observe()
    stale = binding(snapshot) | {"target": target_ref(snapshot, "button", "Complete workflow")}
    await oracle.navigate(origin + "/other")
    result = await flow.invoke("browser_click", stale)
    assert not result.success and result.error_code == "browser_selected_tab_changed", (
        result.error_code
    )
    assert (await oracle.state())["clicks"] == 0
    checks.append("changed_url_denied_without_effect")
    await oracle.navigate(origin + "/form")

    snapshot = await flow.observe()
    stale = binding(snapshot) | {"target": target_ref(snapshot, "button", "Complete workflow")}
    result = await flow.invoke("browser_tabs", binding(snapshot) | {
        "action": "new", "url": origin + "/other",
    })
    assert result.success and result.metadata["goal_verified"] is True, result.error_code
    result = await flow.invoke("browser_click", stale)
    assert not result.success and result.error_code == "browser_snapshot_stale", result.error_code
    await oracle.target(origin + "/other")
    assert (await oracle.state())["clicks"] == 0
    current = await flow.observe()
    assert current["expected_url"] == origin + "/other" and current["tab_index"] == 1
    checks.append("switched_tab_old_context_denied_without_effect")
    return {
        "passed": True, "checks": checks, "approved_invocations": flow.invocations,
        "peer_version": "0.0.80", "real_browser": True, "isolated_profile": True,
        "synthetic_https_only": True, "owner_data_read": False, "model_api_calls": 0,
        "certifies": "bounded_fixture_workflows_not_general_computer_readiness",
    }


async def qualify(arguments: argparse.Namespace) -> dict[str, Any]:
    packages = pinned_packages(arguments.packages)
    chrome, node = arguments.chrome.resolve(strict=True), arguments.node.resolve(strict=True)
    with TemporaryDirectory(prefix="jarvis-browser-workflows-", dir="/private/tmp") as temporary:
        directory = Path(temporary)
        os.chmod(directory, 0o700)
        async with fixture_server(directory) as (origin, port), private_chrome(
            chrome, directory, origin + "/form", port,
        ) as endpoint, aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(total=10), trust_env=False,
        ) as client:
            oracle = FixtureOracle(client, endpoint)
            await oracle.target(origin + "/form")
            scope = IntegrationScope(provider="playwright", browser_origins={origin})
            session = BrowserSessionConfiguration(mode="cdp", cdp_endpoint=endpoint,
                                                  selected_tab_url=origin + "/form",
                                                  remote_content_allowed=True)
            candidate = local_peer_configuration(scope, node=node, packages=packages,
                                                 working_directory=directory,
                                                 browser_session=session)
            discovery = StdioMcpClient(candidate)
            try:
                catalogue = await discovery.start()
            finally:
                await discovery.close()
            expected = set(integration_tools(scope.provider)) - {"browser_close"}
            approved = {tool["name"]: reviewed_schema_sha256(tool["inputSchema"], scope,
                                                              tool["name"])
                        for tool in catalogue if tool["name"] in expected}
            assert set(approved) == expected, "reviewed_browser_catalogue_incomplete"
            config = candidate.model_copy(update={"enabled": True, "approved_tools": approved})
            host = McpHost(McpHostConfiguration(servers=(config,)))
            await host.start()
            try:
                assert len(host.tool_definitions()) == len(expected), "browser_admission_failed"
                return await workflows(host, oracle, directory, origin)
            finally:
                await host.close()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", action="store_true", help="Opt in to the isolated real browser")
    parser.add_argument("--packages", type=Path, default=default_packages())
    parser.add_argument("--node", type=Path, default=Path(shutil.which("node") or "/missing/node"))
    parser.add_argument("--chrome", type=Path, default=Path(
        "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    ))
    arguments = parser.parse_args()
    if not arguments.run:
        parser.error("--run is required; no process, package discovery or connection was started")

    async def bounded() -> dict[str, Any]:
        async with asyncio.timeout(120):
            return await qualify(arguments)

    try:
        print(json.dumps(asyncio.run(bounded()), sort_keys=True))
    except (AssertionError, OSError, ValueError, RuntimeError, TimeoutError) as error:
        # Do not emit peer payloads, browser content, process environment or tracebacks.
        reason = str(error)
        safe_reason = reason if re.fullmatch(r"[a-z0-9_]{1,100}", reason) else "workflow_failed"
        trace = error.__traceback__
        failure_line = None
        while trace is not None:
            if trace.tb_frame.f_code.co_filename == __file__:
                failure_line = trace.tb_lineno
            trace = trace.tb_next
        print(json.dumps({"passed": False, "failure_type": type(error).__name__,
                          "failure_code": safe_reason, "failure_line": failure_line,
                          "certifies": "no_success_claim"}, sort_keys=True))
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
