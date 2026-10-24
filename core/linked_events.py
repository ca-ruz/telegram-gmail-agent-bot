"""Domain helpers for announcements containing multiple related events."""

from datetime import datetime
from urllib.parse import urlparse


class LinkedEventValidationError(ValueError):
    """Raised when a linked-event segment or group is invalid."""


def build_linked_event(
    event_id,
    summary,
    event_start,
    location="",
    url="",
    relationship="",
    event_end=None,
    display_order=0,
):
    """Build one JSON-serializable linked-event segment."""
    event = {
        "event_id": event_id,
        "summary": summary,
        "event_start": event_start,
        "event_end": event_end,
        "location": location,
        "url": url,
        "relationship": relationship,
        "display_order": display_order,
    }
    validate_linked_event(event)
    return event


def validate_linked_event(event):
    """Validate one linked-event segment and return it unchanged."""
    if not isinstance(event, dict):
        raise LinkedEventValidationError("Each linked event must be an object.")

    for field in ("event_id", "summary", "event_start"):
        value = event.get(field)
        if not isinstance(value, str) or not value.strip():
            raise LinkedEventValidationError(
                f"Linked event field '{field}' is required."
            )

    _parse_datetime(event["event_start"], "event_start")
    if event.get("event_end"):
        start = _parse_datetime(event["event_start"], "event_start")
        end = _parse_datetime(event["event_end"], "event_end")
        if end <= start:
            raise LinkedEventValidationError(
                "event_end must be later than event_start."
            )

    display_order = event.get("display_order", 0)
    if isinstance(display_order, bool) or not isinstance(display_order, int):
        raise LinkedEventValidationError("display_order must be an integer.")
    if display_order < 0:
        raise LinkedEventValidationError("display_order cannot be negative.")

    for field in ("location", "url", "relationship"):
        value = event.get(field, "")
        if not isinstance(value, str):
            raise LinkedEventValidationError(
                f"Linked event field '{field}' must be text."
            )

    if event.get("url"):
        parsed_url = urlparse(event["url"])
        if parsed_url.scheme not in {"http", "https"} or not parsed_url.netloc:
            raise LinkedEventValidationError("url must be an HTTP or HTTPS URL.")

    return event


def validate_linked_events(events):
    """Validate and return a deterministically ordered linked-event group."""
    if not isinstance(events, list) or len(events) < 2:
        raise LinkedEventValidationError(
            "A linked-event group must contain at least two events."
        )

    validated = []
    display_orders = set()
    event_ids = set()
    for event in events:
        validate_linked_event(event)
        event_id = event["event_id"]
        display_order = event.get("display_order", 0)
        if event_id in event_ids:
            raise LinkedEventValidationError(
                f"Duplicate linked event ID: {event_id}."
            )
        if display_order in display_orders:
            raise LinkedEventValidationError(
                f"Duplicate linked event order: {display_order}."
            )
        event_ids.add(event_id)
        display_orders.add(display_order)
        validated.append(event)

    return sorted(validated, key=lambda event: event["display_order"])


def _parse_datetime(value, field_name):
    """Parse an ISO datetime field or raise a domain validation error."""
    if not isinstance(value, str) or not value.strip():
        raise LinkedEventValidationError(f"{field_name} must be an ISO datetime.")
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise LinkedEventValidationError(
            f"{field_name} must be an ISO datetime."
        ) from exc
