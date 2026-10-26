from tools.local.data_manager import load_json
from core.group_notification_routing import (
    build_default_group_notification_categories,
    get_group_ids_for_category,
    load_group_notification_categories,
    normalize_group_notification_categories,
    save_group_notification_categories,
)


def test_default_categories_migrate_existing_groups_and_payment_group():
    """Existing broadcast groups and the payment group receive compatible defaults."""
    categories = build_default_group_notification_categories(
        {-100, "-200", "invalid", 0},
        members_group_id=-300,
    )

    assert categories == {
        "events": [-200, -100],
        "payments": [-300],
        "coworking": [-200, -100],
    }


def test_load_initializes_and_persists_default_categories(tmp_path):
    """The first load creates the runtime category file from current configuration."""
    file_path = tmp_path / "group_notification_categories.json"

    categories = load_group_notification_categories(
        str(file_path),
        {-100, -200},
        members_group_id=-300,
    )

    assert categories["events"] == [-200, -100]
    assert categories["payments"] == [-300]
    assert categories["coworking"] == [-200, -100]
    assert load_json(str(file_path), {}) == categories


def test_existing_categories_are_preserved_and_unknown_values_are_removed(tmp_path):
    """A configured file is authoritative and is normalized without migration resets."""
    file_path = tmp_path / "group_notification_categories.json"
    save_group_notification_categories(
        str(file_path),
        {
            "events": [-100, "-100", "bad"],
            "payments": [-300],
            "unknown": [-400],
        },
    )

    categories = load_group_notification_categories(
        str(file_path),
        {-500},
        members_group_id=-600,
    )

    assert categories == {
        "events": [-100],
        "payments": [-300],
        "coworking": [],
    }


def test_get_group_ids_for_category_rejects_unknown_category():
    """Unknown categories never produce broadcast targets."""
    categories = {"events": [-100], "payments": [-200], "coworking": []}

    assert get_group_ids_for_category(categories, "events") == {-100}
    assert get_group_ids_for_category(categories, "unknown") == set()


def test_normalize_categories_always_returns_known_categories():
    """Normalization provides stable keys for incomplete runtime data."""
    assert normalize_group_notification_categories({"payments": ["-100"]}) == {
        "events": [],
        "payments": [-100],
        "coworking": [],
    }
