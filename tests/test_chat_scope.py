import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

from bot.handlers.admin import add_group, remove_group
from core.chat_scope import get_chat_type, is_group_chat, is_private_chat
from tools.local.data_manager import load_json


def test_chat_scope_classifies_private_and_group_chats():
    """Chat-scope helpers distinguish private, group, and unknown chats."""
    private_update = SimpleNamespace(
        effective_chat=SimpleNamespace(type="private"),
    )
    group_chat = SimpleNamespace(type="supergroup")
    unknown_chat = SimpleNamespace(type=None)

    assert get_chat_type(private_update) == "private"
    assert is_private_chat(private_update)
    assert is_group_chat(group_chat)
    assert not is_private_chat(group_chat)
    assert get_chat_type(unknown_chat) == ""


def test_primary_admin_group_management_remains_available(tmp_path):
    """The primary administrator can add and remove a broadcast group."""
    group_id = -100123
    config = {"GROUPS_FILE": str(tmp_path / "groups.json")}
    state = {"groups": set()}
    context = SimpleNamespace()
    update = SimpleNamespace(
        effective_user=SimpleNamespace(id=42),
        effective_chat=SimpleNamespace(
            id=group_id,
            type="group",
            title="Community group",
        ),
        message=SimpleNamespace(reply_text=AsyncMock()),
    )

    asyncio.run(add_group(update, context, admin_id=42, state=state, config=config))

    assert state["groups"] == {group_id}
    assert group_id in load_json(config["GROUPS_FILE"], [])

    asyncio.run(remove_group(update, context, admin_id=42, state=state, config=config))

    assert state["groups"] == set()
    assert group_id not in load_json(config["GROUPS_FILE"], [])


def test_group_management_updates_event_and_coworking_categories(tmp_path):
    """Adding and removing a group keeps category routing aligned with group management."""
    group_id = -100123
    config = {
        "GROUPS_FILE": str(tmp_path / "groups.json"),
        "GROUP_NOTIFICATION_CATEGORIES_FILE": str(
            tmp_path / "group_notification_categories.json"
        ),
        "TELEGRAM_METADATA_FILE": str(tmp_path / "telegram_metadata.json"),
    }
    state = {
        "groups": set(),
        "group_notification_categories": {
            "events": [],
            "payments": [],
            "coworking": [],
        },
        "telegram_metadata": {"users": {}, "groups": {}},
    }
    context = SimpleNamespace()
    update = SimpleNamespace(
        effective_user=SimpleNamespace(id=42),
        effective_chat=SimpleNamespace(
            id=group_id,
            type="supergroup",
            title="Community group",
            username="community_group",
        ),
        message=SimpleNamespace(reply_text=AsyncMock()),
    )

    asyncio.run(add_group(update, context, admin_id=42, state=state, config=config))

    assert state["group_notification_categories"] == {
        "events": [group_id],
        "payments": [],
        "coworking": [group_id],
    }

    asyncio.run(remove_group(update, context, admin_id=42, state=state, config=config))

    assert state["group_notification_categories"] == {
        "events": [],
        "payments": [],
        "coworking": [],
    }
