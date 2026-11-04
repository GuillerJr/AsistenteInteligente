"""Exercise the CI shell fragment without running Swift or its complete suite."""

import json
import os
import subprocess
from pathlib import Path

import pytest
import yaml

WORKFLOW = Path(__file__).resolve().parents[1] / ".github/workflows/ci.yml"
MEDIA_WORKFLOW = WORKFLOW.with_name("media-diagnostic.yml")


@pytest.fixture
def workflow_steps():
    if not WORKFLOW.is_file():
        pytest.skip("CI workflow belongs to the source checkout, not the runtime archive")
    workflow = yaml.safe_load(WORKFLOW.read_text())
    return workflow["jobs"]["python"]["steps"]


@pytest.fixture
def media_workflow(workflow_steps):
    # Reuse the source-checkout admission; runtime archives do not carry workflows.
    return yaml.safe_load(MEDIA_WORKFLOW.read_text())


def test_media_workflow_xcode_choice_is_closed_and_precedes_actual_environment(media_workflow):
    triggers = media_workflow.get("on", media_workflow.get(True))
    assert triggers["workflow_dispatch"]["inputs"]["runner"] == {
        "description": "Runner oficial para el diagnóstico focal", "type": "choice",
        "required": True, "default": "xcode-27", "options": ["xcode-27", "macos-26"]}
    assert triggers["workflow_dispatch"]["inputs"]["xcode"] == {
        "description": "Xcode para el diagnóstico focal", "type": "choice", "required": True,
        "default": "predeterminado", "options": ["predeterminado", "27.2"]}
    job = media_workflow["jobs"]["media"]
    assert job["runs-on"] == (
        "${{ inputs.runner == 'macos-26' && 'macos-26' || 'xcode-27' }}")
    assert job["timeout-minutes"] == 15
    steps = job["steps"]
    runner = next(step for step in steps if step.get("id") == "runner_selection")
    selection = next(step for step in steps if step.get("id") == "xcode_selection")
    environment = next(step for step in steps
                       if "check_macos_ci_environment.py" in step.get("run", ""))
    swift = next(step for step in steps if "test_resource_guard.py run" in step.get("run", ""))
    dependencies = next(step for step in steps if "uv sync" in step.get("run", ""))
    assert steps.index(runner) < steps.index(selection) < steps.index(dependencies)
    assert steps.index(dependencies) < steps.index(environment) < steps.index(swift)
    assert "if" not in runner and runner["shell"] == "bash"
    assert runner["env"] == {"JARVIS_CI_RUNNER_CHOICE": "${{ inputs.runner }}",
                             "JARVIS_CI_XCODE_CHOICE": "${{ inputs.xcode }}"}
    assert "inputs." not in runner["run"] and "self-hosted" not in runner["run"]
    assert "if" not in selection and selection["shell"] == "bash"
    assert selection["env"] == {"JARVIS_CI_XCODE_CHOICE": "${{ inputs.xcode }}"}
    assert "${{" not in selection["run"]
    for forbidden in ("sudo", "xcode-select", "install", "--skip-build"):
        assert forbidden not in selection["run"]
    assert environment["run"] == (
        '.venv/bin/python -B script/check_macos_ci_environment.py '
        '--minimum-major "$JARVIS_CI_MINIMUM_MAJOR" '
        '--output "${{ runner.temp }}/Jarvis-media-probe-environment.json"')
    assert "--selection 'NativeMedia(Executor|Diagnostics)Tests'" in swift["run"]
    assert "--filter 'NativeMedia(Executor|Diagnostics)Tests'" in swift["run"]
    assert "--profile ci --kind swift --full" in swift["run"]
    assert "--budget-mib 6144 --deadline 600" in swift["run"]
    assert "--skip-build" not in swift["run"] and "--native-media" not in swift["run"]


@pytest.mark.parametrize("runner,xcode,major", [
    ("xcode-27", "predeterminado", 27), ("xcode-27", "27.2", 27),
    ("macos-26", "predeterminado", 26), ("macos-26", "27.2", None),
    ("self-hosted", "predeterminado", None), ("", "predeterminado", None),
    ("macos-26/../../untrusted", "predeterminado", None),
    ("$(printf injected)", "predeterminado", None),
    ("xcode-27", "$(printf injected)", None),
])
def test_media_runner_selector_accepts_only_closed_official_combinations(
    tmp_path, media_workflow, runner, xcode, major,
):
    selection = next(step for step in media_workflow["jobs"]["media"]["steps"]
                     if step.get("id") == "runner_selection")
    github_env = tmp_path / "github environment"
    github_env.touch()
    empty_path = tmp_path / "empty-bin"
    empty_path.mkdir()
    result = subprocess.run(
        ["/bin/bash", "--noprofile", "--norc", "-e", "-o", "pipefail", "-c", selection["run"]],
        cwd=tmp_path, env={"PATH": str(empty_path), "GITHUB_ENV": str(github_env),
            "JARVIS_CI_RUNNER_CHOICE": runner, "JARVIS_CI_XCODE_CHOICE": xcode},
        capture_output=True, text=True, timeout=10, check=False,
    )
    assert result.returncode == (0 if major is not None else 2) and result.stdout == ""
    assert github_env.read_text() == (
        f"JARVIS_CI_MINIMUM_MAJOR={major}\n" if major is not None else "")
    assert (result.stderr == "") is (major is not None)
    assert set(tmp_path.iterdir()) == {github_env, empty_path}


@pytest.mark.parametrize("choice,present,status", [
    ("predeterminado", True, 0), ("27.2", True, 0), ("27.2", False, 2),
    ("", True, 2), ("28.0", True, 2),
    ("27.2/../../untrusted", True, 2), ("$(printf injected)", True, 2),
])
def test_media_xcode_selector_uses_only_fixed_path_or_fails_without_effects(
    tmp_path, media_workflow, choice, present, status,
):
    selection = next(step for step in media_workflow["jobs"]["media"]["steps"]
                     if step.get("id") == "xcode_selection")
    developer = "/Applications/Xcode_27.2.app/Contents/Developer"
    github_env = tmp_path / "github environment"
    github_env.touch()
    captured = tmp_path / "directory-arguments"
    empty_path = tmp_path / "empty-bin"
    empty_path.mkdir()
    # Stub only the directory predicate. The real YAML body runs unchanged;
    # this never creates or changes an installed Xcode or global preference.
    predicate = '''test() {
      printf '%s\\n' "$@" > "$JARVIS_CAPTURE_DIRECTORY_ARGS"
      [[ "$#" == 2 && "$1" == -d &&
         "$2" == /Applications/Xcode_27.2.app/Contents/Developer ]] || return 91
      return "$JARVIS_DIRECTORY_STATUS"
    }
    '''
    result = subprocess.run(
        ["/bin/bash", "--noprofile", "--norc", "-e", "-o", "pipefail", "-c",
         predicate + selection["run"]],
        cwd=tmp_path, env={"PATH": str(empty_path), "GITHUB_ENV": str(github_env),
            "JARVIS_CI_XCODE_CHOICE": choice, "JARVIS_CAPTURE_DIRECTORY_ARGS": str(captured),
            "JARVIS_DIRECTORY_STATUS": "0" if present else "1"},
        capture_output=True, text=True, timeout=10, check=False,
    )
    assert result.returncode == status and result.stdout == ""
    assert github_env.read_text() == (
        "DEVELOPER_DIR=" + developer + "\n" if choice == "27.2" and present else "")
    if choice == "27.2":
        assert captured.read_text().splitlines() == ["-d", developer]
    else:
        assert not captured.exists()
    assert (result.stderr == "") is (status == 0)


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
