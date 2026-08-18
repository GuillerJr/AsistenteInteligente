from datetime import UTC, datetime, timedelta

import pytest

from aegis_core.ipc.protocol import (
    FreshnessStatus,
    IpcAuthenticator,
    NonceWindow,
)

SECRET = bytes.fromhex("11" * 32)
NOW = datetime(2026, 8, 18, 15, 0, tzinfo=UTC)


def test_request_and_response_are_mutually_authenticated() -> None:
    authenticator = IpcAuthenticator(SECRET)
    request = authenticator.create_request("health", now=NOW, nonce="a" * 32)
    response = authenticator.create_response(
        request,
        ok=True,
        payload={"status": "ok"},
        now=NOW,
    )

    assert authenticator.verify_request(request) is True
    assert authenticator.verify_response(response) is True
    assert response.request_id == request.request_id
    assert response.request_nonce == request.nonce


def test_request_tampering_invalidates_authentication() -> None:
    authenticator = IpcAuthenticator(SECRET)
    request = authenticator.create_request("health", now=NOW, nonce="b" * 32)
    tampered = request.model_copy(update={"method": "runtime.info"})

    assert authenticator.verify_request(tampered) is False


def test_different_secret_cannot_verify_request() -> None:
    request = IpcAuthenticator(SECRET).create_request("health", now=NOW, nonce="c" * 32)

    assert IpcAuthenticator(bytes.fromhex("22" * 32)).verify_request(request) is False


def test_hex_secret_parser_rejects_non_canonical_input() -> None:
    with pytest.raises(ValueError, match="lowercase"):
        IpcAuthenticator.from_hex("AA" * 32)


def test_nonce_window_rejects_stale_and_replayed_requests() -> None:
    authenticator = IpcAuthenticator(SECRET)
    window = NonceWindow(clock_skew=timedelta(seconds=30))
    current = authenticator.create_request("health", now=NOW, nonce="d" * 32)
    stale = authenticator.create_request("health", now=NOW - timedelta(seconds=31), nonce="e" * 32)

    assert window.accept(current, now=NOW) is FreshnessStatus.ACCEPTED
    assert window.accept(current, now=NOW) is FreshnessStatus.REPLAYED
    assert window.accept(stale, now=NOW) is FreshnessStatus.STALE


def test_nonce_window_fails_closed_at_capacity() -> None:
    authenticator = IpcAuthenticator(SECRET)
    window = NonceWindow(clock_skew=timedelta(seconds=30), max_entries=1)
    first = authenticator.create_request("health", now=NOW, nonce="f" * 32)
    second = authenticator.create_request("health", now=NOW, nonce="0" * 32)

    assert window.accept(first, now=NOW) is FreshnessStatus.ACCEPTED
    assert window.accept(second, now=NOW) is FreshnessStatus.CAPACITY_REACHED


def test_nonce_cannot_replay_at_clock_skew_boundary() -> None:
    authenticator = IpcAuthenticator(SECRET)
    window = NonceWindow(clock_skew=timedelta(seconds=30))
    request = authenticator.create_request("health", now=NOW, nonce="1" * 32)

    assert window.accept(request, now=NOW) is FreshnessStatus.ACCEPTED
    assert window.accept(request, now=NOW + timedelta(seconds=30)) is FreshnessStatus.STALE
