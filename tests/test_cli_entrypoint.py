from __future__ import annotations

from pathlib import Path

import pytest

from aegis_core.cli_entrypoint import run


class RecordingHandlers:
    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple[object, ...], dict[str, object]]] = []

    async def engineering_cli(self, **kwargs: object) -> int:
        self.calls.append(("engineering_cli", (), kwargs))
        return 11

    def doctor(self) -> int:
        self.calls.append(("doctor", (), {}))
        return 12

    def plugins_import_credential(
        self,
        plugin_id: str,
        connector_id: str,
        source: Path,
    ) -> int:
        self.calls.append(("plugins_import_credential", (plugin_id, connector_id, source), {}))
        return 13

    async def daemon_soak(self, **kwargs: object) -> int:
        self.calls.append(("daemon_soak", (), kwargs))
        return 14

    async def production_workflows(self) -> int:
        self.calls.append(("production_workflows", (), {}))
        return 15

    async def long_horizon_reliability(self) -> int:
        self.calls.append(("long_horizon_reliability", (), {}))
        return 16

    async def application_qualification(self) -> int:
        self.calls.append(("application_qualification", (), {}))
        return 17

    async def browser_driver_qualification(self) -> int:
        self.calls.append(("browser_driver_qualification", (), {}))
        return 18

    async def voice_qualification(self) -> int:
        self.calls.append(("voice_qualification", (), {}))
        return 19

    async def pilot_release_qualification(self) -> int:
        self.calls.append(("pilot_release_qualification", (), {}))
        return 20

    async def distribution_release_qualification(self) -> int:
        self.calls.append(("distribution_release_qualification", (), {}))
        return 21

    async def secure_update_qualification(self) -> int:
        self.calls.append(("secure_update_qualification", (), {}))
        return 22

    async def secure_update_recover(self) -> int:
        self.calls.append(("secure_update_recover", (), {}))
        return 23

    def secure_update_verify(self, *args: object) -> int:
        self.calls.append(("secure_update_verify", args, {}))
        return 24

    async def secure_update_install(self, *args: object) -> int:
        self.calls.append(("secure_update_install", args, {}))
        return 25


def test_default_command_routes_to_engineering_session() -> None:
    handlers = RecordingHandlers()

    result = run([], handlers=handlers)

    assert result == 11
    name, arguments, keyword_arguments = handlers.calls.pop()
    assert name == "engineering_cli"
    assert arguments == ()
    assert keyword_arguments["workspace"] is None
    assert keyword_arguments["request"] is None
    assert keyword_arguments["domain"].value == "auto"
    assert keyword_arguments["research_policy"].value == "offline"
    assert keyword_arguments["inference_policy"].value == "hybrid"


def test_sync_command_uses_the_registered_handler() -> None:
    handlers = RecordingHandlers()

    assert run(["doctor"], handlers=handlers) == 12
    assert handlers.calls == [("doctor", (), {})]


def test_production_workflow_command_uses_the_async_handler() -> None:
    handlers = RecordingHandlers()

    assert run(["production-workflows"], handlers=handlers) == 15
    assert handlers.calls == [("production_workflows", (), {})]


def test_long_horizon_command_uses_the_async_handler() -> None:
    handlers = RecordingHandlers()

    assert run(["long-horizon-reliability"], handlers=handlers) == 16
    assert handlers.calls == [("long_horizon_reliability", (), {})]


def test_application_qualification_command_uses_the_async_handler() -> None:
    handlers = RecordingHandlers()

    assert run(["application-qualification"], handlers=handlers) == 17
    assert handlers.calls == [("application_qualification", (), {})]


def test_browser_driver_qualification_command_uses_the_async_handler() -> None:
    handlers = RecordingHandlers()

    assert run(["browser-driver-qualification"], handlers=handlers) == 18
    assert handlers.calls == [("browser_driver_qualification", (), {})]


def test_voice_qualification_command_uses_the_async_handler() -> None:
    handlers = RecordingHandlers()

    assert run(["voice-qualification"], handlers=handlers) == 19
    assert handlers.calls == [("voice_qualification", (), {})]


def test_pilot_release_qualification_command_uses_the_async_handler() -> None:
    handlers = RecordingHandlers()

    assert run(["pilot-release-qualification"], handlers=handlers) == 20
    assert handlers.calls == [("pilot_release_qualification", (), {})]


def test_distribution_release_qualification_command_uses_the_async_handler() -> None:
    handlers = RecordingHandlers()

    assert run(["distribution-release-qualification"], handlers=handlers) == 21
    assert handlers.calls == [("distribution_release_qualification", (), {})]


def test_secure_update_qualification_command_uses_the_async_handler() -> None:
    handlers = RecordingHandlers()

    assert run(["secure-update-qualification"], handlers=handlers) == 22
    assert handlers.calls == [("secure_update_qualification", (), {})]


def test_secure_update_commands_bind_every_evidence_path(tmp_path: Path) -> None:
    handlers = RecordingHandlers()
    manifest = tmp_path / "Jarvis.update.json"
    archive = tmp_path / "Jarvis.zip"
    release = tmp_path / "Jarvis.release.json"
    public = tmp_path / "JarvisUpdatePublicKey.ed25519"

    assert run(
        [
            "secure-update-verify",
            str(manifest),
            "--archive",
            str(archive),
            "--release-manifest",
            str(release),
            "--public-key",
            str(public),
        ],
        handlers=handlers,
    ) == 24
    assert handlers.calls.pop() == (
        "secure_update_verify",
        (manifest, archive, release, public),
        {},
    )
    assert run(
        [
            "secure-update-install",
            str(manifest),
            "--archive",
            str(archive),
            "--release-manifest",
            str(release),
            "--public-key",
            str(public),
            "--confirm-install",
            NEW_REVISION := "b" * 40,
        ],
        handlers=handlers,
    ) == 25
    assert handlers.calls == [
        (
            "secure_update_install",
            (manifest, archive, release, public, NEW_REVISION),
            {},
        )
    ]


def test_plugin_connector_is_split_once_and_preserves_path(tmp_path: Path) -> None:
    handlers = RecordingHandlers()
    source = tmp_path / "credential.txt"

    result = run(
        [
            "plugins-credential-import",
            str(source),
            "--connector",
            "mail.oauth.primary",
        ],
        handlers=handlers,
    )

    assert result == 13
    assert handlers.calls == [
        (
            "plugins_import_credential",
            ("mail", "oauth.primary", source),
            {},
        )
    ]


def test_daemon_soak_configuration_is_parsed_at_the_boundary() -> None:
    handlers = RecordingHandlers()

    result = run(
        ["daemon-soak"],
        handlers=handlers,
        environment={
            "AEGIS_SOAK_CYCLES": "12",
            "AEGIS_SOAK_MAX_P95_MS": "40.5",
            "AEGIS_SOAK_MAX_RSS_GROWTH_MB": "3.5",
        },
    )

    assert result == 14
    assert handlers.calls == [
        (
            "daemon_soak",
            (),
            {
                "cycles": 12,
                "max_p95_ms": 40.5,
                "max_rss_growth_bytes": 3_670_016,
            },
        )
    ]


def test_resource_command_rejects_missing_argument() -> None:
    with pytest.raises(SystemExit) as failure:
        run(["skills-learn"], handlers=RecordingHandlers())

    assert failure.value.code == 2
