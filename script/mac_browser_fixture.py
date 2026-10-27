#!/usr/bin/env python3
"""Perfil vacío, peer SDK y sitio artificial propios para QA nativa de Jarvis.

No es inferencia ni un proveedor alternativo. Sólo opera una raíz temporal nueva,
marcada e identificada; observar nunca abre SQLite en disco ni reproduce acciones.
"""

from __future__ import annotations

import argparse
import hashlib
import html
import json
import os
import shlex
import sqlite3
import stat
import sys
import threading
import time
from collections import Counter
from contextlib import closing
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

_REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO / "src"))

from jarvis_core.config import ProfileConfig  # noqa: E402
from jarvis_core.profiles import ProfilesStore  # noqa: E402
from jarvis_core.recovery_fs import Directory, PinnedFile  # noqa: E402
from jarvis_core.state import StateStore  # noqa: E402

FORMAT = "jarvis-mac-browser-fixture/1"
_TEMP = Path("/private/tmp" if sys.platform == "darwin" else "/tmp")
SEEDS = {"editar.txt": b"ANTES\n", "mover.txt": b"MOVER\n"}
DOWNLOAD_BYTES = b"JARVIS DOWNLOAD 7319\n"
WORKSPACE_FILES = ("editar.txt", "mover.txt", "movido.txt", "nuevo.txt", "descarga-fase-a.txt")
CAPTURE_NAMES = ("captura.png", "captura.jpg", "screenshot.png", "frame.jpg", "capture.png")
MAX_ANSWER_BYTES = 24576

# Independently authored protocol peer. No Jarvis import, network, credentials,
# official SDK execution, prompt history storage, or automatic effect replay.
PEER = r"""
import base64, hashlib, json, os, select, stat, sys, time
from pathlib import Path
from uuid import uuid4
ROOT = Path(__file__).absolute().parent
MAX_ANSWER = 24576
def check_root():
    info=ROOT.lstat()
    assert stat.S_ISDIR(info.st_mode) and info.st_uid==os.getuid()
    assert stat.S_IMODE(info.st_mode)==0o700
    assert [info.st_dev,info.st_ino]==manifest['root_identity']
def encoded(value): return json.dumps(value,ensure_ascii=False,separators=(',',':')).encode()
def digest(raw): return {'bytes':len(raw),'sha256':hashlib.sha256(raw).hexdigest()}
def image_metadata(value):
    raw=value.encode() if isinstance(value,str) else encoded(value)
    result={'redacted_image':True,**digest(raw)}
    if isinstance(value,str) and value.startswith('data:image/'):
        header,separator,payload=value.partition(',')
        result['media_type']=header[5:].split(';')[0][:64]
        if separator and header.endswith(';base64'):
            try: result.update(digest(base64.b64decode(payload,validate=True)))
            except (ValueError,base64.binascii.Error): result['invalid_base64']=True
    return result
def safe(value,depth=0):
    if depth>12: return {'truncated':True}
    if isinstance(value,str):
        if 'data:image/' in value or value.startswith('/9j/'):
            return image_metadata(value)
        return value if len(value)<=2048 else value[:2048]+' [truncado]'
    if isinstance(value,list): return [safe(v,depth+1) for v in value[:160]]
    if isinstance(value,dict):
        result={}
        for key,item in list(value.items())[:192]:
            key=str(key)[:128]
            if key.lower() in {'bytes_base64','imageurl','image_url','data_url'}:
                result[key]=image_metadata(item)
            else: result[key]=safe(item,depth+1)
        return result
    return value if value is None or type(value) in (bool,int,float) else str(type(value))
def record(kind,**values):
    check_root()
    row=encoded({'kind':kind,'at_ns':time.time_ns(),**safe(values)})+b'\n'
    if len(row)>65536: row=encoded({'kind':kind,'record_truncated':True,**digest(row)})+b'\n'
    fd=os.open(ROOT/'wire.jsonl',os.O_WRONLY|os.O_CREAT|os.O_APPEND|os.O_NOFOLLOW,0o600)
    try: os.write(fd,row)
    finally: os.close(fd)
def load_private(path):
    fd=os.open(path,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK)
    try:
        info=os.fstat(fd)
        assert stat.S_ISREG(info.st_mode) and info.st_uid==os.getuid()
        assert stat.S_IMODE(info.st_mode)==0o600 and info.st_nlink==1 and info.st_size<=1048576
        return json.loads(os.read(fd,info.st_size+1))
    finally: os.close(fd)
def save_threads():
    check_root()
    pending=ROOT/('peer-threads-'+uuid4().hex+'.pending')
    fd=os.open(pending,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
    try: os.write(fd,encoded(threads))
    finally: os.close(fd)
    os.replace(pending,ROOT/'peer-threads.json')
def emit(value): print(json.dumps(value,ensure_ascii=False),flush=True)
def reply(message,result):
    if 'id' in message: emit({'id':message['id'],'result':result})
def finish(state,result=None,status='completed'):
    if state['finished']: return
    state['finished']=True
    answer=json.dumps(safe(result),ensure_ascii=False,separators=(',',':'))
    if len(answer.encode())>MAX_ANSWER:
        result={'fixture':True,'output_truncated':True,'receipt':safe(result.get('receipt',{})),
            'output_digest':digest(encoded(result.get('output')))}
        answer=json.dumps(result,ensure_ascii=False,separators=(',',':'))
    if status=='completed':
        emit({'method':'item/agentMessage/delta','params':{'threadId':state['thread'],
            'turnId':state['turn'],'itemId':'answer','delta':answer}})
        emit({'method':'item/completed','params':{'threadId':state['thread'],
            'turnId':state['turn'],'item':{'type':'agentMessage','id':'answer','text':answer}}})
    emit({'method':'turn/completed','params':{'threadId':state['thread'],
        'turnId':state['turn'],'turn':{'id':state['turn'],'status':status}}})
    record('turn.finished',thread=state['thread'],turn=state['turn'],status=status,
        answer_digest=digest(answer.encode()))
def tool_result(message,state):
    result=message.get('result',{})
    items=result.get('contentItems',[]) if isinstance(result,dict) else []
    receipt={}
    frames=[]
    for item in items:
        if not isinstance(item,dict): continue
        if item.get('type')=='inputImage': frames.append(image_metadata(item.get('imageUrl','')))
        elif item.get('type')=='inputText' and not receipt:
            try:
                candidate=json.loads(item.get('text',''))
                if isinstance(candidate,dict): receipt=candidate
            except (ValueError,TypeError): pass
    output=receipt.get('output')
    if isinstance(output,str):
        try: output=json.loads(output)
        except ValueError: pass
    summary={key:receipt.get(key) for key in ('tool_name','succeeded','verified','error',
        'effect_status','operation_id','error_type') if key in receipt}
    record('tool.replied',thread=state['thread'],turn=state['turn'],
        request_id=message.get('id'),tool=state['tool'],receipt=summary,
        success=result.get('success') if isinstance(result,dict) else False,
        reply_digest=digest(encoded(result)),frames=frames)
    finish(state,{'fixture':True,'independent_verification_required':True,
        'transport_success':result.get('success') if isinstance(result,dict) else False,
        'receipt':summary,'output':output,'frames':frames})
def propose(state,prompt):
    global request_number
    try:
        assert prompt.startswith('HERRAMIENTA ')
        proposal=json.loads(prompt[len('HERRAMIENTA '):])
        assert isinstance(proposal,dict) and set(proposal)=={'name','arguments'}
        name,args=proposal['name'],proposal['arguments']
        assert isinstance(name,str) and name in threads[state['thread']]['tools']
        assert isinstance(args,dict) and len(encoded(args))<=131072
    except (AssertionError,ValueError,TypeError):
        finish(state,{'fixture':True,'error':'comando_sintetico_invalido_o_no_ofrecido'}); return
    request_number+=1
    state['tool']=name
    callbacks[request_number]=state
    record('tool.proposed',request_id=request_number,thread=state['thread'],turn=state['turn'],
        tool=name,arguments_digest=digest(encoded(args)))
    emit({'id':request_number,'method':'item/tool/call','params':{
        'threadId':state['thread'],'turnId':state['turn'],
        'callId':'jarvis-mac-browser-call-'+str(request_number),
        'namespace':None,'tool':name,'arguments':args}})
def consume(message):
    method=message.get('method')
    params=message.get('params',{})
    if method is None:
        state=callbacks.pop(message.get('id'),None)
        if state and not state['finished']: tool_result(message,state)
        else: record('reply.ignored',request_id=message.get('id'))
        return
    record('received',method=method,params_digest=digest(encoded(params)))
    result={}
    if method=='initialize':
        assert params['clientInfo']['name']=='jarvis'
    elif method=='account/read':
        result={'account':{'type':'chatgpt'},'authMode':'chatgpt'}
    elif method=='account/login/start':
        emit({'id':message['id'],'error':{'code':-32000,
            'message':'Peer sintético: sin acceso real'}})
        return
    elif method=='thread/start':
        assert params['sandbox']=='read-only' and params['approvalPolicy']=='untrusted'
        identity='jarvis-mac-browser-thread-'+uuid4().hex
        threads[identity]={'tools':[tool['name'] for tool in params.get('dynamicTools',[])]}
        save_threads(); result={'thread':{'id':identity}}
    elif method=='thread/resume':
        assert params['threadId'] in threads
        result={'thread':{'id':params['threadId']}}
    elif method=='thread/inject_items':
        assert params['threadId'] in threads
        record('context.injected',thread=params['threadId'],items=len(params.get('items',[])))
    elif method=='turn/start':
        tid=params['threadId']
        assert tid in threads and (tid not in active or active[tid]['finished'])
        parts=params.get('input',[])
        prompt=parts[0].get('text','') if parts else ''
        state={'thread':tid,'turn':'jarvis-mac-browser-turn-'+uuid4().hex,'finished':False}
        active[tid]=state; result={'turn':{'id':state['turn']}}
    elif method=='turn/steer':
        emit({'id':message['id'],'error':{'code':-32000,'message':'Peer sintético: sin steer'}})
        return
    reply(message,result)
    if method=='turn/interrupt':
        state=active.get(params['threadId'])
        if state: finish(state,status='interrupted')
    elif method=='turn/start':
        record('turn.started',thread=tid,turn=state['turn'],prompt_digest=digest(prompt.encode()),
            offered_tools=threads[tid]['tools'])
        propose(state,prompt)

manifest=load_private(ROOT/'fixture.json')
assert manifest['format']=='jarvis-mac-browser-fixture/1' and manifest['synthetic_only'] is True
assert manifest['root']==str(ROOT)
check_root()
threads_path=ROOT/'peer-threads.json'
threads=load_private(threads_path) if threads_path.exists() else {}
active,callbacks={},{}
# IDs remain disjoint across process restarts, not a replay token.
request_number=int.from_bytes(os.urandom(6),'big')
record('peer.started',pid=os.getpid(),synthetic=True)
buffer=b''
while True:
    ready,_,_=select.select([0],[],[],.05)
    if not ready: continue
    data=os.read(0,65536)
    if not data: break
    buffer+=data
    assert len(buffer)<=2*1024*1024
    while b'\n' in buffer:
        line,buffer=buffer.split(b'\n',1)
        if line: consume(json.loads(line))
"""


def _sha(data):
    return hashlib.sha256(data).hexdigest()


def _write(path, data, mode=0o600):
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, mode)
    with os.fdopen(descriptor, "wb") as output:
        output.write(data)


def _read(path, maximum=64 * 1024 * 1024, *, executable=False):
    with Directory(path.parent) as parent:
        if not executable:
            with PinnedFile(parent, path.name, maximum) as selected:
                return selected.read(lambda: None)
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
                or before.st_size > maximum
            ):
                raise ValueError("fixture_executable_invalid")
            data = os.read(descriptor, before.st_size + 1)
            after = os.stat(path.name, dir_fd=parent.fd, follow_symlinks=False)
            keys = ("st_dev", "st_ino", "st_size", "st_mtime_ns", "st_ctime_ns", "st_mode")
            if any(getattr(before, key) != getattr(after, key) for key in keys):
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
        or not root.name.startswith("jarvis-mac-browser-")
        or ".." in root.parts
    ):
        raise ValueError("fixture_new_private_temp_root_required")
    if new:
        root.mkdir(mode=0o700)
    with Directory(root):
        pass
    return root


def _manifest(root):
    value = json.loads(_read(root / "fixture.json", 1024 * 1024))
    with (
        Directory(root) as selected_root,
        Directory(root / "profile") as profile,
        Directory(root / "workspace") as workspace,
    ):
        if (
            value.get("format") != FORMAT
            or value.get("root") != str(root)
            or value.get("synthetic_only") is not True
            or value.get("root_identity")
            != [selected_root.identity.device, selected_root.identity.inode]
            or value.get("profile_identity") != [profile.identity.device, profile.identity.inode]
            or value.get("workspace_identity")
            != [workspace.identity.device, workspace.identity.inode]
        ):
            raise ValueError("fixture_identity_mismatch")
    return value


def command(name, arguments):
    return "HERRAMIENTA " + json.dumps({"name": name, "arguments": arguments}, ensure_ascii=False)


def prepare(path):
    root = _root(path, new=True)
    profile, workspace = root / "profile", root / "workspace"
    profile.mkdir(mode=0o700)
    workspace.mkdir(mode=0o700)
    for name, data in SEEDS.items():
        _write(workspace / name, data)
    info = workspace.stat()
    ProfileConfig(profile).set_workspace(workspace, (info.st_dev, info.st_ino))
    ProfilesStore(profile / "profiles")
    store = StateStore(profile / "jarvis.sqlite3")
    try:
        session = store.create_session("PRUEBA HERRAMIENTAS MAC Y NAVEGADOR")
    finally:
        store.close()
    peer = root / "jarvis_mac_browser_peer.py"
    _write(peer, PEER.encode())
    executable = root / "jarvis-mac-browser-peer"
    launcher = "#!/bin/sh\nexec " + shlex.quote(os.path.realpath(sys.executable))
    launcher += " -I -S -B -u " + shlex.quote(str(peer)) + ' "$@"\n'
    _write(executable, launcher.encode(), 0o700)
    patch = (
        "--- a/editar.txt\n+++ b/editar.txt\n@@ -1 +1 @@\n-ANTES\n+DESPUES\n"
        "--- a/mover.txt\n+++ b/movido.txt\n"
        "--- /dev/null\n+++ b/nuevo.txt\n@@ -0,0 +1 @@\n+NUEVO JARVIS\n"
    )
    profile_info = profile.stat()
    root_info = root.stat()
    result = {
        "format": FORMAT,
        "synthetic_only": True,
        "runtime_provider": "own_stdio_peer",
        "real_account_required": False,
        "reference_code_copied": False,
        "root": str(root),
        "profile": str(profile),
        "workspace": str(workspace),
        "root_identity": [root_info.st_dev, root_info.st_ino],
        "profile_identity": [profile_info.st_dev, profile_info.st_ino],
        "workspace_identity": [info.st_dev, info.st_ino],
        "session_id": session.ref.session_id,
        "codex_fixture": str(executable),
        "peer_sha256": _sha(PEER.encode()),
        "executable_sha256": _sha(launcher.encode()),
        "seed_files": {
            name: {"bytes": len(data), "sha256": _sha(data)} for name, data in SEEDS.items()
        },
        "download": {
            "path": "descarga-fase-a.txt",
            "bytes": len(DOWNLOAD_BYTES),
            "sha256": _sha(DOWNLOAD_BYTES),
        },
        "desktop_key_prepared": False,
        "key_bootstrap_owner": "native_ServiceProcess",
        "prompts": {
            "browser_open": command("browser_open", {"url": "ORIGIN/"}),
            "browser_search": command("browser_search", {"query": "JARVIS CITA 7319"}),
            "desktop_resolve": command("desktop_resolve", {"bundle_id": "BUNDLE_ID_PROPIO"}),
            "workspace_patch": command(
                "workspace_patch",
                {
                    "patch": patch,
                    "expected_sha256": {
                        "editar.txt": _sha(SEEDS["editar.txt"]),
                        "mover.txt": _sha(SEEDS["mover.txt"]),
                        "movido.txt": None,
                        "nuevo.txt": None,
                    },
                },
            ),
            "pty": command(
                "process_run",
                {"argv": [os.path.realpath(sys.executable), "-I", "-S", "-q"], "pty": True},
            ),
        },
        "search_template": "ORIGIN/search?q={query}",
        "observe_workspace_names": list(WORKSPACE_FILES),
    }
    _write(root / "fixture.json", json.dumps(result, ensure_ascii=False, indent=2).encode())
    return result


PAGE = b"""<!doctype html><html lang="es"><meta charset="utf-8"><title>Jarvis QA propia</title>
<h1>Prueba artificial de navegador</h1><label>Texto de prueba
<input id="texto" aria-label="Texto de prueba"></label><label>Opcion de prueba
<select id="opcion" aria-label="Opcion de prueba"><option value="uno">Uno</option>
<option value="dos">Dos</option></select></label><button id="accion">Aplicar prueba</button>
<p id="marcador" aria-live="polite">SIN CAMBIOS</p><a href="/document">Documento de prueba</a>
<a href="/download" download>Descargar prueba</a><script>
let counter=0;document.getElementById('accion').addEventListener('click',async()=>{
counter++;const text=document.getElementById('texto').value;
const choice=document.getElementById('opcion').value;
document.getElementById('marcador').textContent='JARVIS WEB 7319: '+text+' / '+choice+' / '+counter;
await fetch('/confirm?'+new URLSearchParams({text,choice,count:String(counter)}));});
</script></html>"""


class FixtureHTTPServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = False

    def __init__(self, root, port=0):
        self.root = _root(root)
        self.manifest = _manifest(self.root)
        with Directory(self.root) as selected:
            self.root_identity = selected.identity
        self.log_lock = threading.Lock()
        super().__init__(("127.0.0.1", port), FixtureHandler)

    def record(self, **values):
        raw = json.dumps({"at_ns": time.time_ns(), **values}, ensure_ascii=False).encode() + b"\n"
        with self.log_lock, Directory(self.root, expected=self.root_identity) as root:
            root.check()
            descriptor = os.open(
                "http.jsonl",
                os.O_WRONLY | os.O_CREAT | os.O_APPEND | os.O_NOFOLLOW,
                0o600,
                dir_fd=root.fd,
            )
            try:
                info = os.fstat(descriptor)
                if (
                    not stat.S_ISREG(info.st_mode)
                    or info.st_uid != os.getuid()
                    or info.st_nlink != 1
                    or stat.S_IMODE(info.st_mode) != 0o600
                ):
                    raise ValueError("fixture_http_log_unsafe")
                os.write(descriptor, raw)
            finally:
                os.close(descriptor)


class FixtureHandler(BaseHTTPRequestHandler):
    server_version = "JarvisSyntheticHTTP/1"

    def log_message(self, *_):
        pass  # No headers, account data, request targets or console access log.

    def _respond(self, status, data, media_type="text/html; charset=utf-8", download=False):
        self.send_response(status)
        self.send_header("Content-Type", media_type)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        if download:
            self.send_header("Content-Disposition", 'attachment; filename="jarvis-fixture.txt"')
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        if len(self.path.encode()) > 4096:
            self._respond(414, b"Solicitud demasiado larga", "text/plain")
            return
        parsed = urlsplit(self.path)
        if parsed.netloc:
            self._respond(400, b"Destino invalido", "text/plain")
            return
        try:
            query = parse_qs(parsed.query, max_num_fields=8)
        except ValueError:
            self._respond(400, b"Consulta invalida", "text/plain")
            return
        if parsed.path == "/confirm":
            values = {key: query.get(key, [""])[0][:256] for key in ("text", "choice", "count")}
            if values["choice"] not in {"uno", "dos"} or not values["count"].isdigit():
                self._respond(400, b"Confirmacion invalida", "text/plain")
                return
            self.server.record(route="confirm", **values)
            self._respond(
                200, json.dumps({"confirmed": True, **values}).encode(), "application/json"
            )
        elif parsed.path == "/search":
            query_text = query.get("q", [""])[0][:256]
            self.server.record(route="search", query=query_text)
            content = '<!doctype html><meta charset="utf-8"><title>Busqueda sintetica</title>'
            content += "<h1>Resultados para " + html.escape(query_text) + "</h1>"
            content += '<a href="/document">Referencia sintetica Jarvis</a>'
            content += "<p>DOCUMENTO SINTETICO JARVIS 7319</p>"
            self._respond(200, content.encode())
        elif parsed.path == "/download":
            self.server.record(route="download")
            self._respond(200, DOWNLOAD_BYTES, "text/plain; charset=utf-8", download=True)
        elif parsed.path == "/forbidden":
            self.server.record(route="forbidden")
            self._respond(200, b"DECOY: NO DEBE CONSULTARSE", "text/plain")
        elif parsed.path == "/document":
            self.server.record(route="document")
            self._respond(
                200,
                b"<!doctype html><title>Documento sintetico</title>"
                b'<h1>DOCUMENTO SINTETICO JARVIS 7319</h1><a href="/">Volver</a>',
            )
        elif parsed.path == "/" and not parsed.netloc:
            self.server.record(route="page")
            self._respond(200, PAGE)
        else:
            self.server.record(route="not_found")
            self._respond(404, b"Ruta sintetica inexistente", "text/plain")


def make_server(path, port=0):
    if type(port) is not int or not 0 <= port <= 65535:
        raise ValueError("fixture_port_invalid")
    server = FixtureHTTPServer(path, port)
    result = {
        "format": FORMAT,
        "synthetic_only": True,
        "pid": os.getpid(),
        "origin": "http://127.0.0.1:" + str(server.server_port),
        "port": server.server_port,
    }
    try:
        _write(server.root / "server.json", json.dumps(result, indent=2).encode())
    except BaseException:
        server.server_close()
        raise
    return server, result


def _projection(root):
    journal = root / "profile" / "jarvis.sqlite3-journal"
    if journal.exists() and _read(journal):
        raise ValueError("fixture_sqlite_busy")
    raw = _read(root / "profile" / "jarvis.sqlite3")
    with closing(sqlite3.connect(":memory:")) as database:
        database.deserialize(raw)
        database.execute("PRAGMA trusted_schema=OFF")
        database.execute("PRAGMA query_only=ON")
        if database.execute("PRAGMA quick_check").fetchall() != [("ok",)]:
            raise ValueError("fixture_sqlite_invalid")
        database.row_factory = sqlite3.Row
        projection = {
            table: [dict(row) for row in database.execute("SELECT * FROM " + table)]
            for table in ("sessions", "messages", "runs", "events")
        }
    return raw, projection


def _redact(value, depth=0):
    if depth > 12:
        return {"truncated": True}
    if isinstance(value, str):
        if "data:image/" in value or value.startswith("/9j/"):
            return {
                "redacted_image": True,
                "bytes": len(value.encode()),
                "sha256": _sha(value.encode()),
            }
        return value[:2048]
    if isinstance(value, list):
        return [_redact(item, depth + 1) for item in value[:160]]
    if isinstance(value, dict):
        return {
            str(key)[:128]: (
                {"redacted_image": True, "sha256": _sha(json.dumps(item).encode())}
                if str(key).lower() in {"bytes_base64", "imageurl", "image_url", "data_url"}
                else _redact(item, depth + 1)
            )
            for key, item in list(value.items())[:192]
        }
    return value


def _rows(root, name):
    return (
        [json.loads(row) for row in _read(root / name).splitlines()]
        if (root / name).exists()
        else []
    )


def observe(path):
    root = _root(path)
    manifest = _manifest(root)
    raw, projection = _projection(root)
    wire = _rows(root, "wire.jsonl")
    http = _rows(root, "http.jsonl")
    proposals = [row for row in wire if row["kind"] == "tool.proposed"]
    replies = [row for row in wire if row["kind"] == "tool.replied"]
    files = {}
    for name in WORKSPACE_FILES:
        path = root / "workspace" / name
        data = _read(path) if path.exists() else None
        files[name] = {
            "exists": data is not None,
            "bytes": len(data) if data is not None else None,
            "sha256": _sha(data) if data is not None else None,
        }
    receipts = []
    for run in projection["runs"]:
        receipt = json.loads(run["receipt_json"]) if run.get("receipt_json") else {}
        for action in receipt.get("action_receipts", []):
            output = action.get("output")
            if isinstance(output, str):
                try:
                    output = json.loads(output)
                except ValueError:
                    pass
            receipts.append(_redact({**action, "output": output}))
    result = {
        "format": FORMAT,
        "read_only": True,
        "profile": str(root / "profile"),
        "session_id": manifest["session_id"],
        "sessions_count": len(projection["sessions"]),
        "messages_count": len(projection["messages"]),
        "runs_count": len(projection["runs"]),
        "run_statuses": dict(Counter(row["status"] for row in projection["runs"])),
        "native_tool_receipts": receipts,
        "method_counts": dict(Counter(row["method"] for row in wire if row["kind"] == "received")),
        "sdk_launches": sum(row["kind"] == "peer.started" for row in wire),
        "turns_started": sum(row["kind"] == "turn.started" for row in wire),
        "turns_finished": sum(row["kind"] == "turn.finished" for row in wire),
        "tool_proposals": proposals,
        "tool_replies": replies,
        "one_proposal_per_turn": all(
            count == 1 for count in Counter(row["turn"] for row in proposals).values()
        ),
        "unique_request_ids": len({row["request_id"] for row in proposals}) == len(proposals),
        "image_inputs": [image for row in replies for image in row.get("frames", [])],
        "no_image_carrier_in_sqlite": b"data:image/" not in raw and b"/9j/" not in raw,
        "sqlite_sha256": _sha(raw),
        "workspace_files": files,
        "download_matches": files["descarga-fase-a.txt"]["sha256"]
        == manifest["download"]["sha256"],
        "capture_files_present": [
            str(parent / name)
            for parent in (root, root / "workspace")
            for name in CAPTURE_NAMES
            if (parent / name).exists()
        ],
        "http_counts": dict(Counter(row["route"] for row in http)),
        "http_confirmations": [row for row in http if row["route"] == "confirm"],
        "http_searches": [row for row in http if row["route"] == "search"],
        "peer_unchanged": _sha(_read(root / "jarvis_mac_browser_peer.py"))
        == manifest["peer_sha256"],
        "launcher_unchanged": _sha(
            _read(root / "jarvis-mac-browser-peer", maximum=65536, executable=True)
        )
        == manifest["executable_sha256"],
        "server": json.loads(_read(root / "server.json"))
        if (root / "server.json").exists()
        else None,
    }
    encoded = json.dumps(result, ensure_ascii=False).encode()
    if len(encoded) > 2 * 1024 * 1024:
        raise ValueError("fixture_observation_limit")
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("operation", choices=("prepare", "serve", "observe"))
    parser.add_argument("root", type=Path)
    parser.add_argument("--port", type=int, default=0)
    args = parser.parse_args()
    if args.operation == "serve":
        server, value = make_server(args.root, args.port)
        print(json.dumps(value), flush=True)
        try:
            server.serve_forever()
        finally:
            server.server_close()
    else:
        value = prepare(args.root) if args.operation == "prepare" else observe(args.root)
        print(json.dumps(value, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
