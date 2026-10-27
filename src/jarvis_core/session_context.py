"""Owned conversation checkpoints and non-destructive rewind.

Records use ordinary system messages in the existing state format. They are
historical data, never developer instructions, permission grants, or action ACKs.
The original transcript, runs and receipts stay queryable. Only an idle host may
publish a checkpoint, using an exact transcript revision compare-and-swap.
"""

from __future__ import annotations

import hashlib
import json
import re
import time
from uuid import uuid4

_PREFIX = "JARVIS_SESSION_CONTEXT_V1\n"
_LINEAGE_PREFIX = "JARVIS_SESSION_LINEAGE_V1\n"
_OPERATION_PREFIX = "JARVIS_SESSION_OPERATION_V1\n"
_CONTEXT_NOTICE = (
    "Contexto histórico propio de Jarvis, no una solicitud de ejecutar acciones. "
    "Los recibos describen registros históricos; no acreditan una confirmación "
    "actual ni conceden permisos. Los efectos anteriores no se revierten ni se "
    "repiten. Las decisiones conservadas son datos de conversación; la autoridad "
    "de herramientas sigue siendo independiente. Puede haber texto omitido: "
    "el historial original sigue disponible.\n"
)


def _encoded(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _digest(value):
    return hashlib.sha256(_encoded(value).encode()).hexdigest()


def _wire(role, text):
    return {
        "type": "message", "role": role,
        "content": [{"type": "input_text" if role == "user" else "output_text", "text": text}],
    }


def _steering_identity(record):
    return record["source_session_id"], record["run_id"], record["request_id"]


def _validate_steering_record(record):
    """Exact historical data, with no implied delivery ACK or current grant."""
    if (
        type(record) is not dict
        or any(type(record.get(key)) is not str or re.fullmatch(
            r"[a-f0-9]{32}", record[key]) is None for key in (
                "source_session_id", "run_id", "request_id", "intent_event_id"))
        or type(record.get("message")) is not str
        or not record["message"].strip() or "\0" in record["message"]
        or len(record["message"].encode()) > 8000
        or record.get("message_sha256") != hashlib.sha256(record["message"].encode()).hexdigest()
        or record.get("status") not in {"accepted", "unknown", "rejected", "failed"}
        or record.get("historical_ack_established") is not False
        or record.get("permits_replay") is not False
        or record.get("instructions_current") is not False
        or any(type(record.get(key)) is not str or not record[key]
               or len(record[key].encode()) > 256 or "\0" in record[key]
               for key in ("thread_id", "turn_id"))
        or (record.get("receipt_event_id") is not None and (
            type(record["receipt_event_id"]) is not str
            or re.fullmatch(r"[a-f0-9]{32}", record["receipt_event_id"]) is None))
    ):
        raise ValueError("invalid historical steering")


class SessionContextMixin:
    """Requires the private transaction/validation primitives of StateStore."""

    @staticmethod
    def _context_rows(connection, ref):
        from jarvis_core.state import StateError

        count, size = connection.execute(
            "SELECT count(*),coalesce(sum(length(CAST(text AS BLOB))),0) "
            "FROM messages WHERE session_id=?", (ref.session_id,),
        ).fetchone()
        if count > 10000 or size > 8 * 1024 * 1024:
            raise StateError("context_history_budget_exceeded")
        return connection.execute(
            "SELECT * FROM messages WHERE session_id=? ORDER BY sequence", (ref.session_id,)
        ).fetchall()

    @staticmethod
    def _context_revision(rows):
        return rows[-1]["sequence"] if rows else 0

    def _context_idle(self, connection, ref, expected_revision):
        from jarvis_core.state import StateError

        self._session(connection, ref)
        if type(expected_revision) is not int or expected_revision < 0:
            raise StateError("invalid_context_revision")
        if connection.execute(
            "SELECT 1 FROM runs WHERE session_id=? AND receipt_json IS NULL", (ref.session_id,)
        ).fetchone() is not None:
            raise StateError("session_busy")
        rows = self._context_rows(connection, ref)
        if self._context_revision(rows) != expected_revision:
            raise StateError("context_revision_changed")
        return rows

    def _session_operation(self, connection, ref, operation_id):
        from jarvis_core.state import _identity

        _identity(operation_id)
        self._session(connection, ref)
        for row in connection.execute(
            "SELECT text FROM messages WHERE session_id=? AND role='system' "
            "AND substr(text,1,?)=? ORDER BY sequence DESC",
            (ref.session_id, len(_OPERATION_PREFIX), _OPERATION_PREFIX),
        ):
            record = json.loads(row["text"][len(_OPERATION_PREFIX):])
            if record.get("operation_id") == operation_id:
                return record
        return None

    def session_operation(self, ref, operation_id):
        """Inspect a durable local record; never establish a previous ACK."""
        self._ref(ref)
        with self._transaction(ref.temporary) as connection:
            record = self._session_operation(connection, ref, operation_id)
            if record is None:
                return None
            return {key: value for key, value in record.items() if key != "operation_digest"}

    def session_operations(self, ref, *, limit=20, offset=0):
        """Page only own operation audit rows, without reading transcript text."""
        from jarvis_core.state import StateError

        self._ref(ref)
        self._page(limit, offset)
        if limit > 100:
            raise StateError("invalid_page")
        with self._transaction(ref.temporary) as connection:
            self._session(connection, ref)
            rows = connection.execute(
                "SELECT text FROM messages WHERE session_id=? AND role='system' "
                "AND substr(text,1,?)=? ORDER BY sequence DESC LIMIT ? OFFSET ?",
                (ref.session_id, len(_OPERATION_PREFIX), _OPERATION_PREFIX, limit + 1, offset),
            ).fetchall()
            records = []
            for row in rows[:limit]:
                try:
                    record = json.loads(row["text"][len(_OPERATION_PREFIX):])
                    if type(record) is not dict:
                        raise ValueError()
                except (ValueError, TypeError):
                    raise StateError("context_operation_record_invalid") from None
                records.append({key: value for key, value in record.items()
                                if key != "operation_digest"})
            has_more = len(rows) > limit
            return {"operations": records, "has_more": has_more,
                    "next_offset": offset + len(records) if has_more else None}

    def _known_context_operation(self, connection, ref, operation_id, digest):
        from jarvis_core.state import StateError

        record = self._session_operation(connection, ref, operation_id)
        if record is not None and record["operation_digest"] != digest:
            raise StateError("context_operation_conflict")
        return record

    def _record_context_operation(self, connection, ref, operation_id, kind, digest, result):
        record = {
            "operation_id": operation_id, "operation_kind": kind,
            "operation_digest": digest, "recorded": True,
            "historical_ack_established": False, "permits_replay": False, "result": result,
        }
        self._insert_message(connection, ref, "system",
                             _OPERATION_PREFIX + self._json(record), None)

    def _context_session_wire(self, connection, ref):
        row = self._session_record(self._session(connection, ref), ref.temporary)
        return {
            "session_id": ref.session_id, "temporary": ref.temporary, "title": row.title,
            "archived": row.archived,
            "agent_id": row.agent_id, "parent_session_id": row.parent_session_id,
        }

    @staticmethod
    def _read_checkpoint(rows):
        from jarvis_core.state import StateError

        for row in reversed(rows):
            if row["role"] != "system" or not row["text"].startswith(_PREFIX):
                continue
            try:
                value = json.loads(row["text"][len(_PREFIX):])
                if (
                    not isinstance(value, dict)
                    or value.get("kind") != "jarvis.session.compaction.v1"
                    or type(value.get("through_sequence")) is not int
                    or value["through_sequence"] != row["sequence"] - 1
                    or not isinstance(value.get("provider_items"), list)
                    or not isinstance(value.get("checkpoint"), dict)
                ):
                    raise ValueError()
                checkpoint = value["checkpoint"]
                if (
                    re.fullmatch(r"[a-f0-9]{32}", checkpoint.get("checkpoint_id", "")) is None
                    or re.fullmatch(r"[a-f0-9]{64}", checkpoint.get("source_digest", "")) is None
                    or checkpoint.get("through_sequence") != value["through_sequence"]
                    or type(checkpoint.get("source_revision")) is not int
                    or checkpoint["source_revision"] < 1
                    or any(type(checkpoint.get(key)) is not int or checkpoint[key] < 0 for key in (
                        "kept_message_count", "omitted_message_count", "decisions_count",
                        "receipts_count", "byte_length",
                    ))
                    or not 1 <= len(value["provider_items"]) <= 41
                    or not 1 <= checkpoint["byte_length"] <= 16000
                    or checkpoint["byte_length"] != len(_encoded(value["provider_items"]).encode())
                ):
                    raise ValueError()
                for item in value["provider_items"]:
                    if (
                        type(item) is not dict or set(item) != {"type", "role", "content"}
                        or item["type"] != "message" or item["role"] not in {"user", "assistant"}
                        or type(item["content"]) is not list or len(item["content"]) != 1
                        or type(item["content"][0]) is not dict
                        or set(item["content"][0]) != {"type", "text"}
                        or item["content"][0]["type"] != (
                            "input_text" if item["role"] == "user" else "output_text"
                        )
                        or not isinstance(item["content"][0]["text"], str)
                    ):
                        raise ValueError()
                decisions = value.get("retained_decisions", [])
                receipts = value.get("historical_receipts", [])
                steering = value.get("historical_steering", [])
                if (
                    type(decisions) is not list or len(decisions) > 16
                    or any(type(item) is not str or len(item.encode()) > 1024 for item in decisions)
                    or type(receipts) is not list or len(receipts) > 1000
                    or any(type(item) is not dict or re.fullmatch(
                        r"[a-f0-9]{32}", item.get("run_id", "")
                    ) is None for item in receipts)
                    or type(steering) is not list or len(steering) > 1000
                ):
                    raise ValueError()
                for item in steering:
                    _validate_steering_record(item)
                if len({_steering_identity(item) for item in steering}) != len(steering):
                    raise ValueError()
            except (ValueError, TypeError):
                raise StateError("context_checkpoint_invalid") from None
            return row, value
        return None

    def context_snapshot(self, ref):
        self._ref(ref)
        with self._transaction(ref.temporary) as connection:
            self._session(connection, ref)
            return self._context_snapshot(connection, ref)

    def _context_snapshot(self, connection, ref):
        count, revision = connection.execute(
            "SELECT count(*),coalesce(max(sequence),0) FROM messages WHERE session_id=?",
            (ref.session_id,),
        ).fetchone()
        rows = connection.execute(
            "SELECT * FROM messages WHERE session_id=? AND role='system' AND substr(text,1,?)=? "
            "ORDER BY sequence DESC LIMIT 1", (ref.session_id, len(_PREFIX), _PREFIX),
        ).fetchall()
        found = self._read_checkpoint(rows)
        return {
            "revision": revision,
            "message_count": count,
            "checkpoint": found[1]["checkpoint"] if found else None,
        }

    @staticmethod
    def _eligible_context_rows(connection, rows, *, exclude_run_id=None):
        if not rows:
            return []
        statuses = dict(connection.execute(
            "SELECT run_id,status FROM runs WHERE session_id=?", (rows[0]["session_id"],)
        ))
        return [
            row for row in rows if row["role"] in {"user", "assistant"}
            and (exclude_run_id is None or row["run_id"] != exclude_run_id)
            and (row["run_id"] is None or statuses.get(row["run_id"]) == "completed")
        ]

    @staticmethod
    def _receipt_ledger(connection, ref, *, through_sequence=None):
        """Keep exact IDs/statuses/digests and action outcomes; no output text."""
        rows = SessionContextMixin._context_rows(connection, ref)
        previous = SessionContextMixin._read_checkpoint(rows)
        inherited = previous[1].get("historical_receipts", []) if previous else []
        ledger = list(inherited)
        known = {item["run_id"] for item in ledger}
        for run in connection.execute(
            "SELECT * FROM runs WHERE session_id=? ORDER BY created_ns,run_id", (ref.session_id,)
        ):
            if run["receipt_json"] is None:
                continue
            if run["run_id"] in known:
                continue
            if through_sequence is not None:
                first = connection.execute(
                    "SELECT min(sequence) FROM messages WHERE session_id=? AND run_id=?",
                    (ref.session_id, run["run_id"]),
                ).fetchone()[0]
                if first is None or first > through_sequence:
                    continue
            receipt = json.loads(run["receipt_json"])
            actions = receipt.get("action_receipts", [])
            # Other own/synthetic run receipts can have a distinct payload. They
            # remain covered by the exact digest instead of being reinterpreted.
            item = {
                "run_id": run["run_id"], "status": run["status"], "outcome": run["outcome"],
                "receipt_sha256": hashlib.sha256(run["receipt_json"].encode()).hexdigest(),
                "permits_replay": False,
            }
            if isinstance(actions, list):
                item["actions"] = [
                    {key: action[key] for key in (
                        "tool_name", "operation_id", "succeeded", "verified", "effect_status",
                        "output_sha256", "output_bytes",
                    ) if key in action}
                    for action in actions if isinstance(action, dict)
                ]
            ledger.append(item)
        return ledger

    @staticmethod
    def _steering_ledger(connection, ref, rows, *, exclude_run_id=None):
        """Preserve corrections and uncertainty across compaction and new bindings.

        A receipt labels a stored observation, never a retrospective network ACK.
        A correction after a rewind cutoff remains historical data and is not
        applied as a new instruction to the branch.
        """
        from jarvis_core.state import StateError

        previous = SessionContextMixin._read_checkpoint(rows)
        inherited = previous[1].get("historical_steering", []) if previous else []
        count, size = connection.execute(
            "SELECT count(*),coalesce(sum(length(CAST(e.payload_json AS BLOB))),0) "
            "FROM events e JOIN runs r ON e.run_id=r.run_id WHERE r.session_id=? "
            "AND e.kind IN ('steering.intent','steering.receipt') "
            "AND (? IS NULL OR r.run_id<>?)",
            (ref.session_id, exclude_run_id, exclude_run_id),
        ).fetchone()
        if count > 2000 or size + sum(len(row["text"].encode()) for row in rows) > 8 * 1024 * 1024:
            raise StateError("context_history_budget_exceeded")
        observed, receipts = {}, {}
        try:
            for event in connection.execute(
                "SELECT e.* FROM events e JOIN runs r ON e.run_id=r.run_id "
                "WHERE r.session_id=? AND e.kind IN ('steering.intent','steering.receipt') "
                "AND (? IS NULL OR r.run_id<>?) ORDER BY r.created_ns,r.run_id,e.sequence",
                (ref.session_id, exclude_run_id, exclude_run_id),
            ):
                payload = json.loads(event["payload_json"])
                if (type(payload) is not dict or type(payload.get("request_id")) is not str
                        or re.fullmatch(r"[a-f0-9]{32}", payload["request_id"]) is None
                        or payload.get("permits_replay") is not False):
                    raise ValueError()
                key = (ref.session_id, event["run_id"], payload["request_id"])
                if event["kind"] == "steering.intent":
                    record = {"source_session_id": ref.session_id, "run_id": event["run_id"],
                              "request_id": payload["request_id"],
                              "intent_event_id": event["event_id"],
                              "receipt_event_id": None, "status": "unknown",
                              "message": payload.get("message"),
                              "message_sha256": payload.get("message_sha256"),
                              "thread_id": payload.get("thread_id"),
                              "turn_id": payload.get("turn_id"),
                              "historical_ack_established": False, "permits_replay": False,
                              "instructions_current": False}
                    _validate_steering_record(record)
                    if key in observed:
                        raise ValueError()
                    observed[key] = record
                else:
                    if key not in observed or key in receipts or payload.get("status") not in {
                        "accepted", "unknown", "rejected", "failed",
                    }:
                        raise ValueError()
                    receipts[key] = event["event_id"]
                    observed[key]["receipt_event_id"] = event["event_id"]
                    observed[key]["status"] = payload["status"]
            merged = {_steering_identity(record): dict(record) for record in inherited}
            for key, record in observed.items():
                if key in merged and merged[key] != record:
                    raise ValueError()
                merged[key] = record
        except (ValueError, TypeError, UnicodeError):
            raise StateError("context_steering_record_invalid") from None
        return list(merged.values())

    def compact_context(self, ref, *, expected_revision, operation_id, budget_bytes=12000,
                        keep_recent=12, retained_decisions=None):
        """Publish a bounded extractive checkpoint, never edit prior history.

        The first eligible user request, owner-selected decisions, and all run
        receipt identities/outcomes are mandatory. If these cannot fit, fail
        before mutation. Additional recent messages fit whole or are omitted.
        """
        from jarvis_core.state import StateError

        self._ref(ref)
        if type(budget_bytes) is not int or not 4096 <= budget_bytes <= 16000:
            raise StateError("invalid_context_budget")
        if type(keep_recent) is not int or not 1 <= keep_recent <= 40:
            raise StateError("invalid_context_recent_limit")
        if retained_decisions is not None and (
            not isinstance(retained_decisions, (list, tuple)) or len(retained_decisions) > 16
        ):
            raise StateError("invalid_context_decisions")
        decisions = []
        for value in retained_decisions or ():
            self._text(value, nonempty=True)
            if len(value.encode()) > 1024:
                raise StateError("invalid_context_decisions")
            decisions.append(value)
        digest = _digest({"kind": "compact", "session_id": ref.session_id,
                          "temporary": ref.temporary, "expected_revision": expected_revision,
                          "budget_bytes": budget_bytes, "keep_recent": keep_recent,
                          "retained_decisions": retained_decisions})
        with self._transaction(ref.temporary) as connection:
            known = self._known_context_operation(connection, ref, operation_id, digest)
            if known is not None:
                return known["result"]["context"]
            rows = self._context_idle(connection, ref, expected_revision)
            if not rows:
                raise StateError("context_empty")
            if len(rows) + 2 > self.limits.max_messages_per_session:
                raise StateError("capacity_exceeded")
            previous = self._read_checkpoint(rows)
            if previous:
                decisions = list(dict.fromkeys([
                    *previous[1].get("retained_decisions", []), *decisions,
                ]))
            if len(decisions) > 16:
                raise StateError("invalid_context_decisions")
            eligible = self._eligible_context_rows(connection, rows)
            first = next((row for row in eligible if row["role"] == "user"), None)
            ledger = self._receipt_ledger(connection, ref)
            steering = self._steering_ledger(connection, ref, rows)
            source_digest = _digest({
                "messages": [dict(row) for row in rows], "receipts": ledger,
                "steering": steering,
            })
            checkpoint_id = uuid4().hex
            capsule = {
                "checkpoint_id": checkpoint_id, "source_session_id": ref.session_id,
                "source_revision": expected_revision, "source_digest": source_digest,
                "retained_decisions": decisions,
                "initial_request": (
                    {"message_id": first["message_id"], "text": first["text"]}
                    if first is not None else None
                ),
                "historical_receipts": ledger,
                "historical_steering": steering,
                "history_preserved": True, "permits_replay": False,
            }
            capsule_item = _wire("user", _CONTEXT_NOTICE + _encoded(capsule))
            items = [capsule_item]
            if len(_encoded(items).encode()) > budget_bytes:
                raise StateError("context_essentials_exceed_budget")
            selected = []
            for row in reversed(eligible[-keep_recent:]):
                if first is not None and row["message_id"] == first["message_id"]:
                    continue
                item = _wire(row["role"], row["text"])
                proposed = [capsule_item, item, *reversed(selected)]
                if len(_encoded(proposed).encode()) > budget_bytes:
                    break
                selected.append(item)
            items += list(reversed(selected))
            kept = len(selected) + (1 if first is not None else 0)
            checkpoint = {
                "checkpoint_id": checkpoint_id, "source_revision": expected_revision,
                "source_digest": source_digest, "through_sequence": expected_revision,
                "kept_message_count": kept, "omitted_message_count": len(eligible) - kept,
                "byte_length": len(_encoded(items).encode()),
                "decisions_count": len(decisions), "receipts_count": len(ledger),
            }
            payload = {
                "kind": "jarvis.session.compaction.v1", "through_sequence": expected_revision,
                "checkpoint": checkpoint, "provider_items": items,
                "retained_decisions": decisions, "historical_receipts": ledger,
                "historical_steering": steering,
            }
            text = self._text(_PREFIX + _encoded(payload))
            self._insert_message(connection, ref, "system", text, None)
            connection.execute("UPDATE sessions SET provider_thread_id=NULL WHERE session_id=?",
                               (ref.session_id,))
            snapshot = self._context_snapshot(connection, ref)
            snapshot["revision"] += 1
            snapshot["message_count"] += 1
            self._record_context_operation(
                connection, ref, operation_id, "compact", digest,
                {"context": snapshot, "session": self._context_session_wire(connection, ref)},
            )
            return snapshot

    def context_for_provider(self, ref, *, exclude_run_id=None, max_bytes=16000, max_items=100):
        """Return own bounded history, with checkpoints injected as user data.

        A checkpoint's essential capsule is never silently dropped to make room
        for a long tail. It is refreshed by an explicit compaction if needed.
        """
        from jarvis_core.state import StateError

        self._ref(ref)
        if type(max_bytes) is not int or not 4096 <= max_bytes <= 131072:
            raise StateError("invalid_context_budget")
        if type(max_items) is not int or not 2 <= max_items <= 100:
            raise StateError("invalid_context_recent_limit")
        with self._transaction(ref.temporary) as connection:
            self._session(connection, ref)
            rows = self._context_rows(connection, ref)
            found = self._read_checkpoint(rows)
            items, after = (
                (list(found[1]["provider_items"]), found[0]["sequence"]) if found else ([], 0)
            )
            steering = self._steering_ledger(connection, ref, rows, exclude_run_id=exclude_run_id)
            covered = {_steering_identity(record) for record in (
                found[1].get("historical_steering", []) if found else [])}
            pending = [record for record in steering if _steering_identity(record) not in covered]
            if pending:
                items.append(_wire("user", _CONTEXT_NOTICE + _encoded({
                    "kind": "jarvis.session.steering_history.v1", "historical_steering": pending,
                    "permits_replay": False, "instructions_current": False,
                })))
            candidates = self._eligible_context_rows(
                connection, [row for row in rows if row["sequence"] > after],
                exclude_run_id=exclude_run_id,
            )
            # For an un-compacted session preserve the prior bounded, recent
            # behavior. A published capsule, however, may never be evicted.
            if len(_encoded(items).encode()) > max_bytes or len(items) > max_items:
                raise StateError("context_checkpoint_exceeds_budget")
            selected = []
            for row in reversed(candidates):
                item = _wire(row["role"], row["text"])
                proposed = [*items, item, *reversed(selected)]
                if len(proposed) > max_items or len(_encoded(proposed).encode()) > max_bytes:
                    break
                selected.append(item)
            return items + list(reversed(selected))

    def rewind_session(self, ref, *, expected_revision, operation_id, until_sequence, title=None,
                       _branch_kind="rewind"):
        """Continue before a chosen point in a new branch; never undo effects.

        All settled receipt metadata is carried as non-executable historical
        data, including effects after the chosen point. The original history is
        retained. Provider bindings, approvals and run identities are not copied.
        """
        from jarvis_core.state import SessionRef, StateError

        self._ref(ref)
        if _branch_kind not in {"fork", "rewind"}:
            raise StateError("invalid_context_branch_kind")
        if type(until_sequence) is not int or until_sequence < (0 if _branch_kind == "fork" else 1):
            raise StateError("invalid_page")
        if _branch_kind == "fork" and until_sequence != expected_revision:
            raise StateError("invalid_context_cutoff")
        if title is not None:
            title = self._text(title, nonempty=True)
        digest = _digest({"kind": _branch_kind, "session_id": ref.session_id,
                          "temporary": ref.temporary, "expected_revision": expected_revision,
                          "until_sequence": until_sequence, "title": title})
        with self._transaction(ref.temporary) as connection:
            known = self._known_context_operation(connection, ref, operation_id, digest)
            if known is not None:
                new = SessionRef(known["result"]["session"]["session_id"], ref.temporary)
                return (self._session_record(self._session(connection, new), ref.temporary),
                        known["result"]["lineage"])
            rows = self._context_idle(connection, ref, expected_revision)
            if len(rows) + 1 > self.limits.max_messages_per_session:
                raise StateError("capacity_exceeded")
            parent = self._session(connection, ref)
            target = next((row for row in rows if row["sequence"] == until_sequence), None)
            if target is None and not (_branch_kind == "fork" and not rows):
                raise StateError("message_not_found")
            if _branch_kind == "rewind" and target["role"] not in {"user", "assistant"}:
                raise StateError("invalid_context_cutoff")
            eligible = self._eligible_context_rows(connection, rows)
            copied = [row for row in eligible if row["sequence"] <= until_sequence]
            if len(copied) > 1000 or sum(len(row["text"].encode()) for row in copied) > 262144:
                raise StateError("fork_context_too_large")
            self._capacity(connection, "SELECT count(*) FROM sessions", (),
                           self.limits.max_sessions)
            ledger = self._receipt_ledger(connection, ref)
            steering = self._steering_ledger(connection, ref, rows)
            previous = self._read_checkpoint(rows)
            decisions = (
                previous[1].get("retained_decisions", [])
                if previous and previous[0]["sequence"] <= until_sequence else []
            )
            first = next((row for row in copied if row["role"] == "user"), None)
            lineage = {
                "kind": _branch_kind, "source_session_id": ref.session_id,
                "source_revision": expected_revision, "until_sequence": until_sequence,
                "source_digest": _digest({
                    "messages": [dict(row) for row in rows], "receipts": ledger,
                    "steering": steering,
                }),
                "effects_reverted": False, "permissions_inherited": False,
                "provider_thread_inherited": False,
            }
            capsule = {
                "lineage": lineage, "historical_receipts": ledger,
                "historical_steering": steering,
                "retained_decisions": decisions,
                "initial_request": (
                    {"message_id": first["message_id"], "text": first["text"]}
                    if first is not None else None
                ),
                "permits_replay": False,
                "notice": "El corte cambia el contexto, no revierte efectos externos.",
            }
            capsule_item = _wire("user", _CONTEXT_NOTICE + _encoded(capsule))
            if len(_encoded([capsule_item]).encode()) > 12000:
                raise StateError("context_essentials_exceed_budget")
            new = SessionRef(uuid4().hex, ref.temporary)
            connection.execute("INSERT INTO sessions VALUES(?, ?, 0, ?, NULL, ?, ?)",
                               (new.session_id, title or parent["title"], time.time_ns(),
                                parent["agent_id"], ref.session_id))
            if _branch_kind == "fork" and not (copied or ledger or steering or decisions):
                self._record_context_operation(
                    connection, ref, operation_id, "fork", digest,
                    {"session": self._context_session_wire(connection, new), "lineage": lineage},
                )
                return self._session_record(self._session(connection, new), ref.temporary), lineage
            if len(copied) + 2 > self.limits.max_messages_per_session:
                raise StateError("capacity_exceeded")
            for row in copied:
                self._insert_message(connection, new, row["role"], row["text"], None)
            self._insert_message(connection, new, "system",
                                 _LINEAGE_PREFIX + self._json(lineage), None)
            through = len(copied) + 1
            # The provider's checkpoint carries the rewind warning and all
            # receipt identities; the selected transcript stays ordinary rows.
            items = [capsule_item]
            selected_count = 1 if first is not None else 0
            for row in reversed(copied):
                if first is not None and row["message_id"] == first["message_id"]:
                    continue
                item = _wire(row["role"], row["text"])
                proposed = [capsule_item, item, *items[1:]]
                if len(proposed) > 40 or len(_encoded(proposed).encode()) > 12000:
                    break
                items.insert(1, item)
                selected_count += 1
            checkpoint = {
                "checkpoint_id": uuid4().hex, "source_revision": through,
                "source_digest": lineage["source_digest"], "through_sequence": through,
                "kept_message_count": selected_count,
                "omitted_message_count": len(copied) - selected_count,
                "byte_length": len(_encoded(items).encode()), "decisions_count": len(decisions),
                "receipts_count": len(ledger),
            }
            self._insert_message(connection, new, "system", _PREFIX + self._json({
                "kind": "jarvis.session.compaction.v1", "through_sequence": through,
                "checkpoint": checkpoint, "provider_items": items,
                "historical_receipts": ledger, "retained_decisions": decisions,
                "historical_steering": steering,
            }), None)
            self._record_context_operation(
                connection, ref, operation_id, _branch_kind, digest,
                {"session": self._context_session_wire(connection, new), "lineage": lineage},
            )
            return self._session_record(self._session(connection, new), ref.temporary), lineage
