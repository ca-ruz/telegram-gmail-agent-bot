"""Authorization and roster helpers for the membership payment workflow."""

import logging
import re
from datetime import datetime, timezone
from datetime import timedelta
from zoneinfo import ZoneInfo

from tools.local.data_manager import load_json, save_json


PRIMARY_ADMIN_ROLE = "primary_admin"
PAYMENT_ADMIN_ROLE = "payment_admin"
logger = logging.getLogger(__name__)
PAYMENT_TIMEZONE = ZoneInfo("America/Mexico_City")
PAYMENT_MONTHS = (
    "enero", "febrero", "marzo", "abril", "mayo", "junio",
    "julio", "agosto", "septiembre", "octubre", "noviembre", "diciembre",
)


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
        "public_reminders_sent": payment_state.get("public_reminders_sent", {})
        if isinstance(payment_state.get("public_reminders_sent", {}), dict)
        else {},
    }


def next_month_first(current_date):
    """Return the first day of the month after the supplied date."""
    first_of_month = current_date.replace(day=1)
    return (first_of_month + timedelta(days=32)).replace(day=1)


def get_payment_reminder_stage(now=None, reminder_hour=9):
    """Return the upcoming billing period and due reminder stage, if any."""
    current_time = now or datetime.now(PAYMENT_TIMEZONE)
    if current_time.tzinfo is None:
        current_time = current_time.replace(tzinfo=PAYMENT_TIMEZONE)
    local_time = current_time.astimezone(PAYMENT_TIMEZONE)
    first_of_next_month = next_month_first(local_time.date())
    if local_time.hour < reminder_hour:
        return None

    first_of_current_month = local_time.date().replace(day=1)
    if local_time.date() == first_of_current_month:
        return first_of_current_month.strftime("%Y-%m"), "first_day"

    for days_before, stage in ((7, "7d"), (2, "2d"), (1, "1d")):
        if local_time.date() == first_of_next_month - timedelta(days=days_before):
            return first_of_next_month.strftime("%Y-%m"), stage

    if local_time.date() == first_of_current_month + timedelta(days=5):
        return first_of_current_month.strftime("%Y-%m"), "overdue_5d"

    return None


def format_payment_period(period):
    """Return a Spanish month label for a ``YYYY-MM`` billing period."""
    try:
        year, month = (int(part) for part in period.split("-"))
        return f"{PAYMENT_MONTHS[month - 1]} de {year}"
    except (AttributeError, IndexError, TypeError, ValueError):
        return str(period)


def format_payment_reminder(period, stage):
    """Build an anonymous public reminder for a billing stage."""
    month_label = format_payment_period(period)
    if stage == "overdue_5d":
        return (
            f"💳 Recordatorio: hay pagos de membresía pendientes de {month_label}.\n"
            "Si todavía no has pagado, por favor ponte al corriente."
        )
    if stage == "first_day":
        return (
            f"💳 Hoy comienza el periodo de membresía de {month_label}.\n"
            "Por favor realiza tu pago cuando te sea posible."
        )
    return (
        f"💳 Recordatorio: el periodo de membresía de {month_label} comienza pronto.\n"
        "Por favor prepara tu pago. Si ya pagaste, ignora este mensaje."
    )


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


async def check_payment_reminders(context, config, state, now=None):
    """Send one anonymous public payment reminder when a stage is due."""
    group_id = config.get("MEMBERS_GROUP_ID", 0)
    if not group_id:
        return

    stage_info = get_payment_reminder_stage(
        now,
        config.get("PAYMENT_REMINDER_HOUR", 9),
    )
    if not stage_info:
        return

    period, stage = stage_info
    payment_state = state.setdefault("payment_state", {})
    sent_reminders = payment_state.setdefault("public_reminders_sent", {})
    reminder_key = f"{period}:{stage}"
    if reminder_key in sent_reminders:
        return

    await context.bot.send_message(
        chat_id=group_id,
        text=format_payment_reminder(period, stage),
    )
    sent_reminders[reminder_key] = datetime.now(timezone.utc).isoformat()
    save_payment_state(config["PAYMENT_STATE_FILE"], payment_state)


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
