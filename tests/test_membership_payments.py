from datetime import datetime, timezone

from core.membership_payments import (
    PAYMENT_ADMIN_ROLE,
    PRIMARY_ADMIN_ROLE,
    build_member,
    build_monthly_payment_state,
    can_manage_payments,
    current_billing_period,
    get_payment_admin_role,
    load_members,
    load_payment_state,
    next_member_id,
    save_members,
    save_payment_state,
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
