import asyncio
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from bot.handlers.admin import parse_manual_reminder_request, remind
from core.promoter import send_manual_event_reminder, send_manual_event_reminder_test


class FakeBot:
    """Minimal bot double for manual event reminder tests."""

    def __init__(self, failures=None):
        self.failures = set(failures or [])
        self.messages = []

    async def send_message(self, chat_id, message, **kwargs):
        """Record a message or simulate a failed delivery."""
        if chat_id in self.failures:
            raise RuntimeError("simulated delivery failure")
        self.messages.append({"chat_id": chat_id, "message": message, "kwargs": kwargs})


def make_config(tmp_path):
    """Build configuration for manual reminder tests."""
    return {
        "SCOPES": [],
        "SERVICE_ACCOUNT_FILE": "unused.json",
        "CALENDAR_ID": "calendar-id",
        "REMINDERS_FILE": str(tmp_path / "sent_reminders.json"),
    }


def make_event():
    """Build an upcoming event for manual reminder tests."""
    return {
        "id": "event-001",
        "summary": "Bitcoin Meetup",
        "description": "Register at https://example.com/meetup",
        "htmlLink": "https://calendar.google.com/event",
        "start": {"dateTime": "2026-10-15T12:00:00+00:00"},
    }


def test_parse_manual_reminder_request_validates_supported_targets():
    """Manual reminder syntax normalizes coffee dates and event IDs."""
    assert parse_manual_reminder_request("coffee | 2026-10-17") == {
        "type": "coffee",
        "date": "2026-10-17",
    }
    assert parse_manual_reminder_request("event | event-001") == {
        "type": "event",
        "event_id": "event-001",
    }
    assert parse_manual_reminder_request("test | event | event-001") == {
        "type": "event_test",
        "event_id": "event-001",
    }
    assert parse_manual_reminder_request("coffee | 2026-10-16") is None
    assert parse_manual_reminder_request("unknown | target") is None


def test_manual_event_reminder_delivers_to_enabled_dms_and_groups(tmp_path):
    """Manual event reminders reach eligible DMs and configured groups once."""
    now = datetime(2026, 10, 15, 11, 0, tzinfo=timezone.utc)
    state = {
        "subscribers": {101, 202},
        "groups": {-100, -200},
        "group_notification_categories": {
            "events": [-100],
            "payments": [-200],
            "coworking": [-100, -200],
        },
        "sent_reminders": {},
        "notification_preferences": {
            "101": {"events": True, "coffee": False, "payments": False},
            "202": {"events": False, "coffee": False, "payments": False},
        },
    }
    config = make_config(tmp_path)
    bot = FakeBot()
    context = SimpleNamespace(bot=bot)

    with patch("core.promoter.get_calendar_service_with_creds", return_value=object()), patch(
        "core.promoter.fetch_upcoming_events", return_value=([make_event()], now)
    ):
        first = asyncio.run(
            send_manual_event_reminder(context, config, state, "event-001")
        )
        second = asyncio.run(
            send_manual_event_reminder(context, config, state, "event-001")
        )

    assert first == {
        "found": True,
        "subscribers_sent": 1,
        "groups_sent": 1,
        "failures": 0,
    }
    assert second == {
        "found": True,
        "subscribers_sent": 0,
        "groups_sent": 0,
        "failures": 0,
    }
    assert [message["chat_id"] for message in bot.messages] == [101, -100]


def test_manual_event_reminder_test_only_reaches_administrator(tmp_path):
    """Event reminder tests send only to the administrator without saving state."""
    now = datetime(2026, 10, 15, 11, 0, tzinfo=timezone.utc)
    config = make_config(tmp_path)
    bot = FakeBot()
    context = SimpleNamespace(bot=bot)

    with patch("core.promoter.get_calendar_service_with_creds", return_value=object()), patch(
        "core.promoter.fetch_upcoming_events", return_value=([make_event()], now)
    ):
        result = asyncio.run(
            send_manual_event_reminder_test(context, config, 42, "event-001")
        )

    assert result == {"found": True, "failures": 0}
    assert [message["chat_id"] for message in bot.messages] == [42]
    assert "MODO PRUEBA" in bot.messages[0]["message"]
    assert not (tmp_path / "sent_reminders.json").exists()


def test_remind_command_executes_event_test_without_real_delivery():
    """The test command uses isolated event delivery and reports its result."""
    update = SimpleNamespace(
        effective_user=SimpleNamespace(id=42),
        message=SimpleNamespace(
            text="/remind test | event | event-001",
            reply_text=AsyncMock(),
        ),
    )
    context = SimpleNamespace(bot=SimpleNamespace(), user_data={})
    state = {}
    config = {}
    result = {"found": True, "failures": 0}

    with patch(
        "bot.handlers.admin.send_manual_event_reminder_test",
        new=AsyncMock(return_value=result),
    ) as sender:
        asyncio.run(remind(update, context, 42, state, config))

    sender.assert_awaited_once_with(context, config, 42, "event-001")
    assert "MODO PRUEBA" in update.message.reply_text.await_args.args[0]


def test_remind_command_sends_manual_coffee_request():
    """The admin command executes a validated coffee reminder request."""
    update = SimpleNamespace(
        effective_user=SimpleNamespace(id=42),
        message=SimpleNamespace(
            text="/remind coffee | 2026-10-17",
            reply_text=AsyncMock(),
        ),
    )
    context = SimpleNamespace(bot=SimpleNamespace(), user_data={})
    state = {"coffee_host_schedule": {}}
    config = {}
    result = {"found": True, "sent": 1, "failed": 0, "skipped": 0}

    with patch(
        "bot.handlers.admin.send_manual_coffee_host_reminder",
        new=AsyncMock(return_value=result),
    ) as sender:
        asyncio.run(remind(update, context, 42, state, config))

    sender.assert_awaited_once_with(context, config, state, "2026-10-17")
    assert "1 enviados" in update.message.reply_text.await_args.args[0]

