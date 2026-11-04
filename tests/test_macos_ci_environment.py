"""Remote CI environment admission with synthetic responses and bounded children."""

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "jarvis_macos_ci_environment", ROOT / "script/check_macos_ci_environment.py",
)
assert SPEC is not None and SPEC.loader is not None
checker = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(checker)
COMMIT = "7" * 40


@pytest.fixture
def corpus(monkeypatch):
    monkeypatch.setattr(checker.sys, "platform", "darwin")
    environment = {"CI": "true", "GITHUB_ACTIONS": "true", "GITHUB_SHA": COMMIT,
                   "GITHUB_RUN_ID": "12345"}
    responses = {"os": "27.0.1", "architecture": "arm64", "sdk": "27.0",
                 "xcode": "Xcode 27.1\nBuild version 27A9275",
                 "swift": "Apple Swift version 6.4 (synthetic)\nTarget: arm64-apple-macosx27.0"}
    calls = []

    def run(arguments, *, timeout):
        assert timeout == 10
        calls.append(arguments)
        name = next(key for key, value in checker.COMMANDS.items() if value == arguments)
        return responses[name]

    return environment, responses, calls, run


@pytest.fixture
def corpus26(corpus):
    environment, responses, calls, run = corpus
    responses.update({"os": "26.7.2", "sdk": "26.5",
        "xcode": "Xcode 26.6\nBuild version 26G123",
        "swift": "Apple Swift version 6.3.2 (synthetic)\nTarget: arm64-apple-macosx26.0"})
    return environment, responses, calls, run


def test_records_actual_environment_without_claiming_full_qualification(corpus, tmp_path):
    environment, _responses, calls, run = corpus
    receipt = checker.collect(environment, command_runner=run)
    output = tmp_path / "environment.json"
    logged = checker.write_receipt(output, receipt)
    assert json.loads(output.read_bytes()) == json.loads(logged) == receipt
    assert receipt["source_commit"] == COMMIT and receipt["run_id"] == "12345"
    assert receipt["os_version"] == "27.0.1" and receipt["sdk_version"] == "27.0"
    assert receipt["xcode_version"] == "27.1" and receipt["swift_version"] == "6.4"
    assert receipt["architecture"] == "arm64"
    assert receipt["full_workflow_success_claimed"] is False
    assert calls == list(checker.COMMANDS.values())
    assert output.stat().st_size < checker.RECEIPT_LIMIT


def test_explicit_26_minimum_preserves_v1_receipt_and_actual_versions(corpus26):
    environment, _responses, calls, run = corpus26
    receipt = checker.collect(environment, command_runner=run, minimum_major=26)
    assert receipt == {"format": "jarvis-ci-macos-environment/1", "source_commit": COMMIT,
        "run_id": "12345", "os_version": "26.7.2", "architecture": "arm64",
        "sdk_version": "26.5", "xcode_version": "26.6", "xcode_build": "26G123",
        "swift_version": "6.3.2", "swift_target": "arm64-apple-macosx26.0",
        "full_workflow_success_claimed": False}
    assert calls == list(checker.COMMANDS.values())


def test_default_27_minimum_still_rejects_26_environment(corpus26):
    environment, _responses, _calls, run = corpus26
    with pytest.raises(checker.EnvironmentCheckError, match="macos_27_required"):
        checker.collect(environment, command_runner=run)


@pytest.mark.parametrize("field,value,code", [
    ("os", "25.9.2", "macos_26_required"), ("sdk", "25.5", "sdk_26_required"),
])
def test_26_minimum_rejects_older_actual_os_or_sdk(corpus26, field, value, code):
    environment, responses, _calls, run = corpus26
    responses[field] = value
    with pytest.raises(checker.EnvironmentCheckError, match=code):
        checker.collect(environment, command_runner=run, minimum_major=26)


@pytest.mark.parametrize("minimum", [None, True, False, 26.0, "26", 25, 28])
def test_minimum_major_is_a_closed_integer_choice_before_commands(corpus, minimum):
    environment, _responses, calls, run = corpus
    with pytest.raises(checker.EnvironmentCheckError, match="minimum_major_not_admitted"):
        checker.collect(environment, command_runner=run, minimum_major=minimum)
    assert calls == []


@pytest.mark.parametrize(("field", "value"), [
    ("CI", "false"), ("GITHUB_ACTIONS", "false"), ("GITHUB_SHA", "not-a-commit"),
    ("GITHUB_RUN_ID", ""),
])
def test_rejects_non_ci_or_invalid_identity_before_commands(corpus, field, value):
    environment, _responses, calls, run = corpus
    environment[field] = value
    with pytest.raises(checker.EnvironmentCheckError):
        checker.collect(environment, command_runner=run)
    assert calls == []


def test_rejects_non_macos_before_commands(corpus, monkeypatch):
    environment, _responses, calls, run = corpus
    monkeypatch.setattr(checker.sys, "platform", "linux")
    with pytest.raises(checker.EnvironmentCheckError):
        checker.collect(environment, command_runner=run)
    assert calls == []


@pytest.mark.parametrize(("field", "value"), [
    ("os", "26.7.2"), ("sdk", "26.5"), ("architecture", "x86_64"),
    ("os", "27.untrusted"), ("sdk", "27"), ("xcode", "private synthetic marker"),
    ("swift", "Swift version unknown"), ("sdk", "x" * 4097),
])
def test_rejects_older_or_malformed_actual_environment(corpus, field, value):
    environment, responses, _calls, run = corpus
    responses[field] = value
    with pytest.raises(checker.EnvironmentCheckError):
        checker.collect(environment, command_runner=run)


@pytest.mark.parametrize(("program", "code"), [
    ("import sys; sys.stdout.write('x' * 8192); sys.stdout.flush()", "command_output_limit"),
    ("import time; time.sleep(10)", "command_timeout"),
    ("import sys; sys.stderr.write('private synthetic marker'); sys.exit(9)", "command_failed"),
])
def test_command_bounds_and_cleanup_use_only_synthetic_children(monkeypatch, program, code):
    command = (sys.executable, "-c", program)
    monkeypatch.setattr(checker, "COMMANDS", {"synthetic": command})
    original_popen, children = subprocess.Popen, []

    def spawn(*args, **kwargs):
        children.append(original_popen(*args, **kwargs))
        return children[-1]

    monkeypatch.setattr(checker.subprocess, "Popen", spawn)
    with pytest.raises(checker.EnvironmentCheckError, match=code):
        checker.capture(command, timeout=.5 if code == "command_timeout" else 5)
    assert children and all(child.poll() is not None for child in children)


def test_receipt_never_overwrites_existing_output_or_exceeds_limit(tmp_path):
    output = tmp_path / "environment.json"
    output.write_text("preserve this original")
    with pytest.raises(FileExistsError):
        checker.write_receipt(output, {"format": checker.FORMAT})
    assert output.read_text() == "preserve this original"
    oversized = tmp_path / "oversized.json"
    with pytest.raises(checker.EnvironmentCheckError):
        checker.write_receipt(oversized, {"synthetic": "x" * checker.RECEIPT_LIMIT})
    assert not oversized.exists()


@pytest.mark.parametrize("failure", [OSError("private synthetic marker"),
                                    subprocess.TimeoutExpired("private synthetic marker", 1)])
def test_cli_sanitizes_failures_without_publishing_receipt(monkeypatch, tmp_path, capsys, failure):
    def reject(**_kwargs):
        raise failure

    monkeypatch.setattr(checker, "collect", reject)
    output = tmp_path / "environment.json"
    assert checker.main(["--output", str(output)]) == 2
    captured = capsys.readouterr()
    assert captured.out == "" and "private synthetic marker" not in captured.err
    rejection = json.loads(captured.err)
    assert rejection["error_class"] == type(failure).__name__
    assert "error_code" not in rejection
    assert not output.exists()


def test_cli_reports_controlled_admission_code(corpus, monkeypatch, tmp_path, capsys):
    environment, responses, _calls, run = corpus
    responses["architecture"] = "x86_64"
    original_collect = checker.collect
    monkeypatch.setattr(checker, "collect", lambda **kwargs: original_collect(
        environment, command_runner=run, **kwargs))
    output = tmp_path / "environment.json"
    assert checker.main(["--output", str(output)]) == 2
    rejection = json.loads(capsys.readouterr().err)
    assert rejection["error_class"] == "EnvironmentCheckError"
    assert rejection["error_code"] == "apple_silicon_required"
    assert not output.exists()


def test_cli_forwards_explicit_minimum_without_rewriting_actual_versions(
    corpus26, monkeypatch, tmp_path, capsys,
):
    environment, _responses, _calls, run = corpus26
    original_collect, selected = checker.collect, []

    def collect(*, minimum_major):
        selected.append(minimum_major)
        return original_collect(environment, command_runner=run, minimum_major=minimum_major)

    monkeypatch.setattr(checker, "collect", collect)
    output = tmp_path / "environment.json"
    assert checker.main(["--minimum-major", "26", "--output", str(output)]) == 0
    receipt = json.loads(capsys.readouterr().out)
    assert selected == [26]
    assert receipt["os_version"] == "26.7.2" and receipt["sdk_version"] == "26.5"
    assert json.loads(output.read_text()) == receipt


@pytest.mark.parametrize("minimum", ["25", "28", "26.5", "$(printf injected)"])
def test_cli_rejects_other_minimums_before_collecting(monkeypatch, tmp_path, minimum):
    monkeypatch.setattr(checker, "collect",
                        lambda **_kwargs: pytest.fail("collected invalid major"))
    output = tmp_path / "environment.json"
    with pytest.raises(SystemExit) as failure:
        checker.main(["--minimum-major", minimum, "--output", str(output)])
    assert failure.value.code == 2 and not output.exists()


def test_workflow_uses_one_standard_arm_pool_and_preserves_full_qualification():
    workflow = ROOT / ".github/workflows/ci.yml"
    if not workflow.is_file():
        pytest.skip("CI workflow belongs to the source checkout, not the runtime archive")
    definition = yaml.safe_load(workflow.read_text())
    assert definition["concurrency"]["cancel-in-progress"] is True
    assert definition["concurrency"]["group"] == "ci-jarvis-v3-${{ github.ref }}"
    job = definition["jobs"]["python"]
    assert job["strategy"]["matrix"]["os"] == ["ubuntu-24.04", "xcode-27"]
    steps = job["steps"]
    environment = next(step for step in steps
                       if "check_macos_ci_environment.py" in step.get("run", ""))
    build = next(step for step in steps if "--self-contained" in step.get("run", ""))
    assert steps.index(environment) < steps.index(build)
    assert environment["if"] == "runner.os == 'macOS'"
    assert "--budget-mib 8192" in build["run"] and "--deadline 1200" in build["run"]
    swift = next(step for step in steps if step.get("id") == "native_contracts")
    assert "--full --budget-mib 6144" in swift["run"]
    contracts = next(step for step in steps if step.get("id") == "contracts")
    assert "--full" in contracts["run"] and "--budget-mib 12288" in contracts["run"]
    package = next(step for step in steps if step.get("id") == "python_package")
    assert "--sdist" in package["run"] and "--wheel" in package["run"]
    assert any("--smoke" in step.get("run", "") for step in steps)
    receipt = next(step for step in steps if step.get("with", {}).get("name")
                   == "jarvis-ci-environment-${{ github.sha }}")
    assert receipt["with"]["retention-days"] == 14
    assert receipt["with"]["if-no-files-found"] == "error"
    assert receipt["with"]["path"] == "${{ runner.temp }}/Jarvis-ci-environment.json"
