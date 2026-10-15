import asyncio
from datetime import date, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import patch

from core import coffee_hosts


def test_coffee_reminder_stage_chooses_the_most_urgent_rule():
    """A reminder within one day uses the one-day stage instead of seven days."""
    now = datetime(2026, 10, 14, 11, 0, tzinfo=coffee_hosts.COFFEE_TIMEZONE)
    event = now + timedelta(hours=20)

    assert coffee_hosts._coffee_reminder_stage(event, now, ("7d", "1d")) == "1d"


def test_coffee_reminder_stage_skips_events_outside_the_window():
    """Events more than seven days away do not trigger a reminder."""
    now = datetime(2026, 10, 14, 11, 0, tzinfo=coffee_hosts.COFFEE_TIMEZONE)
    event = now + timedelta(days=8)

    assert coffee_hosts._coffee_reminder_stage(event, now, ("7d", "1d")) is None


def test_coffee_reminder_message_escapes_user_content():
    """Venue and notes are safe when sent with Telegram HTML parsing enabled."""
    assignment = {
        "start_time": "11:00",
        "venue": "Coffee & Co <Centro>",
        "notes": "Bring <name> & confirm.",
    }

    message = coffee_hosts._coffee_reminder_message(assignment, "2026-10-17", "7d")

    assert "Coffee &amp; Co &lt;Centro&gt;" in message
    assert "Bring &lt;name&gt; &amp; confirm." in message


class FakeBot:
    """Minimal Telegram bot double for reminder delivery tests."""

    def __init__(self, failures=None):
        self.failures = set(failures or [])
        self.sent = []

    async def send_message(self, chat_id, text, parse_mode):
        """Record a message or simulate a Telegram delivery failure."""
        if chat_id in self.failures:
            raise RuntimeError("simulated delivery failure")
        self.sent.append({"chat_id": chat_id, "text": text, "parse_mode": parse_mode})


class FrozenDateTime(datetime):
    """Datetime class with a controllable current time for scheduler tests."""

    current = datetime(2026, 10, 14, 11, 0, tzinfo=coffee_hosts.COFFEE_TIMEZONE)

    @classmethod
    def now(cls, tz=None):
        """Return the configured current time with the requested timezone."""
        return cls.current.astimezone(tz) if tz else cls.current.replace(tzinfo=None)


def test_check_coffee_host_reminders_sends_and_deduplicates(tmp_path):
    """Successful reminders are sent privately once and persisted by stage."""
    saturday = date(2026, 10, 17)
    schedule = {
        saturday.isoformat(): {
            "hosts": ["host-001"],
            "venue": "Café Central",
            "start_time": "11:00",
            "notes": "",
            "reminders_sent": {},
        }
    }
    state = {
        "coffee_hosts": {"host-001": {"telegram_id": 123, "name": "Host"}},
        "coffee_host_schedule": schedule,
    }
    bot = FakeBot()
    context = SimpleNamespace(bot=bot)
    config = {
        "COFFEE_HOST_REMINDER_RULES": ("7d", "1d"),
        "COFFEE_HOST_SCHEDULE_FILE": str(tmp_path / "schedule.json"),
    }

    with patch.object(coffee_hosts, "datetime", FrozenDateTime):
        asyncio.run(coffee_hosts.check_coffee_host_reminders(context, config, state))
        asyncio.run(coffee_hosts.check_coffee_host_reminders(context, config, state))

    assert len(bot.sent) == 1
    assert bot.sent[0]["chat_id"] == 123
    assert bot.sent[0]["parse_mode"] == "HTML"
    assert schedule[saturday.isoformat()]["reminders_sent"] == {"7d": ["host-001"]}


def test_check_coffee_host_reminders_retries_failed_delivery(tmp_path):
    """Failed deliveries remain unmarked so a later scheduler run can retry them."""
    saturday = date(2026, 10, 17)
    schedule = {
        saturday.isoformat(): {
            "hosts": ["host-001"],
            "venue": "Café Central",
            "start_time": "11:00",
            "notes": "",
            "reminders_sent": {},
        }
    }
    state = {
        "coffee_hosts": {"host-001": {"telegram_id": 123}},
        "coffee_host_schedule": schedule,
    }
    context = SimpleNamespace(bot=FakeBot(failures={123}))
    config = {
        "COFFEE_HOST_REMINDER_RULES": ("7d", "1d"),
        "COFFEE_HOST_SCHEDULE_FILE": str(tmp_path / "schedule.json"),
    }

    with patch.object(coffee_hosts, "datetime", FrozenDateTime):
        asyncio.run(coffee_hosts.check_coffee_host_reminders(context, config, state))

    assert schedule[saturday.isoformat()]["reminders_sent"] == {}
    assert context.bot.sent == []
