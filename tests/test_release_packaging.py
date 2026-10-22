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
    assert info["NSLocalNetworkUsageDescription"]
    assert info["CFBundleDevelopmentRegion"] == "es"
    assert info["CFBundleLocalizations"] == ["es"]

    with (PROJECT_ROOT / "packaging/JarvisDaemonLocal.entitlements").open("rb") as source:
        local_daemon_entitlements = plistlib.load(source)
    assert local_daemon_entitlements == {
        "com.apple.security.cs.disable-library-validation": True,
    }


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
    notarize_action = release_script.split("    notarize)\n", 1)[1].split("    channel)", 1)[0]
    assert notarize_action.index("stapler staple") < notarize_action.index("spctl --assess")
    assert notarize_action.index("spctl --assess") < notarize_action.index("promote_release")
    assert "--options runtime" in build_script
    assert "--deep" in build_script
    assert 'AEGIS_TIMESTAMP_ARGUMENT="--timestamp"' in build_script
    assert "AegisAudio_AegisVADRuntime.bundle" in build_script
    assert "AegisBuildRevision" in build_script
    assert "AegisBuildDirty" in build_script
    assert '"$AEGIS_PROJECT_ROOT/MANUAL_FACIL.md"' in build_script
    assert '"$AEGIS_APP_RESOURCES/es.lproj"' in build_script
    assert "AEGIS_DAEMON_LOCAL_ENTITLEMENTS_SOURCE" in build_script
    assert 'if [[ "$AEGIS_LOCAL_SIGNING" == "1" ]]' in build_script
    assert 'if [[ "$AEGIS_BUILD_CONFIGURATION" == "release" ]]' in build_script
    assert '/usr/bin/strip -x "$release_executable"' in build_script
    assert "AEGIS_MAX_SWIFT_EXECUTABLE_BYTES" in build_script
    assert "AEGIS_MAX_BUNDLE_KIB" in build_script
    assert "Release bundle exceeds size budget" in build_script
    daemon_builder = (PROJECT_ROOT / "script/build_daemon_bundle.sh").read_text(
        encoding="utf-8"
    )
    assert "os.path.realpath" in daemon_builder
    assert "/private/tmp/*" in daemon_builder
    assert 'die "unsafe_daemon_output"' in daemon_builder
    daemon_spec = (PROJECT_ROOT / "packaging/jarvis_daemon.spec").read_text(
        encoding="utf-8"
    )
    assert 'sys.path.insert(0, str(source_root))' in daemon_spec
    assert "PyInstaller resolved aegis_core outside the repository source tree" in daemon_spec


def test_release_never_builds_or_packages_retired_local_inference() -> None:
    for relative in (
        "script/build_and_run.sh",
        "script/release_macos.sh",
        "native/AegisAudio/Package.swift",
        "native/AegisAudio/Sources/AegisAudioCore/BundledDaemonSupervisor.swift",
    ):
        content = (PROJECT_ROOT / relative).read_text(encoding="utf-8")
        for retired in ("jarvis-mlx-engine", "jarvis-mlx-vlm", "jarvis-local-brain"):
            assert retired not in content, (relative, retired)
        assert "AEGIS_BUILD_MLX" not in content
        assert "mlx-swift_Cmlx.bundle" not in content


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


def test_onnx_is_linked_only_into_the_isolated_vad_helper() -> None:
    package = (PROJECT_ROOT / "native/AegisAudio/Package.swift").read_text()
    targets = package.split("let targets:", 1)[1]
    core = targets.split('name: "AegisAudioCore",', 1)[1].split(".target(", 1)[0]
    assert "onnxruntime" not in core
    assert 'name: "AegisVADRuntime"' in package
    assert 'name: "JarvisVADHelper", dependencies: ["AegisVADRuntime"]' in package
    build = (PROJECT_ROOT / "script/build_and_run.sh").read_text()
    assert 'build_product "$AEGIS_VAD_HELPER_NAME"' in build
    assert '"$AEGIS_VAD_HELPER_BINARY"' in build
    assert 'AegisAudio_AegisVADRuntime.bundle' in build
    assert 'AegisAudio_AegisAudioCore.bundle' not in build


def test_login_installation_does_not_create_another_startup_mechanism() -> None:
    installer = (PROJECT_ROOT / "script/menu_bar_service.sh").read_text()
    assert "write_plist" not in installer
    assert "launchctl bootstrap" not in installer
    assert '/usr/bin/open -g -n "$AEGIS_INSTALLED_BUNDLE"' in installer
    assert "--disable-login-item" in installer
    assert "resolve_installed_bundle.sh" in (PROJECT_ROOT / "script/jarvis_beta.sh").read_text()


def test_version_and_bundled_notices_are_part_of_distribution() -> None:
    with (PROJECT_ROOT / "native/AegisAudio/AppBundle/Info.plist").open("rb") as source:
        info = plistlib.load(source)
    assert info["CFBundleShortVersionString"] == "1.0.0"
    build = (PROJECT_ROOT / "script/build_and_run.sh").read_text()
    assert "installed_build + 1" in build
    assert "bundle_notices.py" in build
    assert (PROJECT_ROOT / "CHANGELOG.md").is_file()
