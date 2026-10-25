"""Real disposable OS-sandbox processes; every approval is a synthetic test choice.

These fixtures never invoke a model, owner account, TCC, credential or personal
file. A system sandbox missing on a platform fails closed rather than downgrades.
"""

import asyncio
import hashlib
import json
import os
import socket
import stat
import sys
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest

from jarvis_core.processes import (
    ProcessError,
    ProcessTools,
    _framework_runtime_root,
    _mac_profile,
    _runtime_roots,
)
from jarvis_core.tools import ApprovalAuthority, ToolCancelled, ToolCatalog, ToolError
from jarvis_core.workspace import WorkspaceTools


@pytest.fixture
async def bundle(tmp_path):
    root = tmp_path.resolve() / "workspace"
    root.mkdir(mode=0o700)
    owner = ["synthetic-chat"]
    manager = ProcessTools(WorkspaceTools(root), lambda: owner[0], max_wall_seconds=5)
    authority = ApprovalAuthority(ttl=2)
    catalog = ToolCatalog(manager.definitions(), manager.specs(), authority=authority)
    try:
        yield manager, authority, catalog, root, owner
    finally:
        await manager.aclose()
        await authority.close()


async def approved(bundle, name, args, *, allow=True, cancellation=None):
    _manager, authority, catalog, _root, owner = bundle
    task = asyncio.create_task(
        catalog.execute(
            name, args, session_id=owner[0], run_id=uuid4().hex, cancellation=cancellation
        )
    )
    if name != "process_poll":
        async with asyncio.timeout(3):
            while not authority.list_pending():
                if task.done():
                    return task.result()
                await asyncio.sleep(0)
        pending = authority.list_pending()[0]
        assert pending.arguments_json == json.dumps(
            args, sort_keys=True, ensure_ascii=False, separators=(",", ":")
        )
        authority.decide(pending.approval_id, allow)
    return await asyncio.wait_for(task, 12)


def sandbox_required(bundle):
    manager = bundle[0]
    if not manager.status()["available"]:
        pytest.skip("No hay sandbox OS configurada en esta plataforma; no se ejecuta sin ella.")


async def settled(manager, owner, identity):
    async with asyncio.timeout(7):
        while True:
            value = manager.poll(owner, identity)
            if not value["running"]:
                return value
            await asyncio.sleep(0.005)


async def run_python(bundle, source, **options):
    return await approved(
        bundle,
        "process_run",
        {"argv": [str(Path(sys.executable).resolve()), "-c", source], "wait_ms": 1000, **options},
    )


def framework_host(monkeypatch, host):
    # Pure layout evidence must not depend on framework symlinks installed on
    # this test host. Production still resolves the physical sys.executable.
    real_resolve = Path.resolve

    def resolve(path, strict=False):
        return path if path == host else real_resolve(path, strict=strict)

    monkeypatch.setattr(sys, "platform", "darwin")
    monkeypatch.setattr(sys, "version_info", (3, 11))
    monkeypatch.setattr(sys, "executable", str(host))
    monkeypatch.setattr(Path, "resolve", resolve)


@pytest.mark.parametrize(
    "relative",
    [
        "bin/python",
        "bin/python3",
        "bin/python3.11",
        "Resources/Python.app/Contents/MacOS/Python",
    ],
)
def test_macos_host_framework_runtime_is_one_exact_version(monkeypatch, relative):
    root = Path("/Library/Frameworks/Python.framework/Versions/3.11")
    host = root / relative
    framework_host(monkeypatch, host)
    assert _framework_runtime_root(host) == root
    assert _runtime_roots(host) == (root,)
    profile = _mac_profile(Path("/private/tmp/synthetic-workspace"), host)
    assert f'(subpath "{root}")' in profile
    assert '(subpath "/Library/Frameworks")' not in profile
    assert '(subpath "/Library/Frameworks/Python.framework")' not in profile
    assert '(deny default)' in profile and '(deny process-fork)' in profile
    assert '(allow network' not in profile
    assert '(deny file-read* file-write* (regex ' in profile


@pytest.mark.parametrize(
    "executable",
    [
        "/Library/Frameworks/Python.framework/Versions/3.11/bin/pip3",
        "/Library/Frameworks/Python.framework/Versions/3.11/bin/unrelated",
        "/Library/Frameworks/Python.framework/Versions/3.12/bin/python3.12",
        "/Library/Frameworks/Python.framework/Versions/Current/bin/python3",
        "/Library/Frameworks/Unrelated.framework/Versions/3.11/bin/python3.11",
        "/Library/Frameworks/Python.framework/Versions/3.11/site-packages/python3.11",
    ],
)
def test_foreign_framework_executable_is_rejected_even_if_it_looks_like_python(
    monkeypatch, executable,
):
    framework_host(
        monkeypatch, Path("/Library/Frameworks/Python.framework/Versions/3.11/bin/python3.11")
    )
    assert _framework_runtime_root(Path(executable)) is None
    assert _runtime_roots(Path(executable)) == ()


def test_framework_named_non_python_host_does_not_grant_runtime_reading(monkeypatch):
    host = Path("/Library/Frameworks/Python.framework/Versions/3.11/bin/unrelated")
    framework_host(monkeypatch, host)
    assert _framework_runtime_root(host) is None and _runtime_roots(host) == ()
    monkeypatch.setattr(sys, "platform", "linux")
    host = Path("/Library/Frameworks/Python.framework/Versions/3.11/bin/python3.11")
    monkeypatch.setattr(sys, "executable", str(host))
    assert _framework_runtime_root(host) is None


def test_command_admits_only_host_framework_interpreter_without_spawning(monkeypatch):
    monkeypatch.setattr(sys, "platform", "darwin")
    monkeypatch.setattr(sys, "version_info", (3, 11))
    host = Path("/Library/Frameworks/Python.framework/Versions/3.11/bin/python3.11")
    foreign = host.with_name("unrelated")
    monkeypatch.setattr(sys, "executable", str(host))
    real_resolve, real_stat, real_access = Path.resolve, Path.stat, os.access

    def resolve(path, strict=False):
        return path if path in {host, foreign} else real_resolve(path, strict=strict)

    def status(path, *args, **kwargs):
        if path in {host, foreign}:
            return SimpleNamespace(st_mode=stat.S_IFREG | 0o755)
        return real_stat(path, *args, **kwargs)

    monkeypatch.setattr(Path, "resolve", resolve)
    monkeypatch.setattr(Path, "stat", status)
    def executable_access(path, mode):
        return True if path in {host, foreign} else real_access(path, mode)

    monkeypatch.setattr(os, "access", executable_access)
    manager = ProcessTools.__new__(ProcessTools)
    manager.workspace = SimpleNamespace(root=Path("/private/tmp/synthetic-workspace"))
    manager._backend, manager._helper = "macos_seatbelt", "/usr/bin/sandbox-exec"
    command = manager._command((str(host), "-c", "print('synthetic')"))
    assert command[-3:] == (str(host), "-c", "print('synthetic')")
    with pytest.raises(ProcessError, match="process_executable_outside_scope"):
        manager._command((str(foreign),))


def test_catalog_preserves_single_authority_and_explicit_sandbox(bundle):
    manager, _authority, catalog, _root, _owner = bundle
    definitions = {item.name: item for item in catalog.definitions()}
    assert set(definitions) == {"process_run", "process_poll", "process_send", "process_stop"}
    assert all(
        item.requires_confirmation for name, item in definitions.items() if name != "process_poll"
    )
    assert not definitions["process_poll"].requires_confirmation
    assert manager.status()["network"] == "denied"
    assert manager.status()["effects_independently_verified"] is False


@pytest.mark.parametrize(
    "args",
    [
        {"argv": []},
        {"argv": ["relative"]},
        {"argv": ["/bin/echo", "x\0y"]},
        {"argv": ["/bin/echo"] * 65},
        {"argv": ["/bin/echo", "x" * 8193]},
        {"argv": ["/bin/echo"], "wait_ms": True},
        {"argv": ["/bin/echo"], "wait_ms": 10001},
        {"argv": ["/bin/echo"], "timeout_seconds": float("nan")},
        {"argv": ["/bin/echo"], "timeout_seconds": True},
        {"argv": ["/bin/echo"], "cwd": "/outside"},
        {"argv": ["/bin/echo"], "env": {"SECRET": "never"}},
    ],
)
async def test_invalid_argv_scope_or_deadline_does_not_propose_or_spawn(bundle, args):
    result = await approved(bundle, "process_run", args)
    assert not result.succeeded and result.error == "invalid_tool_arguments"
    assert not bundle[0].has_running and not bundle[0].list_processes(bundle[4][0])


async def test_sandbox_unavailable_fails_closed_without_launch(bundle, monkeypatch):
    manager, _authority, _catalog, root, _owner = bundle
    monkeypatch.setattr(manager, "_helper", None)
    result = await approved(bundle, "process_run", {"argv": ["/usr/bin/touch", "forbidden.txt"]})
    assert not result.succeeded and result.error == "process_sandbox_unavailable"
    assert not (root / "forbidden.txt").exists() and not manager.has_running


async def test_unapproved_and_direct_calls_have_no_effect(bundle):
    sandbox_required(bundle)
    manager, _authority, _catalog, root, _owner = bundle
    args = {"argv": ["/usr/bin/touch", "unapproved.txt"]}
    result = await approved(bundle, "process_run", args, allow=False)
    assert result.error == "approval_denied" and not (root / "unapproved.txt").exists()
    definition = next(item for item in manager.definitions() if item.name == "process_run")
    with pytest.raises(ToolError):
        await definition.execute(args, asyncio.Event())
    assert not manager.has_running


async def test_zero_exit_is_process_success_but_never_effect_verification(bundle):
    sandbox_required(bundle)
    manager, _authority, _catalog, root, owner = bundle
    result = await approved(bundle, "process_run", {"argv": ["/usr/bin/touch", "created.txt"]})
    data = json.loads(result.output)
    final = await settled(manager, owner[0], data["process_id"])
    assert final["exit_code"] == 0 and final["state"] == "completed"
    assert (root / "created.txt").exists()
    assert result.succeeded and not result.verified and result.effect_status == "known"
    assert final["effects_independently_verified"] is False


async def test_exact_argv_does_not_become_shell_code_and_output_hash_is_lossless(bundle):
    sandbox_required(bundle)
    manager, _authority, _catalog, root, owner = bundle
    text = 'literal; touch injected.txt $(echo nope) "comillas"'
    result = await approved(bundle, "process_run", {"argv": ["/bin/echo", text]})
    data = await settled(manager, owner[0], json.loads(result.output)["process_id"])
    assert data["output"] == text + "\n"
    assert data["output_sha256"] == hashlib.sha256((text + "\n").encode()).hexdigest()
    assert not (root / "injected.txt").exists()


async def test_scope_does_not_allow_poll_send_or_kill_another_conversation(bundle):
    sandbox_required(bundle)
    manager, _authority, _catalog, _root, owner = bundle
    result = await approved(bundle, "process_run", {"argv": ["/bin/cat"], "wait_ms": 10})
    identity = json.loads(result.output)["process_id"]
    owner[0] = "other-synthetic-chat"
    assert manager.list_processes(owner[0]) == ()
    with pytest.raises(ProcessError, match="process_not_found"):
        manager.poll(owner[0], identity)
    for name, args in (
        ("process_poll", {"process_id": identity}),
        ("process_send", {"process_id": identity, "text": "no"}),
        ("process_stop", {"process_id": identity}),
    ):
        result = await approved(bundle, name, args)
        assert not result.succeeded and result.error == "process_not_found"
    assert manager.poll("synthetic-chat", identity)["running"]


async def test_interactive_stdin_then_eof_is_exact_and_not_replayed(bundle):
    sandbox_required(bundle)
    manager, _authority, _catalog, _root, owner = bundle
    result = await approved(bundle, "process_run", {"argv": ["/bin/cat"], "wait_ms": 10})
    identity = json.loads(result.output)["process_id"]
    sent = await approved(
        bundle, "process_send", {"process_id": identity, "text": "línea A\n", "close_stdin": True}
    )
    assert sent.succeeded and not sent.verified
    data = await settled(manager, owner[0], identity)
    assert data["output"] == "línea A\n" and data["exit_code"] == 0
    retry = await approved(bundle, "process_send", {"process_id": identity, "text": "línea B"})
    assert retry.error == "process_stdin_closed"
    assert manager.poll(owner[0], identity)["output"] == "línea A\n"


async def test_deny_stdin_and_stop_do_not_touch_a_running_process(bundle):
    sandbox_required(bundle)
    manager, _authority, _catalog, _root, owner = bundle
    result = await approved(bundle, "process_run", {"argv": ["/bin/cat"], "wait_ms": 10})
    identity = json.loads(result.output)["process_id"]
    for name, args in (
        ("process_send", {"process_id": identity, "text": "denied"}),
        ("process_stop", {"process_id": identity}),
    ):
        result = await approved(bundle, name, args, allow=False)
        assert result.error == "approval_denied"
    assert manager.poll(owner[0], identity)["running"]
    assert manager.poll(owner[0], identity)["output"] == ""
    stopped = await approved(bundle, "process_stop", {"process_id": identity})
    assert stopped.succeeded and not stopped.verified
    assert not manager.has_running and manager.poll(owner[0], identity)["state"] == "stopped"


async def test_outside_sentinel_and_symlink_targets_are_not_read(bundle, tmp_path):
    sandbox_required(bundle)
    manager, _authority, _catalog, root, owner = bundle
    outside = tmp_path.resolve() / "outside.txt"
    outside.write_text("SYNTHETIC-OUTSIDE-SECRET")
    result = await approved(bundle, "process_run", {"argv": ["/bin/cat", str(outside)]})
    data = await settled(manager, owner[0], json.loads(result.output)["process_id"])
    assert data["exit_code"] != 0 and "SYNTHETIC-OUTSIDE-SECRET" not in data["output"]
    (root / "linked.txt").symlink_to(outside)
    result = await approved(bundle, "process_run", {"argv": ["/bin/cat", "linked.txt"]})
    assert result.error == "process_workspace_unsafe_entry"


@pytest.mark.parametrize(
    "name",
    [
        "auth.json",
        "AUTH.JSON",
        ".env",
        ".env.test",
        "secret.key",
        ".ENV.TEST",
        ".JARVIS-TMP-fixture",
        "SECRET.KEY",
    ],
)
async def test_private_workspace_file_cannot_be_read_by_command(bundle, name):
    sandbox_required(bundle)
    manager, _authority, _catalog, root, owner = bundle
    (root / name).write_text("SYNTHETIC-PRIVATE-FIXTURE")
    result = await approved(bundle, "process_run", {"argv": ["/bin/cat", name]})
    if sys.platform.startswith("linux"):
        assert result.error == "process_workspace_private_entry"
        return
    data = await settled(manager, owner[0], json.loads(result.output)["process_id"])
    assert data["exit_code"] != 0 and "SYNTHETIC-PRIVATE-FIXTURE" not in data["output"]


async def test_existing_hardlink_and_fifo_are_rejected_without_spawning(bundle, tmp_path):
    sandbox_required(bundle)
    manager, _authority, _catalog, root, _owner = bundle
    outside = tmp_path.resolve() / "outside.txt"
    outside.write_text("synthetic")
    os.link(outside, root / "linked.txt")
    result = await approved(bundle, "process_run", {"argv": ["/bin/cat", "linked.txt"]})
    assert result.error == "process_workspace_unsafe_entry"
    (root / "linked.txt").unlink()
    os.mkfifo(root / "pipe")
    result = await approved(bundle, "process_run", {"argv": ["/bin/echo", "never"]})
    assert result.error == "process_workspace_unsafe_entry" and not manager.has_running


async def test_network_connection_is_denied_and_environment_is_not_inherited(bundle, monkeypatch):
    sandbox_required(bundle)
    monkeypatch.setenv("JARVIS_TEST_SECRET", "DO-NOT-INHERIT")
    manager, _authority, _catalog, _root, owner = bundle
    server = socket.socket()
    server.bind(("127.0.0.1", 0))
    server.listen(1)
    server.settimeout(0.1)
    try:
        result = await run_python(
            bundle,
            "import socket,os; print(os.environ.get('JARVIS_TEST_SECRET')); "
            f"socket.create_connection(('127.0.0.1',{server.getsockname()[1]}),1)",
        )
        data = await settled(manager, owner[0], json.loads(result.output)["process_id"])
        assert "DO-NOT-INHERIT" not in data["output"] and "None" in data["output"]
        assert data["exit_code"] != 0
        with pytest.raises(TimeoutError):
            server.accept()
    finally:
        server.close()


@pytest.mark.parametrize(
    "source",
    [
        "import os; os.fork()",
        "import os; os.posix_spawn('/usr/bin/touch',"
        "['/usr/bin/touch','escape.txt'],dict(os.environ))",
    ],
)
async def test_darwin_fork_cannot_create_an_escaped_child(bundle, source):
    if sys.platform != "darwin":
        pytest.skip("La política Seatbelt sin fork corresponde a macOS.")
    sandbox_required(bundle)
    manager, _authority, _catalog, _root, owner = bundle
    result = await run_python(bundle, source)
    data = await settled(manager, owner[0], json.loads(result.output)["process_id"])
    assert data["exit_code"] != 0 and "PermissionError" in data["output"]


async def test_nonzero_exit_is_a_reported_failure_with_output(bundle):
    sandbox_required(bundle)
    manager, _authority, _catalog, _root, owner = bundle
    result = await run_python(bundle, "import sys; print('diagnóstico'); sys.exit(7)")
    data = await settled(manager, owner[0], json.loads(result.output)["process_id"])
    assert data["exit_code"] == 7 and "diagnóstico" in data["output"]
    assert not result.succeeded and not result.verified and result.effect_status == "known"


async def test_output_limit_retains_finite_prefix_and_stops_child(bundle, monkeypatch):
    sandbox_required(bundle)
    manager, _authority, _catalog, _root, owner = bundle
    monkeypatch.setattr(manager, "max_output_bytes", 4096)
    result = await run_python(bundle, "import os; os.write(1,b'x'*1000000)")
    data = await settled(manager, owner[0], json.loads(result.output)["process_id"])
    assert data["state"] == "output_limit" and data["output_truncated"]
    assert data["retained_bytes"] == 4096 and data["output_bytes"] > 4096
    assert not result.succeeded and result.effect_status == "unknown"


async def test_poll_cursor_pages_bytes_without_returning_an_unbounded_transcript(bundle):
    sandbox_required(bundle)
    manager, _authority, _catalog, _root, owner = bundle
    result = await run_python(bundle, "import os; os.write(1,b'Z'*10000)")
    first = await settled(manager, owner[0], json.loads(result.output)["process_id"])
    assert len(first["output"]) == 4096 and first["has_more"]
    second = manager.poll(owner[0], first["process_id"], first["next_cursor"])
    third = manager.poll(owner[0], first["process_id"], second["next_cursor"])
    assert first["output"] + second["output"] + third["output"] == "Z" * 10000
    assert (
        not third["has_more"] and third["output_sha256"] == hashlib.sha256(b"Z" * 10000).hexdigest()
    )
    with pytest.raises(ProcessError):
        manager.poll(owner[0], first["process_id"], 10001)
    preview = manager.list_processes(owner[0])[0]
    assert len(preview["output"].encode()) <= 256 and preview["next_cursor"] == 256
    assert preview["has_more"] and preview["output_sha256"] == third["output_sha256"]


async def test_timeout_stops_process_but_does_not_certify_prior_effects(bundle, monkeypatch):
    sandbox_required(bundle)
    manager, _authority, _catalog, root, owner = bundle
    allow_expiry = asyncio.Event()
    real_deadline = manager._deadline

    async def synchronized_deadline(entry, seconds):
        assert seconds == 0.05
        # Control this fixture's deadline clock: startup speed cannot race the
        # independently observed effect. The real timeout/kill/reap path follows.
        await allow_expiry.wait()
        await real_deadline(entry, seconds)

    monkeypatch.setattr(manager, "_deadline", synchronized_deadline)
    running = asyncio.create_task(
        run_python(
            bundle,
            "from pathlib import Path; import time; "
            "Path('before-timeout.txt').write_text('effect'); time.sleep(20)",
            wait_ms=10000,
            timeout_seconds=0.05,
        )
    )
    try:
        # This is only a finite readiness guard, never the process deadline.
        async with asyncio.timeout(8):
            while True:
                effect = root / "before-timeout.txt"
                if effect.exists() and effect.read_text() == "effect":
                    break
                if running.done():
                    pytest.fail("The fixture ended before producing its expected effect")
                await asyncio.sleep(0.005)
        assert (root / "before-timeout.txt").read_text() == "effect"
        assert manager.has_running
        allow_expiry.set()
        result = await running
        data = await settled(manager, owner[0], json.loads(result.output)["process_id"])
        assert data["state"] == "timed_out" and not data["running"]
        assert (root / "before-timeout.txt").read_text() == "effect"
        assert not result.succeeded and not result.verified and result.effect_status == "unknown"
    finally:
        if not running.done():
            running.cancel()
        await asyncio.gather(running, return_exceptions=True)


async def test_close_scope_stops_owned_children_and_keeps_other_chat(bundle):
    sandbox_required(bundle)
    manager, _authority, _catalog, _root, owner = bundle
    result = await approved(bundle, "process_run", {"argv": ["/bin/cat"], "wait_ms": 10})
    first = json.loads(result.output)["process_id"]
    owner[0] = "other"
    result = await approved(bundle, "process_run", {"argv": ["/bin/cat"], "wait_ms": 10})
    second = json.loads(result.output)["process_id"]
    await manager.close_scope("synthetic-chat")
    assert manager.list_processes("synthetic-chat") == ()
    assert manager.poll("other", second)["running"]
    with pytest.raises(ProcessError):
        manager.poll("synthetic-chat", first)
    await manager.aclose()
    assert not manager.has_running and manager.list_processes("other") == ()
    await manager.aclose()


async def test_active_capacity_is_not_bypassed_by_concurrent_approved_runs(bundle, monkeypatch):
    sandbox_required(bundle)
    manager, _authority, _catalog, _root, _owner = bundle
    monkeypatch.setattr(manager, "max_processes", 1)
    first = await approved(bundle, "process_run", {"argv": ["/bin/cat"], "wait_ms": 10})
    assert first.succeeded
    second = await approved(bundle, "process_run", {"argv": ["/bin/echo", "not-started"]})
    assert second.error == "process_capacity"


async def test_workspace_retarget_after_construction_is_rejected(bundle):
    sandbox_required(bundle)
    _manager, _authority, _catalog, root, _owner = bundle
    previous = root.with_name("original")
    root.rename(previous)
    root.mkdir(mode=0o700)
    result = await approved(bundle, "process_run", {"argv": ["/usr/bin/touch", "never.txt"]})
    assert result.error == "process_workspace_changed"
    assert not (root / "never.txt").exists() and not (previous / "never.txt").exists()


async def test_running_cancellation_reaps_child_and_retains_uncertainty(bundle):
    sandbox_required(bundle)
    manager, authority, catalog, _root, owner = bundle
    cancelled = asyncio.Event()
    task = asyncio.create_task(
        catalog.execute(
            "process_run",
            {"argv": ["/bin/cat"], "wait_ms": 10000},
            session_id=owner[0],
            run_id=uuid4().hex,
            cancellation=cancelled,
        )
    )
    async with asyncio.timeout(3):
        while not authority.list_pending():
            await asyncio.sleep(0)
    authority.decide(authority.list_pending()[0].approval_id, True)
    async with asyncio.timeout(3):
        while not manager.list_processes(owner[0]):
            await asyncio.sleep(0)
    cancelled.set()
    with pytest.raises(ToolCancelled) as result:
        await task
    assert result.value.receipt.effect_status == "unknown" and not manager.has_running


async def test_manager_close_during_spawn_waits_and_reaps_its_owned_child(bundle, monkeypatch):
    sandbox_required(bundle)
    manager, authority, catalog, _root, owner = bundle
    original = asyncio.create_subprocess_exec
    spawned, release = asyncio.Event(), asyncio.Event()

    async def delayed_spawn(*args, **kwargs):
        process = await original(*args, **kwargs)
        spawned.set()
        await release.wait()
        return process

    monkeypatch.setattr(asyncio, "create_subprocess_exec", delayed_spawn)
    task = asyncio.create_task(
        catalog.execute(
            "process_run",
            {"argv": ["/bin/cat"], "wait_ms": 10000},
            session_id=owner[0],
            run_id=uuid4().hex,
        )
    )
    async with asyncio.timeout(3):
        while not authority.list_pending():
            await asyncio.sleep(0)
    authority.decide(authority.list_pending()[0].approval_id, True)
    await asyncio.wait_for(spawned.wait(), 3)
    closing = asyncio.create_task(manager.aclose())
    await asyncio.sleep(0)
    assert not closing.done()
    release.set()
    await asyncio.wait_for(closing, 5)
    await asyncio.gather(task, return_exceptions=True)
    assert not manager.has_running and manager.list_processes(owner[0]) == ()


async def test_signal_failure_preserves_live_owner_and_uncertain_stop_receipt(bundle, monkeypatch):
    sandbox_required(bundle)
    manager, _authority, _catalog, _root, owner = bundle
    result = await approved(bundle, "process_run", {"argv": ["/bin/cat"], "wait_ms": 10})
    identity = json.loads(result.output)["process_id"]
    original = manager._signal

    def denied(_entry):
        raise PermissionError("synthetic signal denial")

    monkeypatch.setattr(manager, "_signal", denied)
    stopped = await approved(bundle, "process_stop", {"process_id": identity})
    assert not stopped.succeeded and stopped.effect_status == "unknown"
    assert manager.has_running and manager.poll(owner[0], identity)["running"]
    monkeypatch.setattr(manager, "_signal", original)
    await manager.aclose()
    assert not manager.has_running


async def test_revocation_while_spawn_is_queued_prevents_first_effect(bundle, monkeypatch):
    sandbox_required(bundle)
    manager, authority, catalog, root, owner = bundle
    original = manager._spawn
    waiting, release = asyncio.Event(), asyncio.Event()

    async def paused(*args):
        waiting.set()
        await release.wait()
        return await original(*args)

    monkeypatch.setattr(manager, "_spawn", paused)
    task = asyncio.create_task(
        catalog.execute(
            "process_run",
            {"argv": ["/usr/bin/touch", "no.txt"]},
            session_id=owner[0],
            run_id=uuid4().hex,
        )
    )
    async with asyncio.timeout(3):
        while not authority.list_pending():
            await asyncio.sleep(0)
    authority.decide(authority.list_pending()[0].approval_id, True)
    await asyncio.wait_for(waiting.wait(), 3)
    authority.revoke(owner[0])
    release.set()
    result = await asyncio.wait_for(task, 3)
    assert not result.succeeded and not (root / "no.txt").exists()
    assert not manager.has_running


async def test_unicode_and_quoted_workspace_name_does_not_inject_a_profile(bundle):
    if sys.platform != "darwin":
        pytest.skip("Literal de ruta Seatbelt de macOS.")
    root = bundle[3].parent / 'investigación "segura"'
    root.mkdir(mode=0o700)
    manager = ProcessTools(WorkspaceTools(root), lambda: "synthetic", max_wall_seconds=5)
    authority = ApprovalAuthority(ttl=2)
    catalog = ToolCatalog(manager.definitions(), manager.specs(), authority=authority)
    own = manager, authority, catalog, root, ["synthetic"]
    try:
        result = await approved(own, "process_run", {"argv": ["/bin/echo", "Unicode ok"]})
        assert result.succeeded and "Unicode ok" in result.output
    finally:
        await manager.aclose()
        await authority.close()


async def test_physical_data_volume_path_cannot_bypass_outside_denial(bundle, tmp_path):
    if sys.platform != "darwin":
        pytest.skip("Alias de volumen de datos macOS.")
    sandbox_required(bundle)
    manager, _authority, _catalog, _root, owner = bundle
    outside = tmp_path.resolve() / "outside-physical.txt"
    outside.write_text("SYNTHETIC-DATA-VOLUME-SECRET")
    physical = Path("/System/Volumes/Data") / str(outside).lstrip("/")
    if not physical.exists():
        pytest.skip("El host no publica ese alias de volumen.")
    result = await approved(bundle, "process_run", {"argv": ["/bin/cat", str(physical)]})
    data = await settled(manager, owner[0], json.loads(result.output)["process_id"])
    assert data["exit_code"] != 0 and "SYNTHETIC-DATA-VOLUME-SECRET" not in data["output"]


def test_untrusted_or_nonexistent_bubblewrap_is_never_executed(tmp_path):
    malicious = tmp_path / "bwrap"
    effect = tmp_path / "unapproved-effect"
    malicious.write_text('#!/bin/sh\ntouch "' + str(effect) + '"\n')
    malicious.chmod(0o700)
    assert ProcessTools._linux_supported(str(malicious)) is False
    assert not effect.exists()
    assert ProcessTools._linux_supported(str(tmp_path / "missing")) is False
