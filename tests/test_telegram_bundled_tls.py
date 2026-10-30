"""Every Telegram transport consumes the same admitted, nonambient trust bytes."""

import asyncio
import ssl
import traceback
from types import SimpleNamespace

import pytest
from test_bundled_tls import certificate
from test_telegram import TOKEN

import jarvis_core.telegram as telegram
import jarvis_core.telegram_files as telegram_files
import jarvis_core.web as web

_KINDS = ["api_get", "api_post", "download", "document", "voice"]


class Writer:
    def __init__(self):
        self.writes, self.closed = 0, False

    def write(self, _data):
        self.writes += 1

    async def drain(self):
        pass

    def close(self):
        self.closed = True

    async def wait_closed(self):
        pass


async def request(kind, connector, dispatch):
    if kind in {"api_get", "api_post"}:
        method, api = ("GET", "getMe") if kind == "api_get" else ("POST", "sendMessage")
        return await telegram.TelegramHTTPTransport(connector=connector)(
            method, api, {}, TOKEN, 2, dispatch)
    if kind == "download":
        return await telegram_files.TelegramFileTransport(connector=connector)(
            "voice/owned-fixture.ogg", TOKEN, 2, dispatch)
    transport = (telegram_files.TelegramVoiceTransport if kind == "voice"
                 else telegram_files.TelegramDocumentTransport)
    incoming = SimpleNamespace(chat_id=111, message_id=22, message_thread_id=None)
    # This only tests TLS construction in the transport. Real OPUS admission
    # belongs to the separately tested client/codec before this boundary.
    return await transport(connector=connector)(
        incoming, b"OggS_OWNED_TLS_FIXTURE", "owned.ogg", "audio/ogg", "", TOKEN, 2, dispatch)


@pytest.mark.parametrize("kind", _KINDS)
async def test_all_telegram_transports_use_explicit_ca_without_host_or_ambient_fallback(
    kind, tmp_path, monkeypatch,
):
    monkeypatch.setattr(web, "_bundled_ca_pem", None)
    keylog = tmp_path / "must-not-be-created"
    monkeypatch.setenv("SSL_CERT_FILE", str(tmp_path / "ambient-ca-must-not-be-read"))
    monkeypatch.setenv("SSL_CERT_DIR", str(tmp_path / "ambient-dir-must-not-be-read"))
    monkeypatch.setenv("SSLKEYLOGFILE", str(keylog))
    web.configure_bundled_tls(certificate())

    def forbidden(*_, **__):
        pytest.fail("Telegram attempted ambient or host-default TLS construction")

    monkeypatch.setattr(ssl, "create_default_context", forbidden)
    monkeypatch.setattr(ssl, "get_default_verify_paths", forbidden)
    writer, connections, dispatches = Writer(), [], []

    async def connector(host, port, **options):
        context = options["ssl"]
        assert host == options["server_hostname"] == "api.telegram.org" and port == 443
        assert context.check_hostname and context.verify_mode == ssl.CERT_REQUIRED
        assert context.minimum_version >= ssl.TLSVersion.TLSv1_2
        assert context.cert_store_stats()["x509_ca"] == 1
        assert context.keylog_filename is None
        connections.append(1)
        reader = asyncio.StreamReader()
        reader.feed_data(b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\n\r\n{}")
        reader.feed_eof()
        return reader, writer

    await request(kind, connector, lambda: dispatches.append(1))
    assert connections == dispatches == [1]
    assert writer.writes == 1 and writer.closed and not keylog.exists()


@pytest.mark.parametrize("kind", _KINDS)
async def test_missing_tls_trust_fails_before_connection_or_effect(kind, monkeypatch):
    connections, dispatches = [], []

    def unavailable():
        raise web.WebError("web_tls_trust_unavailable")

    async def connector(*_, **__):
        connections.append(1)
        pytest.fail("connection made without trusted CA")

    monkeypatch.setattr(telegram if kind.startswith("api_") else telegram_files,
                        "tls_context", unavailable)
    with pytest.raises(telegram.TelegramError) as caught:
        await request(kind, connector, lambda: dispatches.append(1))
    assert caught.value.effect_status == "known"
    assert connections == dispatches == []
    assert TOKEN not in str(caught.value)


@pytest.mark.parametrize("kind", _KINDS)
async def test_tls_handshake_failure_is_known_and_never_exposes_token_or_peer_exception(
    kind, monkeypatch,
):
    monkeypatch.setattr(web, "_bundled_ca_pem", None)
    web.configure_bundled_tls(certificate())
    connections, dispatches = [], []
    private_text = "UNTRUSTED_PEER_DETAILS_" + TOKEN

    async def connector(*_, **__):
        connections.append(1)
        raise ssl.SSLCertVerificationError(private_text)

    with pytest.raises(telegram.TelegramError) as caught:
        await request(kind, connector, lambda: dispatches.append(1))
    assert caught.value.effect_status == "known"
    assert connections == [1] and dispatches == []
    visible = "".join(traceback.format_exception(caught.value))
    assert TOKEN not in visible and private_text not in visible
    assert caught.value.__suppress_context__ is True
