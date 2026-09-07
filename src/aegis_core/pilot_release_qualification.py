from __future__ import annotations

import hashlib
import json
import os
import plistlib
import stat
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from aegis_core.build_info import is_build_revision
from aegis_core.macos_qualification import (
    QualificationCheck,
    QualificationStatus,
)

PILOT_QUALIFICATION_SCHEMA_VERSION = "1.0"
PILOT_QUALIFICATION_CHECKS = 5
MAX_QUALIFICATION_BYTES = 256 * 1_024
MAX_MANIFEST_BYTES = 512 * 1_024
MAX_SBOM_BYTES = 4 * 1_024 * 1_024
MAX_ARCHIVE_BYTES = 2 * 1_024 * 1_024 * 1_024

VOICE_CHECK_IDS = frozenset(
    {
        "voice.runtime",
        "voice.adversarial_calibration",
        "voice.wake_word_activation",
        "voice.conversation_continuity",
        "voice.organic_interruption",
    }
)
VOICE_PRIVACY_FIELDS = frozenset(
    {
        "contains_prompt_text",
        "contains_transcripts",
        "contains_audio",
        "contains_speaker_identifier",
        "network_calls",
    }
)
MACOS_CHECK_IDS = frozenset(
    {
        "runtime.security",
        "macos.tcc",
        "voice.owner_gate",
        "automation.visual",
        "latency.endurance",
    }
)
MACOS_PRIVACY_FIELDS = frozenset(
    {
        "contains_prompt_text",
        "contains_target_urls",
        "contains_transcripts",
        "contains_audio",
        "contains_images",
        "network_calls",
    }
)
PRIVATE_MODEL_DIRECTORIES = frozenset(
    {"JarvisSpeakerIdentity.mlmodelc", "JarvisWakeWord.mlmodelc"}
)


class PilotReleaseQualificationError(RuntimeError):
    """P11 evidence is absent, ambiguous, unsafe, or internally inconsistent."""


class _InputCheck(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    check_id: str = Field(min_length=3, max_length=96, pattern=r"^[a-z][a-z0-9._-]+$")
    status: QualificationStatus
    reason: str = Field(min_length=3, max_length=96, pattern=r"^[a-z][a-z0-9_]*$")
    metrics: dict[str, bool | int | float | str | None] = Field(max_length=64)


class _QualificationArtifact(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["1.0"]
    profile: Literal["live_owner_voice_qualification", "live_macos_qualification"]
    build_revision: str = Field(pattern=r"^[0-9a-f]{40}$")
    status: QualificationStatus
    score: int = Field(ge=0, le=100)
    gate_passed: bool
    passed: int = Field(ge=0, le=64)
    total: int = Field(ge=1, le=64)
    duration_ms: int = Field(ge=0, le=3_600_000)
    checks: tuple[_InputCheck, ...] = Field(min_length=1, max_length=64)
    privacy: dict[str, bool | int] = Field(min_length=1, max_length=32)

    @model_validator(mode="after")
    def validate_summary(self) -> _QualificationArtifact:
        passed = sum(check.status is QualificationStatus.PASSED for check in self.checks)
        expected_score = round((passed / len(self.checks)) * 100)
        fully_passed = passed == len(self.checks)
        if self.total != len(self.checks) or self.passed != passed or self.score != expected_score:
            raise ValueError("qualification summary is inconsistent")
        if self.gate_passed != fully_passed:
            raise ValueError("qualification gate result is inconsistent")
        states = {check.status for check in self.checks}
        expected_status = (
            QualificationStatus.PASSED
            if fully_passed
            else QualificationStatus.BLOCKED
            if QualificationStatus.BLOCKED in states
            else QualificationStatus.NEEDS_ATTENTION
            if QualificationStatus.NEEDS_ATTENTION in states
            else QualificationStatus.NEEDS_INTERACTION
        )
        if self.status is not expected_status:
            raise ValueError("qualification status is inconsistent")
        identifiers = [check.check_id for check in self.checks]
        if len(identifiers) != len(set(identifiers)):
            raise ValueError("qualification contains duplicate checks")
        return self


class _ReleaseArtifact(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    name: Literal["Jarvis.zip"]
    format: Literal["zip"]
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    size: int = Field(gt=0, le=MAX_ARCHIVE_BYTES)
    uncompressed_size: int = Field(gt=0, le=MAX_ARCHIVE_BYTES)


class _ReleaseSBOM(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    name: Literal["Jarvis.spdx.json"]
    format: Literal["SPDX-2.3"]
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class _BundleFile(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    path: str = Field(min_length=12, max_length=512)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    size: int = Field(ge=0, le=512 * 1_024 * 1_024)

    @model_validator(mode="after")
    def validate_path(self) -> _BundleFile:
        path = PurePosixPath(self.path)
        if (
            self.path.startswith("/")
            or "\\" in self.path
            or not path.parts
            or path.parts[0] != "Jarvis.app"
            or any(part in {"", ".", ".."} for part in path.parts)
        ):
            raise ValueError("bundle file path is unsafe")
        if not PRIVATE_MODEL_DIRECTORIES.isdisjoint(path.parts):
            raise ValueError("pilot candidate contains a private biometric model")
        return self


class _ReleasePrivacy(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    contains_prompts: Literal[False]
    contains_transcripts: Literal[False]
    contains_images: Literal[False]
    contains_credentials: Literal[False]
    contains_absolute_paths: Literal[False]


class _ReleaseManifest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["1.0"]
    product: Literal["Jarvis"]
    bundle_identifier: Literal["ai.aegis.menubar"]
    version: str = Field(min_length=1, max_length=64)
    build: str = Field(min_length=1, max_length=64)
    build_revision: str = Field(pattern=r"^[0-9a-f]{40}$")
    source_tree_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    created_at: str = Field(min_length=20, max_length=40)
    profile: Literal["local-development"]
    architecture: Literal["arm64"]
    minimum_macos: str = Field(min_length=2, max_length=32)
    artifact: _ReleaseArtifact
    bundle_files: tuple[_BundleFile, ...] = Field(min_length=1, max_length=4_096)
    sbom: _ReleaseSBOM
    privacy: _ReleasePrivacy

    @model_validator(mode="after")
    def validate_bundle_members(self) -> _ReleaseManifest:
        paths = [item.path for item in self.bundle_files]
        if len(paths) != len(set(paths)):
            raise ValueError("release manifest contains duplicate bundle paths")
        required = {
            "Jarvis.app/Contents/Info.plist",
            "Jarvis.app/Contents/MacOS/Jarvis",
        }
        if not required.issubset(paths):
            raise ValueError("release manifest omits required bundle files")
        return self


@dataclass(frozen=True, slots=True)
class PilotReleaseQualificationReport:
    build_revision: str
    candidate_sha256: str
    evidence_sha256: dict[str, str]
    checks: tuple[QualificationCheck, ...]
    duration_ms: int

    @property
    def passed(self) -> int:
        return sum(check.status is QualificationStatus.PASSED for check in self.checks)

    @property
    def score(self) -> int:
        return round((self.passed / PILOT_QUALIFICATION_CHECKS) * 100)

    @property
    def gate_passed(self) -> bool:
        return len(self.checks) == PILOT_QUALIFICATION_CHECKS and self.passed == len(
            self.checks
        )

    @property
    def status(self) -> QualificationStatus:
        states = {check.status for check in self.checks}
        if self.gate_passed:
            return QualificationStatus.PASSED
        if QualificationStatus.BLOCKED in states:
            return QualificationStatus.BLOCKED
        if QualificationStatus.NEEDS_ATTENTION in states:
            return QualificationStatus.NEEDS_ATTENTION
        return QualificationStatus.NEEDS_INTERACTION

    def private_dict(self) -> dict[str, object]:
        return {
            "schema_version": PILOT_QUALIFICATION_SCHEMA_VERSION,
            "profile": "local_pilot_release_qualification",
            "build_revision": self.build_revision,
            "status": self.status.value,
            "score": self.score,
            "gate_passed": self.gate_passed,
            "passed": self.passed,
            "total": len(self.checks),
            "duration_ms": self.duration_ms,
            "candidate_sha256": self.candidate_sha256,
            "evidence_sha256": dict(sorted(self.evidence_sha256.items())),
            "checks": [check.private_dict() for check in self.checks],
            "privacy": {
                "contains_prompt_text": False,
                "contains_transcripts": False,
                "contains_audio": False,
                "contains_images": False,
                "contains_credentials": False,
                "contains_absolute_paths": False,
                "network_calls": 0,
            },
        }

    def private_json(self) -> str:
        return json.dumps(
            self.private_dict(),
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        )


class PilotReleaseQualificationGate:
    def __init__(
        self,
        *,
        project_root: Path,
        installed_info_path: Path,
        voice_report_path: Path,
        macos_report_path: Path,
        release_manifest_path: Path,
        release_sbom_path: Path,
        release_archive_path: Path,
    ) -> None:
        self._project_root = project_root
        self._installed_info_path = installed_info_path
        self._voice_report_path = voice_report_path
        self._macos_report_path = macos_report_path
        self._release_manifest_path = release_manifest_path
        self._release_sbom_path = release_sbom_path
        self._release_archive_path = release_archive_path

    def run(self) -> PilotReleaseQualificationReport:
        started = time.monotonic_ns()
        revision = _source_revision(self._project_root)
        _require_clean_tracked_tree(self._project_root)
        source_tree_sha256 = _source_tree_sha256(self._project_root)
        installed_revision = _installed_revision(self._installed_info_path)
        voice_bytes = _read_private_bytes(
            self._voice_report_path,
            maximum_bytes=MAX_QUALIFICATION_BYTES,
        )
        macos_bytes = _read_private_bytes(
            self._macos_report_path,
            maximum_bytes=MAX_QUALIFICATION_BYTES,
        )
        manifest_bytes = _read_private_bytes(
            self._release_manifest_path,
            maximum_bytes=MAX_MANIFEST_BYTES,
        )
        sbom_sha256, _ = _hash_private_file(
            self._release_sbom_path,
            maximum_bytes=MAX_SBOM_BYTES,
        )
        archive_sha256, archive_size = _hash_private_file(
            self._release_archive_path,
            maximum_bytes=MAX_ARCHIVE_BYTES,
        )
        try:
            voice = _QualificationArtifact.model_validate_json(voice_bytes)
            macos = _QualificationArtifact.model_validate_json(macos_bytes)
            manifest = _ReleaseManifest.model_validate_json(manifest_bytes)
        except ValidationError as error:
            raise PilotReleaseQualificationError("P11 evidence schema is invalid") from error

        checks = (
            self._identity_check(
                revision=revision,
                installed_revision=installed_revision,
                voice=voice,
                macos=macos,
                manifest=manifest,
                source_tree_sha256=source_tree_sha256,
            ),
            self._qualification_check(
                "quality.owner_voice",
                voice,
                expected_profile="live_owner_voice_qualification",
                expected_ids=VOICE_CHECK_IDS,
            ),
            self._qualification_check(
                "quality.live_macos",
                macos,
                expected_profile="live_macos_qualification",
                expected_ids=MACOS_CHECK_IDS,
            ),
            self._candidate_check(
                manifest,
                archive_sha256=archive_sha256,
                archive_size=archive_size,
                sbom_sha256=sbom_sha256,
            ),
            self._privacy_check(voice, macos, manifest),
        )
        elapsed = max(0, (time.monotonic_ns() - started) // 1_000_000)
        return PilotReleaseQualificationReport(
            build_revision=revision,
            candidate_sha256=archive_sha256,
            evidence_sha256={
                "voice": hashlib.sha256(voice_bytes).hexdigest(),
                "macos": hashlib.sha256(macos_bytes).hexdigest(),
                "release_manifest": hashlib.sha256(manifest_bytes).hexdigest(),
                "sbom": sbom_sha256,
            },
            checks=checks,
            duration_ms=min(elapsed, 2_147_483_647),
        )

    @staticmethod
    def _identity_check(
        *,
        revision: str,
        installed_revision: str,
        voice: _QualificationArtifact,
        macos: _QualificationArtifact,
        manifest: _ReleaseManifest,
        source_tree_sha256: str,
    ) -> QualificationCheck:
        matches = (
            installed_revision == revision
            and voice.build_revision == revision
            and macos.build_revision == revision
            and manifest.build_revision == revision
            and manifest.source_tree_sha256 == source_tree_sha256
        )
        return QualificationCheck(
            "release.identity",
            QualificationStatus.PASSED if matches else QualificationStatus.BLOCKED,
            "verified" if matches else "build_revision_mismatch",
            {
                "installed_matches": installed_revision == revision,
                "voice_matches": voice.build_revision == revision,
                "macos_matches": macos.build_revision == revision,
                "candidate_matches": manifest.build_revision == revision,
                "source_tree_matches": manifest.source_tree_sha256 == source_tree_sha256,
            },
        )

    @staticmethod
    def _qualification_check(
        check_id: str,
        artifact: _QualificationArtifact,
        *,
        expected_profile: str,
        expected_ids: frozenset[str],
    ) -> QualificationCheck:
        identifiers = frozenset(check.check_id for check in artifact.checks)
        structure_valid = artifact.profile == expected_profile and identifiers == expected_ids
        passed = structure_valid and artifact.gate_passed
        if not structure_valid:
            status = QualificationStatus.BLOCKED
            reason = "qualification_contract_mismatch"
        elif passed:
            status = QualificationStatus.PASSED
            reason = "verified"
        else:
            status = artifact.status
            reason = "prerequisite_not_passed"
        return QualificationCheck(
            check_id,
            status,
            reason,
            {
                "contract_matches": structure_valid,
                "source_score": artifact.score,
                "source_passed": artifact.passed,
                "source_total": artifact.total,
            },
        )

    @staticmethod
    def _candidate_check(
        manifest: _ReleaseManifest,
        *,
        archive_sha256: str,
        archive_size: int,
        sbom_sha256: str,
    ) -> QualificationCheck:
        passed = (
            manifest.artifact.sha256 == archive_sha256
            and manifest.artifact.size == archive_size
            and manifest.sbom.sha256 == sbom_sha256
        )
        return QualificationCheck(
            "release.candidate",
            QualificationStatus.PASSED if passed else QualificationStatus.BLOCKED,
            "verified" if passed else "release_evidence_mismatch",
            {
                "profile_local_development": manifest.profile == "local-development",
                "archive_hash_matches": manifest.artifact.sha256 == archive_sha256,
                "archive_size_matches": manifest.artifact.size == archive_size,
                "sbom_hash_matches": manifest.sbom.sha256 == sbom_sha256,
                "bundle_files": len(manifest.bundle_files),
            },
        )

    @staticmethod
    def _privacy_check(
        voice: _QualificationArtifact,
        macos: _QualificationArtifact,
        manifest: _ReleaseManifest,
    ) -> QualificationCheck:
        voice_clear = _privacy_map_is_clear(
            voice.privacy,
            required=VOICE_PRIVACY_FIELDS,
        )
        macos_clear = _privacy_map_is_clear(
            macos.privacy,
            required=MACOS_PRIVACY_FIELDS,
        )
        private = voice_clear and macos_clear
        return QualificationCheck(
            "privacy.release_boundary",
            QualificationStatus.PASSED if private else QualificationStatus.BLOCKED,
            "verified" if private else "private_content_detected",
            {
                "voice_evidence_clear": voice_clear,
                "macos_evidence_clear": macos_clear,
                "candidate_evidence_clear": all(
                    value is False for value in manifest.privacy.model_dump().values()
                ),
                "network_calls": 0,
            },
        )


def _privacy_map_is_clear(
    privacy: dict[str, bool | int],
    *,
    required: frozenset[str],
) -> bool:
    if not required.issubset(privacy):
        return False
    for name, value in privacy.items():
        if name.startswith("contains_") and value is not False:
            return False
        if name in {"network_calls", "network_attempts"} and value != 0:
            return False
    return True


def _run_git(project_root: Path, *arguments: str) -> bytes:
    try:
        completed = subprocess.run(
            ("/usr/bin/git", "-C", str(project_root), *arguments),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            check=False,
            timeout=30,
        )
    except (OSError, subprocess.SubprocessError) as error:
        raise PilotReleaseQualificationError("Git inspection failed") from error
    if completed.returncode != 0:
        raise PilotReleaseQualificationError("Git inspection failed")
    return completed.stdout


def _source_revision(project_root: Path) -> str:
    try:
        revision = _run_git(project_root, "rev-parse", "--verify", "HEAD").decode(
            "ascii"
        ).strip()
    except UnicodeDecodeError as error:
        raise PilotReleaseQualificationError("source revision is invalid") from error
    if not is_build_revision(revision) or revision == "development":
        raise PilotReleaseQualificationError("source revision is invalid")
    return revision


def _require_clean_tracked_tree(project_root: Path) -> None:
    for arguments in (("diff", "--quiet"), ("diff", "--cached", "--quiet")):
        try:
            completed = subprocess.run(
                ("/usr/bin/git", "-C", str(project_root), *arguments),
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=False,
                timeout=30,
            )
        except (OSError, subprocess.SubprocessError) as error:
            raise PilotReleaseQualificationError("source tree inspection failed") from error
        if completed.returncode != 0:
            raise PilotReleaseQualificationError("tracked source tree is dirty")


def _source_tree_sha256(project_root: Path) -> str:
    encoded_paths = _run_git(project_root, "ls-files", "-z")
    digest = hashlib.sha256()
    for encoded_path in sorted(item for item in encoded_paths.split(b"\0") if item):
        try:
            relative = encoded_path.decode("utf-8")
            path = project_root / relative
            metadata = path.lstat()
        except (OSError, UnicodeDecodeError) as error:
            raise PilotReleaseQualificationError("tracked source cannot be inspected") from error
        digest.update(encoded_path)
        digest.update(b"\0")
        if stat.S_ISLNK(metadata.st_mode):
            digest.update(b"symlink\0")
            digest.update(os.fsencode(os.readlink(path)))
        elif stat.S_ISREG(metadata.st_mode):
            digest.update(b"file\0")
            try:
                with path.open("rb") as source:
                    while chunk := source.read(1024 * 1024):
                        digest.update(chunk)
            except OSError as error:
                raise PilotReleaseQualificationError("tracked source cannot be read") from error
        else:
            raise PilotReleaseQualificationError("tracked source object is unsupported")
        digest.update(b"\0")
    return digest.hexdigest()


def _installed_revision(info_path: Path) -> str:
    try:
        metadata = info_path.lstat()
        bundle_path = info_path.parents[1]
        if (
            not stat.S_ISREG(metadata.st_mode)
            or stat.S_ISLNK(metadata.st_mode)
            or metadata.st_uid != os.getuid()
            or metadata.st_size < 1
            or metadata.st_size > 65_536
            or bundle_path.is_symlink()
            or not bundle_path.is_dir()
        ):
            raise PilotReleaseQualificationError("installed bundle metadata is unsafe")
        with info_path.open("rb") as source:
            payload = plistlib.load(source)
    except (OSError, plistlib.InvalidFileException) as error:
        raise PilotReleaseQualificationError("installed bundle metadata is invalid") from error
    revision = payload.get("AegisBuildRevision") if isinstance(payload, dict) else None
    if not is_build_revision(revision) or revision == "development":
        raise PilotReleaseQualificationError("installed build revision is invalid")
    return revision


def _open_private_file(path: Path, *, maximum_bytes: int) -> tuple[int, os.stat_result]:
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    descriptor = -1
    try:
        descriptor = os.open(path, flags)
        metadata = os.fstat(descriptor)
    except OSError as error:
        if descriptor >= 0:
            os.close(descriptor)
        raise PilotReleaseQualificationError("P11 evidence is unavailable") from error
    if (
        not stat.S_ISREG(metadata.st_mode)
        or stat.S_ISLNK(metadata.st_mode)
        or metadata.st_uid != os.getuid()
        or stat.S_IMODE(metadata.st_mode) != 0o600
        or not 1 <= metadata.st_size <= maximum_bytes
    ):
        os.close(descriptor)
        raise PilotReleaseQualificationError("P11 evidence file is unsafe")
    return descriptor, metadata


def _read_private_bytes(path: Path, *, maximum_bytes: int) -> bytes:
    descriptor, metadata = _open_private_file(path, maximum_bytes=maximum_bytes)
    try:
        with os.fdopen(descriptor, "rb") as source:
            payload = source.read(maximum_bytes + 1)
    except OSError as error:
        raise PilotReleaseQualificationError("P11 evidence cannot be read") from error
    if len(payload) != metadata.st_size:
        raise PilotReleaseQualificationError("P11 evidence changed while being read")
    return payload


def _hash_private_file(path: Path, *, maximum_bytes: int) -> tuple[str, int]:
    descriptor, metadata = _open_private_file(path, maximum_bytes=maximum_bytes)
    digest = hashlib.sha256()
    measured = 0
    try:
        with os.fdopen(descriptor, "rb") as source:
            while chunk := source.read(1024 * 1024):
                measured += len(chunk)
                if measured > maximum_bytes:
                    raise PilotReleaseQualificationError("P11 evidence exceeds its size budget")
                digest.update(chunk)
    except OSError as error:
        raise PilotReleaseQualificationError("P11 evidence cannot be hashed") from error
    if measured != metadata.st_size:
        raise PilotReleaseQualificationError("P11 evidence changed while being hashed")
    return digest.hexdigest(), measured
