import asyncio
from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from bot.handlers import admin as admin_handlers
from bot.handlers.admin import (
    handle_manual_promo_cancel,
    handle_manual_promo_confirm,
    handle_manual_promo_photo,
    handle_manual_promo_schedule,
    handle_manual_promo_schedule_input,
    handle_manual_promo_send_now,
    handle_manual_promo_test,
    format_manual_promo_preview,
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
        location="Café Adentro",
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
    assert loaded["manual-001"]["location"] == "Café Adentro"
    assert loaded["manual-001"]["status"] == "active"
    assert loaded["manual-001"]["reminders_sent"] == {}


def test_manual_promo_staging_and_confirmation(tmp_path):
    """An admin can stage a flyer and activate it through the confirmation callback."""
    config = {"MANUAL_PROMOS_FILE": str(tmp_path / "manual_promos.json")}
    state = {"manual_promos": {}}
    context = SimpleNamespace(user_data={})
    command_message = SimpleNamespace(
        text="/manualpromo 2026-12-01 11:00 | Bitcoin Meetup | LSS | Join us & learn.",
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
    assert state["manual_promos"][promo_id]["location"] == "LSS"
    assert state["manual_promos"][promo_id]["text"] == "Join us & learn."
    preview_caption = photo_message.reply_photo.await_args.kwargs["caption"]
    assert "📅 Martes 1 de diciembre" in preview_caption
    assert "🕕 11 AM" in preview_caption
    assert "📍 LSS" in preview_caption
    assert "Join us &amp; learn." in preview_caption
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


def test_manual_promo_preview_uses_spanish_date_and_time():
    """Manual promo previews use Spanish metadata and preserve the location."""
    preview = format_manual_promo_preview({
        "summary": "Forks Meetup",
        "event_start": "2026-10-20T18:00:00-06:00",
        "location": "LSS",
        "text": "Publication text",
    })

    assert "📅 Martes 20 de octubre" in preview
    assert "🕕 6 PM" in preview
    assert "📍 LSS" in preview
    assert "Publication text" in preview


def test_manual_promo_caption_appends_missing_event_details():
    """Final captions append localized date, time, and location details."""
    caption = manual_promos.format_manual_promo_caption({
        "event_start": "2026-10-20T18:00:00-06:00",
        "location": "LSS",
        "text": "¿Qué pasa cuando Bitcoin toma caminos diferentes?⚡",
    })

    assert "¿Qué pasa cuando Bitcoin toma caminos diferentes?⚡" in caption
    assert "📅 Martes 20 de octubre" in caption
    assert "🕕 6 PM" in caption
    assert "📍 LSS" in caption


def test_manual_promo_caption_does_not_duplicate_existing_details():
    """Existing matching metadata is not appended a second time."""
    caption = manual_promos.format_manual_promo_caption({
        "event_start": "2026-10-20T18:00:00-06:00",
        "location": "LSS",
        "text": (
            "Publication text\n\n"
            "📅 Martes 20 de octubre\n"
            "🕕 6 PM\n"
            "📍 LSS"
        ),
    })

    assert caption.count("📅 Martes 20 de octubre") == 1
    assert caption.count("🕕 6 PM") == 1
    assert caption.count("📍 LSS") == 1


def test_manual_promo_caption_places_links_after_event_details():
    """A publication link appears after the formatted event details."""
    caption = manual_promos.format_manual_promo_caption({
        "event_start": "2026-10-20T18:00:00-06:00",
        "location": "LSS",
        "text": "Join us!\nhttps://example.com/event",
    })

    assert caption.index("📅 Martes 20 de octubre") < caption.index(
        "https://example.com/event"
    )
    assert caption.index("🕕 6 PM") < caption.index("https://example.com/event")
    assert caption.index("📍 LSS") < caption.index("https://example.com/event")


def test_manual_promo_caption_preserves_multiple_links_at_the_end():
    """Multiple unique publication links remain at the bottom in order."""
    caption = manual_promos.format_manual_promo_caption({
        "event_start": "2026-10-20T18:00:00-06:00",
        "location": "LSS",
        "text": (
            "Join us! https://example.com/event "
            "More information: https://example.com/info "
            "https://example.com/event"
        ),
    })

    assert caption.count("https://example.com/event") == 1
    assert caption.index("https://example.com/event") < caption.index(
        "https://example.com/info"
    )
    assert caption.index("📍 LSS") < caption.index("https://example.com/event")


def test_manual_promo_caption_without_location_or_links_remains_clean():
    """Captions without optional location or links omit those sections."""
    caption = manual_promos.format_manual_promo_caption({
        "event_start": "2026-10-20T18:00:00-06:00",
        "text": "Join us!",
    })

    assert "Join us!" in caption
    assert "📅 Martes 20 de octubre" in caption
    assert "🕕 6 PM" in caption
    assert "📍" not in caption
    assert "http" not in caption


def test_legacy_manual_promo_preview_omits_missing_location():
    """Legacy promos without a location remain readable."""
    preview = format_manual_promo_preview({
        "summary": "Legacy event",
        "event_start": "2026-10-20T18:00:00-06:00",
        "text": "Legacy publication text",
    })

    assert "Legacy event" in preview
    assert "📅 Martes 20 de octubre" in preview
    assert "📍" not in preview
    assert "Legacy publication text" in preview


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
    assert "Coffee &amp; Co." in bot.sent[0]["caption"]
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


def test_manual_promo_immediate_delivery_reaches_subscribers_and_groups(tmp_path):
    """Immediate delivery reaches both DMs and configured broadcast groups."""
    state = {
        "subscribers": {101},
        "groups": {-100},
        "manual_promos": {"manual-001": build_active_promo()},
    }
    state["manual_promos"]["manual-001"]["status"] = "pending"
    config = {
        "MANUAL_PROMOS_FILE": str(tmp_path / "manual_promos.json"),
        "REMINDER_RULES": {"24h": timedelta(days=1), "1h": timedelta(hours=1)},
    }
    query = SimpleNamespace(
        from_user=SimpleNamespace(id=42),
        data="manual_send_now_manual-001",
        answer=AsyncMock(),
        edit_message_caption=AsyncMock(),
    )
    bot = FakeBot()
    context = SimpleNamespace(bot=bot)

    with patch.object(admin_handlers, "datetime", FrozenDateTime):
        asyncio.run(
            handle_manual_promo_send_now(
                SimpleNamespace(callback_query=query),
                context,
                admin_id=42,
                state=state,
                config=config,
            )
        )

    assert {message["chat_id"] for message in bot.sent} == {101, -100}
    expected_caption = manual_promos.format_manual_promo_caption(
        state["manual_promos"]["manual-001"]
    )
    assert {message["caption"] for message in bot.sent} == {expected_caption}
    promo = state["manual_promos"]["manual-001"]
    assert promo["status"] == "active"
    assert promo["first_notification_sent"] is True
    assert set(promo["reminders_sent"]["24h"]) == {101}
    assert set(promo["group_reminders_sent"]["24h"]) == {-100}
    confirmation = query.edit_message_caption.await_args.args[0]
    assert "DM enviados: 1" in confirmation
    assert "Grupos enviados: 1" in confirmation


def test_manual_promo_test_delivery_only_reaches_administrator():
    """Manual promo tests send only to the administrator without changing state."""
    promo = build_active_promo()
    state = {"manual_promos": {"manual-001": promo}}
    query = SimpleNamespace(
        from_user=SimpleNamespace(id=42),
        data="manual_test_manual-001",
        answer=AsyncMock(),
        edit_message_caption=AsyncMock(),
    )
    bot = FakeBot()
    context = SimpleNamespace(bot=bot)
    original_promo = promo.copy()

    asyncio.run(
        handle_manual_promo_test(
            SimpleNamespace(callback_query=query),
            context,
            admin_id=42,
            state=state,
        )
    )

    assert [message["chat_id"] for message in bot.sent] == [42]
    assert "MODO PRUEBA" in bot.sent[0]["caption"]
    assert promo == original_promo
    assert "MODO PRUEBA" in query.edit_message_caption.await_args.args[0]


def test_manual_promo_test_rejects_unauthorized_users():
    """Non-admin users cannot trigger manual promo test delivery."""
    query = SimpleNamespace(
        from_user=SimpleNamespace(id=99),
        data="manual_test_manual-001",
        answer=AsyncMock(),
        edit_message_caption=AsyncMock(),
    )
    bot = FakeBot()
    state = {"manual_promos": {"manual-001": build_active_promo()}}

    asyncio.run(
        handle_manual_promo_test(
            SimpleNamespace(callback_query=query),
            SimpleNamespace(bot=bot),
            admin_id=42,
            state=state,
        )
    )

    assert bot.sent == []
    query.answer.assert_not_awaited()


def test_manual_promo_schedule_persists_first_notification(tmp_path):
    """Scheduling a first notification activates and persists the promo."""
    promo = build_active_promo()
    promo["status"] = "pending"
    state = {"manual_promos": {"manual-001": promo}}
    config = {"MANUAL_PROMOS_FILE": str(tmp_path / "manual_promos.json")}
    context = SimpleNamespace(user_data={})
    schedule_query = SimpleNamespace(
        from_user=SimpleNamespace(id=42),
        data="manual_schedule_manual-001",
        answer=AsyncMock(),
        edit_message_caption=AsyncMock(),
    )

    asyncio.run(
        handle_manual_promo_schedule(
            SimpleNamespace(callback_query=schedule_query),
            context,
            admin_id=42,
            state=state,
        )
    )

    schedule_input = SimpleNamespace(
        effective_user=SimpleNamespace(id=42),
        message=SimpleNamespace(
            text="2026-10-15 12:00",
            reply_text=AsyncMock(),
        ),
    )
    with patch.object(admin_handlers, "datetime", FrozenDateTime):
        asyncio.run(
            handle_manual_promo_schedule_input(
                schedule_input,
                context,
                admin_id=42,
                state=state,
                config=config,
            )
        )

    assert state["manual_promos"]["manual-001"]["status"] == "active"
    assert state["manual_promos"]["manual-001"]["first_notification_at"] == (
        "2026-10-15T12:00:00-06:00"
    )
    assert context.user_data == {}
    assert load_json(config["MANUAL_PROMOS_FILE"], {})["manual-001"][
        "first_notification_at"
    ] == "2026-10-15T12:00:00-06:00"


def test_manual_promo_group_retries_do_not_duplicate_successful_groups(tmp_path):
    """Failed groups retry without repeating successful DM or group deliveries."""
    state = {
        "subscribers": {101},
        "groups": {-100, -200},
        "manual_promos": {"manual-001": build_active_promo()},
    }
    config = {
        "MANUAL_PROMOS_FILE": str(tmp_path / "manual_promos.json"),
        "REMINDER_RULES": {"24h": timedelta(days=1)},
    }
    bot = FakeBot(failures={-200})
    context = SimpleNamespace(bot=bot)

    with patch.object(manual_promos, "datetime", FrozenDateTime):
        asyncio.run(manual_promos.check_manual_promo_reminders(context, config, state))

    assert [message["chat_id"] for message in bot.sent] == [101, -100]
    assert state["manual_promos"]["manual-001"]["group_reminders_sent"] == {
        "24h": [-100]
    }

    bot.failures.clear()
    with patch.object(manual_promos, "datetime", FrozenDateTime):
        asyncio.run(manual_promos.check_manual_promo_reminders(context, config, state))

    assert [message["chat_id"] for message in bot.sent] == [101, -100, -200]


def test_manual_promo_rejects_unauthorized_users():
    """Non-admin users cannot start a manual promo draft."""
    context = SimpleNamespace(user_data={})
    message = SimpleNamespace(
        text="/manualpromo 2026-12-01 11:00 | Event | Text",
        reply_text=AsyncMock(),
    )
    update = SimpleNamespace(
        effective_user=SimpleNamespace(id=99),
        message=message,
    )

    asyncio.run(manual_promo(update, context, admin_id=42))

    assert context.user_data == {}
    message.reply_text.assert_not_awaited()


def test_manual_promo_rejects_invalid_input():
    """Missing fields and invalid dates produce usage feedback."""
    context = SimpleNamespace(user_data={})
    message = SimpleNamespace(
        text="/manualpromo not-a-date | Event",
        reply_text=AsyncMock(),
    )
    update = SimpleNamespace(
        effective_user=SimpleNamespace(id=42),
        message=message,
    )

    asyncio.run(manual_promo(update, context, admin_id=42))

    assert context.user_data == {}
    assert message.reply_text.await_count == 1


def test_manual_promo_photo_without_draft_is_ignored(tmp_path):
    """A photo without a pending command does not create a promo."""
    message = SimpleNamespace(
        photo=[SimpleNamespace(file_id="photo-id")],
        reply_photo=AsyncMock(),
    )
    update = SimpleNamespace(
        effective_user=SimpleNamespace(id=42),
        message=message,
    )
    context = SimpleNamespace(user_data={})
    state = {"manual_promos": {}}
    config = {"MANUAL_PROMOS_FILE": str(tmp_path / "manual_promos.json")}

    asyncio.run(handle_manual_promo_photo(update, context, 42, state, config))

    assert state["manual_promos"] == {}
    message.reply_photo.assert_not_awaited()


def test_manual_promo_cancel_removes_persisted_promo(tmp_path):
    """Canceling a staged promo removes it from memory and JSON storage."""
    config = {"MANUAL_PROMOS_FILE": str(tmp_path / "manual_promos.json")}
    state = {"manual_promos": {"manual-001": build_active_promo()}}
    query = SimpleNamespace(
        from_user=SimpleNamespace(id=42),
        data="manual_cancel_manual-001",
        answer=AsyncMock(),
        edit_message_caption=AsyncMock(),
    )

    asyncio.run(
        handle_manual_promo_cancel(
            SimpleNamespace(callback_query=query),
            SimpleNamespace(),
            42,
            state,
            config,
        )
    )

    assert state["manual_promos"] == {}
    assert load_json(config["MANUAL_PROMOS_FILE"], {"unexpected": True}) == {}


def test_manual_promo_reminders_skip_expired_and_malformed_records(tmp_path):
    """Expired and malformed manual promos are skipped without sending."""
    expired = build_active_promo()
    expired["event_start"] = "2026-10-15T10:00:00-06:00"
    malformed = build_active_promo()
    malformed["event_start"] = "not-a-date"
    state = {
        "subscribers": {101},
        "manual_promos": {"expired": expired, "malformed": malformed},
    }
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
