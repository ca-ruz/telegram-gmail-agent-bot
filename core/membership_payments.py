"""Authorization and roster helpers for the membership payment workflow."""

import logging
import re
from datetime import datetime, timezone
from datetime import timedelta
from zoneinfo import ZoneInfo

from telegram import InlineKeyboardButton, InlineKeyboardMarkup

from core.group_notification_routing import get_group_targets
from core.membership_exchange import (
    MxnBtcRateProvider,
    membership_price_mxn,
    mxn_to_msat,
)
from core.nwc_client import NwcInvoiceClient, is_valid_payment_preimage
from core.notification_preferences import is_notification_enabled
from core.membership_pricing import normalize_membership_plan, normalize_membership_quantity
from core.payment_qr import build_invoice_qr
from core.payment_delivery_policy import resolve_payment_delivery_policy
from core.payment_test_state import (
    PaymentTestInvoiceConflict,
    get_active_payment_test_invoice,
    get_archived_payment_test_invoice,
    save_payment_test_state,
    update_archived_payment_test_invoice,
    update_payment_test_invoice,
)
from tools.local.data_manager import load_json, save_json


PRIMARY_ADMIN_ROLE = "primary_admin"
PAYMENT_ADMIN_ROLE = "payment_admin"
PAYMENT_STATE_VERSION = 2
PAYMENT_INVOICE_STATES = frozenset({
    "pending",
    "settled",
    "expired",
    "failed",
    "unmatched",
})
logger = logging.getLogger(__name__)
PAYMENT_TIMEZONE = ZoneInfo("America/Mexico_City")
PAYMENT_MONTHS = (
    "enero", "febrero", "marzo", "abril", "mayo", "junio",
    "julio", "agosto", "septiembre", "octubre", "noviembre", "diciembre",
)
# The first day has its own public announcement. After the five-day grace
# period, these dates provide one, two, three, and four public reminders per
# month week respectively.
PAYMENT_PUBLIC_REMINDER_DAYS = {
    6: "week_1_1",
    8: "week_2_1",
    11: "week_2_2",
    15: "week_3_1",
    17: "week_3_2",
    19: "week_3_3",
    22: "week_4_1",
    24: "week_4_2",
    26: "week_4_3",
    28: "week_4_4",
}


class PaymentInvoiceConflict(ValueError):
    """Raised when a new invoice would replace an active invoice incorrectly."""


def _invoice_field(invoice, name, default=None):
    """Read an invoice field from a mapping or an invoice data object."""
    if isinstance(invoice, dict):
        return invoice.get(name, default)
    return getattr(invoice, name, default)


def _normalize_invoice_integer(value):
    """Return a non-negative integer invoice field or ``None``."""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, str):
        value = value.removesuffix("msat")
    try:
        normalized = int(value)
    except (TypeError, ValueError):
        return None
    return normalized if normalized >= 0 else None


def _normalize_settlement_notifications(raw_notifications):
    """Normalize durable private notification delivery state for an invoice."""
    if not isinstance(raw_notifications, dict):
        return None

    try:
        member_chat_id = int(raw_notifications.get("member_chat_id") or 0)
    except (TypeError, ValueError):
        member_chat_id = 0
    member_chat_id = member_chat_id if member_chat_id > 0 else None

    admin_chat_ids = []
    raw_admin_ids = raw_notifications.get("admin_chat_ids", [])
    if isinstance(raw_admin_ids, list):
        for raw_admin_id in raw_admin_ids:
            try:
                admin_chat_id = int(raw_admin_id)
            except (TypeError, ValueError):
                continue
            if admin_chat_id > 0 and admin_chat_id not in admin_chat_ids:
                admin_chat_ids.append(admin_chat_id)

    raw_admin_sent_at = raw_notifications.get("admin_sent_at", {})
    admin_sent_at = {}
    if isinstance(raw_admin_sent_at, dict):
        for admin_chat_id in admin_chat_ids:
            sent_at = raw_admin_sent_at.get(str(admin_chat_id))
            admin_sent_at[str(admin_chat_id)] = (
                sent_at if isinstance(sent_at, str) and sent_at else None
            )

    member_sent_at = raw_notifications.get("member_sent_at")
    return {
        "period": str(raw_notifications.get("period", "")),
        "member_id": str(raw_notifications.get("member_id", "")),
        "member_name": str(raw_notifications.get("member_name", "")),
        "member_chat_id": member_chat_id,
        "member_sent_at": (
            member_sent_at
            if isinstance(member_sent_at, str) and member_sent_at
            else None
        ),
        "admin_chat_ids": admin_chat_ids,
        "admin_sent_at": admin_sent_at,
        "amount_mxn": (
            str(raw_notifications["amount_mxn"])
            if raw_notifications.get("amount_mxn") is not None
            else None
        ),
        "amount_msat": _normalize_invoice_integer(
            raw_notifications.get("amount_msat")
        ),
    }


def _normalize_payment_invoice(raw_invoice):
    """Normalize one persisted invoice record without exposing credentials."""
    if not isinstance(raw_invoice, dict):
        return None

    invoice = raw_invoice.get("invoice")
    payment_hash = raw_invoice.get("payment_hash")
    amount_msat = _normalize_invoice_integer(raw_invoice.get("amount_msat"))
    if not invoice or not payment_hash or amount_msat is None or amount_msat <= 0:
        return None

    state = str(raw_invoice.get("state", "pending")).lower()
    if state not in PAYMENT_INVOICE_STATES:
        state = "pending"

    created_at = raw_invoice.get("created_at")
    if not isinstance(created_at, str) or not created_at:
        created_at = None
    settled_at = raw_invoice.get("settled_at")
    if not isinstance(settled_at, str) or not settled_at:
        settled_at = None
    last_checked_at = raw_invoice.get("last_checked_at")
    if not isinstance(last_checked_at, str) or not last_checked_at:
        last_checked_at = None
    pricing_snapshot = raw_invoice.get("pricing_snapshot")
    if not isinstance(pricing_snapshot, dict):
        pricing_snapshot = None

    return {
        "invoice": str(invoice),
        "payment_hash": str(payment_hash),
        "amount_msat": amount_msat,
        "created_at": created_at,
        "expires_at": _normalize_invoice_integer(raw_invoice.get("expires_at")),
        "state": state,
        "expiry_confirmed": raw_invoice.get("expiry_confirmed") is True,
        "settled_at": settled_at,
        "last_checked_at": last_checked_at,
        "pricing_snapshot": pricing_snapshot,
        "settlement_notifications": _normalize_settlement_notifications(
            raw_invoice.get("settlement_notifications")
        ),
    }


def _normalize_payment_invoices(raw_invoices):
    """Normalize the invoice history mapping keyed by payment hash."""
    if not isinstance(raw_invoices, dict):
        return {}

    invoices = {}
    for payment_hash, raw_invoice in raw_invoices.items():
        invoice = _normalize_payment_invoice(raw_invoice)
        if invoice is None or str(payment_hash) != invoice["payment_hash"]:
            continue
        invoices[invoice["payment_hash"]] = invoice
    return invoices


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


def next_billing_period(period=None):
    """Return the billing period immediately after the supplied period."""
    period_key = _normalize_period_key(period) if period else current_billing_period()
    if period_key is None:
        period_key = current_billing_period()
    current_date = datetime.strptime(period_key, "%Y-%m")
    next_month = (current_date.replace(day=28) + timedelta(days=4)).replace(day=1)
    return next_month.strftime("%Y-%m")


def previous_billing_period(period=None):
    """Return the billing period immediately before the supplied period."""
    period_key = _normalize_period_key(period) if period else current_billing_period()
    if period_key is None:
        period_key = current_billing_period()
    current_date = datetime.strptime(period_key, "%Y-%m")
    previous_month = current_date.replace(day=1) - timedelta(days=1)
    return previous_month.strftime("%Y-%m")


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
        if "payment_invoices" in record:
            record["payment_invoices"] = _normalize_payment_invoices(
                record.get("payment_invoices")
            )
        if "active_invoice_payment_hash" in record:
            active_hash = record.get("active_invoice_payment_hash")
            record["active_invoice_payment_hash"] = (
                str(active_hash) if active_hash else None
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


def synchronize_multi_period_payment_state(payment_state, members, periods):
    """Ensure requested periods exist and include newly active members."""
    normalized_state = migrate_payment_state(payment_state)
    requested_periods = {
        normalized_period
        for period in periods or []
        if (normalized_period := _normalize_period_key(period)) is not None
    }

    for period in requested_periods:
        if period not in normalized_state["periods"]:
            normalized_state["periods"][period] = build_payment_period_state(members)
            continue

        period_state = normalized_state["periods"][period]
        period_members = period_state.setdefault("members", {})
        for member_id, member in members.items():
            member_key = str(member_id)
            if not isinstance(member, dict) or not member.get("active", True):
                continue
            if member_key not in period_members:
                period_members[member_key] = {
                    "status": "unpaid",
                    "paid_at": None,
                    "reminders_sent": [],
                }

    return normalized_state


def load_multi_period_payment_state(file_path):
    """Load and migrate the versioned multi-period payment state."""
    return migrate_payment_state(load_json(file_path, {}))


def save_multi_period_payment_state(file_path, payment_state, *, raise_on_error=False):
    """Persist payment state atomically, optionally surfacing write failures."""
    save_json(
        file_path,
        migrate_payment_state(payment_state),
        raise_on_error=raise_on_error,
    )


def _timestamp_string(value=None):
    """Return a UTC ISO timestamp for a datetime or timestamp string."""
    if isinstance(value, datetime):
        timestamp = value
        if timestamp.tzinfo is None:
            timestamp = timestamp.replace(tzinfo=timezone.utc)
        return timestamp.astimezone(timezone.utc).isoformat()
    if isinstance(value, str) and value:
        return value
    return datetime.now(timezone.utc).isoformat()


def build_payment_invoice_record(
    invoice,
    created_at=None,
    pricing_snapshot=None,
    expiry_seconds=None,
):
    """Build a normalized persisted record from an NWC invoice response."""
    invoice_value = _invoice_field(invoice, "invoice")
    payment_hash = _invoice_field(invoice, "payment_hash")
    amount_msat = _invoice_field(invoice, "amount_msat")
    if amount_msat is None:
        amount_msat = _invoice_field(invoice, "amount")
    amount_msat = _normalize_invoice_integer(amount_msat)
    if not invoice_value or not payment_hash or amount_msat is None or amount_msat <= 0:
        raise ValueError("Invoice must include an invoice, payment hash, and amount.")

    expires_at = _normalize_invoice_integer(_invoice_field(invoice, "expires_at"))
    if expires_at is None and expiry_seconds:
        created_timestamp = _timestamp_string(created_at)
        try:
            expires_at = int(
                datetime.fromisoformat(created_timestamp).timestamp()
            ) + int(expiry_seconds)
        except (TypeError, ValueError, OverflowError, OSError):
            expires_at = None
    return {
        "invoice": str(invoice_value),
        "payment_hash": str(payment_hash),
        "amount_msat": amount_msat,
        "created_at": _timestamp_string(created_at),
        "expires_at": expires_at,
        "state": "pending",
        "expiry_confirmed": False,
        "settled_at": None,
        "last_checked_at": None,
        "pricing_snapshot": pricing_snapshot
        if isinstance(pricing_snapshot, dict)
        else None,
    }


def _invoice_needs_reconciliation(invoice):
    """Keep uncertain invoices, including legacy local expiries, in scope."""
    return invoice["state"] in {"pending", "unmatched", "failed"} or (
        invoice["state"] == "expired" and not invoice["expiry_confirmed"]
    )


def get_active_payment_invoice(payment_state, period, member_id):
    """Return a tracked invoice that still needs settlement reconciliation."""
    normalized_state = migrate_payment_state(payment_state)
    period_key = _normalize_period_key(period)
    if period_key is None:
        return None
    period_state = normalized_state["periods"].get(period_key, {})
    member_record = period_state.get("members", {}).get(str(member_id), {})
    if not isinstance(member_record, dict):
        return None
    payment_hash = member_record.get("active_invoice_payment_hash")
    invoices = _normalize_payment_invoices(member_record.get("payment_invoices"))
    active_invoice = invoices.get(str(payment_hash)) if payment_hash else None
    if active_invoice and _invoice_needs_reconciliation(active_invoice):
        return active_invoice
    return next(
        (invoice for invoice in invoices.values() if _invoice_needs_reconciliation(invoice)),
        None,
    )


def record_payment_invoice(
    payment_state,
    period,
    member_id,
    invoice,
    created_at=None,
    pricing_snapshot=None,
    expiry_seconds=None,
):
    """Attach one invoice to a member-period while preventing active duplicates."""
    normalized_state = migrate_payment_state(payment_state)
    period_key = _normalize_period_key(period)
    if period_key is None:
        raise ValueError("A valid billing period is required.")

    period_state = normalized_state["periods"].get(period_key)
    if not isinstance(period_state, dict):
        raise KeyError(f"Payment period does not exist: {period_key}")
    member_key = str(member_id)
    member_record = period_state.get("members", {}).get(member_key)
    if not isinstance(member_record, dict):
        raise KeyError(f"Payment member does not exist: {member_key}")

    invoice_record = build_payment_invoice_record(
        invoice,
        created_at,
        pricing_snapshot=pricing_snapshot,
        expiry_seconds=expiry_seconds,
    )
    payment_hash = invoice_record["payment_hash"]
    invoices = _normalize_payment_invoices(member_record.get("payment_invoices"))
    if payment_hash in invoices:
        member_record["payment_invoices"] = invoices
        member_record["active_invoice_payment_hash"] = payment_hash
        return normalized_state

    if member_record.get("status") == "paid":
        raise PaymentInvoiceConflict(
            f"Cannot create an invoice for an already paid member: {member_key}"
        )

    if any(_invoice_needs_reconciliation(existing) for existing in invoices.values()):
        raise PaymentInvoiceConflict(
            f"An unresolved invoice already exists for member-period: {member_key}"
        )

    invoices[payment_hash] = invoice_record
    member_record["payment_invoices"] = invoices
    member_record["active_invoice_payment_hash"] = payment_hash
    return normalized_state


def update_payment_invoice(
    payment_state,
    period,
    member_id,
    payment_hash,
    *,
    state=None,
    expiry_confirmed=None,
    settled_at=None,
    last_checked_at=None,
):
    """Update invoice metadata while preserving its history and member status."""
    normalized_state = migrate_payment_state(payment_state)
    period_key = _normalize_period_key(period)
    if period_key is None:
        raise ValueError("A valid billing period is required.")
    member_record = (
        normalized_state["periods"].get(period_key, {})
        .get("members", {})
        .get(str(member_id))
    )
    if not isinstance(member_record, dict):
        raise KeyError(f"Payment member does not exist: {member_id}")

    invoices = _normalize_payment_invoices(member_record.get("payment_invoices"))
    payment_hash = str(payment_hash)
    invoice_record = invoices.get(payment_hash)
    if invoice_record is None:
        raise KeyError(f"Payment invoice does not exist: {payment_hash}")

    if state is not None:
        normalized_invoice_state = str(state).lower()
        if normalized_invoice_state not in PAYMENT_INVOICE_STATES:
            raise ValueError(f"Unsupported payment invoice state: {state}")
        invoice_record["state"] = normalized_invoice_state
    if expiry_confirmed is not None:
        if invoice_record["state"] != "expired" or expiry_confirmed is not True:
            raise ValueError("Wallet expiry confirmation requires an expired invoice.")
        invoice_record["expiry_confirmed"] = True
    if settled_at is not None:
        invoice_record["settled_at"] = _timestamp_string(settled_at)
    if last_checked_at is not None:
        invoice_record["last_checked_at"] = _timestamp_string(last_checked_at)

    member_record["payment_invoices"] = invoices
    if (
        member_record.get("active_invoice_payment_hash") == payment_hash
        and (
            invoice_record["state"] == "settled"
            or (invoice_record["state"] == "expired" and invoice_record["expiry_confirmed"])
        )
    ):
        member_record["active_invoice_payment_hash"] = None
    return normalized_state


def _payment_invoice_settings(config):
    """Return validated invoice settings, or ``None`` when automation is off."""
    if not config.get("PAYMENT_VERIFICATION_ENABLED", False):
        return None

    connection_uri = str(config.get("PAYMENT_NWC_URI", "")).strip()
    try:
        expiry_seconds = int(config.get("PAYMENT_INVOICE_EXPIRY_SECONDS", 0))
    except (TypeError, ValueError):
        return None
    if not connection_uri or expiry_seconds <= 0:
        return None
    return connection_uri, expiry_seconds


def _member_invoice_pricing(member, membership_plans, config, now=None):
    """Calculate an invoice amount and retain the rate used for that invoice."""
    amount_mxn = membership_price_mxn(member, membership_plans)
    rate_provider = MxnBtcRateProvider(
        config.get("PAYMENT_EXCHANGE_RATE_URL", ""),
        config.get(
            "PAYMENT_EXCHANGE_RATE_CACHE_FILE",
            "data/payment_exchange_rate.json",
        ),
        max_age_seconds=config.get("PAYMENT_EXCHANGE_RATE_MAX_AGE_SECONDS", 900),
        timeout_seconds=config.get("PAYMENT_EXCHANGE_RATE_TIMEOUT_SECONDS", 10),
    )
    rate = rate_provider.get_rate(now)
    amount_msat = mxn_to_msat(amount_mxn, rate)
    return amount_msat, {
        "plan": str(member.get("membership_plan", "")),
        "quantity": member.get("membership_quantity", 1),
        "amount_mxn": str(amount_mxn),
        "mxn_per_btc": str(rate),
    }


def _invoice_is_expired(invoice, now=None):
    """Return whether an invoice has passed its persisted expiry timestamp."""
    expires_at = _normalize_invoice_integer(_invoice_field(invoice, "expires_at"))
    if expires_at is None:
        return False
    current_time = now or datetime.now(timezone.utc)
    if current_time.tzinfo is None:
        current_time = current_time.replace(tzinfo=timezone.utc)
    return expires_at <= int(current_time.astimezone(timezone.utc).timestamp())


def _replace_payment_member_record(payment_state, normalized_state, period, member_id):
    """Apply one normalized member record without invalidating live state references."""
    period_key = _normalize_period_key(period)
    current_member = payment_state["periods"][period_key]["members"][
        str(member_id)
    ]
    normalized_member = normalized_state["periods"][period_key]["members"][
        str(member_id)
    ]
    current_member.clear()
    current_member.update(normalized_member)
    return current_member


async def get_or_create_payment_invoice(
    payment_state,
    period,
    member_id,
    config,
    now=None,
    member=None,
    membership_plans=None,
    expected_pricing_snapshot=None,
):
    """Return a reusable invoice, creating and persisting one when necessary."""
    settings = _payment_invoice_settings(config)
    if settings is None:
        return None, False

    active_invoice = get_active_payment_invoice(payment_state, period, member_id)
    if active_invoice:
        if active_invoice["state"] != "pending" or _invoice_is_expired(
            active_invoice, now
        ):
            return None, False
        return active_invoice, False

    connection_uri, expiry_seconds = settings
    if not isinstance(member, dict) or not isinstance(membership_plans, dict):
        logger.warning(
            "Could not price membership invoice for %s in %s: missing member pricing data.",
            member_id,
            period,
        )
        return None, False
    try:
        amount_msat, pricing_snapshot = _member_invoice_pricing(
            member,
            membership_plans,
            config,
            now=now,
        )
        if (
            expected_pricing_snapshot is not None
            and pricing_snapshot != expected_pricing_snapshot
        ):
            return None, False
        invoice = await NwcInvoiceClient(connection_uri).make_invoice(
            amount_msat=amount_msat,
            description=(
                f"{str(member.get('name', 'Miembro')).strip()} - "
                f"membresía {format_payment_period(period)}"
            ),
            expiry_seconds=expiry_seconds,
        )
        returned_amount = _invoice_field(invoice, "amount_msat")
        if returned_amount is None:
            returned_amount = _invoice_field(invoice, "amount")
        if _normalize_invoice_integer(returned_amount) != amount_msat:
            raise ValueError("NWC returned an invoice with an unexpected amount.")
        normalized_state = record_payment_invoice(
            payment_state,
            period,
            member_id,
            invoice,
            created_at=now,
            pricing_snapshot=pricing_snapshot,
            expiry_seconds=expiry_seconds,
        )
        save_multi_period_payment_state(
            config["PAYMENT_STATE_FILE"],
            normalized_state,
            raise_on_error=True,
        )
    except Exception:
        logger.warning(
            "Could not create a membership payment invoice for %s in %s.",
            member_id,
            period,
        )
        return None, False

    _replace_payment_member_record(
        payment_state,
        normalized_state,
        period,
        member_id,
    )
    return get_active_payment_invoice(payment_state, period, member_id), True


def quote_member_payment_invoice(member, membership_plans, config, now=None):
    """Return the exact amount and pricing snapshot used for an invoice quote."""
    return _member_invoice_pricing(member, membership_plans, config, now=now)


def _invoice_status_timestamp(value, fallback=None):
    """Convert an NWC Unix timestamp to the persisted UTC timestamp format."""
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        try:
            return datetime.fromtimestamp(value, timezone.utc).isoformat()
        except (OverflowError, OSError, ValueError):
            pass
    if fallback is None:
        return None
    return _timestamp_string(fallback)


def _normalized_invoice_status(status):
    """Map an NWC status to the persisted invoice-state vocabulary."""
    state = str(getattr(status, "state", "unmatched")).lower()
    return state if state in PAYMENT_INVOICE_STATES else "unmatched"


def _invoice_status_hash_matches(status, payment_hash):
    """Return whether an NWC status identifies the tracked payment hash."""
    returned_hash = str(getattr(status, "payment_hash", ""))
    return returned_hash == str(payment_hash)


def _invoice_status_amount_matches(status, amount_msat):
    """Return whether an NWC status reports the expected invoice amount."""
    returned_amount = _normalize_invoice_integer(
        getattr(status, "amount_msat", None)
    )
    expected_amount = _normalize_invoice_integer(amount_msat)
    return returned_amount is not None and returned_amount == expected_amount


def _invoice_status_matches(status, payment_hash, amount_msat):
    """Return whether an NWC status matches both invoice identity and amount."""
    return _invoice_status_hash_matches(
        status, payment_hash
    ) and _invoice_status_amount_matches(status, amount_msat)


def _invoice_status_has_settlement_proof(status, payment_hash):
    """Return whether a settled response proves ownership of the payment hash."""
    return is_valid_payment_preimage(
        str(payment_hash),
        getattr(status, "preimage", None),
    )


def _build_settlement_notification_record(
    period,
    member_id,
    member,
    invoice,
    config,
):
    """Capture private notification recipients and settled invoice details."""
    admin_chat_ids = []
    for admin_id in (
        config.get("ADMIN_ID"),
        config.get("PAYMENT_ADMIN_ID"),
    ):
        normalized_admin_id = _normalize_user_id(admin_id)
        if normalized_admin_id and normalized_admin_id not in admin_chat_ids:
            admin_chat_ids.append(normalized_admin_id)

    pricing_snapshot = invoice.get("pricing_snapshot")
    amount_mxn = (
        pricing_snapshot.get("amount_mxn")
        if isinstance(pricing_snapshot, dict)
        else None
    )
    return _normalize_settlement_notifications({
        "period": period,
        "member_id": member_id,
        "member_name": str(member.get("name", member_id)),
        "member_chat_id": _normalize_user_id(member.get("telegram_id")),
        "member_sent_at": None,
        "admin_chat_ids": admin_chat_ids,
        "admin_sent_at": {
            str(admin_chat_id): None for admin_chat_id in admin_chat_ids
        },
        "amount_mxn": amount_mxn,
        "amount_msat": invoice.get("amount_msat"),
    })


def _verified_test_invoice_state(status, invoice):
    """Require exact invoice identity and proof before trusting settlement."""
    payment_hash = invoice["payment_hash"]
    if not _invoice_status_matches(status, payment_hash, invoice["amount_msat"]):
        return "unmatched"
    status_state = _normalized_invoice_status(status)
    if status_state == "settled" and not _invoice_status_has_settlement_proof(
        status, payment_hash
    ):
        logger.warning("NWC reported payment-test settlement without valid proof.")
        return "unmatched"
    return status_state


async def recheck_archived_payment_test_invoice(config, state, test_id, now=None):
    """Verify one archived test on demand without touching active or member state."""
    settings = _payment_invoice_settings(config)
    if settings is None:
        raise ValueError("Payment verification is not configured.")
    test_state = state.setdefault("payment_test_state", {})
    archived = get_archived_payment_test_invoice(test_state, test_id)
    if archived is None:
        raise KeyError("Archived payment test does not exist.")
    if archived["state"] == "settled":
        return "already_settled"

    checked_at = now or datetime.now(timezone.utc)
    if checked_at.tzinfo is None:
        checked_at = checked_at.replace(tzinfo=timezone.utc)
    checked_at = checked_at.astimezone(timezone.utc)
    status = await NwcInvoiceClient(settings[0]).lookup_invoice(
        payment_hash=archived["payment_hash"]
    )
    result = _verified_test_invoice_state(status, archived)
    if result != "settled":
        return result

    # Re-read after the network call so a concurrent active test is preserved.
    current_state = state["payment_test_state"]
    current = get_archived_payment_test_invoice(current_state, test_id)
    if current is None or current["payment_hash"] != archived["payment_hash"]:
        raise PaymentTestInvoiceConflict("Archived payment test changed during lookup.")
    if current["state"] == "settled":
        return "already_settled"
    normalized_state = update_archived_payment_test_invoice(
        current_state,
        test_id,
        archived["payment_hash"],
        settled_at=_invoice_status_timestamp(getattr(status, "settled_at", None)),
        last_checked_at=checked_at,
    )
    state["payment_test_state"] = normalized_state
    save_payment_test_state(config["PAYMENT_TEST_STATE_FILE"], normalized_state)
    return "settled"


async def check_payment_invoice_status(config, state, now=None):
    """Poll pending invoices and mark member periods paid after settlement."""
    settings = _payment_invoice_settings(config)
    if settings is None:
        return {"checked": 0, "settled": 0, "failed": 0}

    payment_state = state.setdefault("payment_state", {})
    if not isinstance(payment_state.get("periods"), dict):
        return {"checked": 0, "settled": 0, "failed": 0}

    connection_uri, _expiry_seconds = settings
    client = NwcInvoiceClient(connection_uri)
    checked_at = now or datetime.now(timezone.utc)
    if checked_at.tzinfo is None:
        checked_at = checked_at.replace(tzinfo=timezone.utc)
    checked_at = checked_at.astimezone(timezone.utc)
    result = {"checked": 0, "settled": 0, "failed": 0}
    state_changed = False

    for period, period_state in payment_state.get("periods", {}).items():
        members = period_state.get("members", {})
        if not isinstance(members, dict):
            continue
        for member_id, member_record in members.items():
            if not isinstance(member_record, dict):
                continue
            invoices = _normalize_payment_invoices(
                member_record.get("payment_invoices")
            )
            reconcilable_hashes = [
                payment_hash
                for payment_hash, invoice in invoices.items()
                if _invoice_needs_reconciliation(invoice)
            ]
            for payment_hash in reconcilable_hashes:
                result["checked"] += 1
                invoice = invoices[payment_hash]
                try:
                    status = await client.lookup_invoice(
                        payment_hash=payment_hash,
                    )
                except Exception:
                    result["failed"] += 1
                    logger.warning(
                        "Could not check membership invoice for %s in %s.",
                        member_id,
                        period,
                    )
                    continue

                status_state = _normalized_invoice_status(status)
                hash_matches = _invoice_status_hash_matches(
                    status, payment_hash
                )
                amount_matches = _invoice_status_amount_matches(
                    status, invoice.get("amount_msat")
                )
                if not hash_matches or (
                    status_state != "settled" and not amount_matches
                ):
                    status_state = "unmatched"
                elif status_state == "settled" and not _invoice_status_has_settlement_proof(
                    status,
                    payment_hash,
                ):
                    logger.warning(
                        "NWC reported membership settlement without valid proof; "
                        "keeping the invoice unmatched."
                    )
                    status_state = "unmatched"
                elif status_state == "settled" and not amount_matches:
                    logger.warning(
                        "NWC lookup amount differs for settled membership invoice "
                        "for %s in %s (expected=%s msat, returned=%s msat); "
                        "accepting only after hash and settlement proof validation.",
                        member_id,
                        period,
                        invoice.get("amount_msat"),
                        getattr(status, "amount_msat", None),
                    )
                elif status_state == "failed":
                    status_state = "unmatched"
                if status_state == "pending" and _invoice_is_expired(
                    invoice, checked_at
                ):
                    status_state = "unmatched"
                if status_state == "unmatched" and invoice["state"] != "unmatched":
                    logger.warning(
                        "Membership invoice for %s in %s needs further reconciliation.",
                        member_id,
                        period,
                    )

                settled_at = None
                if status_state == "settled":
                    settled_at = _invoice_status_timestamp(
                        getattr(status, "settled_at", None),
                    )
                try:
                    normalized_state = update_payment_invoice(
                        payment_state,
                        period,
                        member_id,
                        payment_hash,
                        state=status_state,
                        expiry_confirmed=True if status_state == "expired" else None,
                        settled_at=settled_at,
                        last_checked_at=checked_at,
                    )
                except (KeyError, ValueError):
                    result["failed"] += 1
                    logger.warning(
                        "Could not update membership invoice for %s in %s.",
                        member_id,
                        period,
                    )
                    continue

                if status_state == "settled" and invoice["state"] != "settled":
                    settled_member = state.get("members", {}).get(
                        str(member_id), {}
                    )
                    settled_invoice = (
                        normalized_state["periods"][str(period)]["members"][
                            str(member_id)
                        ]["payment_invoices"][payment_hash]
                    )
                    settled_invoice["settlement_notifications"] = (
                        _build_settlement_notification_record(
                            str(period),
                            str(member_id),
                            settled_member if isinstance(settled_member, dict) else {},
                            settled_invoice,
                            config,
                        )
                    )

                member_record = _replace_payment_member_record(
                    payment_state,
                    normalized_state,
                    period,
                    member_id,
                )
                state_changed = True
                if status_state == "settled":
                    if member_record.get("status") != "paid":
                        member_record["status"] = "paid"
                        member_record["paid_at"] = settled_at or _timestamp_string(checked_at)
                    result["settled"] += 1

    if state_changed:
        save_multi_period_payment_state(
            config["PAYMENT_STATE_FILE"],
            payment_state,
        )
    return result


def _settlement_member_message(notification):
    """Format the private payment confirmation sent to the member."""
    return (
        "✅ Confirmamos tu pago de membresía para "
        f"{format_payment_period(notification['period'])}. ¡Gracias!"
    )


def _settlement_admin_message(notification):
    """Format the private settlement alert sent to payment administrators."""
    lines = [
        "✅ Pago de membresía confirmado",
        f"Miembro: {notification['member_name']} "
        f"({notification['member_id']})",
        f"Periodo: {format_payment_period(notification['period'])}",
    ]
    amount_msat = notification.get("amount_msat")
    if amount_msat is not None:
        amount_line = f"Monto: {amount_msat / 1000:,.3f} sats"
        if notification.get("amount_mxn") is not None:
            amount_line = (
                f"Monto: MXN {notification['amount_mxn']} / {amount_line}"
            )
        lines.append(amount_line)
    return "\n".join(lines)


async def deliver_membership_settlement_notifications(context, config, state):
    """Deliver queued private settlement notices and persist each success."""
    result = {"sent": 0, "failed": 0}
    payment_state = state.get("payment_state", {})
    periods = payment_state.get("periods", {})
    if not isinstance(periods, dict):
        return result

    for period, period_state in periods.items():
        members = period_state.get("members", {})
        if not isinstance(members, dict):
            continue
        for member_id, member_record in members.items():
            if not isinstance(member_record, dict):
                continue
            invoices = member_record.get("payment_invoices", {})
            if not isinstance(invoices, dict):
                continue
            for invoice in invoices.values():
                if not isinstance(invoice, dict) or invoice.get("state") != "settled":
                    continue
                if member_record.get("status") != "paid":
                    continue
                notification = _normalize_settlement_notifications(
                    invoice.get("settlement_notifications")
                )
                if notification is None:
                    continue
                invoice["settlement_notifications"] = notification

                recipients = []
                member_chat_id = notification.get("member_chat_id")
                if member_chat_id and not notification.get("member_sent_at"):
                    recipients.append((
                        member_chat_id,
                        _settlement_member_message(notification),
                        "member",
                    ))
                for admin_chat_id in notification["admin_chat_ids"]:
                    recipient_key = str(admin_chat_id)
                    if not notification["admin_sent_at"].get(recipient_key):
                        recipients.append((
                            admin_chat_id,
                            _settlement_admin_message(notification),
                            recipient_key,
                        ))

                for chat_id, message, recipient_key in recipients:
                    try:
                        await context.bot.send_message(
                            chat_id=chat_id,
                            text=message,
                        )
                    except Exception:
                        result["failed"] += 1
                        logger.exception(
                            "Could not deliver membership settlement notice "
                            "to recipient %s for %s in %s.",
                            recipient_key,
                            member_id,
                            period,
                        )
                        continue

                    sent_at = datetime.now(timezone.utc).isoformat()
                    if recipient_key == "member":
                        previous_sent_at = notification["member_sent_at"]
                        notification["member_sent_at"] = sent_at
                    else:
                        previous_sent_at = notification["admin_sent_at"].get(
                            recipient_key
                        )
                        notification["admin_sent_at"][recipient_key] = sent_at
                    result["sent"] += 1
                    try:
                        save_multi_period_payment_state(
                            config["PAYMENT_STATE_FILE"],
                            payment_state,
                            raise_on_error=True,
                        )
                    except Exception:
                        if recipient_key == "member":
                            notification["member_sent_at"] = previous_sent_at
                        else:
                            notification["admin_sent_at"][recipient_key] = (
                                previous_sent_at
                            )
                        result["failed"] += 1
                        logger.exception(
                            "Could not persist membership settlement notice "
                            "delivery state for %s in %s.",
                            member_id,
                            period,
                        )
    return result


async def check_payment_test_invoice_status(context, config, state, now=None):
    """Poll the isolated test invoice without changing production payment state."""
    settings = _payment_invoice_settings(config)
    if settings is None:
        return {"checked": 0, "settled": 0, "failed": 0}

    test_state = state.setdefault("payment_test_state", {})
    active_invoice = get_active_payment_test_invoice(test_state)
    if not active_invoice or active_invoice.get("state") not in {"pending", "unmatched"}:
        return {"checked": 0, "settled": 0, "failed": 0}

    connection_uri, _expiry_seconds = settings
    checked_at = now or datetime.now(timezone.utc)
    if checked_at.tzinfo is None:
        checked_at = checked_at.replace(tzinfo=timezone.utc)
    checked_at = checked_at.astimezone(timezone.utc)
    payment_hash = active_invoice["payment_hash"]
    expires_at = active_invoice.get("expires_at")
    if expires_at is not None and expires_at <= int(checked_at.timestamp()):
        normalized_state = update_payment_test_invoice(
            test_state,
            payment_hash,
            state_value="expired",
            last_checked_at=checked_at,
        )
        state["payment_test_state"] = normalized_state
        save_payment_test_state(config["PAYMENT_TEST_STATE_FILE"], normalized_state)
        return {"checked": 1, "settled": 0, "failed": 0}

    try:
        status = await NwcInvoiceClient(connection_uri).lookup_invoice(
            payment_hash=payment_hash,
        )
    except Exception:
        logger.warning("Could not check isolated payment test invoice.")
        return {"checked": 1, "settled": 0, "failed": 1}

    returned_amount = _normalize_invoice_integer(getattr(status, "amount_msat", None))
    expected_amount = _normalize_invoice_integer(active_invoice.get("amount_msat"))
    logger.info(
        "Payment test lookup: state=%s, settled_at_present=%s, "
        "hash_matches=%s, amount_matches=%s, preimage_valid=%s.",
        getattr(status, "state", "unknown"),
        bool(getattr(status, "settled_at", None)),
        str(getattr(status, "payment_hash", "")) == payment_hash,
        returned_amount is not None and returned_amount == expected_amount,
        _invoice_status_has_settlement_proof(status, payment_hash),
    )

    status_state = _verified_test_invoice_state(status, active_invoice)

    settled_at = None
    if status_state == "settled":
        settled_at = _invoice_status_timestamp(
            getattr(status, "settled_at", None),
        )
    normalized_state = update_payment_test_invoice(
        test_state,
        payment_hash,
        state_value=status_state,
        settled_at=settled_at,
        last_checked_at=checked_at,
    )
    state["payment_test_state"] = normalized_state
    save_payment_test_state(config["PAYMENT_TEST_STATE_FILE"], normalized_state)

    if status_state == "settled":
        admin_id = _normalize_user_id(config.get("ADMIN_ID"))
        if admin_id is not None:
            try:
                await context.bot.send_message(
                    chat_id=admin_id,
                    text=(
                        "✅ La factura de prueba fue pagada correctamente.\n"
                        f"Monto: {active_invoice['amount_sats']} sats\n"
                        f"Hash: {payment_hash}\n\n"
                        "Este resultado no modificó ninguna membresía real."
                    ),
                )
            except Exception:
                logger.exception("Could not notify the primary administrator about payment test.")

    return {
        "checked": 1,
        "settled": 1 if status_state == "settled" else 0,
        "failed": 0,
    }


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


def get_payment_reminder_schedule(now=None, reminder_hour=9):
    """Return the private and public reminder actions due on a local date.

    The first day sends both reminder types. Days two through five send only
    daily private reminders. Public reminders resume on fixed dates after the
    five-day grace period, with increasing weekly frequency through the month.
    """
    current_time = now or datetime.now(PAYMENT_TIMEZONE)
    if current_time.tzinfo is None:
        current_time = current_time.replace(tzinfo=PAYMENT_TIMEZONE)
    local_time = current_time.astimezone(PAYMENT_TIMEZONE)
    if local_time.hour < reminder_hour:
        return None

    period = local_time.strftime("%Y-%m")
    day = local_time.day
    if day == 1:
        return {
            "period": period,
            "personal_stage": "first_day",
            "public_stage": "first_day",
        }

    return {
        "period": period,
        "personal_stage": "daily",
        "public_stage": PAYMENT_PUBLIC_REMINDER_DAYS.get(day),
    }


def get_overlapping_payment_reminder_schedule(
    now=None,
    reminder_hour=9,
    advance_days=3,
    available_periods=None,
):
    """Return upcoming, current, and rollover payment reminders due today.

    The upcoming period receives one advance reminder on the configured lead
    day. On the first day of a new period, the previous period receives one
    rollover reminder so its unpaid balance can be handled alongside the new
    period. Delivery and duplicate handling are applied by the scheduler.
    """
    current_time = now or datetime.now(PAYMENT_TIMEZONE)
    if current_time.tzinfo is None:
        current_time = current_time.replace(tzinfo=PAYMENT_TIMEZONE)
    local_time = current_time.astimezone(PAYMENT_TIMEZONE)
    if local_time.hour < reminder_hour:
        return []

    schedules = []
    current_schedule = get_payment_reminder_schedule(local_time, reminder_hour)
    if current_schedule:
        schedules.append(current_schedule)

    current_period = local_time.strftime("%Y-%m")
    next_period = next_billing_period(current_period)
    next_period_start = datetime.strptime(next_period, "%Y-%m").date()
    if local_time.date() == next_period_start - timedelta(days=max(0, advance_days)):
        schedules.append({
            "period": next_period,
            "personal_stage": "advance",
            "public_stage": "advance",
        })

    previous_period = previous_billing_period(current_period)
    if local_time.day == 1:
        schedules.append({
            "period": previous_period,
            "personal_stage": "overdue_daily",
            "public_stage": "overdue_rollover",
        })
    elif previous_period in {str(period) for period in (available_periods or [])}:
        schedules.append({
            "period": previous_period,
            "personal_stage": "overdue_daily",
            "public_stage": None,
        })

    return schedules


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
    if stage == "advance":
        return (
            f"💳 El periodo de membresía de {month_label} comienza en tres días.\n"
            "Por favor prepara tu pago. Si ya pagaste, ignora este mensaje."
        )
    if stage in {"overdue_5d", "overdue_rollover", "overdue_daily"} \
            or str(stage).startswith("week_"):
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


def _append_payment_invoice(message, period, invoice):
    """Append a plain-text Lightning invoice to a private reminder."""
    invoice_value = _invoice_field(invoice, "invoice") if invoice else None
    if not invoice_value:
        return message
    return (
        f"{message}\n\n⚡ Factura Lightning para {format_payment_period(period)}:\n"
        f"{invoice_value}"
    )


def _format_invoice_amount(amount_msat):
    """Format a positive millisatoshi amount for a user-facing message."""
    normalized_amount = _normalize_invoice_integer(amount_msat)
    if normalized_amount is None:
        return "no disponible"
    whole_sats, remainder_msat = divmod(normalized_amount, 1000)
    if remainder_msat == 0:
        sats_label = str(whole_sats)
    else:
        sats_label = f"{whole_sats}.{remainder_msat:03d}".rstrip("0")
    return f"{sats_label} sats ({normalized_amount} msat)"


def _format_invoice_expiry(expires_at):
    """Format an invoice expiry timestamp in the community timezone."""
    normalized_expiry = _normalize_invoice_integer(expires_at)
    if normalized_expiry is None:
        return "no disponible"
    try:
        expiry_time = datetime.fromtimestamp(
            normalized_expiry,
            timezone.utc,
        ).astimezone(PAYMENT_TIMEZONE)
    except (OverflowError, OSError, ValueError):
        return "no disponible"
    return expiry_time.strftime("%d/%m/%Y %H:%M")


def format_payment_invoice_caption(period, invoice):
    """Build a QR caption with payment details and a copyable invoice."""
    invoice_value = _invoice_field(invoice, "invoice") if invoice else None
    if not invoice_value:
        return None
    return "\n".join([
        f"💳 Pago de membresía: {format_payment_period(period)}",
        f"Monto: {_format_invoice_amount(_invoice_field(invoice, 'amount_msat'))}",
        f"Expira: {_format_invoice_expiry(_invoice_field(invoice, 'expires_at'))}",
        "",
        "Escanea el código QR o copia esta factura Lightning:",
        str(invoice_value),
    ])


def format_personal_payment_invoice_message(member_name, period, stage, invoice):
    """Build one private invoice message for QR delivery or text fallback."""
    reminder = format_personal_payment_reminder(member_name, period, stage)
    caption = format_payment_invoice_caption(period, invoice)
    if not caption:
        return reminder
    return f"{reminder}\n\n{caption}"


def format_personal_payment_reminder(member_name, period, stage, invoice=None):
    """Build a private payment reminder for one unpaid member."""
    month_label = format_payment_period(period)
    greeting = f"Hola, {member_name}.\n\n"
    if stage == "manual_pilot":
        message = (
            f"{greeting}⚡ Factura de membresía de {month_label}.\n"
            "Paga esta factura para completar el periodo indicado."
        )
    elif stage == "overdue_5d":
        message = (
            f"{greeting}💳 Pago de membresía de {month_label}: sigue pendiente.\n"
            "Ponte al corriente cuando puedas."
        )
    elif stage == "daily":
        message = (
            f"{greeting}💳 Pago de membresía de {month_label}: pendiente.\n"
            "Realiza tu pago cuando puedas."
        )
    else:
        message = (
            f"{greeting}💳 El periodo de membresía de {month_label} comenzó hoy.\n"
            "Por favor realiza tu pago cuando te sea posible. Si ya pagaste, puedes "
            "ignorar este mensaje."
        )
    return _append_payment_invoice(message, period, invoice)


def format_combined_payment_reminder(periods):
    """Build one anonymous public reminder for several billing periods."""
    lines = ["💳 Recordatorio de pagos de membresía:"]
    for period, stage in periods:
        month_label = format_payment_period(period)
        if stage == "advance":
            detail = "comienza en tres días"
        elif stage == "first_day":
            detail = "comienza hoy"
        else:
            detail = "tiene pagos pendientes"
        lines.append(f"• {month_label}: {detail}")
    lines.append("Si ya pagaste, ignora este mensaje.")
    return "\n".join(lines)


def format_combined_personal_payment_reminder(
    member_name,
    periods,
    invoices=None,
):
    """Build one private reminder for several unpaid billing periods."""
    invoices = invoices if isinstance(invoices, dict) else {}
    lines = [
        f"Hola, {member_name}.",
        "",
        "💳 Pagos de membresía:",
    ]
    for period, stage in periods:
        month_label = format_payment_period(period)
        if stage == "advance":
            detail = "comienza en tres días"
        elif stage == "first_day":
            detail = "comienza hoy"
        else:
            detail = "pendiente"
        lines.append(f"• {month_label}: {detail}")
    for period, _stage in periods:
        invoice = invoices.get(period)
        invoice_value = _invoice_field(invoice, "invoice") if invoice else None
        if invoice_value:
            lines.extend([
                "",
                f"⚡ Factura Lightning para {format_payment_period(period)}:",
                str(invoice_value),
            ])
    lines.append("Realiza los pagos indicados cuando puedas.")
    return "\n".join(lines)


def format_payment_admin_followup(period, stage):
    """Build a private payment-review follow-up for the payment administrator."""
    month_label = format_payment_period(period)
    if stage == "advance":
        stage_label = "recordatorio anticipado"
    elif stage == "first_day":
        stage_label = "primer día del periodo"
    elif stage == "overdue_rollover":
        stage_label = "periodo anterior pendiente"
    else:
        stage_label = "recordatorio de pagos pendientes"
    return (
        "🔔 Revisión de pagos de membresía\n\n"
        f"El {stage_label} de {month_label} ya fue enviado.\n"
        "¿Puedes revisar quién ya pagó y actualizar el estado de cada miembro?\n"
        "Usa /payments para abrir la revisión."
    )


def payment_admin_followup_keyboard(period=None):
    """Build the button that opens the review for the relevant period."""
    callback_data = "payment_review"
    if period:
        callback_data = f"payment_review:{period}"
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📋 Revisar pagos", callback_data=callback_data)]
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
            reply_markup=payment_admin_followup_keyboard(period),
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


def _personal_reminder_key(period, stage, local_date, member_id):
    """Build a duplicate-protection key for one personal reminder."""
    if stage in {"daily", "overdue_daily"}:
        stage = f"{stage}:{local_date}"
    return f"{period}:{stage}:{member_id}"


def invoice_delivery_key(period, local_date, member_id):
    """Build a same-day deduplication key shared by command and scheduler."""
    return f"{period}:invoice:{local_date}:{member_id}"


def _payment_group_ids(config, state):
    """Return configured groups that receive payment reminders."""
    if not config.get("PAYMENT_GROUP_BROADCASTS_ENABLED", False):
        return set()
    configured_group_id = config.get("MEMBERS_GROUP_ID", 0)
    if "group_notification_categories" in state:
        return get_group_targets(state, "payments")
    try:
        return {int(configured_group_id)} if configured_group_id else set()
    except (TypeError, ValueError):
        return set()


def _payment_broadcasts_enabled(config, policy):
    """Require both full delivery and explicit public-message consent."""
    return policy.allows_broadcasts and config.get(
        "PAYMENT_GROUP_BROADCASTS_ENABLED", False
    )


async def deliver_member_payment_invoice(
    context,
    telegram_id,
    member_name,
    period,
    stage,
    invoice,
):
    """Send one member's invoice using the production QR/text fallback path."""
    message_text = format_personal_payment_invoice_message(
        member_name,
        period,
        stage,
        invoice,
    )
    try:
        send_photo = getattr(context.bot, "send_photo", None)
        if send_photo is None:
            raise RuntimeError("Telegram bot does not support photo delivery")
        await send_photo(
            chat_id=telegram_id,
            photo=build_invoice_qr(_invoice_field(invoice, "invoice")),
            caption=message_text,
        )
        return True
    except Exception:
        logger.exception(
            "Failed to send membership invoice QR to %s for %s; using text fallback.",
            telegram_id,
            period,
        )
        try:
            await context.bot.send_message(
                chat_id=telegram_id,
                text=message_text,
            )
            return True
        except Exception:
            logger.exception(
                "Failed to send membership invoice to %s for %s.",
                telegram_id,
                period,
            )
            return False


async def _check_multi_period_payment_reminders(context, config, state, now, policy):
    """Deliver due reminders for upcoming, current, and rollover periods."""
    payment_state = state.setdefault("payment_state", {})
    available_periods = payment_state.get("periods", {})
    schedules = get_overlapping_payment_reminder_schedule(
        now,
        config.get("PAYMENT_REMINDER_HOUR", 9),
        config.get("PAYMENT_ADVANCE_REMINDER_DAYS", 3),
        available_periods=available_periods,
    )
    if not schedules:
        schedules = []

    period_states = {}
    for schedule in schedules:
        payment_state = ensure_payment_period(
            payment_state,
            state.get("members", {}),
            schedule["period"],
        )
        period_states[schedule["period"]] = payment_state["periods"][
            schedule["period"]
        ]
    state["payment_state"] = payment_state

    state_changed = False
    public_error = None
    payment_group_ids = (
        _payment_group_ids(config, state)
        if _payment_broadcasts_enabled(config, policy)
        else set()
    )
    public_entries = []
    for schedule in schedules:
        public_stage = schedule.get("public_stage")
        if not public_stage:
            continue
        period = schedule["period"]
        period_state = period_states[period]
        public_reminders = period_state.setdefault("public_reminders_sent", {})
        public_group_reminders = period_state.setdefault(
            "public_reminder_groups_sent",
            {},
        )
        public_key = f"{period}:{public_stage}"
        if public_key in public_reminders:
            continue
        public_entries.append({
            "period": period,
            "stage": public_stage,
            "key": public_key,
            "period_state": period_state,
            "group_reminders": public_group_reminders,
        })

    if payment_group_ids and public_entries:
        for group_id in sorted(payment_group_ids):
            pending_entries = []
            for entry in public_entries:
                sent_group_ids = entry["group_reminders"].get(entry["key"], [])
                if not isinstance(sent_group_ids, list):
                    sent_group_ids = []
                if group_id not in sent_group_ids:
                    pending_entries.append(entry)
            if not pending_entries:
                continue

            try:
                await context.bot.send_message(
                    chat_id=group_id,
                    text=format_combined_payment_reminder([
                        (entry["period"], entry["stage"])
                        for entry in pending_entries
                    ]),
                )
            except Exception as error:
                public_error = error
                logger.exception(
                    "Failed to send combined payment reminder to group %s.",
                    group_id,
                )
                continue

            for entry in pending_entries:
                sent_group_ids = entry["group_reminders"].setdefault(
                    entry["key"],
                    [],
                )
                if group_id not in sent_group_ids:
                    sent_group_ids.append(group_id)
                state_changed = True

        for entry in public_entries:
            sent_group_ids = set(entry["group_reminders"].get(entry["key"], []))
            if sent_group_ids == payment_group_ids:
                entry["period_state"]["public_reminders_sent"][entry["key"]] = (
                    now.astimezone(timezone.utc).isoformat()
                )
                state_changed = True

    personal_deliveries = {}
    local_date = now.astimezone(PAYMENT_TIMEZONE).date()
    members = state.get("members", {})
    if policy.mode == "pilot":
        members = {
            member_id: member
            for member_id, member in members.items()
            if policy.allows_member(member_id)
        }
    notification_preferences = state.get("notification_preferences")
    for schedule in schedules:
        personal_stage = schedule.get("personal_stage")
        if not personal_stage:
            continue
        period = schedule["period"]
        period_state = period_states[period]
        personal_reminders = period_state.setdefault(
            "personal_reminders_sent",
            {},
        )
        for member_id, telegram_id in get_unpaid_member_recipients(
            period_state,
            members,
            notification_preferences,
        ):
            reminder_key = _personal_reminder_key(
                period,
                personal_stage,
                local_date,
                member_id,
            )
            invoice_key = invoice_delivery_key(
                period,
                local_date,
                member_id,
            )
            if reminder_key in personal_reminders or invoice_key in personal_reminders:
                continue
            personal_deliveries.setdefault(telegram_id, []).append({
                "member_id": member_id,
                "period": period,
                "stage": personal_stage,
                "key": reminder_key,
                "period_state": period_state,
            })

    for telegram_id, entries in personal_deliveries.items():
        first_member = members.get(entries[0]["member_id"], {})
        member_name = str(first_member.get("name", ""))
        invoices = {}
        for entry in entries:
            invoice, invoice_created = await get_or_create_payment_invoice(
                payment_state,
                entry["period"],
                entry["member_id"],
                config,
                now=now,
                member=members.get(entry["member_id"]),
                membership_plans=state.get("membership_plans", {}),
            )
            if invoice_created:
                state_changed = True
            if invoice is not None:
                invoices[entry["period"]] = invoice

        delivered_keys = set()
        invoice_delivered_keys = set()
        text_only_entries = []
        for entry in entries:
            invoice = invoices.get(entry["period"])
            if invoice is None:
                if get_active_payment_invoice(
                    payment_state, entry["period"], entry["member_id"]
                ) is not None:
                    continue
                text_only_entries.append(entry)
                continue

            delivered = await deliver_member_payment_invoice(
                context,
                telegram_id,
                member_name,
                entry["period"],
                entry["stage"],
                invoice,
            )
            if delivered:
                delivered_keys.add(entry["key"])
                invoice_delivered_keys.add(entry["key"])

        if text_only_entries:
            try:
                await context.bot.send_message(
                    chat_id=telegram_id,
                    text=format_combined_personal_payment_reminder(
                        member_name,
                        [
                            (entry["period"], entry["stage"])
                            for entry in text_only_entries
                        ],
                    ),
                )
            except Exception:
                logger.exception(
                    "Failed to send payment reminder without invoice to %s.",
                    telegram_id,
                )
            else:
                delivered_keys.update(entry["key"] for entry in text_only_entries)

        for entry in entries:
            if entry["key"] in delivered_keys:
                entry["period_state"]["personal_reminders_sent"][entry["key"]] = (
                    now.astimezone(timezone.utc).isoformat()
                )
                if entry["key"] in invoice_delivered_keys:
                    entry["period_state"]["personal_reminders_sent"][
                        invoice_delivery_key(
                            entry["period"],
                            local_date,
                            entry["member_id"],
                        )
                    ] = now.astimezone(timezone.utc).isoformat()
                state_changed = True

    payment_admin_id = _normalize_user_id(config.get("PAYMENT_ADMIN_ID"))
    if payment_admin_id is not None and _payment_broadcasts_enabled(config, policy):
        delay_hours = config.get("PAYMENT_ADMIN_FOLLOWUP_DELAY_HOURS", 2)
        try:
            delay_hours = int(delay_hours)
        except (TypeError, ValueError):
            delay_hours = 2
        for period, period_state in payment_state.get("periods", {}).items():
            followups = period_state.setdefault("payment_admin_followups_sent", {})
            if not isinstance(followups, dict):
                followups = {}
                period_state["payment_admin_followups_sent"] = followups
            for reminder_key, followup_period, followup_stage in (
                _payment_admin_followups_due(period_state, now, delay_hours)
            ):
                sent = await send_payment_admin_followup(
                    context,
                    payment_admin_id,
                    followup_period,
                    followup_stage,
                )
                if sent:
                    followups[reminder_key] = now.astimezone(
                        timezone.utc
                    ).isoformat()
                    state_changed = True

    if state_changed:
        save_multi_period_payment_state(
            config["PAYMENT_STATE_FILE"],
            payment_state,
        )
    if public_error:
        raise public_error


async def check_payment_reminders(context, config, state, now=None):
    """Send due public and private payment reminders with duplicate protection."""
    if not config.get("ENABLE_PAYMENT_REMINDERS", True):
        return
    policy = resolve_payment_delivery_policy(config, state.get("members", {}))
    if policy.mode == "off":
        return

    current_time = now or datetime.now(PAYMENT_TIMEZONE)
    if current_time.tzinfo is None:
        current_time = current_time.replace(tzinfo=PAYMENT_TIMEZONE)
    payment_state = state.setdefault("payment_state", {})
    multi_period_state = isinstance(payment_state.get("periods"), dict)
    if multi_period_state:
        await _check_multi_period_payment_reminders(
            context,
            config,
            state,
            current_time,
            policy,
        )
        return

    reminder_hour = config.get("PAYMENT_REMINDER_HOUR", 9)
    stage_info = get_payment_reminder_stage(current_time, reminder_hour)
    if not stage_info:
        return
    period, public_stage = stage_info
    personal_stage = public_stage if public_stage in {
        "first_day",
        "overdue_5d",
    } else None

    state_changed = False
    public_error = None

    period_state = payment_state

    payment_group_ids = (
        _payment_group_ids(config, state)
        if _payment_broadcasts_enabled(config, policy)
        else set()
    )

    public_reminders = period_state.setdefault("public_reminders_sent", {})
    public_group_reminders = period_state.setdefault(
        "public_reminder_groups_sent",
        {},
    )
    if not isinstance(public_group_reminders, dict):
        public_group_reminders = {}
        period_state["public_reminder_groups_sent"] = public_group_reminders
    public_key = f"{period}:{public_stage}" if public_stage else None
    if public_key and payment_group_ids and public_key not in public_reminders:
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
                    text=format_payment_reminder(period, public_stage),
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

    if personal_stage:
        personal_reminders = period_state.setdefault("personal_reminders_sent", {})
        personal_key_stage = personal_stage
        if personal_stage == "daily":
            personal_key_stage = (
                f"daily:{current_time.astimezone(PAYMENT_TIMEZONE).date()}"
            )
        members = state.get("members", {})
        if policy.mode == "pilot":
            members = {
                member_id: member
                for member_id, member in members.items()
                if policy.allows_member(member_id)
            }
        notification_preferences = state.get("notification_preferences")
        pending_recipients = [
            (member_id, telegram_id)
            for member_id, telegram_id in get_unpaid_member_recipients(
                period_state,
                members,
                notification_preferences,
            )
            if f"{period}:{personal_key_stage}:{member_id}" not in personal_reminders
        ]
        if pending_recipients:
            result = await send_personal_payment_reminders(
                context,
                period_state,
                members,
                notification_preferences,
                period,
                personal_stage,
                recipients=pending_recipients,
            )
            for member_id in result.get("sent_member_ids", []):
                personal_reminders[
                    f"{period}:{personal_key_stage}:{member_id}"
                ] = datetime.now(
                    timezone.utc
                ).isoformat()
                state_changed = True

    payment_admin_id = _normalize_user_id(config.get("PAYMENT_ADMIN_ID"))
    if payment_admin_id is not None and _payment_broadcasts_enabled(config, policy):
        followups = period_state.setdefault("payment_admin_followups_sent", {})
        if not isinstance(followups, dict):
            followups = {}
            period_state["payment_admin_followups_sent"] = followups
        delay_hours = config.get("PAYMENT_ADMIN_FOLLOWUP_DELAY_HOURS", 2)
        try:
            delay_hours = int(delay_hours)
        except (TypeError, ValueError):
            delay_hours = 2
        for reminder_key, followup_period, followup_stage in (
            _payment_admin_followups_due(period_state, current_time, delay_hours)
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
        if multi_period_state:
            save_multi_period_payment_state(
                config["PAYMENT_STATE_FILE"],
                payment_state,
            )
        else:
            save_payment_state(config["PAYMENT_STATE_FILE"], payment_state)
    if public_error:
        raise public_error


def build_member(name, telegram_id, membership_plan="", membership_quantity=1):
    """Build a normalized active member record with optional pricing metadata."""
    try:
        normalized_quantity = normalize_membership_quantity(membership_quantity)
    except ValueError:
        normalized_quantity = 1
    return {
        "name": name,
        "telegram_id": telegram_id,
        "active": True,
        "membership_plan": normalize_membership_plan(membership_plan),
        "membership_quantity": normalized_quantity,
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
