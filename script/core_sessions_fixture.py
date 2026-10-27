#!/usr/bin/env python3
"""Corpus nuevo y peer stdio propios para probar la app, nunca un fallback.

Sólo prepara/observa una raíz temporal explícita con marca e identidad propias.
El peer implementa formas de protocolo para pruebas: no es Codex ni inferencia.
Las aprobaciones posteriores pasan por la autoridad y la interfaz productivas.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shlex
import sqlite3
import stat
import sys
from collections import Counter
from contextlib import closing
from dataclasses import asdict
from pathlib import Path
from uuid import uuid4

_REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO / "src"))

from jarvis_core.config import ProfileConfig  # noqa: E402
from jarvis_core.profiles import ProfilesStore  # noqa: E402
from jarvis_core.recovery_fs import Directory, PinnedFile  # noqa: E402
from jarvis_core.runtime import ToolResult  # noqa: E402
from jarvis_core.scheduler import ScheduleStore  # noqa: E402
from jarvis_core.state import StateStore  # noqa: E402

FORMAT = "jarvis-core-sessions-fixture/1"
_TEMP = Path("/private/tmp" if sys.platform == "darwin" else "/tmp")
CHILD_FILE = "fase-a-core-hijo.txt"
CHILD_BYTES = b"PRUEBA NUCLEO HIJO 2"
CONTRACT_BYTES = b"CONTRATO SINTETICO: NO MODIFICAR"

# Independently authored, execution-free until the caller explicitly selects
# this executable as --codex for the synthetic profile. No Jarvis imports.
PEER = r"""
import hashlib, json, os, select, sys, time
from pathlib import Path
from uuid import uuid4
ROOT = Path(__file__).absolute().parent
def record(kind, **values):
    fd=os.open(ROOT/'wire.jsonl',os.O_WRONLY|os.O_CREAT|os.O_APPEND|os.O_NOFOLLOW,0o600)
    with os.fdopen(fd,'a') as output:
        output.write(json.dumps({'kind':kind,'at_ns':time.time_ns(),**values},ensure_ascii=False)+'\n')
def save_threads():
    pending=ROOT/('peer-threads-'+uuid4().hex+'.pending')
    fd=os.open(pending,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
    with os.fdopen(fd,'w') as output: json.dump(threads,output,ensure_ascii=False)
    os.replace(pending,ROOT/'peer-threads.json')
def emit(value): print(json.dumps(value,ensure_ascii=False),flush=True)
def reply(message,result): emit({'id':message['id'],'result':result})
def finish(state,answer='',status='completed'):
    if state['finished']: return
    state['finished']=True
    if status=='completed':
        emit({'method':'item/agentMessage/delta','params':{'threadId':state['thread'],
            'turnId':state['turn'],'itemId':'answer','delta':answer}})
        emit({'method':'item/completed','params':{'threadId':state['thread'],
            'turnId':state['turn'],'item':{'type':'agentMessage','id':'answer','text':answer}}})
    emit({'method':'turn/completed','params':{'threadId':state['thread'],
        'turnId':state['turn'],'turn':{'id':state['turn'],'status':status}}})
    record('turn.finished',thread=state['thread'],turn=state['turn'],status=status,answer=answer)
def propose_write(state):
    global request_number
    if 'workspace_write' not in threads[state['thread']]['tools']:
        finish(state,'ERROR SINTETICO: herramienta no ofrecida',status='failed'); return
    request_number+=1
    arguments={'path':'fase-a-core-hijo.txt','text':'PRUEBA NUCLEO HIJO 2'}
    callbacks[request_number]=state
    record('tool.proposed',request_id=request_number,thread=state['thread'],turn=state['turn'],
        tool='workspace_write',arguments=arguments)
    emit({'id':request_number,'method':'item/tool/call','params':{
        'threadId':state['thread'],'turnId':state['turn'],'callId':'own-call-'+str(request_number),
        'namespace':None,'tool':'workspace_write','arguments':arguments}})
def consume(message):
    record('received',message=message)
    method,params=message.get('method'),message.get('params',{})
    if method is None:
        state=callbacks.pop(message.get('id'),None)
        if state:
            record('tool.replied',request_id=message['id'],reply=message.get('result',message.get('error')))
            finish(state,'REPORTE SINTETICO HIJO 2: recibo recibido; '+
                'el archivo requiere comprobación independiente.')
        return
    result={}
    if method=='initialize':
        assert params['clientInfo']['name']=='jarvis'
    elif method=='account/read':
        result={'account':{'type':'chatgpt'},'authMode':'chatgpt'}
    elif method=='account/login/start':
        emit({'id':message['id'],'error':{'code':-32000,
            'message':'Este peer sintético no inicia sesión real'}})
        return
    elif method=='thread/start':
        assert params['sandbox']=='read-only' and params['approvalPolicy']=='untrusted'
        identity='jarvis-fixture-thread-'+uuid4().hex
        threads[identity]={'tools':[tool['name'] for tool in params.get('dynamicTools',[])],
            'instructions':params.get('developerInstructions',''),'items':[]}
        save_threads(); result={'thread':{'id':identity}}
    elif method=='thread/resume':
        assert params['threadId'] in threads
        result={'thread':{'id':params['threadId']}}
    elif method=='thread/inject_items':
        threads[params['threadId']]['items']=params['items']; save_threads()
    elif method=='turn/start':
        state={'thread':params['threadId'],'turn':'jarvis-fixture-turn-'+uuid4().hex,
            'message':params['input'][0]['text'],'finished':False,'held':False}
        active[state['thread']]=state
        result={'turn':{'id':state['turn']}}
    elif method=='turn/steer':
        state=active[params['threadId']]
        assert params['expectedTurnId']==state['turn'] and not state['finished']
        result={'turnId':state['turn']}
    if 'id' in message: reply(message,result)
    if method=='turn/interrupt':
        state=active.get(params['threadId'])
        if state: finish(state,status='interrupted')
    elif method=='turn/steer':
        finish(state,'CORRECCION SINTETICA APLICADA: '+params['input'][0]['text'])
    elif method=='turn/start':
        text=state['message'].upper()
        record('turn.started',thread=state['thread'],turn=state['turn'],message=state['message'],
            offered_tools=threads[state['thread']]['tools'])
        if 'ESPERAR' in text:
            state['held']=True
        elif 'HIJO 2' in text or 'ESCRIBIR HIJO' in text:
            propose_write(state)
        elif 'HIJO 1' in text:
            finish(state,'INFORME SINTETICO HIJO 1: TOPACIO 4826; sin efectos.')
        elif 'CONTEXTO' in text:
            data=json.dumps(threads[state['thread']]['items'],ensure_ascii=False)
            finish(state,'CONTEXTO SINTETICO: TOPACIO 4826; '+
                ('decisiones conservadas' if 'DECISION' in data.upper()
                    else 'sin decisión explícita')+
                '; recibos históricos son datos sin permiso de repetición.')
        else:
            finish(state,'SALIDA SINTETICA JARVIS NUCLEO 4826: '+state['message'])

assert json.loads((ROOT/'fixture.json').read_text())['format']=='jarvis-core-sessions-fixture/1'
threads_path=ROOT/'peer-threads.json'
threads=json.loads(threads_path.read_text()) if threads_path.exists() else {}
active,callbacks={},{}
request_number=10000
fd=os.open(ROOT/'peer-launches.jsonl',os.O_WRONLY|os.O_CREAT|os.O_APPEND|os.O_NOFOLLOW,0o600)
with os.fdopen(fd,'a') as output:
    output.write(json.dumps({'pid':os.getpid(),'at_ns':time.time_ns(),'synthetic':True})+'\n')
buffer=b''
while True:
    ready,_,_=select.select([0],[],[],.05)
    if ready:
        data=os.read(0,65536)
        if not data: break
        buffer+=data
        assert len(buffer)<=2*1024*1024
        while b'\n' in buffer:
            line,buffer=buffer.split(b'\n',1)
            if line: consume(json.loads(line))
    if (ROOT/'release-held').exists():
        for state in list(active.values()):
            if state['held'] and not state['finished']:
                finish(state,'ESPERA SINTETICA LIBERADA; no se ejecutaron herramientas.')
"""


def _write(path, data, mode=0o600):
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, mode)
    with os.fdopen(descriptor, "wb") as output:
        output.write(data)


def _read(path, maximum=64 * 1024 * 1024):
    with Directory(path.parent) as parent, PinnedFile(parent, path.name, maximum) as selected:
        return selected.read(lambda: None)


def _read_executable(path):
    with Directory(path.parent) as parent:
        descriptor = os.open(
            path.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent.fd
        )
        try:
            before = os.fstat(descriptor)
            if (
                not stat.S_ISREG(before.st_mode)
                or before.st_uid != os.getuid()
                or stat.S_IMODE(before.st_mode) != 0o700
                or before.st_nlink != 1
                or before.st_size > 65536
            ):
                raise ValueError("fixture_executable_invalid")
            data = os.read(descriptor, before.st_size + 1)
            after = os.stat(path.name, dir_fd=parent.fd, follow_symlinks=False)
            if (
                before.st_dev,
                before.st_ino,
                before.st_size,
                before.st_mtime_ns,
                before.st_ctime_ns,
            ) != (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns, after.st_ctime_ns):
                raise ValueError("fixture_executable_changed")
            parent.check()
            return data
        finally:
            os.close(descriptor)


def _root(path, *, new=False):
    root = Path(path)
    if (
        not root.is_absolute()
        or root.parent != _TEMP
        or not root.name.startswith("jarvis-core-sessions-")
        or ".." in root.parts
    ):
        raise ValueError("fixture_new_private_temp_root_required")
    if new:
        root.mkdir(mode=0o700)
    with Directory(root):
        pass
    return root


def _sha(value):
    return hashlib.sha256(value).hexdigest()


def _receipt(result):
    raw = result.output.encode()
    return {
        **asdict(result),
        "output_sha256": _sha(raw),
        "output_bytes": len(raw),
        "output_truncated": False,
    }


def _projection(root):
    raw = _read(root / "profile" / "jarvis.sqlite3")
    with closing(sqlite3.connect(":memory:")) as database:
        database.deserialize(raw)
        database.execute("PRAGMA trusted_schema=OFF")
        database.execute("PRAGMA query_only=ON")
        if database.execute("PRAGMA quick_check").fetchall() != [("ok",)]:
            raise ValueError("fixture_sqlite_invalid")
        return {
            table: [list(row) for row in database.execute("SELECT * FROM " + table)]
            for table in ("sessions", "messages", "runs", "events")
        }


def prepare(path):
    root = _root(path, new=True)
    profile, workspace = root / "profile", root / "workspace"
    profile.mkdir(mode=0o700)
    workspace.mkdir(mode=0o700)
    _write(workspace / "contrato-fase-a.txt", CONTRACT_BYTES)
    config = ProfileConfig(profile)
    info = workspace.stat()
    config.set_workspace(workspace, (info.st_dev, info.st_ino))
    agents = ProfilesStore(profile / "profiles")
    agents.update(
        "jarvis",
        expected_revision=1,
        instructions="INSTRUCCIONES SINTETICAS: responde en español y conserva TOPACIO 4826.",
    )
    agents.create(
        "Analista sintético",
        "Revisa únicamente los datos sintéticos de esta prueba.",
        agent_id="analista",
    )
    store = StateStore(profile / "jarvis.sqlite3")
    try:
        session = store.create_session("PRUEBA NUCLEO Y SESIONES", agent_id="jarvis")
        for index in range(5):
            run = store.create_run(
                session.ref, user_text=("TOPACIO 4826: PRUEBA HISTORICA SINTETICA " + str(index))
            )
            unknown = index == 4
            receipt = _receipt(
                ToolResult(
                    "workspace_write" if unknown else "workspace_read",
                    not unknown,
                    not unknown,
                    output="EFECTO HISTORICO INCIERTO; NO REPETIR"
                    if unknown
                    else "LECTURA SINTETICA",
                    error="synthetic_effect_uncertain" if unknown else None,
                    effect_status="unknown" if unknown else "known",
                    operation_id=uuid4().hex,
                )
            )
            store.finish_run(
                session.ref,
                run.run_id,
                status="failed" if unknown else "completed",
                outcome="unknown" if unknown else "known",
                receipt={
                    "answer": None if unknown else "RESPUESTA HISTORICA " + str(index),
                    "answer_source": "model",
                    "error": "synthetic_unknown" if unknown else None,
                    "action_receipts": [receipt] if index in {0, 4} else [],
                },
                assistant_text=None if unknown else "RESPUESTA HISTORICA " + str(index),
            )
    finally:
        store.close()
    schedule = ScheduleStore(profile / "schedules.sqlite3")
    try:
        assert schedule.list() == ()
    finally:
        schedule.close()
    peer = root / "jarvis_core_stdio_peer.py"
    _write(peer, PEER.encode())
    executable = root / "jarvis-core-stdio-peer"
    command = "#!/bin/sh\nexec " + shlex.quote(os.path.realpath(sys.executable))
    command += " -I -S -B -u " + shlex.quote(str(peer)) + ' "$@"\n'
    _write(executable, command.encode(), 0o700)
    projection = _projection(root)
    info = profile.stat()
    result = {
        "format": FORMAT,
        "synthetic_only": True,
        "runtime_provider": "own_stdio_peer",
        "reference_code_copied": False,
        "real_account_required": False,
        "root": str(root),
        "profile": str(profile),
        "workspace": str(workspace),
        "profile_identity": [info.st_dev, info.st_ino],
        "session_id": session.ref.session_id,
        "codex_fixture": str(executable),
        "peer_sha256": _sha(PEER.encode()),
        "executable_sha256": _sha(command.encode()),
        "desktop_key_prepared": False,
        "key_bootstrap_owner": "native_ServiceProcess",
        "agents": {"jarvis": 2, "analista": 1},
        "seed_messages": projection["messages"],
        "seed_runs": projection["runs"],
        "expected_child_file": CHILD_FILE,
        "expected_child_sha256": _sha(CHILD_BYTES),
        "expected_child_bytes": len(CHILD_BYTES),
        "contract_sha256": _sha(CONTRACT_BYTES),
        "prompts": {
            "simple": "PRUEBA SIMPLE",
            "context": "PRUEBA CONTEXTO",
            "child1": "PRUEBA HIJO 1",
            "child2": "PRUEBA HIJO 2",
            "held": "ESPERAR PRUEBA NUCLEO",
            "steer": "PRUEBA CORRECCION 7319",
        },
    }
    _write(root / "fixture.json", json.dumps(result, ensure_ascii=False, indent=2).encode())
    return result


def observe(path):
    root = _root(path)
    manifest = json.loads(_read(root / "fixture.json", 1024 * 1024))
    with Directory(root / "profile") as profile:
        actual = [profile.identity.device, profile.identity.inode]
    if (
        manifest.get("format") != FORMAT
        or manifest.get("root") != str(root)
        or manifest.get("profile_identity") != actual
        or manifest.get("synthetic_only") is not True
    ):
        raise ValueError("fixture_identity_mismatch")
    projection = _projection(root)
    wire = (
        []
        if not (root / "wire.jsonl").exists()
        else [json.loads(line) for line in _read(root / "wire.jsonl").splitlines()]
    )
    launches = (
        []
        if not (root / "peer-launches.jsonl").exists()
        else [json.loads(line) for line in _read(root / "peer-launches.jsonl").splitlines()]
    )
    methods = Counter(
        row["message"].get("method")
        for row in wire
        if row["kind"] == "received" and row["message"].get("method")
    )
    child = root / "workspace" / CHILD_FILE
    child_data = _read(child) if child.exists() else None
    return {
        "format": FORMAT,
        "profile": str(root / "profile"),
        "read_only": True,
        "seed_messages_preserved": all(
            row in projection["messages"] for row in manifest["seed_messages"]
        ),
        "seed_runs_preserved": all(row in projection["runs"] for row in manifest["seed_runs"]),
        "sessions_count": len(projection["sessions"]),
        "messages_count": len(projection["messages"]),
        "runs_count": len(projection["runs"]),
        "run_statuses": dict(Counter(row[2] for row in projection["runs"])),
        "method_counts": dict(methods),
        "sdk_launches": len(launches),
        "tool_proposals": [row for row in wire if row["kind"] == "tool.proposed"],
        "tool_replies": [row for row in wire if row["kind"] == "tool.replied"],
        "turns": [row for row in wire if row["kind"] == "turn.started"],
        "steering_requests": [
            row
            for row in wire
            if row["kind"] == "received" and row["message"].get("method") == "turn/steer"
        ],
        "child_file_present": child_data is not None,
        "child_file_bytes": len(child_data) if child_data is not None else None,
        "child_file_sha256": _sha(child_data) if child_data is not None else None,
        "child_file_matches": child_data == CHILD_BYTES if child_data is not None else None,
        "contract_unchanged": _sha(_read(root / "workspace" / "contrato-fase-a.txt"))
        == manifest["contract_sha256"],
        "peer_unchanged": _sha(_read(root / "jarvis_core_stdio_peer.py"))
        == manifest["peer_sha256"],
        "launcher_unchanged": _sha(_read_executable(root / "jarvis-core-stdio-peer"))
        == manifest["executable_sha256"],
        "historical_unknown_replayed": any(
            row["message"].get("params", {}).get("input", [{}])[0].get("text", "")
            in {row[4] for row in manifest["seed_messages"]}
            for row in wire
            if row["kind"] == "received" and row["message"].get("method") == "turn/start"
        ),
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("operation", choices=("prepare", "observe", "release", "reset-release"))
    parser.add_argument("root", type=Path)
    args = parser.parse_args()
    if args.operation == "prepare":
        value = prepare(args.root)
    elif args.operation == "observe":
        value = observe(args.root)
    else:
        root = _root(args.root)
        observe(root)  # Validate the exact own corpus before its finite control effect.
        release = root / "release-held"
        if args.operation == "release":
            if not release.exists():
                _write(release, b"")
        elif release.exists():
            _read(release)
            release.unlink()
        value = {"released": release.exists()}
    print(json.dumps(value, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
