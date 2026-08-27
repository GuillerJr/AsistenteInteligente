from aegis_core.privacy import redact_for_remote


def test_redact_for_remote_removes_credentials_and_personal_identifiers() -> None:
    result = redact_for_remote(
        "Bearer abcdefghijklmnop; token=secret-value; owner@example.com; "
        "+593 99 123 4567; /Users/owner/private"
    )

    assert result.text == (
        "Bearer [REDACTED_CREDENTIAL]; token=[REDACTED_SECRET]; [REDACTED_EMAIL]; "
        "[REDACTED_PHONE]; /Users/[REDACTED_USER]/private"
    )
    assert result.categories == frozenset(
        {"authorization", "secret_assignment", "email", "phone", "local_user"}
    )


def test_redact_for_remote_preserves_network_addresses_and_normal_code() -> None:
    text = "Escanea 192.168.1.0/24 y revisa value = 42"

    result = redact_for_remote(text)

    assert result.text == text
    assert result.categories == frozenset()
