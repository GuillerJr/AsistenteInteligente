#!/usr/bin/env python3
"""Independent, bounded loopback MCP/OAuth peer for Jarvis qualification.

This is a synthetic test program, never a product server or an OAuth account.
It imports no Jarvis implementation. Credentials and authorization requests
live only in RAM; its optional journal contains method names and argument hashes.
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import contextlib
import hashlib
import html
import json
import os
import secrets
import signal
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urlsplit

RESOURCE = "jarvis://qualification/note"
TEMPLATE = "jarvis://qualification/item/{key}"
APP_URI = "ui://jarvis/qualification"
RESOURCE_MARKER = "RECURSO JARVIS 7319"
HTML_MARKER = "APP JARVIS 8624"
PRIVATE_MARKER = "SOLO UI JARVIS 5197"

APP_HTML = """<!doctype html><html lang="es"><meta charset="utf-8">
<style>body{font:16px system-ui;padding:20px}button{padding:10px}pre{white-space:pre-wrap}</style>
<h1>APP JARVIS 8624</h1><p>Peer sintético: sin cuentas ni red de terceros.</p>
<button id="call" disabled>Solicitar eco sintético</button>
<pre id="result">Inicializando puente</pre>
<script>
(()=>{'use strict';let sequence=2;const out=document.getElementById('result');
const send=value=>parent.postMessage(value,'*');
window.addEventListener('message',event=>{if(event.source!==parent)return;
 const m=event.data;if(!m||m.jsonrpc!=='2.0')return;
 if(m.id===1&&m.result){send({jsonrpc:'2.0',method:'ui/notifications/initialized'});
  document.getElementById('call').disabled=false;out.textContent='Puente preparado';return;}
 if(m.id)out.textContent=JSON.stringify(m.result||m.error,null,2);});
document.getElementById('call').addEventListener('click',()=>{
 out.textContent='Esperando aprobación en Jarvis';
 send({jsonrpc:'2.0',id:sequence++,method:'tools/call',
 params:{name:'echo',arguments:{text:'APP ECO 6421'}}});});
send({jsonrpc:'2.0',id:1,method:'ui/initialize',params:{protocolVersion:'2026-01-26',
 appInfo:{name:'Jarvis qualification peer',version:'1'}}});})();
</script></html>"""


def digest(value):
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, ensure_ascii=False).encode()
    ).hexdigest()


class QualificationPeer:
    def __init__(self, directory: Path | None = None):
        self.directory = directory
        self.base = None
        self.server = None
        self.tasks = set()
        self.writers = set()
        self.streams = {}
        self.records = []
        self.authorization_requests = {}
        self.codes = {}
        self.access_tokens = set()
        self.refresh_tokens = {}
        self.closed = False
        self.request_count = 0

    def record(self, event, **fields):
        row = {"event": event, **fields}
        if len(self.records) >= 256:
            raise ValueError("synthetic_peer_request_limit")
        self.records.append(row)
        if self.directory is not None:
            path = self.directory / "wire.jsonl"
            raw = (json.dumps(row, sort_keys=True) + "\n").encode()
            if path.exists() and path.stat().st_size + len(raw) > 131072:
                raise ValueError("synthetic_peer_journal_limit")
            descriptor = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
            with os.fdopen(descriptor, "ab") as stream:
                stream.write(raw)

    def metadata(self):
        return {
            "format": "jarvis-mcp-qualification-peer/1",
            "synthetic": True,
            "base": self.base,
            "endpoints": {
                "http2025June": self.base + "/mcp18",
                "http2025": self.base + "/mcp25",
                "http2026": self.base + "/mcp26",
                "sse2024": self.base + "/sse",
                "oauth2026": self.base + "/protected/mcp",
            },
            "oauth": {"client_id": "jarvis-qualification", "scopes": ["qualification:read"]},
            "resource": RESOURCE,
            "template": TEMPLATE,
            "app_uri": APP_URI,
            "contents": "synthetic markers only",
            "credentials_persisted": False,
        }

    async def start(self, port=0):
        self.server = await asyncio.start_server(self._accept, "127.0.0.1", port, limit=32768)
        self.base = "http://127.0.0.1:" + str(self.server.sockets[0].getsockname()[1])
        return self

    async def __aenter__(self):
        return await self.start()

    async def __aexit__(self, *_errors):
        await self.aclose()

    def _accept(self, reader, writer):
        task = asyncio.create_task(self._handle(reader, writer))
        self.tasks.add(task)
        self.writers.add(writer)
        task.add_done_callback(self.tasks.discard)

    async def aclose(self):
        self.closed = True
        if self.server is not None:
            self.server.close()
            await self.server.wait_closed()
        for writer in tuple(self.writers):
            writer.close()
        tasks = tuple(self.tasks)
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        for writer in tuple(self.writers):
            with contextlib.suppress(OSError):
                await asyncio.wait_for(writer.wait_closed(), 1)
            self.writers.discard(writer)
        self.streams.clear()
        self.authorization_requests.clear()
        self.codes.clear()
        self.access_tokens.clear()
        self.refresh_tokens.clear()

    async def _reply(self, writer, status, payload=b"", *, headers=None):
        if isinstance(payload, dict):
            payload = json.dumps(payload, ensure_ascii=False).encode()
        fields = {
            "Content-Type": "application/json",
            "Content-Length": str(len(payload)),
            "Connection": "close",
            **(headers or {}),
        }
        writer.write(
            f"HTTP/1.1 {status} Result\r\n".encode()
            + "".join(f"{key}: {value}\r\n" for key, value in fields.items()).encode()
            + b"\r\n"
            + payload
        )
        await writer.drain()

    async def _handle(self, reader, writer):
        stream_id = None
        try:
            self.request_count += 1
            if self.closed or len(self.tasks) > 32 or self.request_count > 128:
                return
            async with asyncio.timeout(10):
                raw = await reader.readuntil(b"\r\n\r\n")
                if len(raw) > 16384:
                    return
                lines = raw.decode("ascii").split("\r\n")
                method, target, _version = lines[0].split()
                headers = {}
                for line in lines[1:]:
                    if line:
                        key, value = line.split(":", 1)
                        key = key.lower()
                        if key in headers:
                            return
                        headers[key] = value.strip()
                if headers.get("host") != urlsplit(self.base).netloc:
                    return
                count = int(headers.get("content-length", "0"))
                if not 0 <= count <= 65536 or "transfer-encoding" in headers:
                    return
                body = await reader.readexactly(count)
            route = urlsplit(target).path
            if await self._oauth(method, target, body, writer):
                return
            protected = route == "/protected/mcp"
            if (
                protected
                and headers.get("authorization", "").removeprefix("Bearer ")
                not in self.access_tokens
            ):
                self.record("oauth_required")
                await self._reply(
                    writer,
                    401,
                    headers={
                        "WWW-Authenticate": 'Bearer resource_metadata="'
                        + self.base
                        + '/.well-known/oauth-protected-resource/protected/mcp"'
                    },
                )
                return
            if method == "GET" and route == "/sse":
                stream_id = secrets.token_hex(12)
                self.streams[stream_id] = writer
                writer.write(
                    b"HTTP/1.1 200 OK\r\nContent-Type: text/event-stream\r\n\r\n"
                    + f"event: endpoint\ndata: /messages?session={stream_id}\n\n".encode()
                )
                await writer.drain()
                self.record("sse_open")
                await reader.read()
                return
            if method == "DELETE" and route in {"/mcp18", "/mcp25", "/mcp26", "/protected/mcp"}:
                self.record("delete_session", route=route)
                await self._reply(writer, 204)
                return
            if method != "POST" or route not in {
                "/mcp18",
                "/mcp25",
                "/mcp26",
                "/protected/mcp",
                "/messages",
            }:
                await self._reply(writer, 404)
                return
            value = json.loads(body)
            protocol = {
                "/messages": "2024-11-05",
                "/mcp18": "2025-06-18",
                "/mcp25": "2025-11-25",
                "/mcp26": "2026-07-28",
                "/protected/mcp": "2026-07-28",
            }[route]
            if protocol != "2024-11-05" and headers.get("mcp-protocol-version") != protocol:
                raise ValueError("synthetic_peer_protocol_header")
            if protocol == "2026-07-28" and (
                headers.get("mcp-method") != value["method"]
                or value.get("params", {})
                .get("_meta", {})
                .get("io.modelcontextprotocol/protocolVersion")
                != protocol
            ):
                raise ValueError("synthetic_peer_modern_mirror")
            result = self._rpc(value, protocol)
            if "id" not in value:
                await self._reply(writer, 202)
                return
            reply = {"jsonrpc": "2.0", "id": value["id"], "result": result}
            if route == "/messages":
                key = parse_qs(urlsplit(target).query).get("session", [None])[0]
                stream = self.streams.get(key)
                if stream is None:
                    await self._reply(writer, 404)
                    return
                stream.write(b"event: message\ndata: " + json.dumps(reply).encode() + b"\n\n")
                await stream.drain()
                await self._reply(writer, 202)
                return
            fields = (
                {"Mcp-Session-Id": "jarvis-qualification-session"}
                if protocol in {"2025-06-18", "2025-11-25"}
                else {}
            )
            await self._reply(writer, 200, reply, headers=fields)
        except (TimeoutError, OSError, ValueError, KeyError, asyncio.IncompleteReadError):
            pass
        finally:
            if stream_id is not None:
                self.streams.pop(stream_id, None)
                self.record("sse_closed")
            writer.close()
            with contextlib.suppress(OSError):
                await writer.wait_closed()
            self.writers.discard(writer)

    def _rpc(self, value, protocol):
        method, params = value["method"], value.get("params", {})
        details = {"method": method, "protocol": protocol}
        if method in {"tools/call", "prompts/get"}:
            details["arguments_sha256"] = digest(params.get("arguments"))
        if method == "resources/read":
            details["uri_sha256"] = digest(params.get("uri"))
        self.record("rpc", **details)
        if method in {"initialize", "server/discover"}:
            return {
                "protocolVersion": protocol,
                "supportedVersions": [protocol],
                "ttlMs": 0,
                "cacheScope": "private",
                "serverInfo": {"name": "Jarvis qualification peer", "version": "1"},
                "capabilities": {"tools": {}, "resources": {}, "prompts": {}},
            }
        if method == "tools/list":
            return {
                "tools": [
                    {
                        "name": "echo",
                        "description": "Eco sintético, reporte externo",
                        "inputSchema": {
                            "type": "object",
                            "properties": {"text": {"type": "string", "maxLength": 256}},
                            "required": ["text"],
                            "additionalProperties": False,
                        },
                        "_meta": {"ui": {"resourceUri": APP_URI, "visibility": ["model", "app"]}},
                    }
                ]
            }
        if method == "resources/list":
            return {
                "resources": [
                    {"uri": RESOURCE, "name": "Nota sintética", "mimeType": "text/plain"},
                    {
                        "uri": APP_URI,
                        "name": "App sintética",
                        "mimeType": "text/html;profile=mcp-app",
                    },
                ]
            }
        if method == "resources/templates/list":
            return {
                "resourceTemplates": [
                    {
                        "uriTemplate": TEMPLATE,
                        "name": "Elemento sintético",
                        "mimeType": "text/plain",
                    }
                ]
            }
        if method == "prompts/list":
            return {
                "prompts": [
                    {
                        "name": "explain",
                        "description": "Datos sin ejecutar modelo",
                        "arguments": [{"name": "topic", "required": True}],
                    }
                ]
            }
        if method == "resources/read":
            uri = params["uri"]
            app = uri == APP_URI
            return {
                "contents": [
                    {
                        "uri": uri,
                        "mimeType": "text/html;profile=mcp-app" if app else "text/plain",
                        "text": APP_HTML if app else RESOURCE_MARKER,
                        "_meta": {
                            "private": PRIVATE_MARKER,
                            "ui": {"csp": {"connectDomains": [], "resourceDomains": []}},
                        },
                    }
                ]
            }
        if method == "prompts/get":
            return {
                "messages": [
                    {
                        "role": "user",
                        "content": {
                            "type": "text",
                            "text": "PROMPT JARVIS " + params.get("arguments", {}).get("topic", ""),
                        },
                    }
                ],
                "_meta": {"private": PRIVATE_MARKER},
            }
        if method == "tools/call":
            if params["name"] != "echo":
                raise ValueError("synthetic_peer_tool_unknown")
            return {
                "content": [{"type": "text", "text": "ECO EXTERNO " + params["arguments"]["text"]}],
                "isError": False,
                "_meta": {"private": PRIVATE_MARKER},
            }
        return {}

    async def _oauth(self, method, target, body, writer):
        route = urlsplit(target).path
        if method == "GET" and route == "/.well-known/oauth-protected-resource/protected/mcp":
            self.record("oauth_resource_discovery")
            await self._reply(
                writer,
                200,
                {
                    "resource": self.base + "/protected/mcp",
                    "authorization_servers": [self.base + "/issuer"],
                    "scopes_supported": ["qualification:read"],
                },
            )
        elif method == "GET" and route == "/.well-known/oauth-authorization-server/issuer":
            await self._reply(
                writer,
                200,
                {
                    "issuer": self.base + "/issuer",
                    "authorization_endpoint": self.base + "/authorize",
                    "token_endpoint": self.base + "/token",
                    "code_challenge_methods_supported": ["S256"],
                    "authorization_response_iss_parameter_supported": True,
                    "token_endpoint_auth_methods_supported": ["none"],
                },
            )
        elif method == "GET" and route == "/authorize":
            query = {key: values[0] for key, values in parse_qs(urlsplit(target).query).items()}
            callback = urlsplit(query["redirect_uri"])
            if (
                query["client_id"] != "jarvis-qualification"
                or query["resource"] != self.base + "/protected/mcp"
                or query["code_challenge_method"] != "S256"
                or query["scope"] != "qualification:read"
                or callback.scheme != "http"
                or callback.hostname != "127.0.0.1"
                or not callback.port
                or callback.path != "/jarvis-oauth/callback"
                or callback.query
                or callback.fragment
            ):
                await self._reply(writer, 400)
                return True
            ticket = secrets.token_hex(20)
            self.authorization_requests[ticket] = query
            page = (
                '<!doctype html><meta charset="utf-8"><h1>Autorización sintética Jarvis</h1>'
                "<p>No se inicia sesión ni se concede acceso a una cuenta real.</p>"
                '<a href="/approve?ticket='
                + html.escape(ticket)
                + '">Autorizar perfil sintético</a>'
            )
            await self._reply(
                writer, 200, page.encode(), headers={"Content-Type": "text/html; charset=utf-8"}
            )
        elif method == "GET" and route == "/approve":
            ticket = parse_qs(urlsplit(target).query).get("ticket", [None])[0]
            request = self.authorization_requests.pop(ticket, None)
            if request is None:
                await self._reply(writer, 400)
                return True
            code = secrets.token_urlsafe(24)
            self.codes[code] = request
            self.record("oauth_approved")
            location = (
                request["redirect_uri"]
                + "?"
                + urlencode({"code": code, "state": request["state"], "iss": self.base + "/issuer"})
            )
            await self._reply(writer, 302, headers={"Location": location})
        elif method == "POST" and route == "/token":
            fields = {key: values[0] for key, values in parse_qs(body.decode()).items()}
            grant = fields.get("grant_type")
            valid = (
                fields.get("resource") == self.base + "/protected/mcp"
                and fields.get("client_id") == "jarvis-qualification"
            )
            if grant == "authorization_code":
                request = self.codes.pop(fields.get("code"), None)
                challenge = (
                    base64.urlsafe_b64encode(
                        hashlib.sha256(fields.get("code_verifier", "").encode()).digest()
                    )
                    .decode()
                    .rstrip("=")
                )
                valid = (
                    valid
                    and request is not None
                    and request["code_challenge"] == challenge
                    and request["redirect_uri"] == fields.get("redirect_uri")
                )
            elif grant == "refresh_token":
                old = self.refresh_tokens.pop(fields.get("refresh_token"), None)
                valid = valid and old is not None
                self.access_tokens.discard(old)
            else:
                valid = False
            if not valid:
                await self._reply(writer, 400, {"error": "invalid_grant"})
                return True
            access, refresh = secrets.token_urlsafe(24), secrets.token_urlsafe(24)
            self.access_tokens.add(access)
            self.refresh_tokens[refresh] = access
            self.record("oauth_token", grant=grant)
            await self._reply(
                writer,
                200,
                {
                    "token_type": "Bearer",
                    "access_token": access,
                    "refresh_token": refresh,
                    "expires_in": 3600,
                    "scope": "qualification:read",
                },
            )
        else:
            return False
        return True


async def main(directory, port, seconds):
    directory.mkdir(mode=0o700, parents=False, exist_ok=False)
    peer = QualificationPeer(directory)
    await peer.start(port)
    path = directory / "ready.json"
    path.write_text(json.dumps(peer.metadata(), ensure_ascii=False, indent=2) + "\n")
    path.chmod(0o600)
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for name in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(name, stop.set)
    print(json.dumps({"ready_file": str(path), "synthetic": True}), flush=True)
    try:
        await asyncio.wait_for(stop.wait(), seconds)
    except TimeoutError:
        pass
    finally:
        await peer.aclose()
        print(
            json.dumps(
                {
                    "closed": True,
                    "connections_remaining": len(peer.writers),
                    "tasks_remaining": len(peer.tasks),
                }
            ),
            flush=True,
        )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--directory", type=Path, required=True, help="Carpeta sintética nueva")
    parser.add_argument("--port", type=int, default=0)
    parser.add_argument("--seconds", type=int, default=1800)
    options = parser.parse_args()
    if (
        not options.directory.is_absolute()
        or not 1 <= options.seconds <= 3600
        or not 0 <= options.port <= 65535
    ):
        parser.error("Se requiere ruta absoluta, plazo 1..3600 s y puerto válido")
    asyncio.run(main(options.directory, options.port, options.seconds))
