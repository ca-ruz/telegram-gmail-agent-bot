"""Persistence helpers for per-user notification preferences."""

from tools.local.data_manager import load_json, save_json

NOTIFICATION_CATEGORIES = ("events", "coffee", "payments", "coworking")


def build_default_preferences(events=True):
    """Build default notification preferences with every category enabled."""
    return {
        "events": True,
        "coffee": True,
        "payments": True,
        "coworking": True,
    }


def _normalize_preferences(raw_preferences, events_default=True):
    """Normalize one stored preference record without enabling new categories."""
    defaults = build_default_preferences(events=events_default)
    if not isinstance(raw_preferences, dict):
        return defaults

    normalized = {}
    for category in NOTIFICATION_CATEGORIES:
        value = raw_preferences.get(category, defaults[category])
        normalized[category] = value if isinstance(value, bool) else defaults[category]
    return normalized


def load_notification_preferences(file_path, event_subscribers):
    """Load preferences and migrate existing event subscribers into the new format."""
    raw_preferences = load_json(file_path, {})
    if not isinstance(raw_preferences, dict):
        raw_preferences = {}

    preferences = {}
    for user_id, raw_user_preferences in raw_preferences.items():
        preferences[str(user_id)] = _normalize_preferences(
            raw_user_preferences,
            events_default=False,
        )

    for user_id in event_subscribers:
        key = str(user_id)
        if key not in preferences:
            preferences[key] = build_default_preferences(events=True)

    if preferences != raw_preferences:
        save_json(file_path, preferences)

    return preferences


def save_notification_preferences(file_path, preferences):
    """Persist per-user notification preferences atomically."""
    save_json(file_path, preferences)


def ensure_user_preferences(preferences, user_id, events_default=False):
    """Return a user's preferences, creating defaults when needed."""
    key = str(user_id)
    if key not in preferences:
        preferences[key] = build_default_preferences(events=events_default)
    return preferences[key]


def is_notification_enabled(preferences, user_id, category):
    """Return whether a user may receive one notification category."""
    if preferences is None:
        return True

    user_preferences = preferences.get(str(user_id), {})
    default = category in NOTIFICATION_CATEGORIES
    return bool(user_preferences.get(category, default))


def filter_users_by_category(user_ids, preferences, category):
    """Return users who have enabled the requested notification category."""
    return {
        user_id
        for user_id in user_ids
        if is_notification_enabled(preferences, user_id, category)
    }

