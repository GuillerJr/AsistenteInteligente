"""Approval signatures that only the native Jarvis app can produce.

The IPC HMAC key is readable by any process of the user through ``/usr/bin/security``,
so it cannot by itself prove that a human approved an action in the notch/HUD. The
native app owns a P-256 private key in its own Keychain ACL; the daemon pins the
matching public key on first registration and requires a fresh, single-use signature
over the exact job, call digest and evidence for every approval.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import os
import re
import stat
import time
from collections import OrderedDict
from collections.abc import Callable
from enum import StrEnum
from pathlib import Path
from threading import RLock
from uuid import UUID

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec

APPROVAL_SIGNATURE_CONTEXT = "jarvis-approval-v1"
APPROVAL_SIGNATURE_MAX_AGE_MILLISECONDS = 30_000
APPROVAL_SIGNATURE_MAX_REPLAYS = 512
P256_X963_PUBLIC_KEY_BYTES = 65
_NONCE = re.compile(r"^[0-9a-f]{32}$")
_DIGEST = re.compile(r"^[0-9a-f]{64}$")


class ApprovalSignerError(Exception):
    """Raised when an approval signature or signer registration is not acceptable."""


class SignerRegistration(StrEnum):
    PINNED = "pinned"
    ALREADY_PINNED = "already_pinned"


VOICE_EVIDENCE_CONTEXT = "jarvis-voice-evidence-v2"


def voice_evidence_sha256(pcm: bytes, transcript: str | None) -> str:
    """Digest the signed voice evidence; the app's transcript is bound to its audio.

    Mirrors `ApprovalSignatureMessage.voiceEvidenceSHA256` in the native app.
    """

    audio = hashlib.sha256(pcm).hexdigest()
    if transcript is None:
        return audio
    bound = f"{VOICE_EVIDENCE_CONTEXT}\n{audio}\n{transcript}".encode()
    return hashlib.sha256(bound).hexdigest()


def approval_signature_message(
    *,
    method: str,
    job_id: UUID,
    call_digest: str,
    approval_nonce: str,
    issued_at_milliseconds: int,
    evidence_sha256: str | None = None,
) -> bytes:
    """Return the exact bytes the native app signs; any field change invalidates it."""

    if method not in {"jobs.approve", "jobs.approve.voice", "jobs.approve.standing",
                      "jobs.approve.app.conversation", "jobs.approve.app.always"}:
        raise ApprovalSignerError("approval method cannot be signed")
    if _DIGEST.fullmatch(call_digest) is None or _NONCE.fullmatch(approval_nonce) is None:
        raise ApprovalSignerError("approval signature fields are invalid")
    if evidence_sha256 is not None and _DIGEST.fullmatch(evidence_sha256) is None:
        raise ApprovalSignerError("approval evidence digest is invalid")
    return "\n".join(
        (
            APPROVAL_SIGNATURE_CONTEXT,
            method,
            str(job_id).lower(),
            call_digest,
            approval_nonce,
            str(issued_at_milliseconds),
            evidence_sha256 or "-",
        )
    ).encode("ascii")


class PinnedApprovalSigner:
    """Trust-on-first-use P-256 public key with freshness and replay checks."""

    def __init__(
        self,
        public_key_path: Path,
        *,
        clock_milliseconds: Callable[[], int] | None = None,
        max_age_milliseconds: int = APPROVAL_SIGNATURE_MAX_AGE_MILLISECONDS,
        max_replays: int = APPROVAL_SIGNATURE_MAX_REPLAYS,
    ) -> None:
        if max_age_milliseconds <= 0 or max_replays < 1:
            raise ValueError("approval signer limits must be positive")
        self._path = public_key_path
        self._clock = clock_milliseconds or (lambda: time.time_ns() // 1_000_000)
        self._max_age = max_age_milliseconds
        self._max_replays = max_replays
        self._seen: OrderedDict[str, int] = OrderedDict()
        self._lock = RLock()

    @property
    def is_pinned(self) -> bool:
        return self._read_pinned() is not None

    def register(self, public_key_x963: bytes) -> SignerRegistration:
        self._load_public_key(public_key_x963)
        with self._lock:
            pinned = self._read_pinned()
            if pinned is not None:
                if pinned != public_key_x963:
                    raise ApprovalSignerError("a different approval signer is already pinned")
                return SignerRegistration.ALREADY_PINNED
            self._write_pinned(public_key_x963)
            return SignerRegistration.PINNED

    def reset(self) -> bool:
        with self._lock:
            self._seen.clear()
            try:
                self._path.unlink()
            except FileNotFoundError:
                return False
            return True

    def verify(
        self,
        *,
        method: str,
        job_id: UUID,
        call_digest: str,
        approval_nonce: str,
        issued_at_milliseconds: int,
        signature: bytes,
        evidence_sha256: str | None = None,
    ) -> None:
        message = approval_signature_message(
            method=method,
            job_id=job_id,
            call_digest=call_digest,
            approval_nonce=approval_nonce,
            issued_at_milliseconds=issued_at_milliseconds,
            evidence_sha256=evidence_sha256,
        )
        with self._lock:
            pinned = self._read_pinned()
            if pinned is None:
                raise ApprovalSignerError("no approval signer is registered")
            try:
                self._load_public_key(pinned).verify(
                    signature,
                    message,
                    ec.ECDSA(hashes.SHA256()),
                )
            except InvalidSignature as error:
                raise ApprovalSignerError("approval signature is invalid") from error
            now = self._clock()
            if abs(now - issued_at_milliseconds) > self._max_age:
                raise ApprovalSignerError("approval signature is stale")
            for nonce, expiry in list(self._seen.items()):
                if expiry > now:
                    break
                del self._seen[nonce]
            if approval_nonce in self._seen:
                raise ApprovalSignerError("approval signature was replayed")
            if len(self._seen) >= self._max_replays:
                # Never evict a live nonce: saturation must not enable replay.
                raise ApprovalSignerError("approval replay window is saturated")
            self._seen[approval_nonce] = issued_at_milliseconds + self._max_age

    @staticmethod
    def decode_public_key(value: str) -> bytes:
        try:
            raw = base64.b64decode(value, validate=True)
        except (binascii.Error, ValueError) as error:
            raise ApprovalSignerError("approval signer key is not base64") from error
        PinnedApprovalSigner._load_public_key(raw)
        return raw

    @staticmethod
    def _load_public_key(raw: bytes) -> ec.EllipticCurvePublicKey:
        if len(raw) != P256_X963_PUBLIC_KEY_BYTES or raw[0] != 0x04:
            raise ApprovalSignerError("approval signer key must be an uncompressed P-256 point")
        try:
            return ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), raw)
        except ValueError as error:
            raise ApprovalSignerError("approval signer key is not on P-256") from error

    def _read_pinned(self) -> bytes | None:
        try:
            status = self._path.lstat()
        except FileNotFoundError:
            return None
        if (
            not stat.S_ISREG(status.st_mode)
            or status.st_uid != os.getuid()
            or stat.S_IMODE(status.st_mode) & 0o077
            or status.st_size > 256
        ):
            raise ApprovalSignerError("pinned approval signer file is not private")
        try:
            raw = base64.b64decode(self._path.read_bytes().strip(), validate=True)
        except (binascii.Error, ValueError) as error:
            raise ApprovalSignerError("pinned approval signer file is corrupt") from error
        self._load_public_key(raw)
        return raw

    def _write_pinned(self, raw: bytes) -> None:
        directory = self._path.parent
        directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        temporary = directory / f".{self._path.name}.{os.getpid()}.tmp"
        descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        try:
            with os.fdopen(descriptor, "wb") as handle:
                handle.write(base64.b64encode(raw) + b"\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, self._path)
        except BaseException:
            temporary.unlink(missing_ok=True)
            raise
