import base64
import hashlib
import time
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

from aegis_core.approval_signing import (
    ApprovalSignerError,
    PinnedApprovalSigner,
    SignerRegistration,
    approval_signature_message,
    voice_evidence_sha256,
)
from aegis_core.ipc.protocol import IpcAuthenticator
from aegis_core.job_ipc import SwarmIpcService

NOW = 1_800_000_000_000
DIGEST = "ab" * 32
NONCE = "1f" * 16


def _key() -> ec.EllipticCurvePrivateKey:
    return ec.generate_private_key(ec.SECP256R1())


def _public(key: ec.EllipticCurvePrivateKey) -> bytes:
    return key.public_key().public_bytes(Encoding.X962, PublicFormat.UncompressedPoint)


def _sign(key: ec.EllipticCurvePrivateKey, **fields: object) -> bytes:
    return key.sign(approval_signature_message(**fields), ec.ECDSA(hashes.SHA256()))


def _signer(tmp_path: Path, now: int = NOW) -> PinnedApprovalSigner:
    return PinnedApprovalSigner(
        tmp_path / "keys/approval_signer.pub",
        clock_milliseconds=lambda: now,
    )


def test_first_registration_pins_private_key_file_and_rejects_another_key(tmp_path: Path) -> None:
    signer = _signer(tmp_path)
    key = _key()

    assert signer.is_pinned is False
    assert signer.register(_public(key)) is SignerRegistration.PINNED
    assert signer.register(_public(key)) is SignerRegistration.ALREADY_PINNED
    with pytest.raises(ApprovalSignerError, match="already pinned"):
        signer.register(_public(_key()))

    pinned = tmp_path / "keys/approval_signer.pub"
    assert pinned.stat().st_mode & 0o777 == 0o600
    assert (tmp_path / "keys").stat().st_mode & 0o777 == 0o700
    assert signer.reset() is True
    assert signer.is_pinned is False


def test_valid_signature_is_single_use_and_bound_to_every_field(tmp_path: Path) -> None:
    signer = _signer(tmp_path)
    key = _key()
    signer.register(_public(key))
    job_id = uuid4()
    fields = {
        "method": "jobs.approve",
        "job_id": job_id,
        "call_digest": DIGEST,
        "approval_nonce": NONCE,
        "issued_at_milliseconds": NOW,
    }
    signature = _sign(key, **fields)

    with pytest.raises(ApprovalSignerError, match="invalid"):
        signer.verify(**{**fields, "call_digest": "cd" * 32}, signature=signature)
    with pytest.raises(ApprovalSignerError, match="invalid"):
        signer.verify(**{**fields, "method": "jobs.approve.voice"}, signature=signature)
    signer.verify(**fields, signature=signature)
    with pytest.raises(ApprovalSignerError, match="replayed"):
        signer.verify(**fields, signature=signature)


def test_signature_from_unpinned_key_or_stale_time_is_rejected(tmp_path: Path) -> None:
    key = _key()
    fields = {
        "method": "jobs.approve",
        "job_id": uuid4(),
        "call_digest": DIGEST,
        "approval_nonce": NONCE,
        "issued_at_milliseconds": NOW,
    }
    unregistered = _signer(tmp_path / "a")
    with pytest.raises(ApprovalSignerError, match="no approval signer"):
        unregistered.verify(**fields, signature=_sign(key, **fields))

    late = _signer(tmp_path / "b", now=NOW + 31_000)
    late.register(_public(key))
    with pytest.raises(ApprovalSignerError, match="stale"):
        late.verify(**fields, signature=_sign(key, **fields))

    attacker = _signer(tmp_path / "c")
    attacker.register(_public(key))
    with pytest.raises(ApprovalSignerError, match="invalid"):
        attacker.verify(**fields, signature=_sign(_key(), **fields))


def test_group_or_world_readable_pin_is_refused(tmp_path: Path) -> None:
    signer = _signer(tmp_path)
    signer.register(_public(_key()))
    (tmp_path / "keys/approval_signer.pub").chmod(0o644)

    with pytest.raises(ApprovalSignerError, match="not private"):
        _ = signer.is_pinned


@pytest.mark.asyncio
async def test_ipc_registration_and_voice_approval_require_the_pinned_signature(
    tmp_path: Path,
) -> None:
    class _Jobs:
        approved: list[str]

        def __init__(self) -> None:
            self.approved = []

        async def approve_by_voice(self, **kwargs: object) -> object:
            self.approved.append(str(kwargs["call_digest"]))
            raise AssertionError("signature accepted; job manager reached")

    jobs = _Jobs()
    signer = PinnedApprovalSigner(tmp_path / "approval_signer.pub")
    service = SwarmIpcService(jobs, approval_signer=signer)  # type: ignore[arg-type]
    authenticator = IpcAuthenticator(bytes.fromhex("5a" * 32))
    key = _key()

    registered = await service.handle(
        authenticator.create_request(
            "approval.register_signer",
            {"public_key": base64.b64encode(_public(key)).decode()},
        )
    )
    hijack = await service.handle(
        authenticator.create_request(
            "approval.register_signer",
            {"public_key": base64.b64encode(_public(_key())).decode()},
        )
    )
    assert registered.ok is True
    assert registered.payload == {"pinned": True, "newly_pinned": True}
    assert hijack.error_code == "approval_signer_mismatch"

    pcm = b"\x01\x00" * 8_000
    job_id = uuid4()
    issued_at = time.time_ns() // 1_000_000
    base = {
        "job_id": str(job_id),
        "call_digest": DIGEST,
        "approval_nonce": NONCE,
        "issued_at_milliseconds": issued_at,
        "encoding": "pcm_s16le",
        "sample_rate_hz": 16_000,
        "channels": 1,
        "pcm_base64": base64.b64encode(pcm).decode(),
        "speaker_identifier": "owner",
        "speaker_confidence": 0.95,
        "owner_profile_match": True,
    }
    wrong_audio = _sign(
        key,
        method="jobs.approve.voice",
        job_id=job_id,
        call_digest=DIGEST,
        approval_nonce=NONCE,
        issued_at_milliseconds=issued_at,
        evidence_sha256=hashlib.sha256(b"other").hexdigest(),
    )
    rejected = await service.handle(
        authenticator.create_request(
            "jobs.approve.voice",
            {**base, "signature": base64.b64encode(wrong_audio).decode()},
        )
    )
    assert rejected.error_code == "approval_signature_invalid"
    assert jobs.approved == []

    signature = _sign(
        key,
        method="jobs.approve.voice",
        job_id=job_id,
        call_digest=DIGEST,
        approval_nonce=NONCE,
        issued_at_milliseconds=issued_at,
        evidence_sha256=hashlib.sha256(pcm).hexdigest(),
    )
    with pytest.raises(AssertionError, match="job manager reached"):
        await service.handle(
            authenticator.create_request(
                "jobs.approve.voice",
                {**base, "signature": base64.b64encode(signature).decode()},
            )
        )
    assert jobs.approved == [DIGEST]


@pytest.mark.asyncio
async def test_voice_transcript_is_bound_to_its_audio_by_the_signature(tmp_path: Path) -> None:
    class _Jobs:
        def __init__(self) -> None:
            self.transcripts: list[object] = []

        async def approve_by_voice(self, **kwargs: object) -> object:
            self.transcripts.append(kwargs["transcript"])
            raise AssertionError("signature accepted; job manager reached")

    jobs = _Jobs()
    signer = PinnedApprovalSigner(tmp_path / "approval_signer.pub")
    service = SwarmIpcService(jobs, approval_signer=signer)  # type: ignore[arg-type]
    authenticator = IpcAuthenticator(bytes.fromhex("5a" * 32))
    key = _key()
    signer.register(_public(key))
    pcm = b"\x01\x00" * 8_000
    job_id = uuid4()
    issued_at = time.time_ns() // 1_000_000

    def request(nonce: str, signed_evidence: str, transcript: str):
        fields = dict(method="jobs.approve.voice", job_id=job_id, call_digest=DIGEST,
                      approval_nonce=nonce, issued_at_milliseconds=issued_at)
        signature = _sign(key, **fields, evidence_sha256=signed_evidence)
        return authenticator.create_request("jobs.approve.voice", {
            "job_id": str(job_id), "call_digest": DIGEST, "approval_nonce": nonce,
            "issued_at_milliseconds": issued_at, "encoding": "pcm_s16le",
            "sample_rate_hz": 16_000, "channels": 1,
            "pcm_base64": base64.b64encode(pcm).decode(), "speaker_identifier": "owner",
            "speaker_confidence": 0.95, "owner_profile_match": True,
            "transcript": transcript, "signature": base64.b64encode(signature).decode(),
        })

    # A transcript swapped after signing, or signed over the audio alone, is refused.
    tampered = await service.handle(request("1" * 32, voice_evidence_sha256(pcm, "no"), "sí"))
    audio_only = await service.handle(request("2" * 32, hashlib.sha256(pcm).hexdigest(), "sí"))
    assert tampered.error_code == audio_only.error_code == "approval_signature_invalid"
    assert jobs.transcripts == []

    with pytest.raises(AssertionError, match="job manager reached"):
        await service.handle(request("3" * 32, voice_evidence_sha256(pcm, "sí"), "sí"))
    assert jobs.transcripts == ["sí"]
    assert voice_evidence_sha256(pcm, None) == hashlib.sha256(pcm).hexdigest()


@pytest.mark.asyncio
async def test_approvals_fail_closed_without_a_configured_signer() -> None:
    service = SwarmIpcService(object())  # type: ignore[arg-type]
    authenticator = IpcAuthenticator(bytes.fromhex("5a" * 32))
    response = await service.handle(
        authenticator.create_request(
            "jobs.approve",
            {
                "job_id": str(uuid4()),
                "call_digest": DIGEST,
                "approval_nonce": NONCE,
                "issued_at_milliseconds": NOW,
                "signature": base64.b64encode(b"x" * 70).decode(),
            },
        )
    )
    assert response.error_code == "approval_signature_invalid"


def test_message_wire_format_is_stable() -> None:
    # Same vector as ApprovalSignerTests.approvalMessageMatchesTheDaemonWireFormat (Swift).
    message = approval_signature_message(
        method="jobs.approve.voice",
        job_id=UUID("01234567-89AB-CDEF-0123-456789ABCDEF"),
        call_digest=DIGEST,
        approval_nonce=NONCE,
        issued_at_milliseconds=1_800_000_000_000,
        evidence_sha256="cd" * 32,
    )
    assert message.decode() == "\n".join(
        (
            "jarvis-approval-v1",
            "jobs.approve.voice",
            "01234567-89ab-cdef-0123-456789abcdef",
            DIGEST,
            NONCE,
            "1800000000000",
            "cd" * 32,
        )
    )


def test_voice_evidence_digest_is_stable() -> None:
    # Same vectors as ApprovalSignerTests.swift::voiceEvidenceDigestMatchesTheDaemon.
    pcm = b"\x01\x00" * 8
    assert voice_evidence_sha256(pcm, None) == (
        "278abfb53cf32e306305c34f6ad5469b82453fa58f29fd339bf1615e7ffb4331"
    )
    assert voice_evidence_sha256(pcm, "sí") == (
        "fcdc8615ad6f1079e8f7c9edc688529e0fb55a17fbcc51eb8a7dec2d60ad0468"
    )
