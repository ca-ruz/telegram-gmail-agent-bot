import asyncio
from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from bot.handlers.admin import (
    handle_manual_promo_confirm,
    handle_manual_promo_photo,
    manual_promo,
)
from core import manual_promos
from tools.local.data_manager import load_json


class FrozenDateTime(datetime):
    """Datetime class with a controllable current time for scheduler tests."""

    current = datetime(2026, 10, 15, 11, 0, tzinfo=manual_promos.MANUAL_PROMO_TIMEZONE)

    @classmethod
    def now(cls, tz=None):
        """Return the configured current time with the requested timezone."""
        return cls.current.astimezone(tz) if tz else cls.current.replace(tzinfo=None)


class FakeBot:
    """Minimal Telegram bot double for manual promo delivery tests."""

    def __init__(self, failures=None):
        self.failures = set(failures or [])
        self.sent = []

    async def send_photo(self, chat_id, photo, caption, parse_mode):
        """Record a delivery or simulate a subscriber failure."""
        if chat_id in self.failures:
            raise RuntimeError("simulated delivery failure")
        self.sent.append({
            "chat_id": chat_id,
            "photo": photo,
            "caption": caption,
            "parse_mode": parse_mode,
        })


def build_active_promo():
    """Build an active promo that is inside the 24-hour reminder window."""
    promo = manual_promos.build_manual_promo(
        "manual-001",
        "Bitcoin Meetup",
        "2026-10-16T11:00:00-06:00",
        "Meet us at Coffee & Co.",
        "telegram-photo-id",
    )
    promo["status"] = "active"
    return promo


def test_manual_promo_persistence_round_trip(tmp_path):
    """Manual promo records can be saved and loaded from JSON storage."""
    file_path = tmp_path / "manual_promos.json"
    promos = {"manual-001": build_active_promo()}

    manual_promos.save_manual_promos(str(file_path), promos)

    loaded = manual_promos.load_manual_promos(str(file_path))
    assert loaded["manual-001"]["image_file_id"] == "telegram-photo-id"
    assert loaded["manual-001"]["status"] == "active"
    assert loaded["manual-001"]["reminders_sent"] == {}


def test_manual_promo_staging_and_confirmation(tmp_path):
    """An admin can stage a flyer and activate it through the confirmation callback."""
    config = {"MANUAL_PROMOS_FILE": str(tmp_path / "manual_promos.json")}
    state = {"manual_promos": {}}
    context = SimpleNamespace(user_data={})
    command_message = SimpleNamespace(
        text="/manualpromo 2026-12-01 11:00 | Bitcoin Meetup | Join us & learn.",
        reply_text=AsyncMock(),
    )
    command_update = SimpleNamespace(
        effective_user=SimpleNamespace(id=42),
        message=command_message,
    )

    asyncio.run(manual_promo(command_update, context, admin_id=42))

    photo_message = SimpleNamespace(
        photo=[SimpleNamespace(file_id="photo-id")],
        reply_photo=AsyncMock(),
    )
    photo_update = SimpleNamespace(
        effective_user=SimpleNamespace(id=42),
        message=photo_message,
    )
    asyncio.run(
        handle_manual_promo_photo(
            photo_update,
            context,
            admin_id=42,
            state=state,
            config=config,
        )
    )

    promo_id = next(iter(state["manual_promos"]))
    assert state["manual_promos"][promo_id]["status"] == "pending"
    assert state["manual_promos"][promo_id]["image_file_id"] == "photo-id"
    assert load_json(config["MANUAL_PROMOS_FILE"], {})[promo_id]["status"] == "pending"

    query = SimpleNamespace(
        from_user=SimpleNamespace(id=42),
        data=f"manual_confirm_{promo_id}",
        answer=AsyncMock(),
        edit_message_caption=AsyncMock(),
    )
    callback_update = SimpleNamespace(callback_query=query)
    asyncio.run(
        handle_manual_promo_confirm(
            callback_update,
            SimpleNamespace(),
            admin_id=42,
            state=state,
            config=config,
        )
    )

    assert state["manual_promos"][promo_id]["status"] == "active"


def test_manual_promo_reminders_retry_only_failed_subscribers(tmp_path):
    """A failed delivery is retried without duplicating successful deliveries."""
    state = {
        "subscribers": {101, 202},
        "manual_promos": {"manual-001": build_active_promo()},
    }
    config = {
        "MANUAL_PROMOS_FILE": str(tmp_path / "manual_promos.json"),
        "REMINDER_RULES": {
            "24h": timedelta(days=1),
            "1h": timedelta(hours=1),
        },
    }
    bot = FakeBot(failures={202})
    context = SimpleNamespace(bot=bot)

    with patch.object(manual_promos, "datetime", FrozenDateTime):
        asyncio.run(manual_promos.check_manual_promo_reminders(context, config, state))

    assert [message["chat_id"] for message in bot.sent] == [101]
    assert state["manual_promos"]["manual-001"]["reminders_sent"] == {"24h": [101]}

    bot.failures.clear()
    with patch.object(manual_promos, "datetime", FrozenDateTime):
        asyncio.run(manual_promos.check_manual_promo_reminders(context, config, state))
        asyncio.run(manual_promos.check_manual_promo_reminders(context, config, state))

    assert [message["chat_id"] for message in bot.sent] == [101, 202]
    assert state["manual_promos"]["manual-001"]["reminders_sent"] == {
        "24h": [101, 202]
    }


def test_manual_promo_reminders_ignore_pending_promos(tmp_path):
    """Promos awaiting admin confirmation are never delivered."""
    promo = build_active_promo()
    promo["status"] = "pending"
    state = {"subscribers": {101}, "manual_promos": {"manual-001": promo}}
    config = {
        "MANUAL_PROMOS_FILE": str(tmp_path / "manual_promos.json"),
        "REMINDER_RULES": {"24h": timedelta(days=1)},
    }
    bot = FakeBot()

    with patch.object(manual_promos, "datetime", FrozenDateTime):
        asyncio.run(
            manual_promos.check_manual_promo_reminders(
                SimpleNamespace(bot=bot), config, state
            )
        )

    assert bot.sent == []
