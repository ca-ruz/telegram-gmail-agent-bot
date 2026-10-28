import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

from bot.handlers.admin import (
    format_status_detail,
    format_imported_event_label,
    format_prompt_history_entry,
    handle_status_detail,
    manage_members,
    save_prompt_history,
    status_overview_keyboard,
    status,
)
from core.admin_status import build_admin_status_snapshot
from core.membership_payments import build_member, current_billing_period, next_billing_period
from tools.local.data_manager import load_json, save_json


def test_status_reports_disabled_services_and_placeholder_openai_key():
    """Admin status accurately reports runtime service configuration."""
    reply_text = AsyncMock()
    update = SimpleNamespace(
        effective_user=SimpleNamespace(id=42),
        message=SimpleNamespace(reply_text=reply_text),
    )
    state = {"subscribers": set(), "groups": set(), "pending_promos": {}}
    config = {
        "OPENAI_API_KEY": "your_openai_api_key_here",
        "ENABLE_CALENDAR_POLLING": False,
        "ENABLE_COFFEE_HOST_REMINDERS": True,
        "ENABLE_MANUAL_PROMO_REMINDERS": False,
        "CHECK_INTERVAL_MINUTES": 30,
    }

    asyncio.run(status(update, SimpleNamespace(), 42, state, config))

    message = reply_text.await_args.args[0]
    assert "Missing or placeholder key" in message
    assert "Calendar polling:</b> ⛔ Disabled" in message
    assert "Coffee reminders:</b> ✅ Enabled" in message
    assert "Manual promo reminders:</b> ⛔ Disabled" in message
    assert "Check Interval:</b> 30 min" in message


def test_admin_status_snapshot_aggregates_identity_roles_and_preferences():
    """Status data links metadata, members, hosts, subscribers, and categories."""
    state = {
        "subscribers": {101},
        "members": {
            "member-001": {"name": "Ada", "telegram_id": 101, "active": True},
            "member-002": {"name": "Inactive", "telegram_id": 202, "active": False},
        },
        "coffee_hosts": {
            "host-001": {"name": "Ada", "telegram_id": 101, "active": True},
        },
        "groups": {-100123},
        "group_notification_categories": {
            "events": [-100123],
            "payments": [],
            "coworking": [-100123],
        },
        "telegram_metadata": {
            "users": {
                "101": {"display_name": "Ada Lovelace", "username": "ada"},
            },
            "groups": {
                "-100123": {"display_name": "Community", "username": "community"},
            },
        },
        "notification_preferences": {
            "101": {"events": True, "coffee": False, "payments": True, "coworking": False},
        },
    }

    snapshot = build_admin_status_snapshot(state)

    assert snapshot["counts"] == {
        "known_users": 2,
        "subscribers": 1,
        "members": 2,
        "active_members": 1,
        "coffee_hosts": 1,
        "active_coffee_hosts": 1,
        "broadcast_groups": 1,
    }
    ada = next(user for user in snapshot["users"] if user["telegram_id"] == 101)
    assert ada["display_name"] == "Ada Lovelace"
    assert ada["username"] == "ada"
    assert ada["member_ids"] == ["member-001"]
    assert ada["coffee_host_ids"] == ["host-001"]
    assert ada["notification_preferences"]["coffee"] is False
    assert snapshot["groups"][0]["categories"] == ["events", "coworking"]


def test_status_overview_includes_detail_buttons():
    """The status overview exposes navigation for each read-only detail view."""
    callbacks = [
        button.callback_data
        for row in status_overview_keyboard().inline_keyboard
        for button in row
    ]

    assert callbacks == [
        "status_detail:members",
        "status_detail:coffee_hosts",
        "status_detail:groups",
        "status_detail:subscriptions",
    ]


def test_status_detail_callback_is_admin_only_and_renders_subscription_data():
    """Status detail callbacks protect data and render notification categories."""
    query = SimpleNamespace(
        from_user=SimpleNamespace(id=42),
        data="status_detail:subscriptions",
        answer=AsyncMock(),
        edit_message_text=AsyncMock(),
    )
    update = SimpleNamespace(callback_query=query)
    state = {
        "subscribers": {101},
        "telegram_metadata": {
            "users": {"101": {"display_name": "Ada", "username": "ada"}}
        },
        "notification_preferences": {
            "101": {"events": True, "coffee": False, "payments": True, "coworking": False}
        },
    }

    asyncio.run(handle_status_detail(update, SimpleNamespace(), 42, state, {}))

    rendered = query.edit_message_text.await_args.args[0]
    assert "Ada (@ada)" in rendered
    assert "101" in rendered
    assert "events, payments" in rendered
    query.answer.assert_awaited_once_with()

    unauthorized_query = SimpleNamespace(
        from_user=SimpleNamespace(id=99),
        data="status_detail:subscriptions",
        answer=AsyncMock(),
        edit_message_text=AsyncMock(),
    )
    asyncio.run(
        handle_status_detail(
            SimpleNamespace(callback_query=unauthorized_query),
            SimpleNamespace(),
            42,
            state,
            {},
        )
    )

    unauthorized_query.answer.assert_awaited_once()
    unauthorized_query.edit_message_text.assert_not_awaited()


def test_adding_member_synchronizes_current_and_next_payment_periods(tmp_path):
    """Adding a member makes them immediately available for payment review."""
    reply_text = AsyncMock()
    update = SimpleNamespace(
        effective_user=SimpleNamespace(id=42),
        message=SimpleNamespace(
            text="/members add | David R | 549905746",
            reply_text=reply_text,
        ),
    )
    current_period = current_billing_period()
    next_period = next_billing_period()
    state = {
        "members": {"member-001": build_member("Ada", 101)},
        "payment_state": {
            "version": 2,
            "periods": {
                current_period: {
                    "members": {
                        "member-001": {
                            "status": "paid",
                            "paid_at": "2026-10-05T15:00:00+00:00",
                            "reminders_sent": ["2026-10:first_day:member-001"],
                        }
                    }
                }
            },
        },
    }
    config = {
        "MEMBERS_FILE": str(tmp_path / "members.json"),
        "PAYMENT_STATE_FILE": str(tmp_path / "payment_state.json"),
    }

    asyncio.run(
        manage_members(
            update,
            SimpleNamespace(),
            primary_admin_id=42,
            payment_admin_id=0,
            state=state,
            config=config,
        )
    )

    assert "member-002" in state["members"]
    current_members = state["payment_state"]["periods"][current_period]["members"]
    assert current_members["member-001"]["status"] == "paid"
    assert current_members["member-001"]["paid_at"] == "2026-10-05T15:00:00+00:00"
    assert current_members["member-002"]["status"] == "unpaid"
    assert set(state["payment_state"]["periods"][next_period]["members"]) == {
        "member-001",
        "member-002",
    }
    persisted = load_json(str(tmp_path / "payment_state.json"), {})
    assert "member-002" in persisted["periods"][current_period]["members"]


def test_imported_event_label_includes_day_and_month():
    """Flyer assignment buttons distinguish events with the same title."""
    label = format_imported_event_label({
        "summary": "Bitcoin Coffee",
        "start": {"dateTime": "2026-10-24T11:00:00-06:00"},
    })

    assert label == "📸 Bitcoin Coffee · 24/10"


def test_status_reports_payment_deployment_configuration():
    """Admin status exposes safe payment scheduler configuration details."""
    reply_text = AsyncMock()
    update = SimpleNamespace(
        effective_user=SimpleNamespace(id=42),
        message=SimpleNamespace(reply_text=reply_text),
    )
    state = {
        "subscribers": set(),
        "groups": set(),
        "pending_promos": {},
        "members": {"member-001": {"name": "Ada", "active": True}},
        "payment_state": {"period": "2026-11", "members": {}},
    }
    config = {
        "OPENAI_API_KEY": "",
        "ENABLE_CALENDAR_POLLING": False,
        "ENABLE_COFFEE_HOST_REMINDERS": False,
        "ENABLE_MANUAL_PROMO_REMINDERS": False,
        "ENABLE_PAYMENT_REMINDERS": True,
        "MEMBERS_GROUP_ID": -100123,
        "PAYMENT_REMINDER_HOUR": 9,
        "CHECK_INTERVAL_MINUTES": 30,
    }

    asyncio.run(status(update, SimpleNamespace(), 42, state, config))

    message = reply_text.await_args.args[0]
    assert "Payment reminders:</b> ✅ Enabled" in message
    assert "Payment group:</b> ✅ Configured" in message
    assert "Payment members:</b> 1" in message
    assert "Payment period:</b> 2026-11" in message
    assert "Payment reminder hour:</b> 09:00" in message


def test_status_reports_current_period_for_multi_period_payment_state():
    """Admin status reports the current period after payment-state migration."""
    reply_text = AsyncMock()
    update = SimpleNamespace(
        effective_user=SimpleNamespace(id=42),
        message=SimpleNamespace(reply_text=reply_text),
    )
    state = {
        "subscribers": set(),
        "groups": set(),
        "pending_promos": {},
        "members": {},
        "payment_state": {"version": 2, "periods": {"2026-10": {}, "2026-11": {}}},
    }
    config = {"CHECK_INTERVAL_MINUTES": 30}

    asyncio.run(status(update, SimpleNamespace(), 42, state, config))

    message = reply_text.await_args.args[0]
    assert "Payment period:</b> 2026-10" in message


def test_status_reports_coworking_deployment_configuration():
    """Admin status exposes safe coworking scheduler configuration details."""
    reply_text = AsyncMock()
    update = SimpleNamespace(
        effective_user=SimpleNamespace(id=42),
        message=SimpleNamespace(reply_text=reply_text),
    )
    state = {
        "subscribers": set(),
        "groups": set(),
        "pending_promos": {},
        "coworking": {"last_sent_week": "2026-W43"},
    }
    config = {
        "OPENAI_API_KEY": "",
        "ENABLE_CALENDAR_POLLING": False,
        "ENABLE_COFFEE_HOST_REMINDERS": False,
        "ENABLE_MANUAL_PROMO_REMINDERS": False,
        "ENABLE_PAYMENT_REMINDERS": False,
        "ENABLE_COWORKING_NOTIFICATIONS": True,
        "COWORKING_WEEKDAY": 2,
        "COWORKING_HOUR": 10,
        "COWORKING_QUIET_WINDOW_HOURS": 24,
        "CHECK_INTERVAL_MINUTES": 30,
    }

    asyncio.run(status(update, SimpleNamespace(), 42, state, config))

    message = reply_text.await_args.args[0]
    assert "Coworking invitations:</b> ✅ Enabled" in message
    assert "Coworking slot:</b> weekday 2, 10:00" in message
    assert "Coworking quiet window:</b> 24 hours" in message
    assert "Last coworking send:</b> 2026-W43" in message


def test_format_prompt_history_entry_escapes_html():
    """Prompt history output is safe for Telegram HTML parse mode."""
    entry = {
        "event": "BTC <GDL>",
        "image_model": "gpt-image-1-mini",
        "image_quality": "medium",
        "created_at": "2026-05-30T00:00:00+00:00",
        "image_prompt": "Use <strong>orange</strong> & black.",
    }

    formatted = format_prompt_history_entry(entry)

    assert "BTC &lt;GDL&gt;" in formatted
    assert "Use &lt;strong&gt;orange&lt;/strong&gt; &amp; black." in formatted


def test_format_prompt_history_entry_truncates_prompt():
    """Long prompt history output is truncated for compact admin messages."""
    entry = {
        "event": "Long prompt",
        "image_model": "gpt-image-1-mini",
        "image_quality": "medium",
        "created_at": "2026-05-30T00:00:00+00:00",
        "image_prompt": "x" * 20,
    }

    formatted = format_prompt_history_entry(entry, prompt_limit=10)

    assert "xxxxxxxxxx..." in formatted
    assert "x" * 20 not in formatted


def test_save_prompt_history_appends_record(tmp_path):
    """Generated image prompts are appended to prompt history."""
    file_path = tmp_path / "prompt_history.json"
    config = {
        "PROMPT_HISTORY_FILE": str(file_path),
        "OPENAI_IMAGE_MODEL": "gpt-image-1-mini",
        "OPENAI_IMAGE_QUALITY": "medium",
    }
    event = {
        "summary": "Stand Up",
        "start": {"dateTime": "2026-06-05T10:20:00-06:00"},
    }

    save_prompt_history(config, event, "Create a flyer.")

    history = load_json(str(file_path), [])
    assert len(history) == 1
    assert history[0]["event"] == "Stand Up"
    assert history[0]["event_start"] == event["start"]
    assert history[0]["image_model"] == "gpt-image-1-mini"
    assert history[0]["image_quality"] == "medium"
    assert history[0]["image_prompt"] == "Create a flyer."


def test_save_prompt_history_keeps_last_50_records(tmp_path):
    """Prompt history is capped to the most recent 50 entries."""
    file_path = tmp_path / "prompt_history.json"
    config = {
        "PROMPT_HISTORY_FILE": str(file_path),
        "OPENAI_IMAGE_MODEL": "gpt-image-1-mini",
        "OPENAI_IMAGE_QUALITY": "medium",
    }
    existing = [
        {"event": f"Event {index}", "image_prompt": f"Prompt {index}"}
        for index in range(55)
    ]
    save_json(str(file_path), existing)

    save_prompt_history(config, {"summary": "Newest", "start": {}}, "Newest prompt")

    history = load_json(str(file_path), [])
    assert len(history) == 50
    assert history[0]["event"] == "Event 6"
    assert history[-1]["event"] == "Newest"
