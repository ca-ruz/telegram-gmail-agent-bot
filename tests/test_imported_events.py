from datetime import datetime, timezone

from core.imported_events import (
    assign_imported_event_flyer,
    get_events_missing_flyers,
    get_imported_event_key,
    sync_imported_events,
)


def make_event(event_id, start, status="confirmed"):
    """Build a normalized imported event fixture."""
    return {
        "id": event_id,
        "summary": f"Event {event_id}",
        "start": {"dateTime": start},
        "status": status,
    }


def test_sync_imported_events_preserves_flyers_and_marks_removed_events():
    """A feed refresh updates metadata without losing flyers."""
    source = "meetup_ics"
    existing_key = get_imported_event_key(source, "event-existing")
    imported_events = {
        existing_key: {
            "source": source,
            "source_id": "event-existing",
            "summary": "Old title",
            "start": {"dateTime": "2026-10-24T11:00:00-06:00"},
            "status": "confirmed",
            "flyer_file_id": "telegram-file-id",
        }
    }
    now = datetime(2026, 10, 23, 12, 0, tzinfo=timezone.utc)
    refreshed = sync_imported_events(
        imported_events,
        [make_event("event-new", "2026-10-25T11:00:00-06:00")],
        source,
        now=now,
        lookahead_days=8,
    )

    assert refreshed[existing_key]["status"] == "removed"
    assert refreshed[existing_key]["flyer_file_id"] == "telegram-file-id"
    assert refreshed[get_imported_event_key(source, "event-new")]["summary"] == "Event event-new"


def test_missing_flyer_list_excludes_inactive_events_and_sorts():
    """Only future active events without flyers appear in the admin list."""
    now = datetime(2026, 10, 23, 12, 0, tzinfo=timezone.utc)
    imported_events = {
        "meetup_ics:later": make_event("later", "2026-10-26T11:00:00-06:00"),
        "meetup_ics:soon": make_event("soon", "2026-10-24T11:00:00-06:00"),
        "meetup_ics:canceled": make_event(
            "canceled",
            "2026-10-25T11:00:00-06:00",
            status="cancelled",
        ),
        "meetup_ics:removed": make_event(
            "removed",
            "2026-10-25T12:00:00-06:00",
            status="removed",
        ),
    }
    imported_events["meetup_ics:later"]["flyer_file_id"] = "already-uploaded"

    missing = get_events_missing_flyers(imported_events, now=now)

    assert [event["key"] for event in missing] == ["meetup_ics:soon"]


def test_assign_imported_event_flyer_persists_file_id():
    """Flyer assignment records the Telegram file ID and timestamp."""
    imported_events = {
        "meetup_ics:event-1": {
            "source": "meetup_ics",
            "source_id": "event-1",
        }
    }
    assigned_at = datetime(2026, 10, 23, 18, 0, tzinfo=timezone.utc)

    event = assign_imported_event_flyer(
        imported_events,
        "meetup_ics:event-1",
        "telegram-file-id",
        assigned_at=assigned_at,
    )

    assert event["flyer_file_id"] == "telegram-file-id"
    assert event["flyer_assigned_at"] == "2026-10-23T18:00:00+00:00"
