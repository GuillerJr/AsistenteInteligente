"""Owned PTYs and real Seatbelt workloads, never an owner's Terminal window."""
import asyncio
import json
import socket
import sys
from pathlib import Path
from uuid import uuid4

import pytest

from jarvis_core.process_pty import PTYChannel
from jarvis_core.processes import ProcessError, ProcessTools, _mac_profile
from jarvis_core.tools import ApprovalAuthority, ToolCatalog
from jarvis_core.workspace import WorkspaceTools


@pytest.fixture
async def terminal(tmp_path):
    root = tmp_path.resolve() / "workspace"
    root.mkdir(mode=0o700)
    owner = ["pty-chat"]
    manager = ProcessTools(WorkspaceTools(root), lambda: owner[0], max_wall_seconds=5)
    authority = ApprovalAuthority(ttl=4)
    catalog = ToolCatalog(manager.definitions(), manager.specs(), authority=authority)
    try:
        yield manager, authority, catalog, root, owner
    finally:
        await manager.aclose()
        await authority.close()


def require_pty(terminal):
    if not terminal[0].status()["pty_available"]:
        pytest.skip("PTY productivo se cualifica en macOS con Seatbelt; no hay fallback.")


async def invoke(terminal, name, arguments, *, allow=True, cancellation=None):
    _manager, authority, catalog, _root, owner = terminal
    task = asyncio.create_task(catalog.execute(name, arguments, session_id=owner[0],
                                              run_id=uuid4().hex, cancellation=cancellation))
    if name != "process_poll":
        async with asyncio.timeout(3):
            while not task.done() and not authority.list_pending():
                await asyncio.sleep(0)
            if authority.list_pending():
                authority.decide(authority.list_pending()[0].approval_id, allow)
    return await asyncio.wait_for(task, 10)


async def wait_for(terminal, identity, predicate):
    manager, _, _, _, owner = terminal
    async with asyncio.timeout(7):
        while True:
            result = manager.poll(owner[0], identity)
            if predicate(result):
                return result
            await asyncio.sleep(0.005)


async def start(terminal, source, **options):
    require_pty(terminal)
    result = await invoke(terminal, "process_run", {
        "argv": [str(Path(sys.executable).resolve()), "-I", "-u", "-c", source],
        "pty": True, "wait_ms": 20, **options,
    })
    assert result.succeeded, result
    return json.loads(result.output)["process_id"]


async def test_actual_controlling_terminal_has_exact_size_and_single_process_sandbox(terminal):
    identity = await start(terminal,
        "import os,sys; print('PTY',sys.stdin.isatty(),sys.stdout.isatty(),"
        "os.tcgetpgrp(0)==os.getpgrp(),os.get_terminal_size(0),flush=True)", rows=33, cols=99)
    result = await wait_for(terminal, identity, lambda row: not row["running"])
    assert result["exit_code"] == 0 and "PTY True True True" in result["output"]
    assert "columns=99, lines=33" in result["output"]
    assert result["terminal"]["controlling_terminal"] is True
    assert result["terminal"]["stderr"] == "merged_with_stdout"
    assert result["effects_independently_verified"] is False
    assert not terminal[0]._calls


async def test_python_repl_accepts_input_then_exits_without_terminal_app(terminal):
    require_pty(terminal)
    reply = await invoke(terminal, "process_run", {
        "argv": [str(Path(sys.executable).resolve()), "-I", "-q"], "pty": True, "wait_ms": 20,
    })
    assert reply.succeeded
    identity = json.loads(reply.output)["process_id"]
    await wait_for(terminal, identity, lambda row: ">>> " in row["output"])
    sent = await invoke(terminal, "process_send", {"process_id": identity,
                                                 "text": "print(7000 + 319)\n"})
    assert sent.succeeded and not sent.verified
    await wait_for(terminal, identity, lambda row: "\r\n7319\r\n" in row["output"])
    await invoke(terminal, "process_send", {"process_id": identity, "text": "raise SystemExit\n"})
    result = await wait_for(terminal, identity, lambda row: not row["running"])
    assert result["exit_code"] == 0 and terminal[0]._entries[identity].terminal.master == -1


async def test_resize_is_approved_scoped_and_read_back_by_workload(terminal):
    identity = await start(terminal, "import os,sys; print('READY',flush=True); "
                           "sys.stdin.readline(); print(os.get_terminal_size(0),flush=True)")
    await wait_for(terminal, identity, lambda row: "READY" in row["output"])
    denied = await invoke(terminal, "process_resize", {"process_id": identity, "rows": 40,
                                                     "cols": 100}, allow=False)
    assert denied.error == "approval_denied"
    assert terminal[0].poll(terminal[4][0], identity)["terminal"]["rows"] == 24
    terminal[4][0] = "another-chat"
    other = await invoke(terminal, "process_resize", {"process_id": identity, "rows": 40,
                                                     "cols": 100})
    assert other.error == "process_not_found"
    terminal[4][0] = "pty-chat"
    resized = await invoke(terminal, "process_resize", {"process_id": identity, "rows": 40,
                                                       "cols": 100})
    assert resized.succeeded
    await invoke(terminal, "process_send", {"process_id": identity, "text": "go\n"})
    result = await wait_for(terminal, identity, lambda row: not row["running"])
    assert "columns=100, lines=40" in result["output"]


async def test_canonical_eof_delivers_partial_line_and_closes_logical_input(terminal):
    require_pty(terminal)
    reply = await invoke(terminal, "process_run", {"argv": ["/bin/cat"], "pty": True,
                                                  "wait_ms": 10})
    identity = json.loads(reply.output)["process_id"]
    result = await invoke(terminal, "process_send", {"process_id": identity,
                         "text": "PTY EOF 7319", "close_stdin": True})
    assert result.succeeded
    done = await wait_for(terminal, identity, lambda row: not row["running"])
    assert done["exit_code"] == 0 and "PTY EOF 7319" in done["output"]
    again = await invoke(terminal, "process_send", {"process_id": identity, "text": "NO"})
    assert again.error == "process_stdin_closed"


async def test_raw_terminal_rejects_eof_before_writing_any_payload(terminal):
    identity = await start(terminal, "import sys,tty; tty.setraw(0); print('READY',flush=True); "
                           "print('RAWREAD:'+sys.stdin.read(1),flush=True)")
    await wait_for(terminal, identity, lambda row: "READY" in row["output"])
    refused = await invoke(terminal, "process_send", {"process_id": identity,
                            "text": "NO", "close_stdin": True})
    assert refused.error == "process_pty_eof_requires_canonical_mode"
    assert refused.effect_status == "known"
    await invoke(terminal, "process_send", {"process_id": identity, "text": "x"})
    done = await wait_for(terminal, identity, lambda row: not row["running"])
    assert "RAWREAD:x" in done["output"] and "NO" not in done["output"]


@pytest.mark.parametrize("kind,native", [("interrupt", "SIGINT"), ("terminate", "SIGTERM")])
async def test_signal_is_delivered_only_to_the_owned_workload(terminal, kind, native):
    identity = await start(terminal,
        f"import signal; signal.signal(signal.{native},"
        f"lambda *_:print('HANDLED_{native}',flush=True)); "
        "print('READY',flush=True); signal.pause()")
    await wait_for(terminal, identity, lambda row: "READY" in row["output"])
    denied = await invoke(terminal, "process_signal", {"process_id": identity,
                                                      "signal": kind}, allow=False)
    assert denied.error == "approval_denied"
    sent = await invoke(terminal, "process_signal", {"process_id": identity,
                                                    "signal": kind})
    assert sent.succeeded and not sent.verified
    done = await wait_for(terminal, identity, lambda row: not row["running"])
    assert f"HANDLED_{native}" in done["output"] and done["exit_code"] == 0


async def test_pty_does_not_relax_network_fork_or_filesystem_confinement(terminal, tmp_path):
    outside = tmp_path.resolve() / "synthetic-outside.txt"
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        listener.listen(1)
        port = listener.getsockname()[1]
        source = (
            "import os,socket\n"
            "for name,action in [('FILE',lambda:open(" + repr(str(outside)) + ", 'w')),"
            f"('NET',lambda:socket.create_connection(('127.0.0.1',{port}),timeout=.2)),"
            "('FORK',lambda:os.fork())]:\n"
            " try: action(); print(name+':ALLOWED',flush=True)\n"
            " except PermissionError: print(name+':DENIED',flush=True)\n"
        )
        identity = await start(terminal, source)
        done = await wait_for(terminal, identity, lambda row: not row["running"])
    assert done["exit_code"] == 0, done
    assert all(f"{name}:DENIED" in done["output"] for name in ["FILE", "NET", "FORK"]), done
    assert not outside.exists()


async def test_cancel_and_output_limit_keep_owned_cleanup(terminal):
    identity = await start(terminal, "import time; print('READY',flush=True); time.sleep(30)")
    await wait_for(terminal, identity, lambda row: "READY" in row["output"])
    stopped = await invoke(terminal, "process_stop", {"process_id": identity})
    assert stopped.succeeded
    assert terminal[0]._entries[identity].terminal.master == -1
    overflow_reply = await invoke(terminal, "process_run", {
        "argv": [str(Path(sys.executable).resolve()), "-I", "-c",
                 "import os; os.write(1,b'x'*1000000)"], "pty": True, "wait_ms": 20,
    })
    overflow = json.loads(overflow_reply.output)["process_id"]
    done = await wait_for(terminal, overflow, lambda row: not row["running"])
    assert done["termination_reason"] == "output_limit" and done["output_truncated"]
    assert done["retained_bytes"] <= terminal[0].max_output_bytes


async def test_failed_spawn_releases_both_owned_pty_descriptors(terminal, monkeypatch):
    require_pty(terminal)
    channels = []
    original = PTYChannel.__init__
    def create(self, *args):
        original(self, *args)
        channels.append(self)
    async def fail(*_args, **_kwargs):
        raise OSError("synthetic spawn fault")
    monkeypatch.setattr(PTYChannel, "__init__", create)
    monkeypatch.setattr(asyncio, "create_subprocess_exec", fail)
    reply = await invoke(terminal, "process_run", {"argv": ["/bin/cat"], "pty": True})
    assert not reply.succeeded and len(channels) == 1
    assert channels[0].master == channels[0].slave == -1
    assert not terminal[0]._entries and not terminal[0]._calls


async def test_no_pty_fallback_on_other_backend(terminal):
    terminal[0]._backend = "unsupported"
    reply = await invoke(terminal, "process_run", {"argv": ["/bin/cat"], "pty": True})
    assert reply.error == "process_pty_unavailable" and not terminal[0]._entries


@pytest.mark.parametrize("options", [{"pty": "yes"}, {"rows": 10}, {"pty": True, "cols": False},
                                     {"pty": True, "rows": 0}, {"pty": True, "cols": 501}])
def test_invalid_terminal_configuration_is_not_coerced(options):
    with pytest.raises(ProcessError):
        ProcessTools._validate("process_run", {"argv": ["/bin/cat"], **options})


def test_sandbox_opens_only_this_pty_and_keeps_fork_denied():
    profile = _mac_profile(Path("/private/tmp/own-test"), Path("/bin/cat"), "/dev/ttys7319")
    assert '(literal "/dev/ttys7319")' in profile and '(deny process-fork)' in profile
    assert '(regex #"^/dev/ttys")' not in profile
    with pytest.raises(ProcessError):
        _mac_profile(Path("/private/tmp/own-test"), Path("/bin/cat"), "/dev/ttys../personal")


async def test_cancelling_start_waits_for_owned_pty_cleanup(terminal):
    require_pty(terminal)
    manager, authority, catalog, _, owner = terminal
    cancellation = asyncio.Event()
    task = asyncio.create_task(catalog.execute("process_run", {
        "argv": [str(Path(sys.executable).resolve()), "-I", "-c",
                 "import time; print('WAITING',flush=True); time.sleep(30)"],
        "pty": True, "wait_ms": 10000,
    }, session_id=owner[0], run_id=uuid4().hex, cancellation=cancellation))
    async with asyncio.timeout(3):
        while not authority.list_pending():
            await asyncio.sleep(0)
        authority.decide(authority.list_pending()[0].approval_id, True)
        while not manager._entries:
            await asyncio.sleep(0)
        entry = next(iter(manager._entries.values()))
        cancellation.set()
        with pytest.raises(asyncio.CancelledError):
            await task
    assert entry.finished.is_set() and entry.terminal.master == -1
    assert entry.process.returncode is not None and not manager._calls


async def test_revocation_after_one_stdin_byte_stops_uncertain_pty(terminal, monkeypatch):
    require_pty(terminal)
    import jarvis_core.process_pty as channel_module
    identity = await start(terminal, "import sys; print('READY',flush=True); sys.stdin.readline()")
    await wait_for(terminal, identity, lambda row: "READY" in row["output"])
    entry = terminal[0]._entries[identity]
    original = channel_module.os.write
    delivered = []
    def partial(fd, payload):
        if fd != entry.terminal.master:
            return original(fd, payload)
        count = original(fd, payload[:1])
        delivered.append(bytes(payload[:1]))
        terminal[1].revoke(terminal[4][0])
        return count
    monkeypatch.setattr(channel_module.os, "write", partial)
    result = await invoke(terminal, "process_send", {"process_id": identity, "text": "NEVER\n"})
    assert not result.succeeded and result.effect_status == "unknown"
    assert delivered == [b"N"]
    assert entry.finished.is_set() and entry.terminal.master == -1


async def test_pty_bootstrap_preserves_profile_lease_descriptor_until_exit(terminal, tmp_path):
    require_pty(terminal)
    import fcntl
    import os
    lease = tmp_path.resolve() / "synthetic-lease"
    fd = os.open(lease, os.O_RDWR | os.O_CREAT | os.O_EXCL, 0o600)
    inode = os.fstat(fd).st_ino
    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    terminal[0].child_custody = lambda: (fd,)
    held = True
    try:
        identity = await start(terminal, f"import os,sys; print('CUSTODY',os.fstat({fd}).st_ino,"
                               "flush=True); sys.stdin.readline()")
        await wait_for(terminal, identity, lambda row: f"CUSTODY {inode}" in row["output"])
        os.close(fd)
        held = False
        probe = os.open(lease, os.O_RDWR)
        try:
            with pytest.raises(BlockingIOError):
                fcntl.flock(probe, fcntl.LOCK_EX | fcntl.LOCK_NB)
            await invoke(terminal, "process_send", {"process_id": identity, "text": "done\n"})
            await wait_for(terminal, identity, lambda row: not row["running"])
            fcntl.flock(probe, fcntl.LOCK_EX | fcntl.LOCK_NB)
        finally:
            os.close(probe)
    finally:
        if held:
            os.close(fd)
        terminal[0].child_custody = None
