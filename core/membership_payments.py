"""Authorization and roster helpers for the membership payment workflow."""

import logging
import re
from datetime import datetime, timezone
from datetime import timedelta
from zoneinfo import ZoneInfo

from telegram import InlineKeyboardButton, InlineKeyboardMarkup

from core.group_notification_routing import get_group_targets
from core.notification_preferences import is_notification_enabled
from tools.local.data_manager import load_json, save_json


PRIMARY_ADMIN_ROLE = "primary_admin"
PAYMENT_ADMIN_ROLE = "payment_admin"
PAYMENT_STATE_VERSION = 2
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


def get_unpaid_member_recipients(payment_state, members, notification_preferences):
    """Return unique Telegram IDs for active unpaid members with payment alerts enabled."""
    records = payment_state.get("members", {}) if isinstance(payment_state, dict) else {}
    if not isinstance(records, dict) or not isinstance(members, dict):
        return []

    recipients = []
    seen_telegram_ids = set()
    for member_id, member in sorted(members.items()):
        record = records.get(member_id)
        if not isinstance(member, dict) or not member.get("active", True):
            continue
        if not isinstance(record, dict) or record.get("status") == "paid":
            continue

        telegram_id = _normalize_user_id(member.get("telegram_id"))
        if telegram_id is None or telegram_id in seen_telegram_ids:
            continue
        if not is_notification_enabled(
            notification_preferences,
            telegram_id,
            "payments",
        ):
            continue

        seen_telegram_ids.add(telegram_id)
        recipients.append((member_id, telegram_id))

    return recipients


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


def _normalize_period_key(period):
    """Return a valid ``YYYY-MM`` period key or ``None``."""
    try:
        normalized_period = datetime.strptime(str(period), "%Y-%m").strftime("%Y-%m")
    except (TypeError, ValueError):
        return None
    return normalized_period


def _normalize_payment_members(raw_members):
    """Normalize member payment records without discarding historical records."""
    if not isinstance(raw_members, dict):
        return {}

    members = {}
    for member_id, raw_record in raw_members.items():
        if not isinstance(raw_record, dict):
            continue
        record = dict(raw_record)
        record.setdefault("status", "unpaid")
        record.setdefault("paid_at", None)
        record["reminders_sent"] = (
            record.get("reminders_sent", [])
            if isinstance(record.get("reminders_sent", []), list)
            else []
        )
        members[str(member_id)] = record
    return members


def _normalize_payment_period_state(raw_state):
    """Normalize one period record while preserving its delivery history."""
    if not isinstance(raw_state, dict):
        raw_state = {}

    return {
        "members": _normalize_payment_members(raw_state.get("members", {})),
        "public_reminders_sent": raw_state.get("public_reminders_sent", {})
        if isinstance(raw_state.get("public_reminders_sent", {}), dict)
        else {},
        "personal_reminders_sent": raw_state.get("personal_reminders_sent", {})
        if isinstance(raw_state.get("personal_reminders_sent", {}), dict)
        else {},
        "public_reminder_groups_sent": raw_state.get(
            "public_reminder_groups_sent",
            {},
        )
        if isinstance(raw_state.get("public_reminder_groups_sent", {}), dict)
        else {},
        "payment_admin_followups_sent": raw_state.get(
            "payment_admin_followups_sent",
            {},
        )
        if isinstance(raw_state.get("payment_admin_followups_sent", {}), dict)
        else {},
    }


def migrate_payment_state(payment_state):
    """Convert legacy single-period payment state into the versioned period map."""
    if not isinstance(payment_state, dict):
        return {"version": PAYMENT_STATE_VERSION, "periods": {}}

    raw_periods = payment_state.get("periods")
    if isinstance(raw_periods, dict):
        periods = {
            normalized_period: _normalize_payment_period_state(raw_period)
            for period, raw_period in raw_periods.items()
            if (normalized_period := _normalize_period_key(period)) is not None
        }
    else:
        legacy_period = _normalize_period_key(payment_state.get("period"))
        periods = {}
        if legacy_period is not None:
            periods[legacy_period] = _normalize_payment_period_state(payment_state)

    return {
        "version": PAYMENT_STATE_VERSION,
        "periods": periods,
    }


def build_payment_period_state(members):
    """Build a new unpaid payment record for the active membership roster."""
    return {
        "members": {
            str(member_id): {
                "status": "unpaid",
                "paid_at": None,
                "reminders_sent": [],
            }
            for member_id, member in members.items()
            if isinstance(member, dict) and member.get("active", True)
        },
        "public_reminders_sent": {},
        "personal_reminders_sent": {},
        "public_reminder_groups_sent": {},
        "payment_admin_followups_sent": {},
    }


def build_multi_period_payment_state(members, periods=None):
    """Build versioned payment state for one or more billing periods."""
    requested_periods = periods or [current_billing_period()]
    normalized_periods = sorted({
        normalized_period
        for period in requested_periods
        if (normalized_period := _normalize_period_key(period)) is not None
    })
    return {
        "version": PAYMENT_STATE_VERSION,
        "periods": {
            period: build_payment_period_state(members)
            for period in normalized_periods
        },
    }


def ensure_payment_period(payment_state, members, period=None):
    """Ensure one period exists without replacing any existing period records."""
    normalized_state = migrate_payment_state(payment_state)
    period_key = _normalize_period_key(period) or current_billing_period()
    normalized_state["periods"].setdefault(
        period_key,
        build_payment_period_state(members),
    )
    return normalized_state


def load_multi_period_payment_state(file_path):
    """Load and migrate the versioned multi-period payment state."""
    return migrate_payment_state(load_json(file_path, {}))


def save_multi_period_payment_state(file_path, payment_state):
    """Persist normalized multi-period payment state atomically."""
    save_json(file_path, migrate_payment_state(payment_state))


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
        "public_reminder_groups_sent": {},
        "payment_admin_followups_sent": {},
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
        "personal_reminders_sent": payment_state.get("personal_reminders_sent", {})
        if isinstance(payment_state.get("personal_reminders_sent", {}), dict)
        else {},
        "public_reminder_groups_sent": payment_state.get(
            "public_reminder_groups_sent",
            {},
        )
        if isinstance(payment_state.get("public_reminder_groups_sent", {}), dict)
        else {},
        "payment_admin_followups_sent": payment_state.get(
            "payment_admin_followups_sent",
            {},
        )
        if isinstance(payment_state.get("payment_admin_followups_sent", {}), dict)
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


def format_personal_payment_reminder(member_name, period, stage):
    """Build a private payment reminder for one unpaid member."""
    month_label = format_payment_period(period)
    greeting = f"Hola, {member_name}.\n\n"
    if stage == "overdue_5d":
        return (
            f"{greeting}💳 Tu pago de membresía de {month_label} todavía aparece "
            "como pendiente. Por favor ponte al corriente cuando puedas."
        )
    return (
        f"{greeting}💳 El periodo de membresía de {month_label} comenzó hoy.\n"
        "Por favor realiza tu pago cuando te sea posible. Si ya pagaste, puedes "
        "ignorar este mensaje."
    )


def format_payment_admin_followup(period, stage):
    """Build a private payment-review follow-up for the payment administrator."""
    month_label = format_payment_period(period)
    return (
        "🔔 Revisión de pagos de membresía\n\n"
        f"El recordatorio de {month_label} ({stage}) ya fue enviado.\n"
        "¿Puedes revisar quién ya pagó y actualizar el estado de cada miembro?\n"
        "Usa /payments para abrir la revisión."
    )


def payment_admin_followup_keyboard():
    """Build the button that opens the payment review interface."""
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📋 Revisar pagos", callback_data="payment_review")]
    ])


def _payment_admin_followups_due(payment_state, now, delay_hours):
    """Return public reminder keys whose administrator follow-up is due."""
    current_time = now or datetime.now(timezone.utc)
    if current_time.tzinfo is None:
        current_time = current_time.replace(tzinfo=timezone.utc)
    current_time = current_time.astimezone(timezone.utc)

    public_reminders = payment_state.get("public_reminders_sent", {})
    followups_sent = payment_state.get("payment_admin_followups_sent", {})
    if not isinstance(public_reminders, dict):
        return []
    if not isinstance(followups_sent, dict):
        followups_sent = {}

    delay = timedelta(hours=max(0, delay_hours))
    due = []
    for reminder_key, sent_at in public_reminders.items():
        if reminder_key in followups_sent or not isinstance(sent_at, str):
            continue
        try:
            sent_time = datetime.fromisoformat(sent_at.replace("Z", "+00:00"))
        except ValueError:
            logger.warning(
                "Skipping payment follow-up with invalid reminder timestamp: %s",
                reminder_key,
            )
            continue
        if sent_time.tzinfo is None:
            sent_time = sent_time.replace(tzinfo=timezone.utc)
        if current_time < sent_time.astimezone(timezone.utc) + delay:
            continue
        try:
            period, stage = str(reminder_key).split(":", 1)
        except ValueError:
            logger.warning("Skipping malformed payment reminder key: %s", reminder_key)
            continue
        due.append((str(reminder_key), period, stage))

    return due


async def send_payment_admin_followup(context, payment_admin_id, period, stage):
    """Send one private payment-review follow-up to the payment administrator."""
    try:
        await context.bot.send_message(
            chat_id=payment_admin_id,
            text=format_payment_admin_followup(period, stage),
            reply_markup=payment_admin_followup_keyboard(),
        )
    except Exception:
        logger.exception(
            "Failed to send payment follow-up for %s:%s.",
            period,
            stage,
        )
        return False
    return True


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


async def send_personal_payment_reminders(
    context,
    payment_state,
    members,
    notification_preferences,
    period,
    stage,
    recipients=None,
):
    """Send one private payment reminder to each eligible unpaid member."""
    if recipients is None:
        recipients = get_unpaid_member_recipients(
            payment_state,
            members,
            notification_preferences,
        )
    result = {"sent": 0, "failures": 0}
    for member_id, telegram_id in recipients:
        member_name = str(members[member_id].get("name", ""))
        try:
            await context.bot.send_message(
                chat_id=telegram_id,
                text=format_personal_payment_reminder(member_name, period, stage),
            )
            result["sent"] += 1
            result.setdefault("sent_member_ids", []).append(member_id)
        except Exception:
            result["failures"] += 1
            logger.exception(
                "Failed to send personal payment reminder to member %s.",
                member_id,
            )
    return result


async def check_payment_reminders(context, config, state, now=None):
    """Send due public and private payment reminders with duplicate protection."""
    if not config.get("ENABLE_PAYMENT_REMINDERS", True):
        return

    current_time = now or datetime.now(PAYMENT_TIMEZONE)
    if current_time.tzinfo is None:
        current_time = current_time.replace(tzinfo=PAYMENT_TIMEZONE)
    stage_info = get_payment_reminder_stage(
        current_time,
        config.get("PAYMENT_REMINDER_HOUR", 9),
    )
    if not stage_info:
        return

    period, stage = stage_info
    payment_state = state.setdefault("payment_state", {})
    state_changed = False
    public_error = None

    configured_group_id = config.get("MEMBERS_GROUP_ID", 0)
    if "group_notification_categories" in state:
        payment_group_ids = get_group_targets(state, "payments")
    else:
        try:
            payment_group_ids = {int(configured_group_id)} if configured_group_id else set()
        except (TypeError, ValueError):
            payment_group_ids = set()

    public_reminders = payment_state.setdefault("public_reminders_sent", {})
    public_group_reminders = payment_state.setdefault(
        "public_reminder_groups_sent",
        {},
    )
    if not isinstance(public_group_reminders, dict):
        public_group_reminders = {}
        payment_state["public_reminder_groups_sent"] = public_group_reminders
    public_key = f"{period}:{stage}"
    if payment_group_ids and public_key not in public_reminders:
        sent_group_ids = {
            group_id
            for group_id in public_group_reminders.get(public_key, [])
            if group_id in payment_group_ids
        }
        for group_id in sorted(payment_group_ids):
            if group_id in sent_group_ids:
                continue
            try:
                await context.bot.send_message(
                    chat_id=group_id,
                    text=format_payment_reminder(period, stage),
                )
                sent_group_ids.add(group_id)
                state_changed = True
            except Exception as error:
                public_error = error
                logger.exception(
                    "Failed to send public payment reminder to group %s for %s.",
                    group_id,
                    period,
                )

        public_group_reminders[public_key] = sorted(sent_group_ids)
        if sent_group_ids == payment_group_ids:
            public_reminders[public_key] = current_time.astimezone(
                timezone.utc
            ).isoformat()
            state_changed = True

    if stage in {"first_day", "overdue_5d"}:
        personal_reminders = payment_state.setdefault("personal_reminders_sent", {})
        members = state.get("members", {})
        notification_preferences = state.get("notification_preferences")
        pending_recipients = [
            (member_id, telegram_id)
            for member_id, telegram_id in get_unpaid_member_recipients(
                payment_state,
                members,
                notification_preferences,
            )
            if f"{period}:{stage}:{member_id}" not in personal_reminders
        ]
        if pending_recipients:
            result = await send_personal_payment_reminders(
                context,
                payment_state,
                members,
                notification_preferences,
                period,
                stage,
                recipients=pending_recipients,
            )
            for member_id in result.get("sent_member_ids", []):
                personal_reminders[f"{period}:{stage}:{member_id}"] = datetime.now(
                    timezone.utc
                ).isoformat()
                state_changed = True

    payment_admin_id = _normalize_user_id(config.get("PAYMENT_ADMIN_ID"))
    if payment_admin_id is not None:
        followups = payment_state.setdefault("payment_admin_followups_sent", {})
        if not isinstance(followups, dict):
            followups = {}
            payment_state["payment_admin_followups_sent"] = followups
        delay_hours = config.get("PAYMENT_ADMIN_FOLLOWUP_DELAY_HOURS", 2)
        try:
            delay_hours = int(delay_hours)
        except (TypeError, ValueError):
            delay_hours = 2
        for reminder_key, followup_period, followup_stage in (
            _payment_admin_followups_due(payment_state, current_time, delay_hours)
        ):
            sent = await send_payment_admin_followup(
                context,
                payment_admin_id,
                followup_period,
                followup_stage,
            )
            if sent:
                followups[reminder_key] = current_time.astimezone(
                    timezone.utc
                ).isoformat()
                state_changed = True

    if state_changed:
        save_payment_state(config["PAYMENT_STATE_FILE"], payment_state)
    if public_error:
        raise public_error


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
