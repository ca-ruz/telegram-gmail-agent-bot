"""Persistence helpers for users registered for individual events."""

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
