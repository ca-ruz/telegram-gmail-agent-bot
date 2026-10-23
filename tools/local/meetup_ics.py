"""Meetup iCalendar feed fetching and event normalization."""

import logging
import re
from datetime import date, datetime, timedelta, timezone
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from icalendar import Calendar

logger = logging.getLogger(__name__)

MEETUP_TIMEZONE = ZoneInfo("America/Mexico_City")
DEFAULT_TIMEOUT_SECONDS = 20
DEFAULT_LOOKAHEAD_DAYS = 8
URL_PATTERN = re.compile(r"https?://[^\s<>]+")
LOCATION_PATTERNS = (
    re.compile(r"^\s*Nos vemos en\s+(.+?)\s*:?\s*$", re.IGNORECASE | re.MULTILINE),
    re.compile(r"^\s*(?:Lugar|Ubicación|Venue)\s*:\s*(.+?)\s*$", re.IGNORECASE | re.MULTILINE),
)


class MeetupICSFetchError(RuntimeError):
    """Raised when the Meetup ICS feed cannot be downloaded."""


class MeetupICSParseError(ValueError):
    """Raised when the Meetup ICS feed cannot be parsed."""


def fetch_ics_feed(url, timeout=DEFAULT_TIMEOUT_SECONDS):
    """Download an ICS feed and return its raw bytes."""
    feed_url = str(url or "").strip()
    if not feed_url:
        raise MeetupICSFetchError("Meetup ICS URL is not configured.")

    request = Request(
        feed_url,
        headers={"User-Agent": "BTCGDL-Message-Bot/1.0"},
    )
    try:
        with urlopen(request, timeout=timeout) as response:
            return response.read()
    except (HTTPError, URLError, TimeoutError, OSError) as exc:
        logger.error("Failed to fetch Meetup ICS feed: %s", exc)
        raise MeetupICSFetchError("Could not fetch Meetup ICS feed.") from exc


def _property_text(component, name):
    """Return an ICS property as normalized text."""
    value = component.get(name)
    return str(value).strip() if value is not None else ""


def _property_datetime(component, name):
    """Return an ICS date or datetime with a reliable timezone."""
    property_value = component.get(name)
    if property_value is None:
        return None

    decoded_value = component.decoded(name)
    if isinstance(decoded_value, datetime):
        event_time = decoded_value
        if event_time.tzinfo is None:
            timezone_name = str(property_value.params.get("TZID", "")).strip('"')
            try:
                event_timezone = ZoneInfo(timezone_name) if timezone_name else MEETUP_TIMEZONE
            except ZoneInfoNotFoundError:
                event_timezone = MEETUP_TIMEZONE
            event_time = event_time.replace(tzinfo=event_timezone)
        return event_time

    if isinstance(decoded_value, date):
        return decoded_value

    raise MeetupICSParseError(f"Unsupported {name} value in Meetup ICS event.")


def _format_ics_datetime(value):
    """Return an ICS date or datetime in the bot's normalized event format."""
    if value is None:
        return None
    return value.isoformat()


def _extract_location(description):
    """Extract an obvious venue line without guessing from arbitrary prose."""
    for pattern in LOCATION_PATTERNS:
        match = pattern.search(description or "")
        if match:
            return match.group(1).strip()
    return ""


def _extract_event_url(component, description):
    """Return the event URL property or the first URL in its description."""
    event_url = _property_text(component, "URL")
    if event_url:
        return event_url
    match = URL_PATTERN.search(description or "")
    return match.group(0).rstrip(".,") if match else ""


def _normalize_event(component):
    """Convert one VEVENT component into the bot's event dictionary shape."""
    event_id = _property_text(component, "UID")
    if not event_id:
        logger.warning("Skipping Meetup ICS event without a UID.")
        return None

    description = _property_text(component, "DESCRIPTION")
    start_time = _property_datetime(component, "DTSTART")
    if start_time is None:
        logger.warning("Skipping Meetup ICS event %s without DTSTART.", event_id)
        return None

    end_time = _property_datetime(component, "DTEND")
    start_key = "date" if isinstance(start_time, date) and not isinstance(start_time, datetime) else "dateTime"
    event = {
        "id": event_id,
        "ical_uid": event_id,
        "summary": _property_text(component, "SUMMARY") or "Evento",
        "description": description,
        "location": _extract_location(description),
        "htmlLink": _extract_event_url(component, description),
        "start": {start_key: _format_ics_datetime(start_time)},
        "status": (_property_text(component, "STATUS") or "CONFIRMED").lower(),
    }
    if end_time is not None:
        end_key = "date" if isinstance(end_time, date) and not isinstance(end_time, datetime) else "dateTime"
        event["end"] = {end_key: _format_ics_datetime(end_time)}

    for source_name, target_name in (("CREATED", "created"), ("LAST-MODIFIED", "updated"), ("DTSTAMP", "updated_at")):
        timestamp = _property_datetime(component, source_name)
        if timestamp is not None:
            event[target_name] = _format_ics_datetime(timestamp)

    return event


def parse_meetup_ics(content):
    """Parse raw ICS bytes into normalized Meetup event dictionaries."""
    try:
        calendar = Calendar.from_ical(content)
    except (ValueError, TypeError, IndexError) as exc:
        logger.error("Failed to parse Meetup ICS feed: %s", exc)
        raise MeetupICSParseError("Could not parse Meetup ICS feed.") from exc

    events = []
    for component in calendar.walk("VEVENT"):
        try:
            event = _normalize_event(component)
        except (KeyError, TypeError, ValueError) as exc:
            logger.warning("Skipping malformed Meetup ICS event: %s", exc)
            continue
        if event is not None:
            events.append(event)
    return events


def _event_start_datetime(event):
    """Return a normalized event's start time as an aware datetime."""
    start_value = event["start"].get("dateTime", event["start"].get("date"))
    event_time = datetime.fromisoformat(start_value.replace("Z", "+00:00"))
    if event_time.tzinfo is None:
        event_time = event_time.replace(tzinfo=MEETUP_TIMEZONE)
    return event_time


def fetch_meetup_ics_events(
    url,
    days=DEFAULT_LOOKAHEAD_DAYS,
    now=None,
    timeout=DEFAULT_TIMEOUT_SECONDS,
):
    """Fetch and return future Meetup events within the requested time window."""
    current_time = now or datetime.now(timezone.utc)
    if current_time.tzinfo is None:
        current_time = current_time.replace(tzinfo=timezone.utc)

    events = parse_meetup_ics(fetch_ics_feed(url, timeout=timeout))
    cutoff = current_time + timedelta(days=days) if days is not None else None
    upcoming = []
    for event in events:
        event_time = _event_start_datetime(event).astimezone(timezone.utc)
        if event_time < current_time.astimezone(timezone.utc):
            continue
        if cutoff is not None and event_time > cutoff.astimezone(timezone.utc):
            continue
        upcoming.append(event)
    return sorted(upcoming, key=_event_start_datetime)
