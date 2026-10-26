#!/usr/bin/env python3
"""Perfiles sintéticos propios para admisión, memoria y rutas restauradas.

Sólo prepara raíces temporales nuevas: no inicia servicios, importa cuentas,
activa perfiles ni decide tarjetas. Las decisiones de las operaciones que se
cualifican se toman después por la app/autoridad real. La preparación del corpus
usa los motores propios con contextos sintéticos explícitos, fuera del producto.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sqlite3
import stat
import sys
import threading
import time
from contextlib import closing
from pathlib import Path
from uuid import uuid4

_REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO / "src"))

from jarvis_core import memory, profiles, scheduler, skills, state, telegram  # noqa: E402
from jarvis_core.recovery import (  # noqa: E402
    JsonSource,
    NewTarget,
    RecoveryContext,
    SourceBundle,
    SqliteSource,
    canonical,
    create_backup,
    restore_new,
    verify_backup,
)
from jarvis_core.recovery_fs import Directory, FileIdentity, PinnedFile  # noqa: E402

_TEMP = Path("/private/tmp" if sys.platform == "darwin" else "/tmp")
_FORMAT = "jarvis-activation-reconciliation-fixture/1"
_ORIGIN_WORKSPACE_ID = hashlib.sha256(b"jarvis-own-synthetic-original-workspace").hexdigest()
CASES = ("deny", "cancel", "expiry", "allow", "busy", "tamper", "memory_channels")
_SQL = {"state": "jarvis.sqlite3", "memory": "memory.sqlite3",
        "schedules": "schedules.sqlite3"}
_TABLES = {"state": ("sessions", "messages", "runs", "events"),
           "memory": ("scopes", "documents", "chunks", "metadata"),
           "schedules": ("schedules", "occurrences")}


def _sha(raw):
    return hashlib.sha256(raw).hexdigest()


def _write(path, raw):
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(descriptor, "wb") as stream:
        stream.write(raw)


def _context():
    # Corpus construction only, never an authorization of a live profile effect.
    return RecoveryContext(uuid4().hex, "synthetic_activation_source", 1,
                           lambda _stage, _write=False: None, threading.Event(),
                           time.monotonic() + 30)


def _target(root, name):
    return NewTarget(root, FileIdentity.of(root.stat()), name,
                     forbidden_roots=(root / "source", root / "host"))


def _read(path):
    with Directory(path.parent) as directory, PinnedFile(
            directory, path.name, 64 * 1024 * 1024) as source:
        raw = source.read(lambda: None)
        info = os.stat(path.name, dir_fd=directory.fd, follow_symlinks=False)
        source.check()
        return raw, [info.st_dev, info.st_ino, info.st_uid, stat.S_IMODE(info.st_mode),
                     info.st_nlink, info.st_size, info.st_mtime_ns, info.st_ctime_ns]


def _files(profile):
    # Fixed own corpus files only. No credential discovery, directory recursion,
    # SQLite path opens or reading the newly published admission body.
    names = [*_SQL.values(), "configuration.json", "restore_record.json",
             "profiles/agents.json", "profiles/skills.json", "telegram/telegram.json"]
    result = {}
    for name in names:
        raw, metadata = _read(profile / name)
        result[name] = {"sha256": _sha(raw), "metadata": metadata}
    return result


def _projection(profile):
    result = {}
    for role, name in _SQL.items():
        raw, _ = _read(profile / name)
        with closing(sqlite3.connect(":memory:")) as connection:
            connection.deserialize(raw)
            connection.execute("PRAGMA trusted_schema=OFF")
            connection.execute("PRAGMA query_only=ON")
            if connection.execute("PRAGMA quick_check").fetchall() != [("ok",)]:
                raise ValueError("fixture_sqlite_invalid")
            result[role] = {
                "application_id": connection.execute("PRAGMA application_id").fetchone()[0],
                "schema": connection.execute("PRAGMA user_version").fetchone()[0],
                "tables": {table: sorted(
                    [list(row) for row in connection.execute("SELECT * FROM " + table)],
                    key=canonical) for table in _TABLES[role]},
            }
    return result


def _source(root):
    source = root / "source"
    source.mkdir(mode=0o700)
    connections = []
    for role, module in (("state", state), ("memory", memory), ("schedules", scheduler)):
        path = source / _SQL[role]
        connection = sqlite3.connect(path, isolation_level=None, check_same_thread=False)
        path.chmod(0o600)
        connection.executescript(module._SCHEMA)
        connection.execute(f"PRAGMA application_id={module._APPLICATION_ID}")
        connection.execute(f"PRAGMA user_version={module._SCHEMA_VERSION}")
        connections.append(connection)
    chat_ids = ("a" * 32, "b" * 32)
    for index, session in enumerate(chat_ids):
        connections[0].execute("INSERT INTO sessions VALUES(?,?,0,1,NULL,'jarvis',NULL)",
                               (session, f"Chat sintético de ruta {index + 1}"))
        connections[0].execute("INSERT INTO messages VALUES(?,?,1,'user',?,NULL,1)",
                               (str(index + 1) * 32, session,
                                "HISTORIA SINTÉTICA; NO REPETIR NI ENVIAR"))
    # Real service workspaces use 64-hex identities. A shorter opaque core-only
    # label would pass the snapshot inspector but fail the native schedule DTO.
    scope = json.dumps(["jarvis", _ORIGIN_WORKSPACE_ID, None], separators=(",", ":"))
    connections[1].execute("INSERT INTO metadata VALUES('search_mode','lexical_scan')")
    connections[1].execute("INSERT INTO scopes VALUES(?,2)", (scope,))
    for index, text in enumerate(("TOPACIO ACTIVACIÓN 4826", "MEMORIA CANALES 7319")):
        identity = ("d" if index == 0 else "f") * 32
        connections[1].execute("INSERT INTO documents VALUES(?,?,?,?,?,?,?,?,?,?,?,?)", (
            identity, scope, f"nota_{index + 1}", "Nota sintética", "jarvis://prueba-restaurada",
            index + 1, text, _sha(text.encode()), len(text.encode()), 1, 1, 1))
        connections[1].execute("INSERT INTO chunks VALUES(?,?,0,0,?,?)",
                               (index + 1, identity, len(text.encode()), text))
    connections[2].execute("INSERT INTO schedules VALUES(?,?,?,?,?,?,1,1,100,1,1)", (
        "e" * 32, "Tarea sintética", "NO EJECUTAR", "jarvis", _ORIGIN_WORKSPACE_ID,
        '{"kind":"once","at":100}'))
    journal = telegram._journal_initial()
    journal["bot_id"] = 424242
    journal["routes"] = {str(chat): {
        "chat_id": chat, "agent_id": "jarvis", "session_id": session,
        "revision": 1, "enabled": True} for chat, session in zip((42, 84), chat_ids, strict=True)}
    journal["offset"] = 2
    journal["control_messages"] = {"42:99": True}
    journal["events"] = {"0:1": {
        "update_id": 1, "epoch": 0, "chat_id": 42, "message_id": 7,
        "agent_id": "jarvis", "session_id": chat_ids[0], "route_revision": 1,
        "status": "completed", "outcome": "known", "run_id": None,
        "error_code": None, "outgoing_message_id": 8,
        "reply_sha256": _sha(b"SYNTHETIC COMPLETED HISTORY; NEVER SEND"),
        "reply_truncated": False}}
    docs = tuple(JsonSource(role, canonical(value), lambda: None) for role, value in (
        ("agents", profiles._initial()), ("skills", skills._initial()), ("telegram", journal)))
    return SourceBundle("synthetic_activation_source", 1, source,
        FileIdentity.of(source.stat()), tuple(SqliteSource(role, connection, lambda: None)
        for role, connection in zip(_SQL, connections, strict=True)), docs, ()), connections


def _root(path, *, new=False):
    root = Path(path)
    if (not root.is_absolute() or root.parent != _TEMP
            or not root.name.startswith("jarvis-ar-") or ".." in root.parts):
        raise ValueError("fixture_new_private_temp_root_required")
    if new:
        root.mkdir(mode=0o700)
    with Directory(root):
        pass
    return root


def prepare(path):
    root = _root(path, new=True)
    (root / "host").mkdir(mode=0o700)
    source, connections = _source(root)
    try:
        created = create_backup(source, _target(root, "backup"), _context())
        if created.status != "completed":
            raise ValueError("fixture_backup_failed")
        verified = verify_backup(root / "backup", _context())
        rows = {}
        for case in CASES:
            restored = restore_new(root / "backup", verified, _target(root, case), _context())
            if restored.status != "completed":
                raise ValueError("fixture_restore_failed")
            profile = root / case
            if case == "busy":
                _write(profile / "profile.lock", b"")
            journal = json.loads(_read(profile / "telegram/telegram.json")[0])
            if (journal["bot_id"] != 424242 or len(journal["routes"]) != 2
                    or any(route["enabled"] for route in journal["routes"].values())):
                raise ValueError("fixture_restore_not_disarmed")
            rows[case] = {"profile": str(profile), "files": _files(profile),
                          "sql": _projection(profile), "telegram": journal}
        manifest = {"format": _FORMAT, "root": str(root), "host": str(root / "host"),
                    "construction_decisions": "synthetic_corpus_only",
                    "activation_decisions_performed": False, "cases": rows}
        _write(root / "fixture.json", canonical(manifest))
        return manifest
    finally:
        for connection in connections:
            connection.close()


def _observe(path, case, *, require_original=False):
    root = _root(path)
    manifest = json.loads(_read(root / "fixture.json")[0])
    if (manifest.get("format") != _FORMAT or manifest.get("root") != str(root)
            or set(manifest.get("cases", ())) != set(CASES) or case not in CASES):
        raise ValueError("fixture_manifest_invalid")
    expected = manifest["cases"][case]
    profile = root / case
    if expected["profile"] != str(profile):
        raise ValueError("fixture_manifest_invalid")
    current_files = _files(profile)
    changed = [name for name, item in current_files.items() if item != expected["files"][name]]
    if require_original and changed:
        raise ValueError("fixture_original_changed")
    return {"case": case, "originals_unchanged": not changed, "changed": changed,
            "sql": _projection(profile), "initial_sql": expected["sql"],
            "files": current_files,
            "admission_present": os.path.lexists(profile / "restore_admission.json"),
            "owner_decision_verified_by_this_script": False}


def observe(path, case):
    return _observe(path, case)


def verify(path, case):
    result = _observe(path, case, require_original=True)
    if not result["originals_unchanged"] or result["sql"] != result["initial_sql"]:
        raise ValueError("fixture_original_changed")
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("prepare", "verify", "observe"))
    parser.add_argument("root", type=Path)
    parser.add_argument("--case", choices=CASES)
    arguments = parser.parse_args()
    if arguments.command == "prepare":
        result = prepare(arguments.root)
        print(json.dumps({"fixture": str(arguments.root / "fixture.json"),
                          "host": result["host"], "cases": list(result["cases"])},
                         ensure_ascii=False))
    else:
        if arguments.case is None:
            parser.error("verify/observe requieren --case")
        print(canonical((verify if arguments.command == "verify" else observe)(
            arguments.root, arguments.case)).decode())


if __name__ == "__main__":
    main()
