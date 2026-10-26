"""Conservative Telegram second boundary uses only our loopback bot."""

import pytest
from test_telegram import NOW, final_event, update
from test_telegram import bot as bot
from test_telegram import channel_factory as channel_factory


@pytest.mark.parametrize("fraction", [0, .5, .999999])
async def test_previous_and_equal_start_second_are_never_dispatched(
        channel_factory, bot, fraction):
    channel, journal, received = await channel_factory()
    channel.clock = lambda: NOW + fraction
    await bot.updates.put([
        update(1, date=NOW - 1, text="antiguo"),
        update(2, date=NOW, text="mismo segundo previo"),
        update(3, date=NOW + 1, text="nuevo segundo"),
    ])
    channel.start(expected_configuration_hash=journal.configuration_hash())
    final = await final_event(channel, journal, identity=3)
    assert final["status"] == "completed"
    assert [item.text for item in received] == ["nuevo segundo"]
    history = {row["update_id"]: row for row in journal.history()}
    for identity in (1, 2):
        assert history[identity]["error_code"] == "telegram_message_predates_start"
        assert history[identity]["status"] == "ignored"
    assert len(bot.sent()) == 1
