"""Distribution trust is explicit and cannot come from ambient TLS variables."""

import datetime
import ssl

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID
from test_codex import executable as executable

import jarvis_core.codex as codex
import jarvis_core.web as web


def certificate():
    key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "Jarvis synthetic CI CA")])
    now = datetime.datetime.now(datetime.UTC)
    value = (x509.CertificateBuilder().subject_name(name).issuer_name(name)
             .public_key(key.public_key()).serial_number(x509.random_serial_number())
             .not_valid_before(now - datetime.timedelta(minutes=1))
             .not_valid_after(now + datetime.timedelta(days=1))
             .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
             .sign(key, hashes.SHA256()))
    return value.public_bytes(serialization.Encoding.PEM)


def test_explicit_ca_bytes_work_without_any_openssl_host_ca(tmp_path, monkeypatch):
    monkeypatch.setattr(web, "_bundled_ca_pem", None)
    keylog = tmp_path / "never-keylog"
    monkeypatch.setenv("SSLKEYLOGFILE", str(keylog))
    monkeypatch.setenv("SSL_CERT_FILE", str(tmp_path / "ambient"))
    pem = certificate()
    web.configure_bundled_tls(pem)
    monkeypatch.setattr(ssl, "get_default_verify_paths", lambda: pytest.fail("host CA fallback"))
    context = web.tls_context()
    assert context.cert_store_stats()["x509_ca"] == 1
    assert context.check_hostname and context.verify_mode == ssl.CERT_REQUIRED
    assert context.keylog_filename is None and not keylog.exists()
    web.configure_bundled_tls(pem)  # same immutable admission is idempotent
    with pytest.raises(web.WebError, match="web_tls_trust_unavailable"):
        web.configure_bundled_tls(certificate())


@pytest.mark.parametrize("payload", [b"", b"not a CA", b"a" * (512 * 1024 + 1), "text"])
def test_bad_distribution_ca_does_not_install_or_enable_fallback(monkeypatch, payload):
    monkeypatch.setattr(web, "_bundled_ca_pem", None)
    with pytest.raises(web.WebError, match="web_tls_trust_unavailable"):
        web.configure_bundled_tls(payload)
    assert web._bundled_ca_pem is None


@pytest.mark.parametrize("explicit", [True, False])
async def test_codex_receives_only_explicit_distribution_ca(tmp_path, monkeypatch, executable,
                                                           explicit):
    recorded = []
    original = codex.asyncio.create_subprocess_exec

    async def spawn(*args, **kwargs):
        recorded.append(kwargs["env"])
        return await original(*args, **kwargs)

    monkeypatch.setattr(codex.asyncio, "create_subprocess_exec", spawn)
    monkeypatch.setenv("SSL_CERT_FILE", str(tmp_path / "ambient-do-not-use"))
    monkeypatch.setenv("SSLKEYLOGFILE", str(tmp_path / "never-log"))
    path = tmp_path / "distribution.pem" if explicit else None
    client = codex.CodexClient(executable, tmp_path / "provider", certificate_bundle=path)
    try:
        assert (await client.account_status())["connected"]
    finally:
        await client.aclose()
    assert len(recorded) == 1
    assert recorded[0].get("SSL_CERT_FILE") == (str(path) if explicit else None)
    assert "SSLKEYLOGFILE" not in recorded[0]
