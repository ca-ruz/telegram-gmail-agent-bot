import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

from bot.handlers.admin import list_groups, list_users
from core.telegram_metadata import (
    build_group_metadata,
    build_user_metadata,
    load_telegram_metadata,
    record_group_metadata,
    record_user_metadata,
    save_telegram_metadata,
)


def test_user_metadata_normalizes_identity_and_display_name():
    """User metadata keeps the numeric ID and builds a readable name."""
    record = build_user_metadata(
        "101",
        first_name=" Ada ",
        last_name="Lovelace ",
        username="@ada",
    )

    assert record == {
        "id": 101,
        "display_name": "Ada Lovelace",
        "first_name": "Ada",
        "last_name": "Lovelace",
        "username": "ada",
    }


def test_group_metadata_accepts_negative_chat_ids():
    """Group metadata preserves negative Telegram chat IDs as integers."""
    record = build_group_metadata(
        "-100123",
        title="Community Group",
        username="@community",
        chat_type="supergroup",
    )

    assert record == {
        "id": -100123,
        "display_name": "Community Group",
        "title": "Community Group",
        "username": "community",
        "type": "supergroup",
    }


def test_metadata_records_and_loads_normalized_records(tmp_path):
    """Metadata records round-trip through the runtime JSON file."""
    metadata = {"users": {}, "groups": {}}
    record_user_metadata(
        metadata,
        SimpleNamespace(
            id=101,
            first_name="Ada",
            last_name="",
            username="ada",
        ),
    )
    record_group_metadata(
        metadata,
        SimpleNamespace(
            id=-100123,
            title="Community Group",
            username="community",
            type="supergroup",
        ),
    )
    file_path = tmp_path / "telegram_metadata.json"

    save_telegram_metadata(str(file_path), metadata)

    loaded = load_telegram_metadata(str(file_path))
    assert loaded == metadata


def test_metadata_loader_discards_malformed_records(tmp_path):
    """Malformed metadata entries do not poison the normalized store."""
    file_path = tmp_path / "telegram_metadata.json"
    file_path.write_text(
        '{"users": {"bad": [], "101": {"id": "101", "username": "ada"}}, '
        '"groups": {"bad": {"id": "not-an-id"}}}',
        encoding="utf-8",
    )

    loaded = load_telegram_metadata(str(file_path))

    assert list(loaded["users"]) == ["101"]
    assert loaded["users"]["101"]["display_name"] == "@ada"
    assert loaded["groups"] == {}


def test_list_groups_displays_names_usernames_and_ids():
    """The administrator group view shows readable metadata and IDs."""
    reply_text = AsyncMock()
    update = SimpleNamespace(
        effective_user=SimpleNamespace(id=42),
        message=SimpleNamespace(reply_text=reply_text),
    )
    state = {
        "groups": {-100123, -100456},
        "telegram_metadata": {
            "groups": {
                "-100123": {
                    "display_name": "Community Group",
                    "username": "community",
                }
            }
        },
    }

    asyncio.run(list_groups(update, SimpleNamespace(), 42, state))

    message = reply_text.await_args.kwargs["parse_mode"]
    assert message == "HTML"
    rendered = reply_text.await_args.args[0]
    assert "Community Group" in rendered
    assert "@community" in rendered
    assert "-100123" in rendered
    assert "Grupo sin nombre" in rendered
    assert "-100456" in rendered


def test_list_users_displays_metadata_and_subscription_status():
    """The administrator user view identifies subscribers and known users."""
    reply_text = AsyncMock()
    update = SimpleNamespace(
        effective_user=SimpleNamespace(id=42),
        message=SimpleNamespace(reply_text=reply_text),
    )
    state = {
        "subscribers": {101, 202},
        "telegram_metadata": {
            "users": {
                "101": {"display_name": "Ada", "username": "ada"},
                "202": {"display_name": "Lin", "username": "lin"},
                "303": {"display_name": "Sam", "username": "sam"},
            }
        },
    }

    asyncio.run(list_users(update, SimpleNamespace(), 42, state))

    rendered = reply_text.await_args.args[0]
    assert "Ada" in rendered
    assert "@ada" in rendered
    assert "101" in rendered
    assert "suscrito" in rendered
    assert "Sam" in rendered
    assert "no suscrito" in rendered
