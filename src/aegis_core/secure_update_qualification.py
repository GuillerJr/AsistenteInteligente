from __future__ import annotations

import hashlib
import json
import os
import re
import stat
import subprocess
import tempfile
import time
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from aegis_core.macos_qualification import QualificationCheck, QualificationStatus
from aegis_core.secure_update import (
    MAX_ARCHIVE_BYTES,
    MAX_MANIFEST_BYTES,
    MAX_RELEASE_MANIFEST_BYTES,
    TRANSACTION_PREFIX,
    AtomicBundleTransaction,
    SecureUpdateError,
    SignedUpdateManifest,
    UpdateRollbackError,
    verify_signed_update,
)

SCHEMA_VERSION = "1.0"
QUALIFICATION_CHECKS = 5
MAX_REPORT_BYTES = 256 * 1_024
EXPECTED_P13_CHECKS = frozenset(
    {
        "release.p12_chain",
        "runtime.embedded_daemon",
        "runtime.cold_start",
        "runtime.source_independence",
        "privacy.runtime_boundary",
    }
)


class SecureUpdateQualificationError(RuntimeError):
    """P14 cannot authenticate the update channel and rollback boundary."""


class _P13Check(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    check_id: str
    status: QualificationStatus
    reason: str
    metrics: dict[str, bool | int | float | str | None]


class _P13Privacy(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    contains_prompt_text: Literal[False]
    contains_transcripts: Literal[False]
    contains_audio: Literal[False]
    contains_images: Literal[False]
    contains_credentials: Literal[False]
    contains_absolute_paths: Literal[False]
    model_api_calls: Literal[0]
    network_calls: Literal[0]


class _P13Report(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["1.0"]
    profile: Literal["self_contained_runtime_qualification"]
    build_revision: str = Field(pattern=r"^[0-9a-f]{40}$")
    status: QualificationStatus
    score: int = Field(ge=0, le=100)
    gate_passed: bool
    passed: int = Field(ge=0, le=5)
    total: Literal[5]
    duration_ms: int = Field(ge=0, le=3_600_000)
    artifact_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    checks: tuple[_P13Check, ...] = Field(min_length=5, max_length=5)
    privacy: _P13Privacy

    @model_validator(mode="after")
    def validate_contract(self) -> _P13Report:
        identifiers = [check.check_id for check in self.checks]
        actual_passed = sum(
            check.status is QualificationStatus.PASSED for check in self.checks
        )
        if len(identifiers) != len(set(identifiers)):
            raise ValueError("P13 report contains duplicate checks")
        if frozenset(identifiers) != EXPECTED_P13_CHECKS:
            raise ValueError("P13 report contract is incomplete")
        if (
            self.passed != actual_passed
            or self.score != round(actual_passed / QUALIFICATION_CHECKS * 100)
            or self.gate_passed != (actual_passed == QUALIFICATION_CHECKS)
        ):
            raise ValueError("P13 report summary is inconsistent")
        expected = QualificationStatus.PASSED if self.gate_passed else QualificationStatus.BLOCKED
        if self.status is not expected:
            raise ValueError("P13 report status is inconsistent")
        return self


class SecureUpdateQualificationReport:
    def __init__(
        self,
        *,
        build_revision: str,
        artifact_sha256: str,
        update_key_id: str,
        checks: tuple[QualificationCheck, ...],
        duration_ms: int,
    ) -> None:
        self.build_revision = build_revision
        self.artifact_sha256 = artifact_sha256
        self.update_key_id = update_key_id
        self.checks = checks
        self.duration_ms = duration_ms

    @property
    def passed(self) -> int:
        return sum(check.status is QualificationStatus.PASSED for check in self.checks)

    @property
    def score(self) -> int:
        return round(self.passed / QUALIFICATION_CHECKS * 100)

    @property
    def gate_passed(self) -> bool:
        return len(self.checks) == QUALIFICATION_CHECKS and self.passed == QUALIFICATION_CHECKS

    @property
    def status(self) -> QualificationStatus:
        return QualificationStatus.PASSED if self.gate_passed else QualificationStatus.BLOCKED

    def private_dict(self) -> dict[str, object]:
        return {
            "schema_version": SCHEMA_VERSION,
            "profile": "secure_update_qualification",
            "build_revision": self.build_revision,
            "status": self.status.value,
            "score": self.score,
            "gate_passed": self.gate_passed,
            "passed": self.passed,
            "total": len(self.checks),
            "duration_ms": self.duration_ms,
            "artifact_sha256": self.artifact_sha256,
            "update_key_id": self.update_key_id,
            "checks": [check.private_dict() for check in self.checks],
            "privacy": {
                "contains_prompt_text": False,
                "contains_transcripts": False,
                "contains_audio": False,
                "contains_images": False,
                "contains_credentials": False,
                "contains_absolute_paths": False,
                "model_api_calls": 0,
                "network_calls": 0,
            },
        }

    def private_json(self) -> str:
        return json.dumps(self.private_dict(), separators=(",", ":"), sort_keys=True)


class SecureUpdateQualificationGate:
    def __init__(
        self,
        *,
        project_root: Path,
        p13_report_path: Path,
        update_manifest_path: Path,
        release_manifest_path: Path,
        release_archive_path: Path,
        public_key_path: Path,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._project_root = project_root
        self._p13_report_path = p13_report_path
        self._update_manifest_path = update_manifest_path
        self._release_manifest_path = release_manifest_path
        self._release_archive_path = release_archive_path
        self._public_key_path = public_key_path
        self._clock = clock

    def run(self) -> SecureUpdateQualificationReport:
        started = time.monotonic_ns()
        revision = _source_revision(self._project_root)
        _require_clean_tracked_tree(self._project_root)
        p13_bytes = _read_private(self._p13_report_path, MAX_REPORT_BYTES)
        update_bytes = _read_private(self._update_manifest_path, MAX_MANIFEST_BYTES)
        release_bytes = _read_private(
            self._release_manifest_path, MAX_RELEASE_MANIFEST_BYTES
        )
        archive_digest, archive_size = _hash_private(
            self._release_archive_path, MAX_ARCHIVE_BYTES
        )
        try:
            p13 = _P13Report.model_validate_json(p13_bytes)
            envelope = SignedUpdateManifest.model_validate_json(update_bytes)
        except ValidationError as error:
            raise SecureUpdateQualificationError("P14 evidence schema is invalid") from error
        p13_passed = (
            p13.gate_passed
            and p13.build_revision == revision
            and p13.artifact_sha256 == archive_digest
        )
        signed = False
        verified = None
        try:
            verified = verify_signed_update(
                self._update_manifest_path,
                self._release_archive_path,
                self._release_manifest_path,
                self._public_key_path,
                current_build=0,
                now=self._clock(),
            )
            signed = (
                verified.manifest.payload.build_revision == revision
                and verified.manifest.payload.artifact.sha256 == archive_digest
                and verified.manifest.payload.artifact.size == archive_size
                and verified.manifest.payload.artifact.release_manifest_sha256
                == hashlib.sha256(release_bytes).hexdigest()
            )
        except (OSError, SecureUpdateError, ValueError):
            signed = False
        anti_rollback = False
        if verified is not None:
            try:
                verify_signed_update(
                    self._update_manifest_path,
                    self._release_archive_path,
                    self._release_manifest_path,
                    self._public_key_path,
                    current_build=verified.manifest.payload.build,
                    now=self._clock(),
                )
            except UpdateRollbackError:
                anti_rollback = True
        recovery = _probe_atomic_recovery(revision)
        private = (
            all(value is False for value in envelope.payload.privacy.model_dump().values())
            and not _contains_absolute_paths(envelope.model_dump(mode="json"))
            and not _contains_secret_material(update_bytes)
        )
        checks = (
            QualificationCheck(
                "release.p13_chain",
                QualificationStatus.PASSED if p13_passed else QualificationStatus.BLOCKED,
                "verified" if p13_passed else "p13_prerequisite_not_passed",
                {"revision_matches": p13.build_revision == revision},
            ),
            QualificationCheck(
                "channel.ed25519_authenticity",
                QualificationStatus.PASSED if signed else QualificationStatus.BLOCKED,
                "verified" if signed else "signed_channel_invalid",
                {
                    "algorithm": envelope.algorithm,
                    "artifact_bytes": archive_size,
                },
            ),
            QualificationCheck(
                "channel.rollback_resistance",
                QualificationStatus.PASSED if anti_rollback else QualificationStatus.BLOCKED,
                "verified" if anti_rollback else "downgrade_guard_failed",
                {"target_build": envelope.payload.build},
            ),
            QualificationCheck(
                "installer.atomic_recovery",
                QualificationStatus.PASSED if recovery else QualificationStatus.BLOCKED,
                "verified" if recovery else "atomic_recovery_failed",
                {"same_volume_swap": recovery, "recovery_journal": recovery},
            ),
            QualificationCheck(
                "privacy.update_boundary",
                QualificationStatus.PASSED if private else QualificationStatus.BLOCKED,
                "verified" if private else "update_privacy_boundary_failed",
                {"network_calls": 0, "private_payload": private},
            ),
        )
        elapsed = max(0, (time.monotonic_ns() - started) // 1_000_000)
        return SecureUpdateQualificationReport(
            build_revision=revision,
            artifact_sha256=archive_digest,
            update_key_id=envelope.key_id,
            checks=checks,
            duration_ms=min(elapsed, 2_147_483_647),
        )


def _probe_atomic_recovery(revision: str) -> bool:
    try:
        with tempfile.TemporaryDirectory(prefix="aegis-p14-", dir="/private/tmp") as root_text:
            root = Path(root_text)
            root.chmod(0o700)
            applications = root / "Applications"
            state = root / "State"
            applications.mkdir(mode=0o700)
            state.mkdir(mode=0o700)
            installed = applications / "Jarvis.app"
            old_marker = installed / "Contents/version"
            old_marker.parent.mkdir(parents=True)
            old_marker.write_bytes(b"previous")
            transaction_id = uuid4()
            transaction_root = applications / f"{TRANSACTION_PREFIX}{transaction_id}"
            candidate = transaction_root / "Jarvis.app"
            new_marker = candidate / "Contents/version"
            new_marker.parent.mkdir(parents=True)
            new_marker.write_bytes(b"candidate")
            transaction_root.chmod(0o700)
            transaction = AtomicBundleTransaction(installed, state)
            record = transaction.prepare(
                candidate,
                previous_revision="0" * 40,
                target_revision=revision,
            )
            transaction.swap(record, candidate)
            if new_marker.exists() or (installed / "Contents/version").read_bytes() != b"candidate":
                return False
            result = transaction.recover()
            return (
                result == "rolled_back"
                and (installed / "Contents/version").read_bytes() == b"previous"
                and not transaction.journal_path.exists()
                and not transaction_root.exists()
            )
    except (OSError, ValueError):
        return False


def _contains_absolute_paths(value: object) -> bool:
    if isinstance(value, dict):
        return any(_contains_absolute_paths(item) for item in value.values())
    if isinstance(value, list):
        return any(_contains_absolute_paths(item) for item in value)
    return isinstance(value, str) and value.startswith(("/Users/", "/private/", "/tmp/"))


def _contains_secret_material(payload: bytes) -> bool:
    normalized = payload.lower()
    return any(
        marker in normalized
        for marker in (
            b"nvapi-",
            b"-----begin private key-----",
            b"ed25519-private:",
            b"sk-proj-",
        )
    )


def _read_private(path: Path, maximum: int) -> bytes:
    metadata = path.lstat()
    if (
        stat.S_ISLNK(metadata.st_mode)
        or not stat.S_ISREG(metadata.st_mode)
        or metadata.st_uid != os.getuid()
        or stat.S_IMODE(metadata.st_mode) & 0o077
        or not 1 <= metadata.st_size <= maximum
    ):
        raise SecureUpdateQualificationError("P14 evidence file is unsafe")
    return path.read_bytes()


def _hash_private(path: Path, maximum: int) -> tuple[str, int]:
    metadata = path.lstat()
    if (
        stat.S_ISLNK(metadata.st_mode)
        or not stat.S_ISREG(metadata.st_mode)
        or metadata.st_uid != os.getuid()
        or stat.S_IMODE(metadata.st_mode) & 0o077
        or not 1 <= metadata.st_size <= maximum
    ):
        raise SecureUpdateQualificationError("P14 archive is unsafe")
    digest = hashlib.sha256()
    with path.open("rb") as source:
        while chunk := source.read(1_024 * 1_024):
            digest.update(chunk)
    return digest.hexdigest(), metadata.st_size


def _run_git(project_root: Path, *arguments: str) -> bytes:
    try:
        result = subprocess.run(
            ("/usr/bin/git", "-C", str(project_root), *arguments),
            stdin=subprocess.DEVNULL,
            capture_output=True,
            check=False,
            timeout=30,
        )
    except (OSError, subprocess.SubprocessError) as error:
        raise SecureUpdateQualificationError("Git inspection failed") from error
    if result.returncode != 0:
        raise SecureUpdateQualificationError("Git inspection failed")
    return result.stdout


def _source_revision(project_root: Path) -> str:
    revision = _run_git(project_root, "rev-parse", "--verify", "HEAD").decode().strip()
    if re.fullmatch(r"[0-9a-f]{40}", revision) is None:
        raise SecureUpdateQualificationError("source revision is invalid")
    return revision


def _require_clean_tracked_tree(project_root: Path) -> None:
    for arguments in (("diff", "--quiet"), ("diff", "--cached", "--quiet")):
        result = subprocess.run(
            ("/usr/bin/git", "-C", str(project_root), *arguments),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
            timeout=30,
        )
        if result.returncode != 0:
            raise SecureUpdateQualificationError("tracked source tree is dirty")
