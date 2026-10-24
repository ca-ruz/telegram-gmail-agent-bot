import asyncio
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from bot.handlers.admin import handle_payment_review
from core.membership_payments import (
    PAYMENT_ADMIN_ROLE,
    PRIMARY_ADMIN_ROLE,
    build_member,
    build_monthly_payment_state,
    can_manage_payments,
    check_payment_reminders,
    current_billing_period,
    get_payment_admin_role,
    get_payment_reminder_stage,
    get_unpaid_member_recipients,
    load_members,
    load_payment_state,
    next_member_id,
    PAYMENT_TIMEZONE,
    save_members,
    save_payment_state,
    send_payment_admin_followup,
    send_personal_payment_reminders,
)


def test_payment_roles_distinguish_primary_and_payment_administrators():
    """Primary and payment administrators receive their intended roles only."""
    assert get_payment_admin_role(10, 10, 20) == PRIMARY_ADMIN_ROLE
    assert get_payment_admin_role(20, 10, 20) == PAYMENT_ADMIN_ROLE
    assert get_payment_admin_role(30, 10, 20) is None
    assert can_manage_payments(20, 10, 20) is True
    assert can_manage_payments(30, 10, 20) is False


def test_member_roster_helpers_create_sequence_and_round_trip(tmp_path):
    """Member records use sequential IDs and persist through JSON storage."""
    members = {
        "member-001": build_member("Ada", 101),
        "member-004": build_member("Lin", 104),
    }

    assert next_member_id(members) == "member-005"
    file_path = tmp_path / "members.json"
    save_members(str(file_path), members)

    assert load_members(str(file_path)) == members


def test_monthly_payment_state_contains_active_members_only():
    """A new billing period starts active members as unpaid."""
    members = {
        "member-001": build_member("Ada", 101),
        "member-002": {**build_member("Lin", 102), "active": False},
    }

    state = build_monthly_payment_state(members, "2026-10")

    assert state == {
        "period": "2026-10",
        "members": {
            "member-001": {
                "status": "unpaid",
                "paid_at": None,
                "reminders_sent": [],
            }
        },
        "payment_admin_followups_sent": {},
    }


def test_billing_period_uses_utc_month():
    """Billing periods are derived consistently from UTC timestamps."""
    timestamp = datetime(2026, 11, 1, 0, 15, tzinfo=timezone.utc)

    assert current_billing_period(timestamp) == "2026-11"


def test_payment_state_round_trip_and_invalid_data_fallback(tmp_path):
    """Payment state persists and malformed top-level data is rejected safely."""
    file_path = tmp_path / "payment_state.json"
    state = build_monthly_payment_state({}, "2026-10")
    save_payment_state(str(file_path), state)

    assert load_payment_state(str(file_path)) == state

    file_path.write_text("[]", encoding="utf-8")
    assert load_payment_state(str(file_path)) == {}


def test_payment_reminder_stages_follow_the_monthly_schedule():
    """Public payment reminders trigger at each planned local date."""
    assert get_payment_reminder_stage(
        datetime(2026, 10, 25, 9, 0, tzinfo=PAYMENT_TIMEZONE),
    ) == ("2026-11", "7d")
    assert get_payment_reminder_stage(
        datetime(2026, 10, 30, 9, 0, tzinfo=PAYMENT_TIMEZONE),
    ) == ("2026-11", "2d")
    assert get_payment_reminder_stage(
        datetime(2026, 10, 31, 9, 0, tzinfo=PAYMENT_TIMEZONE),
    ) == ("2026-11", "1d")
    assert get_payment_reminder_stage(
        datetime(2026, 11, 1, 9, 0, tzinfo=PAYMENT_TIMEZONE),
    ) == ("2026-11", "first_day")
    assert get_payment_reminder_stage(
        datetime(2026, 11, 6, 9, 0, tzinfo=PAYMENT_TIMEZONE),
    ) == ("2026-11", "overdue_5d")


def test_payment_reminder_sends_once_and_persists_duplicate_key(tmp_path):
    """A due public reminder is delivered once across repeated checks."""
    bot = SimpleNamespace(send_message=AsyncMock())
    context = SimpleNamespace(bot=bot)
    state = {"payment_state": {"period": "2026-10", "members": {}}}
    config = {
        "MEMBERS_GROUP_ID": -100123,
        "PAYMENT_REMINDER_HOUR": 9,
        "PAYMENT_STATE_FILE": str(tmp_path / "payment_state.json"),
    }
    now = datetime(2026, 10, 25, 10, 0, tzinfo=PAYMENT_TIMEZONE)

    asyncio.run(check_payment_reminders(context, config, state, now=now))
    asyncio.run(check_payment_reminders(context, config, state, now=now))

    bot.send_message.assert_awaited_once()
    assert bot.send_message.await_args.kwargs["chat_id"] == -100123
    assert "noviembre de 2026" in bot.send_message.await_args.kwargs["text"]
    assert "2026-11:7d" in state["payment_state"]["public_reminders_sent"]


def test_failed_payment_reminder_is_retried(tmp_path):
    """Failed delivery is not marked sent and can succeed on a later check."""
    bot = SimpleNamespace(
        send_message=AsyncMock(side_effect=[RuntimeError("network"), None])
    )
    context = SimpleNamespace(bot=bot)
    state = {"payment_state": {"period": "2026-10", "members": {}}}
    config = {
        "MEMBERS_GROUP_ID": -100123,
        "PAYMENT_REMINDER_HOUR": 9,
        "PAYMENT_STATE_FILE": str(tmp_path / "payment_state.json"),
    }
    now = datetime(2026, 10, 25, 10, 0, tzinfo=PAYMENT_TIMEZONE)

    with pytest.raises(RuntimeError):
        asyncio.run(check_payment_reminders(context, config, state, now=now))
    asyncio.run(check_payment_reminders(context, config, state, now=now))

    assert bot.send_message.await_count == 2
    assert "2026-11:7d" in state["payment_state"]["public_reminders_sent"]


def test_payment_admin_followup_includes_review_button():
    """Payment-admin follow-ups include the protected payment review action."""
    bot = SimpleNamespace(send_message=AsyncMock())
    context = SimpleNamespace(bot=bot)

    result = asyncio.run(
        send_payment_admin_followup(context, 20, "2026-11", "7d")
    )

    assert result is True
    assert bot.send_message.await_args.kwargs["chat_id"] == 20
    assert "Revisión de pagos" in bot.send_message.await_args.kwargs["text"]
    keyboard = bot.send_message.await_args.kwargs["reply_markup"]
    assert keyboard.inline_keyboard[0][0].callback_data == "payment_review"


def test_payment_admin_followup_waits_for_delay_and_is_sent_once(tmp_path):
    """A successful public reminder leads to one delayed admin follow-up."""
    bot = SimpleNamespace(send_message=AsyncMock())
    context = SimpleNamespace(bot=bot)
    state = {"payment_state": {"period": "2026-10", "members": {}}}
    config = {
        "MEMBERS_GROUP_ID": -100123,
        "PAYMENT_ADMIN_ID": 20,
        "PAYMENT_ADMIN_FOLLOWUP_DELAY_HOURS": 2,
        "PAYMENT_REMINDER_HOUR": 9,
        "PAYMENT_STATE_FILE": str(tmp_path / "payment_state.json"),
    }

    asyncio.run(
        check_payment_reminders(
            context,
            config,
            state,
            now=datetime(2026, 10, 25, 10, 0, tzinfo=PAYMENT_TIMEZONE),
        )
    )
    assert bot.send_message.await_count == 1

    asyncio.run(
        check_payment_reminders(
            context,
            config,
            state,
            now=datetime(2026, 10, 25, 11, 59, tzinfo=PAYMENT_TIMEZONE),
        )
    )
    assert bot.send_message.await_count == 1

    asyncio.run(
        check_payment_reminders(
            context,
            config,
            state,
            now=datetime(2026, 10, 25, 12, 0, tzinfo=PAYMENT_TIMEZONE),
        )
    )
    assert bot.send_message.await_count == 2
    assert bot.send_message.await_args_list[1].kwargs["chat_id"] == 20
    assert "2026-11:7d" in state["payment_state"]["payment_admin_followups_sent"]

    asyncio.run(
        check_payment_reminders(
            context,
            config,
            state,
            now=datetime(2026, 10, 25, 13, 0, tzinfo=PAYMENT_TIMEZONE),
        )
    )
    assert bot.send_message.await_count == 2


def test_failed_payment_admin_followup_is_retried(tmp_path):
    """A failed payment-admin follow-up remains eligible for retry."""
    bot = SimpleNamespace(
        send_message=AsyncMock(side_effect=[None, RuntimeError("network"), None])
    )
    context = SimpleNamespace(bot=bot)
    state = {"payment_state": {"period": "2026-10", "members": {}}}
    config = {
        "MEMBERS_GROUP_ID": -100123,
        "PAYMENT_ADMIN_ID": 20,
        "PAYMENT_ADMIN_FOLLOWUP_DELAY_HOURS": 2,
        "PAYMENT_REMINDER_HOUR": 9,
        "PAYMENT_STATE_FILE": str(tmp_path / "payment_state.json"),
    }

    asyncio.run(
        check_payment_reminders(
            context,
            config,
            state,
            now=datetime(2026, 10, 25, 10, 0, tzinfo=PAYMENT_TIMEZONE),
        )
    )
    asyncio.run(
        check_payment_reminders(
            context,
            config,
            state,
            now=datetime(2026, 10, 25, 12, 0, tzinfo=PAYMENT_TIMEZONE),
        )
    )
    assert "2026-11:7d" not in state["payment_state"]["payment_admin_followups_sent"]

    asyncio.run(
        check_payment_reminders(
            context,
            config,
            state,
            now=datetime(2026, 10, 25, 13, 0, tzinfo=PAYMENT_TIMEZONE),
        )
    )
    assert bot.send_message.await_count == 3
    assert "2026-11:7d" in state["payment_state"]["payment_admin_followups_sent"]


def test_payment_review_callback_requires_payment_admin_access():
    """Only payment administrators can open the review from the follow-up."""
    query = SimpleNamespace(
        from_user=SimpleNamespace(id=30),
        answer=AsyncMock(),
        edit_message_text=AsyncMock(),
    )
    update = SimpleNamespace(callback_query=query)

    asyncio.run(
        handle_payment_review(
            update,
            SimpleNamespace(),
            primary_admin_id=10,
            payment_admin_id=20,
            state={"payment_state": {}, "members": {}},
        )
    )

    query.answer.assert_awaited_once_with(
        "No tienes permisos para revisar pagos.",
        show_alert=True,
    )
    query.edit_message_text.assert_not_awaited()


def test_payment_review_callback_opens_review_for_payment_admin():
    """An authorized follow-up action opens the existing payment review."""
    query = SimpleNamespace(
        from_user=SimpleNamespace(id=20),
        answer=AsyncMock(),
        edit_message_text=AsyncMock(),
    )
    update = SimpleNamespace(callback_query=query)
    state = {
        "payment_state": {
            "period": "2026-11",
            "members": {"member-001": {"status": "unpaid"}},
        },
        "members": {"member-001": {"name": "Ada", "active": True}},
    }

    asyncio.run(
        handle_payment_review(
            update,
            SimpleNamespace(),
            primary_admin_id=10,
            payment_admin_id=20,
            state=state,
        )
    )

    query.answer.assert_awaited_once_with()
    query.edit_message_text.assert_awaited_once()
    assert "2026-11" in query.edit_message_text.await_args.args[0]
    assert query.edit_message_text.await_args.kwargs["reply_markup"] is not None


def test_unpaid_recipients_filter_status_activity_preferences_and_duplicates():
    """Personal recipients exclude paid, inactive, opted-out, and duplicate users."""
    members = {
        "member-001": build_member("Ada", 101),
        "member-002": build_member("Lin", 202),
        "member-003": {**build_member("Sam", 303), "active": False},
        "member-004": build_member("Max", 101),
    }
    payment_state = {
        "members": {
            "member-001": {"status": "unpaid"},
            "member-002": {"status": "paid"},
            "member-003": {"status": "unpaid"},
            "member-004": {"status": "unpaid"},
        }
    }
    preferences = {"101": {"payments": True}, "202": {"payments": False}}

    assert get_unpaid_member_recipients(payment_state, members, preferences) == [
        ("member-001", 101),
    ]


def test_personal_payment_reminders_send_private_messages():
    """Personal reminders are delivered to eligible members as private messages."""
    bot = SimpleNamespace(send_message=AsyncMock())
    context = SimpleNamespace(bot=bot)
    members = {"member-001": build_member("Ada", 101)}
    payment_state = {
        "members": {"member-001": {"status": "unpaid"}},
    }

    result = asyncio.run(
        send_personal_payment_reminders(
            context,
            payment_state,
            members,
            {"101": {"payments": True}},
            "2026-11",
            "first_day",
        )
    )

    assert result == {"sent": 1, "failures": 0, "sent_member_ids": ["member-001"]}
    assert bot.send_message.await_args.kwargs["chat_id"] == 101
    assert "Ada" in bot.send_message.await_args.kwargs["text"]
    assert "noviembre de 2026" in bot.send_message.await_args.kwargs["text"]


def test_personal_reminders_are_deduplicated_and_paid_members_are_skipped(tmp_path):
    """Successful personal reminders are not repeated and paid members are skipped."""
    bot = SimpleNamespace(send_message=AsyncMock())
    context = SimpleNamespace(bot=bot)
    state = {
        "members": {
            "member-001": build_member("Ada", 101),
            "member-002": build_member("Lin", 202),
        },
        "notification_preferences": {
            "101": {"payments": True},
            "202": {"payments": True},
        },
        "payment_state": {
            "period": "2026-11",
            "members": {
                "member-001": {"status": "unpaid"},
                "member-002": {"status": "paid"},
            },
        },
    }
    config = {
        "PAYMENT_REMINDER_HOUR": 9,
        "PAYMENT_STATE_FILE": str(tmp_path / "payment_state.json"),
    }
    now = datetime(2026, 11, 1, 10, 0, tzinfo=PAYMENT_TIMEZONE)

    asyncio.run(check_payment_reminders(context, config, state, now=now))
    asyncio.run(check_payment_reminders(context, config, state, now=now))

    bot.send_message.assert_awaited_once()
    assert bot.send_message.await_args.kwargs["chat_id"] == 101
    assert "2026-11:first_day:member-001" in state["payment_state"]["personal_reminders_sent"]


def test_failed_personal_reminder_is_retried(tmp_path):
    """Failed personal delivery remains eligible for a later retry."""
    bot = SimpleNamespace(
        send_message=AsyncMock(side_effect=[RuntimeError("network"), None])
    )
    context = SimpleNamespace(bot=bot)
    state = {
        "members": {"member-001": build_member("Ada", 101)},
        "notification_preferences": {"101": {"payments": True}},
        "payment_state": {
            "period": "2026-11",
            "members": {"member-001": {"status": "unpaid"}},
        },
    }
    config = {
        "PAYMENT_REMINDER_HOUR": 9,
        "PAYMENT_STATE_FILE": str(tmp_path / "payment_state.json"),
    }
    now = datetime(2026, 11, 6, 10, 0, tzinfo=PAYMENT_TIMEZONE)

    asyncio.run(check_payment_reminders(context, config, state, now=now))
    asyncio.run(check_payment_reminders(context, config, state, now=now))

    assert bot.send_message.await_count == 2
    assert "2026-11:overdue_5d:member-001" in state["payment_state"]["personal_reminders_sent"]
