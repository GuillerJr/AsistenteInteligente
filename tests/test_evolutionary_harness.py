from __future__ import annotations

from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def test_evolutionary_harness_uses_the_same_native_output_contract_as_swift_tests() -> None:
    harness = (PROJECT_ROOT / "script/jarvis_evolutionary_test_harness.py").read_text(
        encoding="utf-8"
    )
    native_tests = (PROJECT_ROOT / "script/test_native.sh").read_text(encoding="utf-8")

    assert '"/private/tmp/aegis-menubar-build"' in harness
    assert 'native_test_root / "out/Products/Debug"' in harness
    assert "/private/tmp/aegis-menubar-build" in native_tests
    assert '"tests/test_production_workflows.py"' in harness
    assert '"tests/test_long_horizon_reliability.py"' in harness
    assert '"tests/test_voice_qualification.py"' in harness
    assert '"production-workflows"' in harness
    assert '"long-horizon-reliability"' in harness
    assert 'production_report.get("total") != 20' in harness
    assert 'reliability_report.get("workflow_runs") != 400' in harness
    assert 'os.environ.get("AEGIS_RUN_OWNER_VOICE_QUALIFICATION") == "1"' in harness
    assert '"deferred_until_voice_final"' in harness
