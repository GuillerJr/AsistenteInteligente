"""All transport, prompt, identity and text-UI evidence here is synthetic."""

import asyncio
import json
import stat
from dataclasses import replace
from pathlib import Path

import pytest

from jarvis_core.client_identity import ClientIdentity
from jarvis_core.product_client import (
    ClientSession,
    ProductClient,
    ProductClientError,
    ProductClientLimits,
    bounded_json,
    create_identity,
    decode_json,
    execute_cli,
    parser,
    run_tui,
)

SESSION = "a" * 32
RUN = "b" * 32
WORKSPACE = "c" * 32


class Writer:
    def __init__(self):
        self.frames = []

    def write(self, data):
        self.frames.append(data)

    async def drain(self):
        pass


class Wire:
    def __init__(self):
        self.calls = []
        self.cancelled = False
        self.pending_after_cancel = 1
        self.now = 0

    async def sleep(self, amount):
        self.now += amount

    async def request(self, method, params):
        self.calls.append((method, params))
        if method == "service.local_status":
            return {"profile_id": "synthetic", "generation": 1}
        if method == "product.initialize":
            return {"workspaces": [{"workspace_id": WORKSPACE, "path": "/fixture/project"}]}
        if method == "sessions.create":
            return {"session": {"session_id": SESSION, "temporary": params["temporary"]}}
        if method == "runs.start":
            return {"run_id": params["request_id"], "status": "queued"}
        if method == "events.poll":
            return {"events": [], "has_more": False}
        if method == "runs.cancel":
            self.cancelled = True
            return {"run_id": RUN, "accepted": True}
        if method == "runs.get":
            if self.cancelled and self.pending_after_cancel:
                self.pending_after_cancel -= 1
                return {"run": {"run_id": RUN, "status": "cleaning", "outcome": "pending"}}
            return {"run": {"run_id": RUN, "status": "cancelled" if self.cancelled else "completed",
                            "outcome": "unknown" if self.cancelled else "known",
                            "answer": None if self.cancelled else "Respuesta sintética"}}
        if method == "sessions.messages":
            return {"messages": [], "has_more": False}
        raise AssertionError(method)

    def client(self, **limits):
        return ProductClient(self.request, clock=lambda: self.now, sleep=self.sleep,
                             limits=replace(ProductClientLimits(), **limits))


def test_identity_creation_is_new_private_and_never_auto_admits(tmp_path):
    directory = tmp_path / "identity"
    public = create_identity(directory)
    assert len(public) == 64
    assert public == ClientIdentity.load(directory / "identity.key").public_key
    assert stat.S_IMODE(directory.stat().st_mode) == 0o700
    assert stat.S_IMODE((directory / "identity.key").stat().st_mode) == 0o600
    assert list(directory.iterdir()) == [directory / "identity.key"]
    with pytest.raises(ProductClientError, match="identity_creation_failed"):
        create_identity(directory)


async def test_connect_uses_only_explicit_identity_no_fallback(tmp_path, monkeypatch):
    directory = tmp_path / "selected"
    create_identity(directory)
    key_path = directory / "identity.key"
    observed = []

    class Transport:
        def __init__(self, state, *, identity, timeout, max_frame_bytes):
            observed.append((state, identity.public_key, timeout, max_frame_bytes))

        async def connect(self):
            return self

        async def call(self, method, params):
            return {"synthetic": True}

        async def aclose(self):
            pass

    def forbidden(*_args, **_kwargs):
        raise AssertionError("implicit identity fallback")

    monkeypatch.setattr("jarvis_core.product_client.LocalRpcClient", Transport)
    monkeypatch.setattr(ClientIdentity, "for_profile", forbidden)
    client = await ProductClient.connect(Path("/fixture/profile"), key_path)
    assert observed[0][0] == Path("/fixture/profile")
    assert observed[0][1] == ClientIdentity.load(key_path).public_key
    await client.close()
    with pytest.raises(ProductClientError, match="explicit_paths_required"):
        await ProductClient.connect(Path("relative"), key_path)


async def test_prompt_preserves_exact_text_scope_and_request_identity_without_retry():
    wire = Wire()
    client = wire.client()
    message = "  texto\n`$(no_ejecutar)` \x1b[31m  "
    started = await client.start_prompt(ClientSession(SESSION, True), message, request_id=RUN)
    assert started["run_id"] == RUN
    assert wire.calls == [("runs.start", {"session_id": SESSION, "temporary": True,
                                         "message": message, "request_id": RUN})]


async def test_lost_mutation_ack_is_unknown_and_exact_request_is_not_resent():
    calls = []

    async def request(method, params):
        calls.append((method, params))
        raise ConnectionResetError("SYNTHETIC_PRIVATE_INPUT_MUST_NOT_LEAK")

    client = ProductClient(request)
    for _ in range(2):
        with pytest.raises(ProductClientError, match="mutation_unconfirmed") as captured:
            await client.start_prompt(ClientSession(SESSION), "synthetic prompt", request_id=RUN)
        assert "SYNTHETIC_PRIVATE_INPUT_MUST_NOT_LEAK" not in str(captured.value)
        assert captured.value.receipt()["outcome"] == "unknown"
    assert len(calls) == 1


async def test_cancel_ack_is_not_success_and_waits_for_real_terminal_receipt():
    wire = Wire()
    receipt = await wire.client().cancel_run(ClientSession(SESSION), RUN)
    assert receipt["cancel_accepted"] is True and receipt["settled"] is True
    assert receipt["cancellation_confirmed"] is False and receipt["effects_known"] is False
    assert receipt["outcome"] == "unknown"
    assert [name for name, _ in wire.calls].count("runs.get") == 2
    assert [name for name, _ in wire.calls].count("runs.cancel") == 1


async def test_cleanup_deadline_preserves_uncertainty_instead_of_succeeding():
    wire = Wire()
    wire.pending_after_cancel = 10000
    with pytest.raises(ProductClientError, match="run_deadline") as captured:
        await wire.client(cleanup_deadline=0.2).cancel_run(ClientSession(SESSION), RUN)
    assert captured.value.unknown is True
    assert [name for name, _ in wire.calls].count("runs.cancel") == 1


def test_frames_reject_duplicates_nonfinite_depth_and_escape_terminal_controls():
    for data in [b'{"x":1,"x":2}', b'{"x":NaN}', b'{"x":1e999}']:
        with pytest.raises(ProductClientError):
            decode_json(data)
    with pytest.raises(ProductClientError, match="frame_limit"):
        decode_json(b" " * 1024, maximum=128)
    value = None
    for _ in range(25):
        value = [value]
    with pytest.raises(ProductClientError, match="frame_limit"):
        bounded_json(value)
    output = bounded_json({"answer": "\x1b]52;c;YQ==\x07\u202e"})
    assert b"\x1b" not in output and b"\x07" not in output
    assert json.loads(output)["answer"].startswith("\x1b")


async def test_history_must_match_exact_scope_and_complete_load_cannot_truncate():
    async def request(_method, params):
        return {"messages": [{"session_id": SESSION, "sequence": params["after"] + 1,
                              "role": "user", "text": "Sintético"}], "has_more": True}

    client = ProductClient(request, limits=replace(ProductClientLimits(), max_pages=1))
    with pytest.raises(ProductClientError, match="history_limit"):
        await client.messages(ClientSession(SESSION), complete=True)
    with pytest.raises(ProductClientError, match="response_invalid"):
        await client.messages(ClientSession("f" * 32))


async def test_tui_is_finite_explicit_and_never_executes_or_echoes_unknown_commands():
    wire, writer = Wire(), Writer()
    reader = asyncio.StreamReader()
    reader.feed_data(b"status\nnew Chat de prueba\nprompt $(touch marker)\nrm -rf privado\nquit\n")
    reader.feed_eof()
    await run_tui(wire.client(), reader, writer)
    start = next(params for method, params in wire.calls if method == "runs.start")
    assert start["message"] == "$(touch marker)"
    frames = [json.loads(row) for row in writer.frames]
    assert frames[-1]["error"] == "client_command_unavailable"
    assert all("rm -rf privado" not in row.decode() for row in writer.frames)
    assert not any(name in {"approvals.decide", "process.spawn"} for name, _ in wire.calls)


async def test_cli_prompt_requires_explicit_selected_session_and_does_not_rewrite_message():
    wire = Wire()
    args = parser().parse_args(["--temporary", "prompt", "--session-id", SESSION,
        "--message", "  texto exacto  ", "--request-id", RUN, "--no-wait"])
    result = await execute_cli(wire.client(), args)
    assert result["run_id"] == RUN
    assert wire.calls[0][1]["message"] == "  texto exacto  "
    assert wire.calls[0][1]["temporary"] is True


async def test_client_exposes_no_approval_mutation_route():
    wire = Wire()
    with pytest.raises(ProductClientError, match="method_unavailable"):
        await wire.client()._call("approvals.decide", {"allow": True})
    assert wire.calls == []
