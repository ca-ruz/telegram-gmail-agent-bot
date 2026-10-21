"""Authorization and roster helpers for the membership payment workflow."""

import logging
import re

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
