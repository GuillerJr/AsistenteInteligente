"""Real synthetic polling/MCP peers prove no implicit stop or quiescence gap."""

import pytest
from test_service_mcp import spec
from test_service_telegram import TOKEN, ready

from jarvis_core.host_profile import HostProfileError

pytest_plugins = ("test_service_mcp", "test_service_telegram")


async def test_polling_telegram_blocks_cut_until_owner_stops(telegram_service):
    service, client, _peer, _bot, _root = telegram_service
    await ready(client)
    channel = service._telegram_channel
    original = channel._lease
    with pytest.raises(HostProfileError, match=r"^profile_not_quiescent$") as raised:
        with service.profile_gate.exclusive():
            pytest.fail("polling Telegram passed quiescence")
    assert "telegram_active_or_cleanup" in raised.value.blockers
    assert channel.running and channel._lease == original
    await client.call("telegram.stop")
    with service.profile_gate.exclusive():
        pass


async def test_pairing_reader_blocks_cut_until_owner_cancels(telegram_service):
    service, client, _peer, _bot, _root = telegram_service
    await client.call("telegram.connect", {"token": TOKEN})
    review = await client.call("telegram.pairing.review", {"agent_id": "jarvis"})
    await client.call("telegram.pairing.start", {
        "agent_id": "jarvis", "expected_profile_revision": review["agent_revision"],
        "review_hash": review["review_hash"], "acknowledge_update_consumption": True,
        "confirm_no_other_consumer": True,
    })
    pairing = service._telegram_pairing
    original = pairing._lease
    with pytest.raises(HostProfileError, match=r"^profile_not_quiescent$") as raised:
        with service.profile_gate.exclusive():
            pytest.fail("pairing journal writer passed quiescence")
    assert "pairing_active_or_cleanup" in raised.value.blockers
    assert pairing._lease == original
    await client.call("telegram.pairing.cancel")
    with service.profile_gate.exclusive():
        pass


async def test_connected_mcp_blocks_cut_until_explicit_disconnect(mcp_service):
    service, client, _peer, root, script = mcp_service
    request = spec(root, script)
    await client.call("mcp.add", request)
    await client.call("mcp.connect", request)
    client_process = service.mcp._entries[request["server_id"]].client._process
    with pytest.raises(HostProfileError, match=r"^profile_not_quiescent$") as raised:
        with service.profile_gate.exclusive():
            pytest.fail("external MCP process passed quiescence")
    assert "mcp_active_or_cleanup" in raised.value.blockers
    assert client_process.returncode is None
    await client.call("mcp.disconnect", {"server_id": request["server_id"]})
    with service.profile_gate.exclusive():
        pass
