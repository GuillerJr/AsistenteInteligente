"""Local workspaces, session ACLs, mailboxes and accountable resource custody.

This store has an explicit private path and the same lazy disk/RAM split as
StateStore. It never discovers profiles, admits keys, executes model text or
grants tool permissions. Hosts supply the existing admission/session/approval
authority. Client identities are admitted local keys, not macOS user accounts,
remote peers, Apple attestation, or an additional inference provider.

Every mutator requires ``authorize_change(client_id, operation, ref, arguments)``
immediately before its transaction's effect. The callback must validate the exact
host request against the one existing authority. Omitting it fails closed.
``assert_client(client_id, write=...)`` and ``assert_session(ref)`` are rechecked;
the latter returns StateStore's SessionRecord. Session group/pin/member changes
use a whole-session revision CAS and require an idle session. Workspace tools are
resolved per SessionRef and physical device/inode, never by a mutable global run
setting. Temporary data, including failed lookups, only opens ``:memory:``.
"""

from __future__ import annotations

import fcntl
import os
import sqlite3
import stat
import threading
import time
from contextlib import contextmanager
from pathlib import Path

from jarvis_core.session_coordination_budgets import CoordinationBudgets
from jarvis_core.session_coordination_messages import CoordinationMessages
from jarvis_core.session_coordination_types import (
    CoordinationError,
    CoordinationLimits,
    identity,
    integer,
    ref_value,
    text,
)
from jarvis_core.state import (
    SessionRef,
    StateError,
    StateStore,
    _directory_identity,
    ensure_private_directory,
)
from jarvis_core.workspace import WorkspaceTools

_APPLICATION_ID = 0x4A435244  # JCRD: Jarvis coordination, never a StateStore DB.
_SCHEMA = """
CREATE TABLE workspaces (
 workspace_id TEXT PRIMARY KEY, name TEXT NOT NULL, path TEXT NOT NULL,
 device INTEGER NOT NULL, inode INTEGER NOT NULL, revision INTEGER NOT NULL,
 owner_id TEXT NOT NULL, created_ns INTEGER NOT NULL,
 UNIQUE(device,inode)
);
CREATE TABLE session_links (
 session_id TEXT PRIMARY KEY, workspace_id TEXT NOT NULL REFERENCES workspaces,
 owner_id TEXT NOT NULL, group_name TEXT, pinned INTEGER NOT NULL CHECK(pinned IN(0,1)),
 parent_session_id TEXT REFERENCES session_links, depth INTEGER NOT NULL,
 revision INTEGER NOT NULL, created_ns INTEGER NOT NULL,
 security_generation INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE members (
 session_id TEXT NOT NULL REFERENCES session_links ON DELETE CASCADE,
 client_id TEXT NOT NULL, role TEXT NOT NULL CHECK(role IN('read','collaborate')),
 PRIMARY KEY(session_id,client_id)
);
CREATE TABLE messages (
 message_id TEXT PRIMARY KEY, request_id TEXT NOT NULL, request_digest TEXT NOT NULL,
 source_session_id TEXT NOT NULL REFERENCES session_links,
 target_session_id TEXT NOT NULL REFERENCES session_links,
 sender_id TEXT NOT NULL, recipient_id TEXT NOT NULL,
 source_agent_id TEXT NOT NULL, target_agent_id TEXT NOT NULL,
 correlation_id TEXT NOT NULL, parent_message_id TEXT REFERENCES messages,
 body TEXT NOT NULL, body_sha256 TEXT NOT NULL, status TEXT NOT NULL,
 revision INTEGER NOT NULL, created_ns INTEGER NOT NULL, expires_ns INTEGER NOT NULL,
 settled_ns INTEGER, UNIQUE(sender_id,request_id)
);
CREATE INDEX messages_inbox ON messages(recipient_id,target_session_id,created_ns,message_id);
CREATE TABLE message_requests (
 sender_id TEXT NOT NULL, request_id TEXT NOT NULL, request_digest TEXT NOT NULL,
 message_id TEXT NOT NULL, status TEXT NOT NULL, PRIMARY KEY(sender_id,request_id)
);
CREATE TABLE message_correlations (
 correlation_id TEXT PRIMARY KEY, endpoint_a TEXT NOT NULL, endpoint_b TEXT NOT NULL
);
CREATE TABLE budget_limits (
 session_id TEXT PRIMARY KEY REFERENCES session_links ON DELETE CASCADE,
 token_limit INTEGER, cost_limit TEXT, currency TEXT NOT NULL, revision INTEGER NOT NULL
);
CREATE TABLE reservations (
 reservation_id TEXT PRIMARY KEY, run_id TEXT NOT NULL UNIQUE,
 session_id TEXT NOT NULL REFERENCES session_links,
 owner_id TEXT NOT NULL,
 parent_reservation_id TEXT REFERENCES reservations,
 request_digest TEXT NOT NULL, reserved_tokens INTEGER, reserved_cost TEXT,
 actual_tokens INTEGER, actual_cost TEXT, usage_digest TEXT,
 status TEXT NOT NULL, effect_status TEXT NOT NULL, cleanup_confirmed INTEGER NOT NULL,
 revision INTEGER NOT NULL, created_ns INTEGER NOT NULL, settled_ns INTEGER
);
CREATE INDEX reservations_session ON reservations(session_id,created_ns,reservation_id);
CREATE TABLE run_requests (
 run_id TEXT PRIMARY KEY, reservation_id TEXT NOT NULL UNIQUE
);
"""


class SessionCoordinationStore(CoordinationMessages, CoordinationBudgets):
    def __init__(self, path, *, assert_client, assert_session, assert_idle,
                 authorize_change=None, clock_ns=time.time_ns, limits=None):
        self.path = Path(path)
        if not self.path.is_absolute() or ".." in self.path.parts:
            raise CoordinationError("coordination_path_invalid")
        callbacks = (assert_client, assert_session, assert_idle, clock_ns)
        if not all(callable(item) for item in callbacks):
            raise CoordinationError("coordination_callbacks_required")
        if authorize_change is not None and not callable(authorize_change):
            raise CoordinationError("coordination_authority_invalid")
        self.assert_client = assert_client
        self.assert_session = assert_session
        self.assert_idle = assert_idle
        self.authorize_change = authorize_change
        self.clock_ns = clock_ns
        self.limits = limits or CoordinationLimits()
        if not isinstance(self.limits, CoordinationLimits):
            raise CoordinationError("coordination_limits_invalid")
        self._mutex = threading.RLock()
        self._disk = self._memory = None
        self._file_fd = self._lock_fd = self._parent_identity = None
        self._closed = False

    def close(self):
        with self._mutex:
            for connection in (self._disk, self._memory):
                if connection is not None:
                    connection.close()
            self._disk = self._memory = None
            self._release_files()
            self._closed = True

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()

    def _release_files(self):
        for descriptor in (self._file_fd, self._lock_fd):
            if descriptor is not None:
                os.close(descriptor)
        self._file_fd = self._lock_fd = self._parent_identity = None

    def _check_disk(self):
        if _directory_identity(self.path.parent, create=False) != self._parent_identity:
            raise CoordinationError("coordination_path_changed")
        for path, descriptor in (
            (self.path, self._file_fd),
            (self.path.with_name(self.path.name + ".lock"), self._lock_fd),
        ):
            linked, opened = path.lstat(), os.fstat(descriptor)
            if (not stat.S_ISREG(linked.st_mode)
                    or (linked.st_dev, linked.st_ino) != (opened.st_dev, opened.st_ino)
                    or linked.st_uid != os.getuid() or stat.S_IMODE(linked.st_mode) != 0o600
                    or linked.st_nlink != 1):
                raise CoordinationError("coordination_path_changed")
        for suffix in ("-journal", "-wal", "-shm"):
            candidate = self.path.with_name(self.path.name + suffix)
            try:
                info = candidate.lstat()
            except FileNotFoundError:
                continue
            if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid()
                    or stat.S_IMODE(info.st_mode) != 0o600 or info.st_nlink != 1):
                raise CoordinationError("coordination_sidecar_unsafe")

    @staticmethod
    def _configure(connection):
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA temp_store=MEMORY")
        connection.execute("PRAGMA trusted_schema=OFF")
        connection.execute("PRAGMA secure_delete=ON")

    @staticmethod
    def _initialize(connection):
        connection.executescript("BEGIN IMMEDIATE;" + _SCHEMA
                                 + f"PRAGMA application_id={_APPLICATION_ID};"
                                 "PRAGMA user_version=1;COMMIT;")

    def _connection(self, temporary):
        if type(temporary) is not bool:
            raise CoordinationError("coordination_temporary_invalid")
        if self._closed:
            raise CoordinationError("coordination_closed")
        if temporary:
            if self._memory is None:
                self._memory = sqlite3.connect(":memory:", isolation_level=None,
                                              check_same_thread=False)
                self._configure(self._memory)
                self._initialize(self._memory)
            return self._memory
        if self._disk is not None:
            self._check_disk()
            return self._disk
        connection = None
        try:
            ensure_private_directory(self.path.parent)
            self._parent_identity = _directory_identity(self.path.parent, create=False)
            self._lock_fd, _ = StateStore._private_file(
                self.path.with_name(self.path.name + ".lock"))
            try:
                fcntl.flock(self._lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise CoordinationError("coordination_in_use") from None
            self._file_fd, created = StateStore._private_file(self.path)
            self._check_disk()
            connection = sqlite3.connect(str(self.path), isolation_level=None,
                                         check_same_thread=False, timeout=1)
            self._check_disk()
            self._configure(connection)
            if created:
                self._initialize(connection)
            elif (connection.execute("PRAGMA application_id").fetchone()[0] != _APPLICATION_ID
                  or connection.execute("PRAGMA user_version").fetchone()[0] != 1
                  or connection.execute("PRAGMA quick_check(1)").fetchone()[0] != "ok"
                  or connection.execute("PRAGMA foreign_key_check").fetchone() is not None):
                raise CoordinationError("coordination_database_invalid")
            if connection.execute("PRAGMA journal_mode=DELETE").fetchone()[0] != "delete":
                raise CoordinationError("coordination_database_invalid")
            # No auto retry after restart: dispatched work retains its full
            # reservation until host evidence reconciles cleanup and usage.
            connection.execute(
                "UPDATE reservations SET status='unknown',effect_status='unknown',"
                "revision=revision+1 WHERE status='dispatched'")
            self._disk = connection
            return connection
        except BaseException:
            if connection is not None:
                connection.close()
            self._release_files()
            raise

    @contextmanager
    def _transaction(self, temporary):
        with self._mutex:
            connection = None
            try:
                connection = self._connection(temporary)
                connection.execute("BEGIN IMMEDIATE")
                yield connection
                connection.execute("COMMIT")
            except (OSError, sqlite3.Error):
                if connection is not None and connection.in_transaction:
                    connection.rollback()
                raise CoordinationError("coordination_unavailable") from None
            except BaseException:
                if connection is not None and connection.in_transaction:
                    connection.rollback()
                raise

    def _client(self, client_id, *, write=False):
        identity(client_id, client=True)
        return self.assert_client(client_id, write=write)

    def _authorize(self, client_id, operation, ref, arguments):
        self._client(client_id, write=True)
        if self.authorize_change is None:
            raise CoordinationError("coordination_authority_required")
        self.authorize_change(client_id, operation, ref, arguments)
        self._client(client_id, write=True)

    @staticmethod
    def _cas(row, expected_revision):
        integer(expected_revision)
        if row["revision"] != expected_revision:
            raise CoordinationError("coordination_revision_changed")

    def _access(self, connection, client_id, ref, *, write=False, owner=False):
        ref_value(ref)
        self._client(client_id, write=write)
        self.assert_session(ref)
        row = connection.execute("SELECT * FROM session_links WHERE session_id=?",
                                 (ref.session_id,)).fetchone()
        if row is None:
            raise CoordinationError("coordination_session_unbound")
        if row["owner_id"] != client_id:
            member = connection.execute(
                "SELECT role FROM members WHERE session_id=? AND client_id=?",
                (ref.session_id, client_id)).fetchone()
            if owner or member is None or (write and member["role"] != "collaborate"):
                raise CoordinationError("coordination_access_denied")
        return row

    @staticmethod
    def _workspace_row(connection, workspace_id):
        identity(workspace_id)
        row = connection.execute("SELECT * FROM workspaces WHERE workspace_id=?",
                                 (workspace_id,)).fetchone()
        if row is None:
            raise CoordinationError("coordination_workspace_not_found")
        return row

    @staticmethod
    def _workspace_value(row, temporary):
        return {"workspace_id": row["workspace_id"], "name": row["name"],
                "owner_id": row["owner_id"],
                "path": row["path"], "identity": [row["device"], row["inode"]],
                "revision": row["revision"], "temporary": temporary}

    @staticmethod
    def _session_value(connection, row, temporary):
        members = [{"client_id": item["client_id"], "role": item["role"]}
                   for item in connection.execute(
                       "SELECT * FROM members WHERE session_id=? ORDER BY client_id",
                       (row["session_id"],))]
        return {"session_id": row["session_id"], "temporary": temporary,
                "workspace_id": row["workspace_id"], "owner_id": row["owner_id"],
                "group": row["group_name"], "pinned": bool(row["pinned"]),
                "parent_session_id": row["parent_session_id"], "depth": row["depth"],
                "revision": row["revision"], "members": members,
                "security_generation": row["security_generation"],
                "permissions_inherited": False}

    def register_workspace(self, client_id, *, workspace_id, name, path,
                           expected_revision=0, temporary=False):
        identity(workspace_id)
        name, path = text(name, 128), text(str(path), 4096)
        integer(expected_revision)
        candidate = WorkspaceTools(Path(path))
        arguments = {"workspace_id": workspace_id, "name": name, "path": path,
                     "identity": list(candidate.identity), "expected_revision": expected_revision,
                     "temporary": temporary}
        self._client(client_id, write=True)
        with self._transaction(temporary) as connection:
            row = connection.execute("SELECT * FROM workspaces WHERE workspace_id=?",
                                     (workspace_id,)).fetchone()
            if row is None:
                if expected_revision != 0:
                    raise CoordinationError("coordination_revision_changed")
                if connection.execute("SELECT count(*) FROM workspaces").fetchone()[0] >= (
                        self.limits.max_workspaces):
                    raise CoordinationError("coordination_capacity_exceeded")
            else:
                if row["owner_id"] != client_id:
                    raise CoordinationError("coordination_access_denied")
                self._cas(row, expected_revision)
                if (path, *candidate.identity) != (row["path"], row["device"], row["inode"]):
                    raise CoordinationError("coordination_workspace_identity_changed")
            if connection.execute(
                "SELECT 1 FROM workspaces WHERE device=? AND inode=? AND workspace_id<>?",
                (*candidate.identity, workspace_id)).fetchone() is not None:
                raise CoordinationError("coordination_workspace_duplicate")
            self._authorize(client_id, "workspace.register", None, arguments)
            if WorkspaceTools(Path(path)).identity != candidate.identity:
                raise CoordinationError("coordination_workspace_identity_changed")
            if row is None:
                connection.execute("INSERT INTO workspaces VALUES(?,?,?,?,?,1,?,?)",
                                   (workspace_id, name, path, *candidate.identity,
                                    client_id, self.clock_ns()))
            else:
                connection.execute("UPDATE workspaces SET name=?,revision=revision+1 "
                                   "WHERE workspace_id=?", (name, workspace_id))
            return self._workspace_value(self._workspace_row(connection, workspace_id), temporary)

    def list_workspaces(self, client_id, *, temporary=False):
        self._client(client_id)
        with self._transaction(temporary) as connection:
            rows = connection.execute(
                "SELECT DISTINCT w.* FROM workspaces w LEFT JOIN session_links s "
                "ON s.workspace_id=w.workspace_id LEFT JOIN members m ON m.session_id=s.session_id "
                "WHERE w.owner_id=? OR s.owner_id=? OR m.client_id=? "
                "ORDER BY w.name,w.workspace_id",
                (client_id, client_id, client_id)).fetchall()
            return [self._workspace_value(row, temporary) for row in rows]

    def bind_session(self, client_id, ref, *, workspace_id, expected_revision=0,
                     group=None, pinned=False, parent_ref=None):
        ref_value(ref)
        identity(workspace_id)
        text(group, 128, nullable=True)
        integer(expected_revision)
        if type(pinned) is not bool:
            raise CoordinationError("coordination_pin_invalid")
        self._client(client_id, write=True)
        self.assert_session(ref)
        self.assert_idle(ref)
        if parent_ref is not None:
            ref_value(parent_ref)
            if ref.temporary != parent_ref.temporary or ref == parent_ref:
                raise CoordinationError("coordination_parent_invalid")
        arguments = {**ref_value(ref), "workspace_id": workspace_id, "group": group,
                     "pinned": pinned, "expected_revision": expected_revision,
                     "parent": None if parent_ref is None else ref_value(parent_ref)}
        with self._transaction(ref.temporary) as connection:
            workspace = self._workspace_row(connection, workspace_id)
            row = connection.execute("SELECT * FROM session_links WHERE session_id=?",
                                     (ref.session_id,)).fetchone()
            parent_id, depth, workspace_changed = None, 0, False
            if row is None:
                if expected_revision != 0:
                    raise CoordinationError("coordination_revision_changed")
                if workspace["owner_id"] != client_id:
                    raise CoordinationError("coordination_access_denied")
                if connection.execute("SELECT count(*) FROM session_links").fetchone()[0] >= (
                        self.limits.max_sessions):
                    raise CoordinationError("coordination_capacity_exceeded")
                if parent_ref is not None:
                    parent = self._access(connection, client_id, parent_ref, write=True)
                    if parent["workspace_id"] != workspace_id:
                        raise CoordinationError("coordination_parent_workspace_changed")
                    parent_id, depth = parent_ref.session_id, parent["depth"] + 1
                    count = connection.execute(
                        "SELECT count(*) FROM session_links WHERE parent_session_id=?",
                        (parent_id,)).fetchone()[0]
                    if depth > self.limits.max_depth or count >= self.limits.max_children:
                        raise CoordinationError("coordination_child_limit_exceeded")
            else:
                row = self._access(connection, client_id, ref, write=True, owner=True)
                self._cas(row, expected_revision)
                parent_id, depth = row["parent_session_id"], row["depth"]
                if parent_ref is not None and parent_ref.session_id != parent_id:
                    raise CoordinationError("coordination_parent_immutable")
                if row["workspace_id"] != workspace_id:
                    workspace_changed = True
                    if connection.execute(
                        "SELECT 1 FROM reservations WHERE session_id=?", (ref.session_id,)
                    ).fetchone() is not None or connection.execute(
                        "SELECT 1 FROM session_links WHERE parent_session_id=?", (ref.session_id,)
                    ).fetchone() is not None or parent_id is not None:
                        raise CoordinationError("coordination_workspace_in_use")
                    if workspace["owner_id"] != client_id:
                        raise CoordinationError("coordination_access_denied")
                    self._revoke_session_messages(connection, ref.session_id)
            self.assert_idle(ref)
            self._authorize(client_id, "session.bind", ref, arguments)
            if row is None:
                connection.execute("INSERT INTO session_links VALUES(?,?,?,?,?,?,?,1,?,0)",
                                   (ref.session_id, workspace_id, client_id, group, int(pinned),
                                    parent_id, depth, self.clock_ns()))
            else:
                if workspace_changed:
                    # Membership reviewed for one workspace cannot silently
                    # expose a different physical directory to collaborators.
                    connection.execute("DELETE FROM members WHERE session_id=?", (ref.session_id,))
                connection.execute("UPDATE session_links SET workspace_id=?,group_name=?,"
                                   "pinned=?,revision=revision+1,"
                                   "security_generation=security_generation+? WHERE session_id=?",
                                   (workspace_id, group, int(pinned), int(workspace_changed),
                                    ref.session_id))
            return self._session_value(connection, connection.execute(
                "SELECT * FROM session_links WHERE session_id=?", (ref.session_id,)).fetchone(),
                ref.temporary)

    def get_session(self, client_id, ref):
        ref_value(ref)
        with self._transaction(ref.temporary) as connection:
            row = self._access(connection, client_id, ref)
            return self._session_value(connection, row, ref.temporary)

    def list_sessions(self, client_id, *, temporary=False, workspace_id=None, group=None):
        self._client(client_id)
        if workspace_id is not None:
            identity(workspace_id)
        text(group, 128, nullable=True)
        with self._transaction(temporary) as connection:
            rows = connection.execute(
                "SELECT DISTINCT s.* FROM session_links s LEFT JOIN members m "
                "ON s.session_id=m.session_id WHERE (s.owner_id=? OR m.client_id=?) "
                "AND (? IS NULL OR s.workspace_id=?) AND (? IS NULL OR s.group_name=?) "
                "ORDER BY s.pinned DESC,s.created_ns,s.session_id",
                (client_id, client_id, workspace_id, workspace_id, group, group)).fetchall()
            result = []
            for row in rows:
                self.assert_session(SessionRef(row["session_id"], temporary))
                result.append(self._session_value(connection, row, temporary))
            return result

    def set_member(self, client_id, ref, *, member_id, role, expected_revision):
        ref_value(ref)
        identity(member_id, client=True)
        if role not in {"read", "collaborate", "remove"}:
            raise CoordinationError("coordination_role_invalid")
        self.assert_idle(ref)
        with self._transaction(ref.temporary) as connection:
            row = self._access(connection, client_id, ref, write=True, owner=True)
            self._cas(row, expected_revision)
            if member_id == row["owner_id"]:
                raise CoordinationError("coordination_owner_immutable")
            if role != "remove":
                self._client(member_id, write=role == "collaborate")
                count = connection.execute("SELECT count(*) FROM members WHERE session_id=?",
                                           (ref.session_id,)).fetchone()[0]
                exists = connection.execute(
                    "SELECT 1 FROM members WHERE session_id=? AND client_id=?",
                    (ref.session_id, member_id)).fetchone()
                if exists is None and count >= self.limits.max_members:
                    raise CoordinationError("coordination_capacity_exceeded")
            self._authorize(client_id, "session.member", ref,
                            {"member_id": member_id, "role": role,
                             "expected_revision": expected_revision})
            if role == "remove":
                connection.execute("DELETE FROM members WHERE session_id=? AND client_id=?",
                                   (ref.session_id, member_id))
            else:
                connection.execute("INSERT INTO members VALUES(?,?,?) ON CONFLICT(session_id,"
                                   "client_id) DO UPDATE SET role=excluded.role",
                                   (ref.session_id, member_id, role))
            if role != "collaborate":
                self._revoke_session_messages(connection, ref.session_id, client_id=member_id)
            connection.execute("UPDATE session_links SET revision=revision+1,"
                               "security_generation=security_generation+1 WHERE session_id=?",
                               (ref.session_id,))
            return self._session_value(connection, connection.execute(
                "SELECT * FROM session_links WHERE session_id=?", (ref.session_id,)).fetchone(),
                ref.temporary)

    def workspace_tools(self, client_id, ref):
        ref_value(ref)
        with self._transaction(ref.temporary) as connection:
            row = self._access(connection, client_id, ref)
            workspace = self._workspace_row(connection, row["workspace_id"])
            candidate = WorkspaceTools(Path(workspace["path"]))
            if candidate.identity != (workspace["device"], workspace["inode"]):
                raise CoordinationError("coordination_workspace_identity_changed")
            return candidate

    def delete_session(self, client_id, ref, *, expected_revision, check_only=False,
                       state_deleted=False):
        """Remove metadata only after host cleanup; uncertain custody is retained."""
        ref_value(ref)
        self.assert_idle(ref)
        with self._transaction(ref.temporary) as connection:
            if state_deleted:
                self._client(client_id, write=True)
                try:
                    self.assert_session(ref)
                except StateError as error:
                    if error.code != "session_not_found":
                        raise
                else:
                    raise CoordinationError("coordination_session_still_present")
                row = connection.execute("SELECT * FROM session_links WHERE session_id=?",
                                         (ref.session_id,)).fetchone()
                if row is None or row["owner_id"] != client_id:
                    raise CoordinationError("coordination_access_denied")
            else:
                row = self._access(connection, client_id, ref, write=True, owner=True)
            self._cas(row, expected_revision)
            if connection.execute("SELECT 1 FROM session_links WHERE parent_session_id=?",
                                  (ref.session_id,)).fetchone() is not None:
                raise CoordinationError("coordination_children_retained")
            if connection.execute(
                "SELECT 1 FROM reservations r LEFT JOIN budget_limits b "
                "ON b.session_id=r.session_id WHERE r.session_id=? AND "
                "(r.status NOT IN('settled','cancelled') OR r.cleanup_confirmed=0 "
                "OR r.effect_status='unknown' OR (b.token_limit IS NOT NULL "
                "AND r.actual_tokens IS NULL) OR (b.cost_limit IS NOT NULL "
                "AND r.actual_cost IS NULL))", (ref.session_id,)
            ).fetchone() is not None:
                raise CoordinationError("coordination_budget_custody_pending")
            # Cross-session messages are evidence: retain their endpoint until
            # explicit mailbox retention removes terminal records.
            if connection.execute("SELECT 1 FROM messages WHERE source_session_id=? OR "
                                  "target_session_id=?", (ref.session_id, ref.session_id)
                                  ).fetchone() is not None:
                raise CoordinationError("coordination_messages_retained")
            if connection.execute("SELECT 1 FROM reservations r JOIN reservations c "
                                  "ON c.parent_reservation_id=r.reservation_id WHERE "
                                  "r.session_id=?", (ref.session_id,)).fetchone() is not None:
                raise CoordinationError("coordination_children_retained")
            self._authorize(client_id, "session.delete", ref,
                            {"expected_revision": expected_revision})
            if check_only:
                return {"allowed": True, "revision": row["revision"]}
            connection.execute("DELETE FROM reservations WHERE session_id=?", (ref.session_id,))
            connection.execute("DELETE FROM session_links WHERE session_id=?", (ref.session_id,))
            return {"deleted": True, "session_id": ref.session_id, "temporary": ref.temporary}
