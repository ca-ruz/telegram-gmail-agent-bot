from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch
from urllib.error import URLError

import pytest

from core.promoter import fetch_upcoming_events_for_source
from tools.local.meetup_ics import (
    MeetupICSFetchError,
    MeetupICSParseError,
    fetch_meetup_ics_events,
    parse_meetup_ics,
)


FIXTURE_PATH = Path(__file__).parent / "fixtures" / "meetup_events.ics"


class FakeResponse:
    """Minimal response double for ICS feed tests."""

    def __init__(self, content):
        self.content = content

    def __enter__(self):
        """Return the response double for a context manager."""
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        """Close the response context without suppressing exceptions."""
        return False

    def read(self):
        """Return fixture bytes."""
        return self.content


def fixture_content():
    """Load the local Meetup ICS fixture."""
    return FIXTURE_PATH.read_bytes()


def test_parse_meetup_ics_normalizes_timed_and_all_day_events():
    """ICS events preserve IDs, timezone, links, status, and date semantics."""
    events = parse_meetup_ics(fixture_content())
    timed_event = next(event for event in events if event["id"].startswith("event_"))
    all_day_event = next(event for event in events if event["id"] == "event-all-day@meetup.com")
    canceled_event = next(event for event in events if event["id"] == "event-cancelled@meetup.com")

    assert timed_event["summary"] == "Bitcoin Coffee"
    assert timed_event["location"] == "Cafe Nonno"
    assert timed_event["start"]["dateTime"] == "2026-10-24T11:00:00-06:00"
    assert timed_event["htmlLink"].endswith("/events/316791130/")
    assert timed_event["created"] == "2026-10-01T02:45:38+00:00"
    assert timed_event["updated"] == "2026-10-01T02:45:38+00:00"
    assert all_day_event["start"] == {"date": "2026-10-26"}
    assert all_day_event["end"] == {"date": "2026-10-27"}
    assert canceled_event["status"] == "cancelled"


def test_fetch_meetup_ics_events_filters_and_sorts_future_events():
    """The provider applies the lookahead window and chronological ordering."""
    now = datetime(2026, 10, 23, 12, 0, tzinfo=timezone.utc)
    response = FakeResponse(fixture_content())

    with patch("tools.local.meetup_ics.urlopen", return_value=response) as opener:
        events = fetch_meetup_ics_events(
            "https://example.com/events.ics",
            now=now,
            days=2,
        )

    assert [event["id"] for event in events] == [
        "event_316791130@meetup.com",
        "event-cancelled@meetup.com",
    ]
    request = opener.call_args.args[0]
    assert request.full_url == "https://example.com/events.ics"


def test_fetch_meetup_ics_events_reports_network_failures():
    """Network failures use the provider-specific fetch exception."""
    with patch(
        "tools.local.meetup_ics.urlopen",
        side_effect=URLError("offline"),
    ):
        with pytest.raises(MeetupICSFetchError):
            fetch_meetup_ics_events("https://example.com/events.ics")


def test_parse_meetup_ics_reports_invalid_calendar_data():
    """Invalid calendar data does not silently become an empty event list."""
    with pytest.raises(MeetupICSParseError):
        parse_meetup_ics(b"not an iCalendar document")


def test_event_source_selection_uses_meetup_ics_when_explicitly_configured():
    """The source boundary selects Meetup only when configured."""
    event = {"id": "event-1", "start": {"dateTime": "2026-10-24T11:00:00-06:00"}}
    config = {
        "EVENT_SOURCE": "meetup_ics",
        "MEETUP_ICS_URL": "https://example.com/events.ics",
    }

    with patch(
        "core.promoter.fetch_meetup_ics_events",
        return_value=[event],
    ) as provider:
        events, now = fetch_upcoming_events_for_source(config)

    assert events == [event]
    assert now.tzinfo is timezone.utc
    provider.assert_called_once()
