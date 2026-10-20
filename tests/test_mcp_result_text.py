from __future__ import annotations

from aegis_core.mcp.result_text import readable_mcp_result

GMAIL_TOOL = "mcp_google_workspace__search_gmail_messages"
SEARCH_TEXT = (
    "Found 2 messages matching 'in:inbox is:unread':\n\n📧 MESSAGES:\n"
    "  1. Message ID: 1a155b62189229fa\n"
    "     Subject: Podrías conocer a una persona de Ejemplo\n"
    '     From: "Ana Pérez en LinkedIn" <messages-noreply@linkedin.com>\n'
    "     Date: Mon, 19 Oct 2026 19:49:21 +0000 (UTC)\n"
    "     Web Link: https://mail.google.com/mail/u/0/#all/1a155b62189229fa\n"
    "     Thread ID: 1a155b62189229fa\n\n"
    "  2. Message ID: 1a1556091806a490\n"
    "     Subject: Oferta en remoto\n"
    "     From: jobs-noreply@linkedin.com\n"
    "     Date: Mon, 19 Oct 2026 12:03:12 -0600\n"
    "     Web Link: https://mail.google.com/mail/u/0/#all/1a1556091806a490\n\n"
    "💡 USAGE:\n  • Pass the Message IDs as a list to get_gmail_messages_content_batch()\n"
    "📄 PAGINATION: To get the next page, call search_gmail_messages again with "
    "page_token='abc'\n"
)


def mcp_payload(text: str, **extra) -> dict:
    return {"content": [{"type": "text", "text": text}], "isError": False, **extra}


def test_gmail_search_becomes_a_numbered_list_without_ids_or_model_guidance() -> None:
    rendered = readable_mcp_result(
        mcp_payload(SEARCH_TEXT), server_label="google-workspace", tool_name=GMAIL_TOOL,
    )
    lines = rendered.splitlines()
    assert lines[0] == "Encontré 2 correos:"
    assert lines[2] == "1. Podrías conocer a una persona de Ejemplo"
    assert lines[3].startswith("   Ana Pérez en LinkedIn · 19 oct, ")
    assert lines[4] == "2. Oferta en remoto"
    assert lines[5].startswith("   jobs-noreply@linkedin.com · 19 oct, ")
    assert "Hay más correos" in rendered
    for noise in ("Message ID", "https://", "USAGE", "get_gmail", '{"content"', "\\n"):
        assert noise not in rendered


def test_empty_gmail_search_says_so() -> None:
    rendered = readable_mcp_result(
        mcp_payload("No messages found for query: 'x'"),
        server_label="google-workspace", tool_name=GMAIL_TOOL,
    )
    assert rendered == "No encontré correos que coincidan con la búsqueda."


def test_other_tools_show_their_text_without_json_or_guidance() -> None:
    rendered = readable_mcp_result(
        mcp_payload("Calendar: Personal\n\n💡 USAGE: call get_events next"),
        server_label="google-workspace", tool_name="mcp_google_workspace__list_calendars",
    )
    assert rendered == "Resultado de google-workspace:\nCalendar: Personal"


def test_structured_only_results_still_render() -> None:
    rendered = readable_mcp_result(
        {"content": [], "structuredContent": {"result": "listo"}},
        server_label="github", tool_name="mcp_github__issue_read",
    )
    assert rendered == "Resultado de github:\nlisto"


def test_unrecognised_payload_falls_back_to_compact_json() -> None:
    rendered = readable_mcp_result(
        {"content": [{"type": "image", "data": "AA==", "mimeType": "image/png"}]},
        server_label="github", tool_name="mcp_github__issue_read",
    )
    assert rendered.startswith('Resultado de github: {"content":')


def test_model_guidance_is_removed_before_data_reaches_a_model() -> None:
    import json

    from aegis_core.mcp.result_text import strip_model_guidance

    cleaned = json.loads(strip_model_guidance(json.dumps(mcp_payload(SEARCH_TEXT))))
    text = cleaned["content"][0]["text"]
    assert "Message ID: 1a155b62189229fa" in text and "Oferta en remoto" in text
    assert "USAGE" not in text and "get_gmail_messages_content_batch" not in text
    assert cleaned["isError"] is False
    assert strip_model_guidance("plain\n💡 USAGE: call x") == "plain"
