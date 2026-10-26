"""Stopped synthetic journals authored by SQLite itself, not recovery doubles."""

import json
import os
import subprocess
import sys

from wal_fixture import NAMES, TABLES

from jarvis_core.memory import MemoryScope, MemoryStore
from jarvis_core.scheduler import ScheduleSpec, ScheduleStore
from jarvis_core.state import StateStore

_WRITER = """
import json, os, sqlite3, sys, uuid
from pathlib import Path
root, roles, mode, hot = Path(sys.argv[1]), json.loads(sys.argv[2]), sys.argv[3], sys.argv[4]=='1'
connections=[]
for role in roles:
    name={'state':'jarvis.sqlite3','memory':'memory.sqlite3','schedules':'schedules.sqlite3'}[role]
    table={'state':'sessions','memory':'documents','schedules':'schedules'}[role]
    db=sqlite3.connect(str(root/name),isolation_level=None)
    connections.append(db)
    db.execute('PRAGMA synchronous=FULL')
    row=list(db.execute('SELECT * FROM '+table+' LIMIT 1').fetchone())
    db.execute('BEGIN IMMEDIATE')
    for index in range(int(sys.argv[5])):
        copied=list(row);copied[0]=uuid.uuid4().hex
        if role=='memory':
            copied[2]='journal-'+str(index)
        db.execute('INSERT INTO '+table+' VALUES('+','.join('?' for _ in copied)+')',copied)
        if role=='memory':
            text=copied[6]
            cursor=db.execute('INSERT INTO chunks VALUES(NULL,?,?,?,?,?)',
                (copied[0],0,0,len(text.encode()),text))
            db.execute('INSERT INTO chunk_fts(rowid,text) VALUES(?,?)',(cursor.lastrowid,text))
    db.execute('COMMIT')
    assert db.execute('PRAGMA journal_mode='+mode).fetchone()[0]==mode.lower()
    db.execute('PRAGMA cache_size=1')
    db.execute('PRAGMA cache_spill=ON')
    db.execute('BEGIN IMMEDIATE')
    db.execute('UPDATE '+table+' SET title=?',('JARVIS JOURNAL NEW '+role,))
    if not hot:
        db.execute('COMMIT')
os._exit(0)
"""


def make_journal(root, roles=("state", "memory", "schedules"), *, mode="DELETE", hot=True,
                 duplicates=256):
    root.mkdir(mode=0o700, exist_ok=True)
    if "state" in roles:
        with StateStore(root / NAMES["state"]) as store:
            store.create_session("JARVIS JOURNAL BASE state")
    if "memory" in roles:
        with MemoryStore(root / NAMES["memory"]) as store:
            store.upsert(MemoryScope("jarvis", "f" * 64), "journal-fixture",
                         "PRUEBA JOURNAL LOCAL", title="JARVIS JOURNAL BASE memory",
                         source_uri="jarvis://synthetic-journal")
    if "schedules" in roles:
        store = ScheduleStore(root / NAMES["schedules"])
        try:
            store.create("JARVIS JOURNAL BASE schedules", "PRUEBA JOURNAL", "jarvis", "f" * 64,
                         ScheduleSpec("once", at=4_000_000_000), enabled=False)
        finally:
            store.close()
    result = subprocess.run([os.path.realpath(sys.executable), "-I", "-c", _WRITER,
                             str(root), json.dumps(roles), mode, "1" if hot else "0",
                             str(duplicates)],
                            capture_output=True, text=True, timeout=20)
    assert result.returncode == 0, result.stderr
    for role in roles:
        path = root / (NAMES[role] + "-journal")
        assert path.exists()
        if hot:
            assert path.read_bytes()[:8] == b"\xd9\xd5\x05\xf9\x20\xa1\x63\xd7"
    return {role: {"title": ("JARVIS JOURNAL BASE " if hot else "JARVIS JOURNAL NEW ") + role,
                   "table": TABLES[role], "rows": duplicates + 1} for role in roles}
