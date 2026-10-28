"""Actual private RPC + owned MCP subprocess + central Runtime approvals."""

import asyncio
import json
import sys
import traceback
from pathlib import Path
from uuid import uuid4

import pytest
from test_plugins import package
from test_service import connection as connection
from test_service import session, settled

from jarvis_core.client_identity import ClientIdentity
from jarvis_core.protocol import LocalRpcClient, RpcError
from jarvis_core.runtime import AgentRuntime
from jarvis_core.service import _ProviderAdapter

UI_MARKER = "MCP_UI_ONLY_9462"
HTML_MARKER = "MCP_APP_HTML_2718"
_FAILURES = {}
SERVER = r'''
import json,sys,threading,time
from pathlib import Path
log,control=sys.argv[1:]
lock=threading.Lock()
def emit(value):
    with lock: print(json.dumps(value),flush=True)
def notification():
    while not Path(control).exists(): time.sleep(.01)
    emit({'jsonrpc':'2.0','method':'notifications/resources/list_changed'})
threading.Thread(target=notification,daemon=True).start()
for line in sys.stdin:
    value=json.loads(line)
    with open(log,'a') as output: output.write(json.dumps(value)+'\n')
    if 'id' not in value: continue
    method=value['method']; params=value.get('params',{})
    if method=='initialize':
        result={'protocolVersion':'2025-11-25','capabilities':{'tools':{},'resources':{},'prompts':{}},
                'serverInfo':{'name':'Jarvis synthetic product peer','version':'1'}}
    elif method=='tools/list':
        result={'tools':[{'name':'echo','description':'Own synthetic effect',
            'inputSchema':{'type':'object','properties':{'text':{'type':'string'}},
                          'required':['text'],'additionalProperties':False},
            '_meta':{'ui':{'resourceUri':'ui://jarvis/product','visibility':['model','app']}}}]}
    elif method=='resources/list':
        result={'resources':[{'uri':'jarvis://product/note','name':'nota','mimeType':'text/plain'},
            {'uri':'ui://jarvis/product','name':'app','mimeType':'text/html;profile=mcp-app'}]}
    elif method=='resources/templates/list':
        result={'resourceTemplates':[{'uriTemplate':'jarvis://product/item/{key}',
                                     'name':'elemento','mimeType':'text/plain'}]}
    elif method=='prompts/list':
        result={'prompts':[{'name':'explain','description':'Datos de prompt',
            'arguments':[{'name':'topic','required':True}]}]}
    elif method=='resources/read':
        uri=params['uri']
        app_document=uri.startswith('ui://') or uri.endswith('/surprise-ui')
        result={'contents':[{'uri':uri,
            'mimeType':'text/html;profile=mcp-app' if app_document else 'text/plain',
            'text':'<html><body>MCP_APP_HTML_2718</body></html>' if app_document
                else 'RESOURCE REPORT 7219',
            '_meta':{'ui':{'csp':{'connectDomains':[],'resourceDomains':[]}},
                     'private':'MCP_UI_ONLY_9462'}}]}
    elif method=='prompts/get':
        result={'messages':[{'role':'user','content':{'type':'text',
            'text':'PROMPT REPORT '+params['arguments']['topic']}}],
            '_meta':{'private':'MCP_UI_ONLY_9462'}}
    elif method=='tools/call':
        result={'content':[{'type':'text','text':'TOOL REPORT '+params['arguments']['text']}],
            'structuredContent':{'value':params['arguments']['text']},
            '_meta':{'private':'MCP_UI_ONLY_9462'},'isError':False}
    else:
        emit({'jsonrpc':'2.0','id':value['id'],'error':{'code':-32601,'message':'unsupported'}})
        continue
    emit({'jsonrpc':'2.0','id':value['id'],'result':result})
'''


@pytest.fixture(autouse=True)
def synthetic_failure_frames(monkeypatch):
    # No exception messages, arguments or source lines. These owned-fixture
    # traces identify the exact failing stage before an approval can exist.
    _FAILURES.clear()

    def wrap(method, stage):
        async def checked(self, *args, **kwargs):
            try:
                return await method(self, *args, **kwargs)
            except Exception as error:
                context = next((item for item in args if hasattr(item, "run_id")), None)
                run_id = context.run_id if context is not None else "unknown"
                cause = error.__cause__ or error
                frames = traceback.StackSummary.extract(traceback.walk_tb(cause.__traceback__),
                                                          lookup_lines=False)
                _FAILURES[run_id] = {"stage": stage, "error_type": type(error).__name__,
                    "cause_type": type(cause).__name__, "frames": [
                        (Path(row.filename).name, row.lineno, row.name) for row in frames]}
                raise
        return checked

    monkeypatch.setattr(_ProviderAdapter, "next_step",
                        wrap(_ProviderAdapter.next_step, "next_step"))
    monkeypatch.setattr(AgentRuntime, "_tool", wrap(AgentRuntime._tool, "tool_before_approval"))


def wire(root):
    path = root / "product-wire.jsonl"
    return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []


def server(root):
    script = root / "product-peer.py"
    script.write_text(SERVER)
    return {"server_id": "synthetic", "protocol_version": "2025-11-25",
            "command": [sys.executable, str(script), str(root / "product-wire.jsonl"),
                        str(root / "product-catalog-changed")]}


async def connected(connection):
    service, client, provider, root = connection
    spec = server(root)
    await client.call("mcp.add", spec)
    await client.call("mcp.connect", spec)
    catalog = await client.call("mcp.catalog", {"server_id": "synthetic"})
    return service, client, provider, root, catalog


async def approve(client, ref, run_id, *, allow=True, before_decision=None):
    async with asyncio.timeout(5):
        while True:
            tickets = (await client.call("approvals.list", {**ref, "run_id": run_id}))["approvals"]
            if tickets:
                ticket = tickets[0]
                break
            current = (await client.call("runs.get", {**ref, "run_id": run_id}))["run"]
            if current["status"] in {"completed", "failed", "cancelled", "interrupted"}:
                pytest.fail("Run settled before approval: " + json.dumps({
                    "run": current, "diagnostic": _FAILURES.get(run_id)}, ensure_ascii=False))
            await asyncio.sleep(.005)
    if before_decision is not None:
        before_decision(ticket)
    accepted = await client.call("approvals.decide", {**ref, "approval_id": ticket["approval_id"],
                                  "digest": ticket["digest"], "allow": allow})
    assert accepted["accepted"]
    return ticket


async def drained(service):
    async with asyncio.timeout(5):
        while service._handles or service._refs:
            await asyncio.sleep(.005)


async def opened(connection):
    service, client, provider, root, catalog = await connected(connection)
    ref = await session(client)
    request = {**ref, "server_id": "synthetic", "resource_uri": "ui://jarvis/product",
               "expected_catalog_hash": catalog["catalog_hash"], "request_id": uuid4().hex}
    run = await client.call("mcp.apps.open", request)
    await approve(client, ref, run["run_id"])
    assert (await settled(client, ref, run["run_id"]))["status"] == "completed"
    async with asyncio.timeout(5):
        while True:
            value = await client.call("mcp.apps.document", {**ref, "run_id": run["run_id"]})
            if value["settled"]:
                break
            await asyncio.sleep(.005)
    assert value["app"] is not None
    return service, client, provider, root, catalog, ref, run, value["app"]


@pytest.mark.parametrize("kind", ["resource", "prompt"])
async def test_catalog_content_uses_central_approval_without_running_a_prompt(connection, kind):
    service, client, provider, root, catalog = await connected(connection)
    assert catalog["resources"] and catalog["prompts"] and catalog["tools"][0]["ui"]
    assert not [row for row in wire(root) if row["method"] in {"resources/read", "prompts/get"}]
    ref = await session(client)
    value = {**ref, "server_id": "synthetic", "kind": kind,
             "expected_catalog_hash": catalog["catalog_hash"], "request_id": uuid4().hex}
    value.update({"uri": "jarvis://product/note"} if kind == "resource"
                 else {"name": "explain", "arguments": {"topic": "datos sintéticos"}})
    run = await client.call("mcp.content.request", value)
    assert not [row for row in wire(root) if row["method"] in {"resources/read", "prompts/get"}]
    ticket = await approve(client, ref, run["run_id"])
    assert ticket["tool_name"] == ("mcp_resource_read" if kind == "resource" else "mcp_prompt_get")
    result = await settled(client, ref, run["run_id"])
    assert result["status"] == "completed" and not result["action_receipts"][0]["verified"]
    assert UI_MARKER not in json.dumps(result)
    assert provider.calls == []
    await drained(service)
    content = await client.call("mcp.content.result", {**ref, "run_id": run["run_id"]})
    assert content["settled"] and content["provenance"] == "external_unverified"
    assert UI_MARKER not in json.dumps(content)


@pytest.mark.parametrize("origin", ["host", "model"])
async def test_resource_template_expands_and_reads_only_approved_exact_uri(connection, monkeypatch,
                                                                           origin):
    service, client, provider, root, catalog = await connected(connection)
    assert catalog["resource_templates"][0]["uriTemplate"] == "jarvis://product/item/{key}"
    assert catalog["resource_templates"][0]["variables"] == ["key"]
    args = {"server_id": "synthetic", "expected_catalog_hash": catalog["catalog_hash"],
            "template_uri": "jarvis://product/item/{key}", "arguments": {"key": "área 42"}}
    expanded = await client.call("mcp.template.expand", args)
    exact = "jarvis://product/item/%C3%A1rea%2042"
    assert expanded == {"uri": exact}
    assert not [row for row in wire(root) if row["method"] == "resources/read"]
    ref = await session(client)
    reports = []
    if origin == "host":
        started = await client.call("mcp.content.request", {
            **ref, **args, "kind": "template", "request_id": uuid4().hex})
    else:
        async def proposal(message, *, thread_id, on_thread, on_tool_call, **options):
            if thread_id is None:
                thread_id = "synthetic-template-" + uuid4().hex
                on_thread(thread_id)
            reports.append(await on_tool_call("mcp_resource_template_read", args))
            return {"thread_id": thread_id, "answer": "Reporte externo recibido, sin verificación."}

        monkeypatch.setattr(provider, "chat", proposal)
        started = await client.call("runs.start", {
            **ref, "message": "Consulta el recurso sintético"})

    def check_pending(ticket):
        assert ticket["tool_name"] == "mcp_resource_template_read"
        assert json.loads(ticket["arguments_json"]) == args
        assert not [row for row in wire(root) if row["method"] == "resources/read"]

    await approve(client, ref, started["run_id"], before_decision=check_pending)
    result = await settled(client, ref, started["run_id"])
    assert result["status"] == "completed" and not result["action_receipts"][0]["verified"]
    assert [row["params"]["uri"] for row in wire(root)
            if row["method"] == "resources/read"] == [exact]
    assert UI_MARKER not in json.dumps(result)
    assert UI_MARKER not in json.dumps(reports, default=str)
    if origin == "model":
        assert len(reports) == 1 and "RESOURCE REPORT" in json.dumps(reports)
    await drained(service)


async def test_known_ui_resource_cannot_use_generic_read_or_leak_via_model_catalog(connection,
                                                                                 monkeypatch):
    service, client, provider, root, catalog = await connected(connection)
    ref = await session(client)
    with pytest.raises(RpcError):
        await client.call("mcp.content.request", {**ref, "server_id": "synthetic",
            "kind": "resource",
            "uri": "ui://jarvis/product", "expected_catalog_hash": catalog["catalog_hash"],
            "request_id": uuid4().hex})
    assert not [row for row in wire(root) if row["method"] == "resources/read"]
    reports = []

    async def proposal(message, *, thread_id, on_thread, on_tool_call, **options):
        if thread_id is None:
            thread_id = "synthetic-private-ui-" + uuid4().hex
            on_thread(thread_id)
        reports.append(await on_tool_call("mcp_catalog", {"server_id": "synthetic"}))
        await on_tool_call("mcp_resource_read", {"server_id": "synthetic",
            "expected_catalog_hash": catalog["catalog_hash"], "uri": "ui://jarvis/product"})
        pytest.fail("known UI content bypassed the generic model-tool boundary")

    monkeypatch.setattr(provider, "chat", proposal)
    started = await client.call("runs.start", {**ref, "message": "Prueba de separación UI/modelo"})
    result = await settled(client, ref, started["run_id"])
    assert result["status"] == "failed" and result["error"] == "invalid_arguments"
    assert not [row for row in wire(root) if row["method"] == "resources/read"]
    assert not service.authority.list_pending()
    catalog_receipt = json.loads(reports[0]["contentItems"][0]["text"])
    public_catalog = json.loads(catalog_receipt["output"])
    assert all(item["mimeType"] != "text/html;profile=mcp-app"
               for item in public_catalog["resources"])
    assert UI_MARKER not in json.dumps(reports) and HTML_MARKER not in json.dumps(reports)


async def test_unexpected_template_app_mime_never_enters_model_or_durable_receipts(connection,
                                                                                monkeypatch):
    service, client, provider, root, catalog = await connected(connection)
    ref = await session(client)
    args = {"server_id": "synthetic", "expected_catalog_hash": catalog["catalog_hash"],
            "template_uri": "jarvis://product/item/{key}", "arguments": {"key": "surprise-ui"}}
    reports = []

    async def proposal(message, *, thread_id, on_thread, on_tool_call, **options):
        if thread_id is None:
            thread_id = "synthetic-unexpected-ui-" + uuid4().hex
            on_thread(thread_id)
        reports.append(await on_tool_call("mcp_resource_template_read", args))
        return {"thread_id": thread_id, "answer": "La información de interfaz permanece privada."}

    monkeypatch.setattr(provider, "chat", proposal)
    started = await client.call("runs.start", {**ref, "message": "Prueba de MIME inesperado"})
    await approve(client, ref, started["run_id"])
    result = await settled(client, ref, started["run_id"])
    await drained(service)
    assert [row["params"]["uri"] for row in wire(root)
            if row["method"] == "resources/read"] == ["jarvis://product/item/surprise-ui"]
    assert UI_MARKER not in json.dumps(result) and HTML_MARKER not in json.dumps(result)
    assert UI_MARKER not in json.dumps(reports) and HTML_MARKER not in json.dumps(reports)
    durable = "\n".join(service.store._disk.iterdump())
    assert UI_MARKER not in durable and HTML_MARKER not in durable


async def test_apps_html_and_ui_metadata_stay_ram_only_and_tool_calls_require_approval(connection):
    service, client, provider, root, _catalog, ref, _source, app = await opened(connection)
    assert HTML_MARKER in app["html"] and app["csp"]["connect_domains"] == []
    request = {"app_instance_id": app["app_instance_id"], "message": {
        "jsonrpc": "2.0", "id": "one", "method": "tools/call",
        "params": {"name": "echo", "arguments": {"text": "EXACT SYNTHETIC 8524"}}}}
    pending = await client.call("mcp.apps.request", request)
    assert not pending["settled"] and not [r for r in wire(root) if r["method"] == "tools/call"]
    ticket = await approve(client, ref, pending["run_id"])
    assert json.loads(ticket["arguments_json"]) == {"text": "EXACT SYNTHETIC 8524"}
    result = await settled(client, ref, pending["run_id"])
    assert result["status"] == "completed" and not result["action_receipts"][0]["verified"]
    await drained(service)
    polled = await client.call("mcp.apps.poll", {"app_instance_id": app["app_instance_id"],
                                               "request_id": pending["request_id"]})
    assert polled["settled"] and UI_MARKER in json.dumps(polled["result"])
    repeated = await client.call("mcp.apps.request", request)
    assert repeated["request_id"] == pending["request_id"]
    assert len([r for r in wire(root) if r["method"] == "tools/call"]) == 1
    durable = "\n".join(service.store._disk.iterdump())
    assert UI_MARKER not in durable and HTML_MARKER not in durable and provider.calls == []
    assert await client.call("mcp.apps.close", {"app_instance_id": app["app_instance_id"]}) == {
        "closed": True}


async def test_closed_app_cannot_be_recreated_by_a_late_document_poll(connection):
    service, client, _provider, _root, _catalog, ref, source, app = await opened(connection)
    await drained(service)
    await client.call("mcp.apps.close", {"app_instance_id": app["app_instance_id"]})
    try:
        value = await client.call("mcp.apps.document", {**ref, "run_id": source["run_id"]})
    except RpcError:
        pass
    else:
        assert value.get("app") is None
    assert not service.mcp_product.apps


async def test_app_close_cancels_pending_approval_without_dispatch_or_replay(connection):
    service, client, _provider, root, _catalog, ref, source, app = await opened(connection)
    pending = await client.call("mcp.apps.request", {"app_instance_id": app["app_instance_id"],
        "message": {"jsonrpc": "2.0", "id": "cancel", "method": "tools/call",
                    "params": {"name": "echo", "arguments": {"text": "NEVER DISPATCH"}}}})
    async with asyncio.timeout(5):
        while not (await client.call("approvals.list", {**ref,
                                                       "run_id": pending["run_id"]}))["approvals"]:
            await asyncio.sleep(.005)
    result = await client.call("mcp.apps.close", {"app_instance_id": app["app_instance_id"]})
    assert result["closed"]
    assert (await settled(client, ref, pending["run_id"]))["status"] in {"cancelled", "failed"}
    await drained(service)
    assert not [row for row in wire(root) if row["method"] == "tools/call"]
    assert not service.authority.list_pending()
    assert service.host_tools.get(source["run_id"]).payload is None


async def test_catalog_notification_and_unknown_app_methods_cannot_dispatch_tools(connection):
    service, client, _provider, root, _catalog, _ref, _source, app = await opened(connection)
    denied = await client.call("mcp.apps.request", {"app_instance_id": app["app_instance_id"],
        "message": {"jsonrpc": "2.0", "id": 1, "method": "owner/approve", "params": {}}})
    assert denied["error"]["code"] == -32601
    (root / "product-catalog-changed").touch()
    async with asyncio.timeout(5):
        while not service.mcp._entries["synthetic"].client._catalog_generations["resources"] > 1:
            await asyncio.sleep(.005)
    with pytest.raises(RpcError):
        await client.call("mcp.apps.request", {"app_instance_id": app["app_instance_id"],
            "message": {"jsonrpc": "2.0", "id": 2, "method": "tools/call",
                        "params": {"name": "echo", "arguments": {"text": "NO"}}}})
    assert not [row for row in wire(root) if row["method"] == "tools/call"]


async def test_client_revocation_revokes_the_existing_app_instance(connection):
    service, client, _provider, _root, _catalog, _ref, _source, app = await opened(connection)
    await drained(service)
    owner = ClientIdentity.generate()
    status = await client.call("clients.status", {})
    review = await client.call("clients.review", {"public_key": owner.public_key, "scope": "owner",
                              "expected_revision": status["revision"]})
    await client.call("clients.admit", {"review_id": review["review_id"],
                                         "review_digest": review["review_digest"]})
    async with LocalRpcClient(service.directory, identity=owner) as admin:
        status = await admin.call("clients.status", {})
        await admin.call("clients.revoke", {"client_id": client.identity.client_id,
                                            "expected_revision": status["revision"]})
    assert service.mcp_product.apps[app["app_instance_id"]].closing
    with pytest.raises(RpcError):
        await client.call("mcp.apps.request", {"app_instance_id": app["app_instance_id"],
            "message": {"jsonrpc": "2.0", "id": 1, "method": "ping", "params": {}}})


async def test_plugin_context_and_hook_mcp_share_separate_reviews(connection, monkeypatch):
    service, client, provider, root = connection
    spec = server(root)

    def descriptor(manifest, files):
        manifest["mcp"] = [{**spec, "transport": "stdio"}]
        manifest["hooks"].append({"event": "before_turn", "mcp_server_id": "synthetic",
                                   "tool": "echo", "arguments": {"text": "SYNTHETIC HOOK"}})

    path = package(root, mutate=descriptor)
    preview = (await client.call("plugins.inspect", {"path": str(path)}))["package"]
    reviewed = (await client.call("plugins.review", {"action": "install", "agent_id": "jarvis",
                  "path": str(path), "package_sha256": preview["sha256"]}))["review"]
    row = (await client.call("plugins.decide", {"review_id": reviewed["review_id"],
                  "digest": reviewed["digest"], "allow": True}))["plugin"]
    assert not wire(root)
    selected = {"agent_id": "jarvis", "plugin_id": row["plugin_id"],
                "expected_revision": row["revision"], "expected_sha256": row["sha256"]}
    reviewed = (await client.call("plugins.mcp.review", {**selected,
                                                         "server_id": "synthetic"}))["review"]
    configured = await client.call("plugins.mcp.decide", {"review_id": reviewed["review_id"],
                                      "digest": reviewed["digest"], "allow": True})
    assert configured["configured"] and not configured["receipt"]["connected"] and not wire(root)
    await client.call("mcp.connect", configured["server"])
    reviewed = (await client.call("plugins.review", {**selected, "action": "enable"}))["review"]
    await client.call("plugins.decide", {"review_id": reviewed["review_id"],
                                         "digest": reviewed["digest"], "allow": True})
    captured, original = [], provider.chat

    async def capture(message, **options):
        captured.append(options)
        return await original(message, **options)

    monkeypatch.setattr(provider, "chat", capture)
    ref = await session(client)
    started = await client.call("runs.start", {**ref, "message": "Responde con un ejemplo"})
    run_id = started["run_id"]
    pending = await approve(client, ref, run_id)
    assert json.loads(pending["arguments_json"]) == {"text": "SYNTHETIC HOOK"}
    result = await settled(client, ref, run_id)
    assert result["status"] == "completed" and len(result["action_receipts"]) == 1
    assert captured and "Explica con un ejemplo" in captured[0]["context_data"]
    assert UI_MARKER not in json.dumps(captured, default=str)
    assert len([item for item in wire(root) if item["method"] == "tools/call"]) == 1
    await drained(service)
    current = (await client.call("plugins.list", {"agent_id": "jarvis"}))["plugins"][0]
    selected.update(expected_revision=current["revision"], expected_sha256=current["sha256"])
    catalog = (await client.call("plugins.catalog", selected))["catalog"]
    hook = next(item for item in catalog["hooks"] if item["event"] == "enable")
    request = {**ref, **selected, "event": "enable", "hook_sha256": hook["sha256"],
               "request_id": uuid4().hex}
    started = await client.call("plugins.hooks.run", request)
    await approve(client, ref, started["run_id"])
    assert (await settled(client, ref, started["run_id"]))["status"] == "completed"
    await drained(service)
    assert (await client.call("plugins.hooks.run", request))["run_id"] == started["run_id"]
    assert len([item for item in wire(root) if item["method"] == "tools/call"]) == 2
    assert len(captured) == 1
