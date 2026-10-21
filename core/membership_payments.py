"""Authorization and roster helpers for the membership payment workflow."""

import logging
import re
from datetime import datetime, timezone

from tools.local.data_manager import load_json, save_json


PRIMARY_ADMIN_ROLE = "primary_admin"
PAYMENT_ADMIN_ROLE = "payment_admin"
logger = logging.getLogger(__name__)


def _normalize_user_id(user_id):
    """Return a positive integer Telegram user ID or ``None``."""
    try:
        normalized = int(user_id)
    except (TypeError, ValueError):
        return None

    return normalized if normalized > 0 else None


def get_payment_admin_role(user_id, primary_admin_id, payment_admin_id):
    """Return the user's payment role, or ``None`` when unauthorized."""
    normalized_user_id = _normalize_user_id(user_id)
    if normalized_user_id is None:
        return None

    if normalized_user_id == _normalize_user_id(primary_admin_id):
        return PRIMARY_ADMIN_ROLE
    if normalized_user_id == _normalize_user_id(payment_admin_id):
        return PAYMENT_ADMIN_ROLE
    return None


def can_manage_payments(user_id, primary_admin_id, payment_admin_id):
    """Return whether a user may manage membership payments."""
    return get_payment_admin_role(
        user_id,
        primary_admin_id,
        payment_admin_id,
    ) is not None


def load_members(file_path):
    """Load the persistent member roster from a JSON mapping."""
    members = load_json(file_path, {})
    if isinstance(members, dict):
        return members

    logger.warning("Member roster %s is not a JSON object; using an empty roster.", file_path)
    return {}


def save_members(file_path, members):
    """Persist the member roster atomically."""
    save_json(file_path, members)


def current_billing_period(now=None):
    """Return the UTC billing period represented as ``YYYY-MM``."""
    current_time = now or datetime.now(timezone.utc)
    return current_time.strftime("%Y-%m")


def build_monthly_payment_state(members, period=None):
    """Build an unpaid payment state for active members in one billing period."""
    billing_period = period or current_billing_period()
    return {
        "period": billing_period,
        "members": {
            member_id: {
                "status": "unpaid",
                "paid_at": None,
                "reminders_sent": [],
            }
            for member_id, member in members.items()
            if isinstance(member, dict) and member.get("active", True)
        },
    }


def synchronize_payment_state(payment_state, members, period=None):
    """Reconcile payment state with the active roster for the billing period."""
    billing_period = period or current_billing_period()
    if not isinstance(payment_state, dict) or payment_state.get("period") != billing_period:
        return build_monthly_payment_state(members, billing_period)

    existing_members = payment_state.get("members", {})
    if not isinstance(existing_members, dict):
        existing_members = {}

    synchronized_members = {}
    for member_id, member in members.items():
        if not isinstance(member, dict) or not member.get("active", True):
            continue

        record = existing_members.get(member_id, {})
        if not isinstance(record, dict):
            record = {}
        synchronized_members[member_id] = {
            "status": record.get("status", "unpaid"),
            "paid_at": record.get("paid_at"),
            "reminders_sent": (
                record.get("reminders_sent", [])
                if isinstance(record.get("reminders_sent", []), list)
                else []
            ),
        }

    return {
        "period": billing_period,
        "members": synchronized_members,
    }


def load_payment_state(file_path):
    """Load persisted monthly payment state, returning an empty state when absent."""
    payment_state = load_json(file_path, {})
    if not isinstance(payment_state, dict):
        logger.warning(
            "Payment state %s is not a JSON object; using an empty state.",
            file_path,
        )
        return {}
    return payment_state


def save_payment_state(file_path, payment_state):
    """Persist monthly payment state atomically."""
    save_json(file_path, payment_state)


def build_member(name, telegram_id):
    """Build a normalized active member record."""
    return {
        "name": name,
        "telegram_id": telegram_id,
        "active": True,
    }


def next_member_id(members):
    """Return the next available sequential member identifier."""
    used_ids = {
        int(match.group(1))
        for member_id in members
        if (match := re.fullmatch(r"member-(\d+)", str(member_id)))
    }
    next_number = max(used_ids, default=0) + 1
    while f"member-{next_number:03d}" in members:
        next_number += 1
    return f"member-{next_number:03d}"
