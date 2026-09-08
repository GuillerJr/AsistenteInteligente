from __future__ import annotations

import hashlib
import json
import os
import plistlib
import re
import stat
import subprocess
import tempfile
import time
import zipfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import ClassVar, Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from aegis_core.build_info import is_build_revision
from aegis_core.macos_qualification import QualificationCheck, QualificationStatus

DISTRIBUTION_QUALIFICATION_SCHEMA_VERSION = "1.0"
DISTRIBUTION_QUALIFICATION_CHECKS = 5
MAX_REPORT_BYTES = 256 * 1_024
MAX_MANIFEST_BYTES = 512 * 1_024
MAX_SBOM_BYTES = 4 * 1_024 * 1_024
MAX_ARCHIVE_BYTES = 2 * 1_024 * 1_024 * 1_024
MAX_ARCHIVE_MEMBERS = 4_096
MAX_ARCHIVE_MEMBER_BYTES = 512 * 1_024 * 1_024
EXPECTED_PILOT_CHECKS = frozenset(
    {
        "release.identity",
        "quality.owner_voice",
        "quality.live_macos",
        "release.candidate",
        "privacy.release_boundary",
    }
)
PRIVATE_MODEL_DIRECTORIES = frozenset(
    {"JarvisSpeakerIdentity.mlmodelc", "JarvisWakeWord.mlmodelc"}
)
REQUIRED_ENTITLEMENTS = frozenset(
    {
        "com.apple.security.automation.apple-events",
        "com.apple.security.device.audio-input",
        "com.apple.security.device.camera",
        "com.apple.security.device.microphone",
        "com.apple.security.personal-information.addressbook",
        "com.apple.security.personal-information.calendars",
    }
)
FORBIDDEN_ENTITLEMENTS = frozenset(
    {
        "com.apple.security.cs.allow-dyld-environment-variables",
        "com.apple.security.cs.allow-unsigned-executable-memory",
        "com.apple.security.cs.disable-executable-page-protection",
        "com.apple.security.cs.disable-library-validation",
        "com.apple.security.cs.get-task-allow",
        "get-task-allow",
    }
)


class DistributionReleaseQualificationError(RuntimeError):
    """P12 cannot authenticate or assess the distribution candidate."""


class _PilotCheck(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    check_id: str = Field(min_length=3, max_length=96, pattern=r"^[a-z][a-z0-9._-]+$")
    status: QualificationStatus
    reason: str = Field(min_length=3, max_length=96, pattern=r"^[a-z][a-z0-9_]*$")
    metrics: dict[str, bool | int | float | str | None] = Field(max_length=64)


class _PilotReport(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["1.0"]
    profile: Literal["local_pilot_release_qualification"]
    build_revision: str = Field(pattern=r"^[0-9a-f]{40}$")
    status: QualificationStatus
    score: int = Field(ge=0, le=100)
    gate_passed: bool
    passed: int = Field(ge=0, le=DISTRIBUTION_QUALIFICATION_CHECKS)
    total: Literal[5]
    duration_ms: int = Field(ge=0, le=3_600_000)
    candidate_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    evidence_sha256: dict[str, str] = Field(min_length=4, max_length=4)
    checks: tuple[_PilotCheck, ...] = Field(min_length=5, max_length=5)
    privacy: dict[str, bool | int] = Field(min_length=7, max_length=16)

    @model_validator(mode="after")
    def validate_contract(self) -> _PilotReport:
        identifiers = [check.check_id for check in self.checks]
        actual_passed = sum(
            check.status is QualificationStatus.PASSED for check in self.checks
        )
        if len(identifiers) != len(set(identifiers)):
            raise ValueError("pilot report contains duplicate checks")
        if frozenset(identifiers) != EXPECTED_PILOT_CHECKS:
            raise ValueError("pilot report contract is incomplete")
        if self.passed != actual_passed or self.score != round(actual_passed / 5 * 100):
            raise ValueError("pilot report summary is inconsistent")
        if self.gate_passed != (actual_passed == 5):
            raise ValueError("pilot report gate is inconsistent")
        expected_status = (
            QualificationStatus.PASSED
            if self.gate_passed
            else QualificationStatus.BLOCKED
            if QualificationStatus.BLOCKED
            in {check.status for check in self.checks}
            else QualificationStatus.NEEDS_ATTENTION
            if QualificationStatus.NEEDS_ATTENTION
            in {check.status for check in self.checks}
            else QualificationStatus.NEEDS_INTERACTION
        )
        if self.status is not expected_status:
            raise ValueError("pilot report status is inconsistent")
        if set(self.evidence_sha256) != {
            "voice",
            "macos",
            "release_manifest",
            "sbom",
        } or any(
            re.fullmatch(r"[0-9a-f]{64}", digest) is None
            for digest in self.evidence_sha256.values()
        ):
            raise ValueError("pilot evidence hashes are invalid")
        if any(
            key.startswith("contains_") and value is not False
            for key, value in self.privacy.items()
        ):
            raise ValueError("pilot report contains private data")
        if self.privacy.get("network_calls") != 0:
            raise ValueError("pilot qualification unexpectedly used the network")
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
    size: int = Field(ge=0, le=MAX_ARCHIVE_MEMBER_BYTES)

    @model_validator(mode="after")
    def validate_path(self) -> _BundleFile:
        path = _safe_archive_path(self.path)
        if not PRIVATE_MODEL_DIRECTORIES.isdisjoint(path.parts):
            raise ValueError("distribution contains a private biometric model")
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
    profile: Literal["developer-id-notarized"]
    architecture: Literal["arm64"]
    minimum_macos: str = Field(min_length=2, max_length=32)
    artifact: _ReleaseArtifact
    bundle_files: tuple[_BundleFile, ...] = Field(min_length=2, max_length=MAX_ARCHIVE_MEMBERS)
    sbom: _ReleaseSBOM
    privacy: _ReleasePrivacy

    @model_validator(mode="after")
    def validate_bundle(self) -> _ReleaseManifest:
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
class MacOSDistributionAssessment:
    code_signature_valid: bool
    identifier_valid: bool
    developer_id_valid: bool
    team_identifier_present: bool
    hardened_runtime: bool
    secure_timestamp: bool
    required_entitlements_present: bool
    forbidden_entitlements_absent: bool
    stapled_ticket_valid: bool
    gatekeeper_accepted: bool


class DistributionArtifactAssessor(Protocol):
    def assess(self, archive_path: Path) -> MacOSDistributionAssessment: ...


class MacOSDistributionArtifactAssessor:
    """Assess the exact ZIP on a private temporary extraction, never a staging bundle."""

    _ENVIRONMENT: ClassVar[dict[str, str]] = {
        "HOME": str(Path.home()),
        "LANG": "C",
        "LC_ALL": "C",
        "PATH": "/usr/bin:/bin:/usr/sbin:/sbin",
    }

    def __init__(self, expected_codesign_identity: str) -> None:
        match = re.fullmatch(
            r"Developer ID Application: .+ \(([A-Z0-9]{10})\)",
            expected_codesign_identity,
        )
        if match is None:
            raise DistributionReleaseQualificationError(
                "Developer ID identity is invalid"
            )
        self._expected_codesign_identity = expected_codesign_identity
        self._expected_team_identifier = match.group(1)

    def assess(self, archive_path: Path) -> MacOSDistributionAssessment:
        if os.uname().sysname != "Darwin":
            raise DistributionReleaseQualificationError("P12 requires macOS")
        try:
            with tempfile.TemporaryDirectory(prefix="aegis-p12-", dir="/private/tmp") as root:
                extraction_root = Path(root)
                extraction_root.chmod(0o700)
                extracted = self._run(
                    "/usr/bin/ditto",
                    "-x",
                    "-k",
                    str(archive_path),
                    str(extraction_root),
                )
                if extracted.returncode != 0:
                    raise DistributionReleaseQualificationError(
                        "distribution archive extraction failed"
                    )
                app = extraction_root / "Jarvis.app"
                self._validate_extracted_tree(app)
                return self._assess_bundle(app)
        except OSError as error:
            raise DistributionReleaseQualificationError(
                "distribution assessment failed"
            ) from error

    def _assess_bundle(self, app: Path) -> MacOSDistributionAssessment:
        verified = self._run(
            "/usr/bin/codesign",
            "--verify",
            "--deep",
            "--strict",
            "--verbose=4",
            str(app),
        )
        details = self._run("/usr/bin/codesign", "-dvvv", str(app))
        detail_text = details.stdout + details.stderr
        entitlements_result = self._run(
            "/usr/bin/codesign",
            "--display",
            "--entitlements",
            "-",
            str(app),
        )
        entitlements = self._parse_entitlements(
            entitlements_result.stdout + entitlements_result.stderr
        )
        stapler = self._run("/usr/bin/xcrun", "stapler", "validate", str(app))
        gatekeeper = self._run(
            "/usr/sbin/spctl",
            "--assess",
            "--type",
            "execute",
            "--verbose=4",
            str(app),
        )
        authority = re.search(r"(?m)^Authority=(.+)$", detail_text)
        team = re.search(r"(?m)^TeamIdentifier=([A-Z0-9]{10})$", detail_text)
        timestamp = re.search(r"(?m)^Timestamp=(.+)$", detail_text)
        identifier_valid = "Identifier=ai.aegis.menubar" in detail_text
        developer_id_valid = bool(
            authority
            and authority.group(1).strip() == self._expected_codesign_identity
            and "Signature=adhoc" not in detail_text
        )
        hardened_runtime = bool(
            re.search(r"(?m)^Runtime Version=", detail_text)
            or re.search(r"(?m)^CodeDirectory .*flags=.*\(runtime\)", detail_text)
        )
        secure_timestamp = bool(
            timestamp and timestamp.group(1).strip().lower() not in {"", "none"}
        )
        required_present = all(entitlements.get(key) is True for key in REQUIRED_ENTITLEMENTS)
        forbidden_absent = all(entitlements.get(key) is not True for key in FORBIDDEN_ENTITLEMENTS)
        return MacOSDistributionAssessment(
            code_signature_valid=verified.returncode == 0 and details.returncode == 0,
            identifier_valid=identifier_valid,
            developer_id_valid=developer_id_valid,
            team_identifier_present=bool(
                team and team.group(1) == self._expected_team_identifier
            ),
            hardened_runtime=hardened_runtime,
            secure_timestamp=secure_timestamp,
            required_entitlements_present=required_present,
            forbidden_entitlements_absent=forbidden_absent,
            stapled_ticket_valid=stapler.returncode == 0,
            gatekeeper_accepted=gatekeeper.returncode == 0,
        )

    @classmethod
    def _run(cls, executable: str, *arguments: str) -> subprocess.CompletedProcess[str]:
        try:
            return subprocess.run(
                (executable, *arguments),
                stdin=subprocess.DEVNULL,
                capture_output=True,
                text=True,
                check=False,
                timeout=120,
                env=cls._ENVIRONMENT,
            )
        except (OSError, subprocess.SubprocessError) as error:
            raise DistributionReleaseQualificationError(
                "native distribution command failed"
            ) from error

    @staticmethod
    def _parse_entitlements(output: str) -> dict[str, object]:
        starts = [
            position
            for position in (output.find("<?xml"), output.find("<plist"))
            if position >= 0
        ]
        end = output.rfind("</plist>")
        if starts and end >= 0:
            payload = output[min(starts) : end + len("</plist>")].encode("utf-8")
            try:
                decoded = plistlib.loads(payload)
            except plistlib.InvalidFileException:
                decoded = None
            if isinstance(decoded, dict):
                return decoded
        entries = re.findall(
            r"(?m)^\s*\[Key\]\s+(\S+)\s*\n"
            r"\s*\[Value\]\s*\n"
            r"\s*\[Bool\]\s+(true|false)\s*$",
            output,
        )
        return {key: value == "true" for key, value in entries}

    @staticmethod
    def _validate_extracted_tree(app: Path) -> None:
        if app.is_symlink() or not app.is_dir():
            raise DistributionReleaseQualificationError("extracted app is missing or unsafe")
        members = 0
        for root, directories, files in os.walk(app, followlinks=False):
            for name in (*directories, *files):
                members += 1
                if members > MAX_ARCHIVE_MEMBERS:
                    raise DistributionReleaseQualificationError(
                        "extracted app exceeds the member safety budget"
                    )
                path = Path(root) / name
                if path.is_symlink():
                    raise DistributionReleaseQualificationError(
                        "extracted app contains a symbolic link"
                    )


@dataclass(frozen=True, slots=True)
class DistributionReleaseQualificationReport:
    build_revision: str
    artifact_sha256: str
    evidence_sha256: dict[str, str]
    checks: tuple[QualificationCheck, ...]
    duration_ms: int

    @property
    def passed(self) -> int:
        return sum(check.status is QualificationStatus.PASSED for check in self.checks)

    @property
    def score(self) -> int:
        return round(self.passed / DISTRIBUTION_QUALIFICATION_CHECKS * 100)

    @property
    def gate_passed(self) -> bool:
        return (
            len(self.checks) == DISTRIBUTION_QUALIFICATION_CHECKS
            and self.passed == DISTRIBUTION_QUALIFICATION_CHECKS
        )

    @property
    def status(self) -> QualificationStatus:
        return QualificationStatus.PASSED if self.gate_passed else QualificationStatus.BLOCKED

    def private_dict(self) -> dict[str, object]:
        return {
            "schema_version": DISTRIBUTION_QUALIFICATION_SCHEMA_VERSION,
            "profile": "notarized_distribution_qualification",
            "build_revision": self.build_revision,
            "status": self.status.value,
            "score": self.score,
            "gate_passed": self.gate_passed,
            "passed": self.passed,
            "total": len(self.checks),
            "duration_ms": self.duration_ms,
            "artifact_sha256": self.artifact_sha256,
            "evidence_sha256": dict(sorted(self.evidence_sha256.items())),
            "checks": [check.private_dict() for check in self.checks],
            "privacy": {
                "contains_prompt_text": False,
                "contains_transcripts": False,
                "contains_audio": False,
                "contains_images": False,
                "contains_credentials": False,
                "contains_absolute_paths": False,
                "model_api_calls": 0,
                "apple_notary_transmits_user_content": False,
            },
        }

    def private_json(self) -> str:
        return json.dumps(
            self.private_dict(),
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        )


class DistributionReleaseQualificationGate:
    def __init__(
        self,
        *,
        project_root: Path,
        pilot_report_path: Path,
        release_manifest_path: Path,
        release_sbom_path: Path,
        release_archive_path: Path,
        expected_codesign_identity: str | None = None,
        assessor: DistributionArtifactAssessor | None = None,
    ) -> None:
        self._project_root = project_root
        self._pilot_report_path = pilot_report_path
        self._release_manifest_path = release_manifest_path
        self._release_sbom_path = release_sbom_path
        self._release_archive_path = release_archive_path
        if assessor is None:
            if expected_codesign_identity is None:
                raise DistributionReleaseQualificationError(
                    "Developer ID identity is required"
                )
            assessor = MacOSDistributionArtifactAssessor(expected_codesign_identity)
        self._assessor = assessor

    def run(self) -> DistributionReleaseQualificationReport:
        started = time.monotonic_ns()
        revision = _source_revision(self._project_root)
        _require_clean_tracked_tree(self._project_root)
        source_tree_sha256 = _source_tree_sha256(self._project_root)
        pilot_bytes = _read_private_bytes(self._pilot_report_path, MAX_REPORT_BYTES)
        manifest_bytes = _read_private_bytes(self._release_manifest_path, MAX_MANIFEST_BYTES)
        sbom_bytes = _read_private_bytes(self._release_sbom_path, MAX_SBOM_BYTES)
        archive_sha256, archive_size = _hash_private_file(
            self._release_archive_path,
            MAX_ARCHIVE_BYTES,
        )
        try:
            pilot = _PilotReport.model_validate_json(pilot_bytes)
            manifest = _ReleaseManifest.model_validate_json(manifest_bytes)
            sbom = json.loads(sbom_bytes)
        except (ValidationError, json.JSONDecodeError) as error:
            raise DistributionReleaseQualificationError(
                "P12 evidence schema is invalid"
            ) from error
        if not isinstance(sbom, dict):
            raise DistributionReleaseQualificationError("P12 SBOM schema is invalid")
        archive_metrics = _verify_archive(
            self._release_archive_path,
            manifest,
            archive_sha256=archive_sha256,
            archive_size=archive_size,
        )
        sbom_valid = _verify_sbom(sbom_bytes, sbom, manifest)
        assessment = self._assessor.assess(self._release_archive_path)
        checks = (
            self._pilot_check(pilot, revision),
            self._provenance_check(
                manifest,
                revision=revision,
                source_tree_sha256=source_tree_sha256,
                archive_metrics=archive_metrics,
                sbom_valid=sbom_valid,
            ),
            self._signature_check(assessment),
            self._notarization_check(assessment),
            self._privacy_check(manifest, archive_metrics),
        )
        elapsed = max(0, (time.monotonic_ns() - started) // 1_000_000)
        return DistributionReleaseQualificationReport(
            build_revision=revision,
            artifact_sha256=archive_sha256,
            evidence_sha256={
                "pilot": hashlib.sha256(pilot_bytes).hexdigest(),
                "release_manifest": hashlib.sha256(manifest_bytes).hexdigest(),
                "sbom": hashlib.sha256(sbom_bytes).hexdigest(),
            },
            checks=checks,
            duration_ms=min(elapsed, 2_147_483_647),
        )

    @staticmethod
    def _pilot_check(pilot: _PilotReport, revision: str) -> QualificationCheck:
        passed = pilot.gate_passed and pilot.build_revision == revision
        return QualificationCheck(
            "release.pilot_chain",
            QualificationStatus.PASSED if passed else QualificationStatus.BLOCKED,
            "verified" if passed else "pilot_prerequisite_not_passed",
            {
                "revision_matches": pilot.build_revision == revision,
                "pilot_score": pilot.score,
                "pilot_gate_passed": pilot.gate_passed,
            },
        )

    @staticmethod
    def _provenance_check(
        manifest: _ReleaseManifest,
        *,
        revision: str,
        source_tree_sha256: str,
        archive_metrics: dict[str, int | bool],
        sbom_valid: bool,
    ) -> QualificationCheck:
        passed = (
            manifest.build_revision == revision
            and manifest.source_tree_sha256 == source_tree_sha256
            and all(archive_metrics.values())
            and sbom_valid
        )
        return QualificationCheck(
            "release.provenance",
            QualificationStatus.PASSED if passed else QualificationStatus.BLOCKED,
            "verified" if passed else "release_provenance_mismatch",
            {
                "revision_matches": manifest.build_revision == revision,
                "source_tree_matches": manifest.source_tree_sha256 == source_tree_sha256,
                "archive_authenticated": all(archive_metrics.values()),
                "sbom_authenticated": sbom_valid,
                "bundle_files": len(manifest.bundle_files),
            },
        )

    @staticmethod
    def _signature_check(assessment: MacOSDistributionAssessment) -> QualificationCheck:
        fields = (
            assessment.code_signature_valid,
            assessment.identifier_valid,
            assessment.developer_id_valid,
            assessment.team_identifier_present,
            assessment.hardened_runtime,
            assessment.secure_timestamp,
            assessment.required_entitlements_present,
            assessment.forbidden_entitlements_absent,
        )
        passed = all(fields)
        return QualificationCheck(
            "security.developer_id",
            QualificationStatus.PASSED if passed else QualificationStatus.BLOCKED,
            "verified" if passed else "developer_id_assessment_failed",
            {
                "signature_valid": assessment.code_signature_valid,
                "identifier_valid": assessment.identifier_valid,
                "developer_id_valid": assessment.developer_id_valid,
                "team_identifier_present": assessment.team_identifier_present,
                "hardened_runtime": assessment.hardened_runtime,
                "secure_timestamp": assessment.secure_timestamp,
                "required_entitlements_present": assessment.required_entitlements_present,
                "forbidden_entitlements_absent": assessment.forbidden_entitlements_absent,
            },
        )

    @staticmethod
    def _notarization_check(assessment: MacOSDistributionAssessment) -> QualificationCheck:
        passed = assessment.stapled_ticket_valid and assessment.gatekeeper_accepted
        return QualificationCheck(
            "security.apple_notarization",
            QualificationStatus.PASSED if passed else QualificationStatus.BLOCKED,
            "verified" if passed else "notarization_assessment_failed",
            {
                "stapled_ticket_valid": assessment.stapled_ticket_valid,
                "gatekeeper_accepted": assessment.gatekeeper_accepted,
            },
        )

    @staticmethod
    def _privacy_check(
        manifest: _ReleaseManifest,
        archive_metrics: dict[str, int | bool],
    ) -> QualificationCheck:
        privacy_clear = all(value is False for value in manifest.privacy.model_dump().values())
        private_models_absent = archive_metrics["private_models_absent"] is True
        passed = privacy_clear and private_models_absent
        return QualificationCheck(
            "privacy.distribution_boundary",
            QualificationStatus.PASSED if passed else QualificationStatus.BLOCKED,
            "verified" if passed else "private_content_detected",
            {
                "manifest_clear": privacy_clear,
                "private_models_absent": private_models_absent,
                "model_api_calls": 0,
                "apple_notary_transmits_user_content": False,
            },
        )


def _safe_archive_path(name: str) -> PurePosixPath:
    path = PurePosixPath(name)
    if (
        not name
        or name.startswith("/")
        or "\\" in name
        or not path.parts
        or path.parts[0] != "Jarvis.app"
        or any(part in {"", ".", ".."} for part in path.parts)
    ):
        raise ValueError("archive path is unsafe")
    return path


def _verify_archive(
    archive_path: Path,
    manifest: _ReleaseManifest,
    *,
    archive_sha256: str,
    archive_size: int,
) -> dict[str, int | bool]:
    expected = {item.path: item for item in manifest.bundle_files}
    observed: set[str] = set()
    seen_members: set[str] = set()
    total_size = 0
    info_payload: bytes | None = None
    private_models_absent = True
    try:
        with zipfile.ZipFile(archive_path) as archive:
            members = archive.infolist()
            if not members or len(members) > MAX_ARCHIVE_MEMBERS:
                raise DistributionReleaseQualificationError(
                    "archive member count exceeds the safety budget"
                )
            for member in members:
                try:
                    member_path = _safe_archive_path(member.filename)
                except ValueError as error:
                    raise DistributionReleaseQualificationError(
                        "archive contains an unsafe path"
                    ) from error
                if member.flag_bits & 0x1:
                    raise DistributionReleaseQualificationError("archive contains encrypted data")
                if member.filename in seen_members:
                    raise DistributionReleaseQualificationError(
                        "archive contains duplicate paths"
                    )
                seen_members.add(member.filename)
                unix_mode = member.external_attr >> 16
                if stat.S_ISLNK(unix_mode):
                    raise DistributionReleaseQualificationError(
                        "archive contains a symbolic link"
                    )
                if not PRIVATE_MODEL_DIRECTORIES.isdisjoint(member_path.parts):
                    private_models_absent = False
                if member.is_dir():
                    continue
                observed.add(member.filename)
                expected_file = expected.get(member.filename)
                if expected_file is None or member.file_size != expected_file.size:
                    raise DistributionReleaseQualificationError(
                        "archive file inventory does not match the manifest"
                    )
                if member.file_size > MAX_ARCHIVE_MEMBER_BYTES:
                    raise DistributionReleaseQualificationError(
                        "archive member exceeds the safety budget"
                    )
                total_size += member.file_size
                if total_size > MAX_ARCHIVE_BYTES:
                    raise DistributionReleaseQualificationError(
                        "archive expansion exceeds the safety budget"
                    )
                digest = hashlib.sha256()
                with archive.open(member, "r") as source:
                    while chunk := source.read(1024 * 1024):
                        digest.update(chunk)
                if digest.hexdigest() != expected_file.sha256:
                    raise DistributionReleaseQualificationError(
                        "archive member authentication failed"
                    )
                if member.filename == "Jarvis.app/Contents/Info.plist":
                    info_payload = archive.read(member)
    except (OSError, RuntimeError, zipfile.BadZipFile) as error:
        if isinstance(error, DistributionReleaseQualificationError):
            raise
        raise DistributionReleaseQualificationError("archive cannot be inspected") from error
    if observed != set(expected):
        raise DistributionReleaseQualificationError(
            "release manifest file inventory is incomplete"
        )
    if not private_models_absent:
        raise DistributionReleaseQualificationError(
            "distribution contains a private biometric model"
        )
    if info_payload is None:
        raise DistributionReleaseQualificationError("bundle metadata is missing")
    try:
        info = plistlib.loads(info_payload)
    except plistlib.InvalidFileException as error:
        raise DistributionReleaseQualificationError("bundle metadata is invalid") from error
    identity_valid = isinstance(info, dict) and all(
        (
            info.get("CFBundleIdentifier") == "ai.aegis.menubar",
            info.get("CFBundleName") == "Jarvis",
            info.get("CFBundleExecutable") == "Jarvis",
            info.get("AegisBuildRevision") == manifest.build_revision,
            info.get("AegisBuildDirty") is False,
        )
    )
    return {
        "archive_hash_matches": manifest.artifact.sha256 == archive_sha256,
        "archive_size_matches": manifest.artifact.size == archive_size,
        "uncompressed_size_matches": manifest.artifact.uncompressed_size == total_size,
        "inventory_matches": observed == set(expected),
        "bundle_identity_matches": identity_valid,
        "private_models_absent": private_models_absent,
    }


def _verify_sbom(sbom_bytes: bytes, sbom: dict[str, object], manifest: _ReleaseManifest) -> bool:
    packages = sbom.get("packages")
    return bool(
        hashlib.sha256(sbom_bytes).hexdigest() == manifest.sbom.sha256
        and sbom.get("spdxVersion") == "SPDX-2.3"
        and sbom.get("SPDXID") == "SPDXRef-DOCUMENT"
        and isinstance(packages, list)
        and any(
            isinstance(package, dict)
            and package.get("SPDXID") == "SPDXRef-Package-Jarvis"
            and package.get("name") == "Jarvis"
            for package in packages
        )
    )


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
        raise DistributionReleaseQualificationError("Git inspection failed") from error
    if completed.returncode != 0:
        raise DistributionReleaseQualificationError("Git inspection failed")
    return completed.stdout


def _source_revision(project_root: Path) -> str:
    try:
        revision = _run_git(project_root, "rev-parse", "--verify", "HEAD").decode(
            "ascii"
        ).strip()
    except UnicodeDecodeError as error:
        raise DistributionReleaseQualificationError("source revision is invalid") from error
    if not is_build_revision(revision) or revision == "development":
        raise DistributionReleaseQualificationError("source revision is invalid")
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
            raise DistributionReleaseQualificationError(
                "source tree inspection failed"
            ) from error
        if completed.returncode != 0:
            raise DistributionReleaseQualificationError("tracked source tree is dirty")


def _source_tree_sha256(project_root: Path) -> str:
    encoded_paths = _run_git(project_root, "ls-files", "-z")
    digest = hashlib.sha256()
    for encoded_path in sorted(item for item in encoded_paths.split(b"\0") if item):
        try:
            relative = encoded_path.decode("utf-8")
            path = project_root / relative
            metadata = path.lstat()
        except (OSError, UnicodeDecodeError) as error:
            raise DistributionReleaseQualificationError(
                "tracked source cannot be inspected"
            ) from error
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
                raise DistributionReleaseQualificationError(
                    "tracked source cannot be read"
                ) from error
        else:
            raise DistributionReleaseQualificationError(
                "tracked source object is unsupported"
            )
        digest.update(b"\0")
    return digest.hexdigest()


def _open_private_file(path: Path, maximum_bytes: int) -> tuple[int, os.stat_result]:
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    descriptor = -1
    try:
        descriptor = os.open(path, flags)
        metadata = os.fstat(descriptor)
    except OSError as error:
        if descriptor >= 0:
            os.close(descriptor)
        raise DistributionReleaseQualificationError("P12 evidence is unavailable") from error
    if (
        not stat.S_ISREG(metadata.st_mode)
        or stat.S_ISLNK(metadata.st_mode)
        or metadata.st_uid != os.getuid()
        or stat.S_IMODE(metadata.st_mode) != 0o600
        or not 1 <= metadata.st_size <= maximum_bytes
    ):
        os.close(descriptor)
        raise DistributionReleaseQualificationError("P12 evidence file is unsafe")
    return descriptor, metadata


def _read_private_bytes(path: Path, maximum_bytes: int) -> bytes:
    descriptor, metadata = _open_private_file(path, maximum_bytes)
    try:
        with os.fdopen(descriptor, "rb") as source:
            payload = source.read(maximum_bytes + 1)
    except OSError as error:
        raise DistributionReleaseQualificationError("P12 evidence cannot be read") from error
    if len(payload) != metadata.st_size:
        raise DistributionReleaseQualificationError("P12 evidence changed while being read")
    return payload


def _hash_private_file(path: Path, maximum_bytes: int) -> tuple[str, int]:
    descriptor, metadata = _open_private_file(path, maximum_bytes)
    digest = hashlib.sha256()
    measured = 0
    try:
        with os.fdopen(descriptor, "rb") as source:
            while chunk := source.read(1024 * 1024):
                measured += len(chunk)
                if measured > maximum_bytes:
                    raise DistributionReleaseQualificationError(
                        "P12 evidence exceeds its size budget"
                    )
                digest.update(chunk)
    except OSError as error:
        raise DistributionReleaseQualificationError("P12 evidence cannot be hashed") from error
    if measured != metadata.st_size:
        raise DistributionReleaseQualificationError("P12 evidence changed while being hashed")
    return digest.hexdigest(), measured
