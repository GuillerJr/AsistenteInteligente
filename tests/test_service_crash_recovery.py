"""Actual killed own service, held flock, stale socket and interrupted SQLite run."""

import asyncio
import os
import signal
import sys
import tempfile
from pathlib import Path

import pytest

from jarvis_core.host_profile import HostProfileError, ProfileLease
from jarvis_core.protocol import LocalRpcClient, TransportSecurityError

_FIXTURE = r"""
import asyncio, signal, sys
from pathlib import Path
from jarvis_core.service import JarvisService
from jarvis_core.mcp_registry import ServerSpec
class Peer:
    async def account_status(self): return {'connected':True,'mode':'chatgpt'}
    async def chat(self, message, *, on_thread, **options):
        on_thread('synthetic-crash-thread')
        marker=Path(sys.argv[1])/'workspace'/'provider-calls'
        marker.write_text((marker.read_text() if marker.exists() else '')+'1')
        await asyncio.Event().wait()
    async def aclose(self): pass
async def main():
    service=JarvisService(Path(sys.argv[1]),Peer())
    done=asyncio.Event()
    asyncio.get_running_loop().add_signal_handler(signal.SIGTERM,done.set)
    try:
        if len(sys.argv)>2:
            await service.mcp.connect(ServerSpec('custody',
                (sys.executable,sys.argv[2],sys.argv[3]),'2025-11-25'))
        await service.start()
        await done.wait()
    finally: await service.aclose()
asyncio.run(main())
"""

_ORPHAN_SERVER = r"""
import json,os,sys,time
from pathlib import Path
release=Path(sys.argv[1])
for line in sys.stdin:
    request=json.loads(line)
    if 'id' not in request: continue
    result={}
    if request.get('method')=='initialize':
        result={'protocolVersion':'2025-11-25','capabilities':{'tools':{}},
            'serverInfo':{'name':'own-custody-peer','version':'1'}}
    elif request.get('method')=='tools/list': result={'tools':[]}
    print(json.dumps({'jsonrpc':'2.0','id':request['id'],'result':result}),flush=True)
release.with_name('orphan-ready').write_text(str(os.getpid()))
deadline=time.monotonic()+15
while not release.exists() and time.monotonic()<deadline: time.sleep(.01)
release.with_name('orphan-closed').touch()
"""


async def ready(profile, process):
    async with asyncio.timeout(8):
        while True:
            assert process.returncode is None
            probe = LocalRpcClient(profile, timeout=0.2)
            try:
                try:
                    await probe.connect()
                    await probe.call("health")
                finally:
                    await probe.aclose()
            except (OSError, TransportSecurityError, TimeoutError):
                await asyncio.sleep(0.01)
                continue
            # The short readiness probe is not an operational RPC deadline.
            # Close it before opening the normal client; never retry runs.start.
            client = LocalRpcClient(profile)
            try:
                await client.connect()
                return client
            except BaseException:
                await client.aclose()
                raise


async def test_own_process_crash_releases_flock_reclaims_only_socket_and_never_replays():
    base = "/private/tmp" if sys.platform == "darwin" else tempfile.gettempdir()
    with tempfile.TemporaryDirectory(prefix="jvs-crash-", dir=base) as temporary:
        profile = Path(temporary).resolve() / "profile"
        environment = {
            "PATH": "/usr/bin:/bin",
            "PYTHONPATH": str(Path(__file__).resolve().parents[1] / "src"),
            "JARVIS_RPC_BOOTSTRAP_PUBLIC_KEY": os.environ["JARVIS_RPC_BOOTSTRAP_PUBLIC_KEY"],
        }
        processes = []
        try:
            first = await asyncio.create_subprocess_exec(
                sys.executable,
                "-c",
                _FIXTURE,
                str(profile),
                env=environment,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.PIPE,
            )
            processes.append(first)
            client = await ready(profile, first)
            session = (await client.call("sessions.create", {"title": "Caída sintética"}))[
                "session"
            ]
            ref = {key: session[key] for key in ("session_id", "temporary")}
            run = await client.call("runs.start", {**ref, "message": "esperar"})
            marker = profile / "workspace" / "provider-calls"
            async with asyncio.timeout(5):
                while not marker.exists():
                    await asyncio.sleep(0.01)
            first.send_signal(signal.SIGKILL)
            await first.wait()
            await client.aclose()
            assert first.returncode == -signal.SIGKILL
            assert (profile / "service.sock").exists()
            assert marker.read_text() == "1"
            second = await asyncio.create_subprocess_exec(
                sys.executable,
                "-c",
                _FIXTURE,
                str(profile),
                env=environment,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.PIPE,
            )
            processes.append(second)
            resumed = await ready(profile, second)
            try:
                value = (await resumed.call("runs.get", {**ref, "run_id": run["run_id"]}))["run"]
                assert value["status"] == "interrupted" and value["outcome"] == "unknown"
                assert marker.read_text() == "1"
                messages = (await resumed.call("sessions.messages", ref))["messages"]
                assert len(messages) == 1 and messages[0]["text"] == "esperar"
                assert not (profile / "provider").exists()
            finally:
                await resumed.aclose()
            second.terminate()
            await asyncio.wait_for(second.wait(), 8)
            assert second.returncode == 0
            assert not (profile / "service.sock").exists()
        finally:
            for process in processes:
                if process.returncode is None:
                    process.kill()
                await process.wait()


async def test_killed_host_cannot_restart_while_controlled_mcp_orphan_keeps_lease():
    base = "/private/tmp" if sys.platform == "darwin" else tempfile.gettempdir()
    with tempfile.TemporaryDirectory(prefix="jvs-orphan-", dir=base) as temporary:
        root = Path(temporary).resolve()
        profile, release = root / "profile", root / "release-own-peer"
        peer = root / "own-mcp-peer.py"
        peer.write_text(_ORPHAN_SERVER)
        environment = {
            "PATH": "/usr/bin:/bin",
            "PYTHONPATH": str(Path(__file__).resolve().parents[1] / "src"),
            "JARVIS_RPC_BOOTSTRAP_PUBLIC_KEY": os.environ["JARVIS_RPC_BOOTSTRAP_PUBLIC_KEY"],
        }
        processes = []
        try:
            first = await asyncio.create_subprocess_exec(
                sys.executable,
                "-c",
                _FIXTURE,
                str(profile),
                str(peer),
                str(release),
                env=environment,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.PIPE,
            )
            processes.append(first)
            client = await ready(profile, first)
            await client.call("sessions.create", {"title": "Custodia sintética"})
            await client.aclose()
            first.kill()
            await first.wait()
            async with asyncio.timeout(5):
                while not (root / "orphan-ready").exists():
                    await asyncio.sleep(0.01)
            with pytest.raises(HostProfileError, match="profile_busy"):
                ProfileLease.acquire(profile)
            before = (profile / "jarvis.sqlite3").read_bytes()
            blocked = await asyncio.create_subprocess_exec(
                sys.executable,
                "-c",
                _FIXTURE,
                str(profile),
                env=environment,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.PIPE,
            )
            processes.append(blocked)
            _, errors = await asyncio.wait_for(blocked.communicate(), 5)
            assert blocked.returncode != 0 and b"profile_busy" in errors
            assert (profile / "jarvis.sqlite3").read_bytes() == before
            release.touch()
            async with asyncio.timeout(5):
                while True:
                    try:
                        lease = ProfileLease.acquire(profile)
                    except HostProfileError as error:
                        assert error.code == "profile_busy"
                        await asyncio.sleep(0.01)
                    else:
                        lease.close()
                        break
            assert (root / "orphan-closed").exists()
            recovered = await asyncio.create_subprocess_exec(
                sys.executable,
                "-c",
                _FIXTURE,
                str(profile),
                env=environment,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.PIPE,
            )
            processes.append(recovered)
            client = await ready(profile, recovered)
            await client.aclose()
            recovered.terminate()
            await asyncio.wait_for(recovered.wait(), 8)
            assert recovered.returncode == 0
        finally:
            release.touch(exist_ok=True)
            for process in processes:
                if process.returncode is None:
                    process.kill()
                await process.wait()
