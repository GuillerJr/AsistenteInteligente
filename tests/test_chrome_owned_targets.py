from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

import aiohttp
import pytest

from aegis_core.tools.chrome_cdp import ChromeAutomationError, ChromeCDPController


class Socket:
    def __init__(self, owner, url):
        self.owner, self.url = owner, url
        self.pending = None

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        return False

    async def send_json(self, frame):
        self.owner.commands.append((self.url, frame["method"], frame["params"]))
        result = {}
        if frame["method"] == "Target.createBrowserContext":
            result = {"browserContextId": "isolated-context"}
        elif frame["method"] == "Target.createTarget":
            result = {"targetId": "owned-page"}
        self.pending = {"id": frame["id"], "result": result}

    async def receive(self):
        return SimpleNamespace(type=aiohttp.WSMsgType.TEXT, data=json.dumps(self.pending))


class Client:
    def __init__(self):
        self.commands = []
        self.connections = []

    def ws_connect(self, url, **kwargs):
        self.connections.append(url)
        return Socket(self, url)


def prepare(monkeypatch, *, missing_owned=False):
    controller = ChromeCDPController()
    client = Client()

    async def discovery(client, route):
        if route == "/json/version":
            return {
                "Browser": "Chrome/140.0.0",
                "Protocol-Version": "1.3",
                "webSocketDebuggerUrl": "ws://127.0.0.1:9222/devtools/browser/browser-id",
            }
        pages = [
            {
                "id": "personal-page",
                "type": "page",
                "webSocketDebuggerUrl": "ws://127.0.0.1:9222/devtools/page/personal-page",
            }
        ]
        if not missing_owned:
            pages.append(
                {
                    "id": "owned-page",
                    "type": "page",
                    "webSocketDebuggerUrl": "ws://127.0.0.1:9222/devtools/page/owned-page",
                }
            )
        return pages

    monkeypatch.setattr(controller, "_read_endpoint_json", discovery)
    return controller, client


@pytest.mark.asyncio
async def test_qualification_owns_isolated_page_even_with_personal_tabs(monkeypatch):
    controller, client = prepare(monkeypatch)
    async with controller._owned_page(client, isolated=True) as page:
        await page.session.command("Page.setDocumentContent", {"html": "fixture-only"})
    assert all("personal-page" not in url for url in client.connections)
    create = next(
        params for _, method, params in client.commands if method == "Target.createTarget"
    )
    assert create == {
        "url": "about:blank",
        "background": True,
        "browserContextId": "isolated-context",
    }
    mutation = next(
        url for url, method, _ in client.commands if method == "Page.setDocumentContent"
    )
    assert mutation.endswith("/owned-page")
    assert ("Target.closeTarget", {"targetId": "owned-page"}) in [
        (method, params) for _, method, params in client.commands
    ]
    assert client.commands[-1][1:] == (
        "Target.disposeBrowserContext",
        {"browserContextId": "isolated-context"},
    )


@pytest.mark.asyncio
async def test_page_failure_cleans_only_owned_target_and_context(monkeypatch):
    controller, client = prepare(monkeypatch, missing_owned=True)
    with pytest.raises(ChromeAutomationError, match="unavailable"):
        async with controller._owned_page(client, isolated=True):
            pytest.fail("Missing owned target must not select a personal tab")
    assert len(client.connections) == 1
    assert client.commands[-2][1:] == ("Target.closeTarget", {"targetId": "owned-page"})
    assert client.commands[-1][1] == "Target.disposeBrowserContext"


@pytest.mark.asyncio
async def test_media_retains_only_new_page_after_verified_playback(monkeypatch):
    controller, client = prepare(monkeypatch)
    async with controller._owned_page(client, isolated=False) as page:
        page.retain = True
    assert not any(
        method in {"Target.closeTarget", "Target.createBrowserContext"}
        for _, method, _ in client.commands
    )
    assert all("personal-page" not in url for url in client.connections)


@pytest.mark.asyncio
async def test_unverified_media_does_not_leave_a_new_tab(monkeypatch):
    controller, client = prepare(monkeypatch)
    with pytest.raises(RuntimeError):
        async with controller._owned_page(client, isolated=False):
            raise RuntimeError("autoplay denied")
    assert client.commands[-1][1:] == ("Target.closeTarget", {"targetId": "owned-page"})


@pytest.mark.asyncio
async def test_cancelled_target_creation_joins_ack_then_closes_exact_new_page(monkeypatch):
    controller, client = prepare(monkeypatch)
    issued, release = asyncio.Event(), asyncio.Event()
    receive = Socket.receive

    async def delayed_receive(socket):
        if socket.pending.get("result", {}).get("targetId"):
            issued.set()
            await release.wait()
        return await receive(socket)

    monkeypatch.setattr(Socket, "receive", delayed_receive)

    async def run():
        async with controller._owned_page(client, isolated=False):
            pytest.fail("Cancelled creation must not proceed to any page action")

    task = asyncio.create_task(run())
    await asyncio.wait_for(issued.wait(), 1)
    task.cancel()
    await asyncio.sleep(0)
    assert not task.done()
    release.set()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(task, 1)
    assert client.commands[-1][1:] == ("Target.closeTarget", {"targetId": "owned-page"})
    assert all("personal-page" not in url for url in client.connections)


@pytest.mark.parametrize(
    "field,value",
    [
        ("paused", True),
        ("paused", 0),
        ("ended", True),
        ("adPlaying", True),
        ("adPlaying", None),
        ("readyState", 1),
        ("readyState", True),
        ("currentTime", float("nan")),
        ("currentTime", 1.0),
        ("url", "https://www.youtube.com/watch?v=other"),
    ],
)
def test_click_or_stale_video_does_not_prove_playback(field, value):
    state = {
        "url": "https://www.youtube.com/watch?v=expected",
        "paused": False,
        "ended": False,
        "adPlaying": False,
        "readyState": 3,
        "currentTime": 2.0,
    }
    state[field] = value
    assert not ChromeCDPController._playback_verified(
        state, 1.0, "https://www.youtube.com/watch?v=expected"
    )


def test_playback_requires_observed_progress_and_exact_requested_video():
    state = {
        "url": "https://www.youtube.com/watch?v=expected",
        "paused": False,
        "ended": False,
        "adPlaying": False,
        "readyState": 3,
        "currentTime": 2.0,
    }
    assert not ChromeCDPController._playback_verified(state, None, state["url"])
    assert not ChromeCDPController._playback_verified(state, float("inf"), state["url"])
    assert ChromeCDPController._playback_verified(state, 1.0, state["url"])


def test_target_binding_rejects_duplicates_and_foreign_websocket():
    target = {
        "id": "owned",
        "type": "page",
        "webSocketDebuggerUrl": "ws://127.0.0.1:9222/devtools/page/owned",
    }
    with pytest.raises(ChromeAutomationError, match="ambiguous"):
        ChromeCDPController._page_websocket_url([target, target], target_id="owned")
    target["webSocketDebuggerUrl"] = "ws://127.0.0.1:9222/devtools/page/personal"
    with pytest.raises(ChromeAutomationError, match="invalid"):
        ChromeCDPController._page_websocket_url([target], target_id="owned")
