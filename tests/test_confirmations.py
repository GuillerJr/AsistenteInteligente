from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from aegis_core.contracts import AgentRole, PolicyDecision, ToolAuthorization, ToolCall
from aegis_core.tools.broker import PolicyContext
from aegis_core.tools.confirmations import ConfirmationError, OneTimeConfirmationStore
from aegis_core.tools.defaults import build_default_tool_broker, default_policy_context

NOW = datetime(2026, 8, 18, 14, 0, tzinfo=UTC)


def _call(call_id: str = "call-network") -> ToolCall:
    return ToolCall(
        call_id=call_id,
        tool_name="network_discover_hosts",
        arguments={"target": "192.168.1.0/24", "ports": [22, 443]},
        requested_by=AgentRole.CODE_SECURITY,
    )


def _pending(call: ToolCall, root: Path) -> ToolAuthorization:
    return build_default_tool_broker().authorize(call, default_policy_context(root))


def _context(root: Path, store: OneTimeConfirmationStore) -> PolicyContext:
    base = default_policy_context(root)
    return PolicyContext(
        workspace_root=root,
        network_scopes=base.network_scopes,
        confirmation_store=store,
        now=NOW,
    )


def test_digest_binds_unique_call_id() -> None:
    assert _call("call-1").digest() != _call("call-2").digest()


def test_confirmation_is_consumed_exactly_once(tmp_path: Path) -> None:
    call = _call()
    broker = build_default_tool_broker()
    pending = _pending(call, tmp_path)
    store = OneTimeConfirmationStore()
    store.issue(call, pending, approved_by="local-user", now=NOW)
    context = _context(tmp_path, store)

    first = broker.authorize(call, context)
    replay = broker.authorize(call, context)

    assert first.decision is PolicyDecision.ALLOW
    assert first.reason_code == "confirmation_consumed"
    assert replay.decision is PolicyDecision.DENY
    assert replay.reason_code == "confirmation_replayed"


def test_confirmation_consumption_is_atomic_under_concurrency(tmp_path: Path) -> None:
    call = _call()
    broker = build_default_tool_broker()
    pending = _pending(call, tmp_path)
    store = OneTimeConfirmationStore()
    store.issue(call, pending, approved_by="local-user", now=NOW)
    context = _context(tmp_path, store)

    with ThreadPoolExecutor(max_workers=8) as pool:
        decisions = tuple(pool.map(lambda _: broker.authorize(call, context), range(16)))

    assert sum(item.decision is PolicyDecision.ALLOW for item in decisions) == 1
    assert sum(item.reason_code == "confirmation_replayed" for item in decisions) == 15


def test_confirmation_rejects_modified_tool_call(tmp_path: Path) -> None:
    call = _call()
    pending = _pending(call, tmp_path)
    modified = call.model_copy(
        update={"arguments": {"target": "192.168.1.0/24", "ports": [22, 8443]}}
    )
    store = OneTimeConfirmationStore()

    with pytest.raises(ConfirmationError, match="does not match"):
        store.issue(modified, pending, approved_by="local-user", now=NOW)


def test_confirmation_rejects_excessive_ttl(tmp_path: Path) -> None:
    call = _call()
    pending = _pending(call, tmp_path)

    with pytest.raises(ConfirmationError, match="TTL"):
        OneTimeConfirmationStore().issue(
            call,
            pending,
            approved_by="local-user",
            ttl=timedelta(minutes=6),
            now=NOW,
        )


def test_confirmation_is_not_valid_before_issue_time(tmp_path: Path) -> None:
    call = _call()
    broker = build_default_tool_broker()
    pending = _pending(call, tmp_path)
    store = OneTimeConfirmationStore()
    store.issue(call, pending, approved_by="local-user", now=NOW)
    base = default_policy_context(tmp_path)
    context = PolicyContext(
        workspace_root=tmp_path,
        network_scopes=base.network_scopes,
        confirmation_store=store,
        now=NOW - timedelta(seconds=1),
    )

    authorization = broker.authorize(call, context)

    assert authorization.decision is PolicyDecision.DENY
    assert authorization.reason_code == "confirmation_not_yet_valid"


def test_expired_confirmation_cannot_revive_after_clock_rollback(tmp_path: Path) -> None:
    call = _call()
    broker = build_default_tool_broker()
    pending = _pending(call, tmp_path)
    store = OneTimeConfirmationStore()
    store.issue(
        call,
        pending,
        approved_by="local-user",
        ttl=timedelta(seconds=1),
        now=NOW,
    )
    base = default_policy_context(tmp_path)

    expired = broker.authorize(
        call,
        PolicyContext(
            workspace_root=tmp_path,
            network_scopes=base.network_scopes,
            confirmation_store=store,
            now=NOW + timedelta(seconds=2),
        ),
    )
    rolled_back = broker.authorize(call, _context(tmp_path, store))

    assert expired.reason_code == "confirmation_expired"
    assert rolled_back.reason_code == "confirmation_expired"


def test_revoked_confirmation_cannot_be_consumed(tmp_path: Path) -> None:
    call = _call()
    broker = build_default_tool_broker()
    pending = _pending(call, tmp_path)
    store = OneTimeConfirmationStore()
    grant = store.issue(call, pending, approved_by="local-user", now=NOW)
    assert store.revoke(grant.grant_id) is True

    authorization = broker.authorize(call, _context(tmp_path, store))

    assert authorization.decision is PolicyDecision.DENY
    assert authorization.reason_code == "confirmation_revoked"


def test_non_pending_authorization_cannot_be_approved(tmp_path: Path) -> None:
    call = ToolCall(
        call_id="call-runtime",
        tool_name="system_describe_runtime",
        arguments={},
        requested_by=AgentRole.CODE_SECURITY,
    )
    allowed = build_default_tool_broker().authorize(call, default_policy_context(tmp_path))

    with pytest.raises(ConfirmationError, match="not pending"):
        OneTimeConfirmationStore().issue(call, allowed, approved_by="local-user", now=NOW)


def test_unknown_confirmation_store_result_fails_closed(tmp_path: Path) -> None:
    class InvalidStore:
        def consume(self, call_digest: str, *, now: datetime) -> object:
            del call_digest, now
            return object()

    call = _call()
    base = default_policy_context(tmp_path)
    context = PolicyContext(
        workspace_root=tmp_path,
        network_scopes=base.network_scopes,
        confirmation_store=InvalidStore(),  # type: ignore[arg-type]
        now=NOW,
    )

    authorization = build_default_tool_broker().authorize(call, context)

    assert authorization.decision is PolicyDecision.DENY
    assert authorization.reason_code == "confirmation_store_error"
