import asyncio
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

from core.coworking import (
    COWORKING_TIMEZONE,
    check_coworking_notifications,
    get_coworking_notification_week,
    has_nearby_event,
)


def test_coworking_notification_slot_uses_configured_local_weekday_and_hour():
    """The weekly slot is due only after its configured local hour."""
    assert get_coworking_notification_week(
        datetime(2026, 10, 21, 10, 0, tzinfo=COWORKING_TIMEZONE),
        weekday=2,
        hour=10,
    ) == "2026-W43"
    assert get_coworking_notification_week(
        datetime(2026, 10, 21, 9, 59, tzinfo=COWORKING_TIMEZONE),
        weekday=2,
        hour=10,
    ) is None
    assert get_coworking_notification_week(
        datetime(2026, 10, 20, 10, 0, tzinfo=COWORKING_TIMEZONE),
        weekday=2,
        hour=10,
    ) is None


def test_coworking_quiet_window_detects_calendar_and_manual_events():
    """Nearby calendar or manual events suppress coworking invitations."""
    now = datetime(2026, 10, 21, 10, 0, tzinfo=COWORKING_TIMEZONE)
    calendar_events = [{"start": {"dateTime": "2026-10-21T18:00:00-06:00"}}]
    manual_promos = {
        "promo-1": {
            "status": "active",
            "event_start": "2026-10-23T10:00:00-06:00",
        }
    }

    assert has_nearby_event(now, calendar_events, {}, quiet_window_hours=24)
    assert not has_nearby_event(now, [], manual_promos, quiet_window_hours=24)


def test_coworking_delivery_respects_preferences_and_deduplicates(tmp_path):
    """Eligible subscribers receive one coworking invitation per week."""
    bot = SimpleNamespace(send_message=AsyncMock())
    context = SimpleNamespace(bot=bot, bot_data={"current_events": []})
    state = {
        "subscribers": {101, 202},
        "notification_preferences": {
            "101": {"coworking": True},
            "202": {"coworking": False},
        },
        "manual_promos": {},
        "coworking": {},
    }
    config = {
        "ENABLE_COWORKING_NOTIFICATIONS": True,
        "COWORKING_WEEKDAY": 2,
        "COWORKING_HOUR": 10,
        "COWORKING_QUIET_WINDOW_HOURS": 24,
        "COWORKING_STATE_FILE": str(tmp_path / "coworking_state.json"),
    }
    now = datetime(2026, 10, 21, 10, 0, tzinfo=COWORKING_TIMEZONE)

    asyncio.run(check_coworking_notifications(context, config, state, now=now))
    asyncio.run(check_coworking_notifications(context, config, state, now=now))

    bot.send_message.assert_awaited_once()
    assert bot.send_message.await_args.kwargs["chat_id"] == 101
    assert state["coworking"]["last_sent_week"] == "2026-W43"


def test_failed_coworking_delivery_is_retried(tmp_path):
    """A failed weekly delivery does not consume the weekly send slot."""
    bot = SimpleNamespace(
        send_message=AsyncMock(side_effect=[RuntimeError("network"), None])
    )
    context = SimpleNamespace(bot=bot, bot_data={"current_events": []})
    state = {
        "subscribers": {101},
        "notification_preferences": {"101": {"coworking": True}},
        "manual_promos": {},
        "coworking": {},
    }
    config = {
        "ENABLE_COWORKING_NOTIFICATIONS": True,
        "COWORKING_WEEKDAY": 2,
        "COWORKING_HOUR": 10,
        "COWORKING_QUIET_WINDOW_HOURS": 24,
        "COWORKING_STATE_FILE": str(tmp_path / "coworking_state.json"),
    }
    now = datetime(2026, 10, 21, 10, 0, tzinfo=COWORKING_TIMEZONE)

    asyncio.run(check_coworking_notifications(context, config, state, now=now))
    asyncio.run(check_coworking_notifications(context, config, state, now=now))

    assert bot.send_message.await_count == 2
    assert state["coworking"]["last_sent_week"] == "2026-W43"
