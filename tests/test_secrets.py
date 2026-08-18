import subprocess
from pathlib import Path

import pytest

from aegis_core.secrets import (
    InvalidSecretError,
    MacOSKeychain,
    import_nvidia_key_from_file,
)


def test_keychain_rejects_non_nvidia_secret(monkeypatch: pytest.MonkeyPatch) -> None:
    called = False

    def fake_run(*args: object, **kwargs: object) -> subprocess.CompletedProcess[str]:
        nonlocal called
        called = True
        return subprocess.CompletedProcess([], 0, "", "")

    monkeypatch.setattr(subprocess, "run", fake_run)
    keychain = MacOSKeychain(service="test", account="test")
    with pytest.raises(InvalidSecretError):
        keychain.set("not-a-key")
    assert called is False


def test_keychain_stores_secret_without_shell(monkeypatch: pytest.MonkeyPatch) -> None:
    captured_command: list[str] = []

    def fake_run(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        captured_command.extend(command)
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(subprocess, "run", fake_run)
    keychain = MacOSKeychain(service="test-service", account="test-account")
    secret = "nvapi-" + "a" * 40
    keychain.set(secret)

    assert captured_command[0] == "/usr/bin/security"
    assert "-U" in captured_command
    assert captured_command[-1] == secret


def test_file_import_requires_owner_only_permissions(tmp_path: Path) -> None:
    source = tmp_path / "key"
    source.write_text("nvapi-" + "a" * 40, encoding="utf-8")
    source.chmod(0o644)

    with pytest.raises(InvalidSecretError):
        import_nvidia_key_from_file(
            MacOSKeychain(service="test-service", account="test-account"), source
        )


def test_file_import_destroys_source(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    source = tmp_path / "key"
    secret = "nvapi-" + "a" * 40
    source.write_text(secret, encoding="utf-8")
    source.chmod(0o600)
    captured: list[str] = []

    monkeypatch.setattr(MacOSKeychain, "set", lambda self, value: captured.append(value))
    import_nvidia_key_from_file(
        MacOSKeychain(service="test-service", account="test-account"), source
    )

    assert captured == [secret]
    assert source.exists() is False
