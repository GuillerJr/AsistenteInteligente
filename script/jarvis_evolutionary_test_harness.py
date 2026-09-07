#!/usr/bin/env python3
from __future__ import annotations

import asyncio
import json
import os
import re
import subprocess
import sys
from pathlib import Path

_OWNER_LABEL = re.compile(r"^[a-z0-9][a-z0-9_-]{1,31}$")
_TARGETED_TESTS = (
    "tests/test_acceptance_benchmark.py",
    "tests/test_active_vision.py",
    "tests/test_chrome_cdp.py",
    "tests/test_evolutionary_runtime.py",
    "tests/test_agent_graph.py",
    "tests/test_direct_actions.py",
    "tests/test_macos_qualification.py",
    "tests/test_production_workflows.py",
)


class HarnessFailure(RuntimeError):
    pass


def project_root() -> Path:
    resolved = Path(__file__).resolve()
    candidate = resolved.parents[1]
    if (candidate / "pyproject.toml").is_file() and (candidate / "native/AegisAudio").is_dir():
        return candidate
    current = Path.cwd().resolve()
    if (current / "pyproject.toml").is_file() and (current / "native/AegisAudio").is_dir():
        return current
    raise HarnessFailure("project root is unavailable")


def run(
    command: tuple[str, ...],
    *,
    cwd: Path,
    timeout: int,
    accepted_codes: frozenset[int] = frozenset({0}),
) -> subprocess.CompletedProcess[str]:
    completed = subprocess.run(
        command,
        cwd=cwd,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
        env={
            **os.environ,
            "PYTHONPATH": str(cwd / "src"),
            "PYTHONDONTWRITEBYTECODE": "1",
        },
    )
    if completed.returncode not in accepted_codes:
        diagnostic = (completed.stderr or completed.stdout)[-4_096:]
        raise HarnessFailure(f"command failed ({completed.returncode}): {command[0]}\n{diagnostic}")
    return completed


def sole_owner(enrollment_directory: Path) -> str:
    owners = sorted(
        path.name
        for path in enrollment_directory.iterdir()
        if path.is_dir()
        and not path.is_symlink()
        and path.name != "background"
        and _OWNER_LABEL.fullmatch(path.name) is not None
    )
    if len(owners) != 1:
        raise HarnessFailure("exactly one enrolled owner is required")
    return owners[0]


def parse_calibration(output: str) -> dict[str, object]:
    try:
        report = json.loads(output)
    except json.JSONDecodeError as error:
        raise HarnessFailure("calibration output is malformed") from error
    required = {
        "accepted",
        "distractorCount",
        "maximumDistractorConfidence",
        "minimumOwnerConfidence",
        "ownerCount",
    }
    if not isinstance(report, dict) or not required.issubset(report):
        raise HarnessFailure("calibration report is incomplete")
    return report


async def main() -> int:
    root = project_root()
    project_python = root / ".venv/bin/python"
    if (
        project_python.is_file()
        and Path(sys.executable).resolve() != project_python.resolve()
        and os.environ.get("AEGIS_HARNESS_REEXEC") != "1"
    ):
        environment = {**os.environ, "AEGIS_HARNESS_REEXEC": "1"}
        os.execve(
            project_python,
            (str(project_python), str(Path(__file__).resolve())),
            environment,
        )
    sys.path.insert(0, str(root / "src"))
    from aegis_core.biometric_training_service import (
        AdversarialDistractorGenerator,
        BiometricTrainingService,
    )

    app_support = Path.home() / "Library/Application Support/Aegis"
    distractor_directory = app_support / "Biometrics/Training/Distractors"
    generated = await AdversarialDistractorGenerator(distractor_directory).generate()
    if len(generated) < 15:
        raise HarnessFailure("adversarial dataset is incomplete")

    python = root / ".venv/bin/python"
    if not python.is_file():
        python = Path(sys.executable)
    run(
        (str(python), "-m", "pytest", "-q", *_TARGETED_TESTS),
        cwd=root,
        timeout=300,
    )
    acceptance = run(
        (str(python), "-m", "aegis_core.cli", "acceptance-benchmark"),
        cwd=root,
        timeout=120,
    )
    try:
        acceptance_report = json.loads(acceptance.stdout)
    except json.JSONDecodeError as error:
        raise HarnessFailure("acceptance benchmark output is malformed") from error
    if (
        not isinstance(acceptance_report, dict)
        or acceptance_report.get("gate_passed") is not True
        or acceptance_report.get("score") != 100
        or acceptance_report.get("total") != 25
    ):
        raise HarnessFailure("acceptance benchmark did not pass")
    production = run(
        (str(python), "-m", "aegis_core.cli", "production-workflows"),
        cwd=root,
        timeout=120,
    )
    try:
        production_report = json.loads(production.stdout)
    except json.JSONDecodeError as error:
        raise HarnessFailure("production workflow output is malformed") from error
    if (
        not isinstance(production_report, dict)
        or production_report.get("gate_passed") is not True
        or production_report.get("score") != 100
        or production_report.get("total") != 20
        or production_report.get("privacy", {}).get("network_attempts") != 0
    ):
        raise HarnessFailure("production workflow gate did not pass")
    run((str(root / "script/test_native.sh"),), cwd=root, timeout=900)

    native_test_root = Path(
        os.environ.get("AEGIS_NATIVE_TEST_ROOT", "/private/tmp/aegis-menubar-build")
    )
    native_products = native_test_root / "out/Products/Debug"
    trainer = native_products / "jarvis-speaker-trainer"
    calibrator = native_products / "jarvis-biometric-calibrator"
    if not trainer.is_file() or not calibrator.is_file():
        raise HarnessFailure("native biometric helpers are unavailable")
    owner = sole_owner(app_support / "SpeakerEnrollment")
    calibration = run(
        (str(calibrator), owner),
        cwd=root,
        timeout=180,
        accepted_codes=frozenset({0, 2}),
    )
    report = parse_calibration(calibration.stdout)
    repaired = False
    if report.get("accepted") is not True:
        service = BiometricTrainingService(
            training_directory=app_support / "Biometrics/Training",
            enrollment_directory=app_support / "SpeakerEnrollment",
            active_model_path=app_support / "Models/JarvisSpeakerIdentity.mlmodelc",
            trainer_executable=trainer,
            calibrator_executable=calibrator,
            runtime_probe=lambda: None,
        )
        repaired = await service.harden_with_distractors()
        if not repaired:
            raise HarnessFailure("adversarial biometric retraining was rejected")
        calibration = run((str(calibrator), owner), cwd=root, timeout=180)
        report = parse_calibration(calibration.stdout)
    if report.get("accepted") is not True:
        raise HarnessFailure("speaker classifier remains unsafe")

    print(
        json.dumps(
            {
                "biometric_repaired": repaired,
                "acceptance_score": acceptance_report["score"],
                "distractors": len(generated),
                "maximum_distractor_confidence": report["maximumDistractorConfidence"],
                "minimum_owner_confidence": report["minimumOwnerConfidence"],
                "python_tests": "passed",
                "production_workflow_score": production_report["score"],
                "status": "ok",
                "swift_tests": "passed",
            },
            sort_keys=True,
            separators=(",", ":"),
        )
    )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(asyncio.run(main()))
    except (HarnessFailure, OSError, subprocess.SubprocessError) as error:
        print(
            json.dumps(
                {"error": type(error).__name__, "status": "failed"},
                sort_keys=True,
                separators=(",", ":"),
            ),
            file=sys.stderr,
        )
        raise SystemExit(1) from None
