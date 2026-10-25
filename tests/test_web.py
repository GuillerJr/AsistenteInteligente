"""Disposable HTTPS peers and synthetic approvals, never the owner's network/data.

The production connector remains fixed to public TCP/443 with normal PKI. The
test host injects only a resolver and connector targeting this test's ephemeral
loopback TLS peer and test CA; no public DNS or internet request is performed.
"""

import asyncio
import gzip
import hashlib
import json
import socket
import ssl
import subprocess
from dataclasses import dataclass, replace
from uuid import uuid4

import pytest

import jarvis_core.web as module
from jarvis_core.tools import ApprovalAuthority, ToolCancelled, ToolCatalog

URL = "https://fixture.example.org/start"
PUBLIC = "93.184.216.34"


@pytest.fixture(scope="session")
def certificates(tmp_path_factory):
    root = tmp_path_factory.mktemp("jarvis-web-ca")
    root.chmod(0o700)
    config = root / "fixture.cnf"
    key, cert = root / "key.pem", root / "cert.pem"
    config.write_text(
        "[req]\nprompt=no\ndistinguished_name=dn\nx509_extensions=ext\n"
        "[dn]\nCN=fixture.example.org\n[ext]\nbasicConstraints=critical,CA:TRUE\n"
        "keyUsage=critical,digitalSignature,keyEncipherment,keyCertSign\n"
        "extendedKeyUsage=serverAuth\n"
        "subjectAltName=DNS:fixture.example.org,DNS:other.example.org\n"
    )
    result = subprocess.run(
        [
            "openssl",
            "req",
            "-new",
            "-newkey",
            "rsa:2048",
            "-x509",
            "-nodes",
            "-days",
            "1",
            "-config",
            str(config),
            "-keyout",
            str(key),
            "-out",
            str(cert),
        ],
        capture_output=True,
        timeout=20,
        check=False,
        env={"PATH": "/opt/homebrew/bin:/usr/bin:/bin", "HOME": str(root)},
    )
    assert result.returncode == 0, "The disposable fixture certificate could not be generated"
    key.chmod(0o600)
    server = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    server.minimum_version = ssl.TLSVersion.TLSv1_2
    server.load_cert_chain(cert, key)
    client = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    client.check_hostname = True
    client.verify_mode = ssl.CERT_REQUIRED
    client.load_verify_locations(cafile=str(cert))
    return server, client


class Peer:
    def __init__(self, server_context, client_context, reply):
        self.server_context, self.client_context, self.reply = server_context, client_context, reply
        self.requests = []
        self.partials = []
        self.connections = []
        self.resolutions = []
        self.tasks = set()
        self.connected = asyncio.Event()
        self.request_received = asyncio.Event()
        self.closed = asyncio.Event()

    async def start(self):
        self.server = await asyncio.start_server(
            self.handle,
            "127.0.0.1",
            0,
            ssl=self.server_context,
            limit=33792,
            ssl_handshake_timeout=1,
            ssl_shutdown_timeout=0.1,
        )
        self.port = self.server.sockets[0].getsockname()[1]
        return self

    async def handle(self, reader, writer):
        task = asyncio.current_task()
        self.tasks.add(task)
        try:
            async with asyncio.timeout(3):
                request = await reader.readuntil(b"\r\n\r\n")
                self.requests.append(request)
                self.request_received.set()
                await self.reply(len(self.requests), reader, writer)
        except asyncio.IncompleteReadError as error:
            self.partials.append(error.partial)
        except (OSError, TimeoutError, asyncio.CancelledError):
            pass
        finally:
            writer.close()
            try:
                await asyncio.wait_for(writer.wait_closed(), 0.2)
            except (OSError, TimeoutError, asyncio.CancelledError):
                pass
            self.tasks.discard(task)
            self.closed.set()

    async def resolver(self, hostname):
        self.resolutions.append(hostname)
        return (PUBLIC,)

    async def connector(self, address, hostname):
        assert address == PUBLIC
        assert hostname in {"fixture.example.org", "other.example.org"}
        self.connections.append((address, hostname))
        streams = await asyncio.open_connection(
            "127.0.0.1",
            self.port,
            ssl=self.client_context,
            server_hostname=hostname,
            ssl_handshake_timeout=1,
            ssl_shutdown_timeout=0.1,
            limit=33792,
        )
        self.connected.set()
        return streams

    async def close(self):
        self.server.close()
        await self.server.wait_closed()
        tasks = tuple(self.tasks)
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)


def response(body=b"hello", *, status=200, headers=None, length=True):
    fields = {"Content-Type": "text/plain; charset=utf-8", **(headers or {})}
    if length:
        fields["Content-Length"] = str(len(body))
    first = f"HTTP/1.1 {status} Fixture\r\n".encode()
    return (
        first
        + b"".join(f"{key}: {value}\r\n".encode() for key, value in fields.items())
        + b"\r\n"
        + body
    )


@pytest.fixture
async def peers(certificates):
    opened = []

    async def make(reply):
        if isinstance(reply, bytes):
            raw = reply

            async def reply(_index, _reader, writer):
                writer.write(raw)
                await writer.drain()

        peer = await Peer(*certificates, reply).start()
        opened.append(peer)
        return peer

    yield make
    for peer in opened:
        await peer.close()


@dataclass
class Bundle:
    manager: module.WebFetchTools
    authority: ApprovalAuthority
    catalog: ToolCatalog


@pytest.fixture
async def bundles():
    opened = []

    def make(peer=None, **options):
        if peer is not None:
            options.setdefault("resolver", peer.resolver)
            options.setdefault("connector", peer.connector)
        manager = module.WebFetchTools(**options)
        authority = ApprovalAuthority(ttl=3)
        catalog = ToolCatalog(manager.definitions(), manager.specs(), authority=authority)
        bundle = Bundle(manager, authority, catalog)
        opened.append(bundle)
        return bundle

    yield make
    for bundle in opened:
        await bundle.manager.aclose()
        await bundle.authority.close()


async def approved(bundle, url=URL, *, allow=True, cancellation=None, args=None):
    run = uuid4().hex
    arguments = {"url": url} if args is None else args
    task = asyncio.create_task(
        bundle.catalog.execute(
            "web_fetch",
            arguments,
            session_id="synthetic-chat",
            run_id=run,
            cancellation=cancellation,
        )
    )
    async with asyncio.timeout(4):
        while True:
            pending = [item for item in bundle.authority.list_pending() if item.run_id == run]
            if pending:
                assert pending[0].arguments_json == json.dumps(
                    arguments, ensure_ascii=False, sort_keys=True, separators=(",", ":")
                )
                assert bundle.authority.decide(pending[0].approval_id, allow)
                break
            if task.done():
                return task.result()
            await asyncio.sleep(0)
        return await task


@pytest.mark.parametrize(
    "value",
    [
        "http://example.org/",
        "https://example.org:80/",
        "https://example.org:444/",
        "https://u:p@example.org/",
        "https://u@example.org/",
        "https://example.org/#fragment",
        "https://localhost/",
        "https://one.local/",
        "https://one.internal/",
        "https://one.lan/",
        "https://one.home.arpa/",
        "https://metadata.google.internal/",
        "https://example.org./",
        "https://ex_ample.org/",
        "https://éxample.org/",
        "https://example.org/a\\b",
        "https://example.org/\r\nCookie: bad",
        "https://example.org/%",
        "https://example.org/%qq",
        "https://127.0.0.1/",
        "https://169.254.169.254/",
        "https://10.0.0.1/",
        "https://[::1]/",
        "https://[::ffff:8.8.8.8]/",
        "https://[64:ff9b::808:808]/",
        "https://2130706433/",
        "https://127.000.000.001/",
        "https://example.org/a b",
        "https://example.org/\u202econceal",
        "https://example.org/" + "a" * 2048,
    ],
)
def test_untrusted_url_forms_are_rejected(value):
    with pytest.raises(module.WebError):
        module.parse_url(value)


@pytest.mark.parametrize(
    "address",
    [
        "0.0.0.0",
        "0.1.2.3",
        "127.0.0.1",
        "10.0.0.1",
        "172.16.0.1",
        "192.168.1.1",
        "169.254.169.254",
        "100.64.1.1",
        "192.0.0.8",
        "192.0.2.1",
        "198.18.0.1",
        "198.51.100.2",
        "203.0.113.2",
        "224.0.0.1",
        "255.255.255.255",
        "::",
        "::1",
        "fe80::1",
        "fc00::1",
        "ff02::1",
        "2001:db8::1",
        "2002:0808:0808::1",
        "2001::1",
        "64:ff9b::808:808",
        "64:ff9b:1::1",
        "::ffff:8.8.8.8",
        "3fff::1",
        "fe80::1%en0",
        "not-an-ip",
    ],
)
def test_private_special_transition_and_metadata_ip_forms_are_rejected(address):
    with pytest.raises(module.WebError):
        module.public_ip(address)


def test_public_url_canonicalization_does_not_change_the_approved_input():
    value = module.parse_url("https://EXAMPLE.ORG:443/café?q=uno&x=%2F")
    assert value.canonical == "https://example.org/caf%C3%A9?q=uno&x=%2F"
    assert value.hostname == value.host_header == "example.org"
    assert module.public_ip("8.8.8.8") == "8.8.8.8"
    assert module.public_ip("2606:4700:4700::1111") == "2606:4700:4700::1111"
    assert module.parse_url("https://[2606:4700:4700::1111]").host_header.startswith("[")


async def test_catalog_has_one_explicit_confirmation_and_no_provider_search(bundles):
    bundle = bundles()
    assert bundle.manager.definitions()[0].requires_confirmation
    assert len(bundle.catalog.specs()) == 1
    schema = bundle.catalog.specs()[0]["inputSchema"]
    assert schema["required"] == ["url"] and schema["additionalProperties"] is False
    assert (
        await approved(bundle, args={"url": URL, "headers": {"Cookie": "bad"}})
    ).error == "invalid_tool_arguments"


async def test_approval_denial_and_direct_call_never_resolve_or_connect(bundles):
    called = []

    async def resolver(host):
        called.append(host)
        raise AssertionError("DNS may not be called")

    bundle = bundles(resolver=resolver)
    assert (await approved(bundle, allow=False)).error == "approval_denied"
    result = await bundle.manager.definitions()[0].execute({"url": URL}, asyncio.Event())
    assert result.error == "web_authorization_invalidated" and not called


async def test_missing_central_continuation_api_fails_before_dns(bundles, monkeypatch):
    called = []

    async def resolver(host):
        called.append(host)
        raise AssertionError("DNS may not be called")

    monkeypatch.delattr(module.tool_authority, "revalidate_committed_tool_authority", raising=False)
    result = await approved(bundles(resolver=resolver))
    assert result.error == "web_authority_continuation_unavailable"
    assert result.effect_status == "known" and not called


async def test_plain_text_has_external_citation_digest_and_never_truth_verification(peers, bundles):
    body = "hola café".encode()
    peer = await peers(response(body))
    bundle = bundles(peer)
    result = await approved(bundle)
    assert result.succeeded and not result.verified
    data = json.loads(result.output)
    assert data["text"] == "hola café"
    assert data["body_sha256"] == hashlib.sha256(body).hexdigest()
    assert data["citation"] == {"url": URL, "body_sha256": data["body_sha256"]}
    assert data["verification"] == "external_untrusted" and data["verified"] is False
    assert data["retrieved_at_ns"] > 0 and data["body_bytes"] == len(body)
    assert data["javascript_executed"] is data["links_fetched"] is False
    assert peer.resolutions == ["fixture.example.org"]
    assert peer.connections == [(PUBLIC, "fixture.example.org")]


async def test_html_is_static_text_no_hidden_script_or_resource_request(peers, bundles):
    body = (
        b"<html><head><title> A &amp; B </title><style>body{bad}</style></head><body>"
        b"<h1>Hola</h1><script>fetch('https://secret.example.org')</script>"
        b"<div hidden>oculto</div><p aria-hidden='true'>privado</p>"
        b"<a href='/next'>Siguiente</a><a href='http://other.example.org/'>No HTTP</a>"
        b"<a href='https://127.0.0.1/'>No privado</a><img src='/pixel'>"
        b"<template>plantilla</template><svg>vector</svg></body></html>"
    )
    peer = await peers(response(body, headers={"Content-Type": "text/html"}))
    result = await approved(bundles(peer))
    assert result.succeeded
    data = json.loads(result.output)
    assert data["title"] == "A & B"
    assert data["text"] == "Hola Siguiente No HTTP No privado"
    assert data["links"] == ["https://fixture.example.org/next"]
    assert len(peer.requests) == 1


async def test_fixed_request_ignores_proxy_cookie_authorization_and_environment(
    peers, bundles, monkeypatch
):
    for key in ("HTTPS_PROXY", "https_proxy", "ALL_PROXY", "HTTP_PROXY"):
        monkeypatch.setenv(key, "https://user:password@127.0.0.1:1")
    monkeypatch.setenv("COOKIE", "secret")
    peer = await peers(response(headers={"Set-Cookie": "session=external"}))
    result = await approved(bundles(peer))
    assert result.succeeded
    request = peer.requests[0].decode()
    assert request == (
        "GET /start HTTP/1.1\r\nHost: fixture.example.org\r\n"
        "User-Agent: Jarvis/0.1\r\nAccept: text/html,text/plain,application/json\r\n"
        "Accept-Encoding: gzip\r\nConnection: close\r\n\r\n"
    )
    assert "secret" not in result.output and "session=" not in result.output


async def test_same_origin_redirect_reuses_pinned_ip_and_single_dns_and_approval(peers, bundles):
    async def reply(index, _reader, writer):
        if index < 4:
            raw = response(status=302, headers={"Location": f"/step-{index}"})
        else:
            raw = response(b"done")
        writer.write(raw)
        await writer.drain()

    peer = await peers(reply)
    result = await approved(bundles(peer))
    assert result.succeeded and json.loads(result.output)["text"] == "done"
    assert peer.resolutions == ["fixture.example.org"] and len(peer.requests) == 4
    assert peer.connections == [(PUBLIC, "fixture.example.org")] * 4
    assert len(json.loads(result.output)["redirects"]) == 3


async def test_cross_origin_redirect_returns_review_without_following(peers, bundles):
    peer = await peers(response(status=302, headers={"Location": "https://other.example.org/"}))
    result = await approved(bundles(peer))
    assert result.error == "web_redirect_review_needed" and not result.succeeded
    data = json.loads(result.output)
    assert data["review_needed"] and not data["followed"]
    assert data["redirect_url"] == "https://other.example.org/"
    assert len(peer.requests) == 1 and len(peer.connections) == 1


@pytest.mark.parametrize(
    ("location", "error"),
    [
        ("/start", "web_redirect_loop"),
        ("https://169.254.169.254/", "web_redirect_blocked"),
        ("http://fixture.example.org/", "web_redirect_blocked"),
        ("/next#fragment", "web_redirect_blocked"),
    ],
)
async def test_redirect_loop_and_unsafe_targets_never_follow(peers, bundles, location, error):
    peer = await peers(response(status=302, headers={"Location": location}))
    result = await approved(bundles(peer))
    assert result.error == error and len(peer.requests) == 1


async def test_redirect_limit_is_three_and_not_reset_on_each_connection(peers, bundles):
    async def reply(index, _reader, writer):
        writer.write(response(status=307, headers={"Location": f"/next-{index}"}))
        await writer.drain()

    peer = await peers(reply)
    result = await approved(bundles(peer))
    assert result.error == "web_redirect_limit" and len(peer.requests) == 4


async def test_mixed_public_private_dns_fails_before_any_tcp(peers, bundles):
    peer = await peers(response())

    async def resolver(_host):
        return (PUBLIC, "127.0.0.1")

    result = await approved(bundles(peer, resolver=resolver))
    assert result.error == "web_address_blocked" and not peer.connections


async def test_revocation_during_dns_prevents_connect_and_http(peers, bundles):
    entered, resume = asyncio.Event(), asyncio.Event()
    peer = await peers(response())

    async def resolver(_host):
        entered.set()
        await resume.wait()
        return (PUBLIC,)

    bundle = bundles(peer, resolver=resolver)
    task = asyncio.create_task(approved(bundle))
    await entered.wait()
    bundle.authority.revoke("synthetic-chat")
    resume.set()
    result = await task
    assert result.error == "web_authorization_invalidated"
    assert result.effect_status == "known" and not peer.connections and not peer.requests


async def test_revocation_after_tls_prevents_http_bytes_and_closes_owned_writer(peers, bundles):
    entered, resume = asyncio.Event(), asyncio.Event()
    peer = await peers(response())

    async def connector(address, hostname):
        streams = await peer.connector(address, hostname)
        entered.set()
        await resume.wait()
        return streams

    bundle = bundles(peer, connector=connector)
    task = asyncio.create_task(approved(bundle))
    await entered.wait()
    bundle.authority.revoke("synthetic-chat")
    resume.set()
    result = await task
    await asyncio.wait_for(peer.closed.wait(), 1)
    assert result.error == "web_authorization_invalidated" and result.effect_status == "known"
    assert not peer.requests and peer.partials == [b""]


async def test_revocation_between_redirects_blocks_the_next_connection(peers, bundles):
    entered, resume = asyncio.Event(), asyncio.Event()
    peer = await peers(response(status=302, headers={"Location": "/next"}))
    bundle = bundles(peer)
    real = bundle.manager._exchange

    async def exchange(*args, **kwargs):
        value = await real(*args, **kwargs)
        entered.set()
        await resume.wait()
        return value

    bundle.manager._exchange = exchange
    task = asyncio.create_task(approved(bundle))
    await entered.wait()
    bundle.authority.revoke("synthetic-chat")
    resume.set()
    result = await task
    assert result.error == "web_authorization_invalidated" and result.effect_status == "unknown"
    assert len(peer.connections) == len(peer.requests) == 1


async def test_revocation_after_response_prevents_returning_external_text(peers, bundles):
    entered, resume = asyncio.Event(), asyncio.Event()
    peer = await peers(response(b"untrusted-data"))
    bundle = bundles(peer)
    real = bundle.manager._exchange

    async def exchange(*args, **kwargs):
        value = await real(*args, **kwargs)
        entered.set()
        await resume.wait()
        return value

    bundle.manager._exchange = exchange
    task = asyncio.create_task(approved(bundle))
    await entered.wait()
    bundle.authority.revoke("synthetic-chat")
    resume.set()
    result = await task
    assert result.error == "web_authorization_invalidated" and not result.output
    assert result.effect_status == "unknown" and len(peer.requests) == 1


async def test_request_lost_ack_is_unknown_and_not_retried(peers, bundles):
    peer = await peers(b"")
    result = await approved(bundles(peer))
    assert result.error == "web_headers_incomplete" and result.effect_status == "unknown"
    assert len(peer.requests) == 1


@pytest.mark.parametrize(
    ("raw", "error"),
    [
        (
            b"HTTP/1.1 200 OK\r\nContent-Length: 1\r\nTransfer-Encoding: chunked\r\n\r\n",
            "web_framing_ambiguous",
        ),
        (
            b"HTTP/1.1 200 OK\r\nContent-Length: 1\r\nContent-Length: 1\r\n\r\nx",
            "web_headers_duplicate",
        ),
        (
            b"HTTP/1.1 200 OK\r\nContent-Type: text/plain\r\nContent-Type: text/html\r\n\r\n",
            "web_headers_duplicate",
        ),
        (b"HTTP/1.1 200 OK\r\n Content-Length: 0\r\n\r\n", "web_headers_invalid"),
        (b"HTTP/1.1 200 OK\r\nContent-Length: -1\r\n\r\n", "web_content_length_invalid"),
        (b"HTTP/1.1 200 OK\r\nBad Name: x\r\n\r\n", "web_headers_invalid"),
        (b"HTTP/1.1 200 OK\r\nX: \x00\r\n\r\n", "web_headers_invalid"),
        (b"HTTP/1.1 200 OK\nContent-Length: 0\n\n", "web_headers_incomplete"),
        (b"HTTP/2 200 OK\r\n\r\n", "web_status_invalid"),
        (b"HTTP/1.0 200 OK\r\nTransfer-Encoding: chunked\r\n\r\n", "web_framing_ambiguous"),
        (b"HTTP/1.1 101 Switching Protocols\r\n\r\n", "web_upgrade_blocked"),
        (b"HTTP/1.1 200 OK\r\nTransfer-Encoding:\r\n\r\n", "web_transfer_unsupported"),
        (b"HTTP/1.1 100 Continue\r\nContent-Length: 0\r\n\r\n", "web_body_forbidden"),
        (b"HTTP/1.1 100 Continue\r\n\r\n" * 4, "web_informational_limit"),
    ],
)
async def test_ambiguous_or_malformed_http_framing_is_rejected(peers, bundles, raw, error):
    peer = await peers(raw)
    result = await approved(bundles(peer))
    assert result.error == error and not result.succeeded
    assert len(peer.requests) == 1


@pytest.mark.parametrize(
    ("body", "error"),
    [
        (b"z\r\ninvalid\r\n", "web_chunk_invalid"),
        (b"2\r\na\r\n0\r\n\r\n", "web_chunk_invalid"),
        (b"3\r\nab", "web_body_incomplete"),
        (b"0\r\nContent-Length: 3\r\n\r\n", "web_trailer_invalid"),
        (b"1;=bad\r\nx\r\n0\r\n\r\n", "web_chunk_invalid"),
        (b'1;ext="unterminated\r\nx\r\n0\r\n\r\n', "web_chunk_invalid"),
    ],
)
async def test_invalid_chunks_and_framing_trailers_are_rejected(peers, bundles, body, error):
    raw = response(body, headers={"Transfer-Encoding": "chunked"}, length=False)
    result = await approved(bundles(await peers(raw)))
    assert result.error == error


async def test_valid_chunk_extensions_and_ignored_safe_trailer(peers, bundles):
    raw = response(
        b'3;label="a;b"\r\nuno\r\n3;flag\r\ndos\r\n0\r\nX-Info: data\r\n\r\n',
        headers={"Transfer-Encoding": "chunked"},
        length=False,
    )
    result = await approved(bundles(await peers(raw)))
    assert result.succeeded and json.loads(result.output)["text"] == "unodos"


async def test_eof_framing_informational_headers_and_http_error_are_untrusted_data(peers, bundles):
    raw = b"HTTP/1.1 100 Continue\r\nX-Info: data\r\n\r\n" + response(
        b"not found",
        status=404,
        length=False,
    )
    result = await approved(bundles(await peers(raw)))
    assert result.error == "web_http_status" and not result.succeeded and not result.verified
    assert json.loads(result.output)["text"] == "not found"


@pytest.mark.parametrize("status", [204, 205, 304])
async def test_no_body_status_rejects_ambiguous_framing(peers, bundles, status):
    raw = response(
        b"0\r\n\r\n",
        status=status,
        headers={"Transfer-Encoding": "chunked"},
        length=False,
    )
    result = await approved(bundles(await peers(raw)))
    assert result.error == "web_body_forbidden"


async def test_no_content_status_has_empty_unverified_text(peers, bundles):
    result = await approved(bundles(await peers(response(b"", status=204))))
    assert result.succeeded and not result.verified and json.loads(result.output)["text"] == ""


@pytest.mark.parametrize(
    ("raw", "error"),
    [
        (response(b"x" * 1025), "web_body_limit"),
        (response(b"x" * 1025, length=False), "web_body_limit"),
        (
            response(
                b"401\r\n" + b"x" * 1025 + b"\r\n0\r\n\r\n",
                headers={"Transfer-Encoding": "chunked"},
                length=False,
            ),
            "web_body_limit",
        ),
        (
            b"HTTP/1.1 200 OK\r\nContent-Type: text/plain\r\nContent-Length: 5\r\n\r\nabc",
            "web_body_incomplete",
        ),
        (b"HTTP/1.1 200 OK\r\nX: " + b"a" * 9000 + b"\r\n\r\n", "web_headers_invalid"),
        (
            b"HTTP/1.1 200 OK\r\n" + (b"X: " + b"a" * 2000 + b"\r\n") * 20 + b"\r\n",
            "web_headers_incomplete",
        ),
        (
            response(b"x", headers={"Content-Type": "application/octet-stream"}),
            "web_content_type_unsupported",
        ),
        (response(b"x", headers={"Content-Encoding": "br"}), "web_content_encoding_unsupported"),
    ],
)
async def test_body_headers_download_and_encoding_limits(peers, bundles, raw, error):
    result = await approved(bundles(await peers(raw), max_body_bytes=1024))
    assert result.error == error and not result.succeeded


async def test_gzip_body_is_decoded_bounded_and_hashed_after_decode(peers, bundles):
    body = b"contenido comprimido" * 30
    peer = await peers(response(gzip.compress(body), headers={"Content-Encoding": "gzip"}))
    result = await approved(bundles(peer))
    assert result.succeeded
    data = json.loads(result.output)
    assert data["body_sha256"] == hashlib.sha256(body).hexdigest()
    assert data["text"] == body.decode()


@pytest.mark.parametrize(
    ("body", "error"),
    [
        (gzip.compress(b"x" * 2048), "web_decoded_body_limit"),
        (gzip.compress(b"abc")[:-3], "web_gzip_invalid"),
        (gzip.compress(b"abc") + gzip.compress(b"def"), "web_gzip_invalid"),
        (b"not gzip", "web_gzip_invalid"),
    ],
)
async def test_gzip_bomb_truncation_and_ambiguous_members_fail(peers, bundles, body, error):
    raw = response(body, headers={"Content-Encoding": "gzip"})
    result = await approved(bundles(await peers(raw), max_body_bytes=1024))
    assert result.error == error


async def test_output_utf8_and_json_escaping_budgets_and_truncation(peers, bundles):
    body = ('"\\\n漢🙂' * 12000).encode()
    result = await approved(bundles(await peers(response(body)), max_output_bytes=4096))
    assert result.succeeded and len(result.output.encode()) <= 4096
    data = json.loads(result.output)
    assert data["text_truncated"] and data["body_bytes"] == len(body)
    assert data["body_sha256"] == hashlib.sha256(body).hexdigest()


async def test_html_depth_nodes_and_link_budgets_are_finite(peers, bundles):
    html = b"<div>" * 150 + b"deep" + b"</div>" * 150
    raw = response(html, headers={"Content-Type": "text/html"})
    result = await approved(bundles(await peers(raw)))
    assert result.succeeded and json.loads(result.output)["text_truncated"]
    html = b"".join(f"<a href='/link-{n}'>link</a>".encode() for n in range(300))
    result = await approved(
        bundles(await peers(response(html, headers={"Content-Type": "text/html"})))
    )
    data = json.loads(result.output)
    assert len(data["links"]) == 12 and data["links_truncated"]


async def test_single_total_deadline_stops_slow_drip_without_retry(peers, bundles):
    async def reply(_index, _reader, writer):
        writer.write(b"HTTP/1.1 200 OK\r\nContent-Type: text/plain\r\nContent-Length: 20\r\n\r\n")
        for _ in range(20):
            writer.write(b"x")
            await writer.drain()
            await asyncio.sleep(0.04)

    peer = await peers(reply)
    start = asyncio.get_running_loop().time()
    result = await approved(bundles(peer, timeout=0.15))
    elapsed = asyncio.get_running_loop().time() - start
    assert result.error == "web_timeout" and result.effect_status == "unknown"
    assert elapsed < 1 and len(peer.requests) == 1


async def test_late_resolver_cannot_dispatch_after_total_deadline(peers, bundles):
    peer = await peers(response())

    async def resolver(_host):
        try:
            await asyncio.sleep(1)
        except asyncio.CancelledError:
            pass
        return (PUBLIC,)

    result = await approved(bundles(peer, resolver=resolver, timeout=0.05))
    assert result.error == "web_timeout" and result.effect_status == "known"
    assert not peer.connections


async def test_late_connector_cannot_send_after_total_deadline(peers, bundles):
    peer = await peers(response())

    async def connector(address, hostname):
        streams = await peer.connector(address, hostname)
        try:
            await asyncio.sleep(1)
        except asyncio.CancelledError:
            pass
        return streams

    result = await approved(bundles(peer, connector=connector, timeout=0.08))
    assert result.error == "web_timeout" and result.effect_status == "known"
    await asyncio.wait_for(peer.closed.wait(), 1)
    assert not peer.requests and peer.partials == [b""]


async def test_cancellation_closes_peer_and_cleans_tasks_without_replay(peers, bundles):
    async def reply(_index, reader, _writer):
        await reader.read()

    peer = await peers(reply)
    bundle, cancellation = bundles(peer), asyncio.Event()
    task = asyncio.create_task(approved(bundle, cancellation=cancellation))
    await peer.request_received.wait()
    cancellation.set()
    with pytest.raises(ToolCancelled):
        await task
    await asyncio.wait_for(peer.closed.wait(), 1)
    assert not bundle.manager._tasks and len(peer.requests) == 1


async def test_manager_close_cancels_owned_connections_and_rejects_new_fetch(peers, bundles):
    async def reply(_index, reader, _writer):
        await reader.read()

    peer = await peers(reply)
    bundle = bundles(peer)
    task = asyncio.create_task(approved(bundle))
    await peer.request_received.wait()
    await bundle.manager.aclose()
    with pytest.raises(ToolCancelled) as cancelled:
        await task
    assert cancelled.value.receipt.effect_status == "unknown"
    await asyncio.wait_for(peer.closed.wait(), 1)
    assert (await approved(bundle)).error == "web_closed"
    assert not bundle.manager._tasks and len(peer.requests) == 1


@pytest.mark.parametrize("stage", ["dns", "tls"])
async def test_close_latches_before_late_dns_or_tls_callback_can_dispatch(peers, bundles, stage):
    entered, cancelled, release = asyncio.Event(), asyncio.Event(), asyncio.Event()
    peer = await peers(response())

    async def return_late():
        entered.set()
        try:
            await release.wait()
        except asyncio.CancelledError:
            cancelled.set()
            await release.wait()

    async def resolver(host):
        if stage == "dns":
            await return_late()
        return await peer.resolver(host)

    async def connector(address, hostname):
        streams = await peer.connector(address, hostname)
        if stage == "tls":
            await return_late()
        return streams

    bundle = bundles(peer, resolver=resolver, connector=connector)
    receipts = []
    definition = bundle.manager.definitions()[0]

    async def observed(args, cancellation):
        try:
            return await definition.execute(args, cancellation)
        except ToolCancelled as error:
            # Observe the adapter's receipt inside the same real central lease.
            # ToolCatalog conservatively wraps cancellation as unknown itself.
            receipts.append(error.receipt)
            raise

    bundle.catalog = ToolCatalog(
        (replace(definition, execute=observed),),
        bundle.manager.specs(),
        authority=bundle.authority,
    )
    request = asyncio.create_task(approved(bundle))
    closing = None
    try:
        await asyncio.wait_for(entered.wait(), 3)
        closing = asyncio.create_task(bundle.manager.aclose())
        await asyncio.wait_for(cancelled.wait(), 3)
        assert bundle.manager._closed and not closing.done()
        release.set()
        await asyncio.wait_for(closing, 3)
        with pytest.raises(ToolCancelled):
            await asyncio.wait_for(request, 3)
        assert len(receipts) == 1
        assert receipts[0].error == "web_cancelled" and receipts[0].effect_status == "known"
        assert not peer.requests and not bundle.manager._tasks
        if stage == "dns":
            assert not peer.connections
        else:
            await asyncio.wait_for(peer.closed.wait(), 1)
            assert peer.partials == [b""]
    finally:
        release.set()
        for task in (closing, request):
            if task is not None and not task.done():
                task.cancel()
        await asyncio.gather(*filter(None, (closing, request)), return_exceptions=True)


@pytest.mark.parametrize("stage", ["dns", "tls"])
async def test_direct_task_cancel_latches_only_its_operation_before_late_callback(
    peers, bundles, stage
):
    entered, cancelled, release = asyncio.Event(), asyncio.Event(), asyncio.Event()
    host_cancellation = asyncio.Event()
    peer = await peers(response())
    delayed = False

    async def return_late():
        nonlocal delayed
        delayed = True
        entered.set()
        try:
            await release.wait()
        except asyncio.CancelledError:
            cancelled.set()
            await release.wait()

    async def resolver(host):
        if stage == "dns" and not delayed:
            await return_late()
        return await peer.resolver(host)

    async def connector(address, hostname):
        streams = await peer.connector(address, hostname)
        if stage == "tls" and not delayed:
            await return_late()
        return streams

    bundle = bundles(peer, resolver=resolver, connector=connector)
    receipts = []
    definition = bundle.manager.definitions()[0]

    async def observed(args, cancellation):
        try:
            return await definition.execute(args, cancellation)
        except ToolCancelled as error:
            receipts.append(error.receipt)
            raise

    bundle.catalog = ToolCatalog(
        (replace(definition, execute=observed),),
        bundle.manager.specs(),
        authority=bundle.authority,
    )
    request = asyncio.create_task(approved(bundle, cancellation=host_cancellation))
    try:
        await asyncio.wait_for(entered.wait(), 3)
        request.cancel()
        await asyncio.wait_for(cancelled.wait(), 3)
        assert not host_cancellation.is_set() and not bundle.manager._closed
        # The latch is local, not revocation of the session or another operation.
        unaffected = await approved(bundle, "https://fixture.example.org/unrelated")
        assert unaffected.succeeded
        release.set()
        with pytest.raises(ToolCancelled):
            await asyncio.wait_for(request, 3)
        assert len(receipts) == 1 and receipts[0].effect_status == "known"
        assert receipts[0].error == "web_cancelled"
        assert not host_cancellation.is_set() and not bundle.manager._tasks
        assert len(peer.requests) == 1 and peer.requests[0].startswith(b"GET /unrelated ")
        assert len(peer.connections) == (1 if stage == "dns" else 2)
        if stage == "tls":
            async with asyncio.timeout(1):
                while not peer.partials:
                    await asyncio.sleep(0)
            assert peer.partials == [b""]
    finally:
        release.set()
        if not request.done():
            request.cancel()
        await asyncio.gather(request, return_exceptions=True)


async def test_capacity_does_not_start_a_fifth_dns_and_cleanup_is_finite(bundles):
    entered, resume = [], asyncio.Event()

    async def resolver(host):
        entered.append(host)
        await resume.wait()
        return ("127.0.0.1",)

    bundle = bundles(resolver=resolver)
    tasks = [asyncio.create_task(approved(bundle)) for _ in range(4)]
    async with asyncio.timeout(1):
        while len(entered) < 4:
            await asyncio.sleep(0)
    result = await approved(bundle)
    assert result.error == "web_capacity" and len(entered) == 4
    resume.set()
    assert all(item.error == "web_address_blocked" for item in await asyncio.gather(*tasks))
    assert not bundle.manager._tasks


def test_tls_defaults_ignore_ambient_ca_override_and_never_keylog(tmp_path, monkeypatch):
    keylog = tmp_path / "must-not-exist"
    monkeypatch.setenv("SSLKEYLOGFILE", str(keylog))
    monkeypatch.setenv("SSL_CERT_FILE", str(tmp_path / "wrong-ca"))
    monkeypatch.setenv("SSL_CERT_DIR", str(tmp_path / "wrong-dir"))
    context = module.tls_context()
    assert context.check_hostname and context.verify_mode == ssl.CERT_REQUIRED
    assert context.minimum_version >= ssl.TLSVersion.TLSv1_2
    assert context.keylog_filename is None and not keylog.exists()


async def test_default_connector_uses_numeric_public_ip_tls_sni_and_no_system_proxy(monkeypatch):
    calls = []

    async def open_connection(**options):
        calls.append(options)
        return "reader", "writer"

    monkeypatch.setattr(asyncio, "open_connection", open_connection)
    assert await module.connect_pinned(PUBLIC, "fixture.example.org") == ("reader", "writer")
    options = calls[0]
    assert options["host"] == PUBLIC and options["port"] == 443
    assert options["server_hostname"] == "fixture.example.org"
    assert options["flags"] == socket.AI_NUMERICHOST
    assert options["ssl"].check_hostname and options["ssl"].verify_mode == ssl.CERT_REQUIRED


async def test_real_tls_hostname_mismatch_never_delivers_http(peers, bundles, certificates):
    peer = await peers(response())

    async def connector(_address, _hostname):
        return await asyncio.open_connection(
            "127.0.0.1",
            peer.port,
            ssl=certificates[1],
            server_hostname="wrong.example.org",
            ssl_handshake_timeout=1,
            ssl_shutdown_timeout=0.1,
        )

    result = await approved(bundles(peer, connector=connector))
    assert result.error == "web_transport_failed" and result.effect_status == "known"
    assert not peer.requests


async def test_real_system_resolver_rows_are_all_validated_without_real_dns(monkeypatch):
    calls = []

    async def resolve(host, port, **options):
        calls.append((host, port, options))
        return [
            (socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", (PUBLIC, 443)),
            (socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", (PUBLIC, 443)),
        ]

    monkeypatch.setattr(asyncio.get_running_loop(), "getaddrinfo", resolve)
    assert await module.resolve_public("fixture.example.org") == (PUBLIC,)
    assert len(calls) == 1 and calls[0][1] == 443
    assert await module.resolve_public("8.8.8.8") == ("8.8.8.8",)
    assert len(calls) == 1


@pytest.mark.parametrize(
    "configuration",
    [
        {"timeout": 0.01},
        {"timeout": 21},
        {"timeout": True},
        {"timeout": float("nan")},
        {"max_body_bytes": 2 * 1024 * 1024 + 1},
        {"max_body_bytes": True},
        {"max_output_bytes": 24001},
        {"max_output_bytes": 4000},
        {"max_redirects": 4},
    ],
)
def test_host_configuration_cannot_remove_bounds(configuration):
    with pytest.raises(module.WebError):
        module.WebFetchTools(**configuration)
