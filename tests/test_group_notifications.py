import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

from bot.handlers.admin import group_notifications
from core.group_notification_routing import (
    add_group_to_category,
    remove_group_from_category,
)
from tools.local.data_manager import load_json


def make_update(user_id=42):
    """Build an administrator command update double."""
    return SimpleNamespace(
        effective_user=SimpleNamespace(id=user_id),
        message=SimpleNamespace(reply_text=AsyncMock()),
    )


def test_category_helpers_add_and_remove_group_ids():
    """Category helpers normalize IDs and report whether assignments changed."""
    categories, changed = add_group_to_category(
        {"events": ["-100"], "payments": [], "coworking": []},
        "payments",
        "-200",
    )

    assert changed is True
    assert categories["payments"] == [-200]

    categories, changed = remove_group_from_category(categories, "payments", -200)

    assert changed is True
    assert categories["payments"] == []


def test_group_notifications_lists_readable_category_targets():
    """The administrator list includes category names, metadata, and IDs."""
    update = make_update()
    context = SimpleNamespace(args=[])
    state = {
        "group_notification_categories": {
            "events": [-100123],
            "payments": [-100456],
            "coworking": [],
        },
        "telegram_metadata": {
            "groups": {
                "-100123": {
                    "display_name": "Events Group",
                    "username": "events_group",
                },
            },
        },
    }
    config = {"GROUP_NOTIFICATION_CATEGORIES_FILE": "/tmp/unused.json"}

    asyncio.run(group_notifications(update, context, 42, state, config))

    rendered = update.message.reply_text.await_args.args[0]
    assert "Eventos" in rendered
    assert "Events Group" in rendered
    assert "@events_group" in rendered
    assert "-100123" in rendered
    assert "Pagos" in rendered
    assert "-100456" in rendered


def test_group_notifications_add_and_remove_persist_assignments(tmp_path):
    """Administrator category changes update memory and the runtime JSON file."""
    file_path = tmp_path / "group_notification_categories.json"
    state = {
        "group_notification_categories": {
            "events": [],
            "payments": [],
            "coworking": [],
        },
        "telegram_metadata": {"groups": {}},
    }
    config = {"GROUP_NOTIFICATION_CATEGORIES_FILE": str(file_path)}

    add_update = make_update()
    asyncio.run(
        group_notifications(
            add_update,
            SimpleNamespace(args=["add", "pagos", "-100456"]),
            42,
            state,
            config,
        )
    )
    assert state["group_notification_categories"]["payments"] == [-100456]
    assert load_json(str(file_path), {})["payments"] == [-100456]
    assert "agregado" in add_update.message.reply_text.await_args.args[0]

    remove_update = make_update()
    asyncio.run(
        group_notifications(
            remove_update,
            SimpleNamespace(args=["remove", "payments", "-100456"]),
            42,
            state,
            config,
        )
    )
    assert state["group_notification_categories"]["payments"] == []
    assert load_json(str(file_path), {})["payments"] == []
    assert "quitado" in remove_update.message.reply_text.await_args.args[0]


def test_group_notifications_rejects_non_administrator():
    """Non-administrators cannot view or change group notification categories."""
    update = make_update(user_id=99)

    asyncio.run(
        group_notifications(
            update,
            SimpleNamespace(args=["add", "events", "-100123"]),
            42,
            {"group_notification_categories": {}},
            {"GROUP_NOTIFICATION_CATEGORIES_FILE": "/tmp/unused.json"},
        )
    )

    update.message.reply_text.assert_not_awaited()
