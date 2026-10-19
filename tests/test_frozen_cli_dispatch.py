from aegis_core import cli_entrypoint
from aegis_core.runtime import frozen_entrypoint


def test_frozen_cli_dispatches_arguments_without_starting_a_daemon(monkeypatch):
    received = []

    def run(arguments):
        received.append(tuple(arguments))
        return 7

    monkeypatch.setattr(cli_entrypoint, "run", run)
    assert frozen_entrypoint.main(("--cli", "engineer", "--domain", "backend")) == 7
    assert received == [("engineer", "--domain", "backend")]


def test_frozen_runtime_keeps_rejecting_unprefixed_cli_commands(capsys):
    assert frozen_entrypoint.main(("engineer",)) == 2
    assert "unsupported_frozen_daemon_argument" in capsys.readouterr().out
