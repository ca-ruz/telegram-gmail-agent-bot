import asyncio
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

from bot.handlers import admin as admin_handlers
from bot.handlers import user
from core.manual_promos import get_upcoming_published_promos
from core.monthly_calendar import (
    build_monthly_calendar,
    format_calendar_title,
    load_monthly_calendar,
    parse_calendar_month,
    save_monthly_calendar,
)


def make_callback_update(callback_data, user_id=42):
    """Build a minimal callback update for public content tests."""
    message = SimpleNamespace(
        photo=[],
        chat_id=123,
        delete=AsyncMock(),
    )
    query = SimpleNamespace(
        data=callback_data,
        from_user=SimpleNamespace(id=user_id),
        message=message,
        answer=AsyncMock(),
        edit_message_text=AsyncMock(),
        edit_message_caption=AsyncMock(),
    )
    return SimpleNamespace(callback_query=query)


def make_promo(event_start, status="active", first_notification_sent=True):
    """Build a persisted promo fixture for public flyer filtering."""
    return {
        "event_id": event_start,
        "summary": "Bitcoin Meetup",
        "location": "Community Space",
        "event_start": event_start,
        "text": "Una conversación sobre Bitcoin.",
        "image_file_id": f"photo-{event_start}",
        "status": status,
        "first_notification_sent": first_notification_sent,
    }


def test_monthly_calendar_round_trip_and_title(tmp_path):
    """Monthly calendar metadata persists and formats in Spanish."""
    file_path = tmp_path / "monthly_calendar.json"
    calendar = build_monthly_calendar(
        "2026-10",
        "calendar-photo-id",
        caption="Calendario de octubre",
        uploaded_at="2026-10-01T09:00:00-06:00",
    )

    save_monthly_calendar(str(file_path), calendar)

    assert load_monthly_calendar(str(file_path)) == calendar
    assert format_calendar_title(calendar) == "🗓 Calendario de octubre de 2026"
    assert parse_calendar_month("2026-10") == "2026-10"


def test_monthly_calendar_defaults_to_current_month():
    """An omitted calendar month uses the supplied Guadalajara date."""
    now = datetime(2026, 11, 3, 12, 0)

    assert parse_calendar_month("", now) == "2026-11"


def test_admin_can_upload_and_replace_monthly_calendar(tmp_path):
    """The administrator's next photo replaces persisted calendar metadata."""
    config = {"MONTHLY_CALENDAR_FILE": str(tmp_path / "monthly_calendar.json")}
    state = {"monthly_calendar": {"month": "2026-09", "file_id": "old-photo"}}
    context = SimpleNamespace(user_data={})
    command_update = SimpleNamespace(
        effective_user=SimpleNamespace(id=42),
        message=SimpleNamespace(
            text="/calendario 2026-10 | Octubre en BTC GDL",
            reply_text=AsyncMock(),
        ),
    )

    asyncio.run(admin_handlers.monthly_calendar(command_update, context, admin_id=42))

    photo_update = SimpleNamespace(
        effective_user=SimpleNamespace(id=42),
        message=SimpleNamespace(
            photo=[SimpleNamespace(file_id="new-photo")],
            reply_text=AsyncMock(),
        ),
    )
    asyncio.run(
        admin_handlers.handle_monthly_calendar_photo(
            photo_update,
            context,
            admin_id=42,
            state=state,
            config=config,
        )
    )

    assert state["monthly_calendar"]["month"] == "2026-10"
    assert state["monthly_calendar"]["file_id"] == "new-photo"
    assert state["monthly_calendar"]["caption"] == "Octubre en BTC GDL"
    assert load_monthly_calendar(config["MONTHLY_CALENDAR_FILE"])["file_id"] == "new-photo"
    assert "guardado correctamente" in photo_update.message.reply_text.await_args.args[0]


def test_public_calendar_callback_sends_stored_image():
    """The public calendar action sends the stored Telegram image."""
    update = make_callback_update("monthly_calendar")
    context = SimpleNamespace(bot=SimpleNamespace(send_photo=AsyncMock()))
    calendar = {"month": "2026-10", "file_id": "calendar-photo-id", "caption": ""}

    asyncio.run(
        user.button_click(
            update,
            context,
            set(),
            monthly_calendar=calendar,
        )
    )

    update.callback_query.message.delete.assert_awaited_once()
    context.bot.send_photo.assert_awaited_once()
    call = context.bot.send_photo.await_args.kwargs
    assert call["photo"] == "calendar-photo-id"
    assert call["caption"] == "🗓 Calendario de octubre de 2026"


def test_public_calendar_callback_handles_missing_image():
    """The public calendar action explains when no image is available."""
    update = make_callback_update("monthly_calendar")

    asyncio.run(user.button_click(update, SimpleNamespace(), set()))

    message = update.callback_query.edit_message_text.await_args.kwargs["text"]
    assert "no hay un calendario" in message.lower()


def test_upcoming_published_promos_are_filtered_and_ordered():
    """Only future published flyers are returned in event order."""
    promos = {
        "later": make_promo("2027-01-20T18:00:00-06:00"),
        "soon": make_promo("2026-12-20T18:00:00-06:00"),
        "pending": make_promo(
            "2026-11-20T18:00:00-06:00",
            status="active",
            first_notification_sent=False,
        ),
        "cancelled": make_promo("2026-11-21T18:00:00-06:00", status="cancelled"),
        "past": make_promo("2026-09-20T18:00:00-06:00"),
    }

    upcoming = get_upcoming_published_promos(
        promos,
        datetime.fromisoformat("2026-10-20T12:00:00-06:00"),
    )

    assert [promo["event_id"] for promo in upcoming] == [
        "2026-12-20T18:00:00-06:00",
        "2027-01-20T18:00:00-06:00",
    ]


def test_public_upcoming_flyers_send_only_eligible_promos():
    """The public flyer action sends eligible flyers with registration actions."""
    update = make_callback_update("upcoming_flyers")
    context = SimpleNamespace(bot=SimpleNamespace(send_photo=AsyncMock()))
    promos = {
        "one": make_promo("2027-01-20T18:00:00-06:00"),
        "two": make_promo("2027-02-20T18:00:00-06:00"),
        "draft": make_promo("2027-03-20T18:00:00-06:00", status="pending"),
    }

    asyncio.run(
        user.button_click(
            update,
            context,
            set(),
            manual_promos=promos,
        )
    )

    update.callback_query.message.delete.assert_awaited_once()
    assert context.bot.send_photo.await_count == 2
    first_call, second_call = context.bot.send_photo.await_args_list
    assert first_call.kwargs["photo"] == "photo-2027-01-20T18:00:00-06:00"
    assert second_call.kwargs["photo"] == "photo-2027-02-20T18:00:00-06:00"
    first_keyboard = first_call.kwargs["reply_markup"]
    second_keyboard = second_call.kwargs["reply_markup"]
    assert first_keyboard is not None
    assert second_keyboard is not None
    assert first_keyboard.inline_keyboard[0][0].callback_data.startswith(
        "event_register_"
    )
    assert second_keyboard.inline_keyboard[0][0].callback_data.startswith(
        "event_register_"
    )
