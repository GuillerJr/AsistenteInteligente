"""Causal CPython startup fixtures: no user site, history, account or real SDK."""

from __future__ import annotations

import array
import asyncio
import errno
import fcntl
import os
import select
import subprocess
import sys
import tempfile
import termios
import time
from pathlib import Path

from jarvis_core.protocol import LocalRpcClient, TransportSecurityError

_PYTHON = os.path.realpath(sys.executable)
_SOURCE = str(Path(__file__).resolve().parents[1] / "src")
_TEMP_ROOT = "/private/tmp" if sys.platform == "darwin" else "/tmp"
_BOOTSTRAP = (
    "import runpy,sys\n"
    "sys.path.insert(0,sys.argv.pop(1))\n"
    "runpy.run_module('jarvis_core.service',run_name='__main__',alter_sys=True)\n"
)


def _writer_when_reader_waits(path, process):
    deadline = time.monotonic() + 3
    while time.monotonic() < deadline:
        assert process.poll() is None
        try:
            return os.open(path, os.O_WRONLY | os.O_NONBLOCK)
        except OSError as error:
            assert error.errno == errno.ENXIO
        time.sleep(0.005)
    raise AssertionError("own fixture never opened its selected FIFO")


def _stop(process):
    if process.poll() is None:
        process.terminate()
    try:
        return process.communicate(timeout=3)
    except subprocess.TimeoutExpired:
        process.kill()
        return process.communicate(timeout=3)


def _write_with_reader_ack(writer, payload, process):
    deadline = time.monotonic() + 3
    assert os.write(writer, payload) == len(payload)
    pending = array.array("i", [0])
    while time.monotonic() < deadline:
        # Opening a FIFO writer only pairs the endpoints. Keep that endpoint
        # alive until the only reader actually consumes the written bytes.
        fcntl.ioctl(writer, termios.FIONREAD, pending, True)
        if pending[0] == 0:
            return deadline
        assert process.poll() is None
        time.sleep(0.001)
    raise AssertionError("own FIFO reader did not acknowledge its payload")


def _release_persistent_configuration(path, writer, payload, process):
    # A real configuration is readable again. Keep the first FIFO descriptor
    # blocked while atomically installing identical bytes for any later probe.
    replacement = path.with_name("released-pyvenv.cfg")
    with replacement.open("xb") as stream:
        stream.write(payload)
    replacement.chmod(0o600)
    os.replace(replacement, path)
    # Renaming the input cannot unblock an already-open FIFO descriptor. The
    # causal intervention remains releasing that descriptor with the same bytes.
    assert process.poll() is None
    assert select.select([process.stdout], [], [], 0)[0] == []
    return _write_with_reader_ack(writer, payload, process)


def test_venv_fifo_blocks_first_frame_and_canonical_avoids_it_same_pid_advances(
    tmp_path,
):
    binary = tmp_path / "own-venv/bin/python"
    binary.parent.mkdir(parents=True)
    binary.symlink_to(_PYTHON)
    config = binary.parent.parent / "pyvenv.cfg"
    os.mkfifo(config, 0o600)
    environment = {"PATH": "/usr/bin:/bin", "HOME": str(tmp_path)}
    marker = "print('OWN FIRST FRAME',flush=True)"
    raw = subprocess.Popen(
        [str(binary), "-S", "-c", marker],
        env=environment,
        cwd=tmp_path,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    writer = None
    try:
        writer = _writer_when_reader_waits(config, raw)
        assert select.select([raw.stdout], [], [], 0)[0] == []
        first_pid = raw.pid
        started = time.monotonic()
        isolated = subprocess.run(
            [_PYTHON, "-I", "-S", "-B", "-u", "-c", marker],
            env=environment,
            cwd="/",
            capture_output=True,
            timeout=3,
        )
        elapsed = time.monotonic() - started
        assert isolated.returncode == 0 and isolated.stdout == b"OWN FIRST FRAME\n"
        assert raw.poll() is None and select.select([raw.stdout], [], [], 0)[0] == []
        deadline = _release_persistent_configuration(
            config, writer, ("home = " + str(Path(_PYTHON).parent) + "\n").encode(), raw
        )
        os.close(writer)
        writer = None
        output, _ = raw.communicate(timeout=max(0.001, deadline - time.monotonic()))
        assert raw.pid == first_pid and raw.returncode == 0 and output == b"OWN FIRST FRAME\n"
        print(f"JARVIS_STARTUP_PATH canonical_first_frame_seconds={elapsed:.6f}")
    finally:
        if writer is not None:
            os.close(writer)
        _stop(raw)


def test_configuration_rendezvous_keeps_first_reader_blocked_and_serves_a_second_read(tmp_path):
    config = tmp_path / "pyvenv.cfg"
    os.mkfifo(config, 0o600)
    payload = b"home = /own/synthetic/runtime\n"
    code = (
        "import sys\n"
        "p=sys.argv[1]\n"
        "first=open(p,'rb').read()\n"
        "second=open(p,'rb').read()\n"
        "assert first==second\n"
        "print('OWN TWO READS',flush=True)\n"
    )
    process = subprocess.Popen(
        [_PYTHON, "-I", "-S", "-B", "-u", "-c", code, str(config)],
        cwd="/",
        env={"PATH": "/usr/bin:/bin", "HOME": str(tmp_path)},
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    writer = None
    try:
        writer = _writer_when_reader_waits(config, process)
        identity = process.pid
        assert select.select([process.stdout], [], [], 0)[0] == []
        deadline = _release_persistent_configuration(config, writer, payload, process)
        os.close(writer)
        writer = None
        output, _ = process.communicate(timeout=max(0.001, deadline - time.monotonic()))
        assert process.pid == identity and process.returncode == 0 and output == b"OWN TWO READS\n"
        assert config.is_file() and config.read_bytes() == payload
    finally:
        if writer is not None:
            os.close(writer)
        _stop(process)


async def _health(path, process):
    started = time.monotonic()
    deadline = started + 5
    while time.monotonic() < deadline:
        assert process.poll() is None
        client = LocalRpcClient(path, timeout=0.2)
        try:
            await client.connect()
            assert (await client.call("health"))["service"] == "jarvis"
            return time.monotonic() - started
        except (OSError, TransportSecurityError, TimeoutError):
            await asyncio.sleep(0.01)
        finally:
            await client.aclose()
    raise AssertionError("own service did not reach health")


async def test_user_site_blocks_health_isolated_argv_skips_hook_and_same_pid_recovers():
    with tempfile.TemporaryDirectory(prefix="jarvis-site-proof-", dir=_TEMP_ROOT) as temporary:
        root = Path(temporary)
        environment = {
            "PATH": "/usr/bin:/bin",
            "HOME": temporary,
            "PYTHONUSERBASE": str(root / "own-user-base"),
            "PYTHONPATH": _SOURCE,
        }
        site = subprocess.run(
            [_PYTHON, "-S", "-c", "import site;print(site.getusersitepackages())"],
            env=environment,
            cwd=root,
            capture_output=True,
            text=True,
            timeout=3,
            check=True,
        )
        user_site = Path(site.stdout.strip())
        assert user_site.is_relative_to(root)
        user_site.mkdir(parents=True, mode=0o700)
        gate, marker = root / "own-site-gate", root / "own-site-entered"
        os.mkfifo(gate, 0o600)
        (user_site / "usercustomize.py").write_text(
            "import os\n"
            + f"open({str(marker)!r},'a').write('entered\\n')\n"
            + f"fd=os.open({str(gate)!r},os.O_RDONLY);os.read(fd,1);os.close(fd)\n"
        )
        raw = subprocess.Popen(
            [
                _PYTHON,
                "-m",
                "jarvis_core.service",
                "--state",
                str(root / "raw"),
                "--codex",
                "/usr/bin/false",
            ],
            env=environment,
            cwd=root,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        isolated = None
        writer = None
        try:
            writer = _writer_when_reader_waits(gate, raw)
            assert marker.read_text() == "entered\n"
            assert not (root / "raw/service.sock").exists()
            first_pid = raw.pid
            isolated = subprocess.Popen(
                [
                    _PYTHON,
                    "-I",
                    "-S",
                    "-B",
                    "-u",
                    "-c",
                    _BOOTSTRAP,
                    _SOURCE,
                    "--state",
                    str(root / "isolated"),
                    "--codex",
                    "/usr/bin/false",
                ],
                env=environment,
                cwd="/",
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            elapsed = await _health(root / "isolated", isolated)
            assert raw.poll() is None and not (root / "raw/service.sock").exists()
            assert marker.read_text() == "entered\n"
            _write_with_reader_ack(writer, b"x", raw)
            os.close(writer)
            writer = None
            await _health(root / "raw", raw)
            assert raw.pid == first_pid and marker.read_text() == "entered\n"
            print(f"JARVIS_STARTUP_PATH isolated_health_seconds={elapsed:.6f}")
        finally:
            if writer is not None:
                os.close(writer)
            _stop(raw)
            if isolated is not None:
                _stop(isolated)


def test_isolated_bootstrap_ignores_invalid_python_home_without_guessing_another_runtime(tmp_path):
    environment = {
        "HOME": str(tmp_path),
        "PATH": "/usr/bin:/bin",
        "PYTHONHOME": str(tmp_path / "own-invalid-home"),
    }
    raw = subprocess.run(
        [_PYTHON, "-S", "-c", "print('OWN FRAME')"],
        env=environment,
        cwd=tmp_path,
        capture_output=True,
        timeout=3,
    )
    assert raw.returncode != 0 and raw.stdout == b""
    isolated = subprocess.run(
        [_PYTHON, "-I", "-S", "-B", "-u", "-c", "print('OWN FRAME')"],
        env=environment,
        cwd="/",
        capture_output=True,
        timeout=3,
    )
    assert isolated.returncode == 0 and isolated.stdout == b"OWN FRAME\n"
