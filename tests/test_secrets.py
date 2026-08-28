import subprocess
from pathlib import Path

import pytest

from aegis_core.secrets import (
    InvalidIpcSecretError,
    InvalidPluginSecretError,
    InvalidSecretError,
    MacOSIpcSecret,
    MacOSKeychain,
    MacOSPluginSecret,
    import_nvidia_key_from_file,
    import_plugin_secret_from_file,
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


def test_keychain_configuration_probe_never_reads_secret(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    observed: dict[str, object] = {}

    def fake_run(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[bytes]:
        observed["command"] = command
        observed.update(kwargs)
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.delenv("NVIDIA_API_KEY", raising=False)
    monkeypatch.setattr("aegis_core.secrets.platform.system", lambda: "Darwin")
    monkeypatch.setattr(subprocess, "run", fake_run)

    configured = MacOSKeychain(service="test-service", account="default").is_configured()

    assert configured is True
    assert observed["command"] == [
        "/usr/bin/security",
        "find-generic-password",
        "-a",
        "default",
        "-s",
        "test-service",
    ]
    assert "-w" not in observed["command"]
    assert observed["stdout"] is subprocess.DEVNULL
    assert observed["stderr"] is subprocess.DEVNULL


def test_invalid_environment_key_is_not_configured(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NVIDIA_API_KEY", "invalid")
    keychain = MacOSKeychain(service="test-service", account="default")

    assert keychain.is_configured() is False
    with pytest.raises(InvalidSecretError):
        keychain.get()


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


def test_ipc_secret_rejects_malformed_keychain_value(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda *args, **kwargs: subprocess.CompletedProcess([], 0, "not-hex\n", ""),
    )

    with pytest.raises(InvalidIpcSecretError):
        MacOSIpcSecret(service="test-ipc", account="default").get()


def test_ipc_secret_returns_existing_key_without_replacing_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[list[str]] = []
    secret = "ab" * 32

    def fake_run(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        calls.append(command)
        return subprocess.CompletedProcess(command, 0, secret + "\n", "")

    monkeypatch.setattr(subprocess, "run", fake_run)

    result = MacOSIpcSecret(service="test-ipc", account="default").get_or_create()

    assert result == secret
    assert len(calls) == 1
    assert "find-generic-password" in calls[0]


def test_ipc_secret_creation_race_uses_keychain_winner(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    existing = "cd" * 32
    results = [
        subprocess.CompletedProcess([], 44, "", "missing"),
        subprocess.CompletedProcess([], 45, "", "duplicate"),
        subprocess.CompletedProcess([], 0, existing + "\n", ""),
    ]

    def fake_run(*args: object, **kwargs: object) -> subprocess.CompletedProcess[str]:
        return results.pop(0)

    monkeypatch.setattr(subprocess, "run", fake_run)

    secret = MacOSIpcSecret(service="test-ipc", account="default").get_or_create()

    assert secret == existing
    assert results == []


def test_plugin_secret_scope_is_closed_before_keychain_access(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    called = False

    def fake_run(*args: object, **kwargs: object) -> subprocess.CompletedProcess[str]:
        nonlocal called
        called = True
        return subprocess.CompletedProcess([], 0, "", "")

    monkeypatch.setattr(subprocess, "run", fake_run)
    with pytest.raises(InvalidPluginSecretError):
        MacOSPluginSecret("../../ipc-auth", "connector")
    assert called is False


def test_plugin_secret_is_stored_in_derived_keychain_service(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    observed: list[str] = []

    def fake_run(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        del kwargs
        observed.extend(command)
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr("aegis_core.secrets.platform.system", lambda: "Darwin")
    monkeypatch.setattr(subprocess, "run", fake_run)
    MacOSPluginSecret("research-kit", "public-web").set("private-bearer-token")

    assert observed == [
        "/usr/bin/security",
        "add-generic-password",
        "-U",
        "-a",
        "default",
        "-s",
        "ai.jarvis.plugin.research-kit.public-web",
        "-w",
        "private-bearer-token",
    ]


def test_plugin_credential_import_destroys_owner_only_source(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    source = tmp_path / "plugin-token"
    source.write_text("private-bearer-token", encoding="utf-8")
    source.chmod(0o600)
    captured: list[str] = []
    monkeypatch.setattr(MacOSPluginSecret, "set", lambda self, value: captured.append(value))

    import_plugin_secret_from_file(
        MacOSPluginSecret("research-kit", "public-web"), source
    )

    assert captured == ["private-bearer-token"]
    assert source.exists() is False
