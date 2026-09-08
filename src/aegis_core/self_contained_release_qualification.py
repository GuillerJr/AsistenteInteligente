from __future__ import annotations

import hashlib
import json
import os
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

from aegis_core.macos_qualification import QualificationCheck, QualificationStatus

SCHEMA_VERSION = "1.0"
QUALIFICATION_CHECKS = 5
MAX_REPORT_BYTES = 256 * 1_024
MAX_MANIFEST_BYTES = 512 * 1_024
MAX_ARCHIVE_BYTES = 2 * 1_024 * 1_024 * 1_024
MAX_ARCHIVE_MEMBERS = 4_096
DAEMON_PATH = "Jarvis.app/Contents/Resources/Daemon/jarvis-daemon"
RUNTIME_PREFIX = "Jarvis.app/Contents/Resources/Daemon/_internal/"
REQUIRED_RUNTIME_MEMBERS = frozenset(
    {
        DAEMON_PATH,
        f"{RUNTIME_PREFIX}base_library.zip",
    }
)
EXPECTED_P12_CHECKS = frozenset(
    {
        "release.pilot_chain",
        "release.provenance",
        "security.developer_id",
        "security.apple_notarization",
        "privacy.distribution_boundary",
    }
)


class SelfContainedReleaseQualificationError(RuntimeError):
    """P13 cannot prove that the distributed app owns its complete runtime."""


class _P12Check(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    check_id: str
    status: QualificationStatus
    reason: str
    metrics: dict[str, bool | int | float | str | None]


class _P12Report(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["1.0"]
    profile: Literal["notarized_distribution_qualification"]
    build_revision: str = Field(pattern=r"^[0-9a-f]{40}$")
    status: QualificationStatus
    score: int = Field(ge=0, le=100)
    gate_passed: bool
    passed: int = Field(ge=0, le=5)
    total: Literal[5]
    duration_ms: int = Field(ge=0, le=3_600_000)
    artifact_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    evidence_sha256: dict[str, str]
    checks: tuple[_P12Check, ...] = Field(min_length=5, max_length=5)
    privacy: dict[str, bool | int]

    @model_validator(mode="after")
    def validate_contract(self) -> _P12Report:
        identifiers = [check.check_id for check in self.checks]
        passed = sum(check.status is QualificationStatus.PASSED for check in self.checks)
        if len(identifiers) != len(set(identifiers)):
            raise ValueError("P12 contains duplicate checks")
        if frozenset(identifiers) != EXPECTED_P12_CHECKS:
            raise ValueError("P12 contract is incomplete")
        if self.passed != passed or self.score != round(passed / 5 * 100):
            raise ValueError("P12 summary is inconsistent")
        if self.gate_passed != (passed == 5):
            raise ValueError("P12 gate is inconsistent")
        expected = QualificationStatus.PASSED if self.gate_passed else QualificationStatus.BLOCKED
        if self.status is not expected:
            raise ValueError("P12 status is inconsistent")
        if any(
            key.startswith("contains_") and value is not False
            for key, value in self.privacy.items()
        ):
            raise ValueError("P12 report contains private data")
        if set(self.evidence_sha256) != {"pilot", "release_manifest", "sbom"} or any(
            re.fullmatch(r"[0-9a-f]{64}", digest) is None
            for digest in self.evidence_sha256.values()
        ):
            raise ValueError("P12 evidence hashes are invalid")
        return self


class _ManifestFile(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    path: str
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    size: int = Field(ge=0, le=512 * 1_024 * 1_024)


class _Artifact(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    name: Literal["Jarvis.zip"]
    format: Literal["zip"]
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    size: int = Field(gt=0, le=MAX_ARCHIVE_BYTES)
    uncompressed_size: int = Field(gt=0, le=MAX_ARCHIVE_BYTES)


class _SBOM(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    name: Literal["Jarvis.spdx.json"]
    format: Literal["SPDX-2.3"]
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class _ManifestPrivacy(BaseModel):
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
    version: str
    build: str
    build_revision: str = Field(pattern=r"^[0-9a-f]{40}$")
    source_tree_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    created_at: str
    profile: Literal["developer-id-notarized"]
    architecture: Literal["arm64"]
    minimum_macos: str
    artifact: _Artifact
    bundle_files: tuple[_ManifestFile, ...] = Field(min_length=3, max_length=MAX_ARCHIVE_MEMBERS)
    sbom: _SBOM
    privacy: _ManifestPrivacy


@dataclass(frozen=True, slots=True)
class FrozenRuntimeAssessment:
    daemon_importable: bool
    executable_present: bool
    executable_arm64: bool
    executable_mode_safe: bool
    self_test_passed: bool
    frozen_runtime: bool
    mlx_runtime_linked: bool
    numba_openmp_linked: bool
    pythonpath_absent: bool
    sqlite_fts5_available: bool
    sqlite_vec_available: bool
    self_test_network_calls: int
    self_test_persistent_writes: int


class RuntimeAssessor(Protocol):
    def assess(self, archive_path: Path) -> FrozenRuntimeAssessment: ...


class FrozenDaemonRuntimeAssessor:
    _ENVIRONMENT: ClassVar[dict[str, str]] = {
        "LANG": "C",
        "LC_ALL": "C",
        "PATH": "/usr/bin:/bin:/usr/sbin:/sbin",
    }

    def assess(self, archive_path: Path) -> FrozenRuntimeAssessment:
        if os.uname().sysname != "Darwin":
            raise SelfContainedReleaseQualificationError("P13 requires macOS")
        try:
            with tempfile.TemporaryDirectory(prefix="aegis-p13-", dir="/private/tmp") as root:
                extraction_root = Path(root)
                extraction_root.chmod(0o700)
                result = subprocess.run(
                    ("/usr/bin/ditto", "-x", "-k", str(archive_path), str(extraction_root)),
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    check=False,
                    timeout=120,
                    env={**self._ENVIRONMENT, "HOME": str(extraction_root)},
                )
                if result.returncode != 0:
                    raise SelfContainedReleaseQualificationError(
                        "runtime archive extraction failed"
                    )
                app = extraction_root / "Jarvis.app"
                self._validate_tree(app)
                executable = app / "Contents/Resources/Daemon/jarvis-daemon"
                metadata = executable.stat()
                present = executable.is_file() and not executable.is_symlink()
                mode_safe = bool(
                    present
                    and metadata.st_uid == os.getuid()
                    and metadata.st_mode & 0o111
                    and metadata.st_mode & 0o022 == 0
                )
                architecture = subprocess.run(
                    ("/usr/bin/file", str(executable)),
                    stdin=subprocess.DEVNULL,
                    capture_output=True,
                    text=True,
                    check=False,
                    timeout=10,
                    env={**self._ENVIRONMENT, "HOME": str(extraction_root)},
                )
                payload = self._run_self_test(executable, extraction_root)
                checks = payload.get("checks")
                privacy = payload.get("privacy")
                if not isinstance(checks, dict) or not isinstance(privacy, dict):
                    raise SelfContainedReleaseQualificationError(
                        "frozen daemon self-test schema is invalid"
                    )
                return FrozenRuntimeAssessment(
                    daemon_importable=checks.get("daemon_importable") is True,
                    executable_present=present,
                    executable_arm64=(
                        architecture.returncode == 0 and "arm64" in architecture.stdout
                    ),
                    executable_mode_safe=mode_safe,
                    self_test_passed=payload.get("gate_passed") is True,
                    frozen_runtime=checks.get("frozen_runtime") is True,
                    mlx_runtime_linked=checks.get("mlx_runtime_linked") is True,
                    numba_openmp_linked=checks.get("numba_openmp_linked") is True,
                    pythonpath_absent=checks.get("pythonpath_absent") is True,
                    sqlite_fts5_available=checks.get("sqlite_fts5") is True,
                    sqlite_vec_available=checks.get("sqlite_vec") is True,
                    self_test_network_calls=self._bounded_zero(
                        privacy.get("network_calls"), "network calls"
                    ),
                    self_test_persistent_writes=self._bounded_zero(
                        privacy.get("persistent_writes"), "persistent writes"
                    ),
                )
        except (OSError, subprocess.SubprocessError) as error:
            if isinstance(error, SelfContainedReleaseQualificationError):
                raise
            raise SelfContainedReleaseQualificationError(
                "frozen daemon assessment failed"
            ) from error

    @classmethod
    def _run_self_test(cls, executable: Path, root: Path) -> dict[str, object]:
        result = subprocess.run(
            (str(executable), "--self-test"),
            cwd=root,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            check=False,
            timeout=30,
            env={**cls._ENVIRONMENT, "HOME": str(root), "TMPDIR": str(root)},
        )
        if result.returncode != 0 or len(result.stdout.encode()) > 16_384:
            raise SelfContainedReleaseQualificationError("frozen daemon self-test failed")
        try:
            payload = json.loads(result.stdout)
        except json.JSONDecodeError as error:
            raise SelfContainedReleaseQualificationError(
                "frozen daemon self-test is invalid"
            ) from error
        if (
            not isinstance(payload, dict)
            or payload.get("profile") != "jarvis_frozen_daemon_self_test"
            or payload.get("schema_version") != "1.0"
        ):
            raise SelfContainedReleaseQualificationError(
                "frozen daemon self-test schema is invalid"
            )
        return payload

    @staticmethod
    def _bounded_zero(value: object, label: str) -> int:
        if type(value) is not int or value != 0:
            raise SelfContainedReleaseQualificationError(f"self-test {label} are invalid")
        return value

    @staticmethod
    def _validate_tree(app: Path) -> None:
        if app.is_symlink() or not app.is_dir():
            raise SelfContainedReleaseQualificationError("runtime app is missing or unsafe")
        root = app.resolve(strict=True)
        members = 0
        for directory, directories, files in os.walk(app, followlinks=False):
            for name in (*directories, *files):
                members += 1
                if members > MAX_ARCHIVE_MEMBERS:
                    raise SelfContainedReleaseQualificationError(
                        "runtime app exceeds the member safety budget"
                    )
                path = Path(directory) / name
                if path.is_symlink():
                    try:
                        resolved = path.resolve(strict=True)
                    except OSError as error:
                        raise SelfContainedReleaseQualificationError(
                            "runtime app contains an invalid symbolic link"
                        ) from error
                    if not resolved.is_relative_to(root):
                        raise SelfContainedReleaseQualificationError(
                            "runtime app symbolic link escapes the bundle"
                        )


@dataclass(frozen=True, slots=True)
class SelfContainedReleaseQualificationReport:
    build_revision: str
    artifact_sha256: str
    checks: tuple[QualificationCheck, ...]
    duration_ms: int

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
            "profile": "self_contained_runtime_qualification",
            "build_revision": self.build_revision,
            "status": self.status.value,
            "score": self.score,
            "gate_passed": self.gate_passed,
            "passed": self.passed,
            "total": len(self.checks),
            "duration_ms": self.duration_ms,
            "artifact_sha256": self.artifact_sha256,
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


class SelfContainedReleaseQualificationGate:
    def __init__(
        self,
        *,
        project_root: Path,
        p12_report_path: Path,
        release_manifest_path: Path,
        release_archive_path: Path,
        assessor: RuntimeAssessor | None = None,
    ) -> None:
        self._project_root = project_root
        self._p12_report_path = p12_report_path
        self._release_manifest_path = release_manifest_path
        self._release_archive_path = release_archive_path
        self._assessor = assessor or FrozenDaemonRuntimeAssessor()

    def run(self) -> SelfContainedReleaseQualificationReport:
        started = time.monotonic_ns()
        revision = _source_revision(self._project_root)
        _require_clean_tracked_tree(self._project_root)
        report_bytes = _read_private(self._p12_report_path, MAX_REPORT_BYTES)
        manifest_bytes = _read_private(self._release_manifest_path, MAX_MANIFEST_BYTES)
        archive_sha256, archive_size = _hash_private(
            self._release_archive_path, MAX_ARCHIVE_BYTES
        )
        try:
            p12 = _P12Report.model_validate_json(report_bytes)
            manifest = _ReleaseManifest.model_validate_json(manifest_bytes)
        except ValidationError as error:
            raise SelfContainedReleaseQualificationError(
                "P13 evidence schema is invalid"
            ) from error
        members = _inspect_runtime_members(self._release_archive_path)
        assessment = self._assessor.assess(self._release_archive_path)
        p12_passed = (
            p12.gate_passed
            and p12.build_revision == revision
            and p12.artifact_sha256 == archive_sha256
            and p12.evidence_sha256["release_manifest"]
            == hashlib.sha256(manifest_bytes).hexdigest()
        )
        manifest_files = {item.path: item for item in manifest.bundle_files}
        inventory_passed = (
            manifest.build_revision == revision
            and manifest.artifact.sha256 == archive_sha256
            and manifest.artifact.size == archive_size
            and REQUIRED_RUNTIME_MEMBERS.issubset(members)
            and REQUIRED_RUNTIME_MEMBERS.issubset(manifest_files)
            and assessment.executable_present
            and assessment.executable_arm64
            and assessment.executable_mode_safe
        )
        cold_start_passed = (
            assessment.daemon_importable
            and assessment.self_test_passed
            and assessment.mlx_runtime_linked
            and assessment.numba_openmp_linked
            and assessment.sqlite_fts5_available
            and assessment.sqlite_vec_available
        )
        independent = assessment.frozen_runtime and assessment.pythonpath_absent
        private = (
            assessment.self_test_network_calls == 0
            and assessment.self_test_persistent_writes == 0
            and all(value is False for value in manifest.privacy.model_dump().values())
            and not any(
                path.endswith((".py", ".pyc", ".env"))
                or "/JarvisSpeakerIdentity.mlmodelc/" in path
                or "/JarvisWakeWord.mlmodelc/" in path
                for path in members
            )
        )
        checks = (
            QualificationCheck(
                "release.p12_chain",
                QualificationStatus.PASSED if p12_passed else QualificationStatus.BLOCKED,
                "verified" if p12_passed else "p12_prerequisite_not_passed",
                {"revision_matches": p12.build_revision == revision},
            ),
            QualificationCheck(
                "runtime.embedded_daemon",
                QualificationStatus.PASSED if inventory_passed else QualificationStatus.BLOCKED,
                "verified" if inventory_passed else "embedded_runtime_invalid",
                {
                    "required_members": len(REQUIRED_RUNTIME_MEMBERS),
                    "runtime_members": sum(path.startswith(RUNTIME_PREFIX) for path in members),
                    "arm64": assessment.executable_arm64,
                    "mode_safe": assessment.executable_mode_safe,
                },
            ),
            QualificationCheck(
                "runtime.cold_start",
                QualificationStatus.PASSED if cold_start_passed else QualificationStatus.BLOCKED,
                "verified" if cold_start_passed else "frozen_runtime_self_test_failed",
                {
                    "fts5": assessment.sqlite_fts5_available,
                    "daemon_importable": assessment.daemon_importable,
                    "mlx_runtime_linked": assessment.mlx_runtime_linked,
                    "numba_openmp_linked": assessment.numba_openmp_linked,
                    "sqlite_vec": assessment.sqlite_vec_available,
                },
            ),
            QualificationCheck(
                "runtime.source_independence",
                QualificationStatus.PASSED if independent else QualificationStatus.BLOCKED,
                "verified" if independent else "source_checkout_dependency_detected",
                {
                    "frozen": assessment.frozen_runtime,
                    "pythonpath_absent": assessment.pythonpath_absent,
                },
            ),
            QualificationCheck(
                "privacy.runtime_boundary",
                QualificationStatus.PASSED if private else QualificationStatus.BLOCKED,
                "verified" if private else "runtime_privacy_boundary_failed",
                {
                    "network_calls": assessment.self_test_network_calls,
                    "persistent_writes": assessment.self_test_persistent_writes,
                    "private_models_absent": private,
                },
            ),
        )
        elapsed = max(0, (time.monotonic_ns() - started) // 1_000_000)
        return SelfContainedReleaseQualificationReport(
            build_revision=revision,
            artifact_sha256=archive_sha256,
            checks=checks,
            duration_ms=min(elapsed, 2_147_483_647),
        )


def _inspect_runtime_members(archive_path: Path) -> set[str]:
    try:
        with zipfile.ZipFile(archive_path) as archive:
            members = archive.infolist()
            if not members or len(members) > MAX_ARCHIVE_MEMBERS:
                raise SelfContainedReleaseQualificationError(
                    "runtime archive member count is invalid"
                )
            names: set[str] = set()
            for member in members:
                path = PurePosixPath(member.filename)
                if (
                    not member.filename
                    or member.filename.startswith("/")
                    or "\\" in member.filename
                    or not path.parts
                    or path.parts[0] != "Jarvis.app"
                    or any(part in {"", ".", ".."} for part in path.parts)
                ):
                    raise SelfContainedReleaseQualificationError(
                        "runtime archive path is unsafe"
                    )
                if member.filename in names or member.flag_bits & 0x1:
                    raise SelfContainedReleaseQualificationError(
                        "runtime archive structure is unsafe"
                    )
                names.add(member.filename)
            return names
    except (OSError, RuntimeError, zipfile.BadZipFile) as error:
        if isinstance(error, SelfContainedReleaseQualificationError):
            raise
        raise SelfContainedReleaseQualificationError(
            "runtime archive cannot be inspected"
        ) from error


def _read_private(path: Path, maximum: int) -> bytes:
    metadata = path.stat()
    if (
        path.is_symlink()
        or not stat.S_ISREG(metadata.st_mode)
        or metadata.st_uid != os.getuid()
        or metadata.st_mode & 0o077
        or metadata.st_size <= 0
        or metadata.st_size > maximum
    ):
        raise SelfContainedReleaseQualificationError("P13 evidence file is unsafe")
    return path.read_bytes()


def _hash_private(path: Path, maximum: int) -> tuple[str, int]:
    metadata = path.stat()
    if (
        path.is_symlink()
        or not stat.S_ISREG(metadata.st_mode)
        or metadata.st_uid != os.getuid()
        or metadata.st_mode & 0o077
        or metadata.st_size <= 0
        or metadata.st_size > maximum
    ):
        raise SelfContainedReleaseQualificationError("P13 archive is unsafe")
    digest = hashlib.sha256()
    with path.open("rb") as source:
        while chunk := source.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest(), metadata.st_size


def _run_git(project_root: Path, *arguments: str) -> bytes:
    try:
        result = subprocess.run(
            ("/usr/bin/git", "-C", str(project_root), *arguments),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            check=False,
            timeout=30,
        )
    except (OSError, subprocess.SubprocessError) as error:
        raise SelfContainedReleaseQualificationError("Git inspection failed") from error
    if result.returncode != 0:
        raise SelfContainedReleaseQualificationError("Git inspection failed")
    return result.stdout


def _source_revision(project_root: Path) -> str:
    revision = _run_git(project_root, "rev-parse", "--verify", "HEAD").decode().strip()
    if re.fullmatch(r"[0-9a-f]{40}", revision) is None:
        raise SelfContainedReleaseQualificationError("source revision is invalid")
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
            raise SelfContainedReleaseQualificationError("tracked source tree is dirty")
