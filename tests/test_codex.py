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
# Refuse the first protocol message if discovery could start in the child.
settings = dict(sys.argv[index + 1].split('=', 1)
                for index, argument in enumerate(sys.argv) if argument == '-c')
assert all(settings.get(key) == 'false' for key in (
    'features.apps', 'features.plugins', 'features.remote_plugin',
))
turns=0
for line in sys.stdin:
    m=json.loads(line); p=m.get('params',{}); method=m.get('method')
    result={}
    if method=='initialize':
        assert p['clientInfo']['name']=='jarvis'
        result={'userAgent':'jarvis/0.163.0 (synthetic peer)'}
    elif method=='thread/resume':
        result={'thread':{'id':p['threadId']}}
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
        if text.startswith(('error:','retry:','turnerror:')):
            kind, info = text.split(':', 1)
            if kind != 'turnerror':
                print(json.dumps({'method':'error','params':{'threadId':thread,'turnId':turn,
                    'willRetry':kind=='retry','error':{'message':'SECRET','codexErrorInfo':info}}}),
                    flush=True)
            status = 'completed' if kind=='retry' else 'failed'
            if status == 'completed':
                print(json.dumps({'method':'item/completed','params':{'threadId':thread,
                    'turnId':turn,'item':{'type':'agentMessage','id':'a','text':'Hola'}}}),flush=True)
            error = {'message':'SECRET','codexErrorInfo':info} if kind=='turnerror' else None
            print(json.dumps({'method':'turn/completed','params':{'threadId':thread,
                'turn':{'id':turn,'status':status,'error':error}}}),flush=True)
            continue
        # Phase-labelled narration, as gpt-6 models send around tool calls.
        phases = {'commentary': ['commentary', 'final_answer'],
                  'commentary-only': ['commentary'],
                  'unknown-phase': [None, 'final_answer']}.get(text)
        items = ([('item/completed',{'item':{'type':'agentMessage','id':'m'+str(n),
                   'text':'Narración '+str(n) if phase=='commentary' else 'Texto '+str(n),
                   'phase':phase}}) for n, phase in enumerate(phases)]
                 if phases else [
            ('item/agentMessage/delta',{'itemId':'a','delta':'Hola'}),
            ('item/completed',{'item':{'type':'agentMessage','id':'a','text':'Hola'}})])
        for method2,extra in [
            *items,
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


@pytest.mark.parametrize("message,answer", [
    ("commentary", "Texto 1"),  # narration before a tool call is not the answer
    ("commentary-only", "Narración 0"),  # nothing else: kept, never an empty answer
    ("unknown-phase", "Texto 0\n\nTexto 1"),  # a missing phase stays unknown
])
async def test_commentary_phase_is_excluded_from_the_run_answer(
        tmp_path, executable, message, answer):
    client = CodexClient(executable, tmp_path / "state")
    try:
        assert (await client.chat(message))["answer"] == answer
    finally:
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


def small_peer(path, *, user_agent, resumed_id=None):
    """One-purpose peer: initialize identity and, optionally, a thread/resume reply."""
    path.write_text(f"#!{sys.executable}\n" + f'''
import json,sys
for line in sys.stdin:
    m=json.loads(line); method=m.get('method'); result={{}}
    if method=='initialize':
        result={{'userAgent':{user_agent!r}}} if {user_agent!r} is not None else {{}}
    elif method=='account/read':
        result={{'account':{{'type':'chatgpt'}}}}
    elif method=='thread/resume':
        result={{'thread':{{'id':{resumed_id!r}}}}}
    if 'id' in m and method:
        print(json.dumps({{'id':m['id'],'result':result}}),flush=True)
''')
    path.chmod(0o700)
    return path


@pytest.mark.parametrize("user_agent", [
    None, "", "jarvis", "jarvis/0.162.9 (macOS)", "jarvis/0.163.0-alpha.1 (macOS)",
    "jarvis/latest", "jarvis/1.x.0", "/0.163.0", "jarvis/0.163.0" + "x" * 600,
])
async def test_unreadable_or_older_codex_version_is_refused_before_any_request(
        tmp_path, user_agent):
    client = CodexClient(small_peer(tmp_path / "peer", user_agent=user_agent), tmp_path / "state")
    try:
        with pytest.raises(CodexError, match="codex_version_unsupported"):
            await client.account_status()
        assert client._process is None and client.codex_version is None
    finally:
        await client.aclose()


@pytest.mark.parametrize("user_agent,version", [
    ("jarvis/0.163.0 (macOS 27; arm64)", "0.163.0"),
    ("Codex Desktop/0.164.2", "0.164.2"),
    ("jarvis/0.163.1-beta (x)", "0.163.1"),
    ("jarvis/1.0.0+build.7", "1.0.0"),
])
async def test_supported_codex_version_is_recorded(tmp_path, user_agent, version):
    client = CodexClient(small_peer(tmp_path / "peer", user_agent=user_agent), tmp_path / "state")
    try:
        assert (await client.account_status())["connected"]
        assert client.codex_version == version
    finally:
        await client.aclose()


@pytest.mark.parametrize("resumed_id", ["other-thread", None])
async def test_resume_that_subscribes_another_thread_is_never_reused(tmp_path, resumed_id):
    peer = small_peer(tmp_path / "peer", user_agent="jarvis/0.163.0", resumed_id=resumed_id)
    client = CodexClient(peer, tmp_path / "state")
    try:
        for _ in range(2):  # the binding stays blocked; no automatic second resume
            with pytest.raises(CodexError, match="codex_thread_recovery_required"):
                await client.chat("hola", thread_id="own-stored-thread")
        assert "own-stored-thread" not in client._threads
    finally:
        await client.aclose()


def catalog_peer(path, pages):
    """Peer serving fixed model/list pages and recording every request."""
    path.write_text(f"#!{sys.executable}\n" + f'''
import json,os,sys
pages={pages!r}
log=os.path.join(os.environ['HOME'],'catalog-wire.jsonl')
for line in sys.stdin:
    m=json.loads(line); method=m.get('method'); p=m.get('params',{{}}); result={{}}
    with open(log,'a') as stream:
        stream.write(json.dumps(m)+'\\n')
    if method=='initialize':
        result={{'userAgent':'jarvis/0.163.0'}}
    elif method=='account/read':
        result={{'account':{{'type':'chatgpt'}}}}
    elif method=='model/list':
        result=pages[p.get('cursor') or '']
    if 'id' in m and method:
        print(json.dumps({{'id':m['id'],'result':result}}),flush=True)
''')
    path.chmod(0o700)
    return path


def model(model_id, **extra):
    return {"id": model_id, "model": model_id, "displayName": model_id.upper(),
            "isDefault": False, "hidden": False, "inputModalities": ["text"], **extra}


async def test_model_catalog_pages_hides_hidden_and_reads_account_first(tmp_path):
    pages = {"": {"data": [model("gpt-a", isDefault=True, inputModalities=["text", "image"]),
                           model("gpt-hidden", hidden=True)], "nextCursor": "c2"},
             "c2": {"data": [model("gpt-b")], "nextCursor": None}}
    client = CodexClient(catalog_peer(tmp_path / "peer", pages), tmp_path / "state")
    try:
        assert await client.list_models() == [
            {"id": "gpt-a", "display_name": "GPT-A", "is_default": True, "accepts_images": True},
            {"id": "gpt-b", "display_name": "GPT-B", "is_default": False, "accepts_images": False},
        ]
        import json
        wire = [json.loads(row) for row in
                (tmp_path / "state" / "home" / "catalog-wire.jsonl").read_text().splitlines()]
        methods = [row.get("method") for row in wire]
        assert methods.index("account/read") < methods.index("model/list")
        assert [row["params"] for row in wire if row.get("method") == "model/list"] == [
            {"limit": 100, "cursor": None, "includeHidden": False},
            {"limit": 100, "cursor": "c2", "includeHidden": False}]
    finally:
        await client.aclose()


@pytest.mark.parametrize("page", [
    {}, {"data": "x"}, {"data": [model("gpt-a"), model("gpt-a")]},
    {"data": [model("bad id")]}, {"data": [{**model("gpt-a"), "displayName": ""}]},
    {"data": [{**model("gpt-a"), "isDefault": "yes"}]},
    {"data": [model("gpt-a")], "nextCursor": ""},
])
async def test_malformed_model_catalog_is_refused(tmp_path, page):
    client = CodexClient(catalog_peer(tmp_path / "peer", {"": page}), tmp_path / "state")
    try:
        with pytest.raises(CodexError, match="codex_models_invalid"):
            await client.list_models()
    finally:
        await client.aclose()


async def test_endless_model_catalog_is_bounded(tmp_path):
    pages = {"": {"data": [], "nextCursor": "c1"},
             **{f"c{n}": {"data": [], "nextCursor": f"c{n + 1}"} for n in range(1, 12)}}
    client = CodexClient(catalog_peer(tmp_path / "peer", pages), tmp_path / "state")
    try:
        with pytest.raises(CodexError, match="codex_models_too_many"):
            await client.list_models()
    finally:
        await client.aclose()


def limits_peer(path, *, account_type, result, update=None):
    """Peer answering account/rateLimits/read, then optionally one rolling update."""
    path.write_text(f"#!{sys.executable}\n" + f'''
import json,os,sys
result={result!r}; update={update!r}; account_type={account_type!r}
log=os.path.join(os.environ['HOME'],'limits-wire.jsonl')
for line in sys.stdin:
    m=json.loads(line); method=m.get('method'); reply={{}}
    with open(log,'a') as stream:
        stream.write(json.dumps(method)+'\\n')
    if method=='initialize':
        reply={{'userAgent':'jarvis/0.163.0'}}
    elif method=='account/read':
        reply={{'account':{{'type':account_type}} if account_type else None}}
    elif method=='account/rateLimits/read':
        reply=result
    if 'id' in m and method:
        print(json.dumps({{'id':m['id'],'result':reply}}),flush=True)
    if method=='account/rateLimits/read' and update is not None:
        print(json.dumps({{'method':'account/rateLimits/updated','params':{{'rateLimits':update}}}}),
              flush=True)
''')
    path.chmod(0o700)
    return path


async def test_account_limits_are_read_validated_and_never_invented(tmp_path):
    result = {"rateLimits": {"limitId": "codex", "planType": "plus",
                             "primary": {"usedPercent": 42.5, "resetsAt": 1800003600,
                                         "windowDurationMins": 300},
                             "secondary": {"windowDurationMins": 10080},
                             "credits": {"hasCredits": True, "unlimited": False,
                                         "balance": "12.5"}},
              "rateLimitsByLimitId": {"other": {"limitId": "other", "limitName": "Otro",
                                                "primary": {"usedPercent": -3, "resetsAt": "x"}}}}
    client = CodexClient(limits_peer(tmp_path / "peer", account_type="chatgpt", result=result),
                         tmp_path / "state")
    try:
        value = await client.read_limits()
        assert value["connected"] is True and type(value["updated_at"]) is int
        codex, other = value["limits"]
        assert codex == {"limit_id": "codex", "name": None, "plan": "plus", "reached": None,
                         "windows": {"primary": {"used_percent": 42.5, "resets_at": 1800003600,
                                                 "window_minutes": 300},
                                     # Absent usage is unknown, never zero.
                                     "secondary": {"used_percent": None, "resets_at": None,
                                                   "window_minutes": 10080}},
                         "credits": {"balance": 12.5, "unlimited": False, "has_credits": True}}
        # Invalid numbers are dropped rather than clamped or zero-filled.
        assert other["limit_id"] == "other"
        assert other["windows"] == {"primary": None, "secondary": None}
    finally:
        await client.aclose()


async def test_rolling_limit_update_merges_after_a_read_and_keeps_sparse_metadata(tmp_path):
    result = {"rateLimits": {"planType": "pro", "credits": {"balance": 5},
                             "primary": {"usedPercent": 10, "windowDurationMins": 300}}}
    update = {"limitId": "codex", "planType": None, "credits": None,
              "primary": {"usedPercent": 97, "windowDurationMins": 300},
              "rateLimitReachedType": "primary"}
    client = CodexClient(limits_peer(tmp_path / "peer", account_type="chatgpt",
                                     result=result, update=update), tmp_path / "state")
    try:
        await client.read_limits()
        for _ in range(100):
            row = client.limits_snapshot()["limits"][0]
            if row["reached"] == "primary":
                break
            await asyncio.sleep(0.01)
        assert row["windows"]["primary"]["used_percent"] == 97
        assert row["plan"] == "pro" and row["credits"]["balance"] == 5
        await client.logout()
        assert client.limits_snapshot() == {"connected": False, "limits": [], "updated_at": None}
    finally:
        await client.aclose()


async def test_limits_without_a_chatgpt_account_are_not_read(tmp_path):
    client = CodexClient(limits_peer(tmp_path / "peer", account_type=None, result={}),
                         tmp_path / "state")
    try:
        assert await client.read_limits() == {"connected": False, "limits": [], "updated_at": None}
        wire = (tmp_path / "state" / "home" / "limits-wire.jsonl").read_text()
        assert "account/rateLimits/read" not in wire
    finally:
        await client.aclose()


@pytest.mark.parametrize("message,code", [
    ("error:usageLimitExceeded", "codex_usage_limit_reached"),
    ("error:rateLimitExceeded", "codex_rate_limited"),
    ("error:serverOverloaded", "codex_server_overloaded"),
    ("error:unauthorized", "codex_login_required"),
    ("turnerror:usageLimitExceeded", "codex_usage_limit_reached"),
    ("error:contextWindowExceeded", "codex_turn_failed"),
])
async def test_terminal_turn_error_is_classified_without_leaking_its_message(
        tmp_path, executable, message, code):
    client = CodexClient(executable, tmp_path / "state")
    try:
        with pytest.raises(CodexError) as error:
            await client.chat(message)
        assert str(error.value) == code and "SECRET" not in str(error.value)
    finally:
        await client.aclose()


async def test_retried_turn_error_only_informs_and_the_turn_can_complete(tmp_path, executable):
    client = CodexClient(executable, tmp_path / "state")
    try:
        assert (await client.chat("retry:serverOverloaded"))["answer"] == "Hola"
    finally:
        await client.aclose()


@pytest.mark.parametrize("kind", ["absent", "not-executable"])
async def test_absent_or_unusable_cli_is_a_bounded_diagnostic_not_a_start_crash(tmp_path, kind):
    path = tmp_path / "bin" / "codex"
    if kind == "not-executable":
        path.parent.mkdir()
        path.write_text("#!/bin/sh\nexit 0\n")
        path.chmod(0o600)
    client = CodexClient(path, tmp_path / "state")  # the service can still start
    try:
        for _ in range(2):  # stable, never a retry with another binary
            with pytest.raises(CodexError, match="codex_unavailable"):
                await client.account_status()
        assert client._process is None and not (tmp_path / "state").exists()
    finally:
        await client.aclose()
