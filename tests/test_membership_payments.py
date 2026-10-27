import asyncio
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from bot.handlers.admin import (
    handle_payment_period,
    handle_payment_review,
    handle_payment_status,
)
from core.membership_payments import (
    PAYMENT_ADMIN_ROLE,
    PRIMARY_ADMIN_ROLE,
    build_member,
    build_monthly_payment_state,
    can_manage_payments,
    check_payment_reminders,
    current_billing_period,
    build_multi_period_payment_state,
    ensure_payment_period,
    get_payment_admin_role,
    get_overlapping_payment_reminder_schedule,
    get_payment_reminder_schedule,
    get_payment_reminder_stage,
    get_unpaid_member_recipients,
    load_members,
    load_multi_period_payment_state,
    load_payment_state,
    migrate_payment_state,
    next_billing_period,
    next_member_id,
    PAYMENT_TIMEZONE,
    previous_billing_period,
    save_members,
    save_multi_period_payment_state,
    save_payment_state,
    send_payment_admin_followup,
    send_personal_payment_reminders,
    synchronize_multi_period_payment_state,
)
from tools.local.data_manager import load_json


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
        "public_reminder_groups_sent": {},
        "payment_admin_followups_sent": {},
    }


def test_billing_period_uses_utc_month():
    """Billing periods are derived consistently from UTC timestamps."""
    timestamp = datetime(2026, 11, 1, 0, 15, tzinfo=timezone.utc)

    assert current_billing_period(timestamp) == "2026-11"
    assert next_billing_period("2026-12") == "2027-01"


def test_legacy_payment_state_migrates_without_losing_period_history():
    """Legacy single-period data is retained under its original period key."""
    legacy_state = {
        "period": "2026-10",
        "members": {
            "member-001": {
                "status": "unpaid",
                "paid_at": None,
                "reminders_sent": ["first_day"],
            },
        },
        "public_reminders_sent": {"2026-10:first_day": "sent-at"},
        "personal_reminders_sent": {"2026-10:first_day:member-001": "sent-at"},
        "payment_admin_followups_sent": {},
    }

    migrated = migrate_payment_state(legacy_state)

    assert migrated["version"] == 2
    assert migrated["periods"]["2026-10"]["members"]["member-001"]["status"] == "unpaid"
    assert migrated["periods"]["2026-10"]["public_reminders_sent"] == {
        "2026-10:first_day": "sent-at"
    }
    assert migrated["periods"]["2026-10"]["personal_reminders_sent"] == {
        "2026-10:first_day:member-001": "sent-at"
    }


def test_ensure_payment_period_adds_new_period_without_replacing_old_period():
    """Creating a new month leaves the previous period and its unpaid records intact."""
    members = {"member-001": build_member("Ada", 101)}
    october_state = {
        "period": "2026-10",
        "members": {"member-001": {"status": "unpaid"}},
    }

    state = ensure_payment_period(october_state, members, "2026-11")

    assert set(state["periods"]) == {"2026-10", "2026-11"}
    assert state["periods"]["2026-10"]["members"]["member-001"]["status"] == "unpaid"
    assert state["periods"]["2026-11"]["members"]["member-001"]["status"] == "unpaid"


def test_synchronize_multi_period_state_preserves_paid_status_and_adds_new_members():
    """Startup synchronization preserves each period while adding new active members."""
    state = {
        "periods": {
            "2026-10": {
                "members": {
                    "member-001": {"status": "paid", "paid_at": "paid-at"},
                },
            },
        },
    }
    members = {
        "member-001": build_member("Ada", 101),
        "member-002": build_member("Lin", 202),
    }

    synchronized = synchronize_multi_period_payment_state(
        state,
        members,
        ["2026-10", "2026-11"],
    )

    october = synchronized["periods"]["2026-10"]["members"]
    assert october["member-001"]["status"] == "paid"
    assert october["member-002"]["status"] == "unpaid"
    assert set(synchronized["periods"]["2026-11"]["members"]) == {
        "member-001",
        "member-002",
    }


def test_multi_period_payment_state_round_trip(tmp_path):
    """Versioned payment state persists multiple periods independently."""
    file_path = tmp_path / "payment_state.json"
    members = {"member-001": build_member("Ada", 101)}
    state = build_multi_period_payment_state(members, ["2026-10", "2026-11"])
    state["periods"]["2026-10"]["members"]["member-001"]["status"] = "paid"

    save_multi_period_payment_state(str(file_path), state)

    loaded = load_multi_period_payment_state(str(file_path))
    assert loaded["periods"]["2026-10"]["members"]["member-001"]["status"] == "paid"
    assert loaded["periods"]["2026-11"]["members"]["member-001"]["status"] == "unpaid"


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


def test_payment_reminder_schedule_waits_until_the_configured_hour():
    """The new cadence does not run before the configured local hour."""
    assert get_payment_reminder_schedule(
        datetime(2026, 11, 1, 8, 59, tzinfo=PAYMENT_TIMEZONE),
    ) is None


@pytest.mark.parametrize(
    ("day", "personal_stage", "public_stage"),
    [
        (1, "first_day", "first_day"),
        (2, "daily", None),
        (5, "daily", None),
        (6, "daily", "week_1_1"),
        (8, "daily", "week_2_1"),
        (11, "daily", "week_2_2"),
        (15, "daily", "week_3_1"),
        (17, "daily", "week_3_2"),
        (19, "daily", "week_3_3"),
        (22, "daily", "week_4_1"),
        (24, "daily", "week_4_2"),
        (26, "daily", "week_4_3"),
        (28, "daily", "week_4_4"),
        (29, "daily", None),
    ],
)
def test_payment_reminder_schedule_has_expected_monthly_cadence(
    day,
    personal_stage,
    public_stage,
):
    """The cadence matches the agreed private and public reminder frequency."""
    schedule = get_payment_reminder_schedule(
        datetime(2026, 11, day, 9, 0, tzinfo=PAYMENT_TIMEZONE),
    )

    assert schedule == {
        "period": "2026-11",
        "personal_stage": personal_stage,
        "public_stage": public_stage,
    }


def test_overlapping_schedule_covers_advance_and_rollover_periods():
    """The overlapping scheduler identifies upcoming and previous periods."""
    assert get_overlapping_payment_reminder_schedule(
        datetime(2026, 10, 28, 8, 59, tzinfo=PAYMENT_TIMEZONE),
    ) == []
    assert get_overlapping_payment_reminder_schedule(
        datetime(2026, 10, 29, 9, 0, tzinfo=PAYMENT_TIMEZONE),
    ) == [
        {
            "period": "2026-10",
            "personal_stage": "daily",
            "public_stage": None,
        },
        {
            "period": "2026-11",
            "personal_stage": "advance",
            "public_stage": "advance",
        },
    ]
    assert get_overlapping_payment_reminder_schedule(
        datetime(2026, 11, 1, 9, 0, tzinfo=PAYMENT_TIMEZONE),
    ) == [
        {
            "period": "2026-11",
            "personal_stage": "first_day",
            "public_stage": "first_day",
        },
        {
            "period": "2026-10",
            "personal_stage": "overdue_daily",
            "public_stage": "overdue_rollover",
        },
    ]
    assert get_overlapping_payment_reminder_schedule(
        datetime(2026, 11, 2, 9, 0, tzinfo=PAYMENT_TIMEZONE),
        available_periods=["2026-10", "2026-11"],
    ) == [
        {
            "period": "2026-11",
            "personal_stage": "daily",
            "public_stage": None,
        },
        {
            "period": "2026-10",
            "personal_stage": "overdue_daily",
            "public_stage": None,
        },
    ]
    assert previous_billing_period("2027-01") == "2026-12"


def test_advance_reminder_combines_current_and_upcoming_periods(tmp_path):
    """The advance reminder reaches both DMs and payment groups once."""
    bot = SimpleNamespace(send_message=AsyncMock())
    context = SimpleNamespace(bot=bot)
    member = build_member("Ada", 101)
    state = {
        "members": {"member-001": member},
        "notification_preferences": {"101": {"payments": True}},
        "payment_state": build_multi_period_payment_state(
            {"member-001": member},
            ["2026-10", "2026-11"],
        ),
    }
    config = {
        "MEMBERS_GROUP_ID": -100123,
        "PAYMENT_REMINDER_HOUR": 9,
        "PAYMENT_ADVANCE_REMINDER_DAYS": 3,
        "PAYMENT_STATE_FILE": str(tmp_path / "payment_state.json"),
    }
    now = datetime(2026, 10, 29, 10, 0, tzinfo=PAYMENT_TIMEZONE)

    asyncio.run(check_payment_reminders(context, config, state, now=now))
    asyncio.run(check_payment_reminders(context, config, state, now=now))

    assert [call.kwargs["chat_id"] for call in bot.send_message.await_args_list] == [
        -100123,
        101,
    ]
    assert "noviembre de 2026" in bot.send_message.await_args_list[0].kwargs["text"]
    personal_text = bot.send_message.await_args_list[1].kwargs["text"]
    assert "octubre de 2026" in personal_text
    assert "noviembre de 2026" in personal_text
    periods = state["payment_state"]["periods"]
    assert "2026-11:advance" in periods["2026-11"]["public_reminders_sent"]
    assert "2026-10:daily:2026-10-29:member-001" in periods["2026-10"][
        "personal_reminders_sent"
    ]
    assert "2026-11:advance:member-001" in periods["2026-11"][
        "personal_reminders_sent"
    ]


def test_first_day_combines_current_and_previous_period_reminders(tmp_path):
    """The first day reminds about both the new and previous unpaid periods."""
    bot = SimpleNamespace(send_message=AsyncMock())
    context = SimpleNamespace(bot=bot)
    member = build_member("Ada", 101)
    state = {
        "members": {"member-001": member},
        "notification_preferences": {"101": {"payments": True}},
        "payment_state": build_multi_period_payment_state(
            {"member-001": member},
            ["2026-10", "2026-11"],
        ),
    }
    config = {
        "MEMBERS_GROUP_ID": -100123,
        "PAYMENT_REMINDER_HOUR": 9,
        "PAYMENT_STATE_FILE": str(tmp_path / "payment_state.json"),
    }
    now = datetime(2026, 11, 1, 10, 0, tzinfo=PAYMENT_TIMEZONE)

    asyncio.run(check_payment_reminders(context, config, state, now=now))
    asyncio.run(check_payment_reminders(context, config, state, now=now))

    assert [call.kwargs["chat_id"] for call in bot.send_message.await_args_list] == [
        -100123,
        101,
    ]
    group_text = bot.send_message.await_args_list[0].kwargs["text"]
    personal_text = bot.send_message.await_args_list[1].kwargs["text"]
    for text in (group_text, personal_text):
        assert "octubre de 2026" in text
        assert "noviembre de 2026" in text
    periods = state["payment_state"]["periods"]
    assert "2026-10:overdue_rollover" in periods["2026-10"][
        "public_reminders_sent"
    ]
    assert "2026-11:first_day" in periods["2026-11"]["public_reminders_sent"]


def test_paid_previous_period_is_excluded_from_combined_private_reminder(tmp_path):
    """A paid previous period does not generate another private reminder."""
    bot = SimpleNamespace(send_message=AsyncMock())
    context = SimpleNamespace(bot=bot)
    member = build_member("Ada", 101)
    state = {
        "members": {"member-001": member},
        "notification_preferences": {"101": {"payments": True}},
        "payment_state": build_multi_period_payment_state(
            {"member-001": member},
            ["2026-10", "2026-11"],
        ),
    }
    state["payment_state"]["periods"]["2026-10"]["members"]["member-001"][
        "status"
    ] = "paid"
    config = {
        "MEMBERS_GROUP_ID": -100123,
        "PAYMENT_REMINDER_HOUR": 9,
        "PAYMENT_STATE_FILE": str(tmp_path / "payment_state.json"),
    }

    asyncio.run(
        check_payment_reminders(
            context,
            config,
            state,
            now=datetime(2026, 11, 1, 10, 0, tzinfo=PAYMENT_TIMEZONE),
        )
    )

    assert bot.send_message.await_args_list[1].kwargs["chat_id"] == 101
    personal_text = bot.send_message.await_args_list[1].kwargs["text"]
    assert "noviembre de 2026" in personal_text
    assert "octubre de 2026" not in personal_text


def test_previous_period_private_reminder_continues_after_rollover(tmp_path):
    """Previous-period private reminders continue after the first day."""
    bot = SimpleNamespace(send_message=AsyncMock())
    context = SimpleNamespace(bot=bot)
    member = build_member("Ada", 101)
    state = {
        "members": {"member-001": member},
        "notification_preferences": {"101": {"payments": True}},
        "payment_state": build_multi_period_payment_state(
            {"member-001": member},
            ["2026-10", "2026-11"],
        ),
    }
    config = {
        "PAYMENT_REMINDER_HOUR": 9,
        "PAYMENT_STATE_FILE": str(tmp_path / "payment_state.json"),
    }

    asyncio.run(
        check_payment_reminders(
            context,
            config,
            state,
            now=datetime(2026, 11, 2, 10, 0, tzinfo=PAYMENT_TIMEZONE),
        )
    )

    assert bot.send_message.await_count == 1
    assert bot.send_message.await_args.kwargs["chat_id"] == 101
    text = bot.send_message.await_args.kwargs["text"]
    assert "octubre de 2026" in text
    assert "noviembre de 2026" in text


def test_daily_payment_dms_are_deduplicated_per_day(tmp_path):
    """Daily private reminders repeat on new dates but not within one date."""
    bot = SimpleNamespace(send_message=AsyncMock())
    context = SimpleNamespace(bot=bot)
    state = {
        "members": {"member-001": build_member("Ada", 101)},
        "notification_preferences": {"101": {"payments": True}},
        "payment_state": build_multi_period_payment_state(
            {"member-001": build_member("Ada", 101)},
            ["2026-11"],
        ),
    }
    config = {
        "PAYMENT_REMINDER_HOUR": 9,
        "PAYMENT_STATE_FILE": str(tmp_path / "payment_state.json"),
    }

    asyncio.run(
        check_payment_reminders(
            context,
            config,
            state,
            now=datetime(2026, 11, 2, 10, 0, tzinfo=PAYMENT_TIMEZONE),
        )
    )
    asyncio.run(
        check_payment_reminders(
            context,
            config,
            state,
            now=datetime(2026, 11, 2, 10, 30, tzinfo=PAYMENT_TIMEZONE),
        )
    )
    asyncio.run(
        check_payment_reminders(
            context,
            config,
            state,
            now=datetime(2026, 11, 3, 10, 0, tzinfo=PAYMENT_TIMEZONE),
        )
    )

    assert [call.kwargs["chat_id"] for call in bot.send_message.await_args_list] == [
        101,
        101,
    ]
    personal_reminders = state["payment_state"]["periods"]["2026-11"][
        "personal_reminders_sent"
    ]
    assert "2026-11:daily:2026-11-02:member-001" in personal_reminders
    assert "2026-11:daily:2026-11-03:member-001" in personal_reminders


def test_public_payment_reminders_follow_weekly_frequency(tmp_path):
    """Public reminders follow the configured weekly frequency after the grace period."""
    bot = SimpleNamespace(bot=SimpleNamespace(send_message=AsyncMock()))
    state = {
        "payment_state": build_multi_period_payment_state({}, ["2026-11"]),
    }
    config = {
        "MEMBERS_GROUP_ID": -100123,
        "PAYMENT_REMINDER_HOUR": 9,
        "PAYMENT_STATE_FILE": str(tmp_path / "payment_state.json"),
    }

    for day in (6, 6, 7, 8, 11):
        asyncio.run(
            check_payment_reminders(
                bot,
                config,
                state,
                now=datetime(2026, 11, day, 10, 0, tzinfo=PAYMENT_TIMEZONE),
            )
        )

    assert [call.kwargs["chat_id"] for call in bot.bot.send_message.await_args_list] == [
        -100123,
        -100123,
        -100123,
    ]
    assert set(
        state["payment_state"]["periods"]["2026-11"]["public_reminders_sent"]
    ) == {
        "2026-11:week_1_1",
        "2026-11:week_2_1",
        "2026-11:week_2_2",
    }


def test_new_cadence_retries_failed_public_delivery(tmp_path):
    """A failed public reminder remains eligible on the next scheduler run."""
    bot = SimpleNamespace(
        send_message=AsyncMock(side_effect=[RuntimeError("network"), None])
    )
    context = SimpleNamespace(bot=bot)
    state = {
        "payment_state": build_multi_period_payment_state({}, ["2026-11"]),
    }
    config = {
        "MEMBERS_GROUP_ID": -100123,
        "PAYMENT_REMINDER_HOUR": 9,
        "PAYMENT_STATE_FILE": str(tmp_path / "payment_state.json"),
    }
    now = datetime(2026, 11, 6, 10, 0, tzinfo=PAYMENT_TIMEZONE)

    with pytest.raises(RuntimeError):
        asyncio.run(check_payment_reminders(context, config, state, now=now))
    asyncio.run(check_payment_reminders(context, config, state, now=now))

    assert bot.send_message.await_count == 2
    assert "2026-11:week_1_1" in state["payment_state"]["periods"]["2026-11"][
        "public_reminders_sent"
    ]


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


def test_payment_reminder_writes_to_due_period_without_overwriting_previous_period(tmp_path):
    """A November reminder updates November while preserving October payment history."""
    bot = SimpleNamespace(send_message=AsyncMock())
    context = SimpleNamespace(bot=bot)
    state = {
        "groups": {-100123},
        "group_notification_categories": {
            "events": [],
            "payments": [-100123],
            "coworking": [],
        },
        "payment_state": {
            "version": 2,
            "periods": {
                "2026-10": {
                    "members": {
                        "member-001": {"status": "unpaid"},
                    },
                },
                "2026-11": {
                    "members": {
                        "member-001": {"status": "unpaid"},
                    },
                },
            },
        },
        "members": {"member-001": build_member("Ada", 101)},
    }
    config = {
        "PAYMENT_REMINDER_HOUR": 9,
        "PAYMENT_STATE_FILE": str(tmp_path / "payment_state.json"),
    }

    asyncio.run(
        check_payment_reminders(
            context,
            config,
            state,
            now=datetime(2026, 11, 6, 10, 0, tzinfo=PAYMENT_TIMEZONE),
        )
    )

    assert "2026-11:week_1_1" in state["payment_state"]["periods"]["2026-11"][
        "public_reminders_sent"
    ]
    assert state["payment_state"]["periods"]["2026-10"][
        "public_reminders_sent"
    ] == {}
    assert "2026-11:week_1_1" in load_json(str(tmp_path / "payment_state.json"), {})[
        "periods"
    ]["2026-11"]["public_reminders_sent"]


def test_payment_reminder_uses_payment_groups_and_excludes_event_groups(tmp_path):
    """Public payment reminders target payment groups without notifying event groups."""
    bot = SimpleNamespace(send_message=AsyncMock())
    context = SimpleNamespace(bot=bot)
    state = {
        "groups": {-100, -200},
        "group_notification_categories": {
            "events": [-200],
            "payments": [-100],
            "coworking": [-100, -200],
        },
        "payment_state": {"period": "2026-10", "members": {}},
    }
    config = {
        "MEMBERS_GROUP_ID": -100,
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

    assert [call.kwargs["chat_id"] for call in bot.send_message.await_args_list] == [-100]
    assert state["payment_state"]["public_reminder_groups_sent"]["2026-11:7d"] == [-100]


def test_payment_reminder_retries_only_failed_payment_groups(tmp_path):
    """A failed payment-group delivery retries without duplicating successful groups."""
    bot = SimpleNamespace(
        send_message=AsyncMock(
            side_effect=[None, RuntimeError("network"), None]
        )
    )
    context = SimpleNamespace(bot=bot)
    state = {
        "group_notification_categories": {
            "events": [],
            "payments": [-100, -200],
            "coworking": [],
        },
        "payment_state": {"period": "2026-10", "members": {}},
    }
    config = {
        "PAYMENT_REMINDER_HOUR": 9,
        "PAYMENT_STATE_FILE": str(tmp_path / "payment_state.json"),
    }
    now = datetime(2026, 10, 25, 10, 0, tzinfo=PAYMENT_TIMEZONE)

    with pytest.raises(RuntimeError):
        asyncio.run(check_payment_reminders(context, config, state, now=now))
    asyncio.run(check_payment_reminders(context, config, state, now=now))

    assert [call.kwargs["chat_id"] for call in bot.send_message.await_args_list] == [
        -200,
        -100,
        -100,
    ]
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
    assert keyboard.inline_keyboard[0][0].callback_data == "payment_review:2026-11"


def test_payment_admin_followup_opens_the_relevant_period():
    """A follow-up review button preserves the billing period it references."""
    bot = SimpleNamespace(send_message=AsyncMock())
    context = SimpleNamespace(bot=bot)
    asyncio.run(send_payment_admin_followup(context, 20, "2026-11", "7d"))

    query = SimpleNamespace(
        from_user=SimpleNamespace(id=20),
        data="payment_review:2026-11",
        answer=AsyncMock(),
        edit_message_text=AsyncMock(),
    )
    state = {
        "payment_state": {
            "version": 2,
            "periods": {
                "2026-10": {"members": {"member-001": {"status": "paid"}}},
                "2026-11": {"members": {"member-001": {"status": "unpaid"}}},
            },
        },
        "members": {"member-001": {"name": "Ada", "active": True}},
    }

    asyncio.run(
        handle_payment_review(
            SimpleNamespace(callback_query=query),
            SimpleNamespace(),
            primary_admin_id=10,
            payment_admin_id=20,
            state=state,
        )
    )

    assert "2026-11" in query.edit_message_text.await_args.args[0]
    assert "Pendientes (1)" in query.edit_message_text.await_args.args[0]


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


def test_payment_period_selector_switches_between_periods():
    """Authorized reviewers can switch the payment review to another period."""
    query = SimpleNamespace(
        from_user=SimpleNamespace(id=20),
        data="payment_period:2026-10",
        answer=AsyncMock(),
        edit_message_text=AsyncMock(),
    )
    state = {
        "payment_state": {
            "version": 2,
            "periods": {
                "2026-10": {"members": {"member-001": {"status": "unpaid"}}},
                "2026-11": {"members": {"member-001": {"status": "paid"}}},
            },
        },
        "members": {"member-001": {"name": "Ada", "active": True}},
    }

    asyncio.run(
        handle_payment_period(
            SimpleNamespace(callback_query=query),
            SimpleNamespace(),
            primary_admin_id=10,
            payment_admin_id=20,
            state=state,
        )
    )

    assert query.answer.await_args.args == ()
    assert "2026-10" in query.edit_message_text.await_args.args[0]
    buttons = query.edit_message_text.await_args.kwargs["reply_markup"].inline_keyboard
    assert buttons[0][0].callback_data == "payment_period:2026-10"
    assert buttons[1][0].callback_data == "payment_period:2026-11"


def test_payment_status_toggle_updates_only_selected_period(tmp_path):
    """Period-scoped status changes do not alter another billing period."""
    query = SimpleNamespace(
        from_user=SimpleNamespace(id=20),
        data="payment_toggle:2026-10:member-001",
        answer=AsyncMock(),
        edit_message_text=AsyncMock(),
    )
    state = {
        "payment_state": {
            "version": 2,
            "periods": {
                "2026-10": {"members": {"member-001": {"status": "unpaid"}}},
                "2026-11": {"members": {"member-001": {"status": "unpaid"}}},
            },
        },
        "members": {"member-001": {"name": "Ada", "active": True}},
    }
    config = {"PAYMENT_STATE_FILE": str(tmp_path / "payment_state.json")}

    asyncio.run(
        handle_payment_status(
            SimpleNamespace(callback_query=query),
            SimpleNamespace(),
            primary_admin_id=10,
            payment_admin_id=20,
            state=state,
            config=config,
        )
    )

    assert state["payment_state"]["periods"]["2026-10"]["members"]["member-001"]["status"] == "paid"
    assert state["payment_state"]["periods"]["2026-11"]["members"]["member-001"]["status"] == "unpaid"
    persisted = load_json(str(tmp_path / "payment_state.json"), {})
    assert persisted["periods"]["2026-10"]["members"]["member-001"]["status"] == "paid"
    assert persisted["periods"]["2026-11"]["members"]["member-001"]["status"] == "unpaid"


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
