from core.notification_preferences import (
    filter_users_by_category,
    load_notification_preferences,
)
from tools.local.data_manager import load_json


def test_load_preferences_migrates_existing_event_subscribers(tmp_path):
    """Existing subscribers receive all categories when no preferences exist."""
    file_path = tmp_path / "notification_preferences.json"

    preferences = load_notification_preferences(str(file_path), {101, 202})

    assert preferences == {
        "101": {"events": True, "coffee": True, "payments": True},
        "202": {"events": True, "coffee": True, "payments": True},
    }
    assert load_json(str(file_path), {}) == preferences


def test_load_preferences_preserves_existing_opt_outs_and_adds_missing_categories(tmp_path):
    """Stored opt-outs remain disabled while newly introduced categories get defaults."""
    file_path = tmp_path / "notification_preferences.json"
    file_path.write_text(
        '{"101": {"events": false, "coffee": true}}',
        encoding="utf-8",
    )

    preferences = load_notification_preferences(str(file_path), {101})

    assert preferences["101"] == {
        "events": False,
        "coffee": True,
        "payments": True,
    }


def test_filter_users_by_category_returns_only_enabled_users():
    """Delivery filters only users who enabled the requested category."""
    preferences = {
        "101": {"events": True, "coffee": False, "payments": False},
        "202": {"events": False, "coffee": True, "payments": False},
    }

    assert filter_users_by_category({101, 202}, preferences, "events") == {101}
    assert filter_users_by_category({101, 202}, preferences, "coffee") == {202}

