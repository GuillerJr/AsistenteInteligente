from __future__ import annotations

import asyncio
from collections import deque
from datetime import UTC, datetime, timedelta
from io import StringIO
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest

from aegis_core.engineering_cli import EngineeringCLI, EngineeringCLIError, read_piped_request
from aegis_core.engineering_input import PrivateHistory
from aegis_core.engineering_terminal import EngineeringTerminal, safe_terminal_text
from aegis_core.ipc.protocol import ProtocolError
from aegis_core.job_contracts import JobSnapshot, JobStatus, PendingToolConfirmation


def snapshot(**changes: object) -> JobSnapshot:
    now = datetime.now(UTC)
    fields = dict(
        job_id=uuid4(),
        request_id=uuid4(),
        status=JobStatus.RUNNING,
        created_at=now,
        updated_at=now,
    )
    fields.update(changes)
    return JobSnapshot.model_validate(fields)


def changed(job: JobSnapshot, **changes: object) -> JobSnapshot:
    return JobSnapshot.model_validate({**job.model_dump(), **changes})


class ScriptedPeer:
    """Deterministic peer responses; real signed socket coverage is in the PTY test."""

    def __init__(self, workspace: Path, *responses: JobSnapshot | Exception) -> None:
        self.workspace = workspace
        self.responses = deque(responses)
        self.calls: list[tuple[str, dict]] = []
        self.last: JobSnapshot | None = None
        self.cancel_failure = False
        self.waiting: asyncio.Event | None = None

    async def call(self, method: str, payload: dict) -> SimpleNamespace:
        self.calls.append((method, payload))
        if method == "engineering.preflight":
            if payload.get("workspace_path", str(self.workspace)) != str(self.workspace):
                return SimpleNamespace(
                    ok=False, payload={}, error_code="invalid_engineering_request"
                )
            return SimpleNamespace(ok=True, payload={"workspace_path": str(self.workspace)})
        if method == "jobs.cancel":
            if self.cancel_failure:
                raise TimeoutError("private cancellation detail")
            assert self.last is not None
            response = changed(self.last, status=JobStatus.CANCELLED, confirmation=None)
        else:
            if method == "jobs.wait" and self.waiting is not None:
                self.waiting.set()
                await asyncio.Event().wait()
            response = self.responses.popleft()
        if isinstance(response, Exception):
            raise response
        self.last = response
        return SimpleNamespace(ok=True, payload=response.model_dump(mode="json"), error_code=None)


def session(
    peer: ScriptedPeer, *, input_text: str = ""
) -> tuple[EngineeringCLI, StringIO, StringIO]:
    output, errors = StringIO(), StringIO()
    cli = EngineeringCLI(
        peer, workspace=peer.workspace, stdin=StringIO(input_text), stdout=output, stderr=errors
    )
    return cli, output, errors


@pytest.mark.asyncio
async def test_streaming_preserves_prefix_when_final_snapshot_omits_partial(tmp_path: Path) -> None:
    first = snapshot(partial_result="uno ", stream_version=1)
    peer = ScriptedPeer(
        tmp_path,
        first,
        changed(first, partial_result="uno dos", stream_version=2),
        changed(
            first,
            status=JobStatus.COMPLETED,
            result="uno dos",
            partial_result=None,
            stream_version=3,
        ),
    )
    cli, output, errors = session(peer)

    assert await cli.run_once("consulta") == 0
    assert output.getvalue() == "uno dos\n"
    assert "Origen: origen no informado" in errors.getvalue()
    assert "no validada técnicamente" in errors.getvalue()
    assert cli._job is None


@pytest.mark.asyncio
async def test_local_context_limit_does_not_end_the_session(tmp_path: Path) -> None:
    peer = ScriptedPeer(
        tmp_path,
        snapshot(status=JobStatus.FAILED, error_code="model_context_limit"),
        snapshot(status=JobStatus.COMPLETED, result="Siguiente respuesta"),
    )
    cli, output, errors = session(peer, input_text="muy largo\n/new\nhola\n/exit\n")
    assert await cli.run_interactive() == 0
    assert "capacidad del modelo local" in errors.getvalue()
    assert output.getvalue() == "Siguiente respuesta\n"
    assert cli._job is None


@pytest.mark.asyncio
async def test_relative_workspace_uses_current_project_not_shell_directory(tmp_path: Path) -> None:
    child = tmp_path / "sub proyecto"
    child.mkdir()
    peer = ScriptedPeer(child)
    cli, _, _ = session(peer)
    cli._workspace = tmp_path
    await cli._handle_command('/workspace "sub proyecto"')
    assert cli._workspace == child
    assert peer.calls[-1][1] == {"workspace_path": str(child)}


@pytest.mark.asyncio
async def test_missing_workspace_is_not_reported_as_broken_ipc(tmp_path: Path) -> None:
    cli, _, errors = session(ScriptedPeer(tmp_path))
    assert await cli._guarded(cli._handle_command('/workspace "missing"')) == 1
    assert cli._workspace == tmp_path
    assert "workspace_unavailable" in errors.getvalue()
    assert "ipc_unavailable" not in errors.getvalue()


@pytest.mark.asyncio
async def test_invalid_utf8_is_an_input_error(tmp_path: Path) -> None:
    path = tmp_path / "invalid.txt"
    path.write_bytes(b"\xff")
    with path.open(encoding="utf-8") as stream:
        with pytest.raises(EngineeringCLIError, match="invalid_input_encoding"):
            await read_piped_request(stream)


@pytest.mark.asyncio
async def test_interactive_transport_failure_does_not_resubmit_and_can_resume(
    tmp_path: Path,
) -> None:
    first = snapshot(partial_result="prefijo ", stream_version=1)
    complete = changed(first, status=JobStatus.COMPLETED, result="prefijo final", stream_version=2)
    peer = ScriptedPeer(tmp_path, first, ConnectionResetError("private provider detail"), complete)
    cli, output, errors = session(peer, input_text="consulta\nsegunda\n/resume\n/exit\n")

    assert await cli.run_interactive() == 0
    assert [method for method, _ in peer.calls].count("engineering.submit") == 1
    assert "jobs.status" in [method for method, _ in peer.calls]
    assert output.getvalue() == "prefijo final\n"
    assert "Hay una tarea sin resolver" in errors.getvalue()
    assert "private provider detail" not in errors.getvalue()


@pytest.mark.asyncio
async def test_interactive_can_continue_after_rejected_submission(tmp_path: Path) -> None:
    peer = ScriptedPeer(
        tmp_path, OSError("private path"), snapshot(status=JobStatus.COMPLETED, result="listo")
    )
    cli, output, errors = session(peer, input_text="primera\nsegunda\n/exit\n")
    assert await cli.run_interactive() == 0
    assert output.getvalue() == "listo\n"
    assert "private path" not in errors.getvalue()
    assert "No reenvié" in errors.getvalue()


@pytest.mark.asyncio
@pytest.mark.parametrize("mismatch", ["job_id", "request_id", "conversation_id", "version"])
async def test_stream_rejects_identity_or_version_changes(tmp_path: Path, mismatch: str) -> None:
    first = snapshot(stream_version=2)
    changes = {"stream_version": 1} if mismatch == "version" else {mismatch: uuid4()}
    peer = ScriptedPeer(tmp_path, first, changed(first, **changes))
    cli, output, errors = session(peer)
    assert await cli.run_once("consulta") == 1
    assert output.getvalue() == ""
    assert "ipc_invalid_response" in errors.getvalue()
    assert cli._job == first


@pytest.mark.asyncio
async def test_confirmation_remains_observed_and_never_approves_from_cli(tmp_path: Path) -> None:
    pending = snapshot(
        status=JobStatus.AWAITING_CONFIRMATION,
        confirmation=PendingToolConfirmation(
            call_digest="a" * 64,
            tool_name="calendar_create",
            summary="Crear evento",
            expires_at=datetime.now(UTC) + timedelta(minutes=1),
        ),
    )
    complete = changed(pending, status=JobStatus.COMPLETED, result="verificado", confirmation=None)
    peer = ScriptedPeer(tmp_path, pending, pending, complete)
    cli, output, errors = session(peer, input_text="consulta\n/exit\n")
    assert await cli.run_interactive() == 0
    assert output.getvalue() == "verificado\n"
    assert errors.getvalue().count("AUTORIZACIÓN REQUERIDA") == 1
    assert "jobs.approve" not in [method for method, _ in peer.calls]


@pytest.mark.asyncio
async def test_confirmation_one_shot_returns_three_without_contaminating_stdout(
    tmp_path: Path,
) -> None:
    pending = snapshot(
        status=JobStatus.AWAITING_CONFIRMATION,
        confirmation=PendingToolConfirmation(
            call_digest="a" * 64,
            tool_name="calendar_create",
            summary="Evento",
            expires_at=datetime.now(UTC) + timedelta(minutes=1),
        ),
    )
    peer = ScriptedPeer(tmp_path, pending)
    cli, output, errors = session(peer)
    assert await cli.run_once("consulta") == 3
    assert output.getvalue() == ""
    assert str(pending.job_id) in errors.getvalue()


@pytest.mark.asyncio
@pytest.mark.parametrize("cancel_parent", [False, True])
async def test_interrupt_cancels_only_the_owned_job(tmp_path: Path, cancel_parent: bool) -> None:
    peer = ScriptedPeer(tmp_path, snapshot())
    peer.waiting = asyncio.Event()
    cli, output, errors = session(peer)
    task = asyncio.create_task(cli.run_once("consulta"))
    await asyncio.wait_for(peer.waiting.wait(), timeout=1)
    target = (
        task
        if cancel_parent
        else next(
            item for item in asyncio.all_tasks() if item.get_name() == "aegis-engineering-operation"
        )
    )
    target.cancel()
    if cancel_parent:
        with pytest.raises(asyncio.CancelledError):
            await task
    else:
        assert await task == 130
    calls = [payload for method, payload in peer.calls if method == "jobs.cancel"]
    assert calls == [{"job_id": str(peer.last.job_id)}]
    assert "Cancelación confirmada" in errors.getvalue()
    assert cli._job is None
    assert output.getvalue() == ""


@pytest.mark.asyncio
async def test_failed_cancellation_does_not_claim_success_or_drop_job(tmp_path: Path) -> None:
    peer = ScriptedPeer(tmp_path, snapshot(), TimeoutError("private timeout"))
    cli, _, errors = session(peer)
    assert await cli.run_once("consulta") == 1
    peer.cancel_failure = True
    assert await cli._cancel_current() == 1
    assert cli._job is not None
    assert "Cancelación confirmada" not in errors.getvalue()
    assert "podría seguir activa" in errors.getvalue()
    assert "private" not in errors.getvalue()


@pytest.mark.asyncio
async def test_eof_cancels_abandoned_job(tmp_path: Path) -> None:
    peer = ScriptedPeer(tmp_path, snapshot(), ProtocolError("private error"))
    cli, _, _ = session(peer, input_text="consulta\n")
    assert await cli.run_interactive() == 0
    assert peer.calls[-1][0] == "jobs.cancel"


@pytest.mark.parametrize(
    "command",
    [
        "/exit ignored",
        "/new ignored",
        "/status ignored",
        "/help ignored",
        "/clear ignored",
        "/domain",
        "/wat x",
    ],
)
def test_commands_reject_ignored_arguments(tmp_path: Path, command: str) -> None:
    cli, _, _ = session(ScriptedPeer(tmp_path))
    with pytest.raises(EngineeringCLIError, match="invalid_command"):
        cli._handle_local_command(command)


def test_changing_to_cloud_starts_new_context(tmp_path: Path) -> None:
    cli, _, _ = session(ScriptedPeer(tmp_path))
    assert cli._inference_policy.value == "nvidia_only"
    cli._handle_local_command("/inference local_only")
    cli._conversation_id = uuid4()
    cli._handle_local_command("/inference nvidia_only")
    assert cli._conversation_id is None
    current = cli._conversation_id = uuid4()
    cli._handle_local_command("/inference nvidia_only")
    assert cli._conversation_id == current


@pytest.mark.asyncio
async def test_workspace_rejection_preserves_context(tmp_path: Path) -> None:
    folder = tmp_path / "outside"
    folder.mkdir()
    cli, _, _ = session(ScriptedPeer(tmp_path))
    previous = cli._conversation_id = uuid4()
    assert await cli._guarded(cli._handle_command(f'/workspace "{folder}"')) == 1
    assert cli._workspace == tmp_path
    assert cli._conversation_id == previous


@pytest.mark.asyncio
@pytest.mark.parametrize("text", ["", " ", "x\0y", "x" * 50_001, "nvapi-" + "x" * 70])
async def test_invalid_or_secret_input_is_not_submitted(tmp_path: Path, text: str) -> None:
    peer = ScriptedPeer(tmp_path)
    cli, output, errors = session(peer)
    assert await cli.run_once(text) == 1
    assert all(method != "engineering.submit" for method, _ in peer.calls)
    assert output.getvalue() == ""
    assert errors.getvalue()


def test_terminal_output_cannot_execute_control_sequences() -> None:
    untrusted = "before\x1b]52;c;YQ==\a\x1b[2J\r\x9b31m\u202eevil\x08after\n"
    safe = safe_terminal_text(untrusted)
    assert not any(character in safe for character in "\x1b\a\r\x9b\u202e\x08")
    assert safe.endswith("after\n")


@pytest.mark.asyncio
async def test_stream_control_sequences_split_across_chunks_are_inert(tmp_path: Path) -> None:
    first = snapshot(partial_result="hola\x1b", stream_version=1)
    peer = ScriptedPeer(
        tmp_path,
        first,
        changed(
            first,
            status=JobStatus.COMPLETED,
            result="hola\x1b]52;c;secret\a",
            stream_version=2,
        ),
    )
    cli, output, _ = session(peer)
    assert await cli.run_once("consulta") == 0
    assert "\x1b" not in output.getvalue()
    assert "\a" not in output.getvalue()


def test_private_history_is_bounded_and_can_be_cleared() -> None:
    history = PrivateHistory()
    for index in range(110):
        history.append_string(f"entrada {index}")
    assert len(history.get_strings()) == 100
    for index in range(10):
        history.append_string(str(index) + "á" * 8_000)
    assert sum(len(item.encode()) for item in history.get_strings()) <= 65_536
    history.append_string("nvapi-" + "x" * 70)
    assert "nvapi-" not in " ".join(history.get_strings())
    history.clear()
    assert history.get_strings() == []


def test_narrow_plain_terminal_has_no_ansi_and_wraps_notices(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("COLUMNS", "30")
    monkeypatch.setenv("NO_COLOR", "1")
    output = StringIO()
    view = EngineeringTerminal(output, output, interactive=True)
    view.banner(Path("/tmp/proyecto"), "architecture", "offline", "local_only")
    assert all(len(line) <= 28 for line in output.getvalue().splitlines())
    assert "\x1b" not in output.getvalue()


@pytest.mark.asyncio
async def test_pipe_is_one_literal_multiline_request() -> None:
    text = "Revisa este código:\n/new\nprint('ejemplo')\n"
    assert await read_piped_request(StringIO(text)) == text
