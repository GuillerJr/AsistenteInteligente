"""Kernel-attested code-signature checks for IPC peers.

The shared HMAC key proves a client could read the Keychain item, which any process
of the same user can do through ``/usr/bin/security``.  It does not prove the client
is Jarvis.  For privileged methods the daemon therefore also asks the kernel for the
peer's audit token (``LOCAL_PEERTOKEN``, race-free because it carries the PID
version) and lets Security.framework check that process's code signature against a
pinned code requirement.
"""

from __future__ import annotations

import ctypes
import platform
import re
import subprocess
import sys
from collections.abc import Callable, Mapping
from typing import Any

SOL_LOCAL = 0
LOCAL_PEERTOKEN = 6
AUDIT_TOKEN_BYTES = 32
_CF_STRING_ENCODING_UTF8 = 0x08000100
_ERR_SEC_SUCCESS = 0
LOCAL_SIGNING_IDENTITY_NAME = "Jarvis Local Development"

PeerIdentifierResolver = Callable[[Any], str | None]

# Methods any authenticated same-user peer may call, signed or not: the Python CLI,
# secure updates and qualification gates use these. Everything else (memory,
# conversations, computer relay, approvals, audio, speech, vision, submissions from
# the app) requires a peer signed like Jarvis.
UNSIGNED_PEER_METHODS = frozenset(
    {
        "capabilities.status",
        "engineering.preflight",
        "engineering.submit",
        "jobs.cancel",
        "jobs.metrics",
        "jobs.status",
        "jobs.wait",
        "mcp.status",
        "performance.snapshot",
        "plugins.status",
        "provider.status",
        "quality.application",
        "quality.browser",
        "runtime.power.status",
        "runtime.preflight",
        "security.status",
        "swarm.activity",
    }
)


# Signing identifiers allowed to act as privileged clients, and what each may call
# (None: every method). The frozen daemon shares the certificate but loads Python
# from outside its signed binary, so it is deliberately absent: a copied daemon with
# edited code must not pass as a client.
JARVIS_CLIENT_METHODS: Mapping[str, frozenset[str] | None] = {
    "ai.aegis.menubar": None,
    "ai.aegis.menubar.computer": frozenset({"vision.localized.analyze"}),
}


class PeerIdentityError(RuntimeError):
    """Raised when a code requirement cannot be compiled or checked."""


def peer_audit_token(peer_socket: Any) -> bytes:
    raw = peer_socket.getsockopt(SOL_LOCAL, LOCAL_PEERTOKEN, AUDIT_TOKEN_BYTES)
    if not isinstance(raw, bytes) or len(raw) != AUDIT_TOKEN_BYTES:
        raise PeerIdentityError("peer audit token is unavailable")
    return raw


def certificate_requirement(sha1_hex: str) -> str:
    if re.fullmatch(r"[0-9A-Fa-f]{40}", sha1_hex) is None:
        raise PeerIdentityError("signing certificate hash must be 40 hex characters")
    return f'certificate leaf = H"{sha1_hex.upper()}"'


def local_signing_requirement(
    identity_name: str = LOCAL_SIGNING_IDENTITY_NAME,
    run: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
) -> str | None:
    """Pin the leaf certificate of the local identity that signs Jarvis builds."""

    try:
        result = run(
            ["/usr/bin/security", "find-identity", "-v", "-p", "codesigning"],
            check=False,
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode != 0:
        return None
    for line in result.stdout.splitlines():
        match = re.match(r'^\s*\d+\)\s+([0-9A-F]{40})\s+"(.+)"\s*$', line)
        if match is not None and match.group(2) == identity_name:
            return certificate_requirement(match.group(1))
    return None


def own_signer_requirement(
    executable: str | None = None,
    run: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
) -> str | None:
    """Reuse a frozen daemon's own signer, minus its identifier, for its peers."""

    if executable is None:
        if not getattr(sys, "frozen", False):
            return None
        executable = sys.executable
    try:
        result = run(
            ["/usr/bin/codesign", "-d", "-r-", executable],
            check=False,
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode != 0:
        return None
    for line in (result.stdout + "\n" + result.stderr).splitlines():
        if not line.startswith("designated => "):
            continue
        requirement = line.removeprefix("designated => ").strip()
        requirement = re.sub(r'^identifier\s+"[^"]+"\s+and\s+', "", requirement)
        # Only a pinned certificate or a Developer ID team narrows trust to Jarvis;
        # a bare Apple anchor would also admit every system binary.
        if 'certificate leaf = H"' in requirement or "subject.OU" in requirement:
            return requirement
        return None
    return None


def resolve_peer_requirement(configured: str) -> str | None:
    if configured.strip():
        return configured.strip()
    return own_signer_requirement() or local_signing_requirement()


class _Security:
    def __init__(self) -> None:
        self.cf = ctypes.CDLL("/System/Library/Frameworks/CoreFoundation.framework/CoreFoundation")
        self.sec = ctypes.CDLL("/System/Library/Frameworks/Security.framework/Security")
        cf, sec = self.cf, self.sec
        void_p = ctypes.c_void_p
        cf.CFRelease.argtypes = [void_p]
        cf.CFRelease.restype = None
        cf.CFDataCreate.argtypes = [void_p, ctypes.c_char_p, ctypes.c_long]
        cf.CFDataCreate.restype = void_p
        cf.CFStringCreateWithCString.argtypes = [void_p, ctypes.c_char_p, ctypes.c_uint32]
        cf.CFStringCreateWithCString.restype = void_p
        cf.CFDictionaryCreate.argtypes = [
            void_p,
            ctypes.POINTER(void_p),
            ctypes.POINTER(void_p),
            ctypes.c_long,
            void_p,
            void_p,
        ]
        cf.CFDictionaryCreate.restype = void_p
        sec.SecRequirementCreateWithString.argtypes = [
            void_p,
            ctypes.c_uint32,
            ctypes.POINTER(void_p),
        ]
        sec.SecRequirementCreateWithString.restype = ctypes.c_int32
        sec.SecCodeCopyGuestWithAttributes.argtypes = [
            void_p,
            void_p,
            ctypes.c_uint32,
            ctypes.POINTER(void_p),
        ]
        sec.SecCodeCopyGuestWithAttributes.restype = ctypes.c_int32
        sec.SecCodeCheckValidity.argtypes = [void_p, ctypes.c_uint32, void_p]
        sec.SecCodeCheckValidity.restype = ctypes.c_int32
        self.audit_key = void_p.in_dll(sec, "kSecGuestAttributeAudit").value
        self.key_callbacks = ctypes.addressof(void_p.in_dll(cf, "kCFTypeDictionaryKeyCallBacks"))
        self.value_callbacks = ctypes.addressof(
            void_p.in_dll(cf, "kCFTypeDictionaryValueCallBacks")
        )


class CodeSignatureVerifier:
    """Checks whether an IPC peer's running code satisfies one code requirement."""

    def __init__(self, requirement: str) -> None:
        if platform.system() != "Darwin":
            raise PeerIdentityError("code signature checks require macOS")
        if not requirement or "\x00" in requirement:
            raise PeerIdentityError("code requirement is empty or malformed")
        self._security = _Security()
        cf, sec = self._security.cf, self._security.sec
        text = cf.CFStringCreateWithCString(
            None, requirement.encode("utf-8"), _CF_STRING_ENCODING_UTF8
        )
        if not text:
            raise PeerIdentityError("code requirement could not be encoded")
        compiled = ctypes.c_void_p()
        try:
            status = sec.SecRequirementCreateWithString(text, 0, ctypes.byref(compiled))
        finally:
            cf.CFRelease(text)
        if status != _ERR_SEC_SUCCESS or not compiled.value:
            raise PeerIdentityError("code requirement is invalid")
        self._requirement = compiled
        self.requirement = requirement

    def __del__(self) -> None:
        requirement = getattr(self, "_requirement", None)
        if requirement is not None and requirement.value:
            self._security.cf.CFRelease(requirement)

    def audit_token_is_trusted(self, audit_token: bytes) -> bool:
        if len(audit_token) != AUDIT_TOKEN_BYTES:
            return False
        security = self._security
        cf, sec = security.cf, security.sec
        token = cf.CFDataCreate(None, audit_token, AUDIT_TOKEN_BYTES)
        if not token:
            return False
        keys = (ctypes.c_void_p * 1)(security.audit_key)
        values = (ctypes.c_void_p * 1)(token)
        attributes = cf.CFDictionaryCreate(
            None, keys, values, 1, security.key_callbacks, security.value_callbacks
        )
        cf.CFRelease(token)
        if not attributes:
            return False
        guest = ctypes.c_void_p()
        try:
            status = sec.SecCodeCopyGuestWithAttributes(None, attributes, 0, ctypes.byref(guest))
        finally:
            cf.CFRelease(attributes)
        if status != _ERR_SEC_SUCCESS or not guest.value:
            return False
        try:
            return sec.SecCodeCheckValidity(guest, 0, self._requirement) == _ERR_SEC_SUCCESS
        finally:
            cf.CFRelease(guest)

    def __call__(self, peer_socket: Any) -> bool:
        try:
            return self.audit_token_is_trusted(peer_audit_token(peer_socket))
        except (OSError, PeerIdentityError):
            return False


class JarvisPeerIdentifier:
    """Names which Jarvis client a peer is, or None when it is not one."""

    def __init__(self, base_requirement: str, identifiers: tuple[str, ...]) -> None:
        if not identifiers:
            raise PeerIdentityError("at least one client identifier is required")
        self._verifiers: list[tuple[str, CodeSignatureVerifier]] = []
        for identifier in identifiers:
            if re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9.-]{0,127}", identifier) is None:
                raise PeerIdentityError("client identifier is malformed")
            self._verifiers.append(
                (
                    identifier,
                    CodeSignatureVerifier(f'identifier "{identifier}" and ({base_requirement})'),
                )
            )

    def __call__(self, peer_socket: Any) -> str | None:
        try:
            token = peer_audit_token(peer_socket)
        except (OSError, PeerIdentityError):
            return None
        for identifier, verifier in self._verifiers:
            if verifier.audit_token_is_trusted(token):
                return identifier
        return None
