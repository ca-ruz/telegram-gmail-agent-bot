import asyncio
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import patch

from core import event_registrations, manual_promos


class FrozenDateTime(datetime):
    """Datetime class with a controllable current time for scheduler tests."""

    current = datetime(
        2026,
        10,
        15,
        11,
        0,
        tzinfo=manual_promos.MANUAL_PROMO_TIMEZONE,
    )

    @classmethod
    def now(cls, tz=None):
        """Return the configured current time with the requested timezone."""
        return cls.current.astimezone(tz) if tz else cls.current.replace(tzinfo=None)


class FakeBot:
    """Minimal Telegram bot double for registered reminder tests."""

    def __init__(self):
        self.sent = []

    async def send_photo(self, chat_id, photo, caption, parse_mode):
        """Record a photo delivery."""
        self.sent.append({
            "chat_id": chat_id,
            "photo": photo,
            "caption": caption,
            "parse_mode": parse_mode,
        })


def test_event_registration_persistence_and_toggle(tmp_path):
    """Registrations persist, deduplicate, and can be removed."""
    file_path = tmp_path / "event_registrations.json"
    registrations = {}

    assert event_registrations.register_user(registrations, "event-1", 42)
    assert not event_registrations.register_user(registrations, "event-1", 42)
    event_registrations.save_event_registrations(str(file_path), registrations)

    loaded = event_registrations.load_event_registrations(str(file_path))
    assert event_registrations.get_registered_users(loaded, "event-1") == {"42"}
    assert event_registrations.unregister_user(loaded, "event-1", 42)
    assert event_registrations.get_registered_users(loaded, "event-1") == set()


def test_cleanup_expired_event_registrations_only_removes_started_events():
    """Cleanup preserves future registrations and removes started events."""
    now = datetime(2026, 10, 15, 12, 0, tzinfo=timezone.utc)
    registrations = {"past": ["1"], "future": ["2"], "unknown": ["3"]}
    promos = {
        "past": {"event_start": "2026-10-15T11:59:00+00:00"},
        "future": {"event_start": "2026-10-15T13:00:00+00:00"},
    }

    removed = event_registrations.cleanup_expired_event_registrations(
        registrations,
        promos,
        now,
    )

    assert removed == 1
    assert registrations == {"future": ["2"], "unknown": ["3"]}


def test_registered_reminder_is_private_and_deduplicated(tmp_path):
    """The extra one-hour reminder reaches registrants once and skips groups."""
    promo = manual_promos.build_manual_promo(
        "manual-1",
        "Bitcoin Meetup",
        "2026-10-15T11:30:00-06:00",
        "Meet us at the community space.",
        "photo-id",
    )
    promo["status"] = "active"
    state = {
        "subscribers": set(),
        "groups": {-100},
        "manual_promos": {"manual-1": promo},
        "event_registrations": {"manual-1": ["42"]},
    }
    config = {
        "MANUAL_PROMOS_FILE": str(tmp_path / "manual_promos.json"),
        "EVENT_REGISTRATIONS_FILE": str(tmp_path / "event_registrations.json"),
        "REMINDER_RULES": {"24h": timedelta(days=1)},
    }
    bot = FakeBot()
    context = SimpleNamespace(bot=bot)

    with patch.object(manual_promos, "datetime", FrozenDateTime):
        asyncio.run(manual_promos.check_manual_promo_reminders(context, config, state))
        asyncio.run(manual_promos.check_manual_promo_reminders(context, config, state))

    assert [message["chat_id"] for message in bot.sent] == [-100, "42"]
    assert promo["registered_reminders_sent"] == {"registered_1h": ["42"]}
