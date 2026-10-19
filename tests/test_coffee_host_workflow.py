import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

from bot.handlers.admin import coffee_host
from tools.local.data_manager import load_json


def make_update(text, user_id=42):
    """Build a minimal update for the coffee-host command."""
    message = SimpleNamespace(text=text, reply_text=AsyncMock())
    return SimpleNamespace(
        effective_user=SimpleNamespace(id=user_id),
        message=message,
    )


def make_config(tmp_path):
    """Build the coffee-host command configuration."""
    return {
        "COFFEE_HOSTS_FILE": str(tmp_path / "coffee_hosts.json"),
        "COFFEE_HOST_SCHEDULE_FILE": str(tmp_path / "coffee_host_schedule.json"),
        "COFFEE_DEFAULT_START_TIME": "11:00",
        "COFFEE_DEFAULT_VENUE": "Default Café",
    }


def test_coffee_host_adds_and_persists_host(tmp_path):
    """The admin can add a host through Telegram and receive a generated ID."""
    config = make_config(tmp_path)
    state = {
        "coffee_hosts": {
            "host-001": {"name": "Existing Host", "telegram_id": 111, "active": True}
        },
        "coffee_host_schedule": {},
    }
    update = make_update("/coffeehost add | New Host | 222")

    asyncio.run(coffee_host(update, SimpleNamespace(), 42, state, config))

    assert state["coffee_hosts"]["host-002"] == {
        "name": "New Host",
        "telegram_id": 222,
        "active": True,
    }
    assert load_json(config["COFFEE_HOSTS_FILE"], {}) == state["coffee_hosts"]
    assert "host-002" in update.message.reply_text.await_args.args[0]


def test_coffee_host_rejects_duplicate_or_invalid_telegram_ids(tmp_path):
    """Duplicate and invalid Telegram IDs are rejected without changing the roster."""
    config = make_config(tmp_path)
    state = {
        "coffee_hosts": {
            "host-001": {"name": "Existing Host", "telegram_id": 111, "active": True}
        },
        "coffee_host_schedule": {},
    }

    duplicate_update = make_update("/coffeehost add | Duplicate | 111")
    asyncio.run(coffee_host(duplicate_update, SimpleNamespace(), 42, state, config))
    assert list(state["coffee_hosts"]) == ["host-001"]
    assert "ya está registrado" in duplicate_update.message.reply_text.await_args.args[0]

    invalid_update = make_update("/coffeehost add | Invalid | not-a-number")
    asyncio.run(coffee_host(invalid_update, SimpleNamespace(), 42, state, config))
    assert list(state["coffee_hosts"]) == ["host-001"]
    assert "numérico" in invalid_update.message.reply_text.await_args.args[0]


def test_coffee_host_remove_deactivates_and_persists_host(tmp_path):
    """Removing a host deactivates it and preserves the roster record."""
    config = make_config(tmp_path)
    state = {
        "coffee_hosts": {
            "host-001": {"name": "Host", "telegram_id": 111, "active": True}
        },
        "coffee_host_schedule": {},
    }
    update = make_update("/coffeehost remove | host-001")

    asyncio.run(coffee_host(update, SimpleNamespace(), 42, state, config))

    assert state["coffee_hosts"]["host-001"]["active"] is False
    assert load_json(config["COFFEE_HOSTS_FILE"], {}) == state["coffee_hosts"]
    assert "desactivado" in update.message.reply_text.await_args.args[0]


def test_coffee_host_view_lists_active_and_inactive_hosts(tmp_path):
    """The roster view includes both active and inactive hosts."""
    state = {
        "coffee_hosts": {
            "host-001": {"name": "Active Host", "active": True},
            "host-002": {"name": "Inactive Host", "active": False},
        },
        "coffee_host_schedule": {},
    }
    update = make_update("/coffeehost")

    asyncio.run(coffee_host(update, SimpleNamespace(), 42, state, make_config(tmp_path)))

    message = update.message.reply_text.await_args.args[0]
    assert "Active Host" in message
    assert "Inactive Host" in message
    assert "Anfitriones activos" in message
    assert "Anfitriones inactivos" in message


def test_coffee_host_assigns_and_persists_active_hosts(tmp_path):
    """A valid Saturday assignment is stored with venue, time, and notes."""
    state = {
        "coffee_hosts": {
            "host-001": {"name": "Host One", "active": True},
            "host-002": {"name": "Host Two", "active": True},
        },
        "coffee_host_schedule": {},
    }
    update = make_update(
        "/coffeehost 2026-10-17 host-001,host-002 | Café Central | 11:00 | Bring coffee"
    )

    context = SimpleNamespace(
        bot=SimpleNamespace(send_message=AsyncMock())
    )
    asyncio.run(coffee_host(update, context, 42, state, make_config(tmp_path)))

    assignment = state["coffee_host_schedule"]["2026-10-17"]
    assert assignment["hosts"] == ["host-001", "host-002"]
    assert assignment["venue"] == "Café Central"
    assert assignment["start_time"] == "11:00"
    assert assignment["notes"] == "Bring coffee"
    assert load_json(make_config(tmp_path)["COFFEE_HOST_SCHEDULE_FILE"], {}) == state[
        "coffee_host_schedule"
    ]


def test_coffee_host_assignment_warns_when_notifications_are_disabled(tmp_path):
    """Assignment warns instead of silently enabling coffee notifications."""
    state = {
        "coffee_hosts": {
            "host-001": {
                "name": "Host One",
                "telegram_id": 111,
                "active": True,
            }
        },
        "coffee_host_schedule": {},
        "notification_preferences": {
            "111": {"events": True, "coffee": False, "payments": False},
        },
    }
    update = make_update("/coffeehost 2026-10-17 host-001 | Café Central | 11:00")

    asyncio.run(coffee_host(update, SimpleNamespace(), 42, state, make_config(tmp_path)))

    message = update.message.reply_text.await_args.args[0]
    assert "Notificaciones de café desactivadas" in message
    assert "Host One" in message


def test_coffee_host_rejects_non_saturday_dates(tmp_path):
    """Assignments for dates other than Saturday are rejected."""
    state = {"coffee_hosts": {"host-001": {"active": True}}, "coffee_host_schedule": {}}
    update = make_update("/coffeehost 2026-10-16 host-001 | Café | 11:00")

    asyncio.run(coffee_host(update, SimpleNamespace(), 42, state, make_config(tmp_path)))

    assert state["coffee_host_schedule"] == {}
    assert "sábado" in update.message.reply_text.await_args.args[0]


def test_coffee_host_rejects_missing_or_inactive_hosts(tmp_path):
    """Unknown and inactive host IDs cannot be assigned."""
    state = {
        "coffee_hosts": {"host-inactive": {"active": False}},
        "coffee_host_schedule": {},
    }
    update = make_update("/coffeehost 2026-10-17 host-missing,host-inactive | Café | 11:00")

    asyncio.run(coffee_host(update, SimpleNamespace(), 42, state, make_config(tmp_path)))

    assert state["coffee_host_schedule"] == {}
    assert "host-missing" in update.message.reply_text.await_args.args[0]
    assert "host-inactive" in update.message.reply_text.await_args.args[0]


def test_coffee_host_clear_removes_assignment(tmp_path):
    """The clear form removes and persists an existing assignment."""
    config = make_config(tmp_path)
    state = {
        "coffee_hosts": {},
        "coffee_host_schedule": {
            "2026-10-17": {"hosts": ["host-001"]},
        },
    }
    update = make_update("/coffeehost 2026-10-17 clear")

    asyncio.run(coffee_host(update, SimpleNamespace(), 42, state, config))

    assert state["coffee_host_schedule"] == {}
    assert load_json(config["COFFEE_HOST_SCHEDULE_FILE"], {"unexpected": True}) == {}


def test_coffee_host_ignores_non_admin_users(tmp_path):
    """Non-admin users cannot view or modify coffee-host assignments."""
    state = {
        "coffee_hosts": {"host-001": {"active": True}},
        "coffee_host_schedule": {},
    }
    update = make_update("/coffeehost 2026-10-17 host-001", user_id=99)

    asyncio.run(coffee_host(update, SimpleNamespace(), 42, state, make_config(tmp_path)))

    assert state["coffee_host_schedule"] == {}
    update.message.reply_text.assert_not_awaited()
