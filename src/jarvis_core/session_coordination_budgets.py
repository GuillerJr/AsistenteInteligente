"""Atomic token/cost reservations with exact decimal accounting and custody.

These are ceilings enforced before dispatch, not claims that a provider reports
billable cost. Missing provider usage/cost remains unknown and keeps custody.
The host must feed trusted provider usage; model text is never an accounting
source. A reservation has one run identity and dispatch is never retryable.
"""

from __future__ import annotations

from decimal import Decimal, localcontext

from jarvis_core.session_coordination_types import (
    CoordinationError,
    digest,
    identity,
    integer,
    money,
    ref_value,
)
from jarvis_core.state import SessionRef


class CoordinationBudgets:
    @staticmethod
    def _reservation_row(connection, reservation_id):
        identity(reservation_id)
        row = connection.execute("SELECT * FROM reservations WHERE reservation_id=?",
                                 (reservation_id,)).fetchone()
        if row is None:
            raise CoordinationError("coordination_reservation_not_found")
        return row

    @staticmethod
    def _reservation_value(row, temporary):
        return {key: row[key] for key in (
            "reservation_id", "run_id", "session_id", "owner_id", "parent_reservation_id",
            "reserved_tokens", "reserved_cost", "actual_tokens", "actual_cost", "status",
            "effect_status", "revision", "created_ns", "settled_ns",
        )} | {"temporary": temporary, "cleanup_confirmed": bool(row["cleanup_confirmed"]),
             "permits_replay": False, "usage_known": row["actual_tokens"] is not None,
             "cost_known": row["actual_cost"] is not None}

    @staticmethod
    def _budget_row(connection, session_id):
        row = connection.execute("SELECT * FROM budget_limits WHERE session_id=?",
                                 (session_id,)).fetchone()
        if row is None:
            raise CoordinationError("coordination_budget_not_configured")
        return row

    def _charge(self, connection, row, dimension, ancestors=()):
        """Inclusive envelope; child allocations never add twice to a parent."""
        if row["reservation_id"] in ancestors or len(ancestors) > self.limits.max_depth:
            raise CoordinationError("coordination_reservation_lineage_invalid")
        with localcontext() as context:
            context.prec = 100
            reserved, actual = row["reserved_" + dimension], row["actual_" + dimension]
            if dimension == "cost":
                reserved = None if reserved is None else Decimal(reserved)
                actual = None if actual is None else Decimal(actual)
            children = connection.execute(
                "SELECT * FROM reservations WHERE parent_reservation_id=?",
                (row["reservation_id"],)).fetchall()
            charges = [self._charge(connection, child, dimension,
                                    (*ancestors, row["reservation_id"])) for child in children]
            known = actual is not None and all(value is not None for value in charges)
            measured = actual + sum(charges) if known else None
            closed = row["status"] in {"settled", "cancelled"} and row["cleanup_confirmed"]
            descendants_closed = all(child["status"] in {"settled", "cancelled"}
                                     and child["cleanup_confirmed"] for child in children)
            if closed and descendants_closed and known:
                return measured
            if reserved is None:
                return None
            return max(reserved, measured) if measured is not None else reserved

    def _session_commitment(self, connection, session_id, dimension):
        with localcontext() as context:
            context.prec = 100
            rows = connection.execute(
                "SELECT r.* FROM reservations r LEFT JOIN reservations p "
                "ON r.parent_reservation_id=p.reservation_id WHERE r.session_id=? "
                "AND (p.reservation_id IS NULL OR p.session_id<>r.session_id)",
                (session_id,)).fetchall()
            charges = [self._charge(connection, row, dimension) for row in rows]
            return None if any(value is None for value in charges) else sum(charges)

    def _measured(self, connection, row, dimension, ancestors=()):
        if row["reservation_id"] in ancestors or len(ancestors) > self.limits.max_depth:
            raise CoordinationError("coordination_reservation_lineage_invalid")
        actual = row["actual_" + dimension]
        if actual is None:
            return None
        if dimension == "cost":
            actual = Decimal(actual)
        children = connection.execute("SELECT * FROM reservations WHERE parent_reservation_id=?",
                                      (row["reservation_id"],)).fetchall()
        measured = [self._measured(connection, child, dimension,
                                   (*ancestors, row["reservation_id"])) for child in children]
        return None if any(value is None for value in measured) else actual + sum(measured)

    def _budget_value(self, connection, ref):
        row = self._budget_row(connection, ref.session_id)
        rows = connection.execute("SELECT * FROM reservations WHERE session_id=? ORDER BY "
                                  "created_ns,reservation_id", (ref.session_id,)).fetchall()
        tokens = self._session_commitment(connection, ref.session_id, "tokens")
        cost = self._session_commitment(connection, ref.session_id, "cost")
        roots = connection.execute(
            "SELECT r.* FROM reservations r LEFT JOIN reservations p "
            "ON r.parent_reservation_id=p.reservation_id WHERE r.session_id=? "
            "AND (p.reservation_id IS NULL OR p.session_id<>r.session_id)",
            (ref.session_id,)).fetchall()
        with localcontext() as context:
            context.prec = 100
            observed_tokens = [self._measured(connection, item, "tokens") for item in roots]
            observed_cost = [self._measured(connection, item, "cost") for item in roots]
            unknown_tokens = any(value is None for value in observed_tokens)
            unknown_cost = any(value is None for value in observed_cost)
            measured_tokens = None if unknown_tokens else sum(observed_tokens)
            measured_cost = None if unknown_cost else format(sum(observed_cost), "f")
        return {**ref_value(ref), "token_limit": row["token_limit"],
                "cost_limit": row["cost_limit"], "currency": row["currency"],
                "revision": row["revision"], "committed_tokens": tokens,
                "committed_cost": None if cost is None else format(cost, "f"),
                "actual_tokens": measured_tokens,
                "actual_cost": measured_cost,
                "usage_known": not unknown_tokens, "cost_known": not unknown_cost,
                "over_token_limit": (tokens is not None and row["token_limit"] is not None
                                     and tokens > row["token_limit"]),
                "over_cost_limit": (cost is not None and row["cost_limit"] is not None
                                    and cost > Decimal(row["cost_limit"])),
                "reservations": [self._reservation_value(item, ref.temporary) for item in rows]}

    def configure_budget(self, client_id, ref, *, token_limit=None, cost_limit=None,
                         currency="USD", expected_revision=0):
        ref_value(ref)
        if token_limit is not None:
            integer(token_limit)
        cost_limit = money(cost_limit)
        if currency != "USD":
            raise CoordinationError("coordination_currency_unsupported")
        integer(expected_revision)
        self.assert_idle(ref)
        with self._transaction(ref.temporary) as connection:
            self._access(connection, client_id, ref, write=True, owner=True)
            row = connection.execute("SELECT * FROM budget_limits WHERE session_id=?",
                                     (ref.session_id,)).fetchone()
            if row is None:
                if expected_revision != 0:
                    raise CoordinationError("coordination_revision_changed")
            else:
                self._cas(row, expected_revision)
            for dimension, ceiling in (("tokens", token_limit), ("cost", cost_limit)):
                if ceiling is None:
                    continue
                used = self._session_commitment(connection, ref.session_id, dimension)
                if used is None or used > (Decimal(ceiling) if dimension == "cost" else ceiling):
                    raise CoordinationError("coordination_budget_below_commitment")
            self._authorize(client_id, "budget.configure", ref,
                            {"token_limit": token_limit, "cost_limit": cost_limit,
                             "currency": currency, "expected_revision": expected_revision})
            connection.execute("INSERT INTO budget_limits VALUES(?,?,?,?,1) "
                               "ON CONFLICT(session_id) "
                               "DO UPDATE SET token_limit=excluded.token_limit,cost_limit="
                               "excluded.cost_limit,currency=excluded.currency,"
                               "revision=budget_limits.revision+1",
                               (ref.session_id, token_limit, cost_limit, currency))
            return self._budget_value(connection, ref)

    def get_budget(self, client_id, ref):
        ref_value(ref)
        with self._transaction(ref.temporary) as connection:
            self._access(connection, client_id, ref)
            return self._budget_value(connection, ref)

    def _within_session_budget(self, connection, ref, tokens, cost):
        budget = self._budget_row(connection, ref.session_id)
        with localcontext() as context:
            context.prec = 100
            for dimension, proposed in (("tokens", tokens), ("cost", cost)):
                ceiling = budget["token_limit" if dimension == "tokens" else "cost_limit"]
                if ceiling is None:
                    continue
                used = self._session_commitment(connection, ref.session_id, dimension)
                if proposed is None or used is None:
                    raise CoordinationError("coordination_budget_usage_unknown")
                if dimension == "cost":
                    proposed, ceiling = Decimal(proposed), Decimal(ceiling)
                if used + proposed > ceiling:
                    raise CoordinationError("coordination_budget_exceeded")

    def _within_parent_budget(self, connection, parent, tokens, cost):
        with localcontext() as context:
            context.prec = 100
            children = connection.execute(
                "SELECT * FROM reservations WHERE parent_reservation_id=?",
                (parent["reservation_id"],)).fetchall()
            if len(children) >= self.limits.max_children:
                raise CoordinationError("coordination_child_limit_exceeded")
            for dimension, proposed in (("tokens", tokens), ("cost", cost)):
                envelope = parent["reserved_" + dimension]
                if envelope is None:
                    continue
                actual = parent["actual_" + dimension]
                used = [self._charge(connection, child, dimension) for child in children]
                if proposed is None or any(value is None for value in used):
                    raise CoordinationError("coordination_budget_usage_unknown")
                if dimension == "cost":
                    envelope, proposed = Decimal(envelope), Decimal(proposed)
                    actual = None if actual is None else Decimal(actual)
                # Before parent's reconciliation its own direct usage remains
                # unknown, so child caps are held inside the reviewed envelope.
                if sum(used) + proposed + (actual if actual is not None else 0) > envelope:
                    raise CoordinationError("coordination_parent_budget_exceeded")

    def reserve_budget(self, client_id, ref, *, reservation_id, run_id, tokens=None, cost=None,
                       parent_reservation_id=None):
        ref_value(ref)
        identity(reservation_id)
        identity(run_id)
        if tokens is not None:
            integer(tokens)
        cost = money(cost)
        if parent_reservation_id is not None:
            identity(parent_reservation_id)
        arguments = {"reservation_id": reservation_id, "run_id": run_id,
                     "tokens": tokens, "cost": cost,
                     "parent_reservation_id": parent_reservation_id}
        request_digest = digest(arguments)
        with self._transaction(ref.temporary) as connection:
            link = self._access(connection, client_id, ref, write=True)
            previous = connection.execute("SELECT * FROM reservations WHERE reservation_id=? "
                                          "OR run_id=?", (reservation_id, run_id)).fetchall()
            if previous:
                if (len(previous) != 1 or previous[0]["request_digest"] != request_digest
                        or previous[0]["session_id"] != ref.session_id
                        or previous[0]["owner_id"] != client_id):
                    raise CoordinationError("coordination_reservation_conflict")
                return self._reservation_value(previous[0], ref.temporary)
            if connection.execute("SELECT 1 FROM run_requests WHERE run_id=? OR reservation_id=?",
                                  (run_id, reservation_id)).fetchone() is not None:
                raise CoordinationError("coordination_reservation_retired")
            if connection.execute("SELECT count(*) FROM run_requests").fetchone()[0] >= (
                    self.limits.max_reservations):
                raise CoordinationError("coordination_capacity_exceeded")
            if connection.execute(
                "SELECT 1 FROM reservations WHERE session_id=? AND "
                "(status='unknown' OR effect_status='unknown' OR "
                "(status IN('settled','cancelled') AND cleanup_confirmed=0))",
                (ref.session_id,)).fetchone() is not None:
                raise CoordinationError("coordination_budget_custody_pending")
            self._within_session_budget(connection, ref, tokens, cost)
            if parent_reservation_id is not None:
                parent = self._reservation_row(connection, parent_reservation_id)
                parent_ref = SessionRef(parent["session_id"], ref.temporary)
                parent_link = self._access(connection, client_id, parent_ref, write=True)
                if (parent["status"] not in {"reserved", "dispatched"}
                        or link["parent_session_id"] != parent_ref.session_id
                        or link["workspace_id"] != parent_link["workspace_id"]):
                    raise CoordinationError("coordination_reservation_parent_invalid")
                self._within_parent_budget(connection, parent, tokens, cost)
            self._authorize(client_id, "budget.reserve", ref, arguments)
            connection.execute(
                "INSERT INTO reservations VALUES(?,?,?,?,?,?,?,?,NULL,NULL,NULL,'reserved',"
                "'known',0,1,?,NULL)",
                (reservation_id, run_id, ref.session_id, client_id,
                 parent_reservation_id, request_digest,
                 tokens, cost, self.clock_ns()))
            connection.execute("INSERT INTO run_requests VALUES(?,?)", (run_id, reservation_id))
            return self._reservation_value(self._reservation_row(connection, reservation_id),
                                           ref.temporary)

    def get_reservation(self, client_id, ref, reservation_id):
        ref_value(ref)
        with self._transaction(ref.temporary) as connection:
            self._access(connection, client_id, ref)
            row = self._reservation_row(connection, reservation_id)
            if row["session_id"] != ref.session_id:
                raise CoordinationError("coordination_access_denied")
            return self._reservation_value(row, ref.temporary)

    def dispatch_reservation(self, client_id, ref, *, reservation_id, expected_revision):
        ref_value(ref)
        with self._transaction(ref.temporary) as connection:
            self._access(connection, client_id, ref, write=True)
            row = self._reservation_row(connection, reservation_id)
            if row["session_id"] != ref.session_id or row["owner_id"] != client_id:
                raise CoordinationError("coordination_access_denied")
            self._cas(row, expected_revision)
            if row["status"] != "reserved":
                raise CoordinationError("coordination_dispatch_not_repeatable")
            if connection.execute(
                "SELECT 1 FROM reservations WHERE session_id=? AND reservation_id<>? "
                "AND (status IN('dispatched','unknown') OR (status IN('settled','cancelled') "
                "AND cleanup_confirmed=0) OR effect_status='unknown')",
                (ref.session_id, reservation_id)).fetchone() is not None:
                raise CoordinationError("coordination_budget_custody_pending")
            self._authorize(client_id, "budget.dispatch", ref,
                            {"reservation_id": reservation_id, "run_id": row["run_id"],
                             "expected_revision": expected_revision})
            connection.execute("UPDATE reservations SET status='dispatched',revision=revision+1 "
                               "WHERE reservation_id=?", (reservation_id,))
            return self._reservation_value(self._reservation_row(connection, reservation_id),
                                           ref.temporary)

    def reconcile_budget(self, client_id, ref, *, reservation_id, actual_tokens=None,
                         actual_cost=None, effect_status="known", cleanup_confirmed=False,
                         status="settled", expected_revision):
        ref_value(ref)
        if actual_tokens is not None:
            integer(actual_tokens)
        actual_cost = money(actual_cost)
        if status not in {"settled", "cancelled", "unknown"}:
            raise CoordinationError("coordination_reservation_status_invalid")
        if effect_status not in {"known", "unknown"} or type(cleanup_confirmed) is not bool:
            raise CoordinationError("coordination_reservation_evidence_invalid")
        arguments = {"reservation_id": reservation_id, "actual_tokens": actual_tokens,
                     "actual_cost": actual_cost, "effect_status": effect_status,
                     "cleanup_confirmed": cleanup_confirmed, "status": status,
                     "expected_revision": expected_revision}
        with self._transaction(ref.temporary) as connection:
            self._access(connection, client_id, ref, write=True)
            row = self._reservation_row(connection, reservation_id)
            if row["session_id"] != ref.session_id or row["owner_id"] != client_id:
                raise CoordinationError("coordination_access_denied")
            self._cas(row, expected_revision)
            if row["status"] == "reserved" and status == "settled":
                raise CoordinationError("coordination_reservation_not_dispatched")
            if row["cleanup_confirmed"] and not cleanup_confirmed:
                raise CoordinationError("coordination_cleanup_regression")
            if ((row["actual_tokens"] is not None and row["actual_tokens"] != actual_tokens)
                    or (row["actual_cost"] is not None and (actual_cost is None
                        or Decimal(row["actual_cost"]) != Decimal(actual_cost)))):
                raise CoordinationError("coordination_usage_conflict")
            # Once an effect became uncertain, ordinary usage settlement cannot
            # turn it known or make the run replayable. Cleanup is separate.
            if row["effect_status"] == "unknown":
                effect_status = "unknown"
            if effect_status == "unknown":
                status = "unknown"
            self._authorize(client_id, "budget.reconcile", ref, arguments)
            connection.execute(
                "UPDATE reservations SET actual_tokens=?,actual_cost=?,usage_digest=?,status=?,"
                "effect_status=?,cleanup_confirmed=?,revision=revision+1,settled_ns=? "
                "WHERE reservation_id=?",
                (actual_tokens, actual_cost, digest(arguments), status, effect_status,
                 int(cleanup_confirmed), self.clock_ns(), reservation_id))
            return self._reservation_value(self._reservation_row(connection, reservation_id),
                                           ref.temporary)
