from __future__ import annotations

from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def test_daily_beta_gate_is_local_private_and_complete() -> None:
    script = (PROJECT_ROOT / "script/jarvis_beta.sh").read_text(encoding="utf-8")

    assert "set -euo pipefail" in script
    assert "codesign --verify --deep --strict" in script
    assert "AEGIS_SOAK_CYCLES=20" in script
    assert "self-evaluation" in script
    assert "acceptance-benchmark" in script
    assert "production-workflows" in script
    assert "long-horizon-reliability" in script
    assert "daemon-status" in script
    assert "runtime-readiness.json" in script
    assert 'value["schema_version"] != "2.0"' in script
    assert '"build_revision"' in script
    assert "native_build_mismatch" in script
    assert "AegisBuildRevision" in script
    assert "AEGIS_SOURCE_REVISION" in script
    assert "expected_mode=600" in script
    assert "network_calls=0" in script
    assert 'menu_bar_service.sh" wake-word-on' in script
    assert "probe-nvidia" not in script
    assert "macos-qualification" not in script


def test_beta_installer_preserves_stable_tcc_identity() -> None:
    script = (PROJECT_ROOT / "script/jarvis_beta.sh").read_text(encoding="utf-8")

    identity_check = 'local_codesign_identity.sh" status'
    menu_install = 'menu_bar_service.sh" install'
    daemon_install = 'daemon_service.sh" install'
    assert identity_check in script
    assert script.index(identity_check) < script.index(menu_install)
    assert script.index(menu_install) < script.index(daemon_install)


def test_launch_agents_do_not_kill_new_process_after_bootstrap() -> None:
    for name in ("daemon_service.sh", "menu_bar_service.sh"):
        script = (PROJECT_ROOT / "script" / name).read_text(encoding="utf-8")
        assert "kickstart -k" not in script

    daemon = (PROJECT_ROOT / "script/daemon_service.sh").read_text(encoding="utf-8")
    assert "stop_service_gracefully" in daemon
    assert 'launchctl disable "$AEGIS_DOMAIN/$AEGIS_LABEL"' in daemon
    assert '/bin/kill -TERM "$process_id"' in daemon


def test_menu_bar_status_is_bound_to_the_installed_executable() -> None:
    script = (PROJECT_ROOT / "script/menu_bar_service.sh").read_text(encoding="utf-8")

    assert "installed_app_is_running" in script
    installed_binary_assignment = (
        'AEGIS_INSTALLED_BINARY="$AEGIS_INSTALLED_BUNDLE/Contents/MacOS/$AEGIS_APP_NAME"'
    )
    assert installed_binary_assignment in script
    assert '/bin/ps -p "$process_id" -o command=' in script
    assert "if ! installed_app_is_running" in script


def test_menu_bar_restart_cannot_reuse_a_stale_bundled_daemon() -> None:
    script = (PROJECT_ROOT / "script/menu_bar_service.sh").read_text(encoding="utf-8")

    daemon_assignment = (
        'AEGIS_INSTALLED_DAEMON="$AEGIS_INSTALLED_BUNDLE/'
        'Contents/Resources/Daemon/jarvis-daemon"'
    )
    assert daemon_assignment in script
    assert "installed_daemon_pids" in script
    assert '/bin/ps -p "$process_id" -o command=' in script
    assert '/bin/kill -TERM "$process_id"' in script
    assert "reason=bundled_daemon_did_not_stop" in script
    assert script.count("stop_installed_daemon") >= 5
    assert "restart_service" in script
    assert "restart)" in script


def test_p7_gate_proves_installed_runtime_without_owner_voice() -> None:
    script = (PROJECT_ROOT / "script/p7_reliability_gate.sh").read_text(encoding="utf-8")

    assert "set -euo pipefail" in script
    assert "AegisBuildRevision" in script
    assert "installed_app_revision_mismatch" in script
    assert "./script/jarvis_beta.sh check" in script
    assert "long-horizon-reliability" in script
    assert "daemon-soak" in script
    assert "owner_voice=deferred" in script
    assert "macos-qualification" not in script
    assert "voice.submit" not in script
    assert "swarm.submit" not in script


def test_p8_gate_uses_only_an_ephemeral_non_voice_fixture() -> None:
    script = (PROJECT_ROOT / "script/p8_application_gate.sh").read_text(encoding="utf-8")

    assert "set -euo pipefail" in script
    assert "installed_app_revision_mismatch" in script
    assert "./script/p7_reliability_gate.sh" in script
    assert "/usr/bin/xcrun --find swiftc" in script
    assert "JarvisUIQualificationFixture/main.swift" in script
    assert "-framework AppKit" in script
    assert '"$AEGIS_SWIFT" build' not in script
    assert "/private/tmp/aegis-p8." in script
    assert "/usr/bin/open -g -n" in script
    assert "application-qualification" in script
    assert "owner_voice=deferred" in script
    assert "macos-qualification" not in script
    assert "voice.submit" not in script


def test_p9_gate_uses_real_chrome_with_an_ephemeral_offline_profile() -> None:
    script = (PROJECT_ROOT / "script/p9_browser_gate.sh").read_text(encoding="utf-8")

    assert "set -euo pipefail" in script
    assert "installed_app_revision_mismatch" in script
    assert "./script/p8_application_gate.sh" in script
    assert 'AEGIS_CHROME_APP="/Applications/Google Chrome.app"' in script
    assert "/usr/bin/codesign --verify --deep --strict" in script
    assert "--remote-debugging-address=127.0.0.1" in script
    assert "--remote-debugging-port=9222" in script
    assert '--user-data-dir="$AEGIS_PROFILE"' in script
    assert "--disable-background-networking" in script
    assert "--proxy-server=127.0.0.1:9" in script
    assert "browser-driver-qualification" in script
    assert "/private/tmp/aegis-p9." in script
    assert "owner_voice=deferred" in script
    assert "macos-qualification" not in script
    assert "voice.submit" not in script
    assert "swarm.submit" not in script


def test_p10_gate_requires_real_owner_voice_without_persisting_audio() -> None:
    script = (PROJECT_ROOT / "script/p10_voice_gate.sh").read_text(encoding="utf-8")

    assert "set -euo pipefail" in script
    assert "umask 077" in script
    assert "installed_app_revision_mismatch" in script
    assert "./script/p9_browser_gate.sh" in script
    assert "AEGIS_RUN_OWNER_VOICE_QUALIFICATION=1" in script
    assert "jarvis-biometric-calibrator" in script
    assert "voice-qualification" in script
    assert "menu_bar_service.sh wake-word-on" in script
    assert "runtime-evidence.json" in script
    assert "/private/tmp/aegis-p10." in script
    assert "voice.submit" not in script
    assert "swarm.submit" not in script


def test_p11_gate_promotes_only_one_coherent_private_pilot_build() -> None:
    script = (PROJECT_ROOT / "script/p11_pilot_release_gate.sh").read_text(
        encoding="utf-8"
    )

    assert "set -euo pipefail" in script
    assert "umask 077" in script
    assert "installed_app_revision_mismatch" in script
    assert "tracked_source_tree_dirty" in script
    assert "./script/p10_voice_gate.sh" in script
    assert "macos-qualification" in script
    assert "./script/release_macos.sh candidate" in script
    assert "pilot-release-qualification" in script
    assert "Jarvis.pilot.json" in script
    assert "/private/tmp/aegis-p11." in script
    assert "voice.submit" not in script
    assert "swarm.submit" not in script
