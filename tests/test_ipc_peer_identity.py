from __future__ import annotations

import os
import platform
import socket
import subprocess
import sys
import tempfile
from collections.abc import Iterator, Mapping
from pathlib import Path
from typing import Any
from uuid import UUID

import pytest

from aegis_core.config import Settings
from aegis_core.ipc.client import IpcClient
from aegis_core.ipc.peer_identity import (
    JARVIS_CLIENT_METHODS,
    UNSIGNED_PEER_METHODS,
    CodeSignatureVerifier,
    JarvisPeerIdentifier,
    PeerIdentityError,
    certificate_requirement,
    local_signing_requirement,
    own_signer_requirement,
    resolve_peer_requirement,
)
from aegis_core.ipc.protocol import IpcAuthenticator, IpcRequest
from aegis_core.ipc.server import AegisDaemon, IpcHandlerResult, ipc_peer_signed
from aegis_core.runtime import daemon as daemon_module

AUTHENTICATOR = IpcAuthenticator(bytes.fromhex("44" * 32))
DARWIN_ONLY = pytest.mark.skipif(platform.system() != "Darwin", reason="Security.framework")


class RecordingAudit:
    def __init__(self) -> None:
        self.events: list[tuple[str, Mapping[str, Any]]] = []

    def record_system_event(
        self,
        request_id: UUID,
        *,
        event_type: str,
        component: str,
        data: Mapping[str, Any],
        call_id: str | None = None,
    ) -> None:
        del request_id, component, call_id
        self.events.append((event_type, dict(data)))


async def _secret(request: IpcRequest) -> IpcHandlerResult:
    del request
    return IpcHandlerResult(ok=True, payload={"turns": ["privado"]})


async def _status(request: IpcRequest) -> IpcHandlerResult:
    del request
    return IpcHandlerResult(ok=True, payload={"state": "ok"})


@pytest.fixture
def ipc_root() -> Iterator[Path]:
    with tempfile.TemporaryDirectory(prefix="ag-", dir="/private/tmp") as value:
        root = Path(value)
        root.chmod(0o700)
        yield root


CLIENTS = {"ai.aegis.menubar": None, "ai.aegis.menubar.computer": frozenset({"vision.ok"})}


def _daemon(path: Path, identifier: Any, audit: RecordingAudit | None = None) -> AegisDaemon:
    async def report_signed(request: IpcRequest) -> IpcHandlerResult:
        del request
        return IpcHandlerResult(ok=True, payload={"signed": ipc_peer_signed.get()})

    return AegisDaemon(
        path,
        AUTHENTICATOR,
        handlers={
            "conversations.history": _secret,
            "security.status": _status,
            "vision.ok": _status,
            "peer.report": report_signed,
        },
        peer_identifier=identifier,
        peer_client_methods=CLIENTS,
        unsigned_peer_methods=frozenset({"security.status", "peer.report"}),
        audit_sink=audit,
    )


@pytest.mark.asyncio
async def test_unsigned_peer_is_refused_privileged_methods_and_audited(ipc_root: Path) -> None:
    path = ipc_root / "aegis.sock"
    audit = RecordingAudit()
    resolved: list[object] = []

    def unsigned(peer_socket: object) -> str | None:
        resolved.append(peer_socket)
        return None

    async with _daemon(path, unsigned, audit):
        client = IpcClient(path, AUTHENTICATOR)
        denied = await client.call("conversations.history")
        status = await client.call("security.status")
        health = await client.call("health")
        report = await client.call("peer.report")

    assert denied.ok is False
    assert denied.error_code == "peer_not_trusted"
    assert denied.payload == {}
    assert status.ok is True and health.ok is True
    # Open methods still learn the peer is unsigned, so they can withhold private context.
    assert report.payload == {"signed": False}
    assert len(resolved) == 3
    assert audit.events == [
        (
            "ipc_peer_untrusted",
            {"severity": "critical", "method": "conversations.history", "client": None},
        )
    ]


@pytest.mark.asyncio
async def test_signed_app_reaches_every_method(ipc_root: Path) -> None:
    path = ipc_root / "aegis.sock"

    async with _daemon(path, lambda _peer: "ai.aegis.menubar"):
        client = IpcClient(path, AUTHENTICATOR)
        response = await client.call("conversations.history")
        report = await client.call("peer.report")

    assert response.ok is True
    assert response.payload == {"turns": ["privado"]}
    assert report.payload == {"signed": True}


@pytest.mark.asyncio
async def test_signed_helper_only_reaches_its_own_methods(ipc_root: Path) -> None:
    path = ipc_root / "aegis.sock"
    audit = RecordingAudit()

    async with _daemon(path, lambda _peer: "ai.aegis.menubar.computer", audit):
        client = IpcClient(path, AUTHENTICATOR)
        allowed = await client.call("vision.ok")
        denied = await client.call("conversations.history")

    assert allowed.ok is True
    assert denied.error_code == "peer_not_trusted"
    assert audit.events[-1][1]["client"] == "ai.aegis.menubar.computer"


@pytest.mark.asyncio
@pytest.mark.parametrize("verdict", ["jarvis-daemon", 1, None])
async def test_unknown_or_failing_identifiers_fail_closed(ipc_root: Path, verdict: Any) -> None:
    path = ipc_root / "aegis.sock"

    def identifier(_peer: object) -> Any:
        if verdict is None:
            raise OSError("token unavailable")
        return verdict

    async with _daemon(path, identifier):
        response = await IpcClient(path, AUTHENTICATOR).call("conversations.history")

    assert response.error_code == "peer_not_trusted"


@pytest.mark.asyncio
async def test_retired_local_brain_cannot_submit_with_current_client_allowlist(
    ipc_root: Path,
) -> None:
    path = ipc_root / "aegis.sock"
    async with AegisDaemon(
        path,
        AUTHENTICATOR,
        handlers={"swarm.submit": _status},
        peer_identifier=lambda _peer: "jarvis-local-brain",
        peer_client_methods=JARVIS_CLIENT_METHODS,
        unsigned_peer_methods=UNSIGNED_PEER_METHODS,
    ):
        response = await IpcClient(path, AUTHENTICATOR).call("swarm.submit")

    assert response.ok is False
    assert response.error_code == "peer_not_trusted"


def test_frozen_daemon_is_not_a_client_and_helpers_are_narrow() -> None:
    assert JARVIS_CLIENT_METHODS["ai.aegis.menubar"] is None
    assert all(
        methods is not None
        for identifier, methods in JARVIS_CLIENT_METHODS.items()
        if identifier != "ai.aegis.menubar"
    )
    assert not any("daemon" in identifier for identifier in JARVIS_CLIENT_METHODS)
    assert "jarvis-local-brain" not in JARVIS_CLIENT_METHODS


def test_unsigned_allowlist_excludes_private_data_and_control_methods() -> None:
    for method in (
        "memory.get",
        "memory.put",
        "conversations.history",
        "conversations.delete",
        "computer.wait",
        "computer.complete",
        "approval.register_signer",
        "swarm.submit",
        "voice.submit",
        "audio.session.open",
        "speech.synthesize",
        "system.focus.update",
    ):
        assert method not in UNSIGNED_PEER_METHODS


def _completed(stdout: str = "", stderr: str = "", code: int = 0) -> Any:
    def run(arguments: list[str], **_: Any) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(arguments, code, stdout, stderr)

    return run


def test_local_signing_requirement_pins_the_named_identity_certificate() -> None:
    output = (
        '  1) 1111111111111111111111111111111111111111 "Someone Else"\n'
        '  2) 062037ED28734EE4EF81E24E04DCC0A9112C334C "Jarvis Local Development"\n'
        "     2 valid identities found\n"
    )

    assert local_signing_requirement(run=_completed(output)) == (
        'certificate leaf = H"062037ED28734EE4EF81E24E04DCC0A9112C334C"'
    )
    assert local_signing_requirement(run=_completed("  1) " + "A" * 40 + ' "Other"\n')) is None
    assert local_signing_requirement(run=_completed(code=1)) is None


@pytest.mark.parametrize(
    ("designated", "expected"),
    [
        (
            'designated => identifier "ai.aegis.daemon" and certificate leaf = H"ABCD"',
            'certificate leaf = H"ABCD"',
        ),
        (
            'designated => identifier "ai.aegis.daemon" and anchor apple generic and '
            "certificate leaf[subject.OU] = TEAM123456",
            "anchor apple generic and certificate leaf[subject.OU] = TEAM123456",
        ),
        ('designated => identifier "com.apple.python3" and anchor apple', None),
        ('designated => cdhash H"00"', None),
    ],
)
def test_own_signer_requirement_only_keeps_requirements_that_name_jarvis(
    designated: str, expected: str | None
) -> None:
    assert (
        own_signer_requirement("/frozen/jarvis-daemon", run=_completed(stderr=designated + "\n"))
        == expected
    )


def test_own_signer_requirement_ignores_an_unfrozen_interpreter() -> None:
    assert getattr(sys, "frozen", False) is False
    assert own_signer_requirement() is None


def test_configured_requirement_wins() -> None:
    assert resolve_peer_requirement('  anchor apple generic and identifier "x" ') == (
        'anchor apple generic and identifier "x"'
    )
    with pytest.raises(PeerIdentityError):
        certificate_requirement("not-a-hash")


def test_daemon_fails_closed_without_a_pinned_signer(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(daemon_module, "resolve_peer_requirement", lambda _configured: None)
    identifier = daemon_module._peer_identifier(Settings())

    assert identifier is not None
    assert identifier(object()) is None
    assert daemon_module._peer_identifier(Settings(ipc_require_signed_peers=False)) is None


@DARWIN_ONLY
def test_invalid_code_requirement_is_rejected() -> None:
    with pytest.raises(PeerIdentityError, match="invalid"):
        CodeSignatureVerifier("not a requirement")


@DARWIN_ONLY
def test_kernel_audit_token_tells_signed_and_unsigned_peers_apart(ipc_root: Path) -> None:
    verifier = CodeSignatureVerifier("anchor apple")
    path = str(ipc_root / "peer.sock")
    server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    server.bind(path)
    server.listen(2)
    peers = {
        "apple_signed": ["/usr/bin/nc", "-U", path],
        "test_python": [
            sys.executable,
            "-c",
            f"import socket,time;s=socket.socket(socket.AF_UNIX);s.connect({path!r});time.sleep(5)",
        ],
    }
    verdicts: dict[str, bool] = {}
    try:
        for name, command in peers.items():
            process = subprocess.Popen(command, stdin=subprocess.PIPE)
            try:
                server.settimeout(5)
                connection, _ = server.accept()
                with connection:
                    verdicts[name] = verifier(connection)
            finally:
                process.kill()
                process.wait()
    finally:
        server.close()
        os.unlink(path)

    assert verdicts["apple_signed"] is True
    # The test interpreter is not Apple-signed; a pinned Jarvis requirement
    # rejects it the same way.
    assert verdicts["test_python"] is (
        subprocess.run(
            ["/usr/bin/codesign", "-v", "-R=anchor apple", os.path.realpath(sys.executable)],
            capture_output=True,
            check=False,
        ).returncode
        == 0
    )


@DARWIN_ONLY
def test_peer_identifier_names_the_matching_client(ipc_root: Path) -> None:
    identifier = JarvisPeerIdentifier("anchor apple", ("com.apple.nc", "not.this"))
    path = str(ipc_root / "named.sock")
    server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    server.bind(path)
    server.listen(1)
    process = subprocess.Popen(["/usr/bin/nc", "-U", path], stdin=subprocess.PIPE)
    try:
        server.settimeout(5)
        connection, _ = server.accept()
        with connection:
            assert identifier(connection) == "com.apple.nc"
    finally:
        process.kill()
        process.wait()
        server.close()
        os.unlink(path)
    with pytest.raises(PeerIdentityError):
        JarvisPeerIdentifier("anchor apple", ('bad" or anchor apple',))
