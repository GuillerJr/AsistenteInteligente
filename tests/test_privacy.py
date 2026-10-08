import json
from copy import deepcopy

import pytest

from aegis_core.privacy import redact_for_remote, sanitize_remote_reference


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
