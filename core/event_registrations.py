"""Persistence helpers for users registered for individual events."""

from datetime import datetime, timezone

from tools.local.data_manager import load_json, save_json


def load_event_registrations(file_path):
    """Load event registrations as an event ID to user ID mapping."""
    raw_registrations = load_json(file_path, {})
    if not isinstance(raw_registrations, dict):
        return {}

    registrations = {}
    for event_id, user_ids in raw_registrations.items():
        if not isinstance(user_ids, list):
            continue
        registrations[str(event_id)] = sorted(
            {str(user_id) for user_id in user_ids}
        )
    return registrations


def save_event_registrations(file_path, registrations):
    """Persist event registrations atomically."""
    save_json(file_path, registrations)


def get_registered_users(registrations, event_id):
    """Return user IDs registered for one event."""
    return set(registrations.get(str(event_id), []))


def register_user(registrations, event_id, user_id):
    """Register a user for an event and report whether it was newly added."""
    event_key = str(event_id)
    user_key = str(user_id)
    registered_users = set(registrations.get(event_key, []))
    if user_key in registered_users:
        return False

    registered_users.add(user_key)
    registrations[event_key] = sorted(registered_users)
    return True


def unregister_user(registrations, event_id, user_id):
    """Remove a user from an event and report whether a record was removed."""
    event_key = str(event_id)
    user_key = str(user_id)
    registered_users = set(registrations.get(event_key, []))
    if user_key not in registered_users:
        return False

    registered_users.remove(user_key)
    if registered_users:
        registrations[event_key] = sorted(registered_users)
    else:
        registrations.pop(event_key, None)
    return True


def cleanup_expired_event_registrations(registrations, promos, now=None):
    """Remove registrations for manual events that have already started."""
    current_time = now or datetime.now(timezone.utc)
    if current_time.tzinfo is None:
        current_time = current_time.replace(tzinfo=timezone.utc)

    expired_event_ids = set()
    for promo_id, promo in promos.items() if isinstance(promos, dict) else []:
        if not isinstance(promo, dict) or not promo.get("event_start"):
            continue
        try:
            event_time = datetime.fromisoformat(
                str(promo["event_start"]).replace("Z", "+00:00")
            )
            if event_time.tzinfo is None:
                event_time = event_time.replace(tzinfo=timezone.utc)
        except (TypeError, ValueError):
            continue
        if event_time <= current_time:
            expired_event_ids.add(str(promo.get("event_id", promo_id)))

    removed = 0
    for event_id in expired_event_ids:
        if event_id in registrations:
            registrations.pop(event_id)
            removed += 1
    return removed
