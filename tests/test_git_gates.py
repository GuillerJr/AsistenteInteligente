from __future__ import annotations

import shutil
import stat
import subprocess
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _run(command: tuple[str, ...], *, cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command,
        cwd=cwd,
        check=False,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        timeout=10,
    )


def _temporary_gate_repository(tmp_path: Path) -> Path:
    repository = tmp_path / "repository"
    (repository / "script").mkdir(parents=True)
    (repository / ".githooks").mkdir()
    setup = repository / "script/setup_git_gates.sh"
    hook = repository / ".githooks/pre-commit"
    shutil.copyfile(PROJECT_ROOT / "script/setup_git_gates.sh", setup)
    shutil.copyfile(PROJECT_ROOT / ".githooks/pre-commit", hook)
    setup.chmod(setup.stat().st_mode | stat.S_IXUSR)
    hook.chmod(hook.stat().st_mode | stat.S_IXUSR)
    initialized = _run(("/usr/bin/git", "init", "--quiet"), cwd=repository)
    assert initialized.returncode == 0, initialized.stderr
    return repository


def test_git_gate_reports_inactive_before_install_and_active_afterward(tmp_path: Path) -> None:
    repository = _temporary_gate_repository(tmp_path)
    script = repository / "script/setup_git_gates.sh"

    inactive = _run((str(script), "--check"), cwd=repository)
    assert inactive.returncode == 2
    assert inactive.stdout.strip() == "status=inactive hooks_path=unset"

    installed = _run((str(script), "--install"), cwd=repository)
    assert installed.returncode == 0, installed.stderr
    assert installed.stdout.strip() == "status=active hooks_path=.githooks"

    active = _run((str(script), "--check"), cwd=repository)
    assert active.returncode == 0, active.stderr
    assert active.stdout.strip() == "status=active hooks_path=.githooks"


def test_git_gate_rejects_unknown_operations(tmp_path: Path) -> None:
    repository = _temporary_gate_repository(tmp_path)
    script = repository / "script/setup_git_gates.sh"

    result = _run((str(script), "--force"), cwd=repository)

    assert result.returncode == 64
    assert "Usage:" in result.stderr
