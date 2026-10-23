"""Persistence helpers for events imported from external providers."""

from datetime import datetime, timedelta, timezone

from tools.local.data_manager import load_json, save_json


def load_imported_events(file_path):
    """Load imported event records from a JSON mapping."""
    imported_events = load_json(file_path, {})
    return imported_events if isinstance(imported_events, dict) else {}


def save_imported_events(file_path, imported_events):
    """Persist imported event records atomically."""
    save_json(file_path, imported_events)


def get_imported_event_key(source, event_id):
    """Return the stable storage key for one provider event."""
    return f"{str(source).strip().lower()}:{str(event_id)}"


def sync_imported_events(
    imported_events,
    events,
    source,
    now=None,
    lookahead_days=None,
):
    """Merge provider events and reconcile absent events in the feed window."""
    records = imported_events if isinstance(imported_events, dict) else {}
    observed_at = now or datetime.now(timezone.utc)
    if observed_at.tzinfo is None:
        observed_at = observed_at.replace(tzinfo=timezone.utc)

    source_name = str(source).strip().lower()
    seen_keys = set()
    for event in events or []:
        event_id = event.get("id")
        if not event_id:
            continue
        key = get_imported_event_key(source_name, event_id)
        seen_keys.add(key)
        previous = records.get(key, {})
        status = str(event.get("status", "confirmed")).strip().lower()
        records[key] = {
            "source": source_name,
            "source_id": str(event_id),
            "summary": event.get("summary", "Evento"),
            "description": event.get("description", ""),
            "location": event.get("location", ""),
            "htmlLink": event.get("htmlLink", ""),
            "start": event.get("start", {}),
            "end": event.get("end", {}),
            "status": status,
            "created": event.get("created"),
            "updated": event.get("updated"),
            "last_seen_at": observed_at.isoformat(),
            "flyer_file_id": previous.get("flyer_file_id"),
            "flyer_assigned_at": previous.get("flyer_assigned_at"),
        }

    if lookahead_days is not None:
        cutoff = observed_at + timedelta(days=lookahead_days)
        for key, record in records.items():
            if not isinstance(record, dict):
                continue
            if record.get("source") != source_name or key in seen_keys:
                continue
            event_time = _event_start(record)
            if event_time is None:
                continue
            event_time = event_time.astimezone(timezone.utc)
            window_start = observed_at.astimezone(timezone.utc)
            if window_start <= event_time <= cutoff.astimezone(timezone.utc):
                record["status"] = "removed"
                record["removed_at"] = observed_at.isoformat()
    return records


def assign_imported_event_flyer(imported_events, event_key, file_id, assigned_at=None):
    """Assign a Telegram file ID to an imported event and return its record."""
    if event_key not in imported_events:
        return None
    assigned_time = assigned_at or datetime.now(timezone.utc)
    if assigned_time.tzinfo is None:
        assigned_time = assigned_time.replace(tzinfo=timezone.utc)
    imported_events[event_key]["flyer_file_id"] = str(file_id)
    imported_events[event_key]["flyer_assigned_at"] = assigned_time.isoformat()
    return imported_events[event_key]


def _event_start(event_record):
    """Return an imported event start as an aware datetime when possible."""
    start = event_record.get("start", {})
    start_value = start.get("dateTime", start.get("date"))
    if not start_value:
        return None
    try:
        event_time = datetime.fromisoformat(str(start_value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if event_time.tzinfo is None:
        event_time = event_time.replace(tzinfo=timezone.utc)
    return event_time


def get_events_missing_flyers(imported_events, now=None):
    """Return future active imported events without assigned flyers."""
    current_time = now or datetime.now(timezone.utc)
    if current_time.tzinfo is None:
        current_time = current_time.replace(tzinfo=timezone.utc)

    missing = []
    for event_key, event in (imported_events or {}).items():
        if not isinstance(event, dict) or event.get("status") in {
            "cancelled",
            "canceled",
            "removed",
        }:
            continue
        if event.get("flyer_file_id"):
            continue
        event_time = _event_start(event)
        if event_time is None or event_time < current_time:
            continue
        missing.append((event_time, event_key, event))
    return [
        {"key": event_key, **event}
        for _, event_key, event in sorted(missing, key=lambda item: item[0])
    ]
