from __future__ import annotations

import asyncio
import base64
import binascii
import hashlib
import json
import os
import plistlib
import re
import shutil
import stat
import subprocess
import tempfile
import zipfile
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path, PurePosixPath
from typing import ClassVar, Literal, Protocol
from uuid import UUID, uuid4

import psutil
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

from aegis_core.audit_anchor import DurableAuditAnchor
from aegis_core.config import Settings
from aegis_core.ipc.client import IpcClient
from aegis_core.ipc.protocol import IpcAuthenticator, ProtocolError
from aegis_core.secrets import (
    MacOSAuditAnchor,
    MacOSGenericSecret,
    MacOSIpcSecret,
    SecretNotFoundError,
)
from aegis_core.tools.audit import HashChainAuditLog

UPDATE_SCHEMA_VERSION = "1.0"
UPDATE_SIGNATURE_ALGORITHM = "Ed25519"
UPDATE_KEY_PREFIX = "ed25519:"
UPDATE_PRIVATE_KEY_PREFIX = "ed25519-private:"
UPDATE_KEYCHAIN_SERVICE = "ai.aegis.update-signing"
UPDATE_KEYCHAIN_ACCOUNT = "default"
UPDATE_PUBLIC_KEY_NAME = "JarvisUpdatePublicKey.ed25519"
UPDATE_MANIFEST_NAME = "Jarvis.update.json"
APP_NAME = "Jarvis"
BUNDLE_IDENTIFIER = "ai.aegis.menubar"
MAX_MANIFEST_BYTES = 64 * 1_024
MAX_RELEASE_MANIFEST_BYTES = 512 * 1_024
MAX_ARCHIVE_BYTES = 2 * 1_024 * 1_024 * 1_024
MAX_ARCHIVE_MEMBER_BYTES = 512 * 1_024 * 1_024
MAX_ARCHIVE_MEMBERS = 4_096
MAX_CLOCK_SKEW = timedelta(minutes=5)
MAX_MANIFEST_LIFETIME = timedelta(days=31)
TRANSACTION_FILE_NAME = "update-transaction.json"
TRANSACTION_PREFIX = ".aegis-update-"
PRIVATE_MODEL_DIRECTORIES = frozenset(
    {"JarvisSpeakerIdentity.mlmodelc", "JarvisWakeWord.mlmodelc"}
)
REQUIRED_ARCHIVE_PATHS = frozenset(
    {
        "Jarvis.app/Contents/Info.plist",
        "Jarvis.app/Contents/MacOS/Jarvis",
        "Jarvis.app/Contents/Resources/Daemon/jarvis-daemon",
        f"Jarvis.app/Contents/Resources/{UPDATE_PUBLIC_KEY_NAME}",
    }
)


class SecureUpdateError(RuntimeError):
    """A signed update failed a closed security or lifecycle invariant."""


class UpdateSignatureError(SecureUpdateError):
    """The update channel signature or pinned key is invalid."""


class UpdateRollbackError(SecureUpdateError):
    """The candidate is not newer than the installed build."""


class UpdateTransactionError(SecureUpdateError):
    """The atomic application swap could not be completed or recovered."""


class UpdateArtifact(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    name: Literal["Jarvis.zip"]
    format: Literal["zip"]
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    size: int = Field(gt=0, le=MAX_ARCHIVE_BYTES)
    release_manifest_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class UpdatePrivacy(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    contains_prompts: Literal[False]
    contains_transcripts: Literal[False]
    contains_audio: Literal[False]
    contains_images: Literal[False]
    contains_credentials: Literal[False]
    contains_absolute_paths: Literal[False]


class ReleasePrivacy(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    contains_prompts: Literal[False]
    contains_transcripts: Literal[False]
    contains_images: Literal[False]
    contains_credentials: Literal[False]
    contains_absolute_paths: Literal[False]


class UpdatePayload(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["1.0"]
    product: Literal["Jarvis"]
    bundle_identifier: Literal["ai.aegis.menubar"]
    channel: Literal["stable"]
    architecture: Literal["arm64"]
    version: str = Field(min_length=1, max_length=64)
    build: int = Field(ge=1, le=2_147_483_647)
    build_revision: str = Field(pattern=r"^[0-9a-f]{40}$")
    minimum_macos: str = Field(min_length=2, max_length=32)
    published_at: datetime
    expires_at: datetime
    artifact: UpdateArtifact
    privacy: UpdatePrivacy

    @field_validator("version", "minimum_macos")
    @classmethod
    def validate_bounded_version(cls, value: str) -> str:
        if re.fullmatch(r"[0-9]+(?:\.[0-9]+){1,3}(?:[-+][A-Za-z0-9.-]+)?", value) is None:
            raise ValueError("version is not canonical")
        return value

    @model_validator(mode="after")
    def validate_lifetime(self) -> UpdatePayload:
        if self.published_at.tzinfo is None or self.expires_at.tzinfo is None:
            raise ValueError("update timestamps must be timezone-aware")
        lifetime = self.expires_at - self.published_at
        if lifetime <= timedelta(0) or lifetime > MAX_MANIFEST_LIFETIME:
            raise ValueError("update manifest lifetime is outside the safety window")
        return self


class SignedUpdateManifest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    payload: UpdatePayload
    algorithm: Literal["Ed25519"]
    key_id: str = Field(pattern=r"^[0-9a-f]{64}$")
    signature: str = Field(min_length=86, max_length=88)


class UpdateTransactionRecord(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["1.0"]
    transaction_id: UUID
    state: Literal["prepared", "swapped", "committed"]
    previous_revision: str = Field(pattern=r"^[0-9a-f]{40}$")
    target_revision: str = Field(pattern=r"^[0-9a-f]{40}$")
    installer_pid: int = Field(gt=1)


@dataclass(frozen=True, slots=True)
class InstalledIdentity:
    version: str
    build: int
    revision: str
    public_key: bytes
    key_id: str


@dataclass(frozen=True, slots=True)
class VerifiedUpdate:
    manifest: SignedUpdateManifest
    archive_path: Path
    release_manifest_path: Path


def _canonical_bytes(value: object) -> bytes:
    return (
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n"
    ).encode("utf-8")


def _require_private_release_boundary(value: object) -> None:
    try:
        ReleasePrivacy.model_validate(value)
    except ValidationError as error:
        raise SecureUpdateError("release evidence violates the private update boundary") from error


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        while chunk := source.read(1_024 * 1_024):
            digest.update(chunk)
    return digest.hexdigest()


def _require_regular_file(path: Path, *, maximum_bytes: int) -> os.stat_result:
    metadata = path.lstat()
    mode = stat.S_IMODE(metadata.st_mode)
    if (
        stat.S_ISLNK(metadata.st_mode)
        or not stat.S_ISREG(metadata.st_mode)
        or metadata.st_uid != os.getuid()
        or mode & 0o022
        or not 1 <= metadata.st_size <= maximum_bytes
    ):
        raise SecureUpdateError("update input is not a private bounded regular file")
    return metadata


def _require_private_directory(path: Path) -> None:
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    metadata = path.lstat()
    if (
        stat.S_ISLNK(metadata.st_mode)
        or not stat.S_ISDIR(metadata.st_mode)
        or metadata.st_uid != os.getuid()
        or stat.S_IMODE(metadata.st_mode) & 0o077
    ):
        raise UpdateTransactionError("update state directory is unsafe")


def _decode_key(encoded: str, *, prefix: str) -> bytes:
    if not encoded.startswith(prefix) or any(character in encoded for character in "\r\n\0"):
        raise UpdateSignatureError("update key encoding is invalid")
    try:
        raw = base64.b64decode(encoded.removeprefix(prefix), validate=True)
    except (binascii.Error, ValueError) as error:
        raise UpdateSignatureError("update key encoding is invalid") from error
    if len(raw) != 32:
        raise UpdateSignatureError("update key length is invalid")
    return raw


def encode_public_key(public_key: bytes) -> str:
    if len(public_key) != 32:
        raise UpdateSignatureError("update public key length is invalid")
    return UPDATE_KEY_PREFIX + base64.b64encode(public_key).decode("ascii")


def public_key_id(public_key: bytes) -> str:
    if len(public_key) != 32:
        raise UpdateSignatureError("update public key length is invalid")
    return hashlib.sha256(public_key).hexdigest()


def read_public_key(path: Path) -> bytes:
    _require_regular_file(path, maximum_bytes=256)
    try:
        encoded = path.read_text(encoding="ascii").strip()
    except (OSError, UnicodeError) as error:
        raise UpdateSignatureError("update public key cannot be read") from error
    return _decode_key(encoded, prefix=UPDATE_KEY_PREFIX)


def _atomic_private_write(path: Path, payload: bytes) -> None:
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary = Path(temporary_name)
    try:
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "wb") as target:
            target.write(payload)
            target.flush()
            os.fsync(target.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def initialize_update_signing_key(public_key_path: Path) -> str:
    """Create the release key once; never silently rotate a pinned public key."""
    store = MacOSGenericSecret(UPDATE_KEYCHAIN_SERVICE, UPDATE_KEYCHAIN_ACCOUNT)
    try:
        encoded_private = store.get()
    except SecretNotFoundError as error:
        if public_key_path.exists():
            raise UpdateSignatureError(
                "pinned update key exists but its private signing key is unavailable"
            ) from error
        private_key = Ed25519PrivateKey.generate()
        seed = private_key.private_bytes(
            serialization.Encoding.Raw,
            serialization.PrivateFormat.Raw,
            serialization.NoEncryption(),
        )
        encoded_private = UPDATE_PRIVATE_KEY_PREFIX + base64.b64encode(seed).decode("ascii")
        store.set(encoded_private)
    seed = _decode_key(encoded_private, prefix=UPDATE_PRIVATE_KEY_PREFIX)
    private_key = Ed25519PrivateKey.from_private_bytes(seed)
    raw_public = private_key.public_key().public_bytes(
        serialization.Encoding.Raw,
        serialization.PublicFormat.Raw,
    )
    if public_key_path.exists():
        if read_public_key(public_key_path) != raw_public:
            raise UpdateSignatureError("Keychain key does not match the pinned public key")
    else:
        _atomic_private_write(
            public_key_path,
            f"{encode_public_key(raw_public)}\n".encode("ascii"),
        )
    return public_key_id(raw_public)


def _safe_archive_path(name: str) -> PurePosixPath:
    path = PurePosixPath(name)
    if (
        not name
        or name.startswith("/")
        or "\\" in name
        or any(part in {"", ".", ".."} for part in path.parts)
        or path.parts[0] != "Jarvis.app"
    ):
        raise SecureUpdateError("update archive contains an unsafe path")
    return path


def _normalized_link_target(link: PurePosixPath, payload: bytes) -> str:
    try:
        raw_target = payload.decode("utf-8")
    except UnicodeDecodeError as error:
        raise SecureUpdateError("update archive contains an invalid symbolic link") from error
    target = PurePosixPath(raw_target)
    if not raw_target or raw_target.startswith("/") or "\\" in raw_target:
        raise SecureUpdateError("update archive contains an unsafe symbolic link")
    parts = list(link.parent.parts)
    for part in target.parts:
        if part in {"", "."}:
            continue
        if part == "..":
            if len(parts) <= 1:
                raise SecureUpdateError("update archive symbolic link escapes the bundle")
            parts.pop()
        else:
            parts.append(part)
    if not parts or parts[0] != "Jarvis.app":
        raise SecureUpdateError("update archive symbolic link escapes the bundle")
    return "/".join(parts)


def _validate_archive_links(links: Mapping[str, str], names: set[str]) -> None:
    normalized_names = {name.rstrip("/") for name in names}
    for source, initial_target in links.items():
        target = initial_target
        visited = {source}
        while target in links:
            if target in visited:
                raise SecureUpdateError("update archive contains a symbolic link cycle")
            visited.add(target)
            target = links[target]
        if target not in normalized_names:
            raise SecureUpdateError("update archive symbolic link target is missing")


def inspect_update_archive(
    archive_path: Path,
    *,
    payload: UpdatePayload,
    pinned_public_key: bytes,
) -> None:
    metadata = _require_regular_file(archive_path, maximum_bytes=MAX_ARCHIVE_BYTES)
    if (
        metadata.st_size != payload.artifact.size
        or _sha256_file(archive_path) != payload.artifact.sha256
    ):
        raise SecureUpdateError("update archive digest does not match the signed manifest")
    try:
        with zipfile.ZipFile(archive_path) as archive:
            members = archive.infolist()
            if not members or len(members) > MAX_ARCHIVE_MEMBERS:
                raise SecureUpdateError("update archive member count is outside the safety budget")
            names: set[str] = set()
            links: dict[str, str] = {}
            total_size = 0
            info_payload: bytes | None = None
            key_payload: bytes | None = None
            for member in members:
                path = _safe_archive_path(member.filename)
                if not PRIVATE_MODEL_DIRECTORIES.isdisjoint(path.parts):
                    raise SecureUpdateError("update archive contains a private biometric model")
                if member.filename in names or member.flag_bits & 0x1:
                    raise SecureUpdateError("update archive has duplicate or encrypted members")
                names.add(member.filename)
                if member.file_size > MAX_ARCHIVE_MEMBER_BYTES:
                    raise SecureUpdateError("update archive member exceeds the safety budget")
                total_size += member.file_size
                if total_size > MAX_ARCHIVE_BYTES:
                    raise SecureUpdateError("update archive expansion exceeds the safety budget")
                unix_mode = member.external_attr >> 16
                if stat.S_ISLNK(unix_mode):
                    links[member.filename.rstrip("/")] = _normalized_link_target(
                        path, archive.read(member)
                    )
                elif member.filename == "Jarvis.app/Contents/Info.plist":
                    if member.file_size > 65_536:
                        raise SecureUpdateError("update bundle metadata exceeds the safety budget")
                    info_payload = archive.read(member)
                elif member.filename == (
                    f"Jarvis.app/Contents/Resources/{UPDATE_PUBLIC_KEY_NAME}"
                ):
                    if member.file_size > 256:
                        raise SecureUpdateError("update public key exceeds the safety budget")
                    key_payload = archive.read(member)
            _validate_archive_links(links, names)
    except (OSError, RuntimeError, zipfile.BadZipFile) as error:
        if isinstance(error, SecureUpdateError):
            raise
        raise SecureUpdateError("update archive cannot be inspected") from error
    if not REQUIRED_ARCHIVE_PATHS.issubset(names) or info_payload is None or key_payload is None:
        raise SecureUpdateError("update archive omits a required runtime member")
    try:
        info = plistlib.loads(info_payload)
        archive_key = _decode_key(key_payload.decode("ascii").strip(), prefix=UPDATE_KEY_PREFIX)
    except (plistlib.InvalidFileException, UnicodeError) as error:
        raise SecureUpdateError("update bundle metadata is invalid") from error
    expected = {
        "CFBundleIdentifier": BUNDLE_IDENTIFIER,
        "CFBundleName": APP_NAME,
        "CFBundleExecutable": APP_NAME,
        "CFBundleShortVersionString": payload.version,
        "CFBundleVersion": str(payload.build),
        "LSMinimumSystemVersion": payload.minimum_macos,
        "AegisBuildRevision": payload.build_revision,
        "AegisBuildDirty": False,
    }
    if not isinstance(info, dict) or any(info.get(key) != value for key, value in expected.items()):
        raise SecureUpdateError("update bundle identity does not match the signed manifest")
    if archive_key != pinned_public_key:
        raise UpdateSignatureError("update bundle changed the pinned channel key")


def read_installed_identity(app_path: Path) -> InstalledIdentity:
    original_metadata = app_path.lstat()
    if stat.S_ISLNK(original_metadata.st_mode):
        raise SecureUpdateError("installed application path is unsafe")
    app = app_path.resolve(strict=True)
    if app.name != "Jarvis.app" or not app.is_dir():
        raise SecureUpdateError("installed application path is unsafe")
    info_path = app / "Contents/Info.plist"
    key_path = app / f"Contents/Resources/{UPDATE_PUBLIC_KEY_NAME}"
    _require_regular_file(info_path, maximum_bytes=65_536)
    public_key = read_public_key(key_path)
    try:
        info = plistlib.loads(info_path.read_bytes())
        build_text = info["CFBundleVersion"]
        revision = info["AegisBuildRevision"]
        version = info["CFBundleShortVersionString"]
    except (KeyError, OSError, plistlib.InvalidFileException) as error:
        raise SecureUpdateError("installed application identity is invalid") from error
    if (
        info.get("CFBundleIdentifier") != BUNDLE_IDENTIFIER
        or info.get("CFBundleName") != APP_NAME
        or info.get("AegisBuildDirty") is not False
        or not isinstance(build_text, str)
        or re.fullmatch(r"[1-9][0-9]{0,9}", build_text) is None
        or not isinstance(revision, str)
        or re.fullmatch(r"[0-9a-f]{40}", revision) is None
        or not isinstance(version, str)
    ):
        raise SecureUpdateError("installed application identity is invalid")
    return InstalledIdentity(
        version=version,
        build=int(build_text),
        revision=revision,
        public_key=public_key,
        key_id=public_key_id(public_key),
    )


def verify_signed_update(
    manifest_path: Path,
    archive_path: Path,
    release_manifest_path: Path,
    public_key_path: Path,
    *,
    current_build: int,
    now: datetime | None = None,
) -> VerifiedUpdate:
    _require_regular_file(manifest_path, maximum_bytes=MAX_MANIFEST_BYTES)
    _require_regular_file(release_manifest_path, maximum_bytes=MAX_RELEASE_MANIFEST_BYTES)
    if current_build < 0:
        raise ValueError("current build cannot be negative")
    try:
        envelope = SignedUpdateManifest.model_validate_json(manifest_path.read_bytes())
    except (OSError, ValueError) as error:
        raise SecureUpdateError("signed update manifest is invalid") from error
    public_key = read_public_key(public_key_path)
    if envelope.key_id != public_key_id(public_key):
        raise UpdateSignatureError("update key identifier does not match the pinned key")
    try:
        signature = base64.b64decode(envelope.signature, validate=True)
        if len(signature) != 64:
            raise ValueError
        Ed25519PublicKey.from_public_bytes(public_key).verify(
            signature,
            _canonical_bytes(envelope.payload.model_dump(mode="json")),
        )
    except (binascii.Error, InvalidSignature, ValueError) as error:
        raise UpdateSignatureError("update manifest signature verification failed") from error
    instant = (now or datetime.now(UTC)).astimezone(UTC)
    published = envelope.payload.published_at.astimezone(UTC)
    expires = envelope.payload.expires_at.astimezone(UTC)
    if published > instant + MAX_CLOCK_SKEW or instant > expires:
        raise UpdateSignatureError("update manifest is not currently valid")
    if envelope.payload.build <= current_build:
        raise UpdateRollbackError("update build is not newer than the installed build")
    release_bytes = release_manifest_path.read_bytes()
    if (
        hashlib.sha256(release_bytes).hexdigest()
        != envelope.payload.artifact.release_manifest_sha256
    ):
        raise SecureUpdateError("release evidence digest does not match the signed update")
    try:
        release = json.loads(release_bytes)
    except (UnicodeError, json.JSONDecodeError) as error:
        raise SecureUpdateError("release evidence is invalid") from error
    if not isinstance(release, dict):
        raise SecureUpdateError("release evidence is invalid")
    artifact = release.get("artifact")
    if (
        release.get("profile") != "developer-id-notarized"
        or release.get("product") != APP_NAME
        or release.get("bundle_identifier") != BUNDLE_IDENTIFIER
        or release.get("architecture") != "arm64"
        or release.get("version") != envelope.payload.version
        or release.get("build") != str(envelope.payload.build)
        or release.get("build_revision") != envelope.payload.build_revision
        or release.get("minimum_macos") != envelope.payload.minimum_macos
        or not isinstance(artifact, dict)
        or artifact.get("name") != envelope.payload.artifact.name
        or artifact.get("sha256") != envelope.payload.artifact.sha256
        or artifact.get("size") != envelope.payload.artifact.size
    ):
        raise SecureUpdateError("release evidence does not match the signed update")
    _require_private_release_boundary(release.get("privacy"))
    inspect_update_archive(
        archive_path,
        payload=envelope.payload,
        pinned_public_key=public_key,
    )
    return VerifiedUpdate(envelope, archive_path, release_manifest_path)


def create_signed_update_manifest(
    release_manifest_path: Path,
    archive_path: Path,
    output_path: Path,
    public_key_path: Path,
) -> SignedUpdateManifest:
    _require_regular_file(release_manifest_path, maximum_bytes=MAX_RELEASE_MANIFEST_BYTES)
    _require_regular_file(archive_path, maximum_bytes=MAX_ARCHIVE_BYTES)
    store = MacOSGenericSecret(UPDATE_KEYCHAIN_SERVICE, UPDATE_KEYCHAIN_ACCOUNT)
    seed = _decode_key(store.get(), prefix=UPDATE_PRIVATE_KEY_PREFIX)
    private_key = Ed25519PrivateKey.from_private_bytes(seed)
    raw_public = private_key.public_key().public_bytes(
        serialization.Encoding.Raw,
        serialization.PublicFormat.Raw,
    )
    if read_public_key(public_key_path) != raw_public:
        raise UpdateSignatureError("release signing key does not match the pinned public key")
    release_bytes = release_manifest_path.read_bytes()
    try:
        release = json.loads(release_bytes)
        published = datetime.fromisoformat(release["created_at"].replace("Z", "+00:00"))
        build_text = release["build"]
        artifact = release["artifact"]
    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as error:
        raise SecureUpdateError("release evidence is invalid") from error
    if (
        release.get("profile") != "developer-id-notarized"
        or release.get("product") != APP_NAME
        or release.get("bundle_identifier") != BUNDLE_IDENTIFIER
        or release.get("architecture") != "arm64"
        or not isinstance(build_text, str)
        or re.fullmatch(r"[1-9][0-9]{0,9}", build_text) is None
        or not isinstance(artifact, dict)
        or artifact.get("name") != archive_path.name
        or artifact.get("sha256") != _sha256_file(archive_path)
        or artifact.get("size") != archive_path.stat().st_size
    ):
        raise SecureUpdateError("only authenticated notarized releases may enter the channel")
    _require_private_release_boundary(release.get("privacy"))
    payload = UpdatePayload(
        schema_version=UPDATE_SCHEMA_VERSION,
        product=APP_NAME,
        bundle_identifier=BUNDLE_IDENTIFIER,
        channel="stable",
        architecture="arm64",
        version=release["version"],
        build=int(build_text),
        build_revision=release["build_revision"],
        minimum_macos=release["minimum_macos"],
        published_at=published,
        expires_at=published + timedelta(days=30),
        artifact=UpdateArtifact(
            name="Jarvis.zip",
            format="zip",
            sha256=artifact["sha256"],
            size=artifact["size"],
            release_manifest_sha256=hashlib.sha256(release_bytes).hexdigest(),
        ),
        privacy=UpdatePrivacy(
            contains_prompts=False,
            contains_transcripts=False,
            contains_audio=False,
            contains_images=False,
            contains_credentials=False,
            contains_absolute_paths=False,
        ),
    )
    signature = private_key.sign(_canonical_bytes(payload.model_dump(mode="json")))
    envelope = SignedUpdateManifest(
        payload=payload,
        algorithm=UPDATE_SIGNATURE_ALGORITHM,
        key_id=public_key_id(raw_public),
        signature=base64.b64encode(signature).decode("ascii"),
    )
    _atomic_private_write(output_path, _canonical_bytes(envelope.model_dump(mode="json")))
    return envelope


class BundleTrustVerifier(Protocol):
    def validate_current(self, app_path: Path) -> str: ...

    def validate_candidate(
        self,
        app_path: Path,
        *,
        expected_team_id: str,
        expected: UpdatePayload,
        pinned_public_key: bytes,
    ) -> None: ...


class MacOSBundleTrustVerifier:
    _ENVIRONMENT: ClassVar[dict[str, str]] = {
        "LANG": "C",
        "LC_ALL": "C",
        "PATH": "/usr/bin:/bin:/usr/sbin:/sbin",
    }

    def validate_current(self, app_path: Path) -> str:
        details = self._validate_system_trust(app_path)
        team = re.search(r"(?m)^TeamIdentifier=([A-Z0-9]{10})$", details)
        if team is None or "Authority=Developer ID Application:" not in details:
            raise SecureUpdateError("installed application is not a Developer ID release")
        return team.group(1)

    def validate_candidate(
        self,
        app_path: Path,
        *,
        expected_team_id: str,
        expected: UpdatePayload,
        pinned_public_key: bytes,
    ) -> None:
        details = self._validate_system_trust(app_path)
        if (
            f"TeamIdentifier={expected_team_id}" not in details
            or "Authority=Developer ID Application:" not in details
        ):
            raise SecureUpdateError("candidate Team ID does not match the installed release")
        identity = read_installed_identity(app_path)
        if (
            identity.build != expected.build
            or identity.version != expected.version
            or identity.revision != expected.build_revision
            or identity.public_key != pinned_public_key
        ):
            raise SecureUpdateError("candidate identity changed after extraction")

    def _validate_system_trust(self, app_path: Path) -> str:
        commands = (
            ("/usr/bin/codesign", "--verify", "--deep", "--strict", "--verbose=4", str(app_path)),
            ("/usr/bin/xcrun", "stapler", "validate", str(app_path)),
            ("/usr/sbin/spctl", "--assess", "--type", "execute", "--verbose=4", str(app_path)),
        )
        for command in commands:
            result = subprocess.run(
                command,
                stdin=subprocess.DEVNULL,
                capture_output=True,
                text=True,
                check=False,
                timeout=60,
                env=self._ENVIRONMENT,
            )
            if result.returncode != 0:
                raise SecureUpdateError("macOS rejected the update bundle trust chain")
        details = subprocess.run(
            ("/usr/bin/codesign", "-dvvv", str(app_path)),
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            check=False,
            timeout=15,
            env=self._ENVIRONMENT,
        )
        output = details.stdout + details.stderr
        if (
            details.returncode != 0
            or "Identifier=ai.aegis.menubar" not in output
            or "Signature=adhoc" in output
            or not (
                re.search(r"(?m)^Runtime Version=", output)
                or re.search(r"(?m)^CodeDirectory .*flags=.*\(runtime\)", output)
            )
        ):
            raise SecureUpdateError("update bundle signing identity is invalid")
        return output


class AppLifecycle(Protocol):
    async def require_idle(self, expected_revision: str) -> None: ...

    async def stop(self, app_path: Path) -> None: ...

    async def start(self, app_path: Path, transaction_id: UUID | None = None) -> None: ...

    async def wait_healthy(self, expected_revision: str) -> bool: ...


class MacOSAppLifecycle:
    def __init__(self, settings: Settings, *, attempts: int = 80, interval: float = 0.25) -> None:
        self._settings = settings
        self._attempts = attempts
        self._interval = interval

    def _client(self) -> IpcClient:
        secret = MacOSIpcSecret(
            self._settings.ipc_keychain_service,
            self._settings.ipc_keychain_account,
        ).get()
        return IpcClient(
            self._settings.ipc_socket_path,
            IpcAuthenticator.from_hex(secret),
            max_frame_bytes=self._settings.ipc_max_frame_bytes,
            max_message_bytes=self._settings.ipc_max_message_bytes,
            clock_skew_seconds=self._settings.ipc_clock_skew_seconds,
        )

    async def require_idle(self, expected_revision: str) -> None:
        client = self._client()
        health, security, activity = await asyncio.gather(
            client.call("health"),
            client.call("security.status"),
            client.call("swarm.activity"),
        )
        agents = activity.payload.get("agents") if activity.ok else None
        if (
            not health.ok
            or not security.ok
            or not activity.ok
            or health.payload.get("build_revision") != expected_revision
            or security.payload.get("state") != "intact"
            or not isinstance(agents, list)
            or any(
                not isinstance(agent, dict) or agent.get("active_jobs") != 0 for agent in agents
            )
        ):
            raise SecureUpdateError("Jarvis must be healthy and idle before an update")

    async def stop(self, app_path: Path) -> None:
        executable = str(app_path / "Contents/MacOS/Jarvis")
        selected: list[psutil.Process] = []
        for process in psutil.process_iter(("pid", "exe", "cmdline")):
            try:
                command = process.info.get("cmdline") or []
                path = process.info.get("exe")
                if path == executable or (command and command[0] == executable):
                    selected.append(process)
            except (psutil.AccessDenied, psutil.NoSuchProcess):
                continue
        if not selected:
            if self._settings.ipc_socket_path.exists():
                raise SecureUpdateError("Jarvis process identity is ambiguous")
            return
        for process in selected:
            process.terminate()
        _, alive = await asyncio.to_thread(psutil.wait_procs, selected, timeout=10)
        if alive:
            raise SecureUpdateError("Jarvis did not stop gracefully")
        for _ in range(40):
            if not self._settings.ipc_socket_path.exists():
                return
            await asyncio.sleep(0.25)
        raise SecureUpdateError("Jarvis daemon did not release its IPC socket")

    async def start(self, app_path: Path, transaction_id: UUID | None = None) -> None:
        arguments = ["/usr/bin/open", "-g", "-n", str(app_path)]
        if transaction_id is not None:
            arguments.extend(("--args", "--update-probe", str(transaction_id)))
        process = await asyncio.create_subprocess_exec(
            *arguments,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        if await process.wait() != 0:
            raise SecureUpdateError("LaunchServices rejected the updated application")

    async def wait_healthy(self, expected_revision: str) -> bool:
        for _ in range(self._attempts):
            await asyncio.sleep(self._interval)
            try:
                client = self._client()
                health, security = await asyncio.gather(
                    client.call("health"), client.call("security.status")
                )
            except (OSError, ProtocolError, TimeoutError):
                continue
            if (
                health.ok
                and security.ok
                and health.payload.get("build_revision") == expected_revision
                and security.payload.get("state") == "intact"
            ):
                return True
        return False


class UpdateAuditRecorder(Protocol):
    def record(
        self,
        event_type: str,
        *,
        previous_revision: str,
        target_revision: str,
    ) -> None: ...


class DurableUpdateAuditRecorder:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    def record(
        self,
        event_type: str,
        *,
        previous_revision: str,
        target_revision: str,
    ) -> None:
        log = HashChainAuditLog(
            self._settings.ipc_socket_path.parent / "audit.jsonl",
            max_bytes=self._settings.audit_max_bytes,
        )
        anchor = DurableAuditAnchor(MacOSAuditAnchor())
        anchor.validate_startup(log)
        log.record_system_event(
            uuid4(),
            event_type=event_type,
            component="secure-updater",
            data={
                "previous_revision": previous_revision,
                "target_revision": target_revision,
            },
        )
        anchor.seal_shutdown(log)


class AtomicBundleTransaction:
    """APFS same-volume swap with a durable, path-independent recovery journal."""

    def __init__(self, installed_app: Path, state_directory: Path) -> None:
        _require_private_directory(state_directory)
        self.installed_app = installed_app
        self.state_directory = state_directory
        self.journal_path = state_directory / TRANSACTION_FILE_NAME

    def prepare(
        self,
        candidate_app: Path,
        *,
        previous_revision: str,
        target_revision: str,
    ) -> UpdateTransactionRecord:
        if self.journal_path.exists():
            raise UpdateTransactionError("an unfinished update transaction must be recovered")
        root = candidate_app.parent
        transaction_id = self._transaction_id(root)
        self._validate_layout(root, candidate_app)
        record = UpdateTransactionRecord(
            schema_version=UPDATE_SCHEMA_VERSION,
            transaction_id=transaction_id,
            state="prepared",
            previous_revision=previous_revision,
            target_revision=target_revision,
            installer_pid=os.getpid(),
        )
        self._write_journal(record)
        return record

    def swap(self, record: UpdateTransactionRecord, candidate_app: Path) -> None:
        root = candidate_app.parent
        self._validate_record_root(record, root)
        backup = root / "previous.app"
        if backup.exists() or not self.installed_app.is_dir():
            raise UpdateTransactionError("atomic swap preconditions are invalid")
        try:
            os.rename(self.installed_app, backup)
            os.rename(candidate_app, self.installed_app)
        except OSError as error:
            if backup.exists() and not self.installed_app.exists():
                os.rename(backup, self.installed_app)
            raise UpdateTransactionError("atomic bundle swap failed") from error
        self._write_journal(record.model_copy(update={"state": "swapped"}))

    def commit(self, record: UpdateTransactionRecord) -> None:
        root = self._root_for(record)
        if not self.installed_app.is_dir() or not (root / "previous.app").is_dir():
            raise UpdateTransactionError("committed update layout is invalid")
        self._write_journal(record.model_copy(update={"state": "committed"}))

    def rollback(self, record: UpdateTransactionRecord) -> None:
        root = self._root_for(record)
        backup = root / "previous.app"
        failed = root / "failed.app"
        if not backup.is_dir():
            raise UpdateTransactionError("rollback backup is unavailable")
        try:
            if self.installed_app.exists():
                if failed.exists():
                    self._remove_transaction_tree(failed)
                os.rename(self.installed_app, failed)
            os.rename(backup, self.installed_app)
            self._remove_transaction_tree(root)
            self.journal_path.unlink(missing_ok=True)
        except OSError as error:
            raise UpdateTransactionError("automatic rollback failed") from error

    def recover(self) -> Literal["none", "rolled_back", "cleaned"]:
        if not self.journal_path.exists():
            return "none"
        record = self.read_record()
        root = self._root_for(record)
        if record.state == "committed":
            self._remove_transaction_tree(root)
            self.journal_path.unlink(missing_ok=True)
            return "cleaned"
        if record.state == "prepared" and self.installed_app.is_dir():
            backup = root / "previous.app"
            if not backup.exists():
                self._remove_transaction_tree(root)
                self.journal_path.unlink(missing_ok=True)
                return "cleaned"
        self.rollback(record)
        return "rolled_back"

    def read_record(self) -> UpdateTransactionRecord:
        _require_regular_file(self.journal_path, maximum_bytes=8_192)
        try:
            return UpdateTransactionRecord.model_validate_json(
                self.journal_path.read_bytes()
            )
        except (OSError, ValueError) as error:
            raise UpdateTransactionError("update recovery journal is invalid") from error

    def _write_journal(self, record: UpdateTransactionRecord) -> None:
        _atomic_private_write(
            self.journal_path,
            _canonical_bytes(record.model_dump(mode="json")),
        )

    def _root_for(self, record: UpdateTransactionRecord) -> Path:
        root = self.installed_app.parent / f"{TRANSACTION_PREFIX}{record.transaction_id}"
        self._validate_record_root(record, root)
        return root

    def _validate_record_root(self, record: UpdateTransactionRecord, root: Path) -> None:
        if root.name != f"{TRANSACTION_PREFIX}{record.transaction_id}":
            raise UpdateTransactionError("update transaction path is invalid")
        self._validate_root(root)

    def _validate_layout(self, root: Path, candidate_app: Path) -> None:
        self._validate_root(root)
        if (
            candidate_app != root / "Jarvis.app"
            or candidate_app.is_symlink()
            or not candidate_app.is_dir()
        ):
            raise UpdateTransactionError("update candidate path is invalid")

    def _validate_root(self, root: Path) -> None:
        metadata = root.lstat()
        if (
            root.parent.resolve(strict=True) != self.installed_app.parent.resolve(strict=True)
            or re.fullmatch(r"\.aegis-update-[0-9a-f-]{36}", root.name) is None
            or stat.S_ISLNK(metadata.st_mode)
            or not stat.S_ISDIR(metadata.st_mode)
            or metadata.st_uid != os.getuid()
            or stat.S_IMODE(metadata.st_mode) & 0o077
        ):
            raise UpdateTransactionError("update transaction directory is unsafe")

    @staticmethod
    def _transaction_id(root: Path) -> UUID:
        try:
            return UUID(root.name.removeprefix(TRANSACTION_PREFIX))
        except ValueError as error:
            raise UpdateTransactionError("update transaction identifier is invalid") from error

    def _remove_transaction_tree(self, path: Path) -> None:
        root = path if path.name.startswith(TRANSACTION_PREFIX) else path.parent
        self._validate_root(root)
        if path.is_symlink():
            raise UpdateTransactionError("refusing to remove a symbolic link")
        if path == root:
            shutil.rmtree(root)
        elif path.parent == root:
            shutil.rmtree(path)
        else:
            raise UpdateTransactionError("refusing to remove an unrelated path")


class SecureUpdateInstaller:
    def __init__(
        self,
        *,
        installed_app: Path,
        state_directory: Path,
        trust: BundleTrustVerifier,
        lifecycle: AppLifecycle,
        audit: UpdateAuditRecorder,
    ) -> None:
        self._installed_app = installed_app
        self._transaction = AtomicBundleTransaction(installed_app, state_directory)
        self._trust = trust
        self._lifecycle = lifecycle
        self._audit = audit

    async def install(
        self,
        manifest_path: Path,
        archive_path: Path,
        release_manifest_path: Path,
        public_key_path: Path,
        *,
        confirmation_revision: str,
    ) -> VerifiedUpdate:
        current = read_installed_identity(self._installed_app)
        verified = await asyncio.to_thread(
            verify_signed_update,
            manifest_path,
            archive_path,
            release_manifest_path,
            public_key_path,
            current_build=current.build,
        )
        target = verified.manifest.payload
        if confirmation_revision != target.build_revision:
            raise SecureUpdateError(
                "explicit update confirmation does not match the target revision"
            )
        await self._lifecycle.require_idle(current.revision)
        team_id = await asyncio.to_thread(self._trust.validate_current, self._installed_app)
        transaction_id = uuid4()
        root = self._installed_app.parent / f"{TRANSACTION_PREFIX}{transaction_id}"
        root.mkdir(mode=0o700)
        incoming = root / "incoming"
        incoming.mkdir(mode=0o700)
        extracted = await asyncio.create_subprocess_exec(
            "/usr/bin/ditto",
            "-x",
            "-k",
            str(archive_path),
            str(incoming),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        if await extracted.wait() != 0:
            self._transaction._remove_transaction_tree(root)
            raise SecureUpdateError("update archive extraction failed")
        candidate = incoming / "Jarvis.app"
        final_candidate = root / "Jarvis.app"
        if candidate.is_symlink() or not candidate.is_dir():
            self._transaction._remove_transaction_tree(root)
            raise SecureUpdateError("update archive did not produce a safe application bundle")
        os.rename(candidate, final_candidate)
        incoming.rmdir()
        try:
            await asyncio.to_thread(
                self._trust.validate_candidate,
                final_candidate,
                expected_team_id=team_id,
                expected=target,
                pinned_public_key=current.public_key,
            )
            record = self._transaction.prepare(
                final_candidate,
                previous_revision=current.revision,
                target_revision=target.build_revision,
            )
            await self._lifecycle.stop(self._installed_app)
            self._transaction.swap(record, final_candidate)
            try:
                await asyncio.to_thread(
                    self._audit.record,
                    "secure_update_swapped",
                    previous_revision=current.revision,
                    target_revision=target.build_revision,
                )
                await self._lifecycle.start(self._installed_app, record.transaction_id)
                if not await self._lifecycle.wait_healthy(target.build_revision):
                    raise SecureUpdateError("updated Jarvis failed its health deadline")
            except Exception:
                try:
                    await self._lifecycle.stop(self._installed_app)
                except Exception:
                    pass
                self._transaction.rollback(record)
                await asyncio.to_thread(
                    self._audit.record,
                    "secure_update_rolled_back",
                    previous_revision=current.revision,
                    target_revision=target.build_revision,
                )
                await self._lifecycle.start(self._installed_app)
                if not await self._lifecycle.wait_healthy(current.revision):
                    raise UpdateTransactionError(
                        "rollback restored files but not service health"
                    ) from None
                raise
            self._transaction.commit(record)
            if self._transaction.recover() != "cleaned":
                raise UpdateTransactionError("committed update cleanup failed")
            return verified
        except Exception:
            if self._transaction.journal_path.exists():
                pending = self._transaction.read_record()
                if pending.state == "prepared":
                    self._transaction.recover()
            elif root.exists():
                self._transaction._remove_transaction_tree(root)
            raise

    async def recover(self) -> Literal["none", "rolled_back", "cleaned"]:
        if not self._transaction.journal_path.exists():
            return "none"
        record = self._transaction.read_record()
        if record.state == "swapped":
            await self._lifecycle.stop(self._installed_app)
        result = self._transaction.recover()
        if result == "rolled_back":
            await asyncio.to_thread(
                self._audit.record,
                "secure_update_recovered",
                previous_revision=record.previous_revision,
                target_revision=record.target_revision,
            )
            await self._lifecycle.start(self._installed_app)
            if not await self._lifecycle.wait_healthy(record.previous_revision):
                raise UpdateTransactionError("recovered release did not become healthy")
        return result


def default_secure_update_installer() -> SecureUpdateInstaller:
    settings = Settings()
    return SecureUpdateInstaller(
        installed_app=Path.home() / "Applications/Jarvis.app",
        state_directory=settings.ipc_socket_path.parent,
        trust=MacOSBundleTrustVerifier(),
        lifecycle=MacOSAppLifecycle(settings),
        audit=DurableUpdateAuditRecorder(settings),
    )


def default_update_paths_from_frozen_executable() -> tuple[Path, Path, Path]:
    executable = Path(os.path.realpath(os.sys.executable))
    expected_suffix = Path("Contents/Resources/Daemon/jarvis-daemon")
    if tuple(executable.parts[-len(expected_suffix.parts) :]) != expected_suffix.parts:
        raise SecureUpdateError("update command is not running from a Jarvis bundle")
    app = executable.parents[3]
    if app.name != "Jarvis.app":
        raise SecureUpdateError("update command bundle identity is invalid")
    resource = app / "Contents/Resources" / UPDATE_PUBLIC_KEY_NAME
    state = Path.home() / "Library/Application Support/Aegis"
    return app, resource, state
