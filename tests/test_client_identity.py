"""Admitted synthetic clients over real sockets; no owner credentials or accounts."""

import base64
import json
import os
import shutil
import socket
import sys
import tempfile
from pathlib import Path

import pytest

from jarvis_core.client_identity import (
    ClientAuthority,
    ClientIdentity,
    ClientIdentityError,
    client_directory,
)
from jarvis_core.host_profile import ProfileLease
from jarvis_core.private_json import PrivateJsonError
from jarvis_core.protocol import LocalRpcClient, LocalRpcServer, RpcError, TransportSecurityError


@pytest.fixture
def profile():
    base = "/private/tmp" if sys.platform == "darwin" else tempfile.gettempdir()
    with tempfile.TemporaryDirectory(prefix="jvs-id-", dir=base) as value:
        path = Path(value).resolve()
        root = path / "profile"
        root.mkdir(mode=0o700)
        yield root


def authority(profile, identity):
    return ClientAuthority(profile, bootstrap_public_key=identity.public_key)


def admit(registry, owner, other, scope="read"):
    revision = registry.status(owner.client_id)["revision"]
    review = registry.review(
        owner.client_id,
        {"public_key": other.public_key, "scope": scope, "expected_revision": revision},
    )
    return registry.admit(
        owner.client_id, {key: review[key] for key in ("review_id", "review_digest")}
    )


def signed(server, identity, challenge, *, sequence=1, method="echo", params=None, request_id=1):
    body = json.dumps(
        {"jsonrpc": "2.0", "id": request_id, "method": method, "params": params or {}},
        separators=(",", ":"),
    ).encode()
    return {
        "jsonrpc": "2.0",
        "id": request_id,
        "method": "client.request",
        "auth": server.token_path.read_text(),
        "params": {
            "body": base64.b64encode(body).decode(),
            "sequence": sequence,
            "signature": identity.sign(challenge, sequence, body),
        },
    }


async def dispatch(server, request, state):
    return await server._dispatch(json.dumps(request).encode() + b"\n", state)


async def test_two_admitted_identities_exact_requests_scopes_and_revocation(profile):
    owner, reader = ClientIdentity.generate(), ClientIdentity.generate()
    registry = authority(profile, owner)
    calls = []

    async def echo(params):
        calls.append(params)
        return params

    async with LocalRpcServer(
        profile, {"echo": echo}, client_authority=registry, read_methods=frozenset({"echo"})
    ):
        async with LocalRpcClient(profile, identity=owner) as one:
            review = await one.call(
                "clients.review",
                {"public_key": reader.public_key, "scope": "read", "expected_revision": 1},
            )
            result = await one.call(
                "clients.admit", {key: review[key] for key in ("review_id", "review_digest")}
            )
            assert result["revision"] == 2
            async with LocalRpcClient(profile, identity=reader) as two:
                assert await two.call("echo", {"texto": "dos clientes"}) == {
                    "texto": "dos clientes"
                }
                with pytest.raises(RpcError) as denied:
                    await two.call("clients.status")
                assert denied.value.code == -32003
                assert (await two.call("health"))["service"] == "jarvis"
                await one.call(
                    "clients.revoke", {"client_id": reader.client_id, "expected_revision": 2}
                )
                with pytest.raises(RpcError) as revoked:
                    await two.call("echo", {"no": "ejecutar"})
                assert revoked.value.code == -32001
    assert calls == [{"texto": "dos clientes"}]
    with pytest.raises(ClientIdentityError, match="client_not_admitted"):
        ClientAuthority(profile, bootstrap_public_key=reader.public_key)
    assert (
        ClientAuthority(profile, bootstrap_public_key=owner.public_key).status(owner.client_id)[
            "revision"
        ]
        == 3
    )


async def test_discovery_token_alone_never_dispatches_productive_handler(profile):
    owner = ClientIdentity.generate()
    calls = []

    async def effect(params):
        calls.append(params)

    async with LocalRpcServer(
        profile, {"effect": effect}, client_authority=authority(profile, owner)
    ) as server:
        response = await dispatch(
            server,
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "effect",
                "params": {},
                "auth": server.token_path.read_text(),
            },
            {},
        )
        assert response["error"]["code"] == -32001
        impostor = ClientIdentity.generate()
        with pytest.raises(RpcError):
            async with LocalRpcClient(profile, identity=impostor):
                pass
    assert calls == []


@pytest.mark.parametrize(
    "corruption", ["body", "signature", "counter", "nonce", "server", "profile"]
)
async def test_signature_binds_exact_body_counter_nonce_server_and_profile(profile, corruption):
    owner = ClientIdentity.generate()
    registry = authority(profile, owner)
    calls = []

    async def echo(params):
        calls.append(params)
        return params

    async with LocalRpcServer(profile, {"echo": echo}, client_authority=registry) as server:
        challenge = registry.challenge(owner.client_id)
        claimed = dict(challenge)
        if corruption in {"nonce", "server", "profile"}:
            claimed[
                {"nonce": "nonce", "server": "server_id", "profile": "profile_binding"}[corruption]
            ] = "f" * 64
        request = signed(server, owner, claimed, params={"original": True})
        if corruption == "body":
            body = json.loads(base64.b64decode(request["params"]["body"]))
            body["params"] = {"alterado": True}
            request["params"]["body"] = base64.b64encode(json.dumps(body).encode()).decode()
        elif corruption == "signature":
            request["params"]["signature"] = "0" * 128
        elif corruption == "counter":
            request["params"]["sequence"] = 2
        state = {"challenge": challenge, "sequence": 0}
        assert (await dispatch(server, request, state))["error"]["code"] == -32001
        assert calls == [] and state["sequence"] == 0
        valid = signed(server, owner, challenge, params={"original": True})
        assert (await dispatch(server, valid, state))["result"] == {"original": True}
        assert (await dispatch(server, valid, state))["error"]["code"] == -32001
        assert calls == [{"original": True}]


async def test_nonce_cannot_move_to_another_connection_or_service_restart(profile):
    owner = ClientIdentity.generate()
    registry = authority(profile, owner)

    async def echo(params):
        return params

    async with LocalRpcServer(profile, {"echo": echo}, client_authority=registry) as server:
        one, two = registry.challenge(owner.client_id), registry.challenge(owner.client_id)
        request = signed(server, owner, one)
        assert (await dispatch(server, request, {"challenge": two, "sequence": 0}))["error"][
            "code"
        ] == -32001
    registry = authority(profile, owner)
    async with LocalRpcServer(profile, {"echo": echo}, client_authority=registry) as server:
        assert (
            await dispatch(
                server, request, {"challenge": registry.challenge(owner.client_id), "sequence": 0}
            )
        )["error"]["code"] == -32001


async def test_locally_oversized_signed_request_does_not_consume_signature_sequence(profile):
    owner = ClientIdentity.generate()
    calls = []

    async def echo(params):
        calls.append(params)
        return params

    async with LocalRpcServer(profile, {"echo": echo}, client_authority=authority(profile, owner)):
        async with LocalRpcClient(profile, identity=owner, max_frame_bytes=1024) as client:
            with pytest.raises(RpcError) as error:
                await client.call("echo", {"large": "X" * 2000})
            assert error.value.code == -32004
            assert await client.call("echo", {"after": "valid"}) == {"after": "valid"}
    assert calls == [{"after": "valid"}]


@pytest.mark.parametrize("failure", ["digest", "expired", "revision", "different_owner"])
def test_pairing_review_is_owner_bound_finite_and_cas(profile, failure):
    owner, second, pending = [ClientIdentity.generate() for _ in range(3)]
    now = [0.0]
    registry = ClientAuthority(profile, bootstrap_public_key=owner.public_key, clock=lambda: now[0])
    admit(registry, owner, second, "owner")
    review = registry.review(
        owner.client_id, {"public_key": pending.public_key, "scope": "read", "expected_revision": 2}
    )
    params = {key: review[key] for key in ("review_id", "review_digest")}
    caller = owner.client_id
    if failure == "digest":
        params["review_digest"] = "f" * 64
    elif failure == "expired":
        now[0] = 120.0
    elif failure == "revision":
        registry.revoke(owner.client_id, {"client_id": second.client_id, "expected_revision": 2})
    elif failure == "different_owner":
        caller = second.client_id
    with pytest.raises(ClientIdentityError):
        registry.admit(caller, params)
    with pytest.raises(ClientIdentityError):
        registry.lookup(pending.client_id)
    with pytest.raises(ClientIdentityError):
        registry.admit(owner.client_id, params)


def test_last_owner_cannot_be_removed_and_profile_copy_is_not_a_credential(profile):
    owner = ClientIdentity.generate()
    registry = authority(profile, owner)
    with pytest.raises(ClientIdentityError, match="client_last_owner"):
        registry.revoke(owner.client_id, {"client_id": owner.client_id, "expected_revision": 1})
    copy = profile.with_name("copied-profile")
    copy.mkdir(mode=0o700)
    shutil.copytree(client_directory(profile), client_directory(copy))
    with pytest.raises(PrivateJsonError, match="client_registry_invalid"):
        ClientAuthority(copy, bootstrap_public_key=owner.public_key)


@pytest.mark.parametrize("kind", ["symlink", "permissions", "hardlink", "wrong_size"])
def test_private_identity_rejects_unsafe_files(profile, kind):
    path = profile / "identity.key"
    path.write_bytes(ClientIdentity.generate().private_key.private_bytes_raw())
    path.chmod(0o600)
    if kind == "symlink":
        alternate = profile / "alternate.key"
        path.rename(alternate)
        path.symlink_to(alternate)
    elif kind == "permissions":
        path.chmod(0o644)
    elif kind == "hardlink":
        os.link(path, profile / "second.key")
    elif kind == "wrong_size":
        path.write_bytes(b"x" * 31)
    with pytest.raises((ClientIdentityError, OSError)):
        ClientIdentity.load(path)


async def test_stale_socket_recovers_under_real_exclusive_profile_lease(profile):
    dead = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    dead.bind(str(profile / "service.sock"))
    (profile / "service.sock").chmod(0o600)
    dead.close()
    lease = ProfileLease.acquire(profile)
    try:
        async with LocalRpcServer(profile, {}, recover_stale=lease.assert_current) as server:
            assert server.recovered_stale_socket
            async with LocalRpcClient(profile) as client:
                assert (await client.call("health"))["service"] == "jarvis"
    finally:
        lease.close()


async def test_live_socket_is_never_removed_by_recovery(profile):
    lease = ProfileLease.acquire(profile)
    async with LocalRpcServer(profile, {}) as first:
        original = first.socket_path.stat()
        second = LocalRpcServer(profile, {}, recover_stale=lease.assert_current)
        try:
            with pytest.raises(TransportSecurityError, match="activo"):
                await second.start()
            assert first.socket_path.stat().st_ino == original.st_ino
            async with LocalRpcClient(profile) as client:
                assert (await client.call("health"))["service"] == "jarvis"
        finally:
            await second.aclose()
            lease.close()


@pytest.mark.parametrize("kind", ["symlink", "file", "unsafe_mode", "lease_closed"])
async def test_residual_recovery_refuses_unsafe_entries_or_missing_lease(profile, kind):
    lease = ProfileLease.acquire(profile)
    path = profile / "service.sock"
    if kind in {"unsafe_mode", "lease_closed"}:
        dead = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        dead.bind(str(path))
        dead.close()
        path.chmod(0o644 if kind == "unsafe_mode" else 0o600)
    elif kind == "file":
        path.write_text("conservar")
        path.chmod(0o600)
    else:
        path.symlink_to(profile / "inexistente")
    before = path.lstat()
    if kind == "lease_closed":
        lease.close()
    server = LocalRpcServer(profile, {}, recover_stale=lease.assert_current)
    try:
        with pytest.raises(ValueError):
            await server.start()
        assert path.lstat().st_ino == before.st_ino
    finally:
        await server.aclose()
        lease.close()
