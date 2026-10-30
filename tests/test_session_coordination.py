"""Synthetic local identities and directories; no owner profile or credentials."""

import sqlite3
import threading
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

import pytest

from jarvis_core.session_coordination import SessionCoordinationStore
from jarvis_core.session_coordination_types import CoordinationError, CoordinationLimits
from jarvis_core.state import SessionRef, StateError, StateStore

OWNER, OTHER, READER, STRANGER = (character * 64 for character in "abcd")


class Harness:
    def __init__(self, tmp_path, *, limits=None, authority=True):
        self.directory = tmp_path.resolve() / "synthetic-profile"
        self.directory.mkdir(mode=0o700)
        self.state = StateStore(self.directory / "state.sqlite3")
        self.clients = {OWNER: "owner", OTHER: "owner", READER: "read", STRANGER: "owner"}
        self.busy = set()
        self.authorizations = []
        self.now = 1_000_000_000
        self.database = self.directory / "coordination.sqlite3"
        self.store = self.open_store(limits=limits, authority=authority)
        self.workspace_path = tmp_path.resolve() / "selected-workspace"
        self.workspace_path.mkdir(mode=0o700)
        self.workspace_id = uuid4().hex

    def client(self, client_id, *, write=False):
        if client_id not in self.clients or (write and self.clients[client_id] != "owner"):
            raise CoordinationError("client_not_admitted")
        return {"client_id": client_id, "scope": self.clients[client_id]}

    def idle(self, ref):
        if ref in self.busy:
            raise CoordinationError("session_busy")

    def authorize(self, client_id, operation, ref, arguments):
        self.client(client_id, write=True)
        self.authorizations.append((client_id, operation, ref, arguments))

    def open_store(self, *, limits=None, authority=True):
        return SessionCoordinationStore(
            self.database, assert_client=self.client, assert_session=self.state.get_session,
            assert_idle=self.idle, authorize_change=self.authorize if authority else None,
            clock_ns=lambda: self.now, limits=limits)

    def workspace(self, *, temporary=False):
        return self.store.register_workspace(
            OWNER, workspace_id=self.workspace_id, name="Trabajo", path=self.workspace_path,
            temporary=temporary)

    def session(self, *, temporary=False, parent=None):
        record = self.state.create_session(temporary=temporary, agent_id="jarvis")
        self.store.bind_session(OWNER, record.ref, workspace_id=self.workspace_id,
                                parent_ref=parent)
        return record.ref

    def close(self):
        self.store.close()
        self.state.close()


@pytest.fixture
def harness(tmp_path):
    value = Harness(tmp_path)
    yield value
    value.close()


def test_two_workspaces_are_selected_per_session_without_global_mutation(harness, tmp_path):
    h = harness
    first = h.workspace()
    ref = h.session()
    other_root = tmp_path.resolve() / "other-workspace"
    other_root.mkdir(mode=0o700)
    second_id = uuid4().hex
    second = h.store.register_workspace(
        OWNER, workspace_id=second_id, name="Otro", path=other_root)
    other = h.state.create_session().ref
    h.store.bind_session(OWNER, other, workspace_id=second_id)
    assert h.store.workspace_tools(OWNER, ref).root == h.workspace_path
    assert h.store.workspace_tools(OWNER, other).root == other_root
    assert first["identity"] != second["identity"]
    assert len(h.store.list_workspaces(OWNER)) == 2
    assert h.store.list_workspaces(STRANGER) == []
    with pytest.raises(CoordinationError, match="workspace_duplicate"):
        h.store.register_workspace(OWNER, workspace_id=uuid4().hex,
                                   name="Duplicado", path=h.workspace_path)


def test_physical_workspace_replacement_is_rejected(harness):
    h = harness
    h.workspace()
    ref = h.session()
    h.workspace_path.rename(h.workspace_path.with_name("original-selected-workspace"))
    h.workspace_path.mkdir(mode=0o700)
    with pytest.raises(CoordinationError, match="workspace_identity_changed"):
        h.store.workspace_tools(OWNER, ref)


def test_group_pin_members_are_one_cas_and_hidden_from_unrelated_clients(harness):
    h = harness
    h.workspace()
    first, second = h.session(), h.session()
    pinned = h.store.bind_session(OWNER, second, workspace_id=h.workspace_id,
                                 expected_revision=1, group="Proyecto", pinned=True)
    assert pinned["revision"] == 2
    assert h.store.list_sessions(OWNER)[0]["session_id"] == second.session_id
    assert h.store.list_sessions(OTHER) == []
    with pytest.raises(CoordinationError, match="access_denied"):
        h.store.get_session(OTHER, first)
    h.store.set_member(OWNER, first, member_id=OTHER, role="collaborate", expected_revision=1)
    h.store.set_member(OWNER, first, member_id=READER, role="read", expected_revision=2)
    assert len(h.store.list_sessions(OTHER)) == 1
    assert len(h.store.list_sessions(READER)) == 1
    assert h.store.list_sessions(OTHER, group="Proyecto") == []
    with pytest.raises(CoordinationError, match="revision_changed"):
        h.store.bind_session(OWNER, first, workspace_id=h.workspace_id, expected_revision=1)
    with pytest.raises(CoordinationError, match="access_denied"):
        h.store.set_member(OTHER, first, member_id=STRANGER, role="read", expected_revision=3)
    with pytest.raises(CoordinationError, match="client_not_admitted"):
        h.store.set_member(OWNER, first, member_id=READER, role="collaborate", expected_revision=3)


def test_changes_require_authority_even_with_owner_identity(tmp_path):
    h = Harness(tmp_path, authority=False)
    try:
        with pytest.raises(CoordinationError, match="authority_required"):
            h.workspace()
        assert h.store.list_workspaces(OWNER) == []
    finally:
        h.close()


def test_busy_session_cannot_change_workspace_or_members(harness):
    h = harness
    h.workspace()
    ref = h.session()
    h.busy.add(ref)
    with pytest.raises(CoordinationError, match="session_busy"):
        h.store.bind_session(OWNER, ref, workspace_id=h.workspace_id, expected_revision=1)
    with pytest.raises(CoordinationError, match="session_busy"):
        h.store.set_member(OWNER, ref, member_id=OTHER, role="read", expected_revision=1)


def test_workspace_change_revokes_collaboration_instead_of_transferring_access(harness, tmp_path):
    h = harness
    h.workspace()
    ref = h.session()
    h.store.set_member(OWNER, ref, member_id=OTHER, role="collaborate", expected_revision=1)
    alternate = tmp_path.resolve() / "newly-selected-workspace"
    alternate.mkdir(mode=0o700)
    alternate_id = uuid4().hex
    h.store.register_workspace(OWNER, workspace_id=alternate_id, name="Nuevo", path=alternate)
    moved = h.store.bind_session(OWNER, ref, workspace_id=alternate_id, expected_revision=2)
    assert moved["members"] == []
    with pytest.raises(CoordinationError, match="access_denied"):
        h.store.workspace_tools(OTHER, ref)


def test_temporary_sessions_and_failed_lookups_never_initialize_disk(harness):
    h = harness
    h.workspace(temporary=True)
    ref = h.session(temporary=True)
    h.store.configure_budget(OWNER, ref, token_limit=100, cost_limit="1")
    h.store.reserve_budget(OWNER, ref, reservation_id=uuid4().hex, run_id=uuid4().hex,
                           tokens=10, cost="0.1")
    with pytest.raises(CoordinationError, match="session_unbound"):
        unrelated = h.state.create_session(temporary=True).ref
        h.store.get_session(OWNER, unrelated)
    assert not h.database.exists()
    assert not h.database.with_name(h.database.name + ".lock").exists()
    assert not h.state.path.exists()


def test_children_have_no_inherited_members_and_are_bounded(tmp_path):
    h = Harness(tmp_path, limits=CoordinationLimits(max_children=1, max_depth=1))
    try:
        h.workspace()
        parent = h.session()
        h.store.set_member(OWNER, parent, member_id=OTHER, role="collaborate", expected_revision=1)
        child = h.session(parent=parent)
        assert h.store.get_session(OWNER, child)["members"] == []
        with pytest.raises(CoordinationError, match="access_denied"):
            h.store.get_session(OTHER, child)
        with pytest.raises(CoordinationError, match="child_limit_exceeded"):
            h.session(parent=parent)
        with pytest.raises(CoordinationError, match="child_limit_exceeded"):
            h.session(parent=child)
        with pytest.raises(CoordinationError, match="children_retained"):
            h.store.delete_session(OWNER, parent, expected_revision=2)
    finally:
        h.close()


def test_mailbox_is_durable_correlated_and_never_grants_authority(harness):
    h = harness
    h.workspace()
    source, target = h.session(), h.session()
    h.store.set_member(OWNER, target, member_id=OTHER, role="collaborate", expected_revision=1)
    request, correlation = uuid4().hex, uuid4().hex
    message = h.store.send_message(
        OWNER, source, recipient_ref=target, recipient_client_id=OTHER,
        request_id=request, correlation_id=correlation, body="Revisa el cambio; no hay permisos.")
    assert message["status"] == "pending"
    assert not message["grants_included"] and not message["effect_verified"]
    assert message["source_agent_id"] == "jarvis"
    inbox = h.store.inbox(OTHER, target)
    assert inbox["messages"] == [message]
    assert not inbox["acknowledgement_inferred"]
    # Reading a mailbox is not an ACK and cannot spend action authority.
    assert h.store.get_message(OWNER, source, message["message_id"])["status"] == "pending"
    before = len(h.authorizations)
    assert h.store.send_message(
        OWNER, source, recipient_ref=target, recipient_client_id=OTHER,
        request_id=request, correlation_id=correlation,
        body="Revisa el cambio; no hay permisos.") == message
    assert len(h.authorizations) == before
    h.store.close()
    h.store = h.open_store()
    assert h.store.inbox(OTHER, target)["messages"][0]["message_id"] == message["message_id"]
    ack = h.store.settle_message(OTHER, target, message_id=message["message_id"],
                                 status="acknowledged", expected_revision=1)
    assert ack["status"] == "acknowledged"
    reply = h.store.send_message(
        OTHER, target, recipient_ref=source, recipient_client_id=OWNER,
        request_id=uuid4().hex, correlation_id=correlation, body="Resultado del análisis.",
        parent_message_id=message["message_id"])
    assert reply["parent_message_id"] == message["message_id"]


def test_mailbox_rejects_cross_mode_cross_workspace_and_correlation_substitution(harness, tmp_path):
    h = harness
    h.workspace()
    source, target, third = h.session(), h.session(), h.session()
    message = h.store.send_message(
        OWNER, source, recipient_ref=target, recipient_client_id=OWNER,
        request_id=uuid4().hex, correlation_id=uuid4().hex, body="Uno")
    with pytest.raises(CoordinationError, match="correlation_conflict"):
        h.store.send_message(
            OWNER, source, recipient_ref=third, recipient_client_id=OWNER,
            request_id=uuid4().hex, correlation_id=message["correlation_id"], body="Otro")
    with pytest.raises(CoordinationError, match="parent_mismatch"):
        h.store.send_message(
            OWNER, source, recipient_ref=third, recipient_client_id=OWNER,
            request_id=uuid4().hex, correlation_id=message["correlation_id"], body="Otro",
            parent_message_id=message["message_id"])
    h.workspace(temporary=True)
    ephemeral = h.session(temporary=True)
    with pytest.raises(CoordinationError, match="mode_mismatch"):
        h.store.send_message(
            OWNER, source, recipient_ref=ephemeral, recipient_client_id=OWNER,
            request_id=uuid4().hex, correlation_id=uuid4().hex, body="No persistir")
    another_path = tmp_path.resolve() / "different-root"
    another_path.mkdir(mode=0o700)
    another_id = uuid4().hex
    h.store.register_workspace(OWNER, workspace_id=another_id, name="Aparte", path=another_path)
    h.store.bind_session(OWNER, third, workspace_id=another_id, expected_revision=1)
    with pytest.raises(CoordinationError, match="workspace_mismatch"):
        h.store.send_message(
            OWNER, source, recipient_ref=third, recipient_client_id=OWNER,
            request_id=uuid4().hex, correlation_id=uuid4().hex, body="No cruzar")


def test_message_expiration_and_retention_keep_idempotency_tombstone(harness):
    h = harness
    h.workspace()
    source, target = h.session(), h.session()
    parameters = {"recipient_ref": target, "recipient_client_id": OWNER,
                  "request_id": uuid4().hex, "correlation_id": uuid4().hex,
                  "body": "Un mensaje finito", "ttl_seconds": 1}
    sent = h.store.send_message(OWNER, source, **parameters)
    h.now += 2_000_000_000
    assert h.store.inbox(OWNER, target)["messages"] == []
    assert h.store.get_message(OWNER, source, sent["message_id"])["status"] == "expired"
    with pytest.raises(CoordinationError, match="message_terminal"):
        h.store.settle_message(OWNER, target, message_id=sent["message_id"],
                               status="acknowledged", expected_revision=1)
    assert h.store.prune_messages(OWNER, before_ns=h.now)["deleted"] == 1
    repeated = h.store.send_message(OWNER, source, **parameters)
    assert repeated["content_pruned"] and repeated["status"] == "expired"
    assert repeated["message_id"] == sent["message_id"]
    assert h.store.inbox(OWNER, target, include_terminal=True)["messages"] == []
    assert not repeated["permits_replay"]
    third = h.session()
    with pytest.raises(CoordinationError, match="correlation_conflict"):
        h.store.send_message(
            OWNER, source, recipient_ref=third, recipient_client_id=OWNER,
            request_id=uuid4().hex, correlation_id=parameters["correlation_id"], body="Otra ruta")


def test_revocation_removes_members_and_pending_messages_without_transfer(harness):
    h = harness
    h.workspace()
    source, target = h.session(), h.session()
    h.store.set_member(OWNER, target, member_id=OTHER, role="collaborate", expected_revision=1)
    sent = h.store.send_message(
        OWNER, source, recipient_ref=target, recipient_client_id=OTHER,
        request_id=uuid4().hex, correlation_id=uuid4().hex, body="Pendiente")
    del h.clients[OTHER]
    h.store.revoke_client(OTHER)
    assert h.store.get_message(OWNER, source, sent["message_id"])["status"] == "revoked"
    assert h.store.get_session(OWNER, target)["owner_id"] == OWNER
    h.clients[OTHER] = "owner"
    assert h.store.list_sessions(OTHER) == []
    with pytest.raises(CoordinationError, match="access_denied"):
        h.store.inbox(OTHER, target)


def test_atomic_token_and_exact_cost_ceilings_include_parallel_reservations(harness):
    h = harness
    h.workspace()
    ref = h.session()
    h.store.configure_budget(OWNER, ref, token_limit=100, cost_limit="0.3")
    barrier = threading.Barrier(2)

    def reserve():
        barrier.wait(timeout=5)
        try:
            h.store.reserve_budget(OWNER, ref, reservation_id=uuid4().hex, run_id=uuid4().hex,
                                   tokens=60, cost="0.2")
            return "reserved"
        except CoordinationError as error:
            return error.code

    with ThreadPoolExecutor(max_workers=2) as workers:
        futures = [workers.submit(reserve), workers.submit(reserve)]
        results = [future.result(timeout=10) for future in futures]
    assert sorted(results) == ["coordination_budget_exceeded", "reserved"]
    budget = h.store.get_budget(OWNER, ref)
    assert budget["committed_tokens"] == 60
    assert Decimal(budget["committed_cost"]) == Decimal("0.2")
    assert budget["actual_tokens"] is None and budget["actual_cost"] is None


@pytest.mark.parametrize("cost", [0.1, 1, True, "NaN", "1e-5", "-1", "0.0000000000001"])
def test_cost_never_accepts_float_or_rounded_accounting(harness, cost):
    h = harness
    h.workspace()
    ref = h.session()
    with pytest.raises(CoordinationError, match="cost_invalid"):
        h.store.configure_budget(OWNER, ref, cost_limit=cost)


def test_known_reconciliation_releases_unused_allocation_and_dispatch_never_repeats(harness):
    h = harness
    h.workspace()
    ref = h.session()
    h.store.configure_budget(OWNER, ref, token_limit=10, cost_limit="0.3")
    reservation_id, run_id = uuid4().hex, uuid4().hex
    reserved = h.store.reserve_budget(OWNER, ref, reservation_id=reservation_id,
                                      run_id=run_id, tokens=10, cost="0.3")
    dispatched = h.store.dispatch_reservation(OWNER, ref, reservation_id=reservation_id,
                                              expected_revision=reserved["revision"])
    with pytest.raises(CoordinationError, match="dispatch_not_repeatable"):
        h.store.dispatch_reservation(OWNER, ref, reservation_id=reservation_id,
                                     expected_revision=dispatched["revision"])
    settled = h.store.reconcile_budget(
        OWNER, ref, reservation_id=reservation_id, actual_tokens=2, actual_cost="0.1",
        cleanup_confirmed=True, expected_revision=dispatched["revision"])
    assert settled["status"] == "settled" and not settled["permits_replay"]
    budget = h.store.get_budget(OWNER, ref)
    assert budget["committed_tokens"] == 2
    assert Decimal(budget["committed_cost"]) == Decimal("0.1")
    second = h.store.reserve_budget(OWNER, ref, reservation_id=uuid4().hex, run_id=uuid4().hex,
                                    tokens=8, cost="0.2")
    assert second["status"] == "reserved"
    assert h.store.reserve_budget(OWNER, ref, reservation_id=reservation_id,
                                  run_id=run_id, tokens=10, cost="0.3")["status"] == "settled"


def test_tokens_only_budget_allows_new_run_when_chatgpt_cost_is_unknown(harness):
    h = harness
    h.workspace()
    ref = h.session()
    h.store.configure_budget(OWNER, ref, token_limit=10)
    reservation_id, run_id = uuid4().hex, uuid4().hex
    h.store.reserve_budget(OWNER, ref, reservation_id=reservation_id, run_id=run_id, tokens=10)
    h.store.dispatch_reservation(OWNER, ref, reservation_id=reservation_id, expected_revision=1)
    h.store.reconcile_budget(OWNER, ref, reservation_id=reservation_id, actual_tokens=2,
                             cleanup_confirmed=True, expected_revision=2)
    budget = h.store.get_budget(OWNER, ref)
    assert budget["actual_tokens"] == 2
    assert budget["actual_cost"] is None and budget["committed_cost"] is None
    assert not budget["cost_known"] and budget["cost_limit"] is None
    next_id = uuid4().hex
    h.store.reserve_budget(OWNER, ref, reservation_id=next_id, run_id=uuid4().hex, tokens=8)
    assert h.store.dispatch_reservation(OWNER, ref, reservation_id=next_id,
                                        expected_revision=1)["status"] == "dispatched"
    old = h.store.reserve_budget(OWNER, ref, reservation_id=reservation_id,
                                 run_id=run_id, tokens=10)
    assert old["status"] == "settled" and old["actual_cost"] is None
    with pytest.raises(CoordinationError, match="dispatch_not_repeatable"):
        h.store.dispatch_reservation(OWNER, ref, reservation_id=reservation_id,
                                     expected_revision=old["revision"])


def test_cost_only_budget_does_not_require_missing_token_usage(harness):
    h = harness
    h.workspace()
    ref = h.session()
    h.store.configure_budget(OWNER, ref, cost_limit="0.3")
    reservation_id = uuid4().hex
    h.store.reserve_budget(OWNER, ref, reservation_id=reservation_id, run_id=uuid4().hex,
                           cost="0.3")
    h.store.dispatch_reservation(OWNER, ref, reservation_id=reservation_id, expected_revision=1)
    h.store.reconcile_budget(OWNER, ref, reservation_id=reservation_id, actual_cost="0.1",
                             cleanup_confirmed=True, expected_revision=2)
    budget = h.store.get_budget(OWNER, ref)
    assert budget["actual_tokens"] is None and not budget["usage_known"]
    assert budget["actual_cost"] == "0.1"
    h.store.reserve_budget(OWNER, ref, reservation_id=uuid4().hex, run_id=uuid4().hex, cost="0.2")


def test_missing_usage_retains_reserved_dimensions_and_unknown_effect_keeps_custody(harness):
    h = harness
    h.workspace()
    ref = h.session()
    h.store.configure_budget(OWNER, ref, token_limit=100, cost_limit="1")
    reservation_id = uuid4().hex
    h.store.reserve_budget(OWNER, ref, reservation_id=reservation_id, run_id=uuid4().hex,
                           tokens=10, cost="0.3")
    h.store.dispatch_reservation(OWNER, ref, reservation_id=reservation_id, expected_revision=1)
    h.store.reconcile_budget(OWNER, ref, reservation_id=reservation_id, status="cancelled",
                             cleanup_confirmed=True, expected_revision=2)
    budget = h.store.get_budget(OWNER, ref)
    assert budget["actual_tokens"] is None
    assert budget["committed_tokens"] == 10
    assert Decimal(budget["committed_cost"]) == Decimal("0.3")
    # A distinct run may use the remaining reviewed balance. The prior request
    # is still non-replayable and its unknown usage is not substituted by zero.
    h.store.reserve_budget(OWNER, ref, reservation_id=uuid4().hex, run_id=uuid4().hex,
                           tokens=1, cost="0.1")
    reconciled = h.store.reconcile_budget(
        OWNER, ref, reservation_id=reservation_id, actual_tokens=4, actual_cost="0.2",
        effect_status="unknown", cleanup_confirmed=True, expected_revision=3)
    assert reconciled["status"] == "unknown"
    assert h.store.get_budget(OWNER, ref)["committed_tokens"] == 11
    with pytest.raises(CoordinationError, match="custody_pending"):
        h.store.reserve_budget(OWNER, ref, reservation_id=uuid4().hex, run_id=uuid4().hex,
                               tokens=1, cost="0.1")
    with pytest.raises(CoordinationError, match="custody_pending"):
        h.store.delete_session(OWNER, ref, expected_revision=1)


def test_restart_preserves_dispatched_unknown_without_replay(harness):
    h = harness
    h.workspace()
    ref = h.session()
    h.store.configure_budget(OWNER, ref, token_limit=100, cost_limit="1")
    reservation_id = uuid4().hex
    h.store.reserve_budget(OWNER, ref, reservation_id=reservation_id, run_id=uuid4().hex,
                           tokens=20, cost="0.5")
    h.store.dispatch_reservation(OWNER, ref, reservation_id=reservation_id, expected_revision=1)
    h.store.close()
    h.store = h.open_store()
    row = h.store.get_reservation(OWNER, ref, reservation_id)
    assert row["status"] == "unknown" and row["effect_status"] == "unknown"
    with pytest.raises(CoordinationError, match="dispatch_not_repeatable"):
        h.store.dispatch_reservation(OWNER, ref, reservation_id=reservation_id,
                                     expected_revision=row["revision"])


def test_reservation_owner_and_cleanup_are_required_at_dispatch(harness):
    h = harness
    h.workspace()
    ref = h.session()
    h.store.set_member(OWNER, ref, member_id=OTHER, role="collaborate", expected_revision=1)
    h.store.configure_budget(OWNER, ref, token_limit=100, cost_limit="1")
    first, second = uuid4().hex, uuid4().hex
    for reservation_id in (first, second):
        h.store.reserve_budget(OWNER, ref, reservation_id=reservation_id, run_id=uuid4().hex,
                               tokens=20, cost="0.2")
    with pytest.raises(CoordinationError, match="access_denied"):
        h.store.dispatch_reservation(OTHER, ref, reservation_id=first, expected_revision=1)
    h.store.dispatch_reservation(OWNER, ref, reservation_id=first, expected_revision=1)
    with pytest.raises(CoordinationError, match="custody_pending"):
        h.store.dispatch_reservation(OWNER, ref, reservation_id=second, expected_revision=1)
    h.store.reconcile_budget(OWNER, ref, reservation_id=first, actual_tokens=1,
                             actual_cost="0.1", cleanup_confirmed=False, expected_revision=2)
    with pytest.raises(CoordinationError, match="custody_pending"):
        h.store.dispatch_reservation(OWNER, ref, reservation_id=second, expected_revision=1)
    h.store.reconcile_budget(OWNER, ref, reservation_id=first, actual_tokens=1,
                             actual_cost="0.1", cleanup_confirmed=True, expected_revision=3)
    assert h.store.dispatch_reservation(OWNER, ref, reservation_id=second,
                                        expected_revision=1)["status"] == "dispatched"


def test_usage_overrun_is_recorded_truthfully_and_prevents_next_reservation(harness):
    h = harness
    h.workspace()
    ref = h.session()
    h.store.configure_budget(OWNER, ref, token_limit=10, cost_limit="0.1")
    reservation_id = uuid4().hex
    h.store.reserve_budget(OWNER, ref, reservation_id=reservation_id, run_id=uuid4().hex,
                           tokens=10, cost="0.1")
    h.store.dispatch_reservation(OWNER, ref, reservation_id=reservation_id, expected_revision=1)
    h.store.reconcile_budget(OWNER, ref, reservation_id=reservation_id, actual_tokens=11,
                             actual_cost="0.100000000001", cleanup_confirmed=True,
                             expected_revision=2)
    budget = h.store.get_budget(OWNER, ref)
    assert budget["actual_tokens"] == 11
    assert budget["actual_cost"] == "0.100000000001"
    assert budget["over_token_limit"] and budget["over_cost_limit"]
    with pytest.raises(CoordinationError, match="budget_exceeded"):
        h.store.reserve_budget(OWNER, ref, reservation_id=uuid4().hex, run_id=uuid4().hex,
                               tokens=1, cost="0.000000000001")


def test_explicit_session_retention_cannot_make_old_run_identity_dispatchable(harness):
    h = harness
    h.workspace()
    ref = h.session()
    h.store.configure_budget(OWNER, ref, token_limit=10)
    reservation_id, run_id = uuid4().hex, uuid4().hex
    h.store.reserve_budget(OWNER, ref, reservation_id=reservation_id, run_id=run_id, tokens=10)
    h.store.dispatch_reservation(OWNER, ref, reservation_id=reservation_id, expected_revision=1)
    h.store.reconcile_budget(OWNER, ref, reservation_id=reservation_id, actual_tokens=2,
                             cleanup_confirmed=True, expected_revision=2)
    h.store.delete_session(OWNER, ref, expected_revision=1)
    another = h.session()
    h.store.configure_budget(OWNER, another, token_limit=10)
    with pytest.raises(CoordinationError, match="reservation_retired"):
        h.store.reserve_budget(OWNER, another, reservation_id=uuid4().hex, run_id=run_id, tokens=1)


def test_parent_child_reservation_cannot_multiply_envelope(harness):
    h = harness
    h.workspace()
    parent = h.session()
    child, other_child = h.session(parent=parent), h.session(parent=parent)
    for ref in (parent, child, other_child):
        h.store.configure_budget(OWNER, ref, token_limit=100, cost_limit="1")
    parent_id = uuid4().hex
    h.store.reserve_budget(OWNER, parent, reservation_id=parent_id, run_id=uuid4().hex,
                           tokens=100, cost="1")
    h.store.reserve_budget(OWNER, child, reservation_id=uuid4().hex, run_id=uuid4().hex,
                           tokens=60, cost="0.6", parent_reservation_id=parent_id)
    with pytest.raises(CoordinationError, match="parent_budget_exceeded"):
        h.store.reserve_budget(OWNER, other_child, reservation_id=uuid4().hex, run_id=uuid4().hex,
                               tokens=60, cost="0.6", parent_reservation_id=parent_id)
    assert h.store.get_budget(OWNER, parent)["committed_tokens"] == 100
    assert h.store.get_budget(OWNER, child)["committed_tokens"] == 60


def test_foreign_db_and_unsafe_paths_fail_without_reading_owner_data(harness, tmp_path):
    h = harness
    h.workspace()
    foreign = h.directory / "foreign.sqlite3"
    with sqlite3.connect(foreign) as database:
        database.execute("CREATE TABLE sentinel(value TEXT)")
    foreign.chmod(0o600)
    store = SessionCoordinationStore(foreign, assert_client=h.client,
                                     assert_session=h.state.get_session, assert_idle=h.idle,
                                     authorize_change=h.authorize)
    try:
        with pytest.raises(CoordinationError, match="database_invalid"):
            store.list_workspaces(OWNER)
    finally:
        store.close()
    with pytest.raises(CoordinationError, match="path_invalid"):
        SessionCoordinationStore(Path("relative"), assert_client=h.client,
                                 assert_session=h.state.get_session, assert_idle=h.idle)
    # A fabricated reference cannot allocate metadata for a nonexistent session.
    with pytest.raises(StateError, match="session_not_found"):
        h.store.bind_session(OWNER, SessionRef(uuid4().hex), workspace_id=h.workspace_id)
