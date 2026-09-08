from __future__ import annotations

import base64
import hashlib
import json
import plistlib
import zipfile
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from aegis_core.secure_update import (
    UPDATE_PUBLIC_KEY_NAME,
    AtomicBundleTransaction,
    SecureUpdateError,
    SecureUpdateInstaller,
    SignedUpdateManifest,
    UpdateArtifact,
    UpdatePayload,
    UpdatePrivacy,
    UpdateRollbackError,
    UpdateSignatureError,
    UpdateTransactionError,
    create_signed_update_manifest,
    encode_public_key,
    public_key_id,
    verify_signed_update,
)
from aegis_core.secure_update_qualification import SecureUpdateQualificationGate

OLD_REVISION = "a" * 40
NEW_REVISION = "b" * 40
PUBLISHED = datetime(2026, 9, 8, 12, tzinfo=UTC)


def _canonical(value: object) -> bytes:
    return (json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n").encode()


def _private(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(payload)
    path.chmod(0o600)


def _key_pair(tmp_path: Path) -> tuple[Ed25519PrivateKey, bytes, Path]:
    tmp_path.mkdir(parents=True, exist_ok=True)
    private = Ed25519PrivateKey.generate()
    public = private.public_key().public_bytes(
        serialization.Encoding.Raw,
        serialization.PublicFormat.Raw,
    )
    path = tmp_path / UPDATE_PUBLIC_KEY_NAME
    _private(path, f"{encode_public_key(public)}\n".encode())
    return private, public, path


def _app_info(*, build: int, revision: str) -> bytes:
    return plistlib.dumps(
        {
            "CFBundleIdentifier": "ai.aegis.menubar",
            "CFBundleName": "Jarvis",
            "CFBundleExecutable": "Jarvis",
            "CFBundleShortVersionString": f"0.{build}.0",
            "CFBundleVersion": str(build),
            "LSMinimumSystemVersion": "14.0",
            "AegisBuildRevision": revision,
            "AegisBuildDirty": False,
        }
    )


def _archive(tmp_path: Path, public: bytes, *, build: int = 2) -> Path:
    tmp_path.mkdir(parents=True, exist_ok=True)
    path = tmp_path / "Jarvis.zip"
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr(
            "Jarvis.app/Contents/Info.plist",
            _app_info(build=build, revision=NEW_REVISION),
        )
        archive.writestr("Jarvis.app/Contents/MacOS/Jarvis", b"arm64-app")
        archive.writestr(
            "Jarvis.app/Contents/Resources/Daemon/jarvis-daemon",
            b"arm64-daemon",
        )
        archive.writestr(
            f"Jarvis.app/Contents/Resources/{UPDATE_PUBLIC_KEY_NAME}",
            f"{encode_public_key(public)}\n".encode(),
        )
    path.chmod(0o600)
    return path


def _signed_fixture(
    tmp_path: Path,
    *,
    expires_at: datetime | None = None,
    build: int = 2,
    signing_key: Ed25519PrivateKey | None = None,
    archive_public_key: bytes | None = None,
) -> tuple[Path, Path, Path, Path, Ed25519PrivateKey]:
    tmp_path.mkdir(parents=True, exist_ok=True)
    if signing_key is None:
        private, public, public_path = _key_pair(tmp_path)
    else:
        private = signing_key
        public = private.public_key().public_bytes(
            serialization.Encoding.Raw,
            serialization.PublicFormat.Raw,
        )
        public_path = tmp_path / UPDATE_PUBLIC_KEY_NAME
        _private(public_path, f"{encode_public_key(public)}\n".encode())
    archive = _archive(tmp_path, archive_public_key or public, build=build)
    release = {
        "schema_version": "1.0",
        "product": "Jarvis",
        "bundle_identifier": "ai.aegis.menubar",
        "version": f"0.{build}.0",
        "build": str(build),
        "build_revision": NEW_REVISION,
        "source_tree_sha256": "c" * 64,
        "created_at": PUBLISHED.isoformat().replace("+00:00", "Z"),
        "profile": "developer-id-notarized",
        "architecture": "arm64",
        "minimum_macos": "14.0",
        "artifact": {
            "name": "Jarvis.zip",
            "format": "zip",
            "sha256": hashlib.sha256(archive.read_bytes()).hexdigest(),
            "size": archive.stat().st_size,
            "uncompressed_size": 1_024,
        },
        "bundle_files": [],
        "sbom": {"name": "Jarvis.spdx.json", "format": "SPDX-2.3", "sha256": "d" * 64},
        "privacy": {
            "contains_prompts": False,
            "contains_transcripts": False,
            "contains_images": False,
            "contains_credentials": False,
            "contains_absolute_paths": False,
        },
    }
    release_path = tmp_path / "Jarvis.release.json"
    _private(release_path, _canonical(release))
    payload = UpdatePayload(
        schema_version="1.0",
        product="Jarvis",
        bundle_identifier="ai.aegis.menubar",
        channel="stable",
        architecture="arm64",
        version=f"0.{build}.0",
        build=build,
        build_revision=NEW_REVISION,
        minimum_macos="14.0",
        published_at=PUBLISHED,
        expires_at=expires_at or PUBLISHED + timedelta(days=30),
        artifact=UpdateArtifact(
            name="Jarvis.zip",
            format="zip",
            sha256=hashlib.sha256(archive.read_bytes()).hexdigest(),
            size=archive.stat().st_size,
            release_manifest_sha256=hashlib.sha256(release_path.read_bytes()).hexdigest(),
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
    signature = private.sign(_canonical(payload.model_dump(mode="json")))
    envelope = SignedUpdateManifest(
        payload=payload,
        algorithm="Ed25519",
        key_id=public_key_id(public),
        signature=base64.b64encode(signature).decode(),
    )
    manifest = tmp_path / "Jarvis.update.json"
    _private(manifest, _canonical(envelope.model_dump(mode="json")))
    return manifest, archive, release_path, public_path, private


def test_signed_update_authenticates_archive_release_and_channel(tmp_path: Path) -> None:
    manifest, archive, release, public, _ = _signed_fixture(tmp_path)

    verified = verify_signed_update(
        manifest,
        archive,
        release,
        public,
        current_build=1,
        now=PUBLISHED + timedelta(hours=1),
    )

    assert verified.manifest.payload.build == 2
    assert verified.manifest.payload.build_revision == NEW_REVISION


def test_release_signer_reads_private_seed_without_exposing_it(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, archive, release, public, private = _signed_fixture(tmp_path)
    seed = private.private_bytes(
        serialization.Encoding.Raw,
        serialization.PrivateFormat.Raw,
        serialization.NoEncryption(),
    )

    class PrivateStore:
        def get(self) -> str:
            return "ed25519-private:" + base64.b64encode(seed).decode()

    monkeypatch.setattr(
        "aegis_core.secure_update.MacOSGenericSecret",
        lambda *_arguments: PrivateStore(),
    )
    output = tmp_path / "signed/Jarvis.update.json"

    envelope = create_signed_update_manifest(release, archive, output, public)
    verified = verify_signed_update(
        output,
        archive,
        release,
        public,
        current_build=1,
        now=PUBLISHED + timedelta(hours=1),
    )

    assert output.stat().st_mode & 0o777 == 0o600
    assert envelope == verified.manifest
    assert b"ed25519-private:" not in output.read_bytes()


def test_signed_update_rejects_signature_tampering(tmp_path: Path) -> None:
    manifest, archive, release, public, _ = _signed_fixture(tmp_path)
    value = json.loads(manifest.read_text())
    value["payload"]["version"] = "0.9.0"
    _private(manifest, _canonical(value))

    with pytest.raises(UpdateSignatureError, match="signature verification"):
        verify_signed_update(
            manifest,
            archive,
            release,
            public,
            current_build=1,
            now=PUBLISHED + timedelta(hours=1),
        )


def test_signed_update_rejects_downgrade_and_expired_replay(tmp_path: Path) -> None:
    manifest, archive, release, public, _ = _signed_fixture(tmp_path, build=2)

    with pytest.raises(UpdateRollbackError, match="not newer"):
        verify_signed_update(
            manifest,
            archive,
            release,
            public,
            current_build=2,
            now=PUBLISHED + timedelta(hours=1),
        )
    with pytest.raises(UpdateSignatureError, match="not currently valid"):
        verify_signed_update(
            manifest,
            archive,
            release,
            public,
            current_build=1,
            now=PUBLISHED + timedelta(days=31),
        )


def test_signed_update_rejects_public_key_rotation_inside_archive(tmp_path: Path) -> None:
    replacement = Ed25519PrivateKey.generate().public_key().public_bytes(
        serialization.Encoding.Raw,
        serialization.PublicFormat.Raw,
    )
    manifest, archive, release, public, _ = _signed_fixture(
        tmp_path,
        archive_public_key=replacement,
    )

    with pytest.raises(UpdateSignatureError, match="changed the pinned"):
        verify_signed_update(
            manifest,
            archive,
            release,
            public,
            current_build=1,
            now=PUBLISHED + timedelta(hours=1),
        )


def test_signed_update_rejects_incomplete_release_privacy_contract(tmp_path: Path) -> None:
    manifest, archive, release, public, private = _signed_fixture(tmp_path)
    release_value = json.loads(release.read_text())
    release_value["privacy"] = {}
    _private(release, _canonical(release_value))
    manifest_value = json.loads(manifest.read_text())
    manifest_value["payload"]["artifact"]["release_manifest_sha256"] = hashlib.sha256(
        release.read_bytes()
    ).hexdigest()
    payload = UpdatePayload.model_validate(manifest_value["payload"])
    manifest_value["signature"] = base64.b64encode(
        private.sign(_canonical(payload.model_dump(mode="json")))
    ).decode()
    _private(manifest, _canonical(manifest_value))

    with pytest.raises(SecureUpdateError, match="private update boundary"):
        verify_signed_update(
            manifest,
            archive,
            release,
            public,
            current_build=1,
            now=PUBLISHED + timedelta(hours=1),
        )


def test_atomic_transaction_rolls_back_exact_previous_bundle(tmp_path: Path) -> None:
    installed = tmp_path / "Applications/Jarvis.app"
    state = tmp_path / "state"
    _private(installed / "Contents/version", b"old")
    state.mkdir(mode=0o700)
    transaction_id = uuid4()
    root = installed.parent / f".aegis-update-{transaction_id}"
    _private(root / "Jarvis.app/Contents/version", b"new")
    root.chmod(0o700)
    transaction = AtomicBundleTransaction(installed, state)

    record = transaction.prepare(
        root / "Jarvis.app",
        previous_revision=OLD_REVISION,
        target_revision=NEW_REVISION,
    )
    transaction.swap(record, root / "Jarvis.app")
    transaction.rollback(record)

    assert (installed / "Contents/version").read_bytes() == b"old"
    assert not transaction.journal_path.exists()
    assert not root.exists()


def test_atomic_transaction_recovers_interrupted_swap(tmp_path: Path) -> None:
    installed = tmp_path / "Applications/Jarvis.app"
    state = tmp_path / "state"
    _private(installed / "Contents/version", b"old")
    state.mkdir(mode=0o700)
    transaction_id = uuid4()
    root = installed.parent / f".aegis-update-{transaction_id}"
    _private(root / "Jarvis.app/Contents/version", b"new")
    root.chmod(0o700)
    transaction = AtomicBundleTransaction(installed, state)
    record = transaction.prepare(
        root / "Jarvis.app",
        previous_revision=OLD_REVISION,
        target_revision=NEW_REVISION,
    )
    transaction.swap(record, root / "Jarvis.app")

    assert transaction.recover() == "rolled_back"
    assert (installed / "Contents/version").read_bytes() == b"old"


def test_atomic_transaction_rejects_public_or_linked_state_directory(
    tmp_path: Path,
) -> None:
    installed = tmp_path / "Applications/Jarvis.app"
    installed.mkdir(parents=True)
    public_state = tmp_path / "public-state"
    public_state.mkdir(mode=0o755)
    with pytest.raises(UpdateTransactionError, match="state directory is unsafe"):
        AtomicBundleTransaction(installed, public_state)

    private_state = tmp_path / "private-state"
    private_state.mkdir(mode=0o700)
    linked_state = tmp_path / "linked-state"
    linked_state.symlink_to(private_state, target_is_directory=True)
    with pytest.raises(UpdateTransactionError, match="state directory is unsafe"):
        AtomicBundleTransaction(installed, linked_state)


class _Trust:
    def validate_current(self, app_path: Path) -> str:
        assert app_path.name == "Jarvis.app"
        return "ABCDEFGHIJ"

    def validate_candidate(
        self,
        app_path: Path,
        *,
        expected_team_id: str,
        expected: UpdatePayload,
        pinned_public_key: bytes,
    ) -> None:
        assert expected_team_id == "ABCDEFGHIJ"
        assert expected.build == 2
        assert len(pinned_public_key) == 32
        assert app_path.is_dir()


class _Lifecycle:
    def __init__(self, *, healthy_revision: str = OLD_REVISION) -> None:
        self.starts: list[tuple[str, UUID | None]] = []
        self.health_revisions: list[str] = []
        self.healthy_revision = healthy_revision

    async def require_idle(self, expected_revision: str) -> None:
        assert expected_revision == OLD_REVISION

    async def stop(self, app_path: Path) -> None:
        assert app_path.name == "Jarvis.app"

    async def start(self, app_path: Path, transaction_id: UUID | None = None) -> None:
        self.starts.append((app_path.name, transaction_id))

    async def wait_healthy(self, expected_revision: str) -> bool:
        self.health_revisions.append(expected_revision)
        return expected_revision == self.healthy_revision


class _Audit:
    def __init__(self) -> None:
        self.events: list[str] = []

    def record(
        self,
        event_type: str,
        *,
        previous_revision: str,
        target_revision: str,
    ) -> None:
        assert previous_revision == OLD_REVISION
        assert target_revision == NEW_REVISION
        self.events.append(event_type)


@pytest.mark.asyncio
async def test_installer_restores_previous_bundle_when_new_daemon_is_unhealthy(
    tmp_path: Path,
) -> None:
    private, _, public_path = _key_pair(tmp_path)
    installed = tmp_path / "Applications/Jarvis.app"
    _private(installed / "Contents/Info.plist", _app_info(build=1, revision=OLD_REVISION))
    _private(installed / "Contents/MacOS/Jarvis", b"old-app")
    _private(
        installed / f"Contents/Resources/{UPDATE_PUBLIC_KEY_NAME}",
        public_path.read_bytes(),
    )
    manifest, archive, release, _, _ = _signed_fixture(
        tmp_path / "release",
        signing_key=private,
    )
    lifecycle = _Lifecycle()
    audit = _Audit()
    installer = SecureUpdateInstaller(
        installed_app=installed,
        state_directory=tmp_path / "state",
        trust=_Trust(),
        lifecycle=lifecycle,
        audit=audit,
    )

    with pytest.raises(SecureUpdateError, match="health deadline"):
        await installer.install(
            manifest,
            archive,
            release,
            public_path,
            confirmation_revision=NEW_REVISION,
        )

    assert (installed / "Contents/MacOS/Jarvis").read_bytes() == b"old-app"
    assert lifecycle.health_revisions == [NEW_REVISION, OLD_REVISION]
    assert lifecycle.starts[0][1] is not None
    assert lifecycle.starts[1][1] is None
    assert audit.events == ["secure_update_swapped", "secure_update_rolled_back"]


@pytest.mark.asyncio
async def test_installer_commits_healthy_update_and_removes_recovery_state(
    tmp_path: Path,
) -> None:
    private, _, public_path = _key_pair(tmp_path)
    installed = tmp_path / "Applications/Jarvis.app"
    _private(installed / "Contents/Info.plist", _app_info(build=1, revision=OLD_REVISION))
    _private(installed / "Contents/MacOS/Jarvis", b"old-app")
    _private(
        installed / f"Contents/Resources/{UPDATE_PUBLIC_KEY_NAME}",
        public_path.read_bytes(),
    )
    manifest, archive, release, _, _ = _signed_fixture(
        tmp_path / "release",
        signing_key=private,
    )
    lifecycle = _Lifecycle(healthy_revision=NEW_REVISION)
    audit = _Audit()
    state = tmp_path / "state"
    installer = SecureUpdateInstaller(
        installed_app=installed,
        state_directory=state,
        trust=_Trust(),
        lifecycle=lifecycle,
        audit=audit,
    )

    verified = await installer.install(
        manifest,
        archive,
        release,
        public_path,
        confirmation_revision=NEW_REVISION,
    )

    assert verified.manifest.payload.build_revision == NEW_REVISION
    assert (installed / "Contents/MacOS/Jarvis").read_bytes() == b"arm64-app"
    assert lifecycle.health_revisions == [NEW_REVISION]
    assert audit.events == ["secure_update_swapped"]
    assert not (state / "update-transaction.json").exists()
    assert not list(installed.parent.glob(".aegis-update-*"))


def _p13_report(tmp_path: Path, archive: Path, *, passed: bool = True) -> Path:
    checks = [
        {
            "check_id": check_id,
            "status": "passed" if passed else "blocked",
            "reason": "verified" if passed else "prerequisite_not_passed",
            "metrics": {},
        }
        for check_id in sorted(
            {
                "release.p12_chain",
                "runtime.embedded_daemon",
                "runtime.cold_start",
                "runtime.source_independence",
                "privacy.runtime_boundary",
            }
        )
    ]
    count = 5 if passed else 0
    report = tmp_path / "Jarvis.runtime.json"
    _private(
        report,
        _canonical(
            {
                "schema_version": "1.0",
                "profile": "self_contained_runtime_qualification",
                "build_revision": NEW_REVISION,
                "status": "passed" if passed else "blocked",
                "score": count * 20,
                "gate_passed": passed,
                "passed": count,
                "total": 5,
                "duration_ms": 1,
                "artifact_sha256": hashlib.sha256(archive.read_bytes()).hexdigest(),
                "checks": checks,
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
        ),
    )
    return report


def test_p14_authenticates_channel_and_proves_atomic_recovery(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manifest, archive, release, public, _ = _signed_fixture(tmp_path)
    p13 = _p13_report(tmp_path, archive)
    monkeypatch.setattr(
        "aegis_core.secure_update_qualification._source_revision",
        lambda _root: NEW_REVISION,
    )
    monkeypatch.setattr(
        "aegis_core.secure_update_qualification._require_clean_tracked_tree",
        lambda _root: None,
    )
    gate = SecureUpdateQualificationGate(
        project_root=tmp_path,
        p13_report_path=p13,
        update_manifest_path=manifest,
        release_manifest_path=release,
        release_archive_path=archive,
        public_key_path=public,
        clock=lambda: PUBLISHED + timedelta(hours=1),
    )

    report = gate.run()

    assert report.gate_passed is True
    assert report.score == 100
    assert [check.check_id for check in report.checks] == [
        "release.p13_chain",
        "channel.ed25519_authenticity",
        "channel.rollback_resistance",
        "installer.atomic_recovery",
        "privacy.update_boundary",
    ]
    assert report.private_dict()["privacy"]["network_calls"] == 0


def test_p14_preserves_p13_as_a_hard_prerequisite(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manifest, archive, release, public, _ = _signed_fixture(tmp_path)
    p13 = _p13_report(tmp_path, archive, passed=False)
    monkeypatch.setattr(
        "aegis_core.secure_update_qualification._source_revision",
        lambda _root: NEW_REVISION,
    )
    monkeypatch.setattr(
        "aegis_core.secure_update_qualification._require_clean_tracked_tree",
        lambda _root: None,
    )
    report = SecureUpdateQualificationGate(
        project_root=tmp_path,
        p13_report_path=p13,
        update_manifest_path=manifest,
        release_manifest_path=release,
        release_archive_path=archive,
        public_key_path=public,
        clock=lambda: PUBLISHED + timedelta(hours=1),
    ).run()

    assert report.gate_passed is False
    check = next(item for item in report.checks if item.check_id == "release.p13_chain")
    assert check.status.value == "blocked"


def test_p14_pipeline_chains_p13_and_publishes_private_evidence() -> None:
    root = Path(__file__).resolve().parents[1]
    gate = (root / "script/p14_secure_update_gate.sh").read_text(encoding="utf-8")
    release = (root / "script/release_macos.sh").read_text(encoding="utf-8")
    build = (root / "script/build_and_run.sh").read_text(encoding="utf-8")

    assert gate.index("./script/p13_self_contained_runtime_gate.sh") < gate.index(
        "secure-update-qualification"
    )
    assert gate.index("./script/release_macos.sh channel") < gate.index(
        "secure-update-qualification"
    )
    assert 'AEGIS_FINAL_REPORT="$AEGIS_PROJECT_ROOT/dist/Jarvis.update-qualification.json"' in gate
    assert "/bin/chmod 600" in gate
    assert "sign_update_channel" in release
    assert "developer-id-notarized" in release
    assert "AEGIS_RELEASE_BUILD_NUMBER" in release
    assert 'AEGIS_BUILD_NUMBER="$AEGIS_RELEASE_BUILD_NUMBER"' in release
    assert "JarvisUpdatePublicKey.ed25519" in build
