"""Isolated state and validation helpers for low-value payment tests."""

from datetime import datetime, timezone
import logging

from tools.local.data_manager import load_json, save_json

logger = logging.getLogger(__name__)

PAYMENT_TEST_STATE_VERSION = 1
MAX_PAYMENT_TEST_AMOUNT_SATS = 100
PAYMENT_TEST_INVOICE_STATES = frozenset({
    "pending",
    "settled",
    "expired",
    "failed",
    "unmatched",
})


class PaymentTestInvoiceConflict(ValueError):
    """Raised when a pending test invoice already exists."""


def normalize_payment_test_amount_sats(value=100):
    """Return a valid whole-sat test amount between one and 100 sats."""
    try:
        amount_sats = int(str(value).strip())
    except (AttributeError, TypeError, ValueError):
        raise ValueError("Test invoice amount must be a whole number of sats.") from None
    if amount_sats <= 0 or amount_sats > MAX_PAYMENT_TEST_AMOUNT_SATS:
        raise ValueError("Test invoice amount must be between 1 and 100 sats.")
    return amount_sats


def parse_payment_test_amount_sats(value, default=100):
    """Parse the configured test amount and fall back safely when invalid."""
    try:
        return normalize_payment_test_amount_sats(value)
    except ValueError:
        logger.warning(
            "Invalid payment test amount; using the safe default of %s sats.",
            default,
        )
        return normalize_payment_test_amount_sats(default)


def _invoice_field(invoice, name, default=None):
    """Read a field from either an invoice mapping or object."""
    if isinstance(invoice, dict):
        return invoice.get(name, default)
    return getattr(invoice, name, default)


def _normalize_integer(value):
    """Return a non-negative integer or ``None`` for invalid input."""
    if value is None or isinstance(value, bool):
        return None
    try:
        normalized = int(value)
    except (TypeError, ValueError):
        return None
    return normalized if normalized >= 0 else None


def _timestamp_string(value=None):
    """Return a UTC ISO timestamp for a datetime or the current time."""
    timestamp = value or datetime.now(timezone.utc)
    if timestamp.tzinfo is None:
        timestamp = timestamp.replace(tzinfo=timezone.utc)
    return timestamp.astimezone(timezone.utc).isoformat()


def _normalize_test_invoice(raw_invoice):
    """Normalize one persisted test invoice record."""
    if not isinstance(raw_invoice, dict):
        return None
    invoice = raw_invoice.get("invoice")
    payment_hash = raw_invoice.get("payment_hash")
    amount_msat = _normalize_integer(raw_invoice.get("amount_msat"))
    amount_sats = _normalize_integer(raw_invoice.get("amount_sats"))
    if (
        not invoice
        or not payment_hash
        or amount_msat is None
        or amount_sats is None
        or amount_msat != amount_sats * 1000
    ):
        return None
    try:
        amount_sats = normalize_payment_test_amount_sats(amount_sats)
    except ValueError:
        return None

    state = str(raw_invoice.get("state", "pending")).lower()
    if state not in PAYMENT_TEST_INVOICE_STATES:
        state = "pending"
    return {
        "test_id": str(raw_invoice.get("test_id", "")),
        "invoice": str(invoice),
        "payment_hash": str(payment_hash),
        "amount_sats": amount_sats,
        "amount_msat": amount_msat,
        "created_at": raw_invoice.get("created_at"),
        "expires_at": _normalize_integer(raw_invoice.get("expires_at")),
        "state": state,
        "settled_at": raw_invoice.get("settled_at"),
        "last_checked_at": raw_invoice.get("last_checked_at"),
    }


def build_payment_test_state():
    """Build an empty isolated payment-test state document."""
    return {
        "version": PAYMENT_TEST_STATE_VERSION,
        "active_invoice": None,
        "history": [],
    }


def normalize_payment_test_state(raw_state):
    """Normalize test state without importing or altering production payment state."""
    state = build_payment_test_state()
    if not isinstance(raw_state, dict):
        return state

    active_invoice = _normalize_test_invoice(raw_state.get("active_invoice"))
    history = []
    raw_history = raw_state.get("history", [])
    if isinstance(raw_history, list):
        history = [
            invoice
            for raw_invoice in raw_history
            if (invoice := _normalize_test_invoice(raw_invoice)) is not None
        ]
    state["active_invoice"] = active_invoice
    state["history"] = history
    return state


def load_payment_test_state(file_path):
    """Load isolated payment-test state from JSON."""
    return normalize_payment_test_state(load_json(file_path, {}))


def save_payment_test_state(file_path, state):
    """Persist isolated payment-test state atomically."""
    save_json(file_path, normalize_payment_test_state(state))


def build_payment_test_invoice_record(
    invoice,
    amount_sats,
    test_id,
    created_at=None,
):
    """Build a normalized test invoice record with an exact sat amount."""
    amount_sats = normalize_payment_test_amount_sats(amount_sats)
    invoice_value = _invoice_field(invoice, "invoice")
    payment_hash = _invoice_field(invoice, "payment_hash")
    amount_msat = _normalize_integer(_invoice_field(invoice, "amount_msat"))
    if amount_msat is None:
        amount_msat = _normalize_integer(_invoice_field(invoice, "amount"))
    expected_msat = amount_sats * 1000
    if (
        not invoice_value
        or not payment_hash
        or amount_msat != expected_msat
    ):
        raise ValueError("Test invoice amount or identity does not match the requested amount.")
    return {
        "test_id": str(test_id),
        "invoice": str(invoice_value),
        "payment_hash": str(payment_hash),
        "amount_sats": amount_sats,
        "amount_msat": expected_msat,
        "created_at": _timestamp_string(created_at),
        "expires_at": _normalize_integer(_invoice_field(invoice, "expires_at")),
        "state": "pending",
        "settled_at": None,
        "last_checked_at": None,
    }


def get_active_payment_test_invoice(state):
    """Return the active test invoice when it is still pending or settled."""
    invoice = normalize_payment_test_state(state).get("active_invoice")
    if invoice and invoice.get("state") in {"pending", "settled"}:
        return invoice
    return None


def record_payment_test_invoice(state, invoice, amount_sats, test_id, created_at=None):
    """Persist one test invoice while preserving prior test history."""
    normalized_state = normalize_payment_test_state(state)
    active_invoice = normalized_state.get("active_invoice")
    if active_invoice and active_invoice.get("state") in {"pending", "settled"}:
        raise PaymentTestInvoiceConflict("A payment test invoice is already active.")

    invoice_record = build_payment_test_invoice_record(
        invoice,
        amount_sats,
        test_id,
        created_at=created_at,
    )
    if active_invoice:
        normalized_state["history"].append(active_invoice)
    normalized_state["active_invoice"] = invoice_record
    return normalized_state
