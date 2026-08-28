from __future__ import annotations

import plistlib
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
