"""Own, stopped WAL fixtures; never opens an owner profile or calls a provider."""

import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

from jarvis_core.memory import MemoryScope, MemoryStore
from jarvis_core.scheduler import ScheduleSpec, ScheduleStore
from jarvis_core.state import StateStore

NAMES = {"state": "jarvis.sqlite3", "memory": "memory.sqlite3", "schedules": "schedules.sqlite3"}
TABLES = {"state": "sessions", "memory": "documents", "schedules": "schedules"}
SUFFIXES = ("", "-wal", "-shm", "-journal")

_WAL_WRITER = """
import json, os, sqlite3, sys
from pathlib import Path
root, rows = Path(sys.argv[1]), json.loads(sys.argv[2])
connections = []
for row in rows:
    db = sqlite3.connect(str(root / row['name']), isolation_level=None)
    connections.append(db)
    assert db.execute('PRAGMA journal_mode=WAL').fetchone()[0] == 'wal'
    db.execute('PRAGMA wal_autocheckpoint=0')
    db.execute('PRAGMA synchronous=FULL')
    for text in row['titles']:
        db.execute('BEGIN IMMEDIATE')
        db.execute('UPDATE ' + row['table'] + ' SET title=?', (text,))
        db.execute('COMMIT')
os._exit(0)
"""


def make_wal(root, roles=("state", "memory", "schedules"), *, shm=True):
    """Return marker metadata after the own writer exited without checkpoint.

    The stores are authored and closed first. Only a separate stdlib-only child
    switches those synthetic SQLite files to WAL and commits three updates.
    os._exit deliberately leaves crash residue; it runs before any host lease.
    """
    root = Path(root)
    root.mkdir(mode=0o700, exist_ok=True)
    markers = {role: {"base": "JARVIS WAL BASE " + role,
                      "committed": "JARVIS WAL COMMIT TRES " + role}
               for role in roles}
    if "state" in roles:
        with StateStore(root / NAMES["state"]) as store:
            store.create_session(markers["state"]["base"])
    if "memory" in roles:
        with MemoryStore(root / NAMES["memory"]) as store:
            store.upsert(MemoryScope("jarvis", "f" * 64), "wal-fixture",
                         "PRUEBA WAL LOCAL ÁMBAR", title=markers["memory"]["base"],
                         source_uri="jarvis://synthetic-wal")
    if "schedules" in roles:
        store = ScheduleStore(root / NAMES["schedules"])
        try:
            store.create(markers["schedules"]["base"], "PRUEBA WAL LOCAL", "jarvis", "f" * 64,
                         ScheduleSpec("once", at=4_000_000_000), enabled=False)
        finally:
            store.close()
    rows = [{"name": NAMES[role], "table": TABLES[role],
             "titles": ["JARVIS WAL COMMIT UNO " + role,
                        "JARVIS WAL COMMIT DOS " + role, markers[role]["committed"]]}
            for role in roles]
    result = subprocess.run([os.path.realpath(sys.executable), "-I", "-c", _WAL_WRITER,
                             str(root), json.dumps(rows)],
                            capture_output=True, text=True, timeout=15)
    assert result.returncode == 0, result.stderr
    for role in roles:
        path = root / NAMES[role]
        assert path.read_bytes()[18:20] == b"\2\2"
        assert (root / (NAMES[role] + "-wal")).stat().st_size > 32
        if not shm:
            (root / (NAMES[role] + "-shm")).unlink()
    return markers


def oracle(root):
    result = {}
    for name in NAMES.values():
        for suffix in SUFFIXES:
            path = Path(root) / (name + suffix)
            try:
                info = path.lstat()
            except FileNotFoundError:
                continue
            result[path.name] = (info.st_dev, info.st_ino, info.st_uid, info.st_mode, info.st_nlink,
                                 info.st_size, info.st_mtime_ns, info.st_ctime_ns,
                                 hashlib.sha256(path.read_bytes()).hexdigest())
    return result
