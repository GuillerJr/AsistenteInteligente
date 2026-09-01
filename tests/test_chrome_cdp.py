from __future__ import annotations

from pathlib import Path

import pytest

from aegis_core.brain.router import analyze_browser_intent, decide_browser_target
from aegis_core.tools.chrome_cdp import (
    ChromeAutomationError,
    ChromeCDPController,
    LocalBrowserDiscovery,
)


def test_chrome_cdp_accepts_only_loopback_debugging_targets() -> None:
    targets = [
        {
            "type": "page",
            "webSocketDebuggerUrl": "ws://127.0.0.1:9222/devtools/page/abc",
        }
    ]

    assert ChromeCDPController._page_websocket_url(targets) == targets[0]["webSocketDebuggerUrl"]
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
async def test_chrome_cdp_fails_closed_when_debugging_is_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    controller = ChromeCDPController()
    calls: list[tuple[str, str]] = []

    async def fail_cdp(query: str) -> None:
        calls.append(("cdp", query))
        raise ChromeAutomationError("unreachable")

    monkeypatch.setattr(controller, "_play_youtube_cdp", fail_cdp)

    with pytest.raises(ChromeAutomationError, match="remote debugging enabled"):
        await controller.play_youtube("  Michael Jackson   Man in the Mirror ")

    assert calls == [("cdp", "Michael Jackson Man in the Mirror")]


def test_chrome_cdp_rejects_remote_or_malformed_configuration() -> None:
    with pytest.raises(ValueError):
        ChromeCDPController(endpoint="http://192.168.1.10:9222")
    with pytest.raises(ValueError):
        ChromeCDPController(endpoint="https://127.0.0.1:9222")


def test_generic_browser_intent_requires_choice_when_two_are_active() -> None:
    intent = analyze_browser_intent("Abre el navegador y busca Apple Silicon")
    decision = decide_browser_target(
        "Abre el navegador y busca Apple Silicon",
        installed_bundle_identifiers=("com.apple.Safari", "com.google.Chrome"),
        running_bundle_identifiers=("com.apple.Safari", "com.google.Chrome"),
    )

    assert intent.generic_target
    assert decision.requires_selection
    assert decision.selected_bundle_identifier is None


def test_browser_router_selects_the_only_running_browser() -> None:
    decision = decide_browser_target(
        "Abre el navegador y busca Apple Silicon",
        installed_bundle_identifiers=("com.apple.Safari", "com.google.Chrome"),
        running_bundle_identifiers=("com.google.Chrome",),
    )

    assert not decision.requires_selection
    assert decision.selected_bundle_identifier == "com.google.Chrome"


@pytest.mark.asyncio
async def test_browser_discovery_reads_only_whitelisted_application_plists(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import plistlib

    for application, bundle_identifier in (
        ("Chrome.app", "com.google.Chrome"),
        ("Safari.app", "com.apple.Safari"),
        ("Unknown.app", "example.untrusted.Browser"),
    ):
        contents = tmp_path / application / "Contents"
        contents.mkdir(parents=True)
        (contents / "Info.plist").write_bytes(
            plistlib.dumps({"CFBundleIdentifier": bundle_identifier})
        )
    discovery = LocalBrowserDiscovery(tmp_path)
    monkeypatch.setattr(
        discovery,
        "_running_bundle_identifiers",
        lambda installed: {"com.google.Chrome"} & installed,
    )

    browsers = await discovery.discover()

    assert [(item.name, item.running) for item in browsers] == [
        ("Chrome", True),
        ("Safari", False),
    ]


@pytest.mark.asyncio
async def test_browser_discovery_includes_system_applications(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import plistlib

    applications = tmp_path / "Applications"
    system_applications = tmp_path / "System/Applications"
    for root, application, bundle_identifier in (
        (applications, "Chrome.app", "com.google.Chrome"),
        (system_applications, "Safari.app", "com.apple.Safari"),
    ):
        contents = root / application / "Contents"
        contents.mkdir(parents=True)
        (contents / "Info.plist").write_bytes(
            plistlib.dumps({"CFBundleIdentifier": bundle_identifier})
        )
    discovery = LocalBrowserDiscovery(applications)
    discovery._application_directories = (applications, system_applications)
    monkeypatch.setattr(discovery, "_running_bundle_identifiers", lambda installed: set())

    browsers = await discovery.discover()

    assert [item.name for item in browsers] == ["Chrome", "Safari"]
