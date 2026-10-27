"""Kernel flock inheritance by each controlled process adapter, not a PID guess."""

import asyncio
import sys
from pathlib import Path
from uuid import uuid4

import pytest

from jarvis_core.codex import CodexClient
from jarvis_core.host_profile import HostProfileError, ProfileLease
from jarvis_core.mcp import LEGACY_VERSION, McpClient
from jarvis_core.processes import ProcessTools
from jarvis_core.tools import ApprovalAuthority, ToolCatalog
from jarvis_core.workspace import WorkspaceTools


def profile(tmp_path):
    root = tmp_path.resolve() / "profile"
    root.mkdir(mode=0o700)
    return root, ProfileLease.acquire(root)


def assert_busy(root):
    with pytest.raises(HostProfileError, match="profile_busy"):
        ProfileLease.acquire(root)


async def assert_released(root):
    async with asyncio.timeout(5):
        while True:
            try:
                current = ProfileLease.acquire(root)
            except HostProfileError as error:
                assert error.code == "profile_busy"
                await asyncio.sleep(0.01)
            else:
                current.close()
                return


async def test_codex_child_retains_profile_custody_after_host_descriptor_closes(tmp_path):
    root, lease = profile(tmp_path)
    script = tmp_path / "official-client-peer"
    script.write_text(
        f"#!{sys.executable}\n"
        + """
import json,sys
for line in sys.stdin:
    value=json.loads(line)
    if 'id' in value and value.get('method'):
        result={'account':None} if value['method']=='account/read' else {}
        print(json.dumps({'id':value['id'],'result':result}),flush=True)
"""
    )
    script.chmod(0o700)
    client = CodexClient(script, root / "provider")
    client.child_custody = lease.child_custody_fds
    try:
        await client.start()
        lease.close()
        assert_busy(root)
    finally:
        lease.close()
        await client.aclose()
    await assert_released(root)


async def test_mcp_child_retains_profile_custody_after_host_descriptor_closes(tmp_path):
    root, lease = profile(tmp_path)
    script = tmp_path / "mcp-peer.py"
    script.write_text("""
import json,sys
for line in sys.stdin:
    value=json.loads(line)
    if value.get('method')=='initialize':
        print(json.dumps({'jsonrpc':'2.0','id':value['id'],'result':{
            'protocolVersion':'2025-11-25','capabilities':{},
            'serverInfo':{'name':'custody-fixture','version':'1'}}}),flush=True)
""")
    client = McpClient(
        (str(Path(sys.executable).resolve()), str(script)),
        root / "mcp",
        protocol_version=LEGACY_VERSION,
    )
    client.child_custody = lease.child_custody_fds
    try:
        await client.start()
        lease.close()
        assert_busy(root)
    finally:
        lease.close()
        await client.aclose()
    await assert_released(root)


async def test_sandbox_process_retains_profile_custody_until_real_child_closes(tmp_path):
    root, lease = profile(tmp_path)
    workspace = root / "workspace"
    workspace.mkdir(mode=0o700)
    manager = ProcessTools(WorkspaceTools(workspace), lambda: "synthetic-session")
    manager.child_custody = lease.child_custody_fds
    authority = ApprovalAuthority()
    catalog = ToolCatalog(manager.definitions(), manager.specs(), authority=authority)
    try:
        if not manager.status()["available"]:
            pytest.skip("No hay sandbox OS utilizable; no se ejecuta sin ella")
        task = asyncio.create_task(
            catalog.execute(
                "process_run",
                {
                    "argv": [
                        str(Path(sys.executable).resolve()),
                        "-c",
                        "import time;time.sleep(30)",
                    ],
                    "wait_ms": 0,
                    "timeout_seconds": 30,
                },
                session_id="synthetic-session",
                run_id=uuid4().hex,
            )
        )
        async with asyncio.timeout(5):
            while not authority.list_pending():
                if task.done():
                    task.result()
                await asyncio.sleep(0)
        pending = authority.list_pending()[0]
        authority.decide(pending.approval_id, True)
        result = await task
        assert result.succeeded
        lease.close()
        assert_busy(root)
    finally:
        lease.close()
        await manager.aclose()
        await authority.close()
    await assert_released(root)
