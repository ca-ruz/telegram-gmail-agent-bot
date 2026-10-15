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
        "COFFEE_HOST_SCHEDULE_FILE": str(tmp_path / "coffee_host_schedule.json"),
        "COFFEE_DEFAULT_START_TIME": "11:00",
        "COFFEE_DEFAULT_VENUE": "Default Café",
    }


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

    asyncio.run(coffee_host(update, SimpleNamespace(), 42, state, make_config(tmp_path)))

    assignment = state["coffee_host_schedule"]["2026-10-17"]
    assert assignment["hosts"] == ["host-001", "host-002"]
    assert assignment["venue"] == "Café Central"
    assert assignment["start_time"] == "11:00"
    assert assignment["notes"] == "Bring coffee"
    assert load_json(make_config(tmp_path)["COFFEE_HOST_SCHEDULE_FILE"], {}) == state[
        "coffee_host_schedule"
    ]


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
