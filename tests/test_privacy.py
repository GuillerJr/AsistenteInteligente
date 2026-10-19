import json
from copy import deepcopy

import pytest

from aegis_core.privacy import redact_for_remote, sanitize_remote_reference, sensitive_topics


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


@pytest.mark.parametrize(
    ("text", "expected", "category"),
    [
        ("Mi tarjeta 4111 1111 1111 1111", "Mi tarjeta [REDACTED_CARD]", "payment_card"),
        ("pago con 5500-0000-0000-0004", "pago con [REDACTED_CARD]", "payment_card"),
        ("IBAN ES9121000418450200051332", "IBAN [REDACTED_BANK_ACCOUNT]", "bank_account"),
        ("cédula 1710034065", "cédula [REDACTED_NATIONAL_ID]", "national_id"),
        ("RUC 1710034065001", "RUC [REDACTED_NATIONAL_ID]", "national_id"),
        ("SSN 123-45-6789", "SSN [REDACTED_NATIONAL_ID]", "national_id"),
        ("DNI 12345678Z", "DNI [REDACTED_NATIONAL_ID]", "national_id"),
        ("llámame al 0991234567", "llámame al [REDACTED_PHONE]", "phone"),
        ("whatsapp +593991234567", "whatsapp [REDACTED_PHONE]", "phone"),
        ("estoy en -2.170998, -79.922359", "estoy en [REDACTED_LOCATION]", "location"),
        (
            "postgres://admin:hunter22@db.local/app",
            "postgres://[REDACTED_CREDENTIAL]@db.local/app",
            "url_credentials",
        ),
        (
            "jwt eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxIn0.c2lnbmF0dXJl",
            "jwt [REDACTED_CREDENTIAL]",
            "credential",
        ),
        ("key sk-ant-api03-abcdefghijkl", "key [REDACTED_CREDENTIAL]", "credential"),
        ("stripe sk_live_abcdefghijkl", "stripe [REDACTED_CREDENTIAL]", "credential"),
        ("hf hf_abcdefghijklmnopqrstu", "hf [REDACTED_CREDENTIAL]", "credential"),
        ("gitlab glpat-abcdefghijkl", "gitlab [REDACTED_CREDENTIAL]", "credential"),
        ("ruta /home/guille/x", "ruta /home/[REDACTED_USER]/x", "local_user"),
        ("ruta C:\\Users\\guille\\docs", "ruta C:\\Users\\[REDACTED_USER]\\docs", "local_user"),
    ],
)
def test_redact_for_remote_covers_identifiers_and_tokens(
    text: str, expected: str, category: str
) -> None:
    result = redact_for_remote(text)

    assert result.text == expected
    assert result.categories == frozenset({category})


@pytest.mark.parametrize(
    "text",
    [
        "Reunión el 2026-10-16 a las 10:30:45",
        "versión 1.2.3, commit 3f2a9c1d y tag v2026.10.16",
        "puerto 8080, pid 12345 y 443/tcp",
        "timestamp 1729100000000 y orden 1234567890123",
        "id 550e8400-e29b-41d4-a716-446655440000",
        "red 10.0.0.0/8 y host 172.16.4.20:5432",
        "tarjeta inválida 4111 1111 1111 1112",
        "cédula inválida 1710034066",
        "coordenada fuera de rango 95.123456, 200.123456",
    ],
)
def test_redact_for_remote_keeps_technical_and_invalid_values(text: str) -> None:
    result = redact_for_remote(text)

    assert result.text == text
    assert result.categories == frozenset()


def test_url_credentials_are_redacted_before_the_email_rule() -> None:
    result = redact_for_remote("conecta a https://user:pa55word@example.com/repo y avisa a a@b.co")

    assert result.text == (
        "conecta a https://[REDACTED_CREDENTIAL]@example.com/repo y avisa a [REDACTED_EMAIL]"
    )
    assert result.categories == frozenset({"url_credentials", "email"})


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        (
            "Escríbele a mi esposa Ana María López que llego tarde",
            "Escríbele a mi esposa [REDACTED_NAME] que llego tarde",
        ),
        ("Recuérdame llamar a mi amigo Carlos", "Recuérdame llamar a mi amigo [REDACTED_NAME]"),
        ("Agenda cita con la Dra. Pérez", "Agenda cita con la Dra. [REDACTED_NAME]"),
        ("my name is John Smith", "my name is [REDACTED_NAME]"),
        ("Saluda a Doña Rosa", "Saluda a Doña [REDACTED_NAME]"),
    ],
)
def test_cue_introduced_names_are_masked(text: str, expected: str) -> None:
    result = redact_for_remote(text)

    assert result.text == expected
    assert result.categories == frozenset({"person_name"})


def test_recognized_names_need_a_personal_context() -> None:
    public = redact_for_remote("¿Quién es Lionel Messi?", person_names=("Lionel Messi",))
    private = redact_for_remote(
        "Avísale a mi colega Laura Pérez y a Laura", person_names=("Laura", "Laura Pérez")
    )

    assert public.text == "¿Quién es Lionel Messi?"
    assert private.text == "Avísale a mi colega [REDACTED_NAME] y a [REDACTED_NAME]"
    assert private.categories == frozenset({"person_name"})


def test_assistant_and_vendor_names_are_never_masked() -> None:
    result = redact_for_remote("mi asistente Jarvis", person_names=("Jarvis",))

    assert result.text == "mi asistente Jarvis"


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Vivo en Av. Amazonas N24-15 y Colón, Quito", "Vivo en [REDACTED_ADDRESS], Quito"),
        ("Mi casa: Cdla. Kennedy Norte Mz 5 Villa 12", "Mi casa: [REDACTED_ADDRESS]"),
        ("Calle Rocafuerte #305, Guayaquil", "[REDACTED_ADDRESS], Guayaquil"),
        (
            "Ship to 1600 Pennsylvania Avenue, Washington",
            "Ship to [REDACTED_ADDRESS], Washington",
        ),
    ],
)
def test_street_addresses_with_a_number_are_masked(text: str, expected: str) -> None:
    result = redact_for_remote(text)

    assert result.text == expected
    assert result.categories == frozenset({"postal_address"})


def test_street_names_without_a_house_number_are_kept() -> None:
    text = "¿Cómo llego a la Avenida 9 de Octubre desde el Malecón?"

    assert redact_for_remote(text).text == text


@pytest.mark.parametrize(
    ("text", "categories"),
    [
        ("Me diagnosticaron diabetes, ¿qué como?", {"health"}),
        ("tengo ansiedad desde hace semanas", {"health"}),
        ("mi sueldo no alcanza, ¿cómo ahorro?", {"personal_finance"}),
        ("soy gay y quiero contárselo a mis padres", {"sexual_orientation"}),
        ("me arrestaron hace años, ¿afecta mi visa?", {"criminal_record"}),
        ("¿qué síntomas tiene la gripe?", set()),
        ("¿cuál es el precio del dólar hoy?", set()),
        ("its value is std::vector", set()),
    ],
)
def test_sensitive_topics_require_a_first_person_subject(
    text: str, categories: set[str]
) -> None:
    assert sensitive_topics(text) == frozenset(categories)
def test_clean_json_source_keeps_exact_lines_whitespace_and_numeric_spelling():
    text = '[\r\n  {"revision": 1, "amount": 1.00},\r\n  {"revision": 2, "amount": 41}\r\n]\r\n'
    sanitized = sanitize_remote_reference({
        "tool_name": "filesystem_read_text", "output": text,
        "metadata": {"path": "fixture.json", "truncated": False},
    })
    assert sanitized["output"] == text
    assert sanitized["output"].splitlines()[2] == '  {"revision": 2, "amount": 41}'
    assert sanitized["metadata"] == {"path": "fixture.json", "truncated": False}


def test_json_redaction_marks_changed_source_layout_and_keeps_nonsecret_evidence():
    text = '{\n  "\\u0074oken": "synthetic-private-value",\n  "current": 41\n}\n'
    sanitized = sanitize_remote_reference({
        "tool_name": "filesystem_read_text", "output": text,
        "metadata": {"path": "fixture.json", "truncated": False},
    })
    assert "synthetic-private-value" not in sanitized["output"]
    assert json.loads(sanitized["output"]) == {"token": "[REDACTED_SECRET]", "current": 41}
    assert sanitized["metadata"]["source_layout_changed"] is True
    assert sanitized["metadata"]["path"] == "fixture.json"
    assert sanitized["metadata"]["truncated"] is False


def test_duplicate_json_keys_cannot_hide_earlier_encoded_secret_on_preservation_path():
    text = '{"\\u0074oken":"shadowed-private-value","token":"[REDACTED_SECRET]","value":7}'
    cleaned = sanitize_remote_reference(text)
    assert "shadowed-private-value" not in cleaned
    assert json.loads(cleaned) == {"token": "[REDACTED_SECRET]", "value": 7}


def test_json_encoded_credential_values_remain_redacted():
    text = '{\n"note":"nvapi\\u002Dsynthetic-value-123456789",\n"value":7\n}'
    cleaned = sanitize_remote_reference(text)
    assert "synthetic-value" not in cleaned
    assert json.loads(cleaned)["value"] == 7


def text_result(text, **metadata):
    return {"call_id": "synthetic-read", "tool_name": "filesystem_read_text", "success": True,
            "output": text, "metadata": {"path": "fixture.txt", "truncated": False, **metadata}}


def test_source_lines_preserve_json_layout_blank_lines_and_exact_numeric_spelling():
    text = '[\r\n\r\n  {"amount": 1.00},\r\n  {"amount": 41}\r\n]\r\n'
    original = text_result(text, path="fixture.json", bytes_read=len(text.encode()))
    snapshot = deepcopy(original)
    cleaned = sanitize_remote_reference(original)
    assert original == snapshot
    assert cleaned["output"] == text and cleaned["metadata"] == original["metadata"]
    assert cleaned["source_lines"] == [
        {"line": 1, "text": "["}, {"line": 2, "text": ""},
        {"line": 3, "text": '  {"amount": 1.00},'},
        {"line": 4, "text": '  {"amount": 41}'}, {"line": 5, "text": "]"},
    ]


def test_source_lines_include_csv_header_and_blank_lines_without_interpreting_rows():
    text = 'name,value\n\n"example, quoted",1.00\nsecond,2\n'
    cleaned = sanitize_remote_reference(text_result(text, path="fixture.csv", truncated=True))
    assert cleaned["output"] == text
    assert cleaned["metadata"] == {"path": "fixture.csv", "truncated": True}
    assert cleaned["source_lines"] == [
        {"line": 1, "text": "name,value"}, {"line": 2, "text": ""},
        {"line": 3, "text": '"example, quoted",1.00'}, {"line": 4, "text": "second,2"},
    ]


@pytest.mark.parametrize("separator", ["\n", "\r", "\r\n", "\u2028"])
def test_line_preserving_secret_redaction_keeps_original_numbers(separator):
    text = separator.join(["heading", "", "token=synthetic-secret", "observed=41"])
    cleaned = sanitize_remote_reference(text_result(text))
    assert "synthetic-secret" not in json.dumps(cleaned)
    assert cleaned["metadata"].get("source_layout_changed") is None
    assert cleaned["source_lines"] == [
        {"line": 1, "text": "heading"}, {"line": 2, "text": ""},
        {"line": 3, "text": "token=[REDACTED_SECRET]"},
        {"line": 4, "text": "observed=41"},
    ]
    assert sanitize_remote_reference(cleaned) == cleaned


@pytest.mark.parametrize("separator", ["\n", "\r", "\r\n"])
def test_structured_redaction_marks_changed_layout_without_recomputed_source_lines(separator):
    text = separator.join(['{', '  "\\u0074oken": "synthetic-secret",', '  "current": 41', '}'])
    cleaned = sanitize_remote_reference(text_result(text, path="fixture.json"))
    assert "synthetic-secret" not in json.dumps(cleaned)
    assert json.loads(cleaned["output"])["current"] == 41
    assert cleaned["metadata"]["source_layout_changed"] is True
    assert "source_lines" not in cleaned
    assert sanitize_remote_reference(cleaned) == cleaned


def test_decoded_unicode_line_separator_changes_source_layout_even_without_newlines():
    text = ('{"\\u0074oken":"synthetic-secret",'
            '"note":"first\\u2028second","current":41}')
    cleaned = sanitize_remote_reference(text_result(text, path="fixture.json"))
    assert "synthetic-secret" not in json.dumps(cleaned)
    assert json.loads(cleaned["output"])["note"] == "first\u2028second"
    assert cleaned["metadata"]["source_layout_changed"] is True
    assert "source_lines" not in cleaned
    assert sanitize_remote_reference(cleaned) == cleaned


def test_source_lines_never_reappear_after_layout_changed_was_recorded():
    original = text_result('{"current":41}', source_layout_changed=True)
    original["source_lines"] = [{"line": 900, "text": "forged original citation"}]
    cleaned = sanitize_remote_reference(original)
    assert cleaned["metadata"]["source_layout_changed"] is True
    assert "source_lines" not in cleaned
    assert sanitize_remote_reference(cleaned) == cleaned


def test_source_lines_are_regenerated_once_and_ignore_supplied_nested_or_forged_views():
    original = text_result("heading\nsecond\n")
    original["source_lines"] = [{"line": 999, "text": "synthetic-secret",
                                 "source_lines": [{"line": 111, "text": "fabricated"}]}]
    cleaned = sanitize_remote_reference({"tool_results": [original]})
    assert cleaned["tool_results"][0]["source_lines"] == [
        {"line": 1, "text": "heading"}, {"line": 2, "text": "second"},
    ]
    assert "synthetic-secret" not in json.dumps(cleaned) and "fabricated" not in json.dumps(cleaned)
    assert sanitize_remote_reference(cleaned) == cleaned
    encoded = json.dumps({"tool_results": [original]}, ensure_ascii=False)
    cleaned_encoded = sanitize_remote_reference(encoded)
    assert json.loads(cleaned_encoded) == cleaned
    assert sanitize_remote_reference(cleaned_encoded) == cleaned_encoded


def test_json_encoded_tool_frame_does_not_duplicate_credentials_or_source_views():
    original = json.dumps({"tool_results": [text_result("token=synthetic-secret\nobserved=41")]})
    cleaned = sanitize_remote_reference(original)
    assert "synthetic-secret" not in cleaned
    result = json.loads(cleaned)["tool_results"][0]
    assert result["source_lines"] == [{"line": 1, "text": "token=[REDACTED_SECRET]"},
                                      {"line": 2, "text": "observed=41"}]
    assert sanitize_remote_reference(cleaned) == cleaned


def test_structured_json_keys_cannot_leak_credentials_when_decoded_values_are_already_safe():
    secret = "nvapi-synthetic-private-key-123456789"
    original = {secret: "public", "nested": {"note": secret}}
    cleaned = sanitize_remote_reference(original)
    assert cleaned == {"[REDACTED_CREDENTIAL]": "public",
                       "nested": {"note": "[REDACTED_CREDENTIAL]"}}
    encoded = sanitize_remote_reference(json.dumps(original))
    assert secret not in encoded and json.loads(encoded) == cleaned
    assert sanitize_remote_reference(encoded) == encoded


def test_json_file_containing_tool_shaped_data_keeps_its_original_output_and_line_layout():
    # A JSON file's body can resemble a result, but is source data. Adding another
    # derived view inside it would change the actual file text and destroy citations.
    body = text_result("nested source\nsecond")
    body["source_lines"] = [{"line": 99, "text": "field contained in the file"}]
    text = json.dumps(body, ensure_ascii=False, indent=2)
    cleaned = sanitize_remote_reference(text_result(text, path="fixture.json"))
    assert cleaned["output"] == text
    assert cleaned["metadata"].get("source_layout_changed") is None
    assert json.loads(cleaned["output"]) == body
    assert cleaned["source_lines"] == [
        {"line": line, "text": value} for line, value in enumerate(text.splitlines(), start=1)
    ]
    assert sanitize_remote_reference(cleaned) == cleaned


def test_strict_structured_reference_redacts_personal_data_without_breaking_source_lines():
    text = ("heading\nowner@example.com\n+593 99 123 4567\n/Users/owner/private\n"
            "token=synthetic-secret\nobserved=41")
    original = {"tool_results": [text_result(text, path="/Users/owner/fixture.txt")],
                "contact": {"owner@example.com": ["+593 99 123 4567", "/Users/owner/private"]}}
    cleaned = sanitize_remote_reference(original, personal_identifiers=True)
    serialized = json.dumps(cleaned, ensure_ascii=False)
    assert all(value not in serialized for value in (
        "owner@example.com", "+593 99 123 4567", "/Users/owner", "synthetic-secret",
    ))
    result = cleaned["tool_results"][0]
    assert result["metadata"]["path"] == "/Users/[REDACTED_USER]/fixture.txt"
    assert result["source_lines"] == [
        {"line": 1, "text": "heading"}, {"line": 2, "text": "[REDACTED_EMAIL]"},
        {"line": 3, "text": "[REDACTED_PHONE]"},
        {"line": 4, "text": "/Users/[REDACTED_USER]/private"},
        {"line": 5, "text": "token=[REDACTED_SECRET]"},
        {"line": 6, "text": "observed=41"},
    ]
    assert cleaned["contact"] == {"[REDACTED_EMAIL]": ["[REDACTED_PHONE]",
                                                      "/Users/[REDACTED_USER]/private"]}
    assert sanitize_remote_reference(cleaned, personal_identifiers=True) == cleaned
    encoded = sanitize_remote_reference(json.dumps(original), personal_identifiers=True)
    assert json.loads(encoded) == cleaned
    assert sanitize_remote_reference(encoded, personal_identifiers=True) == encoded
    default = sanitize_remote_reference(original)
    assert "owner@example.com" in json.dumps(default)
    assert "synthetic-secret" not in json.dumps(default)


def test_strict_json_source_retains_a_verified_line_view_with_all_sensitive_values_removed():
    text = json.dumps({"email": "owner@example.com", "phone": "+593 99 123 4567",
                       "path": "/Users/owner/private", "note": "nvapi-synthetic-secret-123456789"})
    cleaned = sanitize_remote_reference(text_result(text), personal_identifiers=True)
    assert cleaned["source_lines"] == [{"line": 1, "text": cleaned["output"]}]
    assert json.loads(cleaned["output"]) == {
        "email": "[REDACTED_EMAIL]", "phone": "[REDACTED_PHONE]",
        "path": "/Users/[REDACTED_USER]/private", "note": "[REDACTED_CREDENTIAL]",
    }
    assert sanitize_remote_reference(cleaned, personal_identifiers=True) == cleaned


@pytest.mark.parametrize("text", ["a" * 8192, "á" * 4096, "\n".join(["row"] * 128) + "\n", ""])
def test_small_utf8_source_fragment_boundaries_are_included_in_full(text):
    cleaned = sanitize_remote_reference(text_result(text))
    assert cleaned["source_lines"] == [
        {"line": line, "text": value} for line, value in enumerate(text.splitlines(), start=1)
    ]
    assert len(json.dumps(cleaned["source_lines"], ensure_ascii=False).encode()) <= 12_288


@pytest.mark.parametrize("text", [
    "a" * 8193, "á" * 4096 + "a", "\n".join(["row"] * 129),
    "nvapi-" + "a" * 9000, "token=abcd;" * 600, "\\" * 8192,
    "invalid utf8 surrogate: \ud800", b"not decoded UTF-8",
])
def test_large_expanding_or_non_utf8_sources_do_not_duplicate_the_output(text):
    cleaned = sanitize_remote_reference(text_result(text))
    assert "source_lines" not in cleaned
    assert cleaned["metadata"]["path"] == "fixture.txt"
    assert sanitize_remote_reference(cleaned) == cleaned


@pytest.mark.parametrize("overrides", [
    {"success": False}, {"success": 1}, {"success": None},
    {"tool_name": "terminal_run_template"}, {"metadata": {}},
    {"metadata": {"path": ""}}, {"metadata": {"path": None}},
    {"metadata": {"path": "invalid\ud800"}},
])
def test_line_references_require_a_successful_file_read_with_a_utf8_path(overrides):
    original = {**text_result("heading\nsecond"), **overrides}
    cleaned = sanitize_remote_reference(original)
    assert "source_lines" not in cleaned


@pytest.mark.parametrize(
    "identifier",
    [
        "89c9b8d3-9719-4638-9621-0f53a8d971ac",
        "123e4567-e89b-12d3-a456-426614174000",
        # Its digits "310605-6367-479" pass the Luhn check of a payment card.
        "1f310605-6367-479f-b211-818ea2db782e",
        "0b1d7f3c-2a4e-4c6f-9d8b-a1234567890b",
    ],
)
def test_uuids_are_not_mistaken_for_phone_numbers(identifier: str) -> None:
    text = f"Conversación {identifier} y teléfono 099 123 4567"

    redacted = redact_for_remote(text)

    assert identifier in redacted.text
    assert "[REDACTED_PHONE]" in redacted.text
    assert sanitize_remote_reference({"conversation_id": identifier}) == {
        "conversation_id": identifier
    }
