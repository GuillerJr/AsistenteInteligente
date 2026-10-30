"""Exercise the CI shell fragment without running Swift or its complete suite."""

import json
import os
import subprocess
from pathlib import Path

import pytest
import yaml

WORKFLOW = Path(__file__).resolve().parents[1] / ".github/workflows/ci.yml"


@pytest.fixture
def workflow_steps():
    if not WORKFLOW.is_file():
        pytest.skip("CI workflow belongs to the source checkout, not the runtime archive")
    workflow = yaml.safe_load(WORKFLOW.read_text())
    return workflow["jobs"]["python"]["steps"]


@pytest.mark.parametrize("wrapper_status", [37, 0])
def test_swift_diagnostic_preserves_guard_arguments_output_and_exit_status(
    tmp_path, workflow_steps, wrapper_status,
):
    step = next(item for item in workflow_steps if item.get("id") == "native_contracts")
    script = tmp_path / "script/test_desktop.sh"
    script.parent.mkdir()
    arguments = tmp_path / "wrapper-arguments.json"
    script.write_text(
        "#!/bin/bash\n"
        "printf '%s\\n' \"$@\" > \"$JARVIS_TEST_CAPTURE_ARGUMENTS\"\n"
        "printf '%s\\n' 'swift-diagnostic-stdout-marker'\n"
        "printf '%s\\n' 'swift-diagnostic-stderr-marker' >&2\n"
        f"exit {wrapper_status}\n",
    )
    script.chmod(0o700)
    runner_temp = tmp_path / "runner temp"
    runner_temp.mkdir()

    # Execute the actual YAML run body. A failing left side of the tee pipeline
    # must still fail the step, rather than inheriting tee's successful exit.
    result = subprocess.run(
        ["/bin/bash", "--noprofile", "--norc", "-c", step["run"]],
        cwd=tmp_path,
        env={"PATH": os.defpath, "RUNNER_TEMP": str(runner_temp),
             "JARVIS_TEST_CAPTURE_ARGUMENTS": str(arguments)},
        capture_output=True, text=True, timeout=10, check=False,
    )

    assert result.returncode == wrapper_status
    assert arguments.read_text().splitlines() == ["--full", "--budget-mib", "6144"]
    expected = "swift-diagnostic-stdout-marker\nswift-diagnostic-stderr-marker\n"
    assert result.stdout == expected
    assert result.stderr == ""
    log = runner_temp / "jarvis-swift-diagnostic.log"
    assert log.read_text() == expected
    assert list(runner_temp.iterdir()) == [log]
    assert step["shell"] == "bash"
    assert step["if"] == (
        "${{ !cancelled() && runner.os == 'macOS' && "
        "steps.dependencies.outcome == 'success' && "
        "steps.memory_model.outcome == 'success' }}"
    )


def test_swift_diagnostic_upload_precedes_python_and_selects_only_its_log(workflow_steps):
    swift_index = next(index for index, item in enumerate(workflow_steps)
                       if item.get("id") == "native_contracts")
    upload = workflow_steps[swift_index + 1]
    assert upload["id"] == "native_diagnostic"
    assert workflow_steps[swift_index + 2]["id"] == "contracts"
    assert upload["uses"] == "actions/upload-artifact@v4"
    assert upload["if"] == (
        "${{ always() && runner.os == 'macOS' && "
        "steps.native_contracts.outcome != 'skipped' && "
        "steps.native_contracts.outcome != '' }}"
    )
    assert upload["with"] == {
        "name": "jarvis-swift-diagnostic-${{ github.sha }}",
        "path": "${{ runner.temp }}/jarvis-swift-diagnostic.log",
        "if-no-files-found": "error",
        "retention-days": 3,
    }
    # A single literal file prevents uploading sibling profiles, keys or the
    # guarded run directory. Keep the assertion legible in a CI failure.
    assert "*" not in json.dumps(upload["with"])
