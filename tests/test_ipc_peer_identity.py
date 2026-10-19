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
    UNSIGNED_PEER_METHODS,
    CodeSignatureVerifier,
    PeerIdentityError,
    certificate_requirement,
    local_signing_requirement,
    own_signer_requirement,
    resolve_peer_requirement,
)
from aegis_core.ipc.protocol import IpcAuthenticator, IpcRequest
from aegis_core.ipc.server import AegisDaemon, IpcHandlerResult
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


def _daemon(path: Path, resolver: Any, audit: RecordingAudit | None = None) -> AegisDaemon:
    return AegisDaemon(
        path,
        AUTHENTICATOR,
        handlers={"conversations.history": _secret, "security.status": _status},
        peer_trust_resolver=resolver,
        unsigned_peer_methods=frozenset({"security.status"}),
        audit_sink=audit,
    )


@pytest.mark.asyncio
async def test_unsigned_peer_is_refused_privileged_methods_and_audited(ipc_root: Path) -> None:
    path = ipc_root / "aegis.sock"
    audit = RecordingAudit()
    resolved: list[object] = []

    def untrusted(peer_socket: object) -> bool:
        resolved.append(peer_socket)
        return False

    async with _daemon(path, untrusted, audit):
        client = IpcClient(path, AUTHENTICATOR)
        denied = await client.call("conversations.history")
        status = await client.call("security.status")
        health = await client.call("health")

    assert denied.ok is False
    assert denied.error_code == "peer_not_trusted"
    assert denied.payload == {}
    assert status.ok is True and health.ok is True
    # Public methods never pay for a signature check.
    assert len(resolved) == 1
    assert audit.events == [
        (
            "ipc_peer_untrusted",
            {"severity": "critical", "method": "conversations.history"},
        )
    ]


@pytest.mark.asyncio
async def test_signed_peer_reaches_privileged_methods(ipc_root: Path) -> None:
    path = ipc_root / "aegis.sock"

    async with _daemon(path, lambda _peer: True):
        response = await IpcClient(path, AUTHENTICATOR).call("conversations.history")

    assert response.ok is True
    assert response.payload == {"turns": ["privado"]}


@pytest.mark.asyncio
@pytest.mark.parametrize("verdict", ["yes", 1, None])
async def test_a_failing_or_non_boolean_resolver_fails_closed(ipc_root: Path, verdict: Any) -> None:
    path = ipc_root / "aegis.sock"

    def resolver(_peer: object) -> Any:
        if verdict is None:
            raise OSError("token unavailable")
        return verdict

    async with _daemon(path, resolver):
        response = await IpcClient(path, AUTHENTICATOR).call("conversations.history")

    assert response.error_code == "peer_not_trusted"


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
    resolver = daemon_module._peer_trust_resolver(Settings())

    assert resolver is not None
    assert resolver(object()) is False
    assert daemon_module._peer_trust_resolver(Settings(ipc_require_signed_peers=False)) is None


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
