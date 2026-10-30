"""A real local durable mailbox, with correlation and no instruction authority.

Delivery means a recipient fetched a local record. Acknowledgement means that
recipient explicitly settled that record. Neither fact establishes a tool effect
or permission. There is no network transport, model dispatch, or auto execution.
"""

from __future__ import annotations

import hashlib
from uuid import uuid4

from jarvis_core.session_coordination_types import (
    CoordinationError,
    digest,
    identity,
    integer,
    ref_value,
    text,
)

_TERMINAL = frozenset({"acknowledged", "rejected", "revoked", "expired"})


class CoordinationMessages:
    @staticmethod
    def _message_row(connection, message_id):
        identity(message_id)
        row = connection.execute("SELECT * FROM messages WHERE message_id=?",
                                 (message_id,)).fetchone()
        if row is None:
            raise CoordinationError("coordination_message_not_found")
        return row

    def _message_value(self, row, temporary):
        status = ("expired" if row["status"] not in _TERMINAL
                  and row["expires_ns"] <= self.clock_ns() else row["status"])
        return {key: row[key] for key in (
            "message_id", "request_id", "source_session_id", "target_session_id", "sender_id",
            "recipient_id", "source_agent_id", "target_agent_id", "correlation_id",
            "parent_message_id", "body", "body_sha256", "revision", "created_ns", "expires_ns",
            "settled_ns",
        )} | {"status": status, "temporary": temporary, "transport": "local",
             "grants_included": False, "permits_replay": False,
             "effect_verified": False}

    def send_message(self, client_id, sender_ref, *, recipient_ref, recipient_client_id,
                     request_id, correlation_id, body, parent_message_id=None, ttl_seconds=300):
        ref_value(sender_ref)
        ref_value(recipient_ref)
        identity(recipient_client_id, client=True)
        identity(request_id)
        identity(correlation_id)
        text(body, 8000)
        integer(ttl_seconds, 1, 86400)
        if sender_ref.temporary != recipient_ref.temporary:
            raise CoordinationError("coordination_message_mode_mismatch")
        if parent_message_id is not None:
            identity(parent_message_id)
        arguments = {"source": ref_value(sender_ref), "target": ref_value(recipient_ref),
                     "recipient_client_id": recipient_client_id, "request_id": request_id,
                     "correlation_id": correlation_id, "body": body,
                     "parent_message_id": parent_message_id, "ttl_seconds": ttl_seconds}
        request_digest = digest(arguments)
        with self._transaction(sender_ref.temporary) as connection:
            source = self._access(connection, client_id, sender_ref, write=True)
            target = self._access(connection, recipient_client_id, recipient_ref)
            if source["workspace_id"] != target["workspace_id"]:
                raise CoordinationError("coordination_message_workspace_mismatch")
            known = connection.execute("SELECT * FROM message_requests WHERE sender_id=? "
                                       "AND request_id=?", (client_id, request_id)).fetchone()
            if known is not None:
                if known["request_digest"] != request_digest:
                    raise CoordinationError("coordination_message_request_conflict")
                previous = connection.execute("SELECT * FROM messages WHERE message_id=?",
                                              (known["message_id"],)).fetchone()
                if previous is not None:
                    return self._message_value(previous, sender_ref.temporary)
                return {"message_id": known["message_id"], "request_id": request_id,
                        "status": known["status"], "content_pruned": True,
                        "temporary": sender_ref.temporary, "transport": "local",
                        "grants_included": False, "permits_replay": False,
                        "effect_verified": False}
            endpoints = {(sender_ref.session_id, client_id),
                         (recipient_ref.session_id, recipient_client_id)}
            routing = sorted((f"{sender_ref.session_id}:{client_id}",
                              f"{recipient_ref.session_id}:{recipient_client_id}"))
            known_correlation = connection.execute(
                "SELECT * FROM message_correlations WHERE correlation_id=?",
                (correlation_id,)).fetchone()
            if parent_message_id is not None:
                parent = self._message_row(connection, parent_message_id)
                if (parent["correlation_id"] != correlation_id
                        or {(parent["source_session_id"], parent["sender_id"]),
                            (parent["target_session_id"], parent["recipient_id"])} != endpoints):
                    raise CoordinationError("coordination_message_parent_mismatch")
            if known_correlation is not None and routing != [known_correlation["endpoint_a"],
                                                            known_correlation["endpoint_b"]]:
                raise CoordinationError("coordination_message_correlation_conflict")
            correlations = connection.execute(
                "SELECT * FROM messages WHERE correlation_id=?", (correlation_id,)).fetchall()
            if any({(row["source_session_id"], row["sender_id"]),
                    (row["target_session_id"], row["recipient_id"])} != endpoints
                   for row in correlations):
                raise CoordinationError("coordination_message_correlation_conflict")
            if connection.execute("SELECT count(*) FROM message_requests").fetchone()[0] >= (
                    self.limits.max_messages):
                raise CoordinationError("coordination_capacity_exceeded")
            pending = connection.execute(
                "SELECT count(*) FROM messages WHERE recipient_id=? AND target_session_id=? "
                "AND status='pending' AND expires_ns>?",
                (recipient_client_id, recipient_ref.session_id, self.clock_ns())).fetchone()[0]
            if pending >= self.limits.max_pending_messages:
                raise CoordinationError("coordination_mailbox_full")
            source_record, target_record = (self.assert_session(sender_ref),
                                             self.assert_session(recipient_ref))
            source_agent, target_agent = source_record.agent_id, target_record.agent_id
            self._authorize(client_id, "message.send", sender_ref, arguments)
            # Revalidate target admission and ACL at the final publication point.
            self._access(connection, recipient_client_id, recipient_ref)
            message_id, now = uuid4().hex, self.clock_ns()
            if known_correlation is None:
                connection.execute("INSERT INTO message_correlations VALUES(?,?,?)",
                                   (correlation_id, *routing))
            connection.execute(
                "INSERT INTO messages VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,1,?,?,NULL)",
                (message_id, request_id, request_digest, sender_ref.session_id,
                 recipient_ref.session_id, client_id, recipient_client_id, source_agent,
                 target_agent, correlation_id, parent_message_id, body,
                 hashlib.sha256(body.encode()).hexdigest(), "pending", now,
                 now + ttl_seconds * 1_000_000_000))
            connection.execute("INSERT INTO message_requests VALUES(?,?,?,?,'pending')",
                               (client_id, request_id, request_digest, message_id))
            return self._message_value(self._message_row(connection, message_id),
                                       sender_ref.temporary)

    def get_message(self, client_id, ref, message_id):
        ref_value(ref)
        with self._transaction(ref.temporary) as connection:
            self._access(connection, client_id, ref)
            row = self._message_row(connection, message_id)
            if (ref.session_id, client_id) not in {
                (row["source_session_id"], row["sender_id"]),
                (row["target_session_id"], row["recipient_id"]),
            }:
                raise CoordinationError("coordination_access_denied")
            return self._message_value(row, ref.temporary)

    def inbox(self, client_id, ref, *, limit=100, offset=0, include_terminal=False):
        ref_value(ref)
        integer(limit, 1, 100)
        integer(offset, 0, 10000)
        if type(include_terminal) is not bool:
            raise CoordinationError("coordination_message_filter_invalid")
        with self._transaction(ref.temporary) as connection:
            self._access(connection, client_id, ref)
            rows = connection.execute(
                "SELECT * FROM messages WHERE recipient_id=? AND target_session_id=? "
                "AND (?=1 OR (status='pending' AND expires_ns>?)) "
                "ORDER BY created_ns,message_id LIMIT ? OFFSET ?",
                (client_id, ref.session_id, int(include_terminal), self.clock_ns(),
                 limit + 1, offset)).fetchall()
            return {"messages": [self._message_value(row, ref.temporary) for row in rows[:limit]],
                    "has_more": len(rows) > limit,
                    "next_offset": offset + limit if len(rows) > limit else None,
                    "acknowledgement_inferred": False}

    def settle_message(self, client_id, ref, *, message_id, status, expected_revision):
        ref_value(ref)
        if status not in {"acknowledged", "rejected"}:
            raise CoordinationError("coordination_message_status_invalid")
        with self._transaction(ref.temporary) as connection:
            self._access(connection, client_id, ref, write=True)
            row = self._message_row(connection, message_id)
            if row["recipient_id"] != client_id or row["target_session_id"] != ref.session_id:
                raise CoordinationError("coordination_access_denied")
            self._cas(row, expected_revision)
            current = self._message_value(row, ref.temporary)["status"]
            if current != "pending":
                if current == status:
                    return self._message_value(row, ref.temporary)
                raise CoordinationError("coordination_message_terminal")
            self._client(row["sender_id"])
            self._authorize(client_id, "message.settle", ref,
                            {"message_id": message_id, "status": status,
                             "expected_revision": expected_revision})
            connection.execute("UPDATE messages SET status=?,revision=revision+1,settled_ns=? "
                               "WHERE message_id=?", (status, self.clock_ns(), message_id))
            connection.execute("UPDATE message_requests SET status=? WHERE message_id=?",
                               (status, message_id))
            return self._message_value(self._message_row(connection, message_id), ref.temporary)

    def revoke_message(self, client_id, ref, *, message_id, expected_revision):
        ref_value(ref)
        with self._transaction(ref.temporary) as connection:
            self._access(connection, client_id, ref, write=True)
            row = self._message_row(connection, message_id)
            if row["sender_id"] != client_id or row["source_session_id"] != ref.session_id:
                raise CoordinationError("coordination_access_denied")
            self._cas(row, expected_revision)
            if self._message_value(row, ref.temporary)["status"] != "pending":
                raise CoordinationError("coordination_message_terminal")
            self._authorize(client_id, "message.revoke", ref,
                            {"message_id": message_id, "expected_revision": expected_revision})
            connection.execute("UPDATE messages SET status='revoked',revision=revision+1,"
                               "settled_ns=? WHERE message_id=?", (self.clock_ns(), message_id))
            connection.execute("UPDATE message_requests SET status='revoked' WHERE message_id=?",
                               (message_id,))
            return self._message_value(self._message_row(connection, message_id), ref.temporary)

    def _revoke_session_messages(self, connection, session_id, *, client_id=None):
        connection.execute(
            "UPDATE message_requests SET status='revoked' WHERE message_id IN "
            "(SELECT message_id FROM messages WHERE status='pending' "
            "AND (source_session_id=? OR target_session_id=?) "
            "AND (? IS NULL OR sender_id=? OR recipient_id=?))",
            (session_id, session_id, client_id, client_id, client_id))
        connection.execute(
            "UPDATE messages SET status='revoked',revision=revision+1,settled_ns=? "
            "WHERE status='pending' AND (source_session_id=? OR target_session_id=?) "
            "AND (? IS NULL OR sender_id=? OR recipient_id=?)",
            (self.clock_ns(), session_id, session_id, client_id, client_id, client_id))

    def revoke_client(self, client_id):
        """Trusted host hook after ClientAuthority revocation, never an RPC grant.

        The existing authority has already revoked the key. Removing memberships
        prevents a later admission from reviving old collaboration automatically.
        Historical ownership and resource custody remain; no transfer is inferred.
        """
        identity(client_id, client=True)
        with self._mutex:
            modes = ([False] if self._disk is not None or self.path.exists() else [])
            if self._memory is not None:
                modes.append(True)
            for temporary in modes:
                with self._transaction(temporary) as connection:
                    connection.execute(
                        "UPDATE message_requests SET status='revoked' WHERE message_id IN "
                        "(SELECT message_id FROM messages WHERE status='pending' "
                        "AND (sender_id=? OR recipient_id=?))", (client_id, client_id))
                    connection.execute(
                        "UPDATE messages SET status='revoked',revision=revision+1,settled_ns=? "
                        "WHERE status='pending' AND (sender_id=? OR recipient_id=?)",
                        (self.clock_ns(), client_id, client_id))
                    connection.execute("UPDATE session_links SET revision=revision+1 WHERE "
                                       "session_id IN (SELECT session_id FROM members WHERE "
                                       "client_id=?)", (client_id,))
                    connection.execute("DELETE FROM members WHERE client_id=?", (client_id,))

    def prune_messages(self, client_id, *, temporary=False, before_ns):
        """Explicit retention; only own terminal records without retained replies."""
        integer(before_ns)
        self._client(client_id, write=True)
        with self._transaction(temporary) as connection:
            self._authorize(client_id, "message.prune", None,
                            {"temporary": temporary, "before_ns": before_ns})
            # Expiration is deterministic from the recorded TTL. It never ACKs
            # delivery, executes a task, or permits replay of an expired request.
            connection.execute(
                "UPDATE message_requests SET status='expired' WHERE message_id IN "
                "(SELECT message_id FROM messages WHERE sender_id=? AND created_ns<? "
                "AND status='pending' AND expires_ns<=?)",
                (client_id, before_ns, self.clock_ns()))
            cursor = connection.execute(
                "DELETE FROM messages WHERE sender_id=? AND created_ns<? "
                "AND (status IN ('acknowledged','rejected','revoked','expired') OR expires_ns<=?) "
                "AND NOT EXISTS(SELECT 1 FROM messages child "
                "WHERE child.parent_message_id=messages.message_id)",
                (client_id, before_ns, self.clock_ns()))
            return {"deleted": cursor.rowcount, "temporary": temporary,
                    "before_ns": before_ns}
