"""Actual own runtime/tool catalog; synthetic provider never supplies authority."""

import asyncio
import json

import pytest
from test_service import session, settled
from test_service_media import imported
from test_service_media import media_service as media_service
from test_service_telegram import approval

from jarvis_core.tools import ToolError


async def test_media_list_uses_active_session_without_an_approval_lease(media_service):
    service, client, peer, _root = media_service
    ref, other = await session(client), await session(client)
    row = await imported(client, ref)
    foreign = await imported(client, other, b"OTRO DOCUMENTO 6234")
    peer.plans["enumera"] = [("media_list", {})]
    run = await client.call("runs.start", {**ref, "message": "enumera"})
    result = await settled(client, ref, run["run_id"])
    action = result["action_receipts"][0]
    assert result["status"] == "completed" and action["succeeded"] and action["verified"]
    data = json.loads(action["output"])
    assert [x["attachment_id"] for x in data["attachments"]] == [row["attachment_id"]]
    assert foreign["attachment_id"] not in action["output"]
    assert not service.authority.list_pending()


@pytest.mark.parametrize("allow", [True, False])
async def test_pure_document_requires_exact_grant_and_accepts_frozen_arguments(
    media_service, allow
):
    service, client, peer, _root = media_service
    ref = await session(client)
    row = await imported(client, ref, b"TEXTO LOCAL 4826")
    args = {"attachment_id": row["attachment_id"], "sha256": row["sha256"]}
    peer.plans["extrae"] = [("media_inspect", args)]
    started = await client.call("runs.start", {**ref, "message": "extrae"})
    card = await approval(client, ref)
    assert card["tool_name"] == "media_inspect" and not peer.replies
    await client.call(
        "approvals.decide",
        {**ref, "approval_id": card["approval_id"], "digest": card["digest"], "allow": allow},
    )
    result = await settled(client, ref, started["run_id"])
    action = result["action_receipts"][0]
    assert action["succeeded"] is allow and action["verified"] is allow
    if allow:
        assert "TEXTO LOCAL 4826" in action["output"]
        assert row["sha256"] in action["output"] and "external" in action["output"]
    else:
        assert action["error"] == "approval_denied" and action["output"] is None
    assert not service.media_tools.frames and not service._media_native_payloads


async def test_direct_media_inspect_callback_has_no_authority(media_service):
    service, _client, _peer, _root = media_service
    with pytest.raises(ToolError, match="tool_authority_required"):
        await service.media_tools.execute(
            "media_inspect",
            {
                "attachment_id": "a" * 32,
                "sha256": "b" * 64,
            },
            asyncio.Event(),
        )
