"""Local opt-in attention outbox; never requests TCC or repeats an effect.

This mixin only records intents and native delivery receipts. A host bridge must
validate the exact claim immediately before UNUserNotifications, use ``id`` as
the native request identifier, and settle it once. Restart changes in-flight
dispatch to unknown; it never requeues or pretends delivery succeeded. Resume is
one-shot navigation metadata and cannot start runs, replay tools or send prompts.
"""

from __future__ import annotations

import hashlib
import json
from uuid import uuid4


class ProductNotifications:
    def _preference_row(self, connection, scope, actor):
        row = connection.execute(
            "SELECT * FROM product_records WHERE session_key=? AND client_id=? "
            "AND kind='notification_preferences'",
            (self._session_key(scope), actor.client_id),
        ).fetchone()
        return None if row is None else dict(row)

    def _preferences(self, connection, scope, actor):
        row = self._preference_row(connection, scope, actor)
        if row is None:
            return {"revision": 0, "enabled": False, "silenced": False}
        return {"revision": row["revision"], **json.loads(row["payload_json"])}

    def configure_notifications(self, scope, actor, *, expected_revision, enabled, silenced):
        from jarvis_core.product_interactions import _fail, _revision

        _revision(expected_revision)
        if type(enabled) is not bool or type(silenced) is not bool:
            _fail("notification_preferences_invalid")
        payload = {"enabled": enabled, "silenced": silenced}
        with self._tx(scope, actor, write=True) as connection:
            row = self._preference_row(connection, scope, actor)
            if row is None:
                if expected_revision != 0:
                    _fail("revision_changed")
                row = self._put(connection, scope, actor, "notification_preferences", payload)
            else:
                self._cas(row, expected_revision)
                row = self._change(connection, row, payload=payload)
            if not enabled or silenced:
                # A native request already handed off may remain uncertain.
                # The host removes pending native requests on this receipt.
                connection.execute(
                    "UPDATE product_records SET status='revoked',revision=revision+1,updated_ns=? "
                    "WHERE session_key=? AND client_id=? AND kind='notification' "
                    "AND status IN ('queued','dispatching')",
                    (self._now(), self._session_key(scope), actor.client_id),
                )
            return {"revision": row["revision"], **payload}

    def request_attention(self, scope, actor, *, kind, correlation_id, ttl_seconds=900):
        """Host explicit attention only, with generic private lock-screen text."""
        from jarvis_core.product_interactions import _encoded, _fail, _identity

        _identity(correlation_id)
        if type(kind) is not str or kind not in {
            "question", "run_completed", "run_failed", "resume_needed",
        }:
            _fail("attention_kind_invalid")
        expiry = self._expiry(ttl_seconds, 3600)
        with self._tx(scope, actor, write=True) as connection:
            preferences = self._preferences(connection, scope, actor)
            if not preferences["enabled"] or preferences["silenced"]:
                _fail("notifications_disabled")
            if kind == "question":
                question = self._row(connection, scope, actor, correlation_id, "question")
                if self._view(question)["status"] != "pending":
                    _fail("question_closed")
                expiry = min(expiry, question["expires_ns"])
            elif correlation_id != scope.run_id:
                _fail("attention_correlation_invalid")
            identity = hashlib.sha256(_encoded([
                "jarvis-local-attention/1", self._scope_key(scope), actor.client_id,
                kind, correlation_id,
            ]).encode()).hexdigest()
            known = connection.execute(
                "SELECT * FROM product_records WHERE id=?", (identity,),
            ).fetchone()
            if known is not None:
                return self._view(known)
            payload = {"attention_kind": kind, "correlation_id": correlation_id,
                       "title": "Jarvis requiere tu atención",
                       "body": "Abre Jarvis para revisar la actividad de tu chat.",
                       "preference_revision": preferences["revision"]}
            return self._view(self._put(connection, scope, actor, "notification", payload,
                                        identity=identity, status="queued", expires_ns=expiry))

    def _check_attention(self, connection, scope, actor, row, *, require_pending_question=True):
        from jarvis_core.product_interactions import _fail

        payload = json.loads(row["payload_json"])
        preferences = self._preferences(connection, scope, actor)
        if (not preferences["enabled"] or preferences["silenced"]
                or preferences["revision"] != payload["preference_revision"]):
            _fail("notification_preference_changed")
        if row["expires_ns"] <= self._now():
            _fail("notification_expired")
        if payload["attention_kind"] == "question" and require_pending_question:
            question = self._row(connection, scope, actor, payload["correlation_id"], "question")
            if self._view(question)["status"] != "pending":
                _fail("question_closed")
        return payload

    def claim_notification(self, scope, actor, identity, expected_revision):
        from jarvis_core.product_interactions import _fail

        with self._tx(scope, actor, write=True) as connection:
            row = self._row(connection, scope, actor, identity, "notification")
            self._cas(row, expected_revision)
            if row["status"] != "queued":
                _fail("notification_closed")
            payload = self._check_attention(connection, scope, actor, row)
            # A claim is volatile proof of this dispatch attempt, not permission
            # to infer, execute tools, or create a new run.
            claim = uuid4().hex
            payload["claim_digest"] = hashlib.sha256(claim.encode()).hexdigest()
            row = self._change(connection, row, payload=payload, status="dispatching")
            return {"id": identity, "revision": row["revision"], "claim": claim,
                    "title": payload["title"], "body": payload["body"],
                    "session_id": scope.session_id, "run_id": scope.run_id}

    def _claim(self, connection, scope, actor, identity, expected_revision, claim):
        from jarvis_core.product_interactions import _fail, _identity

        _identity(claim)
        row = self._row(connection, scope, actor, identity, "notification")
        self._cas(row, expected_revision)
        payload = json.loads(row["payload_json"])
        if (row["status"] != "dispatching" or payload.get("claim_digest")
                != hashlib.sha256(claim.encode()).hexdigest()):
            _fail("notification_claim_invalid")
        return row

    def validate_notification_claim(self, scope, actor, identity, expected_revision, claim):
        with self._tx(scope, actor, write=True) as connection:
            row = self._claim(connection, scope, actor, identity, expected_revision, claim)
            self._check_attention(connection, scope, actor, row)
            return {"id": identity, "valid": True, "revision": row["revision"]}

    def settle_notification(self, scope, actor, identity, expected_revision, claim, *, outcome):
        from jarvis_core.product_interactions import _fail

        if type(outcome) is not str or outcome not in {"delivered", "failed", "unknown"}:
            _fail("notification_outcome_invalid")
        with self._tx(scope, actor, write=True) as connection:
            row = self._claim(connection, scope, actor, identity, expected_revision, claim)
            row = self._change(connection, row, status=outcome)
            return self._view(row)

    def resume_notification(self, scope, actor, identity, expected_revision):
        """Select only the correlated chat; never resume a tool/model execution."""
        from jarvis_core.product_interactions import _fail

        with self._tx(scope, actor, write=True) as connection:
            row = self._row(connection, scope, actor, identity, "notification")
            self._cas(row, expected_revision)
            if row["status"] not in {"queued", "delivered", "failed", "unknown"}:
                _fail("notification_closed")
            payload = self._check_attention(connection, scope, actor, row,
                                            require_pending_question=False)
            row = self._change(connection, row, status="resumed")
            result = {"id": identity, "revision": row["revision"],
                      "session_id": scope.session_id, "run_id": scope.run_id,
                      "navigation_only": True, "grants_tool_approval": False}
            if payload["attention_kind"] == "question":
                result["question_id"] = payload["correlation_id"]
            return result
