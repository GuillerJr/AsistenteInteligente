"""Explicit test-only desktop launch admission; productive IPC stays signed.

Each test owns a fresh synthetic key, and passes only its public launch pin to
JarvisService. No owner key, account, history or installed app is consulted.
Generic transport parsing tests deliberately omit ClientAuthority and continue
to exercise their separately declared bearer-only protocol fixture.
"""

import pytest

from jarvis_core.client_identity import ClientIdentity


@pytest.fixture(autouse=True)
def signed_fixture_client(monkeypatch, tmp_path_factory):
    identity = ClientIdentity.generate()
    # Credentials belong to the test harness, never the corpus a test may
    # inventory, package or add to its own synthetic Git repository.
    path = tmp_path_factory.mktemp("jarvis-synthetic-client") / "synthetic-client.key"
    path.write_bytes(identity.private_key.private_bytes_raw())
    path.chmod(0o600)
    monkeypatch.setenv("JARVIS_RPC_BOOTSTRAP_PUBLIC_KEY", identity.public_key)
    monkeypatch.setenv("JARVIS_RPC_CLIENT_KEY_FILE", str(path))
