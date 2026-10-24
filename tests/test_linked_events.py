import asyncio
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from bot.handlers.admin import (
    handle_linked_promo_input,
    linked_promo,
    linked_promo_done,
    parse_linked_event_input,
)
from core import event_registrations, manual_promos
from core.linked_events import (
    LinkedEventValidationError,
    build_linked_event,
    validate_linked_events,
)


class FrozenDateTime(datetime):
    """Datetime class with a controllable current time for linked reminders."""

    current = datetime(
        2026,
        10,
        15,
        13,
        0,
        tzinfo=manual_promos.MANUAL_PROMO_TIMEZONE,
    )

    @classmethod
    def now(cls, tz=None):
        """Return the configured current time with the requested timezone."""
        return cls.current.astimezone(tz) if tz else cls.current.replace(tzinfo=None)


class FakeBot:
    """Minimal Telegram bot double for linked reminder tests."""

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


def test_linked_events_are_validated_and_ordered():
    """Linked event groups return segments in their explicit display order."""
    first = build_linked_event(
        "event-2",
        "Coffee + Bitcoin",
        "2026-11-20T20:00:00-06:00",
        display_order=1,
    )
    second = build_linked_event(
        "event-1",
        "Meetup",
        "2026-11-20T18:00:00-06:00",
        display_order=0,
    )

    ordered = validate_linked_events([first, second])

    assert [event["event_id"] for event in ordered] == ["event-1", "event-2"]


def test_linked_event_validation_rejects_duplicate_ids_and_invalid_urls():
    """Invalid linked-event groups are rejected before persistence."""
    event = build_linked_event(
        "event-1",
        "Meetup",
        "2026-11-20T18:00:00-06:00",
    )
    duplicate = build_linked_event(
        "event-1",
        "Coffee",
        "2026-11-20T20:00:00-06:00",
        display_order=1,
    )

    with pytest.raises(LinkedEventValidationError, match="Duplicate linked event ID"):
        validate_linked_events([event, duplicate])

    with pytest.raises(LinkedEventValidationError, match="HTTP or HTTPS"):
        build_linked_event(
            "event-2",
            "Coffee",
            "2026-11-20T20:00:00-06:00",
            url="not-a-url",
        )


def test_admin_parser_builds_structured_event_segment():
    """The guided admin format captures optional end time, URL, and relationship."""
    event = parse_linked_event_input(
        "Meetup | 2099-11-20 18:00 | 2099-11-20 20:00 | "
        "Community Space | https://example.com/meetup | inmediatamente después",
        display_order=0,
        event_group_id="linked-1",
    )

    assert event == {
        "event_id": "linked-1-event-1",
        "summary": "Meetup",
        "event_start": "2099-11-20T18:00:00-06:00",
        "event_end": "2099-11-20T20:00:00-06:00",
        "location": "Community Space",
        "url": "https://example.com/meetup",
        "relationship": "inmediatamente después",
        "display_order": 0,
    }


def test_admin_linked_promo_workflow_collects_events_and_publication_text():
    """The guided workflow creates a linked manual promo draft before flyer upload."""
    context = SimpleNamespace(user_data={})
    reply_text = AsyncMock()
    update = SimpleNamespace(
        effective_user=SimpleNamespace(id=42),
        message=SimpleNamespace(reply_text=reply_text),
    )

    asyncio.run(linked_promo(update, context, admin_id=42))
    group_id = context.user_data["linked_promo_draft"]["event_group_id"]

    for text in (
        "Meetup | 2099-11-20 18:00 | 2099-11-20 20:00 | Community Space | https://example.com/meetup |",
        "Coffee | 2099-11-20 20:30 | | Café Central | https://example.com/coffee | inmediatamente después",
    ):
        event_update = SimpleNamespace(
            effective_user=SimpleNamespace(id=42),
            message=SimpleNamespace(text=text, reply_text=AsyncMock()),
        )
        asyncio.run(handle_linked_promo_input(event_update, context, admin_id=42))

    asyncio.run(linked_promo_done(update, context, admin_id=42))
    text_update = SimpleNamespace(
        effective_user=SimpleNamespace(id=42),
        message=SimpleNamespace(
            text="Dos eventos, una comunidad.",
            reply_text=AsyncMock(),
        ),
    )
    asyncio.run(handle_linked_promo_input(text_update, context, admin_id=42))

    draft = context.user_data["manual_promo_draft"]
    assert draft["event_id"] == group_id
    assert [event["summary"] for event in draft["linked_events"]] == [
        "Meetup",
        "Coffee",
    ]
    assert draft["text"] == "Dos eventos, una comunidad."
    assert "linked_promo_draft" not in context.user_data


def test_linked_caption_places_event_details_before_links():
    """Linked captions show structured details before the event URLs."""
    caption = manual_promos.format_manual_promo_caption({
        "text": "Dos eventos, una comunidad.",
        "linked_events": [
            {
                "event_id": "event-1",
                "summary": "Meetup",
                "event_start": "2026-10-20T18:00:00-06:00",
                "event_end": "2026-10-20T20:00:00-06:00",
                "location": "Community Space",
                "url": "https://example.com/meetup",
                "relationship": "inmediatamente después",
                "display_order": 0,
            },
            {
                "event_id": "event-2",
                "summary": "Coffee",
                "event_start": "2026-10-20T20:30:00-06:00",
                "location": "Café Central",
                "url": "https://example.com/coffee",
                "relationship": "",
                "display_order": 1,
            },
        ],
    })

    assert caption.index("📅 Martes 20 de octubre") < caption.index(
        "https://example.com/meetup"
    )
    assert "🕕 6 PM - 8 PM" in caption
    assert "➡️ inmediatamente después" in caption
    assert caption.index("https://example.com/meetup") < caption.index(
        "https://example.com/coffee"
    )


def test_linked_reminders_use_per_event_keys_and_deduplicate(tmp_path):
    """Each linked event gets its own reminder stage and sends only once."""
    promo = manual_promos.build_manual_promo(
        "linked-1",
        "Meetup + Coffee",
        "2026-10-16T11:00:00-06:00",
        "Dos eventos.",
        "photo-id",
        location="Community Space",
        linked_events=[
            build_linked_event(
                "event-1",
                "Meetup",
                "2026-10-16T11:00:00-06:00",
                location="Community Space",
                display_order=0,
            ),
            build_linked_event(
                "event-2",
                "Coffee",
                "2026-10-15T18:00:00-06:00",
                location="Café Central",
                display_order=1,
            ),
        ],
    )
    promo["status"] = "active"
    state = {
        "subscribers": {101},
        "groups": {-100},
        "manual_promos": {"linked-1": promo},
        "event_registrations": {},
    }
    config = {
        "MANUAL_PROMOS_FILE": str(tmp_path / "manual_promos.json"),
        "REMINDER_RULES": {"24h": timedelta(days=1)},
    }
    bot = FakeBot()
    context = SimpleNamespace(bot=bot)

    with patch.object(manual_promos, "datetime", FrozenDateTime):
        asyncio.run(manual_promos.check_manual_promo_reminders(context, config, state))
        asyncio.run(manual_promos.check_manual_promo_reminders(context, config, state))

    assert len(bot.sent) == 4
    assert set(promo["reminders_sent"]) == {
        "linked:event-1:24h",
        "linked:event-2:same_day",
    }
    assert all("🔔 Recordatorio:" in message["caption"] for message in bot.sent)
    assert any("Recordatorio: Meetup" in message["caption"] for message in bot.sent)
    assert any("Recordatorio: Coffee" in message["caption"] for message in bot.sent)


def test_cleanup_removes_expired_linked_event_registrations():
    """Registration cleanup handles segment IDs inside linked promos."""
    registrations = {"event-1": ["1"], "event-2": ["2"]}
    promos = {
        "linked-1": {
            "event_id": "linked-1",
            "event_start": "2099-11-20T18:00:00+00:00",
            "linked_events": [
                {
                    "event_id": "event-1",
                    "event_start": "2026-10-15T11:00:00+00:00",
                },
                {
                    "event_id": "event-2",
                    "event_start": "2099-11-20T20:00:00+00:00",
                },
            ],
        }
    }

    removed = event_registrations.cleanup_expired_event_registrations(
        registrations,
        promos,
        datetime(2026, 10, 15, 12, 0, tzinfo=timezone.utc),
    )

    assert removed == 1
    assert registrations == {"event-2": ["2"]}
