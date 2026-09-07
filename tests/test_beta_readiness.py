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
    assert "macos-qualification" in script
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
