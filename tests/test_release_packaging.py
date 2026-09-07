from __future__ import annotations

import plistlib
import shutil
import subprocess
import tempfile
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def test_release_entitlements_are_explicit_and_minimal() -> None:
    with (PROJECT_ROOT / "native/AegisAudio/AppBundle/Jarvis.entitlements").open(
        "rb"
    ) as source:
        entitlements = plistlib.load(source)

    assert entitlements == {
        "com.apple.security.automation.apple-events": True,
        "com.apple.security.device.audio-input": True,
        "com.apple.security.device.camera": True,
        "com.apple.security.device.microphone": True,
        "com.apple.security.personal-information.addressbook": True,
        "com.apple.security.personal-information.calendars": True,
    }
    with (PROJECT_ROOT / "native/AegisAudio/AppBundle/Info.plist").open("rb") as source:
        info = plistlib.load(source)
    assert info["NSMicrophoneUsageDescription"]
    assert info["NSAppleEventsUsageDescription"]
    assert info["NSContactsUsageDescription"]
    assert info["NSCalendarsFullAccessUsageDescription"]


def test_release_pipeline_polls_notarytool_and_staples_before_repacking() -> None:
    release_script = (PROJECT_ROOT / "script/release_macos.sh").read_text(
        encoding="utf-8"
    )
    build_script = (PROJECT_ROOT / "script/build_and_run.sh").read_text(
        encoding="utf-8"
    )

    assert "Developer ID Application:" in release_script
    assert "notarytool submit" in release_script
    assert "notarytool info" in release_script
    assert "--wait" not in release_script
    assert release_script.index("stapler staple") < release_script.index("spctl --assess")
    assert "--options runtime" in build_script
    assert "--deep" in build_script
    assert 'AEGIS_TIMESTAMP_ARGUMENT="--timestamp"' in build_script
    assert "jarvis-mlx-engine" in build_script
    assert "mlx-swift_Cmlx.bundle" in build_script
    assert "swift-transformers_Hub.bundle" in build_script
    assert "AegisAudio_AegisAudioCore.bundle" in build_script
    assert "AegisBuildRevision" in build_script
    assert "AegisBuildDirty" in build_script
    assert "AEGIS_BUILD_MLX=1" in release_script


def test_swiftpm_scratch_recovery_removes_only_incomplete_temporary_state() -> None:
    scratch = Path(tempfile.mkdtemp(prefix="aegis-scratch-test-", dir="/private/tmp"))
    recovery = PROJECT_ROOT / "script/prepare_swift_scratch.sh"
    try:
        broken_checkout = scratch / "checkouts/dependency"
        broken_checkout.mkdir(parents=True)
        (scratch / "repositories/dependency").mkdir(parents=True)
        (scratch / "workspace-state.json").write_text("{}", encoding="utf-8")

        repaired = subprocess.run(
            (str(recovery), str(scratch)),
            check=False,
            capture_output=True,
            text=True,
            timeout=10,
        )
        assert repaired.returncode == 0, repaired.stderr
        assert "Recovering an incomplete" in repaired.stdout
        assert not (scratch / "checkouts").exists()
        assert not (scratch / "repositories").exists()
        assert not (scratch / "workspace-state.json").exists()

        healthy_checkout = scratch / "checkouts/dependency"
        healthy_checkout.mkdir(parents=True)
        package_manifest = healthy_checkout / "Package.swift"
        package_manifest.write_text("// swift-tools-version: 6.2\n", encoding="utf-8")
        preserved = subprocess.run(
            (str(recovery), str(scratch)),
            check=False,
            capture_output=True,
            text=True,
            timeout=10,
        )
        assert preserved.returncode == 0, preserved.stderr
        assert preserved.stdout == ""
        assert package_manifest.is_file()
    finally:
        shutil.rmtree(scratch)
