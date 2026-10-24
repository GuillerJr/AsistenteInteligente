"""Real child-process transport tests; the peer simulates the official protocol.

These do not prove account entitlement, model inference or provider tool isolation.
"""

import asyncio
import sys
from pathlib import Path

import pytest

from jarvis_core.codex import CodexClient, CodexError
from jarvis_core.state import StateError


@pytest.fixture
def executable(tmp_path):
    path = tmp_path / "official-peer-fixture"
    path.write_text(f"#!{sys.executable}\n" + '''
import json,os,sys,time
from pathlib import Path
assert Path.home() != Path(os.environ.get('OWNER_HOME','/unlikely'))
assert not any(k in os.environ for k in ('OPENAI_API_KEY','NVIDIA_API_KEY','ANTHROPIC_API_KEY'))
turns=0
for line in sys.stdin:
    m=json.loads(line); p=m.get('params',{}); method=m.get('method')
    result={}
    if method=='initialize':
        assert p['clientInfo']['name']=='jarvis'
    if method=='account/read':
        result={'account':{'type':'chatgpt','email':'private@example.invalid'}}
    elif method=='account/login/start':
        result={'authUrl':'https://auth.openai.com/oauth/authorize?state=fixture'}
    elif method=='thread/start':
        assert p['approvalPolicy']=='untrusted' and p['sandbox']=='read-only'
        result={'thread':{'id':'own-thread'}}
    elif method=='turn/start':
        turns+=1
        result={'turn':{'id':str(turns)}}
    if 'id' in m and method:
        print(json.dumps({'id':m['id'],'result':result}),flush=True)
    if method=='turn/start':
        text=p['input'][0]['text']; thread=p['threadId']; turn=str(turns)
        if text=='wait':
            continue
        if text=='disconnect':
            sys.exit(0)
        if text=='approval':
            print(json.dumps({'id':500,'method':'item/commandExecution/requestApproval',
                              'params':{'threadId':thread,'turnId':turn}}),flush=True)
            continue
        if text=='failure':
            print(json.dumps({'method':'turn/completed','params':{'threadId':thread,
                'turn':{'id':turn,'status':'failed','error':{'message':'SECRET'}}}}),flush=True)
            continue
        for method2,extra in [
            ('item/agentMessage/delta',{'itemId':'a','delta':'Hola'}),
            ('item/completed',{'item':{'type':'agentMessage','id':'a','text':'Hola'}}),
            ('turn/completed',{'turn':{'id':turn,'status':'completed'}}),
        ]:
            print(json.dumps({'method':method2,'params':{'threadId':thread,
                'turnId':turn,**extra}}),flush=True)
    if 'error' in m and m.get('id')==500:
        Path(os.environ['HOME'],'approval-rejected').touch()
''')
    path.chmod(0o700)
    return path


async def test_official_protocol_handshake_streaming_and_no_secret_metadata(tmp_path, executable):
    client = CodexClient(executable, tmp_path / "state")
    events, threads = [], []
    try:
        assert await client.account_status() == {"connected": True, "mode": "chatgpt"}
        result = await client.chat("hola", on_event=lambda *e: events.append(e),
                                   on_thread=threads.append)
        assert result == {"thread_id": "own-thread", "answer": "Hola"}
        assert threads == ["own-thread"]
        assert events == [("answer.delta", {"text": "Hola"})]
        assert (await client.chat("otra", thread_id=threads[0]))["answer"] == "Hola"
        assert (await client.login())["url"].startswith("https://auth.openai.com/")
        assert await client.logout() == {"connected": False, "mode": None}
    finally:
        await client.aclose()
    assert client._process is None
    await client.aclose()


async def test_child_never_inherits_provider_keys(tmp_path, executable, monkeypatch):
    for name in ("OPENAI_API_KEY", "NVIDIA_API_KEY", "ANTHROPIC_API_KEY"):
        monkeypatch.setenv(name, "SYNTHETIC-DO-NOT-PASS")
    client = CodexClient(executable, tmp_path / "state")
    try:
        assert (await client.account_status())["connected"]
    finally:
        await client.aclose()


@pytest.mark.parametrize("message,code", [
    ("failure", "codex_turn_failed"), ("disconnect", "codex_disconnected"),
])
async def test_failed_or_disconnected_turn_is_not_success(tmp_path, executable, message, code):
    client = CodexClient(executable, tmp_path / "state")
    try:
        with pytest.raises(CodexError, match=code) as error:
            await client.chat(message)
        assert "SECRET" not in str(error.value)
    finally:
        await client.aclose()


async def test_timeout_interrupts_without_automatic_retry(tmp_path, executable):
    client = CodexClient(executable, tmp_path / "state")
    try:
        with pytest.raises(TimeoutError):
            await client.chat("wait", turn_timeout=0.05)
        assert client._events == {}
        assert (await client.chat("after"))["answer"] == "Hola"
    finally:
        await client.aclose()


async def test_cancel_interrupts_and_settles(tmp_path, executable):
    client = CodexClient(executable, tmp_path / "state")
    try:
        task = asyncio.create_task(client.chat("wait"))
        while not client._events:
            await asyncio.sleep(0.001)
        await asyncio.sleep(0.02)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert client._events == {}
    finally:
        await client.aclose()


async def test_unimplemented_server_approval_is_denied(tmp_path, executable):
    client = CodexClient(executable, tmp_path / "state")
    try:
        with pytest.raises(TimeoutError):
            await client.chat("approval", turn_timeout=0.08)
        assert (tmp_path / "state/home/approval-rejected").exists()
    finally:
        await client.aclose()


async def test_login_allows_only_official_https_url(tmp_path, executable):
    path = Path(executable)
    path.write_text(path.read_text().replace("auth.openai.com/oauth", "attacker.invalid/oauth"))
    client = CodexClient(executable, tmp_path / "state")
    try:
        with pytest.raises(CodexError, match="codex_login_url_invalid"):
            await client.login()
    finally:
        await client.aclose()


async def test_symlink_directory_is_rejected_before_child_starts(tmp_path, executable):
    (tmp_path / "real").mkdir(mode=0o700)
    (tmp_path / "state").symlink_to(tmp_path / "real", target_is_directory=True)
    client = CodexClient(executable, tmp_path / "state")
    with pytest.raises(StateError):
        await client.start()
    assert client._process is None


@pytest.mark.parametrize("message", ["", " ", "x" * 32001, "á" * 16001, None, 1])
async def test_invalid_message_never_starts_child(tmp_path, executable, message):
    client = CodexClient(executable, tmp_path / "state")
    with pytest.raises(ValueError):
        await client.chat(message)
    assert client._process is None


def test_constructor_requires_explicit_executable(tmp_path):
    with pytest.raises(ValueError):
        CodexClient(Path("codex"), tmp_path)
    with pytest.raises(ValueError):
        CodexClient(Path(sys.executable), tmp_path, timeout=31)
