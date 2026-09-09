from __future__ import annotations

import subprocess
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
GATE = PROJECT_ROOT / "script/rc3_rc8_gate.sh"


def test_rc3_rc8_gate_is_syntactically_valid_and_fail_closed() -> None:
    completed = subprocess.run(
        ["/bin/bash", "-n", str(GATE)],
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 0, completed.stderr
    script = GATE.read_text(encoding="utf-8")
    assert "set -euo pipefail" in script
    assert "umask 077" in script
    assert "tracked_source_tree_dirty" in script
    assert "installed_app_revision_mismatch" in script
    assert "codesign --verify --deep --strict" in script
    assert '"$AEGIS_INSTALLED_APP/Contents/Helpers"' in script
    assert "Contents/Resources/Helpers" not in script
    assert "installed_bundle_size_budget_exceeded" in script
    assert "swift_executable_size_budget_exceeded" in script
    assert "/bin/chmod 600" in script


def test_rc3_rc8_gate_covers_every_nonvoice_release_block() -> None:
    script = GATE.read_text(encoding="utf-8")

    for block in range(3, 9):
        assert f"RC{block}" in script
    assert "release-scope" in script
    assert "jarvis_beta.sh check" in script
    assert '--request "Hola"' in script
    assert "--inference-policy local_only" in script
    assert "acceptance-benchmark" in script
    assert "production-workflows" in script
    assert "daemon-recovery" in script
    assert "self-evaluation" in script
    assert "p9_browser_gate.sh" in script
    assert "AEGIS_P7_SOAK_CYCLES=200" in script


def test_rc3_rc8_gate_never_fakes_owner_voice_evidence() -> None:
    script = GATE.read_text(encoding="utf-8")

    assert '\\"requires_owner_voice\\":false' in script
    assert "owner_voice=deferred" in script
    assert "voice-qualification" not in script
    assert "p10_voice_gate" not in script
    assert "p11_pilot_release_gate" not in script
    assert "macos-qualification" not in script
    assert "voice.submit" not in script


def test_rc3_rc8_report_is_privacy_minimal() -> None:
    script = GATE.read_text(encoding="utf-8")

    for private_field in (
        "contains_prompt_text",
        "contains_transcripts",
        "contains_audio",
        "contains_images",
    ):
        assert f'\\"{private_field}\\":false' in script
    assert "target_urls" not in script
    assert "spoken" not in script.casefold()
