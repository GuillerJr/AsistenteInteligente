from __future__ import annotations

from pathlib import Path

import pytest

from aegis_core.tools.chrome_cdp import ChromeAutomationError, ChromeCDPController


def test_chrome_cdp_accepts_only_loopback_debugging_targets() -> None:
    targets = [
        {
            "type": "page",
            "webSocketDebuggerUrl": "ws://127.0.0.1:9222/devtools/page/abc",
        }
    ]

    assert ChromeCDPController._page_websocket_url(targets) == targets[0][
        "webSocketDebuggerUrl"
    ]
    with pytest.raises(ChromeAutomationError):
        ChromeCDPController._page_websocket_url(
            [
                {
                    "type": "page",
                    "webSocketDebuggerUrl": "ws://192.168.1.20:9222/devtools/page/abc",
                }
            ]
        )


@pytest.mark.asyncio
async def test_chrome_cdp_falls_back_to_jxa_without_leaking_query(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    controller = ChromeCDPController(osascript_path=Path("/usr/bin/osascript"))
    calls: list[tuple[str, str]] = []

    async def fail_cdp(query: str) -> None:
        calls.append(("cdp", query))
        raise ChromeAutomationError("unreachable")

    async def succeed_jxa(query: str) -> None:
        calls.append(("jxa", query))

    monkeypatch.setattr(controller, "_play_youtube_cdp", fail_cdp)
    monkeypatch.setattr(controller, "_play_youtube_jxa", succeed_jxa)

    channel = await controller.play_youtube("  Michael Jackson   Man in the Mirror ")

    assert channel == "jxa"
    assert calls == [
        ("cdp", "Michael Jackson Man in the Mirror"),
        ("jxa", "Michael Jackson Man in the Mirror"),
    ]


def test_chrome_cdp_rejects_remote_or_malformed_configuration() -> None:
    with pytest.raises(ValueError):
        ChromeCDPController(endpoint="http://192.168.1.10:9222")
    with pytest.raises(ValueError):
        ChromeCDPController(endpoint="https://127.0.0.1:9222")
